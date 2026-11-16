import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import netdesk as n
import netdesk_batch as b


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs = b.make_jobs('Office', '192.0.2.1-2', preset='config', credential_group='Shared')
        self.jobs += b.make_jobs('Remote', '192.0.2.3', preset='quick', credential_group='Different')
        self.vault = {'Shared': ('office-user', 'office-secret'), 'Different': ('other-user', 'other-secret')}

    def tearDown(self):
        self.tmp.cleanup()

    def connection(self):
        conn = Mock()
        conn.find_prompt.return_value = '<switch>'
        conn.send_command.return_value = 'test output'
        return conn

    def test_mixed_credentials_reach_only_assigned_hosts(self):
        calls = {}
        def connect(**kw):
            calls[kw['host']] = (kw['username'], kw['password'])
            return self.connection()
        with patch.object(n, 'ConnectHandler', side_effect=connect):
            path, summary = b.run_batch(self.jobs, self.vault, runs_dir=self.root)
        self.assertEqual(calls['192.0.2.1'], self.vault['Shared'])
        self.assertEqual(calls['192.0.2.2'], self.vault['Shared'])
        self.assertEqual(calls['192.0.2.3'], self.vault['Different'])
        self.assertTrue(all(r['status'] == 'ok' for r in summary['results']))
        for file in path.rglob('*'):
            if file.is_file():
                text = file.read_text()
                for credentials in self.vault.values():
                    for secret in credentials:
                        self.assertNotIn(secret, text)

    def test_plan_roundtrip_whitelists_fields_and_file_mode(self):
        self.jobs[0]['password'] = 'should-not-save'
        self.jobs[0]['username'] = 'should-not-save'
        self.jobs[0]['device']['password'] = 'should-not-save'
        path = self.root/'plan.json'
        b.save_plan(path, self.jobs)
        self.assertNotIn('should-not-save', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        loaded = b.load_plan(path)
        self.assertEqual(len(loaded), 3)
        self.assertEqual(loaded[2]['credential_group'], 'Different')

    def test_missing_credentials_prevents_any_network_or_run_creation(self):
        with patch.object(n, 'ConnectHandler') as connect:
            with self.assertRaises(ValueError):
                b.run_batch(self.jobs, {'Shared': self.vault['Shared']}, runs_dir=self.root)
        connect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_same_endpoint_is_serialized_across_scenarios(self):
        jobs = b.make_jobs('A', '192.0.2.1', preset='config', credential_group='Shared') + b.make_jobs('B', '192.0.2.1', preset='config', credential_group='Shared')
        active = 0
        maximum = 0
        guard = threading.Lock()
        def collect(device, commands, run_dir, username, password, *args, **kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.03)
            folder = kwargs['folder_key']
            (run_dir/folder).mkdir()
            with guard:
                active -= 1
            return {'device': device, 'folder': folder, 'status': 'ok'}
        with patch.object(n, 'collect_one', side_effect=collect):
            path, result = b.run_batch(jobs, self.vault, workers=2, runs_dir=self.root)
        self.assertEqual(maximum, 1)
        self.assertEqual(len({r['folder'] for r in result['results']}), 2)

    def test_distinct_devices_run_concurrently(self):
        barrier = threading.Barrier(2)
        def connect(**kwargs):
            barrier.wait(timeout=3)
            return self.connection()
        with patch.object(n, 'ConnectHandler', side_effect=connect):
            _, result = b.run_batch(self.jobs[:2], self.vault, workers=2, runs_dir=self.root)
        self.assertTrue(all(r['status'] == 'ok' for r in result['results']))

    def test_cancel_before_start_connects_to_nothing(self):
        stop = threading.Event()
        stop.set()
        with patch.object(n, 'ConnectHandler') as connect:
            _, result = b.run_batch(self.jobs, self.vault, stop=stop, runs_dir=self.root)
        connect.assert_not_called()
        self.assertEqual(result['status'], 'interrupted')
        self.assertTrue(all(r['status'] == 'cancelled' for r in result['results']))

    def test_duplicate_targets_same_scenario_rejected(self):
        jobs = b.make_jobs('A', '192.0.2.1') + b.make_jobs('A', '192.0.2.1')
        with self.assertRaises(ValueError):
            b.validate_jobs(jobs)

    def test_dry_run_plan_cli_no_credentials(self):
        path = self.root/'plan.json'
        b.save_plan(path, self.jobs)
        with patch.object(n, 'ConnectHandler') as connect, patch.object(n, 'credentials') as credentials:
            self.assertEqual(n.main(['batch', str(path), '--dry-run']), 0)
        connect.assert_not_called()
        credentials.assert_not_called()

    def test_scenario_compare_does_not_cross_match_same_ip(self):
        jobs = b.make_jobs('A', '192.0.2.1', preset='config', credential_group='Shared') + b.make_jobs('B', '192.0.2.1', preset='config', credential_group='Shared')
        with patch.object(n, 'ConnectHandler', side_effect=lambda **kw: self.connection()):
            old, old_data = b.run_batch(jobs, self.vault, runs_dir=self.root)
            new, new_data = b.run_batch(jobs, self.vault, runs_dir=self.root)
        for r in old_data['results']:
            (old/r['folder']/'configuration.txt').write_text(r['scenario'] + '\n')
        for r in new_data['results']:
            (new/r['folder']/'configuration.txt').write_text(r['scenario'] + '\n')
        diff = n.compare_runs(old, new)
        self.assertEqual(diff.count('no configuration differences'), 2)

if __name__ == '__main__':
    unittest.main()
