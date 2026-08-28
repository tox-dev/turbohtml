"""Phone-number detection against the ``phonenumbers`` port of libphonenumber, at the pinned release.

Oracle: the vendored ``python-phonenumbers`` checkout at ``tests/conformance/python-phonenumbers``, the tag whose
version equals the metadata tag the tables were generated from (``generate_phone.LIBPHONENUMBER_TAG``), installed by the
``conformance`` tox env. For every region and every example number its metadata carries, the number is rendered the ways
people write it (national, international, E.164, spaced, dotted, IDD, fullwidth, Arabic-Indic, with each extension form)
and embedded in prose, and ``LinkDetector`` is compared with ``PhoneNumberMatcher`` at the same leniency on the span,
the E.164 number and the extension. A difference must fall into one of the categories the design chose
(``phone_oracle.classify``): a number after a slash date, which the matcher throws away with the date; a payment-card
shape that passes Luhn, which the recognizer rejects; the matcher reading a rendering's extension digits as a number of
their own once its dot rule fires first; and, in possible mode, the matcher reading letters as digits (``10123 ext`` as
a vanity number), which the recognizer never does. Every number the recognizer reports must also be valid (possible, in
possible mode) by the oracle, with the region and type the oracle resolves. A missing submodule or a version other than
the pin is a setup error, never a skip.
"""

from __future__ import annotations

from pathlib import Path

import phonenumbers
import pytest
from generate_phone import LIBPHONENUMBER_TAG
from phone_oracle import CONTEXTS, Found, classify, oracle_matches, renderings
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberType

from turbohtml.clean import LinkDetector, PhoneFormat, PhoneNumber, PhoneNumbers, PhoneType

_ORACLE = Path(__file__).parent / "python-phonenumbers" / "python" / "phonenumbers" / "__init__.py"
if not _ORACLE.exists():  # pragma: no cover
    _HINT = (
        "submodule tests/conformance/python-phonenumbers not checked out; "
        "run: git submodule update --init tests/conformance/python-phonenumbers"
    )
    raise RuntimeError(_HINT)
if phonenumbers.__version__ != LIBPHONENUMBER_TAG.removeprefix("v"):  # pragma: no cover
    _MISMATCH = (
        f"phonenumbers {phonenumbers.__version__} is installed, the tables pin {LIBPHONENUMBER_TAG}; "
        "install the vendored checkout (tox -e conformance) or bump both pins together"
    )
    raise RuntimeError(_MISMATCH)

_TYPES = {
    PhoneNumberType.FIXED_LINE: PhoneType.FIXED_LINE,
    PhoneNumberType.MOBILE: PhoneType.MOBILE,
    PhoneNumberType.FIXED_LINE_OR_MOBILE: PhoneType.FIXED_LINE_OR_MOBILE,
    PhoneNumberType.TOLL_FREE: PhoneType.TOLL_FREE,
    PhoneNumberType.PREMIUM_RATE: PhoneType.PREMIUM_RATE,
    PhoneNumberType.SHARED_COST: PhoneType.SHARED_COST,
    PhoneNumberType.VOIP: PhoneType.VOIP,
    PhoneNumberType.PERSONAL_NUMBER: PhoneType.PERSONAL_NUMBER,
    PhoneNumberType.PAGER: PhoneType.PAGER,
    PhoneNumberType.UAN: PhoneType.UAN,
    PhoneNumberType.VOICEMAIL: PhoneType.VOICEMAIL,
    PhoneNumberType.UNKNOWN: PhoneType.UNKNOWN,
}
_EXPLAINED = frozenset({"agree", "poisoned date", "card shape", "extension digits read as a number", "alpha number"})
_REGIONS = sorted(phonenumbers.SUPPORTED_REGIONS)


def _examples(region: str) -> list[phonenumbers.PhoneNumber]:
    numbers = (phonenumbers.example_number_for_type(region, number_type) for number_type in _TYPES)
    return [number for number in numbers if number is not None]


def _ours(detector: LinkDetector, region: str, text: str) -> tuple[Found, list[phonenumbers.PhoneNumber]]:
    """The recognizer's spans, each re-parsed by the oracle from its text the way the matcher parses a candidate."""
    found: Found = set()
    parsed: list[phonenumbers.PhoneNumber] = []
    for span in detector.find(text):
        if span.phone is None:
            continue
        found.add((span.start, span.end, span.phone.international_number, span.phone.extension or ""))
        number = phonenumbers.parse(span.text, region)
        assert phonenumbers.format_number(number, PhoneNumberFormat.E164) == span.phone.international_number, text
        assert (number.extension or None) == span.phone.extension, text
        assert phonenumbers.region_code_for_number(number) == span.phone.region, text
        parsed.append(number)
    return found, parsed


