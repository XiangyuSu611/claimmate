---
name: claimmate-intake
description: Ingest and organize business-travel reimbursement files with large-model semantic recognition. Use for attachments, trip routing, expense and material classification, invoice-payment pairing, corrections, duplicate handling, and undo. Do not define requirements or decide finance readiness.
---

# ClaimMate Intake

Own document intake and organization. Read [references/folder-model.md](references/folder-model.md) when explaining layout.

## Commands

```powershell
python scripts/claimmate.py check <folder> [--project "合肥"] [--revisit]
python scripts/claimmate.py assign <folder> <file> --project "合肥"
python scripts/claimmate.py expense-label <folder> EXP-001 --project "合肥" --name "机票"
python scripts/claimmate.py expense-merge <folder> --project "合肥" --target EXP-002 --source EXP-004
python scripts/claimmate.py resolve <folder> <file> --project "合肥" --role payment --category 交通 --expense-name 机票
python scripts/claimmate.py undo <folder>
```

## Semantic boundary

- Send content, images when needed, trip context, known relationships, and the current requirements catalog to the configured large model.
- The model decides travel scope, trip, `expense_type_key`, `material_type`, label, merchant, final amount, dates, references, pairing, and applicable special-condition rule IDs.
- Do not use regexes, filename keywords, OCR text rules, or fixed maps to override semantic decisions. OCR only prepares model input.
- Deterministic code validates schemas and IDs, checks confidence/conflicts, hashes and backs up files, moves and renames them, records evidence, and supports undo.
- If the model fails or confidence is low, preserve the file for retry. Never guess.

## Organization

- All sources converge on `待处理`; copy chat attachments there before `check`.
- Matching SHA-256 means duplicate content. An invoice and payment screenshot are not duplicates merely because they describe one expense.
- Files sharing `EXP-###` form one expense. Use flat names such as `EXP-001_机票_1551元_发票.pdf`.
- Unknown routing stays in `待处理/待归属`; within a trip, store uncertainty in hidden state, not visible subdirectories.
- Reconcile silently during collection; raise unresolved questions at the named trip's finance checkpoint.
- User-confirmed corrections are authoritative and undoable.
- One invoice may share an expense ID with multiple payment records. Keep each payment's actual amount; deterministic reconciliation compares the invoice total with the sum of all payments and blocks underpayment, overpayment, or unknown amounts.

## Requirements consumption

Read `报销要求.xlsx` through its validated catalog to constrain allowed types and condition IDs. Do not edit it here; use `$claimmate-requirements`.
