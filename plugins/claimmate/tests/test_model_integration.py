from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "skills" / "claimmate" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import claimmate  # noqa: E402


@unittest.skipUnless(
    os.environ.get("CLAIMMATE_RUN_MODEL_INTEGRATION") == "1",
    "set CLAIMMATE_RUN_MODEL_INTEGRATION=1 to call the authenticated Codex CLI",
)
class ClaimMateModelIntegrationTest(unittest.TestCase):
    def test_authenticated_codex_cli_returns_a_schema_valid_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic-visa-service-invoice.txt"
            source.write_text(
                "Austrian Visa Application Centre service fee invoice, amount EUR 120.00",
                encoding="utf-8",
            )
            config = claimmate.default_config()
            registry = claimmate.new_project_registry()
            project = claimmate.create_project(registry, "2026-06_奥地利")
            item = claimmate.analyze(source, config)
            decisions, report = claimmate.run_model_decisions(
                root, [item], registry, config
            )
            self.assertEqual(report["status"], "used", report)
            self.assertIn(item["sha256"], decisions)
            decision = decisions[item["sha256"]]
            self.assertEqual(decision["project_id"], project["project_id"])
            self.assertEqual(decision["role"], "invoice")
            self.assertTrue(decision["expense_label"])
            self.assertEqual(decision["amount"], "120.00")
            self.assertIsInstance(decision["confidence"], float)


if __name__ == "__main__":
    unittest.main()
