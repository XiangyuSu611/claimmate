#!/usr/bin/env python3
"""Cross-platform background intake for ClaimMate."""

from __future__ import annotations

import argparse
import contextlib
import email
import getpass
import hashlib
import imaplib
import io
import json
import mimetypes
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from email import policy
from pathlib import Path
from typing import Any

import claimmate as core

try:
    import winreg
except ImportError:  # pragma: no cover - only available on Windows
    winreg = None  # type: ignore[assignment]


AUTOMATION_VERSION = 1
AUTOMATION_CONFIG = "automation.json"
EMAIL_STATE = "email-state.json"
ACTIVITY_LOG = "activity.jsonl"
RUNTIME_FOLDER = "runtime"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
EMAIL_PRESETS = {
    "gmail": {"host": "imap.gmail.com", "port": 993, "mailbox": "ClaimMate"},
    "outlook": {"host": "outlook.office365.com", "port": 993, "mailbox": "ClaimMate"},
    "imap": {"host": "", "port": 993, "mailbox": "ClaimMate"},
}


def default_automation_config() -> dict[str, Any]:
    return {
        "version": AUTOMATION_VERSION,
        "watch": {
            "mode": "native",
            "settle_seconds": 1,
            "poll_seconds": 15,
            "stable_checks": 2,
            "reconcile_seconds": 300,
        },
        "email": {
            "enabled": False,
            "provider": "imap",
            "host": "",
            "port": 993,
            "username": "",
            "mailbox": "ClaimMate",
            "poll_seconds": 300,
            "password_env": "CLAIMMATE_EMAIL_PASSWORD",
            "sender_allowlist": [],
            "subject_keywords": [],
            "allowed_extensions": sorted(core.ELIGIBLE_EXTENSIONS),
        },
    }


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def automation_paths(root: Path) -> dict[str, Path]:
    meta = root / core.META
    return {
        "config": meta / AUTOMATION_CONFIG,
        "email_state": meta / "sources" / EMAIL_STATE,
        "activity": meta / ACTIVITY_LOG,
        "runtime": meta / RUNTIME_FOLDER,
        "watcher_log": meta / "watcher.log",
        "watcher_error": meta / "watcher-error.log",
        "watcher_pid": meta / "watcher.pid",
    }


def require_workspace(root: Path) -> None:
    metadata = root / core.META
    if not (metadata / core.PROJECTS_STATE).exists() and not (metadata / "state.json").exists():
        raise SystemExit(f"该文件夹尚未初始化：{root}")


def load_automation_config(root: Path) -> dict[str, Any]:
    path = automation_paths(root)["config"]
    if not path.exists():
        return default_automation_config()
    return merge_dict(default_automation_config(), core.read_json(path))


def save_automation_config(root: Path, config: dict[str, Any]) -> None:
    core.write_json(automation_paths(root)["config"], config)
    core.hide_internal_folder(root / core.META)


def setup_guidance(root: Path) -> str:
    registry, workspace_config = core.load_project_registry(root)
    blocker = core.setup_blocker(root, workspace_config)
    if blocker:
        return (
            f"\nClaimMate 首次配置尚未完成：{blocker}。\n"
            f"使用者：{core.claimant_name(workspace_config) or '尚未设置'}\n"
            f"邮箱附件：{core.email_intake_choice_label(workspace_config)}\n"
            f"请先查看并确认 Scheme：{root / core.REQUIREMENTS_WORKBOOK_NAME}\n"
            "确认前不会启动监听器或自动处理材料。"
        )
    projects = list(registry.get("projects", {}).values())
    project_line = (
        "当前还没有报销项目，请先说“新建一个 6 月北京出差报销”。"
        if not projects
        else "当前报销项目：" + "、".join(str(item.get("case_name")) for item in projects)
    )
    inbox_path = str((root / core.INBOX).resolve())
    return (
        f"\nClaimMate 自动处理已就绪。{project_line}\n"
        "\n1. 新建项目\n"
        "- 说“新建一个 6 月北京出差报销”或“新建 7 月合肥出差报销”。\n"
        "- 只处理出差报销；可以同时建立多个项目，日期不完整也能先开始。\n"
        "\n2. 更新材料\n"
        f"- 直接发送文件、放进“{inbox_path}”，或配置邮箱获取附件。\n"
        "- 不用改名或分类；大模型判断出差范围、费用类型、项目归属、金额和材料配对。\n"
        "- 多个项目可以交叉收材料；不确定的文件会安全保留，可纠正或撤销。\n"
        "- “报销要求.xlsx”只维护“费用类型、必须材料、财务其他要求”三列。\n"
        "- 收到财务反馈时直接发送原文或附件；确认适用范围和修改内容后，ClaimMate 更新 Scheme 或本项目临时要求。\n"
        "\n3. 交付财务\n"
        "- 点名说“北京出差报销要交给财务了”。一张发票可以对应多笔付款记录，按付款合计核验。\n"
        "- 通过后生成包含付款合计和差额的逐项明细表；每项收款人默认使用初始化时登记的姓名。ClaimMate 不会未经确认替你提交。\n"
        "\n原件会备份，最近一次整理可撤销。\n"
        f"完整说明见工作区中的“{core.GUIDE}”。"
    )


