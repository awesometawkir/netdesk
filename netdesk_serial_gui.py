"""Multi-port serial collection window. GUI updates stay on the Qt thread."""
import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QHeaderView, QLineEdit,
    QComboBox, QCheckBox, QLabel, QPlainTextEdit, QDialogButtonBox,
    QMessageBox, QProgressBar, QAbstractItemView, QApplication, QGridLayout, QWidget,
)
import netdesk as n
import netdesk_serial as engine
import netdesk_serial_hotplug as hotplug
from netdesk_gui import button, hint, combo, spin, message
from netdesk_command_gui import CommandEdit, CommandLibrary


class SerialBatchDialog(QDialog):
    def __init__(self, reserved_ports=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Serial collections')
        self.resize(1240, 700)
        self.reserved_ports = list(reserved_ports)
        self.entries = []
        self.excluded_ports = set()
        self.reserved_provider = None
        self._available = {}
        self.busy = False
        self.stop = threading.Event()
        self.events = queue.Queue()
        self.worker = None
        self.last_run = None
        layout = QVBoxLayout(self)
        layout.addWidget(hint('Detected serial adapters appear automatically. Select ports, enter logins, then collect. NetDesk connects, logs in and saves each device’s command output.'))
        self.discovery_status = hint('Scanning for serial adapters…')
        layout.addWidget(self.discovery_status)
        self.auto_console = None
        if hotplug.service_supported():
            auto_row = QHBoxLayout()
            self.auto_console = QCheckBox('Auto-open new USB adapters in a WezTerm console (one pane each)')
            self.auto_console.setToolTip('Runs in the background even when NetDesk is closed. A console pane pauses while NetDesk collects from its port.')
            self.auto_console.setChecked(hotplug.service_active())
            self.auto_console.toggled.connect(self.toggle_auto_console)
            auto_row.addWidget(self.auto_console)
            auto_row.addWidget(button('Console settings', self.open_console_settings))
            auto_row.addWidget(button('Console logs', self.open_console_logs))
            auto_row.addStretch()
            layout.addLayout(auto_row)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(['Use', 'Serial port', 'Remove', 'Baud', 'Platform', 'Preset', 'Username', 'Password', 'Commands', 'Progress'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate([45, 145, 88, 80, 105, 105, 110, 110, 95, 110]):
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().setDefaultSectionSize(45)
        layout.addWidget(self.table, 1)
        self.preview_label = hint('Select a port to review its commands. Choose custom to type below.')
        layout.addWidget(self.preview_label)
        self.preview = CommandEdit()
        self.preview.setMaximumHeight(80)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.preview.textChanged.connect(self.save_custom)
        self.table.currentCellChanged.connect(lambda *_: self.update_preview())
        QApplication.instance().focusChanged.connect(self.follow_focus)
        row = QHBoxLayout()
        self.add_btn = button('＋ Add port', self.add_row)
        self.select_all_btn = button('Select all', lambda: self.select_ports(True))
        self.select_none_btn = button('Select none', lambda: self.select_ports(False))
        self.refresh_btn = button('Detect / restore ports', self.restore_ports)
        self.shared = QCheckBox('Use first row’s login for every port')
        self.shared.toggled.connect(self.update_shared)
        for control in (self.add_btn, self.select_all_btn, self.select_none_btn, self.refresh_btn, self.shared):
            row.addWidget(control)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(hint('Leave login blank for an authenticated console; enter credentials when the device requires a login.'))
        advanced_button = button('Connection settings ▸', lambda: None)
        advanced_button.setCheckable(True)
        layout.addWidget(advanced_button, 0, Qt.AlignmentFlag.AlignLeft)
        advanced = QWidget()
        advanced.hide()
        advanced_button.toggled.connect(advanced.setVisible)
        advanced_button.toggled.connect(lambda checked: advanced_button.setText('Connection settings ▾' if checked else 'Connection settings ▸'))
        row = QGridLayout(advanced)
        self.login_timeout = spin(1, 300, 30)
        self.timeout = spin(1, 86400, 1200)
        self.diag_timeout = spin(1, 86400, 7200)
        for column, (label, control) in enumerate([('Login timeout (s)', self.login_timeout), ('Command timeout (s)', self.timeout), ('Diagnostics timeout (s)', self.diag_timeout)]):
            row.addWidget(QLabel(label), 0, column)
            row.addWidget(control, 1, column)
        layout.addWidget(advanced)
        row = QHBoxLayout()
        self.start_btn = button('Collect selected ports', self.start, True)
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
        self.activity.setMaximumHeight(50)
        self.activity.setPlaceholderText('Review commands before starting. Login progress and command outcomes appear here.')
        layout.addWidget(self.activity)
        self.controls = [self.add_btn, self.select_all_btn, self.select_none_btn, self.refresh_btn, self.shared, self.login_timeout, self.timeout, self.diag_timeout, self.table, self.preview]
        self.refresh_ports()
        if not self.entries:
            self.add_row()
        self.discovery_timer = QTimer(self)
        self.discovery_timer.timeout.connect(self.refresh_ports)
        self.discovery_timer.start(1000)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)

    def add_row(self, path='', select=True):
        if len(self.entries) >= 16:
            message(self, 'At most 16 serial ports can collect at once.')
            return
        entry = {}
        port = combo(list(self._available))
        port.setEditable(True)
        port.setCurrentText(path)
        port.lineEdit().setPlaceholderText('COM3 or /dev/ttyUSB0')
        entry.update(selected=QCheckBox(), port=port, baud=spin(300, 4000000, 9600), platform=combo(list(n.PRESETS)),
                     preset=combo(n.available_presets(['huawei']) + ['custom']),
                     username=QLineEdit(), password=QLineEdit(), custom=None, draft=None, status=QLabel('Planned'))
        entry['selected'].setChecked(True)
        entry['remove'] = button('Remove', lambda: self.remove_entry(entry))
        entry['password'].setEchoMode(QLineEdit.EchoMode.Password)
        entry['command_button'] = button('View / edit', lambda: self.edit_commands(entry))
        def update_presets():
            preset = entry['preset']
            previous = preset.currentText()
            preset.blockSignals(True)
            preset.clear()
            preset.addItems(n.available_presets([entry['platform'].currentText()]) + ['custom'])
            preset.setCurrentText(previous if preset.findText(previous) >= 0 else 'quick')
            preset.blockSignals(False)
            self.select_entry(entry)
        entry['platform'].currentTextChanged.connect(update_presets)
        self.entries.append(entry)
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, key in enumerate(('selected','port','remove','baud','platform','preset','username','password','command_button','status')):
            self.table.setCellWidget(row, col, entry[key])
        entry['preset'].currentTextChanged.connect(lambda *_: self.select_entry(entry))
        entry['port'].currentTextChanged.connect(lambda *_: self.update_preview() if entry in self.entries and self.table.currentRow() == self.entries.index(entry) else None)
        self.update_shared()
        if select:
            self.select_entry(entry)

    def refresh_ports(self):
        if self.busy:
            return
        try:
            devices = n.detected_ports()
        except OSError as error:
            self.discovery_status.setText(f'Port detection unavailable: {error}')
            return
        self._available = {p.device: p for p in devices}
        reserved = {engine.port_identity(p) for p in self.current_reserved()}
        # A removed adapter is hidden only while plugged in; replugging re-adds it.
        self.excluded_ports.intersection_update(engine.port_identity(p) for p in self._available)
        existing = {engine.port_identity(e['port'].currentText()) for e in self.entries if e['port'].currentText()}
        added = []
        for path, device in self._available.items():
            identity = engine.port_identity(path)
            if identity not in existing and identity not in self.excluded_ports and identity not in reserved and len(self.entries) < 16:
                # Fill a pristine placeholder; never overwrite a prepared row.
                blank = next((e for e in self.entries if not e['port'].currentText() and not e['username'].text()
                              and not e['password'].text() and e['preset'].currentText() == 'quick' and not e['custom']), None)
                if blank:
                    blank['port'].setCurrentText(path)
                else:
                    self.add_row(path, select=self.table.currentRow() < 0)
                existing.add(identity)
                added.append(path)
        available = {engine.port_identity(p) for p in self._available}
        for entry in self.entries:
            port = entry['port']
            current = port.currentText()
            # Do not reset an actively edited port field during a detection tick.
            if not port.hasFocus() and not port.lineEdit().hasFocus():
                port.blockSignals(True)
                port.clear()
                port.addItems(self._available)
                port.setCurrentText(current)
                port.blockSignals(False)
            device = self._available.get(current)
            description = getattr(device, 'description', '') if device else ''
            port.setToolTip(current + (' — ' + description if description else ''))
            identity = engine.port_identity(current)
            if entry['status'].text() in ('Planned', 'Detected', 'Not detected / manual', 'In manual console'):
                status = 'In manual console' if identity in reserved else 'Detected' if identity in available else 'Not detected / manual'
                entry['status'].setText(status)
        if added and self.isVisible():
            # Reveal additions without switching the active command editor.
            self.table.scrollToBottom()
        self.discovery_status.setText((f'Added {len(added)} new port(s) • ' if added else '') + f'{len(devices)} adapters detected • {len(self.entries)} rows • defaults: Huawei, 9600 baud (change if needed)')

    def toggle_auto_console(self, enabled):
        ok, text = hotplug.set_service(enabled)
        if not ok:
            self.auto_console.blockSignals(True)
            self.auto_console.setChecked(not enabled)
            self.auto_console.blockSignals(False)
            message(self, 'Could not change automatic consoles:\n' + text)
        else:
            self.discovery_status.setText(text)

    def open_console_settings(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(hotplug.ensure_settings_file())))

    def open_console_logs(self):
        folder = hotplug.default_log_dir()
        try:
            settings = hotplug.SettingsFile(hotplug.SETTINGS_PATH).get()
            folder = Path(settings.get('log_dir') or folder).expanduser()
        except Exception:
            pass
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def restore_ports(self):
        self.excluded_ports.clear()
        self.refresh_ports()

    def current_reserved(self):
        return self.reserved_provider() if self.reserved_provider else self.reserved_ports

    def select_ports(self, checked):
        for entry in self.entries:
            entry['selected'].setChecked(checked)

    def select_entry(self, entry):
        if entry in self.entries:
            self.table.setCurrentCell(self.entries.index(entry), 1)
            self.update_preview()

    def follow_focus(self, _old, now):
        if now is None:
            return
        for index, entry in enumerate(self.entries):
            if any(now is entry[key] or entry[key].isAncestorOf(now) for key in ('port', 'baud', 'platform', 'preset', 'username', 'password')):
                if self.table.currentRow() != index:
                    self.table.setCurrentCell(index, 1)
                return

    def update_preview(self):
        row = self.table.currentRow()
        self.preview.blockSignals(True)
        try:
            if not 0 <= row < len(self.entries):
                self.preview.clear()
                self.preview.setReadOnly(True)
                self.preview_label.setText('Select a port to review its commands.')
                return
            entry = self.entries[row]
            custom = entry['preset'].currentText() == 'custom'
            self.preview.platform = entry['platform'].currentText()
            self.preview.setReadOnly(not custom)
            text = entry['draft'] if custom and entry['draft'] is not None else '\n'.join(entry['custom'] or []) if custom else '\n'.join(self.commands(entry))
            self.preview.setPlainText(text)
            mode = 'Custom commands — type here' if custom else 'Execution preview — choose custom to edit'
            self.preview_label.setText(f"Row {row + 1} · {entry['port'].currentText() or 'Choose a port'} · {mode}")
        finally:
            self.preview.blockSignals(False)

    def save_custom(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.entries) and not self.preview.isReadOnly():
            entry = self.entries[row]
            entry['draft'] = self.preview.toPlainText()
            entry['custom'] = [line.strip() for line in entry['draft'].splitlines() if line.strip() and not line.lstrip().startswith('#')]

    def remove_entry(self, entry):
        if self.busy or entry not in self.entries:
            return
        row = self.entries.index(entry)
        path = entry['port'].currentText()
        text = f'Remove row {row + 1}: {path or "empty port"}?'
        if row == 0 and self.shared.isChecked() and len(self.entries) > 1:
            text += '\nThe next row will become the shared login.'
        if QMessageBox.question(self, 'Remove this port?', text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        if path:
            self.excluded_ports.add(engine.port_identity(path))
        self.table.blockSignals(True)
        self.entries.pop(row)
        self.table.removeRow(row)
        self.table.blockSignals(False)
        self.update_shared()
        self.update_preview()

    def update_shared(self):
        for index, entry in enumerate(self.entries):
            for key in ('username', 'password'):
                entry[key].setEnabled(not self.shared.isChecked() or index == 0)

    def commands(self, entry):
        if entry['preset'].currentText() == 'custom':
            if not entry['custom']:
                raise ValueError('Enter custom commands below the table or choose View / edit for this port.')
            return entry['custom'][:]
        return n.get_commands(entry['platform'].currentText(), entry['preset'].currentText())

    def edit_commands(self, entry):
        commands = (entry['custom'] or []) if entry['preset'].currentText() == 'custom' else self.commands(entry)
        dialog = CommandLibrary(entry['platform'].currentText(), commands, self)
        if dialog.exec():
            entry['custom'] = dialog.commands()
            entry['draft'] = '\n'.join(entry['custom'])
            entry['preset'].setCurrentText('custom')
            self.select_entry(entry)

    def build(self):
        jobs, credentials = [], {}
        for index, entry in enumerate(self.entries):
            if not entry['selected'].isChecked():
                continue
            port = entry['port'].currentText().strip()
            if not port:
                raise ValueError(f'Row {index + 1}: choose a serial port or remove the row.')
            jobs.append({'port': port, 'baud': entry['baud'].value(), 'platform': entry['platform'].currentText(),
                         'preset': entry['preset'].currentText(), 'commands': self.commands(entry)})
            login = self.entries[0] if self.shared.isChecked() else entry
            credentials[port] = (login['username'].text().strip(), login['password'].text())
        return engine.validate_jobs(jobs, self.current_reserved()), credentials

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
            entry['status'].setText('Queued' if entry['port'].currentText() in {j['port'] for j in jobs} else 'Skipped')
        reserved = self.current_reserved()
        options = dict(login_timeout=self.login_timeout.value(), timeout=self.timeout.value(), diagnostic_timeout=self.diag_timeout.value())
        def work():
            try:
                path, summary = engine.run_serial_batch(jobs, credentials, stop=self.stop, on_event=self.events.put,
                                                        reserved_ports=reserved, **options)
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

    def showEvent(self, event):
        self.discovery_timer.start(1000)
        self.timer.start(100)
        super().showEvent(event)

    def reject(self):
        if self.busy:
            message(self, 'Stop the collection and wait for the ports to close before leaving this window.')
            return
        self.clear_credentials()
        self.discovery_timer.stop()
        self.timer.stop()
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
        self.discovery_timer.stop()
        self.timer.stop()
        event.accept()
