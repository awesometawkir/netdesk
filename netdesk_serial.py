"""Parallel, prompt-driven serial collection. No ports open until run_serial_batch."""
from __future__ import annotations

import codecs
import csv
import hashlib
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import serial
import netdesk as n

# Session-only pagination settings. FortiOS uses pager handling rather than changing console configuration.
PAGING = {
    'huawei': 'screen-length 0 temporary', 'hp_comware': 'screen-length disable',
    'cisco_ios': 'terminal length 0', 'cisco_nxos': 'terminal length 0',
    'cisco_xr': 'terminal length 0', 'arista_eos': 'terminal length 0',
    'juniper_junos': 'set cli screen-length 0', 'paloalto_panos': 'set cli pager off',
    'aruba_aoscx': 'no page', 'hp_procurve': 'no page', 'dell_os10': 'terminal length 0',
    'vyos': 'set terminal length 0', 'ubiquiti_edgerouter': 'terminal length 0',
}
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
PROMPT = re.compile(r'(?m)^(?:<[^<>\r\n]+>|[^\s\r\n][^\r\n]{0,160}[#>$])\s*\Z')
USER = re.compile(r'(?i)(?:username|user name|login|user)\s*:\s*\Z')
PASSWORD = re.compile(r'(?i)password\s*:\s*\Z')
PAGER = re.compile(r'(?i)(?:-{2,}\s*more\s*-{2,}|---\(more[^)]*\)---|press any key to continue)\s*\Z')
AUTH_ERROR = re.compile(r'(?i)(?:authentication failed|login incorrect|login invalid|access denied|invalid password|bad passwords)')


class Cancelled(Exception):
    pass


class LoginError(Exception):
    pass


class RedactedWriter:
    """Streaming text log with cross-chunk credential filtering and bounded buffering."""
    def __init__(self, path, secrets=()):
        self.stream = Path(path).open('x', encoding='utf-8')
        self.secrets = sorted({s for s in secrets if s}, key=len, reverse=True)
        self.width = max(map(len, self.secrets), default=1)
        self.pending = ''

    def write(self, text, final=False):
        data = self.pending + text
        position, output = 0, []
        while position < len(data) and (final or len(data) - position >= self.width):
            match = next((s for s in self.secrets if data.startswith(s, position)), None)
            if match:
                output.append('[redacted]')
                position += len(match)
            elif final and any(s.startswith(data[position:]) for s in self.secrets):
                output.append('[redacted]')
                position = len(data)
            else:
                output.append(data[position])
                position += 1
        self.pending = data[position:]
        self.stream.write(''.join(output))
        self.stream.flush()

    def close(self):
        if not self.stream.closed:
            self.write('', final=True)
            self.stream.close()


def validate_jobs(jobs, reserved_ports=()):
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 16:
        raise ValueError('Choose between 1 and 16 serial ports.')
    seen = {os.path.realpath(p) for p in reserved_ports}
    clean = []
    for job in jobs:
        port = str(job['port']).strip()
        if not port.startswith('/dev/') or any(c in port for c in '\r\n\x00'):
            raise ValueError('Choose a serial device path under /dev/.')
        canonical = os.path.realpath(port)
        if canonical in seen:
            raise ValueError(f'Port is duplicated or open in the interactive console: {port}')
        seen.add(canonical)
        baud = int(job.get('baud', 9600))
        if not 300 <= baud <= 4000000:
            raise ValueError('Baud rate must be between 300 and 4000000.')
        platform = job['platform']
        if platform not in n.PRESETS:
            raise ValueError(f'Unsupported platform: {platform}')
        preset = job.get('preset', 'quick')
        commands = job.get('commands') or n.get_commands(platform, preset)
        if not isinstance(commands, list) or not commands or any(not isinstance(c, str) or not c.strip() or any(x in c for x in '\r\n\x00') for c in commands):
            raise ValueError('Commands must be nonempty, single-line strings.')
        # A whitelist prevents credentials supplied accidentally in a job from being persisted.
        clean.append({'port': port, 'baud': baud, 'platform': platform, 'preset': preset,
                      'commands': [c.strip() for c in commands]})
    return clean


def terminal_text(text):
    text = ANSI.sub('', text).replace('\r', '')
    # Backspaces from common pagers should not prevent recognition of the actual prompt.
    while '\b' in text:
        text = re.sub(r'[^\n]?\x08', '', text)
    return text