def append_activity(root: Path, event: str, **details: Any) -> None:
    path = automation_paths(root)["activity"]
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"time": core.now_iso(), "event": event, **details}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_core_check(root: Path) -> str:
    output = io.StringIO()
    arguments = argparse.Namespace(folder=str(root), dry_run=False, project=None)
    with contextlib.redirect_stdout(output):
        try:
            core.command_check(arguments)
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from exc
    rendered = output.getvalue().strip()
    append_activity(root, "claimmate_check", output=rendered)
    return rendered


def input_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in core.discover_inputs(root):
        try:
            stats = path.stat()
            snapshot[str(path.resolve())] = (stats.st_size, stats.st_mtime_ns)
        except FileNotFoundError:
            continue
    return snapshot


class StabilityTracker:
    def __init__(self, stable_checks: int):
        self.stable_checks = max(1, stable_checks)
        self.previous: dict[str, tuple[int, int]] = {}
        self.counts: dict[str, int] = {}

    def observe(self, snapshot: dict[str, tuple[int, int]]) -> bool:
        next_counts: dict[str, int] = {}
        ready = False
        for path, fingerprint in snapshot.items():
            count = self.counts.get(path, 0) + 1 if self.previous.get(path) == fingerprint else 1
            next_counts[path] = count
            if count >= self.stable_checks:
                ready = True
        self.previous = snapshot
        self.counts = next_counts
        return ready

    def reset(self) -> None:
        self.previous.clear()
        self.counts.clear()


