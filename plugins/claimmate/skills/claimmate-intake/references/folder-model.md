# Folder model

ClaimMate uses one selected folder as a reimbursement workspace and tracks multiple active projects at the same time.

```text
报销文件夹/
├─ 待处理/
│  └─ 待归属/
├─ 流程中/
│  ├─ 2026-08-18至08-20_南京/
│  │  ├─ EXP-001_机票_1551元_发票.pdf
│  │  ├─ EXP-001_机票_1551元_付款记录.png
│  │  ├─ EXP-002_住宿费_680元_发票.pdf
│  │  └─ 大模型置信度不足_原文件名.jpg
│  └─ 2026-08_合肥/
├─ 已结束/
│  └─ 2026-07-02至07-05_杭州/
├─ 开始使用 ClaimMate.md
└─ .claimmate/
   ├─ finance-feedback.json
   └─ feedback-sources/
```

Name travel projects as `YYYY-MM-DD至MM-DD_地点` when exact dates are known; `YYYY-MM_地点出差` is valid while only the month is known. Use `YYYY-MM_事项` for non-travel reimbursement. Multiple folders under `流程中` may receive files concurrently. `待处理/待归属` holds files whose project is not yet clear. After a project is known, uncertain files stay directly in that project's root with a concise reason prefix such as `大模型置信度不足_` or `重复文件_`; there is no visible `待确认` subdirectory. These are internal observation states and are not surfaced until a relevant finance-readiness check.

```text
流程中/2026-08-18至08-20_南京/
├─ EXP-003_餐费_268元_发票.pdf
└─ EXP-003_餐费_268元_付款记录.png
```

Every trip project is flat: do not create category, expense, or review subdirectories. The model supplies a concise expense label for the filename. Prefer familiar travel names such as `机票`, `高铁票`, `出租车票`, `注册费`, `住宿费`, `会员费`, `打印费`, and `餐费`; allow equally concise labels only for other in-scope travel expenses. Non-travel material remains outside trip projects. The deterministic layer validates and sanitizes the label but does not infer it from a category or merchant.

Exports are written into the named project folder. When relevant finance feedback exists, the export also contains `财务反馈审查依据.md`; the structured snapshot is embedded in `处理报告.json`. On archive, that whole project folder moves from `流程中` to `已结束`, with its ZIP snapshot inside. For multiple documents with the same role, append a sequence such as `_发票_02`. Keep the project registry, finance-feedback ledger and source copies, routing evidence, backups, hashes, and transaction history under the hidden `.claimmate/`; users should not need to edit that directory.
