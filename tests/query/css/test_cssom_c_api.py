"""The StyleDeclaration accessors' C entry points: the winning-declaration index and the serializer."""

from __future__ import annotations

import pytest

from turbohtml._html import _css_declaration_index, _css_declaration_text

_ITEMS = (("color", "red", False), ("margin", "0", True), ("color", "blue", False))


def test_the_index_keeps_first_seen_order_and_the_last_value() -> None:
    assert _css_declaration_index(_ITEMS) == {"color": 2, "margin": 1}
    assert list(_css_declaration_index(_ITEMS)) == ["color", "margin"]


def test_the_index_of_nothing_is_empty() -> None:
    assert _css_declaration_index(()) == {}


def test_the_text_serializes_in_source_order_with_the_flag() -> None:
    assert _css_declaration_text(_ITEMS) == "color: red; margin: 0 !important; color: blue"


class _Undecided:
    """A flag whose truth test raises, the way a lazy proxy might."""

    def __bool__(self) -> bool:
        msg = "undecided"
        raise RuntimeError(msg)


def test_the_text_propagates_a_flag_whose_truth_test_raises() -> None:
    with pytest.raises(RuntimeError, match="undecided"):
        _css_declaration_text((("color", "red", _Undecided()),))  # ty: ignore[invalid-argument-type]


def test_the_text_of_nothing_is_empty() -> None:
    assert not _css_declaration_text(())


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([("color", "red", False)], id="a-list"),
        pytest.param((("color", "red"),), id="a-pair"),
        pytest.param(((1, "red", False),), id="a-non-str-name"),
        pytest.param((("color", 2, False),), id="a-non-str-value"),
        pytest.param(("color",), id="a-bare-str"),
    ],
)
def test_the_entries_reject_malformed_items(items: object) -> None:
    with pytest.raises(TypeError):
        _css_declaration_index(items)  # ty: ignore[invalid-argument-type]  # the argument check is the point
    with pytest.raises(TypeError):
        _css_declaration_text(items)  # ty: ignore[invalid-argument-type]  # the argument check is the point
