---
name: claimmate-projects
description: Initialize and manage ClaimMate business-travel workspaces and trip projects. Use to record the user's name, create, list, inspect, rename, or update trips. Do not classify documents, edit reimbursement requirements, or produce finance packages.
---

# ClaimMate Projects

Own workspace identity and trip metadata only. Resolve `scripts/claimmate.py` from this plugin.

## Commands

```powershell
python scripts/claimmate.py init <folder> --user-name "姓名" --email-choice skip [--case-name "时间_地点"]
python scripts/claimmate.py setup-email <folder> --choice connect|skip
python scripts/claimmate.py profile-set <folder> --user-name "新姓名"
python scripts/claimmate.py new <folder> --case-name "2026-07_合肥"
python scripts/claimmate.py list <folder>
python scripts/claimmate.py rename <folder> --project "合肥" --new-name "2026-07_合肥"
python scripts/claimmate.py status <folder> [--project "合肥"]
```

## Rules

- Ask for the user's name before first initialization; never infer it.
- Ask whether to connect email before first initialization; pass `connect` or `skip` only from the user's explicit answer.
- Use the stored name as the default `收款人`; update it only when asked.
- Name active trips `时间范围_地点`; approximate months are acceptable until exact dates are known.
- Keep active trips under `流程中`, archived trips under `已结束`, and visible trip folders flat.
- Never create non-travel reimbursement projects.
- Initialization creates the workspace and `报销要求.xlsx`; `$claimmate-requirements` owns display, changes, and confirmation.
- Do not process files or install the listener during pending setup. `$claimmate-requirements` completes setup after the user confirms the full Scheme, then `$claimmate-automation` owns installation and troubleshooting.
