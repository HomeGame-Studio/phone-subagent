"""Reaper: crash recovery for days-unattended operation.

- Stuck slots (claimed, never finished) older than TTL -> repair queue.
- Input reservations older than TTL -> outcome_unknown (NEVER straight back
  to available — the anti-double-spend rule from the adversarial review).
- reconcile() is the judgment call: inspect, then mark spent or available.
"""
import sqlite3, time
from pathlib import Path
from . import ledger

def reap(stuck_slot_ttl_h=4, reservation_ttl_h=4, path=None):
    con = ledger.connect(path)
    cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - stuck_slot_ttl_h*3600))
    resv = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - reservation_ttl_h*3600))
    with con:
        stale_slots = con.execute(
            "SELECT id, device, path FROM slots WHERE state='claimed' AND claimed_at < ?", (cutoff,)).fetchall()
        for s in stale_slots:
            con.execute("UPDATE slots SET state='failed', finished_at=? WHERE id=?",
                        (ledger.now(), s['id']))
            con.execute("UPDATE devices SET state='repair', note='reaper: stuck slot' WHERE id=?", (s['device'],))
            p = Path(s['path'])
            if p.exists(): p.unlink(missing_ok=True)
        # old reservations: the claiming dispatcher/crew died before finishing
        con.execute("UPDATE inputs SET state='outcome_unknown' WHERE state='reserved' AND id NOT IN "
                    "(SELECT input_id FROM slots WHERE state IN ('queued','claimed'))")
    con.close()
    return len(stale_slots)

def reconcile(input_id, spent: bool, path=None):
    """Human/AI decision for outcome_unknown inputs."""
    con = ledger.connect(path)
    with con:
        con.execute("UPDATE inputs SET state=? WHERE id=? AND state='outcome_unknown'",
                    ('spent' if spent else 'available', input_id))
    con.close()
