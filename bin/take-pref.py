#!/usr/bin/env python3
"""take.py with procedure preference: claims the oldest slot matching
--prefer first (e.g. CREW-CREATE), falling back to any queued slot.
Same atomic rename + ledger confirm protocol as claim.take()."""
import argparse, json, os, sys, time
from pathlib import Path
sys.path.insert(0, '/home/adbserver/phone-subagent')
from phone_subagent import ledger
from phone_subagent.claim import QUEUED_TTL_S

p = argparse.ArgumentParser()
p.add_argument('--crew', required=True)
p.add_argument('--spool', required=True)
p.add_argument('--prefer', default=None, help='procedure value to prefer, e.g. CREW-CREATE')
a = p.parse_args()

spool = Path(a.spool)
taken = spool / '.taken' / a.crew
trash = spool / '.trash'
taken.mkdir(parents=True, exist_ok=True)
cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                       time.gmtime(time.time() - QUEUED_TTL_S))

def procedure(cand):
    try:
        return json.loads(cand.read_text()).get('procedure', '')
    except Exception:
        return ''

cands = sorted(spool.glob('slot-*.json'))
if a.prefer:
    pref = [c for c in cands if procedure(c) == a.prefer]
    rest = [c for c in cands if procedure(c) != a.prefer]
    cands = pref + rest

for cand in cands:
    dest = taken / cand.name
    try:
        os.rename(cand, dest)
    except OSError:
        continue
    con = ledger.connect(None)
    try:
        cur = con.execute(
            "UPDATE slots SET state='claimed', claimed_at=?, crew=? "
            "WHERE path=? AND state='queued' AND created_at >= ?",
            (ledger.now(), a.crew, str(cand), cutoff))
        con.commit()
        ok = cur.rowcount == 1
    finally:
        con.close()
    if not ok:
        trash.mkdir(parents=True, exist_ok=True)
        os.rename(dest, trash / dest.name)
        continue
    try:
        print(dest)
        print(json.dumps(json.loads(dest.read_text()), indent=1))
        sys.exit(0)
    except Exception:
        continue
print('EMPTY')
