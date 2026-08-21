"""GB/T 958—2015 目录、花纹规则和注册完整性测试。"""

import os
import tempfile
import unittest
from collections import Counter
from unittest import mock


# Matplotlib 必须在 strat 导入前使用可写缓存目录。
os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())

from strat import gb958
from strat import lithology as L
from strat.gb958_data import ENTRIES


EXPECTED_CATEGORY_COUNTS = {
    "基本花纹": 86,
    "沉积岩": 128,
    "松散堆积物": 34,
    "侵入岩": 104,
    "火山熔岩": 55,
    "火山碎屑岩": 288,
    "脉岩": 8,
    "变质岩": 144,
    "蚀变岩": 36,
    "构造岩": 14,
    "混合岩": 13,
}


class GB958IntegrityTests(unittest.TestCase):
    def test_catalog_has_complete_unique_inventory(self):
        self.assertEqual(len(ENTRIES), 910)
        self.assertEqual(len({code for code, _name in ENTRIES}), 910)

        nonbasic = [(code, name) for code, name in ENTRIES
                    if not code.startswith("RPBP")]
        self.assertEqual(len(nonbasic), 824)
        self.assertEqual(len({name for _code, name in nonbasic}), 824)

        counts = Counter(gb958._section(code)[0] for code, _name in ENTRIES)
        self.assertEqual(dict(counts), EXPECTED_CATEGORY_COUNTS)
        self.assertEqual(tuple(dict(gb958.catalog())), gb958.CATEGORIES)

    def test_corrected_amphibole_schist_name(self):
        entries = dict(ENTRIES)
        self.assertEqual(entries["RPMR011043"], "角闪片岩")
        self.assertEqual(entries["RPMR011044"], "斜长角闪片岩")

    def test_every_official_spec_is_valid(self):
        errors = []
        for code, name in ENTRIES:
            try:
                spec, _color = gb958.spec_for(code, name)
                if isinstance(spec, str):
                    if spec not in L.PATTERNS:
                        raise AssertionError(f"内置花纹 {spec!r} 不存在")
                else:
                    L.normalize_spec(spec, where=f"{code} {name}")
            except Exception as exc:  # 汇总便于一次修复所有回归。
                errors.append(f"{code} {name}: {exc}")
        self.assertEqual(errors, [], "\n" + "\n".join(errors))

    def test_all_nonbasic_names_are_registered(self):
        # 重复调用必须真正幂等，不得清空 GB_NAMES。
        names_before = set(gb958.GB_NAMES)
        self.assertEqual(gb958.register_all(), 0)
        self.assertEqual(gb958.GB_NAMES, names_before)

        missing = []
        broken_patterns = []
        for code, name in ENTRIES:
            if code.startswith("RPBP"):
                continue
            if name not in L.LITHOLOGY:
                missing.append((code, name))
                continue
            _color, pattern_name = L.LITHOLOGY[name]
            if pattern_name is not None and pattern_name not in L.PATTERNS:
                broken_patterns.append((code, name, pattern_name))
        self.assertEqual(missing, [])
        self.assertEqual(broken_patterns, [])

    def test_official_entry_cannot_use_generic_fallback(self):
        with mock.patch.object(gb958, "_spec_by_rules",
                               return_value=(None, None)):
            with self.assertRaisesRegex(ValueError, "未找到国标花纹"):
                gb958.spec_for("RPSE021001", "角砾岩")

        # 非目录扩展仍保留原有的通用兜底行为。
        spec, color = gb958.spec_for("CUSTOM000001", "未知扩展岩性")
        self.assertEqual(spec, [gb958.MK(".", 2.0, 1.6)])
        self.assertEqual(color, "#eceae2")

    def test_registration_errors_are_aggregated_and_transactional(self):
        fake_entries = [
            ("RPSE021991", "完整性测试甲"),
            ("RPSE021992", "完整性测试乙"),
        ]

        def broken_spec(_code, name):
            raise ValueError(f"{name}的测试规则失败")

        patterns_before = set(L.PATTERNS)
        lithologies_before = set(L.LITHOLOGY)
        gb_names_before = set(gb958.GB_NAMES)
        with mock.patch.object(gb958, "ENTRIES", fake_entries), \
                mock.patch.object(gb958, "spec_for", side_effect=broken_spec):
            with self.assertRaises(gb958.GB958RegistrationError) as caught:
                gb958.register_all()

        self.assertEqual(len(caught.exception.failures), 2)
        self.assertIn("RPSE021991", str(caught.exception))
        self.assertIn("RPSE021992", str(caught.exception))
        self.assertEqual(set(L.PATTERNS), patterns_before)
        self.assertEqual(set(L.LITHOLOGY), lithologies_before)
        self.assertEqual(gb958.GB_NAMES, gb_names_before)

    def test_catalog_labels_keep_full_name_and_standard_code(self):
        code = "RPMM037002"
        name = "玄武安山质熔结火山碎屑岩(未分)"
        figure = gb958.render_catalog_sheet("火山碎屑岩")
        try:
            labels = [text.get_text() for text in figure.axes[0].texts]
            matching = [label for label in labels if code in label]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].replace("\n", ""), name + code)
            self.assertNotIn("…", matching[0])
        finally:
            figure.clear()


if __name__ == "__main__":
    unittest.main()
