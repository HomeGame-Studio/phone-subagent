#!/usr/bin/env python3
import argparse, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_subagent.lock import DeviceLock
p = argparse.ArgumentParser()
p.add_argument('--device', required=True)
p.add_argument('cmd', nargs=argparse.REMAINDER)
a = p.parse_args()
with DeviceLock(a.device):
    r = subprocess.run(a.cmd)
sys.exit(r.returncode)
