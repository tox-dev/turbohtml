from __future__ import annotations

from typing import Final

import pytest

from turbohtml._html import css_declaration_index, css_declaration_text

_ITEMS: Final[tuple[tuple[str, str, bool], ...]] = (
    ("color", "red", False),
    ("margin", "0", True),
    ("color", "blue", False),
)


def test_the_index_keeps_first_seen_order_and_the_last_value() -> None:
    assert css_declaration_index(_ITEMS) == {"color": 2, "margin": 1}
    assert list(css_declaration_index(_ITEMS)) == ["color", "margin"]


def test_the_index_of_nothing_is_empty() -> None:
    assert css_declaration_index(()) == {}


def test_the_text_serializes_in_source_order_with_the_flag() -> None:
    assert css_declaration_text(_ITEMS) == "color: red; margin: 0 !important; color: blue"


def test_the_text_propagates_a_flag_whose_truth_test_raises() -> None:
    with pytest.raises(RuntimeError, match="undecided"):
        css_declaration_text((("color", "red", _Undecided()),))  # ty: ignore[invalid-argument-type]  # tests the flag's truth check


class _Undecided:
    def __bool__(self) -> bool:
        msg = "undecided"
        raise RuntimeError(msg)


def test_the_text_of_nothing_is_empty() -> None:
    assert not css_declaration_text(())


@pytest.mark.parametrize(
    "items",
    [
        pytest.param([("color", "red", False)], id="a-list"),
        pytest.param((("color", "red"),), id="a-pair"),
        pytest.param(((1, "red", False),), id="a-non-str-name"),
        pytest.param((("color", 2, False),), id="a-non-str-value"),
        pytest.param((("color", "red", False, 1),), id="a-non-str-raw-name"),
        pytest.param(("color",), id="a-bare-str"),
    ],
)
def test_the_entries_reject_malformed_items(
    items: list[tuple[str, str, bool]]
    | tuple[tuple[str, str]]
    | tuple[tuple[int, str, bool]]
    | tuple[tuple[str, int, bool]]
    | tuple[tuple[str, str, bool, int]]
    | tuple[str],
) -> None:
    with pytest.raises(TypeError):
        css_declaration_index(items)  # ty: ignore[invalid-argument-type]  # tests the C argument guard
    with pytest.raises(TypeError):
        css_declaration_text(items)  # ty: ignore[invalid-argument-type]  # tests the C argument guard
