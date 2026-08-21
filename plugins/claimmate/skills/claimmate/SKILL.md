---
name: claimmate
description: Coordinate a business-travel reimbursement workspace and route work to focused ClaimMate skills. Use for first-time setup, broad ClaimMate help, or requests spanning project setup, document intake, requirements, finance delivery, and automation. Do not use for daily or non-travel reimbursement.
---

# ClaimMate

Coordinate the workflow and route execution:

- `$claimmate-projects`: initialize the workspace, record the user name, and manage trips.
- `$claimmate-intake`: ingest files, use the large model for semantic recognition and pairing, apply corrections, and undo organization.
- `$claimmate-requirements`: maintain and validate the single schema in `报销要求.xlsx`, including sourced finance-policy amendments.
- `$claimmate-finance`: check one trip against that schema, build its finance package, and archive it.
- `$claimmate-automation`: watch folders, configure email intake, and manage the background service.

Read [references/quick-start.md](references/quick-start.md) for the complete lifecycle. Do not duplicate specialized business logic here.

## Boundaries

- Only handle business-travel reimbursement.
- Users maintain one three-column schema: expense type, required materials, and other finance requirements. ClaimMate compiles machine IDs, alternatives, and conditional rules for recognition and finance checks.
- The large model owns semantic judgments: trip scope, expense type, material type, label, amount meaning, routing, pairing, and special-condition applicability.
- Deterministic code owns validation, identifiers, hashing, backups, file moves, audit history, undo, requirement evaluation, invoice-versus-summed-payments reconciliation, export, and archives.
- ClaimMate prepares packages; never submit without explicit authorization.

## First use

Use `$claimmate-projects` first. Ask for the user's name and whether to connect email, explaining that email integration automatically downloads and processes invoices, payment records, and other reimbursement attachments; never infer either answer. Initialization creates the folders and editable `报销要求.xlsx`, but does not process files or start the listener yet. Use `$claimmate-requirements` to show the full three-column Scheme, apply any previewed and confirmed edits, and ask the user to confirm the whole Scheme. Only `requirements-confirm --confirmed` completes setup, starts processing, installs the listener unless declined, and reveals the normal onboarding. If the user chose email, finish its secure configuration before Scheme confirmation. Email attachments that cannot yet be matched to a project stay in `待处理/待归属`; creating a project triggers one re-evaluation and the watcher must not repeatedly scan that holding folder.

Give onboarding in exactly three sections:

1. `新建项目`: create and manage multiple trips.
2. `更新材料`: chat, folder, and email intake; recognition; corrections; undo; requirements maintenance.
3. `交付财务`: named-trip readiness, missing-material review, itemized workbook, automatic ZIP packaging, and archive.

Keep the direct onboarding compact: one or two action-oriented bullets per section, then point to `开始使用 ClaimMate.md` for details. Always show the resolved absolute `待处理` folder path instead of only the folder name. Mention the three user-maintained workbook columns and summed-payment reconciliation; do not repeat implementation internals in the first-use card.

Do not show the three-section onboarding before setup is complete. Before confirmation, show the current Scheme and the remaining configuration step instead.

Reconcile collection-time uncertainty automatically. Raise only unresolved issues for the named trip at finance delivery unless the user asks earlier.
