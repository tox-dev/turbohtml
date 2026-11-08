from __future__ import annotations

from functools import partial
from types import FunctionType
from typing import TYPE_CHECKING, Final, Protocol, TypeIs

import pytest
from bench.competitors.python_dispatch import OPERATIONS
from bench.competitors.python_dispatch import transform_node as python_transform_node

from turbohtml import Comment, Element, Node, Text
from turbohtml.clean import collapse_whitespace_node, transform_node

if TYPE_CHECKING:
    from collections.abc import Callable

    from bench.operations import Timed


@pytest.mark.parametrize("count", [0, 1, 4, 16], ids=["empty", "one", "four", "sixteen"])
def test_dispatch_benchmark(count: int) -> None:
    operation: Final = OPERATIONS["transform-dispatch"][0]
    assert _is_dispatch_operation(operation)
    root: Final = operation(count)
    assert (root.inner_html, operation(count) is root) == ("<p>x</p>", True)


def _is_dispatch_operation(operation: Timed) -> TypeIs[Callable[[int], Node]]:
    return isinstance(operation, FunctionType) and operation.__name__ == "_dispatch"


class _Dispatch(Protocol):
    def __call__(self, node: object, /, *stages: object) -> Node: ...


@pytest.fixture(params=[transform_node, python_transform_node], ids=["native", "python"])
def dispatch(request: pytest.FixtureRequest) -> _Dispatch:
    return request.param


def test_dispatch_empty(dispatch: _Dispatch) -> None:
    root: Final = Text("value")
    assert dispatch(root) is root


@pytest.mark.parametrize("kind", ["function", "partial", "bound-method"])
def test_dispatch_stages(dispatch: _Dispatch, kind: str) -> None:
    def uppercase(node: Node) -> None:
        assert isinstance(node, Text)
        node.data = node.text.upper()

    def replace(node: Node) -> Node:
        return Text(f"{node.text}  end  ")

    stage: Final = {"function": uppercase, "partial": partial(uppercase), "bound-method": partial(uppercase).__call__}[
        kind
    ]
    root: Final = Text(" a   b ")
    result: Final = dispatch(root, stage, replace, collapse_whitespace_node)
    assert (root.text, result.text, result is root) == (" A   B ", " A B end ", False)


def test_dispatch_invalid_root(dispatch: _Dispatch) -> None:
    with pytest.raises(TypeError, match="node must be a Node"):
        dispatch("text")


def test_dispatch_invalid_result(dispatch: _Dispatch) -> None:
    def invalid(_node: Node) -> str:
        return "text"

    root: Final = Text("a   b")
    with pytest.raises(TypeError, match="stage 2 returned str; expected Node or None"):
        dispatch(root, collapse_whitespace_node, invalid)
    assert root.text == "a b"


def test_dispatch_exception(dispatch: _Dispatch) -> None:
    failure: Final = ValueError("stage failure")

    def fail(_node: Node) -> Node:
        raise failure

    root: Final = Text("a   b")
    with pytest.raises(ValueError, match="stage failure") as caught:
        dispatch(root, collapse_whitespace_node, fail)
    assert (root.text, caught.value is failure) == ("a b", True)


def test_dispatch_noncallable(dispatch: _Dispatch) -> None:
    with pytest.raises(TypeError, match="not callable"):
        dispatch(Text("a"), 1)


@pytest.mark.parametrize("replacement_type", [Text, Comment, Element], ids=["text", "comment", "element"])
def test_dispatch_replacement_type(dispatch: _Dispatch, replacement_type: Callable[[str], Node]) -> None:
    replacement: Final = replacement_type("p")

    def replace(_node: Node) -> Node:
        return replacement

    assert dispatch(Text("original"), replace, lambda node: node) is replacement
