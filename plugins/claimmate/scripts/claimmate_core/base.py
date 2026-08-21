#!/usr/bin/env python3
"""ClaimMate: model-assisted reimbursement organizer with deterministic safeguards."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape


VERSION = 10
LAYOUT_VERSION = 5
FEEDBACK_VERSION = 1
INBOX = "待处理"
ACTIVE = "流程中"
FINISHED = "已结束"
REVIEW_FOLDER = "待确认"
UNASSIGNED_FOLDER = "待归属"
META = ".claimmate"
GUIDE = "开始使用 ClaimMate.md"
PROJECTS_STATE = "projects.json"
FINANCE_FEEDBACK_STATE = "finance-feedback.json"
INBOX_ALIASES = ("00_收件箱", "新材料", "收件箱")
CATEGORY_ALIASES = {
    "01_交通": "交通",
    "02_住宿": "住宿",
    "03_餐饮": "餐饮",
    "04_注册费": "注册费",
    "06_办公及打印": "打印费",
    "90_其他": "其他",
}
OBSOLETE_DAILY_CATEGORY_KEYS = {
    "publication", "office", "equipment", "software", "postage", "labor",
}
ELIGIBLE_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
    ".txt", ".md", ".csv", ".json", ".doc", ".docx", ".xls", ".xlsx",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
OCR_TIMEOUT_SECONDS = 45
OCR_MAX_BYTES = 50 * 1024 * 1024
MODEL_PROVIDER = "codex-cli"
MODEL_DECISION_SOURCE = "large-language-model"
SETUP_EMAIL_CHOICES = {"connect", "skip"}
REQUIREMENTS_WORKBOOK_NAME = "报销要求.xlsx"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def safe_name(value: str, fallback: str = "未识别") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" ._")
    value = re.sub(r"\s+", "", value)
    return value[:60] or fallback


def relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def absolute(root: Path, value: str) -> Path:
    return root.joinpath(*PurePosixPath(value.replace("\\", "/")).parts)


@contextmanager
def workspace_operation_lock(root: Path, timeout: float = 30.0):
    lock_path = root / META / "operation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{timestamp()}"
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 15 * 60:
                    lock_path.unlink()
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise SystemExit("ClaimMate 正在处理这个文件夹，请稍后重试。")
            time.sleep(0.25)
    try:
        yield
    finally:
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass


def locked_command(handler):
    @wraps(handler)
    def wrapper(args: argparse.Namespace):
        root = Path(args.folder).expanduser().resolve()
        if not root.exists() or getattr(args, "dry_run", False):
            return handler(args)
        with workspace_operation_lock(root):
            return handler(args)
    return wrapper


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def config_template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "config.example.json"


def model_output_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "model-output.schema.json"


def bundled_reference_path(name: str) -> Path:
    """Resolve source references without coupling runtime copies to skill layout."""
    bundle = Path(__file__).resolve().parents[2]
    return bundle / "skills" / "claimmate" / "references" / name


def compatibility_script_path(name: str) -> Path:
    """Locate thin CLI wrappers in either the plugin source or copied runtime."""
    bundle = Path(__file__).resolve().parents[2]
    source = bundle / "skills" / "claimmate" / "scripts" / name
    return source if source.is_file() else bundle / "scripts" / name


def default_config() -> dict[str, Any]:
    return read_json(config_template_path())


def ensure_config_defaults(config: dict[str, Any]) -> bool:
    """Add runtime defaults and remove obsolete rule-classification settings."""
    changed = False
    defaults = default_config()
    scope = config.get("scope")
    if not isinstance(scope, dict):
        config["scope"] = copy.deepcopy(defaults["scope"])
        changed = True
    else:
        for key, value in defaults["scope"].items():
            if scope.get(key) != value:
                scope[key] = copy.deepcopy(value)
                changed = True
    claimant = config.get("claimant")
    if not isinstance(claimant, dict):
        config["claimant"] = copy.deepcopy(defaults["claimant"])
        changed = True
    else:
        for key, value in defaults["claimant"].items():
            if key not in claimant:
                claimant[key] = copy.deepcopy(value)
                changed = True
    setup = config.get("setup")
    if not isinstance(setup, dict):
        config["setup"] = copy.deepcopy(defaults["setup"])
        changed = True
    else:
        for key, value in defaults["setup"].items():
            if key not in setup:
                setup[key] = copy.deepcopy(value)
                changed = True
    if "model_processing" not in config:
        config["model_processing"] = copy.deepcopy(defaults["model_processing"])
        changed = True
    else:
        model_config = config["model_processing"]
        for key, value in defaults["model_processing"].items():
            if key not in model_config:
                model_config[key] = copy.deepcopy(value)
                changed = True
    if "role_keywords" in config:
        del config["role_keywords"]
        changed = True
    categories = config.setdefault("categories", {})
    for key in OBSOLETE_DAILY_CATEGORY_KEYS:
        if key in categories:
            del categories[key]
            changed = True
    for key, value in defaults.get("categories", {}).items():
        if key not in categories:
            categories[key] = copy.deepcopy(value)
            changed = True
        else:
            for field, field_value in value.items():
                if field not in categories[key]:
                    categories[key][field] = copy.deepcopy(field_value)
                    changed = True
    for category in categories.values():
        if "keywords" in category:
            del category["keywords"]
            changed = True
    return changed


def claimant_name(config: dict[str, Any]) -> str:
    claimant = config.get("claimant")
    if not isinstance(claimant, dict):
        return ""
    return str(claimant.get("name") or "").strip()


def set_claimant_name(config: dict[str, Any], name: str) -> str:
    cleaned = safe_name(name, "")
    if not cleaned:
        raise SystemExit("使用者姓名不能为空。")
    config["claimant"] = {
        "name": cleaned,
        "updated_at": now_iso(),
        "source": "explicit-user-confirmation",
    }
    return cleaned


def setup_state(config: dict[str, Any]) -> dict[str, Any]:
    setup = config.get("setup")
    if not isinstance(setup, dict):
        setup = copy.deepcopy(default_config()["setup"])
        config["setup"] = setup
    return setup


def record_email_intake_choice(config: dict[str, Any], choice: str) -> str:
    normalized = str(choice or "").strip().casefold()
    if normalized not in SETUP_EMAIL_CHOICES:
        raise SystemExit("请选择是否接入邮箱：connect（接入）或 skip（暂不接入）。")
    setup = setup_state(config)
    setup["email_intake_choice"] = normalized
    setup["email_choice_confirmed_at"] = now_iso()
    return normalized


def email_intake_choice_label(config: dict[str, Any]) -> str:
    choice = setup_state(config).get("email_intake_choice")
    return {"connect": "接入邮箱", "skip": "暂不接入邮箱"}.get(str(choice), "尚未选择")


def email_intake_is_configured(root: Path) -> bool:
    path = root / META / "automation.json"
    if not path.is_file():
        return False
    try:
        email_config = read_json(path).get("email", {})
    except (OSError, ValueError, TypeError):
        return False
    return bool(
        isinstance(email_config, dict)
        and email_config.get("enabled")
        and str(email_config.get("username") or "").strip()
    )


def requirements_workbook_hash(root: Path) -> str | None:
    workbook = root / REQUIREMENTS_WORKBOOK_NAME
    return sha256(workbook) if workbook.is_file() else None


def requirements_confirmation_is_current(root: Path, config: dict[str, Any]) -> bool:
    setup = setup_state(config)
    expected = str(setup.get("requirements_sha256") or "")
    current = requirements_workbook_hash(root)
    return bool(setup.get("requirements_confirmed_at") and expected and current == expected)


def record_requirements_confirmation(
    root: Path,
    config: dict[str, Any],
    rows: list[dict[str, str]],
    source: str = "explicit-user-confirmation",
) -> None:
    digest = requirements_workbook_hash(root)
    if not digest:
        raise SystemExit(f"缺少 {REQUIREMENTS_WORKBOOK_NAME}，无法确认报销要求。")
    setup = setup_state(config)
    confirmed_at = now_iso()
    setup["requirements_confirmed_at"] = confirmed_at
    setup["requirements_sha256"] = digest
    setup["requirements_rows"] = copy.deepcopy(rows)
    setup["requirements_confirmation_source"] = source
    if claimant_name(config) and setup.get("email_intake_choice") in SETUP_EMAIL_CHOICES:
        setup["completed_at"] = confirmed_at


def setup_blocker(root: Path, config: dict[str, Any]) -> str | None:
    if not claimant_name(config):
        return "尚未确认使用者姓名"
    if setup_state(config).get("email_intake_choice") not in SETUP_EMAIL_CHOICES:
        return "尚未确认是否接入邮箱"
    if not requirements_confirmation_is_current(root, config):
        return "报销要求.xlsx 尚未确认，或确认后又发生了修改"
    return None


def require_setup_ready(root: Path, config: dict[str, Any]) -> None:
    blocker = setup_blocker(root, config)
    if blocker:
        raise SystemExit(
            f"ClaimMate 首次配置尚未完成：{blocker}。"
            "请先查看当前 Scheme，明确确认后再开始处理材料。"
        )


def migrate_legacy_setup(root: Path, config: dict[str, Any]) -> None:
    """Keep existing workspaces operational while making new setup state explicit."""
    setup = setup_state(config)
    if setup.get("completed_at"):
        return
    automation_path = root / META / "automation.json"
    email_enabled = False
    if automation_path.is_file():
        try:
            email_enabled = bool(read_json(automation_path).get("email", {}).get("enabled"))
        except (OSError, ValueError, TypeError):
            email_enabled = False
    setup["email_intake_choice"] = "connect" if email_enabled else "skip"
    setup["email_choice_confirmed_at"] = now_iso()
    digest = requirements_workbook_hash(root)
    if digest:
        try:
            from .policy import user_requirement_rows

            rows = user_requirement_rows(root / REQUIREMENTS_WORKBOOK_NAME)
        except (ImportError, SystemExit):
            rows = []
        setup["requirements_confirmed_at"] = now_iso()
        setup["requirements_sha256"] = digest
        setup["requirements_rows"] = rows
        setup["requirements_initial_sha256"] = digest
        setup["requirements_initial_rows"] = copy.deepcopy(rows)
        setup["requirements_confirmation_source"] = "legacy-workspace-migration"
        if claimant_name(config):
            setup["completed_at"] = now_iso()


def metadata_paths(root: Path) -> dict[str, Path]:
    meta = root / META
    return {
        "meta": meta,
        "state": meta / "state.json",
        "projects": meta / PROJECTS_STATE,
        "finance_feedback": meta / FINANCE_FEEDBACK_STATE,
        "feedback_sources": meta / "feedback-sources",
        "config": meta / "config.json",
        "originals": meta / "originals",
        "transactions": meta / "transactions",
        "history": meta / "history.jsonl",
    }


def new_state(project_id: str | None = None, case_name: str | None = None) -> dict[str, Any]:
    now = now_iso()
    return {
        "version": VERSION,
        "layout_version": LAYOUT_VERSION,
        "claim_type": "business_travel",
        "project_id": project_id,
        "claim_id": f"CLM-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "case_name": case_name,
        "status": "collecting",
        "initialized_at": now,
        "updated_at": now,
        "next_expense_number": 1,
        "documents": {},
        "duplicates": [],
        "expenses": {},
    }


def new_project_registry() -> dict[str, Any]:
    now = now_iso()
    return {
        "version": VERSION,
        "claim_type": "business_travel",
        "initialized_at": now,
        "updated_at": now,
        "next_project_number": 1,
        "active_project_id": None,
        "projects": {},
        "unassigned_documents": {},
        "duplicates": [],
    }


def new_finance_feedback_registry() -> dict[str, Any]:
    now = now_iso()
    return {
        "version": FEEDBACK_VERSION,
        "initialized_at": now,
        "updated_at": now,
        "next_feedback_number": 1,
        "entries": [],
    }


def load_finance_feedback(root: Path) -> dict[str, Any]:
    path = metadata_paths(root)["finance_feedback"]
    if not path.exists():
        return new_finance_feedback_registry()
    registry = read_json(path)
    registry.setdefault("entries", [])
    registry.setdefault("next_feedback_number", len(registry["entries"]) + 1)
    registry["version"] = FEEDBACK_VERSION
    return registry


def save_finance_feedback(root: Path, registry: dict[str, Any]) -> None:
    registry["version"] = FEEDBACK_VERSION
    registry["updated_at"] = now_iso()
    write_json(metadata_paths(root)["finance_feedback"], registry)
    hide_internal_folder(metadata_paths(root)["meta"])


def save_project_registry(root: Path, registry: dict[str, Any]) -> None:
    registry["version"] = VERSION
    registry["updated_at"] = now_iso()
    paths = metadata_paths(root)
    write_json(paths["projects"], registry)
    active_id = registry.get("active_project_id")
    active = registry.get("projects", {}).get(active_id)
    if active:
        write_json(paths["state"], active)


def load_project_registry(
    root: Path, create: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = metadata_paths(root)
    config = read_json(paths["config"]) if paths["config"].exists() else default_config()
    setup_was_missing = not isinstance(config.get("setup"), dict)
    config_changed = ensure_config_defaults(config)
    if setup_was_missing and paths["config"].exists() and (
        paths["projects"].exists() or paths["state"].exists()
    ):
        migrate_legacy_setup(root, config)
        config_changed = True
    if config_changed and paths["config"].exists():
        write_json(paths["config"], config)
    if paths["projects"].exists():
        registry = read_json(paths["projects"])
        registry_changed = registry.get("claim_type") != "business_travel"
        registry["claim_type"] = "business_travel"
        registry.setdefault("projects", {})
        registry.setdefault("unassigned_documents", {})
        registry.setdefault("duplicates", [])
        registry.setdefault("next_project_number", len(registry["projects"]) + 1)
        for project_id, project in registry["projects"].items():
            if (
                project.get("project_id") != project_id
                or project.get("version") != VERSION
                or project.get("claim_type") != "business_travel"
            ):
                registry_changed = True
            project["project_id"] = project_id
            project["version"] = VERSION
            project["claim_type"] = "business_travel"
        if registry_changed:
            save_project_registry(root, registry)
        return registry, config
    if paths["state"].exists():
        project = read_json(paths["state"])
        project_id = project.get("project_id") or "PRJ-001"
        project["project_id"] = project_id
        project["version"] = VERSION
        project["claim_type"] = "business_travel"
        registry = new_project_registry()
        registry["projects"][project_id] = project
        registry["active_project_id"] = project_id
        registry["next_project_number"] = 2
        save_project_registry(root, registry)
        return registry, config
    if not create:
        raise SystemExit(f"该文件夹尚未初始化：{root}\n请先运行 init。")
    return new_project_registry(), config


def project_matches(registry: dict[str, Any], query: str) -> list[dict[str, Any]]:
    normalized = normalize(query)
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for project_id, project in registry.get("projects", {}).items():
        case_name = normalize(str(project.get("case_name", "")))
        if normalized in {normalize(project_id), case_name}:
            exact.append(project)
        elif normalized and (normalized in case_name or case_name in normalized):
            partial.append(project)
    return exact or partial


def select_project(
    registry: dict[str, Any], query: str | None = None, include_archived: bool = True
) -> dict[str, Any]:
    projects = registry.get("projects", {})
    if not projects:
        raise SystemExit("当前还没有出差报销项目，请先新建一次出差。")
    if query:
        matches = project_matches(registry, query)
        if not include_archived:
            matches = [item for item in matches if item.get("status") != "archived"]
        if not matches:
            raise SystemExit(f"没有找到报销项目：{query}")
        if len(matches) > 1:
            names = "、".join(str(item.get("case_name")) for item in matches)
            raise SystemExit(f"项目名称不够明确，匹配到：{names}")
        return matches[0]
    active_id = registry.get("active_project_id")
    active = projects.get(active_id)
    if active and (include_archived or active.get("status") != "archived"):
        return active
    candidates = [
        item for item in projects.values() if include_archived or item.get("status") != "archived"
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit("当前有多个报销项目，请说明项目名称。")


def require_named_project(registry: dict[str, Any], query: str | None, action: str) -> None:
    open_projects = [
        item for item in registry.get("projects", {}).values()
        if item.get("status") != "archived"
    ]
    if len(open_projects) > 1 and not query:
        raise SystemExit(f"当前有多个报销项目，请说明要{action}哪个项目。")


def create_project(registry: dict[str, Any], case_name: str) -> dict[str, Any]:
    if any(normalize(str(item.get("case_name"))) == normalize(case_name) for item in registry["projects"].values()):
        raise SystemExit(f"报销项目已存在：{case_name}")
    number = int(registry.get("next_project_number", 1))
    project_id = f"PRJ-{number:03d}"
    registry["next_project_number"] = number + 1
    project = new_state(project_id, safe_name(case_name, "报销事项"))
    registry["projects"][project_id] = project
    registry["active_project_id"] = project_id
    return project


def load_workspace(
    root: Path, create: bool = False, project: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry, config = load_project_registry(root, create=create)
    if not registry.get("projects"):
        if not create:
            raise SystemExit("当前还没有出差报销项目，请先新建一次出差。")
        return new_state(), config
    return select_project(registry, project), config


def hide_internal_folder(path: Path) -> None:
    if os.name != "nt" or not path.exists():
        return
    try:
        import ctypes
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attributes != -1:
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes | 0x02)
    except Exception:
        pass


def guide_text(case_name: str, root: Path | None = None) -> str:
    reference = bundled_reference_path("quick-start.md")
    if reference.exists():
        content = reference.read_text(encoding="utf-8").strip()
        if root is not None:
            inbox_path = str((root / INBOX).resolve())
            unassigned_path = str((root / INBOX / UNASSIGNED_FOLDER).resolve())
            content = content.replace("`待处理/待归属`", f"`{unassigned_path}`")
            content = content.replace("`待处理`", f"`{inbox_path}`")
        if content.startswith("# "):
            content = content.split("\n", 1)[1].lstrip()
        return (
            "# 开始使用 ClaimMate\n\n"
            f"最近新建或使用的项目：`{case_name}`\n\n"
            f"{content}\n"
        )
    return f"""# 开始使用 ClaimMate

