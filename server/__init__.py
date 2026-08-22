"""Small, dependency-free HTTP server for the stratigraphy renderer.

Only the HTTP layer uses the Python standard library.  The plotting and XLSX
features continue to use the dependencies already required by ``strat``.
"""

from __future__ import unicode_literals

import copy
import io
import ipaddress
import json
import logging
import math
import mimetypes
import os
import re
import socket
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

# Some headless/service accounts have no writable ``~/.config``.  A stable
# per-user temporary cache avoids a warning and repeated font-cache rebuilds.
if "MPLCONFIGDIR" not in os.environ:
    _mpl_cache = os.path.join(tempfile.gettempdir(), "strat-matplotlib-%s" %
                              getattr(os, "getuid", lambda: "user")())
    try:
        os.makedirs(_mpl_cache, mode=0o700, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = _mpl_cache
    except OSError:
        pass

import matplotlib

# A server must never try to open an interactive graphics window.  This call is
# intentionally made before importing any plotting modules.
matplotlib.use("Agg", force=True)

from matplotlib.figure import Figure
from matplotlib.patches import Polygon

from strat import __version__ as SERVER_VERSION
from strat import fonts as strat_fonts
from strat import gb958
from strat import lithology
from strat.column import (PAGE_SIZES, STRATA_CATS, WIDTH_LIMITS, render_column,
                          resolve_page)
from strat.gb958 import CATEGORIES, render_catalog_sheet
from strat.lithology import render_pattern_sheet, render_shapes_sheet
from strat.section import render_section
from strat.service import MAX_LAYERS, document_metadata, load_any


API_VERSION = "v1"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_DOCUMENTS = 32
MAX_REPOSITORY_BYTES = 64 * 1024 * 1024
DOCUMENT_TTL_SECONDS = 2 * 60 * 60
MIN_DPI = 36
MAX_DPI = 600
DEFAULT_DPI = 300
# Keep the worst-case RGBA canvas at or below 128 MiB.  Matplotlib and image
# encoders need additional working memory, so the previous 80-million-pixel
# ceiling could make a single request consume several hundred MiB.
MAX_RENDER_PIXELS = 32 * 1000 * 1000
MAX_RENDER_SIDE_PIXELS = 20000
MAX_RENDER_MEMORY_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_REQUEST_WORKERS = 8
REQUEST_TIMEOUT_SECONDS = 20.0
UPLOAD_EXTENSIONS = (".csv", ".xlsx", ".xlsm")
OUTPUT_FORMATS = ("png", "pdf", "svg")
SHEET_KINDS = ("patterns", "shapes", "catalog")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNIT_LEVELS = (
    ("eon", "宙"), ("era", "代"), ("period", "纪"),
    ("epoch", "世"), ("age", "期"), ("eonothem", "宇"),
    ("erathem", "界"), ("system", "系"), ("series", "统"),
    ("stage", "阶"), ("group", "群"), ("formation", "组"),
    ("member", "段"), ("unit", "地层单位"),
)
_UNIT_NAMES = {item for pair in _UNIT_LEVELS for item in pair}

_STATIC_CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'"
)

_LOGGER = logging.getLogger("strat.server")


class _OutputTooLarge(Exception):
    pass


class _LimitedBytesIO(io.BytesIO):
    """Bytes buffer that rejects an oversized encoder write immediately."""

    def __init__(self, maximum):
        io.BytesIO.__init__(self)
        self.maximum = int(maximum)
        self._high_water = 0

    def write(self, value):
        end = self.tell() + len(value)
        if end > self.maximum:
            raise _OutputTooLarge()
        written = io.BytesIO.write(self, value)
        self._high_water = max(self._high_water, self.tell())
        return written


def _normalise_hostname(hostname):
    value = str(hostname or "").strip().rstrip(".").lower()
    if not value:
        return ""
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def _is_loopback_host(hostname):
    value = _normalise_hostname(hostname)
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _parse_authority(value):
    """Parse a Host-style authority without accepting user-info or paths."""
    if (not isinstance(value, str) or not value or len(value) > 255
            or _CONTROL_RE.search(value) or any(ch.isspace() for ch in value)
            or any(ch in value for ch in "/\\?#@,")):
        raise ValueError("invalid authority")
    parsed = urlsplit("//" + value)
    if (not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path):
        raise ValueError("invalid authority")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("invalid authority")
    return _normalise_hostname(parsed.hostname), port


class APIError(Exception):
    """An expected request error with a stable machine-readable code."""

    def __init__(self, status, code, message):
        Exception.__init__(self, message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


def _utc_iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _estimate_heap_bytes(value):
    """Conservative, dependency-free estimate for parsed document storage."""
    total = 0
    stack = [value]
    while stack:
        item = stack.pop()
        if item is None or isinstance(item, (bool, int, float)):
            total += 32
        elif isinstance(item, str):
            total += 64 + len(item.encode("utf-8", errors="replace"))
        elif isinstance(item, dict):
            total += 128 + 24 * len(item)
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            total += 72 + 16 * len(item)
            stack.extend(item)
        else:
            total += 128
    return total


class DocumentRecord(object):
    """Parsed document kept in memory; upload bytes and temporary paths vanish."""

    def __init__(self, filename, kind, data, now, ttl_seconds):
        self.id = str(uuid.uuid4())
        self.filename = filename
        self.kind = kind
        self.data = data
        self.created_at = now
        self.expires_at = now + ttl_seconds
        self.size_bytes = _estimate_heap_bytes(data)
        self.info = document_metadata(kind, data)

    def metadata(self):
        result = dict(self.info)
        result.update({
            "id": self.id,
            "filename": self.filename,
            "created_at": _utc_iso(self.created_at),
            "expires_at": _utc_iso(self.expires_at),
        })
        return result


class DocumentRepository(object):
    """Thread-safe bounded document repository with deterministic TTL cleanup."""

    def __init__(self, max_documents=MAX_DOCUMENTS,
                 ttl_seconds=DOCUMENT_TTL_SECONDS, clock=None,
                 max_bytes=MAX_REPOSITORY_BYTES):
        self.max_documents = int(max_documents)
        self.max_bytes = int(max_bytes)
        self.ttl_seconds = float(ttl_seconds)
        if self.max_documents < 1 or self.max_bytes < 1:
            raise ValueError("文档数量和内存上限必须大于 0")
        self._clock = clock or time.time
        self._documents = {}
        self._bytes = 0
        self._lock = threading.RLock()

    def _purge_locked(self, now):
        expired = [key for key, doc in self._documents.items()
                   if doc.expires_at <= now]
        for key in expired:
            self._bytes -= self._documents.pop(key).size_bytes

    def add(self, filename, kind, data):
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            if len(self._documents) >= self.max_documents:
                raise APIError(HTTPStatus.TOO_MANY_REQUESTS,
                               "repository_full",
                               "服务器已达到文档数量上限，请删除旧文档后重试")
            doc = DocumentRecord(filename, kind, data, now, self.ttl_seconds)
            if doc.size_bytes > self.max_bytes:
                raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               "document_too_large",
                               "解析后的文档超过服务器内存限制")
            if self._bytes + doc.size_bytes > self.max_bytes:
                raise APIError(HTTPStatus.TOO_MANY_REQUESTS,
                               "repository_full",
                               "服务器已达到文档内存上限，请删除旧文档后重试")
            self._documents[doc.id] = doc
            self._bytes += doc.size_bytes
            return doc

    def get(self, document_id):
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            doc = self._documents.get(document_id)
            if doc is None:
                raise APIError(HTTPStatus.NOT_FOUND, "document_not_found",
                               "文档不存在或已经过期")
            return doc

    def delete(self, document_id):
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            removed = self._documents.pop(document_id, None)
            if removed is None:
                raise APIError(HTTPStatus.NOT_FOUND, "document_not_found",
                               "文档不存在或已经过期")
            self._bytes -= removed.size_bytes

    def __len__(self):
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            return len(self._documents)

    @property
    def bytes_used(self):
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            return self._bytes


