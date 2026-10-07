"""phone-subagent ledger: SQLite claims for devices, inputs, slots, results.

One transaction reserves (device, input, slot) atomically — the fix for the
'three independent resources, not one atomic workflow' failure mode.
"""
import json, sqlite3, time
from pathlib import Path
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / 'ledger.db'

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
  state TEXT NOT NULL DEFAULT 'available' -- available|reserved|spent|outcome_unknown
);
CREATE TABLE IF NOT EXISTS slots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  device TEXT NOT NULL REFERENCES devices(id),
  input_id INTEGER REFERENCES inputs(id),
  state TEXT NOT NULL DEFAULT 'queued',  -- queued|claimed|done|failed
  created_at TEXT NOT NULL,
  claimed_at TEXT, crew TEXT, finished_at TEXT
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

def init(path=None):
    con = connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()

def register_devices(ids, path=None):
    con = connect(path)
    with con:
        for d in ids:
            con.execute('INSERT OR IGNORE INTO devices (id) VALUES (?)', (d,))

def add_inputs(kind, payloads, path=None):
    con = connect(path)
    with con:
        con.executemany('INSERT INTO inputs (kind, payload) VALUES (?, ?)',
                        [(kind, json.dumps(p)) for p in payloads])

def dispatch(device_id, procedure, task_params, spool_dir, path=None):
    """THE transaction: reserve device + input + publish slot, all-or-nothing.

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
        inp = con.execute(
            "SELECT id, payload FROM inputs WHERE state='available' ORDER BY id LIMIT 1").fetchone()
        if not inp:
            con.rollback(); return None, 'no inputs available'
        slot_path = spool_dir / f'slot-{device_id}-{int(time.time()*1000)}.json'
        con.execute("UPDATE devices SET state='busy' WHERE id=?", (device_id,))
        con.execute("UPDATE inputs SET state='reserved' WHERE id=?", (inp['id'],))
        cur = con.execute(
            "INSERT INTO slots (path, device, input_id, state, created_at) VALUES (?,?,?,?,?)",
            (str(slot_path), device_id, inp['id'], 'queued', now()))
        con.commit()
        slot = {'device': device_id, 'input_id': inp['id'], 'input': json.loads(inp['payload']),
                'procedure': procedure, 'task': task_params}
        slot_path.write_text(json.dumps(slot, indent=1))
        return slot_path, 'ok'
    except Exception:
        con.rollback(); raise
    finally:
        con.close()

def finish_slot(slot_path, crew, ok, line, path=None):
    """Crew reports a finished slot. Input goes to spent (ok) or stays
    reserved-under-outcome-review (not ok) — never silently available."""
    con = connect(path)
    try:
        con.execute('BEGIN IMMEDIATE')
        # crews move slots into .taken/<crew>/ before finishing — match by basename
        s = con.execute('SELECT id, device, input_id, state FROM slots WHERE path LIKE ?',
                        ('%' + Path(str(slot_path)).name,)).fetchone()
        if not s:
            con.rollback(); return f'unknown slot {slot_path}'
        if s['state'] in ('done', 'failed'):
            con.rollback(); return f'slot {slot_path} already finished'
        con.execute("UPDATE slots SET state=?, crew=?, finished_at=? WHERE id=?",
                    ('done' if ok else 'failed', crew, now(), s['id']))
        if ok:
            con.execute("UPDATE inputs SET state='spent' WHERE id=?", (s['input_id'],))
        else:
            # ambiguous: the remote may or may not have consumed it — reconcile decides
            con.execute("UPDATE inputs SET state='outcome_unknown' WHERE id=?", (s['input_id'],))
        con.execute("UPDATE devices SET state='idle', strikes=0 WHERE id=? AND state='busy'", (s['device'],))
        con.execute("INSERT OR REPLACE INTO results (slot_id, device, ok, line, ts) VALUES (?,?,?,?,?)",
                    (s['id'], s['device'], 1 if ok else 0, line, now()))
        con.commit()
        return 'ok'
    finally:
        con.close()

def strike(device_id, note, max_strikes=2, path=None):
    """Two-strike rule: crews call this on a failed attempt."""
    con = connect(path)
    with con:
        con.execute("UPDATE devices SET strikes=strikes+1, note=? WHERE id=?", (note, device_id))
        row = con.execute('SELECT strikes FROM devices WHERE id=?', (device_id,)).fetchone()
        if row['strikes'] >= max_strikes:
            con.execute("UPDATE devices SET state='repair' WHERE id=?", (device_id,))