这是一份可以直接照着操作的完整说明。你不需要整理文件名、记命令或提前分类，只要把材料交给 ClaimMate，并在关键节点说明你的意图。

最近新建或使用的项目：`{case_name}`

## 先记住三件事

1. **一个工作区可以同时管理多个出差报销项目。** 北京出差、南京出差和合肥出差可以一起进行。
2. **材料直接交过来即可。** 可以发在对话里、放进 `待处理`，也可以让 ClaimMate 从邮箱获取。
3. **准备提交时一定要说明项目。** 例如：**北京出差报销要交给财务了**。这时才会集中检查缺失和不确定项。

## 第一次使用：先完成配置

ClaimMate 会先确认使用者姓名、是否接入邮箱，并展示完整的三列 `报销要求.xlsx`。Scheme 未确认前只建立目录，不识别、移动或重命名材料；确认后才启动监听器并显示正常引导。以后直接修改工作簿时，也需要重新展示和确认。

### 第一步：新建报销项目

直接说自然语言即可：

- **新建一个 6 月北京出差报销**
- **新建 7 月合肥出差报销**
- **新建 ICRA 参会出差报销**

只知道月份或地点也可以开始。得到准确日期后，再说：**把北京项目改成 2026-06-12 至 06-15**。

### 第二步：把材料交给 ClaimMate

