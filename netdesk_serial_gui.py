"""Multi-port serial collection window. GUI updates stay on the Qt thread."""
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QHeaderView, QLineEdit,
    QComboBox, QCheckBox, QLabel, QPlainTextEdit, QDialogButtonBox,
    QMessageBox, QProgressBar, QAbstractItemView,
)
import netdesk as n
import netdesk_serial as engine
from netdesk_gui import button, hint, combo, spin, message


class SerialBatchDialog(QDialog):
    def __init__(self, reserved_ports=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Collect multiple serial ports')
        self.resize(1240, 700)
        self.reserved_ports = list(reserved_ports)
        self.entries = []
        self.busy = False
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.worker = None
        self.last_run = None
        layout = QVBoxLayout(self)
        layout.addWidget(hint('One row per serial cable. Each device can have its own baud rate, login, platform and command list. Ports collect in parallel; logs are saved separately.'))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['Serial port', 'Baud', 'Platform', 'Preset', 'Username', 'Password', 'Commands', 'Progress'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(45)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_btn = button('＋ Add port', self.add_row)
        self.remove_btn = button('Remove selected rows', self.remove_rows)
        self.refresh_btn = button('Refresh available ports', self.refresh_ports)
        self.shared = QCheckBox('Use first row’s login for every port')
        self.shared.toggled.connect(self.update_shared)
        for control in (self.add_btn, self.remove_btn, self.refresh_btn, self.shared):
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(hint('Leave login blank only for an already authenticated console or a device without authentication. Password-only consoles may leave username blank. Separate enable passwords, MFA and first-login setup are not automated.'))
        row = QHBoxLayout()
        self.login_timeout = spin(1, 300, 30)
        self.timeout = spin(1, 86400, 1200)
        self.diag_timeout = spin(1, 86400, 7200)
        for label, control in [('Login timeout (s)', self.login_timeout), ('Command timeout (s)', self.timeout), ('Diagnostics timeout (s)', self.diag_timeout)]:
            row.addWidget(QLabel(label))
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        row = QHBoxLayout()
        self.start_btn = button('Collect all serial ports', self.start, True)
        self.stop_btn = button('Stop collection', self.cancel_run)
        self.stop_btn.setEnabled(False)
        self.open_btn = button('Open this run’s logs', self.open_output)
        self.open_btn.setEnabled(False)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.open_btn)
        row.addWidget(button('All serial collection logs', self.open_all))
        row.addStretch()
        self.close_btn = button('Close', self.close)
        row.addWidget(self.close_btn)
        layout.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMaximumBlockCount(2000)
        self.activity.setMaximumHeight(180)
        self.activity.setPlaceholderText('Review commands before starting. Login progress and command outcomes appear here.')
        layout.addWidget(self.activity)
        self.controls = [self.add_btn, self.remove_btn, self.refresh_btn, self.shared, self.login_timeout, self.timeout, self.diag_timeout, self.table]
        ports = n.detected_ports()
        for port in ports[:16]:
            self.add_row(port.device)
        if not ports:
            self.add_row()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def add_row(self, path=''):
        if len(self.entries) >= 16:
            message(self, 'At most 16 serial ports can collect at once.')
            return
        entry = {}
        port = combo([p.device for p in n.detected_ports()])
        port.setEditable(True)
        port.setCurrentText(path)
        port.lineEdit().setPlaceholderText('/dev/ttyUSB0')
        entry.update(port=port, baud=spin(300, 4000000, 9600), platform=combo(list(n.PRESETS)),
                     preset=combo(n.available_presets(['huawei']) + ['custom']),
                     username=QLineEdit(), password=QLineEdit(), custom=None, status=QLabel('Planned'))
        entry['password'].setEchoMode(QLineEdit.EchoMode.Password)
        entry['command_button'] = button('View / edit', lambda: self.edit_commands(entry))
        def update_presets():
            preset = entry['preset']
            previous = preset.currentText()
            preset.clear()
            preset.addItems(n.available_presets([entry['platform'].currentText()]) + ['custom'])
            preset.setCurrentText(previous if preset.findText(previous) >= 0 else 'quick')
        entry['platform'].currentTextChanged.connect(update_presets)
        self.entries.append(entry)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, key in enumerate(('port','baud','platform','preset','username','password','command_button','status')):
            self.table.setCellWidget(row, col, entry[key])
        self.update_shared()

    def refresh_ports(self):
        available = [p.device for p in n.detected_ports()]
        for entry in self.entries:
            current = entry['port'].currentText()
            entry['port'].clear()
            entry['port'].addItems(available)
            entry['port'].setCurrentText(current)

    def remove_rows(self):
        for row in sorted({i.row() for i in self.table.selectionModel().selectedRows()}, reverse=True):
            self.table.removeRow(row)
            del self.entries[row]
        self.update_shared()

    def update_shared(self):
        for index, entry in enumerate(self.entries):
            for key in ('username', 'password'):
                entry[key].setEnabled(not self.shared.isChecked() or index == 0)

    def commands(self, entry):
        if entry['preset'].currentText() == 'custom':
            if not entry['custom']:
                raise ValueError('Choose View / edit and enter the custom commands for each custom row.')
            return entry['custom'][:]
        return n.get_commands(entry['platform'].currentText(), entry['preset'].currentText())

    def edit_commands(self, entry):
        dialog = QDialog(self)
        dialog.setWindowTitle('Serial command list')
        dialog.resize(660, 410)
        layout = QVBoxLayout(dialog)
        layout.addWidget(hint('Review the preset or edit it. Saving changes selects custom for this port. One command per line; interactive confirmations are not automated.'))
        commands = entry['custom'] or [] if entry['preset'].currentText() == 'custom' else self.commands(entry)
        editor = QPlainTextEdit('\n'.join(commands))
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            custom = [c.strip() for c in editor.toPlainText().splitlines() if c.strip() and not c.lstrip().startswith('#')]
            if not custom:
                message(self, 'Enter at least one command.')
                return
            entry['custom'] = custom
            entry['preset'].setCurrentText('custom')

    def build(self):
        jobs, credentials = [], {}
        for index, entry in enumerate(self.entries):
            port = entry['port'].currentText().strip()
            if not port:
                raise ValueError(f'Row {index + 1}: choose a serial port or remove the row.')
            jobs.append({'port': port, 'baud': entry['baud'].value(), 'platform': entry['platform'].currentText(),
                         'preset': entry['preset'].currentText(), 'commands': self.commands(entry)})
            login = self.entries[0] if self.shared.isChecked() else entry
            credentials[port] = (login['username'].text().strip(), login['password'].text())
        return engine.validate_jobs(jobs, self.reserved_ports), credentials

    def start(self):
        if self.busy:
            return
        try:
            jobs, credentials = self.build()
        except (ValueError, KeyError) as error:
            message(self, error)
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle('Review serial collection')
        confirm.setText(f'Log in and collect {len(jobs)} serial devices in parallel?')
        confirm.setInformativeText('Each port gets its own logs. Open Details to review every port and command. Stop preserves partial output but does not abort a command already running on the device.')
        confirm.setDetailedText('\n\n'.join(f"{j['port']} @ {j['baud']} — {j['platform']}\n" + '\n'.join(j['commands']) for j in jobs))
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() == QMessageBox.StandardButton.Yes:
            self.launch(jobs, credentials)

    def launch(self, jobs, credentials):
        self.busy = True
        self.stop = threading.Event()
        for control in self.controls:
            control.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.activity.clear()
        for entry in self.entries:
            entry['status'].setText('Queued')
        options = dict(login_timeout=self.login_timeout.value(), timeout=self.timeout.value(), diagnostic_timeout=self.diag_timeout.value())
        def work():
            try:
                path, summary = engine.run_serial_batch(jobs, credentials, stop=self.stop, on_event=self.events.put,
                                                        reserved_ports=self.reserved_ports, **options)
                self.events.put({'type': 'finished', 'summary': summary})
            except Exception as error:
                self.events.put({'type': 'fatal', 'error': f'Collection could not finish: {type(error).__name__}. Check port access and output folder.'})
            finally:
                credentials.clear()
        self.worker = threading.Thread(target=work, name='netdesk-serial-collection', daemon=False)
        self.worker.start()

    def poll(self):
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event['type'] == 'run':
                self.last_run = Path(event['path'])
                self.open_btn.setEnabled(True)
                self.activity.appendPlainText('Logs: ' + event['path'])
            elif event['type'] in ('progress', 'result'):
                result = event.get('result', event)
                port, status = result['port'], result['status']
                for entry in self.entries:
                    if entry['port'].currentText() == port:
                        entry['status'].setText(status)
                        entry['status'].setToolTip(status)
                self.activity.appendPlainText(f'{port}: {status}' + (' — ' + result['error'] if result.get('error') else ''))
                if event['type'] == 'result':
                    self.progress.setValue(event['done'])
            elif event['type'] in ('finished', 'fatal'):
                self.busy = False
                for control in self.controls:
                    control.setEnabled(True)
                self.update_shared()
                self.start_btn.setEnabled(True)
                self.stop_btn.setEnabled(False)
                if event['type'] == 'finished':
                    data = event['summary']
                    ok = sum(r['status'] == 'ok' for r in data['results'])
                    self.activity.appendPlainText(f"Collection {data['status']}: {ok}/{len(data['results'])} ports successful.")
                else:
                    self.activity.appendPlainText(event['error'])

    def cancel_run(self):
        self.stop.set()
        self.stop_btn.setEnabled(False)
        self.activity.appendPlainText('Stopping reads and closing ports. Partial logs are preserved; devices may still be running their commands.')

    def open_output(self):
        if self.last_run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_run)))

    def open_all(self):
        folder = n.ROOT / 'logs' / 'serial-batches'
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            message(self, 'No serial collections have been saved yet.')

    def reject(self):
        if self.busy:
            message(self, 'Stop the collection and wait for the ports to close before leaving this window.')
            return
        self.clear_credentials()
        super().reject()

    def clear_credentials(self):
        for entry in self.entries:
            entry['username'].clear()
            entry['password'].clear()

    def closeEvent(self, event):
        if self.busy:
            if QMessageBox.question(self, 'Serial collection running', 'Stop the collection? Wait for ports to close before closing the window.') == QMessageBox.StandardButton.Yes:
                self.cancel_run()
            event.ignore()
            return
        self.clear_credentials()
        event.accept()
