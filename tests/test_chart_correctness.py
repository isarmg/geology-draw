"""Regression tests for chart ordering, scale, and layout invariants."""

import copy
import concurrent.futures
import math
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from matplotlib import colors
from matplotlib.figure import Figure

from strat import column, lithology
from strat.column import render_column
from strat.section import _layer_order, render_section


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _layer(thickness=1.0, *, compress="", description="", **units):
    result = {
        "no": "1",
        "unit": "",
        "lith": "砂岩",
        "thick": thickness,
        "desc": description,
        "remark": "",
        "contact": "",
        "compress": compress,
    }
    for key, _head, _category in column._UNIT_LEVELS:
        result[key] = units.get(key, "")
    return result


def _hole(name, x, layer_numbers):
    return {
        "name": name,
        "x": x,
        "elev": 100.0,
        "layers": [(number, "砂岩", 1.0, "")
                   for number in layer_numbers],
    }


class SectionOrderingTests(unittest.TestCase):
    def test_render_sorts_direct_api_input_by_distance(self):
        holes = (
            _hole("ZK3", 20, ["1"]),
            _hole("ZK1", 0, ["1"]),
            _hole("ZK2", 10, ["1"]),
        )

        figure = render_section(holes)
        try:
            self.assertEqual(list(figure.axes[0].get_xticks()), [0.0, 10.0, 20.0])
        finally:
            figure.clear()

    def test_render_rejects_duplicate_distances(self):
        holes = [
            _hole("ZK1", 0, ["1"]),
            _hole("ZK2", 0, ["1"]),
            _hole("ZK3", 20, ["1"]),
        ]

        with self.assertRaisesRegex(ValueError, "距离重复"):
            render_section(holes)

    def test_layer_order_respects_each_hole_instead_of_numeric_sort(self):
        holes = [
            _hole("ZK1", 0, ["10", "2"]),
            _hole("ZK2", 10, ["10", "3", "2"]),
        ]

        self.assertEqual(_layer_order(holes), ["10", "3", "2"])

    def test_layer_order_rejects_conflicting_borehole_sequences(self):
        holes = [
            _hole("ZK1", 0, ["甲", "乙"]),
            _hole("ZK2", 10, ["乙", "甲"]),
        ]

        with self.assertRaisesRegex(ValueError, "层号.*顺序相互冲突"):
            _layer_order(holes)

    def test_lateral_facies_are_not_replaced_by_majority_lithology(self):
        holes = [
            {"name": "ZK1", "x": 0, "elev": 100,
             "layers": [("1", "砂岩", 2.0, "")]},
            {"name": "ZK2", "x": 10, "elev": 100,
             "layers": [("1", "泥岩", 2.0, "")]},
            {"name": "ZK3", "x": 20, "elev": 100,
             "layers": [("1", "泥岩", 2.0, "")]},
        ]
        painted_lithologies = []

        def capture_paint(_axis, _polygon, lithology_name, **_kwargs):
            painted_lithologies.append(lithology_name)

        with mock.patch("strat.section.lithology.paint",
                        side_effect=capture_paint):
            figure = render_section(holes)
        try:
            self.assertIn("砂岩", painted_lithologies)
            self.assertIn("泥岩", painted_lithologies)
        finally:
            figure.clear()

    def test_conflicting_contacts_are_drawn_in_separate_segments(self):
        holes = [
            {"name": "ZK1", "x": 0, "elev": 100,
             "layers": [("1", "砂岩", 2.0, "角度不整合")]},
            {"name": "ZK2", "x": 10, "elev": 100,
             "layers": [("1", "砂岩", 2.0, "整合")]},
        ]
        wavy_segments = []

        def capture_wavy(_axis, points, **_kwargs):
            wavy_segments.append(points)

        with mock.patch("strat.section.lithology.draw_wavy",
                        side_effect=capture_wavy):
            figure = render_section(holes)
        try:
            self.assertEqual(len(wavy_segments), 1)
            self.assertEqual(wavy_segments[0][0][0], 0)
            self.assertEqual(wavy_segments[0][-1][0], 5)
        finally:
            figure.clear()


