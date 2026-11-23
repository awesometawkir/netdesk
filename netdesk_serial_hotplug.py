"""Linux desktop hotplug watcher. Owns only its dedicated WezTerm instance.

Every USB serial adapter gets one pane in a single "USB Serial Consoles" window.
Settings (~/.config/netdesk/serial-hotplug.json) reload automatically:
  default_baud   9600
  baud_by_port   {"<USB serial number> | <vid:pid> | /dev/ttyUSB0": 115200}
  notify         true   desktop notification on plug/unplug
  log            true   save device output under log_dir
  log_dir        <NetDesk data>/logs/serial/auto
"""
import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from serial.tools import list_ports

LOG = logging.getLogger('netdesk-serial-hotplug')
ROOT = Path(__file__).resolve().parent
SERVICE = 'netdesk-serial-hotplug.service'
SETTINGS_PATH = Path.home() / '.config/netdesk/serial-hotplug.json'
DEFAULT_SETTINGS = {'default_baud': 9600, 'baud_by_port': {}, 'notify': True, 'log': True}


def detect_usb_ports():
    found = {}
    for p in list_ports.comports():
        if p.vid is None and not Path(p.device).name.startswith(('ttyUSB', 'ttyACM')):
            continue
        found[p.device] = {
            'description': '' if (p.description or 'n/a') == 'n/a' else p.description,
            'serial': p.serial_number or '',
            'vid_pid': f'{p.vid:04x}:{p.pid:04x}' if p.vid is not None and p.pid is not None else '',
        }
    return found


def split_target(panes):
    pane = max(panes, key=lambda p: p['size']['cols'] * p['size']['rows'])
    direction = '--right' if pane['size']['cols'] >= pane['size']['rows'] * 2.8 else '--bottom'
    return pane['pane_id'], direction


def default_log_dir():
    try:
        from netdesk_paths import data_root
        return data_root() / 'logs' / 'serial' / 'auto'
    except ImportError:
        return ROOT / 'logs' / 'serial' / 'auto'


class SettingsFile:
    """Reloads the JSON settings whenever the file changes; keeps the last good copy."""

    def __init__(self, path):
        self.path = Path(path)
        self.stamp = None
        self.data = dict(DEFAULT_SETTINGS)

    def get(self):
        try:
            stamp = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            stamp = None
        if stamp != self.stamp:
            self.stamp = stamp
            try:
                loaded = json.loads(self.path.read_text()) if stamp else {}
                if not isinstance(loaded, dict):
                    raise ValueError('settings must be a JSON object')
                self.data = {**DEFAULT_SETTINGS, **loaded}
                LOG.info('Settings loaded from %s', self.path)
            except (OSError, ValueError) as error:
                LOG.error('Ignoring invalid settings %s: %s', self.path, error)
        return self.data


