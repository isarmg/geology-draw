"""strat — 地质学地层柱状图 / 地层剖面图绘制小工具"""

__version__ = "0.2.0"

from . import lithology
from .column import load_column, load_column_csv, parse_column, render_column
from .lithology import find_style_file, load_style, render_pattern_sheet
from .section import (load_section, load_section_csv, parse_section,
                      render_section)
from .tableio import read_table

# GB/T 958—2015 表 4 全集：注册后任何标准岩石名都可直接用于数据出图
from . import gb958
from .gb958 import render_catalog_sheet
gb958.register_all()
