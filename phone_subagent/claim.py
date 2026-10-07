"""Crew-side helpers: atomically take the next slot, give it back, log results.

Taking a slot = atomic rename inside spool/ (first mover wins). The SQLite
ledger stays the claim authority; the rename is the cheap handoff.
"""
import json, os, random, shutil, time
from pathlib import Path
from . import ledger

SPOOL = Path(__file__).resolve().parents[1] / 'spool'
TAKEN = SPOOL / '.taken'

def take(crew, spool=None):
    """Rename the oldest queued slot into .taken/<crew>/ — atomic first-mover claim."""
    spool = Path(spool or SPOOL)
    taken = spool / '.taken' / crew
    taken.mkdir(parents=True, exist_ok=True)
    for cand in sorted(spool.glob('slot-*.json')):
        dest = taken / cand.name
        try:
            os.rename(cand, dest)          # atomic on same filesystem
        except OSError:
            continue                       # another crew won it
        try:
            return dest, json.loads(dest.read_text())
        except Exception:
            continue
    return None, None

def finish(slot_path, crew, ok, line, path=None):
    """Log the result and release the device. Input accounting per ledger rules."""
    return ledger.finish_slot(slot_path, crew, ok, line, path=path)

def strike(device, note, path=None):
    ledger.strike(device, note, path=path)

def backoff(attempt):
    time.sleep(min(300, 30 * 2 ** attempt) + random.uniform(0, 5))
