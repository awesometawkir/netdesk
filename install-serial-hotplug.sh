#!/usr/bin/env bash
# Installs the per-user USB serial hotplug service (Linux desktop, systemd --user).
# New USB serial adapters open automatically in one dedicated WezTerm window,
# one pane per adapter. Run with --uninstall to remove the service.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
unit_dir="$HOME/.config/systemd/user"
unit="$unit_dir/netdesk-serial-hotplug.service"
config_dir="$HOME/.config/netdesk"

if [[ ${1:-} == --uninstall ]]; then
    systemctl --user disable --now netdesk-serial-hotplug.service 2>/dev/null || true
    rm -f "$unit"
    systemctl --user daemon-reload
    printf 'Serial hotplug service removed. Settings kept in %s\n' "$config_dir"
    exit 0
fi

command -v wezterm >/dev/null || { printf 'WezTerm is required for the hotplug consoles.\n' >&2; exit 1; }
python="$project_dir/.netdesk-venv/bin/python"
[[ -x $python ]] || { printf 'Run ./install.sh first to create %s\n' "$python" >&2; exit 1; }
id -nG | tr ' ' '\n' | grep -qx dialout || printf 'Warning: add yourself to the dialout group to open serial ports.\n' >&2

mkdir -p "$unit_dir" "$config_dir"
"$python" -c 'import sys; sys.path.insert(0, sys.argv[1]); import netdesk_serial_hotplug as h; h.ensure_settings_file()' "$project_dir"
[[ -e $config_dir/serial-wezterm.lua ]] || cp "$project_dir/packaging/linux/serial-wezterm.lua" "$config_dir/serial-wezterm.lua"

cat > "$unit" <<UNIT
[Unit]
Description=NetDesk: open new USB serial adapters in WezTerm
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart=$python $project_dir/netdesk_serial_hotplug.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now netdesk-serial-hotplug.service
systemctl --user restart netdesk-serial-hotplug.service
printf 'Serial hotplug service running. Baud settings: %s/serial-hotplug.json\n' "$config_dir"
