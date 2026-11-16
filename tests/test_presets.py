import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import netdesk as n
import netdesk_batch as b
from netmiko.ssh_dispatcher import CLASS_MAPPER
from netdesk_presets import PRESETS, PLATFORMS, SCENARIOS, available_presets, get_commands


class PresetTests(unittest.TestCase):
    def test_catalog_drivers_and_commands_are_valid(self):
        self.assertEqual(set(PLATFORMS), set(PRESETS))
        for platform, presets in PRESETS.items():
            self.assertIn(platform, CLASS_MAPPER)
            self.assertTrue({'quick','config','full'} <= presets.keys())
            for name, commands in presets.items():
                self.assertIn(name, SCENARIOS)
                self.assertTrue(commands)
                self.assertEqual(len(commands), len(set(commands)))
                for command in commands:
                    self.assertNotIn('\n', command)
                    self.assertNotIn('password', command.lower())
                    self.assertNotIn('show-sensitive', command)
                jobs = b.make_jobs('test', '192.0.2.1', platform=platform, preset=name)
                self.assertEqual(jobs[0]['commands'], commands)
            self.assertIn(presets['config'][0], n.CONFIG_COMMANDS)
        self.assertEqual(CLASS_MAPPER['ubiquiti_edgerouter'].__name__, 'UbiquitiEdgeRouterSSH')

    def test_mixed_inventory_gets_only_common_scenarios(self):
        common = available_presets(['cisco_ios', 'fortinet'])
        self.assertIn('bgp', common)
        self.assertNotIn('vlans', common)
        self.assertNotIn('vpn', common)
        self.assertEqual(available_presets([]), [])

    def test_unavailable_preset_fails_before_network(self):
        with patch.object(n, 'ConnectHandler') as connect:
            self.assertEqual(n.main(['collect', '--targets', '192.0.2.1', '--platform', 'fortinet', '--preset', 'vlans', '--dry-run']), 2)
        connect.assert_not_called()

    def test_catalog_lists_without_connecting(self):
        with patch.object(n, 'ConnectHandler') as connect:
            self.assertEqual(n.main(['presets', '--platform', 'mikrotik_routeros']), 0)
        connect.assert_not_called()

    def test_bundled_serial_helper_when_local_not_installed(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.object(Path, 'home', return_value=Path(home)), patch.object(n.subprocess, 'call', return_value=0) as call:
                self.assertEqual(n.original_serial_tool('serialports', 'help'), 0)
            self.assertEqual(call.call_args.args[0], [str(n.ROOT/'tools'/'serial'/'serialports'), 'help'])
