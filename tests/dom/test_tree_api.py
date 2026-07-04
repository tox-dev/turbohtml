"""parse()/parse_fragment() entry points and the shared Node protocol."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Doctype, Document, Element, Namespace, Node, Text, parse, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable


def test_parse_returns_document() -> None:
    doc = parse("<p>hi</p>")
    assert isinstance(doc, Document)
    assert doc.root is not None
    assert doc.root.tag == "html"


def test_root_skips_leading_non_elements() -> None:
    doc = parse("<!-- lead --><!DOCTYPE html><html><body>x</body></html>")
    assert isinstance(doc.children[0], Comment)
    assert doc.root is not None
    assert doc.root.tag == "html"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>hi</p>", "Document()", id="document"),
        pytest.param("<p>hi</p>", "Element('p')", id="element"),
        pytest.param("<p>hi</p>", "Text('hi')", id="text"),
        pytest.param("<!--note-->", "Comment('note')", id="comment"),
        pytest.param("<!DOCTYPE html>", "Doctype('html')", id="doctype"),
    ],
)
def test_repr(html: str, expected: str) -> None:
    doc = parse(html)
    node = next((candidate for candidate in doc.descendants if repr(candidate) == expected), doc)
    assert repr(node) == expected


def test_template_content_is_a_bare_node(find: Callable[[str, str], Element]) -> None:
    template = find("<template>inner</template>", "template")
    (content,) = template.children
    assert type(content) is Node
    assert repr(content) == "Node()"
    assert content.text == "inner"


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: parse(123), id="parse"),  # ty: ignore[invalid-argument-type]  # not str or bytes-like
        pytest.param(lambda: parse_fragment(b"x"), id="parse_fragment"),  # ty: ignore[invalid-argument-type]  # non-str
        # a str-typed context rejects bytes instead of latin-1-decoding it into a garbage tag name
        pytest.param(lambda: parse_fragment("<a>", b"svg"), id="context"),  # ty: ignore[invalid-argument-type]
    ],
)
def test_entry_points_reject_non_str(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()


def test_parse_fragment_context_with_lone_surrogate_is_rejected() -> None:
    with pytest.raises(UnicodeEncodeError):
        parse_fragment("<a>", "\udfff")  # a lone surrogate has no UTF-8 form


@pytest.mark.parametrize(
    "context",
    ["zzznotatag", "my-widget", "z" * 40],
    ids=["typo", "custom-element", "overlong"],
)
def test_parse_fragment_rejects_unknown_context(context: str) -> None:
    # an unknown context would silently parse in "in body" mode under a garbage root
    with pytest.raises(ValueError, match="context must be a known element tag"):
        parse_fragment("<p>", context)


@pytest.mark.parametrize(
    ("context", "tag"),
    [
        pytest.param("svg circle", "circle", id="svg-open-registry"),
        pytest.param("math mrow", "mrow", id="math-open-registry"),
        pytest.param("TABLE", "table", id="uppercase-known-tag"),
    ],
)
def test_parse_fragment_accepts_context(context: str, tag: str) -> None:
    # a namespaced foreign registry is open-ended; a known tag matches case-insensitively
    assert parse_fragment("<p/>", context).tag == tag


@pytest.mark.parametrize(
    ("html", "context", "tag", "child_tags", "text"),
    [
        pytest.param("<td>a<td>b", "tr", "tr", ["td", "td"], "ab", id="table-row-context"),
        pytest.param("<b>x</b>", "div", "div", ["b"], "x", id="default-div-context"),
        pytest.param("<path/>", "svg", "svg", ["path"], "", id="svg-context"),
        pytest.param("stray", "tbody", "tbody", [], "stray", id="fosters-stray-text-without-table"),
    ],
)
def test_parse_fragment(html: str, context: str, tag: str, child_tags: list[str], text: str) -> None:
    root = parse_fragment(html, context)
    assert isinstance(root, Element)
    assert root.tag == tag
    assert [child.tag for child in root if isinstance(child, Element)] == child_tags
    assert root.text == text


def test_parse_fragment_context_is_keyword() -> None:
    assert parse_fragment("<col>", context="colgroup").tag == "colgroup"


def test_equality_is_node_identity() -> None:
    doc = parse("<p>hi</p>")
    html, same = doc.find("html"), doc.find("html")  # distinct wrappers of one node
    assert html == same
    assert html != doc.find("body")
    assert (html == "html") is False  # a non-node is never equal
    assert (html != "html") is True


def test_ordering_is_unsupported() -> None:
    doc = parse("<p>hi</p>")
    left, right = doc.root, doc.root
    assert left is not None
    assert right is not None
    with pytest.raises(TypeError):
        _ = left < right  # ty: ignore[unsupported-operator]  # nodes are unordered on purpose


def test_hashable_by_identity() -> None:
    doc = parse("<p>hi</p>")
    seen = {doc.root, doc.find("html"), doc.find("p")}
    assert len(seen) == 2


def test_subtree_outlives_its_document(find: Callable[[str, str], Element]) -> None:
    paragraph = find("<div><p>kept</p></div>", "p")
    gc.collect()
    assert paragraph.text == "kept"


@pytest.mark.parametrize(
    "node_type",
    [
        pytest.param(Node, id="Node"),
        pytest.param(Element, id="Element"),
        pytest.param(Text, id="Text"),
        pytest.param(Comment, id="Comment"),
        pytest.param(Doctype, id="Doctype"),
        pytest.param(Document, id="Document"),
    ],
)
def test_types_are_not_constructible(node_type: type) -> None:
    with pytest.raises(TypeError):
        node_type()


def test_namespace_values() -> None:
    assert {member.value for member in Namespace} == {"html", "svg", "math"}


# find_all()/select()/iteration recycle their node wrappers on a freelist; the count
# exceeds the pool cap so dropping a result also frees past the pool's limit.
_WIDE = "<ul>" + "".join(f'<li class="row">item {index}</li>' for index in range(1200)) + "</ul>"


def test_recycled_wrapper_does_not_alias_a_held_node() -> None:
    document = parse(_WIDE)
    rows = document.find_all("li")
    held = rows[0]
    del rows  # frees every wrapper but `held`, parking them on the freelist
    gc.collect()
    rewrapped = document.find_all("li")  # pops the pooled wrappers and re-stamps them
    assert held.tag == "li"  # the held wrapper is untouched by the reuse
    assert held.attrs["class"] == ["row"]
    assert rewrapped[0] == held  # a fresh wrapper for the same node compares equal


def test_recycled_result_over_cap_stays_correct() -> None:
    document = parse(_WIDE)
    for _ in range(3):  # cycle wrappers through the pool, freeing past its cap each round
        rows = document.find_all("li")
        assert len(rows) == 1200
        assert [row.text for row in rows[:2]] == ["item 0", "item 1"]
        assert rows[-1].text == "item 1199"
        del rows
        gc.collect()


def test_transient_iteration_reads_every_node() -> None:
    # each wrapper is dropped before the next is built, the churn the pool targets
    document = parse(_WIDE)
    li_count = sum(1 for node in document.descendants if isinstance(node, Element) and node.tag == "li")
    assert li_count == 1200


def test_repeated_queries_return_equal_nodes() -> None:
    document = parse(_WIDE)
    assert document.find_all("li") == document.find_all("li")  # equal nodes despite recycled wrappers
