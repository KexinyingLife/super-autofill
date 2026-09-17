# -*- coding: utf-8 -*-
"""Tests for form_builder XML surgery and ai_assist validation pipeline."""
import json
import os
import tempfile
import unittest
from pathlib import Path

# Add parent dir to path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import form_builder as fb
from schema import default_cells, LIB
import ai_assist


class TestXMLSurgery(unittest.TestCase):
    """Verify that XML-level editing preserves the xlsx structure."""

    def _make_workbook(self, tmp_dir: str, cat: str = "") -> Path:
        path = Path(tmp_dir) / "test.xlsx"
        row = default_cells(1, "ID-001", "Test Item", cat)
        fb.make_workbook([row], path, LIB)
        return path

    def test_generate_and_read_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_workbook(td)
            rows = fb.load_sheets(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["uid"], "ID-001")
            self.assertEqual(rows[0]["name"], "Test Item")
            self.assertEqual(rows[0]["f1"], "3")
            self.assertFalse(rows[0]["flag1"])

    def test_update_answers_preserves_structure(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_workbook(td)
            changed = fb.update_answers(path, "ID-001", {
                "f1": "5", "flag1": True, "recNote": "This is a test note",
            })
            self.assertEqual(changed, 1)
            rows = fb.load_sheets(path)
            self.assertEqual(rows[0]["f1"], "5")
            self.assertTrue(rows[0]["flag1"])
            self.assertEqual(rows[0]["recNote"], "This is a test note")
            # Unmodified fields preserved
            self.assertEqual(rows[0]["f2"], "3")
            self.assertFalse(rows[0]["flag2"])

    def test_update_nonexistent_id_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_workbook(td)
            changed = fb.update_answers(path, "NOPE", {"f1": "5"})
            self.assertEqual(changed, 0)

    def test_status_update_preserves_checkboxes(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._make_workbook(td)
            # Set a checkbox first
            fb.update_answers(path, "ID-001", {"flag1": True})
            # Then update status
            fb.update_statuses(path, {"ID-001": ("已提交", "2026-01-01 12:00:00")})
            rows = fb.load_sheets(path)
            self.assertTrue(rows[0]["flag1"])
            self.assertEqual(rows[0]["status"], "已提交")
            self.assertEqual(rows[0]["ts"], "2026-01-01 12:00:00")

    def test_multiple_items(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "multi.xlsx"
            rows = [
                default_cells(1, "A1", "Item A", ""),  # no category → f1=3
                default_cells(2, "B2", "Item B", ""),
                default_cells(3, "C3", "Item C", ""),
            ]
            fb.make_workbook(rows, path, LIB)
            fb.update_answers(path, "B2", {"f2": "5"})
            loaded = fb.load_sheets(path)
            self.assertEqual(len(loaded), 3)
            self.assertEqual(loaded[0]["f1"], "3")
            self.assertEqual(loaded[1]["f2"], "5")
            self.assertEqual(loaded[2]["f3"], "3")


class TestAIDraftValidation(unittest.TestCase):

    def test_rejects_unknown_key(self):
        raw = {
            "facts": [], "notes": [],
            "proposals": {"evil_field": {"value": "x", "evidence": "test"}},
        }
        draft = ai_assist.validate_draft(raw)
        self.assertEqual(draft["proposals"], {})
        self.assertIn("not in whitelist", draft["rejected"][0]["reason"])

    def test_rejects_invalid_option(self):
        raw = {"facts": [], "notes": [], "proposals": {
            "f1": {"value": "99", "evidence": "test"},
        }}
        draft = ai_assist.validate_draft(raw)
        self.assertEqual(draft["proposals"], {})

    def test_rejects_empty_evidence(self):
        raw = {"facts": [], "notes": [], "proposals": {
            "f1": {"value": "4", "evidence": ""},
        }}
        draft = ai_assist.validate_draft(raw)
        self.assertEqual(draft["proposals"], {})

    def test_dimension_mismatch_rejected(self):
        raw = {"facts": [], "notes": [], "proposals": {
            "f1": {"value": "1", "evidence": "loading is very slow"},
        }}
        draft = ai_assist.validate_draft(raw, "loading is very slow")
        self.assertEqual(draft["proposals"], {})

    def test_valid_proposal_accepted(self):
        raw = {"facts": [], "notes": [], "proposals": {
            "f3": {"value": "2", "evidence": "loading is slow", "confidence": "high"},
        }}
        draft = ai_assist.validate_draft(raw, "loading is slow")
        self.assertIn("f3", draft["proposals"])
        self.assertEqual(draft["proposals"]["f3"]["value"], "2")

    def test_checkbox_string_normalized(self):
        raw = {"facts": [], "notes": [], "proposals": {
            "flag1": {"value": "是", "evidence": "yes it has"},
        }}
        draft = ai_assist.validate_draft(raw, "yes it has")
        self.assertIs(draft["proposals"]["flag1"]["value"], True)

    def test_mock_output_validates(self):
        draft = ai_assist.validate_draft(ai_assist.mock_response())
        self.assertIn("f3", draft["proposals"])
        self.assertEqual(draft["proposals"]["rec"]["value"], "不愿意")

    def test_resolve_full_answers_merges_defaults(self):
        item = {"uid": "X1", "name": "Test", "cat": "类型A", "f1": "4"}
        draft = ai_assist.validate_draft({
            "facts": [], "notes": [], "proposals": {
                "f3": {"value": "2", "evidence": "slow", "confidence": "high"},
            },
        })
        ai_assist.resolve_full_answers(draft, item, "It's slow")
        ans = draft["resolved_answers"]
        self.assertEqual(ans["f1"], "4")  # from item preset
        self.assertEqual(ans["f3"], "2")  # from LLM
        self.assertEqual(ans["total"], "5")  # default
        self.assertTrue(ans["w1"])  # default

    def test_writeback_to_excel(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "wb_test.xlsx"
            row = default_cells(1, "WB-001", "Writeback Test", "")
            fb.make_workbook([row], path, LIB)
            item = {"uid": "WB-001", "name": "Writeback Test"}
            draft = ai_assist.validate_draft({
                "facts": [], "notes": [], "proposals": {
                    "f1": {"value": "5", "evidence": "功能很完整，feature set is complete"},
                    "rec": {"value": "愿意", "evidence": "would recommend"},
                },
            })
            ai_assist.resolve_full_answers(draft, item, "功能完整，excellent experience")
            changed = fb.update_answers(path, "WB-001", draft["resolved_answers"])
            self.assertEqual(changed, 1)
            loaded = fb.load_sheets(path)
            self.assertEqual(loaded[0]["f1"], "5")
            self.assertEqual(loaded[0]["rec"], "愿意")


if __name__ == "__main__":
    unittest.main()
