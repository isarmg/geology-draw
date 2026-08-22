"""综合地层柱状图绘制。

数据格式（Excel 自动识别数据工作表，或 CSV；自上而下按地层顺序）：
    [地层单位各列],岩性,厚度,描述,备注,接触关系
必填：岩性、厚度；其余可空。
地层单位列可任选以下三类各级（有数据的列才显示，相邻同名自动合并）：
    地质年代：宙 代 纪 世 期      年代地层：宇 界 系 统 阶
    岩石地层（地质地层）：群 组 段
也兼容旧格式的"地层单位"单列。"备注"整列为空时不显示该栏。
"接触关系"指该层与下伏层的接触：写"平行不整合"或"角度不整合"时，
该层底界在柱状图栏内画波状线（角度不整合另加短斜线）。
"压缩"列填"是"时，该层成为允许省略的候选；仅在图身空间不足时按需
压缩显示（中部画折断符号），厚度栏仍标真实厚度——地质柱状图对厚层
的标准表示法。
"""

import math

from matplotlib.figure import Figure

from . import fonts, lithology
from .tableio import read_table, get as _get

# 地层单位列：key、中文表头、类别。三类各级单位一一对应、均可选：
#   geochron 地质年代（时间单位）：宙 代 纪 世 期
#   chrono   年代地层（时间-地层）：宇 界 系 统 阶
#   litho    岩石地层（地质地层）：群 组 段
_UNIT_LEVELS = (
    ("eon",       "宙", "geochron"),
    ("era",       "代", "geochron"),
    ("period",    "纪", "geochron"),
    ("epoch",     "世", "geochron"),
    ("age",       "期", "geochron"),
    ("eonothem",  "宇", "chrono"),
    ("erathem",   "界", "chrono"),
    ("system",    "系", "chrono"),
    ("series",    "统", "chrono"),
    ("stage",     "阶", "chrono"),
    ("group",     "群", "litho"),
    ("formation", "组", "litho"),
    ("member",    "段", "litho"),
)

_CAT_NAMES = {"geochron": "地质年代", "chrono": "年代地层",
              "litho": "岩石地层", "unit": "地层单位"}
STRATA_CATS = ("geochron", "chrono", "litho")

# 各单位列的 Excel/CSV 列名候选（首个为标准名）
_UNIT_ALIASES = {
    "eon": ("宙",), "era": ("代",), "period": ("纪",),
    "epoch": ("世",), "age": ("期",),
    "eonothem": ("宇",), "erathem": ("界",), "system": ("系", "系统"),
    "series": ("统",), "stage": ("阶",),
    "group": ("群",), "formation": ("组", "组名"), "member": ("段",),
}
_UNIT_HEAD = {k: head for k, head, _ in _UNIT_LEVELS}

_FRAME = "#222222"   # 表格线色
CM = 2.54            # 每英寸厘米数

# 厚层压缩显示：被标记的厚层只是“允许压缩”的候选。仅当自然柱高超过
# 当前版面可用高度时才按需缩短，中部画折断符号；未标记层始终保持比例尺，
# 厚度栏仍标真实值（地质柱状图标准表示法）。
COMPRESS_TRIGGER_IN = 1.6   # 图上高度阈值（英寸），超过才允许压缩
COMPRESS_CAP_IN = 0.95      # 自适应压缩后的最小显示高度（英寸）
_COMPRESS_YES = {"是", "压缩", "y", "yes", "true", "1", "√", "✓", "t"}


def _is_compress(ly):
    return (ly.get("compress") or "").strip().lower() in _COMPRESS_YES


def _display_heights(layers, inch_per_m, target_in):
    """按版面缺口自适应压缩，返回 ``(显示高度, 是否实际压缩)``。

    ``target_in`` 是当前版式无需继续增长时可容纳的图身高度。自然柱高不
    超过它时完全不压缩；超过时只从显式标记且自然图高超过触发阈值的层中
    扣除所需高度。多个候选按各自可压缩量同比分摊，最低保留
    ``COMPRESS_CAP_IN``，因此不会改变任何未标记层的比例，也不会因固定
    压到下限而在柱底制造空白。
    """
    if isinstance(target_in, bool):
        raise ValueError("厚层压缩目标高度必须是大于 0 的有限数")
    try:
        target = float(target_in)
    except (TypeError, ValueError):
        raise ValueError("厚层压缩目标高度必须是大于 0 的有限数")
    if not math.isfinite(target) or target <= 0:
        raise ValueError("厚层压缩目标高度必须是大于 0 的有限数")

    natural = [ly["thick"] * inch_per_m for ly in layers]
    if not all(math.isfinite(value) and value > 0 for value in natural):
        raise ValueError("按当前比例尺计算的地层总高度过大")
    capacities = [
        max(real - COMPRESS_CAP_IN, 0.0)
        if _is_compress(ly) and real > COMPRESS_TRIGGER_IN else 0.0
        for ly, real in zip(layers, natural)
    ]
    try:
        natural_total = math.fsum(natural)
        available = math.fsum(capacities)
    except OverflowError:
        raise ValueError("按当前比例尺计算的地层总高度过大") from None
    required = max(natural_total - target, 0.0)
    eps = 1e-9 * max(natural_total, target, 1.0)
    if required <= eps or available <= eps:
        return natural, [False] * len(layers)

    ratio = min(required / available, 1.0)
    displayed = [real - capacity * ratio
                 for real, capacity in zip(natural, capacities)]
    # 可行时把浮点余差交给最后一个候选，保证柱底与目标线严格重合。
    if ratio < 1.0:
        candidates = [index for index, capacity in enumerate(capacities)
                      if capacity > eps]
        if candidates:
            delta = math.fsum(displayed) - target
            index = candidates[-1]
            displayed[index] = min(
                natural[index],
                max(natural[index] - capacities[index],
                    displayed[index] - delta),
            )
    compressed = [real - shown > eps
                  for real, shown in zip(natural, displayed)]
    return displayed, compressed

# ---------------------------------------------------------------------------
# 页面尺寸（厘米，纵向 宽×高）；landscape=True 时交换宽高。
# 也接受自定义 "宽x高" 字符串（厘米），如 "24x36"。
# ---------------------------------------------------------------------------

PAGE_SIZES = {
    "A0": (84.1, 118.9), "A1": (59.4, 84.1), "A2": (42.0, 59.4),
    "A3": (29.7, 42.0),  "A4": (21.0, 29.7), "A5": (14.8, 21.0),
    "B3": (35.3, 50.0),  "B4": (25.0, 35.3), "B5": (17.6, 25.0),
    "8开": (26.0, 37.0), "16开": (18.5, 26.0),
    "信纸": (21.6, 27.9), "法律纸": (21.6, 35.6),
}


