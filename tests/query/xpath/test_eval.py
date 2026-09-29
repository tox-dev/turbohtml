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

import gc
import sys
from functools import partial
from operator import ge, gt, le, lt
from typing import TYPE_CHECKING, Final, cast
from weakref import finalize, ref

import pytest

import turbohtml
from turbohtml import Document, Element, Text, XPath, XPathString, parse, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
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


def node_list(result: object) -> list[turbohtml.Node | str]:
    """The node-set list an expression is expected to return."""
    assert isinstance(result, list)
    return result


def tags(result: object) -> list[str]:
    return [node.tag for node in node_list(result) if isinstance(node, Element)]


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


def _detached_tree(holder: str) -> tuple[turbohtml.Node, Element]:
    top: turbohtml.Node = turbohtml.DocumentFragment() if holder == "fragment" else Element("div")
    top.append(Element("div") if holder == "fragment" else Element("span"))
    parent = top.children[0]
    assert isinstance(parent, Element)
    parent.append(Element("p", {"id": "x"}))
    paragraph = parent.children[0]
    assert isinstance(paragraph, Element)
    return top, paragraph


@pytest.mark.parametrize("holder", [pytest.param("fragment", id="fragment"), pytest.param("element", id="element")])
@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("//p", id="descendants"),
        pytest.param("id('x')", id="id"),
    ],
)
def test_absolute_path_outside_a_document_starts_at_its_root(holder: str, expression: str) -> None:
    _, paragraph = _detached_tree(holder)
    assert paragraph.xpath(expression) == [paragraph]


@pytest.mark.parametrize("holder", [pytest.param("fragment", id="fragment"), pytest.param("element", id="element")])
def test_root_outside_a_document_is_the_top_of_the_tree(holder: str) -> None:
    top, paragraph = _detached_tree(holder)
    assert paragraph.xpath("/") == [top]


def test_absolute_path_in_a_parsed_fragment_starts_at_the_fragment() -> None:
    bold = turbohtml.parse_fragment("<p><b>x</b></p>").select_one("b")
    assert bold is not None
    assert tags(bold.xpath("/p")) == ["p"]


_NESTED: Final = (
    '<div id="o"><div id="i"><a id="1"></a><p id="p"></p></div><a id="2"></a></div><a id="3"></a>'
    '<div id="s"><a id="4"></a></div>'
)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("/descendant::a", ["1", "2", "3", "4"], id="single-root-descendants"),
        pytest.param("//div[@id='o']/descendant::a", ["1", "2"], id="single-element-descendants"),
        pytest.param("//div//a", ["1", "2", "4"], id="descendant-from-nested-contexts"),
        pytest.param(
            "//div/descendant-or-self::*", ["o", "i", "1", "p", "2", "s", "4"], id="descendant-or-self-nested"
        ),
        pytest.param("//div/descendant::a[1]", ["1", "4"], id="predicate-per-nested-context"),
        pytest.param("//div/descendant::a[last()]", ["1", "2", "4"], id="predicate-last-per-context"),
        pytest.param("//div/@id/descendant-or-self::node()", ["o", "i", "s"], id="attribute-contexts"),
        pytest.param(
            "(//div | //div/@id)/descendant-or-self::node()",
            ["o", "o", "i", "i", "1", "p", "2", "s", "s", "4"],
            id="mixed-node-and-attribute-contexts",
        ),
        pytest.param("(//div | //div/@id)/descendant::a", ["1", "2", "4"], id="attributes-between-contexts"),
        pytest.param("//a/descendant::*", [], id="leaf-contexts"),
    ],
)
def test_descendant_step_from_nested_contexts(expression: str, expected: list[str]) -> None:
    found = turbohtml.parse(_NESTED).xpath(expression)
    assert isinstance(found, list)
    assert [node if isinstance(node, str) else _id_of(node) for node in found] == expected


def _id_of(node: turbohtml.Node) -> str | None:
    assert isinstance(node, Element)
    return node.attr("id")


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


ATTR_CONTEXT_XML: Final[str] = '<div><p n="10">x<b>i</b></p><p n="3">y</p></div>'


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//@n[. > 5]", ["10"], id="dot-is-the-attribute-value"),
        pytest.param("//@n[.='x']", [], id="dot-is-not-the-owner-text"),
        pytest.param("//@n[string-length()=1]", ["3"], id="string-length-of-the-attribute"),
        pytest.param("//@n[number()=3]", ["3"], id="number-of-the-attribute"),
        pytest.param("//@n[name()='n']", ["10", "3"], id="name-of-the-attribute"),
        pytest.param("//@n[name()='p']", [], id="name-is-not-the-owner"),
        pytest.param("//@n[namespace-uri()='']", ["10", "3"], id="namespace-uri-of-the-attribute"),
        pytest.param("//@n/..", ["p", "p"], id="parent-abbreviation"),
        pytest.param("//@n/parent::b", [], id="parent-name-test"),
        pytest.param("//@n/ancestor::*", ["div", "p", "p"], id="ancestor"),
        pytest.param("//@n/ancestor-or-self::*", ["div", "p", "p"], id="ancestor-or-self-element"),
        pytest.param("//@n/ancestor-or-self::node()[1]", ["10", "3"], id="ancestor-or-self-starts-at-self"),
        pytest.param("//@n/self::node()", ["10", "3"], id="self-node"),
        pytest.param("//@n/self::n", [], id="self-name-test-wants-an-element"),
        pytest.param("//@n/descendant-or-self::node()", ["10", "3"], id="descendant-or-self-node"),
        pytest.param("//@n/descendant-or-self::*", [], id="descendant-or-self-element"),
        pytest.param("//@n[.=10]/following::*", ["b", "p"], id="following-includes-owner-descendants"),
        pytest.param("//@n[.=3]/preceding::*", ["p", "b"], id="preceding-skips-the-owner"),
        pytest.param("//@n/child::node()", [], id="child-is-empty"),
        pytest.param("//@n/following-sibling::node()", [], id="following-sibling-is-empty"),
        pytest.param("//@n/@*", [], id="attribute-of-attribute-is-empty"),
        pytest.param("//p/namespace::*/..", ["p", "p"], id="namespace-node-parent"),
    ],
)
def test_attribute_node_as_context(expr: str, expected: list[str]) -> None:
    result = parse_xml(ATTR_CONTEXT_XML).xpath(expr)
    assert isinstance(result, list)
    assert [node.tag if isinstance(node, Element) else node for node in result] == expected