有三种方式，可以混合使用：

1. **在对话中直接发送**：发票、付款截图、行程单、订单、会议通知等都可以直接发。
2. **放进文件夹**：把文件复制到工作区的 `待处理`。首次 Scheme 确认后会默认安装监听器，文件稳定写入后便会自动检查。
3. **从邮箱获取**：说 **接入邮箱**，完成一次安全配置后，符合条件的附件会进入同一处理流程。

不需要先改名，也不需要按餐饮、住宿、交通手动建文件夹。

### 第三步：平时继续收集

- 多个项目的材料可以交叉发送，不需要切换“当前项目”。
- 发送时顺口说明上下文会更准，例如：**这是南京出差的酒店付款记录**。
- 后台大模型会结合消息和材料内容，逐份判断项目归属、费用名称并匹配同一笔费用。
- 暂时无法判断项目的文件会留在 `待处理/待归属`；项目已确定但类型或配对不清楚的文件会直接留在项目根目录，并在文件名前标明待确认原因。
- 收集过程中不会因为一时缺材料就频繁追问，后续线索到达后会继续重算。

## 每笔费用需要什么

默认情况下，每笔可报销费用至少需要：

- **发票**
- **付款记录**

订单、行程单、会议通知、录用通知、住宿水单等属于补充材料。同一笔费用使用相同的 `EXP-###` 编号和简洁费用名称，例如 `EXP-001_机票_1551元_发票.pdf` 与 `EXP-001_机票_1551元_付款记录.jpg`。项目目录始终保持扁平，不再套分类或单笔费用文件夹。

