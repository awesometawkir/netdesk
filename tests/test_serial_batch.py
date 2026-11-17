import os
import pty
import select
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import netdesk as n
import netdesk_serial as s


class VirtualDevice:
    """Tiny device emulator on a PTY; never touches a physical serial adapter."""
    def __init__(self, name='router', user='admin', password='test-pass', barrier=None, hang=False, reject=False, pager=False, authenticated=False):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.name, self.user, self.password = name, user, password
        self.barrier, self.hang, self.reject, self.pager = barrier, hang, reject, pager
        self.state = 'ready' if authenticated else 'wake'
        self.prompt = '<' + name + '>'
        self.received, self.commands = [], []
        self.stop = threading.Event()
        self.command_started = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def write(self, text):
        os.write(self.master, text.encode())

    def serve(self):
        buffer = b''
        pending_more = False
        try:
            while not self.stop.is_set():
                if not select.select([self.master], [], [], .05)[0]:
                    continue
                buffer += os.read(self.master, 4096)
                if pending_more and buffer.startswith(b' '):
                    buffer = buffer[1:]
                    self.write('\r\nsecond page\r\n' + self.prompt)
                    pending_more = False
                while b'\r' in buffer:
                    line, buffer = buffer.split(b'\r', 1)
                    value = line.decode()
                    self.received.append(value)
                    if self.state == 'wake':
                        self.write('Username: ')
                        self.state = 'user'
                    elif self.state == 'user':
                        self.write('Password: ')
                        self.state = 'password'
                    elif self.state == 'password':
                        if self.reject or value != self.password or self.received[-2] != self.user:
                            self.write('\r\nAuthentication failed\r\nUsername: ')
                        else:
                            # Echo across reads to exercise credential redaction.
                            self.write(value[:3])
                            time.sleep(.02)
                            self.write(value[3:] + '\r\n' + self.prompt)
                        self.state = 'ready'
                    elif not value:
                        self.write('\r\n' + self.prompt)
                    elif value in s.PAGING.values():
                        self.write(value + '\r\n' + self.prompt)
                    else:
                        self.commands.append(value)
                        self.command_started.set()
                        if self.barrier:
                            self.barrier.wait(timeout=4)
                        self.write(value + '\r\noutput from ' + self.name + '\r\n')
                        if self.hang:
                            continue
                        if self.pager:
                            self.write('--More--')
                            pending_more = True
                        else:
                            self.write(self.prompt)
        except (OSError, ValueError, threading.BrokenBarrierError):
            return

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass


class SerialBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.devices = []

    def tearDown(self):
        for device in self.devices:
            device.close()
        self.tmp.cleanup()

    def device(self, **kwargs):
        device = VirtualDevice(**kwargs)
        self.devices.append(device)
        return device

    def job(self, device):
        return {'port': device.port, 'baud': 9600, 'platform': 'huawei', 'preset': 'config'}

    def run_devices(self, devices, **kwargs):
        return s.run_serial_batch([self.job(d) for d in devices], {d.port: (d.user, d.password) for d in devices},
                                  runs_dir=self.root, login_timeout=2, timeout=kwargs.pop('timeout', 2), **kwargs)

    def test_three_ports_login_and_collect_concurrently(self):
        barrier = threading.Barrier(3)
        devices = [self.device(name=f'router{i}', user=f'user{i}', password=f'unique-secret-{i}', barrier=barrier) for i in range(3)]
        path, result = self.run_devices(devices)
        self.assertEqual([r['status'] for r in result['results']], ['ok'] * 3)
        self.assertEqual(len({r['folder'] for r in result['results']}), 3)
        for device in devices:
            self.assertEqual(device.received[1:3], [device.user, device.password])
            self.assertEqual(device.commands, ['display current-configuration'])
        for file in path.rglob('*'):
            if file.is_file():
                text = file.read_text()
                for device in devices:
                    self.assertNotIn(device.password, text)
                    self.assertNotIn(device.user, text)
        self.assertTrue((path/'summary.csv').exists())

    def test_timeout_preserves_partial_command(self):
        device = self.device(hang=True)
        path, result = self.run_devices([device], timeout=.4)
        self.assertEqual(result['results'][0]['status'], 'timeout')
        partials = list(path.rglob('*.partial.txt'))
        self.assertEqual(len(partials), 1)
        self.assertIn('output from router', partials[0].read_text())
        self.assertFalse(list(path.rglob('configuration.txt')))

    def test_rejected_login_does_not_run_commands(self):
        device = self.device(reject=True)
        _, result = self.run_devices([device])
        self.assertEqual(result['results'][0]['status'], 'authentication_failed')
        self.assertEqual(device.commands, [])
        self.assertEqual(device.received.count(device.password), 1)

    def test_pager_is_advanced_and_full_response_saved(self):
        device = self.device(pager=True)
        path, result = self.run_devices([device])
        self.assertEqual(result['results'][0]['status'], 'ok')
        self.assertIn('second page', next(path.rglob('configuration.txt')).read_text())

    def test_already_authenticated_console_and_custom_commands(self):
        device = self.device(authenticated=True)
        job = self.job(device)
        job.update(preset='custom', commands=['display clock'])
        _, result = s.run_serial_batch([job], {}, runs_dir=self.root, login_timeout=2, timeout=2)
        self.assertEqual(result['results'][0]['status'], 'ok')
        self.assertEqual(device.commands, ['display clock'])

    def test_duplicate_and_reserved_ports_prevent_open(self):
        device = self.device()
        alias = dict(self.job(device), port=device.port.replace('/pts/', '/pts/../pts/'))
        with patch.object(s.serial, 'Serial') as opened:
            with self.assertRaises(ValueError):
                s.run_serial_batch([self.job(device), alias], {}, runs_dir=self.root)
            with self.assertRaises(ValueError):
                s.run_serial_batch([self.job(device)], {}, reserved_ports=[device.port], runs_dir=self.root)
        opened.assert_not_called()

    def test_cancel_during_read_closes_port_and_preserves_output(self):
        device = self.device(hang=True)
        stop = threading.Event()
        def cancel():
            device.command_started.wait(3)
            time.sleep(.1)
            stop.set()
        thread = threading.Thread(target=cancel)
        thread.start()
        path, result = self.run_devices([device], stop=stop)
        thread.join()
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['results'][0]['status'], 'cancelled')
        self.assertTrue(list(path.rglob('*.partial.txt')))
        # The collector released its serial lock.
        with s.serial.Serial(device.port, exclusive=True, timeout=.1):
            pass

    def test_failed_port_does_not_stop_other_devices(self):
        failed = self.device(name='rejected', reject=True)
        healthy = self.device(name='healthy')
        _, result = self.run_devices([failed, healthy])
        statuses = {r['port']: r['status'] for r in result['results']}
        self.assertEqual(statuses[failed.port], 'authentication_failed')
        self.assertEqual(statuses[healthy.port], 'ok')

    def test_unplug_is_reported_as_failure_and_closes_logs(self):
        device = self.device(hang=True)
        def unplug():
            device.command_started.wait(3)
            device.stop.set()
            descriptor = device.master
            device.master = -1
            os.close(descriptor)
        thread = threading.Thread(target=unplug)
        thread.start()
        path, result = self.run_devices([device])
        thread.join()
        self.assertEqual(result['results'][0]['status'], 'failed')
        self.assertTrue(list(path.rglob('session.log')))
        self.assertTrue((path/'summary.json').exists())

    def test_dry_run_does_not_open_ports(self):
        with patch.object(s.serial, 'Serial') as opened:
            self.assertEqual(n.main(['serial-collect', '--ports', '/dev/ttyUSB0', '/dev/ttyUSB1', '--dry-run']), 0)
        opened.assert_not_called()

    def test_writer_redacts_secrets_split_at_every_boundary(self):
        for boundary in range(1, len('secret-value')):
            path = self.root/f'{boundary}.log'
            writer = s.RedactedWriter(path, ['secret-value'])
            writer.write('before secret-value'[:7+boundary])
            writer.write('secret-value'[boundary:] + ' after')
            writer.close()
            self.assertEqual(path.read_text(), 'before [redacted] after')


if __name__ == '__main__':
    unittest.main()
