import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import netdesk_serial_hotplug as hotplug
from netdesk_serial_ownership import PortLease


class FakeWezTerm:
    def __init__(self):
        self.live = {}
        self.next_id = 0
        self.opened = []

    def panes(self):
        return [{'pane_id': i, 'size': {'cols': 80, 'rows': 24}} for i in self.live]

    def open(self, port, baud, panes, label='', log_dir=None):
        pane_id, self.next_id = self.next_id, self.next_id + 1
        self.live[pane_id] = port
        self.opened.append((port, baud, len(panes)))
        self.last = {'label': label, 'log_dir': log_dir}
        return pane_id

    def close(self, pane_id):
        self.live.pop(pane_id, None)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.term = FakeWezTerm()
        self.ctl = hotplug.Controller(self.term, {'default_baud': 9600, 'baud_by_port': {'/dev/ttyUSB1': 115200}})

    def test_each_new_adapter_gets_its_own_pane_in_one_window(self):
        self.ctl.sync(['/dev/ttyUSB0'])
        self.ctl.sync(['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0'])
        self.assertEqual(sorted(self.term.live.values()), ['/dev/ttyACM0', '/dev/ttyUSB0', '/dev/ttyUSB1'])
        # The first opens a window; later adapters split the existing panes.
        self.assertEqual([n for _, _, n in self.term.opened], [0, 1, 2])
        self.assertIn(('/dev/ttyUSB1', 115200, 2), self.term.opened)

    def test_unplug_closes_pane_and_manual_close_waits_for_replug(self):
        self.ctl.sync(['/dev/ttyUSB0', '/dev/ttyUSB1'])
        self.ctl.sync(['/dev/ttyUSB0'])
        self.assertEqual(list(self.term.live.values()), ['/dev/ttyUSB0'])
        self.term.live.clear()  # user closes the pane
        self.ctl.sync(['/dev/ttyUSB0'])
        self.ctl.sync(['/dev/ttyUSB0'])
        self.assertEqual(self.term.live, {})
        self.ctl.sync([])  # unplug
        self.ctl.sync(['/dev/ttyUSB0'])  # replug
        self.assertEqual(list(self.term.live.values()), ['/dev/ttyUSB0'])

    def test_reset_clears_stale_panes_and_shutdown_closes_owned(self):
        self.term.live[99] = '/dev/ttyUSB9'
        self.ctl.reset()
        self.assertEqual(self.term.live, {})
        self.ctl.sync(['/dev/ttyUSB0'])
        self.ctl.shutdown()
        self.assertEqual(self.term.live, {})

    def test_invalid_baud_is_rejected(self):
        ctl = hotplug.Controller(self.term, {'default_baud': 5})
        with self.assertRaises(ValueError):
            ctl.sync(['/dev/ttyUSB0'])

    def test_split_direction_prefers_wide_panes_side_by_side(self):
        wide = [{'pane_id': 1, 'size': {'cols': 160, 'rows': 46}}]
        self.assertEqual(hotplug.split_target(wide), (1, '--right'))
        tall = [{'pane_id': 2, 'size': {'cols': 80, 'rows': 46}}]
        self.assertEqual(hotplug.split_target(tall), (2, '--bottom'))


