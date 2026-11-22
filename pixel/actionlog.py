"""SQLite action log: one row per run, one row per step.

Lives outside OneDrive by default (%LOCALAPPDATA%\\pixel\\pixel.db): it holds
screen text, and OneDrive locks files it syncs. Stores the pruned tree text the
model saw and the tool arguments, never the raw UIA tree or the full prompt.
Blocked card or ID text is redacted before it gets here.

Logging never stops a run: on a database error PIXEL warns once and goes on.

Lifecycle, driven by loop.Runner:
    log = ActionLog.open(path)   # or NullLog if the database can't be opened
    log.start(command, ...)      # inserts the runs row, sets log.run_id
    log.step(step=1, ...)        # one steps row per step, committed right away
    log.end(outcome=..., ...)    # fills in the rest of the runs row
    log.close()
Every write commits immediately, so a crash mid-run still leaves the steps
up to that point on disk.

Reading: `pixel log` (cli.cmd_log) uses recent_runs / get_run / run_steps at
the bottom of this file. Any SQLite viewer works too.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Created on first open (IF NOT EXISTS makes it safe to run every time).
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    command TEXT NOT NULL,
    path TEXT NOT NULL,          -- execution path; 'a11y' = accessibility tree + model
    chain TEXT,                  -- providers in order, e.g. 'gemini-lite > groq > ollama'
    outcome TEXT,                -- finish | ask_user | cap | stopped | blocked | error | crashed
    success INTEGER,             -- 1 only for finish
    exit_code INTEGER,
    message TEXT,
    steps INTEGER,
    seconds REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    providers TEXT,              -- JSON {provider: steps answered}
    fallbacks INTEGER,
    confirm INTEGER
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    step INTEGER NOT NULL,
    at TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    attempts TEXT,               -- JSON list of failed or skipped providers this step
    tree_stats TEXT,
    tree_tokens INTEGER,
    tree_text TEXT,              -- the pruned tree the model saw (NULL if store_tree = false)
    tool TEXT,
    args TEXT,                   -- JSON
    status TEXT,                 -- ok | error | rejected | declined | blocked | stop | model_error
    result TEXT,
    model_seconds REAL,
    action_seconds REAL,
    input_tokens INTEGER,
    output_tokens INTEGER
);
CREATE INDEX IF NOT EXISTS steps_by_run ON steps(run_id, step);
"""

# Process exit code -> outcome name stored in runs.outcome (see loop.py EXIT_*).
OUTCOMES = {0: "finish", 1: "error", 2: "ask_user", 3: "cap", 4: "stopped", 5: "blocked"}


def _now() -> str:
    """Local time with UTC offset, e.g. '2026-11-22T14:03:07-05:00'."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(v) -> str | None:
    """JSON text for a column, keeping None as SQL NULL."""
    return None if v is None else json.dumps(v, ensure_ascii=False)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating the folder and tables if needed) the log database."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row   # rows can be read by column name: row["outcome"]
    db.executescript(SCHEMA)
    return db


class NullLog:
    """Used when logging is unavailable (and by tests that don't look at it).

    Same methods as ActionLog, all doing nothing, so the loop never has to
    check whether logging is on.
    """
    path = None
    run_id = None
    ended = False

    def start(self, command: str, *, chain: str = "", confirm: bool = True,
              path: str = "a11y") -> None:
        """No-op."""
        return None

    def step(self, **fields) -> None:
        """No-op."""
        pass

    def end(self, **fields) -> None:
        """Only records that end() was called (the loop uses this to mark crashes)."""
        self.ended = True

    def close(self) -> None:
        """No-op."""
        pass


class ActionLog(NullLog):
    """The real log. Create it with ActionLog.open(...), not the constructor."""

    def __init__(self, path: str | Path, *, store_tree: bool = True, err=None):
        self.path = str(path)
        self.store_tree = store_tree   # False: steps rows get NULL tree_text
        self.err = err or sys.stderr   # where warnings go (tests pass a StringIO)
        self.db = connect(path)
        self.run_id = None             # set by start()
        self.ended = False
        self._warned = False           # warn about write failures only once per run

    @classmethod
    def open(cls, path: str | Path, *, store_tree: bool = True, err=None) -> NullLog:
        """An ActionLog, or a NullLog plus a warning if the database can't be opened."""
        try:
            return cls(path, store_tree=store_tree, err=err)
        except (sqlite3.Error, OSError) as exc:
            print(f"warning: action log unavailable ({path}: {exc}); this run is not logged",
                  file=err or sys.stderr)
            return NullLog()

    def _write(self, sql: str, params: tuple):
        """Run one statement and commit. Returns the cursor, or None on a database error."""
        try:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur
        except sqlite3.Error as exc:
            if not self._warned:
                self._warned = True
                print(f"warning: action log write failed ({exc}); continuing", file=self.err)
            return None

    def start(self, command: str, *, chain: str = "", confirm: bool = True,
              path: str = "a11y") -> int | None:
        """Insert the runs row at the start of a run. Returns its id (also kept as run_id)."""
        cur = self._write("INSERT INTO runs (started_at, command, path, chain, confirm) "
                          "VALUES (?, ?, ?, ?, ?)",
                          (_now(), command, path, chain, int(confirm)))
        self.run_id = cur.lastrowid if cur else None
        return self.run_id

    def step(self, *, step: int, provider=None, model=None, attempts=None, tree_stats=None,
             tree_tokens=None, tree_text=None, tool=None, args=None, status=None, result=None,
             model_seconds=None, action_seconds=None, input_tokens=None,
             output_tokens=None) -> None:
        """Insert one steps row. Fields match the steps table columns.

        Skipped silently if start() failed (no run_id). tree_text is dropped
        when store_tree is False.
        """
        if self.run_id is None:
            return
        self._write(
            "INSERT INTO steps (run_id, step, at, provider, model, attempts, tree_stats, "
            "tree_tokens, tree_text, tool, args, status, result, model_seconds, action_seconds, "
            "input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, step, _now(), provider, model, _json(attempts or None), tree_stats,
             tree_tokens, tree_text if self.store_tree else None, tool, _json(args), status,
             result, model_seconds, action_seconds, input_tokens, output_tokens))

    def end(self, *, outcome: str, exit_code: int | None, message: str, steps: int,
            seconds: float, tokens_in: int = 0, tokens_out: int = 0,
            providers: dict | None = None, fallbacks: int = 0) -> None:
        """Fill in the runs row when the run ends. success = 1 only for "finish"."""
        self.ended = True
        if self.run_id is None:
            return
        self._write(
            "UPDATE runs SET ended_at = ?, outcome = ?, success = ?, exit_code = ?, message = ?, "
            "steps = ?, seconds = ?, tokens_in = ?, tokens_out = ?, providers = ?, fallbacks = ? "
            "WHERE id = ?",
            (_now(), outcome, int(outcome == "finish"), exit_code, message, steps,
             round(seconds, 2), tokens_in, tokens_out, _json(providers or {}), fallbacks,
             self.run_id))

    def close(self) -> None:
        """Close the database connection (errors ignored)."""
        try:
            self.db.close()
        except sqlite3.Error:
            pass


# ---------- reading (pixel log) ----------

def recent_runs(db: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    """The newest `limit` runs, newest first."""
    return db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def get_run(db: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    """One run's row, or None if there is no such id."""
    return db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def run_steps(db: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    """All steps of one run, in order."""
    return db.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY step, id",
                      (run_id,)).fetchall()
