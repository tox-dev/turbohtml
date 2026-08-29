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
from typing import Final

import phonenumbers
import pytest
from generate_phone import LIBPHONENUMBER_TAG
from phone_oracle import CONTEXTS, Found, classify, oracle_matches, renderings
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberType

from turbohtml.clean import LinkDetector, PhoneFormat, PhoneGrouping, PhoneNumber, PhoneNumbers, PhoneType

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
        expected = {style: phonenumbers.format_number(number, theirs) for style, theirs in _STYLES.items()}
        if len(str(number.country_code)) + len(nsn) > 15:
            # RFC 3966 composes a global number from E.164; past that limit turbohtml writes the local form
            global_form = expected[PhoneFormat.RFC3966].removeprefix(f"tel:+{number.country_code}-")
            digits, _, ext = global_form.partition(";ext=")
            expected[PhoneFormat.RFC3966] = (
                f"tel:{digits}{';ext=' + ext if ext else ''};phone-context=+{number.country_code}"
            )
        assert {style: ours.format(style) for style in PhoneFormat} == expected, (number.country_code, nsn, extension)


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
        national = phonenumbers.format_number(number, PhoneNumberFormat.NATIONAL)
        held = [("ext-autodial", f"{national},,123"), ("ext-semicolon", f"{national};123")]
        for name, text in [*renderings(number, region), *held]:
            theirs = _oracle_parse(text, region)
            try:
                parsed = PhoneNumber.parse(text, regions=(region,))
            except ValueError:
                ours = None
            else:
                ours = (parsed.country_code, parsed.national_number, parsed.extension)
            assert ours == theirs, (name, text)


_PROSE_SHAPES: Final = [
    "+44 20 7946 0958 (1234)",
    "+1 650-253-0000 (x1234)",
    "(+1 650 253 0000)",
    "((650)) 253-0000",
    "(650) (253) (0000)",
    "650 ----- 253 0000",
    "+ +1 650 253 0000",
    "++1 650 253 0000",
    "+1 +650 253 0000",
    "0xx11 2345 6789 x12",
    "650 253 0000 x1234#",
    "+1 650 253 0000 (ext. 1234)",
    "650-253-0000/x12",
    "650-253-0000\\x12",
    "650-253-0000 / x12",
    "tel:+1-650-253-0000;ext=12",
    "tel:+1-650-253-0000;isub=123",
    "tel:2530000;ext=12;phone-context=+1650",
    "tel:2530000;phone-context=+1650;ext=12",
    "Tel:2530000;phone-context=+1650",
    "1-800-FLOWERS",
    "x650-253-0000",
    "Call +1 650 253 0000 tomorrow",
    "650-253-0000 call",
    "650-253-0000 ok",
    "650-253-0000 today",
    "650-253-0000 or 650-253-0001",
    "+1 650 253 0000 x 1234 # ",
    "+1 650 253 0000 #1234",
    "+1 650 253 0000 ext",
    "650 253 0000 ext 12345678",
    "  +1 650 253 0000  ",
    "+1 (650) 253-0000)",
    "6502530000abc",
    "650.253.0000.",
    "+1 650 253 0000,,1234",
    "+1 650 253 0000;1234",
    "+1 650 253 0000 ,,,,,,1234",
    "650-253-0000 - 1234#",
    "650-253-0000ext1234",
    "650-253-0000 x",
    "+1 650 253 0000 (",
    "650-253-0000 #",
    "12 650-253-0000",
    "650-253-0000 1",
    "+1 650-253-0000\u2460",
    "650-253-0000 \u7535\u8bdd",
    "\uff0b1 650 253 0000",
    "+1 \uff16\uff15\uff10 \uff12\uff15\uff13 \uff10\uff10\uff10\uff10",
    "+1 650 253 0000 \u0434\u043e\u0431. 12",
    "+1 650 253 0000 anexo 12",
    "+1 650 253 0000 int 12",
    "011 44 20 7946 0958",
    "650-253-0000" + " " * 238,
    "650-253-0000" + " " * 239,
    "+1 650 253 0000 x" + "1" * 21,
]


@pytest.mark.parametrize(
    "text", [pytest.param(text, id=f"{index}-{text[:24]!r}") for index, text in enumerate(_PROSE_SHAPES)]
)
def test_parse_reads_prose_shapes_as_the_oracle(text: str) -> None:
    for require_valid in (True, False):
        try:
            number = phonenumbers.parse(text, "US")
        except phonenumbers.NumberParseException:
            theirs = None
        else:
            checks = phonenumbers.is_valid_number(number) if require_valid else phonenumbers.is_possible_number(number)
            theirs = (
                (number.country_code, phonenumbers.national_significant_number(number), number.extension)
                if checks
                else None
            )
        try:
            parsed = PhoneNumber.parse(text, regions=("US",), require_valid=require_valid)
        except ValueError:
            ours = None
        else:
            ours = (parsed.country_code, parsed.national_number, parsed.extension)
        assert ours == theirs, require_valid


@pytest.mark.parametrize(
    ("grouping", "leniency"),
    [
        pytest.param(PhoneGrouping.STRICT, Leniency.STRICT_GROUPING, id="strict"),
        pytest.param(PhoneGrouping.EXACT, Leniency.EXACT_GROUPING, id="exact"),
    ],
)
@pytest.mark.parametrize("region", [pytest.param(region, id=region) for region in _REGIONS])
def test_grouping_leniencies_match_the_matcher(region: str, grouping: PhoneGrouping, leniency: int) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=(region,), grouping=grouping))
    unexplained: list[tuple[str, str, list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]] = []
    for number in _examples(region):
        for _form, rendered in renderings(number, region):
            for context in CONTEXTS:
                text = context.format(rendered)
                ours, _parsed = _ours(detector, region, text)
                theirs = oracle_matches(text, region, leniency)
                category = classify(text, ours, theirs)
                if category not in _EXPLAINED:
                    unexplained.append((category, text, sorted(ours), sorted(theirs)))
    assert unexplained == []
