"""Regression tests for declarative styles and desktop save safety."""

import json
import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path


os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from strat import lithology
from ui.designer import save_style_entry
from ui.state import ChartState, ValidationError
from ui.window import MainWindow


class PatternValidationTests(unittest.TestCase):
    def test_rejects_non_finite_or_non_positive_pattern_dimensions(self):
        invalid = (
            {"type": "lines", "spacing": 0},
            {"type": "markers", "spacing": -1, "size": 2},
            {"type": "wave", "spacing": 1, "wavelength": float("nan")},
            {"type": "shape", "shape": "椭圆", "spacing": 1,
             "w": float("inf"), "h": 1},
            {"type": "rows", "spacing": 1, "heights": [-1],
             "rows": ["横线"]},
        )
        for spec in invalid:
            with self.subTest(spec=spec):
                with self.assertRaisesRegex(ValueError, "有限|大于 0|在 0"):
                    lithology.normalize_spec(spec)

    def test_rejects_excessively_complex_pattern(self):
        spec = [{"type": "lines", "spacing": 1}] * 501
        with self.assertRaisesRegex(ValueError, "不能超过 500"):
            lithology.normalize_spec(spec)

    def test_style_load_is_transactional(self):
        pattern_name = "事务测试花纹_dont_commit"
        lith_name = "事务测试岩性_dont_commit"
        self.assertNotIn(pattern_name, lithology.PATTERNS)
        self.assertNotIn(lith_name, lithology.LITHOLOGY)
        style = {
            "patterns": {
                pattern_name: {"type": "lines", "spacing": 1},
            },
            "lithology": {
                lith_name: {"pattern": "不存在的花纹_dont_commit"},
            },
        }
        with tempfile.TemporaryDirectory(prefix="strat-style-test-") as tmp:
            path = Path(tmp) / "style.json"
            path.write_text(json.dumps(style), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未定义的花纹"):
                lithology.load_style(path)

        self.assertNotIn(pattern_name, lithology.PATTERNS)
        self.assertNotIn(lith_name, lithology.LITHOLOGY)


class StyleFileSafetyTests(unittest.TestCase):
    def test_malformed_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="strat-style-file-") as tmp:
            path = Path(tmp) / "strat_style.json"
            original = "{ this is not valid json"
            path.write_text(original, encoding="utf-8")

            with self.assertRaises((ValueError, json.JSONDecodeError)):
                save_style_entry(path, "新花纹",
                                 {"type": "lines", "spacing": 1})

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_existing_file_is_backed_up_before_atomic_replace(self):
        with tempfile.TemporaryDirectory(prefix="strat-style-file-") as tmp:
            path = Path(tmp) / "strat_style.json"
            original = {"patterns": {"旧花纹": ["横线"]}}
            path.write_text(json.dumps(original, ensure_ascii=False),
                            encoding="utf-8")

            save_style_entry(path, "新花纹",
                             {"type": "lines", "spacing": 1})

            saved = json.loads(path.read_text(encoding="utf-8"))
            backup = json.loads(
                Path(str(path) + ".bak").read_text(encoding="utf-8"))
            self.assertIn("新花纹", saved["patterns"])
            self.assertEqual(backup, original)


class DesktopRevisionTests(unittest.TestCase):
    def test_pattern_row_height_is_shared_by_both_chart_kinds(self):
        interpreter = tk.Tcl()
        state = ChartState(interpreter, on_change=lambda: None)
        state.data = [{"lith": "砂岩", "thick": 1.0}]

        state.kind = "column"
        self.assertEqual(
            state.render_kwargs()["pattern_row_height_mm"], 2.5)

        state.pattern_row_height_mm.set("3.75")
        self.assertEqual(
            state.render_kwargs()["pattern_row_height_mm"], 3.75)

        state.kind = "section"
        self.assertEqual(
            state.render_kwargs()["pattern_row_height_mm"], 3.75)

    def test_pattern_row_height_does_not_leak_between_states(self):
        interpreter = tk.Tcl()
        first = ChartState(interpreter, on_change=lambda: None)
        second = ChartState(interpreter, on_change=lambda: None)
        first.kind = second.kind = "section"
        first.pattern_row_height_mm.set("1.25")
        second.pattern_row_height_mm.set("8.5")

        self.assertEqual(
            first.render_kwargs()["pattern_row_height_mm"], 1.25)
        self.assertEqual(
            second.render_kwargs()["pattern_row_height_mm"], 8.5)

        first.pattern_row_height_mm.set("4")
        self.assertEqual(
            first.render_kwargs()["pattern_row_height_mm"], 4.0)
        self.assertEqual(
            second.render_kwargs()["pattern_row_height_mm"], 8.5)

    def test_pattern_row_height_rejects_non_finite_and_out_of_range(self):
        interpreter = tk.Tcl()
        state = ChartState(interpreter, on_change=lambda: None)
        state.kind = "section"

        for value in ("", "abc", "nan", "inf", "-inf", "0.99", "10.01"):
            with self.subTest(value=value):
                state.pattern_row_height_mm.set(value)
                with self.assertRaises(ValidationError) as caught:
                    state.render_kwargs()
                self.assertEqual(caught.exception.field,
                                 "pattern_row_height_mm")

        for value in ("1", "10"):
            with self.subTest(value=value):
                state.pattern_row_height_mm.set(value)
                self.assertEqual(
                    state.render_kwargs()["pattern_row_height_mm"],
                    float(value),
                )

    def test_desktop_can_hide_all_stratigraphic_categories(self):
        interpreter = tk.Tcl()
        state = ChartState(interpreter, on_change=lambda: None)
        state.kind = "column"
        state.data = [{"lith": "砂岩", "thick": 1.0}]
        for variable in state.strata.values():
            variable.set(False)

        self.assertEqual(state.render_kwargs()["strata"], set())

    def test_dirty_chart_cannot_export_previous_figure(self):
        class View:
            @staticmethod
            def has_figure():
                return True

        class Chart:
            view = View()

        class Window:
            _input_revision = 4
            _rendered_revision = 4
            _render_valid = True
            chart = Chart()

            @staticmethod
            def _sync_export_state():
                pass

        window = Window()
        self.assertTrue(MainWindow._chart_exportable(window))
        MainWindow._mark_chart_dirty(window)
        self.assertFalse(MainWindow._chart_exportable(window))

    def test_pattern_change_rerenders_sections_too(self):
        events = []

        class Library:
            @staticmethod
            def invalidate():
                events.append("library-invalidated")

        class State:
            kind = "section"

        class Window:
            library = Library()
            state_ = State()
            _mode = "chart"

            @staticmethod
            def _mark_chart_dirty():
                events.append("chart-dirty")

            @staticmethod
            def rerender():
                events.append("chart-rendered")

            @staticmethod
            def status(_message):
                events.append("status")

        MainWindow.pattern_registered(Window(), "测试花纹", "砂岩")

        self.assertEqual(events[:3], [
            "library-invalidated", "chart-dirty", "chart-rendered"])


if __name__ == "__main__":
    unittest.main()
