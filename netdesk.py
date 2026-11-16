#!/usr/bin/env python3
"""NetDesk: guided SSH collection and serial consoles. No network activity on import."""
from __future__ import annotations

import argparse
import csv
import difflib
import getpass
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, ReadTimeout
from rich.console import Console
from rich.table import Table
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / 'logs' / 'netdesk'
GROUPS = ROOT / 'inventories'
console = Console(highlight=False, markup=False)
from netdesk_presets import PRESETS, SCENARIOS, PLATFORMS, available_presets, get_commands

CONFIG_COMMANDS = {v['config'][0] for v in PRESETS.values()}
CLI_ERROR = re.compile(r'(?im)^\s*(?:%\s*(?:invalid|error|unknown|incomplete|ambiguous)|error:|unrecognized command|unknown command|command parse error|permission denied|syntax error|bad command name|expected end of command|#error exporting)')


def stamp():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def slug(value):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', value).strip('._')[:100] or 'device'


def ask(label, default=''):
    value = input(f'{label}' + (f' [{default}]' if default != '' else '') + ': ').strip()
    return value or str(default)


def choose(label, options):
    console.print('\n' + label)
    for i, item in enumerate(options, 1):
        console.print(f'  {i}) {item}')
    while True:
        value = ask('Choose', '1')
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value) - 1
        console.print('Please choose one of the listed numbers.')


def number(label, default, low, high):
    while True:
        value = ask(label, str(default))
        if value.isdigit() and low <= int(value) <= high:
            return int(value)
        console.print(f'Enter a number from {low} to {high}.')


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def parse_targets(text, limit=1024):
    """IPv4/IPv6, CIDR and inclusive full/short IPv4 ranges; bounded before expansion."""
    targets = []
    seen = set()
    for token in re.split(r'[,\s]+', text.strip()):
        if not token:
            continue
        if '/' in token:
            network = ipaddress.ip_network(token, strict=False)
            count = network.num_addresses
            if count > limit + 2:
                raise ValueError(f'{token} is too large; maximum {limit} devices.')
            addresses = network.hosts()
        elif '-' in token:
            first, last = token.split('-', 1)
            start = ipaddress.ip_address(first)
            if start.version == 4 and last.isdigit():
                last = first.rsplit('.', 1)[0] + '.' + last
            end = ipaddress.ip_address(last)
            if start.version != end.version or int(end) < int(start):
                raise ValueError(f'Invalid range: {token}')
            if int(end) - int(start) + 1 > limit:
                raise ValueError(f'{token} is too large; maximum {limit} devices.')
            addresses = (type(start)(i) for i in range(int(start), int(end) + 1))
        else:
            addresses = [ipaddress.ip_address(token)]
        for address in addresses:
            value = str(address)
            if value not in seen:
                seen.add(value)
                targets.append(value)
                if len(targets) > limit:
                    raise ValueError(f'Maximum {limit} devices per run.')
    if not targets:
        raise ValueError('Enter at least one IP address.')
    return targets


def validate_devices(rows):
    result, seen = [], set()
    for row in rows:
        ip = str(ipaddress.ip_address(row['ip'].strip()))
        driver = row.get('device_type', 'huawei').strip() or 'huawei'
        if driver not in PRESETS:
            raise ValueError(f'Unsupported device_type: {driver}. Choose {", ".join(PRESETS)}.')
        port = int(row.get('port') or 22)
        if not 1 <= port <= 65535:
            raise ValueError('SSH port must be 1–65535.')
        if (ip, port) in seen:
            continue
        seen.add((ip, port))
        result.append({'name': row.get('name', '').strip() or ip, 'ip': ip, 'device_type': driver, 'port': port})
        if len(result) > 1024:
            raise ValueError('Maximum 1024 devices per run.')
    if not result:
        raise ValueError('The device list is empty.')
    return result


def load_inventory(path):
    with Path(path).expanduser().open(encoding='utf-8-sig', newline='') as stream:
        return validate_devices(list(csv.DictReader(stream)))


