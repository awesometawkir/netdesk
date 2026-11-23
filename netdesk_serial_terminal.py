"""Interactive raw console for the hotplug service.

Ctrl+] opens a one-key menu: q quit, b change baud, k send break, l toggle log,
] send a literal Ctrl+]. Received bytes are logged (raw) when a log dir is given;
typed input is never logged.
"""
import argparse
import os
import select
import signal
import sys
import termios
import time
import tty
from pathlib import Path

import serial
from netdesk_serial_ownership import PortLease

PREFIX = 0x1d  # Ctrl+]
BAUDS = (9600, 19200, 38400, 57600, 115200)
HELP = 'Ctrl+] then: q quit · b baud · k break · l log on/off · ] send Ctrl+]'


def next_baud(baud):
    return next((b for b in BAUDS if b > baud), BAUDS[0])


class Console:
    def __init__(self, port, baud, label='', log_dir=None):
        self.port, self.baud, self.label = port, baud, label
        self.log_dir = Path(log_dir) if log_dir else None
        self.logging = self.log_dir is not None
        self.log = None
        self.connection = None
        self.lease = PortLease(port)
        self.state = None
        self.prefix = False
        self.stopped = False

    # Output helpers -----------------------------------------------------
    def say(self, text):
        sys.stdout.write('\r\n[NetDesk Serial] ' + text + '\r\n')
        sys.stdout.flush()

    def notice(self, value):
        if value != self.state:
            self.say(value)
            self.state = value

    def title(self):
        name = f'{self.label} · ' if self.label else ''
        sys.stdout.write(f'\033]0;{name}{self.port} · {self.baud}\007')
        sys.stdout.flush()

    def open_log(self):
        if not self.logging or self.log:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S')
            path = self.log_dir / f'{stamp}-{Path(self.port).name}.log'
            self.log = path.open('ab')
            self.say(f'Logging device output to {path}')
        except OSError as error:
            self.logging = False
            self.say(f'Logging disabled: {error.strerror or error}')

    def close_log(self):
        if self.log:
            self.log.close()
            self.log = None

    # Menu ---------------------------------------------------------------
    def command(self, key):
        """Handle the key typed after Ctrl+]. Returns False to quit."""
        if key in b'qQ':
            return False
        if key in b'bB':
            self.baud = next_baud(self.baud)
            if self.connection:
                self.connection.baudrate = self.baud
            self.title()
            self.say(f'Baud now {self.baud}.')
        elif key in b'kK':
            if self.connection:
                self.connection.send_break(0.3)
                self.say('Break sent.')
        elif key in b'lL':
            if self.log_dir is None:
                self.say('No log folder configured.')
            elif self.logging:
                self.logging = False
                self.close_log()
                self.say('Logging off.')
            else:
                self.logging = True
                self.open_log()
        elif key in (PREFIX, ord("]")):
            if self.connection:
                self.connection.write(bytes([PREFIX]))
        else:
            self.say(HELP)
        return True

    def typed(self, data):
        """Forward keyboard bytes, intercepting the Ctrl+] menu. Returns False to quit."""
        out = bytearray()
        for byte in data:
            if self.prefix:
                self.prefix = False
                if out and self.connection:
                    self.connection.write(bytes(out))
                out.clear()
                if not self.command(byte):
                    return False
            elif byte == PREFIX:
                self.prefix = True
                self.notice_menu()
            else:
                out.append(byte)
        if out and self.connection:
            self.connection.write(bytes(out))
        return True

    def notice_menu(self):
        sys.stdout.write('\r\n[menu] q quit · b baud · k break · l log · ] send Ctrl+]  ')
        sys.stdout.flush()

    # Port ---------------------------------------------------------------
    def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None
        self.lease.release()

    def connect(self):
        try:
            self.lease.acquire()
            self.connection = serial.Serial(self.port, baudrate=self.baud, timeout=0, write_timeout=1,
                                            exclusive=True, xonxoff=False, rtscts=False, dsrdtr=False)
            self.open_log()
            self.notice('Connected. Press Enter for the device prompt.')
        except (OSError, serial.SerialException, TimeoutError):
            self.disconnect()
            self.notice('Waiting for port access. Check permissions if this persists.')

    def run(self):
        def stop(*_):
            self.stopped = True
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, stop)
        stdin = sys.stdin.fileno()
        previous = termios.tcgetattr(stdin)
        self.title()
        print(f'{self.label + " · " if self.label else ""}{self.port} · {self.baud} baud · 8N1', flush=True)
        print(HELP, flush=True)
        try:
            tty.setraw(stdin)
            while not self.stopped:
                if not os.path.exists(self.port):
                    self.notice('Device unplugged.')
                    return 0
                if self.lease.requested():
                    self.disconnect()
                    self.notice('Paused while NetDesk uses this port; resumes automatically.')
                elif self.connection is None:
                    self.connect()
                watched = [stdin] + ([self.connection.fileno()] if self.connection else [])
                ready, _, _ = select.select(watched, [], [], .15)
                if stdin in ready:
                    data = os.read(stdin, 4096)
                    if not data or not self.typed(data):
                        return 0
                if self.connection and self.connection.fileno() in ready:
                    data = self.connection.read(max(1, min(self.connection.in_waiting, 65536)))
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                        if self.log:
                            self.log.write(data)
                            self.log.flush()
        except (OSError, serial.SerialException) as error:
            self.notice(f'Connection ended: {type(error).__name__}.')
        finally:
            self.disconnect()
            self.close_log()
            termios.tcsetattr(stdin, termios.TCSADRAIN, previous)
        return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    parser.add_argument('--baud', type=int, default=9600)
    parser.add_argument('--label', default='')
    parser.add_argument('--log-dir')
    args = parser.parse_args()
    if not args.port.startswith('/dev/') or not 300 <= args.baud <= 4000000:
        parser.error('Expected a /dev serial port and valid baud rate.')
    raise SystemExit(Console(args.port, args.baud, args.label, args.log_dir).run())
