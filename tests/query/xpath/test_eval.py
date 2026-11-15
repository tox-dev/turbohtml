from __future__ import annotations

import gc
import re
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # the test runs a fixed command in a subprocess
import sys
import threading
from collections import Counter
from contextlib import suppress
from functools import partial
from operator import ge, gt, itemgetter, le, lt
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, TypeAlias, cast
from weakref import finalize, ref

import pytest

import turbohtml
from turbohtml import Document, Element, Text, XPath, XPathString, parse, parse_fragment, parse_xml
from turbohtml.convert import css_to_xpath
from turbohtml.query import XPathDepthError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from types import ModuleType, SimpleNamespace

    from work_steps import WorkSteps


XPathResult: TypeAlias = "Sequence[turbohtml.Node | str | float] | float | bool | str"
XPathExtensions: TypeAlias = (
    "dict[tuple[str | None, str], Callable[[SimpleNamespace], str | float | bool | Element | Iterable[Element]]]"
)


def node_list(result: XPathResult) -> Sequence[turbohtml.Node | str | float]:
    # The node-set list an expression is expected to return.
    assert isinstance(result, list)
    return result


@pytest.fixture
def doc() -> turbohtml.Node:
    html: Final[str] = (
        "<!doctype html><html><head><title>T</title></head><body>"
        '<div id="d1" class="box"><p>a</p><p class="hi">b</p></div>'
        '<div id="d2"><span>s1</span><span>s2</span></div>'
        "<ul><li>1</li><li>2</li></ul>"
        '<a href="/x">L1</a><a href="/y" rel="next">L2</a>'
        "<input disabled>"
        '<my-widget data-k="v">w</my-widget>'
        "<!-- cmt --></body></html>"
    )
    return turbohtml.parse(html)


_SVG: Final[str] = "http://www.w3.org/2000/svg"
_MATHML: Final[str] = "http://www.w3.org/1998/Math/MathML"


@pytest.fixture
def ns_doc() -> turbohtml.Node:
    ns_html: str = (
        "<html><body><p id='para'>html</p><svg width='10'><circle r='5'/><rect/></svg>"
        "<math><mi>x</mi></math></body></html>"
    )
    return turbohtml.parse(ns_html)


def test_descendant_name(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//p")) == ["p", "p"]


def tags(result: XPathResult) -> list[str]:
    return [node.tag for node in node_list(result) if isinstance(node, Element)]


def test_descendant_wildcard_is_elements_only(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("//div/*")) == ["p", "p", "span", "span"]


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("//* or 1", id="descendant-star-or"),
        pytest.param("(* or 1)", id="grouped-star-or"),
    ],
)
def test_operator_after_star_name_test_evaluates(doc: turbohtml.Node, expr: str) -> None:
    # a '*' name test followed by or/and/div/mod is a boolean/arithmetic operator, not a syntax error
    assert doc.xpath(expr) is True


def test_css_has_translation_evaluates(doc: turbohtml.Node) -> None:
    # css_to_xpath emits "descendant::* or descendant::p", which xpath must accept
    assert tags(doc.xpath(css_to_xpath(":has(*, p)"))) == tags(doc.select(":has(*, p)"))


def test_absolute_path(doc: turbohtml.Node) -> None:
    assert tags(doc.xpath("/html/body/div")) == ["div", "div"]


def test_root_node(doc: turbohtml.Node) -> None:
    assert doc.xpath("/") == [doc]


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
def test_root_outside_a_document_is_the_top_of_the_tree(holder: str) -> None:
    top, paragraph = _detached_tree(holder)
    assert paragraph.xpath("/") == [top]


def test_absolute_path_in_a_parsed_fragment_starts_at_the_fragment() -> None:
    bold = turbohtml.parse_fragment("<p><b>x</b></p>").select_one("b")
    assert bold is not None
    assert tags(bold.xpath("/p")) == ["p"]


def test_absolute_path_above_a_parsed_fragment_starts_at_the_document_over_it() -> None:
    fragment: Final = turbohtml.parse_fragment("<p><b>x</b></p>")
    assert fragment.xpath("count(//b/ancestor::node())") == 3  # the p, the fragment, the document it hangs under
    assert fragment.xpath("count(//b/ancestor::node()[/p])") == 2  # from that document /p finds nothing
    document: Final = fragment.xpath_one("..")
    assert isinstance(document, Document)
    assert tags(document.xpath("//b[/p]")) == ["b"]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("//a[@id = /r/@main]/@id", ["2"], id="document"),
        pytest.param("descendant::a[/r][@id = /r/@main]/@id", ["2"], id="relative-outer-path"),
        pytest.param("(//a)[/r/@main = @id]/@id", ["2"], id="filter"),
        pytest.param("//a[/r/@main = @id][not(/k)][/r or other()]/@id", ["2"], id="extension-not-called"),
        pytest.param("$other/a[@id = /@main][/k]/@id", ["1"], id="variable"),
        pytest.param("(//a | $other)[/k]/@main", ["1"], id="union-with-variable"),
        pytest.param("//a[/r][$other/a[/k][not(/r)]][/r][not(/k)]/@id", ["1", "2"], id="variable-between"),
        pytest.param("//a[/r][other()/a[/k][not(/r)]][/r][not(/k)]/@id", ["1", "2"], id="extension-between"),
        pytest.param("//a[id('2')][other()/a[id('1')]]/@id", ["1", "2"], id="id"),
    ],
)
def test_absolute_path_in_a_predicate_starts_at_its_context_root(expression: str, expected: list[str]) -> None:
    document, detached = _two_roots()

    def other(_context: SimpleNamespace) -> list[Element]:
        return [detached]

    result = document.xpath(expression, other=[detached], extensions={(None, "other"): other})
    assert result == expected


def _two_roots() -> tuple[Document, Element]:
    document: Final = parse_xml("<r main='2'><a id='1'><a id='2'/></a><top main='1'><a id='1'/><k/></top></r>")
    detached: Final = one(document, "//top")
    detached.extract()
    return document, detached


def one(node: turbohtml.Node, expr: str) -> Element:
    # The single Element an expression is expected to select.
    result = node.xpath_one(expr)
    assert isinstance(result, Element)
    return result


def test_absolute_path_follows_a_context_node_moved_mid_evaluation() -> None:
    document, detached = _two_roots()

    def adopt(context: SimpleNamespace) -> bool:
        if context.context_node.attrs["id"] == "2":
            detached.append(context.context_node)
        return True

    assert document.xpath("//a[/r][adopt()][/r]/@id", extensions={(None, "adopt"): adopt}) == ["1"]


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("count(//e[/e/@main = @id])", id="comparison"),
        pytest.param("count(//e[/x = @id and unused()])", id="extension-not-called"),
    ],
)
def test_absolute_path_in_a_predicate_over_a_deep_tree_stays_linear(expression: str, work_steps: WorkSteps) -> None:
    def build(size: int) -> Callable[[], object]:
        document: Final = parse_xml("<e>" * size + "</e>" * size)
        return lambda: document.xpath(expression, extensions={(None, "unused"): lambda _context: True})

    work_steps.assert_linear(build, (1000, 4000))


@pytest.mark.parametrize(
    ("expression", "extensions"),
    [
        pytest.param("count(//e[$other and /e/@main = @id])", None, id="variable-first"),
        pytest.param(
            "count(//e[$other and /e/@main = @id])",
            {(None, "unused"): lambda _context: True},
            id="variable-first-with-extensions",
        ),
        pytest.param(
            "count(//e[unused() and /e/@main = @id])", {(None, "unused"): lambda _context: True}, id="extension-first"
        ),
    ],
)
def test_absolute_path_after_a_variable_or_extension_over_a_deep_tree_stays_linear(
    expression: str, extensions: XPathExtensions | None, work_steps: WorkSteps
) -> None:
    def build(size: int) -> Callable[[], object]:
        document: Final = parse_xml("<e>" * size + "</e>" * size)
        root: Final = one(document, "/e")
        return lambda: document.xpath(expression, other=[root], extensions=extensions)

    work_steps.assert_linear(build, (1000, 4000))


def test_absolute_path_from_a_deep_context_node_reaches_the_root() -> None:
    document: Final = parse_xml(f"<r>{_deep_chain(40)}</r>")
    assert one(document, "//e[@id='40']").xpath("name(/*)") == "r"


def _deep_chain(depth: int) -> str:
    return "".join(f"<e id='{number}'>" for number in range(1, depth + 1)) + "</e>" * depth


def test_absolute_path_on_deep_nodes_from_two_roots_keeps_each_root() -> None:
    document: Final = parse_xml(f"<r>{_deep_chain(40)}<t>{_deep_chain(40)}</t></r>")
    detached: Final = one(document, "//t")
    detached.extract()
    assert document.xpath("count((//e | $other//e)[/r])", other=[detached]) == 40


def test_absolute_path_after_a_callback_detaches_a_deep_ancestor_follows_the_new_root() -> None:
    document: Final = parse_xml(f"<r>{_deep_chain(80)}</r>")
    ancestor: Final = one(document, "//e[@id='2']")

    def detach(context: SimpleNamespace) -> bool:
        if context.context_node.attrs["id"] == "70":
            ancestor.extract()
        return True

    result = document.xpath("//e[detach() and /r]/@id", extensions={(None, "detach"): detach})
    assert result == [str(number) for number in range(1, 70)]


def test_absolute_path_after_a_callback_attaches_a_deep_root_follows_the_new_root() -> None:
    document: Final = parse_xml(f"<r><t>{_deep_chain(40)}</t></r>")
    detached: Final = one(document, "//t")
    detached.extract()
    parent: Final = one(document, "/r")

    def attach(context: SimpleNamespace) -> bool:
        if context.context_node.attrs["id"] == "36":
            parent.append(detached)
        return True

    result = document.xpath("$other//e[attach() and /r]/@id", other=[detached], extensions={(None, "attach"): attach})
    assert result == [str(number) for number in range(36, 41)]


@pytest.mark.parametrize(
    "order",
    [
        pytest.param((0, 1, 2), id="in-order"),
        pytest.param((2, 0, 1), id="last-first"),
        pytest.param((1, 2, 0), id="middle-first"),
        pytest.param((2, 1, 0), id="reversed"),
    ],
)
def test_nodes_of_separate_trees_follow_the_order_their_trees_first_appear_in(order: tuple[int, ...]) -> None:
    document, trees = _detached_trees()
    ids: Final = (("a1", "a2"), ("b1", "b2"), ("c1", "c2"))
    result = document.xpath("$other//e/@id", other=[trees[index] for index in order])
    assert result == [node_id for index in order for node_id in ids[index]]


def _detached_trees() -> tuple[Document, list[Element]]:
    document: Final = parse_xml(
        "<r><t><e id='a1'/><e id='a2'/></t><t><e id='b1'/><e id='b2'/></t><t><e id='c1'/><e id='c2'/></t></r>"
    )
    trees: Final = [one(document, f"/r/t[{position}]") for position in (1, 2, 3)]
    for tree in trees:
        tree.extract()
    return document, trees


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("($left//e | $right//e)/@id", ["b1", "b2", "a1", "a2"], id="descendants"),
        pytest.param("($left | $right)/e/@id", ["b1", "b2", "a1", "a2"], id="roots"),
    ],
)
def test_union_of_separate_trees_follows_the_order_of_its_left_operand(expression: str, expected: list[str]) -> None:
    document, (first, second, _third) = _detached_trees()
    assert document.xpath(expression, left=[second], right=[first]) == expected


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
    nested: str = (
        '<div id="o"><div id="i"><a id="1"></a><p id="p"></p></div><a id="2"></a></div><a id="3"></a>'
        '<div id="s"><a id="4"></a></div>'
    )
    found = turbohtml.parse(nested).xpath(expression)
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
    assert doc.xpath(f"//a/@{'z' * 200}") == []


def test_step_after_attribute_yields_nothing(doc: turbohtml.Node) -> None:
    assert doc.xpath("//a/@href/x") == []


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
    attr_context_xml: str = '<div><p n="10">x<b>i</b></p><p n="3">y</p></div>'
    result = parse_xml(attr_context_xml).xpath(expr)
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
    assert doc.xpath(f"//{'a' * 80}") == []


def test_self_axis(doc: turbohtml.Node) -> None:
    paragraph = one(doc, "//p")
    assert tags(paragraph.xpath("self::p")) == ["p"]
    assert paragraph.xpath("self::div") == []


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


_SHARED_HTML: Final[str] = (
    '<div id="a" n="1"><p id="b" n="2" m="3">t<b id="c">u</b><!--c--><b id="d" n="4"><i id="e"></i></b></p>'
    '<p id="f"><b id="g"></b></p></div><div id="h"><b id="i" n="5"></b><b id="j"></b></div>'
)


