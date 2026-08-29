from __future__ import annotations

import gc
import sys
import weakref
from itertools import starmap
from typing import TYPE_CHECKING, Final, cast

import pytest

from turbohtml import parse_fragment
from turbohtml._html import (
    _linkify_apply,
    _linkify_find,
    _linkify_has,
    _phone_config_compile,
    _phone_number_check,
    _phone_number_format,
    _phone_parse,
)
from turbohtml.clean import LinkCandidate, PhoneNumber, PhoneType
from turbohtml.clean._linkify import _PHONE_TYPES  # the spec item under test

if TYPE_CHECKING:
    from turbohtml._html import _PhoneConfig, _PhoneSpec

_SPEC: Final = cast(
    "_PhoneSpec",
    (("US",), True, False, True, True, 0, 0x7FF, ("order", "ref"), PhoneNumber, _PHONE_TYPES, False),
)


def _spec(**overrides: object) -> _PhoneSpec:
    """Overrides stay unvalidated, so the binding's own checks are what a test exercises."""
    return cast(
        "_PhoneSpec",
        tuple(
            starmap(
                overrides.get,
                zip(
                    (
                        "regions",
                        "require_valid",
                        "require_separators",
                        "skip_card_numbers",
                        "require_national_prefix",
                        "grouping",
                        "type_mask",
                        "labels",
                        "number_type",
                        "types",
                        "parsing_extensions",
                    ),
                    _SPEC,
                    strict=True,
                ),
            )
        ),
    )


