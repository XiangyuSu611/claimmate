from .base import *
from .documents import *
from .model import *
from .intake import *
from .policy import *

def auto_install_background_service(root: Path) -> dict[str, Any]:
    script = compatibility_script_path("automation.py")
    result = subprocess.run(
        [sys.executable, str(script), "service-install", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    if result.returncode == 0:
        first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
        return {
            "installed": True,
            "message": first_line or "后台监听器已自动安装并启动。",
        }
    return {
        "installed": False,
        "message": error or output or f"安装命令退出码为 {result.returncode}",
    }


def direct_onboarding_text(root: Path, registry: dict[str, Any]) -> str:
    projects = list(registry.get("projects", {}).values())
    first_step = (
        f"第一个项目已经建立：{projects[-1]['case_name']}。现在可以直接发送材料。"
        if projects
        else "第一步先说：\"新建一个 6 月北京出差报销\"。只知道月份或地点也可以开始。"
    )
    inbox_path = str((root / INBOX).resolve())
    return (
        "首次使用只需要记住三个阶段：\n"
        "\n1. 新建项目\n"
        f"- {first_step}\n"
        "- 只处理出差报销；可以同时建立多个项目，日期不完整也能先开始。\n"
        "\n2. 更新材料\n"
        f"- 材料可以直接发在对话中、放进“{inbox_path}”，或通过邮箱获取。\n"
        "- 不用改名或分类；大模型判断出差范围、费用类型、项目归属、金额和材料配对。\n"
        "- 不同项目的材料可以交叉发送；不确定的文件会安全保留，可纠正或撤销。\n"
        "- “报销要求.xlsx”只维护“费用类型、必须材料、财务其他要求”三列。\n"
        "- 收到财务反馈时直接发送原文或附件；确认适用范围和修改内容后，ClaimMate 更新 Scheme 或本项目临时要求。\n"
        "\n3. 交付财务\n"
        "- 项目结束时点名说“北京出差报销要交给财务了”。\n"
        "- 一张发票可以对应多笔付款记录；ClaimMate 按付款合计核验，少付、多付或金额未知都会提示。\n"
        "- 通过后生成逐项明细表，包含付款合计和差额；每项收款人默认使用初始化时登记的姓名。\n"
        "- ClaimMate 不会未经确认替你提交报销。\n"
        f"\n完整说明：{GUIDE}"
    )


@locked_command
def command_init(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"报销文件夹不存在：{root}")
    paths = metadata_paths(root)
    existed = paths["projects"].exists() or paths["state"].exists()
    registry, config = load_project_registry(root, create=True)
    if args.user_name:
        set_claimant_name(config, args.user_name)
    if not claimant_name(config):
        raise SystemExit("首次初始化需要使用者姓名，请提供 --user-name。")
    if getattr(args, "email_choice", None):
        record_email_intake_choice(config, args.email_choice)
    if setup_state(config).get("email_intake_choice") not in SETUP_EMAIL_CHOICES:
        raise SystemExit("首次初始化需要确认是否接入邮箱，请提供 --email-choice connect 或 skip。")
    setup_state(config)["start_service_after_confirmation"] = not args.no_service
    before_registry = copy.deepcopy(registry) if existed else None
    project = None
    configuration_ready = False
    service_result = None
    if args.case_name:
        matches = project_matches(registry, args.case_name)
        project = matches[0] if len(matches) == 1 else create_project(registry, args.case_name)
    if args.dry_run:
        working = copy.deepcopy(registry)
        result = process_routed_inputs(
            root,
            working,
            config,
            True,
            before_registry,
            project.get("case_name") if project else None,
        )
    else:
        current_name = project.get("case_name") if project else "尚未新建项目"
        ensure_workspace(root, config, current_name)
        workbook = requirements_workbook_path(root)
        catalog_from_workbook(workbook)
        rows = user_requirement_rows(workbook)
        setup = setup_state(config)
        if not setup.get("requirements_initial_sha256"):
            setup["requirements_initial_sha256"] = requirements_workbook_hash(root)
            setup["requirements_initial_rows"] = copy.deepcopy(rows)
        write_json(paths["config"], config)
        save_project_registry(root, registry)
        if project:
            prepare_workspace(root, project, config)
        configuration_ready = setup_blocker(root, config) is None
        if configuration_ready:
            result = process_routed_inputs(
                root,
                registry,
                config,
                False,
                before_registry,
                project.get("case_name") if project else None,
            )
            if not args.no_service:
                service_result = auto_install_background_service(root)
        else:
            result = {
                "discovered": len(discover_inputs(root)),
                "organized": 0,
                "resolved": 0,
                "unassigned": 0,
                "out_of_scope": 0,
                "recovered": 0,
                "duplicates": 0,
                "routed_projects": {},
                "model_status": "deferred",
                "model_processed": 0,
                "model_reason": "等待用户确认首次 Scheme",
            }
    heading = (
        "ClaimMate 出差报销工作区已就绪。"
        if configuration_ready
        else "ClaimMate 出差报销工作区目录已初始化，首次配置尚未完成。"
    )
    print(heading)
    print(f"使用者：{claimant_name(config)}（交付明细表默认收款人）")
    print(f"邮箱附件：{email_intake_choice_label(config)}")
    if project:
        print(f"已创建或选中项目：{project['case_name']}")
    elif configuration_ready:
        print_result(result)
        if args.no_service:
            print("后台监听器：已按要求跳过自动安装。")
        elif service_result and service_result["installed"]:
            print(f"后台监听器：已自动安装并启动。{service_result['message']}")
        else:
            print("后台监听器：自动安装失败，但工作区仍可手动使用。")
            if service_result:
                print(f"失败原因：{service_result['message']}")
        print("\n" + direct_onboarding_text(root, registry))
    else:
        print("当前还没有出差报销项目。")
    if args.dry_run:
        print_result(result)
        print("后台监听器：预览模式不会安装。")
    else:
        print(f"发现 {result['discovered']} 个待处理文件；Scheme 确认前不会识别、移动或重命名。")
        print("\n请确认当前报销要求 Scheme：")
        print(format_user_requirement_table(rows))
        print("\n如果需要修改，请直接说明或编辑报销要求.xlsx；再次查看修改内容后明确确认。")
        listener = "确认 Scheme 后自动启动" if not args.no_service else "已选择不自动启动"
        print(f"后台监听器：{listener}。")
        print("下一步：确认以上 Scheme 正确。确认完成后才会处理材料并显示完整用户引导。")


@locked_command
def command_setup_email(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    _, config = load_project_registry(root)
    choice = record_email_intake_choice(config, args.choice)
    write_json(metadata_paths(root)["config"], config)
    print(f"已确认邮箱附件选择：{email_intake_choice_label(config)}。")
    if choice == "connect" and not email_intake_is_configured(root):
        print("请继续完成邮箱服务器、账号和系统安全凭据配置；完成后再确认 Scheme。")
    else:
        print("下一步请查看并确认报销要求 Scheme。")


@locked_command
def command_profile_set(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    _, config = load_project_registry(root)
    name = set_claimant_name(config, args.user_name)
    write_json(metadata_paths(root)["config"], config)
    print(f"已更新使用者姓名：{name}")
    print("后续交付明细表的每项收款人将默认使用该姓名。")


@locked_command
def command_new(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    before_registry = copy.deepcopy(registry)
    project = create_project(registry, args.case_name)
    save_project_registry(root, registry)
    prepare_workspace(root, project, config)
    if setup_blocker(root, config):
        print(f"已新建出差报销项目：{project['case_name']}（{project['project_id']}）")
        print("首次配置尚未完成；请先查看并确认 Scheme，之后再发送或处理材料。")
        return
    operations: list[dict[str, str]] = []
    resolved = revisit_unassigned_documents(root, registry, config, operations, False)
    save_project_registry(root, registry)
    if resolved:
        record_transaction(
            root,
            before_registry,
            registry,
            operations,
            {"created_project": project["project_id"], "resolved_unassigned": resolved},
        )
    print(f"已新建出差报销项目：{project['case_name']}（{project['project_id']}）")
    print("后续可以继续交叉发送不同项目的材料，ClaimMate 会逐份判断归属。")
    print(f"准备提交时说：“{project['case_name']}要交给财务了”。")


def command_list(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, _ = load_project_registry(root)
    projects = list(registry.get("projects", {}).values())
    if not projects:
        print("当前还没有出差报销项目。可以说“新建一个 6 月北京出差报销”。")
        return
    print(f"当前共有 {len(projects)} 个出差报销项目：")
    for project in sorted(projects, key=lambda item: str(item.get("created_at", ""))):
        summary = build_summary(project)
        stage = {"collecting": "收集中", "ready": "可提交", "archived": "已结束"}.get(
            project.get("status"), str(project.get("status"))
        )
        print(
            f"- {project['case_name']}（{project['project_id']}）| {stage} | "
            f"{summary['expenses']} 笔费用 | {summary['documents']} 个材料"
        )
    unassigned_records = list(registry.get("unassigned_documents", {}).values())
    unassigned = sum(1 for item in unassigned_records if item.get("status") != "out_of_scope")
    out_of_scope = sum(1 for item in unassigned_records if item.get("status") == "out_of_scope")
    if unassigned:
        print(f"另有 {unassigned} 个材料正在等待项目归属，暂时不需要处理。")
    if out_of_scope:
        print(f"另有 {out_of_scope} 个非出差报销材料已安全保留，不会进入出差项目。")


@locked_command
def command_rename(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    project = select_project(registry, args.project)
    new_name = safe_name(args.new_name, "报销事项")
    if any(
        item.get("project_id") != project.get("project_id")
        and normalize(str(item.get("case_name"))) == normalize(new_name)
        for item in registry.get("projects", {}).values()
    ):
        raise SystemExit(f"报销项目已存在：{new_name}")
    old_name = project.get("case_name")
    rename_case_workspace(root, project, new_name)
    ensure_workspace(root, config, new_name)
    save_state(root, project)
    feedback_registry = load_finance_feedback(root)
    feedback_changed = False
    for entry in feedback_registry.get("entries", []):
        if entry.get("project_id") == project.get("project_id"):
            entry["project_name"] = new_name
            feedback_changed = True
    if feedback_changed:
        save_finance_feedback(root, feedback_registry)
    print(f"项目已重命名：{old_name} → {new_name}")


@locked_command
def command_expense_label(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    state = select_project(registry, args.project, include_archived=False)
    prepare_workspace(root, state, config)
    expense = state.get("expenses", {}).get(args.expense_id)
    if not expense:
        raise SystemExit(f"费用编号不存在：{args.expense_id}")
    label = safe_name(args.name, "待识别费用")
    before_registry = copy.deepcopy(registry)
    expense["label"] = label
    operations: list[dict[str, str]] = []
    renamed = rename_expense_documents(root, state, args.expense_id, operations, False)
    state["status"] = "collecting"
    state.pop("ready_at", None)
    save_project_registry(root, registry)
    record_transaction(
        root,
        before_registry,
        registry,
        operations,
        {"expense_label_updated": args.expense_id, "label": label, "renamed": renamed},
    )
    print(f"已更新费用名称：{args.expense_id} → {label}；已重命名 {renamed} 个文件。")


@locked_command
def command_expense_merge(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    state = select_project(registry, args.project, include_archived=False)
    prepare_workspace(root, state, config)
    target_id = str(args.target).upper()
    source_id = str(args.source).upper()
    if target_id == source_id:
        raise SystemExit("合并来源和目标不能是同一费用编号。")
    expenses = state.get("expenses", {})
    target = expenses.get(target_id)
    source = expenses.get(source_id)
    if not target:
        raise SystemExit(f"目标费用编号不存在：{target_id}")
    if not source:
        raise SystemExit(f"来源费用编号不存在：{source_id}")

    before_registry = copy.deepcopy(registry)
    merged_at = now_iso()
    evidence = f"用户明确确认 {source_id} 与 {target_id} 为同一笔费用"
    target_documents = target.setdefault("documents", [])
    for digest in source.get("documents", []):
        if digest not in target_documents:
            target_documents.append(digest)
        record = state.get("documents", {}).get(digest)
        if not record:
            continue
        record["previous_expense_id"] = source_id
        record["expense_id"] = target_id
        record["pairing_source"] = "explicit-user-confirmation"
        record["pairing_confirmed_at"] = merged_at
        record["inference_evidence"] = [
            *record.get("inference_evidence", []),
            evidence,
        ]

    target["merged_from"] = list(dict.fromkeys([
        *target.get("merged_from", []),
        source_id,
        *source.get("merged_from", []),
    ]))
    target["pairing_source"] = "explicit-user-confirmation"
    target["pairing_confirmed_at"] = merged_at
    target["pairing_evidence"] = [*target.get("pairing_evidence", []), evidence]
    target["merged_merchants"] = sorted({
        str(value)
        for value in (
            target.get("merchant"),
            source.get("merchant"),
            *target.get("merged_merchants", []),
            *source.get("merged_merchants", []),
        )
        if value
    })

    del expenses[source_id]
    reconciliation = refresh_expense_amount_reconciliation(target, state)
    operations: list[dict[str, str]] = []
    renamed = rename_expense_documents(root, state, target_id, operations, False)
    state["status"] = "collecting"
    state.pop("ready_at", None)
    save_project_registry(root, registry)
    record_transaction(
        root,
        before_registry,
        registry,
        operations,
        {
            "expenses_merged": {"source": source_id, "target": target_id},
            "renamed": renamed,
            "amount_conflict": target.get("amount_conflict"),
        },
    )
    print(f"已合并费用：{source_id} → {target_id}；已重命名 {renamed} 个文件。")
    if target.get("amount_conflict"):
        print(
            "金额仍需核对："
            f"发票 {amount_text(reconciliation.get('invoice_total'))}，"
            f"付款合计 {amount_text(reconciliation.get('payment_total'))}。"
            "配对关系已保留，但不会交付财务。"
        )


def find_workspace_document(
    registry: dict[str, Any], query: str
) -> tuple[str, dict[str, Any] | None, str, dict[str, Any]]:
    normalized = normalize(query)
    matches: list[tuple[str, dict[str, Any] | None, str, dict[str, Any]]] = []
    for digest, record in registry.get("unassigned_documents", {}).items():
        if (
            digest.startswith(query.lower())
            or normalized == normalize(str(record.get("original_name", "")))
            or normalized == normalize(str(record.get("current_path", "")))
        ):
            matches.append(("unassigned", None, digest, record))
    for project in registry.get("projects", {}).values():
        for digest, record in project.get("documents", {}).items():
            if (
                digest.startswith(query.lower())
                or normalized == normalize(str(record.get("original_name", "")))
                or normalized == normalize(str(record.get("current_path", "")))
            ):
                matches.append(("project", project, digest, record))
    if not matches:
        raise SystemExit(f"没有找到材料：{query}")
    if len(matches) > 1:
        raise SystemExit(f"匹配到多个材料，请使用 SHA-256 前缀指定：{query}")
    return matches[0]


@locked_command
def command_assign(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    target = select_project(registry, args.project, include_archived=False)
    before_registry = copy.deepcopy(registry)
    operations: list[dict[str, str]] = []
    assigned = 0
    for query in args.files:
        location, source_project, digest, record = find_workspace_document(registry, query)
        if source_project and source_project.get("project_id") == target.get("project_id"):
            continue
        source = absolute(root, record["current_path"])
        if not source.exists():
            raise SystemExit(f"材料文件不存在：{record['current_path']}")
        item = analyze(source, config, record.get("original_name"))
        item["sha256"] = digest
        item["added_at"] = record.get("added_at")
        item["from_observation"] = True
        item["inference_evidence"] = ["用户消息已明确项目"]
        for key in (
            "role", "category", "merchant", "amount", "expense_label", "expense_type_key",
            "material_type", "applicable_requirement_ids", "assessed_condition_rule_ids",
            "date_tokens", "reference_tokens"
        ):
            if item.get(key) in (None, "", [], "未识别商户") and record.get(key) not in (None, "", []):
                item[key] = record[key]

        if location == "unassigned":
            del registry["unassigned_documents"][digest]
        elif source_project:
            expense_id = record.get("expense_id")
            if expense_id in source_project.get("expenses", {}):
                expense = source_project["expenses"][expense_id]
                item["expense_label"] = item.get("expense_label") or expense.get("label")
                expense["documents"] = [value for value in expense.get("documents", []) if value != digest]
                if not expense["documents"]:
                    del source_project["expenses"][expense_id]
            del source_project["documents"][digest]
            source_project["status"] = "collecting"
            source_project.pop("ready_at", None)

        organize_item(root, item, target, config, operations, False)
        target["status"] = "collecting"
        target.pop("ready_at", None)
        assigned += 1

    save_project_registry(root, registry)
    if assigned:
        record_transaction(
            root,
            before_registry,
            registry,
            operations,
            {"assigned": assigned, "project": target.get("project_id")},
        )
    print(f"已将 {assigned} 个材料归入：{target['case_name']}。")


@locked_command
def command_check(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, config = load_project_registry(root)
    require_setup_ready(root, config)
    if not registry.get("projects"):
        raise SystemExit("当前还没有出差报销项目，请先说“新建一个……出差报销”。")
    if args.project:
        state = select_project(registry, args.project, include_archived=False)
    else:
        active_id = registry.get("active_project_id")
        state = registry["projects"].get(active_id)
        if not state or state.get("status") == "archived":
            state = next(
                (
                    project for project in registry["projects"].values()
                    if project.get("status") != "archived"
                ),
                None,
            )
        if state is None:
            raise SystemExit("当前没有进行中的报销项目，请先新建一个项目。")
    if not args.dry_run:
        migrated = prepare_workspace(root, state, config)
        if migrated:
            print("已将项目升级为扁平目录：所有材料直接放在项目根目录。")
    before_registry = copy.deepcopy(registry)
    result = process_routed_inputs(
        root,
        registry,
        config,
        args.dry_run,
        before_registry,
        args.project,
        force_model_revisit=getattr(args, "revisit", False),
    )
    print_result(result)


def command_status(args: argparse.Namespace) -> None:
    root = Path(args.folder).expanduser().resolve()
    registry, _ = load_project_registry(root)
    open_projects = [
        item for item in registry.get("projects", {}).values() if item.get("status") != "archived"
    ]
    if not args.project and len(open_projects) > 1:
        command_list(args)
        return
    state, config = load_workspace(root, project=args.project)
    prepare_workspace(root, state, config)
    summary = build_summary(state)
    requirements_review = evaluate_project_requirements(
        state, load_requirement_catalog(root, persist_snapshot=True)
    )
    if args.json:
        print(json.dumps({
            "summary": summary,
            "requirements_review": requirements_review,
            "expenses": state.get("expenses", {}),
        }, ensure_ascii=False, indent=2))
        return
    print(f"出差报销：{summary['case_name']}（{summary['status']}）")
    print(
        f"共 {summary['expenses']} 笔；按报销要求待补 {len(requirements_review.get('gaps', []))} 笔。"
    )
    if summary.get("amount_conflicts"):
        print(f"金额冲突 {summary['amount_conflicts']} 笔。")
    print(f"待确认 {summary['review_items']} 个，重复文件 {summary['duplicates']} 个。")
    for expense_id, expense in sorted(state.get("expenses", {}).items()):
        details = expense_status(expense, state)
        status = expense_state_text(details)
        category = expense.get("label") or config["categories"].get(
            expense.get("category"), {}
        ).get("label", expense.get("category"))
        print(
            f"- {expense_id} | {category} | {expense.get('merchant')} | "
            f"{amount_text(expense.get('amount'))} | {status}"
        )
