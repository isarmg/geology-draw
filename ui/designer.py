"""花纹设计器：按行拼基本图形，实时预览，绑定到岩性。

它不再是个模态对话框——花纹本来就属于"花纹库"工作区，和花纹一览表
是同一件事的两面（左边设计、右边看全库），所以做成常驻面板。
"""

import json
import os
import shutil
import tempfile
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

import matplotlib.colors as mcolors
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.patches as mpatches

from strat import lithology as L

from .widgets import ScrollFrame, Section, field_row

_DEFAULT_FACE = "#f2efe6"
_SEED_ROWS = (("横线", "1"), ("中点", "1"), ("空心圆", "2"))


def _read_style_object(path):
    """读取可追加的样式文件；损坏内容绝不能被当成空文件覆盖。"""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("现有样式文件的顶层不是 JSON 对象")
    for section in ("patterns", "lithology", "aliases"):
        value = data.get(section)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"现有样式文件的 {section} 段不是 JSON 对象")
    return data


def _write_style_atomic(path, data):
    """先完整落盘再原子替换；已有文件同时保留一个 .bak 备份。"""
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.", suffix=".tmp", dir=directory)
    existed = os.path.isfile(target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if existed:
            os.chmod(temporary, os.stat(target).st_mode)
            shutil.copy2(target, target + ".bak")
        os.replace(temporary, target)
        # POSIX 上同步目录项；Windows 不允许打开目录，失败时忽略即可。
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_style_entry(path, name, spec, lith="", color=None):
    """安全地把一个设计器条目合并进样式文件，供 UI 和测试共同使用。"""
    name = (name or "").strip()
    lith = (lith or "").strip()
    if not name:
        raise ValueError("花纹名不能为空")
    normalized = L.normalize_spec(spec, where=f"花纹“{name}”")
    if color is not None and not mcolors.is_color_like(color):
        raise ValueError("底色请填 #RRGGBB 形式的颜色")

    data = _read_style_object(path)
    data.setdefault("patterns", {})[name] = normalized
    if lith:
        data.setdefault("lithology", {})[lith] = {
            "color": color or _DEFAULT_FACE,
            "pattern": name,
        }
        data.setdefault("aliases", {})[lith] = lith
    _write_style_atomic(path, data)


class DesignerPanel(ScrollFrame):
    def __init__(self, parent, theme, actions):
        super().__init__(parent, theme)
        self.theme = theme
        self.actions = actions
        self._rows = []
        self._shapes = list(L.BASIC_SHAPES.keys())

        self.name = tk.StringVar(value="我的花纹")
        self.lith = tk.StringVar()
        self.face = tk.StringVar(value=_DEFAULT_FACE)
        self.tile = tk.StringVar(value="1.3")

        self._build_designer()
        self._build_style_file()
        for shape, h in _SEED_ROWS:
            self.add_row(shape, h)
        self.refresh_preview()

    # ---- 构建 ----
    def _build_designer(self):
        sec = Section(self.content, self.theme, "花纹设计器")
        sec.pack(fill=tk.X)
        b, t = sec.body, self.theme

        row = field_row(b, t, "名称", label_w=8)
        ttk.Entry(row, textvariable=self.name, width=16).pack(
            side=tk.LEFT, fill=tk.X, expand=True)

        row = field_row(b, t, "用于岩性", label_w=8)
        self._lith_cb = ttk.Combobox(row, textvariable=self.lith, width=14)
        self._lith_cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(b, text="填数据里的岩性名（已载入数据时可直接下拉选），"
                          "绘图时该岩性就用这个花纹；留空只入库、不绑定。",
                  style="Hint.TLabel",
                  justify="left", wraplength=t.px(210)).pack(
                      anchor="w", pady=(0, t.px(8)))

        row = field_row(b, t, "底色", label_w=8)
        self._swatch = tk.Label(row, width=2, relief="flat", bd=0,
                                cursor="hand2",
                                highlightthickness=1,
                                highlightbackground=t.c["border_hi"])
        self._swatch.pack(side=tk.LEFT, padx=(0, t.px(6)))
        self._swatch.bind("<Button-1>", lambda _e: self._pick_color())
        e = ttk.Entry(row, textvariable=self.face, width=9)
        e.pack(side=tk.LEFT)
        e.bind("<KeyRelease>", lambda _e: self.refresh_preview())

        row = field_row(b, t, "瓦片高度", label_w=8)
        e = ttk.Entry(row, textvariable=self.tile, width=6)
        e.pack(side=tk.LEFT)
        e.bind("<KeyRelease>", lambda _e: self.refresh_preview())
        ttk.Label(row, text="整块瓦片的高度倍率", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(t.px(6), 0))

        head = ttk.Frame(b, style="Surface.TFrame")
        head.pack(fill=tk.X, pady=(t.px(6), t.px(2)))
        ttk.Label(head, text="各行（自上而下）", style="Field.TLabel").pack(
            side=tk.LEFT)
        ttk.Button(head, text="＋ 添加", style="Soft.TButton",
                   command=lambda: (self.add_row(), self.refresh_preview())
                   ).pack(side=tk.RIGHT)
        self._rows_box = ttk.Frame(b, style="Surface.TFrame")
        self._rows_box.pack(fill=tk.X)

        # 预览：柱内平铺大样 + 图例小样（两者应一致——所见即所得）
        ttk.Label(b, text="预览（左：柱内平铺　右：图例小样）",
                  style="Field.TLabel").pack(
            anchor="w", pady=(t.px(10), t.px(4)))
        holder = ttk.Frame(b, style="Surface.TFrame")
        holder.pack(fill=tk.X)
        self._fig = Figure(figsize=(2.3, 2.4), dpi=100)
        self._fig.patch.set_facecolor(t.c["surface"])
        self._canvas = FigureCanvasTkAgg(self._fig, master=holder)
        self._canvas.get_tk_widget().pack()

        bar = ttk.Frame(b, style="Surface.TFrame")
        bar.pack(fill=tk.X, pady=(t.px(10), 0))
        ttk.Button(bar, text="应用到图件", style="Accent.TButton",
                   command=self.apply_pattern).pack(side=tk.LEFT)
        ttk.Button(bar, text="保存到样式文件…", style="Soft.TButton",
                   command=self.save_to_style).pack(side=tk.LEFT,
                                                    padx=(t.px(6), 0))

    def _build_style_file(self):
        sec = Section(self.content, self.theme, "样式文件",
                      hint="样式文件（JSON）里可存自定义花纹、岩性配色与别名。"
                           "数据同目录下的 strat_style.json 会自动载入。")
        sec.pack(fill=tk.X)
        ttk.Button(sec.body, text="载入样式…", style="Soft.TButton",
                   command=self.actions.load_style).pack(anchor="w")

    # ---- 行 ----
    def add_row(self, shape="横线", height="1"):
        t = self.theme
        rf = ttk.Frame(self._rows_box, style="Alt.TFrame",
                       padding=(t.px(4), t.px(3)))
        rf.pack(fill=tk.X, pady=t.px(1))
        cb = ttk.Combobox(rf, values=self._shapes, width=9, state="readonly")
        cb.set(shape if shape in self._shapes else self._shapes[0])
        cb.pack(side=tk.LEFT)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        he = ttk.Entry(rf, width=3)
        he.insert(0, height)
        he.pack(side=tk.LEFT, padx=(t.px(4), 0))
        he.bind("<KeyRelease>", lambda _e: self.refresh_preview())

        item = {"shape": cb, "height": he, "frame": rf}
        self._rows.append(item)
        for text, delta in (("✕", None), ("↓", 1), ("↑", -1)):
            ttk.Button(rf, text=text, style="AltIcon.TButton",
                       command=(lambda i=item: self._remove(i)) if delta is None
                       else (lambda i=item, d=delta: self._move(i, d))
                       ).pack(side=tk.RIGHT)

    def _remove(self, item):
        if len(self._rows) <= 1:
            return          # 至少留一行，否则瓦片没有内容
        self._rows.remove(item)
        item["frame"].destroy()
        self.refresh_preview()

    def _move(self, item, delta):
        i = self._rows.index(item)
        j = i + delta
        if not 0 <= j < len(self._rows):
            return
        self._rows[i], self._rows[j] = self._rows[j], self._rows[i]
        for it in self._rows:      # 重新按新顺序摆放
            it["frame"].pack_forget()
        for it in self._rows:
            it["frame"].pack(fill=tk.X, pady=self.theme.px(1))
        self.refresh_preview()

    def _pick_color(self):
        cur = self.face.get().strip() or _DEFAULT_FACE
        rgb = colorchooser.askcolor(color=cur if mcolors.is_color_like(cur)
                                    else _DEFAULT_FACE,
                                    title="选择底色", parent=self)
        if rgb and rgb[1]:
            self.face.set(rgb[1])
            self.refresh_preview()

    # ---- 规格与预览 ----
    def _color(self):
        col = self.face.get().strip()
        return col if mcolors.is_color_like(col) else _DEFAULT_FACE

    def build_spec(self):
        rows, heights = [], []
        for item in self._rows:
            rows.append(item["shape"].get() or "空白")
            try:
                heights.append(max(0.1, float(item["height"].get())))
            except ValueError:
                heights.append(1.0)
        try:
            tile = max(0.3, float(self.tile.get()))
        except ValueError:
            tile = 1.3
        if not rows:
            rows, heights = ["空白"], [1.0]
        return [{"type": "rows", "spacing": tile, "heights": heights,
                 "rows": rows}]

    def refresh_liths(self, names):
        """数据载入后刷新“用于岩性”下拉候选（保留手输值）。"""
        self._lith_cb.configure(values=list(names or []))

    def refresh_preview(self):
        self._swatch.configure(bg=self._color())
        self._fig.clear()
        fw, fh = self._fig.get_size_inches()
        spec, face = self.build_spec(), self._color()

        # 左：柱内平铺大样（按 1:1 英寸坐标，密度与出图一致）
        w1, h1 = 1.05, 2.20
        ax = self._fig.add_axes([0.07, (fh - h1) / 2 / fh,
                                 w1 / fw, h1 / fh])
        ax.set_xlim(0, w1)
        ax.set_ylim(h1, 0)
        ax.axis("off")
        try:
            L.paint(ax, [(0, 0), (w1, 0), (w1, h1), (0, h1)], "__preview__",
                    spec=spec, face=face)
        except Exception:
            pass      # 半途输入（如厚度只敲了个"."）不该弹错，等下一次按键
        ax.add_patch(mpatches.Rectangle((0, 0), w1, h1, fill=False,
                                        edgecolor=self.theme.c["border_hi"],
                                        lw=0.8))

        # 右：图例小样（18×12 mm；瓦片过高时自动收紧到整瓦片）
        sw, sh = 0.71, 0.472
        ax2 = self._fig.add_axes([0.07 + (w1 + 0.18) / fw,
                                  (fh - h1) / 2 / fh + (h1 - sh) / fh,
                                  sw / fw, sh / fh])
        ax2.set_xlim(0, sw)
        ax2.set_ylim(sh, 0)
        ax2.axis("off")
        try:
            sp = L.swatch_spacing_for_spec(L.normalize_spec(spec), sh)
            L.paint(ax2, [(0, 0), (sw, 0), (sw, sh), (0, sh)], "__preview__",
                    spec=spec, face=face, spacing=sp)
        except Exception:
            pass
        ax2.add_patch(mpatches.Rectangle((0, 0), sw, sh, fill=False,
                                         edgecolor="#333333", lw=0.8))
        self._canvas.draw_idle()

    # ---- 动作 ----
    def apply_pattern(self):
        name = self.name.get().strip() or "我的花纹"
        lith = self.lith.get().strip()
        col = self.face.get().strip()
        if col and not mcolors.is_color_like(col):
            messagebox.showerror("底色无效", "底色请填 #RRGGBB 形式的颜色。",
                                 parent=self)
            return
        try:
            L.register_pattern(name, self.build_spec(), lith or None,
                               col or None)
        except ValueError as e:
            messagebox.showerror("花纹无法应用", str(e), parent=self)
            return
        self.actions.pattern_registered(name, lith)

    def save_to_style(self):
        name = self.name.get().strip() or "我的花纹"
        lith = self.lith.get().strip()
        path = filedialog.asksaveasfilename(
            parent=self, title="保存 / 追加到样式文件", defaultextension=".json",
            initialfile="strat_style.json",
            filetypes=[("样式文件", "*.json")])
        if not path:
            return
        try:
            save_style_entry(path, name, self.build_spec(), lith,
                             self._color())
        except (OSError, ValueError) as e:
            messagebox.showerror("保存失败", str(e), parent=self)
            return
        self.actions.status(f"已保存花纹“{name}”到样式文件：{path}")