def save_inventory(devices, name):
    GROUPS.mkdir(exist_ok=True)
    path = GROUPS / (slug(name) + '.csv')
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['name', 'ip', 'device_type', 'port'])
        writer.writeheader()
        writer.writerows(devices)
    return path


def commands_for(device, preset, custom=None):
    return custom or get_commands(device['device_type'], preset)


def collect_one(device, commands, run_dir, username, password, timeout=1200,
                diagnostic_timeout=7200, strict=False, stop=None, folder_key=None, on_event=None):
    """One session per worker; never replay commands automatically after a failure."""
    stop = stop or threading.Event()
    key = slug(device['name']) + '__' + slug(device['ip']) + '_' + str(device['port'])
    if folder_key is not None:
        key = slug(folder_key) + '__' + key
    folder = run_dir / key
    folder.mkdir()
    result = {'device': device, 'status': 'running', 'commands': [], 'folder': key, 'started': stamp()}
    connection = None
    stage = 'connecting'
    secret_values = [password] if password else []

    def clean_error(error):
        message = str(error)
        for value in secret_values:
            message = message.replace(value, '[redacted]')
        return message

    try:
        if stop.is_set():
            result['status'] = 'cancelled'
            return result
        if on_event:
            on_event('connecting', '')
        connection = ConnectHandler(
            device_type=device['device_type'], host=device['ip'], port=device['port'],
            username=username, password=password, conn_timeout=20, auth_timeout=20,
            banner_timeout=20, fast_cli=False, ssh_strict=strict, system_host_keys=True,
            session_log=str(folder / 'session.log'), session_log_record_writes=False,
        )
        # Each vendor driver performs its own session paging setup.
        for index, command in enumerate(commands, 1):
            if stop.is_set():
                result['status'] = 'cancelled'
                break
            stage = command
            if on_event:
                on_event('running', command)
            entry = {'command': command, 'status': 'running', 'started': stamp()}
            result['commands'].append(entry)
            console.print(f"  {device['ip']}  [{index}/{len(commands)}] {command}")
            prompt = connection.find_prompt()
            is_long = any(word in command for word in ('diagnostic-information', 'tech-support', 'support information'))
            output = connection.send_command(
                command, expect_string=r'(?m)^' + re.escape(prompt.strip()) + r'\s*$',
                read_timeout=diagnostic_timeout if is_long else timeout,
                strip_prompt=True, strip_command=True, cmd_verify=False,
            )
            output_path = folder / f'{index:02d}-{slug(command)}.txt'
            output_path.write_text(output + '\n', encoding='utf-8')
            entry.update(status='command_error' if CLI_ERROR.search(output) else 'ok', file=output_path.name, finished=stamp())
            if command in CONFIG_COMMANDS and entry['status'] == 'ok':
                (folder / 'configuration.txt').write_text(output + '\n', encoding='utf-8')
            atomic_json(folder / 'result.json', result)
        else:
            result['status'] = 'ok' if all(c['status'] == 'ok' for c in result['commands']) else 'command_error'
    except NetmikoAuthenticationException:
        result.update(status='authentication_failed', error='Authentication rejected. Check credentials and device access policy.')
    except ReadTimeout:
        result.update(status='timeout', error=f'Prompt did not return while running: {stage}. Partial output is in session.log. No commands were retried.')
    except Exception as error:
        result.update(status='failed', error=f'{stage}: {clean_error(error)}')
    finally:
        if result['commands'] and result['commands'][-1]['status'] == 'running':
            result['commands'][-1]['status'] = result['status']
        if connection is not None:
            try:
                connection.disconnect()
            except Exception:
                pass
        result['finished'] = stamp()
        atomic_json(folder / 'result.json', result)
    return result