常见出差费用包括机票、高铁票、出租车票、住宿费、餐费、注册费、会员费、打印费、签证费和旅行保险。与具体出差无关的设备、软件、快递、劳务、论文出版等日常报销不会进入出差项目。

## 同时处理多个项目

- 说 **查看我的报销项目**，可以看到所有进行中和已结束的项目。
- 交叉发送材料时，ClaimMate 会逐文件判断，不会把整批文件盲目放进最近使用的项目。
- 如果你发现归属有误，可以说：**刚才三份都是南京出差的**，或明确指出文件和目标项目。
- 如果线索仍不足，材料会继续等待，不会为了“整理完”而猜测。

## 更新财务反馈

收到财务的文字、邮件、截图或 PDF 后，说：**这是财务反馈，请更新报销要求**。

ClaimMate 会用大模型提取费用类型、必须材料和财务其他要求，再询问它是“以后都适用”还是“只针对这次出差”，并在写入前展示修改内容：

- **以后都适用**：用户确认后，先备份再更新 `报销要求.xlsx`，重新验证并说明具体修改；
- **只针对这次出差**：用户确认后，保存为该项目的临时要求，不修改全局 Scheme；
- 两种情况都保存财务原话、来源、时间、附件原件、确认范围和变更记录；
- 没有明确确认时，不修改 Scheme，也不建立阻断要求。

