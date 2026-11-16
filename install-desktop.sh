#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"$project_dir/.netdesk-venv/bin/python" -c 'import PySide6.QtWidgets, PySide6.QtSerialPort'
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
launcher="$HOME/.local/bin/netdesk-gui"
if [[ -e "$launcher" || -L "$launcher" ]]; then
    if [[ $(readlink -f -- "$launcher") != "$project_dir/netdesk-gui" ]]; then
        printf 'Another launcher exists at %s; leaving it untouched.\n' "$launcher" >&2
        exit 1
    fi
else
    ln -s "$project_dir/netdesk-gui" "$launcher"
fi
cp "$project_dir/assets/netdesk.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/netdesk.svg"
cat > "$HOME/.local/share/applications/netdesk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NetDesk
GenericName=Network collection and serial consoles
Comment=Collect device logs across scenarios, compare backups, and record serial sessions
Exec="$project_dir/netdesk-gui"
Icon=netdesk
Terminal=false
Categories=Network;
Keywords=SSH;Serial;Router;Switch;Logs;Backup;
StartupNotify=false
StartupWMClass=netdesk-gui
Actions=CLI;

[Desktop Action CLI]
Name=Open terminal menu
Exec=gnome-terminal -- "$project_dir/netdesk"
EOF
if command -v desktop-file-validate >/dev/null; then
    desktop-file-validate "$HOME/.local/share/applications/netdesk.desktop"
fi
if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi
printf 'NetDesk desktop app installed. Search Applications for NetDesk, or run netdesk gui.\n'
