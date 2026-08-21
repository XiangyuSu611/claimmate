from .base import *
from .documents import *
from .model import *
from .policy import *

def discover_inputs(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.iterdir():
        if (
            path.is_file()
            and path.name not in {GUIDE, REQUIREMENTS_WORKBOOK}
            and path.suffix.lower() in ELIGIBLE_EXTENSIONS
        ):
            found.append(path)
    inbox = root / INBOX
    if inbox.exists():
        found.extend(
            path for path in inbox.rglob("*")
            if path.is_file()
            and path.suffix.lower() in ELIGIBLE_EXTENSIONS
            and UNASSIGNED_FOLDER not in path.relative_to(inbox).parts
        )
    return sorted(set(found), key=lambda item: str(item).lower())


def amount_text(amount: Decimal | str | None) -> str:
    if amount is None or amount == "":
        return "金额未知"
    value = Decimal(str(amount))
    rendered = format(value.normalize(), "f")
    return f"{rendered}元"


def as_amount(amount: Decimal | str | None) -> Decimal | None:
    if amount is None or amount == "":
        return None
    try:
        return Decimal(str(amount))
    except InvalidOperation:
        return None


def money_value(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    return format(amount.quantize(Decimal("0.01")), "f")


def expense_amount_reconciliation(
    expense: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    role_amounts: dict[str, list[Decimal | None]] = {"invoice": [], "payment": []}
    for digest in expense.get("documents", []):
        record = state.get("documents", {}).get(digest, {})
        role = record.get("role")
        if role in role_amounts:
            role_amounts[role].append(as_amount(record.get("amount")))

    invoices = role_amounts["invoice"]
    payments = role_amounts["payment"]
    known_invoices = [value for value in invoices if value is not None]
    known_payments = [value for value in payments if value is not None]
    invoice_total = sum(known_invoices, Decimal("0")) if known_invoices else None
    payment_total = sum(known_payments, Decimal("0")) if known_payments else None
    all_invoice_amounts_known = bool(invoices) and len(known_invoices) == len(invoices)
    all_payment_amounts_known = bool(payments) and len(known_payments) == len(payments)

    difference: Decimal | None = None
    if not invoices:
        status = "awaiting_invoice" if payments else "unavailable"
    elif not payments:
        status = "awaiting_payment"
    elif not all_invoice_amounts_known or not all_payment_amounts_known:
        status = "incomplete_amounts"
    else:
        assert invoice_total is not None and payment_total is not None
        difference = payment_total - invoice_total
        if difference == 0:
            status = "matched"
        elif difference < 0:
            status = "underpaid"
        else:
            status = "overpaid"

    return {
        "status": status,
        "invoice_count": len(invoices),
        "payment_count": len(payments),
        "known_invoice_amount_count": len(known_invoices),
        "known_payment_amount_count": len(known_payments),
        "invoice_total": money_value(invoice_total),
        "payment_total": money_value(payment_total),
        "difference": money_value(difference),
        "checked_at": now_iso(),
    }


def refresh_expense_amount_reconciliation(
    expense: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any]:
    reconciliation = expense_amount_reconciliation(expense, state)
    expense["amount_reconciliation"] = reconciliation
    expense["invoice_total"] = reconciliation.get("invoice_total")
    expense["payment_total"] = reconciliation.get("payment_total")
    expense["payment_difference"] = reconciliation.get("difference")
    if reconciliation.get("invoice_total") is not None:
        expense["amount"] = reconciliation["invoice_total"]
    elif reconciliation.get("payment_total") is not None:
        expense["amount"] = reconciliation["payment_total"]

    status = reconciliation["status"]
    if status in {"underpaid", "overpaid", "incomplete_amounts"}:
        reasons = {
            "underpaid": "付款合计少于发票价税合计",
            "overpaid": "付款合计多于发票价税合计",
            "incomplete_amounts": "发票或付款记录存在金额未识别",
        }
        amounts = [
            value for value in (
                reconciliation.get("invoice_total"),
                reconciliation.get("payment_total"),
            ) if value is not None
        ]
        expense["amount_conflict"] = {
            "status": status,
            "amounts": amounts,
            "invoice_total": reconciliation.get("invoice_total"),
            "payment_total": reconciliation.get("payment_total"),
            "difference": reconciliation.get("difference"),
            "reason": reasons[status],
            "detected_at": reconciliation["checked_at"],
        }
    else:
        expense.pop("amount_conflict", None)
    return reconciliation


def expense_roles(expense: dict[str, Any], state: dict[str, Any]) -> list[str]:
    return [
        state["documents"][digest].get("role")
        for digest in expense.get("documents", [])
        if digest in state["documents"]
    ]


def next_expense_id(state: dict[str, Any]) -> str:
    number = int(state.get("next_expense_number", 1))
    state["next_expense_number"] = number + 1
    return f"EXP-{number:03d}"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter:02d}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def backup_source(root: Path, item: dict[str, Any], dry_run: bool) -> Path:
    destination = metadata_paths(root)["originals"] / f"{item['sha256']}__{safe_name(item['original_name'])}"
    if not dry_run and not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item["source"], destination)
    return destination


def move_file(root: Path, source: Path, destination: Path, operations: list[dict[str, str]], dry_run: bool) -> Path:
    destination = unique_destination(destination)
    operations.append({"from": relative(source, root), "to": relative(destination, root)})
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    return destination


def role_label(role: str | None) -> str:
    return {"invoice": "发票", "payment": "付款记录", "supporting": "补充材料"}.get(role, "待识别")


def expense_filename(
    expense_id: str,
    expense: dict[str, Any],
    role: str,
    suffix: str,
    role_number: int,
    document_amount: Decimal | str | None = None,
) -> str:
    label = role_label(role)
    if role_number > 1:
        label = f"{label}_{role_number:02d}"
    filename_amount = document_amount if document_amount not in (None, "") else expense.get("amount")
    return safe_name(
        f"{expense_id}_{expense.get('label') or '待识别费用'}_{amount_text(filename_amount)}_{label}"
    ) + suffix.lower()


def rename_expense_documents(
    root: Path,
    state: dict[str, Any],
    expense_id: str,
    operations: list[dict[str, str]],
    dry_run: bool,
) -> int:
    expense = state.get("expenses", {}).get(expense_id)
    if not expense:
        return 0
    renamed = 0
    role_counts: dict[str, int] = {}
    for digest in expense.get("documents", []):
        record = state.get("documents", {}).get(digest)
        if not record or record.get("status") != "organized":
            continue
        source = absolute(root, record.get("current_path", ""))
        if not source.is_file():
            continue
        role = str(record.get("role") or "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        requested = active_case_path(root, state) / expense_filename(
            expense_id,
            expense,
            role,
            source.suffix,
            role_counts[role],
            record.get("amount"),
        )
        try:
            unchanged = source.resolve() == requested.resolve()
        except OSError:
            unchanged = source == requested
        if unchanged:
            record["expense_label"] = expense.get("label")
            continue
        destination = move_file(root, source, requested, operations, dry_run)
        record["current_path"] = relative(destination, root)
        record["expense_label"] = expense.get("label")
        renamed += 1
    return renamed


def review_filename(item: dict[str, Any], reason: str) -> str:
    return safe_name(f"{reason}_{Path(item['original_name']).stem}") + item["source"].suffix.lower()


def review_item(
    root: Path,
    item: dict[str, Any],
    state: dict[str, Any],
    reason: str,
    operations: list[dict[str, str]],
    dry_run: bool,
) -> None:
    backup_source(root, item, dry_run)
    destination = move_file(
        root,
        item["source"],
        active_case_path(root, state) / review_filename(item, reason),
        operations,
        dry_run,
    )
    record = {
        "original_name": item["original_name"],
        "current_path": relative(destination, root),
        "role": item.get("role"),
        "role_confidence": item.get("role_confidence"),
        "category": item.get("category"),
        "category_confidence": item.get("category_confidence"),
        "merchant": item.get("merchant"),
        "amount": str(item["amount"]) if item.get("amount") is not None else None,
        "status": "review",
        "review_reason": reason,
        "inference_evidence": item.get("inference_evidence", []),
        "decision_source": item.get("decision_source", "explicit-confirmation"),
        "model_provider": item.get("model_provider"),
        "model_confidence": item.get("model_confidence"),
        "model_project_id": item.get("model_project_id"),
        "model_expense_key": item.get("model_expense_key"),
        "in_scope": item.get("in_scope"),
        "scope_reason": item.get("scope_reason"),
        "expense_label": item.get("expense_label"),
        "expense_type_key": item.get("expense_type_key"),
        "material_type": item.get("material_type"),
        "applicable_requirement_ids": item.get("applicable_requirement_ids", []),
        "assessed_condition_rule_ids": item.get("assessed_condition_rule_ids", []),
        "date_tokens": item.get("date_tokens", []),
        "reference_tokens": item.get("reference_tokens", []),
        "added_at": item.get("added_at") or now_iso(),
        "last_evaluated_at": now_iso(),
    }
    state["documents"][item["sha256"]] = record


def organize_item(
    root: Path,
    item: dict[str, Any],
    state: dict[str, Any],
    config: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
    preferred_expense_id: str | None = None,
) -> str | None:
    role = item.get("role")
    if role not in {"invoice", "payment", "supporting"}:
        review_item(root, item, state, "待识别类型", operations, dry_run)
        return None

    if preferred_expense_id:
        matched_id = preferred_expense_id if preferred_expense_id in state.get("expenses", {}) else None
    else:
        matched_id = None

    if item.get("category") not in config.get("categories", {}):
        review_item(root, item, state, "待确认费用类型", operations, dry_run)
        return None
    if not item.get("expense_label") and not matched_id:
        review_item(root, item, state, "待确认费用名称", operations, dry_run)
        return None

    if matched_id:
        expense = state["expenses"][matched_id]
    else:
        if role == "supporting":
            review_item(root, item, state, "补充材料待配对", operations, dry_run)
            return None
        matched_id = next_expense_id(state)
        expense = {
            "label": item["expense_label"],
            "expense_type_key": item.get("expense_type_key"),
            "category": item["category"],
            "merchant": item.get("merchant") or "未识别商户",
            "amount": str(item["amount"]) if item.get("amount") is not None else None,
            "documents": [],
            "created_at": now_iso(),
            "applicable_requirement_ids": list(item.get("applicable_requirement_ids", [])),
            "assessed_condition_rule_ids": list(item.get("assessed_condition_rule_ids", [])),
        }
        state["expenses"][matched_id] = expense

    item_expense_type = item.get("expense_type_key")
    if (
        item_expense_type
        and expense.get("expense_type_key")
        and item_expense_type != expense.get("expense_type_key")
    ):
        review_item(root, item, state, "费用类型冲突", operations, dry_run)
        return None
    if item_expense_type and not expense.get("expense_type_key"):
        expense["expense_type_key"] = item_expense_type
    expense["applicable_requirement_ids"] = sorted(set(
        expense.get("applicable_requirement_ids", [])
    ) | set(item.get("applicable_requirement_ids", [])))
    expense["assessed_condition_rule_ids"] = sorted(set(
        expense.get("assessed_condition_rule_ids", [])
    ) | set(item.get("assessed_condition_rule_ids", [])))

    item_amount = as_amount(item.get("amount"))
    if as_amount(expense.get("amount")) is None and item_amount is not None:
        expense["amount"] = str(item_amount)
    if not expense.get("label") and item.get("expense_label"):
        expense["label"] = item["expense_label"]
    if (not expense.get("merchant") or "未识别" in expense.get("merchant", "")) and item.get("merchant"):
        expense["merchant"] = item["merchant"]

    category = expense["category"]
    role_number = expense_roles(expense, state).count(role) + 1
    filename = expense_filename(
        matched_id, expense, role, item["source"].suffix, role_number, item_amount
    )
    backup_source(root, item, dry_run)
    destination = move_file(root, item["source"], active_case_path(root, state) / filename, operations, dry_run)
    state["documents"][item["sha256"]] = {
        "original_name": item["original_name"],
        "current_path": relative(destination, root),
        "expense_id": matched_id,
        "role": role,
        "role_confidence": item.get("role_confidence"),
        "category": category,
        "category_confidence": item.get("category_confidence"),
        "merchant": item.get("merchant"),
        "amount": str(item_amount) if item_amount is not None else None,
        "status": "organized",
        "inference_evidence": item.get("inference_evidence", []),
        "decision_source": item.get("decision_source", "explicit-confirmation"),
        "model_provider": item.get("model_provider"),
        "model_confidence": item.get("model_confidence"),
        "model_project_id": item.get("model_project_id"),
        "model_expense_key": item.get("model_expense_key"),
        "in_scope": item.get("in_scope"),
        "scope_reason": item.get("scope_reason"),
        "expense_label": expense.get("label"),
        "expense_type_key": expense.get("expense_type_key"),
        "material_type": item.get("material_type"),
        "applicable_requirement_ids": item.get("applicable_requirement_ids", []),
        "assessed_condition_rule_ids": item.get("assessed_condition_rule_ids", []),
        "date_tokens": item.get("date_tokens", []),
        "reference_tokens": item.get("reference_tokens", []),
        "added_at": item.get("added_at") or now_iso(),
        "resolved_at": now_iso() if item.get("from_observation") else None,
    }
    expense["documents"].append(item["sha256"])
    refresh_expense_amount_reconciliation(expense, state)
    return matched_id


def revisit_review_items(
    root: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
    prepared_items: dict[str, dict[str, Any]] | None = None,
    model_groups: dict[tuple[str, str, str], str] | None = None,
) -> int:
    """Re-evaluate uncertain files after new expenses add useful context."""
    if dry_run:
        return 0
    prepared_items = prepared_items or {}
    model_groups = model_groups if model_groups is not None else {}
    resolved = 0
    for digest, record in list(state.get("documents", {}).items()):
        if record.get("status") != "review":
            continue
        if digest in prepared_items and not prepared_items[digest].get("from_observation"):
            continue
        source = absolute(root, record.get("current_path", ""))
        if not source.exists() or not source.is_file():
            continue

        item = copy.copy(prepared_items.get(digest)) if digest in prepared_items else analyze(
            source, config, record.get("original_name")
        )
        item["sha256"] = digest
        item["added_at"] = record.get("added_at")
        item["from_observation"] = True
        for key in ("role", "category", "merchant", "amount", "expense_label"):
            if item.get(key) in (None, "", "未识别商户") and record.get(key) not in (None, ""):
                item[key] = record[key]

        if item.get("model_guard_reason"):
            record["role"] = item.get("role")
            record["role_confidence"] = item.get("role_confidence")
            record["category"] = item.get("category")
            record["category_confidence"] = item.get("category_confidence")
            record["merchant"] = item.get("merchant")
            record["amount"] = str(item["amount"]) if item.get("amount") is not None else None
            record["decision_source"] = item.get("decision_source")
            record["model_provider"] = item.get("model_provider")
            record["model_confidence"] = item.get("model_confidence")
            record["model_expense_key"] = item.get("model_expense_key")
            record["in_scope"] = item.get("in_scope")
            record["scope_reason"] = item.get("scope_reason")
            record["expense_label"] = item.get("expense_label")
            record["inference_evidence"] = item.get("inference_evidence", [])
            record["date_tokens"] = item.get("date_tokens", [])
            record["reference_tokens"] = item.get("reference_tokens", [])
            record["last_evaluated_at"] = now_iso()
            continue

        preferred_expense_id = preferred_model_expense(item, state, model_groups)
        if preferred_expense_id:
            matched_id = preferred_expense_id
        else:
            matched_id = None
        evidence: list[str] = list(item.get("inference_evidence", []))
        if matched_id:
            evidence.append(f"大模型指定配对 {matched_id}")

        can_organize = (
            item.get("role") in {"invoice", "payment", "supporting"}
            and item.get("category") in config.get("categories", {})
            and (item.get("role") != "supporting" or matched_id is not None)
        )
        if can_organize:
            item["inference_evidence"] = evidence
            organized_id = organize_item(
                root,
                item,
                state,
                config,
                operations,
                dry_run,
                preferred_expense_id=matched_id,
            )
            remember_model_expense(item, state, organized_id, model_groups)
            if state["documents"].get(digest, {}).get("status") == "organized":
                resolved += 1
                continue

        record["role"] = item.get("role")
        record["role_confidence"] = item.get("role_confidence")
        record["category"] = item.get("category")
        record["category_confidence"] = item.get("category_confidence")
        record["merchant"] = item.get("merchant")
        record["amount"] = str(item["amount"]) if item.get("amount") is not None else None
        record["expense_label"] = item.get("expense_label")
        record["inference_evidence"] = evidence
        record["decision_source"] = item.get("decision_source", record.get("decision_source"))
        record["model_provider"] = item.get("model_provider", record.get("model_provider"))
        record["model_confidence"] = item.get("model_confidence", record.get("model_confidence"))
        record["date_tokens"] = item.get("date_tokens", [])
        record["reference_tokens"] = item.get("reference_tokens", [])
        record["last_evaluated_at"] = now_iso()
    return resolved


def route_project_for_item(
    item: dict[str, Any], registry: dict[str, Any], explicit_project: str | None = None
) -> tuple[dict[str, Any] | None, list[str]]:
    if explicit_project:
        project = select_project(registry, explicit_project, include_archived=False)
        return project, ["用户消息已明确项目"]
    if item.get("decision_source") != MODEL_DECISION_SOURCE:
        return None, []
    project_id = item.get("model_project_id")
    project = registry.get("projects", {}).get(project_id)
    if project and project.get("status") != "archived":
        return project, ["大模型根据项目语义和既有费用判定"]
    return None, []


def model_expense_group_key(item: dict[str, Any], state: dict[str, Any]) -> tuple[str, str, str] | None:
    expense_key = str(item.get("model_expense_key") or "")
    if not expense_key.upper().startswith("NEW-"):
        return None
    return (
        str(state.get("project_id")),
        str(item.get("model_batch_id") or "B000"),
        expense_key.upper(),
    )


def preferred_model_expense(
    item: dict[str, Any],
    state: dict[str, Any],
    groups: dict[tuple[str, str, str], str],
) -> str | None:
    expense_key = str(item.get("model_expense_key") or "")
    if expense_key in state.get("expenses", {}):
        return expense_key
    group_key = model_expense_group_key(item, state)
    return groups.get(group_key) if group_key else None


def remember_model_expense(
    item: dict[str, Any],
    state: dict[str, Any],
    expense_id: str | None,
    groups: dict[tuple[str, str, str], str],
) -> None:
    group_key = model_expense_group_key(item, state)
    if group_key and expense_id:
        groups[group_key] = expense_id


def known_document_owner(
    registry: dict[str, Any], digest: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for project in registry.get("projects", {}).values():
        if digest in project.get("documents", {}):
            return project, project["documents"][digest]
    record = registry.get("unassigned_documents", {}).get(digest)
    return None, record


def recover_missing_document(
    root: Path,
    item: dict[str, Any],
    record: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
) -> bool:
    """Restore a registered document when its canonical path has gone missing."""
    recorded_path = str(record.get("current_path") or "").strip()
    if not recorded_path:
        return False
    destination = absolute(root, recorded_path)
    try:
        relative(destination, root)
    except ValueError:
        return False
    if destination.exists():
        return False

    backup_source(root, item, dry_run)
    destination = move_file(root, item["source"], destination, operations, dry_run)
    record["current_path"] = relative(destination, root)
    record["recovered_at"] = now_iso()
    evidence = record.setdefault("inference_evidence", [])
    recovery_evidence = "相同 SHA-256 的文件重新到达，已恢复缺失的原登记文件"
    if recovery_evidence not in evidence:
        evidence.append(recovery_evidence)
    return True


def hold_unassigned(
    root: Path,
    item: dict[str, Any],
    registry: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
) -> None:
    backup_source(root, item, dry_run)
    destination = move_file(
        root,
        item["source"],
        root / INBOX / UNASSIGNED_FOLDER / safe_name(item["original_name"]),
        operations,
        dry_run,
    )
    registry.setdefault("unassigned_documents", {})[item["sha256"]] = {
        "original_name": item["original_name"],
        "current_path": relative(destination, root),
        "role": item.get("role"),
        "category": item.get("category"),
        "merchant": item.get("merchant"),
        "amount": str(item["amount"]) if item.get("amount") is not None else None,
        "inference_evidence": item.get("inference_evidence", []),
        "decision_source": item.get("decision_source", "explicit-confirmation"),
        "model_provider": item.get("model_provider"),
        "model_confidence": item.get("model_confidence"),
        "model_project_id": item.get("model_project_id"),
        "model_expense_key": item.get("model_expense_key"),
        "in_scope": item.get("in_scope"),
        "scope_reason": item.get("scope_reason"),
        "expense_label": item.get("expense_label"),
        "date_tokens": item.get("date_tokens", []),
        "reference_tokens": item.get("reference_tokens", []),
        "status": "out_of_scope" if item.get("in_scope") is False else "unassigned",
        "added_at": now_iso(),
        "last_evaluated_at": now_iso(),
    }


def revisit_unassigned_documents(
    root: Path,
    registry: dict[str, Any],
    config: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
    prepared_items: dict[str, dict[str, Any]] | None = None,
    model_groups: dict[tuple[str, str, str], str] | None = None,
) -> int:
    if dry_run:
        return 0
    prepared_items = prepared_items or {}
    model_groups = model_groups if model_groups is not None else {}
    resolved = 0
    for digest, record in list(registry.get("unassigned_documents", {}).items()):
        if record.get("status") == "out_of_scope":
            continue
        if digest in prepared_items and not prepared_items[digest].get("from_observation"):
            continue
        source = absolute(root, record.get("current_path", ""))
        if not source.exists():
            continue
        item = copy.copy(prepared_items.get(digest)) if digest in prepared_items else analyze(
            source, config, record.get("original_name")
        )
        item["sha256"] = digest
        item["added_at"] = record.get("added_at")
        if item.get("in_scope") is False:
            record["status"] = "out_of_scope"
            record["in_scope"] = False
            record["scope_reason"] = item.get("scope_reason")
            record["decision_source"] = item.get("decision_source", record.get("decision_source"))
            record["model_provider"] = item.get("model_provider", record.get("model_provider"))
            record["model_confidence"] = item.get("model_confidence", record.get("model_confidence"))
            record["inference_evidence"] = item.get("inference_evidence", [])
            record["last_evaluated_at"] = now_iso()
            continue
        project, evidence = route_project_for_item(item, registry)
        if not project:
            record["decision_source"] = item.get("decision_source", record.get("decision_source"))
            record["model_provider"] = item.get("model_provider", record.get("model_provider"))
            record["model_confidence"] = item.get("model_confidence", record.get("model_confidence"))
            record["model_project_id"] = item.get("model_project_id")
            record["model_expense_key"] = item.get("model_expense_key")
            record["expense_label"] = item.get("expense_label")
            record["role"] = item.get("role")
            record["category"] = item.get("category")
            record["merchant"] = item.get("merchant")
            record["amount"] = str(item["amount"]) if item.get("amount") is not None else None
            record["date_tokens"] = item.get("date_tokens", [])
            record["reference_tokens"] = item.get("reference_tokens", [])
            record["inference_evidence"] = item.get("inference_evidence", [])
            record["last_evaluated_at"] = now_iso()
            continue
        del registry["unassigned_documents"][digest]
        item["from_observation"] = True
        item["inference_evidence"] = [*item.get("inference_evidence", []), *evidence]
        if item.get("model_guard_reason"):
            review_item(
                root,
                item,
                project,
                item["model_guard_reason"],
                operations,
                False,
            )
        else:
            preferred = preferred_model_expense(item, project, model_groups)
            organized_id = organize_item(
                root,
                item,
                project,
                config,
                operations,
                False,
                preferred_expense_id=preferred,
            )
            remember_model_expense(item, project, organized_id, model_groups)
        project["status"] = "collecting"
        project.pop("ready_at", None)
        if project.get("documents", {}).get(digest, {}).get("status") == "organized":
            resolved += 1
    return resolved


def unassigned_candidates_for_project(
    registry: dict[str, Any],
    project: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        record
        for record in registry.get("unassigned_documents", {}).values()
        if record.get("model_project_id") == project.get("project_id")
    ]


def handle_duplicate(
    root: Path,
    item: dict[str, Any],
    state: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
) -> None:
    backup_source(root, item, dry_run)
    destination = move_file(
        root,
        item["source"],
        active_case_path(root, state) / review_filename(item, "重复文件"),
        operations,
        dry_run,
    )
    state.setdefault("duplicates", []).append({
        "sha256": item["sha256"],
        "original_name": item["original_name"],
        "current_path": relative(destination, root),
        "detected_at": now_iso(),
    })


def handle_global_duplicate(
    root: Path,
    item: dict[str, Any],
    registry: dict[str, Any],
    operations: list[dict[str, str]],
    dry_run: bool,
) -> None:
    backup_source(root, item, dry_run)
    destination = move_file(
        root,
        item["source"],
        root / INBOX / UNASSIGNED_FOLDER / review_filename(item, "重复文件"),
        operations,
        dry_run,
    )
    registry.setdefault("duplicates", []).append({
        "sha256": item["sha256"],
        "original_name": item["original_name"],
        "current_path": relative(destination, root),
        "detected_at": now_iso(),
    })


def collect_observation_items(
    root: Path, registry: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for project in registry.get("projects", {}).values():
        if project.get("status") == "archived":
            continue
        for digest, record in project.get("documents", {}).items():
            if record.get("status") != "review":
                continue
            if record.get("in_scope") is False:
                continue
            source = absolute(root, record.get("current_path", ""))
            if not source.is_file():
                continue
            item = analyze(source, config, record.get("original_name"))
            item["sha256"] = digest
            item["observed_project_id"] = project.get("project_id")
            item["from_observation"] = True
            items.append(item)
    for digest, record in registry.get("unassigned_documents", {}).items():
        if record.get("status") == "out_of_scope":
            continue
        source = absolute(root, record.get("current_path", ""))
        if not source.is_file():
            continue
        item = analyze(source, config, record.get("original_name"))
        item["sha256"] = digest
        item["from_observation"] = True
        items.append(item)
    return items


def process_routed_inputs(
    root: Path,
    registry: dict[str, Any],
    config: dict[str, Any],
    dry_run: bool,
    before_registry: dict[str, Any] | None,
    explicit_project: str | None = None,
    force_model_revisit: bool = False,
) -> dict[str, Any]:
    candidates = discover_inputs(root)
    analyzed_candidates = [analyze(source, config) for source in candidates]
    operations: list[dict[str, str]] = []
    results = {
        "discovered": len(candidates),
        "organized": 0,
        "resolved": 0,
        "unassigned": 0,
        "out_of_scope": 0,
        "recovered": 0,
        "duplicates": 0,
        "routed_projects": {},
    }
    observation_items = (
        collect_observation_items(root, registry, config)
        if candidates or force_model_revisit
        else []
    )
    model_items = [*analyzed_candidates, *observation_items]
    decisions, model_report = run_model_decisions(
        root, model_items, registry, config, explicit_project
    )
    config_before_model = json.dumps(config, ensure_ascii=False, sort_keys=True)
    prepared_items: dict[str, dict[str, Any]] = {}
    model_active = model_report.get("status") in {"used", "partial", "failed", "disabled"}
    for item in model_items:
        decision = decisions.get(item["sha256"])
        if decision:
            apply_model_decision(item, decision, config)
        elif model_active:
            mark_model_unresolved(item, model_report)
        prepared_items[item["sha256"]] = item
    if not dry_run and json.dumps(config, ensure_ascii=False, sort_keys=True) != config_before_model:
        write_json(metadata_paths(root)["config"], config)

    results["model_status"] = model_report.get("status")
    results["model_processed"] = model_report.get("processed", 0)
    results["model_reason"] = model_report.get("reason")
    model_groups: dict[tuple[str, str, str], str] = {}
    processing_candidates = sorted(
        analyzed_candidates,
        key=lambda item: (item.get("role") == "supporting", str(item.get("original_name", ""))),
    )
    for item in processing_candidates:
        owner, existing = known_document_owner(registry, item["sha256"])
        if existing:
            if owner and owner.get("status") != "archived":
                if recover_missing_document(root, item, existing, operations, dry_run):
                    results["recovered"] += 1
                else:
                    handle_duplicate(root, item, owner, operations, dry_run)
                    results["duplicates"] += 1
            elif owner is None and recover_missing_document(
                root, item, existing, operations, dry_run
            ):
                results["recovered"] += 1
            else:
                handle_global_duplicate(root, item, registry, operations, dry_run)
                results["duplicates"] += 1
            continue

        if item.get("in_scope") is False:
            hold_unassigned(root, item, registry, operations, dry_run)
            results["out_of_scope"] += 1
            continue

        project, evidence = route_project_for_item(item, registry, explicit_project)
        if not project:
            hold_unassigned(root, item, registry, operations, dry_run)
            results["unassigned"] += 1
            continue
        item["inference_evidence"] = [*item.get("inference_evidence", []), *evidence]
        if item.get("model_guard_reason"):
            review_item(
                root,
                item,
                project,
                item["model_guard_reason"],
                operations,
                dry_run,
            )
            organized_id = None
        else:
            preferred = preferred_model_expense(item, project, model_groups)
            organized_id = organize_item(
                root,
                item,
                project,
                config,
                operations,
                dry_run,
                preferred_expense_id=preferred,
            )
            remember_model_expense(item, project, organized_id, model_groups)
        project["status"] = "collecting"
        project.pop("ready_at", None)
        if organized_id:
            results["organized"] += 1
        name = str(project.get("case_name"))
        results["routed_projects"][name] = results["routed_projects"].get(name, 0) + 1

    allow_revisit = bool(observation_items) and model_active
    for project in registry.get("projects", {}).values():
        if project.get("status") == "archived":
            continue
        resolved = (
            revisit_review_items(
                root,
                project,
                config,
                operations,
                dry_run,
                prepared_items,
                model_groups,
            )
            if allow_revisit
            else 0
        )
        results["resolved"] += resolved
        results["organized"] += resolved
    unassigned_resolved = (
        revisit_unassigned_documents(
            root,
            registry,
            config,
            operations,
            dry_run,
            prepared_items,
            model_groups,
        )
        if allow_revisit
        else 0
    )
    results["resolved"] += unassigned_resolved
    results["organized"] += unassigned_resolved

    if not dry_run:
        save_project_registry(root, registry)
        if candidates or results["resolved"]:
            record_transaction(root, before_registry, registry, operations, results)

    open_projects = [
        project for project in registry.get("projects", {}).values()
        if project.get("status") != "archived"
    ]
    unassigned_records = list(registry.get("unassigned_documents", {}).values())
    results["summary"] = {
        "review_items": sum(build_summary(project)["review_items"] for project in open_projects),
        "duplicates": sum(build_summary(project)["duplicates"] for project in open_projects)
        + len(registry.get("duplicates", [])),
        "unassigned": sum(1 for item in unassigned_records if item.get("status") != "out_of_scope"),
        "out_of_scope": sum(1 for item in unassigned_records if item.get("status") == "out_of_scope"),
    }
    results["dry_run"] = dry_run
    return results


def record_transaction(
    root: Path,
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any],
    operations: list[dict[str, str]],
    results: dict[str, Any],
) -> None:
    transaction_id = f"TX-{timestamp()}-{hashlib.sha1(json.dumps(operations).encode()).hexdigest()[:6]}"
    transaction = {
        "id": transaction_id,
        "created_at": now_iso(),
        "undone": False,
        "before_state": before_state,
        "after_updated_at": after_state.get("updated_at"),
        "operations": operations,
        "results": results,
    }
    paths = metadata_paths(root)
    write_json(paths["transactions"] / f"{transaction_id}.json", transaction)
    with paths["history"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({
            "id": transaction_id,
            "created_at": transaction["created_at"],
            "operation_count": len(operations),
            "results": results,
        }, ensure_ascii=False) + "\n")


def expense_status(expense: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    reconciliation = refresh_expense_amount_reconciliation(expense, state)
    roles = expense_roles(expense, state)
    invoice_count = roles.count("invoice")
    payment_count = roles.count("payment")
    missing: list[str] = []
    amount_conflict = expense.get("amount_conflict")
    if amount_conflict:
        reason = amount_conflict.get("reason") or "材料金额不一致"
        invoice_total = amount_conflict.get("invoice_total")
        payment_total = amount_conflict.get("payment_total")
        if invoice_total is not None and payment_total is not None:
            missing.append(
                f"{reason}（发票 {amount_text(invoice_total)}，付款合计 {amount_text(payment_total)}）"
            )
        else:
            missing.append(reason)
    return {
        "invoice_count": invoice_count,
        "payment_count": payment_count,
        "supporting_count": roles.count("supporting"),
        "complete": not missing,
        "missing": missing,
        "amount_conflict": amount_conflict,
        "amount_reconciliation": reconciliation,
    }


def build_summary(state: dict[str, Any]) -> dict[str, Any]:
    details = {
        expense_id: expense_status(expense, state)
        for expense_id, expense in state.get("expenses", {}).items()
    }
    return {
        "claim_id": state.get("claim_id"),
        "case_name": state.get("case_name"),
        "status": state.get("status"),
        "documents": len(state.get("documents", {})),
        "expenses": len(details),
        "complete_expenses": sum(1 for item in details.values() if item["complete"]),
        "amount_conflicts": sum(1 for item in details.values() if item.get("amount_conflict")),
        "review_items": sum(
            1 for item in state.get("documents", {}).values() if item.get("status") == "review"
        ),
        "duplicates": len(state.get("duplicates", [])),
    }


def expense_state_text(details: dict[str, Any]) -> str:
    if details.get("complete"):
        return "完整"
    return "；".join(str(value) for value in details.get("missing", []))


def print_result(result: dict[str, Any]) -> None:
    summary = result["summary"]
    prefix = "预览完成" if result.get("dry_run") else "处理完成"
    print(
        f"{prefix}：接收 {result['discovered']} 个文件，自动整理 {result['organized']} 个，"
        f"其中根据新增线索补判 {result.get('resolved', 0)} 个。"
    )
    model_status = result.get("model_status")
    if model_status == "used":
        print(f"大模型判定：已完成 {result.get('model_processed', 0)} 个材料的语义分析。")
    elif model_status == "partial":
        print(
            f"大模型判定：完成 {result.get('model_processed', 0)} 个；"
            "未完成的材料已安全保留，稍后会重试。"
        )
    elif model_status == "failed":
        print("大模型判定暂不可用；材料已安全保留，未使用规则结果强行归类。")
    elif model_status == "disabled":
        print("大模型判定已禁用；材料已安全保留，不会使用本地规则替代识别。")
    if result.get("recovered"):
        print(f"已恢复 {result['recovered']} 个原登记路径缺失的材料，未计为重复文件。")
    if result.get("out_of_scope"):
        print(f"有 {result['out_of_scope']} 个非出差报销材料已安全保留，不会进入出差项目。")
    routed = result.get("routed_projects", {})
    if routed:
        print("项目归属：" + "；".join(f"{name} {count} 个" for name, count in routed.items()) + "。")
    if summary["review_items"] or summary["duplicates"] or summary.get("unassigned"):
        print("其余材料已进入持续核对，暂时不需要你处理；新增线索到达后会自动重算。")
    print("准备提交时，请说明项目，例如“北京出差报销要交给财务了”。")
