#!/usr/bin/env python3
"""Fast in-process slot taker: tight-loop dir scan (no per-poll python
startup), CREW-CREATE preferred, atomic rename + ledger confirm protocol
identical to take-pref.py. Prints slot path + JSON when won, else EMPTY."""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, '/home/adbserver/phone-subagent')
from phone_subagent import ledger
from phone_subagent.claim import QUEUED_TTL_S

CREW = sys.argv[1] if len(sys.argv) > 1 else 'keeper-882'
SPOOL = Path(sys.argv[2] if len(sys.argv) > 2 else '/home/adbserver/farm/spool247')
MAX_S = int(sys.argv[3] if len(sys.argv) > 3 else 540)

taken = SPOOL / '.taken' / CREW
trash = SPOOL / '.trash'
taken.mkdir(parents=True, exist_ok=True)

deadline = time.time() + MAX_S
seen = set()

def procedure(p):
    try:
        return json.loads(p.read_text()).get('procedure', '')
    except Exception:
        return ''

def try_claim(cand):
    dest = taken / cand.name
    try:
        os.rename(cand, dest)
    except OSError:
        return None
    cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                           time.gmtime(time.time() - QUEUED_TTL_S))
    con = ledger.connect(None)
    try:
        cur = con.execute(
            "UPDATE slots SET state='claimed', claimed_at=?, crew=? "
            "WHERE path=? AND state='queued' AND created_at >= ?",
            (ledger.now(), CREW, str(cand), cutoff))
        con.commit()
        ok = cur.rowcount == 1
    finally:
        con.close()
    if not ok:
        trash.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(dest, trash / dest.name)
        except OSError:
            pass
        return None
    try:
        return dest, json.loads(dest.read_text())
    except Exception:
        return None

while time.time() < deadline:
    cands = sorted(SPOOL.glob('slot-*.json'))
    new = [c for c in cands if c not in seen]
    if new:
        pref = [c for c in new if procedure(c) == 'CREW-CREATE']
        order = pref + [c for c in new if c not in pref]
        for cand in order:
            won = try_claim(cand)
            if won:
                print(won[0])
                print(json.dumps(won[1], indent=1))
                sys.exit(0)
        seen.update(new)
    time.sleep(0.05)
print('EMPTY')
