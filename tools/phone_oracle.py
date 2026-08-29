"""
The ``phonenumbers`` oracle behind the phone-number differential and the conformance suite.

Renders each example number the ways people write it, embeds it in prose, reads the oracle's matcher, and names every
difference from the recognizer: the categories the design chose (the date rule, the card rule, no vanity numbers) and
the one artifact of the oracle's split order. An unnamed difference is a defect in the recognizer.
"""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Final, TypeAlias

import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberMatcher

CONTEXTS: Final = (
    "{}",
    "call {} now",
    "Tel: {}.",
    "({}) or later",
    "ring {}!",
    "12345 {}",
    "{} 12345",
    "3/10/2011 {}",
    "2012-01-02 08 {}",
    "{} - 5",
    "id 4111 1111 1111 1111 then {}",
    "{}-99",
    "x{}",
    "{0}/{0}",
)
# the extension digits the renderings append; the oracle's dot rule reads them as a number of their own
_EXTENSION_DIGITS: Final = frozenset({"123", "4567", "89"})
_CARD: Final = re.compile(r"4111 1111 1111 1111")
_SLASH_DATE: Final = re.compile(r"(?:[0-3]?\d/[01]?\d|[01]?\d/[0-3]?\d)/(?:[12]\d)?\d{2}")
_FULLWIDTH: Final = str.maketrans(string.digits, "".join(chr(0xFF10 + value) for value in range(10)))
_ARABIC_INDIC: Final = str.maketrans(string.digits, "".join(chr(0x660 + value) for value in range(10)))
_CARD_SHAPES: Final = ([4, 4, 4, 4], [4, 4, 4, 4, 3], [4, 6, 5], [4, 6, 4])

Found: TypeAlias = set[tuple[int, int, str, str]]


def renderings(number: phonenumbers.PhoneNumber, region: str) -> list[tuple[str, str]]:
    """Return the written forms of one number: national with and without prefix, international, IDD, digit scripts."""
    national = phonenumbers.format_number(number, PhoneNumberFormat.NATIONAL)
    international = phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
    nsn = phonenumbers.national_significant_number(number)
    forms = [
        ("national", national),
        ("international", international),
        ("e164", phonenumbers.format_number(number, PhoneNumberFormat.E164)),
        ("nsn", nsn),
    ]
    if len(nsn) > 4:
        forms.extend([
            ("nsn-spaced", f"{nsn[:3]} {nsn[3:6]} {nsn[6:]}".strip()),
            ("nsn-dotted", f"{nsn[:3]}.{nsn[3:6]}.{nsn[6:]}".strip(".")),
        ])
    forms.extend([
        ("idd", phonenumbers.format_out_of_country_calling_number(number, region)),
        ("fullwidth", international.translate(_FULLWIDTH)),
        ("arabic-indic", national.translate(_ARABIC_INDIC)),
        ("ext-x", f"{national} x 123"),
        ("ext-word", f"{national} ext. 4567"),
        ("ext-rfc", f"{international};ext=89"),
    ])
    return forms


def oracle_matches(text: str, region: str | None, leniency: int) -> Found:
    """Return the matcher's spans as ``(start, end, E.164, extension)``."""
    found: Found = set()
    for match in PhoneNumberMatcher(text, region, leniency=leniency):
        number = match.number
        found.add((
            match.start,
            match.end,
            phonenumbers.format_number(number, PhoneNumberFormat.E164),
            number.extension or "",
        ))
    return found


def classify(text: str, ours: Found, theirs: Found) -> str:  # ruff:ignore[too-many-return-statements]  # one per category
    """Name the difference between the recognizer's matches and the matcher's, ``agree`` when there is none."""
    if ours == theirs:
        return "agree"
    if not theirs and ours and _SLASH_DATE.search(text):
        return "poisoned date"
    if _is_card_shape(text, ours, theirs):
        return "card shape"
    # the matcher took a rendering's extension digits as a number and resumed after them, so it never saw the rest
    without_extension_digits = {found for found in theirs if text[found[0] : found[1]] not in _EXTENSION_DIGITS}
    if without_extension_digits != theirs and without_extension_digits <= ours:
        return "extension digits read as a number"
    extra = theirs - ours
    if ours <= theirs and extra and all(_has_letters(text[start:end]) for start, end, _e164, _ext in extra):
        return "alpha number"
    if theirs and not ours:
        return "missed"
    return "span or number differs" if ours and theirs else "extra"


def _is_card_shape(text: str, ours: Found, theirs: Found) -> bool:
    """Check whether the difference is the card-shape rule: a Luhn-valid card number the recognizer rejects."""
    card = _CARD.search(text)
    if (
        card
        and theirs - ours
        and not ours - theirs
        and all(card.start() <= start and end <= card.end() for start, end, _e164, _ext in theirs - ours)
    ):
        return True
    return bool(not ours and theirs and _is_luhn_card(text))


def _is_luhn_card(text: str) -> bool:
    """Check whether the text's digit run has a Luhn-valid card shape, the shape the recognizer rejects."""
    runs = re.findall(r"[0-9]+", "".join(str(unicodedata.digit(char)) if char.isdigit() else char for char in text))
    lengths = [len(run) for run in runs]
    if lengths not in _CARD_SHAPES and not (len(lengths) == 1 and 13 <= lengths[0] <= 19):
        return False
    total = 0
    for position, char in enumerate(reversed("".join(runs))):
        value = int(char)
        if position % 2:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _has_letters(candidate: str) -> bool:
    """Report whether the matcher's candidate holds letters it read as digits, the vanity rule the recognizer omits."""
    return any(char.isalpha() for char in candidate)


__all__ = [
    "CONTEXTS",
    "Found",
    "classify",
    "oracle_matches",
    "renderings",
]
