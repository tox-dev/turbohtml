from __future__ import annotations

import gc
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    DocumentFragment,
    Element,
    IncrementalParser,
    Namespace,
    Node,
    ProcessingInstruction,
    SourceSpan,
    Text,
    parse,
    parse_fragment,
    parse_xml,
)
from turbohtml.query import Query

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from turbohtml._internal._locations import SourceLocation


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


def test_template_content_is_a_document_fragment(find: Callable[[str, str], Element]) -> None:
    template = find("<template>inner</template>", "template")
    (content,) = template.children
    assert (type(content), repr(content), content.text) == (DocumentFragment, "DocumentFragment()", "inner")


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
        pytest.param("svg foreignObject", "foreignObject", id="svg-mixed-case"),
        pytest.param("svg clippath", "clipPath", id="svg-lowercase-adjusted"),
        pytest.param("svg CIRCLE", "circle", id="svg-unadjusted-lowercased"),
        pytest.param("math mI", "mi", id="math-lowercased"),
    ],
)
def test_parse_fragment_accepts_context(context: str, tag: str) -> None:
    # a namespaced foreign registry is open-ended, a known tag matches case-insensitively, and an SVG name takes
    # the mixed case the SVG element name adjustments give a parsed element
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
        pytest.param(Element("div", None, [Element("a")]), Element("div"), id="child-presence"),
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


@pytest.mark.parametrize(
    "released", [pytest.param(0, id="first"), pytest.param(1, id="middle"), pytest.param(2, id="last")]
)
def test_identity_survives_alias_release_and_adoption(released: int) -> None:
    source: Final = Element("main", children=[Element("p")])
    aliases: Final = [source.children[0] for _ in range(3)]
    original_hash: Final = hash(aliases[0])
    del aliases[released]
    gc.collect()
    destination: Final = Element("aside")
    destination.append(aliases[0])
    assert (len({*aliases, destination.children[0]}), hash(aliases[1])) == (1, original_hash)


@pytest.mark.parametrize(
    "count", [pytest.param(1, id="one"), pytest.param(12, id="inline"), pytest.param(32, id="expanded")]
)
@pytest.mark.parametrize("keep", [pytest.param(False, id="released"), pytest.param(True, id="retained")])
def test_adoption_preserves_descendants_after_alias_release(count: int, *, keep: bool) -> None:
    source: Final = Element("section")
    source.set_inner_html("<span>text</span>" * count)
    aliases: Final = source.select("span")
    retained: Final = aliases[-1:] if keep else []
    aliases.clear()
    target: Final = Element("main")
    target.append(source)
    if retained:
        retained[0].attrs["live"] = "yes"
    assert target.inner_html == "<section>" + "<span>text</span>" * (count - 1) + (
        '<span live="yes">text</span></section>' if keep else "<span>text</span></section>"
    )


@pytest.mark.parametrize("operation", ["find", "indexed", "css", "xpath", "parent"])
def test_element_result_types_after_adoption(operation: str) -> None:
    source: Final = parse('<section><a data-x="yes"><b></b></a><a data-x="yes"><b></b></a></section>')
    section: Final = source.select("section")[0]
    expected: Final = section.select("a")
    target: Final = parse("<main></main>")
    target.select("main")[0].append(section)
    if operation == "find":
        result = target.find_all(attrs={"data-x": True})
    elif operation == "indexed":
        result = target.find_all("a")
    elif operation == "css":
        result = target.select("a")
    elif operation == "xpath":
        result = cast("list[Element]", target.xpath("//a"))
    else:
        result = list(Query(target.select("b")).parent())
    assert [(type(node), node) for node in result] == [(Element, node) for node in expected]


def test_hash_survives_release_of_all_adopted_aliases() -> None:
    source: Final = Element("main", children=[Element("p")])
    original_hash: Final = hash(source.children[0])
    destination: Final = Element("aside")
    destination.append(source.children[0])
    gc.collect()
    assert hash(destination.children[0]) == original_hash


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


_DOC = "<section><h2>T</h2><p>one</p><ul><li>a</li><li>b</li></ul></section><footer>f</footer>"


def _tags(nodes: Iterable[Node]) -> list[str]:
    return [node.tag for node in nodes if isinstance(node, Element)]


def _find(html: str, tag: str) -> Element:
    element = parse(html).find(tag)
    assert element is not None
    return element


@pytest.mark.parametrize(
    ("tag", "axis", "expected"),
    [
        pytest.param("h2", "next_siblings", ["p", "ul"], id="next-forward"),
        pytest.param("ul", "next_siblings", [], id="next-at-end"),
        pytest.param("ul", "previous_siblings", ["p", "h2"], id="previous-nearest-first"),
        pytest.param("h2", "previous_siblings", [], id="previous-at-start"),
    ],
)
def test_sibling_axes(tag: str, axis: str, expected: list[str]) -> None:
    assert _tags(getattr(_find(_DOC, tag), axis)) == expected


