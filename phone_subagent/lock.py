"""Per-device flock: one device = one lock = one crew (execution referee)."""
import fcntl, os
from pathlib import Path

LOCKS = Path(__file__).resolve().parents[1] / 'locks'

class DeviceLock:
    def __init__(self, device_id):
        LOCKS.mkdir(parents=True, exist_ok=True)
        self.path = LOCKS / f'{device_id}.lock'
        self.fd = None
    def acquire(self):
        self.fd = open(self.path, 'a')
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self.fd.close(); self.fd = None
            return False
    def release(self):
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close(); self.fd = None
    def __enter__(self):
        ok = self.acquire()
        if not ok: raise RuntimeError(f'device busy: {self.path.stem}')
        return self
    def __exit__(self, *a):
        self.release()