class SerialSession:
    def __init__(self, job, credentials, stop, log):
        self.job = job
        self.username, self.password = credentials
        self.stop, self.log = stop, log
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.connection = serial.Serial(port=job['port'], baudrate=job['baud'], bytesize=8,
                                        parity='N', stopbits=1, timeout=0.1, write_timeout=2,
                                        xonxoff=False, rtscts=False, dsrdtr=False, exclusive=True)

    def send(self, text):
        if self.stop.is_set():
            raise Cancelled()
        self.connection.write(text.encode('utf-8'))

    def receive(self, writer=None):
        if self.stop.is_set():
            raise Cancelled()
        data = self.connection.read(min(max(self.connection.in_waiting, 1), 4096))
        text = self.decoder.decode(data)
        self.log.write(text)
        if writer:
            writer.write(text)
        return text

    def login(self, timeout):
        self.send('\r')
        deadline = time.monotonic() + timeout
        tail, sent_user, sent_password, wakes = '', False, False, 0
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            chunk = self.receive()
            if chunk:
                tail = (tail + chunk)[-16384:]
                last_data = time.monotonic()
            clean = terminal_text(tail)
            if AUTH_ERROR.search(clean):
                raise LoginError('Device rejected the login. No automatic retry was attempted.')
            if USER.search(clean):
                if sent_user or sent_password:
                    raise LoginError('Device requested login again. Check the credentials.')
                if not self.username:
                    raise LoginError('Device requires a username; enter it for this port.')
                self.send(self.username + '\r')
                sent_user, tail = True, ''
            elif PASSWORD.search(clean):
                if sent_password:
                    raise LoginError('Device requested the password again. No automatic retry was attempted.')
                if not self.password:
                    raise LoginError('Device requires a password; enter it for this port.')
                self.send(self.password + '\r')
                sent_password, tail = True, ''
            elif re.search(r'(?i)press (?:return|enter)', clean) and wakes < 3:
                self.send('\r')
                wakes, tail = wakes + 1, ''
            else:
                match = PROMPT.search(clean)
                if match and time.monotonic() - last_data >= 0.15:
                    return match.group(0).strip()
        raise TimeoutError('Login prompt was not recognized. Check baud rate or use the interactive console.')

    def command(self, command, prompt, timeout, writer=None):
        self.send(command + '\r')
        pattern = re.compile(r'(?m)^' + re.escape(prompt) + r'\s*\Z')
        tail, error = '', False
        last_data = time.monotonic()
        deadline = last_data + timeout
        while time.monotonic() < deadline:
            chunk = self.receive(writer)
            if chunk:
                tail = (tail + chunk)[-65536:]
                last_data = time.monotonic()
            clean = terminal_text(tail)
            error = error or bool(n.CLI_ERROR.search(clean))
            if PAGER.search(clean[-300:]):
                self.send(' ')
                tail = ''
                continue
            if pattern.search(clean) and time.monotonic() - last_data >= 0.15:
                return not error
        raise TimeoutError('Command timed out before the device prompt returned. Partial output was preserved.')

    def close(self):
        self.connection.close()


def collect_port(job, credentials, run_dir, stop, emit, login_timeout=30, timeout=1200, diagnostic_timeout=7200):
    identity = hashlib.sha256(os.path.realpath(job['port']).encode()).hexdigest()[:10]
    folder = run_dir / (n.slug(job['port']) + '-' + identity)
    folder.mkdir(mode=0o700)
    result = {'port': job['port'], 'baud': job['baud'], 'platform': job['platform'],
              'folder': folder.name, 'status': 'running', 'commands': [], 'started': n.stamp()}
    secrets = tuple(credentials)
    log = RedactedWriter(folder / 'session.log', secrets)
    session = None
    writer = None
    try:
        if stop.is_set():
            raise Cancelled()
        emit({'type': 'progress', 'port': job['port'], 'status': 'Logging in'})
        session = SerialSession(job, credentials, stop, log)
        prompt = session.login(login_timeout)
        paging = PAGING.get(job['platform'])
        if paging:
            if not session.command(paging, prompt, min(timeout, 30)):
                result['paging_warning'] = 'Paging setup was rejected; common pagers will be handled during collection.'
        for index, command in enumerate(job['commands'], 1):
            if stop.is_set():
                raise Cancelled()
            emit({'type': 'progress', 'port': job['port'], 'status': f'{index}/{len(job["commands"])}: {command}'})
            entry = {'command': command, 'status': 'running'}
            result['commands'].append(entry)
            partial = folder / f'{index:02d}-{n.slug(command)}.partial.txt'
            writer = RedactedWriter(partial, secrets)
            long = any(s in command for s in ('diagnostic-information', 'tech-support', 'support information'))
            ok = session.command(command, prompt, diagnostic_timeout if long else timeout, writer)
            writer.close()
            writer = None
            completed = partial.with_name(partial.name.replace('.partial.txt', '.txt'))
            partial.replace(completed)
            entry.update(status='ok' if ok else 'command_error', file=completed.name)
            if command in n.CONFIG_COMMANDS and ok:
                # Copy the already filtered response, including echo/prompt as returned by the device.
                import shutil
                shutil.copyfile(completed, folder / 'configuration.txt')
            n.atomic_json(folder / 'result.json', result)
        result['status'] = 'ok' if all(c['status'] == 'ok' for c in result['commands']) else 'command_error'
    except Cancelled:
        result.update(status='cancelled', error='Collection stopped. The device may still be executing its last command.')
    except LoginError as error:
        result.update(status='authentication_failed', error=str(error))
    except TimeoutError as error:
        result.update(status='timeout', error=str(error))
    except Exception as error:
        result.update(status='failed', error=f'{type(error).__name__}: check cable, port access, baud rate and available disk space.')
    finally:
        if writer:
            writer.close()
        if session:
            session.close()
        log.close()
        if result['commands'] and result['commands'][-1]['status'] == 'running':
            result['commands'][-1]['status'] = result['status']
        result['finished'] = n.stamp()
        n.atomic_json(folder / 'result.json', result)
    return result


