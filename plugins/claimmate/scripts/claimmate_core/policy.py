"""Single business schema for travel-expense material recognition and readiness."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


REQUIREMENTS_VERSION = 2
REQUIREMENTS_WORKBOOK = "报销要求.xlsx"
REQUIREMENTS_SNAPSHOT = "requirements.snapshot.json"
USER_REQUIREMENT_HEADERS = (
    "费用类型",
    "必须材料",
    "财务其他要求",
)
LEGACY_REQUIREMENT_HEADERS = (
    "规则编号",
    "费用类型ID",
    "费用类型",
    "费用大类ID",
    "费用类型别名",
    "适用条件",
    "必需材料",
    "可替代材料",
    "是否阻断",
    "是否启用",
    "说明",
)

BUILTIN_EXPENSE_TYPES: dict[str, dict[str, Any]] = {
    "机票": {"key": "flight", "category_key": "transport", "aliases": ["航空票", "飞机票"]},
    "高铁票": {"key": "rail", "category_key": "transport", "aliases": ["火车票", "动车票", "铁路票"]},
    "出租车票": {"key": "taxi", "category_key": "transport", "aliases": ["网约车票", "打车费", "出租车费"]},
    "住宿费": {"key": "accommodation", "category_key": "accommodation", "aliases": ["酒店费", "住宿"]},
    "注册费": {"key": "registration", "category_key": "registration", "aliases": ["会议注册费", "会务费"]},
    "保险": {"key": "insurance", "category_key": "insurance", "aliases": ["旅行保险", "境外保险", "差旅保险"]},
    "签证费": {"key": "visa", "category_key": "visa", "aliases": ["签证服务费"]},
    "餐费": {"key": "dining", "category_key": "dining", "aliases": ["出差餐费", "餐饮费"]},
    "会员费": {"key": "membership", "category_key": "membership", "aliases": ["参会会员费"]},
    "打印费": {"key": "printing", "category_key": "printing", "aliases": ["会议打印费", "材料打印费"]},
    "其他出差费用": {"key": "other", "category_key": "other", "aliases": ["其他差旅费"]},
}


def _bundle_root() -> Path:
    return Path(__file__).resolve().parents[2]


def requirements_schema_path() -> Path:
    return _bundle_root() / "assets" / "reimbursement-requirements.schema.json"


def requirements_template_path() -> Path:
    return _bundle_root() / "assets" / "报销要求模板.xlsx"


def requirements_workbook_path(root: Path) -> Path:
    return root / REQUIREMENTS_WORKBOOK


def requirements_snapshot_path(root: Path) -> Path:
    return root / ".claimmate" / REQUIREMENTS_SNAPSHOT


def ensure_requirements_workbook(root: Path) -> Path:
    destination = requirements_workbook_path(root)
    if destination.exists():
        return destination
    template = requirements_template_path()
    if not template.is_file():
        raise SystemExit("ClaimMate 缺少报销要求模板。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, destination)
    return destination


def _column_number(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    value = 0
    for character in letters.upper():
        value = value * 26 + ord(character) - 64
    return value


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", namespace))
        for item in root.findall("x:si", namespace)
    ]


def _worksheet_target(archive: zipfile.ZipFile, sheet_name: str) -> str:
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relations = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_relations = "http://schemas.openxmlformats.org/package/2006/relationships"
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relation_id = None
    for sheet in workbook.findall(f".//{{{main}}}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relation_id = sheet.attrib.get(f"{{{relations}}}id")
            break
    if not relation_id:
        raise SystemExit(f"{REQUIREMENTS_WORKBOOK} 缺少“{sheet_name}”工作表。")
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in rels.findall(f"{{{package_relations}}}Relationship"):
        if relation.attrib.get("Id") == relation_id:
            target = relation.attrib.get("Target", "")
            if target.startswith("/"):
                return target.lstrip("/")
            return "xl/" + target.lstrip("./")
    raise SystemExit(f"无法读取“{sheet_name}”工作表。")


def _xlsx_rows(path: Path, sheet_name: str = "报销要求") -> list[list[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            strings = _shared_strings(archive)
            target = _worksheet_target(archive, sheet_name)
            worksheet = ET.fromstring(archive.read(target))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        raise SystemExit(f"无法读取 {path.name}：{type(error).__name__}") from error
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rows: list[list[str]] = []
    for row in worksheet.findall(f".//{{{main}}}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{{{main}}}c"):
            column = _column_number(cell.attrib.get("r", "A1"))
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(
                    node.text or "" for node in cell.findall(f".//{{{main}}}t")
                )
            else:
                raw = cell.find(f"{{{main}}}v")
                value = raw.text if raw is not None and raw.text is not None else ""
                if cell_type == "s" and value:
                    try:
                        value = strings[int(value)]
                    except (ValueError, IndexError):
                        value = ""
            values[column] = value.strip()
        if values:
            rows.append([values.get(index, "") for index in range(1, max(values) + 1)])
    return rows


def user_requirement_rows(path: Path) -> list[dict[str, str]]:
    """Return only the three fields ordinary users maintain in the workbook."""
    rows = _xlsx_rows(path)
    header_index = next(
        (
            index for index, row in enumerate(rows)
            if all(header in row for header in USER_REQUIREMENT_HEADERS)
        ),
        None,
    )
    if header_index is None:
        # Validate through the normal parser to return its more specific error.
        catalog_from_workbook(path)
        raise SystemExit(f"{path.name} 不是三列用户 Scheme。")
    header = rows[header_index]
    positions = {name: header.index(name) for name in USER_REQUIREMENT_HEADERS}
    result: list[dict[str, str]] = []
    for row in rows[header_index + 1:]:
        values = {
            name: row[position].strip() if position < len(row) else ""
            for name, position in positions.items()
        }
        if not any(values.values()):
            continue
        if values["费用类型"] == "填写方法":
            break
        result.append(values)
    return result


def format_user_requirement_table(rows: list[dict[str, str]]) -> str:
    lines = [
        "| 费用类型 | 必须材料 | 财务其他要求 |",
        "|---|---|---|",
    ]
    for row in rows:
        values = [
            str(row.get(header) or "无").replace("|", "｜").replace("\n", " ")
            for header in USER_REQUIREMENT_HEADERS
        ]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def user_requirement_changes(
    before_rows: list[dict[str, str]], after_rows: list[dict[str, str]]
) -> list[str]:
    before = {str(row.get("费用类型") or ""): row for row in before_rows}
    after = {str(row.get("费用类型") or ""): row for row in after_rows}
    changes: list[str] = []
    for label in before:
        if label not in after:
            changes.append(f"删除费用类型：{label}")
    for label, row in after.items():
        previous = before.get(label)
        if previous is None:
            changes.append(
                f"新增费用类型：{label}（必须材料：{row.get('必须材料') or '无'}；"
                f"财务其他要求：{row.get('财务其他要求') or '无'}）"
            )
            continue
        fields = []
        for field in ("必须材料", "财务其他要求"):
            old = str(previous.get(field) or "无")
            new = str(row.get(field) or "无")
            if old != new:
                fields.append(f"{field}：{old} → {new}")
        if fields:
            changes.append(f"{label}：" + "；".join(fields))
    return changes


def _split_values(value: str) -> list[str]:
    return list(dict.fromkeys(
        item.strip() for item in re.split(r"[|｜;；、,，\n]+", value) if item.strip()
    ))


def _boolean(value: str, field: str, row_number: int) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"是", "启用", "true", "yes", "1"}:
        return True
    if normalized in {"否", "停用", "false", "no", "0"}:
        return False
    raise SystemExit(f"报销要求第 {row_number} 行的“{field}”只能填写是或否。")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _stable_rule_id(*parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"REQ-{digest.upper()}"


def _simple_material_groups(value: str, row_number: int) -> list[tuple[str, list[str]]]:
    groups = [
        item.strip() for item in re.split(r"[、,，;；\n]+", value) if item.strip()
    ]
    if not groups:
        raise SystemExit(f"报销要求第 {row_number} 行必须填写“必须材料”。")
    parsed: list[tuple[str, list[str]]] = []
    for group in groups:
        choices = [
            item.strip() for item in re.split(r"(?:或|[|｜])", group) if item.strip()
        ]
        if not choices:
            raise SystemExit(f"报销要求第 {row_number} 行存在无法读取的材料要求。")
        parsed.append((choices[0], list(dict.fromkeys(choices[1:]))))
    return parsed


def _finance_requirements(value: str, type_key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requirements: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for text in (item.strip() for item in re.split(r"[;；\n]+", value) if item.strip()):
        requirement_id = _stable_rule_id(type_key, "财务其他要求", text)
        requirement: dict[str, Any] = {
            "requirement_id": requirement_id,
            "text": text,
            "enforceable": False,
        }
        # 这里仅解析面向用户的 Schema 句式；费用是否满足条件仍由大模型判断。
        matched = re.fullmatch(r"(.+?)(?:还需|需要|必须提供)(.+)", text)
        if matched:
            condition = matched.group(1).strip()
            material = matched.group(2).strip(" ：:，,。")
            if condition and material:
                requirement["enforceable"] = True
                requirement["condition"] = condition
                requirement["required_material"] = material
                rules.append({
                    "rule_id": requirement_id,
                    "expense_type_key": type_key,
                    "condition": condition,
                    "required_material": material,
                    "alternatives": [],
                    "blocking": True,
                    "enabled": True,
                    "notes": text,
                    "source": "workspace-workbook-finance-requirement",
                })
        requirements.append(requirement)
    return requirements, rules


def _catalog_from_simple_rows(rows: list[list[str]], header_index: int) -> dict[str, Any]:
    header = rows[header_index]
    positions = {name: header.index(name) for name in USER_REQUIREMENT_HEADERS}
    expense_types: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    labels: set[str] = set()
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        values = {
            name: row[position].strip() if position < len(row) else ""
            for name, position in positions.items()
        }
        if not any(values.values()):
            continue
        label = values["费用类型"]
        if label == "填写方法":
            break
        if not label:
            raise SystemExit(f"报销要求第 {offset} 行必须填写“费用类型”。")
        normalized_label = _normalize(label)
        if normalized_label in labels:
            raise SystemExit(f"报销要求中存在重复费用类型：{label}")
        labels.add(normalized_label)
        builtin = BUILTIN_EXPENSE_TYPES.get(label)
        if builtin:
            type_key = str(builtin["key"])
            category_key = str(builtin["category_key"])
            aliases = list(builtin.get("aliases", []))
        else:
            type_key = "user-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
            category_key = "other"
            aliases = []
        finance_requirements, finance_rules = _finance_requirements(
            values["财务其他要求"], type_key
        )
        expense_types.append({
            "key": type_key,
            "label": label,
            "category_key": category_key,
            "aliases": aliases,
            "enabled": True,
            "finance_requirements": finance_requirements,
        })
        for required_material, alternatives in _simple_material_groups(
            values["必须材料"], offset
        ):
            rules.append({
                "rule_id": _stable_rule_id(
                    type_key, "全部", required_material, *alternatives
                ),
                "expense_type_key": type_key,
                "condition": "全部",
                "required_material": required_material,
                "alternatives": alternatives,
                "blocking": True,
                "enabled": True,
                "notes": None,
                "source": "workspace-workbook",
            })
        rules.extend(finance_rules)
    if not rules:
        raise SystemExit(f"{REQUIREMENTS_WORKBOOK} 中没有可读取的报销要求。")
    return {
        "version": REQUIREMENTS_VERSION,
        "expense_types": expense_types,
        "rules": rules,
    }


def _catalog_from_legacy_rows(rows: list[list[str]], header_index: int) -> dict[str, Any]:
    header = rows[header_index]
    positions = {name: header.index(name) for name in LEGACY_REQUIREMENT_HEADERS}
    expense_types: dict[str, dict[str, Any]] = {}
    rules: list[dict[str, Any]] = []
    rule_ids: set[str] = set()
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        values = {
            name: row[position].strip() if position < len(row) else ""
            for name, position in positions.items()
        }
        if not any(values.values()):
            continue
        rule_id = values["规则编号"].upper()
        type_key = values["费用类型ID"].casefold()
        label = values["费用类型"]
        category_key = values["费用大类ID"].casefold()
        required_material = values["必需材料"]
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,63}", rule_id):
            raise SystemExit(f"报销要求第 {offset} 行的规则编号格式不正确。")
        if rule_id in rule_ids:
            raise SystemExit(f"报销要求中存在重复规则编号：{rule_id}")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", type_key):
            raise SystemExit(f"报销要求第 {offset} 行的费用类型ID格式不正确。")
        if not label or not category_key or not required_material:
            raise SystemExit(
                f"报销要求第 {offset} 行必须填写费用类型、费用大类ID和必需材料。"
            )
        enabled = _boolean(values["是否启用"], "是否启用", offset)
        definition = {
            "key": type_key,
            "label": label,
            "category_key": category_key,
            "aliases": _split_values(values["费用类型别名"]),
            "enabled": enabled,
            "finance_requirements": [],
        }
        existing = expense_types.get(type_key)
        if existing and any(
            existing[field] != definition[field]
            for field in ("label", "category_key", "aliases", "enabled")
        ):
            raise SystemExit(f"费用类型 {type_key} 在不同规则行中的定义不一致。")
        expense_types[type_key] = definition
        rules.append({
            "rule_id": rule_id,
            "expense_type_key": type_key,
            "condition": values["适用条件"] or "全部",
            "required_material": required_material,
            "alternatives": _split_values(values["可替代材料"]),
            "blocking": _boolean(values["是否阻断"], "是否阻断", offset),
            "enabled": enabled,
            "notes": values["说明"] or None,
            "source": "workspace-workbook",
        })
        rule_ids.add(rule_id)
    if not rules:
        raise SystemExit(f"{REQUIREMENTS_WORKBOOK} 中没有启用或可读取的报销要求。")
    return {
        "version": REQUIREMENTS_VERSION,
        "expense_types": list(expense_types.values()),
        "rules": rules,
    }


def catalog_from_workbook(path: Path) -> dict[str, Any]:
    rows = _xlsx_rows(path)
    simple_header = next(
        (
            index for index, row in enumerate(rows)
            if set(USER_REQUIREMENT_HEADERS).issubset(set(row))
        ),
        None,
    )
    if simple_header is not None:
        return _catalog_from_simple_rows(rows, simple_header)
    legacy_header = next(
        (
            index for index, row in enumerate(rows)
            if set(LEGACY_REQUIREMENT_HEADERS).issubset(set(row))
        ),
        None,
    )
    if legacy_header is not None:
        return _catalog_from_legacy_rows(rows, legacy_header)
    raise SystemExit(
        f"{path.name} 的表头不完整；请保留“费用类型、必须材料、财务其他要求”三列。"
    )


def _material_display(value: str) -> str:
    choices = [item.strip() for item in re.split(r"(?:或|[|｜])", value) if item.strip()]
    return "或".join(choices)


def _append_display_values(existing: str, additions: list[str], separator: str) -> str:
    if separator == "、":
        current = [
            item.strip() for item in re.split(r"[、,，;；\n]+", existing) if item.strip()
        ]
    else:
        current = [
            item.strip() for item in re.split(r"[;；\n]+", existing) if item.strip()
        ]
    seen = {_normalize(item) for item in current}
    for addition in additions:
        if addition and _normalize(addition) not in seen:
            current.append(addition)
            seen.add(_normalize(addition))
    return separator.join(current)


def plan_requirement_workbook_change(
    path: Path,
    expense_type: str,
    required_materials: list[str],
    finance_requirements: list[str],
    mode: str = "append",
) -> dict[str, Any]:
    label = expense_type.strip()
    if not label:
        raise SystemExit("更新报销要求时必须指定费用类型。")
    if mode not in {"append", "replace"}:
        raise SystemExit(f"未知报销要求更新模式：{mode}")
    materials = list(dict.fromkeys(
        rendered for value in required_materials
        if (rendered := _material_display(str(value).strip()))
    ))
    finance = list(dict.fromkeys(
        str(value).strip() for value in finance_requirements if str(value).strip()
    ))
    rows = _xlsx_rows(path)
    header_index = next((
        index for index, row in enumerate(rows)
        if set(USER_REQUIREMENT_HEADERS).issubset(set(row))
    ), None)
    if header_index is None:
        raise SystemExit(
            f"{path.name} 不是三列用户维护格式，无法自动更新。"
        )
    header = rows[header_index]
    positions = {name: header.index(name) for name in USER_REQUIREMENT_HEADERS}
    before: dict[str, str] | None = None
    for row in rows[header_index + 1:]:
        row_label = row[positions["费用类型"]].strip() if positions["费用类型"] < len(row) else ""
        if row_label == "填写方法":
            break
        if _normalize(row_label) != _normalize(label):
            continue
        before = {
            name: row[position].strip() if position < len(row) else ""
            for name, position in positions.items()
        }
        break
    before_materials = before["必须材料"] if before else ""
    before_finance = before["财务其他要求"] if before else ""
    if mode == "replace":
        if not materials:
            raise SystemExit("替换模式必须提供至少一项必须材料。")
        after_materials = "、".join(materials)
        after_finance = "；".join(finance)
    else:
        after_materials = _append_display_values(before_materials, materials, "、")
        after_finance = _append_display_values(before_finance, finance, "；")
    if not after_materials:
        raise SystemExit("新增费用类型时必须提供至少一项必须材料。")
    after = {
        "费用类型": before["费用类型"] if before else label,
        "必须材料": after_materials,
        "财务其他要求": after_finance,
    }
    return {
        "mode": mode,
        "action": "add" if before is None else (
            "no_change" if before == after else "update"
        ),
        "expense_type": label,
        "before": before,
        "after": after,
    }


def _xml_cell_text(cell: ET.Element, strings: list[str]) -> str:
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{{{main}}}t"))
    raw = cell.find(f"{{{main}}}v")
    value = raw.text if raw is not None and raw.text is not None else ""
    if cell_type == "s" and value:
        try:
            return strings[int(value)]
        except (ValueError, IndexError):
            return ""
    return value


def _set_xml_cell_text(cell: ET.Element, value: str) -> None:
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    for child in list(cell):
        cell.remove(child)
    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{{{main}}}is")
    text = ET.SubElement(inline, f"{{{main}}}t")
    text.text = value


def _shift_cell_reference(reference: str, first_row: int, delta: int) -> str:
    def replace(match: re.Match[str]) -> str:
        column, marker, row_text = match.groups()
        row = int(row_text)
        return f"{column}{marker}{row + delta if row >= first_row else row}"
    return re.sub(r"(\$?[A-Z]+)(\$?)(\d+)", replace, reference)


def apply_requirement_workbook_change(path: Path, change: dict[str, Any]) -> None:
    if change.get("action") == "no_change":
        return
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relations = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ET.register_namespace("x", main)
    ET.register_namespace("r", relations)
    with zipfile.ZipFile(path) as source:
        members = {name: source.read(name) for name in source.namelist()}
        strings = _shared_strings(source)
        worksheet_name = _worksheet_target(source, "报销要求")
    worksheet = ET.fromstring(members[worksheet_name])
    sheet_data = worksheet.find(f"{{{main}}}sheetData")
    if sheet_data is None:
        raise SystemExit(f"{path.name} 缺少工作表数据。")

    rows = list(sheet_data.findall(f"{{{main}}}row"))
    header_row: ET.Element | None = None
    target_row: ET.Element | None = None
    data_rows: list[ET.Element] = []
    after = change["after"]
    for row in rows:
        cells = list(row.findall(f"{{{main}}}c"))
        values = [_xml_cell_text(cell, strings).strip() for cell in cells]
        if set(USER_REQUIREMENT_HEADERS).issubset(set(values)):
            header_row = row
            continue
        if header_row is None or not values:
            continue
        label = values[0] if values else ""
        if label == "填写方法":
            break
        if not label:
            continue
        data_rows.append(row)
        if _normalize(label) == _normalize(str(change["expense_type"])):
            target_row = row
    if header_row is None:
        raise SystemExit(f"{path.name} 缺少三列表头。")

    if target_row is None:
        if not data_rows:
            raise SystemExit(f"{path.name} 中没有可复制格式的数据行。")
        target_row = copy.deepcopy(data_rows[-1])
        new_row_number = int(data_rows[-1].attrib["r"]) + 1
        occupied = {
            int(row.attrib["r"]) for row in rows if row.attrib.get("r", "").isdigit()
        }
        if new_row_number in occupied:
            for row in reversed(rows):
                row_number = int(row.attrib.get("r", "0"))
                if row_number < new_row_number:
                    continue
                row.attrib["r"] = str(row_number + 1)
                for cell in row.findall(f"{{{main}}}c"):
                    cell.attrib["r"] = _shift_cell_reference(
                        cell.attrib.get("r", ""), new_row_number, 1
                    )
            merge_cells = worksheet.find(f"{{{main}}}mergeCells")
            if merge_cells is not None:
                for merge in merge_cells.findall(f"{{{main}}}mergeCell"):
                    merge.attrib["ref"] = _shift_cell_reference(
                        merge.attrib.get("ref", ""), new_row_number, 1
                    )
        target_row.attrib["r"] = str(new_row_number)
        for cell in target_row.findall(f"{{{main}}}c"):
            column = re.sub(r"\d+", "", cell.attrib.get("r", ""))
            cell.attrib["r"] = f"{column}{new_row_number}"
        inserted = False
        for index, row in enumerate(list(sheet_data)):
            if int(row.attrib.get("r", "0")) > new_row_number:
                sheet_data.insert(index, target_row)
                inserted = True
                break
        if not inserted:
            sheet_data.append(target_row)
        data_rows.append(target_row)

    cells_by_column = {
        re.sub(r"\d+", "", cell.attrib.get("r", "")): cell
        for cell in target_row.findall(f"{{{main}}}c")
    }
    for column, field in (("A", "费用类型"), ("B", "必须材料"), ("C", "财务其他要求")):
        cell = cells_by_column.get(column)
        if cell is None:
            cell = ET.SubElement(
                target_row,
                f"{{{main}}}c",
                {"r": f"{column}{target_row.attrib['r']}"},
            )
        _set_xml_cell_text(cell, str(after[field]))

    table_end = max(int(row.attrib["r"]) for row in data_rows)
    modified: dict[str, bytes] = {
        worksheet_name: ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)
    }
    header_number = int(header_row.attrib["r"])
    for name, payload in members.items():
        if not name.startswith("xl/tables/") or not name.endswith(".xml"):
            continue
        try:
            table = ET.fromstring(payload)
        except ET.ParseError:
            continue
        reference = table.attrib.get("ref", "")
        if not reference.startswith(f"A{header_number}:C"):
            continue
        new_reference = f"A{header_number}:C{table_end}"
        table.attrib["ref"] = new_reference
        auto_filter = table.find(f"{{{main}}}autoFilter")
        if auto_filter is not None:
            auto_filter.attrib["ref"] = new_reference
        modified[name] = ET.tostring(table, encoding="utf-8", xml_declaration=True)

    temporary = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as destination:
        for name, payload in members.items():
            destination.writestr(name, modified.get(name, payload))
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_requirement_catalog(
    root: Path, *, persist_snapshot: bool = False
) -> dict[str, Any]:
    workbook = ensure_requirements_workbook(root)
    try:
        catalog = catalog_from_workbook(workbook)
    except SystemExit as error:
        snapshot = requirements_snapshot_path(root)
        if not snapshot.is_file():
            raise
        try:
            catalog = json.loads(snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise error
        catalog["source"] = {
            **copy.deepcopy(catalog.get("source") or {}),
            "fallback_snapshot": snapshot.name,
            "workbook_validation_error": str(error),
            "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        return catalog
    catalog["source"] = {
        "path": workbook.name,
        "sha256": _sha256(workbook),
        "loaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if persist_snapshot:
        snapshot = requirements_snapshot_path(root)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        temporary = snapshot.with_suffix(snapshot.suffix + ".tmp")
        temporary.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(snapshot)
    return catalog


def catalog_for_model(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": catalog.get("version"),
        "expense_types": [
            copy.deepcopy(item) for item in catalog.get("expense_types", [])
            if item.get("enabled", True)
        ],
        "rules": [
            copy.deepcopy(item) for item in catalog.get("rules", [])
            if item.get("enabled", True)
        ],
    }


def expense_type_definition(
    catalog: dict[str, Any], key: str | None
) -> dict[str, Any] | None:
    if not key:
        return None
    return next(
        (
            item for item in catalog.get("expense_types", [])
            if item.get("enabled", True) and item.get("key") == key
        ),
        None,
    )


def expense_type_from_label(catalog: dict[str, Any], label: str | None) -> str | None:
    normalized = _normalize(label or "")
    if not normalized:
        return None
    for item in catalog.get("expense_types", []):
        names = [item.get("label", ""), *item.get("aliases", [])]
        if normalized in {_normalize(str(value)) for value in names if value}:
            return str(item.get("key"))
    return None


def evaluate_expense_requirements(
    state: dict[str, Any], expense_id: str, expense: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    type_key = expense.get("expense_type_key") or expense_type_from_label(
        catalog, expense.get("label")
    )
    definition = expense_type_definition(catalog, type_key)
    if not definition:
        return {
            "expense_id": expense_id,
            "expense_type_key": type_key,
            "passed": False,
            "checks": [],
            "gaps": ["费用类型尚未纳入报销要求"],
        }
    materials: set[str] = set()
    applicable: set[str] = set(expense.get("applicable_requirement_ids", []))
    assessed: set[str] = set(expense.get("assessed_condition_rule_ids", []))
    for digest in expense.get("documents", []):
        document = state.get("documents", {}).get(digest, {})
        material = document.get("material_type")
        if material:
            materials.add(_normalize(str(material)))
        elif document.get("role") == "invoice":
            materials.add(_normalize("发票"))
        elif document.get("role") == "payment":
            materials.add(_normalize("付款记录"))
        applicable.update(document.get("applicable_requirement_ids", []))
        assessed.update(document.get("assessed_condition_rule_ids", []))
    checks: list[dict[str, Any]] = []
    gaps: list[str] = []
    for rule in catalog.get("rules", []):
        if not rule.get("enabled", True) or rule.get("expense_type_key") != type_key:
            continue
        condition = str(rule.get("condition") or "全部").strip()
        if condition not in {"全部", "始终", "all"}:
            if rule.get("rule_id") in applicable:
                applies = True
            elif rule.get("rule_id") in assessed:
                applies = False
            else:
                if rule.get("blocking", True):
                    gaps.append(f"特殊条件尚未判断：{condition}")
                checks.append({
                    "rule_id": rule.get("rule_id"),
                    "condition": condition,
                    "status": "unassessed",
                })
                continue
            if not applies:
                checks.append({
                    "rule_id": rule.get("rule_id"),
                    "condition": condition,
                    "status": "not_applicable",
                })
                continue
        accepted = [rule.get("required_material"), *rule.get("alternatives", [])]
        present = any(_normalize(str(value)) in materials for value in accepted if value)
        checks.append({
            "rule_id": rule.get("rule_id"),
            "condition": condition,
            "required_material": rule.get("required_material"),
            "alternatives": rule.get("alternatives", []),
            "blocking": rule.get("blocking", True),
            "status": "passed" if present else "missing",
        })
        if not present and rule.get("blocking", True):
            alternatives = rule.get("alternatives", [])
            label = str(rule.get("required_material"))
            if alternatives:
                label += "（可替代：" + "、".join(alternatives) + "）"
            gaps.append(label)
    return {
        "expense_id": expense_id,
        "expense_type_key": type_key,
        "expense_type_label": definition.get("label"),
        "materials": sorted(materials),
        "passed": not gaps,
        "checks": checks,
        "gaps": gaps,
    }


def evaluate_project_requirements(
    state: dict[str, Any], catalog: dict[str, Any]
) -> dict[str, Any]:
    expenses = [
        evaluate_expense_requirements(state, expense_id, expense, catalog)
        for expense_id, expense in sorted(state.get("expenses", {}).items())
    ]
    return {
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog_source": copy.deepcopy(catalog.get("source")),
        "expenses": expenses,
        "gaps": [
            {"expense_id": item["expense_id"], "missing": item["gaps"]}
            for item in expenses if item["gaps"]
        ],
        "passed": all(item["passed"] for item in expenses),
    }
