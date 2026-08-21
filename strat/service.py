"""Headless document loading helpers shared by HTTP and other front ends.

This module deliberately has no dependency on :mod:`tkinter`.  The desktop UI
has historically kept these small helpers in ``ui.state``; keeping the service
version here lets a server process inspect a document without constructing a
Tk root window.
"""

from __future__ import unicode_literals

import math
import re

from .column import available_unit_columns, parse_column
from .section import parse_section
from .tableio import get as cell
from .tableio import read_table


SECTION_KEYS = ("钻孔", "孔号", "钻孔编号")
MAX_LAYERS = 2000
MAX_LAYER_THICKNESS = 10 ** 7
MAX_TOTAL_THICKNESS = 10 ** 8
MAX_COORDINATE_ABS = 10 ** 12
MAX_CELL_CHARS = 10000


def sniff_kind(rows):
    """Return ``"section"`` when rows contain borehole data, else ``"column"``.

    A non-empty value is required rather than merely a matching header.  This
    preserves the desktop application's existing type-detection behaviour.
    """
    for row in rows:
        if cell(row, *SECTION_KEYS):
            return "section"
    return "column"


def _safe_parse_message(exc):
    """Turn parser failures into useful messages without echoing whole rows."""
    message = str(exc).strip() or "表格数据格式不正确"
    # parse_column/parse_section historically append ``：{row!r}``.  Apart from
    # being noisy, that can disclose unrelated cells from an uploaded record.
    message = re.sub(r"[：:]\s*\{.*\}\s*$", "", message, flags=re.S)
    # Defensive fallback for third-party parser errors containing a dict repr.
    if "{" in message and "}" in message:
        message = message.split("{", 1)[0].rstrip(" ：:")
    return message[:300]


def _finite(value, label):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("%s必须是数字" % label)
    if not math.isfinite(number):
        raise ValueError("%s必须是有限数字" % label)
    return number


def _validate_data(kind, data, max_layers=MAX_LAYERS):
    """Validate numerical invariants that the plotting functions rely on."""
    if kind == "column":
        layer_count = len(data)
        if layer_count > max_layers:
            raise ValueError("地层层数不能超过 %d 层" % max_layers)
        total = 0.0
        for index, layer in enumerate(data, 1):
            thick = _finite(layer.get("thick"), "第 %d 层厚度" % index)
            if thick <= 0:
                raise ValueError("第 %d 层厚度必须大于 0" % index)
            if thick > MAX_LAYER_THICKNESS:
                raise ValueError("第 %d 层厚度超出允许范围" % index)
            total += thick
            if not math.isfinite(total) or total > MAX_TOTAL_THICKNESS:
                raise ValueError("地层总厚度超出允许范围")
            for value in layer.values():
                if isinstance(value, str) and len(value) > MAX_CELL_CHARS:
                    raise ValueError("第 %d 层包含过长文本" % index)
        return

    layer_count = sum(len(hole.get("layers", ())) for hole in data)
    if layer_count > max_layers:
        raise ValueError("地层层数不能超过 %d 层" % max_layers)
    for hole_index, hole in enumerate(data, 1):
        x = _finite(hole.get("x"), "第 %d 个钻孔距离" % hole_index)
        elev = _finite(hole.get("elev"), "第 %d 个钻孔孔口标高" % hole_index)
        if abs(x) > MAX_COORDINATE_ABS or abs(elev) > MAX_COORDINATE_ABS:
            raise ValueError("第 %d 个钻孔坐标超出允许范围" % hole_index)
        if len(str(hole.get("name") or "")) > MAX_CELL_CHARS:
            raise ValueError("第 %d 个钻孔名称过长" % hole_index)
        total = 0.0
        for layer_index, layer in enumerate(hole.get("layers", ()), 1):
            thick = _finite(layer[2], "第 %d 个钻孔第 %d 层厚度" %
                            (hole_index, layer_index))
            if thick <= 0:
                raise ValueError("第 %d 个钻孔第 %d 层厚度必须大于 0" %
                                 (hole_index, layer_index))
            if thick > MAX_LAYER_THICKNESS:
                raise ValueError("第 %d 个钻孔第 %d 层厚度超出允许范围" %
                                 (hole_index, layer_index))
            total += thick
            if not math.isfinite(total) or total > MAX_TOTAL_THICKNESS:
                raise ValueError("第 %d 个钻孔总厚度超出允许范围" % hole_index)
            if any(isinstance(value, str) and len(value) > MAX_CELL_CHARS
                   for value in layer):
                raise ValueError("第 %d 个钻孔第 %d 层包含过长文本" %
                                 (hole_index, layer_index))


def load_any(path, max_layers=MAX_LAYERS):
    """Read *path* once and return ``(kind, parsed_data)``.

    CSV, XLSX and XLSM support is provided by :func:`strat.tableio.read_table`.
    Parser errors are sanitised so an API response never contains the complete
    contents of an offending row.
    """
    try:
        rows = read_table(path)
        if not rows:
            raise ValueError("表格里没有数据行")
        if len(rows) > max_layers:
            raise ValueError("地层层数不能超过 %d 层" % max_layers)
        kind = sniff_kind(rows)
        data = parse_section(rows) if kind == "section" else parse_column(rows)
        _validate_data(kind, data, max_layers=max_layers)
        return kind, data
    except ValueError as exc:
        raise ValueError(_safe_parse_message(exc)) from None
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError(_safe_parse_message(exc)) from None


def _ordered_unique(values):
    seen = set()
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def document_metadata(kind, data):
    """Return JSON-ready metadata for parsed column or section data."""
    if kind == "column":
        layer_count = len(data)
        total = sum(float(layer["thick"]) for layer in data)
        lithologies = _ordered_unique(layer.get("lith") for layer in data)
        unit_columns = [
            {"key": key, "label": label, "category": category}
            for key, label, category in available_unit_columns(data)
        ]
        return {
            "kind": kind,
            "summary": "柱状图 · %d 层 · 总厚 %g m" % (layer_count, total),
            "lithologies": lithologies,
            "unit_columns": unit_columns,
            "layer_count": layer_count,
        }

    if kind == "section":
        layer_count = sum(len(hole.get("layers", ())) for hole in data)
        lithologies = _ordered_unique(
            layer[1] for hole in data for layer in hole.get("layers", ()))
        return {
            "kind": kind,
            "summary": "剖面图 · %d 个钻孔 · %d 层" %
                       (len(data), layer_count),
            "lithologies": lithologies,
            "unit_columns": [],
            "layer_count": layer_count,
            "hole_count": len(data),
        }

    raise ValueError("未知文档类型")


# A short alias is convenient for callers and maintains a discoverable API.
metadata = document_metadata


__all__ = ["MAX_LAYERS", "SECTION_KEYS", "document_metadata", "load_any",
           "metadata", "sniff_kind"]
