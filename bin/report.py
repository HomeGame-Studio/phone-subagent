#!/usr/bin/env python3
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_subagent import claim
p = argparse.ArgumentParser()
p.add_argument('--slot'); p.add_argument('--crew', default='unknown')
p.add_argument('--ok', type=int, choices=(0,1)); p.add_argument('--line', required=True)
g = p.add_mutually_exclusive_group(); g.add_argument('--strike'); g.add_argument('--slot2', dest='slot')
a = p.parse_args()
if a.slot and a.ok is not None:
    print(claim.finish(a.slot, a.crew, bool(a.ok), a.line))
elif a.strike:
    claim.strike(a.strike, a.line); print(f'strike recorded: {a.strike}')
else:
    p.error('need --slot+--ok+--line, or --strike+--line')
