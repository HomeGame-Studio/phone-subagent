"""Crew-side helpers: atomically take the next slot, give it back, log results.

The atomic file rename is the single-claimant gate (one file, one winner);
the ledger then confirms the claim conditionally — queued AND not expired —
so a lost claim is fatal to execution, never driven. A rename whose ledger
update loses (expired or already terminal) moves the file to .trash/.
"""
import json, os, random, shutil, time
from pathlib import Path
from . import ledger

SPOOL = Path(__file__).resolve().parents[1] / 'spool'
TAKEN = SPOOL / '.taken'
QUEUED_TTL_S = 3600          # a queued slot older than this is dead (reaper's
                             # queued-expiry; take() refuses it defensively too)


def take(crew, spool=None, path=None, queued_ttl_s=QUEUED_TTL_S):
    """Rename the oldest queued slot into .taken/<crew>/ (atomic first-mover
    claim), then confirm it in the ledger: state queued AND not expired.
    Returns (dest, slot_json) or (None, None)."""
    spool = Path(spool or SPOOL)
    taken = spool / '.taken' / crew
    trash = spool / '.trash'
    taken.mkdir(parents=True, exist_ok=True)
    cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                           time.gmtime(time.time() - queued_ttl_s))
    for cand in sorted(spool.glob('slot-*.json')):
        dest = taken / cand.name
        try:
            os.rename(cand, dest)          # atomic on same filesystem
        except OSError:
            continue                       # another crew won it
        con = ledger.connect(path)
        try:
            cur = con.execute(
                "UPDATE slots SET state='claimed', claimed_at=?, crew=? "
                "WHERE path=? AND state='queued' AND created_at >= ?",
                (ledger.now(), crew, str(cand), cutoff))
            con.commit()
            ok = cur.rowcount == 1
        finally:
            con.close()
        if not ok:
            # expired / reaped / cancelled between rename and commit — dead,
            # never driven; trash the file so no one re-consumes it
            trash.mkdir(parents=True, exist_ok=True)
            os.rename(dest, trash / dest.name)
            continue
        try:
            return dest, json.loads(dest.read_text())
        except Exception:
            continue
    return None, None


def finish(slot_ref, crew, outcome, line, strike=None, path=None):
    """Log the result and release the device. Input accounting per ledger rules."""
    return ledger.finish_slot(slot_ref, crew, outcome, line, strike=strike, path=path)


def strike(device, note, path=None, cls='manual'):
    ledger.strike(device, note, path=path, cls=cls)


def backoff(attempt):
    time.sleep(min(300, 30 * 2 ** attempt) + random.uniform(0, 5))
