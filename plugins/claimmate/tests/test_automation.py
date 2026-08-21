from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "skills" / "claimmate" / "scripts"
CORE_SCRIPT = SCRIPTS / "claimmate.py"
AUTOMATION_SCRIPT = SCRIPTS / "automation.py"
FAKE_CODEX = PLUGIN / "tests" / "fake_codex.py"
sys.path.insert(0, str(SCRIPTS))

import automation  # noqa: E402
import claimmate  # noqa: E402
from claimmate_core import projects as projects_core  # noqa: E402


class ClaimMateAutomationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "跨平台报销"
        self.root.mkdir()
        self.case_name = "2026-08-18至08-20_南京"
        self.fake_codex = Path(self.temporary.name) / "fake-codex.cmd"
        self.fake_codex.write_text(
            f'@"{sys.executable}" "{FAKE_CODEX}" %*\n', encoding="utf-8"
        )
        self.run_script(CORE_SCRIPT, "init", str(self.root), "--case-name", self.case_name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_script(self, script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if script == CORE_SCRIPT and arguments and arguments[0] == "init" and "--no-service" not in arguments:
            arguments = (*arguments, "--no-service")
        if script == CORE_SCRIPT and arguments and arguments[0] == "init" and "--user-name" not in arguments:
            arguments = (*arguments, "--user-name", "测试用户")
        if script == CORE_SCRIPT and arguments and arguments[0] == "init" and "--email-choice" not in arguments:
            arguments = (*arguments, "--email-choice", "skip")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["CLAIMMATE_CODEX_COMMAND"] = str(self.fake_codex)
        environment.pop("CLAIMMATE_DISABLE_MODEL", None)
        result = subprocess.run(
            [sys.executable, str(script), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if script == CORE_SCRIPT and arguments and arguments[0] == "init" and "--dry-run" not in arguments:
            confirmed = subprocess.run(
                [
                    sys.executable,
                    str(CORE_SCRIPT),
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

    def test_watch_once_processes_new_file(self) -> None:
        source = self.root / "待处理" / "北京酒店_680元_发票.txt"
        source.write_text("new hotel invoice", encoding="utf-8")
        result = self.run_script(AUTOMATION_SCRIPT, "watch", str(self.root), "--once", "--no-email")
        self.assertIn("处理完成", result.stdout)
        destination = self.root / "流程中" / self.case_name
        self.assertEqual(len(list(destination.glob("EXP-*_住宿费_680元_发票.txt"))), 1)
        self.assertFalse(source.exists())

    def test_stability_tracker_waits_for_unchanged_file(self) -> None:
        tracker = automation.StabilityTracker(2)
        first = {"receipt.pdf": (100, 1)}
        self.assertFalse(tracker.observe(first))
        self.assertFalse(tracker.observe({"receipt.pdf": (120, 2)}))
        self.assertTrue(tracker.observe({"receipt.pdf": (120, 2)}))

    def test_default_watcher_uses_native_events_with_slow_fallback(self) -> None:
        watch = automation.default_automation_config()["watch"]
        self.assertEqual(watch["mode"], "native")
        self.assertGreaterEqual(watch["poll_seconds"], 10)
        self.assertEqual(watch["reconcile_seconds"], 300)

    def test_initialized_workspace_without_a_project_accepts_automation(self) -> None:
        empty_root = Path(self.temporary.name) / "尚未新建项目"
        empty_root.mkdir()
        self.run_script(CORE_SCRIPT, "init", str(empty_root))
        automation.require_workspace(empty_root)
        guidance = automation.setup_guidance(empty_root)
        self.assertIn("新建一个 6 月北京出差报销", guidance)

    def test_scheme_confirmation_installs_the_background_listener_by_default(self) -> None:
        automatic_root = Path(self.temporary.name) / "自动监听工作区"
        automatic_root.mkdir()
        args = claimmate.build_parser().parse_args([
            "init",
            str(automatic_root),
            "--case-name",
            "2026-09_上海出差",
            "--user-name",
            "测试用户",
            "--email-choice",
            "skip",
        ])
        with patch.object(
            projects_core,
            "auto_install_background_service",
            return_value={"installed": True, "message": "测试监听器已启动"},
        ) as installer, patch("builtins.print") as printer:
            args.handler(args)
            installer.assert_not_called()
            confirm = claimmate.build_parser().parse_args([
                "requirements-confirm",
                str(automatic_root),
                "--confirmed",
            ])
            confirm.handler(confirm)
        installer.assert_called_once_with(automatic_root.resolve())
        rendered = "\n".join(
            " ".join(str(value) for value in call.args) for call in printer.call_args_list
        )
        self.assertIn("已自动安装并启动", rendered)
        self.assertTrue((automatic_root / ".claimmate" / "projects.json").exists())

    def test_initial_configuration_can_explicitly_skip_the_background_listener(self) -> None:
        manual_root = Path(self.temporary.name) / "手动监听工作区"
        manual_root.mkdir()
        args = claimmate.build_parser().parse_args([
            "init",
            str(manual_root),
            "--no-service",
            "--user-name",
            "测试用户",
            "--email-choice",
            "skip",
        ])
        with patch.object(projects_core, "auto_install_background_service") as installer, patch(
            "builtins.print"
        ):
            args.handler(args)
            confirm = claimmate.build_parser().parse_args([
                "requirements-confirm",
                str(manual_root),
                "--confirmed",
            ])
            confirm.handler(confirm)
        installer.assert_not_called()

    def test_email_choice_connect_must_be_configured_before_scheme_confirmation(self) -> None:
        email_root = Path(self.temporary.name) / "首次接入邮箱"
        email_root.mkdir()
        init = claimmate.build_parser().parse_args([
            "init",
            str(email_root),
            "--user-name",
            "测试用户",
            "--email-choice",
            "connect",
            "--no-service",
        ])
        with patch("builtins.print"):
            init.handler(init)

        service_install = automation.build_parser().parse_args([
            "service-install",
            str(email_root),
        ])
        with self.assertRaisesRegex(SystemExit, "首次配置尚未完成"):
            service_install.handler(service_install)

        confirm = claimmate.build_parser().parse_args([
            "requirements-confirm",
            str(email_root),
            "--confirmed",
            "--no-service",
        ])
        with self.assertRaisesRegex(SystemExit, "邮箱账号配置尚未完成"):
            confirm.handler(confirm)

        configure = automation.build_parser().parse_args([
            "configure",
            str(email_root),
            "--email-provider",
            "gmail",
            "--email-username",
            "user@example.com",
            "--enable-email",
        ])
        with patch("builtins.print"):
            configure.handler(configure)
            confirm.handler(confirm)
        workspace_config = json.loads(
            (email_root / ".claimmate" / "config.json").read_text(encoding="utf-8")
        )
        self.assertTrue(workspace_config["setup"]["completed_at"])

    def test_listener_install_failure_does_not_rollback_confirmed_configuration(self) -> None:
        fallback_root = Path(self.temporary.name) / "监听失败仍可使用"
        fallback_root.mkdir()
        args = claimmate.build_parser().parse_args([
            "init", str(fallback_root), "--user-name", "测试用户", "--email-choice", "skip"
        ])
        with patch.object(
            projects_core,
            "auto_install_background_service",
            return_value={"installed": False, "message": "系统策略拒绝创建后台任务"},
        ), patch("builtins.print") as printer:
            args.handler(args)
            confirm = claimmate.build_parser().parse_args([
                "requirements-confirm",
                str(fallback_root),
                "--confirmed",
            ])
            confirm.handler(confirm)
        rendered = "\n".join(
            " ".join(str(value) for value in call.args) for call in printer.call_args_list
        )
        self.assertIn("自动安装失败", rendered)
        self.assertIn("系统策略拒绝创建后台任务", rendered)
        self.assertTrue((fallback_root / ".claimmate" / "projects.json").exists())

    def test_native_event_source_observes_new_file(self) -> None:
        source = automation.create_native_event_source(self.root)
        if source is None:
            self.skipTest("当前平台没有原生事件后端")
        try:
            time.sleep(0.05)
            (self.root / "待处理" / "event-test.txt").write_text("event", encoding="utf-8")
            self.assertTrue(source.wait(3))
        finally:
            source.close()

    def test_native_watch_loop_processes_new_event(self) -> None:
        probe = automation.create_native_event_source(self.root)
        if probe is None:
            self.skipTest("当前平台没有原生事件后端")
        probe.close()

        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["CLAIMMATE_CODEX_COMMAND"] = str(self.fake_codex)
        environment.pop("CLAIMMATE_DISABLE_MODEL", None)
        process = subprocess.Popen(
            [sys.executable, str(AUTOMATION_SCRIPT), "watch", str(self.root), "--no-email"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        try:
            time.sleep(0.3)
            source = self.root / "待处理" / "南京酒店_680元_付款记录.txt"
            source.write_text("payment", encoding="utf-8")
            destination = self.root / "流程中" / self.case_name
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if list(destination.glob("EXP-*_住宿费_680元_付款记录.txt")):
                    break
                time.sleep(0.1)
            self.assertTrue(list(destination.glob("EXP-*_住宿费_680元_付款记录.txt")))
        finally:
            process.terminate()
            process.communicate(timeout=5)

    def test_email_configuration_contains_no_password(self) -> None:
        configured = self.run_script(
            AUTOMATION_SCRIPT,
            "configure",
            str(self.root),
            "--email-provider",
            "gmail",
            "--email-username",
            "user@example.com",
            "--enable-email",
        )
        config_path = self.root / ".claimmate" / "automation.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["email"]["host"], "imap.gmail.com")
        self.assertEqual(config["email"]["mailbox"], "ClaimMate")
        self.assertNotIn("password", config["email"])
        self.assertNotIn("secret", json.dumps(list(config["email"].values())).lower())
        self.assertIn("报销要交给财务了", configured.stdout)
        guidance = automation.setup_guidance(self.root)
        self.assertIn(self.case_name, guidance)
        self.assertIn(str(self.root / "待处理"), guidance)
        self.assertIn("报销要交给财务了", guidance)
        self.assertIn("1. 新建项目", guidance)
        self.assertIn("2. 更新材料", guidance)
        self.assertIn("3. 交付财务", guidance)
        self.assertIn("每项收款人默认使用初始化时登记的姓名", guidance)
        self.assertIn("财务反馈", guidance)
        self.assertIn("三列", guidance)
        self.assertIn("按付款合计核验", guidance)
        self.assertIn("最近一次整理可撤销", guidance)
        self.assertIn("不会未经确认替你提交", guidance)
        positions = [
            guidance.index("1. 新建项目"),
            guidance.index("2. 更新材料"),
            guidance.index("3. 交付财务"),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_attachment_parser_filters_and_decodes(self) -> None:
        message = EmailMessage()
        message["Subject"] = "报销附件"
        message.set_content("Please see attachments")
        message.add_attachment(b"invoice", maintype="application", subtype="pdf", filename="酒店发票.pdf")
        message.add_attachment(b"ignored", maintype="text", subtype="plain", filename="notes.log")
        attachments = automation.attachment_payloads(message, {".pdf"})
        self.assertEqual(attachments, [("酒店发票.pdf", b"invoice")])

    def test_imap_sync_downloads_atomically_and_advances_cursor(self) -> None:
        message = EmailMessage()
        message["From"] = "travel@example.com"
        message["Subject"] = "南京出差报销"
        message.set_content("attachment")
        message.add_attachment(
            b"invoice payload",
            maintype="application",
            subtype="pdf",
            filename="南京酒店_680元_发票.pdf",
        )

        class FakeIMAP:
            def __init__(self, _host, _port):
                pass

            def login(self, _username, _password):
                return "OK", []

            def select(self, _mailbox, readonly=True):
                return "OK", [b"1"]

            def response(self, _name):
                return "UIDVALIDITY", [b"123"]

            def uid(self, command, *_args):
                if command == "search":
                    return "OK", [b"7"]
                return "OK", [(b"7 (RFC822)", message.as_bytes())]

            def logout(self):
                return "BYE", []

        config = automation.default_automation_config()
        config["email"].update({
            "enabled": True,
            "host": "imap.example.com",
            "username": "user@example.com",
        })
        with patch.object(automation.imaplib, "IMAP4_SSL", FakeIMAP), patch.object(
            automation, "read_os_credential", return_value="secret"
        ):
            results = automation.sync_email(self.root, config)
        self.assertEqual(results["downloaded"], 1)
        self.assertTrue((self.root / "待处理" / "南京酒店_680元_发票.pdf").exists())
        state = json.loads(
            (self.root / ".claimmate" / "sources" / "email-state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["last_uid"], 7)
        self.assertEqual(state["uid_validity"], "123")
        self.assertFalse(any((self.root / "待处理").glob("*.partial")))

    def test_runtime_and_launchd_manifest_are_portable(self) -> None:
        runtime = automation.copy_runtime(self.root)
        self.assertTrue((runtime / "scripts" / "automation.py").exists())
        self.assertTrue((runtime / "scripts" / "claimmate.py").exists())
        self.assertTrue((runtime / "scripts" / "windows_ocr.ps1").exists())
        self.assertTrue((runtime / "assets" / "model-output.schema.json").exists())
        command = automation.runtime_command(self.root)
        fake_codex = self.root / "bin" / "codex"
        fake_codex.parent.mkdir(parents=True, exist_ok=True)
        fake_codex.write_text("#!/bin/sh\n", encoding="utf-8")
        with patch.object(automation.core, "find_codex_command", return_value=str(fake_codex)):
            manifest = automation.launchd_plist(self.root, command)
        self.assertEqual(manifest["ProgramArguments"], command)
        self.assertTrue(manifest["RunAtLoad"])
        self.assertEqual(
            manifest["EnvironmentVariables"]["CLAIMMATE_CODEX_COMMAND"],
            str(fake_codex.absolute()),
        )
        self.assertIn(str(fake_codex.parent.absolute()), manifest["EnvironmentVariables"]["PATH"])
        restored = claimmate.absolute(self.root, "流程中/2026-08-18至08-20_南京/餐饮/file.pdf")
        self.assertEqual(restored, self.root / "流程中" / self.case_name / "餐饮" / "file.pdf")

    @unittest.skipUnless(sys.platform == "win32", "Windows startup fallback")
    def test_windows_service_falls_back_when_task_scheduler_denies_access(self) -> None:
        denied = subprocess.CompletedProcess(
            args=["schtasks"], returncode=1, stdout="", stderr="ERROR: Access is denied."
        )
        with patch.object(automation.subprocess, "run", return_value=denied), patch.object(
            automation, "set_windows_run_entry"
        ) as run_entry, patch.object(automation, "start_windows_watcher", return_value=4321):
            result = automation.install_service(self.root)
        run_entry.assert_called_once()
        self.assertIn("当前用户启动项", result)
        self.assertIn("PID 4321", result)

    @unittest.skipUnless(sys.platform == "win32", "Windows startup fallback")
    def test_windows_service_status_reports_running_startup_fallback(self) -> None:
        missing = subprocess.CompletedProcess(args=["schtasks"], returncode=1, stdout="", stderr="")
        with patch.object(automation.subprocess, "run", return_value=missing), patch.object(
            automation, "get_windows_run_entry", return_value='"pythonw.exe" watcher.py'
        ), patch.object(automation, "windows_watcher_running", return_value=True):
            status = automation.service_status(self.root)
        self.assertEqual(status, "已安装（当前用户启动项并运行）")


if __name__ == "__main__":
    unittest.main()
