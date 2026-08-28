"""phonenumbers: the Python port of libphonenumber, whose PhoneNumberMatcher finds numbers in text."""

from __future__ import annotations

from phonenumbers import Leniency, PhoneNumberMatcher

REQUIREMENTS = ("phonenumbers>=9.0.38",)

_LENIENCY = {"valid": Leniency.VALID, "possible": Leniency.POSSIBLE}


def phone(case: tuple[str, str]) -> None:
    """Scan plain text with PhoneNumberMatcher at the case's leniency; ``has`` stops at the first match."""
    mode, text = case
    if mode == "has":
        PhoneNumberMatcher(text, "US", leniency=Leniency.VALID).has_next()
        return
    matcher = PhoneNumberMatcher(text, "US", leniency=_LENIENCY.get(mode, Leniency.VALID))
    for _match in matcher:
        pass


# PhoneNumberMatcher takes one default region, so the eight-region rows run it with the first of them; the scan cost
# it reports is the single-region cost, which favors it on those rows.
OPERATIONS = {"phone": (phone, "phonenumbers")}
