from __future__ import annotations

import dataclasses
from collections import deque
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypedDict

import pytest

from turbohtml.clean import (
    DEFAULT_PHONE_LABELS,
    LinkDetector,
    Linker,
    Linkify,
    PhoneFormat,
    PhoneGrouping,
    PhoneNumber,
    PhoneNumbers,
    PhoneType,
    linkify,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


class _Codes:
    # A caller's own iterable: neither a sequence nor a set.
    def __init__(self, *codes: str) -> None:
        self._codes = codes

    def __iter__(self) -> Iterator[str]:
        yield from self._codes


def test_defaults() -> None:
    assert PhoneNumbers() == PhoneNumbers(
        regions=(),
        require_valid=True,
        require_separators=False,
        skip_card_numbers=True,
        require_national_prefix=True,
        collapse_whitespace=False,
        grouping=PhoneGrouping.ANY,
        types=None,
        ignore_numbers_after=DEFAULT_PHONE_LABELS,
    )


@pytest.mark.parametrize(
    ("settings", "error", "message"),
    [
        pytest.param({"grouping": "strict"}, TypeError, "grouping must be a PhoneGrouping", id="str-grouping"),
        pytest.param(
            {"grouping": PhoneGrouping.EXACT, "require_valid": False},
            ValueError,
            "grouping needs require_valid=True",
            id="grouping-in-possible-mode",
        ),
        pytest.param({"regions": ("\udc80\udc80",)}, UnicodeEncodeError, "surrogates", id="surrogate-region"),
        pytest.param({"ignore_numbers_after": ("\udc80",)}, UnicodeEncodeError, "surrogates", id="surrogate-label"),
    ],
)
def test_settings_rejections(
    settings: dict[str, str | bool | PhoneGrouping | tuple[str, ...]], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        PhoneNumbers(**settings)  # ty: ignore[invalid-argument-type]  # the wrong values are the point


def test_default_labels_are_sorted_lowercase_ascii() -> None:
    assert tuple(sorted(DEFAULT_PHONE_LABELS)) == DEFAULT_PHONE_LABELS
    assert all(label.isascii() and label.islower() and label.isalpha() for label in DEFAULT_PHONE_LABELS)


@pytest.mark.parametrize(
    ("regions", "expected"),
    [
        pytest.param(["us", "US", "de"], ("US", "DE"), id="list-folded-deduplicated"),
        pytest.param(("gb", " fr "), ("GB", "FR"), id="tuple-stripped"),
        pytest.param(deque(["JP"]), ("JP",), id="deque"),
        pytest.param((code for code in ("IN", "BR")), ("IN", "BR"), id="generator"),
        pytest.param(_Codes("AU", "NZ"), ("AU", "NZ"), id="custom-iterable"),
        pytest.param([], (), id="empty"),
    ],
)
def test_regions_normalize_in_order(regions: Iterable[str], expected: tuple[str, ...]) -> None:
    assert PhoneNumbers(regions=regions).regions == expected


@pytest.mark.parametrize(
    "regions",
    [
        pytest.param("US", id="bare-str"),
        pytest.param(b"US", id="bytes"),
        pytest.param({"US"}, id="set"),
        pytest.param(frozenset({"US"}), id="frozenset"),
        pytest.param({"US": 1}, id="dict"),
        pytest.param([1], id="int-entry"),
        pytest.param(["US", None], id="none-entry"),
    ],
)
def test_regions_reject_unordered_or_non_str(
    regions: str | bytes | set[str] | frozenset[str] | dict[str, int] | list[int] | list[str | None],
) -> None:
    with pytest.raises(TypeError, match="regions"):
        PhoneNumbers(regions=regions)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_regions_generator_failure_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        PhoneNumbers(regions=_failing_codes())


def _failing_codes() -> Iterator[str]:
    yield "US"
    msg = "boom"
    raise RuntimeError(msg)


@pytest.mark.parametrize(
    ("regions", "message"),
    [
        pytest.param(("XX",), "unknown phone region 'XX'", id="unassigned"),
        pytest.param(("USA",), "unknown phone region 'USA'", id="three-letters"),
        pytest.param(("001",), "unknown phone region '001'", id="non-geographic"),
        pytest.param(("U1",), "unknown phone region 'U1'", id="digit"),
        pytest.param(("\u00df",), "unknown phone region '\u00df'", id="sharp-s-is-not-south-sudan"),
        pytest.param(("\u0131s",), "unknown phone region '\u0131s'", id="dotless-i-is-not-iceland"),
        pytest.param(("US", "GB", "DE", "FR", "IT", "ES", "NL", "BE", "AT"), "at most 8 phone regions", id="nine"),
    ],
)
def test_regions_reject_unknown_codes(regions: tuple[str, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PhoneNumbers(regions=regions)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(name, id=name)
        for name in (
            "require_valid",
            "require_separators",
            "skip_card_numbers",
            "require_national_prefix",
            "collapse_whitespace",
        )
    ],
)
@pytest.mark.parametrize(
    "value", [pytest.param(1, id="int"), pytest.param("yes", id="str"), pytest.param(None, id="none")]
)
def test_flags_must_be_bool(name: str, value: int | str | None) -> None:
    with pytest.raises(TypeError, match=name):
        PhoneNumbers(**{name: value})  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    ("types", "expected"),
    [
        pytest.param({PhoneType.MOBILE}, frozenset({PhoneType.MOBILE}), id="set"),
        pytest.param(
            [PhoneType.FIXED_LINE_OR_MOBILE], frozenset({PhoneType.FIXED_LINE_OR_MOBILE}), id="explicit-either"
        ),
        pytest.param((PhoneType.TOLL_FREE, PhoneType.TOLL_FREE), frozenset({PhoneType.TOLL_FREE}), id="duplicates"),
        pytest.param(iter([PhoneType.VOIP]), frozenset({PhoneType.VOIP}), id="iterator"),
        pytest.param(None, None, id="all"),
    ],
)
def test_types_freeze(types: Iterable[PhoneType] | None, expected: frozenset[PhoneType] | None) -> None:
    assert PhoneNumbers(types=types).types == expected


@pytest.mark.parametrize(
    ("types", "require_valid", "error", "message"),
    [
        pytest.param((), True, ValueError, "non-empty", id="empty"),
        pytest.param(["mobile"], True, TypeError, "PhoneType", id="str-member"),
        pytest.param([True], True, TypeError, "PhoneType", id="bool-member"),
        pytest.param([PhoneType.UNKNOWN], True, ValueError, "UNKNOWN", id="unknown"),
        pytest.param([PhoneType.MOBILE], False, ValueError, "require_valid", id="possible-mode"),
    ],
)
def test_types_rejections(
    types: tuple[()] | list[str | bool | PhoneType],
    require_valid: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a parametrize value
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        PhoneNumbers(types=types, require_valid=require_valid)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        pytest.param(("ticket", "account"), ("account", "ticket"), id="sorted"),
        pytest.param(["Ref", "ref", " REF "], ("ref",), id="folded-deduplicated"),
        pytest.param((), (), id="disabled"),
        pytest.param(deque(["sku"]), ("sku",), id="deque"),
        pytest.param((word for word in ("b", "a")), ("a", "b"), id="generator"),
    ],
)
def test_labels_normalize(labels: Iterable[str], expected: tuple[str, ...]) -> None:
    assert PhoneNumbers(ignore_numbers_after=labels).ignore_numbers_after == expected