def run_collection(devices, preset, custom, username, password, workers=4, timeout=1200,
                   diagnostic_timeout=7200, strict=False, runs_dir=None):
    for device in devices:
        commands_for(device, preset, custom)
    base = runs_dir or RUNS
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir = base / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    run_dir.mkdir(mode=0o700)
    manifest = {'version': 1, 'started': stamp(), 'status': 'running', 'preset': preset,
                'custom_commands': custom, 'devices': devices, 'strict_host_keys': strict,
                'timeout': timeout, 'diagnostic_timeout': diagnostic_timeout, 'results': []}
    atomic_json(run_dir / 'summary.json', manifest)
    stop = threading.Event()
    interrupted = False
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(collect_one, d, commands_for(d, preset, custom), run_dir,
                           username, password, timeout, diagnostic_timeout, strict, stop): d for d in devices}
    console.print(f'\nSaving this run to {run_dir}\nPress Ctrl+C once to stop queued work; active commands finish or time out.')
    try:
        for future in as_completed(futures):
            result = future.result()
            manifest['results'].append(result)
            atomic_json(run_dir / 'summary.json', manifest)
            console.print(f"{len(manifest['results'])}/{len(devices)}  {result['device']['ip']}: {result['status']}")
    except KeyboardInterrupt:
        interrupted = True
        stop.set()
        console.print('Stopping queued work. Waiting for active commands to finish safely…')
    finally:
        pool.shutdown(wait=True)
        # Rebuild from every completed future, including those completed during cancellation.
        results = []
        for future, device in futures.items():
            try:
                results.append(future.result())
            except Exception as error:
                results.append({'device': device, 'status': 'failed', 'error': type(error).__name__})
        manifest.update(results=results, finished=stamp(), status='interrupted' if interrupted else 'complete')
        atomic_json(run_dir / 'summary.json', manifest)
        with (run_dir / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['name', 'ip', 'status', 'error'])
            for result in results:
                writer.writerow([result['device']['name'], result['device']['ip'], result['status'], result.get('error', '')])
    show_summary(manifest)
    console.print(f'Run saved: {run_dir}')
    return run_dir, manifest


def show_summary(manifest):
    table = Table(title='Collection results')
    for column in ('Device', 'IP', 'Status'):
        table.add_column(column)
    for result in manifest['results']:
        table.add_row(result['device']['name'], result['device']['ip'], result['status'])
    console.print(table)
    for result in manifest['results']:
        if result.get('error'):
            console.print(f"{result['device']['ip']}: {result['error']}")


def inventory_wizard():
    saved = sorted(GROUPS.glob('*.csv'))
    mode = choose('Which devices?', ['Enter IPs / range / subnet', 'Saved device group', 'Import CSV'])
    if mode == 1:
        if not saved:
            raise ValueError('No saved device groups yet.')
        return load_inventory(saved[choose('Saved groups', [p.stem for p in saved])])
    if mode == 2:
        return load_inventory(ask('CSV path (name,ip,device_type,port)'))
    console.print('Examples: 192.168.1.10-20   192.168.1.0/28   10.0.0.1,10.0.0.5')
    addresses = parse_targets(ask('Device addresses'))
    driver = list(PRESETS)[choose('Device platform', list(PRESETS))]
    port = number('SSH port', 22, 1, 65535)
    devices = validate_devices([{'ip': ip, 'device_type': driver, 'port': port} for ip in addresses])
    name = ask('Save this group as (leave blank to skip)')
    if name:
        console.print(f'Saved: {save_inventory(devices, name)}')
    return devices


def custom_commands():
    console.print('Enter one device command per line. Blank line finishes. Commands run in the login CLI context.')
    commands = []
    while True:
        command = input('command> ').strip()
        if not command:
            break
        commands.append(command)
    if not commands:
        raise ValueError('No commands supplied.')
    return commands


def preview(devices, preset, commands, workers):
    console.print(f'\nPlan: {len(devices)} devices · up to {workers} simultaneous SSH sessions')
    for device in devices:
        console.print(f"  {device['name']}  {device['ip']}:{device['port']}  ({device['device_type']})")
    for driver in dict.fromkeys(d['device_type'] for d in devices):
        console.print(f'\n{driver} commands:')
        for command in commands or get_commands(driver, preset):
            console.print('  ' + command)