class WindowsDirectoryEvents:
    """Wait for directory changes using the Windows kernel API."""

    name = "Windows 原生文件事件"

    def __init__(self, paths: list[Path]):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._stopped = threading.Event()
        self._changed = threading.Event()
        self._error: OSError | None = None
        self._handles: list[Any] = []
        self._threads: list[threading.Thread] = []

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._create_file.restype = wintypes.HANDLE
        self._read_changes = kernel32.ReadDirectoryChangesW
        self._read_changes.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        self._read_changes.restype = wintypes.BOOL
        self._cancel_io = kernel32.CancelIoEx
        self._cancel_io.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
        self._cancel_io.restype = wintypes.BOOL
        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = [wintypes.HANDLE]
        self._close_handle.restype = wintypes.BOOL

        invalid_handle = ctypes.c_void_p(-1).value
        for path in paths:
            handle = self._create_file(
                str(path),
                0x0001,  # FILE_LIST_DIRECTORY
                0x00000001 | 0x00000002 | 0x00000004,
                None,
                3,  # OPEN_EXISTING
                0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
                None,
            )
            if handle == invalid_handle:
                error = ctypes.get_last_error()
                self.close()
                raise OSError(error, f"无法监听目录：{path}")
            self._handles.append(handle)

        for handle in self._handles:
            thread = threading.Thread(target=self._watch, args=(handle,), daemon=True)
            thread.start()
            self._threads.append(thread)

    def _watch(self, handle: Any) -> None:
        notify_filter = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010
        buffer = self._ctypes.create_string_buffer(65536)
        returned = self._wintypes.DWORD()
        while not self._stopped.is_set():
            succeeded = self._read_changes(
                handle,
                buffer,
                len(buffer),
                False,
                notify_filter,
                self._ctypes.byref(returned),
                None,
                None,
            )
            if self._stopped.is_set():
                return
            if succeeded and returned.value:
                self._changed.set()
                continue
            error = self._ctypes.get_last_error()
            if error not in (6, 995):  # INVALID_HANDLE / OPERATION_ABORTED
                self._error = OSError(error, "Windows 文件事件监听失败")
                self._changed.set()
            return

    def wait(self, timeout: float) -> bool:
        changed = self._changed.wait(max(0.0, timeout))
        self._changed.clear()
        if self._error:
            raise self._error
        return changed

    def close(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._changed.set()
        for handle in self._handles:
            self._cancel_io(handle, None)
        for thread in self._threads:
            thread.join(timeout=1)
        for handle in self._handles:
            self._close_handle(handle)
        self._handles.clear()


class MacOSDirectoryEvents:
    """Wait for directory changes using macOS/BSD kqueue."""

    name = "macOS 原生文件事件"

    def __init__(self, paths: list[Path]):
        import select

        self._select = select
        self._queue = select.kqueue()
        self._descriptors: list[int] = []
        try:
            open_flags = getattr(os, "O_EVTONLY", os.O_RDONLY)
            changes = []
            event_flags = (
                select.KQ_NOTE_WRITE
                | select.KQ_NOTE_EXTEND
                | select.KQ_NOTE_ATTRIB
                | select.KQ_NOTE_LINK
                | select.KQ_NOTE_RENAME
                | select.KQ_NOTE_DELETE
            )
            for path in paths:
                descriptor = os.open(path, open_flags)
                self._descriptors.append(descriptor)
                changes.append(
                    select.kevent(
                        descriptor,
                        filter=select.KQ_FILTER_VNODE,
                        flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
                        fflags=event_flags,
                    )
                )
            self._queue.control(changes, 0, 0)
        except Exception:
            self.close()
            raise

    def wait(self, timeout: float) -> bool:
        return bool(self._queue.control([], 1, max(0.0, timeout)))

    def close(self) -> None:
        for descriptor in getattr(self, "_descriptors", []):
            with contextlib.suppress(OSError):
                os.close(descriptor)
        self._descriptors = []
        queue = getattr(self, "_queue", None)
        if queue is not None:
            with contextlib.suppress(OSError):
                queue.close()


def create_native_event_source(root: Path) -> WindowsDirectoryEvents | MacOSDirectoryEvents | None:
    paths = [root, root / core.INBOX]
    if sys.platform == "win32":
        return WindowsDirectoryEvents(paths)
    if sys.platform == "darwin":
        return MacOSDirectoryEvents(paths)
    return None


def known_hashes(root: Path) -> set[str]:
    try:
        registry, _ = core.load_project_registry(root)
    except SystemExit:
        return set()
    hashes = set(registry.get("unassigned_documents", {}).keys())
    hashes.update(item.get("sha256") for item in registry.get("duplicates", []) if item.get("sha256"))
    for project in registry.get("projects", {}).values():
        hashes.update(project.get("documents", {}).keys())
        hashes.update(
            item.get("sha256") for item in project.get("duplicates", []) if item.get("sha256")
        )
    return hashes


def credential_service(root: Path) -> str:
    return f"ClaimMate-{service_key(root).split('-')[-1]}"


def windows_write_credential(target: str, username: str, password: str) -> None:
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    library.CredWriteW.restype = wintypes.BOOL
    encoded = password.encode("utf-16-le")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = Credential()
    credential.Type = 1
    credential.TargetName = target
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = 2
    credential.UserName = username
    if not library.CredWriteW(ctypes.byref(credential), 0):
        raise OSError(ctypes.get_last_error(), "无法写入 Windows Credential Manager")


def windows_read_credential(target: str) -> str | None:
    import ctypes
    from ctypes import wintypes

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    pointer = ctypes.POINTER(Credential)()
    library.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    library.CredReadW.restype = wintypes.BOOL
    library.CredFree.argtypes = [ctypes.c_void_p]
    if not library.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        return None
    try:
        credential = pointer.contents
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        library.CredFree(pointer)


def windows_delete_credential(target: str) -> None:
    import ctypes
    from ctypes import wintypes

    library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    library.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    library.CredDeleteW.restype = wintypes.BOOL
    library.CredDeleteW(target, 1, 0)


def store_os_credential(root: Path, username: str, password: str) -> None:
    service = credential_service(root)
    if sys.platform == "win32":
        windows_write_credential(service, username, password)
        return
    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", username, "-w", password],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "无法写入 macOS Keychain")
        return
    raise RuntimeError("当前系统不支持安全凭据存储。")


def read_os_credential(root: Path, username: str) -> str | None:
    service = credential_service(root)
    if sys.platform == "win32":
        return windows_read_credential(service)
    if sys.platform == "darwin":
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", username, "-w"],
            capture_output=True,
            text=True,
        )
        return result.stdout.rstrip("\r\n") if result.returncode == 0 else None
    return None


def delete_os_credential(root: Path, username: str) -> None:
    service = credential_service(root)
    if sys.platform == "win32":
        windows_delete_credential(service)
    elif sys.platform == "darwin":
        subprocess.run(
            ["security", "delete-generic-password", "-s", service, "-a", username],
            capture_output=True,
        )