class _RenderBaseline(object):
    """Snapshot mutable plotting globals and restore them in-place."""

    def __init__(self):
        self.lithology = dict(lithology.LITHOLOGY)
        self.patterns = dict(lithology.PATTERNS)
        self.alias_order = tuple(lithology._ALIAS_ORDER)
        self.sheet_hidden = set(lithology.SHEET_HIDDEN)
        self.shape_flip = list(lithology._SHAPE_FLIP)
        self.gb_names = set(gb958.GB_NAMES)
        self.gb_catalog = gb958._CATALOG
        self.font_base_size = strat_fonts.BASE_FS
        self.font_family = strat_fonts._user_family
        self.font_done = strat_fonts._done
        self.rc_font_sans = list(matplotlib.rcParams["font.sans-serif"])
        self.rc_unicode_minus = matplotlib.rcParams["axes.unicode_minus"]

    def restore(self):
        lithology.LITHOLOGY.clear()
        lithology.LITHOLOGY.update(self.lithology)
        lithology.PATTERNS.clear()
        lithology.PATTERNS.update(self.patterns)
        lithology._ALIAS_ORDER = tuple(self.alias_order)
        lithology.SHEET_HIDDEN.clear()
        lithology.SHEET_HIDDEN.update(self.sheet_hidden)
        lithology._SHAPE_FLIP[:] = self.shape_flip
        gb958.GB_NAMES.clear()
        gb958.GB_NAMES.update(self.gb_names)
        gb958._CATALOG = self.gb_catalog
        strat_fonts.BASE_FS = self.font_base_size
        strat_fonts._user_family = self.font_family
        strat_fonts._done = self.font_done
        matplotlib.rcParams["font.sans-serif"] = list(self.rc_font_sans)
        matplotlib.rcParams["axes.unicode_minus"] = self.rc_unicode_minus


_RENDER_LOCK = threading.RLock()
_RENDER_BASELINE = _RenderBaseline()


def _public_message(exc, fallback="请求参数不正确"):
    message = str(exc).strip() or fallback
    # Never return complete row/dict representations from underlying parsers.
    message = re.sub(r"[：:]\s*\{.*\}\s*$", "", message, flags=re.S)
    return message[:300]


def _validate_json_tree(value, depth=0, budget=None):
    """Reject non-finite values and pathologically deep/large JSON trees."""
    if budget is None:
        budget = [20000]
    budget[0] -= 1
    if budget[0] < 0:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "json_too_complex",
                       "JSON 内容过于复杂")
    if depth > 32:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "json_too_deep",
                       "JSON 嵌套层级不能超过 32 层")
    if isinstance(value, float) and not math.isfinite(value):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "non_finite_number",
                       "JSON 中不能使用 NaN 或无穷大")
    if isinstance(value, str) and len(value) > 200000:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "string_too_long",
                       "JSON 字符串过长")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "invalid_json_key", "JSON 对象键必须是字符串")
            _validate_json_tree(item, depth + 1, budget)
    elif isinstance(value, list):
        for item in value:
            _validate_json_tree(item, depth + 1, budget)


def _reject_unknown(mapping, allowed, where):
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "unknown_field",
                       "%s含未知字段：%s" % (where, "、".join(unknown)))


def _number(value, field, minimum, maximum, integer=False, nullable=False,
            clamp=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s必须是数字" % field)
    number = float(value)
    if not math.isfinite(number):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s必须是有限数字" % field)
    if integer and number != int(number):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s必须是整数" % field)
    if not minimum <= number <= maximum and not clamp:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s应在 %g–%g 之间" % (field, minimum, maximum))
    if clamp:
        number = min(max(number, minimum), maximum)
    return int(number) if integer else number


def _boolean(value, field, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, bool):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s必须是布尔值" % field)
    return value


def _short_text(value, field, maximum, allow_empty=True):
    if not isinstance(value, str):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s必须是字符串" % field)
    if len(value) > maximum or _CONTROL_RE.search(value):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s长度或字符不合法" % field)
    value = value.strip()
    if not value and not allow_empty:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                       "%s不能为空" % field)
    return value


def _validate_style(style):
    if style is None:
        return None
    if not isinstance(style, dict):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_style",
                       "style 必须是 JSON 对象")
    _reject_unknown(style, ("patterns", "lithology", "aliases"), "style")
    pattern_budget = [500]
    for section in ("patterns", "lithology", "aliases"):
        part = style.get(section)
        if part is not None and not isinstance(part, dict):
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_style",
                           "style.%s 必须是对象" % section)
        if isinstance(part, dict) and len(part) > 1000:
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_style",
                           "style.%s 条目过多" % section)
        for key in (part or {}):
            _short_text(key, "style.%s 名称" % section, 120,
                        allow_empty=False)
    for name, spec in (style.get("patterns") or {}).items():
        if not name.startswith("_"):
            _validate_pattern_spec(spec, "花纹“%s”" % name, pattern_budget)
    for name, info in (style.get("lithology") or {}).items():
        if name.startswith("_"):
            continue
        if isinstance(info, dict):
            _reject_unknown(info, ("color", "pattern"),
                            "style.lithology.%s" % name)
            for field in ("color", "pattern"):
                value = info.get(field)
                if value is not None:
                    _short_text(value, "style.lithology.%s.%s" %
                                (name, field), 120, allow_empty=False)
        elif isinstance(info, (list, tuple)):
            if len(info) != 2:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_style",
                               "style.lithology.%s 必须包含颜色和花纹" % name)
            for value in info:
                if value is not None:
                    _short_text(value, "style.lithology.%s" % name, 120,
                                allow_empty=False)
    for alias, target in (style.get("aliases") or {}).items():
        if not alias.startswith("_"):
            _short_text(target, "style.aliases.%s" % alias, 120,
                        allow_empty=False)
    return style


