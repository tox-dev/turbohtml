from __future__ import annotations

import dataclasses

import pytest

from turbohtml.clean import LinkDetector, PhoneNumber, PhoneNumbers, PhoneType


class _Code(int):
    """An int subclass: not the exact type the value takes."""


def test_fields_and_derived_strings() -> None:
    number = PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)
    assert (number.country_code, number.national_number, number.extension, number.region) == (
        1,
        "6502530000",
        None,
        "US",
    )
    assert number.international_number == "+16502530000"
    assert number.e164 == "+16502530000"


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param((1, "\udc80\udc80", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="national-number"),
        pytest.param((1, "6502530000", None, "\udc80\udc80", PhoneType.FIXED_LINE_OR_MOBILE), id="region"),
    ],
)
def test_lone_surrogates_do_not_encode(fields: tuple[int, str, str | None, str, PhoneType]) -> None:
    with pytest.raises(UnicodeEncodeError, match="surrogates"):
        PhoneNumber(*fields)


def test_extension_is_not_part_of_the_international_number() -> None:
    number = PhoneNumber(44, "2079460958", "123", "GB", PhoneType.FIXED_LINE)
    assert number.international_number == "+442079460958"
    assert number.extension == "123"


def test_value_is_frozen_and_hashable() -> None:
    number = PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        number.region = "CA"  # ty: ignore[invalid-assignment]
    assert hash(number) == hash(PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE))