def test_following_excludes_own_subtree() -> None:
    following = list(_find(_DOC, "h2").following)
    assert _tags(following) == ["p", "ul", "li", "li", "footer"]
    # the heading's own text child is part of its subtree, so it is not "following"
    assert "T" not in [node.data for node in following if isinstance(node, Text)]


def test_following_at_document_end_is_empty() -> None:
    assert _tags(_find(_DOC, "footer").following) == []


def test_preceding_is_reverse_order_excluding_ancestors() -> None:
    preceding = _find(_DOC, "ul").preceding
    # nearest first, and the enclosing <section>/<body>/<html> ancestors are absent
    assert _tags(preceding) == ["p", "h2", "head"]


def test_preceding_of_root_is_empty() -> None:
    root = parse(_DOC).root
    assert root is not None
    assert list(root.preceding) == []


@pytest.mark.parametrize(
    ("axis", "expected"),
    [
        pytest.param("strings", [" hi ", "  ", "bye"], id="strings-verbatim"),
        pytest.param("stripped_strings", ["hi", "bye"], id="stripped-skips-blank-runs"),
    ],
)
def test_text_iterators(axis: str, expected: list[str]) -> None:
    div = _find("<div><p> hi </p><p>  </p><p>bye</p></div>", "div")
    assert list(getattr(div, axis)) == expected


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


@pytest.mark.parametrize("count", [1, 29, 30, 100], ids=["small", "below-index", "index", "large"])
@pytest.mark.parametrize("position", ["first", "last"])
def test_duplicate_normalized_attributes_keep_the_first(count: int, position: str) -> None:
    ordinary: Final = {f"data-{index}": "é水😀" for index in range(count)}
    duplicates: Final = {"A": "x", "a": "y"}
    kept: Final = {"a": "x"}
    attributes: Final = duplicates | ordinary if position == "first" else ordinary | duplicates
    expected: Final = kept | ordinary if position == "first" else ordinary | kept
    assert Element("div", attributes).equals(Element("div", expected))


@pytest.mark.parametrize("prefix", ["data-", "é", "水", "𐐀"], ids=["ascii", "latin1", "ucs2", "ucs4"])
@pytest.mark.parametrize("different", [False, True], ids=["equal", "different"])
def test_xml_attribute_equality(prefix: str, *, different: bool) -> None:
    left: Final = parse_xml("<root " + " ".join(f'{prefix}{index}="{index}"' for index in range(40)) + "/>")
    right: Final = parse_xml(
        "<root "
        + " ".join(f'{prefix}{index}="{index + int(different and index == 39)}"' for index in reversed(range(40)))
        + "/>"
    )
    assert left.equals(right) is not different


@pytest.mark.parametrize("different", [False, True], ids=["equal", "different"])
def test_attribute_equality_with_sparse_atoms(*, different: bool) -> None:
    left: Final = Element("div")
    right: Final = Element("div")
    for index in range(40):
        left.attrs[f"data-{index}"] = str(index)
        right.attrs[f"data-{index}"] = str(index + int(different and index == 39))
        for filler in range(127):
            right.attrs[f"unused-{index}-{filler}"] = ""
            del right.attrs[f"unused-{index}-{filler}"]
    assert left.equals(right) is not different


@pytest.mark.parametrize("different", [False, True], ids=["equal", "different"])
def test_large_attribute_equality_within_one_tree(*, different: bool) -> None:
    document: Final = parse(
        "<div "
        + " ".join(f'data-{index}="{index}"' for index in range(40))
        + "></div><div "
        + " ".join(f'data-{index}="{index + int(different and index == 39)}"' for index in reversed(range(40)))
        + "></div>"
    )
    left, right = document.find_all("div")
    assert left.equals(right) is not different


def test_duplicate_attributes_reach_index_threshold_at_last_name() -> None:
    names: Final = [
        "".join(letter.upper() if variant & (1 << index) else letter for index, letter in enumerate("abcdef"))
        for variant in range(30)
    ]
    left: Final = Element("div", dict.fromkeys(names, "same") | {"penultimate": "x", "last": "y"})
    right: Final = Element(
        "div", {names[0]: "same", "penultimate": "x"} | dict.fromkeys(names[1:], "same") | {"last": "y"}
    )
    assert left.equals(right)


