from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Axis, Element, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from work_steps import WorkSteps

    from turbohtml import Node

# parse_xml places no cap on nesting, so the origin can sit arbitrarily deep below its ancestors
_DEPTH: Final[int] = 200_000
_EVERY_A: Final[list[str]] = ["a"] * _DEPTH


@pytest.mark.parametrize(
    ("walk", "tags"),
    [
        pytest.param(lambda target: target.find_all(axis=Axis.PRECEDING), [*_EVERY_A, "title"], id="find-all"),
        pytest.param(lambda target: target.find_all(text="x", axis=Axis.PRECEDING), _EVERY_A, id="text-scan"),
        pytest.param(lambda target: target.preceding, [*_EVERY_A, "title"], id="preceding"),
        pytest.param(lambda target: target.xpath_iter("preceding::*[last()]"), ["title"], id="xpath"),
    ],
)
def test_preceding_axis_from_a_deep_origin_skips_its_ancestors(
    walk: Callable[[Element], Iterable[Node | str | float | bool]], tags: list[str]
) -> None:
    assert [node.tag for node in walk(_deep_target(_DEPTH)) if isinstance(node, Element)] == tags


def _deep_target(depth: int) -> Element:
    # <t> sits under depth <b> ancestors, with depth <a> elements and a <title> before them
    target = parse_xml(f"<r><title/>{'<a>x</a>' * depth}{'<b>' * depth}<t/>{'</b>' * depth}</r>").find("t")
    assert isinstance(target, Element)
    return target


def test_find_preceding_from_a_deep_origin_reaches_the_title() -> None:
    found = _deep_target(_DEPTH).find("title", axis=Axis.PRECEDING)
    assert found is not None
    assert found.tag == "title"


@pytest.mark.parametrize(
    "walk",
    [
        pytest.param(lambda target: target.find_all(axis=Axis.PRECEDING), id="find-all"),
        pytest.param(lambda target: target.find_all(text="x", axis=Axis.PRECEDING), id="text-scan"),
        pytest.param(lambda target: list(target.preceding), id="preceding"),
        pytest.param(lambda target: target.xpath("preceding::*"), id="xpath"),
    ],
)
def test_preceding_axis_from_a_deep_origin_scales_linearly(
    walk: Callable[[Element], object], work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda depth: partial(walk, _deep_target(depth)), (2_000, 8_000))
