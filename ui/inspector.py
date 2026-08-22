"""右侧属性检查器：当前图件的全部参数，改完即时重绘。

层级就是界面结构：工作区 › 分组 › 控件。分组按"改的是同一件事"划分，
不适用于当前数据类型的分组直接隐藏——不留置灰的死控件。
"""

import tkinter as tk
from tkinter import ttk

from strat import fonts as strat_fonts
from strat.column import PAGE_SIZES, WIDTH_LIMITS

from .state import AUTO_FONT, STRATA_LABELS, THICK_MODES
from .widgets import ScrollFrame, Section, field_row

_PAGE_CHOICES = list(PAGE_SIZES) + ["21x29.7"]
_LBL = 9        # 标签列宽（平均字宽数）：容得下"垂直夸大"这类四字标签


class Inspector(ScrollFrame):
    def __init__(self, parent, theme, state, actions):
        super().__init__(parent, theme)
        self.theme = theme
        self.state = state
        self.actions = actions       # 由主窗口提供的命令（打开/示例/模板…）
        self._sections = {}
        self._build()
        # 子列跟随父类别置灰：挂在变量上而不是勾选框的 command 上，这样
        # 无论谁改（点击、快捷键、程序还原设置）都能同步。
        for var in self.state.strata.values():
            var.trace_add("write", lambda *_a: self._sync_unit_states())
        self.refresh()

    # ---- 构建 ----
    def _build(self):
        self._build_source()
        self._build_page()
        self._build_content()
        self._build_units()
        self._build_widths()

    def _add(self, key, title, **kw):
        sec = Section(self.content, self.theme, title, **kw)
        sec.pack(fill=tk.X)
        self._sections[key] = sec
        return sec

    def _entry(self, parent, var, width=10, commit=None):
        """文本框：连续输入不该每敲一下就重绘，交给主窗口防抖。"""
        e = ttk.Entry(parent, textvariable=var, width=width)
        e.bind("<KeyRelease>", lambda _e: (commit or self.actions.defer)())
        e.bind("<FocusOut>", lambda _e: self.actions.rerender())
        e.bind("<Return>", lambda _e: self.actions.rerender())
        return e

    def _build_source(self):
        sec = self._add("source", "数据源")
        b = sec.body
        self._src_name = ttk.Label(b, text="未载入数据", style="Body.TLabel",
                                   anchor="w")
        self._src_name.pack(fill=tk.X)
        self._src_meta = ttk.Label(b, text="", style="Hint.TLabel", anchor="w")
        self._src_meta.pack(fill=tk.X, pady=(0, self.theme.px(8)))

        row = ttk.Frame(b, style="Surface.TFrame")
        row.pack(fill=tk.X, pady=(0, self.theme.px(8)))
        ttk.Button(row, text="打开数据…", style="Accent.TButton",
                   command=self.actions.open_data).pack(side=tk.LEFT)
        self._btn_reload = ttk.Button(row, text="重新载入", style="Soft.TButton",
                                      command=self.actions.reload)
        self._btn_reload.pack(side=tk.LEFT, padx=(self.theme.px(6), 0))

        ttk.Label(b, text="打开哪种数据就画哪种图，程序按表头自动识别。",
                  style="Hint.TLabel", justify="left",
                  wraplength=self.theme.px(210)).pack(anchor="w",
                                                      pady=(0, self.theme.px(8)))

        # 两个示例是并列关系，用等宽两列；模板下载是另一件事，单独一行
        demo = ttk.Frame(b, style="Surface.TFrame")
        demo.pack(fill=tk.X)
        for i, (text, cmd) in enumerate((("柱状图示例", self.actions.demo_column),
                                         ("剖面图示例", self.actions.demo_section))):
            ttk.Button(demo, text=text, style="Soft.TButton",
                       command=cmd).grid(row=0, column=i, sticky="ew",
                                         padx=(0, self.theme.px(4)))
            demo.columnconfigure(i, weight=1, uniform="demo")
        ttk.Button(b, text="下载数据模板…", style="Soft.TButton",
                   command=self.actions.download_templates).pack(
                       fill=tk.X, pady=(self.theme.px(4), 0))

    def _build_page(self):
        sec = self._add("page", "图面")
        b, t = sec.body, self.theme

        row = field_row(b, t, "图名", label_w=_LBL)
        self._entry(row, self.state.title, width=18).pack(side=tk.LEFT,
                                                          fill=tk.X, expand=True)

        # 页面 / 比例尺只对柱状图有意义（剖面图按画幅自适应）
        self._page_rows = []
        row = field_row(b, t, "页面", label_w=_LBL)
        self._page_rows.append(row)
        cb = ttk.Combobox(row, textvariable=self.state.page, width=9,
                          values=_PAGE_CHOICES)
        cb.pack(side=tk.LEFT)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.actions.rerender())
        cb.bind("<KeyRelease>", lambda _e: self.actions.defer())
        ttk.Checkbutton(row, text="横向", variable=self.state.landscape,
                        style="Panel.TCheckbutton").pack(side=tk.LEFT,
                                                         padx=(t.px(8), 0))

        row = field_row(b, t, "比例尺", label_w=_LBL)
        self._page_rows.append(row)
        ttk.Label(row, text="1 :", style="Field.TLabel").pack(side=tk.LEFT)
        self._entry(row, self.state.scale, width=7).pack(side=tk.LEFT,
                                                         padx=(t.px(4), 0))
        ttk.Label(row, text="留空自动", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(t.px(6), 0))

        # 垂直夸大只对剖面图有意义
        self._ve_row = field_row(b, t, "垂直夸大", label_w=_LBL)
        self._entry(self._ve_row, self.state.ve, width=7).pack(side=tk.LEFT)
        ttk.Label(self._ve_row, text="× 倍，留空自动", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(t.px(6), 0))

        row = self._font_row = field_row(b, t, "字体", label_w=_LBL)
        self._font_cb = ttk.Combobox(
            row, textvariable=self.state.font, width=14, state="readonly",
            values=[AUTO_FONT] + [cn for cn, _ in
                                  strat_fonts.available_families()])
        self._font_cb.pack(side=tk.LEFT)
        self._font_cb.bind("<<ComboboxSelected>>",
                           lambda _e: self.actions.rerender())

        row = field_row(b, t, "字号", label_w=_LBL)
        self._entry(row, self.state.font_size, width=7).pack(side=tk.LEFT)
        ttk.Label(row, text="pt（6–12）", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(t.px(6), 0))

        # 花纹层厚同时影响柱状图、剖面图及其图例，因此不能
        # 放进只在柱状图中显示的 _page_rows。
        row = field_row(b, t, "花纹层厚", label_w=_LBL, pady=0)
        self._entry(row, self.state.pattern_row_height_mm, width=7).pack(
            side=tk.LEFT)
        ttk.Label(row, text="mm（1–10，默认 2.5）", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(t.px(6), 0))

    def _build_content(self):
        sec = self._add("content", "栏目内容")
        b, t = sec.body, self.theme

        ttk.Label(b, text="厚度栏显示", style="Field.TLabel").pack(anchor="w")
        box = ttk.Frame(b, style="Surface.TFrame")
        box.pack(fill=tk.X, pady=(t.px(2), t.px(10)))
        for key, label in THICK_MODES:
            ttk.Radiobutton(box, text=label, value=key,
                            variable=self.state.thick_mode,
                            style="Panel.TRadiobutton").pack(anchor="w")

        ttk.Label(b, text="附加栏目", style="Field.TLabel").pack(anchor="w")
        box = ttk.Frame(b, style="Surface.TFrame")
        box.pack(fill=tk.X, pady=(t.px(2), t.px(10)))
        for text, var in (("备注栏", self.state.show_remark),
                          ("岩性图例（GB/T 958 花纹）", self.state.show_legend)):
            ttk.Checkbutton(box, text=text, variable=var,
                            style="Panel.TCheckbutton").pack(anchor="w")

        ttk.Label(b, text="排版", style="Field.TLabel").pack(anchor="w")
        ttk.Checkbutton(b, text="地层单位文字竖排",
                        variable=self.state.unit_vertical,
                        style="Panel.TCheckbutton").pack(anchor="w",
                                                         pady=(t.px(2), 0))

    def _build_units(self):
        sec = self._add("units", "地层单位列",
                        hint="先选类别，再逐列微调；类别关掉时其下各列一并不出图。")
        self._units_box = ttk.Frame(sec.body, style="Surface.TFrame")
        self._units_box.pack(fill=tk.X)

    def _build_widths(self):
        sec = self._add("widths", "栏宽", collapsed=True,
                        hint="单位厘米，留空用默认值；超范围自动夹紧。"
                             "“岩性描述”栏为其余各栏的余量；“图例”表示"
                             "图底每个图例项的目标宽度。")
        ttk.Button(sec.head_actions, text="恢复默认", style="Soft.TButton",
                   command=self._reset_widths).pack()
        b, t = sec.body, self.theme
        for name, (lo, hi) in WIDTH_LIMITS.items():
            label = "图例项" if name == "图例" else name
            row = field_row(b, t, label, label_w=9)
            self._entry(row, self.state.widths[name], width=6).pack(
                side=tk.LEFT)
            ttk.Label(row, text=f"{lo:g}–{hi:g}", style="Hint.TLabel").pack(
                side=tk.LEFT, padx=(t.px(6), 0))

    def _reset_widths(self):
        for var in self.state.widths.values():
            var.set("")
        self.actions.rerender()

    # ---- 随数据刷新 ----
    def refresh(self):
        """按当前数据类型显示对应分组——不适用的分组整组隐藏。"""
        kind = self.state.kind
        self._src_name.configure(
            text=self.state.filename or "未载入数据",
            style="Body.TLabel" if kind else "Muted.TLabel")
        self._src_meta.configure(text=self.state.summary())
        self._btn_reload.configure(state="normal" if kind else "disabled")

        applies = {"page": kind is not None,
                   "content": kind == "column",
                   # 没有地层单位列的数据就别摆一个空分组
                   "units": kind == "column" and bool(self.state.unit_columns()),
                   "widths": kind == "column"}
        for key, wanted in applies.items():
            sec = self._sections[key]
            sec.pack(fill=tk.X) if wanted else sec.pack_forget()

        # 页面/比例尺（柱状图）与垂直夸大（剖面图）占同一位置：谁适用谁出现。
        # pack(before=字体行) 同时管显示与顺序，避免重排时行序错乱。
        pad = (0, self.theme.px(6))
        for row in self._page_rows:
            if kind == "column":
                row.pack(fill=tk.X, pady=pad, before=self._font_row)
            else:
                row.pack_forget()
        if kind == "section":
            self._ve_row.pack(fill=tk.X, pady=pad, before=self._font_row)
        else:
            self._ve_row.pack_forget()

        self._rebuild_units()

    def _rebuild_units(self):
        for w in self._units_box.winfo_children():
            w.destroy()
        cols = self.state.unit_columns()
        if not cols:
            return
        t = self.theme
        by_cat = {}
        for key, head, cat in cols:
            by_cat.setdefault(cat, []).append(head)

        for cat, label in STRATA_LABELS:
            if cat not in by_cat:
                continue          # 当前数据没有这一类，不占版面
            cat_var = self.state.strata[cat]
            ttk.Checkbutton(self._units_box, text=label, variable=cat_var,
                            style="Panel.TCheckbutton").pack(
                                anchor="w", pady=(t.px(4), 0))
            kids = ttk.Frame(self._units_box, style="Surface.TFrame")
            kids.pack(fill=tk.X, padx=(t.px(18), 0))
            for head in by_cat[cat]:
                cb = ttk.Checkbutton(kids, text=head,
                                     variable=self.state.hide_units[head],
                                     style="Panel.TCheckbutton")
                cb.pack(anchor="w")
                cb._cat = cat     # 供 _sync_unit_states 按父级置灰
        self._sync_unit_states()

    def _sync_unit_states(self):
        """子列跟随父类别启用/禁用——父子关系在界面上看得见。"""
        for kids in self._units_box.winfo_children():
            for cb in getattr(kids, "winfo_children", lambda: [])():
                cat = getattr(cb, "_cat", None)
                if cat:
                    cb.configure(state="normal"
                                 if self.state.strata[cat].get()
                                 else "disabled")
