# Usage guide

## Quickly add ranges with separate logins

**Collections → Add ranges & logins** opens a table with two initial rows. Enter an IP, inclusive range, subnet or list in each row, then that row's username and password. Every row can choose its own platform, preset and port. Add more rows as needed. Click the row number at the left to select rows for removal.

By default credentials are independent. The optional shared-login checkbox makes all rows use the first row's credentials; turning it off restores the individual fields. Incomplete or invalid rows are reported before anything is added or connected. Entirely blank rows are ignored.

**Add all ranges to collection** expands the addresses and creates distinct groups without overwriting existing credentials. Review the expanded device list and click **Collect all devices** to run. For custom commands, use the existing scenario editor or select added devices and choose **Edit commands**.

## Plans, credentials and selection

A plan contains one or more scenarios. Each expanded device becomes a job with its own command list and login-group name. Multiple devices may share one group, or each may have its own. Updating a named group changes the login for all devices using it. When selecting only part of a shared group, the GUI suggests a new group to keep the other devices unchanged.

Select rows with Ctrl/Shift. **Assign login** changes their group; **Edit commands** replaces their command list. Remove and re-add a scenario to change its targets or platform. Credentials can be entered when adding the scenario or later. Use **SSH & tools → Forget all session credentials** to clear the in-memory logins.

Save/load stores the exact commands, not a reference that silently changes when presets are updated. Loading an old plan therefore retains its original command list. Usernames and passwords are omitted. Saved plans can be used by `netdesk batch PLAN.json`; the CLI prompts once per group.

`examples/multi-scenario.json` illustrates shared credentials plus a per-device override. All example addresses are reserved for documentation. Neither import nor preview makes network connections.

## Address formats and inventories

Collection accepts IPv4/IPv6 addresses, CIDRs, and inclusive ranges. Hostnames are not accepted by the collector.

```text
192.0.2.10-20
192.0.2.10-192.0.2.20
192.0.2.0/28
192.0.2.10,198.51.100.10
2001:db8::1
```

An inventory CSV uses these fields:

```csv
name,ip,device_type,port
branch-router,192.0.2.10,cisco_ios,22
core-switch,198.51.100.10,huawei,22
```

The CSV importer does not read credentials. A missing name defaults to the IP; missing platform defaults to `huawei`; missing port defaults to 22. Imported inventories may mix platforms. Consult [PRESETS.md](PRESETS.md) for supported platform values.

## Commands, permissions and timeouts

Presets use operational commands. Accounts must already have the appropriate privileges; a separate enable-password workflow is not implemented. Netmiko handles platform paging during session preparation. If a command requires confirmation, configuration mode, or another interactive exchange, use the interactive SSH/serial terminal instead.

Custom command files have one command per line; blank lines and lines beginning with `#` are ignored. Custom commands execute as entered and are not restricted to read-only operations. Review the plan before starting.

Normal timeout: 1,200 seconds. Recognized long support commands: 7,200 seconds. Both can be changed in the GUI or with `--timeout` and `--diagnostic-timeout`. A timeout means the expected prompt did not return; partial output remains in the raw session log. Do not treat a timeout as a complete diagnostic capture.

The default concurrency is four devices; the supported range is 1–16. Different scenarios for the same endpoint are serialized in batch runs. Duplicate scenario/IP/port jobs are rejected. Commands are not automatically replayed following connection or command failures.

**Stop after current operations** stops queued jobs. Running operations must finish or hit their timeout. The GUI prevents closing during collection. **Retry** reruns all commands for failed/unfinished jobs, including commands that may have succeeded earlier on those jobs. Every retry creates a new run.

## Output files

Paths are anchored to the checkout, not the current working directory:

```text
logs/
  netdesk/
    <run>/
      summary.json
      summary.csv
      <job-and-device>/
        session.log
        01-command.txt
        02-command.txt
        configuration.txt
        result.json
  serial/
    <timestamp>-<port>-gui.log
```

- `session.log`: raw SSH stream, useful for incomplete commands.
- Numbered command files: completed command responses.
- `configuration.txt`: emitted only when a recognized configuration command completes without a recognized CLI error.
- `summary.json`: job definitions, settings and outcomes; authentication credentials excluded.
- `summary.csv`: scenario/device/IP/status/error table.
- `result.json`: per-device result.

Known CLI error patterns are reported as `command_error`. Unrecognized platform-specific errors can still appear in output; inspect results rather than relying solely on status.

**History & compare** previews the first 256 KB of a selected file. Open its folder to inspect larger files. Configuration comparison is a raw text diff, including timestamps emitted by a device. Batch comparisons match scenario name, IP, port and platform; keep scenario names stable across runs if you want to compare them.

## Automated multi-port serial collection

The GUI's **Collect multiple serial ports** window creates one collection job per selected serial cable. Each row independently chooses port, baud rate, platform, preset/custom commands and login. The optional shared-login checkbox takes credentials from the first row. Password-only logins can leave the username blank; already-authenticated consoles can leave both blank.

