"""Hard-blocked actions. Pure functions; no desktop."""

import pytest

from pixel import safety
from pixel.safety import check, iban_ok, luhn_ok, redact


def btn(name):
    return f"Button '{name}'"


@pytest.mark.parametrize("name", ["Buy now", "Buy", "Place order", "Place your order",
                                  "Checkout", "Check out", "Proceed to checkout", "Pay",
                                  "Pay now", "Complete purchase", "Subscribe", "Donate",
                                  "Confirm and pay", "Add payment method"])
def test_purchase_buttons_blocked(name):
    b = check("click_element", {"id": 3}, btn(name))
    assert b and b.category == safety.PURCHASE


@pytest.mark.parametrize("name", ["Sign up", "Signup", "Create account", "Create an account",
                                  "Register", "Join now"])
def test_account_creation_blocked(name):
    b = check("click_element", {"id": 3}, btn(name))
    assert b and b.category == safety.ACCOUNT


@pytest.mark.parametrize("name", ["Add New Tab", "File", "Save", "Display", "Payments history",
                                  "PayPal help", "Buyer guide", "Registry", "Sign in",
                                  "Settings", "Untitled. Unmodified."])
def test_ordinary_buttons_pass(name):
    assert check("click_element", {"id": 3}, btn(name)) is None


@pytest.mark.parametrize("field", ["Card number", "Credit card", "CVV", "CVC", "Security code",
                                   "Expiry date", "MM / YY", "SIN", "Social insurance number",
                                   "SSN", "Passport number", "Driver's licence number",
                                   "Health card", "Account number", "Routing number", "IBAN",
                                   "Password", "PIN"])
def test_typing_into_card_or_id_fields_blocked_and_redacted(field):
    args = {"id": 4, "text": "hello"}
    b = check("type_text", args, f"Edit '{field}'")
    assert b and b.category == safety.CARD_ID
    assert redact(args, b) == {"id": 4, "text": "[redacted: card or ID data]"}


def test_typing_into_the_notepad_editor_passes():
    assert check("type_text", {"id": 2, "text": "hello"}, "Document 'Text editor'") is None


@pytest.mark.parametrize("text", ["4111 1111 1111 1111", "4111-1111-1111-1111",
                                  "my card is 4111111111111111 thanks", "5555555555554444",
                                  "378282246310005"])
def test_card_numbers_in_text_blocked(text):
    b = check("type_text", {"id": 2, "text": text}, "Document 'Text editor'")
    assert b and b.category == safety.CARD_ID and "payment card" in b.reason


def test_card_number_through_send_keys_blocked():
    b = check("send_keys", {"keys": "4 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1"})
    assert b and b.redact == ["keys"]


@pytest.mark.parametrize("text, what", [("046 454 286", "social insurance"),
                                        ("123-45-6789", "social security"),
                                        ("GB82 WEST 1234 5698 7654 32", "IBAN")])
def test_id_numbers_in_text_blocked(text, what):
    b = check("type_text", {"id": 2, "text": text}, "Document 'Text editor'")
    assert b and what in b.reason


@pytest.mark.parametrize("text", ["hello", "call 905-555-0123", "order 4111 1111 1111 1112",
                                  "2026-11-22", "123 456 789", "room 101, 3 pm",
                                  "1234567890123"])
def test_ordinary_text_passes(text):
    assert check("type_text", {"id": 2, "text": text}, "Document 'Text editor'") is None


@pytest.mark.parametrize("text", [r"C:\Windows\System32\drivers\etc\hosts", r"c:\windows\win.ini",
                                  r"C:\Program Files\app\config.ini", r"%SystemRoot%\x",
                                  r"HKLM\Software\Microsoft", r"C:\ProgramData\x"])
def test_system_locations_blocked(text):
    b = check("type_text", {"id": 2, "text": text}, "Edit 'File name:'")
    assert b and b.category == safety.SYSTEM and b.redact == []


@pytest.mark.parametrize("name, args", [("open_app", {"name": "notepad"}),
                                        ("read_text", {"id": 2}),
                                        ("scroll", {"id": 2, "direction": "down"}),
                                        ("send_keys", {"keys": "ctrl+s"}),
                                        ("finish", {"summary": "Bought nothing"})])
def test_other_tools_pass(name, args):
    assert check(name, args, btn("Buy now") if "id" in args else None) is None


def test_luhn_and_iban():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")
    assert iban_ok("GB82 WEST 1234 5698 7654 32") and not iban_ok("GB82 WEST 1234 5698 7654 33")


def test_message_tells_the_model_to_ask():
    m = check("click_element", {"id": 1}, btn("Buy now")).message()
    assert m.startswith("BLOCKED (purchase): element 'Buy now'") and "call ask_user" in m


def test_redact_text_hides_card_and_id_numbers_only():
    from pixel.safety import redact_text
    assert redact_text("type 4111 1111 1111 1111 in Notepad") == \
        "type [redacted: card number] in Notepad"
    assert redact_text("SIN 046 454 286, SSN 123-45-6789") == \
        "SIN [redacted: ID number], SSN [redacted: ID number]"
    assert redact_text("open Notepad and type hello 905-555-0123") == \
        "open Notepad and type hello 905-555-0123"