def resolve_page(page="A4", landscape=False):
    """页面名或"宽x高"（厘米）→ (宽, 高) 英寸。

    自定义尺寸必须恰好包含两个有限数，且宽、高均在
    5–200 cm 之间。
    """
    key = str(page or "A4").strip()
    alias = {"letter": "信纸", "legal": "法律纸"}
    name = alias.get(key.lower(), key.upper() if key[:1].isascii() else key)
    if name in PAGE_SIZES:
        w_cm, h_cm = PAGE_SIZES[name]
    else:
        parts = key.lower().replace("×", "x").split("x")
        try:
            if len(parts) != 2 or not all(v.strip() for v in parts):
                raise ValueError
            w_cm, h_cm = (float(v.strip()) for v in parts)
        except (TypeError, ValueError):
            raise ValueError(f"未知页面规格“{page}”，可用：{'、'.join(PAGE_SIZES)}"
                             "，或自定义如 24x36（厘米）")
        if (not math.isfinite(w_cm) or not math.isfinite(h_cm)
                or not 5 <= w_cm <= 200 or not 5 <= h_cm <= 200):
            raise ValueError("自定义页面宽高必须是 5–200 厘米之间的有限数")
    if landscape:
        w_cm, h_cm = h_cm, w_cm
    return w_cm / CM, h_cm / CM


# ---------------------------------------------------------------------------
# 栏宽：单位为厘米，可由用户调整，超出范围自动夹紧。
# "岩性描述"栏是其余各栏的余量，不能直接设置，但有最小保障宽度。
# ---------------------------------------------------------------------------

WIDTH_LIMITS = {          # 栏名: (最小cm, 最大cm)
    "地层单位": (0.6, 5.0),   # 旧格式的单列
    "厚度":     (0.4, 3.0),
    "连接带":   (0.2, 1.2),   # 岩性柱两侧的折线连接带（默认版式）
    "柱状图":   (1.0, 8.0),
    "备注":     (0.8, 6.0),
    "图例":     (1.8, 6.0),   # 15 mm 样框+左右各 1.5 mm 净空
}
DESC_MIN_CM = 1.5         # 描述栏（余量）最小宽度（厘米）

_DEFAULT_CM = {"地层单位": 2.2, "厚度": 1.2, "连接带": 0.6, "备注": 2.7,
               "图例": 2.4}

_KEY2NAME = {"unit": "地层单位", "thick": "厚度", "gutl": "连接带",
             "gutr": "连接带", "log": "柱状图", "remark": "备注",
             "legend": "图例"}

# 各单位列以其中文名作为可调栏；岩石地层名较长故默认略宽
for _k, _head, _cat in _UNIT_LEVELS:
    WIDTH_LIMITS[_head] = (0.4, 4.0)
    _DEFAULT_CM[_head] = 1.6 if _cat == "litho" else 1.3
    _KEY2NAME[_k] = _head


def _auto_legend_width(widths, layers, show_legend, fontsize):
    """未指定时，按 15 mm 样框及名称最多两行估算项宽。"""
    if not show_legend:
        return widths
    widths = dict(widths or {})
    if "图例" not in widths:
        widths["图例"] = lithology.legend_col_cm(
            [ly["lith"] for ly in layers], fontsize)
    return widths


def _legend_block_layout(widths, layers, show_legend, fontsize, avail_in):
    """返回 ``(标准化宽度, 图例项宽英寸, 图例块高英寸)``。"""
    widths = _auto_legend_width(widths, layers, show_legend, fontsize)
    if not show_legend:
        return widths, None, 0.0
    unknown = []
    for layer in layers:
        name = layer["lith"]
        if lithology.resolve(name) is None and name not in unknown:
            unknown.append(name)
    if unknown:
        shown = "、".join(unknown[:5])
        more = f"等 {len(unknown)} 项" if len(unknown) > 5 else ""
        raise ValueError(
            "无法生成 GB/T 958 花纹图例：以下岩性没有已定义的唯一花纹——"
            f"{shown}{more}；请改用 GB/T 958 标准名称或在样式文件中定义花纹"
        )
    item_cm = resolve_widths(widths).get("图例", _DEFAULT_CM["图例"])
    item_in = item_cm / CM
    height_in = lithology.legend_height_in(
        [ly["lith"] for ly in layers], avail_in, fontsize,
        item_w_in=item_in)
    return widths, item_in, height_in


def resolve_widths(user):
    """校验并夹紧用户给的栏宽 {栏名: 厘米}；返回标准化后的字典。

    栏宽必须是有限数；有限但超出允许范围的值保持原有行为，
    自动夹紧到 ``WIDTH_LIMITS``。
    """
    out = {}
    if not user:
        return out
    alias = {"岩性柱": "柱状图", "单位": "地层单位", "层厚": "厚度"}
    for k, v in user.items():
        k = alias.get(str(k).strip(), str(k).strip())
        if k in ("描述", "岩性描述"):
            raise ValueError("“岩性描述”栏宽是其余各栏的余量，不能直接设置；"
                             "请通过调整其他栏宽来改变它")
        if k not in WIDTH_LIMITS:
            raise ValueError(f"未知栏名“{k}”，可调栏：{'、'.join(WIDTH_LIMITS)}")
        lo, hi = WIDTH_LIMITS[k]
        try:
            value = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"栏宽“{k}”必须是有限数")
        if not math.isfinite(value):
            raise ValueError(f"栏宽“{k}”必须是有限数")
        out[k] = min(max(value, lo), hi)
    return out


def _build_cols(unit_cols, has_remark, widths, staggered, table_w_cm,
                thick_head="厚度\n(m)", has_legend=False):
    """按默认宽度+用户调整（厘米）生成栏目表（宽度化为占比）；
    描述栏取余量并保证最小宽度。"""
    if not math.isfinite(table_w_cm) or table_w_cm <= 0:
        raise ValueError("页面可用宽度不足，请增大页面或图宽")
    w = dict(_DEFAULT_CM)
    w["柱状图"] = 2.9 if staggered else 3.8
    w.update(resolve_widths(widths))

    def frac(cm):
        return cm / table_w_cm

    cols = [(k, h, frac(w[_KEY2NAME[k]])) for k, h, _ in unit_cols]
    cols.append(("thick", thick_head, frac(w["厚度"])))
    if staggered:
        cols.append(("gutl", "", frac(w["连接带"])))
        cols.append(("log", "柱  状  图", frac(w["柱状图"])))
        cols.append(("gutr", "", frac(w["连接带"])))
    else:
        cols.append(("log", "柱  状  图", frac(w["柱状图"])))
    cols.append(("desc", "岩  性  描  述", 0.0))
    if has_remark:
        cols.append(("remark", "备  注", frac(w["备注"])))
    if has_legend:
        cols.append(("legend", "图  例", frac(w["图例"])))

    minimum_cm = (DESC_MIN_CM
                  + sum(WIDTH_LIMITS[_KEY2NAME[k]][0]
                        for k, _, _ in cols if k != "desc"))
    if table_w_cm + 1e-9 < minimum_cm:
        raise ValueError(
            f"页面可用宽度不足：当前栏目至少需要 {minimum_cm:g} 厘米"
            f"（含 {DESC_MIN_CM:g} 厘米岩性描述栏）；请增大页面或图宽、"
            "改用横向排版，或隐藏部分栏目"
        )

    desc_w = 1 - sum(cw for *_, cw in cols)
    if desc_w < frac(DESC_MIN_CM):  # 余量不足：按各栏可压缩量比例回压
        slack = {i: max(cw - frac(WIDTH_LIMITS[_KEY2NAME[k]][0]), 0.0)
                 for i, (k, _, cw) in enumerate(cols) if k != "desc"}
        needed = frac(DESC_MIN_CM) - desc_w
        available = sum(slack.values())
        if needed > available + 1e-9:
            raise ValueError("页面可用宽度不足，无法为岩性描述栏保留有效宽度")
        f = needed / available if available else 0.0
        cols = [(k, h, cw - slack.get(i, 0.0) * f)
                for i, (k, h, cw) in enumerate(cols)]
        desc_w = 1 - sum(cw for k, _, cw in cols if k != "desc")
    if desc_w <= 0:
        raise ValueError("页面可用宽度不足，岩性描述栏宽度必须大于 0")
    return [(k, h, desc_w if k == "desc" else cw) for k, h, cw in cols]