The collector sends a carriage return to wake the console, recognizes common `Username:`, `login:` and `Password:` prompts, and submits each credential at most once. A rejected login or repeated prompt stops that port rather than retrying and risking an account lockout. Device prompts such as `<Huawei>`, `router#`, `router>`, and shell-style `$` prompts are detected, then each command waits for that exact prompt to return. Unrecognized banners, menus, MFA and initial setup are not automated. Privileged commands require an already appropriate login mode; there is no separate enable-password step.

Known platforms receive a session paging command where available. Common `--More--` and similar pagers receive a space. FortiOS paging is handled this way rather than changing persistent console configuration. Custom commands that change the device prompt or request extra confirmation can time out; use an interactive console for them.

Every port runs on a separate worker (maximum 16). Duplicate paths, including aliases resolving to the same port, are rejected. The GUI also rejects its currently open interactive-console port. An exclusive serial lock is requested; close other terminal programs using the cable before starting.

Serial batch output lives in `logs/serial-batches/<run>/`:

- `summary.json` and `summary.csv`: overall results without supplied login credentials.
- One folder per port: filtered UTF-8 `session.log`, completed command text, per-port results, and a configuration file for recognized successful backup commands.
- Incomplete command output retains `.partial.txt` and is not promoted to a completed backup.

Unlike interactive raw-byte recording, automated collection filters the supplied username/password from its text transcripts, including values split across reads. Device output can still contain other configuration secrets. Authentication values remain in memory and are cleared from the dialog when it closes.

**Stop collection** stops reads and closes ports promptly, preserving partial files. It does **not** send a remote abort or guarantee that the device stops executing its last command. Timeouts also leave partial output. Per-port failures are isolated. Review the progress/error summary and use **Open this run’s logs** or **All serial collection logs**; these runs are separate from SSH history/comparisons.

CLI options:

```bash
netdesk serial-collect --ports /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 \
  --baud 115200 --platform cisco_ios --preset interfaces --individual-logins
netdesk serial-collect --ports /dev/ttyUSB0 /dev/ttyUSB1 \
  --commands-file commands.txt --login-timeout 30 --timeout 1200
```

Without `--individual-logins`, the CLI prompts once and shares the login. The CLI applies one baud/platform/preset to its port list; use the GUI for different per-port settings. `--dry-run` never opens a port. `--diagnostic-timeout` controls recognized long support commands.

Automated behavior is tested with PTY device emulators, not physical hardware. Console syntax, serial adapter behavior, and model-specific presets still require validation on your devices.

## Serial details

The GUI uses Qt SerialPort, with 8N1 and no flow control. It records received bytes; typed input appears only if echoed by the device. **Hide typing** masks the local input field, not remote echoes. The viewer removes common ANSI sequences but is not a complete terminal emulator. Use the picocom wizard for full terminal handling.

GUI logs and the newer `netdesk serial --port ... --log` flow use `logs/serial/`. The original serial helper workflow uses `~/serial-logs`. The GUI has buttons for both folders and the original wizard. Existing executable helpers in `~/.local/bin` take priority over bundled copies.

For a built-in UART, enter its path explicitly (for example `/dev/ttyS0`). Automatic discovery lists USB adapters, avoiding unused onboard UART device nodes.

## Installation and desktop troubleshooting

The app is installed per user; keep the source checkout in place. Run `./install.sh` after pulling dependency changes. Run `./install-desktop.sh` to refresh desktop registration without reinstalling dependencies.

If the app cannot load Qt's platform plugin, install the runtime libraries listed in the README and launch `./netdesk-gui` from a terminal to see the error. The installer targets Ubuntu/Debian-family Linux with GNOME Terminal; other terminals are not yet supported by the GUI's external console buttons.

If `netdesk` is not found, ensure `~/.local/bin` is in PATH or use `./netdesk`. The installer will not overwrite an unrelated command with the same name. Use `netdesk doctor` to check console dependencies and serial-port discovery.

An existing environment moved from another folder may contain stale shebangs. NetDesk uses its own `.netdesk-venv`. If moving the checkout, recreate that environment and update the launchers; saved logs/plans can be retained.

## Privacy and compatibility

Passwords are runtime-only and never accepted in CLI arguments. Both usernames and passwords are omitted from plans and summaries. Raw device output may include sensitive configuration or echoed input. Inventory files, plans and logs are excluded from Git by default; review anything you share separately.

Unknown SSH host keys are accepted by default for compatibility. Strict mode verifies `~/.ssh/known_hosts`; interactive SSH uses your system client's settings. No live credentials, inventories or network output are included in this repository.

Return codes for direct collection: 0 for success/preview, 1 for job failures or interrupted batch runs, 2 for invalid input, and 130 for cancellation outside active collection. The guided menu remains interactive and returns normally after individual actions.
