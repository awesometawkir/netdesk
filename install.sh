#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if command -v uv >/dev/null 2>&1; then
    [[ -x "$project_dir/.netdesk-venv/bin/python" ]] || uv venv --python /usr/bin/python3 "$project_dir/.netdesk-venv"
    uv pip install --python "$project_dir/.netdesk-venv/bin/python" -r "$project_dir/requirements.txt"
else
    /usr/bin/python3 -m venv "$project_dir/.netdesk-venv"
    "$project_dir/.netdesk-venv/bin/python" -m pip install -r "$project_dir/requirements.txt"
fi
mkdir -p "$HOME/.local/bin"
launcher="$HOME/.local/bin/netdesk"
if [[ -e "$launcher" || -L "$launcher" ]]; then
    if [[ $(readlink -f -- "$launcher") != "$project_dir/netdesk" ]]; then
        printf 'Another netdesk command exists at %s; leaving it untouched.\n' "$launcher" >&2
        exit 1
    fi
else
    ln -s "$project_dir/netdesk" "$launcher"
fi
printf 'Installed. Run: netdesk\n'

"$project_dir/install-desktop.sh"
