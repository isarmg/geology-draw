"""图件文档模型：数据 + 全部出图参数 + 校验。

界面只负责把控件绑到这里的变量上；"参数怎么组合成一次绘图"只有这一
处答案，命令行与界面因此不会漂移。
"""

import os
import tkinter as tk

from strat import parse_column, parse_section, read_table
from strat import fonts as strat_fonts
from strat.column import STRATA_CATS, WIDTH_LIMITS, resolve_page, \
    resolve_widths, available_unit_columns
from strat.lithology import (PATTERN_ROW_HEIGHT_MM,
                             resolve_pattern_row_height_mm)
from strat.tableio import get as cell

AUTO_FONT = "（自动）"

# 厚度栏是三选一，不是三个独立开关——旧界面用两个复选框表达，同时勾选
# 时"显示深度"会静默胜出，这里改回它本来的互斥关系。
THICK_MODES = (("group", "按组总厚度"),
               ("layer", "逐层厚度"),
               ("depth", "地层深度"))

STRATA_LABELS = (("geochron", "地质年代"), ("chrono", "年代地层"),
                 ("litho", "岩石地层"))

_SECTION_KEYS = ("钻孔", "孔号", "钻孔编号")


class ValidationError(ValueError):
    """参数不合法：带上出错的字段名，界面据此定位。"""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def sniff_kind(rows):
    """判断表格是剖面数据还是柱状图数据。

    剖面数据每行都属于某个钻孔，所以有"钻孔"列即为剖面——用户不必先
    声明类型再选文件，打开哪份数据就画哪种图。
    """
    for row in rows:
        if cell(row, *_SECTION_KEYS):
            return "section"
    return "column"


def load_any(path):
    """读一次盘，识别类型并解析。返回 (kind, data)。"""
    rows = read_table(path)
    if not rows:
        raise ValueError("表格里没有数据行")
    kind = sniff_kind(rows)
    return kind, (parse_section(rows) if kind == "section"
                  else parse_column(rows))