def load_column(path):
    """读取柱状图数据（Excel 或 CSV），返回层字典列表（自上而下）。"""
    return parse_column(read_table(path))


def parse_column(rows):
    """把已读入的表格行解析为层字典列表（自上而下）。

    与 load_column 分开是为了让调用方（如界面的类型自动识别）只读一次盘
    就能既判断数据类型、又解析内容。

    表格必须包含 1–2000 个层；每层厚度必须是大于 0 的有限数。
    错误信息只报行号和字段，不回显整行数据。
    """
    layers = []
    for i, row in enumerate(rows, 1):
        if i > 2000:
            raise ValueError("柱状图最多支持 2000 层")
        thick = _get(row, "厚度", "层厚")
        lith = _get(row, "岩性", "岩性名称")
        if not thick or not lith:
            raise ValueError(f"第 {i} 行缺少岩性或厚度")
        try:
            thick_value = float(thick)
        except (TypeError, ValueError):
            raise ValueError(f"第 {i} 行厚度必须是数字")
        if not math.isfinite(thick_value) or thick_value <= 0:
            raise ValueError(f"第 {i} 行厚度必须是大于 0 的有限数")
        layer = {
            "no": _get(row, "层号", "序号") or str(i),
            "unit": _get(row, "地层单位", "时代", "地层"),
            "lith": lith,
            "thick": thick_value,
            "desc": _get(row, "描述", "岩性描述", "特征"),
            "remark": _get(row, "备注", "附注"),
            "contact": _get(row, "接触关系", "接触", "不整合"),
            "compress": _get(row, "压缩", "压缩显示", "断裂", "省略"),
        }
        for k, names in _UNIT_ALIASES.items():
            layer[k] = _get(row, *names)
        layers.append(layer)
    if not layers:
        raise ValueError("表格中没有有效数据行")
    return layers


load_column_csv = load_column  # 兼容旧名


def _normalize_render_layers(layers):
    """校验并复制直接传给公共渲染 API 的层记录。

    文件入口已经由 ``parse_column`` 校验，但库调用方可能绕过解析器。渲染
    器不能在深层布局中才因缺键、负厚度或 NaN 失败，更不能接受会制造
    非有限坐标的值。
    """
    try:
        source = list(layers)
    except TypeError:
        raise ValueError("柱状图数据应是层记录列表")
    if not source:
        raise ValueError("柱状图数据不能为空")
    if len(source) > 2000:
        raise ValueError("柱状图最多支持 2000 层")

    text_keys = ("no", "unit", "lith", "desc", "remark", "contact",
                 "compress")
    unit_keys = tuple(key for key, _head, _category in _UNIT_LEVELS)
    result = []
    for index, record in enumerate(source, 1):
        if not isinstance(record, dict):
            raise ValueError(f"第 {index} 层数据必须是字典")
        layer = dict(record)
        for key in text_keys + unit_keys:
            value = layer.get(key, "")
            value = "" if value is None else str(value).strip()
            limit = 200 if key in ("no", "unit", "lith") + unit_keys else 10000
            if len(value) > limit:
                raise ValueError(f"第 {index} 层的 {key} 文本过长（最多 {limit} 字）")
            layer[key] = value
        if not layer["lith"]:
            raise ValueError(f"第 {index} 层缺少岩性")
        if not layer["no"]:
            layer["no"] = str(index)
        thick = layer.get("thick")
        if isinstance(thick, bool):
            raise ValueError(f"第 {index} 层厚度必须是大于 0 的有限数")
        try:
            thick = float(thick)
        except (TypeError, ValueError):
            raise ValueError(f"第 {index} 层厚度必须是数字")
        if not math.isfinite(thick) or thick <= 0:
            raise ValueError(f"第 {index} 层厚度必须是大于 0 的有限数")
        layer["thick"] = thick
        result.append(layer)
    try:
        total = math.fsum(layer["thick"] for layer in result)
    except OverflowError:
        raise ValueError("地层总厚度必须是大于 0 的有限数") from None
    if not math.isfinite(total) or total <= 0:
        raise ValueError("地层总厚度必须是大于 0 的有限数")
    return result


def _char_em(ch):
    """字符宽度估计（em）：全角 1.0，半角 0.55。"""
    return 1.0 if (ord(ch) > 0x2E7F or ch in "…—“”‘’") else 0.55


def _wrap_text(text, usable_in, fs):
    """按可用宽度（英寸）与字号精确折行，全角/半角分别计宽，
    使文字行宽真正跟随栏宽变化。"""
    width_em = max(2.0, usable_in / (fs / 72))
    lines, cur, w = [], "", 0.0
    for ch in text:
        cw = _char_em(ch)
        if cur and w + cw > width_em + 1e-9:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines


def _est_lines(text, usable_in, fs):
    """估算折行后的行数（自动比例尺的预排版用）。"""
    width_em = max(2.0, usable_in / (fs / 72))
    return max(1, math.ceil(sum(map(_char_em, text)) / width_em))


def _stack_blocks(hs, centers, gap, total):
    """一维标签避让：各文字块尽量居中于所属层，重叠时依次下推，
    底部越界再自下而上回推。返回 (各块顶部 y 列表, 是否放得下)。"""
    # 空文本块高度为 0；它们既不占空间，也不应在相邻非空块之间累计
    # gap，否则大量空备注会被误判为“图幅过小、文字已截断”。
    tops = [c - h / 2 for h, c in zip(hs, centers)]
    active = [i for i, h in enumerate(hs) if h > 1e-12]
    for pos, i in enumerate(active):
        if pos:
            previous = active[pos - 1]
            tops[i] = max(tops[i], tops[previous] + hs[previous] + gap)
    if active and tops[active[-1]] + hs[active[-1]] > total:
        last = active[-1]
        tops[last] = total - hs[last]
        for pos in range(len(active) - 2, -1, -1):
            i, following = active[pos], active[pos + 1]
            tops[i] = min(tops[i], tops[following] - gap - hs[i])
    return tops, (not active or tops[active[0]] >= -1e-6)


