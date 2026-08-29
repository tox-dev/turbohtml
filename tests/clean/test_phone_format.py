from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, PhoneFormat, PhoneNumber, PhoneNumbers, PhoneType


def _detected(text: str, region: str) -> PhoneNumber:
    spans = LinkDetector(phones=PhoneNumbers(regions=(region,))).find(text)
    assert len(spans) == 1
    assert spans[0].phone is not None
    return spans[0].phone


@pytest.mark.parametrize(
    ("text", "region", "expected"),
    [
        pytest.param(
            "+1 650-253-0000",
            "US",
            ("+16502530000", "+1 650-253-0000", "(650) 253-0000", "tel:+1-650-253-0000"),
            id="us",
        ),
        pytest.param(
            "+44 20 7123 4567",
            "GB",
            ("+442071234567", "+44 20 7123 4567", "020 7123 4567", "tel:+44-20-7123-4567"),
            id="gb-national-prefix",
        ),
        pytest.param(
            "+39 06 1234 5678",
            "IT",
            ("+390612345678", "+39 06 1234 5678", "06 1234 5678", "tel:+39-06-1234-5678"),
            id="it-leading-zero-kept",
        ),
        pytest.param(
            "+54 9 11 2345-6789",
            "AR",
            ("+5491123456789", "+54 9 11 2345-6789", "011 15-2345-6789", "tel:+54-9-11-2345-6789"),
            id="ar-different-national-template",
        ),
        pytest.param(
            "+7 912 345-67-89",
            "RU",
            ("+79123456789", "+7 912 345-67-89", "8 (912) 345-67-89", "tel:+7-912-345-67-89"),
            id="ru-prefix-rule-with-brackets",
        ),
        pytest.param(
            "+61 2 1234 5678",
            "AU",
            ("+61212345678", "+61 2 1234 5678", "(02) 1234 5678", "tel:+61-2-1234-5678"),
            id="au-bracketed-prefix",
        ),
        pytest.param(
            "+800 1234 5678",
            "US",
            ("+80012345678", "+800 1234 5678", "1234 5678", "tel:+800-1234-5678"),
            id="non-geographic",
        ),
        pytest.param(
            "+1 268-460-1234",
            "AG",
            ("+12684601234", "+1 268-460-1234", "(268) 460-1234", "tel:+1-268-460-1234"),
            id="shared-code-uses-the-main-regions-formats",
        ),
    ],
)
def test_styles_write_each_layout(text: str, region: str, expected: tuple[str, str, str, str]) -> None:
    assert tuple(_detected(text, region).format(style) for style in PhoneFormat) == expected


def test_default_style_is_international() -> None:
    assert _detected("650-253-0000", "US").format() == "+1 650-253-0000"


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param(PhoneFormat.E164, "+16502530000", id="e164-drops-the-extension"),
        pytest.param(PhoneFormat.INTERNATIONAL, "+1 650-253-0000 ext. 1234", id="international"),
        pytest.param(PhoneFormat.NATIONAL, "(650) 253-0000 ext. 1234", id="national"),
        pytest.param(PhoneFormat.RFC3966, "tel:+1-650-253-0000;ext=1234", id="rfc3966"),
    ],
)
def test_extension_follows_the_default_marker(style: PhoneFormat, expected: str) -> None:
    assert _detected("650-253-0000 ext. 1234", "US").format(style) == expected


def test_extension_follows_the_regions_preferred_marker() -> None:
    number = _detected("+51 1 1234567 Anexo 22", "PE")
    assert number.extension == "22"
    assert number.format(PhoneFormat.NATIONAL) == "(01) 1234567 Anexo 22"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("+49 200000000000000", "tel:2000-00000000000;phone-context=+49", id="past-e164"),
        pytest.param(
            "+49 200000000000000 ext. 12", "tel:2000-00000000000;ext=12;phone-context=+49", id="with-extension"
        ),
    ],
)
def test_rfc3966_past_e164_is_a_local_number(text: str, expected: str) -> None:
    number = PhoneNumber.parse(text, require_valid=False)
    assert number.e164 is None
    assert number.format(PhoneFormat.RFC3966) == expected
    parsed = PhoneNumber.parse(number.format(PhoneFormat.RFC3966), require_valid=False)
    assert (parsed.country_code, parsed.national_number, parsed.extension) == (49, "200000000000000", number.extension)


def test_hand_built_number_formats_too() -> None:
    assert PhoneNumber(49, "30123456", None, "DE", PhoneType.FIXED_LINE).format(PhoneFormat.NATIONAL) == "030 123456"


def test_style_must_be_a_phone_format() -> None:
    with pytest.raises(TypeError, match="style must be a PhoneFormat"):
        PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE).format("national")  # ty: ignore[invalid-argument-type]  # the wrong type is the point
