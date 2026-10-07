"""Reaper: crash recovery for days-unattended operation.

Expiry is split by slot state — backlog is not a device fault:

- **claimed** past TTL (crew took it and died) -> slot failed(expired),
  input -> outcome_unknown (NEVER available — the remote may have consumed
  it), device -> repair.
- **queued** past TTL (starvation, dispatcher died before publication,
  or a crew died between rename and claim-commit) -> slot skipped(expired),
  input -> available (never executed), device -> idle + queue-age signal.
- old input reservations with no active slot -> outcome_unknown.
- spool reconciliation: slot files (spool/ or .taken/) whose basename has
  no active (queued/claimed) ledger row, and malformed files, move to
  spool/.trash/ — never re-consumed.

reconcile() is the judgment call for outcome_unknown inputs: inspect, then
mark spent or available.
"""
import json, sqlite3, time
from pathlib import Path
from . import ledger


def reap(claimed_ttl_h=1, queued_ttl_h=1, path=None, spool=None):
    """Returns a dict of counts for monitoring."""
    con = ledger.connect(path)
    out = dict(claimed_expired=0, queued_expired=0, reservations_orphaned=0,
               files_trashed=0)
    try:
        claimed_cut = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                    time.gmtime(time.time() - claimed_ttl_h * 3600))
        queued_cut = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                  time.gmtime(time.time() - queued_ttl_h * 3600))
        with con:
            stale = con.execute(
                "SELECT id, device, input_id, path FROM slots "
                "WHERE state='claimed' AND claimed_at < ?", (claimed_cut,)).fetchall()
            for s in stale:
                con.execute("UPDATE slots SET state='failed', class='expired', "
                            "finished_at=? WHERE id=?", (ledger.now(), s['id']))
                if s['input_id']:
                    con.execute("UPDATE inputs SET state='outcome_unknown' "
                                "WHERE id=?", (s['input_id'],))
                con.execute("UPDATE devices SET state='repair', note=? "
                            "WHERE id=?", ('reaper: stuck claim', s['device']))
                out['claimed_expired'] += 1
            stale_q = con.execute(
                "SELECT id, device, input_id FROM slots "
                "WHERE state='queued' AND created_at < ?", (queued_cut,)).fetchall()
            for s in stale_q:
                con.execute("UPDATE slots SET state='skipped', class='expired', "
                            "finished_at=? WHERE id=?", (ledger.now(), s['id']))
                if s['input_id']:
                    con.execute("UPDATE inputs SET state='available' "
                                "WHERE id=?", (s['input_id'],))
                con.execute("UPDATE devices SET state='idle' "
                            "WHERE id=? AND state='busy'", (s['device'],))
                out['queued_expired'] += 1
            cur = con.execute(
                "UPDATE inputs SET state='outcome_unknown' WHERE state='reserved' "
                "AND id NOT IN (SELECT input_id FROM slots "
                "WHERE state IN ('queued','claimed'))")
            out['reservations_orphaned'] = cur.rowcount
        # spool reconciliation: files without an active row are dead
        if spool:
            spool = Path(spool)
            if not spool.exists():
                spool.mkdir(parents=True, exist_ok=True)
            trash = spool / '.trash'
            trash.mkdir(parents=True, exist_ok=True)
            names = {str(r['path']) for r in con.execute(
                "SELECT path FROM slots WHERE state IN ('queued','claimed')")}
            active = {Path(p).name for p in names}
            for f in list(spool.glob('slot-*.json')) + \
                     list((spool / '.taken').rglob('slot-*.json')):
                if f.name in active:
                    continue
                try:
                    json.loads(f.read_text())          # malformed -> trash too
                except Exception:
                    pass
                f.rename(trash / f.name)
                out['files_trashed'] += 1
    finally:
        con.close()
    return out


def reconcile(input_id, spent: bool, path=None):
    """Human/AI decision for outcome_unknown inputs."""
    con = ledger.connect(path)
    with con:
        con.execute("UPDATE inputs SET state=? WHERE id=? AND state='outcome_unknown'",
                    ('spent' if spent else 'available', input_id))
    con.close()
