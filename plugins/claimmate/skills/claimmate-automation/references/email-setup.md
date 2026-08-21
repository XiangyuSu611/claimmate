# Email intake

The built-in adapter supports IMAP mailboxes that accept an app password or durable access token. `INBOX` can be used directly and a `ClaimMate` label is not required. A dedicated ASCII mailbox or label is optional when the user controls routing and wants to reduce unrelated attachments. If an attachment cannot yet be matched to a project, keep it in `待处理/待归属`; the watcher excludes that holding folder, and creating a project triggers one re-evaluation.

During first setup, ask whether the user wants email intake. If they decline, record `skip` and continue to Scheme confirmation. If they choose email, complete the configuration below before confirming the Scheme; never infer an email choice from an address found elsewhere.

## Configure

```powershell
python scripts/automation.py configure <folder> \
  --email-provider gmail \
  --email-username user@example.com \
  --email-mailbox INBOX \
  --enable-email

python scripts/automation.py credentials-set <folder>
python scripts/automation.py email-sync <folder> --dry-run
```

Use `--email-provider imap --email-host <host>` for another compatible provider. The password or token is stored in Windows Credential Manager or macOS Keychain; it is never written to the workspace. Do not ask for it in chat.

ClaimMate tracks IMAP UID validity, the last processed UID, attachment hashes, and the final content hash. It writes each attachment to a temporary file, flushes it, and atomically renames it into `待处理`. Only then does the normal organization pipeline run.

Some organizational and Microsoft mailboxes require an OAuth refresh flow rather than IMAP password login. The current adapter does not implement that flow; use an authorized Codex email connector or add a provider-specific OAuth adapter instead of storing a normal account password.
