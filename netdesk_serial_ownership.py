"""Cooperative Linux port ownership between NetDesk and auto-open terminals."""
import hashlib
import os
import time
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None


def lock_root():
    base = Path(os.environ.get('XDG_RUNTIME_DIR', f'/tmp/netdesk-runtime-{os.getuid()}'))
    root = base / 'netdesk-serial-locks'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


class PortLease:
    def __init__(self, port, priority=False, root=None):
        self.port, self.priority = port, priority
        self.files = []
        self.root = Path(root) if root else None

    def file(self, suffix):
        root = self.root or lock_root()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256(os.path.realpath(self.port).encode()).hexdigest()
        return (root / (key + suffix)).open('a')

    def requested(self):
        if fcntl is None:
            return False
        with self.file('.priority') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True

    def acquire(self, timeout=0):
        if fcntl is None:
            return self
        deadline = time.monotonic() + timeout
        try:
            for suffix in (['.priority', '.device'] if self.priority else ['.device']):
                stream = self.file(suffix)
                self.files.append(stream)
                while True:
                    try:
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Serial port is already in use. Close the other session and retry.')
                        time.sleep(.05)
            if not self.priority and self.requested():
                raise TimeoutError('NetDesk has requested the port.')
            return self
        except Exception:
            self.release()
            raise

    def release(self):
        for stream in reversed(self.files):
            stream.close()
        self.files.clear()