@pytest.mark.parametrize("different", [False, True], ids=["equal", "different"])
def test_many_duplicate_attributes_preserve_first_value(*, different: bool) -> None:
    names: Final = [
        "".join(letter.upper() if variant & (1 << index) else letter for index, letter in enumerate("abcdefghij"))
        for variant in range(1000)
    ]
    duplicates: Final = dict.fromkeys(names, "same")
    anchors: Final = {"first": "1", "second": "2", "third": "3"}
    left: Final = Element("div", anchors | duplicates)
    right: Final = Element("div", duplicates | anchors)
    if different:
        right.attrs[names[0]] = "different"
    assert left.equals(right) is not different


def child(root: Node, tag: str) -> Element:
    """Find a descendant by tag, narrowing the Element | None result for the checker."""
    found = root.find(tag)
    assert found is not None
    return found


def location(html: str, tag: str) -> SourceLocation:
    """Parse with source locations and return the first matching element's record."""
    element = parse(html, source_locations=True).find(tag)
    assert element is not None
    loc = element.source_location
    assert loc is not None
    return loc


def offsets(span: SourceSpan) -> tuple[int, int]:
    """The half-open code-point range a span covers."""
    return span.start_offset, span.end_offset


def test_start_tag_span_covers_open_angle_through_close_angle() -> None:
    # 1-based line, 0-based column, code-point offset, at both endpoints
    assert location("<div>x</div>", "div").start_tag == SourceSpan(1, 0, 0, 1, 5, 5)


def test_end_tag_span_covers_the_close_tag() -> None:
    loc = location("<div>x</div>", "div")
    assert loc.end_tag is not None
    assert offsets(loc.end_tag) == (6, 12)


def test_attribute_span_slices_the_source() -> None:
    html = '<div id=x class="a b">y</div>'
    loc = location(html, "div")
    assert {name: offsets(span) for name, span in loc.attrs.items()} == {"id": (5, 9), "class": (10, 21)}
    assert html[5:9] == "id=x"
    assert html[10:21] == 'class="a b"'


@pytest.mark.parametrize(
    ("html", "attr", "sliced"),
    [
        pytest.param("<x a>t</x>", "a", "a", id="valueless"),
        pytest.param("<x a=1>t</x>", "a", "a=1", id="unquoted"),
        pytest.param('<x a="1">t</x>', "a", 'a="1"', id="double-quoted"),
        pytest.param("<x a='1'>t</x>", "a", "a='1'", id="single-quoted"),
        pytest.param('<x a="">t</x>', "a", 'a=""', id="empty-quoted"),
        pytest.param("<x a=b&amp;c>t</x>", "a", "a=b&amp;c", id="unquoted-charref"),
    ],
)
def test_attribute_span_shapes(html: str, attr: str, sliced: str) -> None:
    span = location(html, "x").attrs[attr]
    assert html[span.start_offset : span.end_offset] == sliced


def test_two_unquoted_attributes_end_at_their_own_delimiters() -> None:
    html = "<x a=1 b=2>t</x>"
    attrs = location(html, "x").attrs
    assert html[attrs["a"].start_offset : attrs["a"].end_offset] == "a=1"
    assert html[attrs["b"].start_offset : attrs["b"].end_offset] == "b=2"


def test_duplicate_attribute_keeps_the_first_occurrence() -> None:
    html = "<x a=1 a=2>t</x>"
    attrs = location(html, "x").attrs
    assert list(attrs) == ["a"]
    assert html[attrs["a"].start_offset : attrs["a"].end_offset] == "a=1"


def test_element_without_attributes_has_an_empty_attrs_map() -> None:
    assert location("<div>x</div>", "div").attrs == {}


def test_void_element_has_no_end_tag() -> None:
    loc = location("<img src=a.png>", "img")
    assert loc.end_tag is None
    assert offsets(loc.start_tag) == (0, 15)
    assert offsets(loc.attrs["src"]) == (5, 14)


def test_self_closing_void_element_has_no_end_tag() -> None:
    assert location("<br/>", "br").end_tag is None


def test_implicitly_closed_element_has_no_end_tag() -> None:
    # the run of text ends the <p> at EOF, with no </p> in the source
    assert location("<p>hi<div>x</div>", "p").end_tag is None


def test_multiline_spans_carry_line_and_column() -> None:
    loc = location("<p>a\n<b\n c=1>x</b>", "b")
    assert (loc.start_tag.start_line, loc.start_tag.start_col) == (2, 0)
    assert (loc.start_tag.end_line, loc.start_tag.end_col) == (3, 5)
    span = loc.attrs["c"]
    assert (span.start_line, span.start_col) == (3, 1)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("café", id="bmp-2byte"),
        pytest.param("\U0001f600", id="astral-4byte"),
    ],
)
def test_wide_storage_values_do_not_break_offsets(value: str) -> None:
    html = f'<x a="{value}">t</x>'
    span = location(html, "x").attrs["a"]
    assert html[span.start_offset : span.end_offset] == f'a="{value}"'