def collect_wizard():
    devices = inventory_wizard()
    choices = available_presets(d['device_type'] for d in devices) + ['custom']
    mode = choose('What do you want to collect?', [SCENARIOS.get(p, 'My own commands') for p in choices])
    preset = choices[mode]
    commands = custom_commands() if preset == 'custom' else None
    workers = number('Parallel devices', 4, 1, 16)
    preview(devices, preset, commands, workers)
    if ask('Start this collection? y/n', 'n').lower() != 'y':
        return
    username, password = credentials()
    _, result = run_collection(devices, preset, commands, username, password, workers)
    return 0 if all(r['status'] == 'ok' for r in result['results']) else 1


def credentials(username=None):
    username = username or ask('SSH username')
    if not username:
        raise ValueError('Username cannot be empty.')
    password = getpass.getpass('SSH password (not saved): ')
    if not password:
        raise ValueError('Password cannot be empty.')
    return username, password


def read_summary(path):
    path = Path(path).expanduser()
    if path.is_dir():
        path /= 'summary.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('Not a NetDesk collection summary.')
    return data


def history():
    return sorted(RUNS.glob('*/summary.json'), reverse=True)


def select_run(label='Select a run'):
    paths = history()
    if not paths:
        raise ValueError('No NetDesk collections yet. Original logs remain in the logs folder.')
    labels = []
    for path in paths:
        data = read_summary(path)
        ok = sum(r['status'] == 'ok' for r in data['results'])
        labels.append(f"{path.parent.name} · {data['preset']} · {ok}/{len(data['devices'])} OK · {data['status']}")
    return paths[choose(label, labels)]


def retry_run(path):
    data = read_summary(path)
    if data.get('jobs'):
        from netdesk_batch import cli_retry
        return cli_retry(data)
    ok = {(r['device']['ip'], r['device']['port']) for r in data['results'] if r['status'] == 'ok'}
    devices = validate_devices(data['devices'])
    devices = [d for d in devices if (d['ip'], d['port']) not in ok]
    if not devices:
        console.print('All devices succeeded. Nothing to retry.')
        return
    preview(devices, data['preset'], data['custom_commands'], 4)
    console.print('Retry runs the entire command list again on these devices, including commands that previously succeeded.')
    if ask('Retry these devices? y/n', 'n').lower() == 'y':
        username, password = credentials()
        run_collection(devices, data['preset'], data['custom_commands'], username, password,
                       strict=data['strict_host_keys'], timeout=data['timeout'], diagnostic_timeout=data['diagnostic_timeout'])


def compare_runs(old_path, new_path):
    old_path, new_path = Path(old_path), Path(new_path)
    old = read_summary(old_path)
    new = read_summary(new_path)
    old_root = old_path if old_path.is_dir() else old_path.parent
    new_root = new_path if new_path.is_dir() else new_path.parent
    indexed = {(r.get('scenario', ''), r['device']['ip'], r['device']['port'], r['device']['device_type']): r for r in old['results']}
    sections, compared = [], 0
    for result in new['results']:
        device = result['device']
        previous = indexed.get((result.get('scenario', ''), device['ip'], device['port'], device['device_type']))
        if not previous or 'folder' not in previous or 'folder' not in result:
            continue
        before = old_root / previous['folder'] / 'configuration.txt'
        after = new_root / result['folder'] / 'configuration.txt'
        if not before.exists() or not after.exists():
            continue
        compared += 1
        diff = list(difflib.unified_diff(before.read_text().splitlines(True), after.read_text().splitlines(True),
                                       fromfile=f"{device['ip']} / {old_root.name}", tofile=f"{device['ip']} / {new_root.name}"))
        sections.append(''.join(diff) if diff else f"{device['ip']}: no configuration differences\n")
    if not compared:
        return 'No matching configuration backups in these runs. Use Configuration backup or Full diagnostics first.\n'
    return f'Compared {compared} device configuration(s). Raw text comparison includes device timestamps.\n\n' + '\n'.join(sections)


