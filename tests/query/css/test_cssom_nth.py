from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, parse
from turbohtml.cssom import computed_style


@pytest.mark.parametrize("reverse", [pytest.param(False, id="forward"), pytest.param(True, id="reverse")])
@pytest.mark.parametrize("separator", [pytest.param("", id="adjacent"), pytest.param(" \n<!--gap-->", id="spaced")])
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":nth-child(odd)", ("red", "blue", "red", "blue"), id="child"),
        pytest.param(":nth-last-child(odd)", ("blue", "red", "blue", "red"), id="last-child"),
        pytest.param(":nth-of-type(odd)", ("red", "blue", "red", "blue"), id="type"),
        pytest.param(":nth-last-of-type(odd)", ("blue", "red", "blue", "red"), id="last-type"),
        pytest.param(":nth-child(1 of :scope)", ("red",) * 4, id="scope"),
        pytest.param(":nth-child(2 of :scope)", ("blue",) * 4, id="scope-second"),
        pytest.param(":nth-child(2 of .hit)", ("blue", "blue", "red", "blue"), id="filtered"),
    ],
)
def test_computed_style_sibling_positions(
    selector: str, expected: tuple[str, ...], separator: str, *, reverse: bool
) -> None:
    document: Final = parse(
        f"<style>i{{color:blue}}i{selector}{{color:red}}</style>"
        + separator.join(('<i class="hit"></i>', "<i></i>", '<i class="hit"></i>', "<i></i>"))
    )
    nodes: Final = document.select("i")
    assert tuple(computed_style(node)["color"] for node in (reversed(nodes) if reverse else nodes)) == (
        tuple(reversed(expected)) if reverse else expected
    )


def test_computed_style_sibling_positions_after_insertion() -> None:
    document: Final = parse("<style>i{color:blue}i:nth-child(odd){color:red}</style><i></i><i></i><i></i>")
    nodes: Final = document.select("i")
    before: Final = tuple(computed_style(node)["color"] for node in reversed(nodes))
    nodes[0].insert_before(Element("i"))
    assert (before, tuple(computed_style(node)["color"] for node in reversed(nodes))) == (
        ("red", "blue", "red"),
        ("blue", "red", "blue"),
    )


@pytest.mark.parametrize(
    "order",
    [
        pytest.param((0, 1, 2, 3), id="forward"),
        pytest.param((3, 2, 1, 0), id="reverse"),
        pytest.param((0, 3, 1, 2), id="shuffled"),
    ],
)
@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param(":nth-of-type(odd)", ("red", "blue", "red", "blue"), id="type"),
        pytest.param(":nth-last-of-type(odd)", ("blue", "red", "blue", "red"), id="last-type"),
    ],
)
def test_computed_style_sibling_types(selector: str, expected: tuple[str, ...], order: tuple[int, ...]) -> None:
    document: Final = parse(
        f"<style>i{{color:blue}}i{selector}{{color:red}}</style>" + "<i></i> <!--gap--> <b></b>" * 4
    )
    nodes: Final = document.select("i")
    assert tuple(computed_style(nodes[index])["color"] for index in order) == tuple(expected[index] for index in order)


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        pytest.param((1, 2, 0), ("red", "red", "red"), id="siblings-parent"),
        pytest.param((1, 4, 2, 5), ("red", "blue", "red", "blue"), id="different-parents"),
        pytest.param((1, 2, 4, 5, 3), ("red", "red", "blue", "blue", "blue"), id="replace-parent"),
    ],
)
def test_computed_style_inherited_sibling_values(order: tuple[int, ...], expected: tuple[str, ...]) -> None:
    document: Final = parse('<div style="color:red"><i></i><i></i></div><div style="color:blue"><i></i><i></i></div>')
    nodes: Final = document.select("div,i")
    assert tuple(computed_style(nodes[index])["color"] for index in order) == expected
