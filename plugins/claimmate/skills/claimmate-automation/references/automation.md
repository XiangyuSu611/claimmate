# Background automation

ClaimMate uses native filesystem notifications on both supported platforms: `ReadDirectoryChangesW` on Windows and `kqueue` on macOS. It sleeps while the workspace is idle, waits for a file's size and modification time to remain stable after an event, and runs a five-minute reconciliation scan to recover missed events. If the native listener cannot start, it automatically falls back to a low-frequency 15-second scan. A workspace operation lock prevents the watcher, email intake, and a manual check from mutating state simultaneously.

When stable files arrive, the listener runs the same model-assisted `check` pipeline as a manual scan. Claimmate invokes the authenticated Codex CLI with an ephemeral session, a read-only sandbox, no user MCP/config loading, and a strict JSON output schema. The model analyzes bounded document content and project context; it never moves files itself. The deterministic runtime validates the response, performs backups and moves, and holds failed or low-confidence decisions for retry. This processing sends eligible document content to the configured model provider and therefore is not local-only.

Workspace initialization only creates folders and the editable Scheme. The listener is installed and started after the user has confirmed their name, email choice, and the full current `报销要求.xlsx`. Use `requirements-confirm <folder> --confirmed --no-service` only when the user explicitly opts out or the environment is being tested. Service installation and watcher startup must refuse pending setup or an unconfirmed workbook hash. Configuration remains successful if the operating system rejects service installation; report the error and allow `service-install` to retry later.

## Commands

```powershell
python scripts/automation.py watch <folder> --once
python scripts/automation.py service-install <folder>
python scripts/automation.py service-status <folder>
python scripts/automation.py service-uninstall <folder>
```

- Windows first uses a per-workspace Task Scheduler entry triggered at logon. If local policy denies ordinary users permission to create scheduled tasks, ClaimMate falls back to an `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` entry and starts the watcher immediately with `pythonw.exe`; this fallback needs no administrator rights.
- macOS uses a per-workspace `~/Library/LaunchAgents` plist with `RunAtLoad` and `KeepAlive`.
- Native events require no third-party Python package. The watcher records its active mode in `.claimmate/activity.jsonl`.
- Installation copies a stable runtime into `.claimmate/runtime`; rerun `service-install` after a ClaimMate update to refresh it.
- Activity and errors are recorded under `.claimmate/`.
- `service-status` reports whether Windows is using Task Scheduler or the current-user startup fallback. `service-uninstall` removes either form and stops a watcher started by ClaimMate.

The local machine must be awake for immediate handling. Files added while it sleeps are recovered after wake-up. For a cloud-synchronized workspace, keep the same relative directory structure on each device. Hash deduplication catches repeated inputs, but configure email polling on only one device in version 0.1.
