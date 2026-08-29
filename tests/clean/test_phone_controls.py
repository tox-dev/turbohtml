from __future__ import annotations

import dataclasses
from collections import deque
from typing import TYPE_CHECKING, TypedDict

import pytest

from turbohtml.clean import (
    DEFAULT_PHONE_LABELS,
    LinkDetector,
    Linker,
    Linkify,
    PhoneGrouping,
    PhoneNumbers,
    PhoneType,
    linkify,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class _Codes:
    """A caller's own iterable: neither a sequence nor a set."""

    def __init__(self, *codes: str) -> None:
        self._codes = codes

    def __iter__(self) -> Iterator[str]:
        yield from self._codes


def _failing_codes() -> Iterator[str]:
    yield "US"
    msg = "boom"
    raise RuntimeError(msg)


def test_defaults() -> None:
    assert PhoneNumbers() == PhoneNumbers(
        regions=(),
        require_valid=True,
        require_separators=False,
        skip_card_numbers=True,
        require_national_prefix=True,
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
def test_settings_rejections(settings: dict[str, object], error: type[Exception], message: str) -> None:
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
def test_regions_normalize_in_order(regions: object, expected: tuple[str, ...]) -> None:
    assert PhoneNumbers(regions=regions).regions == expected  # ty: ignore[invalid-argument-type]


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
def test_regions_reject_unordered_or_non_str(regions: object) -> None:
    with pytest.raises(TypeError, match="regions"):
        PhoneNumbers(regions=regions)  # ty: ignore[invalid-argument-type]


def test_regions_generator_failure_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        PhoneNumbers(regions=_failing_codes())


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
        for name in ("require_valid", "require_separators", "skip_card_numbers", "require_national_prefix")
    ],
)
@pytest.mark.parametrize(
    "value", [pytest.param(1, id="int"), pytest.param("yes", id="str"), pytest.param(None, id="none")]
)
def test_flags_must_be_bool(name: str, value: object) -> None:
    with pytest.raises(TypeError, match=name):
        PhoneNumbers(**{name: value})  # ty: ignore[invalid-argument-type]


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
def test_types_freeze(types: object, expected: frozenset[PhoneType] | None) -> None:
    assert PhoneNumbers(types=types).types == expected  # ty: ignore[invalid-argument-type]


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
def test_types_rejections(types: object, require_valid: bool, error: type[Exception], message: str) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # a parametrize value
    with pytest.raises(error, match=message):
        PhoneNumbers(types=types, require_valid=require_valid)  # ty: ignore[invalid-argument-type]


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
def test_labels_normalize(labels: object, expected: tuple[str, ...]) -> None:
    assert PhoneNumbers(ignore_numbers_after=labels).ignore_numbers_after == expected  # ty: ignore[invalid-argument-type]


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
def test_labels_rejections(labels: object, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        PhoneNumbers(ignore_numbers_after=labels)  # ty: ignore[invalid-argument-type]


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
        PhoneNumbers().regions = ("US",)  # ty: ignore[invalid-assignment]


def test_value_is_hashable() -> None:
    assert len({PhoneNumbers(regions=["US"]), PhoneNumbers(regions=("us",))}) == 1


@pytest.mark.parametrize(
    "phones",
    [
        pytest.param(object(), id="object"),
        pytest.param({}, id="dict"),
        pytest.param("US", id="str"),
        pytest.param(True, id="bool"),
    ],
)
def test_consumers_reject_non_phone_numbers(phones: object) -> None:
    message = "phones must be PhoneNumbers or None"
    with pytest.raises(TypeError, match=message):
        Linker(Linkify(phones=phones))  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=message):
        LinkDetector(phones=phones)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match=message):
        linkify("650-253-0000", Linkify(phones=phones))  # ty: ignore[invalid-argument-type]


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


def _spans(text: str, settings: _Settings) -> list[str]:
    return [span.url for span in LinkDetector(phones=PhoneNumbers(regions=("US",), **settings)).find(text)]


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
    assert _spans(text, kwargs) == expected


@pytest.mark.parametrize(
    ("skip", "expected"),
    [
        pytest.param(True, [], id="skipped"),
        pytest.param(False, ["tel:+494111", "tel:+49111111111111"], id="kept-as-the-oracle-splits-them"),
    ],
)
def test_skip_card_numbers(skip: bool, expected: list[str]) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
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