@pytest.mark.parametrize("predicate", ["", "[1 = 1]", "[1]", "[last()]", "[@n]", "[not(@m)]"])
@pytest.mark.parametrize(
    "axis",
    [
        "ancestor",
        "ancestor-or-self",
        "following",
        "preceding",
        "following-sibling",
        "preceding-sibling",
        "descendant",
        "descendant-or-self",
    ],
)
@pytest.mark.parametrize("contexts", ["//b", "//*", "(//p | //i)", "unordered(//*)", "unordered(//b)"])
def test_step_from_several_contexts_is_the_union_of_each(contexts: str, axis: str, predicate: str) -> None:
    # several contexts reach the same nodes along these axes; each is kept once, whatever order the contexts come in
    document: Final = parse(_SHARED_HTML)
    step: Final = f"{axis}::*{predicate}"
    order: Final = {node: position for position, node in enumerate(node_list(document.xpath("//*")))}
    each: Final = {
        node
        for context in node_list(document.xpath(contexts, extensions={(None, "unordered"): _unordered}))
        if isinstance(context, Element)
        for node in node_list(context.xpath(step))
    }
    found: Final = document.xpath(f"{contexts}/{step}", extensions={(None, "unordered"): _unordered})
    assert found == sorted(each, key=order.__getitem__)


def _unordered(_context: SimpleNamespace, nodes: list[Element]) -> list[Element]:
    return [*reversed(nodes), nodes[0]]


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//@n/ancestor::*", ["html", "body", "a", "b", "d", "h", "i"], id="ancestor"),
        pytest.param("//@*/ancestor::*[1 = 1]", ["html", "body", *"abcdefghij"], id="ancestor-predicate"),
        pytest.param(
            "//p/@*/ancestor-or-self::node()",
            ["#document", "html", "body", "a", "b", "b", "2", "3", "f", "f"],
            id="ancestor-or-self-keeps-each-attribute",
        ),
        pytest.param(
            "//p/@*/ancestor-or-self::node()[position() < 3]",
            ["b", "b", "2", "3", "f", "f"],
            id="ancestor-or-self-predicate",
        ),
        pytest.param("//@m/following::* | //@n/following::*", [*"bcdefghij"], id="following"),
        pytest.param("//p/@*/following::b", [*"cdgij"], id="following-of-one-owner"),
        pytest.param("//@n/following::*[1 = 1]", [*"bcdefghij"], id="following-predicate"),
        pytest.param("//@*/preceding::*", ["head", *"abcdefgi"], id="preceding"),
        pytest.param("//@n/preceding::*[1]", ["head", "c", "g"], id="preceding-predicate"),
        pytest.param("//@*/following-sibling::node()", [], id="following-sibling-is-empty"),
        pytest.param("//@*/preceding-sibling::node()", [], id="preceding-sibling-is-empty"),
        pytest.param(
            "//p/@*/ancestor-or-self::node()[position() mod 2 = 1]",
            ["html", "a", "b", "2", "3", "f"],
            id="ancestor-or-self-per-context",
        ),
        pytest.param(
            "//p/@*/ancestor-or-self::node()[position() > 1][position() < 3]",
            ["a", "b", "f"],
            id="ancestor-or-self-past-the-attribute",
        ),
        pytest.param("//p/@*/descendant-or-self::node()[1]", ["b", "2", "3", "f"], id="descendant-or-self"),
        pytest.param(
            "//p/@*/descendant-or-self::node()[position() mod 2 = 1]",
            ["b", "2", "3", "f"],
            id="descendant-or-self-per-context",
        ),
        pytest.param(
            "//p/@*/descendant-or-self::node()[. != '2'][1]", ["b", "3", "f"], id="descendant-or-self-filtered"
        ),
    ],
)
def test_step_from_several_attribute_contexts(expr: str, expected: list[str]) -> None:
    result: Final = parse(_SHARED_HTML).xpath(expr)
    assert [_label(node) for node in node_list(result)] == expected


def _label(node: turbohtml.Node | str | float) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, Element):
        return str(node.attrs.get("id") or node.tag)
    return "#document"


@pytest.mark.parametrize(
    ("depth", "expression", "count"),
    [
        pytest.param(1, "//b/following::b", 999, id="following"),
        pytest.param(1, "//b/preceding::b", 999, id="preceding"),
        pytest.param(1, "//b/following-sibling::b", 999, id="following-sibling"),
        pytest.param(1, "//b/preceding-sibling::b", 999, id="preceding-sibling"),
        pytest.param(1, "//@x/following::b", 999, id="following-of-attributes"),
        pytest.param(200, "//b/ancestor::div", 200, id="ancestor"),
        pytest.param(200, "//b/ancestor-or-self::*", 1202, id="ancestor-or-self"),
        pytest.param(200, "//@x/ancestor::div", 200, id="ancestor-of-attributes"),
        pytest.param(1, "//b/following-sibling::b[@x]", 999, id="following-sibling-predicate"),
        pytest.param(1, "//b/preceding::b[@x]", 999, id="preceding-predicate"),
        pytest.param(200, "//div//b[@x]", 1000, id="descendant-predicate"),
    ],
)
def test_step_from_many_contexts_holds_each_node_once(depth: int, expression: str, count: int) -> None:
    # each context appended its whole axis and the sort dropped the repeats afterwards: 1000 siblings held 8 MB of
    # (node, attribute) pairs for following::b, and 200 levels 4 MB for ancestor::div; PyPy ships no tracemalloc
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    document: Final = parse("<div>" * depth + '<b x="1"></b>' * 1000 + "</div>" * depth)
    tracing.start()
    try:
        found: Final = node_list(document.xpath(expression))
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert len(found) == count
    assert peak < 1_000_000, f"{expression} peaked at {peak} bytes"


@pytest.mark.parametrize("axis", ["following", "preceding"])
def test_step_from_unordered_contexts_holds_each_node_once(axis: str) -> None:
    # an extension function hands the contexts over in reverse, so no walk passes the next one and each context gets a
    # walk of its own; the nodes a walk repeats are dropped as it ends instead of held until the sort (8 MB here)
    tracing: Final[ModuleType] = pytest.importorskip("tracemalloc")
    document: Final = parse(f"<div>{'<b></b>' * 1000}</div>")
    tracing.start()
    try:
        found: Final = node_list(
            document.xpath(f"unordered(//b)/{axis}::b", extensions={(None, "unordered"): _unordered})
        )
        peak: Final[int] = tracing.get_traced_memory()[1]
    finally:
        tracing.stop()
    assert len(found) == 999
    assert peak < 1_000_000, f"{axis} peaked at {peak} bytes"


def test_error_in_a_predicate_of_a_shared_document_step_propagates() -> None:
    document: Final = parse("<div><b></b><b></b><b></b></div>")
    with pytest.raises(ValueError, match="unbound variable"):
        document.xpath("//b/following::b[@x = $missing]")


@pytest.mark.parametrize(
    ("number", "expected"),
    [pytest.param(1, [1, 2], id="first"), pytest.param(2, [2], id="second")],
)
def test_variable_predicate_selects_by_position_for_each_context(number: int, expected: list[int]) -> None:
    document: Final = parse("<ul><li></li><li></li><li></li></ul>")
    items: Final = node_list(document.xpath("//li"))
    assert document.xpath("//li/following-sibling::li[$n]", n=number) == [items[index] for index in expected]


@pytest.mark.parametrize("predicate", ["$n", "two()", "number($n)"])
def test_number_predicate_selects_by_position_within_each_parent(predicate: str) -> None:
    document: Final = parse("<ul><li></li><li></li></ul><ul><li></li><li></li></ul>")
    items: Final = node_list(document.xpath("//li"))
    found: Final = document.xpath(f"//li[{predicate}]", n=2, extensions={(None, "two"): lambda _context: 2})
    assert found == [items[1], items[3]]


_NESTED_HTML: Final[str] = (
    '<div k="1"><b k="0">t<b k="2"><i></i>u<b></b></b><i k="1"></i></b><i k="2"><b k="1"></b></i></div>'
    '<div><b><i k="0"><b k="2"></b></i></b>v<b k="1"></b><i></i></div>'
)


@pytest.mark.parametrize(
    "predicates",
    [
        "[2]",
        "[last()]",
        "[last() - 1]",
        "[$n]",
        "[$half]",
        "[position() > 1]",
        "[position() <= last() - 1]",
        "[2 < position()]",
        "[last() - position() < 2]",
        "[-position() > -3]",
        "[position() * 2 > last()]",
        "[position() div 2 <= 1.5]",
        "[position() + 1 = last()]",
        "[position() != 2]",
        "[position() = 2 or position() = last()]",
        "[not(position() = 1)]",
        "[boolean(position() > 2) and position() < 5]",
        "[position() = '2']",
        "[position() > true()]",
        "[position() * 0 = 0]",
        "[position() = $n]",
        "[position() < $none]",
        "[position() < count(//b)]",
        "[position() mod 2 = 0]",
        "[position() < @k + 2]",
        "[@k and position() = 1]",
        "[@k][position() > 1]",
        "[@k = '1'][2]",
        "[position() > 1][@k]",
        "[1][position() = 1][not(@k)]",
        "[position() < 3][last()]",
        "[@k][last()][@k != '0']",
        "[position() = 1 or position() = 3 or position() = 5 or position() = 7 or position() = 9]",
        "[(position() < 3 or position() > 5) and position() < 7]",
        "[position() < 2 or position() > 3 and position() < 5]",
        "[position() = 1 or position() > 3][position() > 1]",
        "[5 - position() > 2]",
        "[position() < number('x')]",
        "[position() != number('x')]",
        "[position() > 'x']",
        "[position() >= 2]",
        "[-position() >= -2]",
        "[-position() <= -2]",
        "[-position() < -2]",
        "[-position() = -2]",
        "[-position() != -2]",
        "[3 > position()]",
        "[3 >= position()]",
        "[3 <= position()]",
        "[position() + $none > 1]",
        "[position() div 0 > 1]",
        "[position() > $none]",
        "[position() = true()]",
        "[boolean(position() = 2)]",
        "[1.5]",
        "[0]",
        "[$n + 1]",
        "[$s]",
        "[$flag]",
        "[position() > lang('en')]",
        "[position() + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 > 11]",
        "[position() > 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 - 9]",
        "[not(not(not(not(not(not(not(not(not(position() = 1)))))))))]",
        "[position() > 3 or position() = 1]",
        "[position() < 2 and position() < 5]",
        "[position() - 1 > 1]",
        "[position() > 1 and 2]",
        "[position() > 1 and 0]",
        "[position() > $none or position() = 1]",
        "[not(position() > $none)]",
        "[position() != true()]",
        "[2 = position()]",
        "[2 != position()]",
        "[position() > 50][2]",
        "[position() > string-length()]",
        "[(position() > 1) = true()]",
        "[position() + count(.) > 2]",
        "[2 div position() > 1]",
        "[1 + string-length() > position()]",
        "[position() > 1 and string-length()]",
        "[1][@k][last()]",
        "[position() > position()]",
        "[position() > 1 and position()]",
        "[1 > position() + count(.)]",
    ],
)
@pytest.mark.parametrize(
    "axis",
    [
        "ancestor",
        "ancestor-or-self",
        "following",
        "preceding",
        "following-sibling",
        "preceding-sibling",
        "descendant",
        "descendant-or-self",
    ],
)
@pytest.mark.parametrize("test", ["*", "b"])
@pytest.mark.parametrize(
    "contexts",
    ["//b", "//*", "(//i | //b)", "//@k", "(//i/@k | //b)", "//text()", "//b/ancestor-or-self::node()", "$v", "$v/@k"],
)
def test_positional_step_from_several_contexts_is_the_union_of_each(
    contexts: str, test: str, axis: str, predicates: str
) -> None:
    # contexts sharing a stretch of the axis count positions along their own view of it, as each would alone
    document: Final = parse(_NESTED_HTML)
    order: Final = {node: position for position, node in enumerate(node_list(document.xpath("//node()")))}
    picked: Final = [*element_list(document.xpath("//b"))[1:4], *element_list(document.xpath("//i"))[:2]]

    def run(expression: str) -> XPathResult:
        return document.xpath(expression, n=2, half=1.5, s="x", flag=True, none=[], v=picked)

    step: Final = f"{axis}::{test}{predicates}"
    count: Final = run(f"count({contexts})")
    assert isinstance(count, float)
    each: Final = {
        node for index in range(1, int(count) + 1) for node in node_list(run(f"({contexts})[{index}]/{step}"))
    }
    assert run(f"{contexts}/{step}") == sorted(each, key=order.__getitem__)


def element_list(result: XPathResult) -> list[Element]:
    # The element node-set an expression is expected to return.
    return [element for element in node_list(result) if isinstance(element, Element)]


@pytest.mark.parametrize(
    "step", ["following::*[1]", "preceding-sibling::*[last()]", "ancestor::*[position() mod 2 = 1]", "descendant::i[1]"]
)
def test_positional_step_from_unordered_contexts(step: str) -> None:
    # an extension function hands the contexts over in reverse with a repeat; the step sorts them before it walks
    document: Final = parse(_NESTED_HTML)
    result: Final = document.xpath(f"unordered(//b)/{step}", extensions={(None, "unordered"): _unordered})
    assert result == document.xpath(f"//b/{step}")


@pytest.mark.parametrize(
    "step",
    [
        "ancestor::d[1]",
        "ancestor::*[2]",
        "following::*[1]",
        "preceding::*[1]",
        "preceding::d[last()]",
        "descendant::e[1]",
    ],
)
def test_positional_step_from_a_detached_subtree_and_its_document(step: str) -> None:
    # the contexts span two trees, which share no walk; the order between the trees is unspecified
    document: Final = parse_xml("<r><d><d><e/></d></d><d><e/></d><x><d><d><e/></d></d><d><e/></d></x></r>")
    detached: Final = node_list(document.xpath("//x"))[0]
    assert isinstance(detached, Element)
    detached.extract()
    inside: Final = element_list(detached.xpath(".//e | .//d"))
    outside: Final = element_list(document.xpath("//e | //d"))
    mixed: Final = [node for pair in zip(outside, inside, strict=True) for node in pair]
    each: Final = {node for context in mixed for node in node_list(context.xpath(step))}
    assert set(node_list(document.xpath(f"$v/{step}", v=mixed))) == each