def test_compile_returns_an_unconstructible_config() -> None:
    config = _phone_config_compile(_SPEC)
    assert type(config).__name__ == "_PhoneConfig"
    with pytest.raises(TypeError):
        type(config)()


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(list(_SPEC), id="list"),
        pytest.param(_SPEC[:10], id="ten-items"),
        pytest.param((*_SPEC, 0), id="twelve-items"),
        pytest.param(None, id="none"),
    ],
)
def test_compile_rejects_malformed_specs(spec: object) -> None:
    with pytest.raises(TypeError, match="tuple of 11 items"):
        _phone_config_compile(spec)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("overrides", "error", "message"),
    [
        pytest.param({"regions": ["US"]}, TypeError, "regions must be a tuple", id="regions-list"),
        pytest.param(
            {"regions": ("US", "GB", "DE", "FR", "IT", "ES", "NL", "BE", "AT")},
            ValueError,
            "at most 8",
            id="nine-regions",
        ),
        pytest.param({"regions": ("US", "US")}, ValueError, "duplicate phone region 'US'", id="repeated-region"),
        pytest.param({"regions": ("USA",)}, ValueError, "unknown phone region 'USA'", id="three-letter-region"),
        pytest.param({"regions": ("us",)}, ValueError, "unknown phone region 'us'", id="lowercase-region"),
        pytest.param({"regions": ("Us",)}, ValueError, "unknown phone region 'Us'", id="lowercase-second-letter"),
        pytest.param({"regions": ("1S",)}, ValueError, "unknown phone region '1S'", id="digit-first"),
        pytest.param({"regions": ("U1",)}, ValueError, "unknown phone region 'U1'", id="digit-second"),
        pytest.param({"regions": ("U",)}, ValueError, "unknown phone region 'U'", id="one-letter"),
        pytest.param({"regions": ("\u00dcS",)}, ValueError, "unknown phone region", id="non-ascii-first"),
        pytest.param({"regions": ("XX",)}, ValueError, "unknown phone region 'XX'", id="unknown-region"),
        pytest.param({"regions": (1,)}, TypeError, "regions must be str", id="int-region"),
        pytest.param({"require_valid": 1}, TypeError, "require_valid must be bool", id="int-flag"),
        pytest.param({"require_separators": "no"}, TypeError, "require_separators must be bool", id="str-flag"),
        pytest.param({"skip_card_numbers": None}, TypeError, "skip_card_numbers must be bool", id="none-flag"),
        pytest.param(
            {"require_national_prefix": 1}, TypeError, "require_national_prefix must be bool", id="int-prefix-flag"
        ),
        pytest.param({"grouping": "1"}, TypeError, "grouping must be int", id="str-grouping"),
        pytest.param({"parsing_extensions": 1}, TypeError, "parsing_extensions must be bool", id="int-parsing-flag"),
        pytest.param({"grouping": 3}, ValueError, "grouping must be between 0 and 2", id="grouping-high"),
        pytest.param({"grouping": -1}, ValueError, "grouping must be between 0 and 2", id="grouping-negative"),
        pytest.param(
            {"grouping": 1, "require_valid": False},
            ValueError,
            "only be checked with require_valid",
            id="grouping-possible",
        ),
        pytest.param({"type_mask": 0}, ValueError, "1..0x7FF", id="mask-zero"),
        pytest.param({"type_mask": 0x800}, ValueError, "1..0x7FF", id="mask-too-wide"),
        pytest.param({"type_mask": True}, TypeError, "type mask must be int", id="mask-bool"),
        pytest.param({"type_mask": "7"}, TypeError, "type mask must be int", id="mask-str"),
        pytest.param(
            {"type_mask": 2, "require_valid": False}, ValueError, "require_valid", id="selective-mask-in-possible-mode"
        ),
        pytest.param({"labels": ["order"]}, TypeError, "labels must be a tuple", id="labels-list"),
        pytest.param(
            {"labels": tuple(f"l{index:03d}" for index in range(257))}, ValueError, "at most 256", id="257-labels"
        ),
        pytest.param({"labels": ("",)}, ValueError, "phone label ''", id="empty-label"),
        pytest.param({"labels": ("abcdefghijklm",)}, ValueError, "phone label", id="13-byte-label"),
        pytest.param({"labels": ("Order",)}, ValueError, "phone label 'Order'", id="uppercase-label"),
        pytest.param({"labels": ("ünit",)}, ValueError, "phone label", id="non-ascii-label"),
        pytest.param({"labels": ("ref", "order")}, ValueError, "sorted and distinct", id="unsorted-labels"),
        pytest.param({"labels": ("ref", "ref")}, ValueError, "sorted and distinct", id="repeated-label"),
        pytest.param({"labels": ("ref", 1)}, TypeError, "labels must be str", id="int-label"),
        pytest.param({"number_type": object()}, TypeError, "must be a class", id="number-type-instance"),
        pytest.param({"number_type": int}, AttributeError, "_from_native", id="number-type-without-factory"),
        pytest.param(
            {"number_type": type("Odd", (), {"_from_native": 3})}, TypeError, "callable", id="factory-not-callable"
        ),
        pytest.param({"types": _PHONE_TYPES[:11]}, TypeError, "12 members", id="eleven-types"),
        pytest.param({"types": list(_PHONE_TYPES)}, TypeError, "12 members", id="types-list"),
    ],
)
def test_compile_rejects_each_malformed_field(
    overrides: dict[str, object], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        _phone_config_compile(_spec(**overrides))


def test_compile_accepts_the_bounds() -> None:
    assert (
        _phone_config_compile(
            _spec(
                regions=("US", "GB", "DE", "FR", "IT", "ES", "NL", "BE"),
                labels=tuple(
                    "l" + "".join("abcdefghij"[int(digit)] for digit in f"{index:03d}") for index in range(256)
                ),
                type_mask=1,
            )
        )
        is not None
    )


_NEEDS_GC_COLLECT: Final = pytest.mark.skipif(
    sys.implementation.name == "pypy", reason="PyPy frees a cycle on its own schedule, not on gc.collect()"
)


@_NEEDS_GC_COLLECT
def test_config_keeps_a_function_scoped_subclass_alive() -> None:
    def build() -> tuple[_PhoneConfig, weakref.ReferenceType[type[PhoneNumber]]]:
        class Local(PhoneNumber):
            __slots__ = ()

        return _phone_config_compile(_spec(number_type=Local)), weakref.ref(Local)

    config, alive = build()
    gc.collect()
    assert alive() is not None
    spans = _linkify_find("650-253-0000", False, False, (), (), ("http",), config)  # ruff:ignore[boolean-positional-value-in-call]  # positional C binding
    assert type(spans[0][4]) is alive()
    del spans
    del config
    gc.collect()
    assert alive() is None


@_NEEDS_GC_COLLECT
def test_config_cycle_through_a_class_attribute_is_collectable() -> None:
    def build() -> weakref.ReferenceType[type[PhoneNumber]]:
        class Holder(PhoneNumber):
            __slots__ = ()

        Holder.config = _phone_config_compile(_spec(number_type=Holder))  # ty: ignore[unresolved-attribute]
        return weakref.ref(Holder)

    class_ref = build()
    assert class_ref() is not None
    gc.collect()
    assert class_ref() is None


class _Raising(PhoneNumber):
    __slots__ = ()

    @classmethod
    def _from_native(cls, *fields: object) -> PhoneNumber:
        msg = f"refused {fields[0]}"
        raise RuntimeError(msg)


class _Foreign(PhoneNumber):
    __slots__ = ()

    @classmethod
    def _from_native(cls, *_fields: object) -> PhoneNumber:
        return object()  # ty: ignore[invalid-return-type]  # the wrong type is the point


def test_factory_exception_propagates_from_find() -> None:
    config = _phone_config_compile(_spec(number_type=_Raising))
    with pytest.raises(RuntimeError, match="refused 1"):
        _linkify_find("650-253-0000", False, False, (), (), ("http",), config)  # ruff:ignore[boolean-positional-value-in-call]


def test_factory_returning_another_type_is_a_type_error() -> None:
    config = _phone_config_compile(_spec(number_type=_Foreign))
    with pytest.raises(TypeError, match="_from_native must return"):
        _linkify_find("650-253-0000", False, False, (), (), ("http",), config)  # ruff:ignore[boolean-positional-value-in-call]


def test_factory_exception_propagates_from_apply() -> None:
    config = _phone_config_compile(_spec(number_type=_Raising))
    with pytest.raises(RuntimeError, match="refused 1"):
        _linkify_apply(parse_fragment("call 650-253-0000"), (), False, (), ("http",), False, (), LinkCandidate, config)  # ruff:ignore[boolean-positional-value-in-call]


def test_has_never_calls_the_factory() -> None:
    config = _phone_config_compile(_spec(number_type=_Raising))
    assert _linkify_has("650-253-0000", False, False, (), (), ("http",), config) is True  # ruff:ignore[boolean-positional-value-in-call]


@pytest.mark.parametrize(
    "phones", [pytest.param((), id="tuple"), pytest.param(1, id="int"), pytest.param(object(), id="object")]
)
def test_entry_points_reject_a_foreign_config(phones: object) -> None:
    with pytest.raises(TypeError, match="_PhoneConfig or None"):
        _linkify_find("x", False, False, (), (), ("http",), phones)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="_PhoneConfig or None"):
        _linkify_has("x", False, False, (), (), ("http",), phones)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError, match="_PhoneConfig or None"):
        _linkify_apply(parse_fragment("x"), (), False, (), ("http",), False, (), LinkCandidate, phones)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]