def test_source_locations_imply_positions() -> None:
    element = child(parse("<div>x</div>", source_locations=True), "div")
    assert element.position == (1, 0)
    assert element.source_location is not None


def test_off_by_default_reports_none() -> None:
    assert child(parse("<div>x</div>"), "div").source_location is None


def test_positions_only_still_reports_none() -> None:
    # line/col tracking on, but the granular record was not requested
    assert child(parse("<div>x</div>", positions=True), "div").source_location is None


def test_source_locations_force_positions_even_with_positions_false() -> None:
    # source_locations subsumes line/col, so it keeps position tracking on despite positions=False
    element = child(parse("<div>x</div>", positions=False, source_locations=True), "div")
    assert element.source_location is not None
    assert element.position == (1, 0)


def test_fragment_source_locations_force_positions() -> None:
    root = parse_fragment("<div>x</div>", "body", positions=False, source_locations=True)
    assert child(root, "div").source_location is not None
    assert child(root, "div").position == (1, 0)


def test_incremental_source_locations_force_positions() -> None:
    parser = IncrementalParser(positions=False, source_locations=True)
    parser.feed("<div>x</div>")
    element = child(parser.close(), "div")
    assert element.source_location is not None
    assert element.position == (1, 0)


def test_synthetic_elements_have_no_location() -> None:
    doc = parse("<p>x</p>", source_locations=True)
    assert child(doc, "html").source_location is None
    assert child(doc, "head").source_location is None
    assert child(doc, "body").source_location is None
    assert child(doc, "p").source_location is not None


def test_explicit_end_tag_on_a_synthetic_element_is_ignored() -> None:
    # <head> is implied by <title>; its explicit </head> closes a synthetic element,
    # which carries no location, so nothing is attached
    doc = parse("<title>t</title></head><p>x</p>", source_locations=True)
    assert child(doc, "head").source_location is None


@pytest.mark.parametrize(
    ("html", "tag", "close"),
    [
        # </body> and </html> switch insertion mode instead of popping their element, and </form> is
        # removed out of stack order; each stamps the end tag through its own path, not the pop path
        pytest.param("<html><body>x</body></html>", "body", "</body>", id="body"),
        pytest.param("<html><body>x</body></html>", "html", "</html>", id="html"),
        pytest.param("<form><input></form>", "form", "</form>", id="form"),
        pytest.param("<form><div>x</form>", "form", "</form>", id="form-closed-with-open-child"),
    ],
)
def test_specially_closed_element_records_its_end_tag(html: str, tag: str, close: str) -> None:
    loc = location(html, tag)
    assert loc.end_tag is not None
    assert html[loc.end_tag.start_offset : loc.end_tag.end_offset] == close


@pytest.mark.parametrize(
    ("html", "tag"),
    [
        # the element the end tag names was never opened in the source, so there is no span to stamp
        pytest.param("<div>x</html>", "html", id="implicit-html"),
        pytest.param("<p>x</body>", "body", id="implicit-body"),
    ],
)
def test_explicit_end_tag_on_a_synthetic_body_or_html_is_ignored(html: str, tag: str) -> None:
    element = parse(html, source_locations=True).find(tag)
    assert element is not None
    assert element.source_location is None


@pytest.mark.parametrize(
    ("html", "tag"),
    [
        pytest.param("<html><body>x", "body", id="body-at-eof"),
        pytest.param("<form><input>", "form", id="form-at-eof"),
    ],
)
def test_specially_closed_element_left_open_has_no_end_tag(html: str, tag: str) -> None:
    assert location(html, tag).end_tag is None


@pytest.mark.parametrize(
    ("html", "node_type"),
    [
        pytest.param("<p>text</p>", Text, id="text-node"),
        pytest.param("<!--c--><p>x</p>", Comment, id="comment-node"),
    ],
)
def test_non_element_nodes_have_no_location(html: str, node_type: type[Node]) -> None:
    node = next(n for n in parse(html, source_locations=True).descendants if isinstance(n, node_type))
    assert node.source_location is None


def test_document_node_has_no_location() -> None:
    assert parse("<p>x</p>", source_locations=True).source_location is None


def test_constructed_element_has_no_location() -> None:
    assert Element("div").source_location is None


def test_fragment_locations_are_relative_to_the_fragment() -> None:
    html = '<div id="a">x</div>'
    root = parse_fragment(html, "body", source_locations=True)
    loc = child(root, "div").source_location
    assert loc is not None
    assert offsets(loc.start_tag) == (0, 12)
    assert html[5:11] == 'id="a"'
    assert offsets(loc.attrs["id"]) == (5, 11)