@pytest.mark.parametrize(
    "predicates",
    [
        "[position() > $missing]",
        "[position() > 1 or $missing]",
        "[position() + $missing > 1]",
        "[$missing]",
        "[position() mod 2 = $missing]",
        "[@k = $missing][position() > 1]",
        "[1][@k = $missing]",
    ],
)
@pytest.mark.parametrize(
    "step",
    [
        "following::*",
        "preceding::*",
        "following-sibling::*",
        "preceding-sibling::*",
        "ancestor::*",
        "ancestor-or-self::node()",
        "descendant::*",
        "descendant-or-self::node()",
    ],
)
@pytest.mark.parametrize("contexts", ["//b", "(//i/@k | //b)"])
def test_positional_step_with_an_unbound_variable_raises(contexts: str, step: str, predicates: str) -> None:
    with pytest.raises(ValueError, match="unbound variable"):
        parse(_NESTED_HTML).xpath(f"{contexts}/{step}{predicates}")


@pytest.mark.parametrize(
    ("predicate", "message"),
    [
        pytest.param("position() > 1 and not()", r"not\(\) takes 1 argument, got 0", id="not-without-argument"),
        pytest.param("position() > 1 and not(1, 2)", r"not\(\) takes 1 argument, got 2", id="not-with-two"),
        pytest.param("position() > concat(1)", r"concat\(\) takes at least 2 arguments", id="concat-with-one"),
    ],
)
def test_positional_step_with_a_wrong_argument_count_raises(predicate: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse(_NESTED_HTML).xpath(f"//b/following::*[{predicate}]")


@pytest.mark.parametrize("predicates", ["[. = $missing][1]", "[position() mod 2 = $missing]"])
@pytest.mark.parametrize("step", ["descendant-or-self::node()", "ancestor-or-self::node()"])
def test_positional_self_step_from_attributes_with_an_unbound_variable_raises(step: str, predicates: str) -> None:
    with pytest.raises(ValueError, match="unbound variable"):
        parse(_NESTED_HTML).xpath(f"//@k/{step}{predicates}")


def test_ancestor_or_self_attribute_predicate_error_raises() -> None:
    # the attribute item is judged after the element path, so a predicate that fails only there must still raise
    document = parse('<p a="1" b="200000">1</p>')
    with pytest.raises(ValueError, match="str:padding length 200000 exceeds"):
        document.xpath("//p/@*/ancestor-or-self::node()[boolean(str:padding(number(.)))][position() < 2]")


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("//c/following-sibling::c[position() mod 2 = 0]", id="following-sibling"),
        pytest.param("//c/preceding::c[position() mod 2 = 0]", id="preceding"),
        pytest.param("//c/following::c[position() < last() and position() mod 2 = 0]", id="following"),
    ],
)
def test_position_test_from_many_contexts_stops_at_the_node_limit(expression: str) -> None:
    # a position test with no closed form for the shared stretch reads each context's axis on its own, up to a limit
    with pytest.raises(ValueError, match="reached more than 10,000,000 nodes"):
        _siblings(5000).xpath(expression)


def test_position_test_from_many_contexts_below_the_node_limit() -> None:
    assert len(node_list(_siblings(2000).xpath("//c/following-sibling::c[position() mod 2 = 0]"))) == 1998


_SIBLING_COUNT: Final[int] = 12


def test_five_disjoint_runs_from_many_contexts_keep_the_odd_offsets() -> None:
    # five ordering runs in one predicate fill the run list past its first capacity; each context keeps the siblings
    # at the odd offsets 1 to 9 from it, and the walks share those members
    count: Final = 12
    runs: Final = " or ".join(f"(position() > {low} and position() < {low + 2})" for low in (0, 2, 4, 6, 8))
    kept: Final = sorted({
        context + offset for context in range(count) for offset in (1, 3, 5, 7, 9) if context + offset < count
    })
    result = node_list(_siblings(count).xpath(f"//c/following-sibling::c[{runs}]"))
    assert sorted(_k_attributes(result)) == sorted(str(index) for index in kept)


def _k_attributes(result: XPathResult) -> list[str]:
    # the k attribute of each element in a node-set, in the order the expression returned them
    return [str(node.attrs["k"]) for node in node_list(result) if isinstance(node, Element)]


@pytest.mark.parametrize(
    ("predicate", "value_predicate"),
    [
        pytest.param("position() > 1 and position() < $v", "position() > 1 and position() < 4", id="second-operand"),
        pytest.param("position() < $v and position() > 1", "position() < 4 and position() > 1", id="first-operand"),
        pytest.param("not(position() < $v)", "not(position() < 4)", id="negated"),
    ],
)
def test_node_set_operand_in_a_positional_test_matches_its_value(predicate: str, value_predicate: str) -> None:
    # a node-set compares by the string values of its members, which the per-context evaluation reads as the number 4
    children: Final = "".join(f'<c k="{index}"/>' for index in range(_SIBLING_COUNT))
    doc: Final = parse_xml(f"<r><v>4</v>{children}</r>")
    bound: Final = one(doc, "//v")
    result = doc.xpath(f"//c/following-sibling::c[{predicate}]", v=bound)
    expected = doc.xpath(f"//c/following-sibling::c[{value_predicate}]")
    assert _k_attributes(result) == _k_attributes(expected)


@pytest.mark.parametrize(
    ("predicate", "keep"),
    [
        pytest.param("position() < 3 or (position() > 2 and position() < 4)", lambda k: k < 4, id="adjacent-runs"),
        pytest.param("position() < 6 or (position() > 1 and position() < 4)", lambda k: k < 6, id="contained-run"),
        pytest.param("position() < 4 or (position() > 1 and position() < 7)", lambda k: k < 7, id="overlapping-runs"),
        pytest.param("position() < 2 or position() > 3", lambda k: k < 2 or k > 3, id="gap-between-runs"),
        pytest.param("position() > 9 or position() < 2", lambda k: k < 2 or k > 9, id="late-run-first"),
        pytest.param(
            "position() < 3 or (position() > 4 and position() < 6) or position() > 8",
            lambda k: k < 3 or 4 < k < 6 or k > 8,
            id="three-runs",
        ),
        pytest.param("(position() + 1) < 4", lambda k: k + 1 < 4, id="add-left"),
        pytest.param("2 < (position() + 1)", lambda k: k + 1 > 2, id="add-right"),
        pytest.param("(position() - 1) < 3", lambda k: k - 1 < 3, id="subtract-left"),
        pytest.param("(2 * position()) < 8", lambda k: 2 * k < 8, id="multiply-left"),
        pytest.param("position() div 2 < 2", lambda k: k / 2 < 2, id="divide-left"),
        pytest.param("10 - position() < 7", lambda k: 10 - k < 7, id="subtract-from-constant"),
        pytest.param("-position() < -2", lambda k: -k < -2, id="negate-left"),
        pytest.param("3 >= position()", lambda k: k <= 3, id="flipped-ge"),
        pytest.param("2 <= position()", lambda k: k >= 2, id="flipped-le"),
        pytest.param("4 > position()", lambda k: k < 4, id="flipped-gt"),
        pytest.param("2 < position()", lambda k: k > 2, id="flipped-lt"),
        pytest.param("position() <= 1", lambda k: k <= 1, id="le-first"),
        pytest.param("position() >= 5", lambda k: k >= 5, id="ge-tail"),
    ],
)
def test_ordering_predicate_over_overlapping_siblings_keeps_its_offsets(
    predicate: str, keep: Callable[[int], bool]
) -> None:
    # a predicate of ordering comparisons and arithmetic, with unions of runs, from every context of a shared walk
    kept: Final = {
        index + offset for index in range(_SIBLING_COUNT) for offset in range(1, _SIBLING_COUNT - index) if keep(offset)
    }
    result = _siblings(_SIBLING_COUNT).xpath(f"//c/following-sibling::c[{predicate}]")
    assert sorted(_k_attributes(result)) == sorted(str(index) for index in kept)


def test_chained_predicates_over_overlapping_siblings_keep_their_offsets() -> None:
    # the second predicate counts the positions the first one kept, in each context's stretch: all but the first
    expected: Final = sorted({
        index + offset
        for index in range(_SIBLING_COUNT)
        for offset in sorted(k for k in range(1, _SIBLING_COUNT - index) if k < 2 or k > 4)[1:]
    })
    result = _siblings(_SIBLING_COUNT).xpath(
        "//c/following-sibling::c[position() < 2 or position() > 4][position() > 1]"
    )
    assert sorted(_k_attributes(result)) == sorted(str(index) for index in expected)


@pytest.mark.parametrize(
    "axis",
    [
        "following::c",
        "following-sibling::c",
        "preceding::c",
        "preceding-sibling::c",
        "ancestor::*",
        "ancestor-or-self::*",
        "descendant::*",
        "descendant-or-self::*",
    ],
)
@pytest.mark.parametrize(
    "predicates",
    [
        "[re:test(., '[')][position() < 5]",
        "[position() < 5][re:test(., '[')]",
        "[position() < 5 and re:test(., '[')]",
    ],
)
def test_failing_predicate_in_a_shared_walk_raises(axis: str, predicates: str) -> None:
    # the regular expression fails to compile on each node, which the walk reports from the predicate it is in
    with pytest.raises(re.error):
        _siblings(12).xpath(f"//*/{axis}{predicates}")


@pytest.mark.parametrize(
    "axis",
    [
        "following::*",
        "following-sibling::*",
        "preceding::*",
        "preceding-sibling::*",
        "ancestor::*",
        "ancestor-or-self::*",
        "descendant::*",
        "descendant-or-self::*",
        "descendant-or-self::node()",
        "ancestor-or-self::node()",
    ],
)
def test_unbound_variable_in_a_predicate_ahead_of_a_position_test_raises(axis: str) -> None:
    # the predicate ahead of the position test runs on each node without a Python call, so the walk reports the unbound
    # variable from the predicate it is in, rather than a per-context evaluation
    with pytest.raises(ValueError, match="unbound variable"):
        _siblings(12).xpath(f"//*/{axis}[true() and $missing][position() < 5]")


@pytest.mark.parametrize("step", ["descendant-or-self::node()", "ancestor-or-self::node()"])
def test_unbound_variable_on_an_attribute_stretch_raises(step: str) -> None:
    # an attribute's own stretch holds only the attribute, which the predicate ahead of the position test judges
    with pytest.raises(ValueError, match="unbound variable"):
        _siblings(12).xpath(f"//@k/{step}[true() and $missing][position() < 5]")


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
    first = next(doc.xpath_iter("//*"))
    assert isinstance(first, Element)
    assert first.tag == "html"


