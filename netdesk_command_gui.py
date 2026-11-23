"""Reusable command library and Huawei-style offline editor."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QPlainTextEdit, QMenu, QDialog, QVBoxLayout,
    QHBoxLayout, QLabel, QComboBox, QLineEdit, QTreeWidget, QTreeWidgetItem,
    QPushButton, QDialogButtonBox, QSplitter, QWidget)
from netdesk_presets import PRESETS, SCENARIOS
from netdesk_commands import category, command_catalog, suggestions


class CommandEdit(QPlainTextEdit):
    def __init__(self, text='', platform='huawei', parent=None):
        super().__init__(text, parent)
        self.platform = platform
        self.setObjectName('commandEditor')
        self.setPlaceholderText('One command per line · Try dis cur then Tab · ? for suggestions')
        self.menu = None

    def replace_line(self, command):
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(command)
        self.setTextCursor(cursor)
        self.setFocus()

    def keyPressEvent(self, event):
        if not self.isReadOnly() and (event.key() == Qt.Key.Key_Tab or event.text() == '?'):
            matches = suggestions(self.textCursor().block().text(), self.platform)
            if len(matches) == 1 and event.key() == Qt.Key.Key_Tab:
                self.replace_line(matches[0])
            else:
                self.menu = QMenu(self)
                for command in matches:
                    action = self.menu.addAction(command)
                    action.triggered.connect(lambda checked=False, c=command: self.replace_line(c))
                if not matches:
                    self.menu.addAction('No catalog match — custom commands are still allowed').setEnabled(False)
                self.menu.popup(self.mapToGlobal(self.cursorRect().bottomLeft()))
            event.accept()
            return
        super().keyPressEvent(event)


class CommandLibrary(QDialog):
    def __init__(self, platform='huawei', commands=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle('Command library • ' + platform)
        self.resize(920, 620)
        self.platform = platform
        layout = QVBoxLayout(self)
        title = QLabel('Build your command collection')
        title.setObjectName('section')
        layout.addWidget(title)
        note = QLabel('Browse presets or individual commands. Preview the exact order on the right. Model and firmware support varies.')
        note.setWordWrap(True)
        layout.addWidget(note)
        filters = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItems(['All categories'] + sorted({category(k) for k in PRESETS[platform]}))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search commands or purpose, e.g. config, BGP, CPU…')
        filters.addWidget(self.category)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Preset / command', 'Purpose'])
        self.tree.setColumnWidth(0, 260)
        split.addWidget(self.tree)
        right = QWidget()
        panel = QVBoxLayout(right)
        panel.setContentsMargins(8, 0, 0, 0)
        self.count = QLabel()
        panel.addWidget(self.count)
        self.editor = CommandEdit('\n'.join(commands), platform)
        panel.addWidget(self.editor)
        help_text = QLabel('Tab completes abbreviations (dis cur). ? lists matches.\nTypos get suggestions; nothing is changed until you choose.\nThis is offline help, not a live device CLI.')
        help_text.setWordWrap(True)
        panel.addWidget(help_text)
        split.addWidget(right)
        split.setSizes([450, 430])
        layout.addWidget(split, 1)
        actions = QHBoxLayout()
        add = QPushButton('Append selected to preview')
        add.clicked.connect(self.append_selected)
        actions.addWidget(add)
        actions.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Use these commands')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        actions.addWidget(self.buttons)
        layout.addLayout(actions)
        self.category.currentTextChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.tree.itemDoubleClicked.connect(lambda *_: self.append_selected())
        self.editor.textChanged.connect(self.update_count)
        self.refresh()
        self.update_count()

    def commands(self):
        return [line.strip() for line in self.editor.toPlainText().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]

    def update_count(self):
        count = len(self.commands())
        self.count.setText(f'Execution preview · {count} commands · top to bottom')
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(count))

    def refresh(self):
        self.tree.clear()
        query = self.search.text().lower().strip()
        for key, commands in PRESETS[self.platform].items():
            group = category(key)
            if self.category.currentIndex() and self.category.currentText() != group:
                continue
            if query and query not in ' '.join([key, SCENARIOS[key], *commands]).lower():
                continue
            item = QTreeWidgetItem([key.replace('_', ' ').title(), SCENARIOS[key]])
            item.setData(0, Qt.ItemDataRole.UserRole, commands)
            item.setToolTip(0, group + '\n' + '\n'.join(commands))
            self.tree.addTopLevelItem(item)
            for command in commands:
                child = QTreeWidgetItem([command, 'Display / inspect' if command.startswith('display ') else 'Operational command'])
                child.setToolTip(0, command)
                child.setToolTip(1, SCENARIOS[key])
                child.setData(0, Qt.ItemDataRole.UserRole, [command])
                item.addChild(child)
        self.tree.expandAll()

    def append_selected(self):
        item = self.tree.currentItem()
        if item is None:
            return
        commands = self.commands()
        for command in item.data(0, Qt.ItemDataRole.UserRole):
            if command not in commands:
                commands.append(command)
        self.editor.setPlainText('\n'.join(commands))
