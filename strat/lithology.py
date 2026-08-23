"""岩性图例库：颜色与花纹。

花纹参照国标 GB/T 958 常用岩性图案的简化画法，用 matplotlib 基本图元
（线段集合、点标记）绘制，并裁剪到目标多边形内，因此柱状图单元格和
剖面图中的任意地层多边形都能填充。

花纹间距以英寸为单位指定，绘制时根据坐标轴的实际比例换算成数据坐标，
保证不同比例尺下花纹疏密一致。
"""

import copy
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

import numpy as np
from matplotlib.patches import Polygon
from matplotlib.collections import LineCollection

# 花纹线的墨色（深灰褐，区别于纯黑的表格线）
INK = "#4a4438"

# 花纹基准间距（英寸）：spacing 倍率为 1 时的疏密，越小越密
BASE_SPACING = 0.09

# 层理、砖纹、波纹和斜线带在主图中的默认基础层高。地层加厚
# 只会增加重复层数，不会拉伸单层。图例小样另用独立的代表性
# 绘法，不受这个主图参数影响。
PATTERN_ROW_HEIGHT_MM = 2.5
PATTERN_ROW_HEIGHT_IN = PATTERN_ROW_HEIGHT_MM / 25.4
PATTERN_ROW_HEIGHT_LIMITS_MM = (1.0, 10.0)

# 单次绘图的层高不能写回 PATTERNS 或模块常量：Web 服务可能同时处理
# 多个不同参数的导出。ContextVar 让深层 paint()/图例代码自动读取本次
# 渲染值，并在异常退出时安全恢复。
_PATTERN_ROW_HEIGHT_CONTEXT = ContextVar(
    "pattern_row_height_mm", default=PATTERN_ROW_HEIGHT_MM)

# 图例小样是符号释义，不是从主图任意裁下的一块。GB/T 958—2015 表 4
# 的常规沉积岩样框以 3 个代表层带展示；主图的可调物理层厚不应让同一
# 张图的图例忽而出现 3 层、4 层乃至十余层。该上下文只在图例小样中
# 启用，页岩等标准明确采用密线的花纹会保留原始密度。
_LEGEND_SWATCH_CONTEXT = ContextVar("legend_swatch", default=None)

# Table 4's ``RPBP`` cells define an individual symbol, not a cropped sample
# of the infinitely repeated rock pattern.  This flag is deliberately separate
# from the normal legend context so column/section lithology swatches continue
# to use their standard three representative rows.
_LEGEND_SINGLE_MOTIF_CONTEXT = ContextVar(
    "legend_single_motif", default=False)

# Symbol geometry must clear the visible swatch frame, not merely remain
# inside its mathematical clipping rectangle.  A quarter millimetre keeps
# 0.1-mm standard strokes and the 0.7-pt frame from visually colliding.
LEGEND_SYMBOL_CLEARANCE_MM = 0.25
_LEGEND_SYMBOL_CLEARANCE_IN = LEGEND_SYMBOL_CLEARANCE_MM / 25.4


def resolve_pattern_row_height_mm(value=PATTERN_ROW_HEIGHT_MM):
    """校验并返回花纹基础层高（毫米）。"""
    low, high = PATTERN_ROW_HEIGHT_LIMITS_MM
    if isinstance(value, bool):
        raise ValueError(f"花纹层厚应为 {low:g}–{high:g} mm 之间的有限数")
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"花纹层厚应为 {low:g}–{high:g} mm 之间的有限数")
    if not math.isfinite(resolved) or not low <= resolved <= high:
        raise ValueError(f"花纹层厚应为 {low:g}–{high:g} mm 之间的有限数")
    return resolved


def current_pattern_row_height_mm():
    """返回当前绘图上下文的花纹基础层高（毫米）。"""
    return _PATTERN_ROW_HEIGHT_CONTEXT.get()


@contextmanager
def pattern_row_height_scope(value=PATTERN_ROW_HEIGHT_MM):
    """在当前单次绘图中使用指定花纹层高，并保证退出后恢复。"""
    token = _PATTERN_ROW_HEIGHT_CONTEXT.set(
        resolve_pattern_row_height_mm(value))
    try:
        yield _PATTERN_ROW_HEIGHT_CONTEXT.get()
    finally:
        _PATTERN_ROW_HEIGHT_CONTEXT.reset(token)


@contextmanager
def legend_swatch_scope(height_mm=10.0, rows=3):
    """在图例样框内绘制规范化的代表性花纹。

    ``height_mm`` 是样框物理高度，``rows`` 是常规层状/重复花纹的代表
    层数。显式 ``rows`` 自定义花纹和页岩式密线不会被强行改写。
    """
    height = float(height_mm)
    count = int(rows)
    if (not math.isfinite(height) or height <= 0 or count <= 0
            or count != rows):
        raise ValueError("图例样框高度和代表层数必须为正数")
    token = _LEGEND_SWATCH_CONTEXT.set((height, count))
    try:
        yield height, count
    finally:
        _LEGEND_SWATCH_CONTEXT.reset(token)


@contextmanager
def legend_single_motif_scope():
    """Render one complete, physically centred basic-symbol motif."""
    token = _LEGEND_SINGLE_MOTIF_CONTEXT.set(True)
    try:
        yield
    finally:
        _LEGEND_SINGLE_MOTIF_CONTEXT.reset(token)

# 接触面与岩性花纹之间的最大单侧净空。接触线保持原有标准线宽；薄层会
# 按图上高度自动收窄白色底衬，避免净空吞没花纹。
CONTACT_CLEARANCE_MM = 0.35

# 标准岩性名 -> (底色, 花纹)。底色遵循地质制图惯例：砂岩黄、灰岩蓝、
# 泥岩绿灰、煤黑、花岗岩肉红等；同时每种岩性花纹唯一，色觉障碍或
# 黑白打印时仍可分辨。
LITHOLOGY = {
    "砾岩":   ("#f2cd93", "gravel"),
    "砂岩":   ("#f7e8a9", "sst_gb"),
    "粉砂岩": ("#ecf0c3", "siltstone_gb"),
    "泥岩":   ("#d9e4c9", "mudstone_gb"),
    "页岩":   ("#d9dee3", "shale_gb"),
    "石灰岩": ("#c4d9ea", "ls_gb"),
    "白云岩": ("#d9cfe8", "dolomite_gb"),
    "泥灰岩": ("#cfe3dc", "marl_gb"),
    "煤":     ("#3a3a3a", None),
    "花岗岩": ("#f3c9c4", "granite"),
    "闪长岩": ("#e6b7ae", "diorite"),
    "辉长岩": ("#b9a7b8", "gabbro"),
    "玄武岩": ("#bfd8c2", "basalt"),
    "安山岩": ("#cbe0cf", "andesite"),
    "流纹岩": ("#f0d6c0", "rhyolite"),
    "凝灰岩": ("#e3dcc0", "tuff"),
    # 变质岩
    "板岩":   ("#c9c6cf", "slate"),
    "千枚岩": ("#cfc9d6", "phyllite"),
    "片岩":   ("#c3cdc0", "schist"),
    "片麻岩": ("#e0d2c0", "gneiss"),
    "石英岩": ("#e7d9cf", "quartzite"),
    "大理岩": ("#d7e6ea", "marble"),
    # 化学岩、生物岩及其他沉积岩
    "石膏":   ("#eef0f2", "gypsum"),
    "岩盐":   ("#eaf1f0", "halite"),
    "硅质岩": ("#dfe6dc", "chert"),
    "生物灰岩": ("#c4d9ea", "bio_ls"),
    "鲕粒灰岩": ("#c9dcea", "oolitic_ls"),
    "白垩":   ("#eef1ec", "chalk"),
    "角砾岩": ("#eecfa0", "breccia"),
    "砂砾岩": ("#f0d59a", "sandy_congl"),
    # 第四系松散土
    "粘土":   ("#e8d5b5", "clay_gb"),
    "粉土":   ("#efe3c8", "silty_soil"),
    "砂土":   ("#f7efc0", "dots"),
    "淤泥":   ("#c2ccc2", "mud_gb"),
    "填土":   ("#e0dad2", "fill_soil"),
    "黄土":   ("#efdfb0", "loess"),
    "碎石土": ("#e9d3a6", "debris_soil"),
    "红土":   ("#e8b89a", "redsoil"),
    # 第四纪堆积物成因类型（覆盖物）
    "冲积":   ("#efe6c0", "q_alluvial"),
    "洪积":   ("#eddfb4", "q_pluvial"),
    "冲洪积": ("#eee3ba", "q_alpl"),
    "坡积":   ("#e4d7b4", "q_slope"),
    "残积":   ("#e0d0ae", "q_eluvial"),
    "残坡积": ("#e2d4b1", "q_elslope"),
    "崩积":   ("#d9cdb2", "q_colluvial"),
    "风积":   ("#f5eec5", "q_aeolian"),
    "湖积":   ("#d7e2d8", "q_lacustrine"),
    "海积":   ("#cfdfe4", "q_marine"),
    "沼泽堆积": ("#c8d6c4", "swamp_dep"),
    "冰水堆积": ("#dcdcd2", "glaciofluvial"),
    "化学堆积": ("#e6e9e2", "chem_dep"),
    "粗砂":   ("#f7efc0", "sand_c"),
    "中砂":   ("#f7efc0", "sand_m"),
    "细砂":   ("#f7efc0", "sand_f"),
    "粉砂":   ("#f3ecc6", "silt_loose"),
    "泥炭土": ("#b9b3a0", "peat"),
    "贝壳层": ("#e7e2cf", "shellbed"),
    "冰碛":   ("#d8d3c8", "till"),
    "漂砾":   ("#ecd7ac", "boulder"),
    # 砾岩/砂岩粒级与成分变种
    "巨砾岩": ("#f0cd96", "congl_huge"),
    "中砾岩": ("#f2cd93", "congl_m"),
    "细砾岩": ("#f4d29c", "congl_f"),
    "粗砂岩": ("#f7e8a9", "sst_c"),
    "中砂岩": ("#f7e8a9", "sst_m"),
    "细砂岩": ("#f8ecb8", "sst_f"),
    "石英砂岩": ("#f2e6c8", "sst_qz"),
    "长石砂岩": ("#f4e3b8", "sst_fsp"),
    "油页岩": ("#cfd4cf", "oilshale"),
    # 灰岩变种
    "结晶灰岩": ("#c4d9ea", "ls_cryst"),
    "礁灰岩":   ("#c4d9ea", "ls_reef"),
    "竹叶状灰岩": ("#c9dcea", "ls_bamboo"),
    "燧石条带灰岩": ("#bfd4e4", "ls_chert"),
    "条带状泥灰岩": ("#cfe3dc", "marl_band"),
    # 岩浆岩（超基性→酸性→碱性；浅成、脉岩）
    "橄榄岩": ("#9fbf9a", "peridotite"),
    "纯橄榄岩": ("#8fb78a", "dunite"),
    "金伯利岩": ("#a8b8a2", "kimberlite"),
    "辉石岩": ("#b9c4ad", "pyroxenite"),
    "角闪石岩": ("#b3c4b8", "hornblendite"),
    "辉绿岩": ("#a9c4b6", "diabase"),
    "玢岩":   ("#d4c2b0", "porphyrite"),
    "斑岩":   ("#e3c7bb", "porphyry"),
    "花岗闪长岩": ("#efc3b2", "granodiorite"),
    "正长岩": ("#f0cabc", "syenite"),
    "二长岩": ("#eccbb5", "monzonite"),
    "斜长岩": ("#e3d4c8", "anorthosite"),
    "伟晶岩": ("#f5cfc7", "pegmatite"),
    "细晶岩": ("#f2d5cc", "aplite"),
    "煌斑岩": ("#c9b8a6", "lamprophyre"),
    "苦橄岩": ("#8fae8c", "picrite"),
    "粗面岩": ("#e7cdb4", "trachyte"),
    "响岩":   ("#d8c8b8", "phonolite"),
    "英安岩": ("#dfe0c2", "dacite"),
    "珍珠岩": ("#dfe3e6", "perlite"),
    "黑曜岩": ("#9a9a9a", "obsidian"),
    "浮岩":   ("#e6e6de", "pumice"),
    "碳酸岩": ("#d9e6df", "carbonatite"),
    # 变质岩补充
    "角岩":   ("#cbb9a8", "hornfels"),
    "矽卡岩": ("#c9b6c4", "skarn"),
    "糜棱岩": ("#c8c2b6", "mylonite"),
    "碎裂岩": ("#d4cec2", "cataclasite"),
    "构造角砾岩": ("#d0c4b4", "tect_breccia"),
    "混合岩": ("#dcc7b4", "migmatite"),
    "混合花岗岩": ("#e9c9ba", "mig_granite"),
    "麻粒岩": ("#cfc0b0", "granulite"),
    "变粒岩": ("#d8cabc", "leptynite"),
    "浅粒岩": ("#e2d6c6", "leuco"),
    "榴辉岩": ("#b78f96", "eclogite"),
    "蛇纹岩": ("#9dbf9d", "serpentinite"),
}

# 未收录岩性的缺省样式
DEFAULT = ("#eceae2", "diag")

# 名称解析顺序：先匹配更具体的关键字（如"粉砂岩"要先于"砂岩"）。
_ALIAS_ORDER = (
    # 守卫：凝灰岩类名先于"灰岩"匹配，防止"熔结晶屑凝灰岩"误判为石灰岩
    ("凝灰岩", "凝灰岩"),
    ("生物碎屑灰岩", "生物灰岩"),
    ("生物灰岩", "生物灰岩"),
    ("鲕粒灰岩", "鲕粒灰岩"),
    ("鲕状灰岩", "鲕粒灰岩"),
    ("竹叶状灰岩", "竹叶状灰岩"),
    ("结晶灰岩", "结晶灰岩"),
    ("亮晶灰岩", "结晶灰岩"),
    ("礁灰岩", "礁灰岩"),
    ("燧石条带灰岩", "燧石条带灰岩"),
    ("燧石结核灰岩", "燧石条带灰岩"),
    ("泥晶灰岩", "石灰岩"),
    ("微晶灰岩", "石灰岩"),
    ("条带状泥灰岩", "条带状泥灰岩"),
    ("泥灰岩", "泥灰岩"),
    ("白云岩", "白云岩"),
    ("石灰岩", "石灰岩"),
    ("灰岩",   "石灰岩"),
    ("砂砾岩", "砂砾岩"),
    ("构造角砾岩", "构造角砾岩"),
    ("角砾岩", "角砾岩"),
    ("粉砂岩", "粉砂岩"),
    ("长石石英砂岩", "石英砂岩"),
    ("石英砂岩", "石英砂岩"),
    ("长石砂岩", "长石砂岩"),
    ("粗砂岩", "粗砂岩"),
    ("中砂岩", "中砂岩"),
    ("细砂岩", "细砂岩"),
    ("砂岩",   "砂岩"),
    ("巨砾岩", "巨砾岩"),
    ("中砾岩", "中砾岩"),
    ("细砾岩", "细砾岩"),
    ("砾岩",   "砾岩"),
    ("卵石",   "砾岩"),
    ("漂砾",   "漂砾"),
    ("砾石",   "砾岩"),
    ("油页岩", "油页岩"),
    ("页岩",   "页岩"),
    ("泥岩",   "泥岩"),
    ("泥炭",   "泥炭土"),
    ("煤",     "煤"),
    # 火成岩：超基性/基性 → 中酸性 → 喷出岩、浅成脉岩
    ("纯橄",   "纯橄榄岩"),
    ("金伯利", "金伯利岩"),
    ("橄榄岩", "橄榄岩"),
    ("辉石岩", "辉石岩"),
    ("角闪石岩", "角闪石岩"),
    ("辉绿",   "辉绿岩"),
    ("混合花岗岩", "混合花岗岩"),
    ("花岗闪长", "花岗闪长岩"),
    ("花岗",   "花岗岩"),
    ("闪长",   "闪长岩"),
    ("辉长",   "辉长岩"),
    ("正长",   "正长岩"),
    ("二长",   "二长岩"),
    ("斜长岩", "斜长岩"),
    ("伟晶",   "伟晶岩"),
    ("细晶岩", "细晶岩"),
    ("煌斑",   "煌斑岩"),
    ("苦橄",   "苦橄岩"),
    ("玢岩",   "玢岩"),
    ("细碧",   "玄武岩"),
    ("碱玄",   "玄武岩"),
    ("玄武",   "玄武岩"),
    ("角斑岩", "安山岩"),
    ("安山",   "安山岩"),
    ("流纹",   "流纹岩"),
    ("英安",   "英安岩"),
    ("粗面",   "粗面岩"),
    ("响岩",   "响岩"),
    ("珍珠岩", "珍珠岩"),
    ("黑曜",   "黑曜岩"),
    ("松脂",   "黑曜岩"),
    ("浮岩",   "浮岩"),
    ("碳酸岩", "碳酸岩"),
    ("凝灰",   "凝灰岩"),
    ("斑岩",   "斑岩"),
    # 变质岩
    ("构造角砾", "构造角砾岩"),
    ("糜棱",   "糜棱岩"),
    ("千糜",   "糜棱岩"),
    ("碎裂",   "碎裂岩"),
    ("压碎",   "碎裂岩"),
    ("混合花岗岩", "混合花岗岩"),
    ("混合岩", "混合岩"),
    ("板岩",   "板岩"),
    ("千枚",   "千枚岩"),
    ("片麻",   "片麻岩"),
    ("麻粒岩", "麻粒岩"),
    ("变粒岩", "变粒岩"),
    ("浅粒岩", "浅粒岩"),
    ("榴辉岩", "榴辉岩"),
    ("蛇纹",   "蛇纹岩"),
    ("片岩",   "片岩"),
    ("石英岩", "石英岩"),
    ("大理",   "大理岩"),
    ("角岩",   "角岩"),
    ("矽卡岩", "矽卡岩"),
    ("砂卡岩", "矽卡岩"),
    # 化学岩及其他
    ("石膏",   "石膏"),
    ("硬石膏", "石膏"),
    ("岩盐",   "岩盐"),
    ("盐岩",   "岩盐"),
    ("硅质",   "硅质岩"),
    ("燧石",   "硅质岩"),
    ("白垩",   "白垩"),
    # 第四系松散土
    ("黏土",   "粘土"),
    ("粘土",   "粘土"),
    ("粉土",   "粉土"),
    ("红土",   "红土"),
    ("碎石",   "碎石土"),
    ("贝壳",   "贝壳层"),
    ("冰碛",   "冰碛"),
    # 第四纪成因类型（长词在前：残坡积/冲洪积先于坡积/冲积）
    ("残坡积", "残坡积"),
    ("冲洪积", "冲洪积"),
    ("洪冲积", "冲洪积"),
    ("冲积",   "冲积"),
    ("洪积",   "洪积"),
    ("坡积",   "坡积"),
    ("残积",   "残积"),
    ("崩积",   "崩积"),
    ("地滑堆积", "崩积"),
    ("风积",   "风积"),
    ("湖积",   "湖积"),
    ("海积",   "海积"),
    ("沼泽",   "沼泽堆积"),
    ("冰水",   "冰水堆积"),
    ("化学堆积", "化学堆积"),
    ("化学沉积", "化学堆积"),
    ("粗砂",   "粗砂"),
    ("中砂",   "中砂"),
    ("细砂",   "细砂"),
    ("粉砂",   "粉砂"),
    ("砂土",   "砂土"),
    ("砂",     "砂土"),
    ("淤泥质", "淤泥"),   # 守卫：防止"淤泥质"被"泥质"修饰词误拆
    ("淤泥",   "淤泥"),
    ("填土",   "填土"),
    ("素填土", "填土"),
    ("填筑土", "填土"),
    ("人工堆积", "填土"),
    ("黄土",   "黄土"),
)


def resolve(name):
    """把用户写的岩性名（如"灰白色中粒砂岩"）解析为标准岩性名。

    解析顺序：精确名 → 修饰词组合（如"硅质页岩"＝页岩＋Si 符号，
    自动生成并注册）→ 关键字别名。
    """
    name = (name or "").strip()
    if name in LITHOLOGY:
        return name
    comp = _try_composite(name)
    if comp:
        return comp
    for key, std in _ALIAS_ORDER:
        if key in name:
            return std
    return None


def style_of(name):
    std = resolve(name)
    return LITHOLOGY.get(std, DEFAULT)


# ---------------------------------------------------------------------------
# 花纹生成器：输入包围盒 (x0,x1,y0,y1) 和数据坐标下的花纹间距 (dx,dy)，
# 返回 (线段列表, 点标记列表)。点标记为 (xs, ys, marker, filled)。
# ---------------------------------------------------------------------------

def _pat_brick(x0, x1, y0, y1, dx, dy):
    """砖形（石灰岩）"""
    dx *= 2.2
    segs = []
    ys = np.arange(y0, y1 + dy, dy)
    for y in ys:
        segs.append([(x0, y), (x1, y)])
    for k, y in enumerate(ys[:-1]):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 - dx + off, x1 + dx, dx):
            segs.append([(x, y), (x, y + dy)])
    return segs, []


def _pat_brick_diag(x0, x1, y0, y1, dx, dy):
    """斜砖形（白云岩）"""
    slant = dx * 0.7  # 一个行高对应的横向偏移（dx、dy 表示相同的英寸间距）
    dx *= 2.2
    segs = []
    ys = np.arange(y0, y1 + dy, dy)
    for y in ys:
        segs.append([(x0, y), (x1, y)])
    for k, y in enumerate(ys[:-1]):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 - dx + off, x1 + dx, dx):
            segs.append([(x, y), (x + slant, y + dy)])
    return segs, []


def _pat_marl(x0, x1, y0, y1, dx, dy):
    """砖形加虚线（泥灰岩）"""
    dx *= 2.2
    segs = []
    ys = np.arange(y0, y1 + dy, dy)
    for y in ys:
        segs.append([(x0, y), (x1, y)])
    dash = dx * 0.35
    for k, y in enumerate(ys[:-1]):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 - dx + off, x1 + dx, dx):
            segs.append([(x + dx * 0.28, y + dy / 2), (x + dx * 0.28 + dash, y + dy / 2)])
            segs.append([(x, y), (x, y + dy)])
    return segs, []


def _pat_dots(x0, x1, y0, y1, dx, dy):
    """点（砂岩、砂土）"""
    dx *= 0.8
    dy *= 0.8
    xs, ys = [], []
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy)):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 + dx / 4 + off, x1, dx):
            xs.append(x)
            ys.append(y)
    return [], [(xs, ys, ".", True)]


def _pat_dots_dash(x0, x1, y0, y1, dx, dy):
    """点线相间（粉砂岩）"""
    segs, xs, ys = [], [], []
    dash = dx * 0.7
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy * 0.8)):
        if k % 2 == 0:
            for x in np.arange(x0 + dx / 4 + (k % 4) * dx / 2, x1, dx):
                xs.append(x)
                ys.append(y)
        else:
            for x in np.arange(x0 - dx, x1 + dx, dx * 1.5):
                segs.append([(x, y), (x + dash, y)])
    return segs, [(xs, ys, ".", True)]


def _pat_dash(x0, x1, y0, y1, dx, dy):
    """短横线（泥岩）"""
    segs = []
    dash = dx * 1.1
    gap = dx * 0.7
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy * 0.9)):
        start = x0 - (k % 2) * (dash + gap) / 2
        for x in np.arange(start, x1 + dash, dash + gap):
            segs.append([(x, y), (x + dash, y)])
    return segs, []