def test_xpath_iter_propagates_errors(doc: turbohtml.Node) -> None:
    with pytest.raises(ValueError, match="unknown function 'bogus-fn'"):
        doc.xpath_iter("bogus-fn(1)")
    with pytest.raises(TypeError, match="must be a str"):
        doc.xpath_iter(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


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


@pytest.fixture
def table_doc() -> turbohtml.Node:
    table_html: str = (
        '<table><tr><td class="num">1</td><td>2</td></tr><tr><td class="num">3</td><td class="num">4</td></tr></table>'
    )
    return turbohtml.parse(table_html)


@pytest.fixture
def links_doc() -> turbohtml.Node:
    links_html: str = "<html><body><a href='/x'>one</a><a href='/y'>two</a></body></html>"
    return turbohtml.parse(links_html)


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
    construct: Callable[[str | int | list[int]], object],
    argument: str | int | list[int],
    exc: type[Exception],
    match: str,
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
    def count_nodes(_context: SimpleNamespace, nodes: list[Element]) -> float:
        return float(len(nodes))

    extensions: dict[tuple[str | None, str], Callable[[SimpleNamespace, list[Element]], str | float | bool]] = {
        (None, "count_nodes"): count_nodes
    }
    selector = XPath("count_nodes(//a)", extensions=extensions)
    assert selector(links_doc) == pytest.approx(2.0)


def test_compiled_extension_receives_context_node(links_doc: turbohtml.Node) -> None:
    def context_tag(context: SimpleNamespace) -> str:
        return context.context_node.tag

    extensions: XPathExtensions = {(None, "tag"): context_tag}
    matched = node_list(XPath("//a[tag()='a']", extensions=extensions)(links_doc))
    assert all(isinstance(node, Element) and node.tag == "a" for node in matched)
    assert len(matched) == 2


@pytest.mark.parametrize(
    "extensions",
    [pytest.param({}, id="empty-dict"), pytest.param(None, id="none")],
)
def test_compiled_falsy_extensions_bind_nothing(
    links_doc: turbohtml.Node,
    extensions: XPathExtensions | None,
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
    "expr",
    [
        pytest.param("count(//a[@x='1'] | //b[@y='2'])", id="union-in-count"),
        pytest.param("count(//b[@y='2'] | //a[@x='1'])", id="union-in-count-swapped"),
    ],
)
def test_predicate_survives_in_compound_expression(union_doc: turbohtml.Node, expr: str) -> None:
    assert union_doc.xpath(expr) == pytest.approx(2.0)


def test_predicate_survives_union_root(union_doc: turbohtml.Node) -> None:
    assert len(node_list(union_doc.xpath("//a[@x='1'] | //b[@y='2']"))) == 2


# Namespace-prefixed name tests bound through the ``namespaces=`` keyword (issue #263).
# A prefixed test like ``//svg:rect`` resolves its prefix against the supplied mapping, then
# matches an element whose foreign-content namespace equals the bound URI and whose local name
# equals the suffix. HTML elements stay in the null namespace, so unprefixed tests are unaffected.
# The binding happens at evaluation time, so the cached compiled program runs under any mapping.


@pytest.mark.parametrize(
    ("expr", "mapping", "expected"),
    [
        pytest.param("//svg:circle", {"svg": _SVG}, ["circle"], id="svg-text-matched-local"),
        pytest.param("//svg:rect", {"svg": _SVG}, ["rect"], id="svg-second-child"),
        pytest.param("//s:circle", {"s": _SVG}, ["circle"], id="prefix-name-is-arbitrary"),
        pytest.param("//m:math", {"m": _MATHML}, ["math"], id="mathml-atom-matched-local"),
        pytest.param("//m:mi", {"m": _MATHML}, ["mi"], id="mathml-leaf"),
        pytest.param("//svg:circle | //m:mi", {"svg": _SVG, "m": _MATHML}, ["circle", "mi"], id="two-prefixes"),
        pytest.param("/html/body/svg:svg", {"svg": _SVG}, ["svg"], id="prefixed-step-in-path"),
        # a prefix bound to the empty URI selects the null (HTML) namespace
        pytest.param("//h:p", {"h": ""}, ["p"], id="null-namespace-prefix-matches-html"),
        # a same-length-but-different bound prefix is skipped; a later exact entry still resolves
        pytest.param("//svg:circle", {"abc": _MATHML, "svg": _SVG}, ["circle"], id="same-length-prefix-distinguished"),
    ],
)
def test_prefixed_match(ns_doc: turbohtml.Node, expr: str, mapping: dict[str, str], expected: list[str]) -> None:
    assert tags(ns_doc.xpath(expr, namespaces=mapping)) == expected


@pytest.mark.parametrize(
    ("expr", "mapping"),
    [
        pytest.param("//svg:circle", {"svg": _MATHML}, id="wrong-uri-for-prefix"),
        pytest.param("//svg:p", {"svg": _SVG}, id="html-local-name-not-in-svg-ns"),
        pytest.param("//html:circle", {"html": ""}, id="svg-element-not-in-null-ns"),
        pytest.param("//svg:nope", {"svg": _SVG}, id="no-such-local-name"),
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
        pytest.param({"other": _SVG}, id="mapping-lacks-prefix"),
        pytest.param({"abc": _SVG}, id="same-length-prefix-only"),
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
        pytest.param([("svg", _SVG)], "namespaces must be a dict", id="not-a-dict"),
        pytest.param({"svg": 1}, "map str prefixes to str URIs", id="non-str-value"),
        pytest.param({1: _SVG}, "map str prefixes to str URIs", id="non-str-key"),
    ],
)
def test_namespaces_type_errors(
    ns_doc: turbohtml.Node, namespaces: list[tuple[str, str]] | dict[str | int, str | int], message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        # a wrong-typed mapping exercises the TypeError path the C binding guards
        ns_doc.xpath("//svg:circle", namespaces=namespaces)  # ty: ignore[invalid-argument-type]


def test_unprefixed_name_test_is_namespace_agnostic(ns_doc: turbohtml.Node) -> None:
    # Without a prefix the test matches by local name in any namespace, as before.
    assert tags(ns_doc.xpath("//circle")) == ["circle"]
    assert tags(ns_doc.xpath("//circle", namespaces={"svg": _SVG})) == ["circle"]


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
    assert ns_doc.xpath("//svg:svg/@svg:width", namespaces={"svg": _SVG}) == []
    assert ns_doc.xpath("//svg:svg/@width", namespaces={"svg": _SVG}) == ["10"]


_XML_NS: Final[str] = "http://www.w3.org/XML/1998/namespace"


@pytest.mark.parametrize(
    ("markup", "expr", "namespaces", "expected"),
    [
        pytest.param("<r><d xml:lang='en'/></r>", "//@xml:lang", None, ["en"], id="xml-tree-implicit-prefix"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@x:lang", {"x": _XML_NS}, ["en"], id="xml-tree-rebound-prefix"),
        pytest.param("<r><d xml:lang='en'/></r>", "//@xml:space", None, [], id="xml-tree-other-local-name"),
        pytest.param("<r><d xml:lang='en'/></r>", f"//@xml:{'z' * 130}", None, [], id="xml-tree-overlong-local-name"),
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


_ATOM: Final[str] = "http://www.w3.org/2005/Atom"


@pytest.mark.parametrize(
    ("markup", "expr", "namespaces", "expected"),
    [
        pytest.param(f'<feed xmlns="{_ATOM}"><entry/></feed>', "//entry", None, [], id="default-namespace-element"),
        pytest.param(
            f'<feed xmlns="{_ATOM}"><entry/></feed>', "//a:entry", {"a": _ATOM}, ["entry"], id="prefixed-registered"
        ),
        pytest.param(
            f'<feed xmlns="{_ATOM}"><entry/></feed>', "//a:entry", {"a": "urn:other"}, [], id="prefixed-other-uri"
        ),
        pytest.param(f'<feed xmlns="{_ATOM}"><entry/></feed>', "/feed", None, [], id="root-in-default-namespace"),
        pytest.param(
            f'<feed xmlns="{_ATOM}"><entry/></feed>', "//entry", {"a": _ATOM}, [], id="unprefixed-ignores-bindings"
        ),
        pytest.param("<feed><entry/></feed>", "//entry", None, ["entry"], id="no-namespace"),
        pytest.param(
            f'<feed xmlns="{_ATOM}"><x xmlns=""><entry/></x></feed>',
            "//entry",
            None,
            ["entry"],
            id="undeclared-default",
        ),
        pytest.param(
            f'<feed xmlns="{_ATOM}"><x xmlns=""><entry/></x></feed>',
            "//a:entry",
            {"a": _ATOM},
            [],
            id="undeclared-not-atom",
        ),
        pytest.param(
            '<p:feed xmlns:p="urn:a"><p:entry/><entry/></p:feed>',
            "//entry",
            None,
            ["entry"],
            id="prefixed-element-skipped",
        ),
        pytest.param(
            '<p:feed xmlns:p="urn:a"><p:entry/></p:feed>', "//p:entry", {"p": "urn:a"}, ["p:entry"], id="own-prefix"
        ),
        pytest.param(
            '<p:feed xmlns:p="urn:a"><p:entry/></p:feed>', "//p:entry", {"p": "urn:b"}, [], id="own-prefix-other"
        ),
        pytest.param(
            '<r xmlns="urn:a"><e1/><q xmlns="urn:b"><e2/></q></r>',
            "//b:e2",
            {"b": "urn:b"},
            ["e2"],
            id="inner-declaration",
        ),
        pytest.param(
            '<r xmlns="urn:a"><e1/><q xmlns="urn:b"><e2/></q></r>',
            "//a:e2",
            {"a": "urn:a"},
            [],
            id="outer-declaration-hidden",
        ),
        pytest.param(
            "<\U0001d49c:x xmlns:\U0001d49c='urn:z'><\U0001d49c:y/></\U0001d49c:x>",
            "//\U0001d49c:y",
            {"\U0001d49c": "urn:z"},
            ["\U0001d49c:y"],
            id="astral-prefix",
        ),
        pytest.param(
            "<é:x xmlns:é='urn:e'><é:y/></é:x>",
            "//é:y",
            {"é": "urn:e"},
            ["é:y"],
            id="two-byte-prefix",
        ),
        pytest.param(
            "<中:x xmlns:中='urn:c'><中:y/></中:x>",
            "//中:y",
            {"中": "urn:c"},
            ["中:y"],
            id="three-byte-prefix",
        ),
    ],
)
def test_xml_name_test_matches_the_namespace_in_scope(
    markup: str, expr: str, namespaces: dict[str, str] | None, expected: list[str]
) -> None:
    assert tags(parse_xml(markup).xpath(expr, namespaces=namespaces)) == expected


@pytest.mark.parametrize(
    ("expr", "namespaces", "expected"),
    [
        pytest.param("//a/@href", None, ["2"], id="unprefixed-attribute"),
        pytest.param("//a/@x:href", {"x": "urn:x"}, ["1"], id="bound-prefixed-attribute"),
        pytest.param("//a/@x:href", {"x": "urn:y"}, [], id="other-uri-attribute"),
    ],
)
def test_xml_name_test_matches_attributes_by_namespace(
    expr: str, namespaces: dict[str, str] | None, expected: list[str]
) -> None:
    assert parse_xml('<a xmlns:x="urn:x" x:href="1" href="2"/>').xpath(expr, namespaces=namespaces) == expected


def test_xml_undefined_prefix_raises() -> None:
    with pytest.raises(ValueError, match="undefined namespace prefix"):
        parse_xml(f'<feed xmlns="{_ATOM}"><entry/></feed>').xpath("//a:entry")


def test_xml_name_test_climbs_scale_linearly(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(_deep_default_namespace, (500, 2000))


def _deep_default_namespace(depth: int) -> Callable[[], object]:
    document = parse_xml(f'<r xmlns="urn:a">{"<x>" * depth}{"</x>" * depth}</r>')
    return partial(document.xpath, "//a:x", namespaces={"a": "urn:a"})


def test_xml_name_test_resolves_many_prefixes() -> None:
    count = 40
    declarations = " ".join(f'xmlns:p{index}="urn:{index}"' for index in range(count))
    document = parse_xml(f"<r {declarations}>{''.join(f'<p{index}:x/>' for index in range(count))}</r>")
    expr = " | ".join(f"//p{index}:x" for index in range(count))
    namespaces = {f"p{index}": f"urn:{index}" for index in range(count)}
    assert tags(document.xpath(expr, namespaces=namespaces)) == [f"p{index}:x" for index in range(count)]


def test_xml_name_test_matches_a_foreign_element_moved_into_a_namespaced_xml_tree() -> None:
    # a foreign element carries no declaration of its own, so it is in no namespace here, as a plain XML tree has it
    document = parse_xml('<r xmlns:q="urn:q"/>')
    svg = parse("<svg><rect/></svg>").select_one("svg")
    assert svg is not None
    one(document, "/r").append(svg)
    assert tags(document.xpath("//rect")) == ["rect"]
    assert tags(document.xpath("//svg")) == ["svg"]


def test_xml_name_test_resolves_a_prefix_declared_after_many_attributes() -> None:
    # the second declaration's atom is interned after hundreds of attribute names, past the table the first one sized
    attributes = " ".join(f'a{index}="1"' for index in range(600))
    document = parse_xml(f'<r xmlns:p0="urn:0" {attributes} xmlns:late="urn:l"><p0:x><late:y/></p0:x></r>')
    result = document.xpath("//p0:x/late:y", namespaces={"p0": "urn:0", "late": "urn:l"})
    assert tags(result) == ["late:y"]


def test_xml_name_test_follows_mutations_between_evaluations() -> None:
    document = parse_xml("<r><s><e1/></s></r>")
    root = one(document, "/r")
    scope = one(document, "/r/s")
    moved = one(document, "//e1")
    assert tags(document.xpath("//e1")) == ["e1"]
    scope.attrs["xmlns"] = "urn:a"
    assert document.xpath("//e1") == []
    assert tags(document.xpath("//a:e1", namespaces={"a": "urn:a"})) == ["e1"]
    root.append(moved)
    assert tags(document.xpath("//e1")) == ["e1"]
    assert document.xpath("//a:e1", namespaces={"a": "urn:a"}) == []


def test_prefix_bound_to_an_empty_uri_matches_nothing() -> None:
    assert parse_xml('<r xmlns="urn:a"><e/></r>').xpath("//p:e", namespaces={"p": ""}) == []


def test_xml_name_test_fills_a_node_appended_after_a_query() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><p:x/></r>')
    assert tags(document.xpath("//p:x", namespaces={"p": "urn:p"})) == ["p:x"]
    fresh = parse_xml('<q xmlns:p="urn:p"><p:y/></q>').select_one("q")
    assert fresh is not None
    one(document, "/r").append(fresh)
    assert tags(document.xpath("//p:y", namespaces={"p": "urn:p"})) == ["p:y"]
    assert tags(document.xpath("//p:x", namespaces={"p": "urn:p"})) == ["p:x"]


def test_xml_attribute_name_test_sees_an_attribute_a_callback_adds_mid_step() -> None:
    document = parse_xml('<r xmlns:p="urn:p"><x p:a="1"/><x/></r>')
    second = document.select("x")[1]
    added: list[bool] = []

    def add(_context: SimpleNamespace) -> bool:
        if not added:
            added.append(True)
            second.attrs["p:a"] = "2"
        return True

    result = document.xpath("//x/@p:a[1][add()]", namespaces={"p": "urn:p"}, extensions={(None, "add"): add})
    assert result == ["1", "2"]


def test_xml_name_test_reads_the_tree_after_a_callback_edits_it() -> None:
    document = parse_xml('<r xmlns:p="urn:a"><p:x><p:y/></p:x></r>')
    root = one(document, "/r")

    def drop(_context: SimpleNamespace) -> bool:
        del root.attrs["xmlns:p"]
        return True

    result = document.xpath("//p:x[drop()]/p:y", namespaces={"p": "urn:a"}, extensions={(None, "drop"): drop})
    assert result == []


def test_xml_attribute_name_test_reads_the_tree_after_a_callback_edits_it() -> None:
    document = parse_xml('<r xmlns:p="urn:a"><p:x p:a="1"/></r>')
    root = one(document, "/r")

    def drop(_context: SimpleNamespace) -> bool:
        del root.attrs["xmlns:p"]
        return True

    result = document.xpath("//p:x[drop()]/@p:a", namespaces={"p": "urn:a"}, extensions={(None, "drop"): drop})
    assert result == []


def test_xpath_one_accepts_namespaces(ns_doc: turbohtml.Node) -> None:
    node = ns_doc.xpath_one("//svg:circle", namespaces={"svg": _SVG})
    assert isinstance(node, Element)
    assert node.tag == "circle"


def test_xpath_iter_accepts_namespaces(ns_doc: turbohtml.Node) -> None:
    assert tags(list(ns_doc.xpath_iter("//m:mi", namespaces={"m": _MATHML}))) == ["mi"]


def test_namespaces_none_is_no_mapping(ns_doc: turbohtml.Node) -> None:
    assert tags(ns_doc.xpath("//p", namespaces=None)) == ["p"]


def test_namespaces_combines_with_variables(ns_doc: turbohtml.Node) -> None:
    result = ns_doc.xpath("//svg:circle[@r=$radius]", namespaces={"svg": _SVG}, radius="5")
    assert tags(result) == ["circle"]


def test_same_expression_rebinds_per_call(ns_doc: turbohtml.Node) -> None:
    # The compiled program is cached by string; the prefix resolves per call.
    assert tags(ns_doc.xpath("//p:circle", namespaces={"p": _SVG})) == ["circle"]
    assert ns_doc.xpath("//p:circle", namespaces={"p": _MATHML}) == []


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
            f"<ul>{''.join(f'<li>{number}</li>' for number in range(15))}</ul>",
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


# Element.xpath_path(): the positional XPath locating a node from the root. Unlike
# css_path() it never anchors on an id, indexing only among same-name siblings.
def _xpath_path(html: str, selector: str) -> str:
    element = turbohtml.parse(html).select_one(selector)
    assert isinstance(element, Element)
    return element.xpath_path()


def test_xpath_path_of_detached_element() -> None:
    assert Element("section").xpath_path() == "/section"


# xpath_path() round-trips: re-evaluating the path returns exactly the node it came from.
_PATH_DOC: Final[str] = (
    "<!doctype html><html><head><title>t</title></head><body>"
    "<header><h1>Title</h1></header>"
    '<main id="content">'
    "<article><p>one</p><p>two</p><p>three</p></article>"
    '<article class="aside"><p>alpha</p><ul><li>a</li><li>b</li><li>c</li></ul></article>'
    "</main>"
    '<footer><a href="/x">x</a><a href="/y">y</a></footer>'
    "</body></html>"
)

_PATH_DOCUMENT: Final[Document] = turbohtml.parse(_PATH_DOC)


def _every_element(document: Document) -> list[Element]:
    root = document.root
    assert root is not None
    return [root, *(node for node in root.descendants if isinstance(node, Element))]


@pytest.mark.parametrize("element", _every_element(_PATH_DOCUMENT), ids=lambda element: element.xpath_path())
def test_xpath_path_reselects_only_this_element(element: Element) -> None:
    assert _PATH_DOCUMENT.xpath(element.xpath_path()) == [element]


# The HTML tokenizer ends a tag name only at whitespace, "/" or ">", so a page can put XPath syntax into one, and
# XPath 1.0 has no escape for it: such a step is "*[n]", the position among all element siblings, as in lxml's getpath.
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("k|child::q", id="union-and-axis"),
        pytest.param("m|self::*[preceding::*[5=6]]", id="nested-predicates"),
        pytest.param("x[7=8]", id="predicate"),
        pytest.param("x|id('q')", id="function-call"),
        pytest.param('x"', id="double-quote"),
        pytest.param("x'", id="single-quote"),
        pytest.param("x*", id="wildcard"),
        pytest.param("x@y", id="attribute-axis"),
        pytest.param("x$y", id="variable"),
        pytest.param("x=y", id="comparison"),
        pytest.param("x::y", id="axis-separator"),
        pytest.param("c:d", id="undeclared-prefix"),
        pytest.param("xml:lang", id="xml-prefix"),
        pytest.param("x:", id="trailing-colon"),
    ],
)
def test_xpath_path_does_not_write_xpath_syntax_from_a_tag_name(name: str) -> None:
    document: Final = parse(f'<p id="a">one</p><{name}>two</{name}>')
    element: Final = document.select("body > :last-child")[0]
    path: Final = element.xpath_path()
    assert (element.tag, path, document.xpath(path)) == (name, "/html/body/*[2]", [element])


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("x-y", id="dash"),
        pytest.param("x.y", id="dot"),
        pytest.param("x_y", id="underscore"),
        pytest.param("x1", id="digit"),
        pytest.param("x\u00e9", id="non-ascii"),
        pytest.param("text", id="node-type-name"),
        pytest.param("div", id="operator-name"),
    ],
)
def test_xpath_path_keeps_a_tag_name_that_is_a_name_test(name: str) -> None:
    document: Final = parse(f"<p>one</p><{name}>two</{name}>")
    element: Final = document.select("body > :last-child")[0]
    path: Final = element.xpath_path()
    assert (path, document.xpath(path)) == (f"/html/body/{name}", [element])


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("1x", id="leading-digit"),
        pytest.param("-x", id="leading-dash"),
        pytest.param(".x", id="leading-dot"),
        pytest.param("m|n", id="union"),
    ],
)
def test_xpath_path_of_constructed_tag_name_outside_xpath_names(name: str) -> None:
    assert Element(name).xpath_path() == "/*[1]"


