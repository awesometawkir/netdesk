# NetDesk desktop packages

NetDesk is a local desktop application. Collection, command discovery and the
built-in serial console do not require a web service, account or internet access.
SSH collection needs access to your devices; serial collection needs the relevant
USB driver and OS permission to use the serial port.

## Run a packaged app

Extract the entire archive, then open **NetDesk** (Linux) or **NetDesk.exe**
(Windows). Keep the `_internal` folder beside the executable. No Python install,
virtual environment, pip command or repository checkout is needed on the target
PC. `NetDeskCLI` is the companion command-line executable.

A separate build is required for each OS and CPU architecture. This is not a
single universal executable. The local Linux x86-64 build was made on Ubuntu
24.04 / glibc 2.39; it targets compatible x86-64 Linux systems, not older glibc
releases. The supplied CI workflow builds on Ubuntu 22.04 for a broader Linux
baseline and separately on Windows. Windows builds must be built and tested on
Windows before claiming Windows support. macOS packaging is not provided yet.

Linux needs a graphical session and Qt's system runtime dependencies (commonly
libegl1, libopengl0, libxkbcommon0, libxcb-cursor0 and the standard XCB libraries).
Interactive SSH uses an installed SSH client and terminal. The core SSH collector
uses bundled Netmiko/Paramiko. Original shell-based serial wizards remain Linux
only; the built-in serial console and batch collection use Qt/pyserial.

## Data stays outside the application

Packaged apps save logs, plans and imported inventories under:

- Windows: `%LOCALAPPDATA%\NetDesk`
- Linux: `$XDG_DATA_HOME/netdesk`, normally `~/.local/share/netdesk`

Use **App data folder** in the sidebar to locate them. Set `NETDESK_DATA_DIR` to
choose another writable directory (for example a folder on a USB drive).
Credentials remain memory-only and are not copied between computers.
Source installations retain their existing project-local data paths.

## Build packages

On the target OS, with Python 3.12 available:

```text
python -m pip install -r requirements.txt "pyinstaller>=6.11,<7"
python packaging/build.py
```

Output is in `dist/NetDesk` plus a Linux tar.gz or Windows zip archive. The
PyInstaller specification includes only explicitly named public resources;
private inventories, saved plans, logs and credentials are not bundled.

`.github/workflows/portable.yml` provides a manually triggered Linux/Windows
build matrix, offline smoke tests and downloadable artifacts. It does not publish
a release. Preparing this workflow does not mean a Windows build has run.

See [PyInstaller's packaging requirements](https://www.pyinstaller.org/en/stable/usage.html)
for OS-specific builds and Linux glibc compatibility.

## Interface

The sidebar switches between Collections, Serial console, Run history and SSH
& tools. Collections has separate setup cards, a resizable device/command split,
and collapsible connection settings. Select a device to inspect its exact command
sequence. Table columns can be resized and scroll horizontally on smaller screens.

All numeric controls use English digits (`22`, `1200`, `7200`) regardless of the
computer's language. Text input remains Unicode; Bangla names are still supported.

## Automatic serial collections

Open **Serial collections → Automatic collection**. NetDesk enumerates serial
adapters on entry and every two seconds while idle, adding new ports without
opening them. Known USB serial ports on Linux and macOS and Windows COM ports
are recognized. An unplugged row stays in the list with its settings intact.

- Check **Use** for each port to collect, or use Select all / Select none.
- Enter each device's username/password, or enable the first-row shared login.
- Check the baud rate and platform: defaults are **9600 baud / Huawei**. Adapter
  detection cannot identify a router's CLI settings or credentials.
- Choose a preset, open **View / edit** for the command library, or choose custom
  and type directly in the preview. Existing custom drafts survive rescans.
- Click a row's **Remove** button to remove that specific port. It stays excluded
  from automatic additions until **Detect / restore ports** is clicked.
- Click **Collect selected ports**, then review and confirm. The app opens only
  selected ports, responds to supported login prompts, runs commands, handles
  common pagination and writes a separate folder for each device.

Logs are under the app data folder's `logs/serial-batches/<run>/`. Each run has a
summary; each device has `session.log`, command output files and (when collected)
`configuration.txt`. **Open this run's logs** opens the result folder. Credentials
are kept in memory and filtered from automated transcripts. Stop preserves
partial output. A port open in the Manual console cannot be collected at the same
time; manual connection is blocked while a serial collection is running.

Automatic login supports ordinary username/password, password-only and already
authenticated consoles. MFA, first-login setup and separate enable passwords are
not automated. Physical hardware and Windows execution still require validation;
local end-to-end tests use virtual serial devices.
