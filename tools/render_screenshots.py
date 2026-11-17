"""Render documentation screenshots using example addresses; never opens a connection."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
import netdesk as n
import netdesk_batch as b
from netdesk_gui import MainWindow, ScenarioDialog, RangesDialog, STYLE

root = n.ROOT
output = root / 'docs' / 'screenshots'
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as temporary, patch.object(n, 'RUNS', Path(temporary)):
    window = MainWindow()
    window.resize(1260, 840)
    window.jobs = b.load_plan(root / 'examples' / 'multi-scenario.json')
    window.refresh_table()
    window.show()
    app.processEvents()
    window.grab().save(str(output / 'collections.png'))
    ranges = RangesDialog(parent=window)
    for row, (targets, user) in enumerate([('192.0.2.1-10', 'branch-a'), ('198.51.100.1-15', 'branch-b')]):
        ranges.rows.cellWidget(row, 0).setText(targets)
        ranges.rows.cellWidget(row, 1).setText(user)
    ranges.show()
    app.processEvents()
    ranges.grab().save(str(output / 'ranges.png'))
    ranges.reject()
    dialog = ScenarioDialog({}, parent=window)
    dialog.name.setText('Core BGP troubleshooting')
    dialog.targets.setPlainText('198.51.100.10-11')
    dialog.platform.setCurrentText('cisco_ios')
    dialog.preset.setCurrentText('bgp')
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / 'scenario.png'))
    dialog.reject()
    window.tabs.setCurrentIndex(1)
    window.port.setEditText('/dev/ttyUSB0')
    window.serial_state.setText('Example preview • Disconnected • No device session opened')
    window.terminal_output.setPlainText('Serial output appears here after connecting.\n\nType commands or login responses below.\nEnable recording before connecting to save raw device output.\n\nFor your original terminal workflow, choose the serialportsconnect wizard.')
    app.processEvents()
    window.grab().save(str(output / 'serial.png'))
    window.dirty = False
    window.close()
print(f'Saved example screenshots to {output}')
