import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import netdesk as n
from netdesk_gui import STYLE
from netdesk_serial_gui import SerialBatchDialog
from test_serial_batch import VirtualDevice


class SerialGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(n, 'ROOT', self.root)
        self.patch.start()
        with patch.object(n, 'detected_ports', return_value=[]):
            self.dialog = SerialBatchDialog()
        self.devices = []

    def tearDown(self):
        self.dialog.close()
        for device in self.devices:
            device.close()
        self.patch.stop()
        self.tmp.cleanup()

    def test_multi_port_gui_collects_and_displays_results(self):
        for index in range(3):
            device = VirtualDevice(name=f'console{index}', user=f'operator{index}', password=f'console-secret-{index}')
            self.devices.append(device)
            if index:
                self.dialog.add_row()
            entry = self.dialog.entries[index]
            entry['port'].setCurrentText(device.port)
            entry['username'].setText(device.user)
            entry['password'].setText(device.password)
            entry['preset'].setCurrentText('config')
        self.dialog.show()
        jobs, credentials = self.dialog.build()
        self.dialog.launch(jobs, credentials)
        deadline = time.monotonic() + 6
        while self.dialog.busy and time.monotonic() < deadline:
            QTest.qWait(30)
            self.dialog.poll()
        self.dialog.worker.join(timeout=2)
        self.assertFalse(self.dialog.busy)
        self.assertEqual(self.dialog.progress.value(), 3)
        self.assertEqual([entry['status'].text() for entry in self.dialog.entries], ['ok']*3)
        self.assertTrue((self.dialog.last_run/'summary.json').exists())
        self.assertEqual(credentials, {})

    def test_custom_commands_and_shared_login(self):
        self.dialog.add_row()
        for index, entry in enumerate(self.dialog.entries):
            entry['port'].setCurrentText(f'/dev/ttyUSB{index}')
            entry['custom'] = ['display clock']
            entry['preset'].setCurrentText('custom')
        self.dialog.entries[0]['username'].setText('shared-user')
        self.dialog.entries[0]['password'].setText('shared-secret')
        self.dialog.shared.setChecked(True)
        jobs, vault = self.dialog.build()
        self.assertEqual([j['commands'] for j in jobs], [['display clock']]*2)
        self.assertEqual(vault['/dev/ttyUSB1'], ('shared-user', 'shared-secret'))
        self.dialog.entries[1]['port'].setCurrentText('/dev/ttyUSB0')
        with self.assertRaises(ValueError):
            self.dialog.build()

    def test_interactive_port_is_reserved(self):
        self.dialog.reserved_ports = ['/dev/ttyUSB0']
        self.dialog.entries[0]['port'].setCurrentText('/dev/ttyUSB0')
        with self.assertRaises(ValueError):
            self.dialog.build()

    def test_close_clears_login_fields(self):
        self.dialog.entries[0]['username'].setText('operator')
        self.dialog.entries[0]['password'].setText('password')
        self.dialog.reject()
        self.assertEqual(self.dialog.entries[0]['username'].text(), '')
        self.assertEqual(self.dialog.entries[0]['password'].text(), '')
