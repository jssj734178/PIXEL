"""The SQLite action log, in a temp folder."""

import io
import json
import sqlite3

from pixel.actionlog import ActionLog, NullLog, get_run, recent_runs, run_steps


def test_run_and_steps_round_trip(tmp_path):
    path = tmp_path / "sub" / "pixel.db"          # parent folder is created
    log = ActionLog(path)
    rid = log.start("open Notepad", chain="a > b", confirm=False)
    log.step(step=1, provider="a", model="m", attempts=[], tree_stats="s", tree_tokens=5,
             tree_text="[1] Button 'x'", tool="open_app", args={"name": "notepad"},
             status="ok", result="ok: launched", model_seconds=0.5, action_seconds=0.2,
             input_tokens=10, output_tokens=2)
    log.end(outcome="finish", exit_code=0, message="finish: done", steps=1, seconds=1.234,
            tokens_in=10, tokens_out=2, providers={"a": 1}, fallbacks=0)
    log.close()

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    run = get_run(db, rid)
    assert run["command"] == "open Notepad" and run["path"] == "a11y"
    assert run["outcome"] == "finish" and run["success"] == 1 and run["seconds"] == 1.23
    assert json.loads(run["providers"]) == {"a": 1} and run["confirm"] == 0
    (s,) = run_steps(db, rid)
    assert s["tree_text"] == "[1] Button 'x'" and json.loads(s["args"]) == {"name": "notepad"}
    assert s["attempts"] is None
    assert [r["id"] for r in recent_runs(db)] == [rid]


def test_store_tree_false_keeps_rows_but_no_tree_text(tmp_path):
    log = ActionLog(tmp_path / "p.db", store_tree=False)
    rid = log.start("x")
    log.step(step=1, tree_text="secret window text", tree_stats="stats", status="ok")
    db = sqlite3.connect(tmp_path / "p.db")
    row = db.execute("SELECT tree_text, tree_stats FROM steps WHERE run_id = ?", (rid,)).fetchone()
    assert row == (None, "stats")


def test_unopenable_path_warns_and_returns_a_null_log(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    err = io.StringIO()
    log = ActionLog.open(blocker / "pixel.db", err=err)
    assert isinstance(log, NullLog) and not isinstance(log, ActionLog)
    assert "this run is not logged" in err.getvalue()
    log.start("x")
    log.step(step=1)
    log.end(outcome="finish", exit_code=0, message="", steps=1, seconds=0)


def test_write_failure_warns_once_and_does_not_raise(tmp_path):
    err = io.StringIO()
    log = ActionLog(tmp_path / "p.db", err=err)
    log.start("x")
    log.db.close()                                 # every later write fails
    log.step(step=1)
    log.step(step=2)
    log.end(outcome="finish", exit_code=0, message="", steps=2, seconds=0)
    assert err.getvalue().count("action log write failed") == 1