class ContactClearanceTests(unittest.TestCase):
    def test_every_contact_symbol_has_a_white_clearance_underlay(self):
        cases = (
            ("整合", 2),
            ("平行不整合", 2),
            ("角度不整合", None),
        )
        for contact, expected_lines in cases:
            with self.subTest(contact=contact):
                figure = Figure(figsize=(4, 2), dpi=100)
                axis = figure.add_axes([0.1, 0.1, 0.8, 0.8])
                axis.set_xlim(0, 4)
                axis.set_ylim(0, 2)
                try:
                    artists = lithology.draw_contact(
                        axis, [(0.3, 1), (3.7, 1)], contact, zorder=3)
                    if expected_lines is None:
                        self.assertGreater(len(artists), 2)
                    else:
                        self.assertEqual(len(artists), expected_lines)
                    self.assertEqual(len(artists) % 2, 0)
                    self.assertEqual(artists, list(axis.lines))
                    self.assertLess(
                        max(item.get_zorder() for item in artists[::2]),
                        min(item.get_zorder() for item in artists[1::2]),
                    )
                    for underlay, ink in zip(artists[::2], artists[1::2]):
                        self.assertEqual(
                            colors.to_rgba(underlay.get_color()),
                            colors.to_rgba("white"),
                        )
                        self.assertGreater(
                            underlay.get_linewidth(), ink.get_linewidth()
                        )
                        self.assertEqual(ink.get_zorder(),
                                         underlay.get_zorder() + 1)
                        self.assertEqual(list(underlay.get_xdata()),
                                         list(ink.get_xdata()))
                        self.assertEqual(list(underlay.get_ydata()),
                                         list(ink.get_ydata()))
                finally:
                    figure.clear()

    def test_wavy_contact_returns_exactly_to_both_segment_endpoints(self):
        figure = Figure(figsize=(4, 2), dpi=100)
        axis = figure.add_axes([0.1, 0.1, 0.8, 0.8])
        axis.set_xlim(0, 4)
        axis.set_ylim(0, 2)
        points = [(0.3, 1.4), (3.7, 0.6)]
        try:
            artists = lithology.draw_contact(
                axis, points, "平行不整合", zorder=3)
            ink = artists[1]
            self.assertAlmostEqual(ink.get_xdata()[0], points[0][0])
            self.assertAlmostEqual(ink.get_ydata()[0], points[0][1])
            self.assertAlmostEqual(ink.get_xdata()[-1], points[-1][0])
            self.assertAlmostEqual(ink.get_ydata()[-1], points[-1][1])
        finally:
            figure.clear()

    def test_clearance_shrinks_for_thin_layers_without_disappearing(self):
        thin_height_in = 1.5 / 25.4
        self.assertAlmostEqual(
            lithology.contact_clearance_mm([thin_height_in]), 0.15)
        self.assertEqual(lithology.contact_clearance_mm([1.0]), 0.35)
        self.assertEqual(
            lithology.contact_clearance_mm([0.1 / 25.4]), 0.08)