def detected_ports():
    # Ignore the kernel's placeholder ttyS ports; built-in ports can be passed explicitly.
    return sorted((p for p in list_ports.comports() if p.vid is not None or
                   Path(p.device).name.startswith(('ttyUSB', 'ttyACM'))), key=lambda p: p.device)


def serial_console(port=None, baud=None, log=None):
    if not shutil.which('picocom'):
        raise ValueError('picocom is not installed. Install it with: sudo apt install picocom')
    ports = detected_ports()
    if port is None:
        if not ports:
            raise ValueError('No USB serial ports found. Plug in your console cable, or specify a built-in port with --port /dev/ttyS0.')
        index = choose('Serial ports', [f'{p.device} — {p.description}' for p in ports])
        port = ports[index].device
    if not Path(port).exists() or not os.access(port, os.R_OK | os.W_OK):
        raise ValueError(f'Cannot access {port}. Check the cable and your dialout group membership (log out/in after changes).')
    if baud is None:
        index = choose('Console speed (8N1, no flow control)', ['9600 baud', '115200 baud', 'Custom'])
        baud = [9600, 115200, None][index]
        if baud is None:
            baud = number('Baud rate', 57600, 300, 4000000)
    if not 300 <= baud <= 4000000:
        raise ValueError('Baud rate must be between 300 and 4000000.')
    if log is None:
        log = choose('Serial session', ['Connect only', 'Connect and save device output']) == 1
    args = ['picocom', '--baud', str(baud), '--databits', '8', '--parity', 'n', '--stopbits', '1', '--flow', 'n']
    logfile = None
    if log:
        logdir = ROOT / 'logs' / 'serial'
        logdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        logfile = logdir / f'{datetime.now():%Y%m%d-%H%M%S-%f}-{slug(Path(port).name)}.log'
        with logfile.open('x') as stream:
            stream.write(f'Serial session {stamp()} | {port} | {baud} baud\n')
        args += ['--logfile', str(logfile)]
        console.print(f'Device output log: {logfile}')
    console.print('Exit the console with Ctrl+A, then Ctrl+X. Device-echoed input is included when logging.')
    status = subprocess.call(args + [port])
    if logfile:
        console.print(f'Serial log: {logfile} (picocom exit status {status})')
    return status


def ssh_console():
    host = str(ipaddress.ip_address(ask('Device IP')))
    username = ask('SSH username')
    if not username or any(c.isspace() for c in username):
        raise ValueError('Enter a valid SSH username.')
    port = number('SSH port', 22, 1, 65535)
    return subprocess.call(['ssh', '-p', str(port), '-l', username, host])


def original_serial_tool(name, *args):
    """Use the user's existing serial helpers, preserving their prompts and log paths."""
    if name not in ('serialports', 'serialportsconnect'):
        raise ValueError('Unknown serial helper.')
    helper = Path.home() / '.local' / 'bin' / name
    if not helper.is_file() or not os.access(helper, os.X_OK):
        helper = ROOT / 'tools' / 'serial' / name
    if not helper.is_file() or not os.access(helper, os.X_OK):
        raise ValueError(f'Serial helper is missing or not executable: {helper}')
    return subprocess.call([str(helper), *args])


