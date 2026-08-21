from .base import *
from .documents import *
from .intake import *
from .policy import *

def category_key(value: str | None, config: dict[str, Any]) -> str | None:
    if not value:
        return None
    normalized = normalize(value)
    for key, definition in config.get("categories", {}).items():
        candidates = {normalize(key), normalize(str(definition.get("label", ""))), normalize(str(definition.get("folder", "")))}
        if normalized in candidates:
            return key
    return None


def next_feedback_id(registry: dict[str, Any]) -> str:
    number = int(registry.get("next_feedback_number", 1))
    registry["next_feedback_number"] = number + 1
    return f"FB-{number:03d}"


def feedback_scope_text(entry: dict[str, Any], config: dict[str, Any]) -> str:
    scope = "全部项目" if entry.get("scope") == "global" else str(
        entry.get("project_name") or entry.get("project_id") or "指定项目"
    )
    if entry.get("expense_type_label"):
        scope += f" / {entry['expense_type_label']}"
    category = entry.get("category")
    if category and not entry.get("expense_type_label"):
        label = config.get("categories", {}).get(category, {}).get("label", category)
        scope += f" / {label}"
    if entry.get("merchant"):
        scope += f" / 商户含“{entry['merchant']}”"
    if entry.get("amount_over") is not None:
        scope += f" / 金额超过 {amount_text(entry['amount_over'])}"
    return scope


def feedback_source_text(path: Path) -> str:
    if path.suffix.lower() in {".txt", ".md", ".csv", ".json"}:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    if path.suffix.lower() in {".pdf", ".docx"}:
        extracted = extract_text(path, path.name).strip()
        return extracted if extracted != normalize(path.stem) else ""
    return ""


