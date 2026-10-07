# CREW brief — the standing worker loop (zero-determinism mode)

You are one crew of N. You drive ONE device at a time, end to end.
**Every action you take comes from looking at the current screenshot.**
No fixed coordinates, no UI-tree parsing, no memorized step sequences —
a multimodal model decides each tap from what it sees right now.

## The loop (repeat until told to stop)

1. **Take a slot**: `python3 bin/take.py --crew <your-name>` → the slot file
   (device, connection, goal, input payload) or `EMPTY`.
2. **Lock the device**: `python3 bin/lock.py --device <id> -- <command>`.
3. **Drive by eye**: run the vision loop (`phone_subagent/vision_driver.py`):

   ```python
   result = drive(goal=slot['task']['goal'], see=screencap_via_adb,
                  act=run_action_via_adb, vision=your_multimodal_model)
   ```

   - The model sees: current screenshot + goal + what it already did.
   - It answers ONE action: `tap`, `type`, `key`, `swipe`, `wait`, `done`, `stuck`.
   - There is nothing else. The goal (from the slot) is the only instruction.
4. **Report one line**: `python3 bin/report.py --slot <path> --outcome done
   --line "<device>: <result.summary>"`. On a device-attributable failure,
   add `--strike-class infra-adb` (or walk-failed / deadline); two strikes
   park the device in repair. On contention or a stale claim, report
   `--outcome skipped` — release without striking.
5. On failure, `--strike <device>`; two strikes parks it in repair.

## Rules

- One device at a time. Never hold two.
- Never pick your own input/credentials — the slot carries them.
- If the model says `stuck`, that's a failed attempt. Do not hand-hold it
  past what it can see.
- Every decision is auditable: keep the per-step screenshots if you need to
  explain an outcome later.

## Why zero-determinism

Scripted taps broke every time a screen moved, rotated, updated, or showed
an unexpected dialog. A model that looks before every action pays a little
latency and gets correctness for free: landscape phones, moved buttons,
surprise popups, error dialogs — all just pictures it reads.

**When to add scripts back:** only when the UI has been stable for months
AND throughput hurts. Even then, scripts are helpers called from inside the
vision loop — the goal, the verification, and every recovery decision stay
vision-driven. If screens mutate weekly (they do here), pure vision wins on
total cost: every UI break costs hours of script debugging that the vision
loop just reads past.