def test_fragment_off_reports_none() -> None:
    root = parse_fragment("<div>x</div>", "body")
    assert child(root, "div").source_location is None


def test_incremental_parser_tracks_locations_across_feeds() -> None:
    parser = IncrementalParser(source_locations=True)
    parser.feed("<ul>\n  <li>a</li>\n")
    parser.feed('  <li id="x">b</li>\n</ul>')
    items = parser.close().find_all("li")
    second = items[1].source_location
    assert second is not None
    assert second.start_tag.start_line == 3
    assert "id" in second.attrs


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<p id=a class="b">x</p><b>y</b>', id="attributes"),
        pytest.param("<div>\r\n<i>a</i>\r<br></div>", id="carriage-returns"),
        pytest.param("<p>\u00e9\U0001f600<i>z</i></p>", id="astral"),
    ],
)
def test_incremental_parser_locations_match_parse_at_every_split(markup: str) -> None:
    expected = [element.source_location for element in parse(markup, source_locations=True).select("*")]
    for cut in range(len(markup) + 1):
        parser = IncrementalParser(source_locations=True)
        parser.feed(markup[:cut])
        parser.feed(markup[cut:])
        assert [element.source_location for element in parser.close().select("*")] == expected, cut


def test_incremental_parser_off_reports_none() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    assert child(parser.close(), "p").source_location is None


def test_span_is_a_named_tuple() -> None:
    span = location("<div>x</div>", "div").start_tag
    assert isinstance(span, SourceSpan)
    assert span == (1, 0, 0, 1, 5, 5)


def _source_position_child(root: Node, tag: str) -> Element:
    """Find a descendant by tag, narrowing the Element | None result for the checker."""
    found = root.find(tag)
    assert found is not None
    return found


@pytest.mark.parametrize(
    ("html", "tag", "expected"),
    [
        pytest.param("<p>x</p>", "p", (1, 0), id="single-element-at-start"),
        pytest.param("<div><span>x</span></div>", "span", (1, 5), id="column-is-zero-based"),
        pytest.param("<a></a><b></b>", "b", (1, 7), id="second-element-same-line"),
        pytest.param("<html>\n<body>\n  <p>x</p></body></html>", "p", (3, 2), id="indented-on-third-line"),
        pytest.param("<ul>\n  <li>a</li>\n  <li>b</li>\n</ul>", "ul", (1, 0), id="list-container"),
        pytest.param("<p class='lead' id='x'>y</p>", "p", (1, 0), id="position-is-the-open-angle"),
        pytest.param("\n\n\n<h1>t</h1>", "h1", (4, 0), id="leading-blank-lines"),
    ],
)
def test_element_position(html: str, tag: str, expected: tuple[int, int]) -> None:
    element = parse(html).find(tag)
    assert element is not None
    assert element.position == expected
    assert (element.source_line, element.source_col) == expected


def test_nested_elements_each_carry_their_own_position() -> None:
    doc = parse("<section>\n  <article>\n    <p>deep</p>\n  </article>\n</section>")
    assert _source_position_child(doc, "section").position == (1, 0)
    assert _source_position_child(doc, "article").position == (2, 2)
    assert _source_position_child(doc, "p").position == (3, 4)


@pytest.mark.parametrize(
    "newline",
    [
        pytest.param("\n", id="lf"),
        pytest.param("\r\n", id="crlf"),
        pytest.param("\r", id="cr"),
    ],
)
def test_newline_normalization_keeps_line_numbers(newline: str) -> None:
    # CRLF and lone CR collapse to one line break, so the line number is invariant
    html = newline.join(["<p>1</p>", "<p>2</p>", "<p>3</p>"])
    assert [p.source_line for p in parse(html).find_all("p")] == [1, 2, 3]


def test_columns_are_unaffected_by_carriage_returns() -> None:
    # a CR only appears at a line end, so a mid-line column matches the LF form
    assert _source_position_child(parse("a\r\n  <b>x</b>"), "b").source_col == 2


def test_positions_false_drops_every_position() -> None:
    doc = parse("<html><body><p>x</p></body></html>", positions=False)
    for tag in ("html", "body", "p"):
        element = _source_position_child(doc, tag)
        assert element.source_line is None
        assert element.source_col is None
        assert element.position is None


def test_implied_elements_have_no_source() -> None:
    # html/head/body are fabricated for a bare fragment, so they carry no position
    doc = parse("<p>real</p>")
    assert _source_position_child(doc, "html").source_line is None
    assert _source_position_child(doc, "head").position is None
    assert _source_position_child(doc, "body").source_line is None
    assert _source_position_child(doc, "p").source_line == 1