class WezTerm:
    """Drives one private WezTerm GUI process through its own socket only."""

    def __init__(self, config, window_class='NetDeskSerial'):
        self.config, self.window_class = str(config), window_class
        self.env = os.environ.copy()
        self.env.pop('WEZTERM_UNIX_SOCKET', None)
        self.env.pop('WEZTERM_PANE', None)
        self.process = None
        runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
        self.socket_dir = Path(runtime) / 'wezterm'

    def socket(self):
        if self.process is None or self.process.poll() is not None:
            self.process = None
            return None
        path = self.socket_dir / f'gui-sock-{self.process.pid}'
        return path if path.exists() else None

    def cli(self, *args):
        socket = self.socket()
        if socket is None:
            raise RuntimeError('The serial console window is not running.')
        env = dict(self.env, WEZTERM_UNIX_SOCKET=str(socket))
        return subprocess.run(['wezterm', '--config-file', self.config, 'cli', '--no-auto-start', *map(str, args)],
                              env=env, capture_output=True, text=True, timeout=5, check=True).stdout

    def panes(self):
        if self.socket() is None:
            return []
        try:
            return json.loads(self.cli('list', '--format', 'json'))
        except (subprocess.CalledProcessError, RuntimeError, ValueError):
            return []

    def raise_window(self):
        # Best effort on X11; Wayland compositors decide focus themselves.
        if shutil.which('wmctrl') and self.env.get('DISPLAY'):
            subprocess.run(['wmctrl', '-x', '-a', self.window_class], env=self.env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)

    def open(self, port, baud, panes, label='', log_dir=None):
        command = [sys.executable, str(ROOT / 'netdesk_serial_terminal.py'), '--port', port, '--baud', str(baud)]
        if label:
            command += ['--label', label]
        if log_dir:
            command += ['--log-dir', str(log_dir)]
        if panes and self.socket():
            target, direction = split_target(panes)
            pane_id = int(self.cli('split-pane', '--pane-id', target, direction, '--percent', '50', '--', *command).strip())
        else:
            if self.process and self.process.poll() is None:
                self.process.terminate()
            self.process = subprocess.Popen(['wezterm', '--config-file', self.config, 'start',
                                             '--always-new-process', '--class', self.window_class, '--', *command],
                                            env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                found = self.panes()
                if found:
                    pane_id = found[0]['pane_id']
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('WezTerm did not create the serial window.')
        self.cli('set-window-title', '--pane-id', pane_id, 'USB Serial Consoles')
        self.cli('activate-pane', '--pane-id', pane_id)
        try:
            self.raise_window()
        except (OSError, subprocess.SubprocessError):
            pass
        return pane_id

    def close(self, pane_id):
        self.cli('kill-pane', '--pane-id', pane_id)


def desktop_notify(summary, body=''):
    if shutil.which('notify-send'):
        subprocess.run(['notify-send', '--app-name=NetDesk', '--icon=utilities-terminal', summary, body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)


class Controller:
    """Keeps one pane per USB serial adapter inside the dedicated WezTerm window."""

    def __init__(self, backend, settings=None, notify=None):
        self.backend = backend
        self.settings = settings or {}
        self.notify = notify
        self.panes = {}
        self.dismissed = set()

    def reset(self):
        # Panes left by a crashed earlier run cannot be mapped to ports; start clean.
        for pane in self.backend.panes():
            try:
                self.backend.close(pane['pane_id'])
            except (subprocess.SubprocessError, RuntimeError):
                LOG.exception('Could not close stale pane %s', pane['pane_id'])
        self.panes.clear()
        self.dismissed.clear()

    def baud(self, port, info=None):
        info = info or {}
        table = self.settings.get('baud_by_port') or {}
        # Most specific first: this exact adapter, this port path, then this adapter model.
        for key in (info.get('serial'), port, info.get('vid_pid')):
            if key and key in table:
                value = table[key]
                break
        else:
            value = self.settings.get('default_baud', 9600)
        baud = int(value)
        if not 300 <= baud <= 4000000:
            raise ValueError('Baud rate must be between 300 and 4000000.')
        return baud

    def log_dir(self):
        if not self.settings.get('log', True):
            return None
        return Path(self.settings.get('log_dir') or default_log_dir()).expanduser()

    def tell(self, summary, body=''):
        if self.notify and self.settings.get('notify', True):
            try:
                self.notify(summary, body)
            except (OSError, subprocess.SubprocessError):
                pass

    def sync(self, ports):
        ports = dict(ports) if isinstance(ports, dict) else {p: {} for p in ports}
        live_ids = {p['pane_id'] for p in self.backend.panes()}
        # An unplugged adapter forgets a manual close, so replugging reopens it.
        self.dismissed.intersection_update(ports)
        for port, pane_id in list(self.panes.items()):
            if port not in ports:
                if pane_id in live_ids:
                    self.backend.close(pane_id)
                del self.panes[port]
                self.tell('Serial adapter removed', port)
            elif pane_id not in live_ids:
                # A manually closed pane stays closed until the adapter is replugged.
                self.dismissed.add(port)
                del self.panes[port]
        for port in sorted(ports.keys() - self.panes.keys() - self.dismissed):
            info = ports[port]
            baud = self.baud(port, info)
            owned_ids = set(self.panes.values())
            owned = [p for p in self.backend.panes() if p['pane_id'] in owned_ids]
            label = info.get('description', '')
            self.panes[port] = self.backend.open(port, baud, owned, label=label, log_dir=self.log_dir())
            LOG.info('Opened %s (%s) at %s baud in pane %s', port, label or 'unknown adapter', baud, self.panes[port])
            self.tell('Serial console opened', f'{port} · {baud} baud' + (f'\n{label}' if label else ''))

    def shutdown(self):
        live_ids = {p['pane_id'] for p in self.backend.panes()}
        for pane_id in self.panes.values():
            if pane_id in live_ids:
                try:
                    self.backend.close(pane_id)
                except (subprocess.SubprocessError, RuntimeError):
                    LOG.exception('Could not close serial pane %s', pane_id)
        self.panes.clear()


# Service control, used by the NetDesk GUI ------------------------------------

def _systemctl(*args):
    return subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=10)


def service_supported():
    return sys.platform.startswith('linux') and bool(shutil.which('systemctl')) and bool(shutil.which('wezterm'))


def service_active():
    if not service_supported():
        return False
    try:
        return _systemctl('is-active', '--quiet', SERVICE).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def set_service(enabled):
    """Enable/disable the user service; installs it on first enable. Returns (ok, message)."""
    unit = Path.home() / '.config/systemd/user' / SERVICE
    installer = ROOT / 'install-serial-hotplug.sh'
    try:
        if enabled and not unit.exists():
            if not installer.exists():
                return False, 'Run install-serial-hotplug.sh from the NetDesk source folder first.'
            result = subprocess.run([str(installer)], capture_output=True, text=True, timeout=60)
        else:
            result = _systemctl('enable' if enabled else 'disable', '--now', SERVICE)
    except (OSError, subprocess.SubprocessError) as error:
        return False, str(error)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip() or 'systemctl failed'
    return True, 'Automatic USB consoles ' + ('on.' if enabled else 'off.')


def ensure_settings_file(path=SETTINGS_PATH):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**DEFAULT_SETTINGS, 'log_dir': str(default_log_dir())}, indent=2) + '\n')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', type=Path, default=SETTINGS_PATH)
    parser.add_argument('--wezterm-config', type=Path, default=Path.home() / '.config/netdesk/serial-wezterm.lua')
    args = parser.parse_args()
    if not args.wezterm_config.exists():
        args.wezterm_config = ROOT / 'packaging/linux/serial-wezterm.lua'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    settings = SettingsFile(args.settings)
    controller = Controller(WezTerm(args.wezterm_config), settings.get(), notify=desktop_notify)
    controller.reset()
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info('Watching USB serial adapters; default baud %s', controller.settings.get('default_baud', 9600))
    try:
        while not stopped:
            controller.settings = settings.get()
            try:
                controller.sync(detect_usb_ports())
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                LOG.exception('Hotplug scan failed; retrying')
            time.sleep(1)
    finally:
        controller.shutdown()


if __name__ == '__main__':
    main()