def load_email_state(root: Path) -> dict[str, Any]:
    path = automation_paths(root)["email_state"]
    if not path.exists():
        return {"uid_validity": None, "last_uid": 0, "attachment_hashes": []}
    return core.read_json(path)


def save_email_state(root: Path, state: dict[str, Any]) -> None:
    hashes = list(dict.fromkeys(state.get("attachment_hashes", [])))
    state["attachment_hashes"] = hashes[-10000:]
    core.write_json(automation_paths(root)["email_state"], state)


def parse_uid_validity(connection: imaplib.IMAP4_SSL) -> str | None:
    _, values = connection.response("UIDVALIDITY")
    if not values:
        return None
    raw = values[-1]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    digits = "".join(character for character in str(raw) if character.isdigit())
    return digits or None


def message_is_allowed(message: email.message.EmailMessage, config: dict[str, Any]) -> bool:
    senders = [str(item).lower() for item in config.get("sender_allowlist", []) if str(item).strip()]
    subjects = [str(item).lower() for item in config.get("subject_keywords", []) if str(item).strip()]
    sender = str(message.get("From", "")).lower()
    subject = str(message.get("Subject", "")).lower()
    if senders and not any(item in sender for item in senders):
        return False
    if subjects and not any(item in subject for item in subjects):
        return False
    return True


def attachment_payloads(
    message: email.message.EmailMessage,
    allowed_extensions: set[str],
) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []
    for index, part in enumerate(message.iter_attachments(), start=1):
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        filename = part.get_filename()
        if not filename:
            extension = mimetypes.guess_extension(part.get_content_type()) or ".bin"
            filename = f"邮件附件_{index}{extension}"
        extension = Path(filename).suffix.lower()
        if extension not in allowed_extensions:
            continue
        cleaned = core.safe_name(Path(filename).stem, f"邮件附件_{index}") + extension
        attachments.append((cleaned, payload))
    return attachments


def write_attachment_atomic(root: Path, filename: str, payload: bytes) -> Path:
    inbox = root / core.INBOX
    inbox.mkdir(parents=True, exist_ok=True)
    destination = core.unique_destination(inbox / filename)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(destination)
    return destination


def fetch_message(connection: imaplib.IMAP4_SSL, uid: bytes) -> email.message.EmailMessage:
    status, payload = connection.uid("fetch", uid, "(RFC822)")
    if status != "OK":
        raise RuntimeError(f"无法读取邮件 UID {uid.decode(errors='ignore')}")
    raw = next((item[1] for item in payload if isinstance(item, tuple) and len(item) > 1), None)
    if not raw:
        raise RuntimeError(f"邮件 UID {uid.decode(errors='ignore')} 没有正文")
    return email.message_from_bytes(raw, policy=policy.default)