@pytest.mark.parametrize(
    ("labels", "error", "message"),
    [
        pytest.param("order", TypeError, "ignore_numbers_after", id="bare-str"),
        pytest.param({"order"}, TypeError, "ignore_numbers_after", id="set"),
        pytest.param([1], TypeError, "ignore_numbers_after", id="int-entry"),
        pytest.param(7, TypeError, "iterable", id="not-iterable"),
        pytest.param(["a-b"], ValueError, "phone label 'a-b'", id="punctuation"),
        pytest.param(["ref2"], ValueError, "phone label 'ref2'", id="digit"),
        pytest.param(["ref~"], ValueError, "phone label 'ref~'", id="past-z"),
        pytest.param([""], ValueError, "phone label ''", id="empty-entry"),
        pytest.param(["abcdefghijklm"], ValueError, "phone label", id="thirteen-bytes"),
        pytest.param(["ünit"], ValueError, "phone label", id="non-ascii"),
    ],
)
def test_labels_rejections(
    labels: str | set[str] | list[int] | int | list[str], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        PhoneNumbers(ignore_numbers_after=labels)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_labels_generator_failure_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        PhoneNumbers(ignore_numbers_after=_failing_codes())


def test_equality_and_repr_use_the_canonical_form() -> None:
    phones = PhoneNumbers(regions=["us", "US", "de"], ignore_numbers_after=("ticket", "Account"))
    assert phones == PhoneNumbers(regions=("US", "DE"), ignore_numbers_after=("account", "ticket"))
    assert "regions=('US', 'DE')" in repr(phones)
    assert "ignore_numbers_after=('account', 'ticket')" in repr(phones)


def test_value_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        PhoneNumbers().regions = ("US",)  # ty: ignore[invalid-assignment]  # assigns a value the type forbids on purpose to check the rejection


def test_value_is_hashable() -> None:
    assert len({PhoneNumbers(regions=["US"]), PhoneNumbers(regions=("us",))}) == 1


@pytest.mark.parametrize(
    "phones",
    [
        pytest.param(SimpleNamespace(), id="object"),
        pytest.param({}, id="dict"),
        pytest.param("US", id="str"),
        pytest.param(True, id="bool"),
    ],
)
def test_consumers_reject_non_phone_numbers(phones: SimpleNamespace | dict[str, str] | str | int) -> None:
    message = "phones must be PhoneNumbers or None"
    with pytest.raises(TypeError, match=message):
        Linker(Linkify(phones=phones))  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
    with pytest.raises(TypeError, match=message):
        LinkDetector(phones=phones)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
    with pytest.raises(TypeError, match=message):
        linkify("650-253-0000", Linkify(phones=phones))  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda phones: Linker(Linkify(phones=phones)), id="linker"),
        pytest.param(lambda phones: LinkDetector(phones=phones), id="detector"),
    ],
)
def test_consumers_keep_the_settings(build: Callable[[PhoneNumbers | None], Linker | LinkDetector]) -> None:
    phones = PhoneNumbers(regions=("US",))
    assert build(phones).phones is phones
    assert build(None).phones is None