def test_xpath_path_of_qualified_xml_names_round_trips() -> None:
    document: Final = parse_xml('<p:r xmlns:p="urn:p"><p:b/>text<b/><p:b><c/><p:c/></p:b></p:r>')
    elements: Final = cast("list[Element]", document.xpath("//*"))
    paths: Final = [element.xpath_path() for element in elements]
    assert paths == ["/*[1]", "/*[1]/*[1]", "/*[1]/b", "/*[1]/*[3]", "/*[1]/*[3]/c", "/*[1]/*[3]/*[2]"]
    assert [document.xpath(path) for path in paths] == [[element] for element in elements]


def test_xpath_path_positions_count_every_element_sibling_in_any_order() -> None:
    document: Final = parse("<x|a></x|a>text<b></b><!--c--><x|b></x|b><i></i><x|c></x|c>")
    first, plain, second, _, third = document.select("body > *")
    # the last first, so its count walks to the start; then the others stop at a sibling already placed
    paths: Final = [element.xpath_path() for element in (third, first, second, third, plain)]
    assert paths == ["/html/body/*[5]", "/html/body/*[1]", "/html/body/*[3]", "/html/body/*[5]", "/html/body/b"]


def test_xpath_path_positions_follow_a_mutation() -> None:
    document: Final = parse("<x|a></x|a><x|b></x|b>")
    first, second = document.select("body > *")
    assert (first.xpath_path(), second.xpath_path()) == ("/html/body/*[1]", "/html/body/*[2]")
    first.decompose()
    assert (second.xpath_path(), document.xpath(second.xpath_path())) == ("/html/body/*[1]", [second])


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
def test_attribute_axis_survives_text_nodes(expr: str, expected: list[str] | str) -> None:
    doc = turbohtml.parse("<a id=x lang=en>hi<b>x</b></a>")
    assert doc.xpath(expr) == expected


def test_attribute_axis_of_a_text_node_is_empty() -> None:
    assert turbohtml.parse("<a>t</a>").xpath("//text()/@id") == []


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param("=".join(["1"] * 5000), id="equality-spine"),
        pytest.param("+".join(["1"] * 5000), id="additive-spine"),
    ],
)
def test_deep_operator_spine_raises_instead_of_overflowing(doc: turbohtml.Node, expr: str) -> None:
    # a long left-associative chain parses iteratively but the evaluator descends its
    # spine; the depth cap raises before the C stack overflows (issue #421)
    with pytest.raises(XPathDepthError, match="nested too deeply"):
        doc.xpath(expr)


def test_nested_xpath_parser_limit_raises_depth_error(doc: turbohtml.Node) -> None:
    with pytest.raises(XPathDepthError, match="nested too deeply"):
        doc.xpath(f"{'(' * 1100}1{')' * 1100}")


def test_compiled_xpath_parser_limit_raises_depth_error() -> None:
    with pytest.raises(XPathDepthError, match="nested too deeply"):
        XPath(f"{'(' * 1100}1{')' * 1100}")


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param(" | ".join(["//p"] * 5000), id="xpath-union"),
        pytest.param(css_to_xpath(",".join(["p"] * 1100)), id="css-selector-list"),
    ],
)
def test_long_union_chain_evaluates(doc: turbohtml.Node, expr: str) -> None:
    # the evaluator walks a union spine instead of recursing per operand, so a long
    # selector list does not hit the depth cap
    assert doc.xpath(expr) == doc.xpath("//p")


@pytest.mark.parametrize(
    "expr",
    [
        pytest.param(f"({'+'.join(['1'] * 5000)}) | //p", id="first-operand"),
        pytest.param(f"//p | ({'+'.join(['1'] * 5000)}) | //p", id="middle-operand"),
    ],
)
def test_deep_union_operand_still_raises(doc: turbohtml.Node, expr: str) -> None:
    with pytest.raises(XPathDepthError, match="nested too deeply"):
        doc.xpath(expr)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param(" or ".join(["1=0"] * 5000), False, id="or-all-false"),
        pytest.param(f"{' or '.join(['1=0'] * 5000)} or 1=1", True, id="or-last-true"),
        pytest.param(" and ".join(["1=1"] * 5000), True, id="and-all-true"),
        pytest.param(f"{' and '.join(['1=1'] * 5000)} and 1=0", False, id="and-last-false"),
        pytest.param(f"1=0 and ({'+'.join(['1'] * 5000)}) and 1=1", False, id="and-stops-before-deep"),
        pytest.param(f"1=1 or ({'+'.join(['1'] * 5000)}) or 1=0", True, id="or-stops-before-deep"),
    ],
)
def test_long_boolean_chain_evaluates(doc: turbohtml.Node, expr: str, *, expected: bool) -> None:
    assert doc.xpath(expr) is expected


def test_long_compound_selector_evaluates() -> None:
    doc = turbohtml.parse('<p class="a">x</p><p>y</p>')
    selector = f"p{'.a' * 1100}"
    assert doc.xpath(css_to_xpath(selector)) == doc.select(selector)


def test_deep_boolean_chain_operand_still_raises(doc: turbohtml.Node) -> None:
    with pytest.raises(XPathDepthError, match="nested too deeply"):
        doc.xpath(f"1=1 and ({'+'.join(['1'] * 5000)}) and 1=1")


# A path tested for emptiness only needs one node of its last step. The walk has to stop at that node, or the work grows
# quadratically with the length of a sibling list.
@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("//p[following-sibling::node()]", id="predicate"),
        pytest.param("//p[not(following-sibling::p)]", id="not"),
        pytest.param("//p[boolean(following-sibling::*)]", id="boolean"),
        pytest.param("//p[@id or following::p]", id="or"),
        pytest.param("//p[following::p and ../p]", id="and"),
        pytest.param("//p[preceding-sibling::p]", id="preceding-sibling"),
        pytest.param("//p[not(preceding::p)]", id="preceding"),
        pytest.param(css_to_xpath("p:last-child", prefix="//"), id="css-last-child"),
    ],
)
def test_a_path_tested_for_emptiness_grows_linearly(expression: str, work_steps: WorkSteps) -> None:
    # Compares the steps for expression with the steps for //p[self::p] on one list of 16,000 p elements. Both visit
    # each p once, so the ratio is near 1 when the walk stops at the first node and grows with the list length
    # otherwise.
    document: Final = parse(f"<section>{'<p>y' * 16_000}</section>")
    work_steps.assert_within(partial(document.xpath, expression), partial(document.xpath, "//p[self::p]"), factor=20)


def _siblings(count: int) -> Document:
    children: Final = "".join(f'<c k="{index}"/>' for index in range(count))
    return parse_xml(f"<r>{children}</r>")


def _chain(depth: int) -> Document:
    opened: Final = "".join(f'<d k="{index}"><e/>' for index in range(1, depth + 1))
    return parse_xml(f"<r>{opened}{'</d>' * depth}</r>")


def _groups(count: int) -> Document:
    children: Final = "<c/>" * count
    return parse_xml(f"<r>{f'<g><h>{children}</h>{children}</g>' * count}</r>")