class PatternPhysicalScaleTests(unittest.TestCase):
    @staticmethod
    def _horizontal_rows(axis, y0, y1):
        return sorted({
            round(float(segment[0][1]), 10)
            for collection in axis.collections
            for segment in collection.get_segments()
            if (abs(float(segment[0][1]) - float(segment[1][1])) < 1e-10
                and y0 - 1e-10 <= float(segment[0][1]) <= y1 + 1e-10)
        })

    @staticmethod
    def _compiled_pattern_pitch(pattern, row_height_mm):
        with lithology.pattern_row_height_scope(row_height_mm):
            segments, _marks, _coloured = pattern(
                0.0, 3.0, 0.0, 2.0,
                lithology.BASE_SPACING, lithology.BASE_SPACING,
            )
        rows = sorted({
            round(float(segment[0][1]), 10)
            for segment in segments
            if abs(float(segment[0][1]) - float(segment[1][1])) < 1e-10
            and 0 <= float(segment[0][1]) <= 2
        })
        differences = [b - a for a, b in zip(rows, rows[1:])
                       if b - a > 1e-8]
        if not differences:
            raise AssertionError("花纹没有可测量的水平层线")
        return differences[0]

    def test_configurable_row_height_changes_pitch_without_leaking(self):
        pattern = lithology.PATTERNS["sst_gb"]
        original_spec = copy.deepcopy(pattern.spec)

        for value in (1.25, 4.0, 2.5, 1.25):
            with self.subTest(value=value):
                self.assertAlmostEqual(
                    self._compiled_pattern_pitch(pattern, value),
                    value / 25.4,
                    places=8,
                )

        self.assertEqual(pattern.spec, original_spec)
        self.assertEqual(lithology.current_pattern_row_height_mm(), 2.5)

    def test_composite_lithology_follows_configurable_row_height(self):
        _face, pattern_name = lithology.style_of("硅质页岩")
        pattern = lithology.PATTERNS[pattern_name]

        for value in (1.5, 4.0):
            with self.subTest(value=value):
                self.assertAlmostEqual(
                    self._compiled_pattern_pitch(pattern, value),
                    value / 25.4,
                    places=8,
                )

    def test_row_height_context_is_nested_exception_safe_and_thread_local(self):
        self.assertEqual(lithology.current_pattern_row_height_mm(), 2.5)
        with lithology.pattern_row_height_scope(4.0):
            self.assertEqual(lithology.current_pattern_row_height_mm(), 4.0)
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with lithology.pattern_row_height_scope(1.2):
                    self.assertEqual(
                        lithology.current_pattern_row_height_mm(), 1.2)
                    raise RuntimeError("stop")
            self.assertEqual(lithology.current_pattern_row_height_mm(), 4.0)
        self.assertEqual(lithology.current_pattern_row_height_mm(), 2.5)

        pattern = lithology.PATTERNS["shale_gb"]
        barrier = threading.Barrier(2)

        def measure(value):
            with lithology.pattern_row_height_scope(value):
                barrier.wait()
                current = lithology.current_pattern_row_height_mm()
                pitch = self._compiled_pattern_pitch(pattern, value)
                return current, pitch

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(measure, (1.5, 4.0)))
        self.assertEqual([item[0] for item in results], [1.5, 4.0])
        for (current, pitch), expected in zip(results, (1.5, 4.0)):
            self.assertAlmostEqual(pitch, expected / 25.4, places=8)
        self.assertEqual(lithology.current_pattern_row_height_mm(), 2.5)

    def test_row_height_validation_rejects_bool_nonfinite_and_out_of_range(self):
        for value in (None, True, False, "", "abc", float("nan"),
                      float("inf"), 0.99, 10.01):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "花纹层厚"):
                    lithology.resolve_pattern_row_height_mm(value)

    def test_public_renderers_isolate_legend_scope_and_restore(self):
        original_paint = lithology.paint

        def render_and_capture(draw):
            seen = []

            def capture(*args, **kwargs):
                seen.append((
                    lithology.current_pattern_row_height_mm(),
                    lithology._LEGEND_SWATCH_CONTEXT.get(),
                ))
                return original_paint(*args, **kwargs)

            with mock.patch.object(lithology, "paint", side_effect=capture):
                figure = draw()
            try:
                self.assertGreaterEqual(len(seen), 2)
                self.assertEqual({item[0] for item in seen}, {4.0})
                self.assertIn(None, {item[1] for item in seen})
                self.assertIn(
                    (lithology.LEGEND_SWATCH_HEIGHT_MM,
                     lithology.LEGEND_REPRESENTATIVE_ROWS),
                    {item[1] for item in seen},
                )
            finally:
                figure.clear()
            self.assertEqual(lithology.current_pattern_row_height_mm(), 2.5)

        layers = [_layer(1.0)]
        render_and_capture(lambda: render_column(
            layers, scale=100, show_legend=True,
            pattern_row_height_mm=4.0,
        ))
        holes = [
            {"name": "ZK1", "x": 0, "elev": 10,
             "layers": [("1", "砂岩", 1.0, "")]},
            {"name": "ZK2", "x": 20, "elev": 10,
             "layers": [("1", "砂岩", 1.0, "")]},
        ]
        render_and_capture(lambda: render_section(
            holes, pattern_row_height_mm=4.0,
        ))

    def test_primary_lithologies_share_fixed_2_5mm_row_height(self):
        expected_pitch = 2.5 / 25.4
        for name in ("砂岩", "页岩", "石灰岩", "白云岩"):
            with self.subTest(name=name):
                figure = Figure(figsize=(4, 4), dpi=100)
                axis = figure.add_axes([0, 0, 1, 1])
                axis.set_xlim(0, 4)
                axis.set_ylim(0, 4)
                y0, y1 = 0.2, 1.2
                try:
                    lithology.paint(
                        axis,
                        [(0.5, y0), (3.5, y0),
                         (3.5, y1), (0.5, y1)],
                        name,
                    )
                    horizontal_y = self._horizontal_rows(axis, y0, y1)
                    self.assertGreater(len(horizontal_y), 1)
                    self.assertAlmostEqual(
                        horizontal_y[0] - y0,
                        y1 - horizontal_y[-1],
                        places=8,
                    )
                    for a, b in zip(horizontal_y, horizontal_y[1:]):
                        self.assertAlmostEqual(
                            b - a,
                            expected_pitch,
                            places=8,
                        )

                    if name == "砂岩":
                        marker_y = sorted({
                            round(float(y), 10)
                            for line in axis.lines
                            for y in line.get_ydata()
                            if y0 < float(y) < y1
                        })
                        self.assertGreater(len(marker_y), 1)
                        for a, b in zip(marker_y, marker_y[1:]):
                            self.assertAlmostEqual(
                                b - a, expected_pitch, places=8)
                        midpoints = [(a + b) / 2 for a, b in zip(
                            horizontal_y, horizontal_y[1:])]
                        for marker in marker_y:
                            self.assertLess(
                                min(abs(marker - midpoint)
                                    for midpoint in midpoints),
                                1e-8,
                            )
                finally:
                    figure.clear()

    def test_thicker_polygon_adds_rows_instead_of_stretching_them(self):
        pitch = 2.5 / 25.4

        def rows_for(name, row_units):
            figure = Figure(figsize=(4, 4), dpi=100)
            axis = figure.add_axes([0, 0, 1, 1])
            axis.set_xlim(0, 4)
            axis.set_ylim(0, 4)
            y0, y1 = 0.2, 0.2 + row_units * pitch
            polygon = lithology.paint(
                axis,
                [(0.5, y0), (3.5, y0), (3.5, y1), (0.5, y1)],
                name,
            )
            rows = self._horizontal_rows(axis, y0, y1)
            height = polygon.get_path().get_extents().height
            figure.clear()
            return rows, height

        for name in ("砂岩", "页岩", "石灰岩", "白云岩"):
            with self.subTest(name=name):
                thin_rows, thin_height = rows_for(name, 5.5)
                thick_rows, thick_height = rows_for(name, 9.5)
                self.assertEqual(len(thick_rows) - len(thin_rows), 4)
                self.assertAlmostEqual(thin_height, 5.5 * pitch, places=8)
                self.assertAlmostEqual(thick_height, 9.5 * pitch, places=8)
                for rows in (thin_rows, thick_rows):
                    for a, b in zip(rows, rows[1:]):
                        self.assertAlmostEqual(b - a, pitch, places=8)

    def test_integer_multiple_height_has_no_floating_point_phase_jump(self):
        pitch = 2.5 / 25.4
        figure = Figure(figsize=(4, 4), dpi=100)
        axis = figure.add_axes([0, 0, 1, 1])
        axis.set_xlim(0, 4)
        axis.set_ylim(0, 4)
        y0, y1 = 0.2, 0.2 + 5 * pitch
        try:
            lithology.paint(
                axis,
                [(0.5, y0), (3.5, y0), (3.5, y1), (0.5, y1)],
                None,
                spec=[{
                    "type": "lines",
                    "angle": 0,
                    "spacing": pitch / lithology.BASE_SPACING,
                }],
            )
            rows = self._horizontal_rows(axis, y0, y1)
            self.assertEqual(len(rows), 5)
            self.assertAlmostEqual(rows[0] - y0, pitch / 2, places=8)
            self.assertAlmostEqual(y1 - rows[-1], pitch / 2, places=8)
        finally:
            figure.clear()

    def test_slanted_and_dashed_vertical_cycles_use_fixed_layer_height(self):
        pitch = 2.5 / 25.4
        diagonal = lithology.build_spec_pattern(
            [{"type": "lines", "angle": 45, "spacing": 1.7}],
            fixed_layer_rows=True,
        ).effective_spec[0]
        diagonal_vertical_pitch = (
            diagonal["spacing"] * lithology.BASE_SPACING
            / abs(math.cos(math.radians(45)))
        )
        self.assertAlmostEqual(diagonal_vertical_pitch, pitch, places=8)

        clay = lithology.PATTERNS["clay_gb"].effective_spec[0]
        dash_cycle = (clay["dash"] + clay["gap"]) * lithology.BASE_SPACING
        self.assertAlmostEqual(dash_cycle, pitch, places=8)

    def test_uniform_section_lithology_uses_one_pattern_and_base_spacing(self):
        holes = [
            {"name": "ZK1", "x": 0, "elev": 100,
             "layers": [("1", "砂岩", 1.0, "")]},
            {"name": "ZK2", "x": 10, "elev": 101,
             "layers": [("1", "砂岩", 1.5, "")]},
            {"name": "ZK3", "x": 20, "elev": 100,
             "layers": [("1", "砂岩", 2.0, "")]},
        ]
        painted = []

        def capture_paint(_axis, points, name, **kwargs):
            painted.append((points, name, kwargs))

        with mock.patch("strat.section.lithology.paint",
                        side_effect=capture_paint), mock.patch(
                            "strat.section.lithology.draw_legend"):
            figure = render_section(holes)
        try:
            self.assertEqual(len(painted), 1)
            points, name, kwargs = painted[0]
            self.assertEqual(name, "砂岩")
            self.assertEqual(len(points), 6)
            self.assertEqual(kwargs["spacing"], lithology.BASE_SPACING)
        finally:
            figure.clear()


