# phone-subagent

> Orchestrate a fleet of remote devices with standing AI-agent crews — a multimodal model drives every action, one device at a time

**phone-subagent** runs work across dozens or hundreds of remotely-controlled devices (phones, test rigs, anything with a scriptable transport). A dumb, transactional dispatcher hands out self-contained work orders. Long-lived agent crews take them one at a time and drive each device **by eye** — a vision model decides every tap from the current screenshot, with zero scripted UI automation. A SQLite ledger guarantees claims and consumables are never double-spent, even across crashes.

## Why

Auto-spawners throw a fresh agent at every task and lose all context on failure. Scripted UI automation breaks the moment a screen moves, rotates, updates, or shows an unexpected dialog. **phone-subagent** takes a different position on both:

- **Standing crews** stay alive across tasks — they learn the procedure, its screens, and its failure modes instead of paying cold-start every time
- **Zero-determinism driving** — a multimodal model looks at the screen before every action. Landscape phones, moved buttons, surprise popups: all just pictures it reads

Three rules keep it honest:

1. **One device, one lock, one crew** — per-device flock files referee execution, the ledger referees claims
2. **Scripts do plumbing, crews do judgment** — repeatable setup (installs, config delivery) stays in deterministic code; agents only drive UI flows, read screens, and decide when a device is unhealthy
3. **Two-strike rule** — a crew tries a device twice, then parks it in the repair queue and moves on

## How it works

```
                ┌─────────────────────────────┐
  dispatcher ──▶│ spool/slot-<device>.json    │──▶ crew #1 ─┐
  (dumb, txn)   │ device + task + creds +     │──▶ crew #2  │  vision loop:
                │ procedure reference         │──▶ crew #3 ─┘  look → decide → act
                └─────────────────────────────┘                     │
                       ▲              ▲                              │
                 reaper ┘              └── one-line result + release ─┘
                                          every step screenshotted → store
```

1. The **dispatcher** (plain Python, no AI) picks an idle device and claims one unit of consumable input — device, input, and slot published together in a single SQLite transaction
2. A **crew** atomically takes the slot (rename = claim), locks the device, drives it end-to-end through the vision loop, and reports one audit line
3. The **reaper** recovers crashes with a split by slot state: **claimed**-past-TTL (a crew died holding it) parks the device in repair and the input in `outcome_unknown`; **queued**-past-TTL (starvation — the slot never ran) releases the device to idle and the input to `available`. Backlog is not a device fault. Orphan spool files move to `.trash/` — never re-consumed

## Quick start

Requires Python 3.10+, stdlib only.

```bash
git clone https://github.com/HomeGame-Studio/phone-subagent.git
cd phone-subagent
python3 -m phone_subagent.ledger init                    # create the ledger
python3 bin/dispatcher.py devices dev-A dev-B dev-C       # register devices
printf '{"user":"u1","pass":"p1"}\n' > inputs.jsonl       # one consumable per line
python3 bin/dispatcher.py inputs --kind creds inputs.jsonl
# consuming flow: each slot burns one 'creds' input (kind-matched — a slot
# can never grab an input of another kind)
python3 bin/dispatcher.py dispatch --count 3 --procedure procedures/signup.md --input-kind creds
# inputless flow (e.g. warming): omit --input-kind entirely
python3 bin/dispatcher.py dispatch --count 3 --procedure procedures/warm.md
```

Then run a crew — an AI agent session pointed at [`CREW.md`](CREW.md), or a plain script using the same helpers:

```bash
python3 bin/take.py --crew crew-1       # -> slot path + JSON, or EMPTY
                                      # (a claim is conditional: queued AND
                                      #  not expired — a lost claim is never driven)
python3 bin/lock.py --device dev-A -- <any command touching the device>
python3 bin/report.py --slot <path> --outcome done --line "dev-A: CREATED"
# failure with a strike (two strikes park the device in repair):
python3 bin/report.py --slot <path> --outcome failed --strike-class infra-adb --line "dev-A: adb gone"
# cancellation / busy device: releases without striking
python3 bin/report.py --slot <path> --outcome skipped --line "dev-A: SKIP: lock busy"
```

## Zero-determinism driving

