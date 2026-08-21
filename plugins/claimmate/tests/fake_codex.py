from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def amount_from(text: str) -> str | None:
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*元", text)
    if not match:
        match = re.search(r"(?<!\d)-\s*([0-9][0-9,]*\.\d{2})(?!\d)", text)
    if not match:
        return None
    return f"{float(match.group(1).replace(',', '')):.2f}"


def category_from(text: str) -> str | None:
    value = text.casefold()
    for category, terms in (
        ("dining", ("海底捞", "餐费", "餐饮", "dining")),
        ("accommodation", ("酒店", "住宿", "hotel")),
        ("transport", ("滴滴", "机票", "交通", "taxi", "flight", "train", "行程单", "itinerary")),
        ("registration", ("icra", "注册费", "registration")),
    ):
        if any(term in value for term in terms):
            return category
    return None


def in_travel_scope(text: str) -> bool:
    value = text.casefold()
    daily_terms = (
        "顺丰", "快递", "postage", "设备", "耗材", "equipment",
        "软件订阅", "劳务", "论文版面费", "动物实验",
    )
    return not any(term in value for term in daily_terms)


def expense_label_from(text: str, category: str | None) -> str | None:
    value = text.casefold()
    if category == "transport":
        if any(term in value for term in ("滴滴", "出租车", "taxi")):
            return "出租车票"
        if any(term in value for term in ("高铁", "火车", "train")):
            return "高铁票"
        if any(term in value for term in ("机票", "flight", "航空", "airline")):
            return "机票"
        return "交通费"
    return {
        "dining": "餐费",
        "accommodation": "住宿费",
        "registration": "注册费",
    }.get(category)


def expense_type_from(label: str | None) -> str | None:
    return {
        "机票": "flight",
        "高铁票": "rail",
        "出租车票": "taxi",
        "交通费": "other",
        "餐费": "dining",
        "住宿费": "accommodation",
        "注册费": "registration",
    }.get(label or "")


def material_type_from(text: str, role: str) -> str | None:
    value = text.casefold()
    if role == "invoice":
        return "发票"
    if role == "payment":
        return "付款记录"
    if "行程单" in value or "itinerary" in value:
        return "行程单"
    return "补充材料" if role == "supporting" else None


def role_from(text: str) -> str:
    value = text.casefold()
    if "发票" in value or "invoice" in value:
        return "invoice"
    if any(term in value for term in ("付款记录", "支付记录", "payment", "交易号")):
        return "payment"
    if any(term in value for term in ("行程单", "行程信息", "itinerary", "项目说明")):
        return "supporting"
    return "unknown"


def merchant_from(text: str) -> str | None:
    value = text.casefold()
    for merchant, terms in (
        ("海底捞", ("海底捞",)),
        ("北京酒店", ("北京酒店", "beijing hotel")),
        ("南京酒店", ("南京酒店", "nanjing hotel")),
        ("滴滴", ("滴滴", "taxi")),
        ("ICRA", ("icra",)),
        ("顺丰", ("顺丰", "postage")),
        ("餐费", ("餐费",)),
    ):
        if any(term in value for term in terms):
            return merchant
    return None


def references_from(text: str) -> list[str]:
    return sorted({
        match.group(1).casefold()
        for match in re.finditer(
            r"(?:订单号|交易号|流水号)\s*[:：]?\s*([a-z0-9][a-z0-9_-]{5,63})",
            text,
            flags=re.IGNORECASE,
        )
    })


