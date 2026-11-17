"""Native Qt desktop UI for NetDesk. Workers communicate only through a queue."""
from __future__ import annotations

import codecs
import ipaddress
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, QIODevice
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QTextCursor
from PySide6.QtSerialPort import QSerialPort
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QTabWidget,
    QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QFileDialog, QProgressBar,
    QSplitter, QGroupBox, QTreeWidget, QTreeWidgetItem,
)
import netdesk as n
import netdesk_batch as batch

STYLE = '''
QWidget { font-family: "DejaVu Sans"; font-size: 13px; color: #24364b; }
QMainWindow, QDialog { background: #f3f6fa; }
QLabel#brand { font-size: 26px; font-weight: 700; color: #0e3257; }
QLabel#subtitle { color: #60748b; }
QLabel#section { font-size: 19px; font-weight: 600; }
QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #e8f1fb; border-color: #6fa0cf; }
QPushButton:disabled { color: #9ca8b6; background: #eef1f5; }
QPushButton#primary { background: #1767b1; color: white; border-color: #1767b1; font-weight: 600; }
QPushButton#primary:hover { background: #11548f; }
QPushButton#primary:disabled { background: #95b4d1; border-color: #95b4d1; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 6px; selection-background-color: #1767b1; }
QTableWidget, QTreeWidget { background: white; alternate-background-color: #f5f8fc; border: 1px solid #d8e0e9; border-radius: 5px; gridline-color: #e7edf4; }
QHeaderView::section { background: #e9eff6; border: none; padding: 9px; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 12px 20px; margin-right: 5px; color: #52687f; }
QTabBar::tab:selected { color: #1767b1; background: #e5effa; border-bottom: 3px solid #1767b1; }
QGroupBox { border: 1px solid #d2dce8; border-radius: 7px; margin-top: 13px; padding: 15px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; }
QProgressBar { border: none; background: #dfe8f2; border-radius: 5px; text-align: center; min-height: 20px; }
QProgressBar::chunk { background: #55a3dd; border-radius: 5px; }
QPlainTextEdit#terminal { background: #101d2c; color: #d9e9f7; font-family: "DejaVu Sans Mono"; font-size: 13px; }
'''


def button(text, callback, primary=False):
    item = QPushButton(text)
    item.clicked.connect(lambda _checked=False: callback())
    if primary:
        item.setObjectName('primary')
    return item


def hint(text):
    label = QLabel(text)
    label.setObjectName('subtitle')
    label.setWordWrap(True)
    return label


def spin(low, high, value):
    item = QSpinBox()
    item.setRange(low, high)
    item.setValue(value)
    return item


def combo(items):
    item = QComboBox()
    item.addItems(items)
    return item


def message(parent, text):
    QMessageBox.warning(parent, 'NetDesk', str(text))


class LoginFields(QWidget):
    def __init__(self, vault, default='', parent=None):
        super().__init__(parent)
        self.vault = vault
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.group = combo(list(vault))
        self.group.setEditable(True)
        self.group.setCurrentText(default)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('Kept in memory for this app session only')
        form.addRow('Login group', self.group)
        form.addRow('Username', self.username)
        form.addRow('Password', self.password)
        form.addRow(hint('Devices with the same login group share credentials. Use a new group for a different password.'))
        self.group.currentTextChanged.connect(self.fill)
        self.fill(default)

    def fill(self, group):
        user, password = self.vault.get(group, ('', ''))
        self.username.setText(user)
        self.password.setText(password)

    def value(self, fallback=''):
        group = self.group.currentText().strip() or fallback
        user, password = self.username.text().strip(), self.password.text()
        if not group:
            raise ValueError('Enter a login group name.')
        if bool(user) != bool(password):
            raise ValueError('Enter both username and password, or leave both blank for later.')
        return group, (user, password) if user else None


