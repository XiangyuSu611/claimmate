from __future__ import annotations

import csv
import hashlib
import json
import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from html import unescape
from pathlib import Path
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "skills" / "claimmate" / "scripts" / "claimmate.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"
sys.path.insert(0, str(PLUGIN / "scripts"))

from claimmate_core import documents as documents_core  # noqa: E402
from claimmate_core import intake as intake_core  # noqa: E402
from claimmate_core import model as model_core  # noqa: E402
from claimmate_core import policy as policy_core  # noqa: E402


def load_claimmate_module():
    spec = importlib.util.spec_from_file_location("claimmate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ClaimMateWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = (Path(self.temporary.name) / "真实报销测试").resolve()
        self.case_name = "2026-08-18至08-20_南京"
        self.root.mkdir()
        if os.name == "nt":
            self.fake_codex = Path(self.temporary.name) / "fake-codex.cmd"
            self.fake_codex.write_text(
                f'@"{sys.executable}" "{FAKE_CODEX}" %*\n', encoding="utf-8"
            )
        else:
            self.fake_codex = Path(self.temporary.name) / "fake-codex"
            self.fake_codex.write_text(
                "#!/bin/sh\n"
                f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_CODEX))} \"$@\"\n",
                encoding="utf-8",
            )
            self.fake_codex.chmod(0o755)
        self.files = {
            "海底捞_268元_发票.txt": "dining invoice unique",
            "海底捞_268元_付款记录.txt": "dining payment unique",
            "北京酒店_1260元_发票.txt": "hotel invoice unique",
            "北京酒店_1260元_付款记录.txt": "hotel payment unique",
            "ICRA会议注册费_4200元_发票.txt": "registration invoice unique",
            "项目说明.txt": "unknown document unique",
        }
        for name, contents in self.files.items():
            (self.root / name).write_text(contents, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(self, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "init" and "--no-service" not in arguments:
            arguments = (*arguments, "--no-service")
        if arguments and arguments[0] == "init" and "--user-name" not in arguments:
            arguments = (*arguments, "--user-name", "测试用户")
        if arguments and arguments[0] == "init" and "--email-choice" not in arguments:
            arguments = (*arguments, "--email-choice", "skip")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["CLAIMMATE_CODEX_COMMAND"] = str(self.fake_codex)
        environment.pop("CLAIMMATE_DISABLE_MODEL", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if (
            arguments
            and arguments[0] == "init"
            and expected == 0
            and "--dry-run" not in arguments
        ):
            confirmed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "requirements-confirm",
                    str(arguments[1]),
                    "--confirmed",
                    "--no-service",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            self.assertEqual(confirmed.returncode, 0, confirmed.stdout + confirmed.stderr)
            return subprocess.CompletedProcess(
                result.args,
                0,
                result.stdout + confirmed.stdout,
                result.stderr + confirmed.stderr,
            )
        return result

    def run_raw_cli(self, *arguments: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["CLAIMMATE_CODEX_COMMAND"] = str(self.fake_codex)
        environment.pop("CLAIMMATE_DISABLE_MODEL", None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def state(self) -> dict:
        return json.loads((self.root / ".claimmate" / "state.json").read_text(encoding="utf-8"))

    def registry(self) -> dict:
        return json.loads((self.root / ".claimmate" / "projects.json").read_text(encoding="utf-8"))

    def init(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("init", str(self.root), "--case-name", self.case_name, *extra)

    def test_first_initialization_requires_a_user_name(self) -> None:
        unnamed_root = Path(self.temporary.name) / "未登记姓名"
        unnamed_root.mkdir()
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "init", str(unnamed_root), "--no-service"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("首次初始化需要使用者姓名", result.stderr)
        self.assertFalse((unnamed_root / ".claimmate" / "projects.json").exists())

    def test_first_configuration_waits_for_email_choice_and_scheme_confirmation(self) -> None:
        setup_root = Path(self.temporary.name) / "等待确认配置"
        setup_root.mkdir()
        source = setup_root / "北京酒店_680元_发票.txt"
        source.write_text("new hotel invoice", encoding="utf-8")

        missing_choice = self.run_raw_cli(
            "init",
            str(setup_root),
            "--user-name",
            "测试用户",
            "--case-name",
            self.case_name,
            "--no-service",
            expected=1,
        )
        self.assertIn("需要确认是否接入邮箱", missing_choice.stderr)
        self.assertIn("自动下载并处理发票、付款记录等报销附件", missing_choice.stderr)
        self.assertFalse((setup_root / ".claimmate" / "projects.json").exists())

        initialized = self.run_raw_cli(
            "init",
            str(setup_root),
            "--user-name",
            "测试用户",
            "--email-choice",
            "skip",
            "--case-name",
            self.case_name,
            "--no-service",
        )
        self.assertIn("首次配置尚未完成", initialized.stdout)
        self.assertIn("| 费用类型 | 必须材料 | 财务其他要求 |", initialized.stdout)
        self.assertIn("Scheme 确认前不会识别、移动或重命名", initialized.stdout)
        self.assertTrue(source.is_file())
        self.assertFalse((setup_root / "开始使用 ClaimMate.md").exists())

        blocked = self.run_raw_cli("check", str(setup_root), expected=1)
        self.assertIn("报销要求.xlsx 尚未确认", blocked.stderr)
        self.assertTrue(source.is_file())

        preview_change = self.run_raw_cli(
            "requirements-change",
            str(setup_root),
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--preview",
        )
        self.assertIn("发票、付款记录 → 发票、付款记录、参会名单", preview_change.stdout)
        applied_change = self.run_raw_cli(
            "requirements-change",
            str(setup_root),
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--confirmed",
        )
        self.assertIn("首次配置仍未完成", applied_change.stdout)

        shown = self.run_raw_cli("requirements-show", str(setup_root))
        self.assertIn("当前不会自动处理材料", shown.stdout)
        self.assertIn("餐费：必须材料：发票、付款记录 → 发票、付款记录、参会名单", shown.stdout)
        rejected = self.run_raw_cli("requirements-confirm", str(setup_root), expected=1)
        self.assertIn("必须先向用户展示当前 Scheme", rejected.stderr)

        confirmed = self.run_raw_cli(
            "requirements-confirm",
            str(setup_root),
            "--confirmed",
            "--no-service",
        )
        self.assertIn("首次配置已完成", confirmed.stdout)
        self.assertIn("首次使用只需要记住三个阶段", confirmed.stdout)
        config = json.loads((setup_root / ".claimmate" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["setup"]["email_intake_choice"], "skip")
        self.assertEqual(
            config["setup"]["requirements_sha256"],
            hashlib.sha256((setup_root / "报销要求.xlsx").read_bytes()).hexdigest(),
        )
        self.assertTrue(config["setup"]["completed_at"])
        self.assertTrue((setup_root / "开始使用 ClaimMate.md").is_file())
        self.assertFalse(source.exists())

    def test_manual_scheme_edit_requires_a_new_confirmation(self) -> None:
        self.init()
        workbook = self.root / "报销要求.xlsx"
        change = policy_core.plan_requirement_workbook_change(
            workbook,
            "餐费",
            ["参会名单"],
            [],
            "append",
        )
        policy_core.apply_requirement_workbook_change(workbook, change)
        source = self.root / "待处理" / "新增餐费_99元_发票.txt"
        source.write_text("dining invoice changed scheme", encoding="utf-8")

        blocked = self.run_cli("check", str(self.root), expected=1)
        self.assertIn("确认后又发生了修改", blocked.stderr)
        self.assertTrue(source.is_file())
        shown = self.run_cli("requirements-show", str(self.root))
        self.assertIn("餐费：必须材料：发票、付款记录 → 发票、付款记录、参会名单", shown.stdout)

        confirmed = self.run_cli(
            "requirements-confirm",
            str(self.root),
            "--confirmed",
            "--no-service",
        )
        self.assertIn("本次确认包含 1 项 Scheme 变化", confirmed.stdout)
        self.assertFalse(source.exists())

    def test_complete_local_workflow(self) -> None:
        initialized = self.init()
        self.assertIn("处理完成", initialized.stdout)
        state = self.state()
        self.assertEqual(state["case_name"], self.case_name)
        config = json.loads(
            (self.root / ".claimmate" / "config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["claimant"]["name"], "测试用户")
        self.assertEqual(len(state["expenses"]), 3)
        statuses = {
            expense_id: {
                state["documents"][digest]["role"]
                for digest in expense["documents"]
            }
            for expense_id, expense in state["expenses"].items()
        }
        self.assertEqual(sum(roles == {"invoice", "payment"} for roles in statuses.values()), 2)
        self.assertEqual(sum(roles == {"invoice"} for roles in statuses.values()), 1)
        self.assertEqual(
            sum(document["status"] == "review" for document in state["documents"].values()),
            1,
        )

        self.assertTrue((self.root / "待处理").is_dir())
        self.assertTrue((self.root / "流程中").is_dir())
        self.assertTrue((self.root / "已结束").is_dir())
        self.assertTrue((self.root / "开始使用 ClaimMate.md").is_file())
        if os.name == "nt":
            import ctypes
            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(self.root / ".claimmate"))
            self.assertTrue(attributes & 0x02)
        case = self.root / "流程中" / self.case_name
        self.assertEqual(len(list(case.glob("EXP-*_餐费_268元_*.txt"))), 2)
        self.assertFalse(any(path.is_dir() for path in case.iterdir()))
        self.assertGreaterEqual(len(list((self.root / ".claimmate" / "originals").iterdir())), 6)

        (self.root / "ICRA会议注册费_4200元_付款记录.txt").write_text(
            "registration payment unique", encoding="utf-8"
        )
        self.run_cli("check", str(self.root))
        state = self.state()
        registration = next(
            expense for expense in state["expenses"].values() if expense["category"] == "registration"
        )
        self.assertEqual(
            {state["documents"][digest]["role"] for digest in registration["documents"]},
            {"invoice", "payment"},
        )

        duplicate_source = next(case.glob("EXP-*_餐费_268元_发票.txt"))
        (self.root / "重复海底捞发票.txt").write_bytes(duplicate_source.read_bytes())
        self.run_cli("check", str(self.root))
        state = self.state()
        self.assertEqual(len(state["duplicates"]), 1)
        self.assertTrue(any(case.glob("重复文件_*.txt")))

        self.run_cli("export", str(self.root))
        self.assertTrue((case / "报销明细表.csv").exists())
        self.assertTrue((case / "报销明细表.xlsx").exists())
        self.assertTrue((case / "缺失材料清单.md").exists())
        self.assertTrue((case / "处理报告.json").exists())

        blocked = self.run_cli("archive", str(self.root), expected=1)
        self.assertIn("尚有未解决材料", blocked.stderr)
        (case / ".DS_Store").write_text("metadata", encoding="utf-8")
        (case / "~$报销明细表.xlsx").write_text("office lock", encoding="utf-8")
        self.run_cli("archive", str(self.root), "--force")
        self.assertEqual(self.state()["status"], "archived")
        finished_case = self.root / "已结束" / self.case_name
        self.assertTrue(finished_case.is_dir())
        self.assertFalse(case.exists())
        archive_path = finished_case / "2026-08-18至08-20-南京-测试用户-报销文件.zip"
        self.assertTrue(archive_path.is_file())
        with zipfile.ZipFile(archive_path) as archive:
            self.assertNotIn(".DS_Store", archive.namelist())
            self.assertNotIn("~$报销明细表.xlsx", archive.namelist())

    def test_reimport_recovers_a_missing_registered_file_instead_of_marking_duplicate(self) -> None:
        self.init()
        state = self.state()
        document = next(
            item
            for item in state["documents"].values()
            if item.get("status") == "organized" and item.get("role") == "invoice"
        )
        canonical = self.root.joinpath(*Path(document["current_path"]).parts)
        replay = self.root / "待处理" / f"重新放入_{document['original_name']}"
        canonical.replace(replay)

        checked = self.run_cli("check", str(self.root))
        state = self.state()

        self.assertIn("已恢复 1 个原登记路径缺失的材料", checked.stdout)
        self.assertTrue(canonical.exists())
        self.assertFalse(state["duplicates"])
        self.assertFalse(any((self.root / "流程中" / self.case_name).glob("重复文件_*")))

    def test_first_use_onboarding_covers_the_complete_workflow(self) -> None:
        initialized = self.init()
        for expected in (
            "首次使用只需要记住三个阶段",
            "1. 新建项目",
            "2. 更新材料",
            "3. 交付财务",
            f"材料可以直接发在对话中、放进“{self.root / '待处理'}”，或通过邮箱获取",
            "不同项目的材料可以交叉发送",
            "“报销要求.xlsx”只维护“费用类型、必须材料、财务其他要求”三列",
            "一张发票可以对应多笔付款记录",
            "按付款合计核验",
            "确认适用范围和修改内容后",
            "报销要交给财务了",
            "不会未经确认替你提交",
        ):
            self.assertIn(expected, initialized.stdout)
        console_positions = [
            initialized.stdout.index("1. 新建项目"),
            initialized.stdout.index("2. 更新材料"),
            initialized.stdout.index("3. 交付财务"),
        ]
        self.assertEqual(console_positions, sorted(console_positions))

        guide = (self.root / "开始使用 ClaimMate.md").read_text(encoding="utf-8")
        self.assertIn(str(self.root / "待处理"), guide)
        for expected in (
            "## 1. 新建项目",
            "## 2. 更新材料",
            "## 3. 交付财务",
            "三种方式可以混合使用",
            "多个项目交叉更新",
            "更新财务反馈",
            "火车票或电子发票、付款记录",
            "一张汇总发票可以对应多笔付款记录",
            "付款合计与发票价税合计核验",
            "付款差额",
            "时间-地点-报销人-报销文件.zip",
        ):
            self.assertIn(expected, guide)
        top_level_sections = [line for line in guide.splitlines() if line.startswith("## ")]
        self.assertEqual(
            top_level_sections,
            ["## 1. 新建项目", "## 2. 更新材料", "## 3. 交付财务"],
        )

    def test_dry_run_and_undo(self) -> None:
        preview = self.init("--dry-run")
        self.assertIn("预览完成", preview.stdout)
        self.assertFalse((self.root / ".claimmate").exists())

        self.init()
        (self.root / "滴滴_35元_发票.txt").write_text("taxi invoice", encoding="utf-8")
        self.run_cli("check", str(self.root))
        self.assertTrue(list((self.root / "流程中" / self.case_name).glob("EXP-*_出租车票_35元_发票.txt")))
        self.run_cli("undo", str(self.root))
        self.assertTrue((self.root / "滴滴_35元_发票.txt").exists())
        self.assertFalse(any(
            expense.get("amount") == "35.00" for expense in self.state()["expenses"].values()
        ))

    def test_non_travel_material_is_held_outside_travel_projects(self) -> None:
        self.init()
        source = self.root / "待处理" / "办公设备_860元_发票.txt"
        source.write_text("equipment invoice", encoding="utf-8")
        checked = self.run_cli("check", str(self.root))
        self.assertIn("非出差报销材料已安全保留", checked.stdout)
        registry = self.registry()
        record = next(iter(registry["unassigned_documents"].values()))
        self.assertEqual(record["status"], "out_of_scope")
        self.assertFalse(record["in_scope"])
        self.assertTrue(record["current_path"].startswith("待处理/待归属/"))
        self.assertFalse(any(
            expense.get("merchant") == "办公设备"
            for project in registry["projects"].values()
            for expense in project.get("expenses", {}).values()
        ))
        config = json.loads(
            (self.root / ".claimmate" / "config.json").read_text(encoding="utf-8")
        )
        for removed in ("publication", "office", "equipment", "software", "postage", "labor"):
            self.assertNotIn(removed, config["categories"])

    def test_legacy_layout_is_migrated(self) -> None:
        self.init()
        state_path = self.root / ".claimmate" / "state.json"
        config_path = self.root / ".claimmate" / "config.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))

        case = self.root / "流程中" / self.case_name
        for document in state["documents"].values():
            source = self.root.joinpath(*Path(document["current_path"]).parts)
            folder = (
                "待确认"
                if document.get("status") == "review"
                else config["categories"][document["category"]]["folder"]
            )
            destination = case / folder / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            document["current_path"] = destination.relative_to(self.root).as_posix()
        state["layout_version"] = 4
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        projects_path = self.root / ".claimmate" / "projects.json"
        registry = json.loads(projects_path.read_text(encoding="utf-8"))
        registry["projects"][state["project_id"]] = state
        projects_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        result = self.run_cli("status", str(self.root))
        self.assertIn("出差报销", result.stdout)
        self.assertTrue((self.root / "待处理").is_dir())
        self.assertFalse(any(path.is_dir() for path in case.iterdir()))
        self.assertTrue(list(case.glob("EXP-*_餐费_268元_*.txt")))
        self.assertEqual(self.state()["layout_version"], 5)

    def test_case_can_be_renamed_without_manual_moves(self) -> None:
        self.init()
        self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "本项目餐费需要附参会名单。",
            "--project",
            self.case_name,
        )
        renamed = "2026-08-18至08-21_苏州"
        self.run_cli("rename", str(self.root), "--project", self.case_name, "--new-name", renamed)
        self.assertFalse((self.root / "流程中" / self.case_name).exists())
        self.assertTrue(list((self.root / "流程中" / renamed).glob("EXP-*_餐费_268元_*.txt")))
        state = self.state()
        self.assertEqual(state["case_name"], renamed)
        self.assertTrue(all(
            not document["current_path"].startswith(f"流程中/{self.case_name}/")
            for document in state["documents"].values()
        ))
        guide = (self.root / "开始使用 ClaimMate.md").read_text(encoding="utf-8")
        self.assertIn(renamed, guide)
        self.assertIn("报销要交给财务了", guide)
        feedback = json.loads(
            (self.root / ".claimmate" / "finance-feedback.json").read_text(encoding="utf-8")
        )["entries"][0]
        self.assertEqual(feedback["project_name"], renamed)

    def test_user_confirmed_expense_label_renames_the_complete_pair_and_is_undoable(self) -> None:
        self.init()
        state = self.state()
        expense_id = next(
            expense_id
            for expense_id, expense in state["expenses"].items()
            if expense.get("category") == "dining"
        )
        case = self.root / "流程中" / self.case_name

        updated = self.run_cli(
            "expense-label",
            str(self.root),
            expense_id,
            "--project",
            self.case_name,
            "--name",
            "工作餐费",
        )

        self.assertIn("已重命名 2 个文件", updated.stdout)
        self.assertEqual(self.state()["expenses"][expense_id]["label"], "工作餐费")
        self.assertEqual(len(list(case.glob(f"{expense_id}_工作餐费_268元_*.txt"))), 2)
        self.assertFalse(any(path.is_dir() for path in case.iterdir()))

        self.run_cli("undo", str(self.root))
        self.assertEqual(self.state()["expenses"][expense_id]["label"], "餐费")
        self.assertEqual(len(list(case.glob(f"{expense_id}_餐费_268元_*.txt"))), 2)

    def test_user_confirmed_expense_merge_reconciles_role_totals_and_is_undoable(self) -> None:
        self.init()
        state = self.state()
        target_id = next(
            expense_id
            for expense_id, expense in state["expenses"].items()
            if expense.get("category") == "dining"
        )
        source_id = next(
            expense_id
            for expense_id, expense in state["expenses"].items()
            if expense.get("category") == "accommodation"
        )
        case = self.root / "流程中" / self.case_name

        merged = self.run_cli(
            "expense-merge",
            str(self.root),
            "--project",
            self.case_name,
            "--target",
            target_id,
            "--source",
            source_id,
        )

        state = self.state()
        self.assertIn(f"{source_id} → {target_id}", merged.stdout)
        self.assertNotIn(source_id, state["expenses"])
        target = state["expenses"][target_id]
        self.assertEqual(len(target["documents"]), 4)
        self.assertNotIn("amount_conflict", target)
        self.assertEqual(target["invoice_total"], "1528.00")
        self.assertEqual(target["payment_total"], "1528.00")
        self.assertEqual(target["payment_difference"], "0.00")
        self.assertEqual(target["amount_reconciliation"]["status"], "matched")
        self.assertTrue(all(
            state["documents"][digest]["expense_id"] == target_id
            for digest in target["documents"]
        ))
        self.assertEqual(len(list(case.glob(f"{target_id}_餐费_*.txt"))), 4)
        self.assertFalse(list(case.glob(f"{source_id}_*.txt")))

        self.run_cli("undo", str(self.root))
        state = self.state()
        self.assertIn(source_id, state["expenses"])
        self.assertEqual(len(state["expenses"][target_id]["documents"]), 2)
        self.assertEqual(len(state["expenses"][source_id]["documents"]), 2)

    def test_one_invoice_can_match_multiple_payments_by_total(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "汇总机票_1000元_发票.txt").write_text("flight invoice", encoding="utf-8")
        (self.root / "汇总机票_600元_付款记录.txt").write_text("flight payment one", encoding="utf-8")
        (self.root / "汇总机票_400元_付款记录.txt").write_text("flight payment two", encoding="utf-8")

        self.init()
        state = self.state()
        self.assertEqual(len(state["expenses"]), 1)
        expense = next(iter(state["expenses"].values()))
        self.assertEqual(expense["invoice_total"], "1000.00")
        self.assertEqual(expense["payment_total"], "1000.00")
        self.assertEqual(expense["payment_difference"], "0.00")
        self.assertEqual(expense["amount_reconciliation"]["status"], "matched")
        self.assertNotIn("amount_conflict", expense)
        roles = [state["documents"][digest]["role"] for digest in expense["documents"]]
        self.assertEqual(roles.count("invoice"), 1)
        self.assertEqual(roles.count("payment"), 2)

        ready = self.run_cli("ready", str(self.root))
        self.assertIn("已生成报销明细表，可以交给财务", ready.stdout)
        csv_path = self.root / "流程中" / self.case_name / "报销明细表.csv"
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["金额（元）"], "1000.00")
        self.assertEqual(row["付款合计（元）"], "1000.00")
        self.assertEqual(row["付款差额（元）"], "0.00")

    def test_partial_payment_blocks_finance_readiness(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "汇总机票_1000元_发票.txt").write_text("flight invoice", encoding="utf-8")
        (self.root / "汇总机票_600元_付款记录.txt").write_text("flight payment", encoding="utf-8")

        self.init()
        ready = self.run_cli("ready", str(self.root))
        self.assertIn("付款合计少于发票价税合计", ready.stdout)
        self.assertIn("发票 1000元，付款合计 600元", ready.stdout)
        self.assertEqual(self.state()["status"], "collecting")

    def test_uncertain_file_is_resolved_from_later_clues(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "海底捞_268元_截图.txt").write_text("opaque attachment", encoding="utf-8")
        initialized = self.init()
        self.assertNotIn("缺付款记录", initialized.stdout)
        state = self.state()
        self.assertEqual(sum(item["status"] == "review" for item in state["documents"].values()), 1)

        (self.root / "待处理" / "海底捞_268元_发票.txt").write_text(
            "dining invoice", encoding="utf-8"
        )
        checked = self.run_cli("check", str(self.root))
        self.assertIn("根据新增线索补判 1 个", checked.stdout)
        state = self.state()
        self.assertEqual(sum(item["status"] == "review" for item in state["documents"].values()), 0)
        self.assertEqual(len(state["expenses"]), 1)
        expense = next(iter(state["expenses"].values()))
        roles = {state["documents"][digest]["role"] for digest in expense["documents"]}
        self.assertEqual(roles, {"invoice", "payment"})
        inferred = next(
            item for item in state["documents"].values()
            if item["original_name"] == "海底捞_268元_截图.txt"
        )
        self.assertEqual(inferred["role_confidence"], "model")
        self.assertGreaterEqual(len(inferred["inference_evidence"]), 2)

    def test_reference_number_and_date_can_resolve_an_opaque_file(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "未知材料.txt").write_text(
            "交易号：ABC1234567\n日期：2026-06-02", encoding="utf-8"
        )
        self.init()
        (self.root / "待处理" / "北京酒店_发票.txt").write_text(
            "住宿发票\n订单号：ABC1234567\n日期：2026-06-02", encoding="utf-8"
        )
        self.run_cli("check", str(self.root))
        state = self.state()
        inferred = next(
            item for item in state["documents"].values() if item["original_name"] == "未知材料.txt"
        )
        self.assertEqual(inferred["status"], "organized")
        self.assertEqual(inferred["role"], "payment")
        self.assertTrue(any("订单、交易或票据编号一致" in value for value in inferred["inference_evidence"]))
        self.assertTrue(any("日期一致" in value for value in inferred["inference_evidence"]))

    def test_local_image_ocr_feeds_raw_text_without_local_business_classification(self) -> None:
        claimmate = load_claimmate_module()
        image = self.root / "opaque.jpg"
        image.write_bytes(b"not-a-real-image")
        config = json.loads(
            (PLUGIN / "assets" / "config.example.json").read_text(encoding="utf-8")
        )
        ocr_text = (
            "账单详情 交易成功 -1,551.00 支付时间 2025-06-30 "
            "商品说明 机票订单付款{8250940481780}"
        )
        with mock.patch.object(documents_core, "extract_image_text", return_value=ocr_text):
            item = claimmate.analyze(image, config)
        self.assertIsNone(item["role"])
        self.assertIsNone(item["category"])
        self.assertIsNone(item["amount"])
        self.assertIsNone(item["merchant"])
        self.assertFalse(item["date_tokens"])
        self.assertFalse(item["reference_tokens"])
        self.assertIn("交易成功", item["routing_text"])
        self.assertIn("8250940481780", item["routing_text"])

    def test_model_runner_uses_structured_read_only_codex_exec(self) -> None:
        claimmate = load_claimmate_module()
        source = self.root / "签证中心_120欧元_材料.txt"
        source.write_text("visa application service charge", encoding="utf-8")
        config = claimmate.default_config()
        item = claimmate.analyze(source, config)
        registry = claimmate.new_project_registry()
        project = claimmate.create_project(registry, "2026-06_奥地利")
        response = {
            "decisions": [{
                "document_id": item["sha256"],
                "in_scope": True,
                "scope_reason": None,
                "project_id": project["project_id"],
                "role": "invoice",
                "category_key": "__new__",
                "category_label": "签证与领事服务费",
                "expense_label": "签证费",
                "expense_key": "NEW-001",
                "merchant": "奥地利签证中心",
                "amount": "120.00",
                "date_tokens": [],
                "reference_tokens": [],
                "confidence": 0.94,
                "needs_review": False,
                "evidence": ["材料明确写明签证申请服务费"],
            }]
        }
        captured: dict[str, object] = {}

        def fake_run(arguments, **kwargs):
            captured["arguments"] = arguments
            captured["kwargs"] = kwargs
            output = Path(arguments[arguments.index("--output-last-message") + 1])
            output.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with mock.patch.object(model_core, "find_codex_command", return_value="codex.exe"), mock.patch.object(
            claimmate.subprocess, "run", side_effect=fake_run
        ):
            decisions, report = claimmate.run_model_decisions(
                self.root, [item], registry, config
            )

        arguments = captured["arguments"]
        self.assertIn("--ephemeral", arguments)
        self.assertIn("--ignore-user-config", arguments)
        self.assertEqual(arguments[arguments.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", arguments)
        self.assertEqual(report["status"], "used")
        self.assertEqual(decisions[item["sha256"]]["role"], "invoice")
        self.assertIn("只处理与某次出差直接相关的报销", captured["kwargs"]["input"])

    def test_model_can_create_an_open_ended_travel_expense_category(self) -> None:
        claimmate = load_claimmate_module()
        config = claimmate.default_config()
        item = {
            "role": "payment",
            "category": "other",
            "merchant": "未识别商户",
            "amount": None,
            "date_tokens": [],
            "reference_tokens": [],
        }
        decision = {
            "in_scope": True,
            "scope_reason": None,
            "project_id": "PRJ-001",
            "role": "payment",
            "category_key": "__new__",
            "category_label": "境外旅行保险费",
            "expense_label": "旅行保险",
            "expense_key": "NEW-009",
            "merchant": "境外保险公司",
            "amount": "860.50",
            "date_tokens": ["2026-06-12"],
            "reference_tokens": ["ANIMAL-9988"],
            "confidence": 0.93,
            "needs_review": False,
            "evidence": ["付款用途为本次出差的境外旅行保险"],
            "_batch_id": "B001",
        }
        changed = claimmate.apply_model_decision(item, decision, config)
        self.assertTrue(changed)
        self.assertTrue(item["category"].startswith("model-"))
        self.assertEqual(config["categories"][item["category"]]["folder"], "境外旅行保险费")
        self.assertEqual(config["categories"][item["category"]]["scope"], "business-travel")
        self.assertEqual(item["role_confidence"], "model")
        self.assertEqual(item["amount"], claimmate.Decimal("860.50"))
        self.assertNotIn("model_guard_reason", item)

    def test_model_fields_override_local_business_rule_guesses(self) -> None:
        claimmate = load_claimmate_module()
        config = claimmate.default_config()
        item = {
            "role": "supporting",
            "category": "other",
            "merchant": "文件名猜测商户",
            "amount": claimmate.Decimal("1463.21"),
            "date_tokens": ["2025-07"],
            "reference_tokens": ["local-guess"],
        }
        decision = {
            "in_scope": True,
            "scope_reason": None,
            "project_id": "PRJ-001",
            "role": "invoice",
            "category_key": "transport",
            "category_label": "交通",
            "expense_label": "机票",
            "expense_key": "NEW-001",
            "merchant": "阿斯兰航空服务（上海）有限公司",
            "amount": "1551.00",
            "date_tokens": ["2025-07-13"],
            "reference_tokens": ["25317000001571521278"],
            "confidence": 0.90,
            "needs_review": True,
            "evidence": ["发票价税合计为1551.00元"],
            "_batch_id": "B001",
        }

        claimmate.apply_model_decision(item, decision, config)

        self.assertEqual(item["role"], "invoice")
        self.assertEqual(item["amount"], claimmate.Decimal("1551.00"))
        self.assertEqual(item["date_tokens"], ["2025-07-13"])
        self.assertEqual(item["reference_tokens"], ["25317000001571521278"])
        self.assertEqual(item["model_guard_reason"], "大模型标记为待确认")
        payload = claimmate.model_document_payload({
            "sha256": "abc",
            "original_name": "发票.pdf",
            "routing_text": "价税合计1551.00元",
            "amount": claimmate.Decimal("1463.21"),
        }, 30000, None)
        self.assertNotIn("local_parser_hints", payload)

    def test_model_failure_holds_material_in_review(self) -> None:
        claimmate = load_claimmate_module()
        for path in self.root.iterdir():
            path.unlink()
        config = claimmate.default_config()
        registry = claimmate.new_project_registry()
        project = claimmate.create_project(registry, self.case_name)
        claimmate.ensure_workspace(self.root, config, self.case_name)
        claimmate.prepare_workspace(self.root, project, config)
        claimmate.save_project_registry(self.root, registry)
        source = self.root / "海底捞_268元_发票.txt"
        source.write_text("dining invoice", encoding="utf-8")
        with mock.patch.object(
            intake_core,
            "run_model_decisions",
            return_value=({}, {"status": "failed", "processed": 0, "reason": "offline"}),
        ):
            result = claimmate.process_routed_inputs(
                self.root, registry, config, False, claimmate.copy.deepcopy(registry)
            )
        document = next(iter(registry["unassigned_documents"].values()))
        self.assertEqual(document["status"], "unassigned")
        self.assertEqual(document["decision_source"], "large-language-model")
        self.assertFalse(project["expenses"])
        self.assertEqual(result["model_status"], "failed")

    def test_ocr_cleanup_handles_windows_spacing_and_punctuation(self) -> None:
        claimmate = load_claimmate_module()
        raw = (
            "支 付 时 间 商 品 说 明 一 1，551．00 交 易 成 功 "
            "2025 一 06 一 30 机 票 订 单 付 款 { 82509404817801 一 2 } 9 元 红 包"
        )
        cleaned = claimmate.clean_ocr_text(raw)
        self.assertIn("支付时间", cleaned)
        self.assertIn("交易成功", cleaned)
        self.assertIn("1,551.00", cleaned)
        self.assertIn("2025-06-30", cleaned)
        self.assertIn("8250940481780", cleaned)
        self.assertFalse(hasattr(claimmate, "find_amount"))
        self.assertFalse(hasattr(claimmate, "find_date_tokens"))
        self.assertFalse(hasattr(claimmate, "find_reference_tokens"))

    def test_ready_surfaces_remaining_questions_and_missing_materials(self) -> None:
        initialized = self.init()
        self.assertNotIn("缺付款记录", initialized.stdout)
        ready = self.run_cli("ready", str(self.root))
        self.assertIn("仍不确定的文件", ready.stdout)
        self.assertIn("项目说明.txt", ready.stdout)
        self.assertIn("依据报销要求仍需补充", ready.stdout)
        self.assertIn("icra", ready.stdout.lower())
        self.assertIn("付款记录", ready.stdout)
        self.assertEqual(self.state()["status"], "collecting")

    def test_ready_marks_a_complete_claim_for_finance(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "海底捞_268元_发票.txt").write_text("invoice", encoding="utf-8")
        (self.root / "海底捞_268元_付款记录.txt").write_text("payment", encoding="utf-8")
        self.init()
        ready = self.run_cli("ready", str(self.root))
        self.assertIn("已生成报销明细表，可以交给财务", ready.stdout)
        self.assertEqual(self.state()["status"], "ready")
        case = self.root / "流程中" / self.case_name
        csv_path = case / "报销明细表.csv"
        xlsx_path = case / "报销明细表.xlsx"
        self.assertTrue(csv_path.exists())
        self.assertTrue(xlsx_path.exists())
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["收款人"], "测试用户")
        self.assertEqual(rows[0]["金额（元）"], "268.00")
        self.assertNotIn("/", rows[0]["材料文件"])
        self.assertNotIn("\\", rows[0]["材料文件"])
        with zipfile.ZipFile(xlsx_path) as workbook:
            sheet_xml = unescape(workbook.read("xl/worksheets/sheet1.xml").decode("utf-8"))
        self.assertIn("测试用户", sheet_xml)
        self.assertIn('r="C5"', sheet_xml)
        self.assertIn('<f>SUM(E5:E5)</f>', sheet_xml)

    def test_profile_name_can_be_updated_and_is_used_as_default_recipient(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "海底捞_268元_发票.txt").write_text("invoice", encoding="utf-8")
        (self.root / "海底捞_268元_付款记录.txt").write_text("payment", encoding="utf-8")
        self.init()
        updated = self.run_cli(
            "profile-set", str(self.root), "--user-name", "李明"
        )
        self.assertIn("已更新使用者姓名：李明", updated.stdout)
        self.run_cli("ready", str(self.root))
        csv_path = self.root / "流程中" / self.case_name / "报销明细表.csv"
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["收款人"], "李明")

    def test_ready_blocks_when_an_upgraded_workspace_has_no_user_name(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "海底捞_268元_发票.txt").write_text("invoice", encoding="utf-8")
        (self.root / "海底捞_268元_付款记录.txt").write_text("payment", encoding="utf-8")
        self.init()
        config_path = self.root / ".claimmate" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["claimant"] = {"name": ""}
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        ready = self.run_cli("ready", str(self.root), expected=1)
        self.assertIn("尚未确认使用者姓名", ready.stderr)
        self.assertEqual(self.state()["status"], "collecting")
        self.assertFalse((self.root / "流程中" / self.case_name / "报销明细表.xlsx").exists())

    def test_existing_workspace_migrates_setup_state_without_stopping_processing(self) -> None:
        self.init()
        config_path = self.root / ".claimmate" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config.pop("setup")
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        source = self.root / "待处理" / "迁移后酒店_77元_发票.txt"
        source.write_text("migration hotel invoice", encoding="utf-8")

        checked = self.run_cli("check", str(self.root))
        self.assertIn("处理完成", checked.stdout)
        migrated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            migrated["setup"]["requirements_confirmation_source"],
            "legacy-workspace-migration",
        )
        self.assertEqual(migrated["setup"]["email_intake_choice"], "skip")
        self.assertTrue(migrated["setup"]["completed_at"])

    def test_claimmate_can_apply_a_confident_document_review(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        filename = "临时材料_88元_截图.txt"
        (self.root / filename).write_text("opaque", encoding="utf-8")
        self.init()
        resolved = self.run_cli(
            "resolve",
            str(self.root),
            filename,
            "--role",
            "payment",
            "--category",
            "餐饮",
            "--expense-name",
            "餐费",
        )
        self.assertIn("ClaimMate 文档复核确认", json.dumps(self.state(), ensure_ascii=False))
        self.assertIn("付款记录", resolved.stdout)
        document = next(iter(self.state()["documents"].values()))
        self.assertEqual(document["status"], "organized")
        self.assertEqual(document["role_confidence"], "confirmed")

    def test_multiple_projects_route_interleaved_files_independently(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        beijing = "2026-06_北京出差"
        nanjing = "2026-07_南京出差"
        self.run_cli("init", str(self.root), "--case-name", beijing)
        self.run_cli("new", str(self.root), "--case-name", nanjing)

        arrivals = (
            ("北京酒店_600元_发票.txt", "beijing hotel invoice"),
            ("南京滴滴_80元_发票.txt", "nanjing taxi invoice"),
            ("北京酒店_600元_付款记录.txt", "beijing hotel payment"),
            ("南京滴滴_80元_付款记录.txt", "nanjing taxi payment"),
        )
        for filename, contents in arrivals:
            (self.root / "待处理" / filename).write_text(contents, encoding="utf-8")
            self.run_cli("check", str(self.root))

        registry = self.registry()
        self.assertEqual(len(registry["projects"]), 2)
        projects = {item["case_name"]: item for item in registry["projects"].values()}
        self.assertEqual(len(projects[beijing]["expenses"]), 1)
        self.assertEqual(len(projects[nanjing]["expenses"]), 1)
        self.assertTrue(list((self.root / "流程中" / beijing).glob("EXP-*_住宿费_600元_*")))
        self.assertTrue(list((self.root / "流程中" / nanjing).glob("EXP-*_出租车票_80元_*")))
        self.assertFalse(registry["unassigned_documents"])

    def test_message_context_can_route_an_opaque_file_to_a_named_project(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        beijing = "2026-06_北京出差"
        nanjing = "2026-07_南京出差"
        self.run_cli("init", str(self.root), "--case-name", beijing)
        self.run_cli("new", str(self.root), "--case-name", nanjing)
        filename = "餐费_100元_发票.txt"
        (self.root / "待处理" / filename).write_text("invoice", encoding="utf-8")
        self.run_cli("check", str(self.root), "--project", "北京出差")
        registry = self.registry()
        projects = {item["case_name"]: item for item in registry["projects"].values()}
        self.assertEqual(len(projects[beijing]["documents"]), 1)
        self.assertEqual(len(projects[nanjing]["documents"]), 0)

    def test_ambiguous_project_assignment_waits_for_more_context(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        self.run_cli("init", str(self.root), "--case-name", "2026-06_北京出差")
        self.run_cli("new", str(self.root), "--case-name", "2026-07_南京出差")
        filename = "付款记录_100元.txt"
        (self.root / "待处理" / filename).write_text("payment", encoding="utf-8")
        self.run_cli("check", str(self.root))
        registry = self.registry()
        self.assertEqual(len(registry["unassigned_documents"]), 1)
        self.assertTrue((self.root / "待处理" / "待归属" / filename).exists())

        self.run_cli("assign", str(self.root), filename, "--project", "北京出差")
        registry = self.registry()
        self.assertFalse(registry["unassigned_documents"])
        beijing = next(
            item for item in registry["projects"].values() if "北京" in item["case_name"]
        )
        document = next(iter(beijing["documents"].values()))
        self.assertTrue(document["current_path"].startswith(f"流程中/{beijing['case_name']}/"))

    def test_email_material_waits_without_a_project_and_new_project_revisits_it_once(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        self.run_cli("init", str(self.root))
        filename = "奥地利酒店_680元_发票.txt"
        source = self.root / "待处理" / filename
        source.write_text("austria hotel invoice", encoding="utf-8")

        checked = self.run_cli("check", str(self.root))
        self.assertIn("当前还没有出差报销项目", checked.stdout)
        registry = self.registry()
        self.assertEqual(len(registry["unassigned_documents"]), 1)
        held = self.root / "待处理" / "待归属" / filename
        self.assertTrue(held.is_file())
        self.assertEqual(intake_core.discover_inputs(self.root), [])

        created = self.run_cli("new", str(self.root), "--case-name", "2026-06_奥地利")
        self.assertIn("重新判断并归入 1 个待归属材料", created.stdout)
        registry = self.registry()
        self.assertFalse(registry["unassigned_documents"])
        project = next(iter(registry["projects"].values()))
        self.assertEqual(len(project["documents"]), 1)
        self.assertFalse(held.exists())
        self.assertTrue(list(
            (self.root / "流程中" / "2026-06_奥地利").glob("EXP-*_住宿费_680元_发票.txt")
        ))

    def test_conversational_correction_can_reassign_several_files(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        beijing = "2026-06_北京出差"
        nanjing = "2026-07_南京出差"
        self.run_cli("init", str(self.root), "--case-name", beijing)
        self.run_cli("new", str(self.root), "--case-name", nanjing)
        filenames = ("餐费_88元_发票.txt", "餐费_88元_付款记录.txt")
        for index, filename in enumerate(filenames):
            (self.root / "待处理" / filename).write_text(f"document {index}", encoding="utf-8")
        self.run_cli("check", str(self.root), "--project", "北京出差")
        self.run_cli("assign", str(self.root), *filenames, "--project", "南京出差")

        projects = {item["case_name"]: item for item in self.registry()["projects"].values()}
        self.assertFalse(projects[beijing]["documents"])
        self.assertEqual(len(projects[nanjing]["documents"]), 2)
        self.assertTrue(list((self.root / "流程中" / nanjing).glob("EXP-*_餐费_88元_*发票*")))
        self.assertTrue(list((self.root / "流程中" / nanjing).glob("EXP-*_餐费_88元_*付款记录*")))

    def test_finance_check_requires_project_name_when_several_are_open(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        self.run_cli("init", str(self.root), "--case-name", "2026-06_北京出差")
        self.run_cli("new", str(self.root), "--case-name", "2026-07_南京出差")
        result = self.run_cli("ready", str(self.root), expected=1)
        self.assertIn("请说明要提交哪个项目", result.stderr)

        named = self.run_cli("ready", str(self.root), "--project", "北京出差")
        self.assertIn("2026-06_北京出差", named.stdout)

    def test_finance_feedback_requires_evidence_and_is_exported(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        (self.root / "滴滴_80元_发票.txt").write_text("taxi invoice", encoding="utf-8")
        (self.root / "滴滴_80元_付款记录.txt").write_text("taxi payment", encoding="utf-8")
        self.run_cli("init", str(self.root), "--case-name", "2026-06_北京出差")
        feedback_source = Path(self.temporary.name) / "财务反馈原件.txt"
        feedback_source.write_text("财务反馈：网约车费用必须同时提供行程单。", encoding="utf-8")
        added = self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "财务反馈：网约车费用必须同时提供行程单。",
            "--source",
            "财务微信",
            "--source-file",
            str(feedback_source),
            "--category",
            "交通",
            "--require-evidence",
            "行程单|行程信息",
        )
        self.assertIn("FB-001", added.stdout)

        blocked = self.run_cli("ready", str(self.root))
        self.assertIn("财务反馈审查依据", blocked.stdout)
        self.assertIn("依据 FB-001 缺少行程单|行程信息", blocked.stdout)
        self.assertEqual(self.state()["status"], "collecting")
        self.assertEqual(len(self.state()["finance_feedback_review"]["gaps"]), 1)
        archive_blocked = self.run_cli("archive", str(self.root), expected=1)
        self.assertIn("尚有未解决材料", archive_blocked.stderr)

        (self.root / "待处理" / "滴滴_80元_行程单.txt").write_text(
            "order itinerary 行程信息", encoding="utf-8"
        )
        self.run_cli("check", str(self.root))
        ready = self.run_cli("ready", str(self.root))
        self.assertIn("FB-001 | 已满足", ready.stdout)
        self.assertEqual(self.state()["status"], "ready")

        self.run_cli("export", str(self.root))
        case = self.root / "流程中" / "2026-06_北京出差"
        evidence = (case / "财务反馈审查依据.md").read_text(encoding="utf-8")
        self.assertIn("FB-001", evidence)
        report = json.loads((case / "处理报告.json").read_text(encoding="utf-8"))
        self.assertTrue(report["finance_feedback_review"]["passed"])
        self.run_cli("archive", str(self.root))
        archive_path = (
            self.root / "已结束" / "2026-06_北京出差" /
            "2026-06-北京出差-测试用户-报销文件.zip"
        )
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
        self.assertIn("manifest/finance-feedback-review.json", names)
        self.assertTrue(any(name.startswith("manifest/finance-feedback-sources/") for name in names))

    def test_global_finance_feedback_previews_then_updates_the_user_scheme(self) -> None:
        self.init()
        workbook = self.root / "报销要求.xlsx"
        before_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
        preview = self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "财务要求餐费增加参会名单。",
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--apply-to-scheme",
            "--preview",
        )
        self.assertIn("财务反馈变更预览（尚未写入）", preview.stdout)
        self.assertIn("发票、付款记录 → 发票、付款记录、参会名单", preview.stdout)
        self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before_hash)

        rejected = self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "财务要求餐费增加参会名单。",
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--apply-to-scheme",
            expected=1,
        )
        self.assertIn("必须先向用户展示预览", rejected.stderr)
        self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before_hash)

        applied = self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "财务要求餐费增加参会名单。",
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--apply-to-scheme",
            "--confirmed",
        )
        self.assertIn("已写入全局报销要求：参会名单", applied.stdout)
        self.assertIn("报销要求.xlsx 已重新验证", applied.stdout)
        from claimmate_core.policy import catalog_from_workbook
        catalog = catalog_from_workbook(workbook)
        dining_materials = {
            rule["required_material"]
            for rule in catalog["rules"]
            if rule["expense_type_key"] == "dining"
        }
        self.assertIn("参会名单", dining_materials)
        feedback = json.loads(
            (self.root / ".claimmate" / "finance-feedback.json").read_text(encoding="utf-8")
        )["entries"][-1]
        self.assertTrue(feedback["incorporated_into_scheme"])
        self.assertTrue((self.root / feedback["scheme_change"]["backup"]).is_file())
        rejected_status = self.run_cli(
            "feedback-status",
            str(self.root),
            feedback["feedback_id"],
            "--status",
            "inactive",
            expected=1,
        )
        self.assertIn("停用审计记录不会撤销", rejected_status.stderr)

    def test_project_finance_feedback_is_temporary_and_does_not_change_scheme(self) -> None:
        self.init()
        workbook = self.root / "报销要求.xlsx"
        before_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
        preview = self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "本次出差餐费要有参会名单。",
            "--project",
            self.case_name,
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--preview",
        )
        self.assertIn("只对该项目生效", preview.stdout)
        self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "本次出差餐费要有参会名单。",
            "--project",
            self.case_name,
            "--expense-type",
            "餐费",
            "--require-evidence",
            "参会名单",
            "--confirmed",
        )
        self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before_hash)
        feedback = json.loads(
            (self.root / ".claimmate" / "finance-feedback.json").read_text(encoding="utf-8")
        )["entries"][-1]
        self.assertEqual(feedback["scope"], "project")
        self.assertEqual(feedback["expense_type_key"], "dining")
        self.assertFalse(feedback["incorporated_into_scheme"])
        ready = self.run_cli("ready", str(self.root))
        self.assertIn("参会名单", ready.stdout)

    def test_project_feedback_does_not_leak_to_another_project(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        beijing = "2026-06_北京出差"
        nanjing = "2026-07_南京出差"
        self.run_cli("init", str(self.root), "--case-name", beijing)
        self.run_cli("new", str(self.root), "--case-name", nanjing)
        self.run_cli(
            "feedback-add",
            str(self.root),
            "--text",
            "北京项目的住宿必须提供住宿水单。",
            "--project",
            "北京出差",
            "--category",
            "住宿",
            "--require-evidence",
            "住宿水单",
        )
        (self.root / "待处理" / "南京酒店_500元_发票.txt").write_text(
            "nanjing hotel invoice", encoding="utf-8"
        )
        (self.root / "待处理" / "南京酒店_500元_付款记录.txt").write_text(
            "nanjing hotel payment", encoding="utf-8"
        )
        self.run_cli("check", str(self.root), "--project", "南京出差")

        ready = self.run_cli("ready", str(self.root), "--project", "南京出差")
        self.assertIn("已生成报销明细表，可以交给财务", ready.stdout)
        self.assertNotIn("FB-001", ready.stdout)

    def test_feedback_source_is_preserved_and_rule_can_be_disabled(self) -> None:
        for path in self.root.iterdir():
            path.unlink()
        self.run_cli("init", str(self.root), "--case-name", "2026-06_北京出差")
        source = self.root / "待处理" / "财务邮件.txt"
        source.write_text("住宿费报销需要住宿水单。", encoding="utf-8")
        self.run_cli(
            "feedback-add",
            str(self.root),
            "--source-file",
            str(source),
            "--category",
            "住宿",
            "--require-evidence",
            "住宿水单",
        )
        feedback = json.loads(
            (self.root / ".claimmate" / "finance-feedback.json").read_text(encoding="utf-8")
        )["entries"][0]
        self.assertTrue((self.root / feedback["source_file"]).exists())
        self.assertFalse(source.exists())
        self.assertTrue(feedback["source_sha256"])

        self.run_cli(
            "feedback-status",
            str(self.root),
            "FB-001",
            "--status",
            "inactive",
            "--reason",
            "财务已更新政策",
        )
        listing = self.run_cli("feedback-list", str(self.root), "--include-inactive")
        self.assertIn("已停用", listing.stdout)
        updated = json.loads(
            (self.root / ".claimmate" / "finance-feedback.json").read_text(encoding="utf-8")
        )["entries"][0]
        self.assertEqual(updated["status"], "inactive")
        self.assertEqual(updated["status_history"][-1]["reason"], "财务已更新政策")


if __name__ == "__main__":
    unittest.main()