def _flow_text_column(ax, texts, mids, thicks, x0, x1, total, inch_per_m,
                      table_w_in, base_fs=None):
    """文字栏整体排版：薄层的长文字自动移出本层范围（上下避让），
    并画引线指回所属层。返回是否发生截断（指定比例尺过小时的最后手段）。"""
    base_fs = base_fs or fonts.BASE_FS
    col_in = (x1 - x0) * table_w_in
    pad_in, lead_in, gap_in = 0.09, 0.20, 0.045
    gap_m = gap_in / inch_per_m

    def layout(fs, indent):
        line_m = fs / 72 * 1.55 / inch_per_m
        usable = col_in - pad_in - indent - 0.06
        blk = [_wrap_text(t, usable, fs) for t in texts]
        hs = [len(b) * line_m for b in blk]
        tops, fits = _stack_blocks(hs, mids, gap_m, total)
        return {"fs": fs, "indent": indent, "line_m": line_m,
                "blk": blk, "hs": hs, "tops": tops, "fits": fits}

    def moved(L):
        """哪些块被移出了本层中心（需要引线）。"""
        return [abs(t + h / 2 - m) > 0.6 * L["line_m"] or h > th
                for t, h, m, th in zip(L["tops"], L["hs"], mids, thicks)]

    chosen = None
    for fs in (base_fs, base_fs - 0.5, base_fs - 1.0, base_fs - 1.5,
               base_fs - 2.0):
        L = layout(fs, 0.0)
        if any(moved(L)):  # 有块需要移出本层 → 预留引线区重排
            L = layout(fs, lead_in)
        if L["fits"]:
            chosen = L
            break

    truncated = False
    if chosen is None:  # 最小字号也放不下（指定比例尺过小）：截断最长块
        L = layout(base_fs - 2.0, lead_in)
        cut = set()
        while not L["fits"]:
            cands = [i for i, b in enumerate(L["blk"]) if len(b) > 1]
            if not cands:
                L["tops"][0] = max(L["tops"][0], 0.0)  # 接受少量重叠
                break
            i = max(cands, key=lambda j: L["hs"][j])
            L["blk"][i].pop()
            cut.add(i)
            L["hs"] = [len(b) * L["line_m"] for b in L["blk"]]
            L["tops"], L["fits"] = _stack_blocks(L["hs"], mids, gap_m, total)
        for i in cut:
            L["blk"][i][-1] = L["blk"][i][-1][:-1] + "…"
        chosen, truncated = L, True

    L = chosen
    fl = moved(L)
    x_text = x0 + (pad_in + L["indent"]) / table_w_in
    for i, (lines, top, h) in enumerate(zip(L["blk"], L["tops"], L["hs"])):
        if not lines:
            continue
        if fl[i]:  # 引线：从层中点指向文字块中心
            ax.plot([x0, x_text - 0.03 / table_w_in], [mids[i], top + h / 2],
                    color="#777777", lw=0.6, zorder=2.5)
        y = top + L["line_m"] / 2
        for ln in lines:
            ax.text(x_text, y, ln, ha="left", va="center", fontsize=L["fs"])
            y += L["line_m"]

    # 本栏层间线：只在不穿过文字块处绘制
    eps = 0.02 / inch_per_m
    d = 0.0
    for i in range(len(texts) - 1):
        d += thicks[i]
        if (L["tops"][i] + L["hs"][i] <= d - eps
                and L["tops"][i + 1] >= d + eps):
            ax.plot([x0, x1], [d, d], color=_FRAME, lw=0.7)
    return truncated


def render_column(layers, title="综合地层柱状图", scale=None, page="A4",
                  landscape=False, to_scale=False, widths=None,
                  fig_width=None, thick_per_layer=False,
                  unit_vertical=False, strata=None, thick_mode=None,
                  show_remark=None, hide_units=None, show_legend=False,
                  pattern_row_height_mm=lithology.PATTERN_ROW_HEIGHT_MM):
    """绘制柱状图，返回 matplotlib Figure。

    默认版式：岩性柱按比例尺绘制，其余各栏行高由文字内容决定（取各栏
    所需的最大行高），两侧用折线连接行界与层界，薄层长文字不再受限。
    to_scale=True 时使用旧版式：所有栏与岩性柱严格按深度对齐。
    scale: 垂直比例尺分母（如 200 表示 1:200）；不传则自动取常用值，
    并尽量使图高贴合页面高度。
    page/landscape: 页面规格（A4/A3/B3/信纸…或"宽x高"厘米）与横向。
    strata: 显示的地层单位类别集合（geochron 地质年代 / chrono 年代地层 /
    litho 岩石地层）；None（默认）时凡有数据的列都显示。
    hide_units: 单独隐藏的地层单位列集合（中文名如"统"、"段"）；
    在 strata 之上再细化到某一列。
    show_legend: 在图底增加表 4 图版尺寸岩性图例块，按首次出现顺序紧凑排列；
                 不挤占主表栏宽，样框和花纹毫米参数不会随项目数缩放。
    pattern_row_height_mm: 内置层状岩性花纹的基础层厚（毫米，1–10）；
                           地层变厚时增加重复层数，不拉伸单层。
    thick_mode: 厚度栏模式——'group'（默认，同组合并显示总厚度）、
    'layer'（逐层显示每种岩性厚度）、'depth'（改显示地层深度，在组交界处
    标注累计深度）。thick_per_layer=True 等价于 'layer'。
    unit_vertical: 地层单位单元格文字竖排（每字一行）。
    show_remark: 备注栏显示开关——None（默认）时有数据才显示，
    False 强制隐藏（即使有数据），True 强制显示。
    widths: 各栏宽度调整 {栏名: 厘米}，如 {"柱状图": 3.5, "备注": 2.0}；
    超出 WIDTH_LIMITS 范围自动夹紧，"岩性描述"为余量不能直接设置。
    fig_width: 直接指定图宽（英寸），兼容旧接口；给定时覆盖页面宽度。

    layers 不能为空且最多 2000 层；thick_mode 仅支持
    group/layer/depth；显式 scale 必须在 1–100000 之间。
    """
    if layers is None:
        raise ValueError("柱状图数据不能为空")
    layers = _normalize_render_layers(layers)
    if thick_mode is None:
        thick_mode = "layer" if thick_per_layer else "group"
    if thick_mode not in ("group", "layer", "depth"):
        raise ValueError("厚度栏模式仅支持 group、layer 或 depth")
    if scale is not None:
        try:
            scale_value = float(scale)
        except (TypeError, ValueError):
            raise ValueError("比例尺分母必须是 1–100000 之间的有限数")
        if (not math.isfinite(scale_value)
                or not 1 <= scale_value <= 100000):
            raise ValueError("比例尺分母必须是 1–100000 之间的有限数")
        scale = int(scale_value) if scale_value.is_integer() else scale_value
    page_w, page_h = resolve_page(page, landscape)
    if fig_width is not None:
        try:
            fig_width_value = float(fig_width)
        except (TypeError, ValueError):
            raise ValueError("图宽必须是大于 0 的有限数")
        if not math.isfinite(fig_width_value) or fig_width_value <= 0:
            raise ValueError("图宽必须是大于 0 的有限数")
        page_w = fig_width_value
    args = (layers, title, scale, page_w, page_h, widths, thick_mode,
            unit_vertical, strata, show_remark, hide_units, show_legend)
    row_height = lithology.resolve_pattern_row_height_mm(
        pattern_row_height_mm)
    with lithology.pattern_row_height_scope(row_height):
        return (_render_to_scale if to_scale else _render_staggered)(*args)


