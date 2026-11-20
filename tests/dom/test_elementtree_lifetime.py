from __future__ import annotations

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, etree
from turbohtml.etree import document_context, fromstring, tostring

if TYPE_CHECKING:
    from collections.abc import Iterator

    from turbohtml.etree import ElementView


def test_element_tree_weak_callback_keeps_replacement_cached() -> None:
    node: Final[Element] = Element("p")
    original = etree.ElementView(node)
    replacements: Final[list[ElementView]] = []

    def recreate(_reference: weakref.ReferenceType[ElementView]) -> None:
        replacements.append(etree.ElementView(node))

    reference: Final[weakref.ReferenceType[ElementView]] = weakref.ref(original, recreate)
    del original
    gc.collect()
    assert (reference(), etree.ElementView(node)) == (None, replacements[0])


def test_element_tree_concurrent_wrapping_preserves_identity() -> None:
    node: Final[Element] = Element("p")
    start: Final[Barrier] = Barrier(4)

    def wrap(_: int) -> list[ElementView]:
        start.wait()
        return [etree.ElementView(node) for _ in range(100)]

    with ThreadPoolExecutor(max_workers=4) as executor:
        views: Final[list[ElementView]] = [view for batch in executor.map(wrap, range(4)) for view in batch]
    assert (len(set(views)), views[0].node) == (1, node)


def test_element_tree_tree_type_requires_factory() -> None:
    tree: Final[ElementView] = fromstring("<p>text</p>")
    with pytest.raises(TypeError, match="cannot create"):
        type(tree.getroottree())()


def test_element_tree_iterator_type_requires_factory() -> None:
    tree: Final[ElementView] = fromstring("<p>text</p>")
    with pytest.raises(TypeError, match="cannot create"):
        type(iter(tree))()


def test_element_tree_context_type_requires_factory() -> None:
    with pytest.raises(TypeError, match="cannot create"):
        type(document_context(lambda: None))()


@pytest.mark.parametrize(
    ("method", "args"),
    [pytest.param("iterchildren", (), id="children"), pytest.param("iter", ("p",), id="descendants")],
)
def test_element_tree_shared_iterator_visits_each_element_once(method: str, args: tuple[str, ...]) -> None:
    tree: Final[ElementView] = fromstring(f"""<div>{"".join(f'<p id="{index}"></p>' for index in range(1000))}</div>""")
    iterator = getattr(tree, method)(*args)
    start: Final[Barrier] = Barrier(4)

    def consume(_: int) -> list[str]:
        start.wait()
        return [element.get("id", "missing") for element in iterator]

    with ThreadPoolExecutor(max_workers=4) as executor:
        identifiers: Final[list[str]] = [value for values in executor.map(consume, range(4)) for value in values]
    assert sorted(identifiers, key=int) == [str(index) for index in range(1000)]


def test_element_tree_context_resets_after_exception() -> None:
    tree: Final[ElementView] = fromstring("<div><b>first</b><p>last</p></div>")
    child: Final[ElementView] = tree[0]

    @document_context
    def detach() -> None:
        tree.remove(child)
        msg: Final[str] = "extraction failed"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="extraction failed"):
        detach()
    assert (child.getroottree().getroot(), child.find("//p")) == (child, None)


def test_element_tree_root_retains_owner() -> None:
    tree = etree.Element("p")
    root = tree.getroottree()
    reference: Final[weakref.ReferenceType[ElementView]] = weakref.ref(tree)
    del tree
    gc.collect()
    owner: Final[ElementView | None] = reference()
    assert owner is not None
    assert root.getroot() is owner


def test_element_tree_nested_context_retains_outer_document() -> None:
    tree: Final[ElementView] = fromstring("<div><b>first</b><p>last</p></div>")
    child: Final[ElementView] = tree[0]
    last: Final[ElementView] = tree[1]

    @document_context
    def find_sibling() -> ElementView | None:
        return child.find("//p")

    @document_context
    def detach() -> ElementView | None:
        tree.remove(child)
        return find_sibling()

    assert detach() is last


def test_element_tree_iterator_retains_tree_after_owner_release() -> None:
    tree = fromstring("<div><p>one</p><p>two</p></div>")
    iterator: Final[Iterator[ElementView]] = tree.iterchildren()
    reference: Final[weakref.ReferenceType[ElementView]] = weakref.ref(tree)
    del tree
    gc.collect()
    assert (reference(), [element.text for element in iterator]) == (None, ["one", "two"])


def test_element_tree_context_releases_detached_nodes() -> None:
    tree: Final[ElementView] = fromstring("<div><p>one</p><p>two</p></div>")

    @document_context
    def detach() -> weakref.ReferenceType[ElementView]:
        child: Final[ElementView] = tree[0]
        reference: Final[weakref.ReferenceType[ElementView]] = weakref.ref(child)
        tree.remove(child)
        return reference

    reference: Final[weakref.ReferenceType[ElementView]] = detach()
    gc.collect()
    assert (reference(), tostring(tree, encoding="unicode")) == (None, "<div><p>two</p></div>")