def store_feedback_source(root: Path, source: Path, feedback_id: str) -> tuple[str, str]:
    if not source.exists() or not source.is_file():
        raise SystemExit(f"财务反馈附件不存在：{source}")
    digest = sha256(source)
    destination = metadata_paths(root)["feedback_sources"] / (
        f"{feedback_id}_{digest[:10]}_{safe_name(source.name)}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        try:
            workspace_relative = source.resolve().relative_to(root.resolve())
        except ValueError:
            workspace_relative = None
        is_intake_file = bool(
            workspace_relative
            and META not in workspace_relative.parts
            and (len(workspace_relative.parts) == 1 or workspace_relative.parts[0] == INBOX)
        )
        if is_intake_file:
            shutil.move(str(source), str(destination))
        else:
            shutil.copy2(source, destination)
    return relative(destination, root), digest


def feedback_fingerprint(entry: dict[str, Any]) -> str:
    payload = {
        key: entry.get(key)
        for key in (
            "text",
            "scope",
            "project_id",
            "category",
            "expense_type_key",
            "merchant",
            "amount_over",
            "required_evidence",
            "finance_requirements",
            "incorporated_into_scheme",
        )
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def find_feedback_entry(registry: dict[str, Any], feedback_id: str) -> dict[str, Any]:
    normalized = normalize(feedback_id)
    matches = [
        entry for entry in registry.get("entries", [])
        if normalize(str(entry.get("feedback_id", ""))) == normalized
    ]
    if not matches:
        raise SystemExit(f"没有找到财务反馈：{feedback_id}")
    return matches[0]


def feedback_for_project(
    feedback_registry: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        entry for entry in feedback_registry.get("entries", [])
        if entry.get("status", "active") == "active"
        and (
            entry.get("scope") == "global"
            or entry.get("project_id") == state.get("project_id")
        )
    ]


def feedback_applies_to_expense(entry: dict[str, Any], expense: dict[str, Any]) -> bool:
    if (
        entry.get("expense_type_key")
        and entry.get("expense_type_key") != expense.get("expense_type_key")
    ):
        return False
    if entry.get("category") and entry.get("category") != expense.get("category"):
        return False
    merchant = normalize(str(entry.get("merchant") or ""))
    if merchant and merchant not in normalize(str(expense.get("merchant") or "")):
        return False
    threshold = as_amount(entry.get("amount_over"))
    expense_amount = as_amount(expense.get("amount"))
    if threshold is not None and (expense_amount is None or expense_amount <= threshold):
        return False
    return True


def expense_material_types(state: dict[str, Any], expense: dict[str, Any]) -> set[str]:
    """Return model-recognized material types, with a legacy-field migration fallback."""
    materials: set[str] = set()
    for digest in expense.get("documents", []):
        document = state.get("documents", {}).get(digest, {})
        material_type = str(document.get("material_type") or "").strip()
        if material_type:
            materials.add(normalize(material_type))
            continue
        legacy_material = {"invoice": "发票", "payment": "付款记录"}.get(
            document.get("role")
        )
        if legacy_material:
            materials.add(normalize(legacy_material))
    return materials


def evidence_requirement_met(requirement: str, materials: set[str]) -> bool:
    alternatives = [normalize(item.strip()) for item in requirement.split("|") if item.strip()]
    return bool(alternatives) and any(item in materials for item in alternatives)


def evaluate_finance_feedback(
    root: Path,
    state: dict[str, Any],
    feedback_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feedback_registry = feedback_registry or load_finance_feedback(root)
    entries = feedback_for_project(feedback_registry, state)
    material_cache: dict[str, set[str]] = {}
    reviewed_entries: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for entry in entries:
        checks: list[dict[str, Any]] = []
        required = [] if entry.get("incorporated_into_scheme") else [
            str(item) for item in entry.get("required_evidence", []) if str(item).strip()
        ]
        for expense_id, expense in sorted(state.get("expenses", {}).items()):
            if not feedback_applies_to_expense(entry, expense):
                continue
            if expense_id not in material_cache:
                material_cache[expense_id] = expense_material_types(state, expense)
            missing = [
                requirement for requirement in required
                if not evidence_requirement_met(requirement, material_cache[expense_id])
            ]
            check = {
                "expense_id": expense_id,
                "merchant": expense.get("merchant"),
                "required_evidence": required,
                "missing_evidence": missing,
                "passed": not missing,
            }
            checks.append(check)
            if missing:
                gaps.append({
                    "feedback_id": entry.get("feedback_id"),
                    "expense_id": expense_id,
                    "merchant": expense.get("merchant"),
                    "missing_evidence": missing,
                    "feedback_text": entry.get("text"),
                })
        reviewed_entries.append({
            "feedback_id": entry.get("feedback_id"),
            "feedback_sha256": entry.get("feedback_sha256"),
            "received_at": entry.get("received_at"),
            "source": entry.get("source"),
            "source_file": entry.get("source_file"),
            "source_sha256": entry.get("source_sha256"),
            "text": entry.get("text"),
            "scope": entry.get("scope"),
            "project_id": entry.get("project_id"),
            "category": entry.get("category"),
            "expense_type_key": entry.get("expense_type_key"),
            "expense_type_label": entry.get("expense_type_label"),
            "merchant": entry.get("merchant"),
            "amount_over": entry.get("amount_over"),
            "required_evidence": required,
            "finance_requirements": entry.get("finance_requirements", []),
            "incorporated_into_scheme": entry.get("incorporated_into_scheme", False),
            "scheme_change": copy.deepcopy(entry.get("scheme_change")),
            "checks": checks,
            "result": "incorporated" if entry.get("incorporated_into_scheme") else (
                "advisory" if not required else (
                "not_applicable" if not checks else (
                    "passed" if all(item["passed"] for item in checks) else "missing_evidence"
                )
            )),
        })
    return {
        "checked_at": now_iso(),
        "project_id": state.get("project_id"),
        "project_name": state.get("case_name"),
        "entries": reviewed_entries,
        "gaps": gaps,
        "passed": not gaps,
    }


def evaluate_effective_requirements(
    root: Path, state: dict[str, Any], catalog: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Evaluate the base workbook plus sourced finance amendments as one review."""
    review = evaluate_project_requirements(state, catalog)
    amendment_review = evaluate_finance_feedback(root, state)
    amendment_gaps = [
        {
            "expense_id": item.get("expense_id"),
            "missing": list(item.get("missing_evidence", [])),
            "source": f"finance-feedback:{item.get('feedback_id')}",
        }
        for item in amendment_review.get("gaps", [])
    ]
    review["sourced_amendments"] = amendment_review.get("entries", [])
    review["amendment_review"] = amendment_review
    review["gaps"] = [*review.get("gaps", []), *amendment_gaps]
    review["passed"] = bool(review.get("passed")) and bool(amendment_review.get("passed"))
    return review, amendment_review


def print_setup_requirements_preview(
    root: Path, config: dict[str, Any]
) -> tuple[list[dict[str, str]], list[str]]:
    workbook = ensure_requirements_workbook(root)
    catalog_from_workbook(workbook)
    rows = user_requirement_rows(workbook)
    setup = setup_state(config)
    reference = setup.get("requirements_rows") or setup.get("requirements_initial_rows") or []
    changes = user_requirement_changes(reference, rows) if reference else []
    heading = "当前报销要求 Scheme" if setup.get("completed_at") else "首次配置：请确认报销要求 Scheme"
    print(heading)
    print(f"- 使用者：{claimant_name(config) or '尚未设置'}")
    print(f"- 邮箱附件：{email_intake_choice_label(config)}")
    print(f"- Scheme 文件：{workbook}")
    print(format_user_requirement_table(rows))
    if changes:
        print("\n与" + ("上次确认" if setup.get("requirements_confirmed_at") else "初始化版本") + "相比：")
        for change in changes:
            print(f"- {change}")
    elif setup.get("requirements_confirmed_at"):
        print("\n当前 Scheme 与上次确认版本一致。")
    else:
        print("\n这是首次确认；确认后当前内容将成为正式报销要求。")
    return rows, changes


@locked_command
def command_requirements_show(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    _, config = load_project_registry(root)
    print_setup_requirements_preview(root, config)
    blocker = setup_blocker(root, config)
    if blocker:
        print(f"\n配置状态：{blocker}；当前不会自动处理材料。")
    else:
        print("\n配置状态：姓名、邮箱选择和 Scheme 均已确认，可以处理材料。")


@locked_command
def command_requirements_change(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    _, config = load_project_registry(root)
    workbook = ensure_requirements_workbook(root)
    required_materials = list(dict.fromkeys(
        item.strip() for item in (args.require_evidence or []) if item.strip()
    ))
    finance_requirements = list(dict.fromkeys(
        item.strip() for item in (args.finance_requirement or []) if item.strip()
    ))
    change = plan_requirement_workbook_change(
        workbook,
        args.expense_type,
        required_materials,
        finance_requirements,
        args.scheme_mode,
    )
    before = change.get("before") or {"必须材料": "（未建立）", "财务其他要求": "（未建立）"}
    after = change["after"]
    print("Scheme 变更预览" + ("（尚未写入）" if args.preview else "" ) + "：")
    print(f"- 费用类型：{change['expense_type']}")
    print(f"- 必须材料：{before.get('必须材料') or '无'} → {after.get('必须材料') or '无'}")
    print(f"- 财务其他要求：{before.get('财务其他要求') or '无'} → {after.get('财务其他要求') or '无'}")
    if args.preview:
        print("确认后才会备份并更新报销要求.xlsx。")
        return
    if not args.confirmed:
        raise SystemExit("Scheme 变更必须先展示预览，并在用户明确确认后传入 --confirmed。")
    if change.get("action") == "no_change":
        print("当前 Scheme 已经是以上内容，无需写入。")
        return

    was_current = requirements_confirmation_is_current(root, config)
    backup_directory = root / META / "requirements-workbook-backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup = backup_directory / f"报销要求_配置变更前_{timestamp()}.xlsx"
    shutil.copy2(workbook, backup)
    try:
        apply_requirement_workbook_change(workbook, change)
        load_requirement_catalog(root, persist_snapshot=True)
    except BaseException:
        shutil.copy2(backup, workbook)
        raise
    if was_current:
        record_requirements_confirmation(
            root,
            config,
            user_requirement_rows(workbook),
            source="explicit-scheme-change",
        )
        write_json(metadata_paths(root)["config"], config)
    print(f"已更新并验证报销要求.xlsx；变更前备份：{relative(backup, root)}")
    if not was_current:
        print("首次配置仍未完成；请查看完整 Scheme，并对整份内容作最终确认。")


@locked_command
def command_requirements_confirm(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    if not args.confirmed:
        raise SystemExit("必须先向用户展示当前 Scheme，并在明确确认后传入 --confirmed。")
    if not claimant_name(config):
        raise SystemExit("首次配置尚未填写使用者姓名。")
    choice = setup_state(config).get("email_intake_choice")
    if choice not in SETUP_EMAIL_CHOICES:
        raise SystemExit("首次配置尚未确认是否接入邮箱。")
    if choice == "connect" and not email_intake_is_configured(root):
        raise SystemExit(
            "已选择接入邮箱，但邮箱账号配置尚未完成。"
            "请完成安全邮箱配置，或明确改为暂不接入邮箱。"
        )

    rows, changes = print_setup_requirements_preview(root, config)
    load_requirement_catalog(root, persist_snapshot=True)
    record_requirements_confirmation(root, config, rows)
    write_json(metadata_paths(root)["config"], config)

    for project in registry.get("projects", {}).values():
        prepare_workspace(root, project, config)
    result = None
    if registry.get("projects"):
        before_registry = copy.deepcopy(registry)
        result = process_routed_inputs(
            root,
            registry,
            config,
            False,
            before_registry,
        )

    print("\n首次配置已完成：姓名、邮箱选择和当前 Scheme 均已确认。")
    if changes:
        print(f"本次确认包含 {len(changes)} 项 Scheme 变化。")
    if result is not None:
        print_result(result)

    start_service = bool(setup_state(config).get("start_service_after_confirmation", True))
    if args.no_service or not start_service:
        print("后台监听器：已按要求跳过自动安装。")
    else:
        from .projects import auto_install_background_service

        service_result = auto_install_background_service(root)
        if service_result["installed"]:
            print(f"后台监听器：已自动安装并启动。{service_result['message']}")
        else:
            print("后台监听器：自动安装失败，但首次配置已经保存。")
            print(f"失败原因：{service_result['message']}")
            print("解决后可再次说“开启自动处理”重试。")

    from .projects import direct_onboarding_text

    print("\n" + direct_onboarding_text(root, registry))


@locked_command
def command_requirements_validate(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    load_project_registry(root)
    workbook = ensure_requirements_workbook(root)
    catalog = catalog_from_workbook(workbook)
    catalog = load_requirement_catalog(root, persist_snapshot=True)
    enabled_rules = sum(1 for item in catalog.get("rules", []) if item.get("enabled", True))
    print(
        f"报销要求有效：{len(catalog.get('expense_types', []))} 个费用类型，"
        f"{enabled_rules} 条启用规则。"
    )
    print(f"已更新有效快照：{requirements_snapshot_path(root).relative_to(root)}")
    _, config = load_project_registry(root)
    if not requirements_confirmation_is_current(root, config):
        print("当前工作簿尚未由用户确认；验证通过不等于确认，自动处理仍保持暂停。")


@locked_command
def command_feedback_add(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    projects, config = load_project_registry(root)
    require_setup_ready(root, config)
    project = select_project(projects, args.project) if args.project else None
    requirement_catalog = load_requirement_catalog(root, persist_snapshot=False)
    expense_type_label = str(args.expense_type or "").strip() or None
    expense_type_key = expense_type_from_label(requirement_catalog, expense_type_label)
    expense_definition = expense_type_definition(requirement_catalog, expense_type_key)
    if project and expense_type_label and not expense_type_key:
        raise SystemExit(
            f"项目临时要求的费用类型尚未纳入报销要求：{expense_type_label}"
        )
    category = category_key(args.category, config)
    if not category and expense_definition:
        category = str(expense_definition.get("category_key") or "") or None
    if args.category and not category:
        raise SystemExit(f"未知费用类型：{args.category}")
    source_path = Path(args.source_file).expanduser().resolve() if args.source_file else None
    text = (args.text or "").strip()
    if not text and source_path:
        text = feedback_source_text(source_path)
    if not text:
        raise SystemExit("请提供财务反馈原文，或提供可读取的反馈附件。")
    amount_over = as_amount(args.amount_over)
    if args.amount_over is not None and amount_over is None:
        raise SystemExit(f"无法识别金额门槛：{args.amount_over}")

    required_evidence = list(dict.fromkeys(
        item.strip() for item in (args.require_evidence or []) if item.strip()
    ))
    finance_requirements = list(dict.fromkeys(
        item.strip() for item in (args.finance_requirement or []) if item.strip()
    ))
    scheme_change = None
    if args.apply_to_scheme:
        if project:
            raise SystemExit("仅本次出差的反馈不能写入全局报销要求。")
        if not expense_type_label:
            raise SystemExit("更新全局报销要求时必须指定费用类型。")
        scheme_change = plan_requirement_workbook_change(
            requirements_workbook_path(root),
            expense_type_label,
            required_evidence,
            finance_requirements,
            args.scheme_mode,
        )
    if args.preview:
        scope = project.get("case_name") if project else "以后所有出差项目"
        print("财务反馈变更预览（尚未写入）：")
        print(f"- 适用范围：{scope}")
        if expense_type_label:
            print(f"- 费用类型：{expense_type_label}")
        if scheme_change:
            before = scheme_change.get("before") or {
                "必须材料": "（未建立）", "财务其他要求": "（未建立）"
            }
            after = scheme_change["after"]
            print(f"- 必须材料：{before.get('必须材料') or '（空）'} → {after['必须材料'] or '（空）'}")
            print(f"- 财务其他要求：{before.get('财务其他要求') or '（空）'} → {after['财务其他要求'] or '（空）'}")
            print("确认后将更新报销要求.xlsx，并保存反馈原文和变更前备份。")
        else:
            print("- 项目临时必须材料：" + ("、".join(required_evidence) or "无"))
            print("- 项目临时财务其他要求：" + ("；".join(finance_requirements) or "无"))
            print("确认后只对该项目生效，不修改全局报销要求.xlsx。")
        return
    if args.apply_to_scheme and not args.confirmed:
        raise SystemExit("全局 Scheme 变更必须先向用户展示预览，并在明确确认后传入 --confirmed。")

    registry = load_finance_feedback(root)
    feedback_id = next_feedback_id(registry)
    entry = {
        "feedback_id": feedback_id,
        "status": "active",
        "received_at": args.received_at or now_iso(),
        "source": args.source or (source_path.name if source_path else "用户转述的财务反馈"),
        "source_file": None,
        "source_sha256": None,
        "text": text,
        "scope": "project" if project else "global",
        "project_id": project.get("project_id") if project else None,
        "project_name": project.get("case_name") if project else None,
        "category": category,
        "expense_type_key": expense_type_key,
        "expense_type_label": expense_type_label,
        "merchant": args.merchant.strip() if args.merchant else None,
        "amount_over": str(amount_over) if amount_over is not None else None,
        "required_evidence": required_evidence,
        "finance_requirements": finance_requirements,
        "incorporated_into_scheme": bool(args.apply_to_scheme),
        "scheme_change": copy.deepcopy(scheme_change),
        "created_at": now_iso(),
        "status_history": [{
            "status": "active",
            "at": now_iso(),
            "reason": "用户确认后录入" if args.confirmed else "首次录入",
        }],
    }
    entry["feedback_sha256"] = feedback_fingerprint(entry)
    duplicate = next(
        (
            item for item in registry.get("entries", [])
            if item.get("feedback_sha256") == entry["feedback_sha256"]
        ),
        None,
    )
    if duplicate:
        raise SystemExit(f"相同财务反馈已经记录：{duplicate.get('feedback_id')}")
    if source_path:
        entry["source_file"], entry["source_sha256"] = store_feedback_source(
            root, source_path, feedback_id
        )
    if scheme_change and scheme_change.get("action") != "no_change":
        workbook = requirements_workbook_path(root)
        backup_directory = root / META / "requirements-workbook-backups"
        backup_directory.mkdir(parents=True, exist_ok=True)
        backup = backup_directory / f"报销要求_{feedback_id}_变更前_{timestamp()}.xlsx"
        shutil.copy2(workbook, backup)
        try:
            apply_requirement_workbook_change(workbook, scheme_change)
            catalog_from_workbook(workbook)
            updated_catalog = load_requirement_catalog(root, persist_snapshot=True)
        except BaseException:
            shutil.copy2(backup, workbook)
            raise
        entry["scheme_change"]["backup"] = relative(backup, root)
        entry["scheme_change"]["workbook_sha256"] = updated_catalog.get("source", {}).get("sha256")
        entry["expense_type_key"] = expense_type_from_label(
            updated_catalog, expense_type_label
        )
        record_requirements_confirmation(
            root,
            config,
            user_requirement_rows(workbook),
            source="confirmed-finance-feedback",
        )
        write_json(metadata_paths(root)["config"], config)
    registry["entries"].append(entry)
    save_finance_feedback(root, registry)
    print(f"已记录财务反馈：{feedback_id}")
    print(f"适用范围：{feedback_scope_text(entry, config)}")
    if entry["required_evidence"]:
        if entry.get("incorporated_into_scheme"):
            print("已写入全局报销要求：" + "、".join(entry["required_evidence"]))
        else:
            print("最终核验将检查：" + "、".join(entry["required_evidence"]))
    else:
        print("这条反馈将作为审查依据引用，但不会在缺少结构化要求时自动阻止提交。")
    if entry["source_file"]:
        print(f"来源附件已留存：{entry['source_file']}")
    if entry.get("scheme_change"):
        change = entry["scheme_change"]
        print(
            f"Scheme 变更：{change.get('action')} {change.get('expense_type')}；"
            "报销要求.xlsx 已重新验证。"
        )


def command_feedback_list(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    projects, config = load_project_registry(root)
    project = select_project(projects, args.project) if args.project else None
    entries = load_finance_feedback(root).get("entries", [])
    if project:
        entries = [
            item for item in entries
            if item.get("scope") == "global" or item.get("project_id") == project.get("project_id")
        ]
    if not args.include_inactive:
        entries = [item for item in entries if item.get("status", "active") == "active"]
    if not entries:
        print("当前没有符合条件的财务反馈。")
        return
    print(f"财务反馈共 {len(entries)} 条：")
    for entry in entries:
        status = "生效中" if entry.get("status", "active") == "active" else "已停用"
        print(
            f"- {entry['feedback_id']} | {status} | {feedback_scope_text(entry, config)} | "
            f"{entry.get('source')} | {entry.get('text')}"
        )
        if entry.get("required_evidence"):
            print("  核验要求：" + "、".join(entry["required_evidence"]))


@locked_command
def command_feedback_status(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    load_project_registry(root)
    registry = load_finance_feedback(root)
    entry = find_feedback_entry(registry, args.feedback_id)
    if entry.get("incorporated_into_scheme") and args.status == "inactive":
        raise SystemExit(
            "这条反馈已经写入报销要求.xlsx，停用审计记录不会撤销 Scheme。"
            "请提交一条新的 Scheme 变更，预览并确认后再更新工作簿。"
        )
    entry["status"] = args.status
    entry.setdefault("status_history", []).append({
        "status": args.status,
        "at": now_iso(),
        "reason": args.reason or "用户更新",
    })
    save_finance_feedback(root, registry)
    label = "重新启用" if args.status == "active" else "停用"
    print(f"已{label}财务反馈：{entry['feedback_id']}。原始记录仍保留用于审计。")


def finance_feedback_review_lines(review: dict[str, Any]) -> list[str]:
    labels = {
        "incorporated": "已写入 Scheme",
        "advisory": "已引用",
        "not_applicable": "本项目未触发",
        "passed": "已满足",
        "missing_evidence": "待补充",
    }
    lines: list[str] = []
    for entry in review.get("entries", []):
        label = labels.get(entry.get("result"), str(entry.get("result")))
        provenance = str(entry.get("source") or "来源未标注")
        if entry.get("source_file"):
            provenance += f"（原件：{entry['source_file']}）"
        lines.append(
            f"- {entry.get('feedback_id')} | {label} | {provenance} | "
            f"{entry.get('text')}"
        )
    return lines