def test_entry_points_accept_none() -> None:
    assert _linkify_find("650-253-0000", False, False, (), (), ("http",), None) == []  # ruff:ignore[boolean-positional-value-in-call]
    assert _linkify_has("650-253-0000", False, False, (), (), ("http",), None) is False  # ruff:ignore[boolean-positional-value-in-call]


def test_entry_points_need_the_phones_argument() -> None:
    with pytest.raises(TypeError):
        _linkify_find("x", False, False, (), (), ("http",))  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[missing-argument]
    with pytest.raises(TypeError):
        _linkify_has("x", False, False, (), (), ("http",))  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[missing-argument]


class _NoPhoneSlot:
    __slots__ = ("attrs", "existing", "text", "url")

    def __init__(self, url: str, text: str, attrs: dict[str, str] | None = None) -> None:
        self.url = url
        self.text = text
        self.attrs = attrs if attrs is not None else {}
        self.existing = False


class _NoExistingSlot:
    __slots__ = ("attrs", "phone", "text", "url")

    def __init__(self, url: str, text: str, attrs: dict[str, str] | None = None) -> None:
        self.url = url
        self.text = text
        self.attrs = attrs if attrs is not None else {}
        self.phone = None


class _Refusing:
    def __init__(self, url: str, *_fields: object) -> None:
        msg = f"no candidate for {url}"
        raise RuntimeError(msg)


