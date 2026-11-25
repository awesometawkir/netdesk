"""Multi-scenario collection shared by GUI and CLI. Secrets never enter saved plans."""
from __future__ import annotations

import csv
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import netdesk as n


def make_jobs(scenario, targets, platform='huawei', port=22, preset='quick', commands=None, credential_group=None, devices=None):
    scenario = scenario.strip()
    if not scenario:
        raise ValueError('Give the scenario a name, such as Branch office.')
    group = (credential_group or scenario).strip()
    devices = devices or n.validate_devices([{'ip': ip, 'device_type': platform, 'port': port} for ip in n.parse_targets(targets)])
    jobs = []
    for device in devices:
        jobs.append({'id': uuid.uuid4().hex, 'scenario': scenario, 'device': device,
                     'preset': preset, 'commands': commands or n.get_commands(device['device_type'], preset),
                     'credential_group': group})
    return validate_jobs(jobs)


def validate_jobs(jobs):
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 1024:
        raise ValueError('A plan must contain between 1 and 1,024 device jobs.')
    cleaned, ids, endpoints = [], set(), set()
    for job in jobs:
        if not isinstance(job, dict) or not isinstance(job.get('device'), dict):
            raise ValueError('Each job must contain a device object.')
        if not isinstance(job.get('scenario'), str) or not isinstance(job.get('credential_group'), str):
            raise ValueError('Each job needs text scenario and login-group names.')
        identity = str(job.get('id', ''))
        if not identity or len(identity) > 64 or not identity.isalnum() or identity in ids:
            raise ValueError('Each job must have a unique alphanumeric ID.')
        ids.add(identity)
        scenario = str(job['scenario']).strip()
        group = str(job['credential_group']).strip()
        if not scenario or not group:
            raise ValueError('Every job needs a scenario and a login group.')
        commands = job['commands']
        if not isinstance(commands, list) or not commands or any(not isinstance(c, str) or not c.strip() or '\n' in c or '\r' in c or '\x00' in c for c in commands):
            raise ValueError('Each job needs a nonempty list of single-line commands.')
        try:
            device = n.validate_devices([job['device']])[0]
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError('Each device needs a valid IP, platform, name and port.') from error
        endpoint = (scenario, device['ip'], device['port'])
        if endpoint in endpoints:
            raise ValueError(f'Duplicate target in scenario {scenario}: {device["ip"]}. Use a different scenario name for a separate collection.')
        endpoints.add(endpoint)
        # Whitelist fields: even a mistakenly supplied password cannot enter the plan.
        cleaned.append({'id': identity, 'scenario': scenario,
                        'device': device,
                        'credential_group': group, 'preset': str(job.get('preset', 'custom')),
                        'commands': [c.strip() for c in commands]})
    return cleaned