def sync_email(root: Path, config: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    email_config = config["email"]
    if not email_config.get("enabled"):
        return {"messages": 0, "downloaded": 0, "duplicates": 0, "disabled": True}
    host = str(email_config.get("host", "")).strip()
    username = str(email_config.get("username", "")).strip()
    password_env = str(email_config.get("password_env", "CLAIMMATE_EMAIL_PASSWORD"))
    password = read_os_credential(root, username) or os.environ.get(password_env)
    if not host or not username:
        raise RuntimeError("邮箱配置缺少 host 或 username。")
    if not password:
        raise RuntimeError(
            f"尚未找到邮箱凭据。请运行 credentials-set，或临时设置环境变量 {password_env}。"
        )

    state = load_email_state(root)
    known = known_hashes(root)
    known.update(state.get("attachment_hashes", []))
    allowed = {str(item).lower() for item in email_config.get("allowed_extensions", [])}
    results = {"messages": 0, "downloaded": 0, "duplicates": 0, "disabled": False}
    connection = imaplib.IMAP4_SSL(host, int(email_config.get("port", 993)))
    try:
        connection.login(username, password)
        status, _ = connection.select(str(email_config.get("mailbox", "ClaimMate")), readonly=True)
        if status != "OK":
            raise RuntimeError("无法打开邮箱目录。请确认已创建 ClaimMate 标签或文件夹。")
        uid_validity = parse_uid_validity(connection)
        if state.get("uid_validity") and uid_validity != state.get("uid_validity"):
            state["last_uid"] = 0
        state["uid_validity"] = uid_validity
        start_uid = int(state.get("last_uid", 0)) + 1
        status, data = connection.uid("search", None, f"UID {start_uid}:*")
        if status != "OK":
            raise RuntimeError("邮箱增量检索失败。")
        uids = data[0].split() if data and data[0] else []
        for uid in uids:
            numeric_uid = int(uid)
            message = fetch_message(connection, uid)
            results["messages"] += 1
            if message_is_allowed(message, email_config):
                for filename, payload in attachment_payloads(message, allowed):
                    digest = hashlib.sha256(payload).hexdigest()
                    if digest in known:
                        results["duplicates"] += 1
                        continue
                    if not dry_run:
                        destination = write_attachment_atomic(root, filename, payload)
                        append_activity(
                            root,
                            "email_attachment_downloaded",
                            uid=numeric_uid,
                            filename=destination.name,
                            sha256=digest,
                        )
                    known.add(digest)
                    state.setdefault("attachment_hashes", []).append(digest)
                    results["downloaded"] += 1
            state["last_uid"] = max(int(state.get("last_uid", 0)), numeric_uid)
            if not dry_run:
                save_email_state(root, state)
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    if not dry_run:
        append_activity(root, "email_sync", **results)
    return results


def print_email_result(results: dict[str, Any]) -> None:
    if results.get("disabled"):
        print("邮箱采集尚未启用。")
        return
    print(
        f"邮箱检查完成：扫描 {results['messages']} 封新邮件，"
        f"获取 {results['downloaded']} 个附件，跳过 {results['duplicates']} 个重复附件。"
    )


def command_configure(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    require_workspace(root)
    config = load_automation_config(root)
    email_config = config["email"]
    if args.email_provider:
        preset = EMAIL_PRESETS[args.email_provider]
        email_config.update(preset)
        email_config["provider"] = args.email_provider
    for argument, key in (
        (args.email_host, "host"),
        (args.email_username, "username"),
        (args.email_mailbox, "mailbox"),
        (args.password_env, "password_env"),
    ):
        if argument is not None:
            email_config[key] = argument
    if args.email_port is not None:
        email_config["port"] = args.email_port
    if args.email_interval is not None:
        email_config["poll_seconds"] = max(60, args.email_interval)
    if args.enable_email:
        email_config["enabled"] = True
    if args.disable_email:
        email_config["enabled"] = False
    save_automation_config(root, config)
    print(f"自动采集配置已保存：{core.relative(automation_paths(root)['config'], root)}")
    if email_config.get("enabled"):
        print(
            "邮箱密码不会写入文件；请运行 credentials-set 保存到系统安全凭据库。"
            f"环境变量 {email_config['password_env']} 仅作为临时后备。"
        )
    print(setup_guidance(root))


def command_email_sync(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    require_workspace(root)
    config = load_automation_config(root)
    results = sync_email(root, config, args.dry_run)
    print_email_result(results)
    if results.get("downloaded") and not args.dry_run:
        print(run_core_check(root))


def command_credentials_set(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    require_workspace(root)
    config = load_automation_config(root)
    username = str(config["email"].get("username", "")).strip()
    if not username:
        raise RuntimeError("请先使用 configure 设置邮箱用户名。")
    password = getpass.getpass("邮箱应用密码或访问令牌：")
    if not password:
        raise RuntimeError("没有输入凭据。")
    store_os_credential(root, username, password)
    print("邮箱凭据已保存到系统安全凭据库。")
    print(setup_guidance(root))


def command_credentials_delete(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    require_workspace(root)
    config = load_automation_config(root)
    username = str(config["email"].get("username", "")).strip()
    if username:
        delete_os_credential(root, username)
    print("邮箱凭据已移除。")


def watch_loop(root: Path, once: bool = False, no_email: bool = False) -> None:
    require_workspace(root)
    _, workspace_config = core.load_project_registry(root)
    core.require_setup_ready(root, workspace_config)
    config = load_automation_config(root)
    watch_config = config["watch"]
    poll_seconds = max(5, float(watch_config.get("poll_seconds", 15)))
    settle_seconds = max(0.25, float(watch_config.get("settle_seconds", 1)))
    reconcile_seconds = max(30, float(watch_config.get("reconcile_seconds", 300)))
    tracker = StabilityTracker(int(watch_config.get("stable_checks", 2)))

    if once:
        if not no_email:
            print_email_result(sync_email(root, config))
        if input_snapshot(root):
            print(run_core_check(root))
        return

    pid_path = automation_paths(root)["watcher_pid"]
    pid_path.write_text(str(os.getpid()), encoding="ascii")

    stopped = threading.Event()

    def stop_handler(_signum, _frame):
        stopped.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), stop_handler)

    event_source = None
    if str(watch_config.get("mode", "native")).lower() != "poll":
        try:
            event_source = create_native_event_source(root)
        except OSError as exc:
            append_activity(root, "native_watcher_unavailable", error=str(exc))

    watcher_mode = event_source.name if event_source else f"低频轮询（{poll_seconds:g} 秒）"
    now = time.monotonic()
    pending = bool(input_snapshot(root))
    next_stability = now if pending else float("inf")
    next_reconcile = now + reconcile_seconds
    next_poll = now + poll_seconds
    next_email = 0.0
    email_interval = max(60, float(config["email"].get("poll_seconds", 300)))
    append_activity(
        root,
        "watcher_started",
        device=os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME"),
        mode=watcher_mode,
    )
    print(f"ClaimMate 正在监听：{root / core.INBOX}（{watcher_mode}）")
    try:
        while not stopped.is_set():
            now = time.monotonic()
            try:
                if not no_email and now >= next_email:
                    results = sync_email(root, config)
                    if results.get("downloaded"):
                        rendered = run_core_check(root)
                        if rendered:
                            print(rendered)
                        pending = False
                        tracker.reset()
                    next_email = now + email_interval

                if now >= next_reconcile:
                    if input_snapshot(root):
                        pending = True
                        next_stability = min(next_stability, now)
                    next_reconcile = now + reconcile_seconds

                if pending and now >= next_stability:
                    snapshot = input_snapshot(root)
                    if not snapshot:
                        pending = False
                        tracker.reset()
                        next_stability = float("inf")
                    elif tracker.observe(snapshot):
                        rendered = run_core_check(root)
                        if rendered:
                            print(rendered)
                        pending = False
                        tracker.reset()
                        next_stability = float("inf")
                        next_reconcile = now + reconcile_seconds
                    else:
                        next_stability = now + settle_seconds
            except Exception as exc:
                append_activity(root, "watcher_error", error=str(exc))
                print(f"ClaimMate 自动处理失败：{exc}", file=sys.stderr)

            now = time.monotonic()
            deadlines = [next_reconcile, now + 5]
            if not no_email:
                deadlines.append(next_email)
            if pending:
                deadlines.append(next_stability)
            if event_source is None:
                deadlines.append(next_poll)
            timeout = max(0.0, min(deadlines) - now)

            changed = False
            if event_source is not None:
                try:
                    changed = event_source.wait(timeout)
                except OSError as exc:
                    append_activity(root, "native_watcher_failed", error=str(exc))
                    event_source.close()
                    event_source = None
                    next_poll = time.monotonic()
                    print(f"原生监听不可用，已切换为{poll_seconds:g}秒低频轮询。", file=sys.stderr)
            else:
                stopped.wait(timeout)
                now = time.monotonic()
                if now >= next_poll:
                    changed = True
                    next_poll = now + poll_seconds

            if changed and not stopped.is_set():
                pending = True
                next_stability = time.monotonic() + settle_seconds
    finally:
        if event_source is not None:
            event_source.close()
        append_activity(root, "watcher_stopped")
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink()
        except FileNotFoundError:
            pass


def runtime_command(root: Path) -> list[str]:
    runtime_script = automation_paths(root)["runtime"] / "scripts" / "automation.py"
    return [sys.executable, str(runtime_script), "watch", str(root)]


def windows_background_command(root: Path) -> list[str]:
    command = runtime_command(root)
    windowless_python = Path(command[0]).with_name("pythonw.exe")
    if windowless_python.is_file():
        command[0] = str(windowless_python)
    return command


def windows_run_entry_name(root: Path) -> str:
    return windows_task_name(root)


def set_windows_run_entry(root: Path, command: list[str]) -> None:
    if winreg is None:
        raise OSError("Windows registry support is unavailable.")
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(
            key,
            windows_run_entry_name(root),
            0,
            winreg.REG_SZ,
            subprocess.list2cmdline(command),
        )


def get_windows_run_entry(root: Path) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, windows_run_entry_name(root))
            return str(value)
    except FileNotFoundError:
        return None


def delete_windows_run_entry(root: Path) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, windows_run_entry_name(root))
        return True
    except FileNotFoundError:
        return False


def windows_watcher_running(root: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        pid = int(automation_paths(root)["watcher_pid"].read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return False
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def start_windows_watcher(root: Path) -> int:
    if windows_watcher_running(root):
        return int(automation_paths(root)["watcher_pid"].read_text(encoding="ascii").strip())
    paths = automation_paths(root)
    command = windows_background_command(root)
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    with paths["watcher_log"].open("ab") as stdout, paths["watcher_error"].open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            close_fds=True,
        )
    paths["watcher_pid"].write_text(str(process.pid), encoding="ascii")
    return process.pid


def stop_windows_watcher(root: Path) -> bool:
    paths = automation_paths(root)
    try:
        pid = int(paths["watcher_pid"].read_text(encoding="ascii").strip())
    except (FileNotFoundError, OSError, ValueError):
        return False
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True
    )
    paths["watcher_pid"].unlink(missing_ok=True)
    return result.returncode == 0


def copy_runtime(root: Path) -> Path:
    runtime = automation_paths(root)["runtime"]
    scripts = runtime / "scripts"
    assets = runtime / "assets"
    references = runtime / "skills" / "claimmate" / "references"
    scripts.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    references.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), scripts / "automation.py")
    core_wrapper = Path(core.__file__).resolve()
    shutil.copy2(core_wrapper, scripts / "claimmate.py")
    source_package = core_wrapper.parents[3] / "scripts" / "claimmate_core"
    if not source_package.is_dir():
        source_package = core_wrapper.parent / "claimmate_core"
    if not source_package.is_dir():
        raise SystemExit("ClaimMate 模块化运行时缺失，无法安装后台监听器。")
    shutil.copytree(
        source_package,
        scripts / "claimmate_core",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    ocr_helper = core_wrapper.with_name("windows_ocr.ps1")
    if ocr_helper.is_file():
        shutil.copy2(ocr_helper, scripts / "windows_ocr.ps1")
    shutil.copy2(core.config_template_path(), assets / "config.example.json")
    model_schema = core.model_output_schema_path()
    if model_schema.is_file():
        shutil.copy2(model_schema, assets / "model-output.schema.json")
    requirements_schema = core.requirements_schema_path()
    if requirements_schema.is_file():
        shutil.copy2(requirements_schema, assets / requirements_schema.name)
    requirements_template = core.requirements_template_path()
    if requirements_template.is_file():
        shutil.copy2(requirements_template, assets / requirements_template.name)
    quick_start = core.bundled_reference_path("quick-start.md")
    if quick_start.is_file():
        shutil.copy2(quick_start, references / quick_start.name)
    core.hide_internal_folder(root / core.META)
    return runtime


def service_key(root: Path) -> str:
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:10]
    return f"claimmate-{digest}"


