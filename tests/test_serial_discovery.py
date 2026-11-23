import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
import netdesk as n
import netdesk_serial as engine
from netdesk_serial_gui import SerialBatchDialog
from test_serial_batch import VirtualDevice


def adapter(path):
    return SimpleNamespace(device=path, description='Test USB adapter', vid=1234)


class SerialDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.ports = [adapter('/dev/ttyUSB0'), adapter('/dev/ttyUSB1')]
        self.detect = patch.object(n, 'detected_ports', side_effect=lambda: self.ports)
        self.detect.start()
        self.dialog = SerialBatchDialog()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.detect.stop()

    def test_discovery_adds_without_connecting_and_preserves_settings(self):
        d = self.dialog
        self.assertEqual(len(d.entries), 2)
        entry = d.entries[0]
        entry['username'].setText('operator')
        entry['password'].setText('secret')
        entry['baud'].setValue(115200)
        entry['preset'].setCurrentText('custom')
        QTest.keyClicks(d.preview, 'dis cur')
        QTest.keyClick(d.preview, Qt.Key.Key_Tab)
        self.ports.append(adapter('/dev/ttyUSB2'))
        with patch.object(engine.serial, 'Serial') as serial:
            d.refresh_ports()
            d.refresh_ports()
            serial.assert_not_called()
        self.assertEqual(len(d.entries), 3)
        self.assertEqual(d.table.currentRow(), 0)
        self.assertEqual(d.preview.toPlainText(), 'display current-configuration')
        self.assertEqual(entry['custom'], ['display current-configuration'])
        self.assertEqual(entry['password'].text(), 'secret')
        self.assertEqual(entry['baud'].value(), 115200)
        self.ports.pop(0)
        d.refresh_ports()
        self.assertEqual(len(d.entries), 3)
        self.assertEqual(entry['status'].text(), 'Not detected / manual')
        self.assertEqual(entry['password'].text(), 'secret')

    def test_removed_port_stays_removed_until_restore(self):
        d = self.dialog
        original_second = d.entries[1]
        with patch('netdesk_serial_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes) as confirm:
            d.entries[0]['remove'].click()
            self.assertIn('/dev/ttyUSB0', confirm.call_args.args[2])
        d.refresh_ports()
        self.assertEqual(d.entries, [original_second])
        d.restore_ports()
        self.assertEqual(len(d.entries), 2)
        with patch('netdesk_serial_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            original_second['remove'].click()
        self.assertEqual(d.entries[0]['port'].currentText(), '/dev/ttyUSB0')

    def test_only_checked_ports_are_validated_and_use_shared_login(self):
        d = self.dialog
        d.entries[0]['username'].setText('shared')
        d.entries[0]['password'].setText('secret')
        d.shared.setChecked(True)
        d.entries[0]['selected'].setChecked(False)
        jobs, credentials = d.build()
        self.assertEqual([j['port'] for j in jobs], ['/dev/ttyUSB1'])
        self.assertEqual(credentials, {'/dev/ttyUSB1': ('shared', 'secret')})
        d.select_ports(False)
        with self.assertRaises(ValueError):
            d.build()

    def test_busy_discovery_is_frozen_and_reserved_port_is_rechecked(self):
        d = self.dialog
        d.busy = True
        self.ports.append(adapter('/dev/ttyUSB2'))
        d.refresh_ports()
        self.assertEqual(len(d.entries), 2)
        d.busy = False
        d.reserved_provider = lambda: ['/dev/ttyUSB1']
        with self.assertRaises(ValueError):
            d.build()
        d.entries[1]['selected'].setChecked(False)
        self.assertEqual(len(d.build()[0]), 1)

    def test_several_new_adapters_appear_and_removed_one_returns_after_replug(self):
        d = self.dialog
        self.ports += [adapter('/dev/ttyUSB2'), adapter('/dev/ttyUSB3')]
        d.refresh_ports()
        self.assertEqual([e['port'].currentText() for e in d.entries],
                         ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3'])
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
            d.remove_entry(d.entries[2])
        d.refresh_ports()
        self.assertNotIn('/dev/ttyUSB2', [e['port'].currentText() for e in d.entries])
        self.ports[:] = [p for p in self.ports if p.device != '/dev/ttyUSB2']
        d.refresh_ports()
        self.ports.append(adapter('/dev/ttyUSB2'))
        d.refresh_ports()
        self.assertIn('/dev/ttyUSB2', [e['port'].currentText() for e in d.entries])

    def test_no_adapter_placeholder_filled_on_hotplug(self):
        self.ports.clear()
        other = SerialBatchDialog()
        try:
            self.assertEqual(len(other.entries), 1)
            self.ports.append(adapter('/dev/ttyUSB7'))
            other.refresh_ports()
            self.assertEqual(len(other.entries), 1)
            self.assertEqual(other.entries[0]['port'].currentText(), '/dev/ttyUSB7')
        finally:
            other.close()
            other.deleteLater()

    def test_main_window_reserves_the_actual_manual_port_path(self):
        from netdesk_gui import MainWindow
        window = MainWindow()
        try:
            window.port.setCurrentText('/dev/ttyUSB0')
            with patch.object(window.serial, 'isOpen', return_value=True):
                self.assertEqual(window.serial_collection.current_reserved(), ['/dev/ttyUSB0'])
                with self.assertRaises(ValueError):
                    window.serial_collection.build()
        finally:
            window.close()
            window.deleteLater()

    def test_windows_com_port_validation_without_opening_device(self):
        job = {'port': 'COM12', 'platform': 'huawei', 'preset': 'config'}
        self.assertEqual(engine.validate_jobs([job])[0]['port'], 'COM12')
        with self.assertRaises(ValueError):
            engine.validate_jobs([job, dict(job, port='com12')])
        with self.assertRaises(ValueError):
            engine.validate_jobs([job], reserved_ports=['com12'])
        with self.assertRaises(ValueError):
            engine.validate_jobs([dict(job, port='COM0')])

    def test_detected_virtual_devices_login_collect_and_leave_unchecked_port_alone(self):
        devices = [VirtualDevice(name=f'auto{i}', user=f'user{i}', password=f'secret{i}') for i in range(3)]
        try:
            self.ports[:] = [adapter(device.port) for device in devices]
            with tempfile.TemporaryDirectory() as folder, patch.object(n, 'ROOT', Path(folder)):
                dialog = SerialBatchDialog()
                try:
                    for entry, device in zip(dialog.entries, devices):
                        entry['username'].setText(device.user)
                        entry['password'].setText(device.password)
                        entry['preset'].setCurrentText('config')
                    dialog.entries[2]['selected'].setChecked(False)
                    jobs, credentials = dialog.build()
                    dialog.launch(jobs, credentials)
                    deadline = time.monotonic() + 8
                    while dialog.busy and time.monotonic() < deadline:
                        QTest.qWait(30)
                    dialog.worker.join(timeout=2)
                    dialog.poll()
                    self.assertFalse(dialog.busy)
                    self.assertEqual([e['status'].text() for e in dialog.entries], ['ok', 'ok', 'Skipped'])
                    self.assertEqual(devices[2].received, [])
                    self.assertEqual(len(list(dialog.last_run.glob('*/configuration.txt'))), 2)
                    self.assertTrue((dialog.last_run / 'summary.json').exists())
                    for log in dialog.last_run.glob('*/session.log'):
                        self.assertNotIn('secret', log.read_text())
                finally:
                    dialog.close()
                    dialog.deleteLater()
        finally:
            for device in devices:
                device.close()
