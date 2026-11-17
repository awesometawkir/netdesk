# Contributing

NetDesk is a Linux desktop and CLI application. Changes should preserve both entry points and avoid introducing network activity during imports or tests.

## Development

```bash
python3 -m venv .netdesk-venv
.netdesk-venv/bin/python -m pip install -r requirements.txt
QT_QPA_PLATFORM=offscreen .netdesk-venv/bin/python -m unittest discover -s tests -v
```

The tests mock SSH connections and use a pseudo-terminal for serial communication. Do not replace these with real-device connections. Screenshots can be regenerated with:

```bash
QT_QPA_PLATFORM=offscreen .netdesk-venv/bin/python tools/render_screenshots.py
```

## Source map

- `netdesk.py`: CLI, address/inventory parsing, single-device collection, history and comparisons.
- `netdesk_batch.py`: saved plan validation, credential-group assignment, concurrent jobs and summaries.
- `netdesk_presets.py`: platform and scenario command catalog; update the preset reference when editing it.
- `netdesk_gui.py`: PySide6 desktop UI and serial text console; collection workers communicate through a queue.
- `netdesk_serial.py`: bounded login/prompt handling and concurrent serial collection; test against PTY emulators.
- `netdesk_serial_gui.py`: multi-port serial settings and progress window.
- `tools/serial/`: portable copies of the original terminal serial helpers.
- `install.sh`, `install-desktop.sh`: environment, launchers and desktop registration.

## Conventions

- Never commit device inventories, collected output, authentication secrets, or virtual environments.
- Keep authentication credentials in memory. Saved plans and summaries must use an explicit field whitelist.
- Preserve separate output folders per job, bounded concurrency, timeout reporting, and partial session logs.
- Avoid concurrent diagnostics on the same IP/port across scenarios.
- All GUI mutations must run on the Qt thread.
- Test failure/cancellation behavior and keep example addresses in the documentation-only IPv4 ranges.

Please describe the problem, resulting behavior, and tests in pull requests. Hardware-specific reports should include the platform/software version and a sanitized error message, never a full unredacted configuration or password.