def windows_task_name(root: Path) -> str:
    return f"ClaimMate-{service_key(root).split('-')[-1]}"


def launchd_label(root: Path) -> str:
    return f"com.claimmate.watcher.{service_key(root).split('-')[-1]}"


def launchd_environment() -> dict[str, str]:
    """Preserve the Codex CLI location for launchd's minimal environment."""
    environment: dict[str, str] = {}
    path_entries: list[str] = []
    codex_command = core.find_codex_command()
    if codex_command:
        located = shutil.which(codex_command) or codex_command
        command_path = Path(located).expanduser()
        if command_path.is_file():
            absolute_command = str(command_path.absolute())
            environment["CLAIMMATE_CODEX_COMMAND"] = absolute_command
            path_entries.append(str(command_path.absolute().parent))

    path_entries.extend(os.environ.get("PATH", "").split(os.pathsep))
    path_entries.extend([
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ])
    environment["PATH"] = os.pathsep.join(
        dict.fromkeys(entry for entry in path_entries if entry)
    )
    return environment


def launchd_plist(root: Path, command: list[str]) -> dict[str, Any]:
    paths = automation_paths(root)
    return {
        "Label": launchd_label(root),
        "ProgramArguments": command,
        "EnvironmentVariables": launchd_environment(),
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(paths["watcher_log"]),
        "StandardErrorPath": str(paths["watcher_error"]),
    }


