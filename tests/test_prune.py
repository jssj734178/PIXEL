import json
from pathlib import Path

import pytest

from pixel.bridge.prune import clean_text, estimate_tokens, prune, render
from synthetic import editor_window, n

FIXTURES = Path(__file__).parent / "fixtures"


def labels(result):
    return [line.strip() for line in result.lines]


# ---------- synthetic trees ----------

def test_editor_window_shape():
    res = prune(editor_window())
    assert labels(res) == [
        '[1] TabItem "Untitled. Unmodified."',
        '[2] Button "Add New Tab"',
        '[3] MenuItem "File"',
        '[4] MenuItem "Edit"',
        '[5] Document "Text editor" = ""',
        'Text "Ln 1, Col 1"',
        'Text "UTF-8"',
    ]


def test_title_bar_and_scroll_bar_dropped():
    text = "\n".join(prune(editor_window()).lines)
    assert "Minimize" not in text and "Line up" not in text


def test_offscreen_and_zero_size_dropped_with_subtree():
    root = n("Window", "w", [
        n("Pane", "", [n("Button", "Hidden child")], offscreen=True),
        n("Button", "Zero", rect=[10, 10, 0, 20]),
        n("Button", "Shown"),
    ])
    assert labels(prune(root)) == ['[1] Button "Shown"']


def test_unnamed_panes_promote_children():
    root = n("Window", "w", [n("Pane", "", [n("Group", "", [
        n("Button", "A"), n("Button", "B")])])])
    res = prune(root)
    assert labels(res) == ['[1] Button "A"', '[2] Button "B"']
    assert all(not line.startswith(" ") for line in res.lines)


def test_named_group_with_one_child_collapses():
    root = n("Window", "w", [n("Group", "Toolbar", [n("Button", "Save")])])
    assert labels(prune(root)) == ['[1] Button "Save"']


def test_named_group_with_several_children_is_context():
    root = n("Window", "w", [n("Group", "Toolbar", [n("Button", "Save"), n("Button", "Open")])])
    res = prune(root)
    assert res.lines == ['Group "Toolbar"', '  [1] Button "Save"', '  [2] Button "Open"']


def test_text_repeating_parent_name_dropped():
    root = n("Window", "w", [n("Hyperlink", "Read more about it", [n("Text", "Read more")])])
    assert labels(prune(root)) == ['[1] Hyperlink "Read more about it"']


def test_unnamed_leaf_button_dropped_but_unnamed_edit_kept():
    root = n("Window", "w", [n("Button", ""), n("Edit", ""), n("Button", "", automation_id="go")])
    assert labels(prune(root)) == ["[1] Edit", "[2] Button #go"]


def test_disabled_marked():
    root = n("Window", "w", [n("Button", "Send", enabled=False)])
    assert labels(prune(root)) == ['[1] Button "Send" (disabled)']


def test_ids_sequential_and_stable():
    a, b = prune(editor_window()), prune(editor_window())
    assert [e.id for e in a.elements] == list(range(1, len(a.elements) + 1))
    assert a.lines == b.lines


def test_depth_limit_adds_marker():
    deep = n("Button", "Deep")
    for _ in range(6):
        deep = n("Pane", "", [deep])
    root = n("Window", "w", [n("Button", "Top"), deep])
    res = prune(root, max_depth=3)
    assert labels(res) == ['[1] Button "Top"']
    assert res.depth_cut
    assert "… deeper content not shown" in render(res)


def test_element_cap_adds_marker():
    root = n("Window", "w", [n("Button", f"B{i}") for i in range(10)])
    res = prune(root, max_elements=4)
    assert len(res.lines) == 4 and res.omitted == 6
    assert render(res).endswith("… 6 more elements not shown")


def test_long_names_and_values_truncated():
    root = n("Window", "w", [n("Edit", "x" * 200, value="y" * 200)])
    line = prune(root).lines[0]
    assert '"' + "x" * 59 + '…"' in line
    assert '"' + "y" * 79 + '…"' in line


def test_odd_glyphs_do_not_raise():
    weird = "Start‍ \x00\u0007tab\tnew\nline \ud800 \"q\" ⊞ 🙂"
    root = n("Window", weird, [n("Button", weird), n("Text", "")])
    out = render(prune(root), weird, "x.exe")
    out.encode("utf-8")  # must be encodable
    assert '[1] Button "Start tab new line \'q\' ⊞ 🙂"' in out
    assert "" not in out and "\x00" not in out


def test_clean_text_quotes_and_whitespace():
    assert clean_text('  a\r\n  "b"  ', 60) == "a 'b'"
    assert clean_text(None, 60) == ""


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


# ---------- saved fixtures (gitignored until reviewed) ----------

FIXTURE_FILES = sorted(FIXTURES.glob("*.json")) if FIXTURES.exists() else []


@pytest.mark.skipif(not FIXTURE_FILES, reason="no fixtures in tests/fixtures/ "
                    "(capture with `pixel tree --window <app> --save tests/fixtures/<app>.json`)")
@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixture_prunes(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    res = prune(raw)
    assert [e.id for e in res.elements] == list(range(1, len(res.elements) + 1))
    text = render(res, raw.get("name", ""), "")
    assert text
    if path.stem.startswith("notepad"):
        assert estimate_tokens(text) < 500, f"{path.name}: {estimate_tokens(text)} tokens"