def save_plan(path, jobs):
    path = Path(path)
    data = {'format': 'netdesk-plan', 'version': 1, 'jobs': validate_jobs(jobs)}
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_plan(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'netdesk-plan' or data.get('version') != 1:
        raise ValueError('Choose a NetDesk plan JSON file saved by the app.')
    return validate_jobs(data['jobs'])


def validate_credentials(jobs, vault):
    missing = []
    for group in dict.fromkeys(j['credential_group'] for j in jobs):
        credentials = vault.get(group)
        if not credentials or len(credentials) != 2 or not credentials[0].strip() or not credentials[1]:
            missing.append(group)
    if missing:
        raise ValueError('Enter a username and password for these login groups: ' + ', '.join(missing))


class AnyEvent:
    """Read-only view that is set when any of the given events is set."""
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(e.is_set() for e in self.events)

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
        return True


def run_batch(jobs, vault, workers=4, timeout=1200, diagnostic_timeout=7200, strict=False,
              stop=None, on_event=None, runs_dir=None, idle_timeout=180, cancel=None):
    """``cancel`` maps job id -> threading.Event to stop one device without stopping the run."""
    jobs = validate_jobs(jobs)
    validate_credentials(jobs, vault)
    if not 1 <= workers <= 16 or not 1 <= timeout <= 86400 or not 1 <= diagnostic_timeout <= 86400 or not 0 <= idle_timeout <= 86400:
        raise ValueError('Use 1–16 workers and timeouts from 1–86400 seconds.')
    stop = stop or threading.Event()
    emit = on_event or (lambda event: None)
    base = Path(runs_dir or n.RUNS)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_dir = base / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '-batch')
    run_dir.mkdir(mode=0o700)
    manifest = {'version': 1, 'started': n.stamp(), 'status': 'running', 'preset': 'batch',
                'custom_commands': None, 'devices': [j['device'] for j in jobs], 'jobs': jobs,
                'strict_host_keys': strict, 'timeout': timeout, 'diagnostic_timeout': diagnostic_timeout, 'results': []}
    n.atomic_json(run_dir / 'summary.json', manifest)
    emit({'type': 'run', 'path': str(run_dir)})
    # Same endpoint in different scenarios is serialized; distinct devices run in parallel.
    locks = {(j['device']['ip'], j['device']['port']): threading.Lock() for j in jobs}

    def execute(job):
        device = job['device']
        lock = locks[(device['ip'], device['port'])]
        job_stop = AnyEvent(stop, cancel[job['id']]) if cancel and job['id'] in cancel else stop
        acquired = False
        try:
            emit({'type': 'progress', 'id': job['id'], 'status': 'waiting', 'detail': ''})
            while not job_stop.is_set():
                acquired = lock.acquire(timeout=0.2)
                if acquired:
                    break
            username, password = vault[job['credential_group']]
            def progress(status, detail):
                emit({'type': 'progress', 'id': job['id'], 'status': status, 'detail': detail})
            result = n.collect_one(device, job['commands'], run_dir, username, password,
                                   timeout, diagnostic_timeout, strict, job_stop,
                                   folder_key=job['id'], on_event=progress, idle_timeout=idle_timeout)
            result.update(job_id=job['id'], scenario=job['scenario'], credential_group=job['credential_group'])
            n.atomic_json(run_dir / result['folder'] / 'result.json', result)
            return result
        except Exception as error:
            # Avoid arbitrary exception text: third-party errors can contain credentials.
            return {'device': device, 'job_id': job['id'], 'scenario': job['scenario'],
                    'credential_group': job['credential_group'], 'status': 'failed', 'error': type(error).__name__}
        finally:
            if acquired:
                lock.release()

    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(execute, job): job for job in jobs}
    recorded = set()
    try:
        for future in as_completed(futures):
            result = future.result()
            recorded.add(result['job_id'])
            manifest['results'].append(result)
            n.atomic_json(run_dir / 'summary.json', manifest)
            emit({'type': 'result', 'result': result, 'done': len(recorded), 'total': len(jobs)})
    except KeyboardInterrupt:
        stop.set()
        n.console.print('Stopping queued jobs; current operations will finish or time out.')
    except Exception:
        stop.set()
        raise
    finally:
        pool.shutdown(wait=True)
        manifest['results'] = [f.result() for f in futures]
        for result in manifest['results']:
            if result['job_id'] not in recorded:
                emit({'type': 'result', 'result': result, 'done': len(manifest['results']), 'total': len(jobs)})
        manifest.update(status='interrupted' if stop.is_set() else 'complete', finished=n.stamp())
        n.atomic_json(run_dir / 'summary.json', manifest)
        with (run_dir / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['scenario', 'device', 'ip', 'status', 'error'])
            for result in manifest['results']:
                writer.writerow([result['scenario'], result['device']['name'], result['device']['ip'], result['status'], result.get('error', '')])
    return run_dir, manifest


def prompt_vault(jobs):
    vault = {}
    for group in dict.fromkeys(j['credential_group'] for j in jobs):
        n.console.print(f'\nLogin group: {group}')
        vault[group] = n.credentials()
    return vault


def cli_batch(path, dry_run=False, workers=4, strict=False, jobs=None, timeout=1200, diagnostic_timeout=7200):
    jobs = validate_jobs(jobs) if jobs is not None else load_plan(path)
    for job in jobs:
        n.console.print(f"{job['scenario']} | {job['device']['ip']} | login: {job['credential_group']} | " + '; '.join(job['commands']))
    if dry_run:
        n.console.print('Preview only. No device connections made.')
        return 0
    if n.ask(f'Collect {len(jobs)} jobs with up to {workers} parallel sessions? y/n', 'n').lower() != 'y':
        return 0
    vault = prompt_vault(jobs)
    try:
        path, data = run_batch(jobs, vault, workers=workers, strict=strict, timeout=timeout, diagnostic_timeout=diagnostic_timeout,
                               on_event=lambda e: n.console.print(f"{e['done']}/{e['total']} {e['result']['scenario']} {e['result']['device']['ip']}: {e['result']['status']}") if e['type'] == 'result' else None)
    finally:
        vault.clear()
    n.console.print(f'Saved: {path}')
    return 0 if data['status'] == 'complete' and all(r['status'] == 'ok' for r in data['results']) else 1


def cli_retry(data):
    ok = {r.get('job_id') for r in data['results'] if r['status'] == 'ok'}
    jobs = [j for j in data['jobs'] if j['id'] not in ok]
    if not jobs:
        n.console.print('All jobs succeeded. Nothing to retry.')
        return 0
    n.console.print('Retry reruns all commands for each failed or unfinished job.')
    return cli_batch(None, jobs=jobs, strict=data['strict_host_keys'], timeout=data['timeout'], diagnostic_timeout=data['diagnostic_timeout'])