def _pat_dash_dots(x0, x1, y0, y1, dx, dy):
    """短横线加点（淤泥）"""
    segs, xs, ys = [], [], []
    dash = dx * 1.1
    gap = dx * 0.7
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy * 0.9)):
        start = x0 - (k % 2) * (dash + gap) / 2
        for x in np.arange(start, x1 + dash, dash + gap):
            segs.append([(x, y), (x + dash, y)])
            xs.append(x + dash + gap / 2)
            ys.append(y)
    return segs, [(xs, ys, ".", True)]


def _pat_hlines(x0, x1, y0, y1, dx, dy):
    """细密横线（页岩）"""
    segs = []
    for y in np.arange(y0 + dy * 0.3, y1, dy * 0.55):
        segs.append([(x0, y), (x1, y)])
    return segs, []


def _pat_gravel(x0, x1, y0, y1, dx, dy):
    """圆砾加点（砾岩）"""
    dx *= 1.8
    dy *= 1.4
    cx, cy, px, py = [], [], [], []
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy)):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 + dx / 4 + off, x1, dx):
            cx.append(x)
            cy.append(y)
            px.append(x + dx / 2)
            py.append(y + dy * 0.15)
    return [], [(cx, cy, "o", False), (px, py, ".", True)]


def _pat_plus(x0, x1, y0, y1, dx, dy):
    """十字（花岗岩类侵入岩）"""
    dx *= 1.6
    dy *= 1.6
    segs = []
    arm_x = dx * 0.22
    arm_y = dy * 0.22
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy)):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 + dx / 4 + off, x1, dx):
            segs.append([(x - arm_x, y), (x + arm_x, y)])
            segs.append([(x, y - arm_y), (x, y + arm_y)])
    return segs, []


def _pat_vees(x0, x1, y0, y1, dx, dy):
    """V 形（玄武岩类喷出岩）"""
    dx *= 1.6
    dy *= 1.6
    segs = []
    ax_ = dx * 0.2
    ay_ = dy * 0.3
    for k, y in enumerate(np.arange(y0 + dy / 2, y1, dy)):
        off = (k % 2) * dx / 2
        for x in np.arange(x0 + dx / 4 + off, x1, dx):
            segs.append([(x - ax_, y + ay_ / 2), (x, y - ay_ / 2)])
            segs.append([(x, y - ay_ / 2), (x + ax_, y + ay_ / 2)])
    return segs, []


def _pat_diag(x0, x1, y0, y1, dx, dy):
    """斜线（粘土）"""
    segs = []
    span = y1 - y0
    w = span * (dx / dy)  # 保证视觉上 45 度
    for x in np.arange(x0 - w, x1 + dx, dx * 1.3):
        segs.append([(x, y0), (x + w, y1)])
    return segs, []


def _pat_diag_dots(x0, x1, y0, y1, dx, dy):
    """斜线加点（粉土、黄土）"""
    segs, _ = _pat_diag(x0, x1, y0, y1, dx * 1.6, dy * 1.6)
    xs, ys = [], []
    for k, y in enumerate(np.arange(y0 + dy * 0.6, y1, dy * 1.2)):
        off = (k % 2) * dx * 0.6
        for x in np.arange(x0 + dx * 0.3 + off, x1, dx * 1.2):
            xs.append(x)
            ys.append(y)
    return segs, [(xs, ys, ".", True)]


def _pat_cross(x0, x1, y0, y1, dx, dy):
    """交叉斜线（填土）"""
    segs = []
    span = y1 - y0
    w = span * (dx / dy)
    for x in np.arange(x0 - w, x1 + dx, dx * 1.5):
        segs.append([(x, y0), (x + w, y1)])
        segs.append([(x + w, y0), (x, y1)])
    return segs, []


PATTERNS = {
    "brick": _pat_brick,
    "brick_diag": _pat_brick_diag,
    "marl": _pat_marl,
    "dots": _pat_dots,
    "dots_dash": _pat_dots_dash,
    "dash": _pat_dash,
    "dash_dots": _pat_dash_dots,
    "hlines": _pat_hlines,
    "gravel": _pat_gravel,
    "plus": _pat_plus,
    "vees": _pat_vees,
    "diag": _pat_diag,
    "diag_dots": _pat_diag_dots,
    "cross": _pat_cross,
}


# ---------------------------------------------------------------------------
# 声明式花纹引擎：一个花纹由若干“图元”叠加而成，每个图元是一个字典，
# 用 JSON 即可描述，供用户自定义（见 examples/strat_style.json）。图元类型：
#   {"type":"lines",  "angle":45, "spacing":1.0, "dash":0, "gap":0, "offset":0}
#       一族平行线：angle 角度(°)，spacing 间距倍率，dash/gap 虚线段长/间隔
#       （倍率，0 为实线），offset 隔行错位比例。
#   {"type":"markers","marker":".", "spacing":1.0, "size":2.0,
#                     "filled":true, "stagger":true}
#       点阵符号：marker 取 matplotlib 记号（. o + x v ^ s * d D | _ 等），
#       size 点大小，filled 是否实心，stagger 隔行错半格。("dots" 同义)
#   {"type":"brick",  "spacing":2.2, "ratio":2.2, "slant":0}
#       砖形：spacing 层高倍率，ratio 砖宽/层高，slant 每层横向斜移(层高的倍数)。
# 所有尺寸都以英寸为基准（乘 BASE_SPACING），因此不同比例尺下疏密一致；
# 角度按真实几何计算，方向正确。
# ---------------------------------------------------------------------------

def _corners_proj(X0, X1, Y0, Y1, ux, uy):
    vals = [cx * ux + cy * uy for cx in (X0, X1) for cy in (Y0, Y1)]
    return min(vals), max(vals)


def _legend_lattice_positions(lo, hi, step, phase=0.0,
                              lower_margin=0.0, upper_margin=0.0):
    """Return a centred, clipped one-dimensional legend lattice.

    The old symbol primitives started every row at ``width / 4`` of one
    grid cell.  That made the visible result depend on the swatch width:
    staggered rows were shifted to the right and the final symbol was often
    cut by the frame.  A legend is a representative symbol, so its lattice is
    instead anchored at the physical centre of the swatch.  Integer phases
    form the odd rows and half phases form the complementary even rows.

    ``lower_margin``/``upper_margin`` are the physical glyph extents around
    its anchor.  Filtering anchors with these margins prevents the frame from
    clipping triangles, text markers and other wide symbols.
    """
    if step <= 1e-12 or hi <= lo:
        return []
    lower = lo + max(0.0, lower_margin)
    upper = hi - max(0.0, upper_margin)
    if upper < lower:
        return [(lo + hi) / 2]
    centre = (lo + hi) / 2
    eps = 1e-10
    first = math.ceil((lower - centre) / step - phase - eps)
    last = math.floor((upper - centre) / step - phase + eps)
    if first > last:
        # A very sparse but still smaller-than-the-box glyph should remain
        # visible once, at the nearest legal anchor to the box centre.
        return [min(max(centre, lower), upper)]
    return [centre + (index + phase) * step
            for index in range(first, last + 1)]


def _legend_lattice_anchors(lo, hi, step, phase, bounds,
                            anchor_offset=0.0):
    """Place a glyph lattice by physical centres and return draw anchors.

    ``bounds`` are the complete glyph (or composite glyph) bounds relative to
    a base anchor.  Several standard symbols such as Γ, L and bold variants
    are not symmetric around their declared vector origin.  Centring those
    origins therefore leaves the visible row shifted and can incorrectly
    discard an edge symbol.  Build the lattice from the physical half-width,
    then map each visual centre back to the primitive's drawing anchor.
    """
    lower, upper = bounds
    centre_offset = (lower + upper) / 2
    half_width = max(0.0, (upper - lower) / 2)
    visual_centres = _legend_lattice_positions(
        lo, hi, step, phase=phase,
        lower_margin=half_width, upper_margin=half_width)
    return [centre - centre_offset + anchor_offset
            for centre in visual_centres]


def _legend_row_positions(Y0, Y1, step, yoff=0.0,
                          lower_margin=0.0, upper_margin=0.0,
                          alternate_phase=None):
    """Return ``(row_index, y)`` pairs for a canonical legend symbol array.

    Normal arrays retain the repeat count implied by ``step`` but distribute
    any spare room equally above and below.  If a glyph is taller than one
    nominal band, only the row pitch is tightened; the glyph itself keeps its
    standard physical size.

    ``alternate_phase`` is used by patterns such as granite: phase 0 occupies
    representative rows 1 and 3, while phase 1 occupies row 2.  This avoids
    drawing two independent three-row arrays on top of one another.
    """
    height = Y1 - Y0
    if step <= 1e-12 or height <= 0:
        return []
    context = _LEGEND_SWATCH_CONTEXT.get()
    if alternate_phase is not None and context is not None:
        row_count = max(1, int(context[1]))
        band = height / row_count
        parity = int(alternate_phase) % 2
        return [
            (index, Y0 + (index + 0.5) * band)
            for index in range(row_count)
            if index % 2 == parity
        ]

    # Match the repeat count of the original half-step-starting grid.
    count = len(np.arange(Y0 + step / 2, Y1, step))
    count = max(1, count)
    centre = (Y0 + Y1) / 2
    if count == 1:
        pitch = 0.0
    else:
        safe_half_span = min(
            centre - Y0 - max(0.0, lower_margin),
            Y1 - centre - max(0.0, upper_margin),
        )
        pitch = min(step, max(0.0, 2 * safe_half_span / (count - 1)))
    start = centre - (count - 1) * pitch / 2
    shift = yoff * step
    low_shift = (Y0 + max(0.0, lower_margin)) - start
    high_shift = (Y1 - max(0.0, upper_margin)) - (
        start + (count - 1) * pitch)
    shift = min(max(shift, low_shift), high_shift)
    return [(index, start + index * pitch + shift)
            for index in range(count)]


def _legend_shared_slot_positions(lo, hi, slots, slot_phase, row_index,
                                  lower_margin=0.0, upper_margin=0.0,
                                  slot_mask=0, slot_period=3):
    """Select positions from one shared row of legend slots.

    ``slot_mask`` is a compact row-major occupancy map.  It is used by
    multi-family standards such as volcaniclastics, whose four columns must
    obey 1:3 / 1:1:2 ratios without two independently centred lattices
    colliding.  The historical phase-only form remains the default for
    two-family alternating patterns such as feldspathic sandstone.
    """
    count = max(1, int(slots))
    width = hi - lo
    mask = int(slot_mask)
    if mask:
        period = max(1, int(slot_period))
        semantic_row = int(row_index) % period
        if _SHAPE_FLIP[0]:
            semantic_row = period - 1 - semantic_row
        row_mask = (mask >> (semantic_row * count)) \
            & ((1 << count) - 1)
        wanted_slots = {
            index for index in range(count) if row_mask & (1 << index)
        }
    else:
        wanted = (int(slot_phase) + int(row_index)) % 2
        wanted_slots = {
            index for index in range(count) if index % 2 == wanted
        }
    result = []
    for index in range(count):
        if index not in wanted_slots:
            continue
        value = lo + (index + 0.5) * width / count
        if (value - lower_margin >= lo - 1e-10
                and value + upper_margin <= hi + 1e-10):
            result.append(value)
    return result


def _prim_lines(X0, X1, Y0, Y1, angle=0.0, spacing=1.0, dash=0.0, gap=0.0,
                offset=0.0, phase=0.0, origin=0.5,
                _legend_single_anchor=None):
    """phase：虚线沿线方向的起始相位（基准间距的倍数），点划线由两层
    错相的虚线叠成。origin：线族相对包围盒边的相位（间距的倍数）——
    缺省 0.5（首条线离边半个间距，小样上不与边框重合）；层理式花纹
    用 0（线在整数倍位置，层间符号恰好居中，样框内层带完整）。"""
    s = spacing * BASE_SPACING
    th = math.radians(angle)
    dx, dy = math.cos(th), math.sin(th)      # 线方向
    nx, ny = -dy, dx                          # 法向
    if _legend_single_anchor is not None:
        cx, cy = _legend_single_anchor
        length = (dash * BASE_SPACING if dash and dash > 0
                  else max(2.0 * _MM, min(spacing * BASE_SPACING,
                                          5.0 * _MM)))
        return [[(cx - length * dx / 2, cy - length * dy / 2),
                 (cx + length * dx / 2, cy + length * dy / 2)]], []
    pmin, pmax = _corners_proj(X0, X1, Y0, Y1, nx, ny)   # 垂直方向范围
    qmin, qmax = _corners_proj(X0, X1, Y0, Y1, dx, dy)   # 沿线方向范围
    ext = 0.12                                # 英寸，向外溢出（由多边形裁剪）
    segs = []
    i0 = math.floor(pmin / s - origin) - 1
    i1 = math.ceil(pmax / s - origin) + 1
    for i in range(i0, i1 + 1):
        p = (i + origin) * s
        bx, by = p * nx, p * ny               # 线上一点（法向偏移 p）
        off = (offset * s) if (i % 2) else 0.0
        q0, q1 = qmin - ext + off, qmax + ext
        if dash and dash > 0:
            step = (dash + gap) * BASE_SPACING
            seg_len = dash * BASE_SPACING
            q = q0 + phase * BASE_SPACING
            while q < q1:
                segs.append([(bx + q * dx, by + q * dy),
                             (bx + (q + seg_len) * dx, by + (q + seg_len) * dy)])
                q += step
        else:
            segs.append([(bx + q0 * dx, by + q0 * dy),
                         (bx + q1 * dx, by + q1 * dy)])
    return segs, []


def _prim_markers(X0, X1, Y0, Y1, marker=".", spacing=1.0, size=2.0,
                  filled=True, stagger=True, xoff=0.0, yoff=0.0,
                  xspacing=0.0, _legend_center_x=False,
                  _legend_center_y=False, _legend_alternate_y=None,
                  _legend_group_x_bounds=None, _legend_phase_shift=0.0,
                  legend_single=False, legend_slots=0,
                  legend_slot_phase=0, legend_slot_mask=0,
                  legend_slot_period=3, _legend_single_anchor=None):
    """点阵符号。xoff/yoff 为网格相位偏移（间距的倍数），用于把多层
    符号对齐到同一网格点上（如 ⊙ = 椭圆图形层 + 圆点层）。
    xspacing 为横向间距倍率（缺省与 spacing 相同），层理式花纹里
    行距与行内符号间距不同时用。"""
    s = spacing * BASE_SPACING
    sx = (xspacing or spacing) * BASE_SPACING
    xs, ys = [], []
    # Matplotlib marker size is a diameter in points.  Include half the
    # 0.55-pt outline so hollow markers also stay clear of the swatch frame.
    radius = (max(0.0, float(size)) / 144.0 + 0.55 / 144.0
              + _LEGEND_SYMBOL_CLEARANCE_IN)
    if _legend_single_anchor is not None:
        cx, cy = _legend_single_anchor
        return [], [([cx], [cy], marker, filled, size)]
    if _legend_center_y:
        row_positions = _legend_row_positions(
            Y0, Y1, s, yoff=yoff,
            lower_margin=radius, upper_margin=radius,
            alternate_phase=_legend_alternate_y)
    else:
        row_positions = list(enumerate(
            np.arange(Y0 + s / 2, Y1, s) + yoff * s))
    for k, y in row_positions:
        if _legend_center_x:
            local_bounds = (-radius, radius)
            group_bounds = _legend_group_x_bounds or local_bounds
            component_offset = (xoff * sx
                                if _legend_group_x_bounds is not None else 0.0)
            if legend_slots:
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centres = _legend_shared_slot_positions(
                    X0, X1, legend_slots, legend_slot_phase, k,
                    lower_margin=half_width, upper_margin=half_width,
                    slot_mask=legend_slot_mask,
                    slot_period=legend_slot_period)
                row_xs = [centre - group_centre + component_offset
                          for centre in centres]
            elif legend_single:
                # Sparse sedimentary qualifiers occupy one complementary
                # anchor per course (left/right/left), rather than starting
                # a second infinite lattice that yields 2-1-2 or 1-2-1.
                side = -0.5 if k % 2 == 0 else 0.5
                component_phase = xoff if abs(xoff) < 0.25 else 0.0
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centre = ((X0 + X1) / 2
                          + (side + component_phase) * sx)
                centre = min(max(centre, X0 + half_width),
                             X1 - half_width)
                row_xs = [centre - group_centre + component_offset]
            else:
                phase = (_legend_phase_shift
                         + (0.5 if stagger and k % 2 else 0.0))
                if _legend_group_x_bounds is None:
                    phase += xoff
                row_xs = _legend_lattice_anchors(
                    X0, X1, sx, phase, group_bounds,
                    anchor_offset=component_offset)
        else:
            off = (sx / 2) if (stagger and k % 2) else 0.0
            row_xs = [x + xoff * sx
                      for x in np.arange(X0 + sx / 4 + off, X1, sx)]
        xs.extend(row_xs)
        ys.extend([y] * len(row_xs))
    return [], [(xs, ys, marker, filled, size)]


# ---------------------------------------------------------------------------
# 矢量图形图元：按 GB/T 958—2015 的制图参数（毫米）精确绘制标准符号。
# 每个图形是单位方框 [-0.5,0.5]²（y 向下为视觉下方）内的折线组，绘制时
# 按 w×h（毫米）缩放、可旋转（tilt 度）、可加粗（双描）。不依赖字体，
# 任何环境下形状一致。
# ---------------------------------------------------------------------------

_MM = 1 / 25.4          # 毫米 → 英寸

def _ellipse_pts(n=16):
    ts = np.linspace(0, 2 * np.pi, n + 1)
    return [list(zip(0.5 * np.cos(ts), 0.5 * np.sin(ts)))]

def _arc_pts(a0, a1, r=0.5, cx=0.0, cy=0.0, n=10):
    ts = np.linspace(math.radians(a0), math.radians(a1), n + 1)
    return list(zip(cx + r * np.cos(ts), cy + r * np.sin(ts)))

def _sine_pts(n=12, periods=1.0, amp=0.5):
    xs = np.linspace(-0.5, 0.5, n + 1)
    return list(zip(xs, -amp * np.sin(2 * np.pi * periods * (xs + 0.5))))


def _solid_lens_polys():
    """Closed, densely inked chert-nodule lens used by RPSE021105."""
    upper = _arc_pts(15, 165, 0.52, 0.0, -0.28)
    lower = _arc_pts(195, 345, 0.52, 0.0, 0.28)
    outline = upper + lower
    outline.append(outline[0])
    fills = []
    for y, half_width in (
            (-0.20, 0.20), (-0.14, 0.36), (-0.07, 0.46),
            (0.00, 0.50), (0.07, 0.46), (0.14, 0.36), (0.20, 0.20)):
        fills.append([(-half_width, y), (half_width, y)])
    return [outline] + fills