def test_text_nodes(doc: turbohtml.Node) -> None:
    assert doc.xpath("//title/text()") == ["T"]
    assert one(doc, "//title").xpath("node()") == ["T"]


def test_comment(doc: turbohtml.Node) -> None:
    assert len(node_list(doc.xpath("//comment()"))) == 1


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
    assert tags(list(iterator)) == ["p", "p"]


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
    assert [cell.text for cell in node_list(selector(table_doc)) if isinstance(cell, Element)] == ["1", "2", "3", "4"]


def test_compiled_reuse_across_many_context_nodes(table_doc: turbohtml.Node) -> None:
    selector = XPath(".//td[@class=$cls]")
    rows = [row for row in node_list(table_doc.xpath("//tr")) if isinstance(row, Element)]
    matched = [[cell.text for cell in node_list(selector(row, cls="num")) if isinstance(cell, Element)] for row in rows]
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
    second_row = [row for row in node_list(table_doc.xpath("//tr")) if isinstance(row, Element)][1]
    assert [cell.text for cell in node_list(selector(second_row)) if isinstance(cell, Element)] == ["3", "4"]


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
    result = node_list(XPath("//a/@href")(links_doc))
    assert result == ["/x", "/y"]
    assert not any(isinstance(value, XPathString) for value in result)


def test_compiled_smart_strings_on_yields_xpath_string(links_doc: turbohtml.Node) -> None:
    result = node_list(XPath("//a/@href", smart_strings=True)(links_doc))
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
    matched = node_list(XPath("//a[tag()='a']", extensions=extensions)(links_doc))
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
        assert len(node_list(result)) == 2
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


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//foreignObject", ["foreignObject"], id="svg-builtin-atom-mixed-case"),
        pytest.param("//foreignobject", [], id="svg-builtin-atom-lowercase"),
        pytest.param("//linearGradient", ["linearGradient"], id="svg-unknown-atom-mixed-case"),
        pytest.param("//lineargradient", [], id="svg-unknown-atom-lowercase"),
        pytest.param("//svg", ["svg"], id="svg-builtin-atom-lowercase-spelling"),
        pytest.param("//SVG", [], id="svg-uppercase"),
        pytest.param("//foreignObject/p", ["p"], id="html-element-below-foreign-object"),
    ],
)
def test_foreign_element_name_test_is_case_sensitive(expr: str, expected: list[str]) -> None:
    document = parse("<svg><foreignObject><p>x</p></foreignObject><linearGradient/></svg>")
    assert tags(document.xpath(expr)) == expected


def test_prefixed_attribute_never_matches(ns_doc: turbohtml.Node) -> None:
    # HTML attributes carry no namespace, so a prefixed attribute test selects nothing,
    # while the unprefixed name still reads the attribute value.
    assert ns_doc.xpath("//svg:svg/@svg:width", namespaces={"svg": SVG}) == []
    assert ns_doc.xpath("//svg:svg/@width", namespaces={"svg": SVG}) == ["10"]


XML_NS: Final[str] = "http://www.w3.org/XML/1998/namespace"


@pytest.mark.parametrize(
    ("markup", "expr", "namespaces", "expected"),
    [
        pytest.param("<r><d xml:lang='en'/></r>", "//@xml:lang", None, ["en"], id="xml-tree-implicit-prefix"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@x:lang", {"x": XML_NS}, ["en"], id="xml-tree-rebound-prefix"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@xml:space", None, [], id="xml-tree-other-local-name"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@xml:" + "z" * 130, None, [], id="xml-tree-overlong-local-name"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@x:lang", {"x": "urn:x"}, [], id="xml-tree-other-namespace"),
    ],
)
def test_xml_namespace_attribute_test_on_xml_tree(
    markup: str, expr: str, namespaces: dict[str, str] | None, expected: list[str]
) -> None:
    assert parse_xml(markup).xpath(expr, namespaces=namespaces) == expected


def test_xml_namespace_attribute_test_on_html_tree_matches_foreign_elements_only() -> None:
    # the HTML parser puts xml:lang in the XML namespace only on a foreign element
    document = parse('<p xml:lang="de">x</p><svg xml:lang="it"></svg>')
    assert document.xpath("//@xml:lang") == ["it"]


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


