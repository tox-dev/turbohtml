"""Node navigation: parents, siblings, descendants, and the sequence protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Element, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable

_SAMPLE = "<body><p id=a>one<b>bold</b></p><p id=b>two</p></body>"


@pytest.fixture
def body(find: Callable[[str, str], Element]) -> Element:
    return find(_SAMPLE, "body")


def test_parent_chain(body: Element) -> None:
    bold = body.find("b")
    assert bold is not None
    assert isinstance(bold.parent, Element)
    assert bold.parent.attrs["id"] == "a"


def test_document_has_no_parent() -> None:
    assert parse("<p>x</p>").parent is None


def test_children_are_a_tuple(body: Element) -> None:
    assert isinstance(body.children, tuple)
    assert [child.attrs["id"] for child in body.children if isinstance(child, Element)] == ["a", "b"]


def test_siblings(body: Element) -> None:
    first = body[0]
    assert isinstance(first, Element)
    second = first.next_sibling
    assert isinstance(second, Element)
    assert second.attrs["id"] == "b"
    assert second.previous_sibling == first
    assert first.previous_sibling is None
    assert second.next_sibling is None


def test_descendants_are_document_order(body: Element) -> None:
    def label(node: object) -> str:
        if isinstance(node, Element):
            return f"Element:{node.tag}"
        assert isinstance(node, Text)
        return f"Text:{node.data}"

    order = [label(node) for node in body.descendants]
    assert order == ["Element:p", "Text:one", "Element:b", "Text:bold", "Element:p", "Text:two"]


def test_descendants_is_lazy_iterator(body: Element) -> None:
    walker = body.descendants
    assert iter(walker) is walker
    assert isinstance(next(walker), Element)


def test_descendants_survives_extracting_the_cached_next_node() -> None:
    # extracting the node the cursor has cached as "next" must not crash the walk (issue #81):
    # its parent chain no longer reaches the root, so the iteration ends instead of dereferencing NULL.
    div = parse("<div><a></a><b></b></div>").find("div")
    assert div is not None
    walker = iter(div.descendants)
    first = next(walker)  # yields <a>; the cursor now caches <b> as the next node
    assert isinstance(first, Element)
    assert first.tag == "a"
    cached_next = div.find("b")
    assert cached_next is not None
    cached_next.extract()
    tags = [node.tag for node in walker if isinstance(node, Element)]  # completes without segfaulting
    assert tags in ([], ["b"])  # ends at once, or yields the now-detached <b> once and then stops


def test_ancestors_reach_the_document(body: Element) -> None:
    bold = body.find("b")
    assert bold is not None
    chain = [node.tag if isinstance(node, Element) else "#document" for node in bold.ancestors]
    assert chain == ["p", "body", "html", "#document"]


def test_len_and_indexing(body: Element) -> None:
    paragraph = body[0]
    assert len(paragraph) == 2
    assert isinstance(paragraph[0], Text)
    assert isinstance(paragraph[-1], Element)


def test_index_out_of_range(body: Element) -> None:
    with pytest.raises(IndexError):
        _ = body[0][5]


def test_negative_index_out_of_range(body: Element) -> None:
    # a negative subscript past the start is out of range, as it is for a list, rather than the first child
    with pytest.raises(IndexError):
        _ = body[0][-3]


def test_iteration_yields_children(body: Element) -> None:
    paragraph = body[0]
    assert list(paragraph) == list(paragraph.children)


@pytest.mark.parametrize(
    ("html", "selector", "child_count"),
    [
        pytest.param("<p>x</p>", "p", 1, id="with-children"),
        pytest.param("<br>", "br", 0, id="empty-void"),
    ],
)
def test_node_is_truthy_regardless_of_children(
    find: Callable[[str, str], Element], html: str, selector: str, child_count: int
) -> None:
    node = find(html, selector)
    assert bool(node) is True
    assert len(node) == child_count
