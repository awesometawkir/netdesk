"""Separate packaged resources from writable user data."""
import os
import sys
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parent


def data_root():
    override = os.environ.get('NETDESK_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if not getattr(sys, 'frozen', False):
        return RESOURCE_ROOT
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'NetDesk'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'NetDesk'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'netdesk'


def cli_command(*args):
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).with_name('NetDeskCLI.exe' if sys.platform == 'win32' else 'NetDeskCLI')
        return [str(executable), *args]
    return [sys.executable, str(RESOURCE_ROOT / 'netdesk.py'), *args]
