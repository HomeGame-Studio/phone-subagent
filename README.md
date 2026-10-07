# phone-subagent

Orchestrate work across a fleet of remote devices with **standing AI-agent
crews**, one device at a time.

```
             ┌────────────────────────────┐
 dispatcher ─▶│ spool/slot-<device>.json   │◀─ crew N takes (rename = claim)
 (dumb, txn)  │ device + task + creds +    │   drives ONE device end-to-end
              │ procedure reference        │   logs one result line
             └────────────────────────────┘   takes the next slot
```

## The five rules

1. **A dumb dispatcher** (plain script, no AI) picks the next idle device and
   claims one unit of consumable input **in a single SQLite transaction**
   (device idle + input available + slot published — all-or-nothing).
2. **Standing crews** — long-lived agent sessions loop over slots: claim →
   drive one device → log one line → repeat. Long-lived beats fresh-spawned:
   crews learn the screens and stop paying cold-start every task.
3. **One device = one lock = one crew.** Per-device flock files referee
   execution; the SQLite ledger referees claims.
4. **Scripts do plumbing, crews do judgment.** Repeatable setup stays in
   deterministic scripts; crews only drive UI flows, read screens, and
   decide when a device is unhealthy.
5. **Two-strike rule.** A crew tries a device twice, flags it, moves on.
   Flagged devices land in the repair queue.

## Ledger (answers the adversarial reviews)

Inputs move through explicit states — never silently back to available:

```
available → reserved(job) → spent
                    ↘ outcome_unknown   (ambiguous crash — needs reconcile)
```

- Claims are **transactional**: a dispatcher crash can never strand an input
  without a slot, or publish a slot without a reserved input.
- `reaper` returns reservations older than a TTL to `outcome_unknown`, never
  straight to `available` — no double-spend after ambiguous failure.
- `reconcile` is the human/AI decision step: inspect the device, mark the
  input `spent` or `available`.

## Layout

- `phone_subagent/ledger.py` — SQLite schema + transactional claim/release
- `phone_subagent/dispatcher.py` — pick idle device + reserve input + write slot
- `phone_subagent/claim.py` — crew-side slot take/give-back + result logging
- `phone_subagent/lock.py` — per-device flock
- `phone_subagent/reaper.py` — crash recovery + repair queue
- `bin/` — CLI entry points
- `CREW.md` — the standing-crew brief (what an AI worker follows)

## Quick start

```bash
python3 -m phone_subagent.ledger init           # create db
python3 -m phone_subagent.dispatcher --count 5  # dispatch 5 slots
bin/crew-loop.sh                                # run a crew (see CREW.md)
python3 -m phone_subagent.reaper                 # recover crashes
```
