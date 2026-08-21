"""Regression tests for chart ordering, scale, and layout invariants."""

import copy
import os
import tempfile
import unittest
from unittest import mock


os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from strat import column
from strat.column import render_column
from strat.section import _layer_order, render_section


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
            self.assertAlmostEqual(compressed_height,
                                   column.COMPRESS_CAP_IN, places=6)
            self.assertAlmostEqual(regular_height, 39.37 / 100, places=6)
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


if __name__ == "__main__":
    unittest.main()
