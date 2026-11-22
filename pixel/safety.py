"""Hard-blocked actions: purchases, account creation, card or ID data, system files.

Loop layer, pure functions. check() runs after a call passes validation and
before the confirm prompt and the bridge. A block cannot be approved at the
prompt and does not depend on confirm mode. The word lists live here, not in
config, so a config edit cannot weaken them.

The tool menu is still the main guard: there is no tool that deletes files,
buys anything, or opens an app outside the allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PURCHASE, ACCOUNT, CARD_ID, SYSTEM = "purchase", "account creation", "card or ID data", "system files"
TEXT_TOOLS = {"type_text": "text", "send_keys": "keys"}


@dataclass
class Block:
    category: str
    reason: str
    redact: list[str] = field(default_factory=list)   # argument keys never written to the log

    def message(self) -> str:
        return (f"BLOCKED ({self.category}): {self.reason}. This is never allowed. "
                "Do not look for another way; call ask_user to tell the user what is needed.")


def _words(*patterns: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.IGNORECASE)


# Matched against the name of a clicked element.
_PURCHASE_NAMES = _words(
    r"buy(?: it)?(?: now)?", r"purchase", r"place (?:your |my )?order", r"check ?out",
    r"pay(?: now)?", r"complete (?:your |my )?(?:purchase|order)", r"subscribe", r"donate",
    r"confirm (?:and|&) pay", r"proceed to (?:checkout|payment)", r"add payment(?: method)?",
)
_ACCOUNT_NAMES = _words(
    r"sign ?up", r"create (?:an |a new |your |my )?account", r"register", r"join now",
    r"open (?:an |a new )?account",
)
# Matched against the name of a field that text is typed into.
_SENSITIVE_FIELDS = _words(
    r"card ?(?:number|no|#)", r"credit card", r"debit card", r"cvv2?", r"cvc", r"security code",
    r"expir(?:y|ation)(?: date)?", r"exp\.? ?date", r"mm ?/ ?yy", r"social insurance", r"sin",
    r"social security", r"ssn", r"passport", r"driver'?s? licen[cs]e", r"health card", r"ohip",
    r"(?:bank )?account (?:number|no|#)", r"transit (?:number|no)", r"institution number",
    r"routing (?:number|no)", r"iban", r"password", r"passcode", r"pin",
)
# Matched against typed text and keys.
_SYSTEM_TEXT = re.compile(
    r"[a-z]:\\windows\b|\\windows\\|\bsystem32\b|\bsyswow64\b|program ?files|\bprogramdata\b"
    r"|%(?:systemroot|windir|programfiles|programdata)%|drivers[\\/]etc|\bhklm\b|\bhkey_",
    re.IGNORECASE)

_CARD_RUN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SIN_RUN = re.compile(r"(?<!\d)\d{3}[ -]?\d{3}[ -]?\d{3}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def iban_ok(s: str) -> bool:
    s = s.replace(" ", "").upper()
    if not 15 <= len(s) <= 34:
        return False
    moved = s[4:] + s[:4]
    try:
        n = int("".join(str(int(c, 36)) for c in moved))
    except ValueError:
        return False
    return n % 97 == 1


def sensitive_number(text: str) -> str | None:
    """Return what kind of card or ID number the text contains, if any."""
    for m in _CARD_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return "a payment card number"
    if _SSN.search(text):
        return "a social security number"
    for m in _SIN_RUN.finditer(text):
        if luhn_ok(re.sub(r"\D", "", m.group())):
            return "a social insurance number"
    for m in _IBAN.finditer(text):
        if iban_ok(m.group()):
            return "a bank account number (IBAN)"
    return None


def redact_text(text: str) -> str:
    """Replace card and ID numbers in free text (e.g. the command, for the log)."""
    def card(m):
        d = re.sub(r"\D", "", m.group())
        return "[redacted: card number]" if 13 <= len(d) <= 19 and luhn_ok(d) else m.group()

    def sin(m):
        return "[redacted: ID number]" if luhn_ok(re.sub(r"\D", "", m.group())) else m.group()

    def iban(m):
        return "[redacted: bank account]" if iban_ok(m.group()) else m.group()

    text = _CARD_RUN.sub(card, text)
    text = _SSN.sub("[redacted: ID number]", text)
    text = _SIN_RUN.sub(sin, text)
    return _IBAN.sub(iban, text)


def element_name(label: str | None) -> str:
    """Labels look like "Button 'Place order'"; return the quoted name."""
    if not label:
        return ""
    m = re.search(r"'(.*)'", label)
    return m.group(1) if m else label


def check(name: str, args: dict, label: str | None = None) -> Block | None:
    """label is the tree's label for args['id'] when the call targets an element."""
    target = element_name(label)
    if name == "click_element" and target:
        if _PURCHASE_NAMES.search(target):
            return Block(PURCHASE, f"element '{target}' looks like a purchase or payment button")
        if _ACCOUNT_NAMES.search(target):
            return Block(ACCOUNT, f"element '{target}' looks like an account sign-up button")

    key = TEXT_TOOLS.get(name)
    if key is None:
        return None
    text = str(args.get(key, ""))
    if name == "type_text" and target and _SENSITIVE_FIELDS.search(target):
        return Block(CARD_ID, f"field '{target}' asks for card, ID or login data", [key])
    found = sensitive_number(text)
    if found:
        return Block(CARD_ID, f"the text contains what looks like {found}", [key])
    if _SYSTEM_TEXT.search(text):
        return Block(SYSTEM, "the text names a Windows system location")
    return None


def redact(args: dict, block: Block | None) -> dict:
    """A copy of args that is safe to print and log."""
    if not block or not block.redact:
        return dict(args)
    return {k: (f"[redacted: {block.category}]" if k in block.redact else v)
            for k, v in args.items()}
