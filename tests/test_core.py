"""Regression tests for the headless stratigraphy core."""

import copy
import os
import tempfile
import unittest
from pathlib import Path


# Matplotlib must have a writable, headless-friendly cache before strat imports.
os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from strat.column import parse_column, resolve_page
from strat.section import parse_section, render_section
from strat.tableio import read_table


class TableIOTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="strat-core-test-")
        self.directory = Path(self._temporary_directory.name)

    def tearDown(self):
        self._temporary_directory.cleanup()

    def test_reads_utf8_csv(self):
        path = self.directory / "column.csv"
        path.write_text(
            "岩性,厚度,描述\n砂岩,2.5,灰白色砂岩\n泥岩,1,深灰色泥岩\n",
            encoding="utf-8",
        )

        rows = read_table(path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["岩性"], "砂岩")
        self.assertEqual(rows[0]["厚度"], "2.5")
        self.assertEqual(rows[1]["描述"], "深灰色泥岩")

    def test_rejects_unknown_file_extension(self):
        path = self.directory / "column.txt"
        path.write_text("岩性,厚度\n砂岩,1\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "不支持的文件格式"):
            read_table(path)


class ColumnValidationTests(unittest.TestCase):
    def test_rejects_zero_negative_and_nan_thickness(self):
        for value in ("0", "-1", "nan"):
            with self.subTest(thickness=value):
                with self.assertRaisesRegex(ValueError, "厚度.*大于 0.*有限数"):
                    parse_column([{"岩性": "砂岩", "厚度": value}])

    def test_rejects_invalid_and_non_finite_page_sizes(self):
        for page in ("not-a-page", "nanx20", "20xinf", "20x-nan", "10x"):
            with self.subTest(page=page):
                with self.assertRaises(ValueError):
                    resolve_page(page)


class SectionValidationTests(unittest.TestCase):
    @staticmethod
    def _row(name, distance, elevation, layer_no="1", lithology="砂岩",
             thickness="1"):
        return {
            "钻孔": name,
            "距离": str(distance),
            "孔口标高": str(elevation),
            "层号": str(layer_no),
            "岩性": lithology,
            "厚度": str(thickness),
        }

    def test_rejects_a_single_borehole(self):
        with self.assertRaisesRegex(ValueError, "至少需要 2 个钻孔"):
            parse_section([self._row("ZK1", 0, 10)])

    def test_rejects_distinct_boreholes_at_the_same_distance(self):
        rows = [self._row("ZK1", 0, 10), self._row("ZK2", 0, 11)]

        with self.assertRaisesRegex(ValueError, "不同的钻孔距离"):
            parse_section(rows)

    def test_rejects_duplicate_layer_number_in_one_borehole(self):
        rows = [
            self._row("ZK1", 0, 10, layer_no="1"),
            self._row("ZK1", 0, 10, layer_no="1", lithology="泥岩"),
            self._row("ZK2", 20, 11, layer_no="1"),
        ]

        with self.assertRaisesRegex(ValueError, "层号.*重复"):
            parse_section(rows)

    def test_rejects_conflicting_borehole_metadata(self):
        conflicts = (
            self._row("ZK1", 1, 10, layer_no="2"),
            self._row("ZK1", 0, 11, layer_no="2"),
        )
        for conflicting_row in conflicts:
            with self.subTest(conflicting_row=conflicting_row):
                rows = [self._row("ZK1", 0, 10), conflicting_row]
                with self.assertRaisesRegex(ValueError, "与前文不一致"):
                    parse_section(rows)

    def test_render_section_does_not_mutate_input(self):
        holes = [
            {
                "name": "ZK1",
                "x": 0.0,
                "elev": 10.0,
                "layers": [("1", "砂岩", 1.0, "")],
            },
            {
                "name": "ZK2",
                "x": 20.0,
                "elev": 11.0,
                "layers": [("1", "泥岩", 1.5, "")],
            },
        ]
        before = copy.deepcopy(holes)

        figure = render_section(holes, ve=1.0)
        try:
            self.assertEqual(holes, before)
            self.assertNotIn("bounds", holes[0])
            self.assertNotIn("bottom", holes[0])
        finally:
            figure.clear()


if __name__ == "__main__":
    unittest.main()
