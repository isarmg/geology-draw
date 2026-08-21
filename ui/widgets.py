"""可复用控件：折叠分组、分段切换、表单行、可缩放图纸视图。

这里只管"长什么样、怎么交互"，不认识地层业务。
"""

import tkinter as tk
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .theme import hairline

ZOOM_STEPS = (0.25, 0.33, 0.5, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)


class Section(ttk.Frame):
    """检查器里的一个折叠分组：标题行 + 主体。

    分组是"属性的父级"——同一分组内的控件互为兄弟且真的相关；
    不适用于当前文档的分组直接 hide()，而不是留在原地置灰。
    """

    def __init__(self, parent, theme, title, collapsed=False, hint=None):
        super().__init__(parent, style="Surface.TFrame")
        self.theme = theme
        self._open = not collapsed

        head = ttk.Frame(self, style="Surface.TFrame",
                         padding=(theme.px(14), theme.px(9)))
        head.pack(fill=tk.X)
        self._chev = ttk.Label(head, text="", style="Subtle.TLabel",
                               width=2)
        self._chev.pack(side=tk.LEFT)
        self._title = ttk.Label(head, text=title, style="Section.TLabel")
        self._title.pack(side=tk.LEFT)
        # 标题行右侧留给分组级动作（如"恢复默认"）
        self.head_actions = ttk.Frame(head, style="Surface.TFrame")
        self.head_actions.pack(side=tk.RIGHT)

        self.body = ttk.Frame(self, style="Surface.TFrame",
                              padding=(theme.px(14), 0, theme.px(14),
                                       theme.px(12)))
        if hint:
            ttk.Label(self.body, text=hint, style="Hint.TLabel",
                      justify="left", wraplength=theme.px(210)).pack(
                          anchor="w", pady=(0, theme.px(8)))
        self._rule = hairline(self, theme)
        self._rule.pack(fill=tk.X, side=tk.BOTTOM)

        for w in (head, self._chev, self._title):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e: self.toggle())
        self._sync()

    def toggle(self):
        self._open = not self._open
        self._sync()

    def _sync(self):
        self._chev.configure(text="▾" if self._open else "▸")
        if self._open:
            self.body.pack(fill=tk.X, before=self._rule)
        else:
            self.body.pack_forget()


class ScrollFrame(ttk.Frame):
    """纵向可滚动的容器；内容装进 .content，宽度始终跟随视口。"""

    def __init__(self, parent, theme, bg=None):
        super().__init__(parent, style="Surface.TFrame")
        self.theme = theme
        bg = bg or theme.c["surface"]
        self._scroll = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._scroll_sb = ttk.Scrollbar(self, orient="vertical",
                                 command=self._scroll.yview)
        self._scroll.configure(yscrollcommand=self._on_scroll)
        self._scroll_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._scroll.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.content = ttk.Frame(self._scroll, style="Surface.TFrame")
        self._scroll_win = self._scroll.create_window(0, 0, window=self.content,
                                               anchor="nw")
        self.content.bind("<Configure>", lambda _e: self._sync())
        self._scroll.bind("<Configure>", lambda e: self._scroll.itemconfigure(
            self._scroll_win, width=e.width))

    def _on_scroll(self, lo, hi):
        # 内容装得下时不占位显示滚动条
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self._scroll_sb.pack_forget()
        else:
            self._scroll_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._scroll_sb.set(lo, hi)

    def _sync(self):
        self._scroll.configure(scrollregion=self._scroll.bbox("all"))

    def on_wheel(self, ev):
        first, last = self._scroll.yview()
        if first <= 0.0 and last >= 1.0:
            return
        up = getattr(ev, "delta", 0) > 0 or getattr(ev, "num", 0) == 4
        self._scroll.yview_scroll(-2 if up else 2, "units")


class Segmented(ttk.Frame):
    """分段切换：一组互斥视图，同层级、同时可见，选中项高亮。"""

    def __init__(self, parent, theme, options, command=None, value=None):
        super().__init__(parent, style="Alt.TFrame", padding=theme.px(2))
        self._command = command
        self._buttons = {}
        for key, label in options:
            b = ttk.Button(self, text=label, style="Seg.TButton",
                           command=lambda k=key: self.set(k, notify=True))
            b.pack(side=tk.LEFT)
            self._buttons[key] = b
        self._value = None
        self.set(value or options[0][0])

    def set(self, key, notify=False):
        if key not in self._buttons:
            return
        self._value = key
        for k, b in self._buttons.items():
            b.configure(style="SegOn.TButton" if k == key else "Seg.TButton")
        if notify and self._command:
            self._command(key)

    def get(self):
        return self._value