def _clustered(count: int) -> Document:
    # The creation-sequence table places a node at its sequence times 0x61C88647 scaled to the table, so members whose
    # products fall in the lowest eighth of the range crowd one eighth of it and every probe walks that crowd.
    members: Final = {index for index in range(count * 8) if index * 0x61C88647 % 2**32 < 2**29}
    children: Final = "".join("<b/>" if index in members else "<x/>" for index in range(count * 8))
    return parse_xml(f"<r>{children}<c/><c/></r>")


def _element_ancestors(node: Element) -> list[Element]:
    return [ancestor for ancestor in node.ancestors if isinstance(ancestor, Element)]


# The sets below pick nodes whose order the tree links settle only after a walk longer than the steps a sort may spend
# per node (a wide sibling list, a deep chain), next to pairs that adjacent links settle at once. Index 0 is the root
# element; in a chain, d at depth j has index 2j - 1 and its e child 2j; in three groups, 6 and 14 are children of the
# first and second g.
@pytest.mark.parametrize(
    ("build", "picks"),
    [
        pytest.param(partial(_siblings, 2400), [501, 504], id="siblings-near"),
        pytest.param(partial(_siblings, 2400), [504, 501], id="siblings-near-reversed"),
        pytest.param(partial(_siblings, 2400), [4, 1500], id="siblings-from-the-first"),
        pytest.param(partial(_siblings, 2400), [2397, 900], id="siblings-from-the-last"),
        pytest.param(partial(_siblings, 2400), [1001, 1701], id="siblings-far"),
        pytest.param(partial(_siblings, 2400), [1701, 1001], id="siblings-far-reversed"),
        pytest.param(partial(_chain, 600), [19, 20], id="parent-then-child"),
        pytest.param(partial(_chain, 600), [20, 19], id="child-then-parent"),
        pytest.param(partial(_chain, 600), [20, 21], id="adjacent-siblings"),
        pytest.param(partial(_chain, 600), [21, 20], id="adjacent-siblings-reversed"),
        pytest.param(partial(_chain, 600), [19, 99], id="ancestor-then-descendant"),
        pytest.param(partial(_chain, 600), [99, 19], id="descendant-then-ancestor"),
        pytest.param(partial(_chain, 600), [20, 22], id="uncle-then-nephew"),
        pytest.param(partial(_chain, 600), [22, 20], id="nephew-then-uncle"),
        pytest.param(partial(_groups, 3), [6, 14], id="first-cousins"),
        pytest.param(partial(_groups, 3), [14, 6], id="first-cousins-reversed"),
        pytest.param(partial(_chain, 600), [20, 100], id="distant-cousins"),
        pytest.param(partial(_chain, 600), [100, 20], id="distant-cousins-reversed"),
        pytest.param(partial(_chain, 600), [200, 999], id="deep-after-shallow"),
        pytest.param(partial(_chain, 600), [999, 200], id="deep-before-shallow"),
    ],
)
def test_variable_node_set_in_document_order(build: Callable[[], Document], picks: list[int]) -> None:
    document: Final = build()
    elements: Final = element_list(document.xpath("//*"))
    nodes: Final = [elements[index] for index in picks]
    assert XPath("$v")(document, v=nodes) == _in_document_order(document, nodes)


def _in_document_order(document: turbohtml.Node, nodes: Iterable[turbohtml.Node]) -> list[turbohtml.Node]:
    position: Final = {node: index for index, node in enumerate(document.descendants)}
    return sorted(set(nodes), key=position.__getitem__)


@pytest.mark.parametrize(
    ("build", "expression", "selects"),
    [
        pytest.param(partial(_siblings, 2400), "$v | $w", lambda node: [node], id="siblings"),
        pytest.param(partial(_chain, 400), "$v | $w", lambda node: [node], id="chain"),
        pytest.param(partial(_groups, 40), "$v | $w", lambda node: [node], id="branches"),
        pytest.param(partial(_groups, 40), "$v/.. | $w", lambda node: [node.parent], id="branch-parents"),
        pytest.param(
            partial(_groups, 40),
            "$v/ancestor-or-self::* | $w",
            lambda node: [node, *_element_ancestors(node)],
            id="branch-ancestors",
        ),
    ],
)
def test_shuffled_node_sets_union_in_document_order(
    build: Callable[[], Document], expression: str, selects: Callable[[Element], list[Element]]
) -> None:
    document: Final = build()
    elements: Final = element_list(document.xpath("//*"))
    first: Final = _shuffled(elements[1:], 300, 1)
    second: Final = _shuffled(elements[1:], 200, 2)
    expected: Final = _in_document_order(document, [*(found for node in first for found in selects(node)), *second])
    assert XPath(expression)(document, v=first + first[:20], w=second) == expected


