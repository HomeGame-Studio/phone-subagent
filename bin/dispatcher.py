#!/usr/bin/env python3
"""Dispatcher CLI: pick idle devices, reserve inputs, publish slots."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_subagent import ledger

def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('init')
    d = sub.add_parser('devices'); d.add_argument('ids', nargs='+')
    i = sub.add_parser('inputs'); i.add_argument('--kind', required=True); i.add_argument('json_file')
    s = sub.add_parser('dispatch')
    s.add_argument('--count', type=int, default=1)
    s.add_argument('--procedure', required=True)
    s.add_argument('--spool', default=str(Path(__file__).resolve().parents[1] / 'spool'))
    s.add_argument('--task', default='{}', help='task params JSON')
    r = sub.add_parser('idle'); a = p.parse_args()

    if a.cmd == 'init':
        ledger.init(); print('ledger ready')
    elif a.cmd == 'devices':
        ledger.register_devices(a.ids); print(f'{len(a.ids)} devices registered')
    elif a.cmd == 'inputs':
        payloads = [json.loads(l) for l in Path(a.json_file).read_text().splitlines() if l.strip()]
        ledger.add_inputs(a.kind, payloads); print(f'{len(payloads)} inputs added')
    elif a.cmd == 'dispatch':
        con = ledger.connect()
        idle = [r['id'] for r in con.execute("SELECT id FROM devices WHERE state='idle' ORDER BY RANDOM()")]
        con.close()
        made = 0
        for dev in idle[:a.count]:
            slot, why = ledger.dispatch(dev, a.procedure, a.task, a.spool)
            print(f'{dev}: {why}' if slot is None else f'{dev}: {slot}')
            made += bool(slot)
        print(f'dispatched {made}/{a.count}')
    elif a.cmd == 'idle':
        con = ledger.connect()
        for row in con.execute("SELECT id, state, strikes, note FROM devices"):
            print(dict(row))
        con.close()

import json  # used by inputs subcommand
if __name__ == '__main__':
    main()
