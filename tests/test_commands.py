import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from netdesk_commands import suggestions
from netdesk_command_gui import CommandEdit, CommandLibrary
from netdesk_gui import RangesDialog, ScenarioDialog


class CommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_abbreviation_typo_and_platform_scope(self):
        self.assertEqual(suggestions('dis cur'), ['display current-configuration'])
        self.assertIn('display current-configuration', suggestions('display current-configuraion'))
        self.assertNotIn('display current-configuration', suggestions('dis cur', 'cisco_ios'))
        self.assertGreater(len(suggestions('display ip')), 1)

    def test_tab_completes_only_current_line_and_question_help_is_not_inserted(self):
        editor = CommandEdit('display version\ndis cur')
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        self.assertEqual(editor.toPlainText(), 'display version\ndisplay current-configuration')
        QTest.keyClicks(editor, '?')
        self.assertNotIn('?', editor.toPlainText())
        editor.menu.close()
        editor.close()

    def test_library_category_search_and_deduplication(self):
        dialog = CommandLibrary()
        dialog.search.setText('current-configuration')
        self.assertGreater(dialog.tree.topLevelItemCount(), 0)
        dialog.tree.setCurrentItem(dialog.tree.topLevelItem(0))
        dialog.append_selected()
        dialog.append_selected()
        self.assertEqual(dialog.commands(), ['display current-configuration'])
        dialog.category.setCurrentText('Routing')
        self.assertEqual(dialog.tree.topLevelItemCount(), 0)
        dialog.close()

    def test_range_custom_commands_preview_and_execution_match(self):
        dialog = RangesDialog()
        dialog.rows.cellWidget(0, 0).setText('192.0.2.1-2')
        dialog.rows.cellWidget(0, 1).setText('test')
        dialog.rows.cellWidget(0, 2).setText('secret')
        presets = dialog.rows.cellWidget(0, 4)
        presets.custom_commands = ['display current-configuration', 'display ip interface brief']
        presets.setCurrentText('custom')
        dialog.rows.setCurrentCell(0, 0)
        jobs, vault = dialog.build()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]['commands'], dialog.range_preview.toPlainText().splitlines())
        dialog.rows.cellWidget(0, 3).setCurrentText('cisco_ios')
        self.assertEqual(presets.currentText(), 'custom')
        self.assertEqual(presets.custom_commands, jobs[0]['commands'])
        dialog.close()

    def test_mixed_platform_preview_contains_exact_commands(self):
        dialog = ScenarioDialog({}, devices=[{'ip': '192.0.2.1', 'device_type': 'huawei'}, {'ip': '192.0.2.2', 'device_type': 'cisco_ios'}])
        dialog.preset.setCurrentText('config')
        self.assertIn('display current-configuration', dialog.commands.toPlainText())
        self.assertIn('show running-config', dialog.commands.toPlainText())
        dialog.close()