项目临时要求过期后，可以说：**停用 FB-001，财务政策已经更新**。已经写入 `报销要求.xlsx` 的全局要求必须通过新的、经过预览和确认的 Scheme 变更来修改；旧记录仍会保留用于追溯。

## 准备交给财务

出差或事项结束后，明确说出项目：

> 北京出差报销要交给财务了。

ClaimMate 会先吸收最后一批材料并重新推理，然后一次性给出：

- 已识别的费用清单；
- 缺少发票或付款记录的费用；
- 仍无法确定的材料和可能属于该项目的待归属文件；
- 重复文件；
- 依据历史财务反馈仍需补充的证据。

补齐后再次说同一句即可重新核验。核验通过后，ClaimMate 会生成明细并自动归档为 `时间-地点-报销人-报销文件.zip`，然后告知最终路径。

ClaimMate 会准备和归档材料，但**不会未经确认替你提交到财务系统**。

## 需要纠正或查看时

- **查看我的报销项目**：查看全部项目。
- **查看北京出差报销状态**：提前查看某个项目，不必等到提交时。
- **刚才三份都是南京项目的**：批量纠正归属。
- **撤销上次整理**：恢复最近一次文件整理。
- **停用 FB-001**：停用已过期的项目临时要求并保留历史；全局 Scheme 要求需另行确认修改。
- **北京出差报销要交给财务了**：核验、导出、打包并移入 `已结束`。