def field_row(parent, theme, label, label_w=6, pady=None, alt=False):
    """一行"标签 + 控件"，返回行框架供调用方塞控件。"""
    style = "Alt.TFrame" if alt else "Surface.TFrame"
    row = ttk.Frame(parent, style=style)
    row.pack(fill=tk.X, pady=(0, theme.px(6) if pady is None else pady))
    if label:
        ttk.Label(row, text=label, style="Field.TLabel", width=label_w,
                  anchor="w").pack(side=tk.LEFT)
    return row


class FigureView(ttk.Frame):
    """图纸视图：按真实尺寸显示 matplotlib 图，可滚动、可缩放。

    图必须按自己的物理尺寸显示——用户设的页面规格才有意义，所以画布
    不随窗口拉伸（那会让 TkAgg 把图尺寸改成窗口大小），而是放进可滚动
    容器并居中；缩放通过改 dpi 实现，文字始终矢量清晰。
    """

    def __init__(self, parent, theme, empty_title="", empty_hint=""):
        super().__init__(parent, style="Canvas.TFrame")
        self.theme = theme
        self._fig = None
        self._canvas = None
        self._zoom = 1.0
        self._base_dpi = 100.0

        self._holder = tk.Canvas(self, bg=theme.c["canvas"],
                                 highlightthickness=0, bd=0)
        self._vsb = ttk.Scrollbar(self, orient="vertical",
                                  command=self._holder.yview)
        self._hsb = ttk.Scrollbar(self, orient="horizontal",
                                  command=self._holder.xview)
        self._holder.configure(yscrollcommand=self._on_vsb,
                               xscrollcommand=self._on_hsb)

        bar = self._build_zoombar(theme)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        hairline(self, theme).pack(side=tk.BOTTOM, fill=tk.X)
        self._holder.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 空状态：没有图时给出下一步提示，而不是一片空白
        self._empty = ttk.Frame(self._holder, style="Canvas.TFrame")
        ttk.Label(self._empty, text=empty_title, style="Empty.TLabel",
                  justify="center").pack()
        ttk.Label(self._empty, text=empty_hint, style="EmptyDim.TLabel",
                  justify="center").pack(pady=(theme.px(6), 0))
        self._empty_id = self._holder.create_window(0, 0, window=self._empty,
                                                    anchor="center")

        # 图纸：白底 + 一圈细边，从背景里"浮"出来
        self._paper = tk.Frame(self._holder, bg=theme.c["border_hi"], bd=0,
                               highlightthickness=0)
        self._paper_id = self._holder.create_window(0, 0, window=self._paper,
                                                    anchor="nw", state="hidden")
        self._holder.bind("<Configure>", lambda _e: self._reflow())

    # 滚动条只在真的滚得动时才出现，空视图下不留两条死轨道。
    # before=holder 保证重新出现时仍插在画布之前，位置不会跳。
    def _on_vsb(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self._vsb.pack_forget()
        else:
            self._vsb.pack(side=tk.RIGHT, fill=tk.Y, before=self._holder)
        self._vsb.set(lo, hi)

    def _on_hsb(self, lo, hi):
        if float(lo) <= 0.0 and float(hi) >= 1.0:
            self._hsb.pack_forget()
        else:
            self._hsb.pack(side=tk.BOTTOM, fill=tk.X, before=self._holder)
        self._hsb.set(lo, hi)

    def _build_zoombar(self, theme):
        bar = ttk.Frame(self, style="Surface.TFrame",
                        padding=(theme.px(8), theme.px(3)))
        # 顺序即语义：减号、当前倍率、加号连成一组，右边才是预设倍率
        self._zoom_btns = []
        minus = ttk.Button(bar, text="－", style="Icon.TButton",
                           command=lambda: self._step(-1))
        minus.pack(side=tk.LEFT)
        self._zoom_label = ttk.Label(bar, text="", style="Status.TLabel",
                                     width=5, anchor="center")
        self._zoom_label.pack(side=tk.LEFT)
        plus = ttk.Button(bar, text="＋", style="Icon.TButton",
                          command=lambda: self._step(1))
        plus.pack(side=tk.LEFT)
        hairline(bar, theme, horizontal=False).pack(
            side=tk.LEFT, fill=tk.Y, padx=theme.px(8), pady=theme.px(3))
        fit = ttk.Button(bar, text="适应窗口", style="Ghost.TButton",
                         command=self.zoom_fit)
        fit.pack(side=tk.LEFT)
        full = ttk.Button(bar, text="100%", style="Ghost.TButton",
                          command=lambda: self.set_zoom(1.0))
        full.pack(side=tk.LEFT)
        self._zoom_btns = [minus, plus, fit, full]
        self._set_zoom_enabled(False)
        return bar

    def _set_zoom_enabled(self, on):
        for b in self._zoom_btns:
            b.configure(state="normal" if on else "disabled")

    # ---- 图 ----
    def set_figure(self, fig, keep_zoom=True):
        if self._canvas is not None:
            self._canvas.get_tk_widget().destroy()
            self._canvas = None
        self._fig = fig
        if fig is None:
            self._holder.itemconfigure(self._paper_id, state="hidden")
            self._holder.itemconfigure(self._empty_id, state="normal")
            self._zoom_label.configure(text="")
            self._set_zoom_enabled(False)
            self._reflow()
            return
        self._set_zoom_enabled(True)
        self._base_dpi = fig.get_dpi()
        # 图的英寸尺寸 = 用户设定的页面规格，是唯一权威，缩放绝不能动它
        self._page_in = tuple(fig.get_size_inches())
        self._canvas = FigureCanvasTkAgg(fig, master=self._paper)
        self._canvas.get_tk_widget().pack(padx=1, pady=1)
        self._holder.itemconfigure(self._empty_id, state="hidden")
        self._holder.itemconfigure(self._paper_id, state="normal")
        if not keep_zoom:
            self._zoom = 1.0
        self._apply_zoom()

    @property
    def figure(self):
        return self._fig

    def has_figure(self):
        return self._fig is not None

    # ---- 缩放 ----
    def set_zoom(self, z):
        self._zoom = max(ZOOM_STEPS[0], min(ZOOM_STEPS[-1], z))
        self._apply_zoom()

    def _step(self, direction):
        cur = self._zoom
        if direction > 0:
            nxt = next((z for z in ZOOM_STEPS if z > cur + 1e-3), cur)
        else:
            nxt = next((z for z in reversed(ZOOM_STEPS) if z < cur - 1e-3), cur)
        self.set_zoom(nxt)

    def zoom_fit(self):
        if self._fig is None:
            return
        w_in, h_in = self._page_in
        pad = self.theme.px(28) * 2
        vw = max(self._holder.winfo_width() - pad, 50)
        vh = max(self._holder.winfo_height() - pad, 50)
        self.set_zoom(min(vw / (w_in * self._base_dpi),
                          vh / (h_in * self._base_dpi)))

    def _apply_zoom(self):
        if self._fig is None or self._canvas is None:
            return
        # 缩放只改 dpi（像素密度），不改英寸尺寸，所以文字始终矢量清晰。
        #
        # 麻烦在于 TkAgg 给画布绑了 <Configure>，其中会用 事件像素/当前dpi
        # 反算并覆盖图的英寸尺寸。改 dpi 后若有一个"旧像素"的 Configure 迟到，
        # 图就会被算成别的尺寸——A4 会悄悄变成 8.26×7.47 英寸，连导出的
        # PDF 页面都跟着错。因此这里先让事件结算，再把权威页面尺寸盖回去；
        # 每次都从 _page_in 重算，避免反复缩放时的取整漂移累积。
        w_in, h_in = self._page_in
        dpi = self._base_dpi * self._zoom
        widget = self._canvas.get_tk_widget()
        self._fig.set_dpi(dpi)
        widget.configure(width=int(round(w_in * dpi)),
                         height=int(round(h_in * dpi)))
        widget.update_idletasks()
        self._fig.set_size_inches(w_in, h_in, forward=False)
        self._zoom_label.configure(text=f"{self._zoom * 100:.0f}%")
        self._canvas.draw_idle()
        self._reflow()

    def _reflow(self):
        """把图纸在视口里居中；图大于视口时才靠边并允许滚动。"""
        self.update_idletasks()
        vw, vh = self._holder.winfo_width(), self._holder.winfo_height()
        self._holder.coords(self._empty_id, vw / 2, vh / 2)
        if self._fig is None or self._canvas is None:
            self._holder.configure(scrollregion=(0, 0, vw, vh))
            return
        pw = self._paper.winfo_reqwidth()
        ph = self._paper.winfo_reqheight()
        pad = self.theme.px(24)
        x = max(pad, (vw - pw) / 2)
        y = max(pad, (vh - ph) / 2)
        self._holder.coords(self._paper_id, x, y)
        self._holder.configure(
            scrollregion=(0, 0, max(vw, x + pw + pad), max(vh, y + ph + pad)))

    # ---- 滚轮：滚动 / Shift 横向 / Ctrl 缩放 ----
    def on_wheel(self, ev):
        if not self.has_figure():
            return
        up = getattr(ev, "delta", 0) > 0 or getattr(ev, "num", 0) == 4
        if ev.state & 0x4:      # Ctrl
            self._step(1 if up else -1)
        elif ev.state & 0x1:    # Shift
            self._holder.xview_scroll(-1 if up else 1, "units")
        else:
            self._holder.yview_scroll(-3 if up else 3, "units")
