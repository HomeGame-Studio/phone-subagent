"""Per-device flock: one device = one lock = one crew (execution referee).

Lock fds are opened O_CLOEXEC so adb subprocesses spawned by the lock holder
can never inherit (and so outlive) the lock. The lock's lifetime is the
holding process's lifetime — the kernel releases it on death.
"""
import fcntl, os
from pathlib import Path

LOCKS = Path(__file__).resolve().parents[1] / 'locks'


class DeviceLock:
    def __init__(self, device_id, locks_dir=None):
        self.dir = Path(locks_dir or LOCKS)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f'{device_id}.lock'
        self.fd = None
    def acquire(self):
        self.fd = os.open(str(self.path),
                          os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            os.close(self.fd); self.fd = None
            return False
    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd); self.fd = None
    def __enter__(self):
        ok = self.acquire()
        if not ok: raise RuntimeError(f'device busy: {self.path.stem}')
        return self
    def __exit__(self, *a):
        self.release()
