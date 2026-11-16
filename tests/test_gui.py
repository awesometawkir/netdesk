import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pty
import select
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest

import netdesk as n
import netdesk_batch as b
from netdesk_gui import MainWindow, ScenarioDialog, STYLE


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.root_patch = patch.object(n, 'ROOT', self.root)
        self.runs_patch = patch.object(n, 'RUNS', self.root/'logs'/'netdesk')
        self.root_patch.start()
        self.runs_patch.start()
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.app.processEvents()
        self.root_patch.stop()
        self.runs_patch.stop()
        self.tmp.cleanup()

    def test_scenario_dialog_expands_range_and_assigns_shared_login(self):
        dialog = ScenarioDialog({}, parent=self.window)
        dialog.name.setText('Branch A')
        dialog.targets.setPlainText('192.0.2.1-3')
        dialog.login.username.setText('test-user')
        dialog.login.password.setText('test-secret')
        dialog.submit()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.window.accept_scenario(dialog)
        self.assertEqual(len(self.window.jobs), 3)
        self.assertEqual(self.window.vault['Branch A'], ('test-user', 'test-secret'))
        self.assertEqual(self.window.table.rowCount(), 3)
        for row in range(3):
            for col in range(7):
                self.assertNotIn('test-secret', self.window.table.item(row, col).text())

    def test_individual_login_defaults_to_separate_group(self):
        self.window.jobs = b.make_jobs('Office', '192.0.2.1-3')
        self.window.vault = {'Office': ('shared-user', 'shared-password')}
        self.window.refresh_table()
        self.window.table.selectRow(1)
        with patch('netdesk_gui.LoginDialog') as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.value = ('Office / 192.0.2.2', ('other-user', 'other-password'))
            self.window.assign_login()
        self.assertEqual(dialog.call_args.args[1], 'Office / 192.0.2.2')
        self.assertEqual(self.window.jobs[0]['credential_group'], 'Office')
        self.assertEqual(self.window.jobs[1]['credential_group'], 'Office / 192.0.2.2')
        self.assertEqual(self.window.jobs[2]['credential_group'], 'Office')
        self.assertEqual(self.window.vault['Office'], ('shared-user', 'shared-password'))

    def test_collection_worker_updates_gui_and_history(self):
        self.window.jobs = b.make_jobs('Office', '192.0.2.1-2', preset='config')
        self.window.vault = {'Office': ('test-user', 'test-secret')}
        self.window.refresh_table()
        conn = Mock()
        conn.find_prompt.return_value = '<switch>'
        conn.send_command.return_value = 'mock configuration'
        with patch.object(n, 'ConnectHandler', return_value=conn):
            self.window.launch_collection(self.window.jobs)
            deadline = time.monotonic() + 5
            while self.window.busy and time.monotonic() < deadline:
                QTest.qWait(30)
                self.window.poll()
            self.window.worker.join(timeout=2)
        self.assertFalse(self.window.busy)
        self.assertEqual(self.window.progress.value(), 2)
        self.assertEqual(self.window.run_tree.topLevelItemCount(), 1)
        self.assertEqual(self.window.table.item(0, 6).text(), 'ok')
        self.assertFalse(self.window.retry_btn.isEnabled())
        self.window.grab().save('/tmp/netdesk-collection-tested.png')

    def test_gui_serial_roundtrip_and_raw_log_with_virtual_port(self):
        master, slave = pty.openpty()
        port = os.ttyname(slave)
        try:
            self.window.tabs.setCurrentIndex(1)
            self.window.port.setEditText(port)
            self.window.baud.setCurrentText('115200')
            self.window.serial_record.setChecked(True)
            with patch('netdesk_gui.message') as warning:
                self.window.toggle_serial()
                self.assertTrue(self.window.serial.isOpen(), warning.call_args)
                os.write(master, b'<test-router>\r\n')
                QTest.qWait(100)
                self.assertIn('<test-router>', self.window.terminal_output.toPlainText())
                self.window.serial_input.setText('display version')
                self.window.send_serial()
                QTest.qWait(50)
                self.assertTrue(select.select([master], [], [], 1)[0])
                self.assertEqual(os.read(master, 4096), b'display version\r')
                self.window.grab().save('/tmp/netdesk-serial-tested.png')
                self.window.disconnect_serial()
                warning.assert_not_called()
            logs = list((self.root/'logs'/'serial').glob('*.log'))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].read_bytes(), b'<test-router>\r\n')
        finally:
            os.close(master)
            os.close(slave)

    def test_platform_change_updates_scenarios_and_preview(self):
        dialog = ScenarioDialog({}, parent=self.window)
        dialog.preset.setCurrentText('vlans')
        self.assertIn('display vlan', dialog.commands.toPlainText())
        dialog.platform.setCurrentText('fortinet')
        self.assertEqual(dialog.preset.findText('vlans'), -1)
        self.assertNotEqual(dialog.preset.findText('vpn'), -1)
        dialog.preset.setCurrentText('vpn')
        self.assertIn('get vpn ipsec tunnel summary', dialog.commands.toPlainText())
        dialog.reject()

    def test_rejected_duplicate_does_not_change_shared_credentials(self):
        self.window.jobs = b.make_jobs('Office', '192.0.2.1')
        self.window.vault = {'Office': ('old', 'old-secret')}
        dialog = Mock()
        dialog.jobs = b.make_jobs('Office', '192.0.2.1')
        dialog.login_value = ('Office', ('new', 'new-secret'))
        with self.assertRaises(ValueError):
            self.window.accept_scenario(dialog)
        self.assertEqual(self.window.vault['Office'], ('old', 'old-secret'))

    def test_original_serial_wizard_launch_remains_available(self):
        with patch('netdesk_gui.shutil.which', return_value='/usr/bin/gnome-terminal'), patch('netdesk_gui.subprocess.Popen') as launch:
            self.window.terminal([str(n.ROOT/'netdesk'), 'serialportsconnect'])
        self.assertEqual(launch.call_args.args[0], ['gnome-terminal', '--', str(n.ROOT/'netdesk'), 'serialportsconnect'])


if __name__ == '__main__':
    unittest.main()