class _Settings(TypedDict, total=False):
    require_valid: bool
    require_separators: bool
    skip_card_numbers: bool
    types: set[PhoneType] | None
    ignore_numbers_after: tuple[str, ...]


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        pytest.param("6502530000", {"require_separators": True}, [], id="bare-run-rejected"),
        pytest.param("650-253-0000", {"require_separators": True}, ["tel:+16502530000"], id="separated-accepted"),
        pytest.param("(650) 253-0000", {"require_separators": True}, ["tel:+16502530000"], id="parenthesized-accepted"),
        pytest.param("01116502530000", {"require_separators": True}, ["tel:+16502530000"], id="idd-run-accepted"),
        pytest.param("+16502530000", {"require_separators": True}, ["tel:+16502530000"], id="plus-run-accepted"),
        pytest.param("6502530000", {}, ["tel:+16502530000"], id="bare-run-default"),
        pytest.param("Ref 650-253-0000", {}, [], id="default-label"),
        pytest.param("Phone no. 650-253-0000", {}, ["tel:+16502530000"], id="no-is-not-a-label"),
        pytest.param("Order 12345, 650-253-0000", {}, ["tel:+16502530000"], id="label-poisons-only-its-run"),
        pytest.param("Phone 650-253-0000", {"ignore_numbers_after": ("phone",)}, [], id="custom-label"),
        pytest.param("Ref 650-253-0000", {"ignore_numbers_after": ()}, ["tel:+16502530000"], id="labels-disabled"),
        pytest.param("REF: 650-253-0000", {}, [], id="label-case-folded-through-colon"),
        pytest.param("800-234-5678", {"types": {PhoneType.TOLL_FREE}}, ["tel:+18002345678"], id="type-kept"),
        pytest.param("650-253-0000", {"types": {PhoneType.TOLL_FREE}}, [], id="type-filtered"),
        pytest.param(
            "650-253-0000", {"types": {PhoneType.FIXED_LINE_OR_MOBILE}}, ["tel:+16502530000"], id="either-type"
        ),
        pytest.param("650-253-0000", {"types": {PhoneType.MOBILE}}, [], id="either-is-not-mobile"),
    ],
)
def test_controls_change_detection(text: str, kwargs: _Settings, expected: list[str]) -> None:
    assert [span.url for span in LinkDetector(phones=PhoneNumbers(regions=("US",), **kwargs)).find(text)] == expected


@pytest.mark.parametrize(
    ("skip", "expected"),
    [
        pytest.param(True, [], id="skipped"),
        pytest.param(False, ["tel:+494111", "tel:+49111111111111"], id="kept-as-the-oracle-splits-them"),
    ],
)
def test_skip_card_numbers(skip: bool, expected: list[str]) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # the parametrized flag selects the expected outcome
    assert [
        span.url
        for span in LinkDetector(
            phones=PhoneNumbers(regions=("DE",), require_valid=False, skip_card_numbers=skip)
        ).find("card 4111 1111 1111 1111 on file")
    ] == expected