def _shuffled(nodes: Sequence[Element], count: int, offset: int) -> list[Element]:
    picked: Final = list(nodes[offset :: max(1, len(nodes) // count)])
    return [picked[index * 7919 % len(picked)] for index in range(len(picked))]


@pytest.mark.parametrize(
    ("build", "first", "second", "expected"),
    [
        pytest.param(partial(_siblings, 2400), [1001], [1701], [1001, 1701], id="siblings"),
        pytest.param(partial(_siblings, 2400), [1701], [1001], [1001, 1701], id="siblings-reversed"),
        pytest.param(partial(_chain, 600), [999, 1001], [600, 1001], [600, 999, 1001], id="chain"),
    ],
)
def test_union_merge_past_the_walk_budget(
    build: Callable[[], Document], first: list[int], second: list[int], expected: list[int]
) -> None:
    document: Final = build()
    elements: Final = element_list(document.xpath("//*"))
    result: Final = XPath("$v | $w")(document, v=[elements[i] for i in first], w=[elements[i] for i in second])
    assert result == [elements[index] for index in expected]


@pytest.mark.parametrize("expression", ["/ | (//e)[3]", "(//e)[3] | /"])
def test_union_with_the_document_node(expression: str) -> None:
    document: Final = _chain(5)
    assert document.xpath(expression) == [document, node_list(document.xpath("//e"))[2]]


# Detached subtrees of one tree have no common ancestor, so the order between them is undefined; only membership holds.
def test_node_set_across_detached_subtrees_keeps_every_node() -> None:
    document: Final = _groups(30)
    groups: Final = element_list(document.xpath("/r/g"))
    for group in groups[:2]:
        group.extract()
    second_children: Final = [child for child in groups[1].children if isinstance(child, Element)]
    leaves: Final = [*element_list(document.xpath("//c")), *element_list(XPath("//c")(groups[0])), *second_children]
    nodes: Final = _shuffled(leaves, 400, 3)
    assert Counter(node_list(XPath("$v")(document, v=nodes))) == Counter(nodes)
    expected: Final = {ancestor for node in nodes for ancestor in _element_ancestors(node) if ancestor.tag == "g"}
    assert set(node_list(XPath("$v/ancestor::g")(document, v=nodes))) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("//e/ancestor::d", "//d", id="ancestor"),
        pytest.param("//e/ancestor-or-self::*", "//*", id="ancestor-or-self"),
        pytest.param("//@k/ancestor::d", "//d", id="attribute-contexts"),
        pytest.param("(//d[@k = 3] | //e)/ancestor::r", "/r", id="one-top"),
        pytest.param("//e/ancestor::d[1]", "//d", id="predicate"),
    ],
)
def test_ancestor_step_from_nested_contexts(expression: str, expected: str) -> None:
    document: Final = _chain(300)
    assert document.xpath(expression) == document.xpath(expected)


@pytest.mark.parametrize(
    ("build", "expression", "expected"),
    [
        pytest.param(
            partial(_chain, 50),
            "(//e)[last()]/ancestor::*",
            lambda elements: [elements[0], *elements[1:-1:2]],
            id="ancestor",
        ),
        pytest.param(
            partial(_chain, 50),
            "(//e)[last()]/ancestor-or-self::*",
            lambda elements: [elements[0], *elements[1:-1:2], elements[-1]],
            id="ancestor-or-self",
        ),
        pytest.param(partial(_chain, 50), "(//e)[last()]/preceding::*", itemgetter(slice(2, -1, 2)), id="preceding"),
        pytest.param(
            partial(_siblings, 50),
            "/r/c[last()]/preceding-sibling::c",
            itemgetter(slice(1, -1)),
            id="preceding-sibling",
        ),
    ],
)
def test_reverse_axis_from_one_node_in_document_order(
    build: Callable[[], Document], expression: str, expected: Callable[[list[Element]], list[Element]]
) -> None:
    document: Final = build()
    assert document.xpath(expression) == expected(element_list(document.xpath("//*")))


def test_union_after_a_callback_reorders_the_tree() -> None:
    document: Final = _siblings(600)
    root: Final = node_list(document.xpath("/r"))[0]
    assert isinstance(root, Element)
    children: Final = element_list(document.xpath("/r/c"))
    first: Final = _shuffled(children, 300, 0)
    second: Final = _shuffled(children, 200, 1)
    calls: Final[list[None]] = []

    def reverse(_context: SimpleNamespace) -> bool:
        if not calls:
            for child in reversed(root.children):
                child.extract()
                root.append(child)
        calls.append(None)
        return True

    before: Final = _in_document_order(document, second)
    result: Final = document.xpath(
        "$v[reverse() and false()] | $w", extensions={(None, "reverse"): reverse}, v=first, w=second
    )
    assert result == before[::-1]


# A sort or merge walks at most a budget proportional to its set before it ranks the set once, and a step from nested
# contexts climbs each ancestor once, so the counted steps grow linearly with the list length or the depth.
@pytest.mark.parametrize(
    ("build", "expression"),
    [
        pytest.param(_siblings, "//c[last()]/preceding-sibling::c | //c[1]", id="reverse-axis"),
        pytest.param(
            _siblings,
            "//c[last()]/preceding-sibling::c | //c[last() - 1]/preceding-sibling::c",
            id="sibling-merge",
        ),
        pytest.param(_chain, "(//d)[position() mod 2 = 0] | //e", id="deep-merge"),
        pytest.param(_chain, "//d[last()]/ancestor::d", id="deep-ancestors"),
        pytest.param(_clustered, "//c/preceding-sibling::b", id="clustered-members"),
    ],
)
def test_document_order_grows_linearly(
    build: Callable[[int], Document], expression: str, work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda size: partial(build(size).xpath, expression), (1000, 4000))


@pytest.mark.parametrize(
    ("build", "expression"),
    [
        pytest.param(_siblings, "//c/following-sibling::c[@k]", id="following-sibling"),
        pytest.param(_siblings, "//c/preceding-sibling::c[@k]", id="preceding-sibling"),
        pytest.param(_siblings, "//c/following::c[@k]", id="following"),
        pytest.param(_siblings, "//c/preceding::c[@k]", id="preceding"),
        pytest.param(_chain, "//e/ancestor::d[@k]", id="ancestor"),
        pytest.param(_chain, "//d//d[@k]", id="descendant"),
        pytest.param(_chain, "//e/ancestor::d[not(@z)][@k = '1' or @k]", id="ancestor-boolean-predicates"),
    ],
)
def test_predicate_from_many_contexts_grows_linearly(
    build: Callable[[int], Document], expression: str, work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda size: partial(build(size).xpath, expression), (1000, 4000))


def _comb(depth: int) -> Document:
    # each level opens with a leaf, so every context's preceding axis meets one leaf per level above it
    opened: Final = "".join(f'<d k="{index % 3}"><f/>' for index in range(1, depth + 1))
    return parse_xml(f"<r>{opened}{'</d>' * depth}</r>")


# Each context counts positions along its own stretch of the axis; contexts whose stretches overlap share one walk and
# the predicate's offsets are read off it, so the counted steps grow with the axis length instead of its square.
@pytest.mark.parametrize(
    ("build", "expression"),
    [
        pytest.param(_siblings, "//c/following-sibling::c[position() > 1]", id="following-sibling"),
        pytest.param(_siblings, "//c/following-sibling::c[last()]", id="following-sibling-last"),
        pytest.param(_siblings, "//c/preceding-sibling::c[position() < last()]", id="preceding-sibling"),
        pytest.param(_siblings, "//c/following::c[2]", id="following"),
        pytest.param(_siblings, "//c/preceding::c[position() > 2]", id="preceding"),
        pytest.param(_siblings, "//c/following-sibling::c[@k > 2][position() > 1]", id="filter-then-position"),
        pytest.param(_chain, "//d/ancestor::d[position() > 1]", id="ancestor"),
        pytest.param(_chain, "//e/ancestor-or-self::*[last() - 1]", id="ancestor-or-self"),
        pytest.param(_chain, "//d/descendant::d[position() > 1]", id="descendant"),
        pytest.param(_chain, "//d/descendant-or-self::e[last()]", id="descendant-or-self"),
        pytest.param(_comb, "//d/preceding::f[position() > 1]", id="preceding-leaves"),
        pytest.param(_comb, "//f/preceding::d[1]", id="preceding-levels"),
    ],
)
def test_positional_predicate_from_many_contexts_grows_linearly(
    build: Callable[[int], Document], expression: str, work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(lambda size: partial(build(size).xpath, expression), (1000, 4000))


def test_clustered_members_come_back_in_document_order() -> None:
    document: Final = _clustered(200)
    assert document.xpath("//c/preceding-sibling::b") == document.xpath("//b")


@pytest.mark.parametrize("context", ["//*", "//@*", "//text()", "//*/namespace::*"])
@pytest.mark.parametrize(
    "path",
    [
        "self::b",
        "child::b",
        "descendant::b",
        "descendant-or-self::b",
        "descendant-or-self::node()",
        "parent::b",
        "ancestor::b",
        "ancestor-or-self::b",
        "ancestor-or-self::node()",
        "following-sibling::b",
        "preceding-sibling::b",
        "following::b",
        "preceding::b",
        "attribute::x",
        "namespace::*",
        "node()",
        "b/i",
        "*//i",
        "*[2]",
        "following-sibling::*[@x][last()]",
        "preceding::*[i][1]/@x",
        "(b | i)/..",
        "$nodes/b",
        "/r/i",
    ],
)
def test_a_path_tested_for_emptiness_agrees_with_its_count(context: str, path: str) -> None:
    document: Final = parse_xml(
        '<r><b x="1"><i/>t<b><i x="2"/><b/></b></b><i><b/><!--c--></i><a><b x="3"/><i/><b><i/></b></a><i x="4"/></r>'
    )
    nodes: Final = document.select("i")
    checks: Final = (
        (path, f"count({path}) > 0"),
        (f"not({path})", f"count({path}) = 0"),
        (f"boolean({path})", f"count({path}) > 0"),
        (f"{path} and @x", f"count({path}) > 0 and @x"),
        (f"@x or {path}", f"@x or count({path}) > 0"),
    )
    tested: Final = [document.xpath(f"{context}[{test}]", nodes=nodes) for test, _ in checks]
    counted: Final = [document.xpath(f"{context}[{count}]", nodes=nodes) for _, count in checks]
    assert tested == counted
    assert document.xpath(f"boolean({path})", nodes=nodes) is document.xpath(f"count({path}) > 0", nodes=nodes)


def test_long_union_in_predicate_needs_no_c_stack() -> None:
    # the //X collapse scans each predicate for position()/last(); a union spine there is as deep as it is long
    code = (
        "import threading, turbohtml\n"
        "doc = turbohtml.parse('<p><a/></p><p></p>')\n"
        "expr = '//p[' + ' | '.join(['a'] * 10000) + ']'\n"
        "result = []\n"
        "threading.stack_size(128 * 1024)\n"
        "worker = threading.Thread(target=lambda: result.append(len(doc.xpath(expr))))\n"
        "worker.start()\n"
        "worker.join()\n"
        "print(result[0])\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # argv is a fixed list, so no shell is involved
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert (result.returncode, result.stdout) == (0, "1\n"), result.stderr


def test_moderately_nested_expression_still_evaluates(doc: turbohtml.Node) -> None:
    assert doc.xpath(f"{'(' * 50}1 + 1{')' * 50}") == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("opening", "innermost", "closing"),
    [
        pytest.param("descendant-or-self::node()[", "true()", "]", id="step-predicate"),
        pytest.param("(self::node())[", "true()", "]", id="filter-predicate"),
        pytest.param("(", "self::node()", ")/descendant-or-self::node()", id="path-base"),
        pytest.param("count(ancestor-or-self::node()[", "true()", "])", id="function-over-predicate"),
        pytest.param("boolean(", "true()", ")", id="function-argument"),
        pytest.param("(2*", "3", ")", id="operator"),
        pytest.param("-(", "1", ")", id="unary-minus"),
    ],
)
def test_nesting_the_cap_admits_fits_a_small_thread_stack(opening: str, innermost: str, closing: str) -> None:
    # a level of each shape puts different frames on the C stack, a predicate in a step three times what an operator
    # does; whatever depth the cap admits has to fit a thread as small as musl's default, and past it the expression
    # raises. A subprocess turns a regression into a clean failure instead of a killed worker.
    code = (
        "import sys, threading, turbohtml\n"
        "doc = turbohtml.parse('<p>')\n"
        "opening, innermost, closing = sys.argv[1:]\n"
        "evaluated = []\n"
        "def run():\n"
        "    for depth in (*range(1, 64 + 7), *(64 * scale for scale in (4, 9, 15))):\n"
        "        try:\n"
        "            doc.xpath(opening * depth + innermost + closing * depth)\n"
        "        except ValueError as error:\n"
        "            assert 'nested too deeply' in str(error), error\n"
        "        else:\n"
        "            evaluated.append(depth)\n"
        "threading.stack_size(128 * 1024)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\n"
        "worker.join()\n"
        "print(8 <= evaluated[-1] < 64 and evaluated == list(range(1, evaluated[-1] + 1)))\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, no shell
        [sys.executable, "-c", code, opening, innermost, closing],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (result.returncode, result.stdout) == (0, "True\n"), result.stderr


def _thread_stack_accepted(kib: int) -> bool:
    previous = threading.stack_size()
    accepted = False
    with suppress(ValueError):  # the platform's minimum thread stack is above this size
        threading.stack_size(kib * 1024)
        accepted = True
    threading.stack_size(previous)
    return accepted


@pytest.mark.parametrize(
    ("opening", "innermost", "closing"),
    [
        pytest.param("(2*", "3", ")", id="operator"),
        pytest.param("boolean(", "true()", ")", id="function-argument"),
        pytest.param("descendant-or-self::node()[", "true()", "]", id="step-predicate"),
    ],
)
@pytest.mark.skipif(not _thread_stack_accepted(52), reason="the platform's minimum thread stack is above 52 KiB")
def test_nesting_on_the_smallest_thread_stack_raises_cleanly(opening: str, innermost: str, closing: str) -> None:
    # 52 KiB is too small for the cap; past what the stack holds the expression raises like one past the cap. Linux on
    # arm64 refuses a thread that small, so there the test is skipped
    code = (
        "import sys, threading, turbohtml\n"
        "doc = turbohtml.parse('<p>')\n"
        "opening, innermost, closing = sys.argv[1:]\n"
        "evaluated = []\n"
        "def run():\n"
        "    for depth in range(1, 64 + 7):\n"
        "        try:\n"
        "            doc.xpath(opening * depth + innermost + closing * depth)\n"
        "        except ValueError as error:\n"
        "            assert 'nested too deeply' in str(error), error\n"
        "        else:\n"
        "            evaluated.append(depth)\n"
        "threading.stack_size(52 * 1024)\n"
        "worker = threading.Thread(target=run)\n"
        "worker.start()\n"
        "worker.join()\n"
        "print(evaluated == list(range(1, evaluated[-1] + 1)))\n"
    )
    result = subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, no shell
        [sys.executable, "-c", code, opening, innermost, closing],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert (result.returncode, result.stdout) == (0, "True\n"), result.stderr


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
        f"<root>{''.join(f'<left>{value}</left>' for value in left)}"
        f"{''.join(f'<right>{value}</right>' for value in right)}</root>"
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
        f"""<root>{"".join(f'<item id="{index}">same</item>' for index in range(size))}</root>"""
    )
    assert document.xpath(f"set:{operation}(//item, //item[position() mod 2 = 0])/@id") == [
        str(index) for index in range(size) if (index % 2 == 1) == (operation == "intersection")
    ]


@pytest.mark.parametrize(
    "operation", [pytest.param("intersection", id="intersection"), pytest.param("difference", id="difference")]
)
def test_set_filter_distinguishes_attributes(operation: str) -> None:
    document: Final = parse_xml(f"""<root>{'<item a="same" b="same"/>' * 64}</root>""")
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
    document: Final = parse_xml(f"<root>{'<item/>' * 64}</root>")
    assert document.xpath("set:has-same-node(//item, //item[position() > 32])") is True


@pytest.mark.parametrize(
    ("expression", "selector"),
    [
        pytest.param("unordered(//li) | //ul", "ul, li", id="left-extension"),
        pytest.param("//ul | unordered(//li)", "ul, li", id="right-extension"),
        pytest.param("unordered(//li) | unordered(//li)", "li", id="both-extensions"),
    ],
)
def test_union_orders_extension_results(expression: str, selector: str) -> None:
    document: Final[Document] = parse(f"<ul>{''.join(f'<li>{index}</li>' for index in range(100))}</ul>")
    assert document.xpath(expression, extensions={(None, "unordered"): _unordered}) == document.select(selector)


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
        f"<root>{''.join(f'<left>{value}</left>' for value in left)}"
        f"{''.join(f'<right>{value}</right>' for value in right)}</root>"
    )
    assert document.xpath(f"//left {operation} //right") is expected[index]


def test_comparison_uses_attribute_values() -> None:
    document: Final = parse_xml(f"""<root>{'<item left="same" right="same"/>' * 32}</root>""")
    assert document.xpath("//item/@left = //item/@right") is True


def test_comparison_uses_descendant_text() -> None:
    document: Final = parse_xml(f"<root>{'<left>a<b>b</b>c</left><right>abc</right>' * 32}</root>")
    assert document.xpath("//left = //right") is True


@pytest.mark.parametrize(
    "selection", [pytest.param("//item", id="elements"), pytest.param("//item/@value", id="attributes")]
)
def test_comparison_same_nodes(selection: str) -> None:
    document: Final = parse_xml(f"""<root>{'<item value="same">same</item>' * 32}</root>""")
    assert document.xpath(f"{selection} = {selection}") is True


@pytest.fixture
def variable_doc() -> turbohtml.Node:
    variable_html: str = (
        "<html><body>"
        "<table><tr id='r1'><td>a</td><td>b</td></tr><tr id='r2'><td>c</td></tr></table>"
        "<p id='a'>one</p><p id='b'>two</p><p id='c'>three</p>"
        "</body></html>"
    )
    return turbohtml.parse(variable_html)


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


def variable_tags(result: XPathResult) -> list[str]:
    return [node.tag if isinstance(node, Element) else str(node) for node in node_list(result)]


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
    variable_doc: turbohtml.Node,
    expr: str,
    kwargs: dict[str, str | int | float | bool],
    *,
    expected: str | float | bool,
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


def ids(result: XPathResult) -> list[str | None]:
    return [node.attr("id") for node in node_list(result) if isinstance(node, Element)]


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
    return query(turbohtml.parse("<html><body><span id='x'>elsewhere</span></body></html>"), "//span")


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
    "evaluate",
    [
        pytest.param(lambda node, items: node.xpath("$items/text()", items=items), id="node-xpath"),
        pytest.param(lambda node, items: XPath("$items/text()")(node, items=items), id="compiled"),
    ],
)
def test_node_set_variable_iterable_moving_the_context_node_to_another_tree(
    evaluate: Callable[[Element, Iterator[Element]], object],
) -> None:
    fragment: Final = parse_fragment("<p><a>one</a></p>")
    host: Final = Element("div")

    def anchors() -> Iterator[Element]:
        yield from fragment.select("a")
        host.append(fragment)  # fragment is the only wrapper of its tree, so the move drops the tree's last owner

    with pytest.raises(ValueError, match="context node moved to a different tree"):
        evaluate(fragment, anchors())
    assert fragment.parent == host


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


_LXML_DOCS: Final[MappingProxyType[str, str]] = MappingProxyType({
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
})

_LXML_EXPRS: Final[tuple[str, ...]] = (
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
)


_LXML_SCALAR_PREFIXES: Final[tuple[str, ...]] = ("boolean(", "count(", "name(", "string(")
_LXML_NODESET_EXPRS: Final[list[str]] = [expr for expr in _LXML_EXPRS if not expr.startswith(_LXML_SCALAR_PREFIXES)]
_LXML_SCALAR_EXPRS: Final[list[str]] = [expr for expr in _LXML_EXPRS if expr.startswith(_LXML_SCALAR_PREFIXES)]


@pytest.mark.parametrize("expr", _LXML_NODESET_EXPRS, ids=lambda expr: expr)
@pytest.mark.parametrize("doc_name", list(_LXML_DOCS), ids=list(_LXML_DOCS))
@pytest.mark.oracle
def test_matches_lxml(doc_name: str, expr: str) -> None:
    lxml_html: Final = pytest.importorskip("lxml.html")
    html: Final = _LXML_DOCS[doc_name]
    ours: Final = turbohtml.parse(html).xpath(expr)
    assert isinstance(ours, list)
    theirs: Final = lxml_html.document_fromstring(html).xpath(expr)
    assert _normalize_lxml(ours) == _normalize_lxml(theirs)


@pytest.mark.oracle
def _normalize_lxml(result: Iterable[turbohtml.Node | str]) -> list[str]:
    out: Final[list[str]] = []
    for item in result:
        if isinstance(item, str):
            out.append(item)
        else:
            tag: Final = getattr(item, "tag", None)
            out.append(tag if isinstance(tag, str) else f"<{type(item).__name__}>")
    return out


@pytest.mark.parametrize("expr", _LXML_SCALAR_EXPRS, ids=lambda expr: expr)
@pytest.mark.parametrize("doc_name", list(_LXML_DOCS), ids=list(_LXML_DOCS))
@pytest.mark.oracle
def test_matches_lxml_scalar(doc_name: str, expr: str) -> None:
    lxml_html: Final = pytest.importorskip("lxml.html")
    html: Final = _LXML_DOCS[doc_name]
    assert turbohtml.parse(html).xpath(expr) == lxml_html.document_fromstring(html).xpath(expr)


class _Evaluate(Protocol):
    def __call__(self, source: Element, expression: str, extensions: XPathExtensions, /) -> XPathResult: ...