@pytest.mark.parametrize(
    ("html", "node_type"),
    [
        pytest.param("<p>text</p>", Text, id="text-node"),
        pytest.param("<!--c--><p>x</p>", Comment, id="comment-node"),
    ],
)
def test_non_element_nodes_have_no_position(html: str, node_type: type[Node]) -> None:
    node = next(n for n in parse(html).descendants if isinstance(n, node_type))
    assert node.source_line is None
    assert node.source_col is None
    assert node.position is None


def test_constructed_element_has_no_position() -> None:
    element = Element("div")
    assert element.source_line is None
    assert element.source_col is None
    assert element.position is None


def test_document_node_has_no_position() -> None:
    doc = parse("<p>x</p>")
    assert doc.source_line is None
    assert doc.position is None


def test_fragment_positions_are_relative_to_the_fragment() -> None:
    root = parse_fragment("<div>\n<span>x</span></div>", "body")
    assert _source_position_child(root, "div").position == (1, 0)
    assert _source_position_child(root, "span").position == (2, 0)


def test_fragment_positions_false_drops_them() -> None:
    root = parse_fragment("<div>x</div>", "body", positions=False)
    assert _source_position_child(root, "div").source_line is None


def test_adoption_agency_clone_keeps_the_original_line() -> None:
    # </i> closes across the block <p>, so the adoption agency clones <i> into <p>;
    # the clone stands in for the same source tag, so both <i> nodes report line 1
    doc = parse("<i><p>x</i>y</p>")
    italics = doc.find_all("i")
    assert len(italics) == 2
    assert [i.source_line for i in italics] == [1, 1]


def test_adoption_agency_without_positions_is_unaffected() -> None:
    doc = parse("<i><p>x</i>y</p>", positions=False)
    italics = doc.find_all("i")
    assert len(italics) == 2
    assert all(i.source_line is None for i in italics)


def test_incremental_parser_tracks_positions() -> None:
    parser = IncrementalParser()
    parser.feed("<ul>\n  <li>a</li>\n")
    parser.feed("  <li>b</li>\n</ul>")
    doc = parser.close()
    assert [item.source_line for item in doc.find_all("li")] == [2, 3]


def test_incremental_parser_positions_false() -> None:
    parser = IncrementalParser(positions=False)
    parser.feed("<p>x</p>")
    assert _source_position_child(parser.close(), "p").source_line is None


def test_position_survives_a_structural_move() -> None:
    doc = parse("<div>\n  <p>x</p>\n</div>")
    paragraph = _source_position_child(doc, "p")
    assert paragraph.position == (2, 2)
    paragraph.extract()
    _source_position_child(doc, "div").append(paragraph)
    assert paragraph.position == (2, 2)


@pytest.mark.parametrize("library", ["core", "competitors.beautifulsoup4"], ids=["turbohtml", "beautifulsoup"])
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(0, True, id="one"),
        pytest.param(1, True, id="ten"),
        pytest.param(2, True, id="below-index"),
        pytest.param(3, True, id="index"),
        pytest.param(4, True, id="hundred"),
        pytest.param(5, True, id="thousand"),
        pytest.param(6, True, id="reversed-hundred"),
        pytest.param(7, True, id="reversed-thousand"),
        pytest.param(8, False, id="early-value"),
        pytest.param(9, False, id="late-value"),
        pytest.param(10, False, id="missing-name"),
        pytest.param(11, True, id="rotated"),
    ],
)
def test_node_equality_benchmark_result(library: str, case: int, *, expected: bool) -> None:
    module: Final = pytest.importorskip(f"bench.{library}", exc_type=ImportError)
    operation: Final = cast("Callable[[tuple[int, str]], bool]", module.OPERATIONS["node-equals"][0])
    assert operation(cast("tuple[int, str]", INPUTS["node-equals"]()[case][1])) is expected


def test_node_equality_duplicate_benchmark_result() -> None:
    operation: Final = cast("Callable[[tuple[int, str]], bool]", OPERATIONS["node-equals"][0])
    assert operation(cast("tuple[int, str]", INPUTS["node-equals"]()[12][1]))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("node-equals", True, id="aligned"),
        pytest.param("node-equals-reversed", True, id="reversed"),
        pytest.param("node-equals-early-mismatch", False, id="early-mismatch"),
        pytest.param("node-equals-duplicates", True, id="duplicates"),
    ],
)
def test_codspeed_node_equality_result(name: str, *, expected: bool) -> None:
    _, operation, load = next(case for case in benchmarks() if case[0] == name)
    assert cast("Callable[[object], bool]", operation)(load()) is expected


