# CREW brief — the standing worker loop

You are one crew of N. You drive ONE device at a time, end to end.

## The loop (repeat until told to stop)

1. **Take a slot**: run `python3 bin/take.py --crew <your-name>`. It prints
   the slot file path + contents, or `EMPTY` if the spool is dry (then wait
   60s and retry).
2. **Lock the device**: `python3 bin/lock.py --device <id> -- <command...>`
   for every command that touches the device. If it says busy, put the
   slot back and take another.
3. **Drive the device** following the slot's `procedure` (a file reference)
   with its `task` params and `input` payload. Screenshot FIRST whenever a UI
   tap does nothing — look at the screen before debugging.
4. **Report one line**: `python3 bin/report.py --slot <path> --ok 1 --line
   "<device>: CREATED"` (or `--ok 0 --line "<device>: FAILED <reason>"`).
5. On a failed attempt, also `python3 bin/report.py --strike <device>
   --note "<reason>"`. Two strikes parks the device in the repair queue —
   do NOT keep trying it.
6. Take the next slot.

## Rules

- One device at a time. Never hold two.
- Never pick your own input/credentials — the slot carries them. If something
  is missing, fail the slot with `--line "FAILED: bad slot"`.
- If the device drops mid-task, that's a failed attempt (strike + report).
- Everything you do must be reconstructable from your one-line reports.
