---
name: claimmate-automation
description: Manage ClaimMate's background folder watcher and email intake plumbing. Use to install, inspect, or remove the listener, configure IMAP metadata, store credentials securely, or run one synchronization pass. Do not make semantic decisions or define policy.
---

# ClaimMate Automation

Own transport and scheduling only. Read [references/automation.md](references/automation.md) before watcher changes and [references/email-setup.md](references/email-setup.md) before email configuration.

## Commands

```powershell
python scripts/automation.py watch <folder> --once
python scripts/automation.py service-install <folder>
python scripts/automation.py service-status <folder>
python scripts/automation.py service-uninstall <folder>
python scripts/automation.py configure <folder> ...
python scripts/automation.py credentials-set <folder>
python scripts/automation.py credentials-delete <folder>
python scripts/automation.py email-sync <folder>
```

## Boundaries

- Watch for stable files, download eligible attachments atomically into `待处理`, and trigger the intake command.
- Do not install or start the listener until the user's name, email choice, and current Scheme are all confirmed. A later unconfirmed workbook edit pauses processing even if the listener process is still present.
- Never classify, route, pair, or rename here; `$claimmate-intake` owns those model decisions.
- Never edit the requirements workbook.
- Store secrets only in Windows Credential Manager or macOS Keychain. Never ask users to paste them into chat or store them in `automation.json`.
- On Windows prefer Task Scheduler; if rejected, fall back to an `HKCU` Run entry and hidden watcher without admin rights.
- The machine must be awake for immediate processing; reconciliation recovers missed events.
- Service failure must not roll back workspace initialization.