@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param(("2",) * 32, ("1",) * 32, id="descending"),
        pytest.param(("1",) * 32, ("2",) * 32, id="ascending"),
        pytest.param(("2",) * 32, ("2",) * 32, id="equal"),
        pytest.param(("2",) * 31 + ("0",), ("1",) * 32, id="late-left-minimum"),
        pytest.param(("1",) * 31 + ("3",), ("2",) * 32, id="late-left-maximum"),
        pytest.param(("2",) * 32, ("1",) * 31 + ("3",), id="late-right-maximum"),
        pytest.param(("1",) * 32, ("2",) * 31 + ("0",), id="late-right-minimum"),
        pytest.param(("NaN",) * 32, ("1",) * 32, id="left-nan"),
        pytest.param(("1",) * 32, ("NaN",) * 32, id="right-nan"),
        pytest.param(("NaN", "1") * 16, ("NaN", "2") * 16, id="nan-and-numbers"),
        pytest.param(("1", "NaN") * 16, ("2", "NaN") * 16, id="numbers-and-nan"),
        pytest.param(("-0",) * 16, ("0",) * 16, id="signed-zero"),
        pytest.param(("2",) * 15, ("1",) * 16, id="small-left"),
        pytest.param(("2",) * 16, ("1",) * 15, id="small-right"),
    ],
)
@pytest.mark.parametrize(
    ("operation", "compare"),
    [
        pytest.param("<", lt, id="lt"),
        pytest.param("<=", le, id="le"),
        pytest.param(">", gt, id="gt"),
        pytest.param(">=", ge, id="ge"),
    ],
)
def test_numeric_node_set_comparison(
    left: tuple[str, ...], right: tuple[str, ...], operation: str, compare: Callable[[float, float], bool]
) -> None:
    document: Final = parse_xml(
        "<root>"
        + "".join(f"<left>{value}</left>" for value in left)
        + "".join(f"<right>{value}</right>" for value in right)
        + "</root>"
    )
    assert document.xpath(f"//left {operation} //right") is any(
        compare(float(first), float(second)) for first in left for second in right
    )


@pytest.mark.parametrize("size", [pytest.param(size, id=str(size)) for size in (0, 1, 15, 16, 17, 32, 64)])
@pytest.mark.parametrize(
    "operation", [pytest.param("intersection", id="intersection"), pytest.param("difference", id="difference")]
)
def test_set_filter_preserves_nodes(size: int, operation: str) -> None:
    document: Final = parse_xml(
        "<root>" + "".join(f'<item id="{index}">same</item>' for index in range(size)) + "</root>"
    )
    assert document.xpath(f"set:{operation}(//item, //item[position() mod 2 = 0])/@id") == [
        str(index) for index in range(size) if (index % 2 == 1) == (operation == "intersection")
    ]


@pytest.mark.parametrize(
    "operation", [pytest.param("intersection", id="intersection"), pytest.param("difference", id="difference")]
)
def test_set_filter_distinguishes_attributes(operation: str) -> None:
    document: Final = parse_xml("<root>" + '<item a="same" b="same"/>' * 64 + "</root>")
    assert document.xpath(f"set:{operation}(//item/@*, //item/@a)") == ["same"] * 64


@pytest.mark.parametrize("size", [pytest.param(size, id=str(size)) for size in (0, 1, 15, 16, 17, 64)])
@pytest.mark.parametrize(
    ("right", "overlap"),
    [pytest.param("//right/item", False, id="disjoint"), pytest.param("//item", True, id="first-overlap")],
)
def test_sets_share_a_node(size: int, right: str, *, overlap: bool) -> None:
    document: Final = parse_xml(f"<root><left>{'<item/>' * size}</left><right>{'<item/>' * size}</right></root>")
    assert document.xpath(f"set:has-same-node(//left/item, {right})") is (overlap and size > 0)


def test_sets_share_a_later_node() -> None:
    document: Final = parse_xml("<root>" + "<item/>" * 64 + "</root>")
    assert document.xpath("set:has-same-node(//item, //item[position() > 32])") is True


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("unordered(//li) | //ul", id="left-extension"),
        pytest.param("//ul | unordered(//li)", id="right-extension"),
        pytest.param("unordered(//li) | unordered(//li)", id="both-extensions"),
    ],
)
def test_union_orders_extension_results(expression: str) -> None:
    document: Final[Document] = parse("<ul>" + "".join(f"<li>{index}</li>" for index in range(100)) + "</ul>")
    assert document.xpath(expression, extensions={(None, "unordered"): _unordered}) == document.select(
        "ul, li" if "//ul" in expression else "li"
    )


def _unordered(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [*reversed(nodes), nodes[0]]


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        pytest.param((), (), (False, False), id="empty"),
        pytest.param(("a",) * 16, (), (False, False), id="empty-right"),
        pytest.param((), ("a",) * 16, (False, False), id="empty-left"),
        pytest.param(("a",) * 15, ("b",) * 16, (False, True), id="small-left"),
        pytest.param(("a",) * 16, ("b",) * 15, (False, True), id="small-right"),
        pytest.param(("a",) * 16, ("b",) * 16, (False, True), id="disjoint"),
        pytest.param(("a",) * 32, ("a",) * 32, (True, False), id="equal"),
        pytest.param(("a",) * 31 + ("b",), ("c",) * 31 + ("b",), (True, True), id="late-match"),
        pytest.param(("a",) * 31 + ("b",), ("a",) * 32, (True, True), id="unequal-left"),
        pytest.param(("a",) * 32, ("a",) * 31 + ("b",), (True, True), id="unequal-right"),
        pytest.param(("",) * 16, ("",) * 16, (True, False), id="empty-strings"),
        pytest.param(("é",) * 16, ("e\u0301",) * 16, (False, True), id="no-normalization"),
        pytest.param(("雪𐀀",) * 16, ("雪𐀀",) * 16, (True, False), id="wide-unicode"),
    ],
)
@pytest.mark.parametrize(
    ("operation", "index"), [pytest.param("=", 0, id="equal"), pytest.param("!=", 1, id="unequal")]
)
def test_node_set_value_comparison(
    left: tuple[str, ...], right: tuple[str, ...], expected: tuple[bool, bool], operation: str, index: int
) -> None:
    document: Final = parse_xml(
        "<root>"
        + "".join(f"<left>{value}</left>" for value in left)
        + "".join(f"<right>{value}</right>" for value in right)
        + "</root>"
    )
    assert document.xpath(f"//left {operation} //right") is expected[index]