@pytest.mark.oracle
def test_competitor_node_equality_duplicates_unsupported() -> None:
    beautifulsoup: Final = pytest.importorskip("bench.competitors.beautifulsoup4", exc_type=ImportError)
    operation: Final = cast("Callable[[tuple[int, str]], bool]", beautifulsoup.OPERATIONS["node-equals"][0])
    with pytest.raises(ValueError, match="constructor does not normalize case variants"):
        operation(cast("tuple[int, str]", INPUTS["node-equals"]()[12][1]))


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<body><p>a<b>b</b><i>c</i></p>d</body>", "body", "abcd", id="nested-elements"),
        pytest.param("<p>a&amp;b\U0001f600c</p>", "p", "a&b\U0001f600c", id="entities-and-astral-span"),
        pytest.param("<body></body>", "body", "", id="empty"),
    ],
)
def test_text_concatenates_descendant_character_data(
    find: Callable[[str, str], Element], html: str, selector: str, expected: str
) -> None:
    assert find(html, selector).text == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("ascii", id="ascii"),
        pytest.param("café", id="latin1"),
        pytest.param("雪", id="bmp"),
        pytest.param("😀", id="supplementary"),
        pytest.param("\U00100000\U000f0000", id="or-exceeds-unicode-range"),
        pytest.param("\ufeff\ufffe", id="bom-and-noncharacter"),
        pytest.param("\ud800", id="lone-surrogate"),
        pytest.param("\ud800\udc00", id="surrogate-pair"),
        pytest.param("a\x00b", id="embedded-nul"),
    ],
)
@pytest.mark.parametrize("wrapped", [False, True], ids=["text-root", "element-root"])
def test_text_preserves_unicode(text: str, *, wrapped: bool) -> None:
    child: Final = Text(text)
    root: Final = Element("p", children=[child]) if wrapped else child
    assert root.text == text


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(CData("ignored"), "", id="cdata-root"),
        pytest.param(Element("p", children=[CData("ignored"), Text("visible")]), "visible", id="cdata-child"),
    ],
)
def test_text_ignores_cdata(node: Node, expected: str) -> None:
    assert node.text == expected


def test_text_subnode_excludes_siblings() -> None:
    root: Final = Element("main", children=[Text("before"), Element("p", children=[Text("inside")]), Text("after")])
    paragraph: Final = root.find("p")
    assert paragraph is not None
    assert paragraph.text == "inside"


def test_text_ascii_flag_ignores_non_text_data() -> None:
    root: Final = Element("p", children=[Comment("😀"), CData("雪"), Text("ascii")])
    result: Final = root.text
    assert (result, result.isascii()) == ("ascii", True)


@pytest.mark.parametrize("tags", [None, "*", ["*"], {"*": True}], ids=["none", "star", "list", "mapping"])
def test_iter_elements_skips_non_elements(tags: str | list[str] | dict[str, bool] | None) -> None:
    root: Final = Element("main", children=[Text("before"), Element("p"), Text("after")])
    assert [node.tag for node in root.iter_elements(tags)] == ["p"]


def test_iter_elements_filters_eagerly() -> None:
    root: Final = Element("main", children=[Element("p"), Element("li"), Element("p")])
    tags: Final = iter(["p", "li"])
    result: Final = root.iter_elements(tags, include_self=True)
    assert [node.tag for node in result] == ["p", "li", "p"]


def test_iter_elements_matches_known_and_custom_tags() -> None:
    root: Final = Element("main", children=[Element("p"), Element("widget"), Element("li")])
    assert [node.tag for node in root.iter_elements(("p", "widget"))] == ["p", "widget"]


def test_iter_elements_snapshots_tag_list() -> None:
    root: Final = Element("main", children=[Element("p"), Element("li")])
    tags: Final = ["p"]
    result: Final = root.iter_elements(tags)
    tags.append("li")
    assert [node.tag for node in result] == ["p"]


def test_iter_elements_empty_walk() -> None:
    assert list(Element("main").iter_elements()) == []


def test_iter_elements_includes_root() -> None:
    root: Final = Element("main", children=[Element("p")])
    assert [node.tag for node in root.iter_elements("main", include_self=True)] == ["main"]


def test_iter_elements_keeps_prefetched_sibling() -> None:
    first: Final = Element("li")
    second: Final = Element("li")
    root: Final = Element("ul", children=[first, second])
    iterator: Final = root.iter_elements("li")
    assert next(iterator) == first
    holder: Final = Element("section")
    holder.append(second)
    assert next(iterator) == second


def test_iter_elements_keeps_renamed_pending_node() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    iterator: Final = Element("main", children=[first, second]).iter_elements("p")
    assert next(iterator) == first
    second.tag = "aside"
    assert list(iterator) == [second]


