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
from typing import TYPE_CHECKING, Final

import phonenumbers
import pytest
from generate_phone import LIBPHONENUMBER_TAG
from phone_oracle import CONTEXTS, Found, classify, oracle_matches, renderings
from phonenumbers import Leniency, PhoneNumberFormat, PhoneNumberType

from turbohtml.clean import LinkDetector, PhoneFormat, PhoneGrouping, PhoneNumber, PhoneNumbers, PhoneType

if TYPE_CHECKING:
    from collections.abc import Callable

if not (
    Path(__file__).parent / "python-phonenumbers" / "python" / "phonenumbers" / "__init__.py"
).exists():  # pragma: no cover
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

_TYPES: Final = {
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
_EXPLAINED: Final = frozenset({
    "agree",
    "poisoned date",
    "card shape",
    "extension digits read as a number",
    "alpha number",
})
_REGION_PARAMS: Final = [pytest.param(region, id=region) for region in sorted(phonenumbers.SUPPORTED_REGIONS)]
_NON_GEO_PARAMS: Final = [
    pytest.param(code, id=str(code)) for code in sorted(phonenumbers.COUNTRY_CODES_FOR_NON_GEO_REGIONS)
]


def _examples(region: str) -> list[phonenumbers.PhoneNumber]:
    return [
        number
        for number in (phonenumbers.example_number_for_type(region, number_type) for number_type in _TYPES)
        if number is not None
    ]


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


@pytest.mark.parametrize(
    ("settings", "leniency", "check"),
    [
        pytest.param(
            lambda region: PhoneNumbers(regions=(region,)), Leniency.VALID, phonenumbers.is_valid_number, id="valid"
        ),
        pytest.param(
            lambda region: PhoneNumbers(regions=(region,), require_valid=False),
            Leniency.POSSIBLE,
            phonenumbers.is_possible_number,
            id="possible",
        ),
        pytest.param(
            lambda region: PhoneNumbers(regions=(region,), grouping=PhoneGrouping.STRICT),
            Leniency.STRICT_GROUPING,
            phonenumbers.is_valid_number,
            id="strict",
        ),
        pytest.param(
            lambda region: PhoneNumbers(regions=(region,), grouping=PhoneGrouping.EXACT),
            Leniency.EXACT_GROUPING,
            phonenumbers.is_valid_number,
            id="exact",
        ),
    ],
)
@pytest.mark.parametrize("region", _REGION_PARAMS)
def test_detection_matches_the_matcher(
    region: str,
    settings: Callable[[str], PhoneNumbers],
    leniency: int,
    check: Callable[[phonenumbers.PhoneNumber], bool],
) -> None:
    detector = LinkDetector(phones=settings(region))
    unexplained: list[tuple[str, str, list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]] = []
    for number in _examples(region):
        for _form, rendered in renderings(number, region):
            for context in CONTEXTS:
                text = context.format(rendered)
                ours, parsed = _ours(detector, region, text)
                for found in parsed:
                    assert check(found), text
                theirs = oracle_matches(text, region, leniency)
                if (category := classify(text, ours, theirs)) not in _EXPLAINED:
                    unexplained.append((category, text, sorted(ours), sorted(theirs)))
    assert unexplained == []


@pytest.mark.parametrize("region", _REGION_PARAMS)
def test_valid_mode_reports_the_oracles_type(region: str) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=(region,)))
    for number in _examples(region):
        text = phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
        assert [span.phone.type for span in detector.find(text) if span.phone is not None] == [
            _TYPES[phonenumbers.number_type(number)]
        ], text


@pytest.mark.parametrize("country_code", _NON_GEO_PARAMS)
def test_non_geographic_entities_are_found_without_a_region(country_code: int) -> None:
    number = phonenumbers.example_number_for_non_geo_entity(country_code)
    assert number is not None
    assert [
        (span.phone.international_number, span.phone.region)
        for span in LinkDetector(phones=PhoneNumbers()).find(
            phonenumbers.format_number(number, PhoneNumberFormat.INTERNATIONAL)
        )
        if span.phone
    ] == [(phonenumbers.format_number(number, PhoneNumberFormat.E164), "001")]


_STYLES: Final = {
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
            digits, _, ext = (
                expected[PhoneFormat.RFC3966].removeprefix(f"tel:+{number.country_code}-").partition(";ext=")
            )
            expected[PhoneFormat.RFC3966] = (
                f"tel:{digits}{';ext=' + ext if ext else ''};phone-context=+{number.country_code}"
            )
        assert {style: ours.format(style) for style in PhoneFormat} == expected, (number.country_code, nsn, extension)


@pytest.mark.parametrize("region", _REGION_PARAMS)
def test_format_writes_every_style_as_the_oracle(region: str) -> None:
    for number in _examples(region):
        _formats_agree(number)


@pytest.mark.parametrize("country_code", _NON_GEO_PARAMS)
def test_format_writes_non_geographic_numbers_as_the_oracle(country_code: int) -> None:
    number = phonenumbers.example_number_for_non_geo_entity(country_code)
    assert number is not None
    _formats_agree(number)


def _oracle_parse(text: str, region: str, *, require_valid: bool = True) -> tuple[int | None, str, str | None] | None:
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return None
    if not (phonenumbers.is_valid_number if require_valid else phonenumbers.is_possible_number)(number):
        return None
    return (number.country_code, phonenumbers.national_significant_number(number), number.extension)


def _our_parse(text: str, region: str, *, require_valid: bool = True) -> tuple[int, str, str | None] | None:
    try:
        parsed = PhoneNumber.parse(text, regions=(region,), require_valid=require_valid)
    except ValueError:
        return None
    return (parsed.country_code, parsed.national_number, parsed.extension)


@pytest.mark.parametrize("region", _REGION_PARAMS)
def test_parse_reads_every_rendering_as_the_oracle(region: str) -> None:
    for number in _examples(region):
        national = phonenumbers.format_number(number, PhoneNumberFormat.NATIONAL)
        for name, text in [
            *renderings(number, region),
            ("ext-autodial", f"{national},,123"),
            ("ext-semicolon", f"{national};123"),
        ]:
            assert _our_parse(text, region) == _oracle_parse(text, region), (name, text)


@pytest.mark.parametrize("require_valid", [pytest.param(True, id="valid"), pytest.param(False, id="possible")])
@pytest.mark.parametrize(
    "text",
    [
        pytest.param(text, id=f"{index}-{text[:24]!r}")
        for index, text in enumerate([
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
        ])
    ],
)
def test_parse_reads_prose_shapes_as_the_oracle(text: str, *, require_valid: bool) -> None:
    assert _our_parse(text, "US", require_valid=require_valid) == _oracle_parse(text, "US", require_valid=require_valid)