def test_card_shape_with_plus_is_never_a_card() -> None:
    assert [
        span.url
        for span in LinkDetector(phones=PhoneNumbers(regions=("DE",), require_valid=False)).find(
            "+49 4111 1111 1111 1111"
        )
    ] == ["tel:+494111", "tel:+49111111111111"]


@pytest.mark.parametrize(
    ("regions", "text", "expected"),
    [
        pytest.param(("GB", "US"), "650-253-0000", "tel:+16502530000", id="second-region-reads-a-us-number"),
        pytest.param(("US", "GB"), "020 7946 0958", "tel:+442079460958", id="second-region-reads-a-gb-number"),
    ],
)
def test_regions_order_decides_the_national_reading(regions: tuple[str, ...], text: str, expected: str) -> None:
    assert [span.url for span in LinkDetector(phones=PhoneNumbers(regions=regions)).find(text)] == [expected]


@pytest.mark.parametrize(
    ("phones", "expected"),
    [
        pytest.param(PhoneNumbers(regions=("GB",)), [], id="required-by-default"),
        pytest.param(
            PhoneNumbers(regions=("GB",), require_national_prefix=False), ["tel:+442079460958"], id="not-required"
        ),
        pytest.param(
            PhoneNumbers(regions=("GB",), require_valid=False), ["tel:+442079460958"], id="possible-mode-never-asks"
        ),
    ],
)
def test_national_prefix_requirement(phones: PhoneNumbers, expected: list[str]) -> None:
    assert [span.url for span in LinkDetector(phones=phones).find("ring 20 7946 0958 today")] == expected


@pytest.mark.parametrize("required", [pytest.param(True, id="required"), pytest.param(False, id="not-required")])
def test_written_prefix_links_either_way(required: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # pytest passes the row positionally
    assert [
        span.url
        for span in LinkDetector(phones=PhoneNumbers(regions=("GB",), require_national_prefix=required)).find(
            "ring 020 7946 0958"
        )
    ] == ["tel:+442079460958"]


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param(" ", id="space"),
        pytest.param("\u00a0", id="nbsp"),
        pytest.param("\u3000", id="ideographic-space"),
        pytest.param("\t", id="tab"),
        pytest.param("\n", id="newline"),
        pytest.param("\r\n", id="crlf"),
        pytest.param(": ", id="colon-and-space"),
    ],
)
def test_label_reaches_over_any_whitespace(separator: str) -> None:
    assert LinkDetector(phones=PhoneNumbers(regions=("US",))).find(f"Order{separator}650-253-0000") == []


def test_label_stops_at_a_symbol() -> None:
    assert [
        span.text for span in LinkDetector(phones=PhoneNumbers(regions=("US",))).find("Order\u2192650-253-0000")
    ] == ["650-253-0000"]


def test_settings_convert_to_a_dict_and_a_tuple() -> None:
    phones = PhoneNumbers(regions=("US",), ignore_numbers_after=("order",))
    assert dataclasses.asdict(phones)["regions"] == ("US",)
    assert dataclasses.astuple(phones)[0] == ("US",)


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
        pytest.param(
            "+998 90 123 45 67",
            "UZ",
            ("+998901234567", "+998 90 123 45 67", "90 123 45 67", "tel:+998-90-123-45-67"),
            id="last-country-code-group",
        ),
    ],
)
def test_styles_write_each_layout(text: str, region: str, expected: tuple[str, str, str, str]) -> None:
    assert tuple(_detected(text, region).format(style) for style in PhoneFormat) == expected


def _detected(text: str, region: str) -> PhoneNumber:
    spans = LinkDetector(phones=PhoneNumbers(regions=(region,))).find(text)
    assert len(spans) == 1
    assert spans[0].phone is not None
    return spans[0].phone


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


@pytest.mark.parametrize(
    ("country_code", "national_number", "region", "number_type", "expected"),
    [
        pytest.param(49, "30123456", "DE", PhoneType.FIXED_LINE, "030 123456", id="de"),
        pytest.param(1, "6502530000", "US", PhoneType.FIXED_LINE_OR_MOBILE, "(650) 253-0000", id="first-group"),
        pytest.param(998, "901234567", "UZ", PhoneType.MOBILE, "90 123 45 67", id="last-group"),
    ],
)
def test_hand_built_number_formats_too(
    country_code: int, national_number: str, region: str, number_type: PhoneType, expected: str
) -> None:
    assert (
        PhoneNumber(country_code, national_number, None, region, number_type).format(PhoneFormat.NATIONAL) == expected
    )