def _unit_text(name, vertical):
    """地层单位单元格文字：横排时名称与代号分行；竖排时每字一行。"""
    if vertical:
        return "\n".join(name.replace(" ", ""))
    return name.replace(" ", "\n")


def _thick_spans(layers, unit_cols):
    """厚度栏合并区段：与最深一级地层单位（组/系/界）的分组一致，
    同组多层岩性只显示合计厚度。返回 [(起, 止(不含), 合计), ...]；
    没有地层单位列时退回每层一段。"""
    if not unit_cols:
        return [(i, i + 1, ly["thick"]) for i, ly in enumerate(layers)]
    spans, prev = [], None
    for i, ly in enumerate(layers):
        key = tuple(ly[k] for k, _, _ in unit_cols)
        if spans and key == prev:
            spans[-1][1] = i + 1
            spans[-1][2] += ly["thick"]
        else:
            spans.append([i, i + 1, ly["thick"]])
        prev = key
    return [tuple(s) for s in spans]


def available_unit_columns(layers):
    """数据中有内容的地层单位列，返回 [(key, 中文名, 类别), ...]（按等级序）。
    供界面列出可单独控制显隐的列。"""
    return [(k, head, cat) for k, head, cat in _UNIT_LEVELS
            if any(ly.get(k) for ly in layers)]


def _unit_layout(layers, strata=None, hide_units=None):
    """确定地层单位各列与备注列的取舍。

    strata: 允许显示的类别集合（geochron/chrono/litho）；None=全部。
    hide_units: 单独隐藏的列（中文名如"统"或 key 如"series"）集合。
    返回 (unit_cols, grouped, has_remark)，unit_cols 项为 (key, 表头, 类别)。
    """
    if strata is None:
        cats = None
    else:
        try:
            cats = {strata} if isinstance(strata, str) else set(strata)
        except TypeError:
            raise ValueError("strata 必须是地层单位类别集合")
        unknown = cats - set(STRATA_CATS)
        if unknown:
            values = "、".join(sorted(map(str, unknown)))
            raise ValueError(
                f"未知地层单位类别“{values}”；可用："
                f"{'、'.join(STRATA_CATS)}"
            )
    hide = set(hide_units) if hide_units else set()
    unit_cols = [(k, head, cat) for k, head, cat in _UNIT_LEVELS
                 if (cats is None or cat in cats)
                 and head not in hide and k not in hide
                 and any(ly.get(k) for ly in layers)]
    grouped = bool(unit_cols)  # 有真实单位列时用两行分组表头
    show_legacy = cats is None or cats == set(STRATA_CATS)
    if (not unit_cols and show_legacy
            and any(ly.get("unit") for ly in layers)):
        unit_cols = [("unit", "地层\n单位", "unit")]
    return unit_cols, grouped, any(ly["remark"] for ly in layers)


def _unit_header_segments(unit_cols):
    """把单位列按连续同类别分段，返回 [(起, 止(不含), 类别中文名), ...]。"""
    segs, i, n = [], 0, len(unit_cols)
    while i < n:
        cat = unit_cols[i][2]
        j = i
        while j < n and unit_cols[j][2] == cat:
            j += 1
        segs.append((i, j, _CAT_NAMES.get(cat, "地层单位")))
        i = j
    return segs


def _draw_grouped_header(ax, unit_cols, xs, top, bfs):
    """绘制两行分组表头：上行按类别跨栏、下行各单位名。top 为表头顶 y(负)。
    返回需整高绘制的内部竖线 xs 索引集合（类别分段边界）。"""
    mid = top / 2
    seg_bounds = set()
    for i0, i1, label in _unit_header_segments(unit_cols):
        ax.text((xs[i0] + xs[i1]) / 2, top * 0.72, label, ha="center",
                va="center", fontsize=bfs + 1, fontweight="bold")
        if i0 > 0:
            seg_bounds.add(i0)
    ax.plot([xs[0], xs[len(unit_cols)]], [mid, mid], color=_FRAME, lw=0.7)
    for idx, (k, head, cat) in enumerate(unit_cols):
        ax.text((xs[idx] + xs[idx + 1]) / 2, top * 0.25,
                head.replace("\n", ""), ha="center", va="center",
                fontsize=bfs + 1, fontweight="bold")
    return seg_bounds


