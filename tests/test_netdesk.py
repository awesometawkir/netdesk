import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import netdesk as n
from netmiko.exceptions import NetmikoAuthenticationException, ReadTimeout


class NetDeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.device = {'name': '../test device', 'ip': '192.0.2.1', 'device_type': 'huawei', 'port': 22}

    def tearDown(self):
        self.tmp.cleanup()

    def connection(self, output='configuration data'):
        conn = Mock()
        conn.find_prompt.return_value = '<switch>'
        conn.send_command.return_value = output
        return conn

    def test_ranges_dedup_cidr_ipv6_and_bounds(self):
        self.assertEqual(n.parse_targets('192.0.2.1-3,192.0.2.2 192.0.2.4/30'),
                         ['192.0.2.1','192.0.2.2','192.0.2.3','192.0.2.5','192.0.2.6'])
        self.assertEqual(n.parse_targets('2001:db8::/127'), ['2001:db8::','2001:db8::1'])
        for bad in ('', '192.0.2.10-1', '10.0.0.0/8', '2001:db8::/32', '-oProxyCommand=x', '192.0.2.256'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                n.parse_targets(bad)

    def test_csv_roundtrip_and_no_overwrite(self):
        devices = n.validate_devices([self.device, self.device])
        with patch.object(n, 'GROUPS', self.root):
            path = n.save_inventory(devices, '../test')
            self.assertEqual(path.parent, self.root)
            self.assertEqual(n.load_inventory(path), devices)
            with self.assertRaises(FileExistsError):
                n.save_inventory(devices, '../test')
        with self.assertRaises(ValueError):
            n.validate_devices([dict(self.device, port=70000)])

    def test_success_config_and_prompt(self):
        conn = self.connection()
        with patch.object(n, 'ConnectHandler', return_value=conn) as connect:
            result = n.collect_one(self.device, ['display current-configuration'], self.root, 'user', 'secret')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual((self.root/result['folder']/'configuration.txt').read_text(), 'configuration data\n')
        self.assertEqual((self.root/result['folder']).parent, self.root)
        self.assertEqual(connect.call_args.kwargs['password'], 'secret')
        self.assertFalse(connect.call_args.kwargs['session_log_record_writes'])
        self.assertTrue(conn.send_command.call_args.kwargs['expect_string'].startswith('(?m)^'))
        conn.disconnect.assert_called_once()

    def test_command_error_is_not_success_or_config_backup(self):
        with patch.object(n, 'ConnectHandler', return_value=self.connection('Error: Unrecognized command')):
            result = n.collect_one(self.device, ['display current-configuration'], self.root, 'u', 'p')
        self.assertEqual(result['status'], 'command_error')
        self.assertFalse((self.root/result['folder']/'configuration.txt').exists())

    def test_timeout_preserves_partial_session_and_stops_commands(self):
        conn = self.connection()
        conn.send_command.side_effect = ReadTimeout('unfinished')
        def connect(**kwargs):
            Path(kwargs['session_log']).write_text('partial device output')
            return conn
        with patch.object(n, 'ConnectHandler', side_effect=connect):
            result = n.collect_one(self.device, ['display diagnostic-information', 'display version'], self.root, 'u', 'p')
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(conn.send_command.call_count, 1)
        self.assertEqual(conn.send_command.call_args.kwargs['read_timeout'], 7200)
        self.assertEqual((self.root/result['folder']/'session.log').read_text(), 'partial device output')
        self.assertEqual(result['commands'][0]['status'], 'timeout')
        conn.disconnect.assert_called_once()

    def test_auth_failure_and_no_credentials_in_metadata(self):
        with patch.object(n, 'ConnectHandler', side_effect=NetmikoAuthenticationException('secret')):
            result = n.collect_one(self.device, ['display version'], self.root, 'user', 'secret')
        self.assertEqual(result['status'], 'authentication_failed')
        self.assertNotIn('secret', json.dumps(result))

    def test_cancelled_device_never_connects(self):
        event = threading.Event()
        event.set()
        with patch.object(n, 'ConnectHandler') as connect:
            result = n.collect_one(self.device, ['display version'], self.root, 'u', 'p', stop=event)
        self.assertEqual(result['status'], 'cancelled')
        connect.assert_not_called()

    def test_concurrent_results_isolate_failures(self):
        devices = [self.device, dict(self.device, ip='192.0.2.2')]
        def connect(**kwargs):
            if kwargs['host'].endswith('.2'):
                raise NetmikoAuthenticationException('no')
            return self.connection()
        with patch.object(n, 'ConnectHandler', side_effect=connect):
            path, result = n.run_collection(devices, 'config', None, 'u', 'secret', runs_dir=self.root)
        self.assertEqual([r['status'] for r in result['results']], ['ok', 'authentication_failed'])
        self.assertNotIn('secret', (path/'summary.json').read_text())
        self.assertTrue((path/'summary.csv').exists())
        self.assertEqual(len(n.read_summary(path)['results']), 2)

    def test_diff_only_matches_same_device(self):
        with patch.object(n, 'ConnectHandler', return_value=self.connection('before')):
            old, _ = n.run_collection([self.device], 'config', None, 'u', 'p', runs_dir=self.root)
        with patch.object(n, 'ConnectHandler', return_value=self.connection('after')):
            new, _ = n.run_collection([self.device], 'config', None, 'u', 'p', runs_dir=self.root)
        diff = n.compare_runs(old, new)
        self.assertIn('-before', diff)
        self.assertIn('+after', diff)

    def test_dry_run_never_prompts_or_connects(self):
        with patch.object(n, 'ConnectHandler') as connect, patch.object(n, 'credentials') as creds:
            self.assertEqual(n.main(['collect', '--targets', '192.0.2.1-3', '--dry-run']), 0)
        connect.assert_not_called()
        creds.assert_not_called()

    def test_serial_launch_uses_argument_list_and_unique_log(self):
        port = self.root/'ttyUSB0'
        port.touch()
        with patch.object(n, 'ROOT', self.root), patch.object(n.shutil, 'which', return_value='/usr/bin/picocom'), patch.object(n.subprocess, 'call', return_value=0) as call:
            self.assertEqual(n.serial_console(str(port), 115200, True), 0)
        args = call.call_args.args[0]
        self.assertEqual(args[0:3], ['picocom', '--baud', '115200'])
        self.assertEqual(args[-1], str(port))
        self.assertTrue(Path(args[args.index('--logfile')+1]).exists())

    def test_serial_menu_routes_original_workflows(self):
        with patch.object(n, 'choose', side_effect=[0, 1, 2, 3, 4, 5]), patch.object(n, 'number', return_value=115200), patch.object(n, 'original_serial_tool') as helper, patch.object(n, 'serial_logs') as logs:
            self.assertEqual(n.serial_menu(), 0)
        self.assertEqual([call.args for call in helper.call_args_list], [
            ('serialports',), ('serialportsconnect',),
            ('serialports', 'log', '115200'), ('serialports', 'connect', '115200')])
        logs.assert_called_once()

    def test_serial_cli_routes_menu_and_original_aliases(self):
        with patch.object(n, 'serial_menu', return_value=0) as menu:
            self.assertEqual(n.main(['serial']), 0)
        menu.assert_called_once()
        with patch.object(n, 'original_serial_tool', return_value=0) as helper:
            self.assertEqual(n.main(['serialports', 'log', '115200']), 0)
            helper.assert_called_with('serialports', 'log', '115200')
            self.assertEqual(n.main(['serialportsconnect']), 0)
            helper.assert_called_with('serialportsconnect')

    def test_retry_excludes_success_and_preserves_commands(self):
        data = {'version': 1, 'devices': [self.device, dict(self.device, ip='192.0.2.2')],
                'results': [{'device': self.device, 'status': 'ok'}], 'preset': 'quick',
                'custom_commands': ['display clock'], 'strict_host_keys': True,
                'timeout': 60, 'diagnostic_timeout': 120}
        path = self.root/'summary.json'
        path.write_text(json.dumps(data))
        with patch.object(n, 'ask', return_value='y'), patch.object(n, 'credentials', return_value=('u','p')), patch.object(n, 'run_collection') as run:
            n.retry_run(path)
        self.assertEqual(len(run.call_args.args[0]), 1)
        self.assertEqual(run.call_args.args[0][0]['ip'], '192.0.2.2')
        self.assertEqual(run.call_args.args[2], ['display clock'])
        self.assertTrue(run.call_args.kwargs['strict'])


if __name__ == '__main__':
    unittest.main()