@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_valid_mode_matches_the_matcher(region: str) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=(region,)))
    unexplained: list[tuple[str, str, list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]] = []
    for number in _examples(region):
        for _form, rendered in renderings(number, region):
            for context in CONTEXTS:
                text = context.format(rendered)
                ours, parsed = _ours(detector, region, text)
                for found in parsed:
                    assert phonenumbers.is_valid_number(found), text
                theirs = oracle_matches(text, region, Leniency.VALID)
                category = classify(text, ours, theirs)
                if category not in _EXPLAINED:
                    unexplained.append((category, text, sorted(ours), sorted(theirs)))
    assert unexplained == []


@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_valid_mode_reports_the_oracles_type(region: str) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=(region,)))
    for number in _examples(region):
        text = phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
        spans = [span for span in detector.find(text) if span.phone is not None]
        assert [span.phone.type for span in spans if span.phone] == [_TYPES[phonenumbers.number_type(number)]], text


@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_possible_mode_matches_the_matcher(region: str) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=(region,), require_valid=False))
    unexplained: list[tuple[str, str, list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]] = []
    for number in _examples(region):
        for _form, rendered in renderings(number, region):
            for context in CONTEXTS:
                text = context.format(rendered)
                ours, parsed = _ours(detector, region, text)
                for found in parsed:
                    assert phonenumbers.is_possible_number(found), text
                theirs = oracle_matches(text, region, Leniency.POSSIBLE)
                category = classify(text, ours, theirs)
                if category not in _EXPLAINED:
                    unexplained.append((category, text, sorted(ours), sorted(theirs)))
    assert unexplained == []


@pytest.mark.parametrize(
    "country_code",
    [pytest.param(code, id=str(code)) for code in sorted(phonenumbers.COUNTRY_CODES_FOR_NON_GEO_REGIONS)],
)
def test_non_geographic_entities_are_found_without_a_region(country_code: int) -> None:
    number = phonenumbers.example_number_for_non_geo_entity(country_code)
    assert number is not None
    text = phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
    spans = LinkDetector(phones=PhoneNumbers()).find(text)
    assert [(span.phone.international_number, span.phone.region) for span in spans if span.phone] == [
        (phonenumbers.format_number(number, PhoneNumberFormat.E164), "001")
    ]


_STYLES = {
    PhoneFormat.E164: PhoneNumberFormat.E164,
    PhoneFormat.INTERNATIONAL: PhoneNumberFormat.INTERNATIONAL,
    PhoneFormat.NATIONAL: PhoneNumberFormat.NATIONAL,
    PhoneFormat.RFC3966: PhoneNumberFormat.RFC3966,
}


def _formats_agree(number: phonenumbers.PhoneNumber) -> None:
    nsn = phonenumbers.national_significant_number(number)
    region = phonenumbers.region_code_for_number(number)
    resolved = _TYPES[phonenumbers.number_type(number)]
    assert number.country_code is not None
    for extension in (None, "123"):
        number.extension = extension
        ours = PhoneNumber(number.country_code, nsn, extension, region, resolved)
        assert {style: ours.format(style) for style in PhoneFormat} == {
            style: phonenumbers.format_number(number, theirs) for style, theirs in _STYLES.items()
        }, (number.country_code, nsn, extension)


@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_format_writes_every_style_as_the_oracle(region: str) -> None:
    for number in _examples(region):
        _formats_agree(number)


@pytest.mark.parametrize(
    "country_code",
    [pytest.param(code, id=str(code)) for code in sorted(phonenumbers.COUNTRY_CODES_FOR_NON_GEO_REGIONS)],
)
def test_format_writes_non_geographic_numbers_as_the_oracle(country_code: int) -> None:
    number = phonenumbers.example_number_for_non_geo_entity(country_code)
    assert number is not None
    _formats_agree(number)


def _oracle_parse(text: str, region: str) -> tuple[int | None, str, str | None] | None:
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return (number.country_code, phonenumbers.national_significant_number(number), number.extension)


@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_parse_reads_every_rendering_as_the_oracle(region: str) -> None:
    for number in _examples(region):
        for name, text in renderings(number, region):
            theirs = _oracle_parse(text, region)
            try:
                parsed = PhoneNumber.parse(text, regions=(region,))
            except ValueError:
                ours = None
            else:
                ours = (parsed.country_code, parsed.national_number, parsed.extension)
            assert ours == theirs, (name, text)