def test_comparison_uses_attribute_values() -> None:
    document: Final = parse_xml("<root>" + '<item left="same" right="same"/>' * 32 + "</root>")
    assert document.xpath("//item/@left = //item/@right") is True


def test_comparison_uses_descendant_text() -> None:
    document: Final = parse_xml("<root>" + "<left>a<b>b</b>c</left><right>abc</right>" * 32 + "</root>")
    assert document.xpath("//left = //right") is True


@pytest.mark.parametrize(
    "selection", [pytest.param("//item", id="elements"), pytest.param("//item/@value", id="attributes")]
)
def test_comparison_same_nodes(selection: str) -> None:
    document: Final = parse_xml("<root>" + '<item value="same">same</item>' * 32 + "</root>")
    assert document.xpath(f"{selection} = {selection}") is True


VARIABLE_HTML = (
    "<html><body>"
    "<table><tr id='r1'><td>a</td><td>b</td></tr><tr id='r2'><td>c</td></tr></table>"
    "<p id='a'>one</p><p id='b'>two</p><p id='c'>three</p>"
    "</body></html>"
)


def variable_tags(result: object) -> list[str]:
    return [node.tag if isinstance(node, Element) else str(node) for node in node_list(result)]


def ids(result: object) -> list[str | None]:
    return [node.attr("id") for node in node_list(result) if isinstance(node, Element)]


@pytest.fixture
def variable_doc() -> turbohtml.Node:
    return turbohtml.parse(VARIABLE_HTML)


@pytest.mark.parametrize(
    ("expr", "kwargs", "expected"),
    [
        pytest.param("//p[@id=$want]", {"want": "b"}, ["p"], id="string-in-predicate"),
        pytest.param("//p[position()=$n]", {"n": 2}, ["p"], id="int-in-predicate"),
        pytest.param("//p[$keep]", {"keep": True}, ["p", "p", "p"], id="bool-true"),
        pytest.param("//p[$keep]", {"keep": False}, [], id="bool-false"),
        pytest.param("//p[@id=$a or @id=$b]", {"a": "a", "b": "c"}, ["p", "p"], id="two-variables"),
    ],
)
def test_variable_node_set(
    variable_doc: turbohtml.Node, expr: str, kwargs: dict[str, str | int | float | bool], expected: list[str]
) -> None:
    assert variable_tags(variable_doc.xpath(expr, **kwargs)) == expected  # ty: ignore[invalid-argument-type]  # variables unpacked as a dict


@pytest.mark.parametrize(
    ("expr", "kwargs", "expected"),
    [
        pytest.param("$s", {"s": "hi"}, "hi", id="string-value"),
        pytest.param("$s", {"s": ""}, "", id="empty-string-value"),
        pytest.param("$n", {"n": 7}, 7.0, id="int-value"),
        pytest.param("$n", {"n": 2.5}, 2.5, id="float-value"),
        pytest.param("$n", {"n": -1}, -1.0, id="minus-one-is-a-value-not-an-error"),
        pytest.param("$b", {"b": True}, True, id="bool-value"),
        pytest.param("$a + $b", {"a": 2, "b": 3}, 5.0, id="arithmetic"),
        pytest.param("count(//p) = $n", {"n": 3}, True, id="compared-to-count"),
    ],
)
def test_variable_scalar(
    variable_doc: turbohtml.Node, expr: str, kwargs: dict[str, str | int | float | bool], expected: object
) -> None:
    assert variable_doc.xpath(expr, **kwargs) == expected  # ty: ignore[invalid-argument-type]  # variables unpacked as a dict


