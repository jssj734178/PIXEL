"""pixel window | tree | click | type | keys | open | run | log

Also runs as `python -m pixel.cli ...`, which avoids the pixel.exe launcher
(blocked by Application Control on this machine, see QUIRKS.md).

Each subcommand is a cmd_* function, wired up in build_parser() at the bottom.
    window, tree, click, type, keys, open   one bridge tool by hand (M1)
    run                                     the full model loop (loop.Runner)
    log                                     show the SQLite action log
Bridge imports are inside each function so `pixel --help` and `pixel log`
start fast and don't load UI Automation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

# pywinauto 0.6.9 has a docstring escape that warns on Python 3.12+.
warnings.filterwarnings("ignore", category=SyntaxWarning)


def _utf8_stdout() -> None:
    """Print UTF-8 even on a legacy Windows console, replacing anything unprintable.

    Screen text often has characters (…, emoji, accents) the default code page can't show.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _verbose(args, msg: str) -> None:
    """Print a [verbose] line to stderr when -v was given."""
    if args.verbose:
        print(f"[verbose] {msg}", file=sys.stderr)


def _wait(args) -> None:
    """--delay N: sleep N seconds first so you can click into the target window."""
    if getattr(args, "delay", 0):
        print(f"waiting {args.delay} s, switch to the target window…", file=sys.stderr)
        time.sleep(args.delay)


def _result(args, res: dict) -> int:
    """Print a tool result. Exit code 1 on failure."""
    if res.get("ok"):
        _verbose(args, json.dumps({k: v for k, v in res.items() if k not in ("text", "raw")},
                                  ensure_ascii=False))
        return 0
    print(f"error: {res.get('error')}", file=sys.stderr)
    if res.get("tried"):
        _verbose(args, f"tried: {res['tried']}")
    return 1


def cmd_window(args) -> int:
    """pixel window: print the focused window's title, process, pid and handle."""
    from pixel.bridge import get_focused_window
    _wait(args)
    res = get_focused_window()
    if res.get("ok"):
        print(f"{res['title']} ({res['process']}, pid {res['pid']}, handle {res['handle']})")
    return _result(args, res)


def cmd_tree(args) -> int:
    """pixel tree: print a window's pruned tree with [id]s (and save it for click/type).

    --save PATH also writes the raw tree as JSON, the format of tests/fixtures/.
    """
    from pixel.bridge import get_ui_tree
    _wait(args)
    res = get_ui_tree(args.window, max_depth=args.depth, max_elements=args.max,
                      include_raw=bool(args.save))
    if not res.get("ok"):
        return _result(args, res)
    print(res["text"])
    _verbose(args, f"raw nodes {res['raw_nodes']} -> {res['lines']} lines, "
                   f"{res['elements']} with IDs, ~{res['tokens']} tokens (estimate), "
                   f"capture {res['seconds']} s")
    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(res["raw"], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"saved raw tree to {path}", file=sys.stderr)
    return 0


def cmd_click(args) -> int:
    """pixel click ID: activate an element from the last `pixel tree`."""
    from pixel.bridge import click_element
    _wait(args)
    res = click_element(args.id)
    if res.get("ok"):
        print(f"clicked {res['target']} via {res['method']}")
    return _result(args, res)


def cmd_type(args) -> int:
    """pixel type ID TEXT: put text into an element from the last `pixel tree`."""
    from pixel.bridge import type_text
    _wait(args)
    res = type_text(args.id, args.text)
    if res.get("ok"):
        print(f"typed {res['chars']} chars into {res['target']} via {res['method']}")
        if res.get("note"):
            print(f"note: {res['note']}", file=sys.stderr)
    return _result(args, res)


def cmd_keys(args) -> int:
    """pixel keys "ctrl+s": send a shortcut to the window from the last `pixel tree`."""
    from pixel.bridge import send_keys
    _wait(args)
    res = send_keys(args.keys)
    if res.get("ok"):
        print(f"sent {res['sent']}")
    return _result(args, res)


def cmd_open(args) -> int:
    """pixel open NAME: launch or focus an app from [apps.*] in config.toml."""
    from pixel.bridge import open_app
    res = open_app(args.name)
    if res.get("ok"):
        front = "" if res.get("foreground") else " (could not bring it to the front)"
        print(f"{res['action']} '{res['window']}'{front}")
    return _result(args, res)