class AdaptiveCompressionTests(unittest.TestCase):
    def test_space_sufficient_keeps_candidate_at_true_scale(self):
        layers = [_layer(100.0, compress="是"), _layer(1.0)]

        displayed, compressed = column._display_heights(
            layers, inch_per_m=0.05, target_in=6.0)

        self.assertEqual(displayed, [5.0, 0.05])
        self.assertEqual(compressed, [False, False])

    def test_single_candidate_only_gives_up_required_height(self):
        layers = [_layer(4.0, compress="是"), _layer(1.0)]

        displayed, compressed = column._display_heights(
            layers, inch_per_m=1.0, target_in=4.0)

        self.assertAlmostEqual(math.fsum(displayed), 4.0, places=12)
        self.assertAlmostEqual(displayed[0], 3.0, places=12)
        self.assertGreater(displayed[0], column.COMPRESS_CAP_IN)
        self.assertEqual(displayed[1], 1.0)
        self.assertEqual(compressed, [True, False])

    def test_multiple_candidates_share_reduction_proportionally(self):
        layers = [
            _layer(4.0, compress="是"),
            _layer(3.0, compress="是"),
            _layer(1.0),
        ]

        displayed, compressed = column._display_heights(
            layers, inch_per_m=1.0, target_in=5.0)

        self.assertAlmostEqual(math.fsum(displayed), 5.0, places=12)
        self.assertEqual(compressed, [True, True, False])
        self.assertEqual(displayed[2], 1.0)
        ratios = [
            (real - shown) / (real - column.COMPRESS_CAP_IN)
            for real, shown in zip((4.0, 3.0), displayed[:2])
        ]
        self.assertAlmostEqual(ratios[0], ratios[1], places=12)

        reordered, _ = column._display_heights(
            [layers[1], layers[0], layers[2]],
            inch_per_m=1.0,
            target_in=5.0,
        )
        self.assertEqual(
            sorted(round(value, 12) for value in displayed[:2]),
            sorted(round(value, 12) for value in reordered[:2]),
        )

    def test_insufficient_capacity_stops_at_minimum_without_distorting_others(self):
        layers = [
            _layer(4.0, compress="是"),
            _layer(3.0, compress="是"),
            _layer(5.0),
        ]

        displayed, compressed = column._display_heights(
            layers, inch_per_m=1.0, target_in=4.0)

        self.assertAlmostEqual(displayed[0], column.COMPRESS_CAP_IN)
        self.assertAlmostEqual(displayed[1], column.COMPRESS_CAP_IN)
        self.assertEqual(displayed[2], 5.0)
        self.assertGreater(math.fsum(displayed), 4.0)
        self.assertEqual(compressed, [True, True, False])

    def test_threshold_unmarked_and_invalid_targets(self):
        layers = [
            _layer(column.COMPRESS_TRIGGER_IN, compress="是"),
            _layer(4.0),
        ]
        displayed, compressed = column._display_heights(
            layers, inch_per_m=1.0, target_in=1.0)
        self.assertEqual(displayed,
                         [column.COMPRESS_TRIGGER_IN, 4.0])
        self.assertEqual(compressed, [False, False])

        for target in (None, True, 0, -1, float("nan"), float("inf")):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "目标高度"):
                    column._display_heights(layers, 1.0, target)

    def test_render_rejects_non_finite_total_thickness(self):
        with self.assertRaisesRegex(ValueError, "总厚度.*有限数"):
            render_column([_layer(1e308), _layer(1e308)], scale=100)

    def test_example_auto_scale_uses_space_before_omitting_thick_layer(self):
        layers = column.load_column(PROJECT_ROOT / "examples/column_demo.xlsx")

        for to_scale in (False, True):
            with self.subTest(to_scale=to_scale):
                painted, breaks = [], []

                def capture(axis, polygon, _name, **_kwargs):
                    painted.append((axis, polygon))

                with mock.patch.object(
                        column.lithology, "paint", side_effect=capture), \
                     mock.patch.object(
                        column.lithology, "draw_break",
                        side_effect=lambda *args, **kwargs: breaks.append(args)):
                    figure = render_column(layers, to_scale=to_scale)
                try:
                    scale_text = next(
                        text.get_text() for text in figure.texts
                        if "比例尺" in text.get_text()
                    )
                    scale = float(scale_text.split("1:", 1)[1].split("（", 1)[0])
                    inch_per_m = 39.37 / scale
                    shown = []
                    for _axis, polygon in painted:
                        height = abs(polygon[2][1] - polygon[1][1])
                        shown.append(height * inch_per_m if to_scale else height)
                    self.assertEqual(len(shown), len(layers))
                    for height, layer in zip(shown, layers):
                        self.assertAlmostEqual(
                            height, layer["thick"] * inch_per_m, places=8)
                    self.assertEqual(breaks, [])
                    self.assertNotIn("折断层压缩显示", scale_text)
                    self.assertAlmostEqual(
                        sum(abs(poly[2][1] - poly[1][1])
                            for _axis, poly in painted),
                        max(figure.axes[0].get_ylim()),
                        places=8,
                    )
                finally:
                    figure.clear()

    def test_explicit_scale_partially_compresses_and_reaches_chart_bottom(self):
        layers = column.load_column(PROJECT_ROOT / "examples/column_demo.xlsx")
        inch_per_m = 39.37 / 500

        for to_scale in (False, True):
            with self.subTest(to_scale=to_scale):
                painted, breaks = [], []

                def capture(axis, polygon, _name, **_kwargs):
                    painted.append((axis, polygon))

                with mock.patch.object(
                        column.lithology, "paint", side_effect=capture), \
                     mock.patch.object(
                        column.lithology, "draw_break",
                        side_effect=lambda *args, **kwargs: breaks.append(args)):
                    figure = render_column(
                        layers, scale=500, to_scale=to_scale)
                try:
                    physical = []
                    for _axis, polygon in painted:
                        height = abs(polygon[2][1] - polygon[1][1])
                        physical.append(height * inch_per_m
                                        if to_scale else height)
                    self.assertEqual(len(breaks), 1)
                    self.assertTrue(any(
                        "1:500（折断层压缩显示）" in text.get_text()
                        for text in figure.texts
                    ))
                    for index, (height, layer) in enumerate(zip(physical, layers)):
                        natural = layer["thick"] * inch_per_m
                        if index == 9:
                            self.assertGreater(height, column.COMPRESS_CAP_IN)
                            self.assertLess(height, natural)
                        else:
                            self.assertAlmostEqual(height, natural, places=8)
                    self.assertAlmostEqual(
                        sum(abs(poly[2][1] - poly[1][1])
                            for _axis, poly in painted),
                        max(figure.axes[0].get_ylim()),
                        places=8,
                    )
                    self.assertAlmostEqual(
                        figure.get_figheight(),
                        column.resolve_page("A4")[1],
                        places=8,
                    )
                finally:
                    figure.clear()


