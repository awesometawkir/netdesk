# Changelog

## Unreleased

- Stop now closes open SSH sessions immediately (partial logs kept); closing the app during a run stops and exits cleanly.
- A device that sends nothing for a set time (default 180 s, diagnostics at least 15 min) is closed and marked "Timed out" instead of hanging the run.
- One automatic reconnect for transient SSH channel errors such as "Unable to open channel" (never after commands started, never for unreachable hosts or bad logins).
- New session button; adding devices after a finished run asks "Start fresh" or "Keep them and add" (kept devices are unticked).
- Run tick boxes: only ticked devices are collected; successful devices are unticked after a run. Per-device "Stop selected device".
- Review screen lists every device, username and command before anything connects; plain-language status column and a result banner.
- Run history shows abandoned runs as "interrupted" instead of "running".
- Plain-language connection errors (unreachable, refused session, host key, unknown prompt) instead of long library text.
- Only one NetDesk window can run at a time; unexpected errors show a dialog and are logged to `logs/netdesk-gui-errors.log` instead of silently breaking.
- Progress updates can no longer freeze the window; a dead collection thread unlocks the UI. "Quit now" option if closing waits on connecting devices.
- Review screen warns when the same device is listed more than once; custom rows with no commands get a clear message.

- Added concurrent serial login and command collection for up to 16 ports, with per-port settings/credentials, presets and custom commands.
- Added a multi-port GUI and `netdesk serial-collect`, separate output folders, credential-filtered transcripts, partial output, cancellation, and duplicate/busy-port checks.
- Added virtual-device tests for three concurrent ports, login rejection, pagers, timeout/cancellation, disconnects and GUI progress.

- Added an **Add ranges & logins** table for multiple IP ranges or single devices, each with its own username/password, platform, preset and port.
- Added optional shared login across rows, validation before import, and automatic isolated login groups.
- Verified a 25-device, two-range simulated collection uses the intended credentials for every IP; 39 offline tests pass.

## 0.1.0 — 2026-11-16

Initial public release.

- Native Linux desktop GUI and preserved guided CLI.
- Multi-scenario collection with shared and per-device login groups.
- 15 platform families and 159 platform/scenario preset combinations.
- Parallel SSH sessions, same-endpoint serialization, partial logs, summaries and retries.
- Configuration comparison and saved-run browsing.
- Built-in serial recording and portable original serial helper workflows.
- Desktop installer, screenshots, sample plans/inventory, and offline test automation.

Presets are not physically validated on every platform. Model and software-specific limitations are documented in `docs/PRESETS.md`.