def test_apply_reports_a_candidate_type_without_a_phone_slot() -> None:
    root = parse_fragment("call 650-253-0000")
    with pytest.raises(AttributeError, match="phone"):
        _linkify_apply(root, (), False, (), ("http",), False, (), _NoPhoneSlot, _phone_config_compile(_SPEC))  # ruff:ignore[boolean-positional-value-in-call]


def test_apply_reports_a_candidate_type_without_an_existing_slot() -> None:
    root = parse_fragment('<a href="/x">link</a>')
    with pytest.raises(AttributeError, match="existing"):
        _linkify_apply(root, (), False, (), ("http",), True, (), _NoExistingSlot, None)  # ruff:ignore[boolean-positional-value-in-call]


def test_apply_propagates_a_refusing_candidate_constructor() -> None:
    with pytest.raises(RuntimeError, match=r"no candidate for http://example\.com"):
        _linkify_apply(parse_fragment("see example.com"), (), False, (), ("http",), False, (), _Refusing, None)  # ruff:ignore[boolean-positional-value-in-call]


def test_find_span_shape_for_a_phone() -> None:
    spans = _linkify_find("call 650-253-0000 x12", False, False, (), (), ("http",), _phone_config_compile(_SPEC))  # ruff:ignore[boolean-positional-value-in-call]
    assert spans == [
        (5, 21, 4, "tel:+16502530000;ext=12", PhoneNumber(1, "6502530000", "12", "US", PhoneType.FIXED_LINE_OR_MOBILE))
    ]