_SHAPES = {
    # 砾（扁椭圆）、角砾（空心三角）
    "椭圆":   _ellipse_pts(),
    "三角":   [[(-0.5, 0.5), (0.0, -0.5), (0.5, 0.5), (-0.5, 0.5)]],
    # 碎裂岩符（RPTC011003）：直角在右下，斜边自左下升向右上
    "直角三角": [[(-0.5, 0.5), (0.5, 0.5), (0.5, -0.5), (-0.5, 0.5)]],
    "菱形":   [[(-0.5, 0.0), (0.0, -0.5), (0.5, 0.0), (0.0, 0.5),
                (-0.5, 0.0)]],
    # 火成岩矿物符号
    "十字":   [[(-0.5, 0.0), (0.5, 0.0)], [(0.0, -0.5), (0.0, 0.5)]],
    "X形":    [[(-0.5, -0.5), (0.5, 0.5)], [(-0.5, 0.5), (0.5, -0.5)]],
    "T形":    [[(-0.5, -0.5), (0.5, -0.5)], [(0.0, -0.5), (0.0, 0.5)]],
    "倒T形":  [[(-0.5, 0.5), (0.5, 0.5)], [(0.0, -0.5), (0.0, 0.5)]],
    "工形":   [[(-0.5, -0.5), (0.5, -0.5)], [(-0.5, 0.5), (0.5, 0.5)],
               [(0.0, -0.5), (0.0, 0.5)]],
    "Γ形":    [[(-0.35, 0.5), (-0.35, -0.5), (0.5, -0.5)]],
    "L形":    [[(-0.35, -0.5), (-0.35, 0.5), (0.5, 0.5)]],
    "N形":    [[(-0.4, 0.5), (-0.4, -0.5), (0.4, 0.5), (0.4, -0.5)]],
    "人字":   [[(-0.45, 0.5), (0.0, -0.5), (0.45, 0.5)]],
    "V字":    [[(-0.45, -0.5), (0.0, 0.5), (0.45, -0.5)]],
    "斜线":   [[(-0.5, 0.5), (0.5, -0.5)]],
    "双斜线": [[(-0.5, 0.5), (0.2, -0.5)], [(-0.2, 0.5), (0.5, -0.5)]],
    "上箭头": [[(0.0, 0.5), (0.0, -0.5)],
               [(-0.28, -0.12), (0.0, -0.5), (0.28, -0.12)]],
    "下箭头": [[(0.0, -0.5), (0.0, 0.5)],
               [(-0.28, 0.12), (0.0, 0.5), (0.28, 0.12)]],
    "左箭头": [[(0.5, 0.0), (-0.5, 0.0)],
               [(-0.12, -0.28), (-0.5, 0.0), (-0.12, 0.28)]],
    # 化学岩、特殊符号
    "梳形":   [[(-0.5, -0.5), (0.5, -0.5)], [(-0.33, -0.5), (-0.33, 0.5)],
               [(0.0, -0.5), (0.0, 0.5)], [(0.33, -0.5), (0.33, 0.5)]],
    "方框":   [[(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5),
                (-0.5, -0.5)]],
    "波折线": [_sine_pts(periods=1.0, amp=0.4)],
    "S形":    [_arc_pts(90, 270, 0.28, 0.0, -0.22) +
               _arc_pts(90, -90, 0.28, 0.0, 0.34)[1:]],
    "贝壳形": [_arc_pts(80, -80, 0.3, -0.05, -0.2),
               _arc_pts(80, -80, 0.3, -0.05, 0.3)],
    # 弧形符号（y 向下为视觉下方）：礁灰岩=同心双拱（RPSE021093）
    "拱形":   [_arc_pts(180, 360, 0.5, 0.0, 0.4),
               _arc_pts(180, 360, 0.3, 0.0, 0.4)],
    "蛇纹符": [_arc_pts(0, 180, 0.42, 0.0, -0.05),
               [(0.0, 0.15), (0.0, -0.45)]],
    "燧石符": [_sine_pts(periods=1.0, amp=0.28), [(-0.3, 0.5), (0.3, -0.5)]],
    # Table 4 basic-symbol motifs.  These are complete local glyphs rather
    # than two unrelated repeated primitives.
    "结核符": [[(-0.5, 0.05), (-0.32, -0.30), (0.10, -0.42),
                (0.5, -0.08), (0.28, 0.32), (-0.18, 0.42),
                (-0.5, 0.05)]],
    "流纹质符": [[(-0.46, -0.46), (-0.08, 0.34)],
                 [(0.08, 0.34), (0.46, -0.46)]],
    "英安质符": [[(-0.46, -0.42), (-0.06, 0.30), (0.42, -0.40)],
                 [(0.12, 0.04), (0.42, 0.30)]],
    "竖条":   [[(0.0, -0.5), (0.0, 0.5)]],
    "横条":   [[(-0.5, 0.0), (0.5, 0.0)]],
    "三竖":   [[(-0.4, -0.5), (-0.4, 0.5)], [(0.0, -0.5), (0.0, 0.5)],
               [(0.4, -0.5), (0.4, 0.5)]],
    # 黑曜岩符（RPMM024012）：两条上翘弧臂加下垂短柄，形似曲臂 Y
    "黑曜符": [_arc_pts(150, 250, 0.55, -0.28, 0.0),
               _arc_pts(290, 390, 0.55, 0.28, 0.0),
               [(0.0, -0.05), (0.0, 0.5)]],
    "圆加尾": _ellipse_pts(12) + [[(0.35, 0.35), (0.6, 0.6)]],
    # 贝壳层符（RPSE022027）：近全圆的螺形加尾
    "螺形":   [_arc_pts(60, 360, 0.38, 0.0, -0.05) +
               [(0.42, 0.28), (0.5, 0.5)]],
    # 花岗岩长石十字（RPMM014002）：横臂为分离的双短粗线，竖臂细长
    "长石十字": [[(-0.5, -0.06), (-0.14, -0.06)], [(-0.5, 0.06), (-0.14, 0.06)],
                 [(-0.5, -0.06), (-0.5, 0.06)], [(-0.14, -0.06), (-0.14, 0.06)],
                 [(0.14, -0.06), (0.5, -0.06)], [(0.14, 0.06), (0.5, 0.06)],
                 [(0.14, -0.06), (0.14, 0.06)], [(0.5, -0.06), (0.5, 0.06)],
                 [(0.0, -0.5), (0.0, 0.5)]],
    # 油页岩符（RPSE021063，宽×高 3×1）：左空右实的矩形
    "油页符": [[(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5),
                (-0.5, -0.5)], [(-0.1, -0.5), (-0.1, 0.5)],
               [(-0.1, -0.3), (0.5, -0.3)], [(-0.1, -0.1), (0.5, -0.1)],
               [(-0.1, 0.1), (0.5, 0.1)], [(-0.1, 0.3), (0.5, 0.3)]],
    # 燧石结核符（RPSE021105）：实心黑透镜体
    "实心透镜": _solid_lens_polys(),
    # —— GB/T 958 补充符号（火山碎屑、结晶结构、碱性岩等）——
    "F形":    [[(-0.35, 0.5), (-0.35, -0.5), (0.5, -0.5)],
               [(-0.35, 0.0), (0.35, 0.0)]],
    "π形":    [[(-0.5, -0.5), (0.5, -0.5)], [(-0.25, -0.5), (-0.25, 0.5)],
               [(0.25, -0.5), (0.25, 0.5)]],
    "安粗符": [[(-0.5, -0.5), (0.5, -0.5)], [(-0.35, -0.15), (0.35, -0.15)],
               [(0.0, -0.5), (0.0, 0.5)]],
    "集块符": [[(-0.45, 0.25), (-0.3, -0.35), (0.1, -0.45), (0.45, -0.1),
                (0.35, 0.4), (-0.1, 0.45), (-0.45, 0.25)]],
    "玻屑弧": [_arc_pts(70, -70, 0.5)],
    "浆屑弧": [_arc_pts(100, -100, 0.45)],
    "空心十字": [[(-0.15, -0.5), (0.15, -0.5), (0.15, -0.15), (0.5, -0.15),
                  (0.5, 0.15), (0.15, 0.15), (0.15, 0.5), (-0.15, 0.5),
                  (-0.15, 0.15), (-0.5, 0.15), (-0.5, -0.15), (-0.15, -0.15),
                  (-0.15, -0.5)]],
    "双圆":   _ellipse_pts(14) + [list(zip(
                  0.25 * np.cos(np.linspace(0, 2 * np.pi, 11)),
                  0.25 * np.sin(np.linspace(0, 2 * np.pi, 11))))],
    "八形":   [_arc_pts(90, 450, 0.24, 0.0, -0.26),
               _arc_pts(90, 450, 0.24, 0.0, 0.26)],
    "竖梭":   [[(0.0, -0.5), (0.22, 0.0), (0.0, 0.5), (-0.22, 0.0),
                (0.0, -0.5)]],
    "横梭":   [[(-0.5, 0.0), (0.0, 0.28), (0.5, 0.0), (0.0, -0.28),
                (-0.5, 0.0)]],
    "倒三角": [[(-0.5, -0.5), (0.5, -0.5), (0.0, 0.5), (-0.5, -0.5)]],
    "正负号": [[(-0.5, -0.25), (0.5, -0.25)], [(0.0, -0.5), (0.0, 0.0)],
               [(-0.5, 0.35), (0.5, 0.35)]],
    "等号":   [[(-0.5, -0.2), (0.5, -0.2)], [(-0.5, 0.2), (0.5, 0.2)]],
    "双短横": [[(-0.5, 0.0), (-0.1, 0.0)], [(0.1, 0.0), (0.5, 0.0)]],
    "三短横": [[(-0.5, -0.35), (0.5, -0.35)], [(-0.5, 0.0), (0.5, 0.0)],
               [(-0.5, 0.35), (0.5, 0.35)]],
    "Z形":    [[(-0.4, -0.5), (0.4, -0.5), (-0.4, 0.5), (0.4, 0.5)]],
    "半圆":   [_arc_pts(180, 360, 0.45, 0.0, 0.1)],
    "钙壳符": [[(-0.5, -0.4), (0.5, -0.4)], [(0.0, -0.4), (0.0, 0.5)]],
    "枕状符": [_arc_pts(180, 360, 0.28, -0.22, 0.15),
               _arc_pts(180, 360, 0.28, 0.22, 0.15),
               [(-0.5, 0.15), (0.5, 0.15)]],
    "透镜符": [_arc_pts(15, 165, 0.52, 0.0, -0.28),
               _arc_pts(195, 345, 0.52, 0.0, 0.28)],
}


def _prim_shape(X0, X1, Y0, Y1, shape="椭圆", w=2.0, h=1.0, spacing=2.0,
                stagger=True, tilt=0.0, bold=False, xoff=0.0, yoff=0.0,
                xspacing=0.0, _legend_center_x=False,
                _legend_center_y=False, _legend_alternate_y=None,
                _legend_group_x_bounds=None, _legend_phase_shift=0.0,
                legend_single=False, legend_slots=0,
                legend_slot_phase=0, legend_slot_mask=0,
                legend_slot_period=3, _legend_single_anchor=None,
                _legend_explicit_positions=None):
    """标准图形阵：shape 图形名，w×h 毫米，spacing 网格间距倍率，
    tilt 旋转角（度），bold 加粗（双描），xoff/yoff 网格相位偏移，
    xspacing 横向间距倍率（缺省与 spacing 相同）。
    网格与 markers 图元一致，同参数的多层符号会对齐到同一格点。"""
    polys = _SHAPES.get(shape)
    if not polys:
        return [], []
    s = spacing * BASE_SPACING
    sx = (xspacing or spacing) * BASE_SPACING
    wx, hy = w * _MM, h * _MM
    th = math.radians(tilt)
    ct, st = math.cos(th), math.sin(th)
    flip = -1.0 if _SHAPE_FLIP[0] else 1.0
    templates = []
    for poly in polys:
        template = []
        for px, py in poly:
            px, py = px * wx, py * hy * flip
            template.append((px * ct - py * st,
                             px * st + py * ct))
        templates.append(template)
    offsets = [point for template in templates for point in template]
    bold_offset = 0.1 * _MM if bold else 0.0
    if bold:
        offsets += [(x + bold_offset, y + bold_offset)
                    for x, y in offsets]
    min_x = (min(point[0] for point in offsets)
             - _LEGEND_SYMBOL_CLEARANCE_IN)
    max_x = (max(point[0] for point in offsets)
             + _LEGEND_SYMBOL_CLEARANCE_IN)
    min_y = (min(point[1] for point in offsets)
             - _LEGEND_SYMBOL_CLEARANCE_IN)
    max_y = (max(point[1] for point in offsets)
             + _LEGEND_SYMBOL_CLEARANCE_IN)
    if _legend_explicit_positions is not None:
        row_positions = [
            (index, point[1])
            for index, point in enumerate(_legend_explicit_positions)
        ]
    elif _legend_single_anchor is not None:
        row_positions = [(0, _legend_single_anchor[1])]
    elif _legend_center_y:
        centre_y = (min_y + max_y) / 2
        half_height = (max_y - min_y) / 2
        visual_rows = _legend_row_positions(
            Y0, Y1, s, yoff=yoff,
            lower_margin=half_height, upper_margin=half_height,
            alternate_phase=_legend_alternate_y)
        row_positions = [(index, y - centre_y)
                         for index, y in visual_rows]
    else:
        row_positions = list(enumerate(
            np.arange(Y0 + s / 2, Y1, s) + yoff * s))
    segs = []
    for k, cy in row_positions:
        if _legend_explicit_positions is not None:
            row_xs = [_legend_explicit_positions[k][0]]
        elif _legend_single_anchor is not None:
            row_xs = [_legend_single_anchor[0]]
        elif _legend_center_x:
            local_bounds = (min_x, max_x)
            group_bounds = _legend_group_x_bounds or local_bounds
            component_offset = (xoff * sx
                                if _legend_group_x_bounds is not None else 0.0)
            if legend_slots:
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centres = _legend_shared_slot_positions(
                    X0, X1, legend_slots, legend_slot_phase, k,
                    lower_margin=half_width, upper_margin=half_width,
                    slot_mask=legend_slot_mask,
                    slot_period=legend_slot_period)
                row_xs = [centre - group_centre + component_offset
                          for centre in centres]
            elif legend_single:
                side = -0.5 if k % 2 == 0 else 0.5
                component_phase = xoff if abs(xoff) < 0.25 else 0.0
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centre = ((X0 + X1) / 2
                          + (side + component_phase) * sx)
                centre = min(max(centre, X0 + half_width),
                             X1 - half_width)
                row_xs = [centre - group_centre + component_offset]
            else:
                phase = (_legend_phase_shift
                         + (0.5 if stagger and k % 2 else 0.0))
                if _legend_group_x_bounds is None:
                    phase += xoff
                row_xs = _legend_lattice_anchors(
                    X0, X1, sx, phase, group_bounds,
                    anchor_offset=component_offset)
        else:
            off = (sx / 2) if (stagger and k % 2) else 0.0
            row_xs = [x + xoff * sx
                      for x in np.arange(X0 + sx / 4 + off, X1, sx)]
        for cx in row_xs:
            for template in templates:
                pts = [(cx + px, cy + py) for px, py in template]
                for a, b in zip(pts[:-1], pts[1:]):
                    segs.append([a, b])
                if bold:   # 双描加粗：整体沿对角偏移 0.1 mm 再画一遍
                    for a, b in zip(pts[:-1], pts[1:]):
                        segs.append([(a[0] + bold_offset,
                                      a[1] + bold_offset),
                                     (b[0] + bold_offset,
                                      b[1] + bold_offset)])
    return segs, []


# paint() 按坐标轴方向设置：深度轴（y 向下）不翻转，普通轴（剖面
# 标高）翻转图形的 y，保证 ∧/∨、Γ 等方向敏感符号视觉方向正确。
_SHAPE_FLIP = [False]


def _prim_brick(X0, X1, Y0, Y1, spacing=2.2, ratio=2.2, slant=0.0,
                double=0.0, jdouble=0.0, jshort=0.0,
                _legend_center_x=False):
    """砖形层理：spacing 层高倍率，ratio 砖宽/层高，slant 节理斜率
    （每层高的横向偏移倍数），double 层面双线间距（毫米，0 为单线，
    大理岩用），jdouble 节理双线间距（毫米，白云岩双斜线用），
    jshort 节理缩短比例（0~1，泥灰岩式的短斜节理用）。"""
    s = spacing * BASE_SPACING                # 层高
    w = ratio * BASE_SPACING                  # 砖宽
    dd = double * _MM
    jd = jdouble * _MM
    segs = []
    ys = np.arange(Y0, Y1 + s, s)
    for y in ys:
        segs.append([(X0, y), (X1, y)])
        if dd:
            segs.append([(X0, y + dd), (X1, y + dd)])
    j0 = jshort / 2 * s                       # 节理上下留白
    for k, y in enumerate(ys[:-1]):
        if _legend_center_x:
            # Even courses have half bricks at both edges; odd courses have
            # full bricks.  The accompanying staggered symbol lattice then
            # falls at the exact centre of every visible brick.
            phase = 0.5 if k % 2 == 0 else 0.0
            joints = _legend_lattice_positions(
                X0, X1, w, phase=phase,
                lower_margin=-w, upper_margin=-w)
        else:
            off = (k % 2) * w / 2
            joints = np.arange(X0 - w + off, X1 + w, w)
        for x in joints:
            a = (x + slant * j0, y + j0)
            b = (x + slant * (s - j0), y + s - j0)
            segs.append([a, b])
            if jd:
                segs.append([(a[0] + jd, a[1]), (b[0] + jd, b[1])])
    return segs, []


def _prim_ell(X0, X1, Y0, Y1, spacing=2.0, size=0.32, stagger=True,
              xspacing=0.0, xoff=0.0,
              _legend_center_x=False, _legend_center_y=False,
              _legend_alternate_y=None, _legend_group_x_bounds=None,
              _legend_phase_shift=0.0, legend_single=False,
              legend_slots=0, legend_slot_phase=0, legend_slot_mask=0,
              legend_slot_period=3, _legend_single_anchor=None):
    """钙质符号"∟"（GB/T 958）：短横+短竖组成的直角。"""
    s = spacing * BASE_SPACING
    sx = (xspacing or spacing) * BASE_SPACING
    a = size * BASE_SPACING
    # ``ell`` predates the generic vector-shape primitive but represents the
    # same direction-sensitive GB/T symbol: visually it must remain ``└`` on
    # both depth (y-down) and elevation/catalogue (y-up) axes.  ``paint`` sets
    # _SHAPE_FLIP from the actual axis direction before invoking the pattern.
    vertical = a if _SHAPE_FLIP[0] else -a
    segs = []
    if _legend_single_anchor is not None:
        row_positions = [(0, _legend_single_anchor[1])]
    elif _legend_center_y:
        row_positions = _legend_row_positions(
            Y0, Y1, s,
            lower_margin=a / 2 + _LEGEND_SYMBOL_CLEARANCE_IN,
            upper_margin=a / 2 + _LEGEND_SYMBOL_CLEARANCE_IN,
            alternate_phase=_legend_alternate_y)
    else:
        row_positions = list(enumerate(np.arange(Y0 + s / 2, Y1, s)))
    for k, y in row_positions:
        if _legend_single_anchor is not None:
            row_xs = [_legend_single_anchor[0]]
        elif _legend_center_x:
            local_bounds = (-a / 2 - _LEGEND_SYMBOL_CLEARANCE_IN,
                            a / 2 + _LEGEND_SYMBOL_CLEARANCE_IN)
            group_bounds = _legend_group_x_bounds or local_bounds
            component_offset = (xoff * sx
                                if _legend_group_x_bounds is not None else 0.0)
            if legend_slots:
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centres = _legend_shared_slot_positions(
                    X0, X1, legend_slots, legend_slot_phase, k,
                    lower_margin=half_width, upper_margin=half_width,
                    slot_mask=legend_slot_mask,
                    slot_period=legend_slot_period)
                row_xs = [centre - group_centre + component_offset
                          for centre in centres]
            elif legend_single:
                side = -0.5 if k % 2 == 0 else 0.5
                group_min, group_max = group_bounds
                group_centre = (group_min + group_max) / 2
                half_width = (group_max - group_min) / 2
                centre = (X0 + X1) / 2 + side * sx
                centre = min(max(centre, X0 + half_width),
                             X1 - half_width)
                row_xs = [centre - group_centre + component_offset]
            else:
                phase = (_legend_phase_shift
                         + (0.5 if stagger and k % 2 else 0.0))
                if _legend_group_x_bounds is None:
                    phase += xoff
                row_xs = _legend_lattice_anchors(
                    X0, X1, sx, phase, group_bounds,
                    anchor_offset=component_offset)
        else:
            off = (sx / 2) if (stagger and k % 2) else 0.0
            row_xs = (np.arange(X0 + sx / 4 + off, X1, sx)
                      + xoff * sx)
        for cx in row_xs:
            if (_legend_center_x or _legend_center_y
                    or _legend_single_anchor is not None):
                # The historical anchor was the lower-left corner of ∟, so
                # placing that anchor on a grid point made the visible glyph
                # sit half its width to the right and half its height below
                # every other symbol.  Legend lattices represent visual
                # centres: keep the same ∟ orientation but centre its actual
                # physical bounds on (cx, y).
                x = cx - a / 2
                baseline = y - vertical / 2
            else:
                x = cx
                baseline = y
            segs.append([(x, baseline), (x + a, baseline)])
            segs.append([(x, baseline), (x, baseline + vertical)])
    return segs, []


def _prim_wave(X0, X1, Y0, Y1, spacing=1.0, amp=0.25, wavelength=1.2,
               dash=0.0, gap=0.0, yoff=0.0):
    """一族水平波状线（正弦），可虚线。amp/wavelength 以基准间距计；
    yoff 纵向偏移（毫米），双波状线（板岩）、虚实相间（片麻岩）用。"""
    s = spacing * BASE_SPACING
    a = amp * BASE_SPACING
    wl = max(wavelength * BASE_SPACING, 1e-4)
    segs = []
    xs = np.linspace(X0, X1, max(int((X1 - X0) / (wl / 12)), 8))
    for y in np.arange(Y0 + s / 2 + yoff * _MM, Y1, s):
        ys = y + a * np.sin(2 * np.pi * (xs - X0) / wl)
        pts = list(zip(xs, ys))
        if dash and dash > 0:
            step = int(max(2, (dash + gap)))
            on = int(max(1, dash))
            for i in range(0, len(pts) - 1):
                if (i % step) < on:
                    segs.append([pts[i], pts[i + 1]])
        else:
            for i in range(len(pts) - 1):
                segs.append([pts[i], pts[i + 1]])
    return segs, []


_PRIMS = {"lines": _prim_lines, "markers": _prim_markers,
          "dots": _prim_markers, "brick": _prim_brick, "ell": _prim_ell,
          "wave": _prim_wave, "shape": _prim_shape}


# ---------------------------------------------------------------------------
# 基本图形库：几十种最基础的地质图例组成图形，供用户拼行设计花纹。
# 键为中文名，值为图元 spec（可在 rows 行内容里按名字引用）。
# ---------------------------------------------------------------------------

def _cluster3(spacing=2.4, size=1.7):
    """石英"∴"三点簇（GB/T 958 石英符号）：三个点绕格点排成小三角。"""
    return [{"type": "markers", "marker": ".", "spacing": spacing,
             "size": size, "xoff": 0.0, "yoff": -0.10},
            {"type": "markers", "marker": ".", "spacing": spacing,
             "size": size, "xoff": -0.09, "yoff": 0.07},
            {"type": "markers", "marker": ".", "spacing": spacing,
             "size": size, "xoff": 0.09, "yoff": 0.07}]


BASIC_SHAPES = {
    # 点
    "小点":   {"type": "markers", "marker": ".", "spacing": 1.0, "size": 1.8},
    "中点":   {"type": "markers", "marker": ".", "spacing": 1.2, "size": 2.6},
    "大点":   {"type": "markers", "marker": ".", "spacing": 1.5, "size": 3.6},
    "密点":   {"type": "markers", "marker": ".", "spacing": 0.7, "size": 1.6},
    "疏点":   {"type": "markers", "marker": ".", "spacing": 2.2, "size": 2.4},
    # 圆
    "实心圆": {"type": "markers", "marker": "o", "spacing": 1.8, "size": 3.4},
    "空心圆": {"type": "markers", "marker": "o", "spacing": 1.8, "size": 3.4,
               "filled": False},
    "小空心圆": {"type": "markers", "marker": "o", "spacing": 1.3, "size": 2.4,
                 "filled": False},
    "大空心圆": {"type": "markers", "marker": "o", "spacing": 2.6, "size": 5.0,
                 "filled": False},
    # 横线
    "横线":   {"type": "lines", "angle": 0, "spacing": 1.0},
    "细密横线": {"type": "lines", "angle": 0, "spacing": 0.6},
    "疏横线": {"type": "lines", "angle": 0, "spacing": 1.8},
    "虚横线": {"type": "lines", "angle": 0, "spacing": 1.0, "dash": 1.4,
               "gap": 1.0},
    "点划横线": {"type": "lines", "angle": 0, "spacing": 1.0, "dash": 2.2,
                 "gap": 1.2, "offset": 0.5},
    # 竖线
    "竖线":   {"type": "lines", "angle": 90, "spacing": 1.0},
    "密竖线": {"type": "lines", "angle": 90, "spacing": 0.6},
    "虚竖线": {"type": "lines", "angle": 90, "spacing": 1.0, "dash": 1.2,
               "gap": 1.0},
    # 斜线
    "右斜线": {"type": "lines", "angle": 45, "spacing": 1.0},
    "左斜线": {"type": "lines", "angle": -45, "spacing": 1.0},
    "缓右斜线": {"type": "lines", "angle": 30, "spacing": 1.0},
    "陡右斜线": {"type": "lines", "angle": 65, "spacing": 1.0},
    "密右斜线": {"type": "lines", "angle": 45, "spacing": 0.6},
    "虚斜线": {"type": "lines", "angle": 45, "spacing": 1.0, "dash": 1.4,
               "gap": 1.0},
    "交叉斜线": [{"type": "lines", "angle": 45, "spacing": 1.2},
                 {"type": "lines", "angle": -45, "spacing": 1.2}],
    "网格":   [{"type": "lines", "angle": 0, "spacing": 1.2},
               {"type": "lines", "angle": 90, "spacing": 1.2}],
    # 记号
    "十字":   {"type": "markers", "marker": "+", "spacing": 1.6, "size": 3.4},
    "叉号":   {"type": "markers", "marker": "x", "spacing": 1.6, "size": 3.0},
    "星号":   {"type": "markers", "marker": "*", "spacing": 1.8, "size": 4.0},
    "上三角": {"type": "markers", "marker": "^", "spacing": 1.8, "size": 3.6},
    "空上三角": {"type": "markers", "marker": "^", "spacing": 1.8, "size": 3.6,
                 "filled": False},
    "下三角": {"type": "markers", "marker": "v", "spacing": 1.8, "size": 3.6},
    "空下三角": {"type": "markers", "marker": "v", "spacing": 1.8, "size": 3.6,
                 "filled": False},
    "实方块": {"type": "markers", "marker": "s", "spacing": 1.8, "size": 3.2},
    "空方块": {"type": "markers", "marker": "s", "spacing": 1.8, "size": 3.2,
               "filled": False},
    "实菱形": {"type": "markers", "marker": "D", "spacing": 1.8, "size": 3.0},
    "空菱形": {"type": "markers", "marker": "D", "spacing": 1.8, "size": 3.0,
               "filled": False},
    "短横":   {"type": "markers", "marker": "_", "spacing": 1.6, "size": 4.0},
    "短竖":   {"type": "markers", "marker": "|", "spacing": 1.4, "size": 4.0},
    "V形":    {"type": "markers", "marker": "v", "spacing": 1.8, "size": 3.4,
               "filled": False},
    "Y形":    {"type": "markers", "marker": "1", "spacing": 1.8, "size": 4.0},
    # 波浪
    "波浪线": {"type": "wave", "spacing": 1.2, "amp": 0.28, "wavelength": 1.4},
    "密波浪线": {"type": "wave", "spacing": 0.8, "amp": 0.2, "wavelength": 1.0},
    "波状双线": [{"type": "wave", "spacing": 1.8, "amp": 0.16, "wavelength": 2.6},
                 {"type": "wave", "spacing": 1.8, "amp": 0.16,
                  "wavelength": 2.6, "yoff": 0.5}],
    "波状虚线": {"type": "wave", "spacing": 1.5, "amp": 0.16,
                 "wavelength": 2.6, "dash": 9, "gap": 2},
    # 砖
    "砖纹":   {"type": "brick", "spacing": 2.0, "ratio": 2.2},
    "斜砖纹": {"type": "brick", "spacing": 2.0, "ratio": 2.2, "slant": 0.7},
    "双线砖纹": {"type": "brick", "spacing": 2.4, "ratio": 2.4, "double": 0.5},
    # GB/T 958 标准图形（毫米制矢量符号）
    "砾石椭圆": {"type": "shape", "shape": "椭圆", "w": 2.0, "h": 1.0,
                 "spacing": 2.2},
    "角砾三角": {"type": "shape", "shape": "三角", "w": 1.5, "h": 1.5,
                 "spacing": 2.2},
    "X形":    {"type": "shape", "shape": "X形", "w": 1.8, "h": 1.8,
               "spacing": 2.4},
    "⊥形":    {"type": "shape", "shape": "倒T形", "w": 1.8, "h": 1.8,
               "spacing": 2.4},
    "⊤形":    {"type": "shape", "shape": "T形", "w": 1.8, "h": 1.8,
               "spacing": 2.4},
    "工形":   {"type": "shape", "shape": "工形", "w": 1.6, "h": 2.0,
               "spacing": 2.5},
    "Γ形":    {"type": "shape", "shape": "Γ形", "w": 1.6, "h": 1.6,
               "spacing": 2.3},
    "人字形": {"type": "shape", "shape": "人字", "w": 1.8, "h": 1.3,
               "spacing": 2.3},
    "V字形":  {"type": "shape", "shape": "V字", "w": 1.7, "h": 1.3,
               "spacing": 2.3},
    "S形":    {"type": "shape", "shape": "S形", "w": 1.2, "h": 2.0,
               "spacing": 2.4},
    "贝壳形": {"type": "shape", "shape": "贝壳形", "w": 1.2, "h": 1.8,
               "spacing": 2.4},
    "梳形":   {"type": "shape", "shape": "梳形", "w": 2.6, "h": 1.4,
               "spacing": 2.6},
    "方框加点": [{"type": "shape", "shape": "方框", "w": 2.0, "h": 2.0,
                  "spacing": 2.8, "stagger": False},
                 {"type": "markers", "marker": ".", "spacing": 2.8,
                  "size": 1.6, "stagger": False}],
    "三点簇": _cluster3(2.6),
    "空白":   [],  # 空行（留白）
}