def _render_to_scale(layers, title, scale, page_w, page_h, widths=None,
                     thick_mode="group", unit_vertical=False, strata=None,
                     show_remark=None, hide_units=None, show_legend=False):
    """旧版式：所有栏与岩性柱严格按深度比例对齐。"""
    fonts.setup()
    bfs = fonts.BASE_FS
    fig_width = page_w
    depth_mode = thick_mode == "depth"

    total = sum(ly["thick"] for ly in layers)

    # 版面尺寸（英寸）
    m_left, m_right, m_top, m_bottom = 0.45, 0.45, 0.85, 0.35
    avail_in = fig_width - m_left - m_right

    # 版面栏目：地层单位各级（有数据的列才显示；兼容旧"地层单位"单列）
    unit_cols, grouped, has_remark = _unit_layout(layers, strata, hide_units)
    if show_remark is not None:  # 备注栏显示开关
        has_remark = bool(show_remark)
    widths, legend_item_in, legend_h = _legend_block_layout(
        widths, layers, show_legend, bfs, avail_in)
    cols = _build_cols(unit_cols, has_remark, widths, staggered=False,
                       table_w_cm=avail_in * CM,
                       thick_head="深度\n(m)" if depth_mode else "厚度\n(m)",
                       has_legend=False)
    header_in = 0.60 if grouped else 0.52
    available_body = page_h - m_top - m_bottom - header_in - legend_h
    if show_legend and available_body < 3.0 - 1e-9:
        raise ValueError(
            "图例项目过多，所选页面无法在保留 15 mm × 10 mm "
            "样框的同时容纳图身；请改用更大页面、横向纸张或减少岩性种类"
        )
    page_body = max(3.0, available_body)

    # 对齐版式的可用高度同时受页面和文字避让需求约束。显式比例尺也使用
    # 同一目标，避免一经标记就把整根柱压得很短并导致正文无处排布。
    def col_text_in(key, texts):
        w = next(w for k, _, w in cols if k == key) * avail_in - 0.15
        return (sum(_est_lines(t, w, bfs) * (bfs / 72 * 1.55)
                    for t in texts if t)
                + 0.045 * (len(layers) - 1))

    need = col_text_in("desc", [ly["desc"] or ly["lith"] for ly in layers])
    if has_remark:
        need = max(need, col_text_in("remark", [ly["remark"] for ly in layers]))
    target_body = max(page_body, need * 1.06)

    nice = (10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500,
            600, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000)
    if not scale:
        # 自动：图身高度取"页面可用高度"与"文字放得下"二者较大者，
        # 比例尺取整到常用值
        body = target_body
        raw = 39.37 * total / body
        if body > page_body:  # 文字驱动：分母只能取小（图幅放大），确保放得下
            scale = next((s for s in reversed(nice) if s <= raw),
                         max(1, round(raw)))
        else:
            scale = next((s for s in nice if s >= raw), round(raw))
    inch_per_m = 39.37 / scale
    # 显示坐标：只有自然柱高超过实际可用高度时才按需压缩候选厚层；
    # 纵坐标用“米当量”，厚度/深度标注仍取真实值。
    disp_in, comp = _display_heights(layers, inch_per_m, target_body)
    disp_m = [d / inch_per_m for d in disp_in]
    disp_bounds = [0.0]
    for dm in disp_m:
        disp_bounds.append(disp_bounds[-1] + dm)
    real_bounds = [0.0]
    for ly in layers:
        real_bounds.append(real_bounds[-1] + ly["thick"])
    disp_total = disp_bounds[-1]
    body_in = sum(disp_in)
    fig_h = m_top + header_in + body_in + legend_h + m_bottom

    fig = Figure(figsize=(fig_width, fig_h), dpi=100)
    fig.patch.set_facecolor("white")

    ax = fig.add_axes([m_left / fig_width, (m_bottom + legend_h) / fig_h,
                       1 - (m_left + m_right) / fig_width,
                       (header_in + body_in) / fig_h])
    ax.axis("off")
    header_m = header_in / inch_per_m  # 表头高度折算成"米"
    ax.set_xlim(0, 1)
    ax.set_ylim(disp_total, -header_m)  # 深度向下（显示坐标）

    # 标题与比例尺
    fig.text(0.5, 1 - 0.32 / fig_h, title, ha="center", va="center",
             fontsize=bfs + 6, fontweight="bold")
    scale_note = "（折断层压缩显示）" if any(comp) else ""
    fig.text(0.5, 1 - 0.62 / fig_h,
             f"垂直比例尺  1:{scale}{scale_note}",
             ha="center", va="center", fontsize=bfs + 1, color="#444444")

    # 各栏横向边界
    xs = [0.0]
    for _, _, w in cols:
        xs.append(xs[-1] + w)
    edge = {k: (x0, x1) for (k, _, _), x0, x1 in zip(cols, xs[:-1], xs[1:])}
    table_w_in = avail_in

    # 表头：单位列上方按类别分组跨栏表头
    n_unit = len(unit_cols)
    seg_bounds = set()
    if grouped:
        seg_bounds = _draw_grouped_header(ax, unit_cols, xs, -header_m, bfs)
        rest = cols[n_unit:]
        rest_xs = xs[n_unit:]
    else:
        rest = cols
        rest_xs = xs
    for (k, head, _), x0, x1 in zip(rest, rest_xs[:-1], rest_xs[1:]):
        ax.text((x0 + x1) / 2, -header_m / 2, head, ha="center",
                va="center", fontsize=bfs + 1, fontweight="bold",
                linespacing=1.4)
    ax.plot([0, 1], [0, 0], color=_FRAME, lw=1.0)

    # 逐层绘制：岩性柱与层间线（显示坐标；压缩层中部画折断符号）
    lx0, lx1 = edge["log"]
    mids = []
    for i, ly in enumerate(layers):
        d0, d1 = disp_bounds[i], disp_bounds[i + 1]
        mids.append((d0 + d1) / 2)

        lithology.paint(ax, [(lx0, d0), (lx1, d0), (lx1, d1), (lx0, d1)],
                        ly["lith"])
        if comp[i]:
            lithology.draw_break(ax, lx0, lx1, (d0 + d1) / 2)

        # 岩性柱内层间线；接触线下的白色隔离描边避免与花纹混线。
        if d1 < disp_total:
            unconf, _angular = lithology.is_unconformity(ly["contact"])
            lithology.draw_contact(
                ax,
                [(lx0, d1), (lx1, d1)],
                ly["contact"],
                color=_FRAME,
                lw=0.9 if unconf else 0.7,
                clearance_mm=lithology.contact_clearance_mm(
                    disp_in[i:i + 2]
                ),
            )

    # 厚度/深度栏（文字为真实值，位置用显示坐标）
    spans = _thick_spans(layers, [] if thick_mode == "layer" else unit_cols)
    tx0, tx1 = edge["thick"]
    tx = (tx0 + tx1) / 2
    if depth_mode:  # 深度模式：组交界处标注累计深度（真实深度）
        for i1 in [0] + [s[1] for s in spans]:
            ax.text(tx, disp_bounds[i1],
                    f"{real_bounds[i1]:.2f}".rstrip("0").rstrip("."),
                    ha="center", va="center", fontsize=max(6.0, bfs - 0.5),
                    bbox=dict(facecolor="white", edgecolor="none", pad=0.4))
        for _, i1, _ in spans:
            if disp_bounds[i1] < disp_total:
                ax.plot([tx0, tx1], [disp_bounds[i1]] * 2, color=_FRAME, lw=0.7)
    else:  # 厚度模式：格中标注真实厚度（薄组缩小字号）
        for i0, i1, tot in spans:
            p0, p1 = disp_bounds[i0], disp_bounds[i1]
            cell_in = (p1 - p0) * inch_per_m
            fs_num = bfs if cell_in >= 0.16 else max(5.5, bfs * cell_in / 0.16)
            ax.text(tx, (p0 + p1) / 2, f"{tot:.2f}".rstrip("0").rstrip("."),
                    ha="center", va="center", fontsize=fs_num)
            if p1 < disp_total:
                ax.plot([tx0, tx1], [p1, p1], color=_FRAME, lw=0.7)

    truncated = _flow_text_column(
        ax, [ly["desc"] or ly["lith"] for ly in layers], mids, disp_m,
        *edge["desc"], disp_total, inch_per_m, table_w_in, base_fs=bfs)
    if has_remark:
        truncated |= _flow_text_column(
            ax, [ly["remark"] for ly in layers], mids, disp_m,
            *edge["remark"], disp_total, inch_per_m, table_w_in, base_fs=bfs)

    # 地层单位各列：相邻同名合并（逐级分组，位置用显示坐标）
    for i, (k, _, _) in enumerate(unit_cols):
        ux0, ux1 = edge[k]
        d, groups = 0.0, []
        for li, ly in enumerate(layers):
            key = tuple(ly.get(kk, "") for kk, _, _ in unit_cols[:i + 1])
            if groups and groups[-1][0] == key:
                groups[-1][2] += disp_m[li]
            else:
                groups.append([key, d, disp_m[li], ly.get(k, "")])
            d += disp_m[li]
        for key, g0, g_th, name in groups:
            g1 = g0 + g_th
            if g1 < disp_total:
                ax.plot([ux0, ux1], [g1, g1], color=_FRAME, lw=0.7)
            if name:
                ax.text((ux0 + ux1) / 2, (g0 + g1) / 2,
                        _unit_text(name, unit_vertical), ha="center",
                        va="center", fontsize=bfs + 0.5,
                        linespacing=1.15 if unit_vertical else 1.5)

    # 竖向表格线与外框（段内分隔线从表头下半段起，类别段边界整高）
    for i, x in enumerate(xs[1:-1], 1):
        if grouped and i < n_unit:
            top = -header_m if i in seg_bounds else -header_m / 2
        else:
            top = -header_m
        ax.plot([x, x], [top, disp_total], color=_FRAME, lw=0.7,
                zorder=5)
    for x in (0, 1):
        ax.plot([x, x], [-header_m, disp_total], color=_FRAME, lw=1.4,
                zorder=5)
    for y in (-header_m, disp_total):
        ax.plot([0, 1], [y, y], color=_FRAME, lw=1.4, zorder=5)

    if show_legend:
        lithology.draw_legend(
            fig, [ly["lith"] for ly in layers],
            [m_left / fig_width, m_bottom / fig_h,
             avail_in / fig_width, legend_h / fig_h],
            fontsize=bfs, item_w_in=legend_item_in)

    notes = []
    if any(lithology.is_unconformity(ly["contact"])[0] for ly in layers):
        notes.append("波状线示不整合面，附短斜线者为角度不整合")
    if truncated:
        notes.append("图幅过小，部分文字已截断（建议减小比例尺分母放大图幅）")
    if notes:
        fig.text(m_left / fig_width, 0.14 / fig_h, "注：" + "；".join(notes) + "。",
                 ha="left", va="bottom", fontsize=max(6.0, bfs - 0.5),
                 color="#555555")

    return fig


