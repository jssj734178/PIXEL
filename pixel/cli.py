"""pixel window | tree | click | type | keys | open"""

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
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _verbose(args, msg: str) -> None:
    if args.verbose:
        print(f"[verbose] {msg}", file=sys.stderr)


def _wait(args) -> None:
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
    from pixel.bridge import get_focused_window
    _wait(args)
    res = get_focused_window()
    if res.get("ok"):
        print(f"{res['title']} ({res['process']}, pid {res['pid']}, handle {res['handle']})")
    return _result(args, res)


def cmd_tree(args) -> int:
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
    from pixel.bridge import click_element
    _wait(args)
    res = click_element(args.id)
    if res.get("ok"):
        print(f"clicked {res['target']} via {res['method']}")
    return _result(args, res)


def cmd_type(args) -> int:
    from pixel.bridge import type_text
    _wait(args)
    res = type_text(args.id, args.text)
    if res.get("ok"):
        print(f"typed {res['chars']} chars into {res['target']} via {res['method']}")
        if res.get("note"):
            print(f"note: {res['note']}", file=sys.stderr)
    return _result(args, res)


def cmd_keys(args) -> int:
    from pixel.bridge import send_keys
    _wait(args)
    res = send_keys(args.keys)
    if res.get("ok"):
        print(f"sent {res['sent']}")
    return _result(args, res)


def cmd_open(args) -> int:
    from pixel.bridge import open_app
    res = open_app(args.name)
    if res.get("ok"):
        front = "" if res.get("foreground") else " (could not bring it to the front)"
        print(f"{res['action']} '{res['window']}'{front}")
    return _result(args, res)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        help="print sizes, timings and the action method")
    delay = argparse.ArgumentParser(add_help=False)
    delay.add_argument("--delay", type=float, default=0,
                       help="seconds to wait first, so you can switch windows")

    p = argparse.ArgumentParser(prog="pixel", description="PIXEL bridge commands (M1)")
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
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