def _iter_elements(row):
    """把一行内容规范成图元 dict 列表（支持中文名、dict、或它们的列表）。"""
    if isinstance(row, str):
        row = BASIC_SHAPES.get(row.strip(), [])
    if isinstance(row, dict):
        return [row]
    out = []
    for it in (row or []):
        out.extend(_iter_elements(it))
    return out


def _prim_rows(X0, X1, Y0, Y1, heights=None, rows=None, spacing=1.0):
    """行式瓦片：把 rows 各行按 heights 相对厚度纵向叠成一个瓦片，
    再沿整个区域纵向重复平铺；每行内的图形横向重复填满。
    rows[i] 可以是基本图形中文名、图元 dict、或它们的列表。
    spacing 控制瓦片整体高度（越大越高、图案越舒展）。"""
    rows = list(rows or [])
    if not rows:
        return [], []
    heights = list(heights or [1.0] * len(rows))
    if len(heights) < len(rows):
        heights += [1.0] * (len(rows) - len(heights))
    heights = heights[:len(rows)]
    total = sum(heights) or 1.0
    tile = total * spacing * BASE_SPACING
    if tile <= 1e-6:
        return [], []
    segs, marks = [], []
    # 瓦片自区域顶部（Y0）起铺：图例小样与柱内单元格看到的行序一致
    n = math.ceil((Y1 - Y0) / tile)
    for t in range(0, n + 1):
        base = Y0 + t * tile
        yy = base
        for h, row in zip(heights, rows):
            rh = h / total * tile
            ry0, ry1 = yy, yy + rh
            yy = ry1
            if ry1 <= Y0 or ry0 >= Y1:
                continue
            cy0, cy1 = max(ry0, Y0), min(ry1, Y1)
            for el in _iter_elements(row):
                prim = _PRIMS.get(el.get("type"))
                if not prim:
                    continue
                params = {k: v for k, v in el.items()
                          if k not in ("type", "color", "lw",
                                       "motif_x_mm", "motif_y_mm",
                                       "legend_group",
                                       "legend_stagger",
                                       "legend_center_once",
                                       "legend_course_boundary")}
                if (_LEGEND_SWATCH_CONTEXT.get() is not None
                        and el.get("type") in (
                            "markers", "dots", "shape", "ell")
                        and "legend_stagger" in el):
                    params["stagger"] = bool(el["legend_stagger"])
                s, m = prim(X0, X1, cy0, cy1, **params)
                segs += s
                marks += m
    return segs, marks


_PRIMS["rows"] = _prim_rows


def _txt(s, spacing=2.4, size=5.0, stagger=True, **kw):
    """快捷写法：文字符号点阵（Si、Fe、Cu……），用 mathtext 作记号。
    额外关键字（xoff/yoff 等）原样传给 markers 图元。"""
    return {"type": "markers", "marker": rf"$\mathrm{{{s}}}$",
            "spacing": spacing, "size": size, "stagger": stagger, **kw}


# 各图元类型允许的参数：校验自定义样式时用，写错参数名能及早报出来，
# 而不是等绘图时从 matplotlib 深处抛 TypeError。
_PRIM_PARAMS = {
    "lines":   {"angle", "spacing", "dash", "gap", "offset", "phase",
                "origin", "color", "lw"},
    "markers": {"marker", "spacing", "xspacing", "size", "filled", "stagger",
                "xoff", "yoff", "legend_single", "legend_slots",
                "legend_slot_phase", "legend_slot_mask",
                "legend_slot_period", "legend_group",
                "legend_stagger",
                "motif_x_mm", "motif_y_mm",
                "legend_center_once", "color"},
    "dots":    {"marker", "spacing", "xspacing", "size", "filled", "stagger",
                "xoff", "yoff", "legend_single", "legend_slots",
                "legend_slot_phase", "legend_slot_mask",
                "legend_slot_period", "legend_group",
                "legend_stagger",
                "motif_x_mm", "motif_y_mm",
                "legend_center_once", "color"},
    "brick":   {"spacing", "ratio", "slant", "double", "jdouble", "jshort",
                "color", "lw"},
    "ell":     {"spacing", "xspacing", "size", "stagger", "xoff",
                "legend_single", "legend_slots", "legend_slot_phase",
                "legend_slot_mask", "legend_slot_period", "legend_group",
                "legend_stagger",
                "motif_x_mm", "color", "lw"},
    "wave":    {"spacing", "amp", "wavelength", "dash", "gap", "yoff",
                "color", "lw"},
    "shape":   {"shape", "w", "h", "spacing", "xspacing", "stagger", "tilt",
                "bold", "xoff", "yoff", "legend_single", "legend_slots",
                "legend_slot_phase", "legend_slot_mask",
                "legend_slot_period", "legend_group",
                "legend_stagger",
                "motif_x_mm", "motif_y_mm",
                "legend_center_once", "legend_course_boundary", "color", "lw"},
    "rows":    {"heights", "rows", "spacing"},
}

# 声明式花纹最终会转成 numpy 网格和大量 matplotlib artist。这里是核心
# 层的最后一道边界：CLI、桌面端和服务端都必须得到同样的校验结果，不能
# 只依赖某个入口在外面补一层检查。
_SPEC_MAX_ELEMENTS = 500
_SPEC_MAX_ROWS = 100
_SPEC_MAX_DEPTH = 16
_SPEC_MAX_TEXT = 64

_SPEC_POSITIVE = {
    "spacing": (0.0, 100.0),
    "w": (0.0, 50.0),
    "h": (0.0, 50.0),
    "size": (0.0, 100.0),
    "ratio": (0.0, 100.0),
    "wavelength": (0.0, 100.0),
    "legend_slots": (0.0, 20.0),
}
_SPEC_NONNEGATIVE = {
    "dash": (0.0, 100.0),
    "gap": (0.0, 100.0),
    "amp": (0.0, 100.0),
    "double": (0.0, 50.0),
    "jdouble": (0.0, 50.0),
    "lw": (0.0, 20.0),
}
_SPEC_SIGNED = {
    "angle": (-3600.0, 3600.0),
    "offset": (-100.0, 100.0),
    "phase": (-100.0, 100.0),
    "origin": (-100.0, 100.0),
    "slant": (-100.0, 100.0),
    "tilt": (-3600.0, 3600.0),
    "xoff": (-100.0, 100.0),
    "yoff": (-100.0, 100.0),
    "legend_slot_phase": (-20.0, 20.0),
    "motif_x_mm": (-50.0, 50.0),
    "motif_y_mm": (-50.0, 50.0),
}


def _spec_number(value, field, limits, *, allow_zero=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 应是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} 应是有限数字")
    lo, hi = limits
    if not allow_zero and number == 0:
        raise ValueError(f"{field} 应大于 0")
    if not lo <= number <= hi:
        qualifier = "大于 0 且" if not allow_zero and lo == 0 else ""
        raise ValueError(f"{field} 应{qualifier}在 {lo:g}–{hi:g} 之间")
    return number


def normalize_spec(spec, where="花纹"):
    """把用户写的花纹 spec 规范成图元 dict 列表，并做参数校验。

    允许的写法：单个图元 dict、基本图形中文名（str）、以及它们的
    任意嵌套列表。校验失败抛 ValueError，消息带上下文（where），
    使样式文件写错时能得到明确指引，而不是绘图时的一句 TypeError。
    """
    out = []
    budget = [_SPEC_MAX_ELEMENTS]

    def add(el, location=where, depth=0, destination=None):
        if destination is None:
            destination = out
        if depth > _SPEC_MAX_DEPTH:
            raise ValueError(f"{location}：嵌套不能超过 {_SPEC_MAX_DEPTH} 层")
        if isinstance(el, str):
            name = el.strip()
            if name not in BASIC_SHAPES:
                raise ValueError(
                    f"{location}：未知的基本图形“{name}”。可在“花纹库 › 基本"
                    f"图形一览”查看全部可用名称。")
            add(BASIC_SHAPES[name], location, depth + 1, destination)
            return
        if isinstance(el, (list, tuple)):
            if len(el) > _SPEC_MAX_ELEMENTS:
                raise ValueError(
                    f"{location}：图元不能超过 {_SPEC_MAX_ELEMENTS} 个")
            for it in el:
                add(it, location, depth + 1, destination)
            return
        if not isinstance(el, dict):
            raise ValueError(f"{location}：图元应是对象（字典）或基本图形名，"
                             f"不是 {type(el).__name__}")
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError(
                f"{where}：图元总数不能超过 {_SPEC_MAX_ELEMENTS} 个")
        typ = el.get("type")
        if typ not in _PRIM_PARAMS:
            raise ValueError(f"{location}：未知图元类型“{typ}”，可用类型："
                             + "、".join(sorted(_PRIM_PARAMS)))
        bad = set(el) - _PRIM_PARAMS[typ] - {"type"}
        if bad:
            raise ValueError(
                f"{location}：图元“{typ}”不支持参数 " + "、".join(sorted(bad))
                + "（可用：" + "、".join(sorted(_PRIM_PARAMS[typ])) + "）")
        if typ == "shape" and el.get("shape") not in _SHAPES:
            raise ValueError(f"{location}：未知图形“{el.get('shape')}”，可用："
                             + "、".join(_SHAPES))
        if "color" in el:
            import matplotlib.colors as _mc
            if not _mc.is_color_like(el["color"]):
                raise ValueError(f"{location}：图元颜色“{el['color']}”无效")

        for key, value in el.items():
            field = f"{location} 的 {key}"
            if key in ("legend_slots", "legend_slot_phase",
                       "legend_slot_mask", "legend_slot_period",
                       "legend_group"):
                if key in ("legend_slots", "legend_slot_period"):
                    limits = (1.0, 20.0)
                elif key == "legend_slot_mask":
                    limits = (0.0, float((1 << 60) - 1))
                elif key == "legend_group":
                    limits = (1.0, 1000.0)
                else:
                    limits = (-20.0, 20.0)
                number = _spec_number(value, field, limits,
                                      allow_zero=(key not in (
                                          "legend_slots", "legend_slot_period",
                                          "legend_group")))
                if number != int(number):
                    raise ValueError(f"{field} 应是整数")
            elif key in _SPEC_POSITIVE:
                _spec_number(value, field, _SPEC_POSITIVE[key],
                             allow_zero=False)
            elif key == "xspacing":
                # 0 是现有 API 中“与 spacing 相同”的显式写法。
                _spec_number(value, field, (0.0, 100.0))
            elif key in _SPEC_NONNEGATIVE:
                _spec_number(value, field, _SPEC_NONNEGATIVE[key])
            elif key in _SPEC_SIGNED:
                _spec_number(value, field, _SPEC_SIGNED[key])
            elif key == "jshort":
                _spec_number(value, field, (0.0, 1.0))
            elif key in ("filled", "stagger", "bold", "legend_single",
                         "legend_stagger",
                         "legend_center_once", "legend_course_boundary"):
                if not isinstance(value, bool):
                    raise ValueError(f"{field} 应是布尔值")
            elif key in ("marker", "shape"):
                if (not isinstance(value, str) or not value
                        or len(value) > _SPEC_MAX_TEXT):
                    raise ValueError(
                        f"{field} 应是 1–{_SPEC_MAX_TEXT} 个字符的文本")

        if "legend_slot_mask" in el:
            mask = int(el["legend_slot_mask"])
            if mask <= 0:
                raise ValueError(
                    f"{location} 的 legend_slot_mask 必须是非零占位掩码")
            if "legend_slots" not in el:
                raise ValueError(
                    f"{location} 使用 legend_slot_mask 时必须同时指定 "
                    "legend_slots")
            slots = int(el["legend_slots"])
            period = int(el.get("legend_slot_period", 3))
            if mask >= (1 << (slots * period)):
                raise ValueError(
                    f"{location} 的 legend_slot_mask 超出 "
                    f"{slots} 列 × {period} 行矩阵范围")

        if typ == "lines" and el.get("dash", 0) > 0:
            if el.get("dash", 0) + el.get("gap", 0) <= 0:
                raise ValueError(f"{location}：dash 与 gap 不能形成零步长")
        if typ == "rows":
            rows = el.get("rows") or []
            if not isinstance(rows, (list, tuple)):
                raise ValueError(f"{location}：rows 应是行列表")
            if len(rows) > _SPEC_MAX_ROWS:
                raise ValueError(
                    f"{location}：rows 不能超过 {_SPEC_MAX_ROWS} 行")
            hs = el.get("heights")
            if hs is not None:
                if not isinstance(hs, (list, tuple)):
                    raise ValueError(f"{location}：rows 的 heights 应是数字列表")
                if len(hs) > _SPEC_MAX_ROWS:
                    raise ValueError(
                        f"{location}：heights 不能超过 {_SPEC_MAX_ROWS} 项")
                for index, height in enumerate(hs, 1):
                    _spec_number(height, f"{location} 的 heights[{index}]",
                                 (0.0, 100.0), allow_zero=False)
            normalized_rows = []
            for index, row in enumerate(rows, 1):
                normalized_row = []
                add(row, f"{location} 的第 {index} 行", depth + 1,
                    normalized_row)
                normalized_rows.append(normalized_row)
            normalized = copy.deepcopy(el)
            normalized["rows"] = normalized_rows
            if hs is not None:
                normalized["heights"] = list(hs)
        else:
            normalized = copy.deepcopy(el)
        destination.append(normalized)

    add(spec)
    return out


def _layer_carrier_spacing(el):
    """返回会产生可见层带的图元纵向节距倍率。

    纯竖线只控制横向密度，不是“一层”；显式 rows 花纹的各行
    高度由用户定义，也不改写。
    """
    if not isinstance(el, dict):
        return None
    typ = el.get("type")
    if typ == "lines":
        angle = float(el.get("angle", 0.0)) % 180.0
        if math.isclose(angle, 90.0, abs_tol=1e-9):
            dash = float(el.get("dash", 0.0))
            gap = float(el.get("gap", 0.0))
            return dash + gap if dash > 0 and dash + gap > 0 else None
        # spacing 是斜线族的法向间距；除以 cos(角度)
        # 才是固定 x 位置上看到的纵向层高。
        cosine = abs(math.cos(math.radians(angle)))
        if cosine <= 1e-9:
            return None
        return float(el.get("spacing", 1.0)) / cosine
    if typ in ("brick", "wave"):
        return float(el.get("spacing", 1.0))
    return None


def _is_dense_horizontal_texture(el):
    """页岩式密集横线是连续纹理，不是可数的“代表层”。"""
    if not isinstance(el, dict) or el.get("type") != "lines":
        return False
    angle = float(el.get("angle", 0.0)) % 180.0
    return (math.isclose(angle, 0.0, abs_tol=1e-9)
            and float(el.get("spacing", 1.0)) <= 1.0
            and float(el.get("dash", 0.0)) <= 0.0)


def _repeat_spacing(el):
    """返回图元纵向重复节距倍率；显式 rows 由用户定义，不参与。"""
    carrier = _layer_carrier_spacing(el)
    if carrier is not None:
        return carrier
    if isinstance(el, dict) and el.get("type") in (
            "markers", "dots", "ell", "shape"):
        return float(el.get("spacing", 1.0))
    return None


def _fixed_layer_row_spec(spec, row_height_mm=PATTERN_ROW_HEIGHT_MM,
                          preserve_dense=False):
    """把一幅标准层状花纹的基础层高统一为指定毫米值。

    主图保留复合花纹各图元的原始纵向比例。图例模式则把所有离散
    markers/shape 锚到同一组代表层中心；标准规定的 1:3、1:2 等数量
    关系由横向 ``xspacing`` 表达，不能靠漏画某个水平层带来表达。
    行内横向间距始终保持原值。
    """
    carriers = [
        value for el in spec
        for value in [_repeat_spacing(el)]
        if (value is not None and value > 0
            and not (preserve_dense and _is_dense_horizontal_texture(el)))
    ]
    if not carriers:
        return copy.deepcopy(spec)
    # spec 的首个层带图元是主图元；其余稀疏符号保持与它的
    # 原始 1:n 重复关系。
    row_height_in = resolve_pattern_row_height_mm(row_height_mm) / 25.4
    factor = (row_height_in / BASE_SPACING) / carriers[0]
    out = copy.deepcopy(spec)
    for el in out:
        if not isinstance(el, dict):
            continue
        if preserve_dense and _is_dense_horizontal_texture(el):
            continue
        typ = el.get("type")
        if typ == "lines":
            angle = float(el.get("angle", 0.0)) % 180.0
            if math.isclose(angle, 90.0, abs_tol=1e-9):
                if float(el.get("dash", 0.0)) > 0:
                    el["dash"] = float(el.get("dash", 0.0)) * factor
                    el["gap"] = float(el.get("gap", 0.0)) * factor
                    el["phase"] = float(el.get("phase", 0.0)) * factor
                continue
        elif typ not in ("markers", "dots", "brick", "ell", "wave",
                          "shape"):
            continue
        original = float(el.get("spacing", 1.0))
        if typ in ("markers", "dots", "shape", "ell") \
                and not el.get("xspacing"):
            el["xspacing"] = original
        if preserve_dense and typ in ("markers", "dots", "shape", "ell"):
            el["spacing"] = row_height_in / BASE_SPACING
        else:
            el["spacing"] = original * factor
    return out


def _centered_pattern_phase(height, pitch):
    """返回上下对称的剩余相位，并消除浮点整倍数取模误差。"""
    if pitch <= 1e-9 or height <= 0:
        return 0.0
    ratio = height / pitch
    if math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-9):
        return 0.0
    remainder = height - math.floor(ratio) * pitch
    if math.isclose(remainder, pitch, rel_tol=0.0, abs_tol=1e-9):
        remainder = 0.0
    return remainder / 2


def _alternating_symbol_spacings(spec):
    """Find symbol pitches whose elements alternate on half-row phases.

    Granite-family patterns, for example, combine one element at ``yoff=0``
    with another at ``yoff=0.5``.  In the standard plate these occupy rows
    1/3 and row 2 respectively; treating both as independent arrays creates
    five apparent rows and puts the last symbol on the frame.
    """
    zero_phase = set()
    half_phase = set()
    for element in spec or []:
        if (not isinstance(element, dict)
                or element.get("type") not in ("markers", "dots", "shape")):
            continue
        spacing = round(float(element.get("spacing", 1.0)), 12)
        phase = float(element.get("yoff", 0.0)) % 1.0
        if math.isclose(phase, 0.0, abs_tol=1e-9):
            zero_phase.add(spacing)
        elif math.isclose(phase, 0.5, abs_tol=1e-9):
            half_phase.add(spacing)
    return zero_phase & half_phase


def _legend_element_bounds(element):
    """Return physical ``(xmin, xmax, ymin, ymax)`` around a draw anchor."""
    typ = element.get("type")
    if typ in ("markers", "dots"):
        radius = max(0.0, float(element.get("size", 2.0))) / 144.0
        radius += 0.55 / 144.0 + _LEGEND_SYMBOL_CLEARANCE_IN
        return -radius, radius, -radius, radius
    if typ == "ell":
        radius = float(element.get("size", 0.32)) * BASE_SPACING / 2
        radius += _LEGEND_SYMBOL_CLEARANCE_IN
        return -radius, radius, -radius, radius
    if typ != "shape":
        return 0.0, 0.0, 0.0, 0.0
    polys = _SHAPES.get(element.get("shape", "椭圆")) or []
    if not polys:
        return 0.0, 0.0, 0.0, 0.0
    wx = float(element.get("w", 2.0)) * _MM
    hy = float(element.get("h", 1.0)) * _MM
    angle = math.radians(float(element.get("tilt", 0.0)))
    ct, st = math.cos(angle), math.sin(angle)
    flip = -1.0 if _SHAPE_FLIP[0] else 1.0
    points = []
    for poly in polys:
        for px, py in poly:
            px, py = px * wx, py * hy * flip
            points.append((px * ct - py * st, px * st + py * ct))
    if element.get("bold"):
        bold_offset = 0.1 * _MM
        points += [(x + bold_offset, y + bold_offset) for x, y in points]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    clearance = _LEGEND_SYMBOL_CLEARANCE_IN
    return (min(xs) - clearance, max(xs) + clearance,
            min(ys) - clearance, max(ys) + clearance)


def _legend_element_x_bounds(element):
    """Return a symbol element's physical x bounds around its grid origin."""
    xmin, xmax, _ymin, _ymax = _legend_element_bounds(element)
    return xmin, xmax


def _legend_stagger_value(element):
    """Return swatch-only staggering without changing the chart pattern."""
    return bool(element.get(
        "legend_stagger", element.get("stagger", True)))


