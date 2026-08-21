"""Tests for the UI-independent document service facade."""

import os
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from strat.service import document_metadata, load_any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = PROJECT_ROOT / "examples"


class DocumentServiceTests(unittest.TestCase):
    def test_column_example_is_detected_and_described(self):
        kind, data = load_any(EXAMPLES / "column_demo.xlsx")
        info = document_metadata(kind, data)

        self.assertEqual(kind, "column")
        self.assertEqual(info["kind"], "column")
        self.assertEqual(info["layer_count"], 11)
        self.assertIn("11 层", info["summary"])
        self.assertIn("砂岩", info["lithologies"])
        self.assertIn(
            {"key": "formation", "label": "组", "category": "litho"},
            info["unit_columns"],
        )

    def test_section_example_is_detected_and_described(self):
        kind, data = load_any(EXAMPLES / "section_demo.xlsx")
        info = document_metadata(kind, data)

        self.assertEqual(kind, "section")
        self.assertEqual(info["kind"], "section")
        self.assertEqual(info["hole_count"], 4)
        self.assertEqual(info["layer_count"], 19)
        self.assertIn("4 个钻孔", info["summary"])
        self.assertIn("填土", info["lithologies"])
        self.assertEqual(info["unit_columns"], [])

    def test_parse_error_does_not_echo_the_uploaded_row(self):
        secret = "TOP-SECRET-CELL-CONTENT"
        with tempfile.TemporaryDirectory(prefix="strat-service-test-") as tmp:
            path = Path(tmp) / "invalid.csv"
            path.write_text(
                "岩性,厚度,内部备注\n砂岩,,%s\n" % secret,
                encoding="utf-8",
            )

            with self.assertRaises(ValueError) as caught:
                load_any(path)

        message = str(caught.exception)
        self.assertIn("第 1 行", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("内部备注", message)
        self.assertNotIn("{", message)


if __name__ == "__main__":
    unittest.main()