def install_service(root: Path) -> str:
    require_workspace(root)
    copy_runtime(root)
    command = runtime_command(root)
    if sys.platform == "win32":
        task_name = windows_task_name(root)
        task_command = subprocess.list2cmdline(command)
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/SC", "ONLOGON", "/TR", task_command, "/F"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True)
            return f"Windows 后台任务已安装并启动：{task_name}"
        scheduler_error = result.stderr.strip() or result.stdout.strip()
        append_activity(root, "task_scheduler_install_failed", error=scheduler_error)
        command = windows_background_command(root)
        set_windows_run_entry(root, command)
        pid = start_windows_watcher(root)
        return (
            f"Windows 任务计划程序不可用，已改用当前用户启动项并启动监听："
            f"{windows_run_entry_name(root)}（PID {pid}）"
        )
    if sys.platform == "darwin":
        label = launchd_label(root)
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with plist_path.open("wb") as handle:
            plistlib.dump(launchd_plist(root, command), handle)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)], capture_output=True)
        result = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return f"macOS 后台任务已安装并启动：{label}"
    raise RuntimeError("当前仅支持 Windows 和 macOS 后台启动。")


def uninstall_service(root: Path) -> str:
    if sys.platform == "win32":
        task_name = windows_task_name(root)
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, text=True
        )
        scheduler_text = (result.stderr + result.stdout).lower()
        if result.returncode != 0 and not any(
            value in scheduler_text for value in ("cannot find", "找不到", "does not exist")
        ) and get_windows_run_entry(root) is None:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        removed_run_entry = delete_windows_run_entry(root)
        stopped = stop_windows_watcher(root)
        mode = "；当前用户启动项已移除" if removed_run_entry else ""
        process = "；后台进程已停止" if stopped else ""
        return f"Windows 后台任务已移除：{task_name}{mode}{process}"
    if sys.platform == "darwin":
        label = launchd_label(root)
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        domain = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", domain, str(plist_path)], capture_output=True)
        plist_path.unlink(missing_ok=True)
        return f"macOS 后台任务已移除：{label}"
    raise RuntimeError("当前仅支持 Windows 和 macOS 后台启动。")