def _legend_single_motif_anchors(spec, width, height):
    """Return one shared, centred anchor for every discrete motif component."""
    components = []
    for index, element in enumerate(spec or []):
        if (not isinstance(element, dict)
                or element.get("type") not in (
                    "markers", "dots", "shape", "ell")):
            continue
        spacing = float(element.get("spacing", 1.0))
        xspacing = float(element.get("xspacing") or spacing)
        xoff = (float(element["motif_x_mm"]) * _MM
                if "motif_x_mm" in element
                else (float(element.get("xoff", 0.0))
                      * xspacing * BASE_SPACING))
        yoff = (float(element["motif_y_mm"]) * _MM
                if "motif_y_mm" in element
                else (float(element.get("yoff", 0.0))
                      * spacing * BASE_SPACING))
        xmin, xmax, ymin, ymax = _legend_element_bounds(element)
        components.append((
            index, xoff, yoff,
            xmin + xoff, xmax + xoff,
            ymin + yoff, ymax + yoff,
        ))
    if not components:
        return {}
    motif_xmin = min(item[3] for item in components)
    motif_xmax = max(item[4] for item in components)
    motif_ymin = min(item[5] for item in components)
    motif_ymax = max(item[6] for item in components)
    base_x = width / 2 - (motif_xmin + motif_xmax) / 2
    base_y = height / 2 - (motif_ymin + motif_ymax) / 2
    return {
        index: (base_x + xoff, base_y + yoff)
        for index, xoff, yoff, *_bounds in components
    }


def _legend_symbol_group_bounds(spec):
    """Return shared bounds for multi-primitive symbols in a legend.

    A quartz three-dot sign or an oolite circle-plus-dot is built from
    several primitives.  Filtering those primitives independently leaves
    stray dots or half a cluster at the frame.  Elements on the same grid
    whose phases are within a quarter cell are therefore clipped as one
    physical glyph.
    """
    # Some standards allocate several different symbol families to one
    # explicit four-column matrix.  ``legend_group`` identifies primitives
    # that make one physical token in that matrix (for example the four-dot
    # volcaniclastics sign).  Their internal offsets must be bounded as a
    # whole before a slot is accepted near the frame.
    explicit_groups = {}
    explicit_indices = set()
    for index, element in enumerate(spec or []):
        if (not isinstance(element, dict)
                or element.get("type") not in (
                    "markers", "dots", "shape", "ell")
                or "legend_group" not in element):
            continue
        spacing = float(element.get("spacing", 1.0))
        xspacing = float(element.get("xspacing") or spacing)
        offset = (float(element["motif_x_mm"]) * _MM
                  if "motif_x_mm" in element
                  else float(element.get("xoff", 0.0))
                  * xspacing * BASE_SPACING)
        explicit_groups.setdefault(int(element["legend_group"]), []).append(
            (index, offset, element))
        explicit_indices.add(index)

    result = {}
    for entries in explicit_groups.values():
        bounds = []
        for _index, offset, element in entries:
            left, right = _legend_element_x_bounds(element)
            bounds.append((offset + left, offset + right))
        group_bounds = (min(item[0] for item in bounds),
                        max(item[1] for item in bounds))
        for index, _offset, _element in entries:
            result[index] = group_bounds

    grids = {}
    for index, element in enumerate(spec or []):
        if (not isinstance(element, dict)
                or element.get("type") not in (
                    "markers", "dots", "shape", "ell")
                or index in explicit_indices):
            continue
        spacing = float(element.get("spacing", 1.0))
        xspacing = float(element.get("xspacing") or spacing)
        stagger = _legend_stagger_value(element)
        key = (round(xspacing, 12), round(spacing, 12), stagger)
        grids.setdefault(key, []).append((
            index, float(element.get("xoff", 0.0)), element))

    for (xspacing, _spacing, _stagger), entries in grids.items():
        entries.sort(key=lambda item: item[1])
        clusters = []
        for entry in entries:
            if (not clusters
                    or entry[1] - clusters[-1][-1][1] > 0.250000001):
                clusters.append([entry])
            else:
                clusters[-1].append(entry)
        step = xspacing * BASE_SPACING
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            bounds = []
            for _index, phase, element in cluster:
                left, right = _legend_element_x_bounds(element)
                bounds.append((phase * step + left,
                               phase * step + right))
            group_bounds = (min(item[0] for item in bounds),
                            max(item[1] for item in bounds))
            for index, _phase, _element in cluster:
                result[index] = group_bounds
    return result


def build_spec_pattern(spec, *, fixed_layer_rows=False):
    """把声明式花纹 spec（图元字典列表）编译成与内置花纹同签名的函数。

    花纹以目标多边形包围盒为局部坐标：层状/砖格式花纹保持固定物理节距，
    并把不足一个完整节距的余量对称分配到上下边界，避免每层底部出现厚度
    不一的窄残行。符号宽高和线宽不随多边形高度缩放。
    """
    source_spec = normalize_spec(spec)
    has_layer_carrier = any(
        _layer_carrier_spacing(el) is not None for el in source_spec
    )
    has_repeat = any(
        (_repeat_spacing(el) or 0) > 0 for el in source_spec
    )
    has_explicit_rows = any(
        isinstance(el, dict) and el.get("type") == "rows"
        for el in source_spec
    )
    uses_fixed_rows = bool(fixed_layer_rows and has_layer_carrier)
    # 小样还需要处理只含点阵/符号阵的国标花纹；用户显式
    # rows 花纹保留其语义行，不强制归一。
    uses_legend_rows = bool(
        fixed_layer_rows and has_repeat and not has_explicit_rows)

    @lru_cache(maxsize=32)
    def effective_variant(row_height_mm, swatch_height_mm=0.0,
                          swatch_rows=0):
        if swatch_height_mm and swatch_rows:
            if uses_legend_rows:
                target_mm = swatch_height_mm / swatch_rows
                swatch_spec = _fixed_layer_row_spec(
                    source_spec, target_mm, preserve_dense=True)
                return swatch_spec, _vertical_pitch_in(swatch_spec)
            # 用户显式 rows/非标准花纹在小样中使用声明时的
            # 原始 spec，不得意外继承主图的可调层高。
            return source_spec, _vertical_pitch_in(source_spec)
        if uses_fixed_rows:
            return (_fixed_layer_row_spec(source_spec, row_height_mm),
                    row_height_mm / 25.4)
        return source_spec, _vertical_pitch_in(source_spec)

    default_spec, _default_pitch = effective_variant(PATTERN_ROW_HEIGHT_MM)

    def fn(x0, x1, y0, y1, dx, dy):
        # 在真正绘制时读取请求级参数，避免模块导入阶段把 2.5 mm 永久
        # 捕获进全局 PATTERNS。有限缓存避免同一张图的每层重复换算。
        row_height_mm = current_pattern_row_height_mm()
        swatch = _LEGEND_SWATCH_CONTEXT.get()
        if swatch is None:
            active_spec, vertical_pitch = effective_variant(row_height_mm)
        else:
            active_spec, vertical_pitch = effective_variant(
                row_height_mm, swatch[0], swatch[1])
        sx, sy = dx / BASE_SPACING, dy / BASE_SPACING   # 单位英寸对应的数据跨度
        X0, Y0 = x0 / sx, y0 / sy                       # 包围盒原点（英寸）
        W, H = (x1 - x0) / sx, (y1 - y0) / sy           # 包围盒尺寸（英寸）
        single_motif = bool(
            swatch is not None and _LEGEND_SINGLE_MOTIF_CONTEXT.get())
        single_motif_anchors = (
            _legend_single_motif_anchors(active_spec, W, H)
            if single_motif else {})
        phase_y = _centered_pattern_phase(H, vertical_pitch)
        # 复合页岩小样要同时保留密线的标准节距，又让 Ca/Si/C
        # 等三行修饰符号独立居中。两组共用密线相位会令符号
        # 上下边距不对称；主图则仍共用相位以保持原有组合关系。
        dense_swatch = bool(
            swatch is not None
            and any(_is_dense_horizontal_texture(el) for el in active_spec))
        regular_spec = [
            el for el in active_spec
            if not _is_dense_horizontal_texture(el)
        ]
        regular_phase_y = _centered_pattern_phase(
            H, _vertical_pitch_in(regular_spec)) if regular_spec else 0.0
        canonical_symbol_rows = bool(swatch is not None and uses_legend_rows)
        alternating_spacings = (
            _alternating_symbol_spacings(active_spec)
            if canonical_symbol_rows else set())
        brick_element = next((
            element for element in active_spec
            if isinstance(element, dict) and element.get("type") == "brick"
        ), None)
        brick_cell_spacing = (
            float(brick_element.get("ratio", 2.2))
            if brick_element is not None else None)
        symbol_group_bounds = (
            _legend_symbol_group_bounds(active_spec)
            if swatch is not None else {})
        legend_phase_shift = 0.0
        if canonical_symbol_rows and brick_cell_spacing is None:
            for index, element in enumerate(active_spec):
                if (not isinstance(element, dict)
                        or element.get("type") not in (
                            "markers", "dots", "shape", "ell")
                        or not _legend_stagger_value(element)
                        or not math.isclose(
                            float(element.get("xoff", 0.0)) % 1.0,
                            0.0, abs_tol=1e-9)):
                    continue
                xstep = float(
                    element.get("xspacing")
                    or element.get("spacing", 1.0)) * BASE_SPACING
                bounds = symbol_group_bounds.get(
                    index, _legend_element_x_bounds(element))
                half_width = (bounds[1] - bounds[0]) / 2
                count_integer = len(_legend_lattice_positions(
                    0.0, W, xstep, phase=0.0,
                    lower_margin=half_width, upper_margin=half_width))
                count_half = len(_legend_lattice_positions(
                    0.0, W, xstep, phase=0.5,
                    lower_margin=half_width, upper_margin=half_width))
                # Table 4 uses the denser row at the top and bottom and the
                # complementary sparse row in the middle.  Depending on the
                # glyph width and pitch, that denser phase may be integer or
                # half-integer; choose it from the actual physical bounds.
                if count_half > count_integer:
                    legend_phase_shift = 0.5
                break
        all_segs, all_marks, all_csegs = [], [], []
        for element_index, el in enumerate(active_spec):
            prim = _PRIMS.get(el.get("type"))
            if not prim:
                continue
            typ = el.get("type")
            color = el.get("color")
            lw = el.get("lw")
            params = {k: v for k, v in el.items()
                      if k not in ("type", "color", "lw",
                                   "motif_x_mm", "motif_y_mm",
                                   "legend_group",
                                   "legend_stagger",
                                   "legend_center_once",
                                   "legend_course_boundary")}
            if (swatch is not None
                    and typ in ("markers", "dots", "shape", "ell")
                    and "legend_stagger" in el):
                params["stagger"] = bool(el["legend_stagger"])
            if single_motif:
                if element_index in single_motif_anchors:
                    params["_legend_single_anchor"] = (
                        single_motif_anchors[element_index])
                elif typ == "lines":
                    params["_legend_single_anchor"] = (W / 2, H / 2)
            elif swatch is not None and el.get("legend_center_once"):
                xmin, xmax, ymin, ymax = _legend_element_bounds(el)
                params["_legend_single_anchor"] = (
                    W / 2 - (xmin + xmax) / 2,
                    H / 2 - (ymin + ymax) / 2,
                )
            if (canonical_symbol_rows and brick_cell_spacing is not None
                    and typ == "shape"
                    and el.get("legend_course_boundary")):
                xmin, xmax, ymin, ymax = _legend_element_bounds(el)
                pitch = brick_cell_spacing * BASE_SPACING
                row_count = max(1, int(swatch[1]))
                positions = []
                for boundary in range(1, row_count):
                    phase = 0.5 if boundary % 2 else 0.0
                    anchors = _legend_lattice_anchors(
                        0.0, W, pitch, phase, (xmin, xmax))
                    visual_boundary = (
                        row_count - boundary
                        if _SHAPE_FLIP[0] else boundary)
                    visual_y = H * visual_boundary / row_count
                    anchor_y = visual_y - (ymin + ymax) / 2
                    positions.extend((anchor_x, anchor_y)
                                     for anchor_x in anchors)
                params["_legend_explicit_positions"] = positions
            # All legend symbol grids share a centred horizontal origin.
            # Canonical built-in patterns additionally centre their vertical
            # rows and collapse half-row alternation to the three standard
            # representative bands.  Explicit user ``rows`` retain their
            # original vertical semantics.
            if swatch is not None and typ in (
                    "markers", "dots", "shape", "ell", "brick"):
                params["_legend_center_x"] = True
                if (brick_cell_spacing is None
                        and element_index in symbol_group_bounds):
                    params["_legend_group_x_bounds"] = (
                        symbol_group_bounds[element_index])
                    if ("legend_group" in el
                            and "motif_x_mm" in el):
                        xspacing = float(
                            el.get("xspacing") or el.get("spacing", 1.0))
                        params["xoff"] = (
                            float(el["motif_x_mm"]) * _MM
                            / (xspacing * BASE_SPACING))
            if (canonical_symbol_rows and brick_cell_spacing is not None
                    and typ in ("markers", "dots", "shape", "ell")):
                # Brick modifiers use the brick's own cell lattice.  Generic
                # qualifier spacing otherwise puts Fe/Si/C directly on the
                # vertical joints.  Cell centres are phase 0 in courses 1/3
                # and phase 1/2 in course 2, i.e. the standard 1-2-1 layout.
                params["xspacing"] = brick_cell_spacing
                params["xoff"] = 0.0
                params["stagger"] = True
            if canonical_symbol_rows and typ in (
                    "markers", "dots", "shape", "ell"):
                params["_legend_center_y"] = True
                params["_legend_phase_shift"] = legend_phase_shift
                spacing_key = round(float(el.get("spacing", 1.0)), 12)
                if spacing_key in alternating_spacings:
                    yphase = float(el.get("yoff", 0.0)) % 1.0
                    params["_legend_alternate_y"] = (
                        1 if math.isclose(yphase, 0.5, abs_tol=1e-9)
                        else 0)
            segs, marks = prim(0.0, W, 0.0, H, **params)
            element_phase_y = phase_y
            if dense_swatch:
                if _is_dense_horizontal_texture(el):
                    element_phase_y = _centered_pattern_phase(
                        H, _vertical_pitch_in([el]))
                else:
                    element_phase_y = regular_phase_y
            if canonical_symbol_rows and typ in (
                    "markers", "dots", "shape", "ell"):
                # These primitives have already centred and clipped their
                # physical glyph rows; applying the carrier phase a second
                # time would move them off the band centres.
                element_phase_y = 0.0
            if single_motif:
                # Motif anchors are already absolute within the 15×10 mm
                # sample; a pattern-repeat phase would displace the symbol.
                element_phase_y = 0.0
            out = [[((a[0] + X0) * sx,
                     (a[1] + element_phase_y + Y0) * sy),
                    ((b[0] + X0) * sx,
                     (b[1] + element_phase_y + Y0) * sy)]
                   for a, b in segs]
            if color or lw:
                all_csegs.append((color, lw, out))
            else:
                all_segs += out
            for mx, my, mk, fl, *rest in marks:
                sz = rest[0] if rest else 2.0
                all_marks.append(([(x + X0) * sx for x in mx],
                                  [(y + element_phase_y + Y0) * sy for y in my],
                                  mk, fl, sz,
                                  color))
        return all_segs, all_marks, all_csegs
    # 对外保留声明时的 spec，避免标准组合花纹再次编译时把已换算过的
    # spacing 当作新的源参数；effective_spec 供调试和物理尺度测试查看。
    fn.spec = source_spec
    fn.effective_spec = default_spec
    fn.effective_spec_for = lambda value: effective_variant(
        resolve_pattern_row_height_mm(value))[0]
    fn.source_spec = source_spec
    fn.fixed_layer_rows = uses_fixed_rows
    fn.legend_fixed_rows = uses_legend_rows
    fn.legend_spec_for = lambda height_mm=10.0, rows=3: effective_variant(
        PATTERN_ROW_HEIGHT_MM, float(height_mm), int(rows))[0]
    return fn


def _tile_h_in(spec):
    """spec 里行式瓦片（rows）的最大瓦片高（英寸）；无 rows 时为 0。"""
    h = 0.0
    for el in spec or []:
        if isinstance(el, dict) and el.get("type") == "rows":
            rows = el.get("rows") or []
            heights = list(el.get("heights") or [])
            heights += [1.0] * (len(rows) - len(heights))
            total = sum(heights[:len(rows)]) or 1.0
            h = max(h, total * el.get("spacing", 1.0) * BASE_SPACING)
    return h


def _row_pitch(spec):
    """spec 的层带节距（间距倍率）：层理式水平线（origin=0）或砖形
    的 spacing 取最大者；无层带结构时为 0。"""
    r = 0.0
    for el in spec or []:
        if not isinstance(el, dict):
            continue
        t = el.get("type")
        if (t == "lines" and el.get("origin", 0.5) == 0
                and not el.get("angle", 0)):
            r = max(r, el.get("spacing", 1.0))
        elif t == "brick":
            r = max(r, el.get("spacing", 2.2))
    return r


def _vertical_pitch_in(spec):
    """用于单元格上下余量居中的主要纵向重复节距（英寸）。"""
    tile = _tile_h_in(spec)
    if tile > 0:
        return tile
    pitch = 0.0
    for el in spec or []:
        if not isinstance(el, dict):
            continue
        typ = el.get("type")
        if typ == "lines" and el.get("angle", 0) % 180 != 0:
            continue
        if typ in ("lines", "markers", "dots", "brick", "ell", "wave",
                   "shape"):
            value = float(el.get("spacing", 1.0))
            pitch = value if pitch <= 0 else min(pitch, value)
    return pitch * BASE_SPACING


