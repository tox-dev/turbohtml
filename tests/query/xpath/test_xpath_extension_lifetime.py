from __future__ import annotations

import gc
import sys
from functools import partial
from typing import TYPE_CHECKING, Final, cast
from weakref import ref

import pytest

from turbohtml import Element, XPath

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from types import SimpleNamespace


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
def test_xpath_extension_keeps_source_arena(*, compiled: bool) -> None:
    source: Final = Element("section")
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> float:
        target.append(source)
        return 41.0

    extensions: Final[dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]]] = {
        (None, "adopt"): adopt
    }
    result: Final = (
        XPath("adopt() + count(.)", extensions=extensions)(source)
        if compiled
        else source.xpath("adopt() + count(.)", extensions=extensions)
    )
    assert (result, target.children) == (42.0, (source,))


def test_xpath_extension_keeps_cached_program() -> None:
    source: Final = Element("section")

    def evict(_context: SimpleNamespace) -> float:
        for index in range(80):
            source.xpath(str(index))
        return 41.0

    assert source.xpath("evict() + 1", extensions={(None, "evict"): evict}) == pytest.approx(42.0)


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
def test_xpath_extension_keeps_removed_callable(*, compiled: bool) -> None:
    source: Final = Element("section")
    extensions: Final[
        dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]]
    ] = {}

    def remove(_context: SimpleNamespace) -> bool:
        extensions.clear()
        return function() is not None

    extensions[None, "remove"] = partial(remove)
    function: Final = ref(extensions[None, "remove"])
    result: Final = (
        XPath("remove()", extensions=extensions)(source)
        if compiled
        else source.xpath("remove()", extensions=extensions)
    )
    gc.collect()
    assert (result, function()) == (True, None)


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
def test_xpath_extension_validates_yielded_owner(*, compiled: bool) -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    target: Final = Element("main")

    def move_after_yield(_context: SimpleNamespace) -> Iterator[Element]:
        assert isinstance(child, Element)
        yield child
        target.append(child)

    extensions: Final[dict[tuple[str | None, str], Callable[..., str | float | bool | Element | Iterable[Element]]]] = {
        (None, "move"): move_after_yield
    }
    invoke: Final = (
        partial(XPath("move()", extensions=extensions), source)
        if compiled
        else partial(source.xpath, "move()", extensions=extensions)
    )
    with pytest.raises(ValueError, match="different document"):
        invoke()
    assert target.children == (child,)


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2, 4, 32])
def test_xpath_extension_snapshots_arguments(offset: int) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    source: Final = Element("section", children=[Element("a", {"id": "old"}), Element("b")])
    first, second = source.children
    target: Final = Element("main")
    changed = False

    def inspect(_context: SimpleNamespace, attrs: list[str], nodes: list[Element]) -> bool:
        return attrs == ["old"] and nodes == [second]

    extensions: Final[dict[tuple[str | None, str], Callable[..., bool]]] = {(None, "inspect"): inspect}
    selector: Final = XPath("inspect(.//a/@id, .//b)", extensions=extensions)
    context: Final = (source,)

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.append(source)
            assert isinstance(first, Element)
            first.attrs["id"] = "changed"

    reserve: Final[list[list[None]]] = [[] for _ in range(512)]
    gc.callbacks.append(adopt)
    try:
        gc.set_threshold(gc.get_count()[0] + offset, thresholds[1], thresholds[2])
        gc.enable()
        result: Final = selector(*context)
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(adopt)
        gc.set_threshold(*thresholds)
        restore_gc()
        reserve.clear()
    assert (changed, result, target.children) == (True, True, (source,))


@pytest.mark.parametrize(
    ("kind", "error", "match"),
    [
        ("type", TypeError, "extension result must be"),
        ("owner", ValueError, "different document"),
        ("iterator", LookupError, "iteration resumed"),
    ],
    ids=["type", "owner", "iterator"],
)
def test_xpath_extension_validates_iterator(kind: str, error: type[Exception], match: str) -> None:
    source: Final = Element("section")

    def invalid(_context: SimpleNamespace) -> Iterator[Element]:
        yield cast("Element", 42) if kind == "type" else Element("other") if kind == "owner" else source
        msg = "iteration resumed"
        raise LookupError(msg)

    with pytest.raises(error, match=match):
        source.xpath("invalid()", extensions={(None, "invalid"): invalid})