def run_serial_batch(jobs, credentials, stop=None, on_event=None, runs_dir=None,
                     login_timeout=30, timeout=1200, diagnostic_timeout=7200, reserved_ports=()):
    jobs = validate_jobs(jobs, reserved_ports)
    if any(not 0 < value <= 86400 for value in (login_timeout, timeout, diagnostic_timeout)):
        raise ValueError('Timeouts must be greater than zero and at most 86400 seconds.')
    for job in jobs:
        pair = credentials.get(job['port'], ('', ''))
        if len(pair) != 2 or not all(isinstance(v, str) for v in pair):
            raise ValueError('Each serial login must contain a username and password string.')
        if any(c in value for value in pair for c in ('\r', '\n', '\x00')):
            raise ValueError('Serial login fields cannot contain line breaks or NUL characters.')
    stop = stop or threading.Event()
    emit = on_event or (lambda event: None)
    root = Path(runs_dir or n.ROOT / 'logs' / 'serial-batches')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    path.mkdir(mode=0o700)
    data = {'version': 1, 'transport': 'serial', 'started': n.stamp(), 'status': 'running', 'jobs': jobs, 'results': []}
    n.atomic_json(path / 'summary.json', data)
    emit({'type': 'run', 'path': str(path)})
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {pool.submit(collect_port, job, credentials.get(job['port'], ('', '')), path, stop, emit,
                           login_timeout, timeout, diagnostic_timeout): job for job in jobs}
    try:
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                result = {'port': futures[future]['port'], 'status': 'failed', 'error': type(error).__name__}
            data['results'].append(result)
            n.atomic_json(path / 'summary.json', data)
            emit({'type': 'result', 'result': result, 'done': len(data['results']), 'total': len(jobs)})
    except KeyboardInterrupt:
        stop.set()
    finally:
        pool.shutdown(wait=True)
        recorded = {r['port'] for r in data['results']}
        for future, job in futures.items():
            if job['port'] not in recorded:
                try:
                    data['results'].append(future.result())
                except Exception as error:
                    data['results'].append({'port': job['port'], 'status': 'failed', 'error': type(error).__name__})
        data.update(status='interrupted' if stop.is_set() else 'complete', finished=n.stamp())
        n.atomic_json(path / 'summary.json', data)
        with (path / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['port', 'status', 'error'])
            writer.writerows([r['port'], r['status'], r.get('error', '')] for r in data['results'])
    return path, data


def cli_collect(args):
    import getpass
    custom = None
    if args.commands_file:
        custom = [c.strip() for c in args.commands_file.read_text().splitlines() if c.strip() and not c.lstrip().startswith('#')]
        if not custom:
            raise ValueError('The command file is empty.')
    jobs = validate_jobs([{'port': p, 'baud': args.baud, 'platform': args.platform, 'preset': args.preset,
                           'commands': custom} for p in args.ports])
    for job in jobs:
        n.console.print(f"{job['port']} @ {job['baud']} | {job['platform']}: " + '; '.join(job['commands']))
    if args.dry_run:
        n.console.print('Preview only. No serial ports opened.')
        return 0
    if n.ask('Start serial collection? y/n', 'n').lower() != 'y':
        return 0
    credentials = {}
    common = None
    for job in jobs:
        if args.individual_logins or common is None:
            n.console.print('Login for ' + (job['port'] if args.individual_logins else 'all ports') + ' (leave blank for an already logged-in console).')
            common = (n.ask('Username'), getpass.getpass('Password: '))
        credentials[job['port']] = common
    try:
        path, result = run_serial_batch(jobs, credentials, login_timeout=args.login_timeout, timeout=args.timeout,
                                        diagnostic_timeout=args.diagnostic_timeout,
                                        on_event=lambda e: n.console.print(f"{e['result']['port']}: {e['result']['status']}") if e['type'] == 'result' else None)
        n.console.print(f'Saved: {path}')
        return 0 if result['status'] == 'complete' and all(r['status'] == 'ok' for r in result['results']) else 1
    finally:
        credentials.clear()