class ColumnCorrectnessTests(unittest.TestCase):
    def test_direct_api_validates_and_does_not_mutate_minimal_layers(self):
        layers = [{"lith": "砂岩", "thick": 1.0}]
        before = copy.deepcopy(layers)

        figure = render_column((layer for layer in layers), scale=100)
        try:
            self.assertEqual(layers, before)
        finally:
            figure.clear()

    def test_direct_api_rejects_invalid_layer_records(self):
        invalid = (
            [None],
            [{"lith": "", "thick": 1}],
            [{"lith": "砂岩", "thick": 0}],
            [{"lith": "砂岩", "thick": float("nan")}],
            [{"lith": "砂岩", "thick": True}],
        )
        for layers in invalid:
            with self.subTest(layers=layers):
                with self.assertRaises(ValueError):
                    render_column(layers)

    def test_uncompressed_layers_keep_the_declared_scale_next_to_a_break(self):
        layers = [
            _layer(100.0, compress="是", description="厚层"),
            _layer(1.0, description="较长描述" * 300),
        ]
        painted = []

        def capture_paint(_axis, polygon, _lithology, **_kwargs):
            painted.append(polygon)

        with mock.patch.object(column.lithology, "paint",
                               side_effect=capture_paint):
            figure = render_column(layers, scale=100)
        try:
            self.assertEqual(len(painted), 2)
            compressed_height = painted[0][2][1] - painted[0][1][1]
            regular_height = painted[1][2][1] - painted[1][1][1]
            self.assertGreater(compressed_height, column.COMPRESS_CAP_IN)
            self.assertLess(compressed_height, 100 * 39.37 / 100)
            self.assertAlmostEqual(regular_height, 39.37 / 100, places=6)
            self.assertAlmostEqual(
                compressed_height + regular_height,
                max(figure.axes[0].get_ylim()),
                places=6,
            )
            self.assertTrue(any("折断层压缩显示" in text.get_text()
                                for text in figure.texts))
        finally:
            figure.clear()

    def test_impossible_page_width_is_rejected_instead_of_negative_desc(self):
        with self.assertRaisesRegex(ValueError, "页面可用宽度不足"):
            render_column([_layer()], page="5x20")

    def test_empty_blocks_do_not_accumulate_stack_gaps(self):
        heights = [0.2] + [0.0] * 20 + [0.2]
        centers = [0.1] + [0.5] * 20 + [0.9]

        tops, fits = column._stack_blocks(
            heights, centers, gap=0.1, total=1.0)

        self.assertTrue(fits)
        self.assertAlmostEqual(tops[0], 0.0)
        self.assertAlmostEqual(tops[-1], 0.8)

    def test_empty_strata_means_hide_all_categories(self):
        layer = _layer(period="第四纪", formation="某组")
        layer["unit"] = "旧式地层单位"

        unit_columns, grouped, _has_remark = column._unit_layout(
            [layer], strata=[])

        self.assertEqual(unit_columns, [])
        self.assertFalse(grouped)

    def test_unknown_strata_category_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知地层单位类别"):
            render_column([_layer()], strata={"not-a-category"})

    def test_both_column_layouts_route_all_contacts_through_clearance(self):
        layers = [_layer() for _ in range(4)]
        for index, contact in enumerate(
                ("整合", "平行不整合", "角度不整合")):
            layers[index]["contact"] = contact

        for to_scale in (False, True):
            with self.subTest(to_scale=to_scale):
                with mock.patch.object(
                        column.lithology, "draw_contact") as draw_contact:
                    figure = render_column(
                        layers, scale=100, to_scale=to_scale)
                try:
                    self.assertEqual(
                        [call.args[2] for call in draw_contact.call_args_list],
                        ["整合", "平行不整合", "角度不整合"],
                    )
                    self.assertTrue(all(
                        call.kwargs["clearance_mm"] ==
                        lithology.CONTACT_CLEARANCE_MM
                        for call in draw_contact.call_args_list
                    ))
                finally:
                    figure.clear()

    def test_both_column_layouts_reduce_clearance_around_a_thin_layer(self):
        layers = [_layer(0.15), _layer(1.0)]
        layers[0]["contact"] = "整合"

        for to_scale in (False, True):
            with self.subTest(to_scale=to_scale):
                with mock.patch.object(
                        column.lithology, "draw_contact") as draw_contact:
                    figure = render_column(
                        layers, scale=100, to_scale=to_scale)
                try:
                    thin_contact = next(
                        call for call in draw_contact.call_args_list
                        if call.args[2] == "整合"
                    )
                    self.assertAlmostEqual(
                        thin_contact.kwargs["clearance_mm"],
                        0.15,
                        places=5,
                    )
                finally:
                    figure.clear()