"By hand" means **a multimodal model decides every action from the current screenshot** — no fixed coordinates, no UI-tree parsing, no memorized step sequences. `phone_subagent/vision_driver.py` implements the loop:

```
screenshot → model(goal + history + what it sees) → ONE action → repeat
```

The model's entire action space is `tap / type / key / swipe / wait / done / stuck`. Procedures become goals ("create an account, pfp set, proofs captured"), not scripts.

```python
from phone_subagent.vision_driver import drive

result = drive(goal, see=screencap, act=run_input, vision=my_model,
              max_steps=200, max_seconds=15*60)   # wall-clock budget too
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

## Visual memory

Every vision-loop step saves its screenshot and the model's decision to a shared store:

```
{SCREEN_STORE}/{device}/{YYYY-MM-DD}/{flow}-{step:03d}.png
                                      {flow}-{step:03d}.json   # action, args, thought, ts
```

Point `SCREEN_STORE` at any filesystem-shaped target — a local dir or a NAS mount. This is the fleet's shared visual memory: "what did this phone's screen look like when it failed" is a file lookup, and vision prompts can few-shot from real screens.

```python
from phone_subagent import store

result = drive(goal, see, act, vision, on_step=store.make_sink(device, flow))
store.recent(device)                    # a phone's last decisions — slot injection / crew context
```

Prune with `python3 -m phone_subagent.store --prune-days 14`.

## The ledger

Claims are **transactional** (device + input + slot in one `BEGIN IMMEDIATE`) and inputs move through explicit states — never silently back to available:

```
available → reserved(job) → spent
                    ↘ outcome_unknown   (ambiguous crash — reconcile decides)
```

`reaper.reconcile(input_id, spent=True)` closes the loop on unknowns; `reaper.reap()` splits expiry by slot state (claimed → repair + `outcome_unknown`; queued → idle + `available`) and trashes orphan spool files. Every slot gets a one-line result, so the `results` table is the complete audit trail.

**Outcomes and strikes.** `finish(slot, crew, outcome, line, strike=...)` settles a slot exactly once: `done` spends the input and resets strikes, `failed` parks the input in `outcome_unknown` (optionally recording one strike — class-tagged, applied atomically with the terminal transition), `skipped` releases without striking. Strikes are event rows (`ledger.reverse_strikes` reverses a class/window — a farm-wide outage must not park healthy devices as repairs), and repair is only ever cleared by reconcile/reversal, never by finish.

## Layout

| Path | What it does |
|---|---|
| `phone_subagent/ledger.py` | SQLite schema + transactional claim/dispatch/finish + strike rows |
| `phone_subagent/claim.py` | Crew-side conditional slot take (rename + ledger confirm) |
| `phone_subagent/lock.py` | Per-device flock (context manager or CLI; O_CLOEXEC, configurable dir) |
| `phone_subagent/reaper.py` | Crash recovery (claimed/queued split + spool reconciliation) + `reconcile()` |
| `phone_subagent/vision_driver.py` | Zero-determinism driver: the look-decide-act loop |
| `phone_subagent/store.py` | Centralized screenshot + decision store (NAS-ready) |
| `bin/dispatcher.py` | CLI: init, register devices, add inputs, dispatch |
| `bin/take.py` / `bin/report.py` / `bin/lock.py` | Crew CLIs |
| `CREW.md` | The standing-worker brief your AI crews follow |

## Design notes

**Why SQLite for claims?** Filesystem renames are atomic for one object — but a dispatch touches three (device availability, input ownership, slot publication). One transaction makes the whole claim all-or-nothing, closing every crash window between them.

**Why standing crews instead of fresh spawns?** A crew that has driven fifty devices through a procedure knows its screens, its failure modes, and its workarounds. Fresh spawns pay that learning cost on every task.

**Why `outcome_unknown` instead of auto-release?** If a worker dies mid-task, the remote system may already have consumed the input. Silently returning it to the pool double-spends; parking it for reconciliation spends a moment of judgment instead.

**Why one device per crew?** Determinism and auditability: every action in a device's log comes from one driver, contention is impossible by construction, and "screenshot first" debugging actually works — with the store, retroactively too.

## License

MIT
