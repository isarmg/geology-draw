"""设计令牌与 ttk 样式表。

界面里所有颜色、字号、间距都在这里定义，其余模块只引用 Theme 实例的
属性，不出现字面量色值——改配色、改字号只动这一个文件。
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# 用来自绘勾选框/单选钮的指示器。PIL 是 matplotlib 的硬依赖，而 TkAgg
# 后端本身就 `from PIL import Image, ImageTk`（见 backends/_backend_tk.py），
# 所以界面能启动就一定有它，不必额外准备退路。
from PIL import Image, ImageDraw, ImageTk

# 中文字体候选（按优先级；找不到则用系统默认族）
_CJK = ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
        "Noto Sans CJK SC", "Source Han Sans SC", "Noto Sans SC",
        "WenQuanYi Micro Hei", "SimHei")

# 浅色方案：一个强调色 + 一组中性灰，层级靠明度而非描边堆叠
LIGHT = {
    "bg":          "#f1f2f5",   # 窗口底色 / 左侧模式栏
    "surface":     "#ffffff",   # 顶栏、检查器、卡片
    "surface_alt": "#f7f8fa",   # 卡片内的次级区块
    "canvas":      "#e3e5eb",   # 预览画布的背景（衬出白色图纸）
    "border":      "#e4e7ec",   # 常规分隔线
    "border_hi":   "#ccd2dc",   # 输入框描边
    "ink":         "#151920",   # 主文字
    "muted":       "#5b6472",   # 次要文字
    "subtle":      "#98a1ae",   # 提示、单位、范围说明
    "accent":      "#2f6fed",
    "accent_dk":   "#2358c8",
    "accent_soft": "#e9f0fe",   # 选中态底色
    "hover":       "#edeff3",
    "disabled":    "#b4bbc5",
    "shadow":      "#d5d8de",   # 图纸投影（用细边模拟）
}


class Theme:
    """持有配色、字体与间距刻度，并把 ttk 样式一次性注册好。"""

    def __init__(self, root, colors=None):
        self.root = root
        self.c = dict(colors or LIGHT)
        self.u = self._detect_scale(root)
        self._init_fonts()
        self._init_styles()

    # ---- 缩放与字体 ----
    @staticmethod
    def _detect_scale(root):
        """按屏幕 DPI 得到界面缩放系数（高分屏下控件不显小）。"""
        try:
            return max(1.0, min(2.5, root.winfo_fpixels("1i") / 96.0))
        except tk.TclError:
            return 1.0

    def px(self, n):
        """把设计稿上的逻辑像素换算成当前屏幕的实际像素。"""
        return max(1, round(n * self.u))

    def _init_fonts(self):
        fams = set(tkfont.families())
        self.family = next((f for f in _CJK if f in fams), "")
        base = max(9, round(10 * self.u))

        def mk(size_delta=0, weight="normal"):
            f = tkfont.Font(root=self.root, size=base + size_delta,
                            weight=weight)
            if self.family:
                f.configure(family=self.family)
            return f

        self.f_body = mk()
        self.f_small = mk(-1)
        self.f_strong = mk(0, "bold")
        self.f_section = mk(-1, "bold")   # 检查器分组标题
        self.f_brand = mk(3, "bold")      # 顶栏应用名
        self.f_rail = mk(-1)              # 模式栏文字

        # 让 messagebox / 下拉菜单 / 文件对话框跟随同一套字体
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkTooltipFont"):
            try:
                f = tkfont.nametofont(name)
            except tk.TclError:
                continue
            f.configure(size=base)
            if self.family:
                f.configure(family=self.family)

    # ---- ttk 样式 ----
    def _init_styles(self):
        c, s = self.c, ttk.Style(self.root)
        try:
            s.theme_use("clam")   # clam 才允许改描边色，是最好的定制底座
        except tk.TclError:
            pass
        pad_x, pad_y = self.px(10), self.px(5)

        s.configure(".", background=c["bg"], foreground=c["ink"],
                    font=self.f_body, borderwidth=0, focuscolor=c["bg"])

        # 容器
        for name, bg in (("Bg.TFrame", c["bg"]),
                         ("Surface.TFrame", c["surface"]),
                         ("Alt.TFrame", c["surface_alt"]),
                         ("Canvas.TFrame", c["canvas"]),
                         ("Rail.TFrame", c["bg"])):
            s.configure(name, background=bg)

        # 文字：名字即用途，背景默认贴在 surface 上
        text_styles = {
            "Brand.TLabel":   (c["ink"], self.f_brand, c["surface"]),
            "Body.TLabel":    (c["ink"], self.f_body, c["surface"]),
            "Muted.TLabel":   (c["muted"], self.f_body, c["surface"]),
            "Subtle.TLabel":  (c["subtle"], self.f_small, c["surface"]),
            "Field.TLabel":   (c["muted"], self.f_body, c["surface"]),
            "Section.TLabel": (c["ink"], self.f_section, c["surface"]),
            "Status.TLabel":  (c["muted"], self.f_small, c["surface"]),
            "File.TLabel":    (c["subtle"], self.f_small, c["surface"]),
            "Hint.TLabel":    (c["subtle"], self.f_small, c["surface"]),
            "AltHint.TLabel": (c["subtle"], self.f_small, c["surface_alt"]),
            "AltBody.TLabel": (c["ink"], self.f_body, c["surface_alt"]),
            "Empty.TLabel":   (c["muted"], self.f_body, c["canvas"]),
            "EmptyDim.TLabel": (c["subtle"], self.f_small, c["canvas"]),
        }
        for name, (fg, font, bg) in text_styles.items():
            s.configure(name, foreground=fg, font=font, background=bg)
        s.map("Field.TLabel", foreground=[("disabled", c["disabled"])])

        # 按钮
        s.configure("Accent.TButton", background=c["accent"],
                    foreground="#ffffff", font=self.f_body, relief="flat",
                    padding=(self.px(14), pad_y), focuscolor=c["accent"])
        s.map("Accent.TButton",
              background=[("disabled", c["disabled"]),
                          ("pressed", c["accent_dk"]),
                          ("active", c["accent_dk"])],
              foreground=[("disabled", "#ffffff")])

        s.configure("Ghost.TButton", background=c["surface"],
                    foreground=c["ink"], font=self.f_body, relief="flat",
                    padding=(pad_x, pad_y), focuscolor=c["surface"],
                    anchor="center")
        s.map("Ghost.TButton",
              background=[("disabled", c["surface"]), ("pressed", c["hover"]),
                          ("active", c["hover"])],
              foreground=[("disabled", c["disabled"]),
                          ("active", c["accent"])])

        # 次级按钮：浅底，用于检查器里的成组动作
        s.configure("Soft.TButton", background=c["surface_alt"],
                    foreground=c["ink"], font=self.f_small, relief="flat",
                    padding=(self.px(8), self.px(5)),
                    focuscolor=c["surface_alt"])
        s.map("Soft.TButton",
              background=[("disabled", c["surface_alt"]),
                          ("pressed", c["accent_soft"]),
                          ("active", c["hover"])],
              foreground=[("disabled", c["disabled"]),
                          ("active", c["accent"])])

        # 图标按钮（顶栏右侧、行内删除等）
        s.configure("Icon.TButton", background=c["surface"],
                    foreground=c["muted"], font=self.f_body, relief="flat",
                    padding=(self.px(6), self.px(4)), focuscolor=c["surface"],
                    width=2, anchor="center")
        s.map("Icon.TButton",
              background=[("pressed", c["hover"]), ("active", c["hover"])],
              foreground=[("active", c["accent"])])
        s.configure("AltIcon.TButton", background=c["surface_alt"],
                    foreground=c["subtle"], relief="flat",
                    padding=(self.px(4), self.px(2)),
                    focuscolor=c["surface_alt"], width=2, anchor="center")
        s.map("AltIcon.TButton",
              background=[("pressed", c["hover"]), ("active", c["hover"])],
              foreground=[("active", c["accent"])])

        # 左侧模式栏：选中项用强调底色，未选中透明
        s.configure("Rail.TButton", background=c["bg"], foreground=c["muted"],
                    font=self.f_rail, relief="flat", focuscolor=c["bg"],
                    padding=(self.px(4), self.px(8)), anchor="center")
        s.map("Rail.TButton",
              background=[("pressed", c["hover"]), ("active", c["hover"])],
              foreground=[("active", c["ink"])])
        s.configure("RailOn.TButton", background=c["accent_soft"],
                    foreground=c["accent"], font=self.f_rail, relief="flat",
                    focuscolor=c["accent_soft"],
                    padding=(self.px(4), self.px(8)), anchor="center")
        s.map("RailOn.TButton",
              background=[("pressed", c["accent_soft"]),
                          ("active", c["accent_soft"])],
              foreground=[("active", c["accent"])])

        # 分段控件（视图切换）
        s.configure("Seg.TButton", background=c["surface_alt"],
                    foreground=c["muted"], font=self.f_small, relief="flat",
                    padding=(self.px(12), self.px(5)),
                    focuscolor=c["surface_alt"])
        s.map("Seg.TButton",
              background=[("pressed", c["hover"]), ("active", c["hover"])],
              foreground=[("active", c["ink"])])
        s.configure("SegOn.TButton", background=c["surface"],
                    foreground=c["accent"], font=self.f_small, relief="flat",
                    padding=(self.px(12), self.px(5)),
                    focuscolor=c["surface"])
        s.map("SegOn.TButton",
              background=[("pressed", c["surface"]),
                          ("active", c["surface"])],
              foreground=[("active", c["accent"])])

        # 勾选框 / 单选钮
        self._indicator_elems = {}
        self._indicator_imgs = {}
        self._init_indicators(s)
        for kind in ("TCheckbutton", "TRadiobutton"):
            layout = self._indicator_layout(kind)
            for prefix, bg in (("Panel", c["surface"]), ("Alt",
                                                         c["surface_alt"])):
                name = f"{prefix}.{kind}"
                s.layout(name, layout)
                s.configure(name, background=bg, foreground=c["ink"],
                            font=self.f_body, focuscolor=bg,
                            padding=(self.px(2), self.px(3)))
                s.map(name,
                      background=[("active", bg)],
                      foreground=[("disabled", c["disabled"]),
                                  ("active", c["ink"])])

        # 输入控件：平底细描边，聚焦时描边转强调色
        s.configure("TEntry", fieldbackground=c["surface"],
                    foreground=c["ink"], insertcolor=c["ink"],
                    bordercolor=c["border_hi"], lightcolor=c["border_hi"],
                    darkcolor=c["border_hi"], borderwidth=1, relief="flat",
                    padding=(self.px(6), self.px(4)))
        s.map("TEntry",
              bordercolor=[("focus", c["accent"]), ("disabled", c["border"])],
              lightcolor=[("focus", c["accent"])],
              darkcolor=[("focus", c["accent"])],
              foreground=[("disabled", c["disabled"])],
              fieldbackground=[("disabled", c["surface_alt"])])

        # light/dark 与底色同色：clam 会用它们给下拉箭头描一圈立体边框，
        # 同色即"抹掉"那圈边，只留 bordercolor 画的外框，控件才是平的。
        s.configure("TCombobox", fieldbackground=c["surface"],
                    background=c["surface"], foreground=c["ink"],
                    bordercolor=c["border_hi"], lightcolor=c["surface"],
                    darkcolor=c["surface"], arrowcolor=c["muted"],
                    borderwidth=1, relief="flat",
                    padding=(self.px(6), self.px(3)))
        s.map("TCombobox",
              bordercolor=[("focus", c["accent"]), ("disabled", c["border"])],
              arrowcolor=[("disabled", c["disabled"]),
                          ("active", c["accent"])],
              fieldbackground=[("disabled", c["surface_alt"])],
              foreground=[("disabled", c["disabled"])])
        # 下拉列表（是 Tk 部件，不吃 ttk 样式，只能用 option 设）
        self.root.option_add("*TCombobox*Listbox.background", c["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground",
                             c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.root.option_add("*TCombobox*Listbox.font", self.f_body)

        # 细滚动条：无箭头、无边框
        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=c["border_hi"],
                        troughcolor=c["canvas"], bordercolor=c["canvas"],
                        arrowcolor=c["canvas"], relief="flat", borderwidth=0,
                        arrowsize=self.px(1), width=self.px(10))
            s.map(f"{orient}.TScrollbar",
                  background=[("pressed", c["muted"]),
                              ("active", c["subtle"])])

        s.configure("TSeparator", background=c["border"])
        s.configure("TSizegrip", background=c["surface"])

    # ---- 勾选框 / 单选钮的指示器 ----
    def _init_indicators(self, s):
        """自绘指示器：clam 自带的把"选中"画成一个 ✕，容易被读成"否"，
        尺寸也偏小。这里改画圆角方框 + 白色对勾（单选钮为圆点）。
        """
        c = self.c
        box = self.px(15)
        gap = self.px(7)          # 指示器与文字之间的留白
        ss = 4                    # 超采样倍数，缩回去即得平滑边缘

        def render(shape, on, disabled):
            im = Image.new("RGBA", ((box + gap) * ss, box * ss), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            edge = box * ss - 1
            if on:
                fill = c["disabled"] if disabled else c["accent"]
                line = fill
            else:
                fill = c["surface_alt"] if disabled else c["surface"]
                line = c["border"] if disabled else c["border_hi"]
            w = max(1, round(1.3 * ss))
            if shape == "check":
                d.rounded_rectangle([0, 0, edge, edge], radius=int(3 * ss),
                                    fill=fill, outline=line, width=w)
                if on:      # 对勾
                    pts = [(0.26, 0.53), (0.44, 0.71), (0.75, 0.32)]
                    d.line([(x * edge, y * edge) for x, y in pts],
                           fill="#ffffff", width=max(1, round(1.7 * ss)),
                           joint="curve")
            else:
                d.ellipse([0, 0, edge, edge], fill=fill, outline=line, width=w)
                if on:      # 圆点
                    r = edge * 0.21
                    mid = edge / 2
                    d.ellipse([mid - r, mid - r, mid + r, mid + r],
                              fill="#ffffff")
            im = im.resize((box + gap, box), Image.LANCZOS)
            return ImageTk.PhotoImage(im, master=self.root)

        for kind, shape in (("Checkbutton", "check"), ("Radiobutton", "dot")):
            imgs = {st: render(shape, on, dis)
                    for st, (on, dis) in (("off", (False, False)),
                                          ("on", (True, False)),
                                          ("off_dis", (False, True)),
                                          ("on_dis", (True, True)))}
            # 图片必须留引用，否则被回收后指示器会变成空白
            self._indicator_imgs[kind] = imgs
            name = f"Modern.{kind}.indicator"
            # 状态组合越具体越要靠前：先匹配到的先生效
            s.element_create(
                name, "image", imgs["off"],
                ("disabled", "selected", imgs["on_dis"]),
                ("disabled", imgs["off_dis"]),
                ("selected", imgs["on"]),
                border=0, sticky="")
            self._indicator_elems[f"T{kind}"] = name

    def _indicator_layout(self, kind):
        """把自绘指示器装进标准的 padding › indicator + focus›label 结构。"""
        elem = self._indicator_elems[kind]
        base = kind[1:]        # TCheckbutton -> Checkbutton
        return [(f"{base}.padding", {"sticky": "nswe", "children": [
            (elem, {"side": "left", "sticky": ""}),
            (f"{base}.focus", {"side": "left", "sticky": "w", "children": [
                (f"{base}.label", {"sticky": "nswe"})]})]})]


def hairline(parent, theme, horizontal=True, color=None):
    """1 物理像素的分隔线——比 ttk.Separator 更细、更可控。"""
    kw = {"bg": color or theme.c["border"], "bd": 0, "highlightthickness": 0}
    if horizontal:
        return tk.Frame(parent, height=1, **kw)
    return tk.Frame(parent, width=1, **kw)