def dates_from(text: str) -> list[str]:
    return sorted({
        f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        for match in re.finditer(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    })


def project_for(document: dict, parsed: dict, payload: dict) -> str | None:
    if payload.get("explicit_project_id"):
        return str(payload["explicit_project_id"])
    combined = f"{document.get('filename', '')} {document.get('text', '')}".casefold()
    for project in payload.get("active_projects", []):
        name = str(project.get("name", ""))
        if (
            ("北京" in name and ("北京" in combined or "beijing" in combined))
            or ("南京" in name and ("南京" in combined or "nanjing" in combined))
            or ("奥地利" in name and ("奥地利" in combined or "austria" in combined))
            or ("合肥" in name and "合肥" in combined)
        ):
            return str(project["project_id"])
    observed = document.get("currently_recorded_project_id")
    if observed and any(project.get("project_id") == observed for project in payload.get("active_projects", [])):
        return str(observed)
    projects = payload.get("active_projects", [])
    return str(projects[0]["project_id"]) if len(projects) == 1 else None


def main() -> None:
    arguments = sys.argv[1:]
    output_path = Path(arguments[arguments.index("--output-last-message") + 1])
    prompt = sys.stdin.read()
    payload = json.loads(prompt.split("输入数据：", 1)[1].strip())
    documents = payload.get("documents", [])
    parsed: list[dict] = []
    for document in documents:
        combined = f"{document.get('filename', '')} {document.get('text', '')}"
        parsed.append({
            "in_scope": in_travel_scope(combined),
            "role": role_from(combined),
            "category": category_from(combined),
            "expense_label": expense_label_from(combined, category_from(combined)),
            "expense_type": expense_type_from(expense_label_from(combined, category_from(combined))),
            "material_type": material_type_from(combined, role_from(combined)),
            "merchant": merchant_from(combined),
            "amount": amount_from(combined),
            "dates": dates_from(combined),
            "references": references_from(combined),
            "evidence": [],
        })

    for index, current in enumerate(parsed):
        if not current["in_scope"]:
            continue
        if current["role"] != "unknown" and current["category"]:
            continue
        for peer_index, peer in enumerate(parsed):
            if peer_index == index or not peer["in_scope"] or peer["role"] != "invoice":
                continue
            shared_reference = bool(set(current["references"]) & set(peer["references"]))
            shared_identity = bool(
                current["amount"]
                and current["amount"] == peer["amount"]
                and current["merchant"]
                and current["merchant"] == peer["merchant"]
            )
            if shared_reference or shared_identity:
                current["role"] = "payment"
                current["category"] = peer["category"]
                current["expense_label"] = peer["expense_label"]
                current["expense_type"] = peer["expense_type"]
                current["material_type"] = "付款记录"
                current["merchant"] = current["merchant"] or peer["merchant"]
                current["amount"] = current["amount"] or peer["amount"]
                if shared_reference:
                    current["evidence"].append("订单、交易或票据编号一致")
                if set(current["dates"]) & set(peer["dates"]):
                    current["evidence"].append("日期一致")
                if shared_identity:
                    current["evidence"].append("同批发票的商户和金额一致")
                break

    group_numbers: dict[tuple, str] = {}
    decisions: list[dict] = []
    for document, current in zip(documents, parsed):
        project_id = project_for(document, current, payload) if current["in_scope"] else None
        expense_key = None
        project = next(
            (item for item in payload.get("active_projects", []) if item.get("project_id") == project_id),
            None,
        )
        if project and current["role"] != "unknown" and current["category"]:
            for expense in project.get("expenses", []):
                same_category = expense.get("category_key") == current["category"]
                same_amount = current["amount"] and str(expense.get("amount")) == current["amount"]
                same_merchant = current["merchant"] and expense.get("merchant") == current["merchant"]
                if same_category and (same_amount or same_merchant):
                    expense_key = str(expense["expense_id"])
                    break
            if not expense_key:
                group = (
                    project_id,
                    current["category"],
                    current["merchant"],
                )
                expense_key = group_numbers.setdefault(group, f"NEW-{len(group_numbers) + 1:03d}")

        needs_review = current["in_scope"] and bool(
            not project_id
            or current["role"] == "unknown"
            or not current["category"]
            or (current["role"] == "supporting" and not expense_key)
        )
        evidence = current["evidence"] or ["测试模型直接分析文件名和材料文字"]
        conditional_rules = [
            rule for rule in payload.get("reimbursement_requirements", {}).get("rules", [])
            if rule.get("expense_type_key") == current["expense_type"]
            and str(rule.get("condition") or "全部") not in {"全部", "始终", "all"}
        ]
        assessed_rule_ids = [str(rule["rule_id"]) for rule in conditional_rules]
        combined = f"{document.get('filename', '')} {document.get('text', '')}"
        applicable_rule_ids = [
            str(rule["rule_id"]) for rule in conditional_rules
            if str(rule.get("condition")) in combined
            or (str(rule.get("condition")) == "网约车" and "滴滴" in combined)
        ]
        decisions.append({
            "document_id": document["document_id"],
            "in_scope": current["in_scope"],
            "scope_reason": None if current["in_scope"] else "材料属于与具体出差无关的日常报销",
            "project_id": project_id,
            "role": current["role"] if current["in_scope"] else "unknown",
            "category_key": current["category"] if current["in_scope"] else None,
            "category_label": None,
            "expense_type_key": current["expense_type"] if current["in_scope"] else None,
            "expense_label": current["expense_label"] if current["in_scope"] else None,
            "expense_key": expense_key if current["in_scope"] else None,
            "material_type": current["material_type"] if current["in_scope"] else None,
            "applicable_requirement_ids": applicable_rule_ids if current["in_scope"] else [],
            "assessed_condition_rule_ids": assessed_rule_ids if current["in_scope"] else [],
            "merchant": current["merchant"] if current["in_scope"] else None,
            "amount": current["amount"] if current["in_scope"] else None,
            "date_tokens": current["dates"] if current["in_scope"] else [],
            "reference_tokens": current["references"] if current["in_scope"] else [],
            "confidence": 0.55 if needs_review else 0.94,
            "needs_review": needs_review,
            "evidence": evidence,
        })

    output_path.write_text(json.dumps({"decisions": decisions}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
