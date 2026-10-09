"""Centralized screenshot + decision store.

Every vision-loop step writes its screenshot and the model's decision to a
shared root (local dir or a NAS mount — anything filesystem-shaped):

  {root}/{device}/{YYYY-MM-DD}/{flow}-{step:03d}.png
  {root}/{device}/{YYYY-MM-DD}/{flow}-{step:03d}.json   (goal, action, thought)

Wire it via the vision driver's on_step callback:

    drive(goal, see, act, vision, on_step=store.make_sink(device, flow))

Set the root with the SCREEN_STORE env var (default ./screenstore).
Prune old days with `python3 -m phone_subagent.store --prune-days 14`.

The store is the fleet's shared visual memory: "what did this phone's screen
look like when it failed" is a file lookup, and future crews can few-shot
from real screens ("here is the code screen from last week").
"""
import json, os, time
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('SCREEN_STORE',
                        str(Path(__file__).resolve().parents[1] / 'screenstore')))

LOCAL_FALLBACK_ROOT = Path('/home/adbserver/farm/screenstore-local')

def make_sink(device, flow, root=None):
    """Return an on_step(step, decision, screenshot_bytes) for vision_driver.drive."""
    root = Path(root or DEFAULT_ROOT)
    day = time.strftime('%Y-%m-%d')
    out = root / device / day
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        # dead NAS / soft-cifs mount ("Host is down"): never block the drive
        # loop — keep the audit trail in a local fallback root instead.
        out = LOCAL_FALLBACK_ROOT / device / day
        out.mkdir(parents=True, exist_ok=True)
    def sink(step, decision, shot: bytes):
        base = out / f'{flow}-{step:03d}'
        try:
            base.with_suffix('.png').write_bytes(shot)
            base.with_suffix('.json').write_text(json.dumps({
                'device': device, 'flow': flow, 'step': step, 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'action': decision.get('action'), 'args': decision.get('args', {}),
                'thought': decision.get('thought', '')}, indent=1))
        except OSError:
            pass  # store is best-effort: never block the drive loop
    return sink

def recent(device, days=7, root=None):
    """Latest N decisions for a device — for slot injection / crew context."""
    root = Path(root or DEFAULT_ROOT) / device
    out = []
    if not root.exists(): return out
    for day_dir in sorted(root.iterdir())[-days:]:
        for j in sorted(day_dir.glob('*.json')):
            try: out.append(json.loads(j.read_text()))
            except Exception: pass
    return out

def prune(days=14, root=None):
    root = Path(root or DEFAULT_ROOT)
    if not root.exists(): return 0
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - days * 86400))
    n = 0
    for day_dir in root.rglob('*'):
        if day_dir.is_dir() and day_dir.name.isdigit() is False and len(day_dir.name) == 10:
            if day_dir.name < cutoff:
                for f in day_dir.iterdir(): f.unlink(missing_ok=True); n += 1
                day_dir.rmdir()
    return n

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prune-days', type=int, default=None)
    a = p.parse_args()
    if a.prune_days:
        print(f'pruned {prune(a.prune_days)} files older than {a.prune_days}d')
    else:
        print(f'store root: {DEFAULT_ROOT}')