def test_iter_elements_skips_insertion_before_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    first.insert_after(Element("p"))
    assert list(iterator) == [second]


def test_iter_elements_stops_at_detached_boundary() -> None:
    child: Final = Element("p")
    first: Final = Element("section", children=[child])
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    Element("holder").append(first)
    assert next(iterator) == child
    assert list(iterator) == []


def test_iter_elements_stops_after_extracted_pending_node() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    second.extract()
    assert list(iterator) == [second]


def test_iter_elements_stops_after_pending_leaves_root() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("section", children=[first, second])
    holder: Final = Element("aside")
    Element("main", children=[root, holder])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    holder.append(second)
    assert list(iterator) == [second]


def test_iter_elements_follows_new_siblings_of_moved_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    tail: Final = Element("p")
    root: Final = Element("section", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    Element("aside", children=[second, tail])
    assert list(iterator) == [second, tail]


def test_iter_elements_follows_detached_current_subtree() -> None:
    first: Final = Element("a", children=[Element("b"), Element("c")])
    root: Final = Element("div", children=[first, Element("d")])
    iterator: Final = root.iter_elements(include_self=True)
    assert next(iterator) == root
    assert next(iterator) == first
    first.extract()
    assert [node.tag for node in iterator] == ["b", "c"]


def test_iter_elements_follows_detached_ancestor_subtree() -> None:
    first: Final = Element("b")
    outer: Final = Element(
        "a", children=[Element("x", children=[first, Element("c")]), Element("y", children=[Element("d")])]
    )
    root: Final = Element("div", children=[outer])
    iterator: Final = root.iter_elements(("b", "c", "d"))
    assert next(iterator) == first
    outer.extract()
    assert [node.tag for node in iterator] == ["c", "d"]


def test_iter_elements_crosses_nested_following_sibling() -> None:
    nested: Final = Element("p")
    following: Final = Element("p")
    root: Final = Element("main", children=[Element("section", children=[nested]), following])
    assert list(root.iter_elements("p")) == [nested, following]


def test_iter_elements_walks_detached_pending_subtree() -> None:
    first: Final = Element("p")
    child: Final = Element("p")
    second: Final = Element("section", children=[child])
    root: Final = Element("main", children=[first, second, Element("p")])
    iterator: Final = root.iter_elements()
    assert next(iterator) == first
    Element("holder").append(second)
    assert list(iterator) == [second, child]


def test_iter_elements_follows_moved_root() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    Element("holder").append(root)
    assert next(iterator) == second


def test_iter_elements_sees_insertions_after_pending() -> None:
    first: Final = Element("p")
    second: Final = Element("p")
    root: Final = Element("main", children=[first, second])
    iterator: Final = root.iter_elements("p")
    assert next(iterator) == first
    third: Final = Element("p")
    second.insert_after(third)
    assert list(iterator) == [second, third]


def test_iter_elements_preserves_xml_case() -> None:
    root: Final = parse_xml("<Root><item/><Item/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("Item")] == ["Item"]


def test_iter_elements_matches_xml_known_tag() -> None:
    root: Final = parse_xml("<Root><p/><P/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("p")] == ["p"]


def test_iter_elements_matches_unicode_custom_tag() -> None:
    root: Final = parse_xml("<Root><é/><e/></Root>").root
    assert root is not None
    assert [node.tag for node in root.iter_elements("é")] == ["é"]


def test_iter_elements_surrogate_tag_has_no_match() -> None:
    assert list(Element("main", children=[Element("p")]).iter_elements("\ud800")) == []


def test_iter_elements_concurrent_next_yields_each_once() -> None:
    nodes: Final = [Element("p") for _ in range(200)]
    iterator: Final = Element("main", children=nodes).iter_elements("p")

    def consume() -> list[Element]:
        return list(iterator)

    with ThreadPoolExecutor(max_workers=4) as executor:
        found: Final = [node for future in (executor.submit(consume) for _ in range(4)) for node in future.result()]
    assert set(found) == set(nodes)
    assert len(found) == len(nodes)


def test_iter_elements_rejects_non_string_tags() -> None:
    root: Final = Element("main")
    with pytest.raises(TypeError, match="only str"):
        root.iter_elements(cast("Iterable[str]", [1]))


def test_iter_elements_rejects_non_iterable_tags() -> None:
    with pytest.raises(TypeError):
        Element("main").iter_elements(cast("Iterable[str]", 1))


def test_iter_elements_rejects_extra_arguments() -> None:
    with pytest.raises(TypeError):
        cast("Callable[..., object]", Element("main").iter_elements)(None, None, None)
