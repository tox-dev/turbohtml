from __future__ import annotations

import dataclasses

import pytest

from turbohtml.clean import LinkDetector, PhoneNumber, PhoneNumbers, PhoneType


@pytest.mark.parametrize(
    ("text", "regions", "expected"),
    [
        pytest.param("650-253-0000", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="us"),
        pytest.param("6502530000", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="bare"),
        pytest.param(
            "(650) 253-0000", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="brackets"
        ),
        pytest.param("+44 20 7946 0958 x12", (), (44, "2079460958", "12", "GB", PhoneType.FIXED_LINE), id="plus-ext"),
        pytest.param(
            "tel:+1-650-253-0000;ext=12",
            (),
            (1, "6502530000", "12", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="rfc3966",
        ),
        pytest.param(
            "Tel: (650) 253-0000.",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="label-and-trailing-period",
        ),
        pytest.param(
            "650-253-0000!?", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="ascii-marks"
        ),
        pytest.param(
            "650-253-0000\u2003\u2026\u3001\uff01",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="unicode-space-and-marks",
        ),
        pytest.param(
            "(650) 253-0000)", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="closer-after"
        ),
        pytest.param(
            "x650-253-0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="letter-glued-to-the-digits",
        ),
        pytest.param(
            "650-253-0000 ok",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="two-letters-after",
        ),
        pytest.param(
            "650-253-0000 x", ("US",), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="lone-x-after"
        ),
        pytest.param("1-800-FLOWERS", ("US",), (1, "8003569377", None, "US", PhoneType.TOLL_FREE), id="vanity-letters"),
        pytest.param(
            "1-800-goog-411", ("US",), (1, "8004664411", None, "US", PhoneType.TOLL_FREE), id="vanity-lowercase"
        ),
        pytest.param(
            "+1 650 253 0000 (ext. 1234)",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="bracketed-extension",
        ),
        pytest.param(
            "+1 650-253-0000 (x1234)",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="bracketed-x-extension",
        ),
        pytest.param(
            "650 253 0000 ext 12345678",
            ("US",),
            (1, "6502530000", "12345678", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="long-explicit-extension",
        ),
        pytest.param(
            "650-253-0000/x12",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="second-number-cut",
        ),
        pytest.param(
            "650-253-0000\\x12",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="second-number-after-backslash",
        ),
        pytest.param(
            "650-253-0000 / x12",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="second-number-after-spaced-slash",
        ),
        pytest.param(
            "((650)) 253-0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="doubled-brackets",
        ),
        pytest.param(
            "(650) (253) (0000)",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="every-group-bracketed",
        ),
        pytest.param(
            "650 ----- 253 0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="long-separator",
        ),
        pytest.param(
            "+1 650 253 0000 (",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="opener-after",
        ),
        pytest.param(
            "++1 650 253 0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="doubled-plus",
        ),
        pytest.param(
            "+1 650 253 0000 ,,,,,,1234",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="many-commas-autodial",
        ),
        pytest.param(
            "650-253-0000;ext=2;isub=1",
            ("US",),
            (1, "6502530000", "2", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="isub-after-the-extension",
        ),
        pytest.param(
            "650-253-0000;isub=1;ext=2",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="isub-cuts-the-extension",
        ),
        pytest.param(
            "0xx11 2345 6789", ("BR",), (55, "1123456789", None, "BR", PhoneType.FIXED_LINE), id="carrier-code-marks"
        ),
        pytest.param(
            "020 7946 0958",
            ("US", "GB", "DE"),
            (44, "2079460958", None, "GB", PhoneType.FIXED_LINE),
            id="middle-region-reads-the-number",
        ),
        pytest.param(
            "+011 44 20 7946 0958",
            ("US",),
            (44, "2079460958", None, "GB", PhoneType.FIXED_LINE),
            id="plus-then-idd-reads-without-the-plus",
        ),
        pytest.param(
            "  tel:  +1 650 253 0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="spaces-around-the-scheme",
        ),
        pytest.param(
            "  +1 650 253 0000  ", (), (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="whitespace"
        ),
        pytest.param(
            "\uff0b\uff11 \uff16\uff15\uff10-\uff12\uff15\uff13-\uff10\uff10\uff10\uff10",
            (),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="fullwidth",
        ),
        pytest.param(
            "20 7946 0958", ("GB",), (44, "2079460958", None, "GB", PhoneType.FIXED_LINE), id="prefix-not-required"
        ),
        pytest.param("011 44 20 7946 0958", ("US",), (44, "2079460958", None, "GB", PhoneType.FIXED_LINE), id="idd"),
        pytest.param(
            "650-253-0000,,1234",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="autodial",
        ),
        pytest.param(
            "650-253-0000;1234",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="semicolon",
        ),
        pytest.param(
            "650-253-0000,1234",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="one-comma",
        ),
        pytest.param(
            "650-253-0000 ,, 1234#",
            ("US",),
            (1, "6502530000", "1234", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="spaced-autodial-with-suffix",
        ),
        pytest.param(
            "tel:2530000;phone-context=+1650",
            (),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="local-number-with-a-global-context",
        ),
        pytest.param(
            " tel:2530000;phone-context=+1650",
            (),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="space-before-the-scheme",
        ),
        pytest.param(
            "tel:2530000;ext=12;phone-context=+1-650",
            (),
            (1, "6502530000", "12", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="local-number-extension-before-the-context",
        ),
        pytest.param(
            "tel:2530000;phone-context=+1650;ext=12",
            (),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="parameters-after-the-context-are-not-the-number",
        ),
        pytest.param(
            "tel:650-253-0000;phone-context=example.com",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="domain-context-reads-nationally",
        ),
        pytest.param(
            "tel:650-253-0000;phone-context=example.com.",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="domain-context-with-a-trailing-dot",
        ),
        pytest.param(
            "tel:650-253-0000;phone-context=a1-b.example",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="domain-context-with-hyphens-and-digits",
        ),
        pytest.param(
            "tel:0000;phone-context=+1(650)253",
            (),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="global-context-with-visual-separators",
        ),
        pytest.param(
            "tel:650-253-0000;isub=1234",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="isub-ends-the-number",
        ),
        pytest.param(
            "tel:650-253-0000;ext=12;isub=1234",
            ("US",),
            (1, "6502530000", "12", "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="extension-before-isub",
        ),
        pytest.param(
            "030 12345678", ("US", "DE"), (49, "3012345678", None, "DE", PhoneType.FIXED_LINE), id="second-region"
        ),
        pytest.param(
            "030 12345678", ("us", " de "), (49, "3012345678", None, "DE", PhoneType.FIXED_LINE), id="regions-normalize"
        ),
    ],
)
def test_parse_reads_one_number(
    text: str, regions: tuple[str, ...], expected: tuple[int, str, str | None, str | None, PhoneType]
) -> None:
    assert dataclasses.astuple(PhoneNumber.parse(text, regions=regions)) == expected


@pytest.mark.parametrize(
    ("text", "regions"),
    [
        pytest.param("", ("US",), id="empty"),
        pytest.param("no number here", ("US",), id="no-digits"),
        pytest.param("650-253-0000", (), id="no-plus-and-no-region"),
        pytest.param("555-123-4567", ("US",), id="invalid"),
        pytest.param("650-253-0000 or 650-253-0001", ("US",), id="two-numbers"),
        pytest.param("650-253-0000 today", ("US",), id="letters-after"),
        pytest.param("650-253-0000 \u7535\u8bdd", ("US",), id="han-after"),
        pytest.param("650-253-0000 \u0437\u0430\u0432\u0442\u0440\u0430", ("US",), id="cyrillic-after"),
        pytest.param("650-253-0000 \u0915", ("US",), id="devanagari-letter-after"),
        pytest.param("+1 650-253-0000\u2460", (), id="circled-digit-after"),
        pytest.param("+1 650-253-0000\u00bd", (), id="fraction-after"),
        pytest.param("+1 650-253-0000\u2168", (), id="roman-numeral-after"),
        pytest.param("650-253-0000 1", ("US",), id="digit-after"),
        pytest.param("650-253-0000 #", ("US",), id="hash-after"),
        pytest.param("12 650-253-0000", ("US",), id="digits-before"),
        pytest.param("650-253-0000 abc", ("US",), id="three-letters-after-spell-digits"),
        pytest.param("650-253-0000 x12 ok", ("US",), id="letters-after-the-extension-spell-digits"),
        pytest.param("12 ext 34", ("US",), id="letter-before-the-third-digit"),
        pytest.param("+1 2", ("US",), id="too-few-digits"),
        pytest.param("+999 650 253 0000", ("US",), id="unassigned-code-after-a-plus"),
        pytest.param("+00 1 650 253 0000", ("US",), id="zeros-after-a-plus"),
        pytest.param("+1 650 253 0000 ext", ("US",), id="extension-label-without-digits"),
        pytest.param("+ +1 650 253 0000", ("US",), id="plus-after-a-gap"),
        pytest.param("+1 +650 253 0000", ("US",), id="plus-after-the-code"),
        pytest.param("+1 650 253 0000 x 1234 #", ("US",), id="hash-after-a-space"),
        pytest.param("Tel:2530000;phone-context=+1650", ("US",), id="uppercase-scheme-with-a-context"),
        pytest.param("650-253-0000" + " " * 239, ("US",), id="over-250-characters"),
        pytest.param("tel:2530000;phone-context=", ("US",), id="empty-context"),
        pytest.param("tel:2530000;phone-context=+", ("US",), id="context-without-digits"),
        pytest.param("tel:2530000;phone-context=+1 650!", ("US",), id="context-with-a-stray-character"),
        pytest.param("tel:2530000;phone-context=ex ample", ("US",), id="context-that-is-no-domain"),
        pytest.param("tel:2530000;phone-context=a..b", ("US",), id="domain-with-an-empty-label"),
        pytest.param("tel:2530000;phone-context=-a.com", ("US",), id="domain-label-starting-with-a-hyphen"),
        pytest.param("tel:2530000;phone-context=a-.com", ("US",), id="domain-label-ending-with-a-hyphen"),
        pytest.param("tel:2530000;phone-context=.example.com", ("US",), id="domain-starting-with-a-dot"),
        pytest.param("tel:2530000;phone-context=1", ("US",), id="top-label-starting-with-a-digit"),
        pytest.param("tel:2530000;phone-context=a.b_c", ("US",), id="domain-with-an-underscore"),
        pytest.param("tel:1;phone-context=+" + "1" * 500, ("US",), id="context-past-any-number"),
        pytest.param("tel:1;phone-context=+" + "1" * 25, ("US",), id="context-past-a-calling-code-and-a-number"),
        pytest.param("tel:+1-650-253-0000;phone-context=+1", ("US",), id="global-number-with-a-global-context"),
        pytest.param("tel:2530000;phone-contex", ("US",), id="truncated-parameter-name"),
        pytest.param("tel:2530000;phone-context=+1\u00e9650", ("US",), id="context-with-a-non-ascii-mark"),
        pytest.param(
            "tel:2530000;phone-context=ex\u00e4mple.com", ("US",), id="domain-context-with-a-non-ascii-letter"
        ),
        pytest.param(";phone-context=+1650", ("US",), id="context-without-a-number"),
        pytest.param("tel:" + "1 " * 300 + ";phone-context=+1", ("US",), id="number-part-past-the-buffer"),
    ],
)
def test_parse_refuses_text_that_is_not_one_number(text: str, regions: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="is not a phone number"):
        PhoneNumber.parse(text, regions=regions)


def test_card_shape_is_read_unlike_detection() -> None:
    text = "0800 123456 7899"
    assert LinkDetector(phones=PhoneNumbers(regions=("DE",))).find(text) == []
    assert dataclasses.astuple(PhoneNumber.parse(text, regions=("DE",))) == (
        49,
        "8001234567899",
        None,
        "DE",
        PhoneType.TOLL_FREE,
    )


@pytest.mark.parametrize(
    ("text", "regions", "expected"),
    [
        pytest.param("+44 20 7946 0958#", (), (44, "207946", "0958"), id="international"),
        pytest.param("020 7946 0958#", ("IT",), (39, "0207946", "0958"), id="national-keeps-the-italian-zero"),
    ],
)
def test_trailing_hash_makes_the_last_group_an_extension(
    text: str, regions: tuple[str, ...], expected: tuple[int, str, str]
) -> None:
    number = PhoneNumber.parse(text, regions=regions, require_valid=False)
    assert (number.country_code, number.national_number, number.extension) == expected


def test_possible_mode_accepts_a_plausible_length() -> None:
    assert dataclasses.astuple(PhoneNumber.parse("555-123-4567", regions=("US",), require_valid=False)) == (
        1,
        "5551234567",
        None,
        None,
        PhoneType.UNKNOWN,
    )


def test_possible_mode_reads_a_slash_date_unlike_detection() -> None:
    assert LinkDetector(phones=PhoneNumbers(regions=("GB",), require_valid=False)).find("25/12/2012") == []
    number = PhoneNumber.parse("25/12/2012", regions=("GB",), require_valid=False)
    assert (number.country_code, number.national_number) == (44, "25122012")


def test_parse_reads_exactly_250_characters() -> None:
    assert PhoneNumber.parse("650-253-0000" + " " * 238, regions=("US",)).national_number == "6502530000"


def test_parsed_number_formats() -> None:
    assert PhoneNumber.parse("+49 30 12345678").format() == "+49 30 12345678"


def test_subclass_parses_to_itself() -> None:
    class Held(PhoneNumber):
        __slots__ = ()

    assert type(Held.parse("+1 650-253-0000")) is Held


def test_text_must_be_str() -> None:
    with pytest.raises(TypeError, match="text must be str"):
        PhoneNumber.parse(b"650-253-0000")  # ty: ignore[invalid-argument-type]  # the wrong type is the point


@pytest.mark.parametrize(
    ("regions", "error", "message"),
    [
        pytest.param("US", TypeError, "regions", id="bare-string"),
        pytest.param(("XX",), ValueError, "XX", id="unknown-code"),
    ],
)
def test_regions_validate_like_phone_numbers(regions: object, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        PhoneNumber.parse("650-253-0000", regions=regions)  # ty: ignore[invalid-argument-type]  # the wrong value is the point