def serial_logs():
    folders = [Path.home() / 'serial-logs', ROOT / 'logs' / 'serial']
    for folder in folders:
        console.print(f'\nSerial logs: {folder}')
        paths = sorted(folder.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not paths:
            console.print('  No saved logs here yet.')
        for path in paths[:20]:
            console.print(f'  {path.name}  ({path.stat().st_size:,} bytes)')
        if len(paths) > 20:
            console.print(f'  Showing the 20 most recent of {len(paths)} logs.')


def serial_menu():
    while True:
        action = choose('Serial ports & logging', [
            'List connected USB serial ports — serialports',
            'Guided setup: configuration only / configuration + clean log — serialportsconnect',
            'Record a serial log — serialports log',
            'Connect without a log — serialports connect',
            'Show saved serial logs',
            'Back',
        ])
        if action == 0:
            original_serial_tool('serialports')
        elif action == 1:
            original_serial_tool('serialportsconnect')
        elif action in (2, 3):
            baud = number('Baud rate (9600 standard, 115200 fast)', 9600, 300, 4000000)
            original_serial_tool('serialports', 'log' if action == 2 else 'connect', str(baud))
        elif action == 4:
            serial_logs()
        else:
            return 0


def doctor():
    console.print(f'Project: {ROOT}\nPython: {sys.executable}\nSSH collection logs: {RUNS}')
    for command in ('picocom', 'ssh'):
        console.print(f'{command}: {shutil.which(command) or "NOT INSTALLED"}')
    ports = detected_ports()
    console.print(f'USB serial ports found: {len(ports)}')
    for port in ports:
        console.print(f'  {port.device}: {port.description}; accessible={os.access(port.device, os.R_OK | os.W_OK)}')
    console.print(f'Saved device groups: {len(list(GROUPS.glob("*.csv")))}')
    console.print('SSH collection defaults to accepting unknown host keys; --strict-host-keys requires known_hosts entries.')


def menu():
    while True:
        console.print('\nNETDESK · Network collection & consoles')
        action = choose('What would you like to do?', ['Collect logs / back up configurations over SSH', 'Serial ports & logging — serialports / serialportsconnect', 'Open an interactive SSH console', 'View past collections / retry failed devices', 'Compare two configuration backups', 'Check setup / list serial ports', 'Exit'])
        try:
            if action == 0:
                collect_wizard()
            elif action == 1:
                serial_menu()
            elif action == 2:
                ssh_console()
            elif action == 3:
                path = select_run()
                show_summary(read_summary(path))
                console.print(f'Files: {path.parent}')
                if choose('Next', ['Back to menu', 'Retry failed / unfinished devices']) == 1:
                    retry_run(path)
            elif action == 4:
                console.print(compare_runs(select_run('Older run'), select_run('Newer run')))
            elif action == 5:
                doctor()
            else:
                return 0
        except (ValueError, OSError, KeyError) as error:
            console.print(f'Could not complete that action: {error}')


def bounded(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f'Must be between {low} and {high}.')
        return number
    return parse


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description='NetDesk — run without arguments for the guided menu.')
    sub = parser.add_subparsers(dest='action')
    collect = sub.add_parser('collect', help='Guided collection, or supply targets and options')
    source = collect.add_mutually_exclusive_group()
    source.add_argument('--targets', help='IPs, CIDR or inclusive ranges, e.g. 192.168.1.10-20')
    source.add_argument('--inventory', type=Path, help='CSV: name,ip,device_type,port')
    collect.add_argument('--platform', choices=list(PRESETS), default='huawei')
    collect.add_argument('--port', type=bounded(1, 65535), default=22)
    collect.add_argument('--preset', choices=list(SCENARIOS), default='quick')
    collect.add_argument('--commands-file', type=Path, help='One command per line; # comments ignored')
    collect.add_argument('--workers', type=bounded(1, 16), default=4)
    collect.add_argument('--username')
    collect.add_argument('--timeout', type=bounded(1, 86400), default=1200)
    collect.add_argument('--diagnostic-timeout', type=bounded(1, 86400), default=7200)
    collect.add_argument('--strict-host-keys', action='store_true')
    collect.add_argument('--dry-run', action='store_true', help='Preview only; no credentials or connections')
    collect.add_argument('--yes', action='store_true', help='Skip plan confirmation (password still prompted)')
    serial = sub.add_parser('serial', help='Serial ports and logging menu (or direct console with options)')
    serial.add_argument('--port')
    serial.add_argument('--baud', type=bounded(300, 4000000))
    serial.add_argument('--log', action='store_true', default=None)
    ports = sub.add_parser('serialports', help='Run your original serialports helper')
    ports.add_argument('arguments', nargs=argparse.REMAINDER)
    sub.add_parser('serialportsconnect', help='Run your original guided serial logging wizard')
    sub.add_parser('ssh', help='Interactive SSH console')
    sub.add_parser('doctor', help='Check setup without making connections')
    sub.add_parser('history', help='List collection runs')
    sub.add_parser('gui', help='Open the NetDesk desktop app')
    catalog = sub.add_parser('presets', help='List platform-aware scenario commands')
    catalog.add_argument('--platform', choices=list(PRESETS))
    batch = sub.add_parser('batch', help='Collect multiple scenarios using a saved GUI plan')
    batch.add_argument('plan', type=Path)
    batch.add_argument('--dry-run', action='store_true')
    batch.add_argument('--workers', type=bounded(1, 16), default=4)
    batch.add_argument('--strict-host-keys', action='store_true')
    batch.add_argument('--timeout', type=bounded(1, 86400), default=1200)
    batch.add_argument('--diagnostic-timeout', type=bounded(1, 86400), default=7200)
    retry = sub.add_parser('retry', help='Retry failed devices from a saved run')
    retry.add_argument('run', type=Path)
    diff = sub.add_parser('diff', help='Compare saved configuration text')
    diff.add_argument('older', type=Path)
    diff.add_argument('newer', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == 'presets':
            for platform in ([args.platform] if args.platform else PRESETS):
                console.print(f'\n{PLATFORMS[platform]} ({platform})')
                for preset, commands in PRESETS[platform].items():
                    console.print(f'  {preset}: {SCENARIOS[preset]} — ' + '; '.join(commands))
            return 0
        if args.action == 'gui':
            from netdesk_gui import main as gui_main
            return gui_main()
        if args.action == 'batch':
            from netdesk_batch import cli_batch
            return cli_batch(args.plan, args.dry_run, args.workers, args.strict_host_keys, timeout=args.timeout, diagnostic_timeout=args.diagnostic_timeout)
        if args.action is None:
            return menu()
        if args.action == 'collect':
            if not args.targets and not args.inventory:
                if len(argv or sys.argv[1:]) > 1:
                    parser.error('Supply --targets or --inventory when using collection options.')
                return collect_wizard() or 0
            devices = load_inventory(args.inventory) if args.inventory else validate_devices([
                {'ip': ip, 'device_type': args.platform, 'port': args.port} for ip in parse_targets(args.targets)])
            custom = None
            if args.commands_file:
                custom = [line.strip() for line in args.commands_file.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')]
                if not custom:
                    raise ValueError('The commands file is empty.')
            preview(devices, args.preset, custom, args.workers)
            if args.dry_run:
                console.print('Preview only. No connections made.')
                return 0
            if not args.yes and ask('Start this collection? y/n', 'n').lower() != 'y':
                return 0
            username, password = credentials(args.username)
            _, result = run_collection(devices, args.preset, custom, username, password, args.workers,
                                       args.timeout, args.diagnostic_timeout, args.strict_host_keys)
            return 0 if all(r['status'] == 'ok' for r in result['results']) else 1
        if args.action == 'serial':
            if args.port is None and args.baud is None and args.log is None:
                return serial_menu()
            return serial_console(args.port, args.baud, args.log)
        if args.action == 'serialports':
            return original_serial_tool('serialports', *args.arguments)
        if args.action == 'serialportsconnect':
            return original_serial_tool('serialportsconnect')
        if args.action == 'ssh':
            return ssh_console()
        if args.action == 'doctor':
            doctor()
        elif args.action == 'history':
            for path in history():
                data = read_summary(path)
                console.print(f"{path.parent}  {data['preset']}  {data['status']}")
            if not history():
                console.print('No NetDesk collections yet.')
        elif args.action == 'retry':
            return retry_run(args.run) or 0
        elif args.action == 'diff':
            console.print(compare_runs(args.older, args.newer))
        return 0
    except (ValueError, OSError, KeyError) as error:
        console.print(f'Error: {error}')
        return 2
    except (KeyboardInterrupt, EOFError):
        console.print('\nCancelled.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
