---
name: claimmate-finance
description: Prepare one named business trip for finance using ClaimMate's reimbursement-requirements schema. Use for readiness checks, missing-material review, itemized reimbursement workbooks, exports, and archives. Do not classify new documents or mutate requirements.
---

# ClaimMate Finance

Own the final named-trip delivery checkpoint.

## Commands

```powershell
python scripts/claimmate.py ready <folder> --project "合肥"
python scripts/claimmate.py export <folder> --project "合肥"
python scripts/claimmate.py archive <folder> --project "合肥"
```

## Flow

1. Run one final reconciliation through `$claimmate-intake`.
2. Load validated `报销要求.xlsx` and applicable sourced amendments.
3. Deterministically compare recognized expenses/materials with applicable rules.
4. Report only this trip's unresolved files, duplicates, conflicts, missing profile, and missing blocking materials, citing rule IDs.
5. Reconcile each invoice total against the sum of all matched payment records. Partial, excess, or unknown payment totals block delivery.
6. When complete, create `报销明细表.xlsx` and `.csv`, one row per expense, including payment total and difference. Default `收款人` to the stored user name.
7. Export requirement review, missing-material list, processing report, and policy evidence.
8. Archive after a passing export; force only after explicit acceptance.

## Boundaries

- Semantic fields come from the model; formal requirements come from the one catalog.
- Invoice plus payment is not hard-coded outside the schema; if required, they appear as two rules.
- Do not change the schema here; use `$claimmate-requirements`.
- Do not submit to external finance systems without separate authorization.
