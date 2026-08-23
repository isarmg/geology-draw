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

from strat import column, gb958, lithology
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

    @staticmethod
    def _legend_geometry(name):
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        _face, pattern_name = lithology.style_of(name)
        pattern = lithology.PATTERNS[pattern_name]
        with lithology.legend_swatch_scope(10, 3):
            return pattern(
                0.0, width, 0.0, height,
                lithology.BASE_SPACING, lithology.BASE_SPACING)

    @classmethod
    def _legend_mark_rows(cls, name, marker=None):
        _segments, marks, _coloured = cls._legend_geometry(name)
        rows = {}
        for mark in marks:
            if marker is not None and mark[2] != marker:
                continue
            for x, y in zip(mark[0], mark[1]):
                rows.setdefault(round(float(y) * 25.4, 4), []).append(
                    round(float(x) * 25.4, 4))
        return {key: sorted(value) for key, value in sorted(rows.items())}

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

    def test_basic_rpbp_symbol_is_one_complete_centred_motif(self):
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4

        spec, _face = gb958.spec_for("RPBP000014", "生物碎屑")
        pattern = lithology.build_spec_pattern(spec)
        with lithology.legend_swatch_scope(10, 3):
            with lithology.legend_single_motif_scope():
                _segments, marks, _coloured = pattern(
                    0.0, width, 0.0, height,
                    lithology.BASE_SPACING, lithology.BASE_SPACING)
        points = [(float(x) * 25.4, float(y) * 25.4)
                  for mark in marks for x, y in zip(mark[0], mark[1])]
        self.assertEqual(points, [(7.5, 5.0)])

        spec, _face = gb958.spec_for("RPBP000036", "不等粒")
        pattern = lithology.build_spec_pattern(spec)
        with lithology.legend_swatch_scope(10, 3):
            with lithology.legend_single_motif_scope():
                segments, _marks, _coloured = pattern(
                    0.0, width, 0.0, height,
                    lithology.BASE_SPACING, lithology.BASE_SPACING)
        xs = [float(x) * 25.4 for segment in segments for x, _y in segment]
        ys = [float(y) * 25.4 for segment in segments for _x, y in segment]
        self.assertEqual(len(segments), 4)
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 7.5, places=6)
        self.assertAlmostEqual((min(ys) + max(ys)) / 2, 5.0, places=6)

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

    def test_standard_sandstone_uses_table4_four_three_four_array(self):
        for name in ("砂岩", "粗砂岩", "中砂岩", "细砂岩"):
            with self.subTest(name=name):
                rows = self._legend_mark_rows(name, ".")
                self.assertEqual([len(items) for items in rows.values()],
                                 [4, 3, 4])
                first, middle, last = list(rows.values())
                self.assertEqual(first, last)
                self.assertAlmostEqual(
                    sum(first) / len(first), 7.5, places=6)
                self.assertAlmostEqual(
                    sum(middle) / len(middle), 7.5, places=6)

    def test_clastic_qualifier_has_one_complete_alternating_anchor_per_band(self):
        rows = self._legend_mark_rows("钙质砂岩", r"$\mathrm{Ca}$")
        self.assertEqual([len(items) for items in rows.values()], [1, 1, 1])
        first, middle, last = [items[0] for items in rows.values()]
        self.assertEqual(first, last)
        self.assertLess(first, 7.5)
        self.assertGreater(middle, 7.5)

        segments, _marks, _coloured = self._legend_geometry("含砾砂岩")
        # Each ellipse is made of 16 short segments.  Its leftmost/rightmost
        # points must clear the frame, and three bands contain one ellipse.
        curved = [segment for segment in segments
                  if abs(float(segment[0][1]) - float(segment[1][1])) > 1e-7]
        self.assertEqual(len(curved), 48)
        xs = [float(point[0]) * 25.4
              for segment in curved for point in segment]
        self.assertGreaterEqual(min(xs), lithology.LEGEND_SYMBOL_CLEARANCE_MM)
        self.assertLessEqual(
            max(xs),
            lithology.LEGEND_SWATCH_WIDTH_MM
            - lithology.LEGEND_SYMBOL_CLEARANCE_MM)

    def test_clastic_base_and_modifier_share_standard_ratio_slots(self):
        quality = self._legend_mark_rows("钙质砂岩", ".")
        contained = self._legend_mark_rows("含砾砂岩", ".")
        self.assertEqual([len(items) for items in quality.values()], [2, 2, 2])
        self.assertEqual([len(items) for items in contained.values()],
                         [3, 3, 3])

        cases = (
            ("钙质砂岩", [3, 6]),
            ("含砾砂岩", [3, 9]),
            ("石英杂砂岩", [6, 6]),
            ("长石石英砂岩", [3, 3, 6]),
        )
        for name, expected_counts in cases:
            with self.subTest(name=name):
                _face, pattern_name = lithology.style_of(name)
                spec = lithology.PATTERNS[pattern_name].source_spec
                grouped = {}
                bounds = lithology._legend_symbol_group_bounds(spec)
                widths = {}
                slots = None
                for index, element in enumerate(spec):
                    group = element.get("legend_group")
                    if group is None:
                        continue
                    slots = int(element["legend_slots"])
                    grouped.setdefault(group, set()).add(
                        int(element["legend_slot_mask"]))
                    widths[group] = bounds[index][1] - bounds[index][0]
                masks = [next(iter(values)) for values in grouped.values()]
                self.assertTrue(all(len(values) == 1
                                    for values in grouped.values()))
                self.assertEqual(sorted(mask.bit_count() for mask in masks),
                                 expected_counts)
                occupied = 0
                for mask in masks:
                    self.assertEqual(occupied & mask, 0)
                    occupied |= mask
                self.assertEqual(occupied, (1 << (3 * slots)) - 1)
                slot_width = (lithology.LEGEND_SWATCH_WIDTH_MM
                              / slots / 25.4)
                self.assertTrue(all(width <= slot_width + 1e-9
                                    for width in widths.values()))

    def test_generated_quality_composite_uses_the_same_shared_slots(self):
        # “钙质泥岩” is intentionally resolved by the generic qualifier
        # fallback rather than the static GB catalogue.  That path must obey
        # the same 2:1 matrix and must not overlay two independently centred
        # symbol arrays.
        _face, pattern_name = lithology.style_of("钙质泥岩")
        pattern = lithology.PATTERNS[pattern_name]
        groups = {}
        slots = None
        for element in pattern.source_spec:
            group = element.get("legend_group")
            if group is None:
                continue
            slots = int(element["legend_slots"])
            groups.setdefault(group, set()).add(
                int(element["legend_slot_mask"]))
        self.assertEqual(slots, 3)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(len(values) == 1 for values in groups.values()))
        masks = [next(iter(values)) for values in groups.values()]
        self.assertEqual(sorted(mask.bit_count() for mask in masks), [3, 6])
        self.assertEqual(masks[0] & masks[1], 0)
        self.assertEqual(masks[0] | masks[1], (1 << 9) - 1)

        calcium_rows = self._legend_mark_rows(
            "钙质泥岩", r"$\mathrm{Ca}$")
        self.assertEqual([len(items) for items in calcium_rows.values()],
                         [1, 1, 1])

    def test_generated_multi_modifier_is_complete_and_history_independent(self):
        def resolve_signature(prime_inner):
            local_lithologies = dict(lithology.LITHOLOGY)
            local_patterns = dict(lithology.PATTERNS)
            with mock.patch.object(
                    lithology, "LITHOLOGY", local_lithologies), \
                    mock.patch.object(
                        lithology, "PATTERNS", local_patterns):
                if prime_inner:
                    self.assertEqual(
                        lithology.resolve("硅质泥岩"), "硅质泥岩")
                self.assertEqual(
                    lithology.resolve("钙质硅质泥岩"), "钙质硅质泥岩")
                _face, pattern_name = lithology.style_of("钙质硅质泥岩")
                spec = lithology.PATTERNS[pattern_name].source_spec
                return [
                    (element.get("marker") or element.get("shape"),
                     int(element["legend_group"]),
                     int(element["legend_slot_mask"]),
                     int(element["legend_slots"]))
                    for element in spec if "legend_group" in element
                ]

        direct = resolve_signature(False)
        primed = resolve_signature(True)
        self.assertEqual(direct, primed)
        self.assertEqual(
            [item[0] for item in direct],
            ["横条", r"$\mathrm{Si}$", r"$\mathrm{Ca}$"])
        masks = [item[2] for item in direct]
        self.assertEqual(sorted(mask.bit_count() for mask in masks),
                         [3, 3, 6])
        self.assertEqual(masks[0] | masks[1] | masks[2], (1 << 12) - 1)
        self.assertFalse(masks[0] & masks[1])
        self.assertFalse(masks[0] & masks[2])
        self.assertFalse(masks[1] & masks[2])

        # Both an explicitly grouped GB base and a legacy phase-only base
        # must keep their existing constituent families when Ca is added.
        for name, expected in (
                ("钙质含砾砂岩", [".", "椭圆", r"$\mathrm{Ca}$"]),
                ("钙质长石砂岩", [".", "N形", r"$\mathrm{Ca}$"])):
            with self.subTest(name=name):
                _face, pattern_name = lithology.style_of(name)
                spec = lithology.PATTERNS[pattern_name].source_spec
                families = [
                    element for element in spec
                    if "legend_group" in element
                ]
                self.assertEqual(
                    [element.get("marker") or element.get("shape")
                     for element in families], expected)
                self.assertEqual(
                    sorted(int(element["legend_slot_mask"]).bit_count()
                           for element in families), [3, 3, 6])

    def test_shale_quality_and_contains_use_distinct_standard_quotas(self):
        quality = self._legend_mark_rows("钙质页岩", r"$\mathrm{Ca}$")
        contained = self._legend_mark_rows("含碳质页岩", r"$\mathrm{C}$")
        self.assertEqual([len(items) for items in quality.values()], [2, 1, 2])
        self.assertEqual([len(items) for items in contained.values()], [1, 1, 1])
        first, middle, last = [items[0] for items in contained.values()]
        self.assertEqual(first, last)
        self.assertLess(first, 7.5)
        self.assertGreater(middle, 7.5)

    def test_brick_modifier_uses_one_two_one_cell_centres(self):
        rows = self._legend_mark_rows("铁质灰岩", r"$\mathrm{Fe}$")
        self.assertEqual([len(items) for items in rows.values()], [1, 2, 1])
        first, middle, last = list(rows.values())
        self.assertEqual(first, [7.5])
        self.assertEqual(first, last)
        self.assertAlmostEqual(sum(middle) / 2, 7.5, places=6)

    def test_chert_nodules_use_internal_course_boundaries(self):
        original = lithology._PRIMS["shape"]

        def positions_for(flip):
            captured = []

            def capture(*args, **kwargs):
                captured.extend(
                    kwargs.get("_legend_explicit_positions") or [])
                return original(*args, **kwargs)

            lithology._SHAPE_FLIP[0] = flip
            with mock.patch.dict(lithology._PRIMS, {"shape": capture}):
                self._legend_geometry("含燧石结核灰岩")
            return captured

        previous_flip = lithology._SHAPE_FLIP[0]
        try:
            captured = positions_for(False)  # inverted depth axis
            normal_axis = positions_for(True)
        finally:
            lithology._SHAPE_FLIP[0] = previous_flip

        _face, pattern_name = lithology.style_of("含燧石结核灰岩")
        element = next(
            item for item in lithology.PATTERNS[pattern_name].source_spec
            if item.get("legend_course_boundary"))
        xmin, xmax, ymin, ymax = lithology._legend_element_bounds(element)
        centres = sorted(
            ((round((x + (xmin + xmax) / 2) * 25.4, 4),
              round((y + (ymin + ymax) / 2) * 25.4, 4))
             for x, y in captured),
            key=lambda point: (point[1], point[0]))
        self.assertEqual(len(centres), 3)
        self.assertEqual([point[1] for point in centres],
                         [3.3333, 3.3333, 6.6667])
        self.assertAlmostEqual(centres[0][0] + centres[1][0], 15.0,
                               places=4)
        self.assertEqual(centres[2][0], 7.5)
        normal_rows = {}
        for _x, y in normal_axis:
            normal_rows[round(y * 25.4, 4)] = (
                normal_rows.get(round(y * 25.4, 4), 0) + 1)
        # On a normal y-up catalogue axis the denser boundary remains
        # visually upper; on a depth y-down axis it remains visually upper
        # by occupying the smaller data-coordinate value.
        self.assertEqual([normal_rows[key] for key in sorted(normal_rows)],
                         [1, 2])

        outline = lithology._SHAPES["实心透镜"][0]
        self.assertEqual(outline[0], outline[-1])
        self.assertGreaterEqual(len(lithology._SHAPES["实心透镜"]), 8)

    def test_ell_modifier_is_visually_centred_on_brick_cells(self):
        segments, _marks, _coloured = self._legend_geometry("灰质白云岩")
        glyph_size = 0.32 * lithology.BASE_SPACING * 25.4
        horizontals = []
        for segment in segments:
            x0, y0 = (float(value) * 25.4 for value in segment[0])
            x1, y1 = (float(value) * 25.4 for value in segment[1])
            if (abs(y0 - y1) < 1e-8
                    and math.isclose(abs(x1 - x0), glyph_size,
                                     rel_tol=0, abs_tol=1e-7)):
                horizontals.append(((x0 + x1) / 2, (y0 + y1) / 2))

        rows = {}
        for x, y in horizontals:
            rows.setdefault(round(y, 4), []).append(round(x, 4))
        rows = [sorted(items) for _y, items in sorted(rows.items())]
        self.assertEqual([len(items) for items in rows], [1, 2, 1])
        self.assertEqual(rows[0], [7.5])
        self.assertEqual(rows[0], rows[2])
        self.assertAlmostEqual(sum(rows[1]) / 2, 7.5, places=4)
        self.assertGreaterEqual(
            min(x for row in rows for x in row) - glyph_size / 2,
            lithology.LEGEND_SYMBOL_CLEARANCE_MM)
        self.assertLessEqual(
            max(x for row in rows for x in row) + glyph_size / 2,
            lithology.LEGEND_SWATCH_WIDTH_MM
            - lithology.LEGEND_SYMBOL_CLEARANCE_MM)

    def test_ell_keeps_declared_horizontal_phase_and_pitch(self):
        base, _marks = lithology._prim_ell(
            0.0, 1.0, 0.0, 1.0, spacing=2.0, xspacing=3.0,
            xoff=0.0, stagger=False)
        shifted, _marks = lithology._prim_ell(
            0.0, 1.0, 0.0, 1.0, spacing=2.0, xspacing=3.0,
            xoff=0.5, stagger=False)
        expected_shift = 0.5 * 3.0 * lithology.BASE_SPACING
        self.assertAlmostEqual(
            shifted[0][0][0] - base[0][0][0], expected_shift, places=9)

        pattern = lithology.build_spec_pattern([
            {"type": "brick", "spacing": 2.0, "ratio": 2.2},
            {"type": "ell", "spacing": 3.0, "xspacing": 4.0},
        ], fixed_layer_rows=True)
        effective = pattern.effective_spec_for(5.0)
        ell = next(element for element in effective
                   if element["type"] == "ell")
        self.assertEqual(ell["xspacing"], 4.0)

    def test_ell_keeps_lower_left_visual_orientation_on_both_axis_directions(self):
        previous_flip = lithology._SHAPE_FLIP[0]
        try:
            for flip, visual_scale in ((False, -1.0), (True, 1.0)):
                with self.subTest(y_axis="up" if flip else "down"):
                    lithology._SHAPE_FLIP[0] = flip
                    segments, _marks = lithology._prim_ell(
                        0.0, 1.0, 0.0, 1.0,
                        _legend_single_anchor=(0.5, 0.5))
                    horizontal, vertical = segments
                    baseline = float(horizontal[0][1])
                    tip = next(
                        float(point[1]) for point in vertical
                        if not math.isclose(float(point[1]), baseline,
                                            abs_tol=1e-12))
                    # Screen-space y grows upward after applying the axis
                    # direction.  The vertical stem must rise from the left
                    # end of the horizontal stroke, i.e. remain visually └.
                    self.assertGreater(
                        (tip - baseline) * visual_scale, 0.0)
                    self.assertEqual(vertical[0][0], horizontal[0][0])
        finally:
            lithology._SHAPE_FLIP[0] = previous_flip

    def test_asymmetric_vector_rows_are_centred_by_visible_bounds(self):
        segments, _marks, _coloured = self._legend_geometry("拉斑玄武岩")
        bands = {0: [], 1: [], 2: []}
        for segment in segments:
            points = [(float(x) * 25.4, float(y) * 25.4)
                      for x, y in segment]
            centre_y = sum(point[1] for point in points) / len(points)
            band = min(bands, key=lambda index: abs(
                centre_y - (index + 0.5) * 10 / 3))
            bands[band].extend(point[0] for point in points)

        self.assertEqual(
            [round((min(xs) + max(xs)) / 2, 5)
             for xs in bands.values()],
            [7.5, 7.5, 7.5])
        counts = []
        for xs in bands.values():
            # Γ is 1.6 mm wide and the bold duplicate extends 0.1 mm.  Each
            # gap larger than that visible glyph width starts another symbol.
            ordered = sorted(set(round(value, 6) for value in xs))
            counts.append(1 + sum(
                right - left > 1.700001
                for left, right in zip(ordered, ordered[1:])))
        self.assertEqual(counts, [4, 3, 4])

    def test_feldspathic_sandstone_uses_shared_four_slot_rows(self):
        rows = self._legend_mark_rows("长石砂岩", ".")
        self.assertEqual([len(items) for items in rows.values()], [2, 2, 2])
        first, middle, last = list(rows.values())
        self.assertEqual(first, last)
        self.assertNotEqual(first, middle)

        segments, _marks, _coloured = self._legend_geometry("长石砂岩")
        # Each N has two vertical stems; two N symbols per band therefore
        # produce four long vertical segments on each of the three rows.
        stems = [segment for segment in segments
                 if (abs(float(segment[0][0]) - float(segment[1][0])) < 1e-8
                     and abs(float(segment[0][1]) - float(segment[1][1]))
                     > 1.0 / 25.4)]
        stem_rows = {}
        for segment in stems:
            centre = round(
                (float(segment[0][1]) + float(segment[1][1])) * 12.7, 4)
            stem_rows[centre] = stem_rows.get(centre, 0) + 1
        self.assertEqual(sorted(stem_rows.values()), [4, 4, 4])

    def test_basic_composites_use_fixed_local_component_offsets(self):
        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        cases = (
            ("RPBP000038", "似斑状", (0.0, 0.0)),
            ("RPBP000041", "含斑", (1.45, 0.95)),
            ("RPBP000057", "石泡", (0.0, 0.0)),
            ("RPBP000075", "泥晶", (0.0, 0.0)),
            ("RPBP010078", "细晶", (0.0, 0.0)),
            ("RPBP010082", "渗透(状)", (0.0, 0.0)),
        )
        for code, name, expected_offset in cases:
            with self.subTest(code=code):
                spec, _face = gb958.spec_for(code, name)
                anchors = lithology._legend_single_motif_anchors(
                    spec, width, height)
                self.assertEqual(len(anchors), 2)
                first, second = anchors[0], anchors[1]
                self.assertAlmostEqual(
                    (second[0] - first[0]) * 25.4,
                    expected_offset[0], places=6)
                self.assertAlmostEqual(
                    (second[1] - first[1]) * 25.4,
                    expected_offset[1], places=6)

        spec, _face = gb958.spec_for("RPBP000037", "斑状")
        self.assertEqual(len(spec), 1)
        spec, _face = gb958.spec_for("RPBP000040", "少斑")
        self.assertEqual(len(spec), 1)

    def test_basic_texture_has_one_independent_central_feature(self):
        spec, _face = gb958.spec_for("RPBP010084", "眼球(状)")
        pattern = lithology.build_spec_pattern(spec, fixed_layer_rows=True)
        calls = []
        original = lithology._PRIMS["shape"]

        def capture(*args, **kwargs):
            calls.append(kwargs.get("_legend_single_anchor"))
            return original(*args, **kwargs)

        width = lithology.LEGEND_SWATCH_WIDTH_MM / 25.4
        height = lithology.LEGEND_SWATCH_HEIGHT_MM / 25.4
        with mock.patch.dict(lithology._PRIMS, {"shape": capture}):
            with lithology.legend_swatch_scope(10, 2):
                pattern(0.0, width, 0.0, height,
                        lithology.BASE_SPACING, lithology.BASE_SPACING)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0])
        self.assertIsNotNone(calls[1])

    def test_pyroclastic_legends_share_one_four_slot_standard_matrix(self):
        cases = (
            ("RPMM034002", "安山质集块岩", 3, [3, 9]),
            ("RPMM034015", "安山质熔结火山碎屑岩(未分)",
             3, [3, 3, 6]),
            ("RPMM034028", "安山质火山碎屑熔岩(未分)",
             9, [3, 9]),
        )
        for code, name, composition_count, expected_counts in cases:
            with self.subTest(code=code):
                spec, _face = gb958.spec_for(code, name)
                groups = {}
                for element in spec:
                    group = element.get("legend_group")
                    if group is None:
                        continue
                    groups.setdefault(group, set()).add(
                        int(element["legend_slot_mask"]))
                self.assertTrue(groups)
                self.assertTrue(all(len(masks) == 1
                                    for masks in groups.values()))
                masks = [next(iter(value)) for value in groups.values()]
                self.assertEqual(sorted(mask.bit_count() for mask in masks),
                                 expected_counts)
                self.assertEqual(
                    next(iter(groups[1])).bit_count(), composition_count)
                occupied = 0
                for mask in masks:
                    self.assertEqual(occupied & mask, 0)
                    occupied |= mask
                self.assertEqual(occupied, (1 << 12) - 1)

    def test_composite_motif_stagger_is_legend_only(self):
        code = "RPSE021091"
        name = next(name for entry_code, name in gb958.ENTRIES
                    if entry_code == code)
        spec, _face = gb958.spec_for(code, name)
        mud_crystal = [
            element for element in spec
            if ((element.get("type") == "shape"
                 and element.get("shape") == "竖梭")
                or (element.get("type") == "markers"
                    and element.get("marker") == "."))
        ]
        self.assertEqual(len(mud_crystal), 2)
        self.assertTrue(all(element["stagger"] is False
                            for element in mud_crystal))
        self.assertTrue(all(element["legend_stagger"] is True
                            for element in mud_crystal))
        self.assertEqual({element["xoff"] for element in mud_crystal}, {0.5})

        pyro_code = "RPMM031015"
        pyro_name = next(name for entry_code, name in gb958.ENTRIES
                         if entry_code == pyro_code)
        pyro_spec, _face = gb958.spec_for(pyro_code, pyro_name)
        colons = [element for element in pyro_spec
                  if element.get("marker") == r"$\mathrm{:}$"]
        self.assertEqual(len(colons), 2)
        self.assertTrue(all(element["stagger"] is False
                            for element in colons))
        self.assertTrue(all(element["legend_stagger"] is True
                            for element in colons))

        # Outside a legend scope the two colon components retain a constant
        # local 0.16-cell separation on every row of the actual chart.
        pattern = lithology.build_spec_pattern(
            pyro_spec, fixed_layer_rows=True)
        width, height = 30 / 25.4, 25 / 25.4
        _segments, marks, _coloured = pattern(
            0.0, width, 0.0, height,
            lithology.BASE_SPACING, lithology.BASE_SPACING)
        colon_marks = [mark for mark in marks
                       if mark[2] == r"$\mathrm{:}$"]
        self.assertEqual(len(colon_marks), 2)
        by_row = []
        for mark in colon_marks:
            rows = {}
            for x, y in zip(mark[0], mark[1]):
                rows.setdefault(round(float(y), 9), []).append(float(x))
            by_row.append(rows)
        common_rows = sorted(set(by_row[0]) & set(by_row[1]))
        self.assertGreaterEqual(len(common_rows), 3)
        expected_gap = 0.16 * 2.0 * lithology.BASE_SPACING
        for row in common_rows:
            nearest = min(abs(left - right)
                          for left in by_row[0][row]
                          for right in by_row[1][row])
            self.assertAlmostEqual(nearest, expected_gap, places=9)

    def test_all_pyroclastic_slot_groups_fit_without_physical_overlap(self):
        slot_width = lithology.LEGEND_SWATCH_WIDTH_MM / 4 / 25.4
        groups = dict(gb958.catalog())["火山碎屑岩"]
        for _subsection, entries in groups:
            for code, name in entries:
                with self.subTest(code=code):
                    spec, _face = gb958.spec_for(code, name)
                    grouped_masks = {}
                    grouped_widths = {}
                    bounds = lithology._legend_symbol_group_bounds(spec)
                    for index, element in enumerate(spec):
                        group = element.get("legend_group")
                        if group is None:
                            continue
                        grouped_masks.setdefault(group, set()).add(
                            int(element["legend_slot_mask"]))
                        if index in bounds:
                            left, right = bounds[index]
                            grouped_widths[group] = right - left
                    masks = []
                    for group, values in grouped_masks.items():
                        self.assertEqual(len(values), 1)
                        mask = next(iter(values))
                        self.assertGreater(mask, 0)
                        masks.append(mask)
                        self.assertLessEqual(
                            grouped_widths[group], slot_width + 1e-9)
                    occupied = 0
                    for mask in masks:
                        self.assertEqual(occupied & mask, 0)
                        occupied |= mask
                    self.assertEqual(occupied, (1 << 12) - 1)

    def test_explicit_custom_rows_are_not_forced_to_three(self):
        spec = [{"type": "rows", "spacing": 1.2,
                 "rows": [["小点"], ["横线"], ["十字"]]},
                {"type": "lines", "angle": 0, "spacing": 1.7}]
        pattern = lithology.build_spec_pattern(spec, fixed_layer_rows=True)
        self.assertFalse(pattern.legend_fixed_rows)
        self.assertEqual(pattern.legend_spec_for(10, 3), pattern.source_spec)

    def test_shared_slot_mask_rejects_empty_or_out_of_matrix_metadata(self):
        base = {"type": "markers", "marker": ".", "spacing": 2.0}
        cases = (
            (dict(base, legend_slots=4, legend_slot_mask=0), "非零"),
            (dict(base, legend_slot_mask=1), "legend_slots"),
            (dict(base, legend_slots=4, legend_slot_period=3,
                  legend_slot_mask=1 << 12), "超出"),
        )
        for element, message in cases:
            with self.subTest(element=element):
                with self.assertRaisesRegex(ValueError, message):
                    lithology.build_spec_pattern([element])

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
