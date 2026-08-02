# SPDX-FileCopyrightText: Copyright 2026 Sudheer Kumar Nanduri, Ankan Karmakar, Venkata Santosh Kumar Delhi
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for arc.core.config.

Covers: Config merging, immutable freeze, tolerance lookup.
"""
import unittest

from arc.core.config import Config, get_tolerance


class TestConfig(unittest.TestCase):

    def test_merge_overrides_specified_fields(self):
        base = Config(project_name="base", rules_path="rules", output_path="out")
        merged = Config.merge(base, {"project_name": "override", "output_path": "out2"})
        self.assertEqual(merged.project_name, "override")
        self.assertEqual(merged.rules_path, "rules")       # untouched
        self.assertEqual(merged.output_path, "out2")

    def test_merge_ignores_unknown_keys(self):
        base = Config()
        merged = Config.merge(base, {"totally_unknown": "value"})
        self.assertFalse(hasattr(merged, "totally_unknown"))

    def test_freeze_prevents_mutation(self):
        c = Config()
        c.freeze()
        with self.assertRaises(AttributeError):
            c.project_name = "mutated"


class TestTolerances(unittest.TestCase):

    def test_known_discipline_pair(self):
        tol = get_tolerance("architectural", "structural")
        self.assertEqual(tol, 0.05)

    def test_unknown_disciplines_return_default(self):
        tol = get_tolerance("unknown_a", "unknown_b")
        self.assertEqual(tol, 0.01)


if __name__ == "__main__":
    unittest.main()