_PATTERN_POSITIVE = {
    "spacing": (0.25, 100.0), "w": (0.05, 50.0),
    "h": (0.05, 50.0), "size": (0.05, 100.0),
    "ratio": (0.05, 100.0), "wavelength": (0.1, 100.0),
}
_PATTERN_NONNEGATIVE = {
    "dash": (0.0, 100.0), "gap": (0.0, 100.0),
    "amp": (0.0, 100.0), "double": (0.0, 50.0),
    "jdouble": (0.0, 50.0), "lw": (0.0, 20.0),
}
_PATTERN_SIGNED = {
    "angle": (-3600.0, 3600.0), "offset": (-100.0, 100.0),
    "phase": (-100.0, 100.0), "origin": (-100.0, 100.0),
    "slant": (-100.0, 100.0), "tilt": (-3600.0, 3600.0),
    "xoff": (-100.0, 100.0), "yoff": (-100.0, 100.0),
}


def _pattern_number(value, field, limits, zero_or_positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                       "%s 必须是数字" % field)
    number = float(value)
    if not math.isfinite(number):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                       "%s 必须是有限数字" % field)
    lo, hi = limits
    if zero_or_positive and number == 0:
        return
    if not lo <= number <= hi:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                       "%s 应在 %g–%g 之间" % (field, lo, hi))


def _validate_pattern_spec(spec, where="花纹", budget=None):
    """Fully validate a declarative pattern before any drawing/global writes."""
    if budget is None:
        budget = [500]

    def walk(candidate, location):
        try:
            elements = lithology.normalize_spec(candidate, where=location)
        except (ValueError, TypeError, RecursionError) as exc:
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                           _public_message(exc, "%s定义不正确" % location))
        for index, element in enumerate(elements, 1):
            budget[0] -= 1
            if budget[0] < 0:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "pattern_too_complex", "花纹图元不能超过 500 个")
            item_at = "%s第 %d 个图元" % (location, index)
            for key, value in element.items():
                field = "%s.%s" % (item_at, key)
                if key in _PATTERN_POSITIVE:
                    _pattern_number(value, field, _PATTERN_POSITIVE[key])
                elif key == "xspacing":
                    _pattern_number(value, field, (0.25, 100.0),
                                    zero_or_positive=True)
                elif key in _PATTERN_NONNEGATIVE:
                    _pattern_number(value, field, _PATTERN_NONNEGATIVE[key])
                elif key in _PATTERN_SIGNED:
                    _pattern_number(value, field, _PATTERN_SIGNED[key])
                elif key == "jshort":
                    _pattern_number(value, field, (0.0, 1.0))
                elif key in ("filled", "stagger", "bold"):
                    if not isinstance(value, bool):
                        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                       "invalid_spec", "%s 必须是布尔值" % field)
                elif key in ("type", "marker", "shape", "color"):
                    _short_text(value, field, 64, allow_empty=False)

            if element.get("type") == "rows":
                rows = element.get("rows") or []
                heights = element.get("heights")
                if not isinstance(rows, (list, tuple)) or len(rows) > 100:
                    raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                   "pattern_too_complex",
                                   "%s.rows 不能超过 100 行" % item_at)
                if heights is not None:
                    if (not isinstance(heights, (list, tuple))
                            or len(heights) > 100):
                        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                       "invalid_spec",
                                       "%s.heights 必须是最多 100 项的数字列表" %
                                       item_at)
                    for height_index, height in enumerate(heights, 1):
                        _pattern_number(height,
                                        "%s.heights[%d]" %
                                        (item_at, height_index),
                                        (0.05, 100.0))
                for row_index, row in enumerate(rows, 1):
                    walk(row, "%s第 %d 行" % (item_at, row_index))

    walk(spec, where)
    return spec


def _apply_style_object(style):
    if not style:
        return
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         suffix=".json", prefix="strat-style-",
                                         delete=False) as temp:
            path = temp.name
            json.dump(style, temp, ensure_ascii=False, allow_nan=False)
        lithology.load_style(path)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _render_bytes(draw, data, style, font, font_size, output_format, dpi):
    """Execute one fully isolated render transaction under the global lock."""
    figure = None
    with _RENDER_LOCK:
        _RENDER_BASELINE.restore()
        try:
            _apply_style_object(style)
            strat_fonts.set_family(font)
            strat_fonts.set_base_size(font_size)
            strat_fonts.setup()
            request_data = copy.deepcopy(data)
            figure = draw(request_data)
            width, height = (float(v) for v in figure.get_size_inches())
            if (not math.isfinite(width) or not math.isfinite(height)
                    or width <= 0 or height <= 0):
                raise ValueError("图幅尺寸无效")
            pixel_width = int(math.ceil(width * dpi))
            pixel_height = int(math.ceil(height * dpi))
            pixel_count = pixel_width * pixel_height
            estimated_buffer = pixel_count * 4
            if (pixel_width > MAX_RENDER_SIDE_PIXELS
                    or pixel_height > MAX_RENDER_SIDE_PIXELS
                    or pixel_count > MAX_RENDER_PIXELS
                    or estimated_buffer > MAX_RENDER_MEMORY_BYTES):
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "render_too_large",
                               "图幅与 DPI 的组合过大，请减小页面、层数或 DPI")
            output = _LimitedBytesIO(MAX_OUTPUT_BYTES)
            try:
                figure.savefig(output, format=output_format, dpi=dpi,
                               facecolor="white")
            except _OutputTooLarge:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "render_too_large", "输出图件过大")
            # A memoryview avoids duplicating the complete encoded image just
            # before it is written to the response socket.
            return output.getbuffer()
        finally:
            if figure is not None:
                try:
                    figure.clear()
                except Exception:
                    pass
            _RENDER_BASELINE.restore()