class LegendCorrectnessTests(unittest.TestCase):
    @staticmethod
    def _legend_horizontal_rows(name, main_row_height_mm):
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        _face, pattern_name = lithology.style_of(name)
        pattern = lithology.PATTERNS[pattern_name]
        with lithology.pattern_row_height_scope(main_row_height_mm):
            with lithology.legend_swatch_scope(
                    lithology.LEGEND_SWATCH_HEIGHT_MM,
                    lithology.LEGEND_REPRESENTATIVE_ROWS):
                segments, _marks, _coloured = pattern(
                    0.0, width, 0.0, height,
                    lithology.BASE_SPACING, lithology.BASE_SPACING)
        return sorted({
            round(float(segment[0][1]), 9)
            for segment in segments
            if (abs(float(segment[0][1]) - float(segment[1][1])) < 1e-9
                and -1e-9 <= float(segment[0][1]) <= height + 1e-9)
        })

    def test_standard_swatch_keeps_15_by_10_mm_and_canonical_pattern(self):
        names = ["砂岩", "泥岩", "石灰岩"]
        width = 6.0
        height = lithology.legend_height_in(names, width, item_w_in=1.5)
        figure = Figure(figsize=(width, height), dpi=100)
        painted = []

        def capture_paint(_axis, polygon, name, **kwargs):
            painted.append((polygon, name, kwargs))

        with mock.patch.object(lithology, "paint", side_effect=capture_paint):
            lithology.draw_legend(
                figure, names, [0, 0, 1, 1], item_w_in=1.5)
        try:
            self.assertEqual([item[1] for item in painted], names)
            for polygon, _name, kwargs in painted:
                width_in = abs(polygon[1][0] - polygon[0][0])
                height_in = abs(polygon[2][1] - polygon[1][1])
                self.assertAlmostEqual(width_in, 15 / 25.4, places=6)
                self.assertAlmostEqual(height_in, 10 / 25.4, places=6)
                self.assertEqual(kwargs["spacing"], lithology.BASE_SPACING)
        finally:
            figure.clear()

    def test_sandstone_and_limestone_have_three_representative_bands(self):
        expected_pitch = (
            lithology.LEGEND_SWATCH_HEIGHT_MM
            / lithology.LEGEND_REPRESENTATIVE_ROWS / 25.4)
        for name in ("砂岩", "砾岩", "石灰岩"):
            variants = []
            for main_height in (1.0, 2.5, 4.0, 10.0):
                rows = self._legend_horizontal_rows(name, main_height)
                variants.append(rows)
                self.assertEqual(len(rows), 4, name)
                for first, second in zip(rows, rows[1:]):
                    self.assertAlmostEqual(
                        second - first, expected_pitch, places=7)
            self.assertTrue(all(rows == variants[0] for rows in variants[1:]))

    def test_shale_keeps_dense_texture_independent_of_main_row_height(self):
        variants = [
            self._legend_horizontal_rows("页岩", main_height)
            for main_height in (1.0, 2.5, 4.0, 10.0)
        ]
        self.assertTrue(all(rows == variants[0] for rows in variants[1:]))
        self.assertGreaterEqual(len(variants[0]), 8)
        self.assertNotEqual(len(variants[0]), 4)

    def test_composite_shale_keeps_dense_base_and_three_modifier_rows(self):
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        for name in ("硅质页岩", "钙质页岩", "碳质页岩"):
            _face, pattern_name = lithology.style_of(name)
            pattern = lithology.PATTERNS[pattern_name]
            with lithology.legend_swatch_scope(10, 3):
                segments, marks, _coloured = pattern(
                    0.0, width, 0.0, height,
                    lithology.BASE_SPACING, lithology.BASE_SPACING)
            dense_rows = {
                round(float(segment[0][1]), 9)
                for segment in segments
                if abs(float(segment[0][1]) - float(segment[1][1])) < 1e-9
            }
            modifier_rows = {
                round(float(y), 9)
                for mark in marks for y in mark[1]
                if -1e-9 <= float(y) <= height + 1e-9
            }
            self.assertGreaterEqual(len(dense_rows), 8, name)
            self.assertEqual(len(modifier_rows), 3, name)
            expected = [height / 6, height / 2, height * 5 / 6]
            for actual, target in zip(sorted(modifier_rows), expected):
                self.assertAlmostEqual(actual, target, places=7)

    def test_sandy_soil_legacy_dot_pattern_is_normalised_to_three_rows(self):
        _face, pattern_name = lithology.style_of("砂土")
        pattern = lithology.PATTERNS[pattern_name]
        self.assertTrue(hasattr(pattern, "legend_spec_for"))
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        variants = []
        for main_height in (1.0, 4.0, 10.0):
            with lithology.pattern_row_height_scope(main_height):
                with lithology.legend_swatch_scope(10, 3):
                    _segments, marks, _coloured = pattern(
                        0.0, width, 0.0, height,
                        lithology.BASE_SPACING, lithology.BASE_SPACING)
            variants.append(sorted({
                round(float(y), 9)
                for mark in marks for y in mark[1]
                if -1e-9 <= float(y) <= height + 1e-9
            }))
        self.assertTrue(all(rows == variants[0] for rows in variants[1:]))
        self.assertEqual(len(variants[0]), 3)

    def test_explicit_custom_rows_are_not_forced_to_three(self):
        spec = [{"type": "rows", "spacing": 1.2,
                 "rows": [["小点"], ["横线"], ["十字"]]},
                {"type": "lines", "angle": 0, "spacing": 1.7}]
        pattern = lithology.build_spec_pattern(spec, fixed_layer_rows=True)
        self.assertFalse(pattern.legend_fixed_rows)
        self.assertEqual(pattern.legend_spec_for(10, 3), pattern.source_spec)

    def test_grid_balances_rows_and_avoids_singleton_last_row(self):
        for count, expected_rows in ((19, 2), (28, 3)):
            items = [f"岩性{index + 1}" for index in range(count)]
            rows, _cols, _cell, layouts, _heights = lithology._legend_grid(
                items, 7.4, fontsize=8.0)
            self.assertEqual(rows, expected_rows)
            counts = [sum(item[4] == row for item in layouts)
                      for row in range(rows)]
            self.assertLessEqual(max(counts) - min(counts), 1)
            self.assertGreater(min(counts), 1)
            self.assertEqual([item[0] for item in layouts], items)

    def test_grid_keeps_complete_labels_with_at_most_two_lines(self):
        names = ["含砾中粗粒长石石英砂岩（未固结）",
                 "钙质页岩", "石灰岩"]
        _rows, _cols, _cell, layouts, _heights = lithology._legend_grid(
            names, 7.4, fontsize=8.0)
        for original, lines, *_rest in layouts:
            self.assertEqual("".join(lines), original)
            self.assertLessEqual(len(lines), 2)
            self.assertFalse(any(line.endswith("（") for line in lines))
            self.assertFalse(any(line.startswith("）") for line in lines))

    def test_grid_prefers_to_keep_parenthetical_phrase_together(self):
        name = "钾镁煌斑岩(橄榄金云煌斑岩)"
        self.assertEqual(
            lithology._wrap_legend_label(name, 8.2),
            ["钾镁煌斑岩", "(橄榄金云煌斑岩)"],
        )

    def test_excessive_legend_is_rejected_instead_of_growing_past_page(self):
        names = [
            name for name, (_face, pattern) in lithology.LITHOLOGY.items()
            if pattern
        ][:80]
        layers = []
        for index, name in enumerate(names, 1):
            layer = _layer(0.1)
            layer["no"] = str(index)
            layer["lith"] = name
            layers.append(layer)
        for to_scale in (False, True):
            with self.subTest(to_scale=to_scale):
                with self.assertRaisesRegex(ValueError, "图例项目过多"):
                    render_column(
                        layers, page="A4", landscape=True,
                        show_legend=True, to_scale=to_scale)

    def test_long_legend_name_is_wrapped_without_truncation(self):
        name = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
        width = 3.2
        height = lithology.legend_height_in([name], width, item_w_in=3.2)
        figure = Figure(figsize=(width, height), dpi=100)
        axis = lithology.draw_legend(
            figure, [name], [0, 0, 1, 1], item_w_in=3.2)
        try:
            label_parts = [
                text.get_text() for text in axis.texts
                if not text.get_text().startswith("岩性图例")
                and not text.get_text().startswith("注：")
            ]
            self.assertEqual("".join(label_parts), name)
        finally:
            figure.clear()

    def test_column_legend_is_below_table_and_does_not_take_a_table_column(self):
        layers = [_layer(), _layer()]
        layers[1]["lith"] = "泥岩"
        plain = render_column(layers, show_legend=False)
        with_legend = render_column(layers, show_legend=True)
        try:
            self.assertEqual(len(plain.axes), 1)
            self.assertEqual(len(with_legend.axes), 2)
            main, legend = with_legend.axes
            self.assertLessEqual(legend.get_position().y1,
                                 main.get_position().y0 + 1e-9)
            self.assertAlmostEqual(
                plain.axes[0].get_position().width * plain.get_figwidth(),
                main.get_position().width * with_legend.get_figwidth(),
                places=6)
            self.assertTrue(any("岩性图例" in text.get_text()
                                for text in legend.texts))
            self.assertFalse(any(text.get_text() == "图  例"
                                 for text in main.texts))
        finally:
            plain.clear()
            with_legend.clear()

    def test_standard_legend_rejects_unrecognised_lithology(self):
        layer = _layer()
        layer["lith"] = "完全未定义的岩性"
        with self.assertRaisesRegex(ValueError, "没有已定义的唯一花纹"):
            render_column([layer], show_legend=True)

    def test_legend_item_width_cannot_shrink_below_standard_layout(self):
        self.assertEqual(column.WIDTH_LIMITS["图例"], (1.8, 6.0))


if __name__ == "__main__":
    unittest.main()
