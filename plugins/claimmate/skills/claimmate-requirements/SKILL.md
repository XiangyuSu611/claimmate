---
name: claimmate-requirements
description: Own ClaimMate's single reimbursement-requirements schema. Use to explain, inspect, validate, or update expense types, required materials, accepted alternatives, special conditions, and sourced finance-policy amendments. Do not classify files or deliver claims.
---

# ClaimMate Requirements

Own one business schema, exposed as editable `报销要求.xlsx` in the workspace root. Intake and finance consume the same validated catalog. Read [references/schema.md](references/schema.md) before changes.

## User-facing schema

The workbook exposes only `费用类型`, `必须材料`, and `财务其他要求`. One row defines one expense type. Separate required materials with `、`; use `或` inside one material group for alternatives. A finance requirement may use natural wording such as `网约车还需行程单`: ClaimMate compiles the wording into an internal condition/material rule, while the model decides whether the condition applies to a concrete expense.

## Ownership boundary

- This skill changes and validates the catalog; it does not recognize documents.
- `$claimmate-intake` provides model-recognized types and condition applicability.
- `$claimmate-finance` evaluates recognized materials against the catalog.
- Global finance feedback becomes a workbook change only after the model extracts the proposed fields, the user chooses scope, sees the before/after diff, and explicitly confirms. Project-only feedback remains a temporary sourced requirement and never changes the global workbook. Preserve original wording and provenance in both cases.

## Maintenance

- Prefer workbook edits over JSON maintenance.
- Keep machine IDs, aliases, categories, rule IDs, blocking flags, and enablement in the generated internal catalog rather than exposing them to ordinary users.
- Validate the three columns, duplicate expense types, required-material groups, alternatives, and supported conditional wording.
- Keep the last valid snapshot under `.claimmate/requirements.snapshot.json`. If the workbook is invalid, report row errors and use the last valid snapshot.
- Model-proposed unknown types remain proposals until explicitly accepted.

## First configuration

After `$claimmate-projects` records the user's name and email choice, show the entire current workbook:

```powershell
python scripts/claimmate.py requirements-show <folder>
```

If the user requests a change, use the structured fields extracted from their instruction, preview it, and apply it only after explicit confirmation:

```powershell
python scripts/claimmate.py requirements-change <folder> --expense-type 餐费 --require-evidence 参会名单 --preview
python scripts/claimmate.py requirements-change <folder> --expense-type 餐费 --require-evidence 参会名单 --confirmed
```

Show the whole Scheme again. Only when the user confirms the complete table, finish setup:

```powershell
python scripts/claimmate.py requirements-confirm <folder> --confirmed
```

Until then, ClaimMate may create folders and projects but must not process materials or start the listener. Confirmation records the workbook hash; a later manual workbook edit pauses processing until the changed Scheme is shown and confirmed again. A confirmed finance-feedback or `requirements-change` update refreshes the recorded hash because that exact diff was already approved.

Validate user edits and refresh the last-valid snapshot with:

```powershell
python scripts/claimmate.py requirements-validate <folder>
```

## Finance feedback

```powershell
python scripts/claimmate.py feedback-add <folder> --text "原文" --expense-type 出租车票 --finance-requirement "网约车还需行程单" --apply-to-scheme --preview
python scripts/claimmate.py feedback-add <folder> --text "原文" --expense-type 出租车票 --finance-requirement "网约车还需行程单" --apply-to-scheme --confirmed
python scripts/claimmate.py feedback-add <folder> --text "原文" --project "合肥" --expense-type 出租车票 --require-evidence "行程单|行程信息" --preview
python scripts/claimmate.py feedback-add <folder> --text "原文" --project "合肥" --expense-type 出租车票 --require-evidence "行程单|行程信息" --confirmed
python scripts/claimmate.py feedback-list <folder> [--project "合肥"] [--include-inactive]
python scripts/claimmate.py feedback-status <folder> FB-001 --status inactive --reason "政策更新"
```

Use the large model to extract only `费用类型`, `必须材料`, and `财务其他要求` from the raw feedback. Ask whether it applies to all future trips or only one named trip. Always run `--preview` and show its exact diff before asking for confirmation; never infer confirmation from the feedback itself. After explicit confirmation, rerun with the same structured fields and `--confirmed`.

- Global scope: add `--apply-to-scheme`; ClaimMate backs up and updates `报销要求.xlsx`, validates it, refreshes the snapshot, and records the diff and source.
- Project scope: add `--project`; ClaimMate records a temporary requirement for that trip and leaves the workbook unchanged.
- Use `--scheme-mode replace` only when the preview contains the complete replacement row and the user confirms removal of old values. Default to append.
- Only explicit material requirements block delivery. Use `|` for alternatives.
- `feedback-status inactive` may stop a project-scoped or legacy amendment. It must not undo a global requirement already incorporated into the workbook; propose, preview, and confirm a new Scheme change instead.
