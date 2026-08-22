#!/usr/bin/env python3
"""地层绘图服务：综合地层柱状图 / 地层剖面图。

Web 服务：   python3 app.py
桌面界面：   python3 app.py gui
命令行出图：python3 app.py column examples/column_demo.xlsx -o 柱状图.png
            python3 app.py section examples/section_demo.xlsx -o 剖面图.png
数据支持 Excel（.xlsx/.xlsm，自动识别数据工作表）或 CSV，格式见 examples/。
"""

import argparse
import os
import sys
import tempfile

# Configure a writable font cache before importing matplotlib.  This matters
# for headless service accounts whose home/config directory is read-only.
if "MPLCONFIGDIR" not in os.environ:
    _mpl_cache = os.path.join(
        tempfile.gettempdir(),
        "strat-matplotlib-%s" % getattr(os, "getuid", lambda: "user")())
    try:
        os.makedirs(_mpl_cache, mode=0o700, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = _mpl_cache
    except OSError:
        pass

import matplotlib

from strat import (load_column, render_column, load_section, render_section,
                   load_style, find_style_file, render_pattern_sheet)
from strat import fonts as strat_fonts
from strat.lithology import (PATTERN_ROW_HEIGHT_LIMITS_MM,
                             PATTERN_ROW_HEIGHT_MM,
                             render_shapes_sheet,
                             resolve_pattern_row_height_mm)


def _pattern_row_height_arg(value):
    """argparse adapter using the renderer's canonical validation rules."""
    try:
        return resolve_pattern_row_height_mm(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _base_dir():
    if getattr(sys, "frozen", False):  # PyInstaller 打包后资源在临时解压目录
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _exe_dir():
    """打包后 exe 所在目录（未打包时为脚本目录）——用于查找同目录样式文件。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


EXAMPLE_DIR = os.path.join(_base_dir(), "examples")
WEB_DIR = os.path.join(_base_dir(), "web")


def apply_style(explicit=None, data_path=None):
    """载入自定义样式：显式指定 > 数据文件同目录 > 当前目录 > 程序目录。
    返回实际载入的样式文件路径（无则 None）。"""
    path = explicit
    if not path:
        data_dir = os.path.dirname(os.path.abspath(data_path)) if data_path else None
        path = find_style_file(data_dir, os.getcwd(), _exe_dir())
    if path:
        load_style(path)
    return path


# ---------------------------------------------------------------- 命令行 ----

def run_cli(args):
    matplotlib.use("Agg")
    if args.kind == "patterns":
        apply_style(getattr(args, "style", None))
        fig = render_pattern_sheet()
        out = args.output or "岩性花纹一览.png"
        fig.savefig(out, dpi=args.dpi, facecolor="white")
        print(f"已保存：{out}")
        return
    if args.kind == "shapes":
        fig = render_shapes_sheet()
        out = args.output or "基本图形一览.png"
        fig.savefig(out, dpi=args.dpi, facecolor="white")
        print(f"已保存：{out}")
        return
    if args.kind == "catalog":
        from strat.gb958 import CATEGORIES, render_catalog_sheet
        if args.list:
            print("可用类别：", "、".join(CATEGORIES))
            return
        cats = [args.category] if args.category else list(CATEGORIES)
        outdir = None
        if not args.category:
            outdir = args.output or "."
            os.makedirs(outdir, exist_ok=True)
        for cat in cats:
            fig = render_catalog_sheet(cat)
            out = (args.output if args.category and args.output
                   else os.path.join(outdir or ".", f"国标图例·{cat}.png"))
            fig.savefig(out, dpi=args.dpi, facecolor="white")
            print(f"已保存：{out}")
        return

    if getattr(args, "font", None):
        strat_fonts.set_family(args.font)
    if getattr(args, "font_size", None):
        strat_fonts.set_base_size(args.font_size)
    used = apply_style(getattr(args, "style", None), args.data)
    if used:
        print(f"已载入样式：{used}")
    pattern_row_height_mm = resolve_pattern_row_height_mm(
        getattr(args, "pattern_row_height_mm", PATTERN_ROW_HEIGHT_MM)
    )
    if args.kind == "column":
        widths = {}
        for item in (args.width or []):
            for part in item.split(","):
                if not part.strip():
                    continue
                k, _, v = part.partition("=")
                if not v.strip():
                    raise SystemExit(f"--width 格式应为 栏名=比例：{part!r}")
                widths[k.strip()] = float(v)
        thick_mode = "depth" if args.depth else (
            "layer" if args.thick_per_layer else "group")
        strata = set(args.strata) if args.strata else None
        layers = load_column(args.data)
        fig = render_column(layers, title=args.title or "综合地层柱状图",
                            scale=args.scale, to_scale=args.to_scale,
                            widths=widths, page=args.page,
                            landscape=args.landscape,
                            thick_mode=thick_mode,
                            unit_vertical=args.unit_vertical, strata=strata,
                            show_remark=False if args.no_remark else None,
                            hide_units=set(args.hide_units)
                            if args.hide_units else None,
                            show_legend=args.legend,
                            pattern_row_height_mm=pattern_row_height_mm)
    else:
        holes = load_section(args.data)
        fig = render_section(
            holes,
            title=args.title or "地层剖面图",
            ve=args.ve,
            pattern_row_height_mm=pattern_row_height_mm,
        )
    out = args.output or (os.path.splitext(args.data)[0] + ".png")
    fig.savefig(out, dpi=args.dpi, facecolor="white")
    print(f"已保存：{out}")


# ---------------------------------------------------------------- 图形界面 --

def run_gui():
    """启动图形界面（界面代码在 ui/ 包里）。"""
    from ui import run
    run(EXAMPLE_DIR, apply_style)


def run_web(host="127.0.0.1", port=8000, open_browser=False,
            allow_network=False, allowed_hosts=None):
    """启动同域 HTTP API 与浏览器端界面。"""
    matplotlib.use("Agg")
    from server import serve
    serve(host=host, port=port, web_dir=WEB_DIR, example_dir=EXAMPLE_DIR,
          open_browser=open_browser, allow_remote=allow_network,
          allowed_hosts=allowed_hosts)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="kind")

    sp = sub.add_parser("serve", help="启动 Server + Web 服务（默认命令）")
    sp.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1；局域网可用 "
                         "0.0.0.0 并同时指定 --allow-network）")
    sp.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    sp.add_argument("--open-browser", action="store_true",
                    help="启动后自动打开默认浏览器")
    sp.add_argument("--allow-network", action="store_true",
                    help="显式允许非回环/局域网访问（请配合防火墙）")
    sp.add_argument("--allowed-host", action="append", default=None,
                    help="限制可接受的 Host，可多次指定；"
                         "局域网模式默认接受任意合法 Host")

    sub.add_parser("gui", help="启动旧版 Tkinter 桌面界面")

    for kind, help_ in (("column", "绘制综合地层柱状图"),
                        ("section", "绘制地层剖面图")):
        sp = sub.add_parser(kind, help=help_)
        sp.add_argument("data", help="数据文件（.xlsx/.xlsm/.csv）")
        sp.add_argument("-o", "--output", help="输出图片路径（默认同名 .png）")
        sp.add_argument("--title", help="图名")
        sp.add_argument("--dpi", type=int, default=300)
        sp.add_argument("--style", help="自定义样式 JSON（不指定则自动查找 "
                        "数据同目录/当前目录/程序目录下的 strat_style.json）")
        sp.add_argument("--font", help="中文字体，可用中文名：宋体/黑体/仿宋/"
                        "楷体/微软雅黑/等线/华文楷体…，也可用族名如 SimHei")
        sp.add_argument("--font-size", type=float,
                        help="正文基准字号 pt（6–12，默认 8）")
        row_min, row_max = PATTERN_ROW_HEIGHT_LIMITS_MM
        sp.add_argument(
            "--pattern-row-height-mm",
            type=_pattern_row_height_arg,
            default=PATTERN_ROW_HEIGHT_MM,
            metavar="MM",
            help=(f"岩性花纹基础层高（毫米，{row_min:g}–{row_max:g}，"
                  f"默认 {PATTERN_ROW_HEIGHT_MM:g}）"),
        )
        if kind == "column":
            sp.add_argument("--scale", type=int,
                            help="垂直比例尺分母，如 200 表示 1:200")
            sp.add_argument("--page", default="A4",
                            help="页面规格：A0–A5/B3–B5/8开/16开/信纸/法律纸，"
                                 "或自定义如 24x36（厘米），默认 A4")
            sp.add_argument("--landscape", action="store_true", help="页面横向")
            sp.add_argument("--to-scale", action="store_true",
                            help="各栏与岩性柱严格按深度对齐（旧版式）")
            sp.add_argument("--thick-per-layer", action="store_true",
                            help="厚度栏逐层显示每种岩性厚度"
                                 "（默认同组合并显示总厚度）")
            sp.add_argument("--depth", action="store_true",
                            help="厚度栏改为显示地层深度（组交界处标注累计深度）")
            sp.add_argument("--strata", nargs="+",
                            choices=("geochron", "chrono", "litho"),
                            help="只显示指定类别的地层单位列："
                                 "geochron 地质年代 / chrono 年代地层 / "
                                 "litho 岩石地层（默认有数据的列都显示）")
            sp.add_argument("--unit-vertical", action="store_true",
                            help="地层单位单元格文字竖排（每字一行）")
            sp.add_argument("--no-remark", action="store_true",
                            help="隐藏备注栏（即使数据里有备注）")
            sp.add_argument("--legend", action="store_true",
                            help="在图底加 GB/T 958 花纹岩性图例（按首次出现顺序排列）")
            sp.add_argument("--hide-units", nargs="+", metavar="列名",
                            help="单独隐藏某些地层单位列（中文名，如 统 段），"
                                 "在 --strata 之上再细化到某一列")
            sp.add_argument("--width", action="append", metavar="栏=厘米",
                            help="调整栏宽（厘米），可多次或逗号分隔，"
                                 "如 --width 柱状图=3.5,备注=2.0；"
                                 "可调栏：界/系/组/地层单位/厚度/连接带/柱状图/备注，"
                                 "超范围自动夹紧，描述栏为余量")
        else:
            sp.add_argument("--ve", type=float, help="垂直夸大系数，如 2")

    sp = sub.add_parser("patterns", help="导出岩性花纹一览表（含自定义花纹）")
    sp.add_argument("-o", "--output", help="输出图片路径（默认 岩性花纹一览.png）")
    sp.add_argument("--dpi", type=int, default=200)
    sp.add_argument("--style", help="自定义样式 JSON")

    sp = sub.add_parser("shapes", help="导出基本图形一览（供自行设计花纹）")
    sp.add_argument("-o", "--output", help="输出图片路径（默认 基本图形一览.png）")
    sp.add_argument("--dpi", type=int, default=200)

    sp = sub.add_parser("catalog",
                        help="导出 GB/T 958—2015 岩石花纹图例全集（分类）")
    sp.add_argument("-c", "--category", help="只导出某一大类（默认全部，"
                    "每类一张图）；可用类别见 --list")
    sp.add_argument("--list", action="store_true", help="列出全部类别")
    sp.add_argument("-o", "--output", help="输出路径：单类为图片文件，"
                    "全部导出时为文件夹（默认当前目录）")
    sp.add_argument("--dpi", type=int, default=200)

    args = p.parse_args()
    if args.kind == "serve":
        try:
            run_web(args.host, args.port, args.open_browser,
                    allow_network=args.allow_network,
                    allowed_hosts=args.allowed_host)
        except ValueError as exc:
            p.error(str(exc))
    elif args.kind == "gui":
        run_gui()
    elif args.kind:
        run_cli(args)
    else:
        run_web()


if __name__ == "__main__":
    main()
