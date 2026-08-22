"""主窗口：模式栏 › 工作区 › 分组 › 控件。

顶层只有两个工作区——「图件」画你的数据，「花纹库」管岩性花纹。二者
是真正的兄弟：各自拥有自己的画布、自己的检查器、自己的导出物，互不
干扰；窗口只负责在它们之间切换，并把"导出"转交给当前工作区。
"""

import os
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from strat import (load_style, render_column, render_pattern_sheet,
                   render_section)
from strat.gb958 import CATEGORIES as GB_CATEGORIES, render_catalog_sheet
from strat.lithology import render_shapes_sheet

from .designer import DesignerPanel
from .inspector import Inspector
from .state import ChartState, ValidationError, load_any
from .theme import Theme, hairline
from .widgets import FigureView, ScrollFrame, Segmented

_FILETYPES = [("表格文件", "*.xlsx *.xlsm *.csv"),
              ("Excel 工作簿", "*.xlsx *.xlsm"),
              ("CSV 文件", "*.csv")]
_IMAGETYPES = [("PNG 图片", "*.png"), ("PDF 矢量图", "*.pdf"),
               ("SVG 矢量图", "*.svg")]
_KIND_NAMES = {"column": "综合地层柱状图", "section": "地层剖面图"}


class ChartWorkspace(ttk.Frame):
    """图件工作区：中间图纸 + 右侧检查器。"""

    def __init__(self, parent, theme, state, actions):
        super().__init__(parent, style="Canvas.TFrame")
        self.export_label = "导出图片"
        self.view = FigureView(
            self, theme, empty_title="打开一份数据开始绘图",
            empty_hint="支持 Excel（.xlsx/.xlsm）与 CSV；\n"
                       "柱状图还是剖面图，程序按表头自动识别。")
        panel = ttk.Frame(self, style="Surface.TFrame",
                          width=theme.px(272))
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)
        hairline(self, theme, horizontal=False).pack(side=tk.RIGHT, fill=tk.Y)
        self.view.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inspector = Inspector(panel, theme, state, actions)
        self.inspector.pack(fill=tk.BOTH, expand=True)

    def export_defaults(self, state):
        base = os.path.splitext(state.filename)[0] if state.filename else "图件"
        return base or "图件"


class LibraryWorkspace(ttk.Frame):
    """花纹库工作区：左看全库一览 / 国标分类图例，右设计新花纹。"""

    def __init__(self, parent, theme, actions):
        super().__init__(parent, style="Canvas.TFrame")
        self.actions = actions
        self.export_label = "导出一览表"
        self._sheets = {}
        self._which = "patterns"
        self._shown_key = None
        self._category = tk.StringVar(value=GB_CATEGORIES[1])  # 沉积岩

        panel = ttk.Frame(self, style="Surface.TFrame", width=theme.px(272))
        panel.pack(side=tk.RIGHT, fill=tk.Y)
        panel.pack_propagate(False)
        hairline(self, theme, horizontal=False).pack(side=tk.RIGHT, fill=tk.Y)
        self.designer = DesignerPanel(panel, theme, actions)
        self.designer.pack(fill=tk.BOTH, expand=True)

        main = ttk.Frame(self, style="Canvas.TFrame")
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar = ttk.Frame(main, style="Surface.TFrame",
                        padding=(theme.px(8), theme.px(5)))
        bar.pack(fill=tk.X)
        Segmented(bar, theme, (("patterns", "常用岩性一览"),
                               ("shapes", "基本图形一览"),
                               ("catalog", "国标图例库")),
                  command=self.show_sheet).pack(side=tk.LEFT)
        self._cat_cb = ttk.Combobox(bar, textvariable=self._category,
                                    values=list(GB_CATEGORIES), width=10,
                                    state="readonly")
        self._cat_cb.bind("<<ComboboxSelected>>",
                          lambda _e: self.show_sheet("catalog"))
        hairline(main, theme).pack(fill=tk.X)
        self.view = FigureView(main, theme, empty_title="正在生成花纹一览…")
        self.view.pack(fill=tk.BOTH, expand=True)

    def _sheet_key(self, which):
        return (which if which != "catalog"
                else f"catalog:{self._category.get()}")

    def show_sheet(self, which=None):
        self._which = which or self._which
        # 类别下拉只在"国标图例库"下出现
        if self._which == "catalog":
            self._cat_cb.pack(side=tk.LEFT, padx=(self.actions.theme.px(8), 0))
        else:
            self._cat_cb.pack_forget()
        key = self._sheet_key(self._which)
        if self._sheets.get(key) is None:
            try:
                if self._which == "patterns":
                    self._sheets[key] = render_pattern_sheet()
                elif self._which == "shapes":
                    self._sheets[key] = render_shapes_sheet()
                else:
                    self._sheets[key] = render_catalog_sheet(
                        self._category.get())
            except Exception as e:
                self._shown_key = None
                self.actions._sync_export_state()
                self.actions.status(f"生成失败：{type(e).__name__}: {e}",
                                    warn=True)
                return
        self.view.set_figure(self._sheets[key])
        self._shown_key = key
        self.actions._sync_export_state()
        msg = {"patterns": "常用岩性花纹一览（含自定义花纹）",
               "shapes": "基本图形一览：按行组合这些图形即可拼出自定义花纹",
               "catalog": f"GB/T 958—2015 岩石花纹图例 · "
                          f"{self._category.get()}——数据里直接写标准岩石名"
                          f"即可出图"}[self._which]
        self.actions.status(msg)

    def invalidate(self):
        """花纹/样式/字体变了，一览表要重画。"""
        self._sheets = {}
        self._shown_key = None
        self.actions._sync_export_state()

    def is_exportable(self):
        return (self._shown_key == self._sheet_key(self._which)
                and self.view.has_figure())

    def export_defaults(self, _state):
        if self._which == "catalog":
            return f"国标图例·{self._category.get()}"
        return ("岩性花纹一览" if self._which == "patterns" else "基本图形一览")


