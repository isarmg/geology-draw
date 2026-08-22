"""地层剖面图绘制（多钻孔连线对比）。

数据格式（Excel 自动识别数据工作表，或 CSV；每行一个钻孔的一层，层自上而下）：
    钻孔,距离,孔口标高,层号,岩性,厚度,接触关系

约定：同一"层号"在各钻孔间对比连线；某钻孔缺失某层视为该层尖灭
（厚度为 0，层面线在该孔处合并）。
"接触关系"可选，指该层与下伏层的接触；标注会作用于相邻钻孔区段，
不同钻孔的标注不一致时在区段中点分段表达，不再静默取第一项。
"""

import copy
import heapq
import math

from matplotlib.figure import Figure

from . import fonts, lithology
from .tableio import read_table, get as _get

_GROUND = "#5a4632"   # 地面线颜色
_BOUND = "#40403c"    # 层面线颜色


def load_section(path):
    """读取剖面数据（Excel 或 CSV），返回钻孔列表（按距离排序）：
    [{'name','x','elev','layers':[(层号,岩性,厚度,接触关系), ...]}, ...]
    """
    return parse_section(read_table(path))


def parse_section(rows):
    """把已读入的表格行解析为钻孔列表（按距离排序）。

    与 load_section 分开是为了让调用方只读一次盘就能既判断数据类型、
    又解析内容。

    总层数不得超过 2000；距离、孔口标高和厚度必须是有限数，
    厚度必须大于 0。同孔各行的距离/标高必须一致，层号不得重复。
    错误信息不回显整行数据。
    """
    holes = {}
    layer_nos = {}
    for i, row in enumerate(rows, 1):
        if i > 2000:
            raise ValueError("剖面图总层数最多为 2000")
        name = _get(row, "钻孔", "孔号", "钻孔编号")
        if not name:
            raise ValueError(f"第 {i} 行缺少钻孔编号")
        try:
            x = float(_get(row, "距离", "里程") or 0)
            elev = float(_get(row, "孔口标高", "标高", "高程") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"第 {i} 行距离和孔口标高必须是数字")
        if not math.isfinite(x) or not math.isfinite(elev):
            raise ValueError(f"第 {i} 行距离和孔口标高必须是有限数")
        bh = holes.get(name)
        if bh is None:
            bh = {"name": name, "x": x, "elev": elev, "layers": []}
            holes[name] = bh
            layer_nos[name] = set()
        elif bh["x"] != x or bh["elev"] != elev:
            raise ValueError(f"第 {i} 行钻孔“{name}”的距离或孔口标高与前文不一致")
        no = _get(row, "层号", "序号") or str(len(bh["layers"]) + 1)
        if no in layer_nos[name]:
            raise ValueError(f"第 {i} 行钻孔“{name}”的层号“{no}”重复")
        lith = _get(row, "岩性", "岩性名称")
        thick = _get(row, "厚度", "层厚")
        if not lith or not thick:
            raise ValueError(f"第 {i} 行缺少岩性或厚度")
        try:
            thick_value = float(thick)
        except (TypeError, ValueError):
            raise ValueError(f"第 {i} 行厚度必须是数字")
        if not math.isfinite(thick_value) or thick_value <= 0:
            raise ValueError(f"第 {i} 行厚度必须是大于 0 的有限数")
        contact = _get(row, "接触关系", "接触", "不整合")
        bh["layers"].append((no, lith, thick_value, contact))
        layer_nos[name].add(no)
    if len(holes) < 2:
        raise ValueError("剖面图至少需要 2 个钻孔")
    if len({bh["x"] for bh in holes.values()}) < 2:
        raise ValueError("剖面图至少需要 2 个不同的钻孔距离")
    return sorted(holes.values(), key=lambda b: b["x"])


load_section_csv = load_section  # 兼容旧名