## 自动处理

- 姓名、邮箱选择和完整 Scheme 全部确认后，才会默认安装并启动后台监听器；成功后无需再手动开启。
- 如果自动安装失败或之前明确关闭过，说 **开启自动处理** 可以重试。
- 说 **查看自动处理状态**：确认监听器是否工作。
- 说 **关闭自动处理**：移除后台任务。
- 说 **接入邮箱**：配置附件采集。密码或应用专用密码只进入系统安全凭据库，不写入工作区配置。

电脑休眠时不会即时处理；唤醒后会补扫遗漏。普通空闲监听不会持续高占用。

## 文件夹分别表示什么

- `待处理`：新材料入口。
- `待处理/待归属`：暂时无法确定属于哪个项目的材料。
- `流程中/项目名称`：正在收集或核验的项目。发票、付款记录、补充材料和待确认文件都直接放在这里，不建分类子目录。
- `已结束`：已经完成并归档的项目。
- `.claimmate`：隐藏的索引、原件备份、操作历史和财务反馈证据。不要手动修改。

## 一个完整示例

1. 说：**新建一个 6 月北京出差报销**。
2. 接下来几天陆续发送机票发票、付款截图、酒店发票、付款记录和餐费材料。
3. 中间穿插发送南京项目材料也没关系；必要时在消息里点明项目。
4. 财务说网约车必须有行程单时，把反馈转给 ClaimMate；选择适用范围，检查修改前后内容并明确确认。
5. 出差结束后说：**北京出差报销要交给财务了**。
6. 按一次性清单补齐材料，再次核验。
7. 通过后自动生成汇总、打包并归档。

## 安全和可恢复性

- 自动整理前会保留原件备份，最近一次整理可以撤销。
- 不会删除不确定或重复材料，也不会编造发票信息。
- 邮箱凭据不写入普通配置文件。
- 报销文件、个人信息和财务反馈都不应提交到代码仓库或公开分享。
- 自动处理只整理、核验、导出和归档；对外提交仍需要你的明确确认。

