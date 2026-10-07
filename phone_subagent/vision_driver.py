"""Zero-determinism device driver: a multimodal model looks at the screen and
decides every action.

No coordinates, no UI-tree parsing, no step scripts. The ONLY primitives
are look / tap / type / key / swipe / wait — and every tap coordinate comes
from the vision model reading the CURRENT screenshot. The procedure is a goal,
not a script.

Wire `see()` and `act()` to your transport (adb, computer-use, whatever);
`see()` must return PNG bytes, `act()` must faithfully execute one action.
"""
import json, time
from typing import Callable

ACTION_SCHEMA = {
    'tap':    {'x': int, 'y': int},
    'swipe':  {'x1': int, 'y1': int, 'x2': int, 'y2': int},
    'type':   {'text': str},
    'key':    {'code': str},      # back / home / enter ...
    'wait':   {'seconds': int},
    'done':   {'summary': str},
    'stuck':  {'summary': str},   # crew gives up on this device
}

PROMPT = """You are driving one remote device toward a goal. You see a
screenshot and what you have done so far. Reply with ONE action as JSON.

Goal: {goal}
Actions so far: {history}

Rules:
- Decide ONLY from what is visible in the screenshot right now.
- Reply with exactly one JSON object: {{"action": "...", "args": {{...}}, "thought": "one short sentence"}}
- Valid actions and args: {actions}
- Use "done" when the goal is achieved (say what proves it).
- Use "stuck" if the screen shows no path forward after what you tried.
- Never invent UI you cannot see. If a tap did nothing, say so in thought
  and try something different."""

def drive(goal: str, see: Callable[[], bytes], act: Callable[[dict], None],
          vision: Callable[[str, bytes], str], max_steps: int = 120,
          on_step=None, device=None, flow=None, max_seconds=None) -> dict:
    """Run the vision loop. Returns {'ok': bool, 'summary': str, 'steps': int}.

    vision(prompt, png_bytes) -> model text (must contain the action JSON).
    on_step(step, action, screenshot) lets callers audit every decision —
    pass store.make_sink(device, flow) to persist the visual memory.
    max_seconds is a wall-clock budget (monotonic clock) alongside
    max_steps — a session should never be only step-bounded.
    """
    deadline = time.monotonic() + max_seconds if max_seconds else None
    history = []
    for step in range(max_steps):
        if deadline is not None and time.monotonic() > deadline:
            return {'ok': False, 'summary': 'max seconds reached', 'steps': step}
        shot = see()
        prompt = PROMPT.format(goal=goal, history=' | '.join(history[-12:]) or 'none',
                              actions=json.dumps({k: str(v) for k, v in ACTION_SCHEMA.items()}))
        raw = vision(prompt, shot)
        try:
            # tolerate code fences around the JSON
            blob = raw[raw.find('{'): raw.rfind('}') + 1]
            decision = json.loads(blob)
        except Exception:
            history.append(f'step {step}: unparseable model reply')
            time.sleep(2); continue
        action = decision.get('action')
        args = decision.get('args', {})
        if not isinstance(args, dict):
            # model sometimes returns a repr/json string for args — best-effort parse
            try:
                import ast
                parsed = json.loads(args) if isinstance(args, str) else None
                if not isinstance(parsed, dict):
                    parsed = ast.literal_eval(args)
                args = parsed if isinstance(parsed, dict) else {}
            except Exception:
                args = {}
        decision['args'] = args
        if on_step: on_step(step, decision, shot)
        if action == 'done':
            return {'ok': True, 'summary': args.get('summary', ''),
                    'steps': step + 1}
        if action == 'stuck':
            return {'ok': False, 'summary': args.get('summary', 'stuck'),
                    'steps': step + 1}
        if action in ACTION_SCHEMA:
            act({'action': action, **args})
            history.append(decision.get('thought', action))
            if action == 'wait':
                time.sleep(args.get('seconds', 2))
        else:
            history.append(f'step {step}: unknown action {action}')
    return {'ok': False, 'summary': 'max steps reached', 'steps': max_steps}