class _UnionEvaluate(Protocol):
    def __call__(
        self,
        source: Element,
        expression: str,
        extensions: XPathExtensions,
        *,
        smart: bool,
    ) -> XPathResult: ...


def _union_compiled(
    source: Element,
    expression: str,
    extensions: XPathExtensions,
    *,
    smart: bool,
) -> XPathResult:
    return XPath(expression, extensions=extensions, smart_strings=smart)(source)


def _union_method(
    source: Element,
    expression: str,
    extensions: XPathExtensions,
    *,
    smart: bool,
) -> XPathResult:
    return source.xpath(expression, extensions=extensions, smart_strings=smart)


@pytest.mark.parametrize(
    "evaluate",
    [pytest.param(_union_method, id="method"), pytest.param(_union_compiled, id="compiled")],
)
@pytest.mark.parametrize("smart", [pytest.param(False, id="plain"), pytest.param(True, id="smart")])
@pytest.mark.parametrize("change", ["replace", "remove", "readd", "unrelated"])
def test_xpath_callback_attribute_union(*, evaluate: _UnionEvaluate, smart: bool, change: str) -> None:
    source: Final = Element("section", {"id": "old", "other": "value"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        if change in {"remove", "readd"}:
            del source.attrs["id"]
        if change in {"replace", "readd"}:
            source.attrs["id"] = "new"
        if change == "unrelated":
            source.attrs["other"] = "changed"
        return []

    extensions: Final[XPathExtensions] = {(None, "mutate"): mutate}
    assert evaluate(source, "@id | mutate() | @id", extensions, smart=smart) == ["old"]


@pytest.mark.parametrize("change", ["replace", "remove", "readd", "unrelated"])
def test_xpath_callback_attribute_union_smart_owner(change: str) -> None:
    source: Final = Element("section", {"id": "old", "other": "value"})

    def mutate(_context: SimpleNamespace) -> list[Element]:
        if change in {"remove", "readd"}:
            del source.attrs["id"]
        if change in {"replace", "readd"}:
            source.attrs["id"] = "new"
        if change == "unrelated":
            source.attrs["other"] = "changed"
        return []

    result = source.xpath("@id | mutate() | @id", extensions={(None, "mutate"): mutate}, smart_strings=True)
    assert isinstance(result, list)
    assert result == ["old"]
    assert isinstance(result[0], XPathString)
    assert (result[0].getparent(), result[0].attrname) == (source, "id")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("name((@id | mutate())[1])", "id", id="name-first"),
        pytest.param("str:concat(@id | mutate())", "old", id="concat"),
        pytest.param("set:distinct(@id | mutate())", ["old"], id="distinct"),
        pytest.param("set:intersection(@id | mutate(), @id)", ["old"], id="intersection"),
        pytest.param("set:difference(@id | mutate(), @id)", [], id="difference"),
        pytest.param("@id = string(mutate())", False, id="comparison"),
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


@pytest.mark.parametrize("smart", [pytest.param(False, id="plain"), pytest.param(True, id="smart")])
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


def test_xpath_callback_adopted_pending_owner_smart_string_owner() -> None:
    source: Final = Element("section", {"id": "old"}, children=[Element("b")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    result: Final = source.xpath(". | @id | adopt()", extensions={(None, "adopt"): adopt}, smart_strings=True)
    assert isinstance(result, list)
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
    assert result == [str(index) for index in range(size)] * (operation == "set:intersection")


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


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("text() | adopt() | empty()", id="flat"),
        pytest.param(f"text() | empty() | {'self::node()[' * 8}adopt(){']' * 8}", id="nested"),
        pytest.param(f"text() | {'self::node()[' * 8}adopt(){']' * 8} | empty()", id="after-nested"),
    ],
)
def test_xpath_callback_preserves_text_parent(expression: str) -> None:
    source: Final = Element("section", children=[Text("body")])
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> list[Element]:
        target.append(source)
        return []

    def empty(_context: SimpleNamespace) -> list[Element]:
        return []

    result: Final = source.xpath(
        expression, extensions={(None, "adopt"): adopt, (None, "empty"): empty}, smart_strings=True
    )
    assert isinstance(result, list)
    assert isinstance(result[0], XPathString)
    result[0].getparent().attrs["live"] = "yes"
    assert (str(result[0]), target.select("section[live=yes]")) == ("body", [source])


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        pytest.param("set:leading(@*, .)", [], id="leading"),
        pytest.param("@*[position() > 1 and keep()]", ["two", "three"], id="predicate-callback"),
        pytest.param("@*/../@*", ["one", "two", "three"], id="parent-round-trip"),
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
    for index in [*range(1, 16), *range(17, 32), *range(33, 48)]:
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
        pytest.param("@id[self::node()[move()]]", ["old"], 1, id="attribute-self"),
        pytest.param("@id[parent::section[move()]]", ["old"], 1, id="attribute-parent"),
        pytest.param("@id[child::node()[move()]]", [], 0, id="empty-first-step"),
    ],
)
def test_xpath_callback_initial_attribute_context(expression: str, expected: list[str], moved: int) -> None:
    source: Final = Element("section", {"id": "old"})
    target: Final = Element("main")

    def move(_context: SimpleNamespace) -> bool:
        target.append(source)
        del source.attrs["id"]
        return True

    result: Final = source.xpath(expression, extensions={(None, "move"): move})
    assert (result, tuple(target.children)) == (expected, (source,)[:moved])


_XPATH_EVALUATORS: Final[list[pytest.ParameterSet]] = [
    pytest.param(lambda node, expression, extensions: node.xpath(expression, extensions=extensions), id="method"),
    pytest.param(lambda node, expression, extensions: XPath(expression, extensions=extensions)(node), id="compiled"),
]


@pytest.mark.parametrize("evaluate", _XPATH_EVALUATORS)
def test_xpath_extension_keeps_source_arena(evaluate: _Evaluate) -> None:
    source: Final = Element("section")
    target: Final = Element("main")

    def adopt(_context: SimpleNamespace) -> float:
        target.append(source)
        return 41.0

    extensions: Final[XPathExtensions] = {(None, "adopt"): adopt}
    result: Final = evaluate(source, "adopt() + count(.)", extensions)
    assert (result, target.children) == (42.0, (source,))


def test_xpath_extension_keeps_cached_program() -> None:
    source: Final = Element("section")

    def evict(_context: SimpleNamespace) -> float:
        for index in range(80):
            source.xpath(str(index))
        return 41.0

    assert source.xpath("evict() + 1", extensions={(None, "evict"): evict}) == pytest.approx(42.0)


@pytest.mark.parametrize("evaluate", _XPATH_EVALUATORS)
def test_xpath_extension_keeps_removed_callable(evaluate: _Evaluate) -> None:
    source: Final = Element("section")
    extensions: Final[XPathExtensions] = {}

    def remove(_context: SimpleNamespace) -> bool:
        extensions.clear()
        return function() is not None

    extensions[None, "remove"] = partial(remove)
    function: Final = ref(extensions[None, "remove"])
    result: Final = evaluate(source, "remove()", extensions)
    gc.collect()
    assert (result, function()) == (True, None)


@pytest.mark.parametrize("evaluate", _XPATH_EVALUATORS)
def test_xpath_extension_validates_yielded_owner(evaluate: _Evaluate) -> None:
    source: Final = Element("section", children=[Element("b")])
    child: Final = source.children[0]
    target: Final = Element("main")

    def move_after_yield(_context: SimpleNamespace) -> Iterator[Element]:
        assert isinstance(child, Element)
        yield child
        target.append(child)

    extensions: Final[XPathExtensions] = {(None, "move"): move_after_yield}
    with pytest.raises(ValueError, match="different document"):
        evaluate(source, "move()", extensions)
    assert target.children == (child,)


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("offset", [0, 1, 2, 4, 32])
def test_xpath_extension_snapshots_arguments(offset: int) -> None:
    thresholds: Final = gc.get_threshold()
    gc.disable()
    gc.collect()
    source: Final = Element("section", children=[Element("a", {"id": "old"}), Element("b")])
    first, second = source.children
    target: Final = Element("main")
    changed = False

    def inspect(_context: SimpleNamespace, attrs: list[str], nodes: list[Element]) -> bool:
        return attrs == ["old"] and nodes == [second]

    extensions: Final[dict[tuple[str | None, str], Callable[[SimpleNamespace, list[str], list[Element]], bool]]] = {
        (None, "inspect"): inspect
    }
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
        gc.enable()
        reserve.clear()
    assert (changed, result, target.children) == (True, True, (source,))


@pytest.mark.parametrize(
    ("kind", "error", "match"),
    [
        pytest.param("type", TypeError, "extension result must be", id="type"),
        pytest.param("owner", ValueError, "different document", id="owner"),
        pytest.param("iterator", LookupError, "iteration resumed", id="iterator"),
    ],
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
    ("call", "extensions"),
    [
        pytest.param("matches('a', 'b')", None, id="regex"),
        pytest.param("ext()", {(None, "ext"): lambda _context: False}, id="extension"),
    ],
)
def test_arguments_stored_before_a_python_call_are_pinned_once(
    call: str, extensions: XPathExtensions | None, work_steps: WorkSteps
) -> None:
    def expression(count: int) -> str:
        return f"concat(//li{',1=1' * count},{call})"

    assert parse("<li>" * 3).xpath(expression(3), extensions=extensions) == f"{'true' * 3}false"

    def build(size: int) -> Callable[[], object]:
        document: Final = parse("<li>" * size)
        return partial(document.xpath, expression(size), extensions=extensions)

    work_steps.assert_linear(build, (250, 1000))


def test_nested_predicates_find_pinned_nodes_in_linear_time(work_steps: WorkSteps) -> None:
    def expression(depth: int) -> str:
        # matches() lets Python run, so each comparison pins its node-sets under the frames nesting it; the `0 = 1`
        # between two `//li` comparisons pins no list item, so the next one looks every item up again
        compared = " or 0 = 1 or ".join(f"//li = '{letter}'" for letter in "abc")
        return f"{'self::node()[' * depth}{compared} or true(){']' * depth} and matches('', '')"

    def build(size: int, depth: int) -> Callable[[], object]:
        return partial(parse("<li>" * size).xpath, expression(depth))

    assert build(8, 10)() is True
    work_steps.assert_linear(lambda size: build(size, 10), (300, 1200))
    work_steps.assert_within(build(1200, 10), build(1200, 0), factor=1.5)


def test_nested_predicates_pair_live_frames_in_linear_time(work_steps: WorkSteps) -> None:
    document: Final = parse("<li>" * 200)

    def build(depth: int) -> Callable[[], object]:
        # each item's comparison lets Python run under `depth` predicates whose frames are new for every item
        return partial(document.xpath, f"count(//li[{'self::li[' * depth}. != 'z'{']' * depth}]) and matches('', '')")

    assert build(16)() is True
    work_steps.assert_linear(build, (4, 16))


@pytest.mark.parametrize(
    "expression",
    [
        pytest.param("div/b[adopt()]", id="contexts"),
        pytest.param("div[seen()]/b[adopt()]", id="steps"),
        pytest.param(f"div/b[{'self::b[' * 8}adopt(){']' * 8}]", id="nested"),
    ],
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
@pytest.mark.parametrize("smart", [pytest.param(False, id="plain"), pytest.param(True, id="smart")])
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
@pytest.mark.parametrize(
    ("expression", "mixed"),
    [pytest.param("//a | //b", False, id="nodes"), pytest.param("//a/@id | //a/text() | //b", True, id="mixed")],
)
@pytest.mark.parametrize("smart_strings", [pytest.param(False, id="plain"), pytest.param(True, id="smart")])
@pytest.mark.parametrize("offset", [0, 1, 2, 4])
def test_xpath_snapshot_during_collection(offset: int, *, expression: str, smart_strings: bool, mixed: bool) -> None:
    thresholds: Final = gc.get_threshold()
    gc.disable()
    gc.collect()
    document: Final = parse('<section><a id="old">first</a><b>second</b></section>')
    section: Final = document.select("section")[0]
    first, second = section.select("*")
    target: Final = Element("main")
    selector: Final = XPath(expression, smart_strings=smart_strings)
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
        gc.enable()
        reserve.clear()
    assert isinstance(result, list)
    assert (changed, result, target.select("b"), document.select("b")) == (
        True,
        {False: [first, second], True: ["old", "first", second]}[mixed],
        [second],
        [],
    )
    owners: Final = {(True, True): [(first, "id"), (first, None)]}.get((smart_strings, mixed), [])
    assert [(item.getparent(), item.attrname) for item in result if isinstance(item, XPathString)] == owners


def _empty_snapshot_compiled(document: turbohtml.Node) -> XPathResult:
    return XPath("//a", smart_strings=True)(document)


def _empty_snapshot_method(document: turbohtml.Node) -> XPathResult:
    return document.xpath("//a", smart_strings=True)


@pytest.mark.parametrize(
    "evaluate",
    [pytest.param(_empty_snapshot_method, id="method"), pytest.param(_empty_snapshot_compiled, id="compiled")],
)
def test_xpath_empty_smart_snapshot(evaluate: Callable[[turbohtml.Node], object]) -> None:
    document: Final = parse("<section></section>")
    assert evaluate(document) == []
