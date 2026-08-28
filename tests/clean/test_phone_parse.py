from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, PhoneNumber, PhoneNumbers, PhoneType


def _fields(number: PhoneNumber) -> tuple[int, str, str | None, str | None, PhoneType]:
    return (number.country_code, number.national_number, number.extension, number.region, number.type)


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
    assert _fields(PhoneNumber.parse(text, regions=regions)) == expected


@pytest.mark.parametrize(
    ("text", "regions"),
    [
        pytest.param("", ("US",), id="empty"),
        pytest.param("no number here", ("US",), id="no-digits"),
        pytest.param("650-253-0000", (), id="no-plus-and-no-region"),
        pytest.param("555-123-4567", ("US",), id="invalid"),
        pytest.param("650-253-0000 or 650-253-0001", ("US",), id="two-numbers"),
        pytest.param("650-253-0000 today", ("US",), id="letters-after"),
        pytest.param("650-253-0000 1", ("US",), id="digit-after"),
        pytest.param("650-253-0000 #", ("US",), id="hash-after"),
        pytest.param("12 650-253-0000", ("US",), id="digits-before"),
        pytest.param("x650-253-0000", ("US",), id="letter-glued-to-the-digits"),
    ],
)
def test_parse_refuses_text_that_is_not_one_number(text: str, regions: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="is not a phone number"):
        PhoneNumber.parse(text, regions=regions)


def test_card_shape_is_read_unlike_detection() -> None:
    text = "0800 123456 7899"
    assert LinkDetector(phones=PhoneNumbers(regions=("DE",))).find(text) == []
    assert _fields(PhoneNumber.parse(text, regions=("DE",))) == (49, "8001234567899", None, "DE", PhoneType.TOLL_FREE)


def test_possible_mode_accepts_a_plausible_length() -> None:
    number = PhoneNumber.parse("555-123-4567", regions=("US",), require_valid=False)
    assert _fields(number) == (1, "5551234567", None, None, PhoneType.UNKNOWN)


def test_parsed_number_formats() -> None:
    assert PhoneNumber.parse("+49 30 12345678").format() == "+49 30 12345678"


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