class ScenarioDialog(QDialog):
    def __init__(self, vault, devices=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add collection scenario')
        self.resize(670, 690)
        self.devices = devices
        self.jobs = []
        self.login_value = None
        layout = QVBoxLayout(self)
        layout.addWidget(hint('One scenario can contain a single device, several IPs, or a whole range. Add more scenarios to collect them together.'))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('e.g. Branch routers, Core switches, Customer A')
        form.addRow('Scenario name', self.name)
        self.targets = QPlainTextEdit()
        self.targets.setMaximumHeight(80)
        self.targets.setPlaceholderText('192.168.1.10-20\n10.0.0.5, 10.0.0.8\n192.168.2.0/28')
        if devices:
            self.targets.setPlainText('\n'.join(d['ip'] for d in devices))
            self.targets.setReadOnly(True)
        form.addRow('IPs / ranges / subnets', self.targets)
        self.platform = combo(list(n.PRESETS))
        self.port = spin(1, 65535, 22)
        if devices:
            self.platform.setEnabled(False)
            self.port.setEnabled(False)
        form.addRow('Platform', self.platform)
        form.addRow('SSH port', self.port)
        platforms = [d['device_type'] for d in devices] if devices else [self.platform.currentText()]
        self.preset = combo(n.available_presets(platforms) + ['custom'])
        form.addRow('Command preset', self.preset)
        self.preset_hint = hint('')
        form.addRow(self.preset_hint)
        self.commands = QPlainTextEdit()
        self.commands.setMaximumHeight(110)
        form.addRow('Commands (one per line)', self.commands)
        layout.addLayout(form)
        self.login = LoginFields(vault)
        self.login.group.setEditText('')
        self.login.group.lineEdit().setPlaceholderText('Defaults to scenario name')
        layout.addWidget(self.login)
        layout.addWidget(hint('You can leave login fields blank now, then select individual devices and choose “Assign login”. Saved plans never store login credentials.'))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Add to collection')
        self.buttons.accepted.connect(self.submit)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.preset.currentTextChanged.connect(self.update_commands)
        self.platform.currentTextChanged.connect(self.update_presets)
        self.update_commands()

    def update_presets(self):
        previous = self.preset.currentText()
        choices = n.available_presets([self.platform.currentText()]) + ['custom']
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItems(choices)
        self.preset.setCurrentText(previous if previous in choices else 'quick')
        self.preset.blockSignals(False)
        self.update_commands()

    def update_commands(self):
        preset = self.preset.currentText()
        self.preset.setToolTip(n.SCENARIOS.get(preset, 'Your own commands'))
        self.preset_hint.setText(n.SCENARIOS.get(preset, 'Your own commands') + ' • Preview below; model and firmware support may vary.')
        self.commands.setReadOnly(preset != 'custom')
        if preset != 'custom':
            if self.devices and len({d['device_type'] for d in self.devices}) > 1:
                self.commands.setPlainText('Mixed platforms: each device uses its platform’s ' + preset + ' preset.\nChoose custom to use the same commands for all.')
            else:
                driver = self.devices[0]['device_type'] if self.devices else self.platform.currentText()
                self.commands.setPlainText('\n'.join(n.PRESETS[driver][preset]))

    def submit(self):
        try:
            name = self.name.text().strip()
            group, credentials = self.login.value(name)
            custom = None
            if self.preset.currentText() == 'custom':
                custom = [x.strip() for x in self.commands.toPlainText().splitlines() if x.strip() and not x.lstrip().startswith('#')]
                if not custom:
                    raise ValueError('Enter at least one custom command.')
            self.jobs = batch.make_jobs(name, self.targets.toPlainText(), self.platform.currentText(), self.port.value(),
                                       self.preset.currentText(), custom, group, self.devices)
            self.login_value = (group, credentials)
            self.accept()
        except (ValueError, KeyError, TypeError) as error:
            message(self, error)


class RangesDialog(QDialog):
    """One visible row per address set, with independent runtime credentials."""
    def __init__(self, existing_jobs=(), existing_groups=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add IP ranges with separate logins')
        self.resize(1100, 470)
        self.existing_jobs = list(existing_jobs)
        self.existing_groups = set(existing_groups)
        self.jobs, self.credentials = [], {}
        layout = QVBoxLayout(self)
        layout.addWidget(hint('Each row is a separate range or single IP with its own login. Add as many rows as you need, then collect them together.'))
        self.rows = QTableWidget(0, 6)
        self.rows.setHorizontalHeaderLabels(['IP / range / subnet', 'Username', 'Password', 'Platform', 'Preset', 'Port'])
        self.rows.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rows.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rows.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.rows.setColumnWidth(5, 85)
        self.rows.verticalHeader().setDefaultSectionSize(44)
        layout.addWidget(self.rows, 1)
        controls = QHBoxLayout()
        controls.addWidget(button('＋ Add another row', self.add_row))
        controls.addWidget(button('Remove selected rows', self.remove_rows))
        self.shared = QCheckBox('Use the first row’s username and password for every row')
        self.shared.toggled.connect(self.update_shared)
        controls.addWidget(self.shared)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(hint('Examples: 192.168.1.1-10, 192.168.10.1-15, or one IP such as 10.0.0.5. Passwords are masked and never saved in plans. Use Add scenario for custom command lists.'))
        self.error = QLabel('')
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #a12b25;')
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Add all ranges to collection')
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.add_row()
        self.add_row()

    def add_row(self):
        row = self.rows.rowCount()
        self.rows.insertRow(row)
        targets, username, password = QLineEdit(), QLineEdit(), QLineEdit()
        targets.setPlaceholderText('IP or range, e.g. 192.168.1.1-10')
        password.setEchoMode(QLineEdit.EchoMode.Password)
        password.setPlaceholderText('Password for this row')
        platform = combo(list(n.PRESETS))
        presets = combo(n.available_presets(['huawei']))
        def update_presets():
            previous = presets.currentText()
            presets.clear()
            presets.addItems(n.available_presets([platform.currentText()]))
            presets.setCurrentText(previous if presets.findText(previous) >= 0 else 'quick')
        def show_commands():
            if presets.currentText():
                presets.setToolTip('\n'.join(n.get_commands(platform.currentText(), presets.currentText())))
        platform.currentTextChanged.connect(update_presets)
        presets.currentTextChanged.connect(show_commands)
        for column, widget in enumerate((targets, username, password, platform, presets, spin(1, 65535, 22))):
            self.rows.setCellWidget(row, column, widget)
        show_commands()
        self.update_shared()

    def update_shared(self):
        for row in range(self.rows.rowCount()):
            for column in (1, 2):
                self.rows.cellWidget(row, column).setEnabled(row == 0 or not self.shared.isChecked())

    def remove_rows(self):
        for row in sorted({index.row() for index in self.rows.selectionModel().selectedRows()}, reverse=True):
            self.rows.removeRow(row)
        if not self.rows.rowCount():
            self.add_row()
        self.update_shared()

    def build(self):
        jobs, vault = [], {}
        used_names = {j['scenario'] for j in self.existing_jobs}
        used_groups = self.existing_groups | {j['credential_group'] for j in self.existing_jobs}
        count = 1
        first_group = None
        for row in range(self.rows.rowCount()):
            targets = self.rows.cellWidget(row, 0).text().strip()
            own_user = self.rows.cellWidget(row, 1).text().strip()
            own_password = self.rows.cellWidget(row, 2).text()
            if not targets:
                if own_user or own_password:
                    raise ValueError(f'Row {row + 1}: enter the IP or range for this login.')
                continue
            credential_row = 0 if self.shared.isChecked() else row
            username = self.rows.cellWidget(credential_row, 1).text().strip()
            password = self.rows.cellWidget(credential_row, 2).text()
            if not username or not password:
                raise ValueError(f'Row {row + 1}: enter both a username and password' + (' in the first row.' if self.shared.isChecked() else '.'))
            while f'Range {count}' in used_names or f'Range {count} login' in used_groups:
                count += 1
            name = f'Range {count}'
            group = first_group if self.shared.isChecked() and first_group else name + ' login'
            try:
                new_jobs = batch.make_jobs(name, targets, self.rows.cellWidget(row, 3).currentText(),
                                          self.rows.cellWidget(row, 5).value(), self.rows.cellWidget(row, 4).currentText(),
                                          credential_group=group)
            except ValueError as error:
                raise ValueError(f'Row {row + 1}: {error}') from error
            jobs.extend(new_jobs)
            vault[group] = (username, password)
            first_group = first_group or group
            used_names.add(name)
            used_groups.add(group)
            count += 1
        if not jobs:
            raise ValueError('Enter at least one IP or range and its login.')
        batch.validate_jobs(self.existing_jobs + jobs)
        return jobs, vault

    def submit(self):
        try:
            self.jobs, self.credentials = self.build()
            self.accept()
        except (ValueError, KeyError, TypeError) as error:
            self.error.setText(str(error))


class LoginDialog(QDialog):
    def __init__(self, vault, default, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Assign login to selected devices')
        self.resize(570, 260)
        layout = QVBoxLayout(self)
        self.fields = LoginFields(vault, default)
        layout.addWidget(self.fields)
        layout.addWidget(hint('Updating an existing login group changes it for every device assigned to that group.'))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.value = None

    def submit(self):
        try:
            self.value = self.fields.value()
            if self.value[1] is None:
                raise ValueError('Enter a username and password.')
            self.accept()
        except ValueError as error:
            message(self, error)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('NetDesk — Network collection & consoles')
        self.setWindowIcon(QIcon(str(n.ROOT / 'assets' / 'netdesk.svg')))
        self.resize(1260, 840)
        self.setMinimumSize(1020, 720)
        self.jobs, self.vault, self.results = [], {}, {}
        self.events = queue.Queue()
        self.worker = None
        self.stop = threading.Event()
        self.last_run = None
        self.dirty = False
        self.busy = False
        self.serial = QSerialPort(self)
        self.serial.readyRead.connect(self.read_serial)
        self.serial.errorOccurred.connect(self.serial_error)
        self.serial_log = None
        self.serial_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 18, 24, 18)
        head = QHBoxLayout()
        title = QLabel('NetDesk')
        title.setObjectName('brand')
        head.addWidget(title)
        head.addWidget(hint('Network collection & consoles'), 1)
        head.addWidget(button('Open CLI', lambda: self.terminal([str(n.ROOT / 'netdesk')])))
        root.addLayout(head)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.collection_tab()
        self.serial_tab()
        self.history_tab()
        self.ssh_tab()
        self.setCentralWidget(central)
        self.statusBar().showMessage('Ready • Credentials stay in memory • Existing CLI commands are available')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.refresh_table()
        self.refresh_ports()
        self.refresh_history()

    def collection_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        row = QHBoxLayout()
        label = QLabel('Collect across devices and scenarios')
        label.setObjectName('section')
        row.addWidget(label, 1)
        self.count = hint('No devices yet')
        self.count.setMinimumWidth(230)
        row.addWidget(self.count)
        layout.addLayout(row)
        quick = QHBoxLayout()
        self.ranges_btn = button('＋ Add ranges && logins', self.add_ranges, True)
        quick.addWidget(self.ranges_btn)
        quick.addWidget(hint('Each range or single IP can have its own username and password.'), 1)
        layout.addLayout(quick)
        toolbar = QHBoxLayout()
        self.edit_buttons = [self.ranges_btn]
        for text, callback, primary in [('＋ Add scenario', self.add_scenario, False), ('Import inventory', self.import_inventory, False),
                                        ('Assign login', self.assign_login, False), ('Edit commands', self.edit_commands, False),
                                        ('Remove selected', self.remove_selected, False), ('Save plan', self.save_plan, False), ('Load plan', self.load_plan, False)]:
            control = button(text, callback, primary)
            self.edit_buttons.append(control)
            toolbar.addWidget(control)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Scenario', 'Device / IP', 'Platform', 'Commands', 'Login group', 'Login ready', 'Status'])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self.assign_login() if not self.busy else None)
        layout.addWidget(self.table, 1)
        self.empty = hint('Start with “Add ranges & logins”. Enter each IP range and its own username/password, then collect all rows together.')
        layout.addWidget(self.empty)
        settings = QHBoxLayout()
        self.workers = spin(1, 16, 4)
        self.timeout = spin(1, 86400, 1200)
        self.diag_timeout = spin(1, 86400, 7200)
        for label, control in [('Parallel sessions', self.workers), ('Command timeout (s)', self.timeout), ('Diagnostics timeout (s)', self.diag_timeout)]:
            settings.addWidget(QLabel(label))
            settings.addWidget(control)
        self.strict = QCheckBox('Verify known SSH host keys')
        settings.addWidget(self.strict)
        settings.addStretch()
        layout.addLayout(settings)
        actions = QHBoxLayout()
        self.start_btn = button('Collect all devices', self.start_collection, True)
        self.stop_btn = button('Stop after current operations', self.stop_collection)
        self.stop_btn.setEnabled(False)
        self.retry_btn = button('Retry failed jobs', self.retry_failed)
        self.retry_btn.setEnabled(False)
        self.open_run_btn = button('Open output folder', self.open_last_run)
        self.open_run_btn.setEnabled(False)
        for b in (self.start_btn, self.stop_btn, self.retry_btn, self.open_run_btn):
            actions.addWidget(b)
        actions.addStretch()
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(2000)
        self.activity.setMaximumHeight(125)
        self.activity.setPlaceholderText('Collection progress appears here. You can keep using the app while it runs.')
        layout.addWidget(self.activity)
        self.tabs.addTab(page, 'Collections')

    def selected_indices(self):
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})

    def refresh_table(self):
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            device = job['device']
            ready = job['credential_group'] in self.vault
            result = self.results.get(job['id'], {})
            values = [job['scenario'], f"{device['name']}\n{device['ip']}:{device['port']}" if device['name'] != device['ip'] else f"{device['ip']}:{device['port']}",
                      device['device_type'], f"{job['preset']} · {len(job['commands'])} " + ("command" if len(job['commands']) == 1 else "commands"), job['credential_group'],
                      'Ready' if ready else 'Needs login', result.get('status', 'Planned')]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip('\n'.join(job['commands']) if column == 3 else result.get('error', value))
                if column == 5:
                    item.setForeground(QColor('#18765c' if ready else '#b45b0b'))
                if column == 6 and result.get('status'):
                    item.setForeground(QColor('#18765c' if result['status'] == 'ok' else '#a34b1a'))
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 48)
        self.count.setText(f"{len(self.jobs)} device jobs · {len(set(j['scenario'] for j in self.jobs))} scenarios")
        self.empty.setVisible(not self.jobs)
        self.start_btn.setEnabled(bool(self.jobs) and not self.busy)

    def accept_scenario(self, dialog):
        if len(self.jobs) + len(dialog.jobs) > 1024:
            raise ValueError('Maximum 1,024 device jobs per collection plan.')
        batch.validate_jobs(self.jobs + dialog.jobs)
        group, credentials = dialog.login_value
        if credentials:
            self.vault[group] = credentials
        self.jobs.extend(dialog.jobs)
        self.dirty = True
        self.refresh_table()

    def add_ranges(self):
        dialog = RangesDialog(self.jobs, self.vault, self)
        if dialog.exec():
            self.jobs.extend(dialog.jobs)
            self.vault.update(dialog.credentials)
            self.dirty = True
            self.refresh_table()
            self.statusBar().showMessage(f'Added {len(dialog.jobs)} device jobs with {len(dialog.credentials)} login groups. Review and click Collect all devices.')

    def add_scenario(self):
        dialog = ScenarioDialog(self.vault, parent=self)
        if dialog.exec():
            try:
                self.accept_scenario(dialog)
            except ValueError as error:
                message(self, error)

    def import_inventory(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import a device inventory', str(n.GROUPS), 'CSV files (*.csv)')
        if not path:
            return
        try:
            devices = n.load_inventory(path)
            dialog = ScenarioDialog(self.vault, devices, self)
            dialog.name.setText(Path(path).stem)
            if dialog.exec():
                self.accept_scenario(dialog)
        except (ValueError, KeyError, OSError) as error:
            message(self, error)

    def assign_login(self):
        selected = self.selected_indices()
        if not selected:
            message(self, 'Select one or more device rows first. Use Ctrl or Shift to select several.')
            return
        default = self.jobs[selected[0]]['credential_group']
        if any(j['credential_group'] == default and i not in selected for i, j in enumerate(self.jobs)):
            default = (self.jobs[selected[0]]['scenario'] + ' / ' + self.jobs[selected[0]]['device']['ip']) if len(selected) == 1 else f'Selected devices {len(self.vault) + 1}'
        dialog = LoginDialog(self.vault, default, self)
        if dialog.exec():
            group, credentials = dialog.value
            self.vault[group] = credentials
            for row in selected:
                self.jobs[row]['credential_group'] = group
            self.dirty = True
            self.refresh_table()

    def edit_commands(self):
        selected = self.selected_indices()
        if not selected:
            message(self, 'Select the device rows whose commands you want to change.')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Commands for selected devices')
        dialog.resize(650, 410)
        layout = QVBoxLayout(dialog)
        layout.addWidget(hint('One command per line. These replace the command list for every selected device. Commands run in the login CLI context.'))
        editor = QPlainTextEdit('\n'.join(self.jobs[selected[0]]['commands']))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            commands = [x.strip() for x in editor.toPlainText().splitlines() if x.strip() and not x.lstrip().startswith('#')]
            if not commands:
                message(self, 'Enter at least one command. Nothing was changed.')
                return
            for row in selected:
                self.jobs[row]['commands'] = commands[:]
                self.jobs[row]['preset'] = 'custom'
            self.dirty = True
            self.refresh_table()

    def remove_selected(self):
        for row in reversed(self.selected_indices()):
            del self.jobs[row]
        self.dirty = True
        self.refresh_table()

    def save_plan(self):
        if not self.jobs:
            message(self, 'Add a scenario first.')
            return
        folder = n.ROOT / 'plans'
        folder.mkdir(exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, 'Save plan — credentials excluded', str(folder / 'collection.json'), 'NetDesk plan (*.json)')
        if path:
            try:
                batch.save_plan(path, self.jobs)
                self.dirty = False
                self.statusBar().showMessage(f'Saved plan: {path} — no usernames or passwords included')
            except (ValueError, OSError) as error:
                message(self, error)

    def replace_plan(self, jobs):
        if self.jobs and self.dirty and QMessageBox.question(self, 'Replace current plan?', 'Replace the current unsaved plan? Save it first if you need it later.') != QMessageBox.StandardButton.Yes:
            return False
        self.jobs = batch.validate_jobs(jobs)
        self.results.clear()
        self.dirty = False
        self.refresh_table()
        return True

    def load_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Load a saved plan', str(n.ROOT / 'plans'), 'NetDesk plan (*.json)')
        if path:
            try:
                self.replace_plan(batch.load_plan(path))
            except (ValueError, KeyError, OSError, TypeError) as error:
                message(self, error)

    def start_collection(self, jobs=None):
        if self.busy:
            return
        jobs = jobs if jobs is not None else self.jobs
        try:
            jobs = batch.validate_jobs(jobs)
            batch.validate_credentials(jobs, self.vault)
        except (ValueError, KeyError) as error:
            message(self, error)
            return
        preview = '\n\n'.join(f"{j['scenario']} — {j['device']['ip']}:{j['device']['port']} — login group {j['credential_group']}\n" + '\n'.join(j['commands']) for j in jobs)
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Review collection')
        confirm.setText(f"Collect {len(jobs)} device jobs across {len(set(j['scenario'] for j in jobs))} scenarios?")
        confirm.setInformativeText(f'Up to {self.workers.value()} devices at once. Credentials are not saved. Expand Details to review every target and command.')
        confirm.setDetailedText(preview)
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        self.launch_collection(jobs)

    def launch_collection(self, jobs):
        """Called only after the UI has validated and reviewed the collection."""
        self.busy = True
        self.stop = threading.Event()
        self.results = {k: v for k, v in self.results.items() if k not in {j['id'] for j in jobs}}
        self.active_ids = {j['id'] for j in jobs}
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.activity.clear()
        self.activity.appendPlainText('Starting collection. Same-device scenarios run sequentially; different devices run in parallel.')
        self.stop_btn.setEnabled(True)
        self.retry_btn.setEnabled(False)
        for control in self.edit_buttons + [self.workers, self.timeout, self.diag_timeout, self.strict]:
            control.setEnabled(False)
        vault = {group: self.vault[group] for group in {j['credential_group'] for j in jobs}}
        options = {'workers': self.workers.value(), 'timeout': self.timeout.value(), 'diagnostic_timeout': self.diag_timeout.value(), 'strict': self.strict.isChecked()}
        self.refresh_table()
        def work():
            try:
                path, data = batch.run_batch(jobs, vault, stop=self.stop, on_event=self.events.put, **options)
                self.events.put({'type': 'finished', 'path': str(path), 'summary': data})
            except Exception as error:
                self.events.put({'type': 'fatal', 'error': f'Collection could not finish ({type(error).__name__}). Check disk access and saved partial results.'})
            finally:
                vault.clear()
        self.worker = threading.Thread(target=work, name='netdesk-collection', daemon=False)
        self.worker.start()

    def stop_collection(self):
        self.stop.set()
        self.stop_btn.setEnabled(False)
        self.activity.appendPlainText('Stop requested. Queued jobs will not connect; active operations must finish or time out.')

    def poll(self):
        changed = False
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event['type']
            if kind == 'run':
                self.last_run = Path(event['path'])
                self.open_run_btn.setEnabled(True)
                self.activity.appendPlainText('Output: ' + event['path'])
            elif kind == 'progress':
                self.results[event['id']] = {'status': event['status']}
                if event['detail']:
                    job = next(j for j in self.jobs if j['id'] == event['id'])
                    self.activity.appendPlainText(f"{job['scenario']} / {job['device']['ip']}: {event['detail']}")
                changed = True
            elif kind == 'result':
                result = event['result']
                self.results[result['job_id']] = result
                self.progress.setValue(event['done'])
                self.activity.appendPlainText(f"{result['scenario']} / {result['device']['ip']}: {result['status']}" + (f" — {result['error']}" if result.get('error') else ''))
                changed = True
            elif kind in ('finished', 'fatal'):
                self.busy = False
                self.stop_btn.setEnabled(False)
                for control in self.edit_buttons + [self.workers, self.timeout, self.diag_timeout, self.strict]:
                    control.setEnabled(True)
                self.retry_btn.setEnabled(any(self.results.get(i, {}).get('status') != 'ok' for i in self.active_ids))
                if kind == 'finished':
                    data = event['summary']
                    ok = sum(r['status'] == 'ok' for r in data['results'])
                    self.activity.appendPlainText(f"Collection {data['status']}: {ok}/{len(data['results'])} successful. Logs are saved.")
                else:
                    self.activity.appendPlainText(event['error'])
                self.refresh_history()
                changed = True
        if changed:
            self.refresh_table()

    def retry_failed(self):
        jobs = [j for j in self.jobs if j['id'] in getattr(self, 'active_ids', set()) and self.results.get(j['id'], {}).get('status') != 'ok']
        if jobs:
            self.start_collection(jobs)

    def open_path(self, path):
        path = Path(path)
        if not path.exists():
            message(self, f'No saved files at {path} yet.')
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_last_run(self):
        if self.last_run:
            self.open_path(self.last_run)

    def terminal(self, args):
        if not shutil.which('gnome-terminal'):
            message(self, 'gnome-terminal is required for the original console tools.')
            return
        try:
            subprocess.Popen(['gnome-terminal', '--', *args], start_new_session=True)
        except OSError as error:
            message(self, error)

    def serial_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        title = QLabel('Serial console & recording')
        title.setObjectName('section')
        layout.addWidget(title)
        layout.addWidget(hint('Use the built-in text console below, or open your original serialportsconnect wizard for a full terminal session.'))
        row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(300)
        self.baud = combo(['9600', '115200', '57600', '38400', '19200'])
        self.baud.setEditable(True)
        self.serial_record = QCheckBox('Record device output')
        self.serial_record.setChecked(True)
        self.connect_btn = button('Connect', self.toggle_serial, True)
        self.refresh_port_btn = button('Refresh ports', self.refresh_ports)
        for item in (QLabel('Port'), self.port, QLabel('Baud'), self.baud, self.serial_record, self.refresh_port_btn, self.connect_btn):
            row.addWidget(item)
        layout.addLayout(row)
        self.serial_state = hint('Disconnected • 8 data bits, no parity, 1 stop bit, no flow control')
        layout.addWidget(self.serial_state)
        self.terminal_output = QPlainTextEdit()
        self.terminal_output.setObjectName('terminal')
        self.terminal_output.setReadOnly(True)
        self.terminal_output.setMaximumBlockCount(15000)
        self.terminal_output.setPlaceholderText('Serial device output appears here. Connect, then press Send / Enter to wake the device prompt.')
        layout.addWidget(self.terminal_output, 1)
        row = QHBoxLayout()
        self.serial_input = QLineEdit()
        self.serial_input.setPlaceholderText('Type a command or login response; Enter sends it to the device')
        self.serial_input.returnPressed.connect(self.send_serial)
        self.hide_input = QCheckBox('Hide typing (password)')
        self.hide_input.toggled.connect(lambda checked: self.serial_input.setEchoMode(QLineEdit.EchoMode.Password if checked else QLineEdit.EchoMode.Normal))
        row.addWidget(self.serial_input, 1)
        row.addWidget(self.hide_input)
        row.addWidget(button('Send ↵', self.send_serial))
        row.addWidget(button('Ctrl+C', lambda: self.write_serial(b'\x03')))
        row.addWidget(button('Space / more', lambda: self.write_serial(b' ')))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(button('Original serialportsconnect wizard', lambda: self.terminal([str(n.ROOT / 'netdesk'), 'serialportsconnect'])))
        row.addWidget(button('Original serialports menu', lambda: self.terminal([str(n.ROOT / 'netdesk'), 'serial'])))
        row.addWidget(button('Open serial logs', lambda: self.open_path(n.ROOT / 'logs' / 'serial')))
        row.addWidget(button('Open original logs', lambda: self.open_path(Path.home() / 'serial-logs')))
        layout.addLayout(row)
        layout.addWidget(hint('Recording saves raw received bytes, including anything the device echoes. Hidden typing only hides the local input field. Use the original wizard for full-screen terminal applications.'))
        self.tabs.addTab(page, 'Serial ports && logs')

    def refresh_ports(self):
        current = self.port.currentText()
        self.port.clear()
        for port in n.detected_ports():
            self.port.addItem(port.device, port.description)
        if self.port.count() == 0:
            self.port.setEditText(current if current else '')
            self.port.lineEdit().setPlaceholderText('No USB adapter — enter /dev path')
        elif current:
            self.port.setCurrentText(current)

    def toggle_serial(self):
        if self.serial.isOpen():
            self.disconnect_serial()
            return
        try:
            port = self.port.currentText().strip()
            baud = int(self.baud.currentText())
            if not port or not Path(port).exists():
                raise ValueError('Choose a connected serial port or enter its /dev path.')
            if not 300 <= baud <= 4000000:
                raise ValueError('Baud rate must be between 300 and 4000000.')
            self.serial.setPortName(port)
            self.serial.setBaudRate(baud)
            self.serial.setDataBits(QSerialPort.DataBits.Data8)
            self.serial.setParity(QSerialPort.Parity.NoParity)
            self.serial.setStopBits(QSerialPort.StopBits.OneStop)
            self.serial.setFlowControl(QSerialPort.FlowControl.NoFlowControl)
            if not self.serial.open(QIODevice.OpenModeFlag.ReadWrite):
                raise ValueError(self.serial.errorString())
            self.serial_decoder.reset()
            logfile = None
            if self.serial_record.isChecked():
                folder = n.ROOT / 'logs' / 'serial'
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                logfile = folder / f'{datetime.now():%Y%m%d-%H%M%S-%f}-{n.slug(Path(port).name)}-gui.log'
                self.serial_log = logfile.open('xb')
            self.serial_state.setText(f'Connected: {port} at {baud} baud' + (f' • Log: {logfile}' if logfile else ' • Recording off'))
            self.connect_btn.setText('Disconnect')
            for control in (self.port, self.baud, self.serial_record, self.refresh_port_btn):
                control.setEnabled(False)
            self.serial_input.setFocus()
        except (ValueError, OSError) as error:
            self.disconnect_serial()
            message(self, error)

    def disconnect_serial(self):
        if self.serial.isOpen():
            self.read_serial()
            self.serial.close()
        if self.serial_log:
            self.serial_log.close()
            self.serial_log = None
        self.connect_btn.setText('Connect')
        self.serial_state.setText('Disconnected • Any recorded output has been saved')
        for control in (self.port, self.baud, self.serial_record, self.refresh_port_btn):
            control.setEnabled(True)

    def read_serial(self):
        data = bytes(self.serial.readAll())
        if not data:
            return
        if self.serial_log:
            try:
                self.serial_log.write(data)
                self.serial_log.flush()
            except OSError as error:
                self.serial_log.close()
                self.serial_log = None
                self.serial_state.setText(f'Recording stopped: {error}')
                message(self, 'Could not save serial output. Recording has stopped; check free disk space.')
        text = self.serial_decoder.decode(data)
        text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text).replace('\r', '')
        self.terminal_output.moveCursor(QTextCursor.MoveOperation.End)
        self.terminal_output.insertPlainText(text)
        self.terminal_output.ensureCursorVisible()

    def write_serial(self, data):
        if not self.serial.isOpen():
            message(self, 'Connect a serial device first.')
            return
        if self.serial.write(data) == -1:
            message(self, self.serial.errorString())

    def send_serial(self):
        if self.serial.isOpen():
            self.write_serial((self.serial_input.text() + '\r').encode('utf-8'))
            self.serial_input.clear()
        else:
            message(self, 'Connect a serial device first.')

    def serial_error(self, error):
        if error == QSerialPort.SerialPortError.ResourceError and self.serial.isOpen():
            detail = self.serial.errorString()
            self.disconnect_serial()
            self.serial_state.setText('Connection lost: ' + detail)

    def history_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        row = QHBoxLayout()
        title = QLabel('Saved runs & configuration changes')
        title.setObjectName('section')
        row.addWidget(title, 1)
        row.addWidget(button('Refresh', self.refresh_history))
        row.addWidget(button('All log folders', lambda: self.open_path(n.ROOT / 'logs')))
        layout.addLayout(row)
        layout.addWidget(hint('Select a run to inspect its files. Select two runs with Ctrl to compare configurations. Load failed jobs back into Collections to retry.'))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.run_tree = QTreeWidget()
        self.run_tree.setHeaderLabels(['Run', 'Success', 'Status'])
        self.run_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.run_tree.itemSelectionChanged.connect(self.show_run)
        split.addWidget(self.run_tree)
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(['Files'])
        self.file_tree.itemClicked.connect(self.show_file)
        split.addWidget(self.file_tree)
        split.setSizes([500, 650])
        layout.addWidget(split, 1)
        self.history_empty = hint('No saved collections yet. Complete a collection to see its results, files, and configuration comparisons here.')
        layout.addWidget(self.history_empty)
        row = QHBoxLayout()
        row.addWidget(button('Open run folder', self.open_selected_run))
        row.addWidget(button('Compare two selected runs', self.compare_selected))
        self.history_retry = button('Load failed jobs into Collections', self.load_failed)
        row.addWidget(self.history_retry)
        row.addStretch()
        layout.addLayout(row)
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setMaximumHeight(260)
        self.viewer.setPlaceholderText('Select a text file to preview it here. Large logs show the first 256 KB.')
        layout.addWidget(self.viewer)
        self.tabs.addTab(page, 'History && compare')

    def refresh_history(self):
        if not hasattr(self, 'run_tree'):
            return
        self.run_tree.clear()
        for path in n.history():
            try:
                data = n.read_summary(path)
                ok = sum(r['status'] == 'ok' for r in data['results'])
                item = QTreeWidgetItem([path.parent.name, f"{ok}/{len(data['devices'])}", data['status']])
                item.setData(0, Qt.ItemDataRole.UserRole, str(path.parent))
                self.run_tree.addTopLevelItem(item)
            except (ValueError, KeyError, OSError):
                continue
        self.run_tree.resizeColumnToContents(0)
        self.history_empty.setVisible(self.run_tree.topLevelItemCount() == 0)

    def selected_runs(self):
        return [Path(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.run_tree.selectedItems()]

    def show_run(self):
        self.file_tree.clear()
        paths = self.selected_runs()
        if len(paths) != 1:
            return
        for path in sorted(paths[0].iterdir()):
            item = QTreeWidgetItem([path.name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.file_tree.addTopLevelItem(item)
            if path.is_dir():
                for child in sorted(path.iterdir()):
                    if child.is_file():
                        entry = QTreeWidgetItem([child.name])
                        entry.setData(0, Qt.ItemDataRole.UserRole, str(child))
                        item.addChild(entry)

    def show_file(self, item, _column=0):
        path = Path(item.data(0, Qt.ItemDataRole.UserRole))
        if path.is_file():
            try:
                with path.open('rb') as stream:
                    data = stream.read(262145)
                self.viewer.setPlainText(str(path) + '\n\n' + data[:262144].decode('utf-8', errors='replace') + ('\n\n[Preview truncated. Open the run folder to read the full file.]' if len(data) > 262144 else ''))
            except OSError as error:
                message(self, error)

    def open_selected_run(self):
        paths = self.selected_runs()
        if len(paths) == 1:
            self.open_path(paths[0])
        else:
            message(self, 'Select one run first.')

    def compare_selected(self):
        paths = sorted(self.selected_runs())
        if len(paths) != 2:
            message(self, 'Select two runs with Ctrl, then compare.')
            return
        try:
            self.viewer.setPlainText(n.compare_runs(*paths))
        except (ValueError, KeyError, OSError) as error:
            message(self, error)

    def load_failed(self):
        if self.busy:
            message(self, 'Wait for the current collection to finish before replacing its plan.')
            return
        paths = self.selected_runs()
        if len(paths) != 1:
            message(self, 'Select one run first.')
            return
        try:
            data = n.read_summary(paths[0])
            if data.get('jobs'):
                ok = {r.get('job_id') for r in data['results'] if r['status'] == 'ok'}
                jobs = [j for j in data['jobs'] if j['id'] not in ok]
            else:
                ok = {(r['device']['ip'], r['device']['port']) for r in data['results'] if r['status'] == 'ok'}
                devices = [d for d in data['devices'] if (d['ip'], d['port']) not in ok]
                jobs = batch.make_jobs('Retry ' + paths[0].name, '', preset=data['preset'], commands=data['custom_commands'], devices=devices) if devices else []
            if not jobs:
                message(self, 'All jobs in this run succeeded. Nothing to retry.')
                return
            if self.replace_plan(jobs):
                self.strict.setChecked(data.get('strict_host_keys', False))
                self.timeout.setValue(data.get('timeout', 1200))
                self.diag_timeout.setValue(data.get('diagnostic_timeout', 7200))
                self.tabs.setCurrentIndex(0)
                self.statusBar().showMessage('Failed jobs loaded. Assign missing logins, then collect. Every command for these jobs will run again.')
        except (ValueError, KeyError, OSError) as error:
            message(self, error)

    def ssh_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 18, 0, 0)
        title = QLabel('Interactive SSH & tools')
        title.setObjectName('section')
        layout.addWidget(title)
        layout.addWidget(hint('Open a full terminal for interactive device configuration. The system SSH client asks for your password.'))
        group = QGroupBox('SSH connection')
        form = QFormLayout(group)
        self.ssh_host = QLineEdit()
        self.ssh_host.setPlaceholderText('192.168.1.1')
        self.ssh_user = QLineEdit()
        self.ssh_port = spin(1, 65535, 22)
        form.addRow('Device IP', self.ssh_host)
        form.addRow('Username', self.ssh_user)
        form.addRow('Port', self.ssh_port)
        form.addRow(button('Open SSH terminal', self.open_ssh, True))
        layout.addWidget(group)
        tools = QGroupBox('Existing tools && help')
        row = QVBoxLayout(tools)
        row.addWidget(button('Open NetDesk CLI menu', lambda: self.terminal([str(n.ROOT / 'netdesk')])))
        row.addWidget(button('Open serialportsconnect logging wizard', lambda: self.terminal([str(n.ROOT / 'netdesk'), 'serialportsconnect'])))
        row.addWidget(button('Read the usage guide', lambda: self.open_path(n.ROOT / 'README.md')))
        row.addWidget(button('Open project folder', lambda: self.open_path(n.ROOT)))
        row.addWidget(button('Forget all session credentials', self.forget_credentials))
        layout.addWidget(tools)
        layout.addWidget(hint('SSH collection defaults to accepting unknown host keys; enable verification on Collections to require known_hosts entries. Live device compatibility depends on platform and command privileges.'))
        layout.addStretch()
        self.tabs.addTab(page, 'SSH && tools')

    def open_ssh(self):
        try:
            host = str(ipaddress.ip_address(self.ssh_host.text().strip()))
            user = self.ssh_user.text().strip()
            if not user or any(c.isspace() for c in user):
                raise ValueError('Enter a valid SSH username.')
            self.terminal(['ssh', '-p', str(self.ssh_port.value()), '-l', user, host])
        except ValueError as error:
            message(self, error)

    def forget_credentials(self):
        if self.busy:
            message(self, 'Wait for the current collection to finish before clearing its credentials.')
            return
        self.vault.clear()
        self.refresh_table()
        self.statusBar().showMessage('Session credentials cleared. Assign logins again before collecting.')

    def closeEvent(self, event):
        if self.busy:
            if QMessageBox.question(self, 'Collection is running', 'Stop queued jobs? Active operations must finish or time out before you can close the app.') == QMessageBox.StandardButton.Yes:
                self.stop_collection()
            event.ignore()
            return
        if self.dirty and self.jobs:
            answer = QMessageBox.question(self, 'Unsaved collection plan', 'Close without saving the plan? Login credentials are never saved.')
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.disconnect_serial()
        self.vault.clear()
        event.accept()


def main():
    os.umask(0o077)
    app = QApplication.instance() or QApplication(['netdesk-gui'])
    app.setApplicationName('NetDesk')
    app.setOrganizationName('NetDesk')
    app.setDesktopFileName('netdesk')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
