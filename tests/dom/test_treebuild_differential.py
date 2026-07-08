"""parse_into cross-checked by rebuilding into a real lxml.etree tree.

lxml is a bench dependency, not a test one, and ships no wheels for 3.15, the free-threaded builds, or Windows, so this
module importorskips itself where the oracle is absent and is omitted from the coverage gate (see ``[tool.coverage]``).
It still runs and validates wherever lxml installs.

The check retargets the parser at lxml's node model through :func:`turbohtml.treebuild.parse_into` -- elements, their
attributes, text threaded through ``.text``/``.tail``, and comments -- then asserts the reconstructed lxml tree flattens
to the very sequence ``turbohtml.parse`` builds for the same source. A faithful builder round-trip through a foreign
tree library is the property under test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

from turbohtml import Comment, Doctype, Element, Text, parse
from turbohtml.treebuild import parse_into

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from turbohtml import Node

etree = pytest.importorskip("lxml.etree")

Event = tuple[object, ...]


class _LxmlNode(Protocol):
    """The slice of lxml's element interface the builder and the flatten touch; lxml ships no stubs ty can read."""

    text: str | None
    tail: str | None

    @property
    def tag(self) -> object: ...
    @property
    def attrib(self) -> Mapping[str, str]: ...
    def set(self, key: str, value: str) -> None: ...
    def append(self, child: object) -> None: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> _LxmlNode: ...
    def __iter__(self) -> Iterator[_LxmlNode]: ...


class LxmlBuilder:
    """Retarget the parser at real lxml nodes; parse_into binds each method per instance, hence the PLR6301 waivers."""

    def create_document(self) -> object:  # noqa: PLR6301
        return etree.Element("document")

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> object:  # noqa: ARG002, PLR6301
        return ("doctype",)

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> object:  # noqa: ARG002, PLR6301
        node = etree.Element(name)
        for attr_name, value in attrs:
            node.set(attr_name, value or "")
        return node

    def create_text(self, data: str) -> object:  # noqa: PLR6301
        return ("text", data)

    def create_comment(self, data: str) -> object:  # noqa: PLR6301
        return etree.Comment(data)

    def create_pi(self, data: str) -> object:  # noqa: PLR6301
        return etree.Comment(data)

    def append(self, parent: object, child: object) -> None:  # noqa: PLR6301
        node = cast("_LxmlNode", parent)
        if isinstance(child, tuple):
            if child[0] == "text":
                data = str(child[1])
                if len(node) == 0:
                    node.text = (node.text or "") + data
                else:
                    node[-1].tail = (node[-1].tail or "") + data
            return
        node.append(child)


def _flatten_lxml(node: object, out: list[Event]) -> None:
    element = cast("_LxmlNode", node)
    if element.tag is etree.Comment:
        out.append(("comment", element.text or ""))
        return
    out.append(("element", element.tag, tuple(sorted(element.attrib.items()))))
    if element.text:
        out.append(("text", element.text))
    for child in element:
        _flatten_lxml(child, out)
        if child.tail:
            out.append(("text", child.tail))


def _attr_value(value: str | list[str] | None) -> str:
    """Normalize turbohtml's attribute view -- a token list for ``class``, None for valueless -- to the raw string."""
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def _flatten_turbo(node: Node, out: list[Event]) -> None:
    for child in node.children:
        if isinstance(child, Doctype):
            continue
        if isinstance(child, Comment):
            out.append(("comment", child.data))
        elif isinstance(child, Text):
            out.append(("text", child.data))
        else:
            assert isinstance(child, Element)
            attrs = tuple(sorted((name, _attr_value(value)) for name, value in child.attrs.items()))
            out.append(("element", child.tag, attrs))
            _flatten_turbo(child, out)


_CORPUS = [
    pytest.param("<!DOCTYPE html><title>t</title><p id=a class=lead>hi<b>x</b>tail</p>", id="basic"),
    pytest.param("<ul><li>a<li>b</ul><ol><li>c</li></ol>", id="implied-close"),
    pytest.param("<table><tr><td>c</td></tr></table>", id="table"),
    pytest.param("<div><!--note-->text<span>y</span>after</div>", id="comment-and-tail"),
    pytest.param("<p><b><i>abc</p>def", id="adoption-agency"),
    pytest.param("<section><input disabled><img src=x alt=y></section>", id="void-and-valueless"),
]


@pytest.mark.parametrize("markup", _CORPUS)
def test_parse_into_rebuilds_the_tree_in_lxml(markup: str) -> None:
    built = cast("_LxmlNode", parse_into(markup, LxmlBuilder()))
    mine: list[Event] = []
    _flatten_lxml(built[0], mine)
    theirs: list[Event] = []
    _flatten_turbo(parse(markup), theirs)
    assert mine == theirs