class MainWindow(tk.Tk):
    def __init__(self, example_dir, apply_style):
        super().__init__()
        self._example_dir = example_dir
        self._apply_style = apply_style
        self._pending = None
        self._ready = False
        self._input_revision = 0
        self._rendered_revision = -1
        self._render_valid = False

        self.title("地层绘图 — 综合地层柱状图 / 地层剖面图")
        self.theme = Theme(self)
        t = self.theme
        self._place_window(t.px(1300), t.px(880))
        self.configure(bg=t.c["bg"])

        self.state_ = ChartState(self, on_change=lambda: self.defer(80))
        self._build()
        self._bind_keys()
        self._ready = True

        used, err = self._auto_style()
        if err:
            self.after(80, lambda: self.status(err, warn=True))
        elif used:
            self.after(80, lambda: self.status(f"已自动载入样式：{used}"))

    def _place_window(self, want_w, want_h):
        """按理想尺寸开窗，但绝不超出屏幕，并居中摆放。

        理想尺寸是按屏幕 DPI 放大过的：1300×880 在 125% 缩放下就是
        1625×1100。笔记本常见的 1536×864 屏根本放不下，右边的检查器和
        底部状态栏会被推到屏幕外——看起来就像"没有检查器"。所以这里按
        实际可用区域夹紧；最小尺寸同样不能大于窗口，否则窗口缩不回来。
        """
        t = self.theme
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # 留出任务栏与窗口边框的余量
        avail_w, avail_h = sw - t.px(40), sh - t.px(80)
        w, h = min(want_w, avail_w), min(want_h, avail_h)
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(t.px(980), w), min(t.px(640), h))

    def _auto_style(self, data_path=None):
        """自动查找并载入 strat_style.json（数据同目录优先）。

        样式是锦上添花：文件写坏了只提示、不该拦住开图，更不该让异常从
        Tk 回调里漏出去。返回 (载入的路径, 出错说明)。
        """
        try:
            return self._apply_style(data_path=data_path), None
        except Exception as e:
            return None, f"同目录样式文件有误，已忽略：{type(e).__name__}: {e}"

    # ---- 布局 ----
    def _build(self):
        t = self.theme
        self._build_header()
        hairline(self, t).pack(fill=tk.X)
        self._build_statusbar()      # 先占住底部，中间区域才吃剩余空间

        body = ttk.Frame(self, style="Bg.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        self._build_rail(body)
        hairline(body, t, horizontal=False).pack(side=tk.LEFT, fill=tk.Y)

        self._stack = ttk.Frame(body, style="Canvas.TFrame")
        self._stack.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chart = ChartWorkspace(self._stack, t, self.state_, self)
        self.library = LibraryWorkspace(self._stack, t, self)
        self._mode = None
        self.set_mode("chart")

    def _build_header(self):
        t = self.theme
        h = ttk.Frame(self, style="Surface.TFrame",
                      padding=(t.px(16), t.px(9)))
        h.pack(fill=tk.X)
        ttk.Label(h, text="地层绘图", style="Brand.TLabel").pack(side=tk.LEFT)
        self._doc_label = ttk.Label(h, text="", style="Muted.TLabel")
        self._doc_label.pack(side=tk.LEFT, padx=(t.px(12), 0))

        ttk.Button(h, text="❔", style="Icon.TButton",
                   command=self._help_dialog).pack(side=tk.RIGHT)
        self._export_btn = ttk.Button(h, text="导出图片", style="Accent.TButton",
                                      command=self.export)
        self._export_btn.pack(side=tk.RIGHT, padx=(0, t.px(8)))

    def _build_rail(self, parent):
        t = self.theme
        rail = ttk.Frame(parent, style="Rail.TFrame", width=t.px(78),
                         padding=(t.px(6), t.px(10)))
        rail.pack(side=tk.LEFT, fill=tk.Y)
        rail.pack_propagate(False)
        self._rail_btns = {}
        for key, label in (("chart", "📊\n图件"), ("library", "🎨\n花纹库")):
            b = ttk.Button(rail, text=label, style="Rail.TButton",
                           command=lambda k=key: self.set_mode(k))
            b.pack(fill=tk.X, pady=(0, t.px(4)))
            self._rail_btns[key] = b

    def _build_statusbar(self):
        t = self.theme
        hairline(self, t).pack(side=tk.BOTTOM, fill=tk.X)
        sb = ttk.Frame(self, style="Surface.TFrame",
                       padding=(t.px(14), t.px(4)))
        sb.pack(side=tk.BOTTOM, fill=tk.X)
        self._status_var = tk.StringVar(
            value="打开 Excel / CSV 数据开始绘图，或从“数据源 › 下载模板”取模板。")
        self._status_lbl = ttk.Label(sb, textvariable=self._status_var,
                                     style="Status.TLabel", anchor="w")
        self._status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._meta_lbl = ttk.Label(sb, text="", style="File.TLabel")
        self._meta_lbl.pack(side=tk.RIGHT)

    def _bind_keys(self):
        self.bind_all("<Control-o>", lambda _e: self.open_data())
        self.bind_all("<Control-e>", lambda _e: self.export())
        self.bind_all("<Control-Key-1>", lambda _e: self.set_mode("chart"))
        self.bind_all("<Control-Key-2>", lambda _e: self.set_mode("library"))
        self.bind_all("<Control-Key-0>", lambda _e: self._view().zoom_fit())
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(seq, self._on_wheel)

    def _on_wheel(self, ev):
        """滚轮交给指针所在的那块可滚区域——检查器和图纸各滚各的。"""
        w = self.winfo_containing(ev.x_root, ev.y_root)
        while w is not None:
            if isinstance(w, (FigureView, ScrollFrame)):
                w.on_wheel(ev)
                return
            w = getattr(w, "master", None)

    # ---- 模式 ----
    def set_mode(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        for w in (self.chart, self.library):
            w.pack_forget()
        ws = self._workspace()
        ws.pack(fill=tk.BOTH, expand=True)
        for key, b in self._rail_btns.items():
            b.configure(style="RailOn.TButton" if key == mode
                        else "Rail.TButton")
        self._export_btn.configure(text=ws.export_label)
        if mode == "library":
            self.library.show_sheet()
        self._sync_export_state()
        self._sync_header()

    def _workspace(self):
        return self.chart if self._mode == "chart" else self.library

    def _view(self):
        return self._workspace().view

    def _chart_exportable(self):
        return (self._render_valid
                and self._rendered_revision == self._input_revision
                and self.chart.view.has_figure())

    def _workspace_exportable(self):
        if self._mode == "chart":
            return self._chart_exportable()
        return self.library.is_exportable()

    def _sync_export_state(self):
        if not hasattr(self, "_export_btn") or self._mode is None:
            return
        self._export_btn.configure(
            state="normal" if self._workspace_exportable() else "disabled")

    def _mark_chart_dirty(self):
        """任何会影响图件的输入变化，都立即令旧预览失去导出资格。"""
        self._input_revision += 1
        self._render_valid = False
        self._sync_export_state()

    # ---- 状态栏 ----
    def status(self, msg, warn=False):
        self._status_var.set(msg)
        self._status_lbl.configure(
            foreground=self.theme.c["accent" if warn else "muted"])

    def _sync_header(self):
        kind = self.state_.kind
        self._doc_label.configure(
            text=_KIND_NAMES.get(kind, "") if self._mode == "chart"
            else "岩性花纹库")
        self._meta_lbl.configure(
            text=f"{self.state_.summary()} · {self.state_.filename}"
            if kind else "")

    # ---- 绘图 ----
    def defer(self, ms=350):
        """连续输入时合并重绘请求，避免每敲一个字符就重画一次。"""
        if not self._ready:
            return
        self._mark_chart_dirty()
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(ms, self.rerender)

    def rerender(self):
        if self._pending:
            self.after_cancel(self._pending)
            self._pending = None
        if not self._ready:
            return
        st = self.state_
        if st.kind is None:
            self.chart.view.set_figure(None)
            self._render_valid = False
            self._sync_export_state()
            return
        try:
            st.apply_fonts()
            kwargs = st.render_kwargs()
        except ValidationError as e:
            self._render_valid = False
            self._sync_export_state()
            self.status(str(e), warn=True)   # 保留上一张图，别把画布清空
            return
        render = render_column if st.kind == "column" else render_section
        try:
            fig = render(st.data, **kwargs)
        except Exception as e:
            self._render_valid = False
            self._sync_export_state()
            self.status(f"绘图失败：{type(e).__name__}: {e}", warn=True)
            return
        self.chart.view.set_figure(fig)
        self._rendered_revision = self._input_revision
        self._render_valid = True
        self._sync_export_state()
        self.library.invalidate()        # 字体会影响花纹一览
        if self._mode == "library":
            self.library.show_sheet()
        self.status(f"已绘制{_KIND_NAMES[st.kind]}：{st.filename}")
        self._sync_header()

    # ---- 数据 ----
    def open_data(self):
        path = filedialog.askopenfilename(title="打开数据（Excel / CSV）",
                                          filetypes=_FILETYPES)
        if path:
            self.load(path)

    def load(self, path):
        try:
            kind, data = load_any(path)
        except Exception as e:
            messagebox.showerror("读取失败", f"{type(e).__name__}: {e}",
                                 parent=self)
            return
        self._mark_chart_dirty()
        self.state_.set_data(kind, path, data)
        _used, err = self._auto_style(data_path=path)   # 数据同目录的样式优先
        self.set_mode("chart")
        self.chart.inspector.refresh()
        self.library.designer.refresh_liths(self.data_liths())
        self.library.invalidate()
        self.rerender()
        self.chart.view.zoom_fit()
        if err:
            self.status(err, warn=True)   # 盖过重绘的成功提示：这条更要紧

    def reload(self):
        if self.state_.path:
            self.load(self.state_.path)

    def demo_column(self):
        self.load(os.path.join(self._example_dir, "column_demo.xlsx"))

    def demo_section(self):
        self.load(os.path.join(self._example_dir, "section_demo.xlsx"))

    def load_style(self):
        path = filedialog.askopenfilename(title="载入样式（JSON）",
                                          filetypes=[("样式文件", "*.json")])
        if not path:
            return
        try:
            names = load_style(path)
        except Exception as e:
            messagebox.showerror("样式载入失败", f"{type(e).__name__}: {e}",
                                 parent=self)
            return
        self._mark_chart_dirty()
        self.library.invalidate()
        if self._mode == "library":
            self.library.show_sheet()
        self.rerender()
        self.status(f"已载入样式：{os.path.basename(path)}（{len(names)} 个岩性）")

    def data_liths(self):
        """当前数据里出现的岩性名（按出现顺序去重），供设计器下拉候选。"""
        st = self.state_
        names = []
        if st.kind == "column":
            names = [ly["lith"] for ly in st.data]
        elif st.kind == "section":
            names = [ly[1] for bh in st.data for ly in bh["layers"]]
        seen, out = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def pattern_registered(self, name, lith):
        """设计器应用了新花纹：一览表与图件都要跟着更新。"""
        self.library.invalidate()
        # 即使没有绑定新岩性，也可能覆盖了当前图正在使用的同名花纹；
        # 柱状图和剖面图都必须刷新，不能留下可导出的旧图。
        self._mark_chart_dirty()
        if self.state_.kind is not None:
            self.rerender()
        elif self._mode == "library":
            self.library.show_sheet()
        self.status(f"已应用花纹“{name}”"
                    + (f" → 岩性“{lith}”，图件已更新" if lith
                       else "，花纹库与图件已刷新"))

    def download_templates(self):
        # 优先给空白模板：在完整示例上直接追加数据，会把示例一起画出来
        wanted = (("column_template.xlsx", "column_demo.xlsx",
                   "柱状图模板.xlsx"),
                  ("section_template.xlsx", "section_demo.xlsx",
                   "剖面图模板.xlsx"))
        picks = []
        for tpl, demo, outname in wanted:
            src = os.path.join(self._example_dir, tpl)
            if not os.path.isfile(src):
                src, outname = os.path.join(self._example_dir, demo), demo
            if os.path.isfile(src):
                picks.append((src, outname))
        if not picks:
            messagebox.showerror("下载模板", "未找到内置模板文件。", parent=self)
            return
        dest = filedialog.askdirectory(title="选择保存模板的文件夹")
        if not dest:
            return
        try:
            for src, outname in picks:
                shutil.copyfile(src, os.path.join(dest, outname))
        except OSError as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}",
                                 parent=self)
            return
        messagebox.showinfo(
            "下载完成",
            "已保存到：\n" + dest + "\n\n"
            + "、".join(n for _s, n in picks)
            + "\n\n模板里只有表头和几行【示范】数据。请把示范行替换成你自己的"
              "数据或删除，不要保留——否则绘图时示范数据会跟着画出来。",
            parent=self)
        self.status(f"已下载数据模板到：{dest}")

    # ---- 导出 ----
    def export(self):
        ws = self._workspace()
        if not self._workspace_exportable():
            self.status("当前预览尚未成功更新，不能导出旧图。", warn=True)
            return
        path = filedialog.asksaveasfilename(
            title=ws.export_label, defaultextension=".png",
            initialfile=ws.export_defaults(self.state_),
            filetypes=_IMAGETYPES)
        if not path:
            return
        try:
            # 导出用 300 dpi，与屏幕缩放无关；显示 dpi 只影响预览
            ws.view.figure.savefig(path, dpi=300, facecolor="white")
        except Exception as e:
            messagebox.showerror("导出失败", f"{type(e).__name__}: {e}",
                                 parent=self)
            return
        self.status(f"已导出：{path}")

    # ---- 帮助 ----
    def _help_dialog(self):
        t = self.theme
        dlg = tk.Toplevel(self)
        dlg.title("帮助")
        dlg.configure(bg=t.c["surface"])
        dlg.transient(self)
        dlg.resizable(False, False)
        body = ttk.Frame(dlg, style="Surface.TFrame",
                         padding=(t.px(20), t.px(16)))
        body.pack(fill=tk.BOTH, expand=True)

        blocks = (
            ("数据格式",
             "数据放在 Excel 第一张有数据的工作表（或 CSV），首行为列名。\n"
             "柱状图：宙代纪世期 / 宇界系统阶 / 群组段等地层单位列（可选）、\n"
             "　　　　岩性、厚度、描述、备注、接触关系、压缩\n"
             "剖面图：钻孔、距离、孔口标高、层号、岩性、厚度、接触关系\n"
             "只有「岩性」「厚度」必填；有「钻孔」列即按剖面图识别。"),
            ("快捷键",
             "Ctrl+O 打开数据　　Ctrl+E 导出　　Ctrl+1 / Ctrl+2 切换工作区\n"
             "Ctrl+0 适应窗口　　Ctrl+滚轮 缩放　　Shift+滚轮 横向滚动"),
            ("关于",
             "地层绘图 · 基于 Python + matplotlib\n"
             "内置 90 余种岩性花纹与成分修饰自动组合，支持自定义样式、\n"
             "厚层压缩、图底 GB/T 958 花纹图例、多种页面与字体；可导出 PNG / PDF / SVG。"),
        )
        for i, (title, text) in enumerate(blocks):
            ttk.Label(body, text=title, style="Section.TLabel").pack(
                anchor="w", pady=(0 if not i else t.px(14), t.px(4)))
            ttk.Label(body, text=text, style="Muted.TLabel",
                      justify="left").pack(anchor="w")
        ttk.Button(body, text="知道了", style="Accent.TButton",
                   command=dlg.destroy).pack(anchor="e", pady=(t.px(18), 0))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.update_idletasks()
        # 居中到主窗口
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()


def run(example_dir, apply_style):
    """启动图形界面。apply_style(data_path=None) 由调用方注入。"""
    import matplotlib
    matplotlib.use("TkAgg")
    if sys.platform == "win32":
        _enable_dpi_awareness()
    MainWindow(example_dir, apply_style).mainloop()


def _enable_dpi_awareness():
    """Windows 高分屏：避免界面被系统位图拉伸而模糊。"""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
