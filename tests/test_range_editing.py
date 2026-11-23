import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from netdesk_gui import RangesDialog


class RangeEditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = RangesDialog()
        self.dialog.show()
        self.app.processEvents()
        for row in range(2):
            self.dialog.rows.cellWidget(row, 0).setText(f'192.0.2.{row + 1}')
            self.dialog.rows.cellWidget(row, 1).setText('test-user')
            self.dialog.rows.cellWidget(row, 2).setText('test-secret')

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_custom_is_editable_and_each_row_keeps_its_own_commands(self):
        d = self.dialog
        for row, command in [(0, 'display current-configuration'), (1, 'display version')]:
            d.rows.cellWidget(row, 4).setCurrentText('custom')
            self.assertEqual(d.rows.currentRow(), row)
            self.assertFalse(d.range_preview.isReadOnly())
            QTest.mouseClick(d.range_preview.viewport(), Qt.MouseButton.LeftButton)
            QTest.keyClicks(d.range_preview, command)
        QTest.mouseClick(d.rows.cellWidget(0, 1), Qt.MouseButton.LeftButton)
        self.assertEqual(d.rows.currentRow(), 0)
        self.assertEqual(d.range_preview.toPlainText(), 'display current-configuration')
        self.assertIn('192.0.2.1', d.preview_label.text())
        jobs, _ = d.build()
        self.assertEqual(jobs[0]['commands'], ['display current-configuration'])
        self.assertEqual(jobs[1]['commands'], ['display version'])

    def test_custom_draft_survives_preset_switch_and_completion(self):
        d = self.dialog
        preset = d.rows.cellWidget(0, 4)
        preset.setCurrentText('custom')
        QTest.keyClicks(d.range_preview, 'dis cur')
        QTest.keyClick(d.range_preview, Qt.Key.Key_Tab)
        QTest.keyClick(d.range_preview, Qt.Key.Key_Return)
        expected = 'display current-configuration\n'
        self.assertEqual(d.range_preview.toPlainText(), expected)
        preset.setCurrentText('quick')
        self.assertTrue(d.range_preview.isReadOnly())
        preset.setCurrentText('custom')
        self.assertEqual(d.range_preview.toPlainText(), expected)

    def test_remove_button_targets_its_row_even_when_another_is_selected(self):
        d = self.dialog
        d.add_row()
        d.rows.cellWidget(2, 0).setText('192.0.2.3')
        last_button = d.rows.cellWidget(2, 6)
        d.rows.setCurrentCell(0, 0)
        with patch('netdesk_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes) as confirm:
            d.rows.cellWidget(1, 6).click()
            self.assertIn('row 2: 192.0.2.2', confirm.call_args.args[2])
            self.assertNotIn('test-secret', confirm.call_args.args[2])
            self.assertEqual(d.rows.rowCount(), 2)
            self.assertEqual(d.rows.cellWidget(1, 0).text(), '192.0.2.3')
            # The original button must follow its range after rows shift.
            last_button.click()
            self.assertIn('row 2: 192.0.2.3', confirm.call_args.args[2])
            self.assertEqual(d.rows.rowCount(), 1)
            self.assertEqual(d.rows.cellWidget(0, 0).text(), '192.0.2.1')

    def test_cancel_removal_and_last_row_removal(self):
        d = self.dialog
        with patch('netdesk_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            d.rows.cellWidget(0, 6).click()
        self.assertEqual(d.rows.rowCount(), 2)
        with patch('netdesk_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            d.rows.cellWidget(1, 6).click()
            d.rows.cellWidget(0, 6).click()
        self.assertEqual(d.rows.rowCount(), 1)
        self.assertEqual(d.rows.cellWidget(0, 0).text(), '')
        self.assertTrue(d.range_preview.isReadOnly())

    def test_shared_login_removal_explains_change(self):
        d = self.dialog
        d.shared.setChecked(True)
        with patch('netdesk_gui.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as confirm:
            d.rows.cellWidget(0, 6).click()
        self.assertIn('next row will become the shared login', confirm.call_args.args[2])