def test_unbound_variable_without_any_binding(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unbound variable"):
        variable_doc.xpath("$missing")


def test_unbound_variable_with_other_bindings_present_length_differs(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unbound variable"):
        variable_doc.xpath("$missing", other="x")


def test_unbound_variable_same_length_different_name(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unbound variable"):
        variable_doc.xpath("$abc", xyz="v")


def test_unsupported_variable_type(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="an iterable of elements"):
        variable_doc.xpath("//p[@id=$x]", x=[1, 2])  # ty: ignore[invalid-argument-type]  # ints are not elements


def test_unsupported_type_after_a_valid_binding_frees_the_partial(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError, match="an iterable of elements"):
        variable_doc.xpath("$good", good="x", bad=[1])  # ty: ignore[invalid-argument-type]  # the second binding is unsupported


def test_integer_too_large_for_a_double(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(OverflowError):
        variable_doc.xpath("$n", n=10**400)


def test_missing_expression_argument(variable_doc: turbohtml.Node) -> None:
    with pytest.raises(TypeError):
        variable_doc.xpath()  # ty: ignore[missing-argument]  # the no-argument path raises at the C boundary


def test_variable_through_xpath_iter(variable_doc: turbohtml.Node) -> None:
    items = [node for node in variable_doc.xpath_iter("//p[@id=$w]", w="a") if isinstance(node, Element)]
    assert [node.tag for node in items] == ["p"]


def test_variable_through_xpath_one(variable_doc: turbohtml.Node) -> None:
    result = variable_doc.xpath_one("//p[@id=$w]", w="c")
    assert isinstance(result, Element)
    assert result.text == "three"


def query(node: turbohtml.Node, expression: str) -> list[Element]:
    return [item for item in node_list(node.xpath(expression)) if isinstance(item, Element)]


def reversed_with_duplicate(node: turbohtml.Node) -> list[Element]:
    paragraphs = query(node, "//p")
    return [paragraphs[1], paragraphs[0], paragraphs[1]]


@pytest.mark.parametrize(
    ("expr", "make_kwargs", "expected_ids"),
    [
        pytest.param(
            "$start//tr",
            lambda document: {"start": query(document, "//table")[0]},
            ["r1", "r2"],
            id="single-element-feeds-a-descendant-step",
        ),
        pytest.param(
            "$rows",
            lambda document: {"rows": query(document, "//tr")},
            ["r1", "r2"],
            id="node-set-returned-directly-as-a-list",
        ),
        pytest.param(
            "$rows | //p",
            lambda document: {"rows": query(document, "//tr")},
            ["r1", "r2", "a", "b", "c"],
            id="union-with-a-node-set",
        ),
        pytest.param(
            "//tr[. = $first]",
            lambda document: {"first": query(document, "//tr[@id='r1']")},
            ["r1"],
            id="predicate-references-a-node-set",
        ),
        pytest.param(
            "$items",
            lambda document: {"items": reversed_with_duplicate(document)},
            ["a", "b"],
            id="normalized-to-document-order-without-duplicates",
        ),
        pytest.param(
            "$items",
            lambda document: {"items": query(document, "//section")},
            [],
            id="empty-node-set",
        ),
    ],
)
def test_node_set_variable_resolves_to_elements(
    variable_doc: turbohtml.Node,
    expr: str,
    make_kwargs: Callable[[turbohtml.Node], dict[str, Element | list[Element]]],
    expected_ids: list[str],
) -> None:
    assert ids(variable_doc.xpath(expr, **make_kwargs(variable_doc))) == expected_ids  # ty: ignore[invalid-argument-type]  # variables unpacked as a dict


def test_node_set_variable_feeds_a_path_step(variable_doc: turbohtml.Node) -> None:
    assert variable_tags(variable_doc.xpath("$rows/td", rows=query(variable_doc, "//tr"))) == ["td", "td", "td"]


@pytest.mark.parametrize(
    ("query_expr", "expected"),
    [
        pytest.param("//p", 3.0, id="count-over-a-populated-node-set"),
        pytest.param("//section", 0.0, id="count-over-an-empty-node-set"),
    ],
)
def test_count_over_a_node_set_variable(variable_doc: turbohtml.Node, query_expr: str, expected: float) -> None:
    assert variable_doc.xpath("count($items)", items=query(variable_doc, query_expr)) == pytest.approx(expected)


def test_node_set_variable_through_xpath_iter(variable_doc: turbohtml.Node) -> None:
    cells = [
        node
        for node in variable_doc.xpath_iter("$rows/td", rows=query(variable_doc, "//tr"))
        if isinstance(node, Element)
    ]
    assert [node.tag for node in cells] == ["td", "td", "td"]


def test_node_set_variable_through_xpath_one(variable_doc: turbohtml.Node) -> None:
    first = variable_doc.xpath_one("$rows", rows=query(variable_doc, "//tr"))
    assert isinstance(first, Element)
    assert first.attr("id") == "r1"


def foreign_nodes() -> list[Element]:
    other = turbohtml.parse("<html><body><span id='x'>elsewhere</span></body></html>")
    return query(other, "//span")


def yield_then_raise(node: turbohtml.Node) -> Iterator[Element]:
    yield query(node, "//p")[0]
    msg = "boom"
    raise RuntimeError(msg)


@pytest.mark.parametrize(
    ("make_items", "exc", "match"),
    [
        pytest.param(lambda _document: foreign_nodes(), ValueError, "different tree", id="node-from-a-different-tree"),
        pytest.param(yield_then_raise, RuntimeError, "boom", id="iterable-raises-partway"),
    ],
)
def test_node_set_variable_rejects_bad_node_sets(
    variable_doc: turbohtml.Node,
    make_items: Callable[[turbohtml.Node], Iterable[Element]],
    exc: type[Exception],
    match: str,
) -> None:
    with pytest.raises(exc, match=match):
        variable_doc.xpath("$items", items=make_items(variable_doc))


@pytest.mark.parametrize(
    "make_items",
    [
        pytest.param(
            lambda document: [query(document, "//p")[0], "not-an-element"], id="non-element-inside-an-iterable"
        ),
        pytest.param(lambda _document: None, id="non-iterable-non-scalar-value"),
    ],
)
def test_node_set_variable_rejects_unsupported_values(
    variable_doc: turbohtml.Node, make_items: Callable[[turbohtml.Node], object]
) -> None:
    with pytest.raises(TypeError, match="an iterable of elements"):
        variable_doc.xpath("$items", items=make_items(variable_doc))  # ty: ignore[invalid-argument-type]  # deliberately wrong value type


_LXML_DOCS: Final = {
    "article": (
        "<!doctype html><html><head><title>T</title></head><body>"
        '<main><article id="a1"><h2>One</h2><p>p1</p><p class="lead">p2</p></article>'
        '<article id="a2"><h2>Two</h2><p>p3</p></article></main>'
        '<nav><ul><li><a href="/x">x</a></li><li><a href="/y">y</a></li></ul></nav>'
        "</body></html>"
    ),
    "table": (
        "<!doctype html><html><head></head><body><table><thead>"
        "<tr><th>H1</th><th>H2</th></tr></thead><tbody>"
        "<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr>"
        "</tbody></table></body></html>"
    ),
}

_LXML_EXPRS: Final = [
    "//p",
    "//a",
    "//a/@href",
    "//p/text()",
    "//h2/text()",
    "/html/body//p",
    "//article",
    "//article/p",
    "//article/h2",
    "//main/article",
    "//*",
    "//div//span",
    "//nav//a/@href",
    "//td",
    "//tr/td",
    "//table//th/text()",
    "//thead/tr/th",
    "descendant::li",
    "//ul/li/a",
    "/html/head/title/text()",
    "//body/*",
    "//p[1]",
    "//p[2]",
    "//p[last()]",
    "//article/p[1]",
    "//li[position()=2]",
    "//li[position()<3]",
    "//li[position()>1]",
    "//p[@class]",
    "//p[@class='lead']",
    "//a[@href='/x']",
    "//article[@id='a2']/p",
    "//article[h2]",
    "//*[contains(@class,'lea')]",
    "//th[text()='H1']",
    "(//p)[1]",
    "(//p)[last()]",
    "//tr/td[1]",
    "//tr/td[last()]",
    "//p[position()=last()]",
    "//main/following::nav",
    "//nav/preceding::article",
    "//article[1]/following::h2",
    "//article[2]/preceding::h2",
    "//h2/following::p",
    "//td/following::td",
    "//tbody/preceding::th",
    "//thead/following::td",
    "//p | //h2",
    "//th | //td",
    "count(//p)",
    "count(//li)",
    "count(//article)",
    "string(//title)",
    "//p[count(//article)=2]",
    "boolean(//p)",
    "boolean(//zzz)",
    "count(//namespace::*)",
    "name(//body/namespace::*)",
    "string(//body/namespace::*)",
    "//article[position()=1]/h2/text()",
]


@pytest.mark.parametrize("expr", _LXML_EXPRS, ids=lambda expr: expr)
@pytest.mark.parametrize("doc_name", list(_LXML_DOCS), ids=list(_LXML_DOCS))
@pytest.mark.oracle
def test_matches_lxml(doc_name: str, expr: str) -> None:
    lxml_html: Final = pytest.importorskip("lxml.html")
    html: Final = _LXML_DOCS[doc_name]
    ours: Final = turbohtml.parse(html).xpath(expr)
    theirs: Final = lxml_html.document_fromstring(html).xpath(expr)
    if isinstance(ours, list):
        assert _normalize_lxml(ours) == _normalize_lxml(theirs)
    else:
        assert ours == theirs


@pytest.mark.oracle
def _normalize_lxml(result: Iterable[object]) -> list[str]:
    out: Final[list[str]] = []
    for item in result:
        if isinstance(item, str):
            out.append(item)
        else:
            tag: Final = getattr(item, "tag", None)
            out.append(tag if isinstance(tag, str) else f"<{type(item).__name__}>")
    return out


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
@pytest.mark.parametrize("change", ["replace", "remove", "readd", "unrelated"])
def test_xpath_callback_attribute_union(*, compiled: bool, smart: bool, change: str) -> None:
    source: Final = Element("section", {"id": "old", "other": "value"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        if change in {"remove", "readd"}:
            del source.attrs["id"]
        if change in {"replace", "readd"}:
            source.attrs["id"] = "new"
        if change == "unrelated":
            source.attrs["other"] = "changed"
        return []

    extensions: Final[dict[tuple[str | None, str], Callable[..., list[Element]]]] = {(None, "mutate"): mutate}
    expression: Final = "@id | mutate() | @id"
    result: Final = (
        XPath(expression, extensions=extensions, smart_strings=smart)(source)
        if compiled
        else source.xpath(expression, extensions={(None, "mutate"): mutate}, smart_strings=smart)
    )
    assert result == ["old"]
    if smart:
        assert isinstance(result, list)
        assert isinstance(result[0], XPathString)
        assert (result[0].getparent(), result[0].attrname) == (source, "id")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("name((@id | mutate())[1])", "id"),
        ("str:concat(@id | mutate())", "old"),
        ("set:distinct(@id | mutate())", ["old"]),
        ("set:intersection(@id | mutate(), @id)", ["old"]),
        ("set:difference(@id | mutate(), @id)", []),
        ("@id = string(mutate())", False),
    ],
)
def test_xpath_callback_attribute_consumers(expression: str, *, expected: str | bool | list[str]) -> None:
    source: Final = Element("section", {"id": "old"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        source.attrs["id"] = "new"
        return []

    assert source.xpath(expression, extensions={(None, "mutate"): mutate}) == expected


def test_xpath_callback_translate_uses_prior_attribute_value() -> None:
    source: Final = Element("section", {"id": "old"})

    def mutate(_context: SimpleNamespace) -> str:
        assert source.xpath("string(@id)") == "old"
        source.attrs["id"] = "new"
        return "o"

    assert source.xpath("translate(@id, mutate(), 'O')", extensions={(None, "mutate"): mutate}) == "Old"


@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
def test_xpath_callback_adopted_pending_owner(*, smart: bool) -> None:
    source: Final = Element("section", {"id": "old"}, children=[Element("b")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    result: Final = source.xpath(". | @id | adopt()", extensions={(None, "adopt"): adopt}, smart_strings=smart)
    assert isinstance(result, list)
    assert result[0] == source
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert target.select("section[live=yes]") == [source]
    assert result[1] == "old"
    if smart:
        assert isinstance(result[1], XPathString)
        assert result[1].getparent() == source


@pytest.mark.parametrize("size", [3, 24])
@pytest.mark.parametrize("operation", ["set:intersection", "set:difference"])
def test_xpath_callback_attribute_membership(size: int, operation: str) -> None:
    source: Final = Element("section", {f"attr{index}": str(index) for index in range(size)})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for index in range(size):
            source.attrs[f"attr{index}"] = "changed"
        return []

    result: Final = source.xpath(f"{operation}(@* | mutate(), @*)", extensions={(None, "mutate"): mutate})
    assert result == ([str(index) for index in range(size)] if operation == "set:intersection" else [])


def test_xpath_callback_preserves_adopted_context() -> None:
    source: Final = Element("section")
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    def inspect(context: SimpleNamespace) -> bool:
        context.context_node.attrs["live"] = "yes"
        return context.context_node == source and target.select("section[live=yes]") == [source]

    assert (
        source.xpath("count(. | adopt()) + inspect()", extensions={(None, "adopt"): adopt, (None, "inspect"): inspect})
        == 2
    )


def test_xpath_callback_attribute_ordinal_collision() -> None:
    source: Final = Element("section", {"old": "first"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        del source.attrs["old"]
        source.attrs["new"] = "second"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["first", "second"]


def test_xpath_callback_shrinks_parsed_attributes() -> None:
    source: Final = parse('<section a="one" b="two" c="three" d="four"></section>').select("section")[0]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for name in ("a", "b", "c", "d"):
            del source.attrs[name]
        source.attrs["new"] = "changed"
        return []

    assert source.xpath("@* | mutate()", extensions={(None, "mutate"): mutate}) == ["one", "two", "three", "four"]


def test_xpath_callback_result_finalizer_adopts_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    assert isinstance(child, Element)
    target: Final = Element("main")

    def produce(_context: SimpleNamespace) -> Iterator[Element]:
        result = (node for node in (child,))
        finalize(result, target.append, child)
        return result

    result: Final = source.xpath("produce()", extensions={(None, "produce"): produce})
    assert isinstance(result, list)
    assert result[0] == child
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert child.attrs["live"] == "yes"


def test_xpath_callback_preserves_text_parent() -> None:
    source: Final = Element("section", children=[Text("body")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    result: Final = source.xpath(
        "text() | adopt() | empty()", extensions={(None, "adopt"): adopt, (None, "empty"): empty}, smart_strings=True
    )
    assert isinstance(result, list)
    assert isinstance(result[0], XPathString)
    result[0].getparent().attrs["live"] = "yes"
    assert (str(result[0]), target.select("section[live=yes]")) == ("body", [source])


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("set:leading(@*, .)", []),
        ("@*[position() > 1 and keep()]", ["two", "three"]),
        ("@*/../@*", ["one", "two", "three"]),
    ],
)
def test_xpath_callback_attribute_paths(expression: str, expected: list[str]) -> None:
    source: Final = Element("section", {"first": "one", "second": "two", "third": "three"})

    def keep(_context: SimpleNamespace) -> bool:
        return True

    assert source.xpath(expression, extensions={(None, "keep"): keep}) == expected


def test_xpath_callback_adopts_variable_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    assert isinstance(child, Element)
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(child)
        return []

    result: Final = source.xpath("adopt() | $node", extensions={(None, "adopt"): adopt}, node=child)
    assert isinstance(result, list)
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert target.select("b[live=yes]") == [child]


def test_xpath_callback_sparse_attribute_union() -> None:
    source: Final = Element("section", {f"data-item-{index}": str(index) for index in range(48)})
    for index in range(48):
        if index % 16:
            del source.attrs[f"data-item-{index}"]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        for name in source.attrs:
            source.attrs[name] = "changed"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["0", "16", "32"]


@pytest.mark.parametrize("expression", ["@second | empty() | @first", "@first | empty() | @second"])
def test_xpath_callback_attribute_union_order(expression: str) -> None:
    source: Final = Element("section", {"first": "one", "second": "two"})

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    assert source.xpath(expression, extensions={(None, "empty"): empty}) == ["one", "two"]


def test_xpath_callback_reuses_attribute_name() -> None:
    source: Final = Element("section", {"new": "placeholder", "old": "first"})
    del source.attrs["new"]

    def mutate(_context: SimpleNamespace) -> list[Element]:
        del source.attrs["old"]
        source.attrs["new"] = "second"
        return []

    assert source.xpath("@* | mutate() | @*", extensions={(None, "mutate"): mutate}) == ["second", "first"]


def test_xpath_callback_mixed_attribute_namespace() -> None:
    source: Final = Element("section", {"id": "value"})

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    assert source.xpath("@id | namespace::* | empty()", extensions={(None, "empty"): empty}) == [
        "value",
        "http://www.w3.org/XML/1998/namespace",
    ]


@pytest.mark.parametrize(
    ("expression", "expected", "moved"),
    [
        pytest.param("@id[self::node()[move()]]", ["old"], True, id="attribute-self"),
        pytest.param("@id[parent::section[move()]]", ["old"], True, id="attribute-parent"),
        pytest.param("@id[child::node()[move()]]", [], False, id="empty-first-step"),
    ],
)
def test_xpath_callback_initial_attribute_context(expression: str, expected: list[str], *, moved: bool) -> None:
    source: Final = Element("section", {"id": "old"})
    target: Final = Element("main")

    def move(_context: SimpleNamespace) -> bool:
        target.append(source)
        del source.attrs["id"]
        return True

    result: Final = source.xpath(expression, extensions={(None, "move"): move})
    assert (result, tuple(target.children)) == (expected, (source,) if moved else ())


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


def test_xpath_frame_completed_arguments_keep_adopted_node() -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    target: Final = Element("main")

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(child)
        return []

    def inspect(_context: SimpleNamespace, _first: list[Element], nodes: list[Element], _last: list[Element]) -> bool:
        nodes[0].attrs["live"] = "yes"
        return target.select("b[live=yes]") == [child]

    assert (
        source.xpath(
            "inspect(empty(), .//b, adopt())",
            extensions={(None, "empty"): empty, (None, "adopt"): adopt, (None, "inspect"): inspect},
        )
        is True
    )


@pytest.mark.parametrize(
    "expression",
    [pytest.param("div/b[adopt()]", id="contexts"), pytest.param("div[seen()]/b[adopt()]", id="steps")],
)
def test_xpath_frame_path_candidates_keep_adopted_nodes(expression: str) -> None:
    source: Final = Element(
        "section", children=[Element("div", children=[Element("b", {"id": str(index)})]) for index in range(2)]
    )
    target: Final = Element("main")

    def seen(_context: SimpleNamespace) -> bool:
        return True

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return True

    result: Final = source.xpath(expression, extensions={(None, "seen"): seen, (None, "adopt"): adopt})
    assert isinstance(result, list)
    for node in result:
        assert isinstance(node, Element)
        node.attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == ["0", "1"]


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("(.//b)[adopt()]", id="callback-first"),
        pytest.param("(@id | .//b)[self::b and adopt()]", id="attribute-first"),
    ],
)
def test_xpath_frame_filter_compaction_keeps_adopted_node(expression: str) -> None:
    source: Final = Element(
        "section", {"id": "root"}, children=[Element("b", {"id": str(index)}) for index in range(3)]
    )
    target: Final = Element("main")

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return context.context_node.attrs["id"] == "2"

    result: Final = source.xpath(expression, extensions={(None, "adopt"): adopt})
    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], Element)
    result[0].attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == ["2"]


def test_xpath_frame_path_retains_accepted_nodes_after_compaction() -> None:
    source: Final = Element(
        "section",
        children=[
            Element("div", children=[Element("b", {"id": str(index * 3 + child)}) for child in range(3)])
            for index in range(8)
        ],
    )
    target: Final = Element("main")

    def adopt(context: SimpleNamespace) -> bool:
        target.append(context.context_node)
        return True

    def keep(context: SimpleNamespace) -> bool:
        return int(context.context_node.attrs["id"]) % 2 == 1

    result: Final = source.xpath("div/b[adopt()][keep()]", extensions={(None, "adopt"): adopt, (None, "keep"): keep})
    assert isinstance(result, list)
    for node in result:
        assert isinstance(node, Element)
        node.attrs["live"] = "yes"
    assert [node.attrs["id"] for node in target.select("b[live=yes]")] == [str(index) for index in range(1, 24, 2)]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("//i[2]/@id", ["b"], id="literal"),
        pytest.param("//i[1.5]/@id", [], id="fraction"),
        pytest.param("//i[0]/@id", [], id="zero"),
        pytest.param("//i[999999999999999999999]/@id", [], id="large"),
        pytest.param("//i[last()]/@id", ["c"], id="last"),
        pytest.param("//i[2][last()]/@id", ["b"], id="literal-last"),
        pytest.param("//i[last()][2]/@id", [], id="last-literal"),
        pytest.param("//i[@id='c']/preceding-sibling::i[1]/@id", ["b"], id="reverse-first"),
        pytest.param("//i[@id='c']/preceding-sibling::i[last()]/@id", ["a"], id="reverse-last"),
        pytest.param("(//i[@id='c']/preceding-sibling::i)[last()]/@id", ["b"], id="sorted-filter"),
        pytest.param("//missing[last()]/@id", [], id="empty-last"),
        pytest.param("//missing[1]/@id", [], id="empty-literal"),
    ],
)
def test_xpath_positional_predicate(expression: str, expected: list[str]) -> None:
    root: Final = parse('<main><i id="a"></i><i id="b"></i><i id="c"></i></main>')
    assert root.xpath(expression) == expected


@pytest.mark.parametrize("predicate", ["1", "last()", "0", "1.5"])
@pytest.mark.parametrize("smart", [False, True], ids=["plain", "smart"])
def test_xpath_positional_attribute_snapshot(predicate: str, *, smart: bool) -> None:
    root: Final = Element("main", {"first": "a", "middle": "b", "last": "c"})
    target: Final = Element("section")

    def move(_context: SimpleNamespace) -> bool:
        target.append(root)
        root.attrs.clear()
        return True

    result: Final = root.xpath(f"@*[{predicate}][move()]", extensions={(None, "move"): move}, smart_strings=smart)
    assert result == {"1": ["a"], "last()": ["c"], "0": [], "1.5": []}[predicate]


@pytest.mark.parametrize(
    ("name", "expected"),
    [pytest.param("custom:last", ["a"], id="extension"), pytest.param("last", ["b"], id="builtin")],
)
def test_xpath_positional_extension_resolution(name: str, expected: list[str]) -> None:
    root: Final = parse('<main><i id="a"></i><i id="b"></i></main>')

    def last(_context: SimpleNamespace) -> int:
        return 1

    assert root.xpath(f"//i[{name}()]/@id", extensions={(None, name): last}) == expected


def test_xpath_positional_last_arity() -> None:
    with pytest.raises(ValueError, match=r"last\(\) takes 0 arguments, got 1"):
        Element("main").xpath("self::*[last(1)]")


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("smart_strings", [False, True], ids=["plain", "smart"])
@pytest.mark.parametrize("mixed", [False, True], ids=["nodes", "mixed"])
@pytest.mark.parametrize("offset", [0, 1, 2, 4])
def test_xpath_snapshot_during_collection(offset: int, *, smart_strings: bool, mixed: bool) -> None:
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    gc.disable()
    gc.collect()
    document: Final = parse('<section><a id="old">first</a><b>second</b></section>')
    section: Final = document.select("section")[0]
    first, second = section.select("*")
    target: Final = Element("main")
    selector: Final = XPath("//a/@id | //a/text() | //b" if mixed else "//a | //b", smart_strings=smart_strings)
    context: Final = (document,)
    changed = False

    def adopt(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            target.append(section)
            first.attrs["id"] = "changed"

    # Empty lists exhaust the freelist before the result allocation.
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
    assert (changed, result, target.select("b"), document.select("b")) == (
        True,
        ["old", "first", second] if mixed else [first, second],
        [second],
        [],
    )
    if smart_strings and mixed:
        assert isinstance(result, list)
        assert [(item.getparent(), item.attrname) for item in result if isinstance(item, XPathString)] == [
            (first, "id"),
            (first, None),
        ]


@pytest.mark.parametrize("compiled", [False, True], ids=["method", "compiled"])
def test_xpath_empty_smart_snapshot(*, compiled: bool) -> None:
    document: Final = parse("<section></section>")
    assert (XPath("//a", smart_strings=True)(document) if compiled else document.xpath("//a", smart_strings=True)) == []