def _validate_render_request(payload, kind):
    _reject_unknown(payload, ("format", "dpi", "download", "options", "style"),
                    "请求")
    output_format = payload.get("format", "png")
    if output_format not in OUTPUT_FORMATS:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_format",
                       "format 可用值：png、pdf、svg")
    dpi = _number(payload.get("dpi", DEFAULT_DPI), "dpi", MIN_DPI, MAX_DPI,
                  integer=True)
    download = _boolean(payload.get("download", False), "download")
    style = _validate_style(payload.get("style"))
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_options",
                       "options 必须是 JSON 对象")

    common = {"title", "font", "font_size", "pattern_row_height_mm"}
    column_only = {"page", "landscape", "scale", "to_scale", "thick_mode",
                   "show_remark", "show_legend", "unit_vertical", "strata",
                   "hide_units", "widths"}
    section_only = {"ve"}
    allowed = common | (column_only if kind == "column" else section_only)
    _reject_unknown(options, allowed, "options")

    title_default = "综合地层柱状图" if kind == "column" else "地层剖面图"
    title = _short_text(options.get("title", title_default), "title", 200)
    if not title:
        title = title_default
    font_value = options.get("font")
    font = None if font_value is None else _short_text(font_value, "font", 100)
    font_size = _number(options.get("font_size", 8.0), "font_size", 6, 12)
    row_min, row_max = lithology.PATTERN_ROW_HEIGHT_LIMITS_MM
    pattern_row_height_mm = _number(
        options.get("pattern_row_height_mm", lithology.PATTERN_ROW_HEIGHT_MM),
        "pattern_row_height_mm", row_min, row_max)

    render_options = {
        "title": title,
        "pattern_row_height_mm": pattern_row_height_mm,
    }
    if kind == "section":
        render_options["ve"] = _number(options.get("ve"), "ve", 0.1, 50,
                                        nullable=True)
    else:
        page = _short_text(options.get("page", "A4"), "page", 40,
                           allow_empty=False)
        landscape = _boolean(options.get("landscape", False), "landscape")
        try:
            page_width, page_height = resolve_page(page, landscape)
        except (TypeError, ValueError) as exc:
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_page",
                           _public_message(exc, "页面规格不正确"))
        if (not all(math.isfinite(v) for v in (page_width, page_height))
                or not all(5 / 2.54 <= v <= 150 / 2.54
                           for v in (page_width, page_height))):
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_page",
                           "页面宽高应在 5–150 厘米之间")

        scale = _number(options.get("scale"), "scale", 1, 100000,
                        integer=True, nullable=True)
        to_scale = _boolean(options.get("to_scale", False), "to_scale")
        thick_mode = options.get("thick_mode", "group")
        if thick_mode not in ("group", "layer", "depth"):
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                           "thick_mode 可用值：group、layer、depth")
        show_remark = _boolean(options.get("show_remark"), "show_remark",
                               nullable=True)
        show_legend = _boolean(options.get("show_legend", False),
                               "show_legend")
        unit_vertical = _boolean(options.get("unit_vertical", False),
                                 "unit_vertical")

        strata = options.get("strata")
        if strata is not None:
            if (not isinstance(strata, list)
                    or len(strata) > len(STRATA_CATS)
                    or any(item not in STRATA_CATS for item in strata)):
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                               "strata 必须由 geochron、chrono、litho 组成")
            strata = set(strata)

        hide_units = options.get("hide_units")
        if hide_units is not None:
            if (not isinstance(hide_units, list) or len(hide_units) > 20
                    or any(not isinstance(item, str) or item not in _UNIT_NAMES
                           for item in hide_units)):
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                               "hide_units 含未知地层单位列")
            hide_units = set(hide_units)

        widths = options.get("widths") or {}
        if not isinstance(widths, dict):
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                           "widths 必须是对象")
        aliases = {"岩性柱": "柱状图", "单位": "地层单位", "层厚": "厚度"}
        clean_widths = {}
        for name, value in widths.items():
            if not isinstance(name, str):
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                               "栏宽名称必须是字符串")
            standard = aliases.get(name.strip(), name.strip())
            if standard not in WIDTH_LIMITS:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_option",
                               "未知栏宽：%s" % name)
            lo, hi = WIDTH_LIMITS[standard]
            clean_widths[standard] = _number(
                value, "%s栏宽" % standard, lo, hi, clamp=True)

        render_options.update({
            "page": page, "landscape": landscape, "scale": scale,
            "to_scale": to_scale, "thick_mode": thick_mode,
            "show_remark": show_remark, "show_legend": show_legend,
            "unit_vertical": unit_vertical, "strata": strata,
            "hide_units": hide_units, "widths": clean_widths,
        })

    return {
        "format": output_format,
        "dpi": dpi,
        "download": download,
        "style": style,
        "font": font,
        "font_size": font_size,
        "options": render_options,
    }


def _validate_sheet_request(payload):
    _reject_unknown(payload, ("kind", "category", "style", "format", "dpi",
                              "download", "font", "font_size"), "请求")
    kind = payload.get("kind")
    if kind not in SHEET_KINDS:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_sheet_kind",
                       "kind 可用值：patterns、shapes、catalog")
    category = payload.get("category")
    if kind == "catalog":
        if category not in CATEGORIES:
            raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_category",
                           "category 不是有效的国标图例类别")
    elif category is not None:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_category",
                       "只有 catalog 图表可以指定 category")
    output_format = payload.get("format", "png")
    if output_format not in OUTPUT_FORMATS:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_format",
                       "format 可用值：png、pdf、svg")
    dpi = _number(payload.get("dpi", 200), "dpi", MIN_DPI, MAX_DPI,
                  integer=True)
    download = _boolean(payload.get("download", False), "download")
    style = _validate_style(payload.get("style"))
    font_value = payload.get("font")
    font = None if font_value is None else _short_text(font_value, "font", 100)
    font_size = _number(payload.get("font_size", 8.0), "font_size", 6, 12)
    return kind, category, output_format, dpi, download, style, font, font_size


def _validate_preview_request(payload):
    _reject_unknown(payload, ("spec", "face", "dpi"), "请求")
    if "spec" not in payload:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "missing_spec",
                       "缺少 spec")
    spec = payload["spec"]
    if not isinstance(spec, (dict, list, str)):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                       "spec 必须是图元对象、列表或基本图形名")
    _validate_pattern_spec(spec, "预览花纹")
    face = payload.get("face", "#f2efe6")
    if not isinstance(face, str) or not matplotlib.colors.is_color_like(face):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_face",
                       "face 必须是有效颜色")
    dpi = _number(payload.get("dpi", 200), "dpi", MIN_DPI, MAX_DPI,
                  integer=True)
    return spec, face, dpi


