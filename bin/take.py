#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phone_subagent import claim
p = argparse.ArgumentParser()
p.add_argument('--crew', required=True)
a = p.parse_args()
slot, data = claim.take(a.crew)
if slot is None:
    print('EMPTY')
else:
    print(slot); print(json.dumps(data, indent=1))