def cmd_run(args) -> int:
    """pixel run "task": set everything up and hand over to loop.Runner.

    Order: check flags, build the model chain from config (with any
    --simulate-fail wrappers), load the system prompt, start the kill hotkey
    (refusing --no-confirm without it), open the action log, run. The hotkey
    and log are always cleaned up in `finally`, even after a crash.
    """
    from pixel import config
    from pixel.actionlog import ActionLog
    from pixel.killswitch import KillSwitch
    from pixel.loop import Desktop, Runner
    from pixel.models import ModelError, from_config

    run_cfg = config.run()
    confirm = bool(run_cfg["confirm"]) and not args.no_confirm
    if args.max_steps is not None and args.max_steps < 1:
        print("error: --max-steps must be at least 1", file=sys.stderr)
        return 1
    if args.max_steps and args.max_steps > run_cfg["step_cap"]:
        print(f"note: --max-steps can only lower the cap; using {run_cfg['step_cap']}",
              file=sys.stderr)

    missing = [a for a in run_cfg["cloud_apps"] if a not in config.apps()]
    if missing:
        print(f"warning: run.cloud_apps lists apps with no [apps.*] entry: {', '.join(missing)}",
              file=sys.stderr)
    try:
        model = from_config(config.models(), simulate=args.simulate_fail or ())
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    system_prompt = (config.PROJECT_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")

    kill = KillSwitch(run_cfg["kill_hotkey"])
    if not kill.start():
        if not confirm:
            print(f"error: kill hotkey unavailable ({kill.error}); "
                  "refusing to run with --no-confirm", file=sys.stderr)
            return 1
        print(f"warning: kill hotkey unavailable ({kill.error}); confirm mode still asks "
              "before every action", file=sys.stderr)
    log_cfg = config.log()
    log = ActionLog.open(log_cfg["path"], store_tree=bool(log_cfg["store_tree"]))
    try:
        runner = Runner(args.command, model=model, desktop=Desktop(config.apps()),
                        run_cfg=run_cfg, apps=config.apps(), system_prompt=system_prompt,
                        confirm=confirm, max_steps=args.max_steps, verbose=args.verbose,
                        show_prompt=args.show_prompt, kill=kill, log=log,
                        chars_per_token=config.bridge()["chars_per_token"])
        return runner.run()
    finally:
        kill.stop()
        log.close()


def _clip(s, n: int) -> str:
    """Collapse whitespace and cut to n characters, for table columns."""
    s =" ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def cmd_log(args) -> int:
    """pixel log: list recent runs. pixel log ID: one run's steps. --tree adds each tree.

    Opens the database read-only (mode=ro), so viewing can't change or lock it.
    """
    import sqlite3
    from pixel import config
    from pixel import actionlog

    path = Path(config.log()["path"])
    if not path.exists():
        print(f"no action log yet at {path}", file=sys.stderr)
        return 1
    try:
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        print(f"error: cannot open {path}: {exc}", file=sys.stderr)
        return 1
    try:
        # No id: a table of recent runs.
        if args.run_id is None:
            rows = actionlog.recent_runs(db, args.n)
            print(f"{path}")
            print(f"{'id':>4}  {'started':19}  {'outcome':8}  {'steps':>5}  {'secs':>6}  "
                  f"{'fb':>2}  providers / command")
            for r in rows:
                provs = json.loads(r["providers"] or "{}")
                pv = ", ".join(f"{k} {n}" for k, n in provs.items()) or "-"
                print(f"{r['id']:>4}  {(r['started_at'] or '')[:19]:19}  "
                      f"{r['outcome'] or 'running?':8}  {r['steps'] if r['steps'] is not None else '':>5}  "
                      f"{r['seconds'] if r['seconds'] is not None else '':>6}  "
                      f"{r['fallbacks'] if r['fallbacks'] is not None else '':>2}  "
                      f"{pv} | {_clip(r['command'], 60)}")
            return 0
        # An id: the run's summary, then each step with its failed attempts.
        run = actionlog.get_run(db, args.run_id)
        if run is None:
            print(f"error: no run #{args.run_id} in {path}", file=sys.stderr)
            return 1
        print(f"run #{run['id']}  {run['started_at']} -> {run['ended_at'] or '?'}")
        print(f"command  {run['command']}")
        print(f"path {run['path']}  chain {run['chain']}  confirm {'on' if run['confirm'] else 'off'}")
        print(f"outcome  {run['outcome']} (exit {run['exit_code']}): {run['message']}")
        print(f"steps {run['steps']}  {run['seconds']} s  tokens in {run['tokens_in']} "
              f"out {run['tokens_out']}  providers {run['providers']}  fallbacks {run['fallbacks']}")
        for s in actionlog.run_steps(db, args.run_id):
            print(f"\n[{s['step']}] {s['at']}  {s['provider'] or '-'} {s['model'] or ''}  "
                  f"model {s['model_seconds']} s  "
                  f"action {s['action_seconds'] if s['action_seconds'] is not None else '-'} s")
            for a in json.loads(s["attempts"] or "[]"):
                tag = "skipped" if a.get("skipped") else ("retried" if a.get("retried") else "failed")
                print(f"    {tag} {a['provider']}: {a['kind']} ({_clip(a['error'], 100)})")
            print(f"    tree   {s['tree_stats']}")
            print(f"    {s['status']:7} {s['tool'] or '-'} {s['args'] or ''}")
            print(f"    result {_clip(s['result'], 200)}")
            if args.tree and s["tree_text"]:
                print("    " + s["tree_text"].replace("\n", "\n    "))
        return 0
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    """All subcommands and their flags. Each sets func= to the cmd_* that runs it."""
    # Shared flags, added to subcommands through parents=[...].
    common =argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        help="print sizes, timings and the action method")
    delay = argparse.ArgumentParser(add_help=False)
    delay.add_argument("--delay", type=float, default=0,
                       help="seconds to wait first, so you can switch windows")

    p = argparse.ArgumentParser(prog="pixel", description="PIXEL: control Windows apps through the accessibility tree")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("window", parents=[common, delay], help="show the focused window")
    s.set_defaults(func=cmd_window)

    s = sub.add_parser("tree", parents=[common, delay], help="print the pruned tree with IDs")
    s.add_argument("--window", "-w", help="title or process substring (default: focused window)")
    s.add_argument("--depth", type=int, help="max raw depth (default from config.toml)")
    s.add_argument("--max", type=int, help="max lines (default from config.toml)")
    s.add_argument("--save", metavar="PATH", help="also write the raw tree as JSON (fixtures)")
    s.set_defaults(func=cmd_tree)

    s = sub.add_parser("click", parents=[common, delay], help="activate element ID")
    s.add_argument("id", type=int)
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("type", parents=[common, delay], help="type text into element ID")
    s.add_argument("id", type=int)
    s.add_argument("text")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser("keys", parents=[common, delay],
                       help="send a shortcut to the last-read window, e.g. ctrl+s")
    s.add_argument("keys")
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("open", parents=[common], help="launch or focus an allowlisted app")
    s.add_argument("name")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("run", parents=[common], help="carry out a task with the model loop")
    s.add_argument("command", help='the task, e.g. "open Notepad and type hello"')
    s.add_argument("--no-confirm", action="store_true",
                   help="do not ask before each action (needs the kill hotkey)")
    s.add_argument("--max-steps", type=int, help="lower the step cap for this run")
    s.add_argument("--show-prompt", action="store_true",
                   help="print the full prompt sent on each step (long)")
    s.add_argument("--simulate-fail", action="append", metavar="PROVIDER[:KIND][@N]",
                   help="make a chain provider fail from its Nth call on, with no network "
                        "call (KIND: rate_limit (default), overloaded, timeout, network, auth, "
                        "bad_model, other). Repeatable. For testing the fallback chain.")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("log", help="show the action log (recent runs, or one run's steps)")
    s.add_argument("run_id", type=int, nargs="?", help="show this run's steps")
    s.add_argument("-n", type=int, default=20, help="how many recent runs to list")
    s.add_argument("--tree", action="store_true", help="also print each step's tree text")
    s.set_defaults(func=cmd_log)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse the command line and run the chosen subcommand. Returns the exit code."""
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
