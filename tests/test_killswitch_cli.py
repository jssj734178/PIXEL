"""Kill hotkey parsing and `pixel run` argument parsing. Registers nothing."""

import pytest

from pixel.cli import build_parser
from pixel.killswitch import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, parse_hotkey


def test_parse_default_hotkey():
    assert parse_hotkey("ctrl+alt+q") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("Q"))


@pytest.mark.parametrize("bad", ["q", "ctrl+alt+", "win+q", "ctrl+alt+enter"])
def test_bad_hotkeys_rejected(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_run_args():
    a = build_parser().parse_args(["run", "open Notepad and type hello", "-v",
                                   "--no-confirm", "--max-steps", "2"])
    assert a.command == "open Notepad and type hello"
    assert a.verbose and a.no_confirm and a.max_steps == 2 and not a.show_prompt


def test_run_defaults_to_confirm():
    a = build_parser().parse_args(["run", "x"])
    assert not a.no_confirm and a.max_steps is None