def test_style_must_be_a_phone_format() -> None:
    with pytest.raises(TypeError, match="style must be a PhoneFormat"):
        PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE).format("national")  # ty: ignore[invalid-argument-type]  # the wrong type is the point


class _Code(int):
    # An int subclass: not the exact type the value takes.
    ...


def test_fields_and_derived_strings() -> None:
    number = PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)
    assert (number.country_code, number.national_number, number.extension, number.region) == (
        1,
        "6502530000",
        None,
        "US",
    )
    assert (number.international_number, number.e164) == ("+16502530000", "+16502530000")


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
    assert (number.international_number, number.extension) == ("+442079460958", "123")


def test_value_is_frozen_and_hashable() -> None:
    number = PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        number.region = "CA"  # ty: ignore[invalid-assignment]  # assigns a value the type forbids on purpose to check the rejection
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
    country_code: float | str,
    national_number: int | str | bytes,
    extension: int | None,
    region: str | bytes,
    number_type: PhoneType | str | int,
) -> None:
    with pytest.raises(TypeError):
        PhoneNumber(country_code, national_number, extension, region, number_type)  # ty: ignore[invalid-argument-type]  # passes untyped values on purpose to check the TypeError


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
        pytest.param(
            8, "6502530000", None, None, PhoneType.UNKNOWN, "country code 8 is not assigned", id="unassigned-one-digit"
        ),
        pytest.param(
            99,
            "6502530000",
            None,
            None,
            PhoneType.UNKNOWN,
            "country code 99 is not assigned",
            id="unassigned-two-digit",
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
            "+\U0001fbf1 \U0001fbf6\U0001fbf5\U0001fbf0-\U0001fbf2\U0001fbf5\U0001fbf3-"
            "\U0001fbf0\U0001fbf0\U0001fbf0\U0001fbf0",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="segmented-digits",
        ),
        pytest.param(
            "+\u0661 \u0666\u0665\u0660-\u0662\u0665\u0663-\u0660\u0660\u0660\u0660",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="arabic-indic-digits",
        ),
        pytest.param(
            "+\U0001d7f7 \U0001d7fc\U0001d7fb\U0001d7f6-\U0001d7f8\U0001d7fb\U0001d7f9-"
            "\U0001d7f6\U0001d7f6\U0001d7f6\U0001d7f6",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="mathematical-monospace-digits",
        ),
        pytest.param(
            "\U0001fbef+1 650-253-0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="before-segmented-digits",
        ),
        pytest.param(
            "\U0001fbfa+1 650-253-0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="after-segmented-digits",
        ),
        pytest.param(
            "\U0001d7cd+1 650-253-0000",
            ("US",),
            (1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
            id="before-mathematical-digits",
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
        pytest.param(f"650-253-0000{' ' * 239}", ("US",), id="over-250-characters"),
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
        pytest.param(f"tel:1;phone-context=+{'1' * 500}", ("US",), id="context-past-any-number"),
        pytest.param(f"tel:1;phone-context=+{'1' * 25}", ("US",), id="context-past-a-calling-code-and-a-number"),
        pytest.param("tel:+1-650-253-0000;phone-context=+1", ("US",), id="global-number-with-a-global-context"),
        pytest.param("tel:2530000;phone-contex", ("US",), id="truncated-parameter-name"),
        pytest.param("tel:2530000;phone-context=+1\u00e9650", ("US",), id="context-with-a-non-ascii-mark"),
        pytest.param(
            "tel:2530000;phone-context=ex\u00e4mple.com", ("US",), id="domain-context-with-a-non-ascii-letter"
        ),
        pytest.param(";phone-context=+1650", ("US",), id="context-without-a-number"),
        pytest.param(f"tel:{'1 ' * 300};phone-context=+1", ("US",), id="number-part-past-the-buffer"),
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
    assert PhoneNumber.parse(f"650-253-0000{' ' * 238}", regions=("US",)).national_number == "6502530000"


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
def test_regions_validate_like_phone_numbers(regions: str | tuple[str], error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        PhoneNumber.parse("650-253-0000", regions=regions)
