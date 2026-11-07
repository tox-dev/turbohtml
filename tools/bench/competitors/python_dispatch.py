"""Use native Nodes on both sides to isolate the cost of checked Python dispatch."""

from __future__ import annotations

from functools import cache, partial
from typing import TYPE_CHECKING, Final

from turbohtml import Node, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable

REQUIREMENTS = ("turbohtml>=1.8",)


def _dispatch(count: int) -> Node:
    return _pipeline(count)()


@cache
def _pipeline(count: int) -> partial[Node]:
    return partial(transform_node, parse_fragment("<p>x</p>"), *(_identity,) * count)


def transform_node(node: Node, /, *steps: Callable[[Node], Node | None]) -> Node:
    # Mirror root replacement and error handling without native dispatch.
    if not isinstance(node, Node):
        message: Final = "node must be a Node"
        raise TypeError(message)
    for index, step in enumerate(steps, 1):
        if (result := step(node)) is None:
            continue
        if not isinstance(result, Node):
            message = f"stage {index} returned {type(result).__name__}; expected Node or None"
            raise TypeError(message)
        node = result
    return node


def _identity(node: Node) -> Node:
    return node


OPERATIONS = {"transform-dispatch": (_dispatch, "Python (validated)")}

__all__ = [
    "OPERATIONS",
    "transform_node",
]
