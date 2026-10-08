"""phone-subagent ledger: SQLite claims for devices, inputs, slots, results.

One transaction reserves (device, input, slot) atomically — the fix for the
'three independent resources, not one atomic workflow' failure mode.

Phase-0 hardening (from C2's adversarial review rounds):

- dispatch() takes an optional ``input_kind``; inputs are optional
  (``input_kind=None`` — flows without consumables). A kind is REQUIRED
  when an input is requested, so a warm slot can never eat a gmail.
- slot identity is a uuid (file name + row) — survives ledger recreation
  and is safe to reference from external event stores.
- finish() takes ``outcome`` done|failed|skipped plus a failure-class;
  the optional strike is applied ATOMICALLY with the slot's terminal
  transition (exactly-once), strikes are event rows (reversible,
  auditable, never a blind counter reset), and repair is never cleared
  by finish — only reconcile()/reverse_strikes() clear it.
- take() (claim.py) claims conditionally: queued AND not expired.
"""
import json, sqlite3, time, uuid as _uuid
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / 'ledger.db'

TERMINAL = ('done', 'failed', 'skipped')   # slot terminal states

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL DEFAULT 'idle',      -- idle|busy|flagged|repair
  strikes INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE TABLE IF NOT EXISTS inputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
    payload TEXT NOT NULL,                  -- JSON blob (credentials etc.)
  state TEXT NOT NULL DEFAULT 'available' -- available|reserved|spent|outcome_unknown|contaminated
);
CREATE TABLE IF NOT EXISTS slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid TEXT,
  path TEXT UNIQUE NOT NULL,
  device TEXT NOT NULL REFERENCES devices(id),
  input_id INTEGER REFERENCES inputs(id),
  state TEXT NOT NULL DEFAULT 'queued',  -- queued|claimed|done|failed|skipped
  created_at TEXT NOT NULL,
  claimed_at TEXT, crew TEXT, finished_at TEXT,
  class TEXT                              -- failure/outcome class (audit)
);
CREATE UNIQUE INDEX IF NOT EXISTS slots_uuid ON slots(uuid);
CREATE TABLE IF NOT EXISTS strikes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device TEXT NOT NULL,
  slot TEXT,                              -- slot uuid the strike came from
  class TEXT NOT NULL,                    -- infra-adb|walk-failed|deadline|...
  note TEXT,
  ts TEXT NOT NULL,
  reversed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS results (
  slot_id INTEGER PRIMARY KEY REFERENCES slots(id),
  device TEXT NOT NULL,
  ok INTEGER NOT NULL,
  line TEXT NOT NULL,                    -- one audit line
  ts TEXT NOT NULL
);
"""


def now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    con.execute('PRAGMA journal_mode=WAL')
    return con


def _migrate(con):
    """Bring pre-phase0 ledgers up to the current schema (idempotent)."""
    cols = {r[1] for r in con.execute('PRAGMA table_info(slots)')}
    if cols:
        if 'uuid' not in cols:
            con.execute("ALTER TABLE slots ADD COLUMN uuid TEXT")
        if 'class' not in cols:
            con.execute("ALTER TABLE slots ADD COLUMN class TEXT")
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS slots_uuid ON slots(uuid)')
    con.execute("""CREATE TABLE IF NOT EXISTS strikes (
      id INTEGER PRIMARY KEY AUTOINCREMENT, device TEXT NOT NULL, slot TEXT,
      class TEXT NOT NULL, note TEXT, ts TEXT NOT NULL,
      reversed INTEGER NOT NULL DEFAULT 0)""")
    con.commit()


def init(path=None):
    con = connect(path)
    con.executescript(SCHEMA)
    _migrate(con)
    con.commit()
    con.close()


def register_devices(ids, path=None):
    con = connect(path)
    with con:
        for d in ids:
            con.execute('INSERT OR IGNORE INTO devices (id) VALUES (?)', (d,))
    con.close()


def add_inputs(kind, payloads, path=None):
    con = connect(path)
    with con:
        con.executemany('INSERT INTO inputs (kind, payload) VALUES (?, ?)',
                        [(kind, json.dumps(p)) for p in payloads])
    con.close()


def dispatch(device_id, procedure, task_params, spool_dir, input_kind=None,
            path=None):
    """THE transaction: reserve device + input + publish slot, all-or-nothing.

    ``input_kind=None`` dispatches without consuming any input (flows with
    no consumables). With a kind, only inputs of that kind are eligible —
    a wrong-kind input can never be burned by an unrelated flow.

    Returns the slot path, or None with a reason string.
    """
    spool_dir = Path(spool_dir); spool_dir.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute(
            "SELECT id, state FROM devices WHERE id=? AND state='idle'", (device_id,)).fetchone()
        if not row:
            con.rollback(); return None, f'{device_id}: not idle'
        inp = None
        if input_kind is not None:
            inp = con.execute(
                "SELECT id, payload FROM inputs WHERE state='available' AND kind=? "
                "ORDER BY id LIMIT 1", (input_kind,)).fetchone()
            if not inp:
                con.rollback(); return None, f'no available inputs of kind {input_kind!r}'
        slot_uuid = _uuid.uuid4().hex[:12]
        slot_path = spool_dir / f'slot-{device_id}-{slot_uuid}.json'
        con.execute("UPDATE devices SET state='busy' WHERE id=?", (device_id,))
        if inp:
            con.execute("UPDATE inputs SET state='reserved' WHERE id=?", (inp['id'],))
        cur = con.execute(
            "INSERT INTO slots (uuid, path, device, input_id, state, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (slot_uuid, str(slot_path), device_id, inp['id'] if inp else None,
             'queued', now()))
        con.commit()
        slot = {'slot': slot_uuid, 'device': device_id,
                'input_id': inp['id'] if inp else None,
                'input': json.loads(inp['payload']) if inp else None,
                'procedure': procedure, 'task': task_params}
        slot_path.write_text(json.dumps(slot, indent=1))
        return slot_path, 'ok'
    except Exception:
        con.rollback(); raise
    finally:
        con.close()


def _find_slot(con, slot_ref):
    """By uuid, or by file basename (crews move files into .taken/)."""
    s = con.execute('SELECT * FROM slots WHERE uuid=?', (str(slot_ref),)).fetchone()
    if not s:
        s = con.execute('SELECT * FROM slots WHERE path LIKE ?',
                       ('%' + Path(str(slot_ref)).name,)).fetchone()
    return s


def finish_slot(slot_ref, crew, outcome, line, strike=None, path=None):
    """Settle a slot EXACTLY ONCE. ``outcome``: done|failed|skipped (bool ok
    still accepted for older callers). ``strike``: optional
    {'class': ..., 'note': ...} — applied only with failed, atomically with
    the slot's terminal transition.

    Input disposition: done->spent, failed->outcome_unknown, skipped->
    available. Strikes reset only on done. Device release is conditional on
    busy, so repair is never cleared here.
    """
    if isinstance(outcome, bool):
        outcome = 'done' if outcome else 'failed'
    if outcome not in TERMINAL:
        return f'bad outcome {outcome!r}'
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        s = _find_slot(con, slot_ref)
        if not s:
            con.rollback(); return f'unknown slot {slot_ref}'
        if s['state'] in TERMINAL:
            con.rollback(); return f'slot {slot_ref} already finished ({s["state"]})'
        cur = con.execute(
            "UPDATE slots SET state=?, crew=?, finished_at=?, class=? WHERE id=? "
            "AND state IN ('queued','claimed')",
            (outcome, crew, now(),
             (strike or {}).get('class') if outcome == 'failed' else outcome,
             s['id']))
        if cur.rowcount != 1:            # lost the race to reap/cancel
            con.rollback(); return f'slot {slot_ref} already finished'
        if s['input_id']:
            if outcome == 'done':
                con.execute("UPDATE inputs SET state='spent' WHERE id=?", (s['input_id'],))
            elif outcome == 'failed':
                # ambiguous: the remote may or may not have consumed it
                con.execute("UPDATE inputs SET state='outcome_unknown' WHERE id=?", (s['input_id'],))
            else:
                con.execute("UPDATE inputs SET state='available' WHERE id=?", (s['input_id'],))
        if outcome == 'done':
            con.execute("UPDATE devices SET state='idle', strikes=0 "
                        "WHERE id=? AND state='busy'", (s['device'],))
        else:
            if outcome == 'failed' and strike:
                con.execute('INSERT INTO strikes (device, slot, class, note, ts) '
                            'VALUES (?,?,?,?,?)',
                            (s['device'], s['uuid'], strike.get('class', 'unknown'),
                             strike.get('note', line), now()))
                _recount_strikes(con, s['device'])
            con.execute("UPDATE devices SET state='idle' WHERE id=? AND state='busy'",
                       (s['device'],))
        con.execute("INSERT OR REPLACE INTO results (slot_id, device, ok, line, ts) "
                    "VALUES (?,?,?,?,?)",
                    (s['id'], s['device'], 1 if outcome == 'done' else 0, line, now()))
        con.commit()
        return 'ok'
    finally:
        con.close()


def _recount_strikes(con, device_id, max_strikes=2):
    n = con.execute('SELECT COUNT(*) c FROM strikes WHERE device=? AND reversed=0',
                   (device_id,)).fetchone()['c']
    con.execute('UPDATE devices SET strikes=? WHERE id=?', (n, device_id))
    if n >= max_strikes:
        con.execute("UPDATE devices SET state='repair' WHERE id=? AND state != 'repair'",
                    (device_id,))


def strike(device_id, note, max_strikes=2, path=None, cls='manual'):
    """Record a strike (standalone path; finish(failed, strike=...) is the
    preferred atomic route). Two strikes park the device in repair."""
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT INTO strikes (device, class, note, ts) VALUES (?,?,?,?)',
                    (device_id, cls, note, now()))
        _recount_strikes(con, device_id, max_strikes)
        con.commit()
    finally:
        con.close()


def reverse_strikes(device_id, cls=None, since=None, path=None):
    """Reverse strike rows (e.g. a farm-wide outage must not park healthy
    devices). Per-row and idempotent; a device parked only by reversed
    strikes is un-parked. Returns the number reversed."""
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        q = 'SELECT id FROM strikes WHERE device=? AND reversed=0'
        args = [device_id]
        if cls:
            q += ' AND class=?'; args.append(cls)
        if since:
            q += ' AND ts >= ?'; args.append(since)
        rows = con.execute(q, args).fetchall()
        for r in rows:
            con.execute('UPDATE strikes SET reversed=1 WHERE id=?', (r['id'],))
        _recount_strikes(con, device_id, 10**9)   # re-derive; never parks here
        left = con.execute('SELECT COUNT(*) c FROM strikes WHERE device=? AND reversed=0',
                           (device_id,)).fetchone()['c']
        if not rows and left == 0:
            # nothing to reverse and no live strikes: leave repair alone
            # unless it was parked purely by the strikes we just reversed
            pass
        if rows and left == 0:
            con.execute("UPDATE devices SET state='idle' WHERE id=? AND state='repair'",
                       (device_id,))
        con.commit()
        return len(rows)
    finally:
        con.close()

def contaminate(input_id, reason, path=None):
    """Worker-discovered bad input (dupe in the wild, code suppression).
    Terminal state — never returns to available."""
    con = connect(path)
    with con:
        con.execute("UPDATE inputs SET state='contaminated' WHERE id=? AND state IN ('available','outcome_unknown')",
                    (input_id,))

def sweep_contaminated(values, path=None):
    """Pre-issue sweep: bulk-contaminate available inputs whose payload
    references any of the given values (e.g. every email the ledger ever
    used + the dupe-event list). Returns how many were moved."""
    con = connect(path)
    moved = 0
    with con:
        for row in con.execute("SELECT id, payload FROM inputs WHERE state='available'"):
            try: blob = json.dumps(json.loads(row['payload']))
            except Exception: continue
            if any(str(v) in blob for v in values):
                con.execute("UPDATE inputs SET state='contaminated' WHERE id=?", (row['id'],))
                moved += 1
    con.close()
    return moved