不需要记住命令。像平时对同事说话一样说明“新建、这是哪个项目、这是财务反馈、准备提交”即可。
"""


def ensure_workspace(root: Path, config: dict[str, Any], case_name: str) -> None:
    from .policy import ensure_requirements_workbook

    paths = metadata_paths(root)
    root.mkdir(parents=True, exist_ok=True)
    for folder in (
        root / INBOX,
        root / ACTIVE,
        root / FINISHED,
        paths["originals"],
        paths["transactions"],
        paths["feedback_sources"],
    ):
        folder.mkdir(parents=True, exist_ok=True)
    hide_internal_folder(paths["meta"])
    if not paths["config"].exists():
        write_json(paths["config"], config)
    ensure_requirements_workbook(root)
    if requirements_confirmation_is_current(root, config):
        guide = root / GUIDE
        content = guide_text(case_name, root)
        if not guide.exists() or guide.read_text(encoding="utf-8", errors="ignore") != content:
            guide.write_text(content, encoding="utf-8")


def default_case_name(root: Path, state: dict[str, Any]) -> str:
    date = str(state.get("initialized_at") or now_iso())[:10]
    subject = re.sub(r"(?i)claimmate|报销|材料|文件", "", root.name).strip(" _-")
    return safe_name(f"{date}_{subject or '报销事项'}")


def active_case_path(root: Path, state: dict[str, Any]) -> Path:
    return root / ACTIVE / state["case_name"]


def replace_path_prefix(value: str | None, mapping: dict[str, str]) -> str | None:
    if not value:
        return value
    normalized = value.replace("\\", "/")
    for prefix in sorted(mapping, key=len, reverse=True):
        if normalized == prefix:
            return mapping[prefix]
        if normalized.startswith(prefix + "/"):
            return mapping[prefix] + normalized[len(prefix):]
    return normalized


def migrate_state_paths(state: dict[str, Any], mapping: dict[str, str], case_name: str) -> None:
    for document in state.get("documents", {}).values():
        document["current_path"] = replace_path_prefix(document.get("current_path"), mapping)
    for duplicate in state.get("duplicates", []):
        duplicate["current_path"] = replace_path_prefix(duplicate.get("current_path"), mapping)
    state["archive_path"] = replace_path_prefix(state.get("archive_path"), mapping)
    state["case_name"] = case_name
    state["layout_version"] = LAYOUT_VERSION


def merge_or_rename_directory(source: Path, destination: Path) -> None:
    if not destination.exists():
        source.rename(destination)
        return
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir() and target.exists() and target.is_dir():
            merge_or_rename_directory(child, target)
        else:
            shutil.move(str(child), str(unique_destination(target)))
    source.rmdir()


def migration_mapping(case_name: str, stage: str) -> dict[str, str]:
    case_prefix = f"{stage}/{case_name}"
    mapping = {alias: INBOX for alias in INBOX_ALIASES}
    for old_name, category_name in CATEGORY_ALIASES.items():
        mapping[old_name] = f"{case_prefix}/{category_name}"
        mapping[f"{ACTIVE}/{old_name}"] = f"{case_prefix}/{category_name}"
    for category_name in set(CATEGORY_ALIASES.values()):
        mapping[category_name] = f"{case_prefix}/{category_name}"
        mapping[f"{ACTIVE}/{category_name}"] = f"{case_prefix}/{category_name}"
    mapping.update({
        "98_待确认": f"{case_prefix}/{REVIEW_FOLDER}",
        "待确认": f"{case_prefix}/{REVIEW_FOLDER}",
        f"{ACTIVE}/待确认": f"{case_prefix}/{REVIEW_FOLDER}",
        "99_输出": case_prefix,
        "报销汇总": case_prefix,
    })
    return mapping


def rewrite_transactions(root: Path, mapping: dict[str, str], case_name: str) -> None:
    paths = metadata_paths(root)
    for transaction_path in paths["transactions"].glob("TX-*.json"):
        transaction = read_json(transaction_path)
        before_state = transaction.get("before_state")
        if before_state:
            if "projects" in before_state:
                for project in before_state.get("projects", {}).values():
                    changed = any(
                        replace_path_prefix(document.get("current_path"), mapping)
                        != document.get("current_path")
                        for document in project.get("documents", {}).values()
                    )
                    if changed:
                        migrate_state_paths(project, mapping, case_name)
            else:
                migrate_state_paths(before_state, mapping, case_name)
        for operation in transaction.get("operations", []):
            operation["from"] = replace_path_prefix(operation.get("from"), mapping)
            operation["to"] = replace_path_prefix(operation.get("to"), mapping)
        write_json(transaction_path, transaction)


def move_legacy_contents(root: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for old_name, category_name in CATEGORY_ALIASES.items():
        for source in (root / old_name, root / ACTIVE / old_name):
            if source.exists() and source.is_dir():
                merge_or_rename_directory(source, destination / category_name)
    for category_name in set(CATEGORY_ALIASES.values()):
        for source in (root / category_name, root / ACTIVE / category_name):
            if source.exists() and source.is_dir():
                merge_or_rename_directory(source, destination / category_name)
    for source in (root / "98_待确认", root / "待确认", root / ACTIVE / "待确认"):
        if source.exists() and source.is_dir():
            merge_or_rename_directory(source, destination / REVIEW_FOLDER)
    for source in (root / "99_输出", root / "报销汇总"):
        if source.exists() and source.is_dir():
            merge_or_rename_directory(source, destination)
    active_root = root / ACTIVE
    if active_root.exists():
        for child in list(active_root.iterdir()):
            if child == destination or child.is_dir():
                continue
            shutil.move(str(child), str(unique_destination(destination / child.name)))


def flatten_project_workspace(root: Path, state: dict[str, Any]) -> dict[str, str]:
    """Move every visible project file into the project root and remove empty subdirectories."""
    # Imported lazily because filename formatting belongs to the intake module,
    # while legacy layout migration remains part of workspace storage.
    from .intake import expense_filename, unique_destination
    stage = FINISHED if state.get("status") == "archived" else ACTIVE
    project_path = root / stage / state["case_name"]
    project_path.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    moved_paths: set[str] = set()

    def relocate(source: Path, requested: Path) -> Path:
        if not source.is_file():
            return source
        try:
            if source.resolve() == requested.resolve():
                moved_paths.add(str(source.resolve()).casefold())
                return source
        except OSError:
            pass
        destination = unique_destination(requested)
        old_relative = relative(source, root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        new_relative = relative(destination, root)
        mapping[old_relative] = new_relative
        moved_paths.add(str(destination.resolve()).casefold())
        return destination

    for expense_id, expense in sorted(state.get("expenses", {}).items()):
        role_counts: dict[str, int] = {}
        for digest in expense.get("documents", []):
            record = state.get("documents", {}).get(digest)
            if not record or record.get("status") != "organized":
                continue
            source = absolute(root, record.get("current_path", ""))
            role = str(record.get("role") or "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1
            requested = project_path / expense_filename(
                expense_id,
                expense,
                role,
                source.suffix,
                role_counts[role],
            )
            destination = relocate(source, requested)
            record["current_path"] = relative(destination, root)

    for record in state.get("documents", {}).values():
        if record.get("status") == "organized":
            continue
        source = absolute(root, record.get("current_path", ""))
        destination = relocate(source, project_path / source.name)
        record["current_path"] = relative(destination, root)

    for duplicate in state.get("duplicates", []):
        source = absolute(root, duplicate.get("current_path", ""))
        destination = relocate(source, project_path / source.name)
        duplicate["current_path"] = relative(destination, root)

    for source in sorted(
        (path for path in project_path.rglob("*") if path.is_file()),
        key=lambda path: (len(path.parts), str(path).casefold()),
    ):
        if source.parent == project_path:
            continue
        if str(source.resolve()).casefold() in moved_paths:
            continue
        relocate(source, project_path / source.name)

    directories = sorted(
        (path for path in project_path.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
    state["layout_version"] = LAYOUT_VERSION
    return mapping


def migrate_workspace(root: Path, state: dict[str, Any], config: dict[str, Any]) -> bool:
    current_layout = int(state.get("layout_version", 1))
    if current_layout >= LAYOUT_VERSION:
        hide_internal_folder(metadata_paths(root)["meta"])
        return False
    case_name = safe_name(state.get("case_name") or default_case_name(root, state), "报销事项")
    stage = FINISHED if state.get("status") == "archived" else ACTIVE
    destination = root / stage / case_name
    paths = metadata_paths(root)
    if current_layout < 4:
        for old_name in INBOX_ALIASES:
            source = root / old_name
            if source.exists() and source.is_dir():
                merge_or_rename_directory(source, root / INBOX)
        move_legacy_contents(root, destination)
        for category in config.get("categories", {}).values():
            folder_name = Path(str(category.get("folder", category.get("label", "其他")))).name
            category["folder"] = CATEGORY_ALIASES.get(folder_name, folder_name)
        mapping = migration_mapping(case_name, stage)
        migrate_state_paths(state, mapping, case_name)
        rewrite_transactions(root, mapping, case_name)
    else:
        state["case_name"] = case_name

    flat_mapping = flatten_project_workspace(root, state)
    if flat_mapping:
        rewrite_transactions(root, flat_mapping, case_name)
    write_json(paths["config"], config)
    save_state(root, state)
    hide_internal_folder(paths["meta"])
    return True


def rename_case_workspace(root: Path, state: dict[str, Any], new_case_name: str) -> bool:
    old_case_name = state.get("case_name")
    if not old_case_name or old_case_name == new_case_name:
        state["case_name"] = new_case_name
        return False
    mapping = {
        f"{ACTIVE}/{old_case_name}": f"{ACTIVE}/{new_case_name}",
        f"{FINISHED}/{old_case_name}": f"{FINISHED}/{new_case_name}",
    }
    for stage in (ACTIVE, FINISHED):
        source = root / stage / old_case_name
        if source.exists() and source.is_dir():
            merge_or_rename_directory(source, root / stage / new_case_name)
    migrate_state_paths(state, mapping, new_case_name)
    rewrite_transactions(root, mapping, new_case_name)
    save_state(root, state)
    return True


def prepare_workspace(
    root: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    requested_case_name: str | None = None,
) -> bool:
    state["version"] = VERSION
    target_case_name = safe_name(
        requested_case_name or state.get("case_name") or default_case_name(root, state),
        "报销事项",
    )
    renamed = False
    if int(state.get("layout_version", 1)) >= LAYOUT_VERSION:
        renamed = rename_case_workspace(root, state, target_case_name)
    else:
        state["case_name"] = target_case_name
    ensure_workspace(root, config, target_case_name)
    migrated = migrate_workspace(root, state, config)
    stage = FINISHED if state.get("status") == "archived" else ACTIVE
    (root / stage / state["case_name"]).mkdir(parents=True, exist_ok=True)
    return migrated or renamed


def save_state(root: Path, state: dict[str, Any]) -> None:
    project_id = state.get("project_id")
    if not project_id:
        raise RuntimeError("报销项目缺少 project_id，无法保存。")
    state["updated_at"] = now_iso()
    registry, _ = load_project_registry(root, create=True)
    registry["projects"][project_id] = state
    if not registry.get("active_project_id"):
        registry["active_project_id"] = project_id
    save_project_registry(root, registry)