class SettingsTests(unittest.TestCase):
    def test_baud_follows_adapter_serial_before_port_and_model(self):
        ctl = hotplug.Controller(FakeWezTerm(), {'default_baud': 9600, 'baud_by_port': {
            'A9XYZ': 115200, '/dev/ttyUSB0': 38400, '10c4:ea60': 19200}})
        info = {'serial': 'A9XYZ', 'vid_pid': '10c4:ea60'}
        self.assertEqual(ctl.baud('/dev/ttyUSB3', info), 115200)
        self.assertEqual(ctl.baud('/dev/ttyUSB0', {'vid_pid': '10c4:ea60'}), 38400)
        self.assertEqual(ctl.baud('/dev/ttyUSB5', {'vid_pid': '10c4:ea60'}), 19200)
        self.assertEqual(ctl.baud('/dev/ttyUSB5', {}), 9600)

    def test_label_notification_and_logging_switch(self):
        term, told = FakeWezTerm(), []
        ctl = hotplug.Controller(term, {'log': True, 'log_dir': '/tmp/x'}, notify=lambda *a: told.append(a))
        ctl.sync({'/dev/ttyUSB0': {'description': 'CP2102 USB to UART'}})
        self.assertEqual(term.last['label'], 'CP2102 USB to UART')
        self.assertEqual(str(term.last['log_dir']), '/tmp/x')
        self.assertEqual(told[0][0], 'Serial console opened')
        ctl.settings = {'log': False, 'notify': False}
        ctl.sync({})
        ctl.sync({'/dev/ttyUSB1': {}})
        self.assertIsNone(term.last['log_dir'])
        self.assertEqual(len(told), 1)

    def test_settings_file_reloads_and_survives_bad_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 's.json'
            loader = hotplug.SettingsFile(path)
            self.assertEqual(loader.get()['default_baud'], 9600)
            path.write_text('{"default_baud": 115200}')
            self.assertEqual(loader.get()['default_baud'], 115200)
            import os
            path.write_text('{broken')
            os.utime(path, ns=(1, 1))
            self.assertEqual(loader.get()['default_baud'], 115200)
            created = hotplug.ensure_settings_file(Path(root) / 'new' / 's.json')
            self.assertTrue(created.exists())


class TerminalMenuTests(unittest.TestCase):
    def setUp(self):
        import io
        import netdesk_serial_terminal as t
        self.t = t
        self.out = patch_stdout = io.StringIO()
        self.patch = __import__('unittest.mock').mock.patch.object(t.sys, 'stdout', patch_stdout)
        self.patch.start()
        self.console = t.Console('/dev/ttyUSB0', 9600)
        self.console.connection = FakeSerial()

    def tearDown(self):
        self.patch.stop()

    def test_plain_typing_is_forwarded(self):
        self.assertTrue(self.console.typed(b'display version\r'))
        self.assertEqual(self.console.connection.sent, b'display version\r')

    def test_menu_changes_baud_sends_break_and_literal_prefix(self):
        self.assertTrue(self.console.typed(b'ab\x1db'))
        self.assertEqual(self.console.connection.sent, b'ab')
        self.assertEqual(self.console.connection.baudrate, 19200)
        self.console.typed(b'\x1d')  # prefix split across reads
        self.console.typed(b'k')
        self.assertTrue(self.console.connection.broke)
        self.console.typed(b'\x1d]')
        self.assertEqual(self.console.connection.sent, b'ab\x1d')
        self.assertFalse(self.console.typed(b'\x1dq'))

    def test_baud_cycle_wraps(self):
        self.assertEqual(self.t.next_baud(115200), 9600)
        self.assertEqual(self.t.next_baud(9600), 19200)

    def test_logging_toggle_writes_received_bytes_only(self):
        with tempfile.TemporaryDirectory() as root:
            c = self.t.Console('/dev/ttyUSB0', 9600, log_dir=root)
            c.open_log()
            self.assertEqual(len(list(Path(root).iterdir())), 1)
            c.typed(b'\x1dl')
            self.assertIsNone(c.log)
            c.typed(b'\x1dl')
            self.assertIsNotNone(c.log)
            c.close_log()


class FakeSerial:
    def __init__(self):
        self.sent, self.baudrate, self.broke = b'', 9600, False

    def write(self, data):
        self.sent += data

    def send_break(self, duration):
        self.broke = True


class LeaseTests(unittest.TestCase):
    def test_netdesk_priority_makes_terminal_yield(self):
        with tempfile.TemporaryDirectory() as root:
            terminal = PortLease('/dev/ttyUSB0', root=root).acquire()
            self.assertFalse(terminal.requested())
            priority = PortLease('/dev/ttyUSB0', priority=True, root=root)
            priority.files.append(priority.file('.priority'))
            import fcntl
            fcntl.flock(priority.files[0], fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(terminal.requested())
            terminal.release()  # terminal pauses
            priority.files[0].close(); priority.files.clear()
            priority.acquire(timeout=1)
            with self.assertRaises(TimeoutError):
                PortLease('/dev/ttyUSB0', root=root).acquire()
            priority.release()
            PortLease('/dev/ttyUSB0', root=root).acquire().release()


if __name__ == '__main__':
    unittest.main()