def _draw_pattern_preview(spec, face):
    figure = Figure(figsize=(4.0, 2.5), dpi=100)
    figure.patch.set_facecolor("white")
    axis = figure.add_axes([0.08, 0.12, 0.84, 0.80])
    axis.set_xlim(0, 4)
    axis.set_ylim(0, 2.4)
    axis.axis("off")
    vertices = [(0.1, 0.1), (3.9, 0.1), (3.9, 2.3), (0.1, 2.3)]
    lithology.paint(axis, vertices, None, spec=spec, face=face)
    axis.add_patch(Polygon(vertices, closed=True, facecolor="none",
                           edgecolor="#333333", linewidth=0.9, zorder=3))
    return figure


def _check_xlsx_archive(path):
    """Bound decompressed OOXML input before openpyxl reads the archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 2000:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "invalid_workbook", "工作簿包含过多文件")
            total = 0
            for info in members:
                total += info.file_size
                if info.flag_bits & 0x1:
                    raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                   "invalid_workbook", "不支持加密工作簿")
                if info.file_size > 32 * 1024 * 1024:
                    raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                   "workbook_too_large", "工作簿解压内容过大")
            if total > 64 * 1024 * 1024:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "workbook_too_large", "工作簿解压内容过大")
    except APIError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_workbook",
                       "工作簿文件无效")


def _load_uploaded_document(raw, filename):
    extension = os.path.splitext(filename)[1].lower()
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=extension,
                                         prefix="strat-upload-",
                                         delete=False) as temp:
            path = temp.name
            temp.write(raw)
        if extension in (".xlsx", ".xlsm"):
            _check_xlsx_archive(path)
        return load_any(path, max_layers=MAX_LAYERS)
    except APIError:
        raise
    except ValueError as exc:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_document",
                       _public_message(exc, "表格数据格式不正确"))
    except Exception:
        raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_document",
                       "无法读取上传的表格，请检查文件格式")
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _content_type(output_format):
    return {
        "png": "image/png",
        "pdf": "application/pdf",
        "svg": "image/svg+xml; charset=utf-8",
    }[output_format]


def _download_header(filename):
    base, extension = os.path.splitext(filename)
    fallback_base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.")
    fallback = (fallback_base or "stratigraphy") + extension
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        fallback.replace('"', ""), quote(filename.encode("utf-8")))


def _capabilities(repository=None):
    try:
        families = [{"label": label, "value": value}
                    for label, value in strat_fonts.available_families()]
    except Exception:
        families = []
    fonts = [{"label": "自动", "value": ""}] + families
    shapes = list(lithology.BASIC_SHAPES)
    return {
        "api_version": API_VERSION,
        "version": SERVER_VERSION,
        "upload": {
            "formats": [ext[1:] for ext in UPLOAD_EXTENSIONS],
            "max_bytes": MAX_UPLOAD_BYTES,
            "max_documents": getattr(repository, "max_documents",
                                     MAX_DOCUMENTS),
            "max_repository_bytes": getattr(repository, "max_bytes",
                                             MAX_REPOSITORY_BYTES),
            "ttl_seconds": getattr(repository, "ttl_seconds",
                                   DOCUMENT_TTL_SECONDS),
            "max_layers": MAX_LAYERS,
        },
        "document": {"kinds": ["column", "section"]},
        "render": {
            "formats": list(OUTPUT_FORMATS),
            "dpi": {"min": MIN_DPI, "max": MAX_DPI,
                    "default": DEFAULT_DPI},
            "limits": {
                "max_pixels": MAX_RENDER_PIXELS,
                "max_side_pixels": MAX_RENDER_SIDE_PIXELS,
                "max_buffer_bytes": MAX_RENDER_MEMORY_BYTES,
                "max_output_bytes": MAX_OUTPUT_BYTES,
            },
            "pages": list(PAGE_SIZES),
            "page_sizes": {
                name: {"width_cm": dimensions[0],
                       "height_cm": dimensions[1]}
                for name, dimensions in PAGE_SIZES.items()
            },
            "thick_modes": ["group", "layer", "depth"],
            "strata": list(STRATA_CATS),
            "fonts": fonts,
            "pattern_row_height_mm": {
                "min": lithology.PATTERN_ROW_HEIGHT_LIMITS_MM[0],
                "max": lithology.PATTERN_ROW_HEIGHT_LIMITS_MM[1],
                "default": lithology.PATTERN_ROW_HEIGHT_MM,
                "step": 0.1,
                "unit": "mm",
            },
            "width_limits": {key: {"min": value[0], "max": value[1]}
                             for key, value in WIDTH_LIMITS.items()},
        },
        "sheets": {"kinds": list(SHEET_KINDS),
                   "categories": list(CATEGORIES)},
        "pattern": {"preview": True, "shapes": shapes},
        "pattern_preview": True,
        "basic_shapes": shapes,
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, server_address, handler_class,
                 max_workers=MAX_REQUEST_WORKERS,
                 request_timeout=REQUEST_TIMEOUT_SECONDS):
        workers = int(max_workers)
        timeout = float(request_timeout)
        if workers < 1:
            raise ValueError("max_workers 必须大于 0")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("request_timeout 必须是大于 0 的有限数")
        self.max_workers = workers
        self.request_timeout = timeout
        self._request_slots = threading.BoundedSemaphore(workers)
        ThreadingHTTPServer.__init__(self, server_address, handler_class)

    def get_request(self):
        request, address = ThreadingHTTPServer.get_request(self)
        request.settimeout(self.request_timeout)
        return request, address

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(False):
            self._reject_busy(request)
            self.shutdown_request(request)
            return
        try:
            ThreadingHTTPServer.process_request(self, request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            ThreadingHTTPServer.process_request_thread(
                self, request, client_address)
        finally:
            self._request_slots.release()

    @staticmethod
    def _reject_busy(request):
        body = json.dumps({
            "error": {
                "code": "server_busy",
                "message": "服务器当前请求过多，请稍后重试",
            }
        }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        response = (
            "HTTP/1.1 503 Service Unavailable\r\n"
            "Content-Type: application/json; charset=utf-8\r\n"
            "Content-Length: %d\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n" % len(body)).encode("ascii") + body
        try:
            request.sendall(response)
        except (OSError, socket.timeout):
            pass


def create_handler(web_dir, example_dir, repository=None,
                   allow_remote=False, allowed_hosts=None):
    """Create a configured request-handler class (useful for unit tests)."""
    web_root = os.path.realpath(os.path.abspath(web_dir))
    example_root = os.path.realpath(os.path.abspath(example_dir))
    documents = repository if repository is not None else DocumentRepository()
    if allowed_hosts is None:
        permitted_hosts = None if allow_remote else {
            "localhost", "127.0.0.1", "::1",
        }
    else:
        permitted_hosts = set()
        for configured_host in allowed_hosts:
            try:
                raw_host = str(configured_host).strip()
                if raw_host.count(":") >= 2 and not raw_host.startswith("["):
                    hostname = _normalise_hostname(
                        ipaddress.ip_address(raw_host).compressed)
                else:
                    hostname, _port = _parse_authority(raw_host)
            except ValueError:
                raise ValueError("无效的 allowed host：%s" % configured_host)
            permitted_hosts.add(hostname)
        # Explicit LAN host restrictions must not make the server's printed
        # loopback URL (or --open-browser) unusable on the host machine.
        permitted_hosts.update(("localhost", "127.0.0.1", "::1"))

    class StratRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "StratHTTP/1.0"
        sys_version = ""

        def log_message(self, format_, *args):
            # Keep BaseHTTPRequestHandler's useful access log but avoid logging
            # request bodies (which this method never receives).
            BaseHTTPRequestHandler.log_message(self, format_, *args)

        def _validate_request_boundary(self):
            host_values = self.headers.get_all("Host") or []
            if len(host_values) != 1:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_host",
                               "请求必须包含唯一有效的 Host")
            try:
                request_host, request_port = _parse_authority(host_values[0])
            except ValueError:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_host",
                               "Host 不合法")
            if (permitted_hosts is not None
                    and request_host not in permitted_hosts):
                raise APIError(HTTPStatus.FORBIDDEN, "host_not_allowed",
                               "Host 不在服务器允许范围内")

            fetch_site = self.headers.get("Sec-Fetch-Site", "").lower()
            if fetch_site == "cross-site":
                raise APIError(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                               "不允许跨站请求")
            origin_values = self.headers.get_all("Origin") or []
            if not origin_values:
                return
            if len(origin_values) != 1 or origin_values[0] == "null":
                raise APIError(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                               "Origin 不合法")
            try:
                origin = urlsplit(origin_values[0])
                origin_host = _normalise_hostname(origin.hostname)
                origin_port = origin.port
            except (TypeError, ValueError):
                raise APIError(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                               "Origin 不合法")
            if (origin.scheme not in ("http", "https") or not origin_host
                    or origin.username is not None
                    or origin.password is not None or origin.path not in ("", "/")
                    or origin.query or origin.fragment):
                raise APIError(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                               "Origin 不合法")
            request_effective_port = request_port or 80
            origin_effective_port = origin_port or (
                443 if origin.scheme == "https" else 80)
            if (origin_host != request_host
                    or origin_effective_port != request_effective_port):
                raise APIError(HTTPStatus.FORBIDDEN, "origin_not_allowed",
                               "只允许同源请求")

        def _security_headers(self, static=False):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            request_id = getattr(self, "request_id", None)
            if request_id:
                self.send_header("X-Request-ID", request_id)
            if self.close_connection:
                self.send_header("Connection", "close")
            if static:
                self.send_header("Content-Security-Policy", _STATIC_CSP)

        def _send_json(self, status, value, head=False):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _send_error_json(self, error, head=False):
            self._send_json(error.status,
                            {"error": {"code": error.code,
                                       "message": error.message}}, head=head)

        def _send_empty(self, status, headers=None):
            self.send_response(int(status))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()

        def _send_binary(self, body, content_type, filename=None,
                         attachment=False, static=False, head=False,
                         cache_control="no-store"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            if attachment and filename:
                self.send_header("Content-Disposition",
                                 _download_header(filename))
            self._security_headers(static=static)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _content_length(self, maximum, required=True):
            if self.headers.get("Transfer-Encoding"):
                raise APIError(HTTPStatus.BAD_REQUEST,
                               "unsupported_transfer_encoding",
                               "不支持分块请求体，请发送 Content-Length")
            raw = self.headers.get("Content-Length")
            if raw is None:
                if required:
                    raise APIError(HTTPStatus.LENGTH_REQUIRED,
                                   "length_required", "缺少 Content-Length")
                return 0
            try:
                length = int(raw)
            except (TypeError, ValueError):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_content_length",
                               "Content-Length 无效")
            if length < 0:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_content_length",
                               "Content-Length 无效")
            if length > maximum:
                self.close_connection = True
                raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                               "request_too_large", "请求体超过大小限制")
            return length

        def _read_bytes(self, maximum, allow_empty=False):
            length = self._content_length(maximum, required=not allow_empty)
            if not length:
                if allow_empty:
                    return b""
                raise APIError(HTTPStatus.BAD_REQUEST, "empty_body",
                               "请求体不能为空")
            body = self.rfile.read(length)
            if len(body) != length:
                self.close_connection = True
                raise APIError(HTTPStatus.BAD_REQUEST, "incomplete_body",
                               "请求体不完整")
            return body

        def _read_json(self, allow_empty=False):
            raw = self._read_bytes(MAX_JSON_BYTES, allow_empty=allow_empty)
            if not raw and allow_empty:
                return {}
            content_type = self.headers.get("Content-Type", "")
            if content_type and not content_type.lower().startswith(
                    "application/json"):
                raise APIError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                               "unsupported_media_type",
                               "请求体必须使用 application/json")
            try:
                value = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=lambda _value: (_ for _ in ()).throw(
                        ValueError("non-finite number")))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_json",
                               "请求体不是有效的 UTF-8 JSON")
            if not isinstance(value, dict):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_json",
                               "JSON 顶层必须是对象")
            _validate_json_tree(value)
            return value

        def _parsed_target(self):
            parsed = urlsplit(self.path)
            try:
                path = unquote(parsed.path, errors="strict")
            except (UnicodeDecodeError, ValueError):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_path",
                               "URL 路径无效")
            if "\x00" in path:
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_path",
                               "URL 路径无效")
            return path, parse_qs(parsed.query, keep_blank_values=True,
                                  strict_parsing=False)

        def _serve_static(self, path, head=False):
            if "\\" in path:
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "资源不存在")
            parts = [part for part in path.split("/") if part]
            if any(part in (".", "..") or part.startswith(".")
                   for part in parts):
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "资源不存在")
            relative = os.path.join(*parts) if parts else "index.html"
            candidate = os.path.realpath(os.path.join(web_root, relative))
            try:
                inside = os.path.commonpath((web_root, candidate)) == web_root
            except ValueError:
                inside = False
            if not inside:
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "资源不存在")
            if os.path.isdir(candidate):
                candidate = os.path.realpath(os.path.join(candidate, "index.html"))
                try:
                    inside = (os.path.commonpath((web_root, candidate)) ==
                              web_root)
                except ValueError:
                    inside = False
                if not inside:
                    raise APIError(HTTPStatus.NOT_FOUND, "not_found",
                                   "资源不存在")
            if not os.path.isfile(candidate):
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "资源不存在")
            try:
                with open(candidate, "rb") as source:
                    body = source.read()
            except OSError:
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "资源不存在")
            content_type = mimetypes.guess_type(candidate)[0] or (
                "application/octet-stream")
            if content_type.startswith("text/") or content_type in (
                    "application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self._send_binary(body, content_type, static=True, head=head,
                              cache_control="no-cache")

        def _serve_template(self, kind, head=False):
            if kind not in ("column", "section"):
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "模板不存在")
            filename = "%s_template.xlsx" % kind
            candidate = os.path.realpath(os.path.join(example_root, filename))
            try:
                inside = os.path.commonpath((example_root, candidate)) == example_root
            except ValueError:
                inside = False
            if not inside or not os.path.isfile(candidate):
                raise APIError(HTTPStatus.NOT_FOUND, "template_not_found",
                               "模板文件不存在")
            try:
                with open(candidate, "rb") as source:
                    body = source.read()
            except OSError:
                raise APIError(HTTPStatus.NOT_FOUND, "template_not_found",
                               "模板文件不存在")
            self._send_binary(
                body,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=filename, attachment=True, head=head)

        def _upload(self, query):
            filenames = query.get("filename", [])
            if len(filenames) != 1:
                raise APIError(HTTPStatus.BAD_REQUEST, "missing_filename",
                               "查询参数 filename 必须且只能提供一次")
            filename = filenames[0]
            if (not filename or len(filename) > 255 or _CONTROL_RE.search(filename)
                    or filename != os.path.basename(filename)
                    or "/" in filename or "\\" in filename):
                raise APIError(HTTPStatus.BAD_REQUEST, "invalid_filename",
                               "文件名不合法")
            extension = os.path.splitext(filename)[1].lower()
            if extension not in UPLOAD_EXTENSIONS:
                raise APIError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                               "unsupported_file_type",
                               "仅支持 CSV、XLSX 和 XLSM 文件")
            content_type = self.headers.get("Content-Type", "").lower()
            if content_type.startswith("multipart/"):
                raise APIError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                               "multipart_not_supported",
                               "请直接发送文件原始字节，不要使用 multipart")
            raw = self._read_bytes(MAX_UPLOAD_BYTES)
            kind, data = _load_uploaded_document(raw, filename)
            doc = documents.add(filename, kind, data)
            self._send_json(HTTPStatus.CREATED, doc.metadata())

        def _example(self, kind):
            # Consume an optional empty JSON object so persistent connections
            # remain aligned even when fetch sends ``{}``.
            payload = self._read_json(allow_empty=True)
            _reject_unknown(payload, (), "请求")
            candidates = ["%s_demo.xlsx" % kind, "%s_demo.csv" % kind]
            source_path = next((os.path.join(example_root, name)
                                for name in candidates
                                if os.path.isfile(os.path.join(example_root, name))),
                               None)
            if source_path is None:
                raise APIError(HTTPStatus.NOT_FOUND, "example_not_found",
                               "示例数据不存在")
            try:
                parsed_kind, data = load_any(source_path, max_layers=MAX_LAYERS)
            except ValueError as exc:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "invalid_example", _public_message(exc))
            if parsed_kind != kind:
                raise APIError(HTTPStatus.INTERNAL_SERVER_ERROR,
                               "invalid_example", "示例数据类型不正确")
            doc = documents.add(os.path.basename(source_path), parsed_kind, data)
            self._send_json(HTTPStatus.CREATED, doc.metadata())

        def _render_document(self, document_id):
            if not _UUID_RE.match(document_id):
                raise APIError(HTTPStatus.NOT_FOUND, "document_not_found",
                               "文档不存在或已经过期")
            doc = documents.get(document_id)
            request = _validate_render_request(self._read_json(), doc.kind)

            def draw(data):
                if doc.kind == "column":
                    return render_column(data, **request["options"])
                return render_section(data, **request["options"])

            try:
                body = _render_bytes(
                    draw, doc.data, request["style"], request["font"],
                    request["font_size"], request["format"], request["dpi"])
            except APIError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "render_failed",
                               _public_message(exc, "绘图参数不正确"))
            except MemoryError:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "render_too_large", "图件过大，无法渲染")

            extension = "." + request["format"]
            filename = os.path.splitext(doc.filename)[0] + extension
            attachment = request["download"] or request["format"] == "svg"
            self._send_binary(body, _content_type(request["format"]),
                              filename=filename, attachment=attachment)

        def _render_sheet(self):
            values = _validate_sheet_request(self._read_json())
            kind, category, fmt, dpi, download, style, font, font_size = values

            def draw(_unused):
                if kind == "patterns":
                    return render_pattern_sheet()
                if kind == "shapes":
                    return render_shapes_sheet()
                return render_catalog_sheet(category)

            try:
                body = _render_bytes(draw, None, style, font, font_size, fmt, dpi)
            except APIError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "render_failed",
                               _public_message(exc, "图表参数不正确"))
            except MemoryError:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "render_too_large", "图表过大，无法渲染")
            label = category if kind == "catalog" else kind
            filename = "%s.%s" % (label, fmt)
            self._send_binary(body, _content_type(fmt), filename=filename,
                              attachment=download or fmt == "svg")

        def _preview_pattern(self):
            spec, face, dpi = _validate_preview_request(self._read_json())

            def draw(_unused):
                return _draw_pattern_preview(spec, face)

            try:
                body = _render_bytes(draw, None, None, None, 8.0, "png", dpi)
            except APIError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_spec",
                               _public_message(exc, "花纹定义不正确"))
            except MemoryError:
                raise APIError(HTTPStatus.UNPROCESSABLE_ENTITY,
                               "render_too_large", "花纹预览过大")
            self._send_binary(body, "image/png")

        def _dispatch_get(self, head=False):
            path, _query = self._parsed_target()
            if path == "/api/v1/health":
                self._send_json(HTTPStatus.OK,
                                {"status": "ok", "api_version": API_VERSION,
                                 "version": SERVER_VERSION},
                                head=head)
                return
            if path == "/api/v1/capabilities":
                self._send_json(HTTPStatus.OK, _capabilities(documents),
                                head=head)
                return
            template = re.match(r"^/api/v1/templates/(column|section)$", path)
            if template:
                self._serve_template(template.group(1), head=head)
                return
            if path.startswith("/api/"):
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
            self._serve_static(path, head=head)

        def _dispatch_post(self):
            path, query = self._parsed_target()
            if path == "/api/v1/documents":
                self._upload(query)
                return
            example = re.match(r"^/api/v1/examples/(column|section)$", path)
            if example:
                self._example(example.group(1))
                return
            render = re.match(
                r"^/api/v1/documents/([^/]+)/render$", path)
            if render:
                self._render_document(render.group(1))
                return
            if path == "/api/v1/sheets/render":
                self._render_sheet()
                return
            if path == "/api/v1/patterns/preview":
                self._preview_pattern()
                return
            if path.startswith("/api/"):
                raise APIError(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
            raise APIError(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed",
                           "该资源不支持 POST")

        def _dispatch_delete(self):
            path, _query = self._parsed_target()
            match = re.match(r"^/api/v1/documents/([^/]+)$", path)
            if not match:
                if path.startswith("/api/"):
                    raise APIError(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")
                raise APIError(HTTPStatus.METHOD_NOT_ALLOWED,
                               "method_not_allowed", "该资源不支持 DELETE")
            document_id = match.group(1)
            if not _UUID_RE.match(document_id):
                raise APIError(HTTPStatus.NOT_FOUND, "document_not_found",
                               "文档不存在或已经过期")
            documents.delete(document_id)
            self._send_empty(HTTPStatus.NO_CONTENT)

        def _run(self, dispatch, head=False):
            self.request_id = uuid.uuid4().hex[:16]
            try:
                self._validate_request_boundary()
                dispatch()
            except APIError as exc:
                # A route can reject a POST before reading its request body
                # (unknown route, expired document, unsupported upload type).
                # Closing that failed persistent connection prevents those
                # unread bytes from being parsed as a second HTTP request.
                if self.command == "POST":
                    self.close_connection = True
                try:
                    self._send_error_json(exc, head=head)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            except (socket.timeout, TimeoutError):
                self.close_connection = True
                try:
                    self._send_error_json(APIError(
                        HTTPStatus.REQUEST_TIMEOUT, "request_timeout",
                        "读取请求超时"), head=head)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                # 完整异常只进服务日志；响应仅带不可预测的请求 ID，便于定位
                # 同一次失败而不向客户端泄露路径、栈或数据内容。
                _LOGGER.exception(
                    "unhandled request error request_id=%s method=%s path=%s",
                    self.request_id, self.command,
                    urlsplit(self.path).path if self.path else "",
                )
                if self.command == "POST":
                    self.close_connection = True
                try:
                    self._send_error_json(APIError(
                        HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error",
                        "服务器处理请求时发生错误"), head=head)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_GET(self):
            self._run(lambda: self._dispatch_get(head=False))

        def do_HEAD(self):
            self._run(lambda: self._dispatch_get(head=True), head=True)

        def do_POST(self):
            self._run(self._dispatch_post)

        def do_DELETE(self):
            self._run(self._dispatch_delete)

        def do_OPTIONS(self):
            self._run(lambda: self._send_empty(HTTPStatus.NO_CONTENT, {
                "Allow": "GET, HEAD, POST, DELETE, OPTIONS"}))

    StratRequestHandler.web_dir = web_root
    StratRequestHandler.example_dir = example_root
    StratRequestHandler.repository = documents
    return StratRequestHandler


def create_server(host="127.0.0.1", port=8000, web_dir=None,
                  example_dir=None, repository=None, allow_remote=False,
                  allowed_hosts=None, max_workers=MAX_REQUEST_WORKERS,
                  request_timeout=REQUEST_TIMEOUT_SECONDS):
    """Create (but do not start) the threaded HTTP server."""
    if not allow_remote and not _is_loopback_host(host):
        raise ValueError(
            "非回环监听需要显式启用 allow_remote/--allow-network")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if web_dir is None:
        web_dir = os.path.join(project_root, "web")
    if example_dir is None:
        example_dir = os.path.join(project_root, "examples")
    effective_hosts = allowed_hosts
    if effective_hosts is None and not allow_remote:
        effective_hosts = ("localhost", "127.0.0.1", "::1")
    elif effective_hosts is not None:
        effective_hosts = tuple(effective_hosts)
    handler = create_handler(
        web_dir, example_dir, repository=repository,
        allow_remote=allow_remote, allowed_hosts=effective_hosts)
    return _Server((host, int(port)), handler, max_workers=max_workers,
                   request_timeout=request_timeout)


def serve(host="127.0.0.1", port=8000, web_dir=None, example_dir=None,
          open_browser=False, allow_remote=False, allowed_hosts=None,
          max_workers=MAX_REQUEST_WORKERS,
          request_timeout=REQUEST_TIMEOUT_SECONDS):
    """Serve the web UI and API until interrupted."""
    httpd = create_server(
        host, port, web_dir, example_dir, allow_remote=allow_remote,
        allowed_hosts=allowed_hosts, max_workers=max_workers,
        request_timeout=request_timeout)
    actual_host, actual_port = httpd.server_address[:2]
    display_host = actual_host
    if display_host in ("0.0.0.0", "::"):
        display_host = "127.0.0.1" if display_host == "0.0.0.0" else "::1"
    if ":" in display_host and not display_host.startswith("["):
        display_host = "[%s]" % display_host
    url = "http://%s:%d/" % (display_host, actual_port)
    print("地层绘图 Web 服务已启动：%s" % url, flush=True)
    if open_browser:
        timer = threading.Timer(0.25, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


__all__ = [
    "APIError", "DocumentRepository", "create_handler", "create_server",
    "serve", "API_VERSION", "MAX_UPLOAD_BYTES", "MAX_JSON_BYTES",
    "MAX_DOCUMENTS", "DOCUMENT_TTL_SECONDS",
    "MAX_REPOSITORY_BYTES",
    "MAX_RENDER_PIXELS", "MAX_RENDER_SIDE_PIXELS",
    "MAX_RENDER_MEMORY_BYTES", "MAX_OUTPUT_BYTES", "MAX_REQUEST_WORKERS",
    "REQUEST_TIMEOUT_SECONDS",
    "SERVER_VERSION",
]
