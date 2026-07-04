"""parse()/parse_fragment() entry points and the shared Node protocol."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import pytest

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    Namespace,
    Node,
    ProcessingInstruction,
    Text,
    parse,
    parse_fragment,
)

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


def test_equals_is_structural_while_eq_stays_identity() -> None:
    left = parse("<!DOCTYPE html><p class='a'>hi</p>")
    right = parse("<!DOCTYPE html><p class='a'>hi</p>")
    assert left.equals(right)  # same markup: structurally equal
    assert left != right  # but distinct nodes: == is identity, not structure
    assert left is not right


def test_equals_within_one_tree_ignores_attribute_order() -> None:
    doc = parse("<ul><li id=x class=a>t</li><li class=a id=x>t</li></ul>")
    first, second = doc.find_all("li")  # two nodes sharing one tree and handle
    assert first.equals(second)


def test_equals_treats_valueless_attribute_as_empty_value() -> None:
    # per the DOM an attribute with no value carries the empty string
    assert Element("input", {"disabled": None}).equals(Element("input", {"disabled": ""}))


def test_equals_is_namespace_aware() -> None:
    svg_anchor = parse("<svg><a></a></svg>").find("a")
    html_anchor = parse("<a></a>").find("a")
    assert svg_anchor is not None
    assert html_anchor is not None
    assert not svg_anchor.equals(html_anchor)  # same tag name, different namespace


def test_equals_matches_documents_with_the_same_doctype() -> None:
    markup = "<!DOCTYPE html><html><body><p>hi</p></body></html>"
    assert parse(markup).equals(parse(markup))


def test_equals_distinguishes_doctype_names() -> None:
    assert not parse("<!DOCTYPE html>").equals(parse("<!DOCTYPE svg>"))


def test_equals_distinguishes_doctype_identifiers() -> None:
    plain = parse("<!DOCTYPE html>")
    legacy = parse('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN">')
    assert not plain.equals(legacy)  # a present public id differs from none


def test_equals_matches_template_content() -> None:
    markup = "<template><p>x</p></template>"
    assert parse(markup).equals(parse(markup))


def test_equals_rejects_non_node() -> None:
    with pytest.raises(TypeError):
        parse("<p></p>").equals("not a node")  # ty: ignore[invalid-argument-type]  # other must be a node


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(Element("div"), Element("span"), id="tag"),
        pytest.param(Element("a", {"href": "/x"}), Element("a", {"href": "/y"}), id="attr-value"),
        pytest.param(Element("a", {"href": "/x"}), Element("a", {"href": "/xyz"}), id="attr-value-length"),
        pytest.param(Element("a", {"href": "/x"}), Element("a"), id="attr-presence"),
        pytest.param(Element("a", {"href": "/x"}), Element("a", {"rel": "/x"}), id="attr-name"),
        pytest.param(
            Element("div", None, [Element("a")]),
            Element("div", None, [Element("a"), Element("b")]),
            id="fewer-children",
        ),
        pytest.param(
            Element("div", None, [Element("a"), Element("b")]),
            Element("div", None, [Element("a")]),
            id="more-children",
        ),
        pytest.param(
            Element("p", None, [Element("a"), Element("b")]),
            Element("p", None, [Element("b"), Element("a")]),
            id="child-order",
        ),
        pytest.param(Text("x"), Text("y"), id="text"),
        pytest.param(Text("x"), Comment("x"), id="node-type"),
        pytest.param(Comment("x"), Comment("yy"), id="comment"),
        pytest.param(CData("x"), CData("y"), id="cdata"),
        pytest.param(ProcessingInstruction("xml", "x"), ProcessingInstruction("t", "x"), id="pi-target"),
        pytest.param(ProcessingInstruction("t", "x"), ProcessingInstruction("t", "y"), id="pi-data"),
    ],
)
def test_equals_rejects_structural_differences(left: Node, right: Node) -> None:
    assert not left.equals(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(Element("div", {"a": "1", "b": "2"}), Element("div", {"b": "2", "a": "1"}), id="attr-order"),
        pytest.param(Text(""), Text(""), id="empty-text"),
        pytest.param(Text("same"), Text("same"), id="text"),
        pytest.param(Comment("same"), Comment("same"), id="comment"),
        pytest.param(CData("same"), CData("same"), id="cdata"),
        pytest.param(ProcessingInstruction("t", "d"), ProcessingInstruction("t", "d"), id="pi"),
        pytest.param(
            Element("ul", None, [Element("li", None, [Text("x")])]),
            Element("ul", None, [Element("li", None, [Text("x")])]),
            id="nested",
        ),
    ],
)
def test_equals_accepts_structural_matches(left: Node, right: Node) -> None:
    assert left.equals(right)


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
