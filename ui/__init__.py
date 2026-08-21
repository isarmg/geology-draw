"""ui — 地层绘图的图形界面（Tkinter）。

分层：theme 设计令牌 › widgets 通用控件 › state 文档模型 ›
inspector / designer 面板 › window 主窗口与工作区编排。
绘图逻辑全在 strat/ 里，这里只管界面。
"""

from .window import run

__all__ = ["run"]