# 内置花纹：按 GB/T 958—2015 表 4/表 5 的图例画法与制图参数（毫米）定义。
# 沉积岩横向交错排列、松散堆积物纵向要素、变质岩横向波状线（附录 A.1.3），
# 粒级尺寸按表 4 制图参数（如砾岩 2×1、巨砾岩 3×1.5、角砾 1.5×1.5）。
_BUILTIN_SPECS = {
    # ===== 沉积岩：碎屑岩 =====
    # 砂土点阵：主图保留原 0.8 倍节距，图例按 3 行代表单元展示。
    "dots":       [{"type": "markers", "marker": ".", "spacing": 0.8,
                    "xspacing": 0.8, "size": 2.0}],
    # 成岩碎屑岩图例均带水平层理线，层间符号隔层错半格（表 4 图例画法）。
    # 层理线 origin=0（线在整数倍位置），符号行天然落在层间正中，
    # 任意高度的样框/单元格里层带都完整，不出现半行。
    # 层带高度统一取 1.6（标准图例样框各岩性层带等高，粒度差异只
    # 体现在符号大小上），行内符号间距用 xspacing 单独控制。
    # 砾岩=层理线+扁椭圆 2×1（RPSE021007）；巨/中/细砾按 3×1.5 / 2×1 / 1×0.5
    "gravel":     [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "shape", "shape": "椭圆", "w": 2.0, "h": 1.0,
                    "spacing": 1.6, "xspacing": 2.2}],
    "congl_huge": [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "shape", "shape": "椭圆", "w": 3.0, "h": 1.4,
                    "spacing": 1.6, "xspacing": 2.6}],
    "congl_m":    [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "shape", "shape": "椭圆", "w": 2.0, "h": 1.0,
                    "spacing": 1.6, "xspacing": 2.2}],
    "congl_f":    [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "shape", "shape": "椭圆", "w": 1.0, "h": 0.5,
                    "spacing": 1.6, "xspacing": 1.3}],
    # 砂砾岩=层理线+椭圆与点相间（RPSE021014，点、圆间距 3）
    "sandy_congl": [{"type": "lines", "angle": 0, "spacing": 1.6,
                     "origin": 0},
                    {"type": "shape", "shape": "椭圆", "w": 1.6, "h": 0.8,
                     "spacing": 1.6, "xspacing": 2.6},
                    {"type": "markers", "marker": ".", "spacing": 1.6,
                     "xspacing": 2.6, "size": 1.8, "xoff": 0.5}],
    # 角砾岩=层理线+空心三角 1.5×1.5（RPSE021001）
    "breccia":    [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "shape", "shape": "三角", "w": 1.5, "h": 1.5,
                    "spacing": 1.6, "xspacing": 2.0}],
    # 砂岩=层理线+点（RPSE021021）；点径按附录 A 表 A.1：
    # 粗砂 0.8、中砂 0.6、细砂 0.4、粉砂 0.25 mm（1 mm≈2.835 磅）
    "sst_gb":     [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 1.6, "size": 1.7}],
    "sst_c":      [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 1.6, "size": 2.3}],
    "sst_m":      [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 1.6, "size": 1.7}],
    "sst_f":      [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 1.6, "size": 1.15}],
    # 石英砂岩=层理线+点与∴相间（RPSE021026）；长石砂岩=点与N相间
    # （021027，附录 A.2.3 注 3：长石与砂符号 1:1）
    "sst_qz":     [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 2.4, "size": 1.7, "xoff": 0.5}]
                  + [dict(e, xspacing=2.4)
                     for e in _cluster3(1.6, 1.7)],
    "sst_fsp":    [{"type": "lines", "angle": 0, "spacing": 1.6, "origin": 0},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 2.4, "size": 1.7, "xoff": 0.5,
                    "legend_slots": 4, "legend_slot_phase": 1},
                   {"type": "shape", "shape": "N形", "w": 1.4, "h": 1.8,
                    "spacing": 1.6, "xspacing": 2.4,
                    "legend_slots": 4, "legend_slot_phase": 0}],
    # 粉砂岩=层理线+成对细点“‥”（RPSE021042，点径 0.25 mm）
    "siltstone_gb": [{"type": "lines", "angle": 0, "spacing": 1.6,
                      "origin": 0},
                     {"type": "markers", "marker": ".", "spacing": 1.6,
                      "xspacing": 1.8, "size": 0.9, "xoff": -0.09},
                     {"type": "markers", "marker": ".", "spacing": 1.6,
                      "xspacing": 1.8, "size": 0.9, "xoff": 0.09}],
    # 泥岩=层理线+层间短横线（RPSE021064）
    "mudstone_gb": [{"type": "lines", "angle": 0, "spacing": 1.6,
                     "origin": 0},
                    {"type": "shape", "shape": "横条", "w": 2.0, "h": 0.2,
                     "spacing": 1.6, "xspacing": 2.0}],
    # 页岩=细密横线（RPSE021051）
    # 图例中 10 mm 高小样约显示 8–9 条密线，与表 4 RPSE021051
    # 图版接近；这是连续底纹，不归一为 3 个代表层。
    "shale_gb":   [{"type": "lines", "angle": 0, "spacing": 0.5}],
    # 油页岩=页岩线+左空右实矩形 3×1（RPSE021063）
    "oilshale":   [{"type": "lines", "angle": 0, "spacing": 0.9},
                   {"type": "shape", "shape": "油页符", "w": 3.0, "h": 1.0,
                    "spacing": 2.7, "xspacing": 3.0}],
    # ===== 碳酸盐岩 =====
    # 白云岩=层面线+通高双线斜节理（RPSE021111，双线间距 1）
    "dolomite_gb": [{"type": "brick", "spacing": 2.2, "ratio": 3.4,
                     "slant": 0.55, "jdouble": 0.9}],
    # 泥灰岩=层面线+通高单斜节理（RPSE021110）
    "marl_gb":    [{"type": "brick", "spacing": 2.2, "ratio": 2.6,
                    "slant": 0.55}],
    # 灰岩=宽砖形（RPSE021072，砖宽约为层高 3 倍）
    "ls_gb":      [{"type": "brick", "spacing": 2.0, "ratio": 3.2}],
    # 结晶灰岩=砖+空心菱形（RPSE021080），符号对齐砖格、隔层错半砖
    "ls_cryst":   [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   {"type": "shape", "shape": "菱形", "w": 1.4, "h": 1.4,
                    "spacing": 2.0, "xspacing": 3.2}],
    # 生物（屑）灰岩=砖+e（RPSE021085）
    "bio_ls":     [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   _txt("e", 2.0, 4.4, xspacing=3.2)],
    # 鲕状灰岩=砖+小圆内实心点（RPSE021087）
    "oolitic_ls": [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   {"type": "markers", "marker": "o", "spacing": 2.0,
                    "xspacing": 3.2, "size": 2.8, "filled": False},
                   {"type": "markers", "marker": ".", "spacing": 2.0,
                    "xspacing": 3.2, "size": 1.2}],
    # 礁灰岩=砖+同心双拱（RPSE021093）
    "ls_reef":    [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   {"type": "shape", "shape": "拱形", "w": 2.0, "h": 1.2,
                    "spacing": 2.0, "xspacing": 3.2}],
    # 竹叶状灰岩=砖+斜置叶状椭圆（RPSE021107）
    "ls_bamboo":  [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   {"type": "shape", "shape": "椭圆", "w": 1.8, "h": 0.6,
                    "spacing": 2.0, "xspacing": 3.2, "tilt": 20}],
    # 含燧石结核灰岩=砖+实心黑透镜体 3×1（RPSE021105）
    "ls_chert":   [{"type": "brick", "spacing": 2.0, "ratio": 3.2},
                   {"type": "shape", "shape": "实心透镜", "w": 2.8, "h": 1.0,
                    "spacing": 2.0, "xspacing": 3.2, "bold": True,
                    "legend_course_boundary": True}],
    # 条带状泥灰岩=泥灰岩+波折线（参 RPSE021106 条带状灰岩）
    "marl_band":  [{"type": "brick", "spacing": 2.2, "ratio": 2.6,
                    "slant": 0.55},
                   {"type": "shape", "shape": "波折线", "w": 2.0, "h": 0.6,
                    "spacing": 2.2, "xspacing": 3.4}],
    # ===== 化学岩、生物岩（表 5 矿物符号）=====
    # 石膏=梳形符 3×1.5（MPCS190007）；岩盐=方框加点 3×3（MPCS190012）
    "gypsum":     [{"type": "shape", "shape": "梳形", "w": 2.6, "h": 1.4,
                    "spacing": 2.1}],
    "halite":     [{"type": "shape", "shape": "方框", "w": 2.0, "h": 2.0,
                    "spacing": 2.3, "stagger": False},
                   {"type": "markers", "marker": ".", "spacing": 2.3,
                    "size": 1.6, "stagger": False}],
    # 硅质岩/燧石=波线加斜杠（MPCS190019）
    "chert":      [{"type": "shape", "shape": "燧石符", "w": 2.4, "h": 1.2,
                    "spacing": 1.9}],
    # 白垩=宽工形 3×1（MPCS020001 白垩土）
    "chalk":      [{"type": "shape", "shape": "工形", "w": 2.6, "h": 1.0,
                    "spacing": 2.1}],
    # ===== 松散堆积物（纵向要素，4.4.2.2；点径按表 A.1 粒级）=====
    "sand_c":     [{"type": "markers", "marker": ".", "spacing": 1.5, "size": 2.3}],
    "sand_m":     [{"type": "markers", "marker": ".", "spacing": 1.4, "size": 1.7}],
    "sand_f":     [{"type": "markers", "marker": ".", "spacing": 1.2, "size": 1.15}],
    "silt_loose": [{"type": "markers", "marker": ".", "spacing": 1.0, "size": 0.75}],
    # 黄土=竖直细线（RPSE022013）；红土=竖虚线密排（RPSE022014）
    "loess":      [{"type": "lines", "angle": 90, "spacing": 1.5}],
    "redsoil":    [{"type": "lines", "angle": 90, "spacing": 0.9, "dash": 1.2,
                    "gap": 0.6, "offset": 0.5}],
    # 黏土=竖短线 长2间2（RPSE022015）
    "clay_gb":    [{"type": "lines", "angle": 90, "spacing": 1.7, "dash": 0.9,
                    "gap": 0.9, "offset": 0.5}],
    # 淤泥=S 形 2×1（RPSE022020）
    "mud_gb":     [{"type": "shape", "shape": "S形", "w": 1.2, "h": 2.0,
                    "spacing": 1.9}],
    # 粉土（亚砂土）=成对细点“‥”与竖短线相间（RPSE022033）
    "silty_soil": [{"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 2.2, "size": 1.2, "xoff": -0.07},
                   {"type": "markers", "marker": ".", "spacing": 1.6,
                    "xspacing": 2.2, "size": 1.2, "xoff": 0.07},
                   {"type": "shape", "shape": "竖条", "w": 0.5, "h": 1.4,
                    "spacing": 1.6, "xspacing": 2.2, "stagger": False,
                    "xoff": 0.5}],
    # 填土（人工堆积）=人字+小椭圆（RPSE022029）
    "fill_soil":  [{"type": "shape", "shape": "人字", "w": 1.6, "h": 1.4,
                    "spacing": 2.0},
                   {"type": "shape", "shape": "椭圆", "w": 0.8, "h": 1.2,
                    "spacing": 2.0, "stagger": False, "xoff": 0.5}],
    # 泥炭土=粗黑人字编织纹（RPSE022022，粗线宽 0.5）：密排粗折线
    # 加等波长的粗竖线，形似编席
    "peat":       [{"type": "wave", "spacing": 0.55, "amp": 0.14,
                    "wavelength": 3.2, "lw": 1.5},
                   {"type": "lines", "angle": 90, "spacing": 3.2,
                    "lw": 1.5}],
    # 贝壳层=螺形贝壳符（RPSE022027）
    "shellbed":   [{"type": "shape", "shape": "螺形", "w": 1.4, "h": 1.4,
                    "spacing": 1.9}],
    # 冰碛（砾岩）=斜置椭圆（RPSE021020）
    "till":       [{"type": "shape", "shape": "椭圆", "w": 1.8, "h": 0.9,
                    "spacing": 1.9, "tilt": 25}],
    # 漂砾=不规则大砾块（RPSE022002）
    "boulder":    [{"type": "shape", "shape": "集块符", "w": 3.2, "h": 2.2,
                    "spacing": 2.8, "bold": True}],
    # 碎石土（角砾+砂）：三角+点（RPSE022006 角砾）
    "debris_soil": [{"type": "shape", "shape": "三角", "w": 1.5, "h": 1.5,
                     "spacing": 1.9},
                    {"type": "markers", "marker": ".", "spacing": 1.9,
                     "size": 1.6, "stagger": False, "xoff": 0.5}],
    # ===== 岩浆岩（4.4.3，矿物符号规律组合）=====
    # 花岗岩=长石十字与细十字隔行相间（RPMM014002）；粒级尺寸按
    # 表 A.1：中粒 2、粗粒 3、巨粒（伟晶）4、细粒（细晶）1.5 mm
    "granite":    [{"type": "shape", "shape": "长石十字", "w": 2.0, "h": 2.0,
                    "spacing": 3.8, "xspacing": 1.9, "stagger": False},
                   {"type": "shape", "shape": "十字", "w": 1.8, "h": 1.8,
                    "spacing": 3.8, "xspacing": 1.9, "stagger": False,
                    "xoff": 0.5, "yoff": 0.5}],
    "pegmatite":  [{"type": "shape", "shape": "十字", "w": 4.0, "h": 4.0,
                    "spacing": 3.4}],
    "aplite":     [{"type": "shape", "shape": "十字", "w": 1.5, "h": 1.5,
                    "spacing": 1.6}],
    # 花岗斑岩式：粗斑+细基质十字相间（RPMM014003）——通用"斑岩"；
    # 斑晶规格按表 A.1：粗斑 3×0.5（粗线宽 0.5 mm≈1.4 磅）
    "porphyry":   [{"type": "shape", "shape": "十字", "w": 3.0, "h": 3.0,
                    "spacing": 2.4, "lw": 1.4},
                   {"type": "shape", "shape": "十字", "w": 1.5, "h": 1.5,
                    "spacing": 2.4, "stagger": False, "xoff": 0.5}],
    # 闪长岩=⊥（RPMM013002）；正长岩=⊤（RPMM013016）
    "diorite":    [{"type": "shape", "shape": "倒T形", "w": 1.8, "h": 1.8,
                    "spacing": 1.9}],
    "syenite":    [{"type": "shape", "shape": "T形", "w": 1.8, "h": 1.8,
                    "spacing": 1.9}],
    # 花岗闪长岩=⊥与+相间 1:1（RPMM014017）
    "granodiorite": [{"type": "shape", "shape": "倒T形", "w": 1.8, "h": 1.8,
                      "spacing": 2.0},
                     {"type": "shape", "shape": "十字", "w": 1.8, "h": 1.8,
                      "spacing": 2.0, "stagger": False, "xoff": 0.5}],
    # 二长岩=两个方向的短双斜线（RPMM013013）
    "monzonite":  [{"type": "shape", "shape": "双斜线", "w": 1.8, "h": 1.4,
                    "spacing": 2.0},
                   {"type": "shape", "shape": "双斜线", "w": 1.8, "h": 1.4,
                    "spacing": 2.0, "tilt": 90, "stagger": False, "xoff": 0.5}],
    # 斜长岩=N（RPMM012002）
    "anorthosite": [{"type": "shape", "shape": "N形", "w": 1.5, "h": 1.9,
                     "spacing": 2.0}],
    # 辉长岩=X（RPMM012005）；辉绿岩=粗斜 X（RPMM012009）
    "gabbro":     [{"type": "shape", "shape": "X形", "w": 1.8, "h": 1.8,
                    "spacing": 1.9}],
    "diabase":    [{"type": "shape", "shape": "X形", "w": 1.8, "h": 1.8,
                    "spacing": 1.9, "tilt": 20, "bold": True}],
    # 玢岩=λ 形符号（RPMM012012）
    "porphyrite": [_txt(r"\lambda", 1.9, 4.8)],
    # 超基性：橄榄岩=∧（RPMM011002）；纯橄榄岩=粗 ∧（RPMM011004）
    "peridotite": [{"type": "shape", "shape": "人字", "w": 1.8, "h": 1.3,
                    "spacing": 1.8}],
    "dunite":     [{"type": "shape", "shape": "人字", "w": 1.8, "h": 1.3,
                    "spacing": 1.8, "bold": True}],
    # 金伯利岩=空心三角与粗人字相间（RPMM011005）
    "kimberlite": [{"type": "shape", "shape": "三角", "w": 1.4, "h": 1.4,
                    "spacing": 1.9},
                   {"type": "shape", "shape": "人字", "w": 1.6, "h": 1.2,
                    "spacing": 1.9, "bold": True, "stagger": False,
                    "xoff": 0.5}],
    # 辉石岩=左箭头符（RPMM011011）；角闪石岩=<（RPMM011018）
    "pyroxenite": [{"type": "shape", "shape": "左箭头", "w": 2.0, "h": 1.2,
                    "spacing": 1.9}],
    "hornblendite": [_txt("<", 1.7, 4.8)],
    # 煌斑岩=粗 L 形（RPMM016001）；碳酸岩=C（RPMM017001）
    "lamprophyre": [{"type": "shape", "shape": "L形", "w": 1.6, "h": 1.6,
                     "spacing": 1.9, "bold": True}],
    "carbonatite": [_txt("C", 1.8, 4.6)],
    # 苦橄岩=上箭头（RPMM021002/022003）
    "picrite":    [{"type": "shape", "shape": "上箭头", "w": 1.2, "h": 2.0,
                    "spacing": 1.9}],
    # ===== 火山岩（熔岩）=====
    # 玄武岩=Γ 形（RPMM022002）；安山岩=∨（RPMM023004）
    "basalt":     [{"type": "shape", "shape": "Γ形", "w": 1.6, "h": 1.6,
                    "spacing": 1.8}],
    "andesite":   [{"type": "shape", "shape": "V字", "w": 1.7, "h": 1.3,
                    "spacing": 1.8}],
    # 流纹岩=斜十字与短斜线相间（RPMM024003）
    "rhyolite":   [{"type": "shape", "shape": "X形", "w": 1.6, "h": 1.6,
                    "spacing": 1.9, "tilt": 20},
                   {"type": "shape", "shape": "斜线", "w": 1.2, "h": 1.2,
                    "spacing": 1.9, "stagger": False, "xoff": 0.5}],
    # 英安岩=∨与斜线相间（RPMM023009）
    "dacite":     [{"type": "shape", "shape": "V字", "w": 1.6, "h": 1.2,
                    "spacing": 1.9},
                   {"type": "shape", "shape": "斜线", "w": 1.4, "h": 1.4,
                    "spacing": 1.9, "stagger": False, "xoff": 0.5}],
    # 粗面岩=工形（RPMM025002）；响岩=π（RPMM025010）
    "trachyte":   [{"type": "shape", "shape": "工形", "w": 1.6, "h": 2.0,
                    "spacing": 2.0}],
    "phonolite":  [_txt(r"\pi", 1.9, 5.0)],
    # 珍珠岩=三个相切实心圆组成的三叶簇（RPMM024010）
    "perlite":    [{"type": "markers", "marker": "o", "spacing": 2.1,
                    "size": 2.4, "xoff": 0.0, "yoff": -0.09},
                   {"type": "markers", "marker": "o", "spacing": 2.1,
                    "size": 2.4, "xoff": -0.085, "yoff": 0.06},
                   {"type": "markers", "marker": "o", "spacing": 2.1,
                    "size": 2.4, "xoff": 0.085, "yoff": 0.06}],
    # 黑曜岩=曲臂弧加下垂短柄（RPMM024012）
    "obsidian":   [{"type": "shape", "shape": "黑曜符", "w": 1.6, "h": 1.6,
                    "spacing": 1.9}],
    # 浮岩=圆加尾线（RPMM024013，圆径1.2 短线1.5）
    "pumice":     [{"type": "shape", "shape": "圆加尾", "w": 1.2, "h": 1.2,
                    "spacing": 1.8}],
    # 凝灰岩：按附录 A.2.4 a)，火山熔岩化学成分符号与火山碎屑符号
    # （密集双点“∶”）按 1:3 组成；成分未指明时化学符号取 ∨（安山质）
    "tuff":       [_txt(":", 1.4, 3.6),
                   {"type": "shape", "shape": "V字", "w": 1.4, "h": 1.1,
                    "spacing": 2.8, "xoff": 0.5}],
    # —— 第四纪堆积物成因类型（覆盖物）——
    "q_alluvial": [{"type": "markers", "marker": "o", "spacing": 1.9,
                    "size": 2.6, "filled": False},
                   {"type": "markers", "marker": ".", "spacing": 1.9,
                    "size": 1.6, "stagger": False}],
    "q_pluvial":  [{"type": "markers", "marker": "o", "spacing": 2.5,
                    "size": 3.6, "filled": False},
                   {"type": "markers", "marker": ".", "spacing": 1.25,
                    "size": 1.8}],
    "q_alpl":     [{"type": "markers", "marker": "o", "spacing": 2.1,
                    "size": 3.0, "filled": False},
                   {"type": "markers", "marker": ".", "spacing": 1.05,
                    "size": 1.5, "stagger": False}],
    "q_slope":    [{"type": "markers", "marker": "^", "spacing": 2.0,
                    "size": 2.9}],
    "q_eluvial":  [{"type": "markers", "marker": "^", "spacing": 1.8,
                    "size": 3.0, "filled": False}],
    "q_elslope":  [{"type": "markers", "marker": "^", "spacing": 2.3,
                    "size": 3.0, "filled": False},
                   {"type": "markers", "marker": "^", "spacing": 2.3,
                    "size": 2.5, "stagger": False}],
    "q_colluvial": [{"type": "markers", "marker": "^", "spacing": 2.7,
                     "size": 4.8}],
    "q_aeolian":  [{"type": "markers", "marker": ".", "spacing": 0.8,
                    "size": 1.4},
                   {"type": "lines", "angle": 0, "spacing": 2.8, "dash": 1.8,
                    "gap": 2.4}],
    "q_lacustrine": [{"type": "lines", "angle": 0, "spacing": 1.5,
                      "dash": 1.8, "gap": 1.4},
                     {"type": "markers", "marker": "|", "spacing": 2.5,
                      "size": 2.6}],
    "q_marine":   [{"type": "lines", "angle": 0, "spacing": 1.3}],
    "q_swamp":    [{"type": "markers", "marker": "|", "spacing": 1.25,
                    "size": 3.4},
                   {"type": "lines", "angle": 0, "spacing": 2.3, "dash": 1.3,
                    "gap": 1.9}],
    "q_glacioflu": [{"type": "markers", "marker": "o", "spacing": 2.1,
                     "size": 2.8, "filled": False},
                    {"type": "markers", "marker": ".", "spacing": 1.4,
                     "size": 1.6, "stagger": False}],
    "q_chemical": [{"type": "markers", "marker": "x", "spacing": 2.1,
                    "size": 2.8},
                   {"type": "markers", "marker": ".", "spacing": 2.1,
                    "size": 1.6, "stagger": False}],
    # ===== 变质岩（4.4.4，横向波状线体系，附录 A.1.3）=====
    # 板岩=粗细波状双线成对（RPMR011001，上粗下细，双线间距 1）
    "slate":      [{"type": "wave", "spacing": 1.9, "amp": 0.16,
                    "wavelength": 2.6, "lw": 1.1},
                   {"type": "wave", "spacing": 1.9, "amp": 0.16,
                    "wavelength": 2.6, "yoff": 0.9}],
    # 千枚岩=波状虚线（RPMR011021，实 2.5 虚 0.5）
    "phyllite":   [{"type": "wave", "spacing": 1.5, "amp": 0.16,
                    "wavelength": 2.6, "dash": 10, "gap": 2}],
    # 片岩=波状实线（RPMR011041）
    "schist":     [{"type": "wave", "spacing": 1.5, "amp": 0.2,
                    "wavelength": 3.0}],
    # 片麻岩=波状实线与波状虚线相间（RPMR011094）
    "gneiss":     [{"type": "wave", "spacing": 2.2, "amp": 0.18,
                    "wavelength": 2.8},
                   {"type": "wave", "spacing": 2.2, "amp": 0.18,
                    # 固定 2.5 mm 基础层高的一半：虚、实波线隔层相间。
                    "wavelength": 2.8, "yoff": 1.25, "dash": 7, "gap": 4}],
    # 石英岩=波状线+细点散布（RPMR011536）
    "quartzite":  [{"type": "wave", "spacing": 2.2, "amp": 0.14,
                    "wavelength": 2.8},
                   {"type": "markers", "marker": ".", "spacing": 1.5,
                    "size": 1.4, "yoff": 0.5}],
    # 大理岩=砖形层理+双线竖节理（RPMR011191，双线间距 0.5）
    "marble":     [{"type": "brick", "spacing": 2.2, "ratio": 3.0,
                    "jdouble": 0.5}],
    # 麻粒岩=波状虚线+细点（RPMR011131）
    "granulite":  [{"type": "wave", "spacing": 1.9, "amp": 0.16,
                    "wavelength": 2.8, "dash": 7, "gap": 4},
                   {"type": "markers", "marker": ".", "spacing": 2.6,
                    "size": 1.3, "stagger": False}],
    # 变粒岩=波状线+短横（RPMR011152）；浅粒岩=波状线（RPMR011151）
    "leptynite":  [{"type": "wave", "spacing": 2.0, "amp": 0.16,
                    "wavelength": 2.8},
                   {"type": "markers", "marker": "_", "spacing": 2.8,
                    "size": 3.0, "stagger": False}],
    "leuco":      [{"type": "wave", "spacing": 1.3, "amp": 0.14,
                    "wavelength": 2.8}],
    # 榴辉岩=波状线+⊙与短箭头相间（RPMR011231，圆径 1.5）
    "eclogite":   [{"type": "wave", "spacing": 2.4, "amp": 0.16,
                    "wavelength": 2.8},
                   {"type": "shape", "shape": "椭圆", "w": 1.5, "h": 1.5,
                    "spacing": 2.9},
                   {"type": "markers", "marker": ".", "spacing": 2.9,
                    "size": 1.4},
                   {"type": "shape", "shape": "左箭头", "w": 1.6, "h": 0.9,
                    "spacing": 2.9, "stagger": False, "xoff": 0.5}],
    # 蛇纹岩=蛇纹石符（⌣加中竖，RPAR011035）
    "serpentinite": [{"type": "shape", "shape": "蛇纹符", "w": 1.8, "h": 1.4,
                     "spacing": 1.9}],
    # 角岩=波状线与单向斜线相交成网（RPMR011251）
    "hornfels":   [{"type": "wave", "spacing": 1.9, "amp": 0.14,
                    "wavelength": 3.2},
                   {"type": "lines", "angle": 35, "spacing": 1.9}],
    # 矽卡岩=45° 斜网格（RPMR011527）
    "skarn":      [{"type": "lines", "angle": 45, "spacing": 1.5},
                   {"type": "lines", "angle": -45, "spacing": 1.5}],
    # ===== 构造岩（4.4.6）=====
    # 糜棱岩=斜点划线（RPTC011010，线距 2）：长划与点两层错相叠成
    "mylonite":   [{"type": "lines", "angle": 30, "spacing": 1.4, "dash": 2.2,
                    "gap": 1.4},
                   {"type": "lines", "angle": 30, "spacing": 1.4, "dash": 0.25,
                    "gap": 3.35, "phase": 2.85}],
    # 碎裂岩=直角三角（RPTC011003）
    "cataclasite": [{"type": "shape", "shape": "直角三角", "w": 1.6,
                     "h": 1.4, "spacing": 1.9}],
    # 压碎（构造）角砾岩=实心三角（RPTC011001）
    "tect_breccia": [{"type": "markers", "marker": "^", "spacing": 1.9,
                      "size": 3.6}],
    # 混合岩=粗短波状条痕（RPMI011001，0.5×2 条痕）；混合花岗岩=条痕+十字
    "migmatite":  [{"type": "shape", "shape": "波折线", "w": 2.0, "h": 0.7,
                    "spacing": 1.8, "lw": 1.1}],
    "mig_granite": [{"type": "shape", "shape": "波折线", "w": 2.0, "h": 0.7,
                     "spacing": 2.0, "lw": 1.1},
                    {"type": "shape", "shape": "十字", "w": 1.6, "h": 1.6,
                     "spacing": 2.0, "stagger": False, "xoff": 0.5}],
    # ===== 松散堆积物补充 =====
    # 化学堆积=三竖线簇（RPSE022030，间距宽）
    "chem_dep":   [{"type": "shape", "shape": "三竖", "w": 1.4, "h": 1.6,
                    "spacing": 2.6}],
    # 冰水堆积=竖椭圆与竖线相间（RPSE022023）
    "glaciofluvial": [{"type": "shape", "shape": "椭圆", "w": 1.0, "h": 1.5,
                       "spacing": 2.1},
                      {"type": "shape", "shape": "竖条", "w": 0.5, "h": 1.5,
                       "spacing": 2.1, "stagger": False, "xoff": 0.5}],
    # 沼泽（植物）堆积=下箭头与竖短线相间（RPSE022028 植物堆积层）
    "swamp_dep":  [{"type": "shape", "shape": "下箭头", "w": 1.0, "h": 1.8,
                    "spacing": 2.2},
                   {"type": "shape", "shape": "竖条", "w": 0.5, "h": 1.6,
                    "spacing": 2.2, "stagger": False, "xoff": 0.5}],
}
for _name, _spec in _BUILTIN_SPECS.items():
    PATTERNS[_name] = build_spec_pattern(_spec, fixed_layer_rows=True)


# ---------------------------------------------------------------------------
# 成分/结构修饰符（GB/T 958 组合体系）：岩性名 = 修饰词 + 基础岩性时，
# 自动在基础花纹上叠加修饰符号，如"硅质页岩"＝页岩＋Si、"钙质砂岩"＝
# 砂岩＋∟、"含砾砂岩"＝砂岩＋小圆。匹配按表内顺序，长词优先。
# ---------------------------------------------------------------------------

