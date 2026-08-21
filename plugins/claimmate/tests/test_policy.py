from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PLUGIN / "scripts"))

from claimmate_core.base import save_finance_feedback  # noqa: E402
from claimmate_core.intake import discover_inputs  # noqa: E402
from claimmate_core.policy import (  # noqa: E402
    catalog_from_workbook,
    ensure_requirements_workbook,
    evaluate_expense_requirements,
    load_requirement_catalog,
    requirements_template_path,
)
from claimmate_core.requirements import evaluate_finance_feedback  # noqa: E402


class ReimbursementRequirementsTest(unittest.TestCase):
    def test_bundled_workbook_is_a_valid_single_schema(self) -> None:
        catalog = catalog_from_workbook(requirements_template_path())
        self.assertEqual(len(catalog["expense_types"]), 11)
        self.assertEqual(len(catalog["rules"]), 23)
        self.assertTrue(all(item["required_material"] for item in catalog["rules"]))
        rail_rules = [
            item for item in catalog["rules"]
            if item["expense_type_key"] == "rail"
        ]
        self.assertEqual(rail_rules[0]["required_material"], "火车票")
        self.assertEqual(rail_rules[0]["alternatives"], ["电子发票"])
        taxi_type = next(
            item for item in catalog["expense_types"] if item["key"] == "taxi"
        )
        self.assertEqual(taxi_type["finance_requirements"][0]["text"], "网约车还需行程单")
        taxi_special = next(
            item for item in catalog["rules"]
            if item["expense_type_key"] == "taxi" and item["condition"] == "网约车"
        )
        self.assertEqual(taxi_special["required_material"], "行程单")

    def test_workspace_workbook_is_not_ingested_and_has_snapshot_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = ensure_requirements_workbook(root)
            first = load_requirement_catalog(root, persist_snapshot=True)
            self.assertNotIn(workbook, discover_inputs(root))
            workbook.write_bytes(b"not an xlsx")
            fallback = load_requirement_catalog(root)
            self.assertEqual(first["rules"], fallback["rules"])
            self.assertIn("workbook_validation_error", fallback["source"])

    def test_special_condition_and_alternative_are_schema_driven(self) -> None:
        catalog = {
            "expense_types": [
                {"key": "flight", "label": "机票", "aliases": [], "enabled": True}
            ],
            "rules": [
                {
                    "rule_id": "REQ-FLIGHT-001",
                    "expense_type_key": "flight",
                    "condition": "乘坐国际航班",
                    "required_material": "登机牌",
                    "alternatives": ["电子登机凭证"],
                    "blocking": True,
                    "enabled": True,
                }
            ],
        }
        state = {
            "documents": {"doc": {"material_type": "电子登机凭证"}},
        }
        expense = {
            "expense_type_key": "flight",
            "documents": ["doc"],
            "applicable_requirement_ids": ["REQ-FLIGHT-001"],
            "assessed_condition_rule_ids": ["REQ-FLIGHT-001"],
        }
        passed = evaluate_expense_requirements(state, "EXP-001", expense, catalog)
        self.assertTrue(passed["passed"])

        expense["applicable_requirement_ids"] = []
        expense["assessed_condition_rule_ids"] = []
        unassessed = evaluate_expense_requirements(state, "EXP-001", expense, catalog)
        self.assertFalse(unassessed["passed"])
        self.assertIn("特殊条件尚未判断", unassessed["gaps"][0])

    def test_finance_amendment_checks_model_material_type_not_text_substrings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".claimmate").mkdir()
            save_finance_feedback(root, {
                "version": 1,
                "next_feedback_number": 2,
                "entries": [{
                    "feedback_id": "FB-001",
                    "status": "active",
                    "scope": "global",
                    "required_evidence": ["行程单|行程信息"],
                    "text": "网约车需要行程单",
                }],
            })
            state = {
                "project_id": "PRJ-001",
                "case_name": "合肥",
                "documents": {
                    "doc": {
                        "material_type": "其他说明",
                        "original_name": "名称里写了行程单但模型没有识别为行程单.txt",
                    }
                },
                "expenses": {"EXP-001": {"documents": ["doc"]}},
            }
            blocked = evaluate_finance_feedback(root, state)
            self.assertFalse(blocked["passed"])
            state["documents"]["doc"]["material_type"] = "行程信息"
            passed = evaluate_finance_feedback(root, state)
            self.assertTrue(passed["passed"])


if __name__ == "__main__":
    unittest.main()