def service_status(root: Path) -> str:
    if sys.platform == "win32":
        task_name = windows_task_name(root)
        result = subprocess.run(["schtasks", "/Query", "/TN", task_name], capture_output=True, text=True)
        if result.returncode == 0:
            return "已安装（任务计划程序）"
        if get_windows_run_entry(root) is not None:
            running = "并运行" if windows_watcher_running(root) else "，等待下次登录启动"
            return f"已安装（当前用户启动项{running}）"
        return "未安装"
    if sys.platform == "darwin":
        label = launchd_label(root)
        domain = f"gui/{os.getuid()}"
        result = subprocess.run(["launchctl", "print", f"{domain}/{label}"], capture_output=True)
        return "已安装" if result.returncode == 0 else "未安装"
    return "不支持"


def command_watch(args: argparse.Namespace) -> None:
    watch_loop(Path(args.folder).expanduser().resolve(), args.once, args.no_email)


def command_service_install(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    require_workspace(root)
    _, workspace_config = core.load_project_registry(root)
    core.require_setup_ready(root, workspace_config)
    print(install_service(root))
    print(setup_guidance(root))


def command_service_uninstall(args: argparse.Namespace) -> None:
    print(uninstall_service(Path(args.folder).expanduser().resolve()))


def command_service_status(args: argparse.Namespace) -> None:
    print(service_status(Path(args.folder).expanduser().resolve()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClaimMate 跨平台自动采集器")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="配置文件监听和邮箱采集")
    configure.add_argument("folder")
    configure.add_argument("--email-provider", choices=sorted(EMAIL_PRESETS))
    configure.add_argument("--email-host")
    configure.add_argument("--email-port", type=int)
    configure.add_argument("--email-username")
    configure.add_argument("--email-mailbox")
    configure.add_argument("--email-interval", type=int)
    configure.add_argument("--password-env")
    configure.add_argument("--enable-email", action="store_true")
    configure.add_argument("--disable-email", action="store_true")
    configure.set_defaults(handler=command_configure)

    email_sync = subparsers.add_parser("email-sync", help="立即检查一次邮箱附件")
    email_sync.add_argument("folder")
    email_sync.add_argument("--dry-run", action="store_true")
    email_sync.set_defaults(handler=command_email_sync)

    for name, handler, help_text in (
        ("credentials-set", command_credentials_set, "将邮箱凭据保存到系统安全凭据库"),
        ("credentials-delete", command_credentials_delete, "从系统安全凭据库移除邮箱凭据"),
    ):
        child = subparsers.add_parser(name, help=help_text)
        child.add_argument("folder")
        child.set_defaults(handler=handler)

    watch = subparsers.add_parser("watch", help="持续监听文件夹并定时检查邮箱")
    watch.add_argument("folder")
    watch.add_argument("--once", action="store_true", help="只执行一次补扫")
    watch.add_argument("--no-email", action="store_true")
    watch.set_defaults(handler=command_watch)

    for name, handler, help_text in (
        ("service-install", command_service_install, "安装并启动后台任务"),
        ("service-uninstall", command_service_uninstall, "移除后台任务"),
        ("service-status", command_service_status, "查看后台任务状态"),
    ):
        child = subparsers.add_parser(name, help=help_text)
        child.add_argument("folder")
        child.set_defaults(handler=handler)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        args.handler(args)
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, imaplib.IMAP4.error) as exc:
        print(f"ClaimMate 自动采集失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
