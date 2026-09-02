"""The linkify configuration folding in C: name lists, region codes, the E.164 check and the type mask."""

from __future__ import annotations

import pytest

from turbohtml._html import _linkify_fold, _phone_e164, _phone_regions
from turbohtml.clean import LinkDetector, PhoneNumber, PhoneNumbers, PhoneType


@pytest.mark.parametrize(
    ("values", "kind", "expected"),
    [
        pytest.param(["Pre", "code", "pre"], 0, ("code", "pre"), id="lowercased-deduplicated-sorted"),
        pytest.param([".Dev", "dev", "app"], 1, ("app", "dev"), id="a-leading-dot-falls-off-a-tld"),
        pytest.param(["Git:", "ssh::", "git"], 2, ("git", "ssh"), id="trailing-colons-fall-off-a-scheme"),
        pytest.param([" Fax ", "tel", "fax"], 3, ("fax", "tel"), id="words-are-stripped"),
        pytest.param([], 0, (), id="nothing"),
        pytest.param(["."], 1, ("",), id="a-lone-dot-is-an-empty-tld"),
        pytest.param([""], 1, ("",), id="an-empty-tld"),
        pytest.param([":", "::"], 2, ("",), id="lone-colons-are-an-empty-scheme"),
    ],
)
def test_fold(values: list[str], kind: int, expected: tuple[str, ...]) -> None:
    assert _linkify_fold(values, "field", kind) == expected


def test_fold_names_the_field_in_its_error() -> None:
    with pytest.raises(TypeError, match="skip_tags entries must be str"):
        _linkify_fold(["pre", 5], "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_fold_takes_three_arguments() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(["a"], "field")  # ty: ignore[missing-argument]  # the arity check is the point


def test_fold_needs_an_iterable() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(5, "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_strip_uppercase_and_keep_first_seen_order() -> None:
    assert _phone_regions([" gb", "US", "gb", "de "]) == ("GB", "US", "DE")


def test_a_non_ascii_region_is_left_alone() -> None:
    # `ß` would otherwise fold to South Sudan's `SS`
    assert _phone_regions(["ß", "us"]) == ("ß", "US")


def test_regions_entries_must_be_str() -> None:
    with pytest.raises(TypeError, match="regions entries must be str"):
        _phone_regions(["US", 1])  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_need_an_iterable() -> None:
    with pytest.raises(TypeError):
        _phone_regions(5)  # ty: ignore[invalid-argument-type]  # the check is the point


@pytest.mark.parametrize(
    ("country_code", "national", "expected"),
    [
        pytest.param(44, "2079460958", "+442079460958", id="fits"),
        pytest.param(1, "2" * 14, "+1" + "2" * 14, id="exactly-fifteen-digits"),
        pytest.param(1, "2" * 15, None, id="one-digit-too-many"),
    ],
)
def test_e164(country_code: int, national: str, expected: str | None) -> None:
    assert _phone_e164(country_code, national) == expected


def test_e164_rejects_a_non_str_number() -> None:
    with pytest.raises(TypeError):
        _phone_e164(1, 5)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_the_public_e164_reads_the_same() -> None:
    number = PhoneNumber.parse("+44 20 7946 0958")
    assert number.e164 == "+442079460958"


def test_the_type_mask_restricts_the_detector() -> None:
    settings = PhoneNumbers(regions=("GB",), types=frozenset({PhoneType.MOBILE}))
    found = LinkDetector(phones=settings).find("call 020 7946 0958 or 07400 123456")
    assert [span.text for span in found] == ["07400 123456"]