def _render_staggered(layers, title, scale, page_w, page_h, widths=None,
                      thick_mode="group", unit_vertical=False, strata=None,
                      show_remark=None, hide_units=None, show_legend=False):
    """默认版式：岩性柱按比例尺绘制，其余各栏按文字内容定行高，
    两侧以折线连接表格行界与岩性柱层界。纵坐标单位为英寸。"""
    fonts.setup()
    bfs = fonts.BASE_FS
    fig_width = page_w
    depth_mode = thick_mode == "depth"

    total_m = sum(ly["thick"] for ly in layers)
    n = len(layers)

    m_left, m_right, m_top, m_bottom = 0.45, 0.45, 0.85, 0.35
    avail_in = fig_width - m_left - m_right

    unit_cols, grouped, has_remark = _unit_layout(layers, strata, hide_units)
    if show_remark is not None:  # 备注栏显示开关
        has_remark = bool(show_remark)
    widths, legend_item_in, legend_h = _legend_block_layout(
        widths, layers, show_legend, bfs, avail_in)
    cols = _build_cols(unit_cols, has_remark, widths, staggered=True,
                       table_w_cm=avail_in * CM,
                       thick_head="深度\n(m)" if depth_mode else "厚度\n(m)",
                       has_legend=False)
    header_in = 0.60 if grouped else 0.52
    available_body = page_h - m_top - m_bottom - header_in - legend_h
    if show_legend and available_body < 3.0 - 1e-9:
        raise ValueError(
            "图例项目过多，所选页面无法在保留 15 mm × 10 mm "
            "样框的同时容纳图身；请改用更大页面、横向纸张或减少岩性种类"
        )
    page_body = max(3.0, available_body)

    # 行高：由描述/备注文字行数决定（取两者较大者）
    fs = bfs
    line_in = fs / 72 * 1.55

    def wrap_col(key, texts):
        usable = next(w for k, _, w in cols if k == key) * avail_in - 0.18
        return [_wrap_text(t, usable, fs) if t else [] for t in texts]

    desc_blk = wrap_col("desc", [ly["desc"] or ly["lith"] for ly in layers])
    rem_blk = (wrap_col("remark", [ly["remark"] for ly in layers])
               if has_remark else [[] for _ in layers])
    row_h = [max(0.30, len(d) * line_in + 0.14, len(r) * line_in + 0.14)
             for d, r in zip(desc_blk, rem_blk)]
    h_table = sum(row_h)

    nice = (10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500,
            600, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000)
    if not scale:
        # 自动：在"文字放得下"（分母上限）内尽量贴合页面可用高度
        raw_table = 39.37 * total_m / h_table
        raw_page = 39.37 * total_m / page_body
        scale = next((s for s in nice if raw_page <= s <= raw_table),
                     None)
        if scale is None:  # 页面装不下文字：以文字为准（图会超出页高）
            scale = next((s for s in reversed(nice) if s <= raw_table),
                         None) or max(1, int(raw_table))
    inch_per_m = 39.37 / scale
    # 页面能容纳自然柱时不省略；确实超出页面/文字表格所需高度时，只从
    # 标记候选层扣除恰好需要的高度。未压缩层始终严格使用 inch_per_m。
    target_body = max(page_body, h_table)
    disp_in, comp = _display_heights(
        layers, inch_per_m, target_body)
    h_log = sum(disp_in)
    if h_log > h_table:  # 拉伸行距，使表格底与岩性柱底对齐
        k = h_log / h_table
        row_h = [h * k for h in row_h]
        h_table = h_log
    height = max(h_table, h_log)

    r_bounds = [0.0]
    for h in row_h:
        r_bounds.append(r_bounds[-1] + h)
    l_bounds = [0.0]
    for d in disp_in:
        l_bounds.append(l_bounds[-1] + d)

    fig_h = m_top + header_in + height + legend_h + m_bottom
    fig = Figure(figsize=(fig_width, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([m_left / fig_width, (m_bottom + legend_h) / fig_h,
                       1 - (m_left + m_right) / fig_width,
                       (header_in + height) / fig_h])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(height, -header_in)  # 纵坐标：英寸，向下

    fig.text(0.5, 1 - 0.32 / fig_h, title, ha="center", va="center",
             fontsize=bfs + 6, fontweight="bold")
    scale_note = "（折断层压缩显示）" if any(comp) else ""
    fig.text(0.5, 1 - 0.62 / fig_h,
             f"柱状图比例尺  1:{scale}{scale_note}",
             ha="center", va="center", fontsize=bfs + 1, color="#444444")

    xs = [0.0]
    for _, _, w in cols:
        xs.append(xs[-1] + w)
    edge = {k: (x0, x1) for (k, _, _), x0, x1 in zip(cols, xs[:-1], xs[1:])}
    pad = 0.09 / avail_in

    # 表头："柱状图"跨左右连接带；单位列上方按类别分组跨栏表头
    n_unit = len(unit_cols)
    seg_bounds = set()
    if grouped:
        seg_bounds = _draw_grouped_header(ax, unit_cols, xs, -header_in, bfs)
        rest, rest_xs = cols[n_unit:], xs[n_unit:]
    else:
        rest, rest_xs = cols, xs
    for (k, head, _), x0, x1 in zip(rest, rest_xs[:-1], rest_xs[1:]):
        if k in ("gutl", "gutr"):
            continue
        if k == "log":
            x0, x1 = edge["gutl"][0], edge["gutr"][1]
        ax.text((x0 + x1) / 2, -header_in / 2, head, ha="center",
                va="center", fontsize=bfs + 1, fontweight="bold",
                linespacing=1.4)
    ax.plot([0, 1], [0, 0], color=_FRAME, lw=1.0)

    # 表格各行：描述、备注（行坐标；逐层）
    for i, ly in enumerate(layers):
        r0, r1 = r_bounds[i], r_bounds[i + 1]
        rm = (r0 + r1) / 2
        for blk, key in ((desc_blk[i], "desc"), (rem_blk[i], "remark")):
            if not blk:
                continue
            y = rm - (len(blk) - 1) / 2 * line_in
            for ln in blk:
                ax.text(edge[key][0] + pad, y, ln, ha="left", va="center",
                        fontsize=fs)
                y += line_in
        if i < n - 1:
            ax.plot([edge["desc"][0], 1.0], [r1, r1],
                    color=_FRAME, lw=0.7)

    # 厚度/深度栏
    spans = _thick_spans(layers, [] if thick_mode == "layer" else unit_cols)
    tx0, tx1 = edge["thick"]
    tx = (tx0 + tx1) / 2
    if depth_mode:  # 组交界处标注累计深度（行坐标定位、米深度取值）
        bounds_m = [0.0]
        for ly in layers:
            bounds_m.append(bounds_m[-1] + ly["thick"])
        marks = [(0.0, 0.0)] + [(r_bounds[i1], bounds_m[i1])
                                for _, i1, _ in spans]
        for ry, dm in marks:
            ax.text(tx, ry, f"{dm:.2f}".rstrip("0").rstrip("."),
                    ha="center", va="center", fontsize=max(6.0, bfs - 0.5),
                    bbox=dict(facecolor="white", edgecolor="none", pad=0.4))
        for _, i1, _ in spans:
            if r_bounds[i1] < h_table - 1e-6:
                ax.plot([tx0, tx1], [r_bounds[i1]] * 2, color=_FRAME, lw=0.7)
    else:
        for i0, i1, tot in spans:
            r0, r1 = r_bounds[i0], r_bounds[i1]
            ax.text(tx, (r0 + r1) / 2, f"{tot:.2f}".rstrip("0").rstrip("."),
                    ha="center", va="center", fontsize=fs)
            if r1 < h_table - 1e-6:
                ax.plot([tx0, tx1], [r1, r1], color=_FRAME, lw=0.7)

    # 岩性柱（比例尺坐标）与层底线；压缩层中部画折断符号
    lx0, lx1 = edge["log"]
    for i, ly in enumerate(layers):
        d0, d1 = l_bounds[i], l_bounds[i + 1]
        lithology.paint(ax, [(lx0, d0), (lx1, d0), (lx1, d1), (lx0, d1)],
                        ly["lith"])
        if comp[i]:
            lithology.draw_break(ax, lx0, lx1, (d0 + d1) / 2)
        unconf, _angular = lithology.is_unconformity(ly["contact"])
        if unconf or i < n - 1 or d1 < height - 1e-6:
            lithology.draw_contact(
                ax,
                [(lx0, d1), (lx1, d1)],
                ly["contact"],
                color=_FRAME,
                lw=0.9 if unconf else 0.7,
                clearance_mm=lithology.contact_clearance_mm(
                    disp_in[i:i + 2]
                ),
            )
    for x in (lx0, lx1):  # 岩性柱左右边线
        ax.plot([x, x], [0, l_bounds[-1]], color=_FRAME, lw=0.8,
                zorder=5)

    # 两侧连接折线：表格行界 <-> 岩性柱层界
    for j in range(1, n + 1):
        rj, lj = r_bounds[j], l_bounds[j]
        ax.plot([edge["gutl"][0], lx0], [rj, lj], color=_FRAME, lw=0.7)
        ax.plot([lx1, edge["gutr"][1]], [lj, rj], color=_FRAME, lw=0.7)

    # 地层单位各列：相邻同名合并（行坐标）
    for i, (k, _, _) in enumerate(unit_cols):
        ux0, ux1 = edge[k]
        groups, d = [], 0.0
        for li, ly in enumerate(layers):
            key = tuple(ly.get(kk, "") for kk, _, _ in unit_cols[:i + 1])
            if groups and groups[-1][0] == key:
                groups[-1][2] += row_h[li]
            else:
                groups.append([key, d, row_h[li], ly.get(k, "")])
            d += row_h[li]
        for key, g0, g_th, name in groups:
            g1 = g0 + g_th
            if g1 < h_table - 1e-6:
                ax.plot([ux0, ux1], [g1, g1], color=_FRAME, lw=0.7)
            if name:
                ax.text((ux0 + ux1) / 2, (g0 + g1) / 2,
                        _unit_text(name, unit_vertical), ha="center",
                        va="center", fontsize=bfs + 0.5,
                        linespacing=1.15 if unit_vertical else 1.5)

    # 竖向表格线与外框（跳过岩性柱两侧内边；类别段边界整高、段内从中线起）
    for i, x in enumerate(xs[1:-1], 1):
        if cols[i - 1][0] == "log" or cols[i][0] == "log":
            continue
        if grouped and i < n_unit:
            top = -header_in if i in seg_bounds else -header_in / 2
        else:
            top = -header_in
        ax.plot([x, x], [top, height], color=_FRAME, lw=0.7, zorder=5)
    for x in (0, 1):
        ax.plot([x, x], [-header_in, height], color=_FRAME, lw=1.4,
                zorder=5)
    for y in (-header_in, height):
        ax.plot([0, 1], [y, y], color=_FRAME, lw=1.4, zorder=5)

    if show_legend:
        lithology.draw_legend(
            fig, [ly["lith"] for ly in layers],
            [m_left / fig_width, m_bottom / fig_h,
             avail_in / fig_width, legend_h / fig_h],
            fontsize=bfs, item_w_in=legend_item_in)

    if any(lithology.is_unconformity(ly["contact"])[0] for ly in layers):
        fig.text(m_left / fig_width, 0.14 / fig_h,
                 "注：波状线示不整合面，附短斜线者为角度不整合。",
                 ha="left", va="bottom", fontsize=max(6.0, bfs - 0.5),
                 color="#555555")

    return fig
