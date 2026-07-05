"""Evaluation of XPath location paths through ``Node.xpath`` / ``Node.xpath_one``.

Covers the structural axes, the name/``*``/``node()``/``text()``/``comment()`` node
tests, attribute access, and the namespace axis.

The second half exercises the precompiled, reusable :class:`turbohtml.XPath` object
(issue #267): ``XPath(expr)`` parses an expression once, and calling it with a context
node plus optional ``$name`` keyword variables evaluates it, returning the same results
as :meth:`turbohtml.Node.xpath`. The compiled program is tree-independent, so one object
runs against many nodes and documents, with ``smart_strings`` and ``extensions`` bound at
construction (mirroring ``lxml.etree.XPath``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import turbohtml
from turbohtml import Document, Element, XPath, XPathString

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import SimpleNamespace

HTML = (
    "<!doctype html><html><head><title>T</title></head><body>"
    '<div id="d1" class="box"><p>a</p><p class="hi">b</p></div>'
    '<div id="d2"><span>s1</span><span>s2</span></div>'
    "<ul><li>1</li><li>2</li></ul>"
    '<a href="/x">L1</a><a href="/y" rel="next">L2</a>'
    "<input disabled>"
    '<my-widget data-k="v">w</my-widget>'
    "<!-- cmt --></body></html>"
)


def tags(result: Iterable[object]) -> list[str]:
    return [node.tag for node in result if isinstance(node, Element)]


def one(node: turbohtml.Node, expr: str) -> Element:
    """The single Element an expression is expected to select."""
    result = node.xpath_one(expr)
    assert isinstance(result, Element)
    return result


@pytest.fixture
def doc() -> turbohtml.Node:
    return turbohtml.parse(HTML)


SVG = "http://www.w3.org/2000/svg"
MATHML = "http://www.w3.org/1998/Math/MathML"

NS_HTML = (
    "<html><body><p id='para'>html</p><svg width='10'><circle r='5'/><rect/></svg><math><mi>x</mi></math></body></html>"
)


@pytest.fixture
def ns_doc() -> turbohtml.Node:
    return turbohtml.parse(NS_HTML)


def test_descendant_name(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//p")) == ["p", "p"]


def test_descendant_wildcard_is_elements_only(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//div/*")) == ["p", "p", "span", "span"]


def test_absolute_path(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("/html/body/div")) == ["div", "div"]


def test_root_node(doc: turbohtml.Node) -> None:
    assert doc.xpath("/") == [doc]


def test_attribute_values(doc: turbohtml.Node) -> None:
    assert doc.xpath("//a/@href") == ["/x", "/y"]
    assert doc.xpath("//a/@rel") == ["next"]
    assert doc.xpath("//p/@class") == ["hi"]


def test_attribute_wildcard(doc: turbohtml.Node) -> None:
    assert one(doc, "//div").xpath("@*") == ["d1", "box"]


def test_attribute_node_test_matches_all(doc: turbohtml.Node) -> None:
    assert one(doc, "//div").xpath("attribute::node()") == ["d1", "box"]
    assert one(doc, "//div").xpath("attribute::text()") == []


def test_valueless_attribute(doc: turbohtml.Node) -> None:
    assert doc.xpath("//input/@disabled") == [""]


def test_unicode_and_overlong_attribute_names_match_nothing(doc: turbohtml.Node) -> None:
    assert doc.xpath("//a/@café") == []
    assert doc.xpath("//a/@" + "z" * 200) == []


def test_step_after_attribute_yields_nothing(doc: turbohtml.Node) -> None:
    assert doc.xpath("//a/@href/x") == []


def test_text_nodes(doc: turbohtml.Node) -> None:
    assert doc.xpath("//title/text()") == ["T"]
    assert one(doc, "//title").xpath("node()") == ["T"]


def test_comment(doc: turbohtml.Node) -> None:
    assert len(doc.xpath("//comment()")) == 1


def test_processing_instruction_absent(doc: turbohtml.Node) -> None:
    assert doc.xpath("//processing-instruction()") == []


def test_unknown_element_matches_by_name(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//my-widget")) == ["my-widget"]
    assert doc.xpath("//my-widget/@data-k") == ["v"]


def test_unknown_element_name_mismatch(doc: turbohtml.Node) -> None:
    assert doc.xpath("//my-gadget") == []


def test_unicode_and_overlong_names_match_nothing(doc: turbohtml.Node) -> None:
    assert doc.xpath("//café") == []
    assert doc.xpath("//" + "a" * 80) == []


def test_self_axis(doc: turbohtml.Node) -> None:
    p = one(doc, "//p")
    assert tags(p.xpath("self::p")) == ["p"]
    assert p.xpath("self::div") == []


def test_parent_axis(doc: turbohtml.Node) -> None:
    assert tags(one(doc, "//p").xpath("..")) == ["div"]
    assert one(doc, "//html").xpath("..") == [doc]
    assert doc.xpath("..") == []  # the document node has no parent
    assert doc.xpath("//p/parent::span") == []  # parent exists but fails the name test


def test_ancestor_axes_are_document_ordered(doc: turbohtml.Node) -> None:
    li = one(doc, "//li")
    assert tags(li.xpath("ancestor::*")) == ["html", "body", "ul"]
    assert tags(li.xpath("ancestor-or-self::*")) == ["html", "body", "ul", "li"]


def test_descendant_axis(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//body/descendant::span")) == ["span", "span"]


def test_following_and_preceding_sibling(doc: turbohtml.Node) -> None:
    assert tags(one(doc, "//div").xpath("following-sibling::div")) == ["div"]
    assert tags(one(doc, "//ul").xpath("preceding-sibling::div")) == ["div", "div"]


def test_following_axis(doc: turbohtml.Node) -> None:
    # everything after the first span's subtree, in document order
    span = one(doc, "//span")
    assert tags(span.xpath("following::a")) == ["a", "a"]
    assert tags(span.xpath("following::*")) == ["span", "ul", "li", "li", "a", "a", "input", "my-widget"]


def test_preceding_axis_excludes_ancestors(doc: turbohtml.Node) -> None:
    # elements before <ul>, minus its ancestors (html/body), in document order
    assert tags(one(doc, "//ul").xpath("preceding::div")) == ["div", "div"]
    assert tags(one(doc, "//ul").xpath("preceding::p")) == ["p", "p"]


def test_preceding_axis_proximity_order_in_predicate(doc: turbohtml.Node) -> None:
    # preceding is a reverse axis: [1] is the nearest preceding element
    assert tags(one(doc, "//ul").xpath("preceding::span[1]")) == ["span"]


def test_results_are_deduplicated(doc: turbohtml.Node) -> None:
    # both spans share the same ancestor div, which must appear once
    assert tags(doc.xpath("//span/ancestor::div")) == ["div"]


def test_context_relative(doc: turbohtml.Node) -> None:
    body = one(doc, "//body")
    assert tags(body.xpath("div")) == ["div", "div"]
    assert tags(body.xpath(".//span")) == ["span", "span"]


def test_xpath_one_returns_first_or_none(doc: turbohtml.Node) -> None:
    assert one(doc, "//a").tag == "a"
    assert doc.xpath_one("//a/@href") == "/x"
    assert doc.xpath_one("//zzz") is None


def test_xpath_iter_yields_results(doc: turbohtml.Node) -> None:
    iterator = doc.xpath_iter("//p")
    assert iter(iterator) is iterator
    assert tags(iterator) == ["p", "p"]


def test_xpath_iter_supports_partial_consumption(doc: turbohtml.Node) -> None:
    iterator = doc.xpath_iter("//*")
    first = next(iterator)
    assert isinstance(first, Element)
    assert first.tag == "html"


def test_xpath_iter_propagates_errors(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'bogus-fn'"):
        doc.xpath_iter("bogus-fn(1)")
    with pytest.raises(TypeError, match="must be a str"):
        doc.xpath_iter(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


def test_namespace_axis(doc: turbohtml.Node) -> None:
    # every element exposes the implicit xml namespace node, named xml
    assert doc.xpath("name(//p/namespace::*)") == "xml"
    assert doc.xpath("//p/namespace::*") == ["http://www.w3.org/XML/1998/namespace"] * 2


def test_xpath_one_unknown_function_raises(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'bogus-fn'"):
        doc.xpath_one("bogus-fn(1)")


def test_invalid_expression_raises_value_error(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="node test"):
        doc.xpath("//")


def test_non_string_argument(doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="must be a str"):
        doc.xpath(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path
    with pytest.raises(TypeError, match="must be a str"):
        doc.xpath_one(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


TABLE_HTML = (
    '<table><tr><td class="num">1</td><td>2</td></tr><tr><td class="num">3</td><td class="num">4</td></tr></table>'
)
LINKS_HTML = "<html><body><a href='/x'>one</a><a href='/y'>two</a></body></html>"


@pytest.fixture
def table_doc() -> turbohtml.Node:
    return turbohtml.parse(TABLE_HTML)


@pytest.fixture
def links_doc() -> turbohtml.Node:
    return turbohtml.parse(LINKS_HTML)


@pytest.fixture
def paragraph_doc() -> turbohtml.Node:
    return turbohtml.parse("<p id='a'>one</p>")


def test_compiled_evaluate_node_set(table_doc: turbohtml.Node) -> None:
    selector = XPath("//td")
    assert [cell.text for cell in selector(table_doc) if isinstance(cell, Element)] == ["1", "2", "3", "4"]


def test_compiled_reuse_across_many_context_nodes(table_doc: turbohtml.Node) -> None:
    selector = XPath(".//td[@class=$cls]")
    rows = [row for row in table_doc.xpath("//tr") if isinstance(row, Element)]
    matched = [[cell.text for cell in selector(row, cls="num") if isinstance(cell, Element)] for row in rows]
    assert matched == [["1"], ["3", "4"]]


def test_compiled_reuse_across_documents() -> None:
    selector = XPath("//p")
    first = turbohtml.parse("<p>a</p><p>b</p>")
    second = turbohtml.parse("<div><p>c</p></div>")
    assert tags(selector(first)) == ["p", "p"]
    assert tags(selector(second)) == ["p"]


def test_compiled_variable_binding_is_per_call(table_doc: turbohtml.Node) -> None:
    selector = XPath("//td[@class=$cls]")
    assert tags(selector(table_doc, cls="num")) == ["td", "td", "td"]
    assert tags(selector(table_doc, cls="missing")) == []


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("count(//td)", 4.0, id="number"),
        pytest.param("string(//td)", "1", id="string"),
        pytest.param("count(//td) > 3", True, id="boolean"),
    ],
)
def test_compiled_scalar_result(table_doc: turbohtml.Node, expr: str, *, expected: float | str | bool) -> None:
    result = XPath(expr)(table_doc)
    assert result == expected
    assert type(result) is type(expected)


def test_compiled_context_node_scopes_relative_path(table_doc: turbohtml.Node) -> None:
    selector = XPath("td")
    second_row = [row for row in table_doc.xpath("//tr") if isinstance(row, Element)][1]
    assert [cell.text for cell in selector(second_row) if isinstance(cell, Element)] == ["3", "4"]


@pytest.mark.parametrize(
    ("introspect", "expected"),
    [
        pytest.param(lambda selector: selector.path, "//td[@class=$cls]", id="path-attribute"),
        pytest.param(repr, "XPath('//td[@class=$cls]')", id="repr"),
    ],
)
def test_compiled_source_introspection(introspect: Callable[[XPath], str], expected: str) -> None:
    assert introspect(XPath("//td[@class=$cls]")) == expected


def test_compiled_variable_named_like_an_option_is_a_variable(table_doc: turbohtml.Node) -> None:
    # smart_strings/extensions are constructor options, never call-time variables,
    # so a $smart_strings variable binds normally rather than being swallowed
    selector = XPath("//td[@class=$smart_strings]")
    assert tags(selector(table_doc, smart_strings="num")) == ["td", "td", "td"]


@pytest.mark.parametrize(
    ("construct", "argument", "exc", "match"),
    [
        pytest.param(XPath, "//[bad", ValueError, "node test", id="syntax-error"),
        pytest.param(XPath, 123, TypeError, "must be str", id="non-str"),
        pytest.param(
            lambda argument: XPath("//a", extensions=argument),
            [1, 2],
            TypeError,
            "extensions",
            id="extensions-not-dict",
        ),
    ],
)
def test_compiled_construction_rejects(
    construct: Callable[[object], object], argument: object, exc: type[Exception], match: str
) -> None:
    with pytest.raises(exc, match=match):
        construct(argument)


@pytest.mark.parametrize(
    ("expr", "invoke", "exc", "match"),
    [
        pytest.param("//p", lambda selector, _doc: selector(), TypeError, "exactly one context node", id="no-context"),
        pytest.param(
            "//p",
            lambda selector, doc: selector(doc, doc),
            TypeError,
            "exactly one context node",
            id="extra-positional",
        ),
        pytest.param(
            "//p", lambda selector, _doc: selector("not a node"), TypeError, "must be a turbohtml Node", id="not-a-node"
        ),
        pytest.param("//p[@id=$want]", lambda selector, doc: selector(doc), ValueError, "xpath", id="unbound-variable"),
        pytest.param(
            "//p[@id=$want]",
            lambda selector, doc: selector(doc, want=[1, 2]),
            TypeError,
            "variable",
            id="unsupported-variable-type",
        ),
    ],
)
def test_compiled_call_site_rejects(
    paragraph_doc: turbohtml.Node,
    expr: str,
    invoke: Callable[[XPath, turbohtml.Node], object],
    exc: type[Exception],
    match: str,
) -> None:
    selector = XPath(expr)
    with pytest.raises(exc, match=match):
        invoke(selector, paragraph_doc)


def test_compiled_smart_strings_off_yields_plain_str(links_doc: turbohtml.Node) -> None:
    result = XPath("//a/@href")(links_doc)
    assert result == ["/x", "/y"]
    assert not any(isinstance(value, XPathString) for value in result)


def test_compiled_smart_strings_on_yields_xpath_string(links_doc: turbohtml.Node) -> None:
    result = XPath("//a/@href", smart_strings=True)(links_doc)
    assert all(isinstance(value, XPathString) for value in result)
    first = result[0]
    assert isinstance(first, XPathString)
    assert first.getparent().tag == "a"
    assert first.is_attribute is True
    assert first.attrname == "href"


def test_compiled_extensions_bound_at_construction(links_doc: turbohtml.Node) -> None:
    def count_nodes(_context: SimpleNamespace, nodes: list[object]) -> float:
        return float(len(nodes))

    extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] = {(None, "count_nodes"): count_nodes}
    selector = XPath("count_nodes(//a)", extensions=extensions)
    assert selector(links_doc) == pytest.approx(2.0)


def test_compiled_extension_receives_context_node(links_doc: turbohtml.Node) -> None:
    def context_tag(context: SimpleNamespace) -> str:
        return context.context_node.tag

    extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] = {(None, "tag"): context_tag}
    matched = XPath("//a[tag()='a']", extensions=extensions)(links_doc)
    assert all(isinstance(node, Element) and node.tag == "a" for node in matched)
    assert len(matched) == 2


@pytest.mark.parametrize(
    "extensions",
    [pytest.param({}, id="empty-dict"), pytest.param(None, id="none")],
)
def test_compiled_falsy_extensions_bind_nothing(
    links_doc: turbohtml.Node,
    extensions: dict[tuple[str | None, str], Callable[..., str | float | bool]] | None,
) -> None:
    assert XPath("//a/@href", extensions=extensions)(links_doc) == ["/x", "/y"]


# A union of two predicated paths, parsed across an arena growth, must keep both
# predicates. The parser stored a step's predicate list with
# ``nodes[step].first = parse_predicates(ps)``; ``parse_predicates`` calls ``xn_new``,
# which can reallocate the arena, so the left-hand address taken from the pre-call
# ``nodes`` pointer could dangle and the store be lost -- silently dropping the
# predicate of a path parsed across the growth. Whether the growth landed on a given
# predicate depended on the surrounding node count, so wrapping a union in a function
# call (``count(//a[@x] | //b[@y])``) could drop the second path's predicate.
@pytest.fixture
def union_doc() -> turbohtml.Node:
    return turbohtml.parse("<r><a x='1'>A</a><b y='2'>B</b><b>B2</b></r>")


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("count(//a[@x='1'] | //b[@y='2'])", 2.0, id="union-in-count"),
        pytest.param("count(//b[@y='2'] | //a[@x='1'])", 2.0, id="union-in-count-swapped"),
        pytest.param("//a[@x='1'] | //b[@y='2']", None, id="union-root"),
    ],
)
def test_predicate_survives_in_compound_expression(
    union_doc: turbohtml.Node, expr: str, expected: float | None
) -> None:
    result = union_doc.xpath(expr)
    if expected is None:
        assert len(result) == 2
    else:
        assert result == pytest.approx(expected)


# Namespace-prefixed name tests bound through the ``namespaces=`` keyword (issue #263).
# A prefixed test like ``//svg:rect`` resolves its prefix against the supplied mapping, then
# matches an element whose foreign-content namespace equals the bound URI and whose local name
# equals the suffix. HTML elements stay in the null namespace, so unprefixed tests are unaffected.
# The binding happens at evaluation time, so the cached compiled program runs under any mapping.


@pytest.mark.parametrize(
    ("expr", "mapping", "expected"),
    [
        pytest.param("//svg:circle", {"svg": SVG}, ["circle"], id="svg-text-matched-local"),
        pytest.param("//svg:rect", {"svg": SVG}, ["rect"], id="svg-second-child"),
        pytest.param("//s:circle", {"s": SVG}, ["circle"], id="prefix-name-is-arbitrary"),
        pytest.param("//m:math", {"m": MATHML}, ["math"], id="mathml-atom-matched-local"),
        pytest.param("//m:mi", {"m": MATHML}, ["mi"], id="mathml-leaf"),
        pytest.param("//svg:circle | //m:mi", {"svg": SVG, "m": MATHML}, ["circle", "mi"], id="two-prefixes"),
        pytest.param("/html/body/svg:svg", {"svg": SVG}, ["svg"], id="prefixed-step-in-path"),
        # a prefix bound to the empty URI selects the null (HTML) namespace
        pytest.param("//h:p", {"h": ""}, ["p"], id="null-namespace-prefix-matches-html"),
        # a same-length-but-different bound prefix is skipped; a later exact entry still resolves
        pytest.param("//svg:circle", {"abc": MATHML, "svg": SVG}, ["circle"], id="same-length-prefix-distinguished"),
    ],
)
def test_prefixed_match(ns_doc: turbohtml.Node, expr: str, mapping: dict[str, str], expected: list[str]) -> None:
    assert tags(ns_doc.xpath(expr, namespaces=mapping)) == expected


@pytest.mark.parametrize(
    ("expr", "mapping"),
    [
        pytest.param("//svg:circle", {"svg": MATHML}, id="wrong-uri-for-prefix"),
        pytest.param("//svg:p", {"svg": SVG}, id="html-local-name-not-in-svg-ns"),
        pytest.param("//html:circle", {"html": ""}, id="svg-element-not-in-null-ns"),
        pytest.param("//svg:nope", {"svg": SVG}, id="no-such-local-name"),
        pytest.param("//c:circle", {"c": "urn:custom"}, id="custom-uri-matches-no-element"),
        # a URI the same length as the SVG one but differing in content is not the SVG namespace
        pytest.param("//c:circle", {"c": "http://www.w3.org/2000/SVG"}, id="same-length-near-miss-uri"),
        pytest.param("//xml:circle", {}, id="implicit-xml-prefix-matches-no-element"),
    ],
)
def test_prefixed_no_match(ns_doc: turbohtml.Node, expr: str, mapping: dict[str, str]) -> None:
    assert ns_doc.xpath(expr, namespaces=mapping) == []


@pytest.mark.parametrize(
    "mapping",
    [
        pytest.param({}, id="empty-mapping"),
        pytest.param({"other": SVG}, id="mapping-lacks-prefix"),
        pytest.param({"abc": SVG}, id="same-length-prefix-only"),
    ],
)
def test_undefined_prefix_raises(ns_doc: turbohtml.Node, mapping: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="undefined namespace prefix"):
        ns_doc.xpath("//svg:circle", namespaces=mapping)


def test_undefined_prefix_raises_without_mapping(ns_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="undefined namespace prefix"):
        ns_doc.xpath("//svg:circle")


@pytest.mark.parametrize(
    ("namespaces", "message"),
    [
        pytest.param([("svg", SVG)], "namespaces must be a dict", id="not-a-dict"),
        pytest.param({"svg": 1}, "map str prefixes to str URIs", id="non-str-value"),
        pytest.param({1: SVG}, "map str prefixes to str URIs", id="non-str-key"),
    ],
)
def test_namespaces_type_errors(ns_doc: turbohtml.Node, namespaces: object, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        # a wrong-typed mapping exercises the TypeError path the C binding guards
        ns_doc.xpath("//svg:circle", namespaces=namespaces)  # ty: ignore[invalid-argument-type]


def test_unprefixed_name_test_is_namespace_agnostic(ns_doc: turbohtml.Node) -> None:
    # Without a prefix the test matches by local name in any namespace, as before.
    assert tags(ns_doc.xpath("//circle")) == ["circle"]
    assert tags(ns_doc.xpath("//circle", namespaces={"svg": SVG})) == ["circle"]


def test_prefixed_attribute_never_matches(ns_doc: turbohtml.Node) -> None:
    # HTML attributes carry no namespace, so a prefixed attribute test selects nothing,
    # while the unprefixed name still reads the attribute value.
    assert ns_doc.xpath("//svg:svg/@svg:width", namespaces={"svg": SVG}) == []
    assert ns_doc.xpath("//svg:svg/@width", namespaces={"svg": SVG}) == ["10"]


def test_xpath_one_accepts_namespaces(ns_doc: turbohtml.Node) -> None:
    node = ns_doc.xpath_one("//svg:circle", namespaces={"svg": SVG})
    assert isinstance(node, Element)
    assert node.tag == "circle"


def test_xpath_iter_accepts_namespaces(ns_doc: turbohtml.Node) -> None:
    assert tags(list(ns_doc.xpath_iter("//m:mi", namespaces={"m": MATHML}))) == ["mi"]


def test_namespaces_none_is_no_mapping(ns_doc: turbohtml.Node) -> None:
    assert tags(ns_doc.xpath("//p", namespaces=None)) == ["p"]


def test_namespaces_combines_with_variables(ns_doc: turbohtml.Node) -> None:
    result = ns_doc.xpath("//svg:circle[@r=$radius]", namespaces={"svg": SVG}, radius="5")
    assert tags(result) == ["circle"]


def test_same_expression_rebinds_per_call(ns_doc: turbohtml.Node) -> None:
    # The compiled program is cached by string; the prefix resolves per call.
    assert tags(ns_doc.xpath("//p:circle", namespaces={"p": SVG})) == ["circle"]
    assert ns_doc.xpath("//p:circle", namespaces={"p": MATHML}) == []


# Element.xpath_path(): the positional XPath locating a node from the root. Unlike
# css_path() it never anchors on an id, indexing only among same-name siblings.
def _xpath_path(html: str, selector: str) -> str:
    element = turbohtml.parse(html).select_one(selector)
    assert isinstance(element, Element)
    return element.xpath_path()


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<html><body><p>x</p></body></html>", "html", "/html", id="root-element"),
        pytest.param("<html><body><p>x</p></body></html>", "p", "/html/body/p", id="descends-from-root"),
        pytest.param(
            "<body><div>a</div><div>b</div><div>c</div></body>",
            "div:nth-of-type(2)",
            "/html/body/div[2]",
            id="index-among-same-name-siblings",
        ),
        pytest.param("<body><h1>t</h1><p>x</p></body>", "p", "/html/body/p", id="no-index-for-distinct-names"),
        pytest.param('<body><div id="main"><p>x</p></div></body>', "#main", "/html/body/div", id="ids-do-not-anchor"),
        pytest.param(
            "<body><div><p>a</p></div><div><p>b</p><p>c</p></div></body>",
            "div:nth-of-type(2) p:nth-of-type(2)",
            "/html/body/div[2]/p[2]",
            id="mixed-indices",
        ),
        pytest.param(
            "<ul>" + "".join(f"<li>{number}</li>" for number in range(15)) + "</ul>",
            "li:nth-of-type(15)",
            "/html/body/ul/li[15]",
            id="multi-digit-index",
        ),
        pytest.param(
            "<body><my-widget>a</my-widget><my-widget>b</my-widget></body>",
            "my-widget:nth-of-type(2)",
            "/html/body/my-widget[2]",
            id="unknown-tag-uses-its-name",
        ),
    ],
)
def test_xpath_path(html: str, selector: str, expected: str) -> None:
    assert _xpath_path(html, selector) == expected


def test_xpath_path_of_detached_element() -> None:
    assert Element("section").xpath_path() == "/section"


# xpath_path() round-trips: re-evaluating the path returns exactly the node it came from.
_PATH_DOC = (
    "<!doctype html><html><head><title>t</title></head><body>"
    "<header><h1>Title</h1></header>"
    '<main id="content">'
    "<article><p>one</p><p>two</p><p>three</p></article>"
    '<article class="aside"><p>alpha</p><ul><li>a</li><li>b</li><li>c</li></ul></article>'
    "</main>"
    '<footer><a href="/x">x</a><a href="/y">y</a></footer>'
    "</body></html>"
)

_PATH_DOCUMENT = turbohtml.parse(_PATH_DOC)


def _every_element(document: Document) -> list[Element]:
    root = document.root
    assert root is not None
    return [root, *(node for node in root.descendants if isinstance(node, Element))]


@pytest.mark.parametrize("element", _every_element(_PATH_DOCUMENT), ids=lambda element: element.xpath_path())
def test_xpath_path_reselects_only_this_element(element: Element) -> None:
    assert _PATH_DOCUMENT.xpath(element.xpath_path()) == [element]


# //@id expands to descendant-or-self::node()/@id, so the attribute axis visits the text
# nodes in the tree. A text-node span reuses attr_count as a source offset with
# attrs == NULL, so an axis that read attribute storage without an element-type gate
# dereferenced a null pointer (issue #401).
@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//@id", ["x"], id="named"),
        pytest.param("//@*", ["x", "en"], id="wildcard"),
        pytest.param("//node()/@id", ["x"], id="via-node-test"),
        pytest.param("string(//@id)", "x", id="string-of-attribute"),
    ],
)
def test_attribute_axis_survives_text_nodes(expr: str, expected: object) -> None:
    doc = turbohtml.parse("<a id=x lang=en>hi<b>x</b></a>")
    assert doc.xpath(expr) == expected


def test_attribute_axis_of_a_text_node_is_empty() -> None:
    assert turbohtml.parse("<a>t</a>").xpath("//text()/@id") == []


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param(" or ".join(["1=1"] * 5000), id="or-spine"),
        pytest.param("+".join(["1"] * 5000), id="additive-spine"),
    ],
)
def test_deep_operator_spine_raises_instead_of_overflowing(doc: turbohtml.Node, expr: str) -> None:
    # a long left-associative chain parses iteratively but the evaluator descends its
    # spine; the depth cap raises before the C stack overflows (issue #421)
    with pytest.raises(ValueError, match="nested too deeply"):
        doc.xpath(expr)


def test_moderately_nested_expression_still_evaluates(doc: turbohtml.Node) -> None:
    assert doc.xpath("(" * 100 + "1 + 1" + ")" * 100) == pytest.approx(2.0)