def _layer_order(holes):
    """按各孔的输入层序建立统一的自上而下层序。

    每个钻孔中相邻两层构成一条“上层先于下层”的约束，再对这些约束
    做稳定拓扑排序。互不约束的层按首次出现顺序排列；若不同钻孔给出的
    层序相互矛盾则拒绝绘图，避免仅按层号数字排序后悄悄颠倒地层。
    """
    first_seen = {}
    successors = {}
    indegree = {}
    for bh in holes:
        sequence = [layer[0] for layer in bh["layers"]]
        for no in sequence:
            if no not in first_seen:
                first_seen[no] = len(first_seen)
                successors[no] = set()
                indegree[no] = 0
        for upper, lower in zip(sequence, sequence[1:]):
            if lower not in successors[upper]:
                successors[upper].add(lower)
                indegree[lower] += 1

    available = [(first_seen[no], no) for no, degree in indegree.items()
                 if degree == 0]
    heapq.heapify(available)
    order = []
    while available:
        _, no = heapq.heappop(available)
        order.append(no)
        for lower in sorted(successors[no], key=first_seen.__getitem__):
            indegree[lower] -= 1
            if indegree[lower] == 0:
                heapq.heappush(available, (first_seen[lower], lower))

    if len(order) != len(first_seen):
        conflicted = sorted(
            (no for no, degree in indegree.items() if degree > 0),
            key=first_seen.__getitem__,
        )
        preview = "、".join(conflicted[:8])
        if len(conflicted) > 8:
            preview += "等"
        raise ValueError(
            "各钻孔的层号自上而下顺序相互冲突，无法建立统一层序"
            f"（涉及层号：{preview}）"
        )
    return order


def render_section(holes, title="地层剖面图", ve=None, fig_width=11.0,
                   pattern_row_height_mm=lithology.PATTERN_ROW_HEIGHT_MM):
    """绘制剖面图，返回 matplotlib Figure。

    ve: 垂直夸大系数（如 2 表示纵向放大 2 倍）；不传则自动适配画幅。
    pattern_row_height_mm: 内置层状岩性花纹的基础层厚（毫米，1–10）；
                           地层变厚时增加重复层数，不拉伸单层。

    函数会深拷贝输入，不向调用方的钻孔字典写入边界坐标。
    ve 必须在 0.1–50 之间；fig_width 必须在 2–100 英寸之间。
    """
    row_height = lithology.resolve_pattern_row_height_mm(
        pattern_row_height_mm)
    with lithology.pattern_row_height_scope(row_height):
        return _render_section_impl(holes, title, ve, fig_width)


