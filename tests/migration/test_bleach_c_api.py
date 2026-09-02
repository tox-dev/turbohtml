"""The bleach shim's attribute translation in C: the three shapes and the per-tag filter it binds."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from turbohtml._html import _bleach_attributes


def test_a_flat_list_admits_its_names_on_every_tag() -> None:
    assert _bleach_attributes(["href", "title"], Mapping) == ({"*": frozenset({"href", "title"})}, None)


def test_a_callable_admits_every_name_and_judges_each_value() -> None:
    names, judge = _bleach_attributes(lambda _tag, name, _value: name == "href", Mapping)
    assert names == {"*": frozenset({"*"})}
    assert judge is not None
    assert (judge("a", "href", "/x"), judge("a", "title", "t")) == ("/x", None)


def test_a_mapping_lists_names_per_tag_and_binds_its_callables() -> None:
    names, judge = _bleach_attributes({"a": lambda _tag, name, _value: name == "href", "b": ["data-z"]}, Mapping)
    assert names == {"a": frozenset({"*"}), "b": frozenset({"data-z"})}
    assert judge is not None
    assert (judge("a", "href", "/x"), judge("a", "rel", "r"), judge("b", "data-z", "1")) == ("/x", None, "1")


def test_a_wildcard_callable_is_the_fallback_for_other_tags() -> None:
    _, judge = _bleach_attributes({"*": lambda _tag, name, _value: name == "title", "a": lambda *_: True}, Mapping)
    assert judge is not None
    assert (judge("a", "x", "1"), judge("p", "title", "t"), judge("p", "x", "1")) == ("1", "t", None)


def test_a_mapping_without_callables_binds_no_filter() -> None:
    assert _bleach_attributes({"a": ["href"]}, Mapping)[1] is None


def test_a_predicate_error_propagates() -> None:
    def judge(_tag: str, _name: str, _value: str) -> bool:
        msg = "boom"
        raise RuntimeError(msg)

    _, bound = _bleach_attributes(judge, Mapping)
    assert bound is not None
    with pytest.raises(RuntimeError, match="boom"):
        bound("a", "href", "/x")


class _Undecided:
    """A verdict whose truth test raises, the way a lazy proxy might."""

    def __bool__(self) -> bool:
        msg = "undecided"
        raise RuntimeError(msg)


def test_a_verdict_that_cannot_be_judged_propagates() -> None:
    _, bound = _bleach_attributes(lambda *_: _Undecided(), Mapping)
    assert bound is not None
    with pytest.raises(RuntimeError, match="undecided"):
        bound("a", "href", "/x")


def test_the_filter_takes_three_arguments() -> None:
    _, bound = _bleach_attributes(lambda *_: True, Mapping)
    assert bound is not None
    with pytest.raises(TypeError):
        bound("a", "href")  # ty: ignore[missing-argument]  # the arity check is the point


@pytest.mark.parametrize(
    "attributes",
    [pytest.param(5, id="not-iterable"), pytest.param({"a": 5}, id="a-tag-listing-a-non-iterable")],
)
def test_a_shape_that_lists_nothing_is_rejected(attributes: object) -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(attributes, Mapping)


def test_the_entry_takes_two_arguments() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes(["href"])  # ty: ignore[missing-argument]  # the arity check is the point


def test_the_shape_test_must_be_a_type() -> None:
    with pytest.raises(TypeError):
        _bleach_attributes({"a": ["href"]}, 5)  # ty: ignore[invalid-argument-type]  # the argument check is the point