class ChartState:
    """一份图件文档：载入的数据，以及所有影响出图的参数。"""

    def __init__(self, root, on_change):
        self._on_change = on_change
        self.kind = None      # None / "column" / "section"
        self.path = None
        self.data = None

        v_str, v_bool = tk.StringVar, tk.BooleanVar
        # 图面（两种图都适用）
        self.title = v_str(root)
        self.page = v_str(root, value="A4")
        self.landscape = v_bool(root, value=False)
        self.scale = v_str(root)              # 空 = 自动
        self.font = v_str(root, value=AUTO_FONT)
        self.font_size = v_str(root, value=f"{strat_fonts.BASE_FS:g}")
        self.pattern_row_height_mm = v_str(
            root, value=f"{PATTERN_ROW_HEIGHT_MM:g}")
        # 内容（柱状图）
        self.thick_mode = v_str(root, value="group")
        self.show_remark = v_bool(root, value=True)
        self.show_legend = v_bool(root, value=False)
        self.unit_vertical = v_bool(root, value=False)
        self.strata = {k: v_bool(root, value=True) for k in STRATA_CATS}
        self.hide_units = {}                  # 表头 -> BooleanVar（随数据重建）
        # 栏宽（柱状图）
        self.widths = {name: v_str(root) for name in WIDTH_LIMITS}
        # 剖面
        self.ve = v_str(root)                 # 空 = 自动适配画幅

        for var in self._live_vars():
            var.trace_add("write", lambda *_a: self._on_change())

    def _live_vars(self):
        """改动即需重绘的开关类变量（文本框由界面自己做防抖）。"""
        return [self.landscape, self.thick_mode, self.show_remark,
                self.show_legend, self.unit_vertical, *self.strata.values()]

    # ---- 数据 ----
    def set_data(self, kind, path, data):
        self.kind, self.path, self.data = kind, path, data
        self._rebuild_unit_toggles()

    def clear(self):
        self.kind = self.path = self.data = None
        self.hide_units = {}

    @property
    def filename(self):
        return os.path.basename(self.path) if self.path else ""

    def _rebuild_unit_toggles(self):
        """地层单位列随数据而变，换数据就按新数据重建这组开关。"""
        self.hide_units = {}
        if self.kind != "column":
            return
        for _key, head, _cat in available_unit_columns(self.data):
            v = tk.BooleanVar(value=True)
            v.trace_add("write", lambda *_a: self._on_change())
            self.hide_units[head] = v

    def unit_columns(self):
        return available_unit_columns(self.data) if self.kind == "column" \
            else []

    def summary(self):
        """状态栏里的一句话概览。"""
        if self.kind == "column":
            n = len(self.data)
            total = sum(ly["thick"] for ly in self.data)
            return f"柱状图 · {n} 层 · 总厚 {total:g} m"
        if self.kind == "section":
            n = len(self.data)
            layers = sum(len(h["layers"]) for h in self.data)
            return f"剖面图 · {n} 个钻孔 · {layers} 层"
        return ""

    # ---- 校验 ----
    def _parse_scale(self):
        s = self.scale.get().strip().replace("1:", "").replace("1：", "")
        if not s:
            return None
        try:
            v = int(float(s))
        except ValueError:
            raise ValidationError("scale", "比例尺要填数字，如 200 或 1:200")
        if not 1 <= v <= 100000:
            raise ValidationError("scale", "比例尺分母应在 1–100000 之间")
        return v

    def _parse_font_size(self):
        s = self.font_size.get().strip()
        if not s:
            return strat_fonts.BASE_FS
        try:
            v = float(s)
        except ValueError:
            raise ValidationError("font_size", "字号要填数字（6–12）")
        if not 6 <= v <= 12:
            raise ValidationError("font_size", "字号应在 6–12 pt 之间")
        return v

    def _parse_pattern_row_height_mm(self):
        s = self.pattern_row_height_mm.get().strip()
        try:
            return resolve_pattern_row_height_mm(s)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "pattern_row_height_mm", str(exc)) from None

    def _parse_page(self):
        page = self.page.get().strip() or "A4"
        try:
            resolve_page(page, self.landscape.get())
        except ValueError as e:
            raise ValidationError("page", str(e))
        return page

    def _parse_widths(self):
        raw = {}
        for name, var in self.widths.items():
            s = var.get().strip()
            if not s:
                continue
            try:
                raw[name] = float(s)
            except ValueError:
                raise ValidationError(f"width:{name}",
                                      f"“{name}”栏宽要填数字（厘米）")
        try:
            return resolve_widths(raw) if raw else {}
        except Exception as e:
            raise ValidationError("widths", str(e))

    def _parse_ve(self):
        s = self.ve.get().strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            raise ValidationError("ve", "垂直夸大系数要填数字，如 2")
        if not 0.1 <= v <= 50:
            raise ValidationError("ve", "垂直夸大系数应在 0.1–50 之间")
        return v

    def apply_fonts(self):
        """字体是全局绘图状态，绘图前统一生效。"""
        name = self.font.get().strip()
        strat_fonts.set_family(None if name in ("", AUTO_FONT) else name)
        strat_fonts.set_base_size(self._parse_font_size())

    def render_kwargs(self):
        """算出传给 render_* 的参数；不合法则抛 ValidationError。"""
        pattern_row_height_mm = self._parse_pattern_row_height_mm()
        if self.kind == "section":
            return {"title": self.title.get().strip() or "地层剖面图",
                    "ve": self._parse_ve(),
                    "pattern_row_height_mm": pattern_row_height_mm}
        chosen = {k for k, v in self.strata.items() if v.get()}
        hidden = {h for h, v in self.hide_units.items() if not v.get()}
        return {
            "title": self.title.get().strip() or "综合地层柱状图",
            "scale": self._parse_scale(),
            "page": self._parse_page(),
            "landscape": self.landscape.get(),
            "widths": self._parse_widths(),
            "thick_mode": self.thick_mode.get(),
            "unit_vertical": self.unit_vertical.get(),
            "strata": None if len(chosen) == len(STRATA_CATS) else chosen,
            "show_remark": None if self.show_remark.get() else False,
            "hide_units": hidden or None,
            "show_legend": self.show_legend.get(),
            "pattern_row_height_mm": pattern_row_height_mm,
        }
