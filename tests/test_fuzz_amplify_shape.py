"""How the super-linear lane expands a shape: each kind, the counter, the byte budget, and the shapes it refuses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fuzz.amplify import Shape

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("shape", "size", "expected"),
    [
        pytest.param(Shape("repeat", "ab", head="<", tail=">"), 8, "<ababab>", id="repeat"),
        pytest.param(Shape("repeat", "ab"), 7, "ababab", id="repeat-never-splits-a-piece"),
        pytest.param(Shape("sandwich", "[", middle="x", right="]"), 7, "[[[x]]]", id="sandwich"),
        pytest.param(Shape("sandwich", "(", right="))"), 9, "(((" + "))" * 3, id="sandwich-uneven-sides"),
        pytest.param(Shape("tree", "-", middle="a"), 10, "a-a--a---a", id="tree-interleaves-growing-prefixes"),
        pytest.param(Shape("repeat", "é"), 5, "éé", id="budget-counts-utf8-bytes"),
    ],
)
def test_shape_render(shape: Shape, size: int, expected: str) -> None:
    assert shape.render(size) == expected


@pytest.mark.parametrize(
    ("shape", "size", "expected"),
    [
        pytest.param(Shape("repeat", "<b c{n}>", counter=True), 30, "<b c000000><b c000001>", id="repeat"),
        pytest.param(
            Shape("sandwich", "<i{n}>", right="</i{n}>", counter=True),
            40,
            "<i000000><i000001></i000001></i000000>",
            id="sandwich-closes-in-reverse",
        ),
        pytest.param(Shape("tree", "-", middle="{n};", counter=True), 20, "000000;-000001;", id="tree"),
        pytest.param(Shape("repeat", "{n}"), 9, "{n}{n}{n}", id="placeholder-kept-without-counter"),
    ],
)
def test_shape_render_numbers_each_repetition(shape: Shape, size: int, expected: str) -> None:
    assert shape.render(size) == expected


@pytest.mark.parametrize(
    ("build", "message"),
    [
        pytest.param(lambda: Shape("spiral", "a"), "shape kind 'spiral' is not one of", id="unknown-kind"),
        pytest.param(lambda: Shape("repeat", ""), "non-empty left piece", id="empty-left"),
        pytest.param(lambda: Shape("repeat", "a", right="b"), "repeat shape has no right", id="repeat-right"),
        pytest.param(lambda: Shape("tree", "a", right="b"), "tree shape has no right", id="tree-right"),
        pytest.param(lambda: Shape("repeat", "a", middle="b"), "repeat shape has no middle", id="repeat-middle"),
        pytest.param(lambda: Shape("repeat", "a" * 129), "left exceed 128 bytes", id="long-left"),
        pytest.param(lambda: Shape("repeat", "a", tail="é" * 65), "tail exceed 128 bytes", id="long-tail-bytes"),
    ],
)
def test_shape_rejects(build: Callable[[], Shape], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build()


def test_shape_accepts_a_piece_of_exactly_128_bytes() -> None:
    assert Shape("repeat", "a" * 128).render(256) == "a" * 256
