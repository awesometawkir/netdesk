"""Hung devices, real Stop, reconnect and the session/tick workflow (all mocked; no devices)."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

import netdesk as n
import netdesk_batch as b
import netdesk_gui as gui


class BlockingConnection:
    """send_command blocks like a stalled SSH read until the transport is closed."""
    def __init__(self, log_path=None):
        self.closed = threading.Event()
        self.remote_conn = Mock(close=self.closed.set)
        self.remote_conn_pre = None
        self.log_path = log_path

    def find_prompt(self):
        return '<switch>'

    def send_command(self, command, **kwargs):
        if command == 'display version':
            return 'version text'
        if not self.closed.wait(10):
            raise AssertionError('watchdog never closed the session')
        raise OSError('Socket is closed')

    def disconnect(self):
        self.closed.set()


class CollectorControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.device = {'name': '192.0.2.1', 'ip': '192.0.2.1', 'device_type': 'huawei', 'port': 22}

    def tearDown(self):
        self.tmp.cleanup()

    def test_silent_device_is_closed_and_marked_timeout(self):
        conn = BlockingConnection()
        with patch.object(n, 'ConnectHandler', return_value=conn):
            started = time.monotonic()
            result = n.collect_one(self.device, ['display version', 'display ip routing-table'], self.root, 'u', 'p', idle_timeout=1)
        self.assertLess(time.monotonic() - started, 6)
        self.assertEqual(result['status'], 'timeout')
        self.assertIn('silent', result['error'])
        self.assertEqual(result['commands'][0]['status'], 'ok')

    def test_stop_closes_blocked_session(self):
        conn = BlockingConnection()
        stop = threading.Event()
        threading.Timer(0.5, stop.set).start()
        with patch.object(n, 'ConnectHandler', return_value=conn):
            started = time.monotonic()
            result = n.collect_one(self.device, ['display version', 'display ip routing-table'], self.root, 'u', 'p',
                                   stop=stop, idle_timeout=0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(result['status'], 'cancelled')

    def test_channel_error_reconnects_once(self):
        good = Mock()
        good.find_prompt.return_value = '<switch>'
        good.send_command.return_value = 'ok output'
        calls = [Exception('Unable to open channel.'), good]
        def connect(**kwargs):
            item = calls.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        with patch.object(n, 'ConnectHandler', side_effect=connect), patch.object(threading.Event, 'wait', return_value=False):
            result = n.collect_one(self.device, ['display version'], self.root, 'u', 'p')
        self.assertEqual(result['status'], 'ok')

    def test_unreachable_is_not_retried(self):
        with patch.object(n, 'ConnectHandler', side_effect=Exception('TCP connection to device failed.')) as connect:
            result = n.collect_one(self.device, ['display version'], self.root, 'u', 'p')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(connect.call_count, 1)

    def test_batch_cancels_one_job_only(self):
        jobs = b.make_jobs('A', '192.0.2.1-2', credential_group='G')
        cancel = {j['id']: threading.Event() for j in jobs}
        cancel[jobs[0]['id']].set()
        good = Mock()
        good.find_prompt.return_value = '<switch>'
        good.send_command.return_value = 'x'
        with patch.object(n, 'ConnectHandler', return_value=good):
            _, data = b.run_batch(jobs, {'G': ('u', 'p')}, runs_dir=self.root, cancel=cancel)
        status = {r['job_id']: r['status'] for r in data['results']}
        self.assertEqual(status[jobs[0]['id']], 'cancelled')
        self.assertEqual(status[jobs[1]['id']], 'ok')


class SessionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(n, 'ROOT', root), patch.object(n, 'RUNS', root / 'runs')]
        for p in self.patches:
            p.start()
        self.window = gui.MainWindow()

    def tearDown(self):
        self.window.dirty = False
        self.window.close()
        self.app.processEvents()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_only_ticked_devices_are_collected(self):
        self.window.jobs = b.make_jobs('Old', '192.0.2.1-3', credential_group='G')
        self.window.vault = {'G': ('u', 'p')}
        self.window.refresh_table()
        self.window.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual([j['device']['ip'] for j in self.window.ticked_jobs()], ['192.0.2.1', '192.0.2.3'])
        self.assertEqual(self.window.start_btn.text(), 'Collect 2 devices')
        with patch.object(gui, 'ReviewDialog') as review, patch.object(self.window, 'launch_collection') as launch:
            review.return_value.exec.return_value = 1
            self.window.start_collection()
        self.assertEqual([j['device']['ip'] for j in launch.call_args.args[0]], ['192.0.2.1', '192.0.2.3'])

    def test_adding_after_a_run_offers_fresh_start(self):
        self.window.jobs = b.make_jobs('Range 1', '192.0.2.1', credential_group='Range 1 login')
        self.window.vault = {'Range 1 login': ('u', 'p')}
        self.window.results = {self.window.jobs[0]['id']: {'status': 'ok'}}
        def choose(label):
            def run(box):
                for item in box.buttons():
                    if item.text() == label:
                        box.setProperty('picked', label)
                        return item
            return run
        with patch.object(QMessageBox, 'exec', return_value=0), \
             patch.object(QMessageBox, 'clickedButton', lambda box: next(x for x in box.buttons() if x.text() == 'Start fresh')):
            self.assertTrue(self.window.prepare_to_add())
        self.assertEqual(self.window.jobs, [])
        self.assertEqual(self.window.vault, {})

    def test_keep_and_add_unticks_old_devices(self):
        self.window.jobs = b.make_jobs('Range 1', '192.0.2.1', credential_group='G')
        self.window.results = {self.window.jobs[0]['id']: {'status': 'failed'}}
        with patch.object(QMessageBox, 'exec', return_value=0), \
             patch.object(QMessageBox, 'clickedButton', lambda box: next(x for x in box.buttons() if x.text() == 'Keep them and add')):
            self.assertTrue(self.window.prepare_to_add())
        self.assertEqual(len(self.window.jobs), 1)
        self.assertEqual(self.window.ticked_jobs(), [])

    def test_close_during_run_stops_and_closes(self):
        self.window.jobs = b.make_jobs('A', '192.0.2.1', credential_group='G')
        self.window.vault = {'G': ('u', 'p')}
        conn = BlockingConnection()
        with patch.object(n, 'ConnectHandler', return_value=conn):
            self.window.launch_collection(self.window.jobs)
            QTest.qWait(300)
            with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                self.window.close()
            deadline = time.monotonic() + 8
            while self.window.busy and time.monotonic() < deadline:
                QTest.qWait(50)
            self.window.worker.join(timeout=3)
        self.assertFalse(self.window.busy)
        self.assertEqual(next(iter(self.window.results.values()))['status'], 'cancelled')
        self.assertTrue(self.window.close_after_stop)


if __name__ == '__main__':
    unittest.main()


class RobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_friendly_errors(self):
        self.assertIn('Cannot reach', n.friendly_error(Exception('TCP connection to device failed.\n\nCommon causes...')))
        self.assertIn('refused a new SSH session', n.friendly_error(Exception('Unable to open channel.')))
        self.assertEqual(n.friendly_error(Exception('weird\nsecond line')), 'weird')

    def test_unreachable_message_is_short(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(n, 'ConnectHandler', side_effect=Exception('TCP connection to device failed.\n\nCommon causes:\n1. x')):
            result = n.collect_one({'name': 'a', 'ip': '192.0.2.9', 'device_type': 'huawei', 'port': 22}, ['display version'], Path(tmp), 'u', 'p')
        self.assertNotIn('\n', result['error'])
        self.assertIn('Cannot reach', result['error'])

    def test_custom_row_without_commands_has_clear_error(self):
        dialog = gui.RangesDialog()
        dialog.rows.cellWidget(0, 0).setText('192.0.2.1')
        dialog.rows.cellWidget(0, 1).setText('u')
        dialog.rows.cellWidget(0, 2).setText('p')
        dialog.rows.cellWidget(0, 4).setCurrentText('custom')
        dialog.rows.cellWidget(0, 4).custom_commands = []
        with self.assertRaisesRegex(ValueError, 'did not type any'):
            dialog.build()

    def test_review_warns_about_duplicate_devices(self):
        jobs = b.make_jobs('A', '192.0.2.1', credential_group='G') + b.make_jobs('B', '192.0.2.1', credential_group='G')
        dialog = gui.ReviewDialog(jobs, {'G': ('u', 'p')}, 4)
        texts = [w.text() for w in dialog.findChildren(gui.QLabel)]
        self.assertTrue(any('more than once' in t for t in texts))

    def test_dead_worker_unlocks_window(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(n, 'ROOT', Path(tmp)), patch.object(n, 'RUNS', Path(tmp) / 'r'):
            window = gui.MainWindow()
            window.busy = True
            window.active_ids = set()
            window.worker = threading.Thread(target=lambda: None)
            window.worker.start(); window.worker.join()
            window.poll(); window.poll()
            self.assertFalse(window.busy)
            window.close()