@pytest.mark.parametrize(
    "args",
    [
        pytest.param((1, "6502530000", "US"), id="three-args"),
        pytest.param((1, "6502530000", "US", 10, 0), id="five-args"),
        pytest.param((True, "6502530000", "US", 10), id="bool-code"),
        pytest.param(("1", "6502530000", "US", 10), id="str-code"),
        pytest.param((1.0, "6502530000", "US", 10), id="float-code"),
        pytest.param((1, b"6502530000", "US", 10), id="bytes-nsn"),
        pytest.param((1, 6502530000, "US", 10), id="int-nsn"),
        pytest.param((1, "6502530000", b"US", 10), id="bytes-region"),
        pytest.param((1, "6502530000", 1, 10), id="int-region"),
        pytest.param((1, "6502530000", "US", True), id="bool-type"),
        pytest.param((1, "6502530000", "US", "10"), id="str-type"),
    ],
)
def test_number_check_argument_types(args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        _phone_number_check(*args)  # ty: ignore[invalid-argument-type]  # the wrong types are the point


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param((1, "6502530000", "US", 12), "between 0 and 11", id="type-twelve"),
        pytest.param((1, "6502530000", "US", -1), "between 0 and 11", id="type-negative"),
        pytest.param((1, "1" * 18, "US", 10), "2-17 digits", id="eighteen-digits-rejected-before-lookup"),
        pytest.param((1, "1" * 16, "US", 10), "no number", id="sixteen-digits-reach-the-plan"),
        pytest.param((1, "65O2530000", "US", 10), "ASCII digits", id="letter"),
        pytest.param((2**70, "6502530000", "US", 10), "between 1 and 999", id="huge-code"),
    ],
)
def test_number_check_value_errors(args: tuple[object, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _phone_number_check(*args)  # ty: ignore[invalid-argument-type]  # the wrong types are the point


def test_number_check_accepts_a_produced_value() -> None:
    assert _phone_number_check(1, "6502530000", "US", 10) is None


@pytest.mark.parametrize(
    ("args", "error", "message"),
    [
        pytest.param(("1", "6502530000", None, 1), TypeError, "country_code and style must be int", id="code-str"),
        pytest.param((1, "6502530000", None, "1"), TypeError, "country_code and style must be int", id="style-str"),
        pytest.param((1, "6502530000", 12, 1), TypeError, "extension must be str or None", id="extension-int"),
        pytest.param((0, "6502530000", None, 1), ValueError, "between 1 and 999", id="code-zero"),
        pytest.param((1000, "6502530000", None, 1), ValueError, "between 1 and 999", id="code-high"),
        pytest.param((1, "6502530000", None), TypeError, "takes exactly 4 arguments", id="three-arguments"),
        pytest.param((1, "6502530000", None, 4), ValueError, "style must be between 0 and 3", id="style-high"),
        pytest.param((1, "6502530000", None, -1), ValueError, "style must be between 0 and 3", id="style-negative"),
        pytest.param((1, "65O2530000", None, 1), ValueError, "ASCII digits", id="nsn-letter"),
        pytest.param((1, "6502530000", "", 1), ValueError, "extension must be 1-20", id="extension-empty"),
        pytest.param((1, "6502530000", "1" * 21, 1), ValueError, "extension must be 1-20", id="extension-long"),
        pytest.param((1, "6502530000", "\udc80", 1), UnicodeEncodeError, "surrogates", id="extension-surrogate"),
    ],
)
def test_number_format_rejects_bad_arguments(args: tuple[object, ...], error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        _phone_number_format(*args)  # ty: ignore[invalid-argument-type]  # the wrong types are the point


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param((999, "6502530000", None, 1), "6502530000", id="unassigned-code"),
        pytest.param((1, "1234", None, 2), "1234", id="no-format-takes-a-first-digit-1"),
        pytest.param((1, "65", None, 2), "65", id="shorter-than-every-format"),
        pytest.param((1, "65025300001", None, 2), "65025300001", id="longer-than-every-format"),
        pytest.param((49, "15", None, 2), "15", id="shorter-than-a-leading-digits-pattern"),
        pytest.param((1, "1234", "5", 3), "tel:+1-1234;ext=5", id="rfc3966-keeps-the-extension"),
    ],
)
def test_number_format_falls_back_to_the_bare_national_number(args: tuple[object, ...], expected: str) -> None:
    assert _phone_number_format(*args) == expected  # ty: ignore[invalid-argument-type]  # a mixed tuple of the row


def test_number_format_writes_each_style() -> None:
    assert [_phone_number_format(1, "6502530000", "12", style) for style in range(4)] == [
        "+16502530000",
        "+1 650-253-0000 ext. 12",
        "(650) 253-0000 ext. 12",
        "tel:+1-650-253-0000;ext=12",
    ]


_CONFIG: Final = object()  # stands for the compiled configuration in a parametrized argument list


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param((_CONFIG,), "takes exactly 2 arguments", id="one-argument"),
        pytest.param((None, "650-253-0000"), "config must be a _PhoneConfig", id="config-none"),
        pytest.param((_CONFIG, b"650-253-0000"), "text must be str", id="text-bytes"),
    ],
)
def test_parse_rejects_bad_arguments(args: tuple[object, ...], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        _phone_parse(*(_phone_config_compile(_SPEC) if item is _CONFIG else item for item in args))  # ty: ignore[invalid-argument-type]  # the wrong shapes are the point


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("no digits here", None, id="none"),
        pytest.param("650-253-0000", "+16502530000", id="number"),
    ],
)
def test_parse_returns_the_number_or_none(text: str, expected: str | None) -> None:
    number = _phone_parse(_phone_config_compile(_SPEC), text)
    assert (None if number is None else number.international_number) == expected