def _render_section_impl(holes, title="地层剖面图", ve=None, fig_width=11.0):
    """在已建立花纹层高上下文后绘制剖面图。"""
    holes = copy.deepcopy(holes)
    if not isinstance(holes, (list, tuple)) or len(holes) < 2:
        raise ValueError("剖面图至少需要 2 个钻孔")
    holes = list(holes)
    try:
        fig_width = float(fig_width)
    except (TypeError, ValueError):
        raise ValueError("剖面图宽度必须是 2–100 英寸之间的有限数")
    if not math.isfinite(fig_width) or not 2 <= fig_width <= 100:
        raise ValueError("剖面图宽度必须是 2–100 英寸之间的有限数")
    if ve is not None:
        try:
            ve = float(ve)
        except (TypeError, ValueError):
            raise ValueError("垂直夸大系数必须是 0.1–50 之间的有限数")
        if not math.isfinite(ve) or not 0.1 <= ve <= 50:
            raise ValueError("垂直夸大系数必须是 0.1–50 之间的有限数")

    seen_names, total_layers = set(), 0
    for hi, bh in enumerate(holes, 1):
        if not isinstance(bh, dict):
            raise ValueError(f"第 {hi} 个钻孔数据必须是字典")
        name = str(bh.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {hi} 个钻孔缺少编号")
        if name in seen_names:
            raise ValueError(f"钻孔编号“{name}”重复")
        seen_names.add(name)
        try:
            x = float(bh.get("x"))
            elev = float(bh.get("elev"))
        except (TypeError, ValueError):
            raise ValueError(f"钻孔“{name}”的距离和孔口标高必须是数字")
        if not math.isfinite(x) or not math.isfinite(elev):
            raise ValueError(f"钻孔“{name}”的距离和孔口标高必须是有限数")
        layers = bh.get("layers")
        if not isinstance(layers, (list, tuple)) or not layers:
            raise ValueError(f"钻孔“{name}”至少需要 1 层数据")
        normalised, seen_nos = [], set()
        for li, layer in enumerate(layers, 1):
            total_layers += 1
            if total_layers > 2000:
                raise ValueError("剖面图总层数最多为 2000")
            if not isinstance(layer, (list, tuple)) or len(layer) < 3:
                raise ValueError(f"钻孔“{name}”第 {li} 层格式无效")
            no = str(layer[0] or "").strip()
            lith = str(layer[1] or "").strip()
            if not no or not lith:
                raise ValueError(f"钻孔“{name}”第 {li} 层缺少层号或岩性")
            if no in seen_nos:
                raise ValueError(f"钻孔“{name}”的层号“{no}”重复")
            seen_nos.add(no)
            try:
                thick = float(layer[2])
            except (TypeError, ValueError):
                raise ValueError(f"钻孔“{name}”第 {li} 层厚度必须是数字")
            if not math.isfinite(thick) or thick <= 0:
                raise ValueError(f"钻孔“{name}”第 {li} 层厚度必须是大于 0 的有限数")
            contact = str(layer[3] or "").strip() if len(layer) > 3 else ""
            normalised.append((no, lith, thick, contact))
        bh.update(name=name, x=x, elev=elev, layers=normalised)
    holes_at_x = {}
    for bh in holes:
        previous = holes_at_x.get(bh["x"])
        if previous is not None:
            raise ValueError(
                f"钻孔“{previous}”与“{bh['name']}”的距离重复"
                f"（{bh['x']:g} m）；每个钻孔必须使用不同距离"
            )
        holes_at_x[bh["x"]] = bh["name"]
    # Python 排序是稳定的；明确按距离排序可防止直接调用核心 API 时生成
    # 折返、自交的地层多边形。
    holes.sort(key=lambda bh: bh["x"])

    fonts.setup()

    order = _layer_order(holes)
    for bh in holes:
        thick = {ly[0]: ly[2] for ly in bh["layers"]}
        bounds, z = {}, bh["elev"]
        for no in order:
            t = thick.get(no, 0.0)
            bounds[no] = (z, z - t)
            z -= t
        bh["bounds"] = bounds
        bh["bottom"] = z

    # 保留每个钻孔的真实岩性与接触关系。相同层号并不意味着横向岩性必然
    # 相同；用“多数岩性”填满整层会把侧向相变悄悄抹掉。
    lith_at, contact_at = {}, {}
    layers_by_hole = [{ly[0]: ly for ly in bh["layers"]} for bh in holes]
    for no in order:
        layers = [layer_map.get(no) for layer_map in layers_by_hole]
        lith_at[no] = [ly[1] if ly else "" for ly in layers]
        contact_at[no] = [ly[3] if ly else "" for ly in layers]

    xs = [bh["x"] for bh in holes]
    top = max(bh["elev"] for bh in holes)
    bot = min(bh["bottom"] for bh in holes)
    span_x = max(xs) - min(xs)
    span_y = top - bot

    legend_names = list(dict.fromkeys(
        lith for no in order for lith in lith_at[no] if lith))
    legend_in = max(0.50, lithology.legend_height_in(legend_names,
                                                     0.895 * fig_width))
    fig_h = 6.3 + legend_in

    fig = Figure(figsize=(fig_width, fig_h), dpi=100)
    fig.patch.set_facecolor("white")
    ax_b = (legend_in + 0.55) / fig_h
    ax = fig.add_axes([0.075, ax_b, 0.895, 1 - ax_b - 0.55 / fig_h])

    pad_x = span_x * 0.04
    ax.set_xlim(min(xs) - pad_x, max(xs) + pad_x)
    ax.set_ylim(bot - span_y * 0.06, top + span_y * 0.22)  # 顶部留出孔号标注
    if ve:
        ax.set_aspect(ve)
    pattern_sy = lithology._units_per_inch(ax)[1]

    def paint_interval(points, left_lith, right_lith):
        """相邻两孔岩性不同时在中点分区，显式保留侧向相变。"""
        if not left_lith and not right_lith:
            return
        if left_lith == right_lith or not left_lith or not right_lith:
            lithology.paint(
                ax, points, left_lith or right_lith,
                spacing=lithology.BASE_SPACING,
            )
            return
        top_left, top_right, bot_right, bot_left = points
        mid_top = ((top_left[0] + top_right[0]) / 2,
                   (top_left[1] + top_right[1]) / 2)
        mid_bot = ((bot_left[0] + bot_right[0]) / 2,
                   (bot_left[1] + bot_right[1]) / 2)
        lithology.paint(ax, [top_left, mid_top, mid_bot, bot_left],
                        left_lith, spacing=lithology.BASE_SPACING)
        lithology.paint(ax, [mid_top, top_right, bot_right, mid_bot],
                        right_lith, spacing=lithology.BASE_SPACING)

    def draw_contact(points, contact, clearance_mm):
        unconf, _angular = lithology.is_unconformity(contact)
        lithology.draw_contact(
            ax,
            points,
            contact,
            color=_BOUND,
            lw=1.1 if unconf else 0.9,
            zorder=3,
            clearance_mm=clearance_mm,
        )

    # 地层多边形按孔间区段填充；底界接触关系也按区段表达。缺层端点
    # 仍保持厚度为零，因而自然形成尖灭三角形。
    for layer_index, no in enumerate(order):
        tops = [(bh["x"], bh["bounds"][no][0]) for bh in holes]
        bots = [(bh["x"], bh["bounds"][no][1]) for bh in holes]
        names = lith_at[no]
        contacts = contact_at[no]
        if names and names[0] and all(name == names[0] for name in names):
            # 同一岩性横跨多个孔间区段时一次填充，避免每个区段重新起纹后
            # 在钻孔处出现半行厚度不同或相位跳变。
            lithology.paint(
                ax,
                tops + list(reversed(bots)),
                names[0],
                spacing=lithology.BASE_SPACING,
            )
        else:
            for index in range(len(holes) - 1):
                points = [tops[index], tops[index + 1],
                          bots[index + 1], bots[index]]
                paint_interval(points, names[index], names[index + 1])

        next_no = order[layer_index + 1] if layer_index + 1 < len(order) else None
        for index in range(len(holes) - 1):
            nearby_heights = []
            for boundary_no in (no, next_no):
                if boundary_no is None:
                    continue
                for hole_index in (index, index + 1):
                    upper, lower = holes[hole_index]["bounds"][boundary_no]
                    height_in = abs(upper - lower) / pattern_sy
                    if height_in > 0:
                        nearby_heights.append(height_in)
            clearance_mm = lithology.contact_clearance_mm(nearby_heights)

            left_contact, right_contact = contacts[index:index + 2]
            left_style = lithology.is_unconformity(left_contact)
            right_style = lithology.is_unconformity(right_contact)
            if left_style == right_style or not left_contact or not right_contact:
                draw_contact(bots[index:index + 2],
                             left_contact or right_contact,
                             clearance_mm)
            else:
                midpoint = ((bots[index][0] + bots[index + 1][0]) / 2,
                            (bots[index][1] + bots[index + 1][1]) / 2)
                draw_contact([bots[index], midpoint], left_contact,
                             clearance_mm)
                draw_contact([midpoint, bots[index + 1]], right_contact,
                             clearance_mm)

    # 地面线
    ax.plot(xs, [bh["elev"] for bh in holes], color=_GROUND, lw=2.2, zorder=4)

    # 钻孔
    for bh in holes:
        ax.plot([bh["x"], bh["x"]], [bh["elev"], bh["bottom"]],
                color="#111111", lw=1.1, zorder=5)
        ax.plot([bh["x"]], [bh["bottom"]], marker="_", ms=9,
                color="#111111", zorder=5)
        ax.annotate(f"{bh['name']}\n{bh['elev']:.2f} m",
                    (bh["x"], bh["elev"]), textcoords="offset points",
                    xytext=(0, 10), ha="center", fontsize=8.5,
                    fontweight="bold", zorder=6, linespacing=1.4)

    ax.set_xlabel("距离 (m)", fontsize=9)
    ax.set_ylabel("标高 (m)", fontsize=9)
    ax.set_xticks(xs)
    ax.tick_params(labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#888888")
    ax.grid(axis="y", color="#000000", alpha=0.08, lw=0.6, zorder=0)

    fig.text(0.5, 1 - 0.30 / fig_h, title, ha="center", va="center",
             fontsize=14, fontweight="bold")
    if ve:
        fig.text(0.97, 1 - 0.32 / fig_h, f"垂直夸大 ×{ve:g}", ha="right",
                 va="center", fontsize=8, color="#444444")

    lithology.draw_legend(
        fig, legend_names,
        [0.075, 0.12 / fig_h, 0.895, legend_in / fig_h])
    if any(lithology.is_unconformity(contact)[0]
           for contacts in contact_at.values() for contact in contacts):
        fig.text(0.97, 0.14 / fig_h, "波状线示不整合面，附短斜线者为角度不整合",
                 ha="right", va="bottom", fontsize=7.5, color="#555555")
    return fig
