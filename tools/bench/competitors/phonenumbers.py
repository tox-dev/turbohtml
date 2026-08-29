"""phonenumbers: the Python port of libphonenumber, whose PhoneNumberMatcher finds numbers in text."""

from __future__ import annotations

from typing import Final

import phonenumbers
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberMatcher

REQUIREMENTS = ("phonenumbers>=9.0.38",)

_LENIENCY: Final = {"valid": Leniency.VALID, "possible": Leniency.POSSIBLE}


def phone(case: tuple[str, str]) -> None:
    """Scan plain text with PhoneNumberMatcher at the case's leniency; ``has`` stops at the first match."""
    mode, text = case
    if mode == "has":
        PhoneNumberMatcher(text, "US", leniency=Leniency.VALID).has_next()
        return
    for _match in PhoneNumberMatcher(text, "US", leniency=_LENIENCY.get(mode, Leniency.VALID)):
        pass


def phone_parse(case: tuple[str, tuple[tuple[str, str], ...]]) -> None:
    """Parse each held string with phonenumbers.parse and check it at the case's leniency."""
    mode, held = case
    check = phonenumbers.is_valid_number if mode == "valid" else phonenumbers.is_possible_number
    for region, text in held:
        check(phonenumbers.parse(text, region))


_STYLES: Final = {
    "e164": PhoneNumberFormat.E164,
    "international": PhoneNumberFormat.INTERNATIONAL,
    "national": PhoneNumberFormat.NATIONAL,
    "rfc3966": PhoneNumberFormat.RFC3966,
}
_PARSED: Final[dict[tuple[str, str], phonenumbers.PhoneNumber]] = {}  # the format op times formatting, not the parse


def phone_format(case: tuple[str, tuple[tuple[str, str], ...]]) -> None:
    """Write each number in the case's layout with phonenumbers.format_number."""
    style, held = case
    for region, text in held:
        if (number := _PARSED.get((region, text))) is None:
            number = _PARSED[region, text] = phonenumbers.parse(text, region)
        phonenumbers.format_number(number, _STYLES[style])


# PhoneNumberMatcher takes one default region, so the eight-region rows run it with the first of them; the scan cost
# it reports is the single-region cost, which favors it on those rows.
OPERATIONS = {
    "phone": (phone, "phonenumbers"),
    "phone-parse": (phone_parse, "phonenumbers"),
    "phone-format": (phone_format, "phonenumbers"),
}
