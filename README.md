# phone-subagent

> Orchestrate work across a fleet of remote devices with standing AI-agent crews — one device at a time

**phone-subagent** coordinates dozens or hundreds of remotely-controlled devices (phones, test rigs, anything scriptable) by giving each one to a long-lived AI agent worker, one at a time. A dumb, transactional dispatcher hands out self-contained work orders; smart crews do the judgment work; the ledger guarantees nothing gets double-spent — even across crashes.

## Why

Auto-spawners throw a fresh agent at every task and lose all context on failure. **phone-subagent** keeps a small pool of *standing crews* that learn the procedure and its screens, adapt inside a device when something surprising happens, and escalate cleanly when they're stuck.

Three rules keep it honest:

- **One device, one lock, one crew** — per-device flock files referee execution, the SQLite ledger referees claims
- **Scripts do plumbing, crews do judgment** — repeatable setup stays in deterministic code; agents only drive UI flows, read screens, and decide when a device is unhealthy
- **Two-strike rule** — a crew tries a device twice, then parks it in the repair queue and moves on

## How it works

```
                ┌─────────────────────────────┐
  dispatcher ──▶│ spool/slot-<device>.json    │──▶ crew #1 (takes slot, drives device)
  (dumb, txn)   │ device + task + creds +     │──▶ crew #2
                │ procedure reference         │──▶ crew #3 ...
                └─────────────────────────────┘
                       ▲              │
                reaper ┘              └── one-line result + release
```

1. The **dispatcher** (plain Python, no AI) picks an idle device and claims one unit of consumable input — device, input, and slot published together in a single SQLite transaction
2. A **crew** atomically takes the slot (rename = claim), locks the device, drives it end-to-end following the slot's procedure, and reports one audit line
3. The **reaper** recovers crashes: stuck slots go to the repair queue, ambiguous reservations go to `outcome_unknown` — never silently back to available

> [!IMPORTANT]
> Inputs move through explicit states — `available → reserved → spent`, with `outcome_unknown` for ambiguous mid-task deaths. An input can never be double-spent by a crash or a retry; a human or AI reconciles the unknowns.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/HomeGame-Studio/phone-subagent.git
cd phone-subagent
python3 -m phone_subagent.ledger init            # create the ledger
python3 bin/dispatcher.py devices dev-A dev-B dev-C
python3 bin/dispatcher.py inputs --kind creds inputs.jsonl
python3 bin/dispatcher.py dispatch --count 3 --procedure procedures/signup.md
```

Then run a crew — an AI agent session pointed at [`CREW.md`](CREW.md), or a plain script using the same helpers:

```bash
python3 bin/take.py --crew crew-1       # -> slot path + JSON, or EMPTY
python3 bin/lock.py --device dev-A -- <any command touching the device>
python3 bin/report.py --slot <path> --ok 1 --line "dev-A: CREATED"
```

## Layout

| Path | What it does |
|---|---|
| `phone_subagent/ledger.py` | SQLite schema + transactional claim/release/finish |
| `phone_subagent/claim.py` | Crew-side slot take (atomic rename) + result logging |
| `phone_subagent/lock.py` | Per-device flock (context manager or CLI) |
| `phone_subagent/reaper.py` | Crash recovery + `reconcile()` for unknown outcomes |
| `phone_subagent/vision_driver.py` | Zero-determinism driver: a multimodal model decides every action from the current screenshot |
| `bin/dispatcher.py` | CLI: init, register devices, add inputs, dispatch |
| `bin/take.py` / `bin/report.py` / `bin/lock.py` | Crew CLIs |
| `CREW.md` | The standing-worker brief your AI crews follow |

## Design notes

**Why SQLite for claims?** Filesystem renames are atomic for one object — but a dispatch touches three (device availability, input ownership, slot publication). One `BEGIN IMMEDIATE` transaction makes the whole claim all-or-nothing, closing every crash window between them.

**Why standing crews instead of fresh spawns?** A crew that has driven fifty devices through a procedure knows its screens, its failure modes, and its workarounds. Fresh spawns pay that learning cost on every task.

**Why `outcome_unknown` instead of auto-release?** If a worker dies mid-task, the remote system may already have consumed the input. Silently returning it to the pool double-spends; parking it for reconciliation spends a moment of judgment instead. `reaper.reconcile(input_id, spent=True/False)` closes the loop.

**Why one device per crew?** Determinism and auditability: every action in a device's log comes from one driver, contention is impossible by construction, and "screenshot first" debugging actually works.

## Zero-determinism driving

"By hand" here means **a multimodal model decides every action from the current screenshot** — no fixed coordinates, no UI-tree parsing, no memorized step sequences. `phone_subagent/vision_driver.py` implements the loop:

```
screenshot → model(goal + history + what it sees) → ONE action → repeat
```

The model's entire action space is `tap / type / key / swipe / wait / done / stuck`. Procedures become goals ("create an account, pfp set, city set, proofs captured"), not scripts. Landscape phones, moved buttons, surprise dialogs — all just pictures it reads.

```python
from phone_subagent.vision_driver import drive
result = drive(goal, see=screencap_via_adb, act=run_via_adb, vision=my_model)
```

### Why it's accurate (it's not pixel perfection)

Vision models misread small text and tap 50px off all the time. The accuracy comes from the **closed loop**: the model looks *after* every action, so a misread self-corrects on the next screenshot. A script that taps wrong never knows it tapped wrong.

### When to use scripts instead

| Environment | Right call |
|---|---|
| UI mutates weekly | **Pure vision** — robustness beats speed; script maintenance costs more than latency |
| UI stable for months | Hybrid — scripted fast path, vision tripwire on any surprise |
| Unknown | Start pure vision, measure, add scripts for the stable 90% only if throughput hurts |

The driver supports both: nothing stops a crew from calling a deterministic helper *inside* the loop — but the goal, the verification, and every recovery decision stay vision-driven.

## License

MIT