_QUALIFIERS = (
    ("粉砂质", [{"type": "markers", "marker": ".", "spacing": 1.2, "size": 1.5}]),
    # 白云质=双斜线（RPSE021102 白云质灰岩的 // 符号）
    ("白云质", [{"type": "shape", "shape": "双斜线", "w": 1.4, "h": 1.2,
                 "spacing": 2.8}]),
    ("凝灰质", [_txt(":", 2.0, 4.5)]),
    ("生物碎屑", [_txt("e", 2.4, 4.5)]),
    ("砂质",   [{"type": "markers", "marker": ".", "spacing": 1.6, "size": 2.0}]),
    ("泥质",   [{"type": "lines", "angle": 0, "spacing": 1.6,
                 "dash": 1.1, "gap": 2.2, "offset": 0.5}]),
    # GB/T 958—2015：钙质用 Ca 注记（RPSE021004 等），非旧标准的 ∟
    ("钙质",   [_txt("Ca", 2.6, 4.4)]),
    ("硅质",   [_txt("Si", 2.4)]),
    ("碳质",   [_txt("C", 2.2)]),
    ("铁质",   [_txt("Fe", 2.4)]),
    ("海绿石", [_txt("Gl", 2.6)]),
    # 有机质=粗竖条（RPSE022018）
    ("有机质", [{"type": "shape", "shape": "竖条", "w": 0.6, "h": 1.8,
                 "spacing": 2.6, "bold": True}]),
    # 含砾=稀疏扁椭圆（RPSE021022 含砾砂岩；表 A.2：含××附加:基本=1:3）
    ("含砾",   [{"type": "shape", "shape": "椭圆", "w": 2.0, "h": 1.0,
                 "spacing": 2.8}]),
    ("含砂",   [{"type": "markers", "marker": ".", "spacing": 2.8, "size": 1.7}]),
    # 含指示元素：稀疏表示（附录 A.2.3 b，附加:基本=1:3）
    ("含铜",   [_txt("Cu", 3.0)]),
    ("含磷",   [_txt("P", 3.0)]),
    ("含锰",   [_txt("Mn", 3.0)]),
    ("含钾",   [_txt("K", 3.0)]),
    # 含油=短粗竖条（RPSE021041 含油砂岩，粗线宽 0.5 线长 2）
    ("含油",   [{"type": "shape", "shape": "竖条", "w": 0.6, "h": 2.0,
                 "spacing": 3.0, "bold": True}]),
    ("铝土",   [_txt("Al", 2.4)]),
    # 构造附加花纹与基本花纹 1:1 相间（附录 A.2.2）：角砾状、砾状、鲕状
    ("角砾状", [{"type": "shape", "shape": "三角", "w": 1.5, "h": 1.5,
                 "spacing": 2.0}]),
    ("砾状",   [{"type": "shape", "shape": "椭圆", "w": 2.0, "h": 1.0,
                 "spacing": 2.0}]),
    ("鲕状",   [{"type": "markers", "marker": ".", "spacing": 2.0, "size": 2.4}]),
    ("条带状", [{"type": "lines", "angle": 0, "spacing": 2.6}]),
    ("瘤状",   [{"type": "markers", "marker": "o", "spacing": 2.2, "size": 2.6}]),
)


def _norm3(res):
    """统一花纹函数返回值为 (segs, marks, csegs) 三元组。"""
    return (res[0], res[1], res[2] if len(res) > 2 else [])


_COMPOSITE_DISCRETE_TYPES = frozenset(("markers", "dots", "shape", "ell"))


def _composite_discrete_families(spec):
    """Return ordered semantic families from an existing legend spec.

    New GB patterns identify a family with ``legend_group``.  Older built-in
    two-component patterns use complementary ``legend_slot_phase`` values,
    while multi-primitive glyphs such as the quartz three-dot sign share one
    bounded lattice.  Recognising all three forms prevents a later modifier
    from collapsing an already-composite base into one overlapping token.
    """
    spec = list(spec or [])
    bounds = _legend_symbol_group_bounds(spec)
    keyed = {}
    for index, element in enumerate(spec):
        if (not isinstance(element, dict)
                or element.get("type") not in _COMPOSITE_DISCRETE_TYPES):
            continue
        if "legend_group" in element:
            key = ("group", int(element["legend_group"]))
        elif int(element.get("legend_slot_mask", 0)):
            key = (
                "mask", int(element.get("legend_slots", 0)),
                int(element["legend_slot_mask"]),
            )
        elif int(element.get("legend_slots", 0)):
            key = (
                "phase", int(element["legend_slots"]),
                int(element.get("legend_slot_phase", 0)),
            )
        elif index in bounds:
            spacing = round(float(element.get("spacing", 1.0)), 12)
            xspacing = round(float(
                element.get("xspacing") or spacing), 12)
            left, right = bounds[index]
            key = (
                "motif", spacing, xspacing,
                _legend_stagger_value(element),
                round(left, 12), round(right, 12),
            )
        else:
            key = ("element", index)
        keyed.setdefault(key, []).append(index)
    return sorted((tuple(indices) for indices in keyed.values()),
                  key=lambda indices: indices[0])


def _composite_legend_masks(family_count, qualifier):
    """Build one shared three-row matrix for every semantic family.

    GB/T 958 appendix A.2.3 assigns one common lattice to the subject and
    additional symbols.  A quality modifier (``××质``) is 2:1, a contained
    constituent (``含××``) is 3:1, and the remaining two-component forms are
    1:1.  For three or more constituents, the subject keeps two slots and
    every secondary family gets one (2:1:1, 2:1:1:1, ...).
    """
    count = max(2, int(family_count))
    if count > 2:
        # Family 0 is the subject/base.  Existing secondary constituents keep
        # source order; the newly added modifier is the final family.
        top = (1, 0, *range(2, count), 0)
        slots = count + 1
    elif qualifier.startswith("含"):
        slots = 4
        top = (1, 0, 0, 0)
    elif qualifier.endswith("质"):
        slots = 3
        top = (1, 0, 0)
    else:
        slots = 4
        top = (1, 0, 1, 0)
    rows = (top, tuple(reversed(top)), top)
    masks = [0] * count
    for row_index, row in enumerate(rows):
        for slot_index, family in enumerate(row):
            masks[family] |= 1 << (row_index * slots + slot_index)
    return slots, masks


def _tag_composite_legend_families(spec, families, *, slots, masks,
                                   first_group=1):
    """Retag semantic families while preserving each motif's components."""
    tagged = copy.deepcopy(list(spec or []))
    for family_index, indices in enumerate(families):
        for index in indices:
            element = tagged[index]
            element.update({
                "legend_slots": slots,
                "legend_slot_mask": masks[family_index],
                "legend_slot_period": LEGEND_REPRESENTATIVE_ROWS,
                "legend_group": first_group + family_index,
            })
            element.pop("legend_slot_phase", None)
    return tagged


def _composite_pattern(base_patt, overlay_spec, qualifier=""):
    base_fn = PATTERNS.get(base_patt) if base_patt else None
    ov_fn = build_spec_pattern(overlay_spec, fixed_layer_rows=True)
    base_spec = list(getattr(base_fn, "source_spec", []) or [])
    overlay_source_spec = list(ov_fn.source_spec)
    base_families = _composite_discrete_families(base_spec)
    overlay_families = _composite_discrete_families(overlay_source_spec)
    if base_families and overlay_families:
        family_count = len(base_families) + len(overlay_families)
        slots, masks = _composite_legend_masks(family_count, qualifier)
        legend_spec = _tag_composite_legend_families(
            base_spec, base_families, slots=slots,
            masks=masks[:len(base_families)])
        legend_spec += _tag_composite_legend_families(
            overlay_source_spec, overlay_families, slots=slots,
            masks=masks[len(base_families):],
            first_group=1 + len(base_families))
    else:
        legend_spec = copy.deepcopy(base_spec + overlay_source_spec)
    legend_fn = build_spec_pattern(legend_spec, fixed_layer_rows=True)

    def fn(x0, x1, y0, y1, dx, dy):
        if _LEGEND_SWATCH_CONTEXT.get() is not None:
            return legend_fn(x0, x1, y0, y1, dx, dy)
        segs, marks, csegs = [], [], []
        if base_fn:
            segs, marks, csegs = _norm3(base_fn(x0, x1, y0, y1, dx, dy))
        s2, m2, c2 = _norm3(ov_fn(x0, x1, y0, y1, dx, dy))
        return (list(segs) + list(s2), list(marks) + list(m2),
                list(csegs) + list(c2))
    # Expose the actual legend declaration so catalogue audits and subsequent
    # combinations retain the ratio metadata instead of recompiling two
    # unrelated grids.  The normal chart path above deliberately preserves
    # the original full-area textures.
    fn.spec = legend_fn.spec
    fn.source_spec = legend_fn.source_spec
    fn.effective_spec = legend_fn.effective_spec
    fn.effective_spec_for = legend_fn.effective_spec_for
    fn.fixed_layer_rows = legend_fn.fixed_layer_rows
    fn.legend_fixed_rows = legend_fn.legend_fixed_rows
    fn.legend_spec_for = legend_fn.legend_spec_for
    fn.auto_composite = True
    return fn


def _try_composite(name):
    """Resolve every modifier and build a deterministic nested combination.

    A long registered/alias base wins over its shorter suffix (for example
    ``生物碎屑灰岩`` must keep its dedicated pattern).  All remaining
    qualifier tokens are then consumed in source order and applied from the
    base outward.  This makes a long-running web service independent of which
    related name happened to be requested first and prevents silently
    dropping the second modifier in names such as ``钙质硅质泥岩``.
    """
    candidates = []
    for base in LITHOLOGY:
        if not base or base == name:
            continue
        start = name.rfind(base)
        if start >= 0:
            candidates.append((len(base), 1, start, start,
                               start + len(base), base, 0))
    for alias_index, (key, std) in enumerate(_ALIAS_ORDER):
        if not key or std not in LITHOLOGY:
            continue
        start = name.rfind(key)
        if start >= 0:
            # Registered names win an equal-length tie; alias order resolves
            # equal aliases exactly as the public fallback resolver does.
            candidates.append((len(key), 0, start, start,
                               start + len(key), std, -alias_index))
    if not candidates:
        return None
    winner = max(candidates)
    base_start, base_end, base = winner[3], winner[4], winner[5]
    remainder = name[:base_start] + name[base_end:]

    qualifier_table = sorted(_QUALIFIERS, key=lambda item: len(item[0]),
                             reverse=True)
    qualifiers = []
    index = 0
    while index < len(remainder):
        matched = next((item for item in qualifier_table
                        if remainder.startswith(item[0], index)), None)
        if matched is None:
            index += 1       # colour/grain-size prose is descriptive only
            continue
        qualifiers.append(matched)
        index += len(matched[0])
    if not qualifiers:
        return None

    current_base = base
    for keyword, overlay_spec in reversed(qualifiers):
        std_name = keyword + current_base
        if std_name not in LITHOLOGY:
            face, pattern_name = LITHOLOGY[current_base]
            composite_name = f"{current_base}+{keyword}"
            PATTERNS[composite_name] = _composite_pattern(
                pattern_name, overlay_spec, keyword)
            LITHOLOGY[std_name] = (face, composite_name)
        current_base = std_name
    return current_base


def _units_per_inch(ax):
    """当前坐标轴上 1 英寸对应的数据坐标跨度 (sx, sy)。"""
    fig = ax.figure
    fw, fh = fig.get_size_inches()
    pos = ax.get_position()
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    sx = abs(x1 - x0) / (pos.width * fw)
    sy = abs(y1 - y0) / (pos.height * fh)
    return sx, sy


def paint(ax, verts, lith_name, spacing=BASE_SPACING, lw=0.55,
          spec=None, face=None, fixed_layer_rows=False):
    """在 ax 上以岩性样式填充多边形 verts（数据坐标顶点列表）。

    必须在坐标轴范围（xlim/ylim）和位置确定之后调用，花纹间距才会正确。
    传入 spec（图元/行式列表）时直接用它绘制花纹（供花纹设计/预览用），
    此时可用 face 指定底色，并保留用户 spec 的原始重复语义。
    """
    if spec is not None:
        pat_fn = build_spec_pattern(
            spec, fixed_layer_rows=bool(fixed_layer_rows))
        if face is None:
            face = "#f2efe6"
    else:
        f2, pattern = style_of(lith_name)
        if face is None:
            face = f2
        pat_fn = PATTERNS.get(pattern) if pattern else None
    poly = Polygon(list(verts), closed=True, facecolor=face,
                   edgecolor="none", zorder=1)
    ax.add_patch(poly)
    if pat_fn is None:
        return poly
    # 方向敏感符号（∧/∨、Γ、箭头…）在标高轴（y 向上）里要翻转 y
    _SHAPE_FLIP[0] = not ax.yaxis_inverted()

    sx, sy = _units_per_inch(ax)
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    res = pat_fn(x0, x1, y0, y1, spacing * sx, spacing * sy)
    segs, marks = res[0], res[1]
    csegs = res[2] if len(res) > 2 else []   # 彩色线段组（旧式花纹无此项）

    if segs:
        lc = LineCollection(segs, colors=INK, linewidths=lw, zorder=2)
        ax.add_collection(lc)
        lc.set_clip_path(poly)
    for col, w, cs in csegs:
        if not cs:
            continue
        lc = LineCollection(cs, colors=col or INK, linewidths=w or lw,
                            zorder=2)
        ax.add_collection(lc)
        lc.set_clip_path(poly)
    for mark in marks:
        mx, my, marker, filled = mark[0], mark[1], mark[2], mark[3]
        if not mx:
            continue
        ms = mark[4] if len(mark) > 4 else (3.4 if marker == "o" else 2.0)
        col = (mark[5] if len(mark) > 5 else None) or INK
        marker_zorder = 2
        if (_LEGEND_SWATCH_CONTEXT.get() is not None
                and isinstance(marker, str)
                and marker.startswith("$") and marker.endswith("$")
                and face not in (None, "none")):
            # Dense shale lines and brick strokes must not run through Ca/Si/C
            # labels.  The standard plate leaves a small knockout around
            # text symbols; use the lithology face colour so the mask remains
            # correct for coloured and monochrome legends alike.
            clearance_pt = LEGEND_SYMBOL_CLEARANCE_MM * 72.0 / 25.4
            (mask,) = ax.plot(
                mx, my, ls="none", marker="s",
                ms=ms + 2 * clearance_pt,
                mfc=face, mec=face, mew=0, zorder=2.1)
            mask.set_clip_path(poly)
            marker_zorder = 2.2
        (line,) = ax.plot(mx, my, ls="none", marker=marker, ms=ms,
                          mfc=(col if filled else "none"),
                          mec=col, mew=0.55, zorder=marker_zorder)
        line.set_clip_path(poly)
    return poly


def paint_legend_swatch(ax, verts, lith_name, spacing=BASE_SPACING, lw=0.55,
                         spec=None, face=None, height_mm=10.0, rows=3,
                         fixed_layer_rows=False):
    """统一绘制图例小样，与主图可调层高完全解耦。

    标准内置层状/符号阵展示三个代表重复单元；页岩式
    密集连续底纹与用户传入的自定义 spec 保留原有密度/语义。
    """
    with legend_swatch_scope(height_mm, rows):
        return paint(
            ax, verts, lith_name, spacing=spacing, lw=lw, spec=spec,
            face=face, fixed_layer_rows=fixed_layer_rows)


def paint_basic_symbol_sample(ax, verts, spec, spacing=BASE_SPACING, lw=0.55,
                              face="#ffffff", height_mm=10.0):
    """Draw one complete RPBP symbol motif at the centre of a sample box."""
    with legend_single_motif_scope():
        return paint_legend_swatch(
            ax, verts, None, spacing=spacing, lw=lw, spec=spec, face=face,
            height_mm=height_mm, rows=LEGEND_REPRESENTATIVE_ROWS)


def _draw_cased_line(ax, xs, ys, color, lw, zorder, clearance_mm,
                     background, capstyle, joinstyle="round"):
    """先画白色底衬再画墨线；两种 zorder 保证分段拼接处不会互相擦除。"""
    clearance_pt = max(0.0, float(clearance_mm)) * 72.0 / 25.4
    common = dict(
        solid_capstyle=capstyle,
        solid_joinstyle=joinstyle,
    )
    (underlay,) = ax.plot(
        xs,
        ys,
        color=background,
        lw=float(lw) + 2 * clearance_pt,
        zorder=zorder,
        **common,
    )
    (line,) = ax.plot(
        xs,
        ys,
        color=color,
        lw=lw,
        zorder=zorder + 1,
        **common,
    )
    return [underlay, line]


def contact_clearance_mm(layer_heights_in, maximum=CONTACT_CLEARANCE_MM,
                         fraction=0.10, minimum=0.08):
    """按相邻层最小图上高度限制净空，避免白色底衬吞没薄层花纹。"""
    heights = []
    for height in layer_heights_in:
        try:
            value = float(height)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            heights.append(value)
    if not heights:
        return float(maximum)
    thin_mm = min(heights) * 25.4
    return min(float(maximum), max(float(minimum), thin_mm * float(fraction)))


def draw_wavy(ax, pts, color=INK, lw=1.0, amp=0.028, wavelength=0.17,
              ticks=False, zorder=3, clearance_mm=CONTACT_CLEARANCE_MM,
              background="white"):
    """沿折线 pts（数据坐标）绘制波状线，表示不整合面。

    ticks=True 时在波状线下方加短斜线（角度不整合，示下伏地层被削截）。
    波幅、波长以英寸计，需在坐标轴范围确定后调用。
    """
    sx, sy = _units_per_inch(ax)
    p = np.asarray(pts, float)
    if p.ndim != 2 or len(p) < 2:
        return []
    x_in, y_in = p[:, 0] / sx, p[:, 1] / sy  # 换算到英寸空间，保证波形均匀
    seg = np.hypot(np.diff(x_in), np.diff(y_in))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    length = s[-1]
    if length <= 0:
        return []
    si = np.linspace(0, length, max(int(length / (wavelength / 24)), 16))
    xi = np.interp(si, s, x_in)
    yi = np.interp(si, s, y_in)
    tx = np.gradient(xi, si)
    ty = np.gradient(yi, si)
    norm = np.hypot(tx, ty)
    nx, ny = -ty / norm, tx / norm  # 单位法向
    # 取最接近目标波长的整数个周期，使波线严格回到两个接触端点；剖面中
    # 不同接触类型在区段中点拼接时不会再出现相位错口。
    cycles = max(1, int(round(length / wavelength)))
    off = amp * np.sin(2 * np.pi * cycles * si / length)
    artists = _draw_cased_line(
        ax,
        (xi + nx * off) * sx,
        (yi + ny * off) * sy,
        color,
        lw,
        zorder,
        clearance_mm,
        background,
        "butt",
    )

    if ticks:
        # 视觉"下方"对应的英寸空间 y 方向（柱状图深度轴反向时为正）
        down = 1.0 if ax.yaxis_inverted() else -1.0
        step = 0.22
        for s0 in np.arange(step / 2, length, step):
            x0 = np.interp(s0, s, x_in)
            y0 = np.interp(s0, s, y_in)
            tangent = np.array([
                np.interp(s0, si, tx),
                np.interp(s0, si, ty),
            ])
            tangent /= np.hypot(*tangent)
            normal = np.array([-tangent[1], tangent[0]])
            if normal[1] * down < 0:
                normal *= -1
            start = (np.array([x0, y0])
                     + 0.015 * tangent + 0.035 * normal)
            end = (np.array([x0, y0])
                   + 0.055 * tangent + 0.10 * normal)
            tick_lw = lw * 0.8
            artists.extend(
                _draw_cased_line(
                    ax,
                    [start[0] * sx, end[0] * sx],
                    [start[1] * sy, end[1] * sy],
                    color,
                    tick_lw,
                    zorder,
                    clearance_mm,
                    background,
                    "round",
                )
            )
    return artists


def draw_contact(ax, pts, contact="", color=INK, lw=0.9, zorder=3,
                 clearance_mm=CONTACT_CLEARANCE_MM, background="white"):
    """绘制普通或不整合接触面，并隔离其与两侧岩性花纹。

    普通接触面为直线；平行不整合为波状线；角度不整合在波状线的下伏侧
    加短斜线。所有线条都使用相同毫米净空，避免显示和打印时与花纹混线。
    返回参与绘制的 ``Line2D`` 列表，便于调用方和测试检查。
    """
    unconf, angular = is_unconformity(contact)
    if unconf:
        return draw_wavy(
            ax,
            pts,
            color=color,
            lw=lw,
            ticks=angular,
            zorder=zorder,
            clearance_mm=clearance_mm,
            background=background,
        )
    p = np.asarray(pts, float)
    if len(p) < 2:
        return []
    return _draw_cased_line(
        ax,
        p[:, 0],
        p[:, 1],
        color,
        lw,
        zorder,
        clearance_mm,
        background,
        "butt",
    )


def draw_break(ax, x0, x1, yc, gap=0.13, color=INK, lw=0.9, zorder=4):
    """厚层压缩折断符号：一段留白 + 上下两条水平波状线，表示中间省略。

    yc 为折断带中心（数据坐标），gap 为留白高度（英寸）。需在坐标轴
    范围确定后调用；会遮盖该处的岩性花纹。
    """
    from matplotlib.patches import Rectangle

    _sx, sy = _units_per_inch(ax)
    half = gap / 2 * sy
    ax.add_patch(Rectangle((x0, yc - half), x1 - x0, 2 * half,
                           facecolor="white", edgecolor="none",
                           zorder=zorder - 0.5))
    for y in (yc - half, yc + half):
        draw_wavy(ax, [(x0, y), (x1, y)], color=color, lw=lw, zorder=zorder,
                  amp=0.018, wavelength=0.13)


def is_unconformity(contact):
    """接触关系文字是否表示不整合；返回 (是否不整合, 是否角度不整合)。"""
    c = (contact or "").strip()
    unconf = "不整合" in c
    return unconf, unconf and "角度" in c


def legend_items(lith_names):
    """按出现顺序去重，返回标准岩性名列表（供图例用）。"""
    seen, items = set(), []
    for name in lith_names:
        std = resolve(name) or (name or "").strip() or "未知"
        if std not in seen:
            seen.add(std)
            items.append(std)
    return items


# 复刻 GB/T 958—2015 表 4 正式图版的“图例（Legend or symbol）”样框：
# 约 15 mm × 10 mm。正文没有把样框尺寸另列为强制条款，强制的是每个
# 图元的毫米制图参数；因此这里称“表 4 图版样框”，并保留其精确物理尺寸。
_MM_IN = 1.0 / 25.4
LEGEND_SWATCH_WIDTH_MM = 15.0
LEGEND_SWATCH_HEIGHT_MM = 10.0
LEGEND_REPRESENTATIVE_ROWS = 3
LEGEND_MAX_LABEL_LINES = 2
_SW_IN = LEGEND_SWATCH_WIDTH_MM * _MM_IN
_SH_IN = LEGEND_SWATCH_HEIGHT_MM * _MM_IN
_LEGEND_PAD_X_IN = 1.5 * _MM_IN
_LEGEND_PAD_Y_IN = 1.5 * _MM_IN
_LEGEND_GAP_X_IN = 1.5 * _MM_IN
_LEGEND_GAP_Y_IN = 1.5 * _MM_IN
_LEGEND_LABEL_GAP_Y_IN = 1.0 * _MM_IN


def _em_width(text):
    """文字宽度估计（em）：全角 1.0，半角 0.55。"""
    return sum(1.0 if (ord(ch) > 0x2E7F or ch in "…—“”‘’") else 0.55
               for ch in (text or ""))


