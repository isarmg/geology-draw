"""中文字体配置：从系统已安装字体中挑选可用的中文字体。

支持用户指定字体（set_family）与全局基准字号（set_base_size，
影响柱状图正文/表头/标题等所有文字的相对大小）。
"""

_done = False
_user_family = None

BASE_FS = 8.0        # 正文基准字号（pt），表头/标题按它加成
_FS_RANGE = (6.0, 12.0)

_CANDIDATES = (
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "KaiTi",
    "FangSong",
    "PingFang SC",
    "Noto Sans CJK JP",
)

# 中文字体识别关键字（列出系统可用中文字体时用）
_ZH_HINTS = ("CJK", "Hei", "Song", "Kai", "FangSong", "YaHei", "PingFang",
             "WenQuanYi", "Source Han", "SimSun", "SimHei", "MingLiU",
             "DengXian", "LiSu", "YouYuan")

# 常用中文字体：中文显示名 -> 字体族名（Windows/办公环境标配为主）
FAMILY_CN = {
    "宋体":       "SimSun",
    "新宋体":     "NSimSun",
    "黑体":       "SimHei",
    "仿宋":       "FangSong",
    "楷体":       "KaiTi",
    "微软雅黑":   "Microsoft YaHei",
    "等线":       "DengXian",
    "隶书":       "LiSu",
    "幼圆":       "YouYuan",
    "华文宋体":   "STSong",
    "华文中宋":   "STZhongsong",
    "华文楷体":   "STKaiti",
    "华文仿宋":   "STFangsong",
    "华文细黑":   "STXihei",
    "华文隶书":   "STLiti",
    "华文行楷":   "STXingkai",
    "华文新魏":   "STXinwei",
    "方正舒体":   "FZShuTi",
    "方正姚体":   "FZYaoTi",
    "思源黑体":   "Noto Sans CJK SC",
    "文泉驿微米黑": "WenQuanYi Micro Hei",
}


def available_families():
    """系统可用的中文字体，返回 [(显示名, 字体族名), ...]。
    常用字体用中文名显示，其余检测到的中文字体按原名列出。"""
    from matplotlib import font_manager
    installed = {f.name for f in font_manager.fontManager.ttflist}
    out = [(cn, en) for cn, en in FAMILY_CN.items() if en in installed]
    seen = {en for _, en in out}
    out += [(n, n) for n in zh_font_list() if n not in seen]
    return out


def zh_font_list():
    """返回系统已安装、可用于中文的字体名列表（按字母排序去重）。"""
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist
             if any(h.lower() in f.name.lower() for h in _ZH_HINTS)}
    return sorted(names)


def set_family(name):
    """指定中文字体（可用中文名如"宋体"，也可用族名如 SimSun；
    None 恢复自动挑选）。下次 setup 时生效。"""
    global _user_family, _done
    name = (name or "").strip() or None
    if name:
        name = FAMILY_CN.get(name, name)
    _user_family = name
    _done = False


def set_base_size(v):
    """设置正文基准字号（pt），夹紧到合理范围。返回实际值。"""
    global BASE_FS
    BASE_FS = min(_FS_RANGE[1], max(_FS_RANGE[0], float(v)))
    return BASE_FS


def _patch_no_bitmap():
    """宋体(simsun.ttc)等字体内嵌了点阵字形，Agg 渲染在命中点阵的字号
    （约 8.5–12pt @100dpi）取不到矢量轮廓，文字会整块空白。
    给字形加载旗标加 NO_BITMAP，强制始终使用矢量轮廓。"""
    import matplotlib.backends.backend_agg as agg
    if getattr(agg.get_hinting_flag, "_no_bitmap", False):
        return
    from matplotlib import ft2font
    flag = (ft2font.LoadFlags.NO_BITMAP if hasattr(ft2font, "LoadFlags")
            else ft2font.LOAD_NO_BITMAP)
    orig = agg.get_hinting_flag

    def get_hinting_flag():
        return orig() | flag

    get_hinting_flag._no_bitmap = True
    agg.get_hinting_flag = get_hinting_flag


def setup():
    global _done
    if _done:
        return
    from matplotlib import font_manager, rcParams

    installed = {f.name for f in font_manager.fontManager.ttflist}
    order = ((_user_family,) if _user_family else ()) + _CANDIDATES
    for name in order:
        if name and name in installed:
            rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    rcParams["axes.unicode_minus"] = False
    try:
        _patch_no_bitmap()
    except Exception:
        pass  # 渲染后端不可用时跳过（如纯 PDF 环境），不影响出图
    _done = True
