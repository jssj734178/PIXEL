"""Tool menu validation. Pure, no desktop."""

from pixel.tools import TEXT_LIMIT, menu, validate

SPECS = menu(["notepad"])
IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9}


def ok(name, args, ids=IDS):
    good, out = validate(name, args, SPECS, ids)
    assert good, out
    return out


def bad(name, args, ids=IDS):
    good, out = validate(name, args, SPECS, ids)
    assert not good
    assert out.startswith("ERROR:")
    return out


def test_menu_has_the_m2_tools():
    assert [s.name for s in SPECS] == ["open_app", "click_element", "type_text", "send_keys",
                                       "scroll", "read_text", "finish", "ask_user"]


def test_valid_calls_pass():
    assert ok("type_text", {"id": 9, "text": "hello"}) == {"id": 9, "text": "hello"}
    assert ok("open_app", {"name": "notepad"}) == {"name": "notepad"}
    assert ok("finish", {"summary": "done"}) == {"summary": "done"}
    assert ok("scroll", {"id": 2, "direction": "down"})


def test_unknown_tool_lists_the_menu():
    err = bad("delete_file", {"path": "x"})
    assert "unknown tool 'delete_file'" in err and "open_app, click_element" in err


def test_missing_arg_says_what_is_needed():
    err = bad("type_text", {"text": "hello"})
    assert err == "ERROR: type_text needs 'id' (integer), 'text' (string). Got: {\"text\": \"hello\"}"


def test_extra_arg_rejected():
    assert "does not take 'force'" in bad("click_element", {"id": 1, "force": True})


def test_whole_float_becomes_int():
    assert ok("click_element", {"id": 3.0}) == {"id": 3}


def test_numeric_string_id_becomes_int():
    assert ok("click_element", {"id": "4"}) == {"id": 4}


def test_fractional_float_and_bool_rejected():
    assert "must be an integer" in bad("click_element", {"id": 3.5})
    assert "must be an integer" in bad("click_element", {"id": True})


def test_wrong_string_type():
    assert "'text' must be a string" in bad("type_text", {"id": 1, "text": 5})


def test_enum_enforced():
    assert "must be one of notepad" in bad("open_app", {"name": "chrome"})
    assert "must be one of up, down" in bad("scroll", {"id": 1, "direction": "sideways"})


def test_string_length_limit():
    err = bad("type_text", {"id": 1, "text": "x" * (TEXT_LIMIT + 1)})
    assert f"the limit is {TEXT_LIMIT}" in err


def test_id_must_exist_in_current_tree():
    assert bad("click_element", {"id": 42}) == \
        "ERROR: no element 42 in the current tree (IDs 1 to 9)"


def test_id_with_no_tree():
    assert "no app window yet" in bad("click_element", {"id": 1}, ids=set())


def test_non_dict_args():
    assert "must be an object" in bad("click_element", [1])


def test_none_args_treated_as_empty():
    assert "needs 'id'" in bad("click_element", None)
