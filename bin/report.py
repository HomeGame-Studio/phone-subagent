#!/usr/bin/env python3
"""Report a finished slot: --outcome done|failed|skipped (+ --strike-class
for failed) or record a standalone strike. Bool --ok still accepted."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_subagent import claim
p = argparse.ArgumentParser()
p.add_argument('--slot'); p.add_argument('--crew', default='unknown')
p.add_argument('--outcome', choices=('done', 'failed', 'skipped'))
p.add_argument('--ok', type=int, choices=(0, 1), default=None,
               help='legacy bool form: 1=done 0=failed')
p.add_argument('--line', required=True)
p.add_argument('--strike-class', default=None,
               help='with --outcome failed: apply one strike of this class')
p.add_argument('--strike', help='standalone strike: device id')
p.add_argument('--db', default=None)
a = p.parse_args()
outcome = a.outcome or ({1: 'done', 0: 'failed'}.get(a.ok) if a.ok is not None else None)
if a.slot and outcome:
    strike = {'class': a.strike_class, 'note': a.line} if (
        outcome == 'failed' and a.strike_class) else None
    print(claim.finish(a.slot, a.crew, outcome, a.line, strike=strike, path=a.db))
elif a.strike:
    claim.strike(a.strike, a.line, path=a.db); print(f'strike recorded: {a.strike}')
else:
    p.error('need --slot+--outcome+--line (optionally --strike-class), '
            'or --strike+--line')
