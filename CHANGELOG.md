# Changelog

## Unreleased

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