@pytest.mark.parametrize(
    ("country_code", "national_number", "extension", "region", "number_type"),
    [
        pytest.param(True, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="bool-country-code"),
        pytest.param(
            _Code(1), "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="int-subclass-country-code"
        ),
        pytest.param("1", "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="str-country-code"),
        pytest.param(1.0, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="float-country-code"),
        pytest.param(1, b"6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="bytes-nsn"),
        pytest.param(1, 6502530000, None, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="int-nsn"),
        pytest.param(1, "6502530000", 123, "US", PhoneType.FIXED_LINE_OR_MOBILE, id="int-extension"),
        pytest.param(1, "6502530000", None, b"US", PhoneType.FIXED_LINE_OR_MOBILE, id="bytes-region"),
        pytest.param(1, "6502530000", None, "US", "fixed_line_or_mobile", id="str-type"),
        pytest.param(1, "6502530000", None, "US", 10, id="int-type"),
    ],
)
def test_wrong_field_types(
    country_code: object, national_number: object, extension: object, region: object, number_type: object
) -> None:
    with pytest.raises(TypeError):
        PhoneNumber(country_code, national_number, extension, region, number_type)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("country_code", "national_number", "extension", "region", "number_type", "message"),
    [
        pytest.param(0, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "between 1 and 999", id="zero-code"),
        pytest.param(
            1000, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "between 1 and 999", id="four-digit-code"
        ),
        pytest.param(
            999, "6502530000", None, None, PhoneType.UNKNOWN, "country code 999 is not assigned", id="unassigned-code"
        ),
        pytest.param(1, "", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "2-17 digits", id="empty-nsn"),
        pytest.param(1, "6", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "2-17 digits", id="one-digit-nsn"),
        pytest.param(1, "1" * 18, None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "2-17 digits", id="eighteen-digit-nsn"),
        pytest.param(1, "65O2530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE, "ASCII digits", id="letter-in-nsn"),
        pytest.param(
            1,
            "\u0666\u0665\u0660\u0662\u0665\u0663\u0660\u0660\u0660\u0660",
            None,
            "US",
            PhoneType.FIXED_LINE_OR_MOBILE,
            "digits",
            id="arabic-indic-nsn",
        ),
        pytest.param(1, "6502530000", "", "US", PhoneType.FIXED_LINE_OR_MOBILE, "extension", id="empty-extension"),
        pytest.param(
            1, "6502530000", "12a", "US", PhoneType.FIXED_LINE_OR_MOBILE, "extension", id="letter-in-extension"
        ),
        pytest.param(1, "6502530000", "1" * 21, "US", PhoneType.FIXED_LINE_OR_MOBILE, "extension", id="long-extension"),
        pytest.param(
            44,
            "6502530000",
            None,
            "US",
            PhoneType.FIXED_LINE_OR_MOBILE,
            "region 'US' is not in country code 44",
            id="region-outside-group",
        ),
        pytest.param(1, "6502530000", None, "us", PhoneType.FIXED_LINE_OR_MOBILE, "region 'us'", id="lowercase-region"),
        pytest.param(
            1, "6502530000", None, "USA", PhoneType.FIXED_LINE_OR_MOBILE, "region 'USA'", id="three-letter-region"
        ),
        pytest.param(1, "12", None, "US", PhoneType.VOICEMAIL, "no number", id="nsn-the-plan-rejects"),
        pytest.param(1, "6502530000", None, "US", PhoneType.FIXED_LINE, "no number", id="wrong-type"),
        pytest.param(
            1, "6502530000", None, None, PhoneType.FIXED_LINE_OR_MOBILE, "no number", id="resolved-type-without-region"
        ),
        pytest.param(
            1,
            "6502530000",
            None,
            "CA",
            PhoneType.FIXED_LINE_OR_MOBILE,
            "no number",
            id="region-in-group-but-not-routed",
        ),
        pytest.param(
            1, "5551234567", None, "US", PhoneType.UNKNOWN, "no number", id="unknown-with-a-region-possible-mode-omits"
        ),
        pytest.param(1, "555123", None, None, PhoneType.UNKNOWN, "no number", id="unknown-length-not-possible"),
        pytest.param(
            1,
            "00000000000006502530000",
            None,
            "US",
            PhoneType.FIXED_LINE_OR_MOBILE,
            "2-17 digits",
            id="leading-zeros-past-the-cap",
        ),
    ],
)
def test_values_the_tables_do_not_produce(  # ruff:ignore[too-many-arguments, too-many-positional-arguments]  # one row per field
    country_code: int,
    national_number: str,
    extension: str | None,
    region: str | None,
    number_type: PhoneType,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PhoneNumber(country_code, national_number, extension, region, number_type)


@pytest.mark.parametrize(
    "number",
    [
        pytest.param(PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE), id="us-either"),
        pytest.param(PhoneNumber(1, "8002345678", None, "US", PhoneType.TOLL_FREE), id="us-toll-free"),
        pytest.param(PhoneNumber(1, "2684641234", None, "AG", PhoneType.MOBILE), id="routed-shared-code"),
        pytest.param(PhoneNumber(44, "7400123456", "9", "GB", PhoneType.MOBILE), id="gb-mobile-with-extension"),
        pytest.param(PhoneNumber(800, "12345678", None, "001", PhoneType.TOLL_FREE), id="non-geographic"),
        pytest.param(PhoneNumber(1, "5551234567", None, None, PhoneType.UNKNOWN), id="possible-only"),
        pytest.param(PhoneNumber(39, "0612345678", None, "IT", PhoneType.FIXED_LINE), id="italian-leading-zero"),
    ],
)
def test_values_the_tables_produce(number: PhoneNumber) -> None:
    assert (
        PhoneNumber(number.country_code, number.national_number, number.extension, number.region, number.type) == number
    )


@pytest.mark.parametrize(
    ("text", "e164"),
    [
        pytest.param("+49 123456789012345", None, id="seventeen-digits"),
        pytest.param("+49 1234567890123", "+491234567890123", id="fifteen-digits"),
    ],
)
def test_e164_stops_at_fifteen_digits(text: str, e164: str | None) -> None:
    number = PhoneNumber.parse(text, require_valid=False)
    assert (number.international_number, number.e164) == (text.replace(" ", ""), e164)


def test_detected_numbers_round_trip_through_the_constructor() -> None:
    phones = [
        span.phone
        for span in LinkDetector(phones=PhoneNumbers(regions=("US", "GB"))).find(
            "650-253-0000, 020 7946 0958 x12, +800 1234 5678, 268 464 1234"
        )
        if span.phone
    ]
    assert [phone.international_number for phone in phones] == [
        "+16502530000",
        "+442079460958",
        "+80012345678",
        "+12684641234",
    ]
    assert [PhoneNumber(*dataclasses.astuple(phone)) for phone in phones] == phones
