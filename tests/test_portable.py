import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication
from netdesk_gui import MainWindow, ScenarioDialog, RangesDialog, spin
import netdesk_paths as paths
import netdesk_batch as batch


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_numeric_controls_remain_ascii_under_bangla_locale(self):
        original = QLocale()
        QLocale.setDefault(QLocale('bn_BD'))
        try:
            self.assertEqual(spin(1, 65535, 22).text(), '22')
            self.assertEqual(spin(1, 86400, 7200).text(), '7200')
            scenario = ScenarioDialog({})
            ranges = RangesDialog()
            self.assertEqual(scenario.port.text(), '22')
            self.assertEqual(ranges.rows.cellWidget(0, 5).text(), '22')
            scenario.close(); ranges.close()
        finally:
            QLocale.setDefault(original)

    def test_packaged_data_is_writable_user_location_not_bundle(self):
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {'XDG_DATA_HOME': '/tmp/netdesk-data'}, clear=True), patch.object(sys, 'platform', 'linux'):
            self.assertEqual(paths.data_root(), Path('/tmp/netdesk-data/netdesk'))
            self.assertNotEqual(paths.data_root(), paths.RESOURCE_ROOT)
        with patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, {'LOCALAPPDATA': '/tmp/local-data'}, clear=True), patch.object(sys, 'platform', 'win32'):
            self.assertEqual(paths.data_root(), Path('/tmp/local-data/NetDesk'))

    def test_custom_data_root_and_console_launcher(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NETDESK_DATA_DIR': folder}):
            self.assertEqual(paths.data_root(), Path(folder).resolve())
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', '/opt/NetDesk/NetDesk'), patch.object(sys, 'platform', 'linux'):
            self.assertEqual(paths.cli_command('serial'), ['/opt/NetDesk/NetDeskCLI', 'serial'])

    def test_sidebar_tracks_navigation_and_selected_commands(self):
        window = MainWindow()
        window.tabs.setCurrentIndex(2)
        self.assertTrue(window.nav_buttons[2].isChecked())
        self.assertEqual(window.page_heading.text(), 'Run history')
        window.jobs = batch.make_jobs('Test', '192.0.2.1', preset='config')
        window.refresh_table()
        window.table.selectRow(0)
        self.assertEqual(window.job_preview.toPlainText(), 'display current-configuration')
        window.close()