def _wrap_em(text, width_em):
    """按 em 宽度折行（全角/半角分别计宽）。"""
    lines, cur, w = [], "", 0.0
    width_em = max(width_em, 1.0)
    for ch in text:
        cw = 1.0 if (ord(ch) > 0x2E7F or ch in "…—“”‘’") else 0.55
        if cur and w + cw > width_em + 1e-9:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += cw
    if cur:
        lines.append(cur)
    return lines or [""]


_OPEN_PUNCTUATION = set("（【《〈「『“‘([{")
_CLOSE_PUNCTUATION = set("）】》〉」』”’，。、；：！？)]}")
_BRACKET_PAIRS = {
    "（": "）", "【": "】", "《": "》", "〈": "〉", "「": "」", "『": "』",
    "“": "”", "‘": "’", "(": ")", "[": "]", "{": "}",
}


def _break_inside_brackets(text, index):
    """断点是否位于一对括号/引号之内。"""
    expected = []
    for char in text[:index]:
        if char in _BRACKET_PAIRS:
            expected.append(_BRACKET_PAIRS[char])
        elif expected and char == expected[-1]:
            expected.pop()
    return bool(expected)


def _legal_legend_break(text, index):
    """优先整段保留括号内容，并避免括号/标点在行首行尾悬挂。"""
    return (0 < index < len(text)
            and text[index - 1] not in _OPEN_PUNCTUATION
            and text[index] not in _CLOSE_PUNCTUATION
            and not _break_inside_brackets(text, index))


def _minimum_two_line_width_em(text):
    """完整名称在最多两行内需要的最小行宽（em）。"""
    text = str(text or "")
    whole = _em_width(text)
    candidates = [
        max(_em_width(text[:index]), _em_width(text[index:]))
        for index in range(1, len(text))
        if _legal_legend_break(text, index)
    ]
    if not candidates:
        candidates = [
            max(_em_width(text[:index]), _em_width(text[index:]))
            for index in range(1, len(text))
        ]
    return min([whole] + candidates) if candidates else whole


def _wrap_legend_label(text, width_em, max_lines=LEGEND_MAX_LABEL_LINES):
    """名称感知的均衡折行：完整保留文字，且不拆散括号与标点。"""
    text = str(text or "")
    width_em = max(float(width_em), 1.0)
    if _em_width(text) <= width_em + 1e-9:
        return [text]
    if max_lines != 2:
        lines = _wrap_em(text, width_em)
        if len(lines) <= max_lines:
            return lines
        raise ValueError(f"图例名称“{text}”无法在 {max_lines} 行内完整显示")

    candidates = []
    for index in range(1, len(text)):
        left, right = text[:index], text[index:]
        lw, rw = _em_width(left), _em_width(right)
        if max(lw, rw) > width_em + 1e-9:
            continue
        legal = _legal_legend_break(text, index)
        # 同样能放下时优先括号/标点完整，再优先两行视觉平衡。
        candidates.append(((0 if legal else 1, abs(lw - rw), max(lw, rw)),
                           [left, right]))
    if not candidates:
        raise ValueError(
            f"图例名称“{text}”无法在两行内完整显示；请增大图例项宽度")
    return min(candidates, key=lambda item: item[0])[1]


def legend_col_cm(lith_names, fontsize=8.0):
    """底部图例项建议宽度：样框在上、名称在下且最多两行。"""
    items = legend_items(lith_names)
    max_em = max((_minimum_two_line_width_em(n) for n in items), default=1.0)
    need_in = (2 * _LEGEND_PAD_X_IN
               + max(_SW_IN, max_em * fontsize / 72))
    return need_in * 2.54


def _legend_column_metrics(lith_names, col_in, fontsize=8.0):
    """返回 ``(items, layouts, required_height_in)`` 并校验图版样框宽度。"""
    items = legend_items(lith_names)
    if not items:
        return [], [], 0.0
    text_in = (col_in - 2 * _LEGEND_PAD_X_IN - _SW_IN
               - _LEGEND_GAP_X_IN)
    if text_in < 7 * _MM_IN - 1e-9:
        need_cm = (2 * _LEGEND_PAD_X_IN + _SW_IN + _LEGEND_GAP_X_IN
                   + 7 * _MM_IN) * 2.54
        raise ValueError(
            "图例列过窄：参照 GB/T 958—2015 表 4 图版的样框为 "
            "15 mm × 10 mm，"
            f"图例列至少需要 {need_cm:.1f} 厘米"
        )

    fs = min(max(float(fontsize), 6.0), 9.0)
    wrap_em = text_in / (fs / 72)
    layouts = []
    for std in items:
        lines = _wrap_legend_label(std, wrap_em)
        line_h_in = fs / 72 * 1.25
        content_h_in = max(_SH_IN, len(lines) * line_h_in)
        layouts.append((std, lines, line_h_in, content_h_in, fs))
    required_in = (2 * _LEGEND_PAD_Y_IN
                   + sum(item[3] for item in layouts)
                   + _LEGEND_GAP_Y_IN * max(0, len(layouts) - 1))
    return items, layouts, required_in


def legend_column_height_in(lith_names, col_in, fontsize=8.0):
    """保持表 4 图版 15 mm × 10 mm 样框时，图例列需要的物理高度。"""
    return _legend_column_metrics(lith_names, col_in, fontsize)[2]


def draw_legend_column(ax, x0, x1, ytop, ybot, lith_names, fontsize=8.0):
    """在柱状图图例列内按标准样框紧凑排列岩性图例。

    每个花纹样框固定为参照 GB/T 958—2015 表 4 图版的 15 mm × 10 mm，
    花纹以原始毫米制图参数绘制；不会再随图例项数量缩小或改变疏密。
    名称在样框右侧完整折行，各项从视觉顶部开始排列，并以细横线分隔。
    如果可用高度不足以保留标准物理尺寸，明确报错而不是静默变形。
    """
    sx, sy = _units_per_inch(ax)
    col_in = abs(x1 - x0) / sx
    span_in = abs(ybot - ytop) / sy
    items, layouts, required_in = _legend_column_metrics(
        lith_names, col_in, fontsize)
    if not items:
        return []
    if required_in > span_in + 1e-9:
        raise ValueError(
            "图例区高度不足：为保持表 4 图版的 15 mm × 10 mm "
            f"样框，{len(items)} 项图例至少需要 "
            f"{required_in * 2.54:.1f} 厘米，当前只有 {span_in * 2.54:.1f} 厘米；"
            "请减小比例尺分母、增大页面，或减少岩性种类"
        )

    direction = 1.0 if ybot >= ytop else -1.0
    bx = min(x0, x1) + _LEGEND_PAD_X_IN * sx
    sw, sh = _SW_IN * sx, _SH_IN * sy
    cursor_in = _LEGEND_PAD_Y_IN
    artists = []
    for index, (std, lines, line_h_in, content_h_in, fs) in enumerate(layouts):
        yc = ytop + direction * (cursor_in + content_h_in / 2) * sy
        verts = [(bx, yc - sh / 2), (bx + sw, yc - sh / 2),
                 (bx + sw, yc + sh / 2), (bx, yc + sh / 2)]
        artists.append(paint_legend_swatch(
            ax, verts, std, spacing=BASE_SPACING,
            height_mm=LEGEND_SWATCH_HEIGHT_MM,
            rows=LEGEND_REPRESENTATIVE_ROWS))
        border = Polygon(verts, closed=True, facecolor="none",
                         edgecolor="#000000", lw=0.1 * 72 / 25.4,
                         zorder=3)
        ax.add_patch(border)
        artists.append(border)
        line_dy = line_h_in * sy
        ty = yc - (len(lines) - 1) / 2 * line_dy
        for ln in lines:
            artists.append(ax.text(
                bx + sw + _LEGEND_GAP_X_IN * sx, ty, ln,
                ha="left", va="center", fontsize=fs
            ))
            ty += line_dy
        cursor_in += content_h_in
        if index < len(layouts) - 1:
            separator_y = ytop + direction * (
                cursor_in + _LEGEND_GAP_Y_IN / 2) * sy
            artists.extend(ax.plot(
                [x0, x1], [separator_y, separator_y],
                color="#b8b8b8", lw=0.35, zorder=3
            ))
            cursor_in += _LEGEND_GAP_Y_IN
    return artists


def _legend_grid(items, w_in, fontsize=8.0, item_w_in=None, nrows=None):
    """量算完整名称、固定标准样框的均衡图例网格。

    返回 ``(rows, cols, cell_w, layouts, row_heights)``。项目按阅读顺序
    先左后右、再换到下一行；名称放在样框下方，最多两行。
    末行不留 6+1/9+1 式孤项，而是在各行均衡分配并居中。
    """
    if not items:
        return 0, 0, 0.0, [], []
    fs = min(max(float(fontsize), 6.0), 9.0)
    minimum_cell = 2 * _LEGEND_PAD_X_IN + _SW_IN
    if w_in + 1e-9 < minimum_cell:
        raise ValueError(
            "图例区过窄：15 mm × 10 mm 图版样框至少需要 "
            f"{minimum_cell * 2.54:.1f} 厘米宽度"
        )
    label_cell = max(
        (2 * _LEGEND_PAD_X_IN
         + _minimum_two_line_width_em(std) * fs / 72)
        for std in items
    )
    target = max(float(item_w_in or 0.0), minimum_cell, label_cell)
    if nrows is not None:
        rows = max(1, min(int(nrows), len(items)))
        cols = -(-len(items) // rows)
        if w_in / cols + 1e-9 < target:
            raise ValueError("指定的图例行数无法容纳标准样框和两行完整名称")
    else:
        capacity = max(1, min(len(items), int(w_in / target)))
        rows = -(-len(items) // capacity)
        cols = -(-len(items) // rows)
    cell_w = w_in / cols
    text_w = cell_w - 2 * _LEGEND_PAD_X_IN
    wrap_em = text_w / (fs / 72)
    layouts = []
    row_heights = [0.0 for _ in range(rows)]
    base, extra = divmod(len(items), rows)
    counts = [base + (1 if row < extra else 0) for row in range(rows)]
    index = 0
    line_h = fs / 72 * 1.22
    for row, count in enumerate(counts):
        # 比最长行短的行在图例块内整体居中。
        offset = (cols - count) / 2
        for position in range(count):
            std = items[index]
            lines = _wrap_legend_label(std, wrap_em)
            content_h = (_SH_IN + _LEGEND_LABEL_GAP_Y_IN
                         + len(lines) * line_h)
            row_heights[row] = max(row_heights[row], content_h)
            layouts.append(
                (std, lines, line_h, fs, row, offset + position))
            index += 1
    return rows, cols, cell_w, layouts, row_heights


def legend_height_in(lith_names, w_in, fontsize=8.0, nrows=None,
                     item_w_in=None, include_note=True):
    """GB/T 958 花纹图例块所需的物理高度，供调用方在绘图前预留。"""
    items = legend_items(lith_names)
    if not items:
        return 0.0
    rows, _cols, _cell_w, _layouts, row_heights = _legend_grid(
        items, w_in, fontsize, item_w_in=item_w_in, nrows=nrows)
    title_h = 0.30
    note_h = 0.24 if include_note else 0.0
    return (0.06 + title_h + 0.04 + sum(row_heights)
            + 0.08 * max(0, rows - 1) + 0.05 + note_h)


def draw_legend(fig, lith_names, rect,
                title="岩性图例",
                spacing=BASE_SPACING, fontsize=8.0, nrows=None,
                item_w_in=None, include_note=True):
    """在图底绘制固定 15 mm × 10 mm 图版样框的自适应岩性图例块。

    参照论文底部图例的行优先阅读方式：样框在上、名称在下，
    宽度不足时整项换行并均衡分配。图例小样的代表重复与主图
    ``pattern_row_height_mm`` 解耦，页岩等密集连续纹理保留标准密度。
    """
    items = legend_items(lith_names)
    if not items:
        return None

    ax = fig.add_axes(rect)
    ax.axis("off")
    fw, fh = fig.get_size_inches()
    w_in = rect[2] * fw
    h_in = rect[3] * fh
    ax.set_xlim(0, w_in)
    ax.set_ylim(h_in, 0)

    rows, cols, cell_w, layouts, row_heights = _legend_grid(
        items, w_in, fontsize, item_w_in=item_w_in, nrows=nrows)
    required = legend_height_in(
        items, w_in, fontsize, nrows=nrows, item_w_in=item_w_in,
        include_note=include_note)
    if h_in + 1e-9 < required:
        raise ValueError(
            f"图例区高度不足：标准图例需要 {required * 2.54:.1f} 厘米，"
            f"当前只有 {h_in * 2.54:.1f} 厘米"
        )

    y = 0.06
    title_h = 0.30
    ax.text(0, y + title_h / 2, title, fontsize=fontsize + 1,
            fontweight="bold", ha="left", va="center")
    ax.plot([0, w_in], [y + title_h, y + title_h],
            color="#777777", lw=0.35)
    y += title_h + 0.04
    row_tops = []
    for row_h in row_heights:
        row_tops.append(y)
        y += row_h + 0.08

    frame_lw = 0.1 * 72 / 25.4
    for std, lines, line_h, fs, row, col in layouts:
        x = col * cell_w + (cell_w - _SW_IN) / 2
        top = row_tops[row]
        verts = [(x, top), (x + _SW_IN, top),
                 (x + _SW_IN, top + _SH_IN), (x, top + _SH_IN)]
        paint_legend_swatch(
            ax, verts, std, spacing=spacing,
            height_mm=LEGEND_SWATCH_HEIGHT_MM,
            rows=LEGEND_REPRESENTATIVE_ROWS)
        ax.add_patch(Polygon(verts, closed=True, facecolor="none",
                             edgecolor="#000000", lw=frame_lw, zorder=3))
        tx = (col + 0.5) * cell_w
        ty = top + _SH_IN + _LEGEND_LABEL_GAP_Y_IN + line_h / 2
        for line in lines:
            ax.text(tx, ty, line, fontsize=fs, ha="center", va="center")
            ty += line_h

    if include_note:
        ax.text(0, required - 0.12,
                "注：花纹参照 GB/T 958—2015 表 4；填充色仅用于辅助区分。",
                fontsize=max(6.0, fontsize - 1), color="#555555",
                ha="left", va="center")
    return ax


# ---------------------------------------------------------------------------
# 用户自定义样式：一个 JSON 文件即可增改颜色、花纹、别名，无需改代码。
# ---------------------------------------------------------------------------

def load_style(path):
    """载入 JSON 样式文件，合并进内置花纹/岩性/别名表。返回载入的岩性名列表。

    JSON 结构（各段均可选，示例见 examples/strat_style.json）：
        {
          "patterns":  {"花纹名": [ {图元…}, … ]},        # 自定义花纹
          "lithology": {"岩性名": {"color":"#rrggbb",
                                    "pattern":"花纹名"}},   # 颜色/花纹（可只给其一）
          "aliases":   {"关键字": "标准岩性名"}             # 名称匹配（优先于内置）
        }
    """
    global _ALIAS_ORDER
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("样式文件的顶层应是 JSON 对象"
                         "（含 patterns / lithology / aliases 段）")

    unknown_sections = set(data) - {"patterns", "lithology", "aliases"}
    unknown_sections = {key for key in unknown_sections
                        if not str(key).startswith("_")}
    if unknown_sections:
        raise ValueError("样式文件包含未知段：" + "、".join(
            sorted(map(str, unknown_sections))))

    sections = {}
    for section in ("patterns", "lithology", "aliases"):
        value = data.get(section) or {}
        if not isinstance(value, dict):
            raise ValueError(f"样式文件的 {section} 段应是 JSON 对象")
        sections[section] = value

    # 候选内容先在临时对象中构建和交叉校验。只有全部成功后才更新全局
    # 注册表，保证坏文件不会留下“前一半已生效”的隐蔽状态。
    pending_patterns = {}
    for name, spec in sections["patterns"].items():
        if str(name).startswith("_"):
            continue    # 下划线开头的键当注释（示例文件里的 "_说明"）
        if not isinstance(name, str) or not name.strip():
            raise ValueError("花纹名应是非空文本")
        try:
            pending_patterns[name] = build_spec_pattern(spec)
        except ValueError as e:
            raise ValueError(f"花纹“{name}”定义有误——{e}") from None

    import matplotlib.colors as _mcolors
    touched = []
    pending_lithology = {}
    available_patterns = set(PATTERNS) | set(pending_patterns)
    available_lithology = dict(LITHOLOGY)
    for name, info in sections["lithology"].items():
        if str(name).startswith("_"):
            continue
        if not isinstance(name, str) or not name.strip():
            raise ValueError("岩性名应是非空文本")
        color, patt = available_lithology.get(name, DEFAULT)
        if isinstance(info, dict):
            bad = set(info) - {"color", "pattern"}
            if bad:
                raise ValueError(f"岩性“{name}”包含未知字段："
                                 + "、".join(sorted(map(str, bad))))
            color = info.get("color", color)
            patt = info.get("pattern", patt)
        elif isinstance(info, (list, tuple)) and len(info) == 2:
            color, patt = info
        else:
            raise ValueError(f"岩性“{name}”应写成对象（含 color/pattern），"
                             f"如 {{\"color\": \"#f7e8a9\"}}")
        if not _mcolors.is_color_like(color):
            raise ValueError(f"岩性“{name}”的颜色“{color}”无效，"
                             "请用 #RRGGBB 形式")
        if patt is not None and (not isinstance(patt, str)
                                 or patt not in available_patterns):
            raise ValueError(f"岩性“{name}”引用了未定义的花纹“{patt}”"
                             "（请在 patterns 段先定义，或改用内置花纹名）")
        pending_lithology[name] = (color, patt)
        available_lithology[name] = (color, patt)
        touched.append(name)

    aliases = {k: v for k, v in sections["aliases"].items()
               if not str(k).startswith("_")}
    for kw, std in aliases.items():
        if (not isinstance(kw, str) or not kw.strip()
                or not isinstance(std, str) or not std.strip()):
            raise ValueError("别名和目标岩性都应是非空文本")
        if std not in available_lithology:
            raise ValueError(f"别名“{kw}”指向的岩性“{std}”不存在"
                             "（请先在 lithology 段定义，或用内置岩性名）")

    PATTERNS.update(pending_patterns)
    LITHOLOGY.update(pending_lithology)
    if aliases:  # 用户别名放最前，优先于内置匹配；重复载入时去重
        new = tuple(aliases.items())
        replaced_keys = set(aliases)
        _ALIAS_ORDER = new + tuple(
            item for item in _ALIAS_ORDER if item[0] not in replaced_keys)
    return touched


def find_style_file(*dirs):
    """在给定目录中查找默认样式文件 strat_style.json，返回首个存在的路径。"""
    import os
    for d in dirs:
        if not d:
            continue
        p = os.path.join(d, "strat_style.json")
        if os.path.isfile(p):
            return p
    return None


# 注册进岩性库但不进"常用岩性一览"的名称（GB/T 958 全集条目在
# 分类图例表里展示，避免把常用一览撑成 900 行）。
SHEET_HIDDEN = set()


def render_pattern_sheet(names=None, title="岩性花纹一览", ncols=4):
    """把所有（或指定的）岩性花纹排成一张对照表，供挑选/校对自定义花纹。"""
    from matplotlib.figure import Figure

    from . import fonts
    fonts.setup()

    if names is None:
        names = [n for n in LITHOLOGY if n not in SHEET_HIDDEN]
    n = len(names)
    nrows = -(-n // ncols)
    cell_w, cell_h = 2.45, 0.82
    sw, sh = _SW_IN, _SH_IN  # 与图件图例共用 15×10 mm 小样
    fig_w = ncols * cell_w + 0.4
    fig_h = nrows * cell_h + 0.9
    fig = Figure(figsize=(fig_w, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    fig.text(0.5, 1 - 0.35 / fig_h, title, ha="center", va="top",
             fontsize=13, fontweight="bold")

    top = fig_h - 0.7
    for i, name in enumerate(names):
        r, c = divmod(i, ncols)
        x = 0.25 + c * cell_w
        yc = top - (r + 0.5) * cell_h   # 行带中心：各行等高、小样垂直居中
        verts = [(x, yc - sh / 2), (x + sw, yc - sh / 2),
                 (x + sw, yc + sh / 2), (x, yc + sh / 2)]
        paint_legend_swatch(
            ax, verts, name, spacing=BASE_SPACING,
            height_mm=LEGEND_SWATCH_HEIGHT_MM,
            rows=LEGEND_REPRESENTATIVE_ROWS)
        ax.add_patch(Polygon(verts, closed=True, facecolor="none",
                             edgecolor="#333333", lw=0.7, zorder=3))
        ax.text(x + sw + 0.1, yc, name, fontsize=8.5,
                ha="left", va="center")
    return fig


def register_pattern(name, spec, lith_name=None, color=None):
    """把设计好的花纹 spec 注册进引擎，立即可用；可选绑定到某岩性名
    （及底色），使该岩性在柱状图里用此花纹。返回花纹名。
    spec 写法同样式文件（图元 dict / 基本图形名 / 列表），定义有误
    抛 ValueError。"""
    global _ALIAS_ORDER
    name = (name or "").strip()
    if not name:
        raise ValueError("花纹名不能为空")
    if color is not None:
        import matplotlib.colors as _mcolors
        if not _mcolors.is_color_like(color):
            raise ValueError(f"底色“{color}”无效，请用 #RRGGBB 形式")
    PATTERNS[name] = build_spec_pattern(spec)
    if lith_name:
        face = color or LITHOLOGY.get(lith_name, DEFAULT)[0]
        LITHOLOGY[lith_name] = (face, name)
        if not any(k == lith_name for k, _ in _ALIAS_ORDER):
            _ALIAS_ORDER = ((lith_name, lith_name),) + _ALIAS_ORDER
    return name


def render_shapes_sheet(title="基本图形一览", ncols=4):
    """把 BASIC_SHAPES 里所有基本图形排成对照表，供设计花纹时挑选。"""
    from matplotlib.figure import Figure

    from . import fonts
    fonts.setup()

    names = list(BASIC_SHAPES.keys())
    n = len(names)
    nrows = -(-n // ncols)
    cell_w, cell_h = 2.05, 0.60
    sw, sh = 0.472, 0.315
    fig_w = ncols * cell_w + 0.4
    fig_h = nrows * cell_h + 0.9
    fig = Figure(figsize=(fig_w, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    fig.text(0.5, 1 - 0.35 / fig_h, title, ha="center", va="top",
             fontsize=13, fontweight="bold")
    top = fig_h - 0.7
    for i, name in enumerate(names):
        r, c = divmod(i, ncols)
        x = 0.25 + c * cell_w
        yc = top - (r + 0.5) * cell_h   # 行带中心：各行等高、小样垂直居中
        verts = [(x, yc - sh / 2), (x + sw, yc - sh / 2),
                 (x + sw, yc + sh / 2), (x, yc + sh / 2)]
        paint(ax, verts, name, spec=BASIC_SHAPES[name], face="#ffffff")
        ax.add_patch(Polygon(verts, closed=True, facecolor="none",
                             edgecolor="#333333", lw=0.7, zorder=3))
        # 部分常见中文字体不含 Unicode 数学符号 ⊤/⊥；图形本身由矢量
        # 线段绘制，标签改用等价中文名，避免导出时出现缺字方框和警告。
        display_name = {"⊤形": "T形", "⊥形": "倒T形"}.get(name, name)
        ax.text(x + sw + 0.1, yc, display_name, fontsize=8.5,
                ha="left", va="center")
    return fig
