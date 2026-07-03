"""The node-oriented core functions ``id()``, ``namespace-uri()``, and ``lang()``.

``id()`` matches lxml exactly. Two functions diverge, and in both cases lxml's
legacy HTML parser is the outlier while turbohtml follows the DOM:

* ``lang()`` is defined over ``xml:lang``, which an HTML document never carries,
  so lxml's ``lang()`` is dead on HTML. turbohtml reads the HTML ``lang``
  attribute, which is what the markup actually uses.
* ``namespace-uri()`` returns the real SVG / MathML URI for foreign content,
  where lxml's HTML parser leaves every element un-namespaced. HTML elements
  report no namespace in both.

Neither divergence breaks a migrating query: each only makes a function that
returns nothing under lxml-on-HTML return something useful.
"""

from __future__ import annotations

import pytest

import turbohtml
from turbohtml import Element

ID_HTML = (
    "<html><body><p id='a'>one</p><p id='b'>two</p><div class='x' id='c'>three</div>"
    "<span>no id</span><i id=''>empty</i><!--note--></body></html>"
)

NS_HTML = "<html><body><p id='p'>html</p><svg id='s'><circle/></svg><math><mi>x</mi></math></body></html>"

LANG_HTML = (
    "<html><body><p id='nolang'>x</p>"
    "<div lang='en-US'><p id='inherit'>a</p></div>"
    "<div lang='en'><p id='outer'>o</p>"
    "<div lang='FR'><span><p id='inner'>i</p></span></div></div>"
    "</body></html>"
)


def tags(result: list[Element | str]) -> list[str]:
    return [node.tag if isinstance(node, Element) else node for node in result]


@pytest.fixture
def ids() -> turbohtml.Node:
    return turbohtml.parse(ID_HTML)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("id('a')", ["p"], id="single"),
        pytest.param("id('a c')", ["p", "div"], id="multiple-tokens"),
        pytest.param("id('  a   c  ')", ["p", "div"], id="surrounding-and-inner-space"),
        pytest.param("id('xx a')", ["p"], id="token-length-mismatch-then-hit"),
        pytest.param("id('missing')", [], id="no-such-id"),
        pytest.param("id('')", [], id="empty-string"),
        pytest.param("id(//div/@id)", ["div"], id="nodeset-argument"),
        pytest.param("id(//div/@class)", [], id="nodeset-argument-no-match"),
        pytest.param("id(//p)", [], id="nodeset-string-values-are-text"),
        pytest.param("id(//zzz)", [], id="empty-nodeset-argument"),
    ],
)
def test_id(ids: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert tags(ids.xpath(expr)) == expected


def test_id_string_value(ids: turbohtml.Node) -> None:
    assert ids.xpath("string(id('b'))") == "two"


@pytest.fixture
def foreign() -> turbohtml.Node:
    return turbohtml.parse(NS_HTML)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("namespace-uri(//p)", "", id="html-element"),
        pytest.param("namespace-uri(//*[local-name()='svg'])", "http://www.w3.org/2000/svg", id="svg-element"),
        pytest.param("namespace-uri(//*[local-name()='math'])", "http://www.w3.org/1998/Math/MathML", id="mathml"),
        pytest.param("namespace-uri(//p/@id)", "", id="attribute"),
        pytest.param("namespace-uri(//p/text())", "", id="text-node"),
        pytest.param("namespace-uri('literal')", "", id="non-nodeset-argument"),
        pytest.param("namespace-uri(//zzz)", "", id="empty-nodeset-argument"),
    ],
)
def test_namespace_uri(foreign: turbohtml.Node, expr: str, expected: str) -> None:
    assert foreign.xpath(expr) == expected


def test_namespace_uri_context_node_counts_svg_subtree(foreign: turbohtml.Node) -> None:
    # The <svg> and its <circle> child are both in the SVG namespace.
    assert foreign.xpath("count(//*[namespace-uri()='http://www.w3.org/2000/svg'])") == pytest.approx(2.0)


@pytest.fixture
def langs() -> turbohtml.Node:
    return turbohtml.parse(LANG_HTML)


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        pytest.param("//p[@id='inherit'][lang('en')]", ["p"], id="subtag-prefix"),
        pytest.param("//p[@id='inherit'][lang('en-US')]", ["p"], id="exact"),
        pytest.param("//p[@id='inherit'][lang('EN')]", ["p"], id="case-insensitive-want"),
        pytest.param("//p[@id='outer'][lang('en')]", ["p"], id="direct-attribute"),
        pytest.param("//p[@id='inner'][lang('fr')]", ["p"], id="case-insensitive-tag-via-ancestor"),
        pytest.param("//p[@id='inner'][lang('en')]", [], id="nearest-wins-no-fallthrough"),
        pytest.param("//p[@id='nolang'][lang('en')]", [], id="no-lang-in-any-ancestor"),
        pytest.param("//p[@id='inherit'][lang('e')]", [], id="not-a-subtag-boundary"),
        pytest.param("//p[@id='inherit'][lang('en-US-extra')]", [], id="want-longer-than-tag"),
    ],
)
def test_lang(langs: turbohtml.Node, expr: str, expected: list[str]) -> None:
    assert tags(langs.xpath(expr)) == expected


def test_lang_reads_html_lang_attribute_where_lxml_reads_xml_lang(langs: turbohtml.Node) -> None:
    # lxml's lang() returns nothing here (no xml:lang); turbohtml matches the
    # 'inherit' (lang='en-US') and 'outer' (lang='en') paragraphs.
    assert len(langs.xpath("//p[lang('en')]")) == 2


def test_lang_on_a_text_node_context_reads_ancestor_lang() -> None:
    # lang() walks self-or-ancestor elements from the context node; on a text-node
    # context it used to loop over a text-node span's attr_count with attrs == NULL and
    # dereference a null pointer (issue #422)
    doc = turbohtml.parse("<p lang=en>hi<b>x</b></p>")
    assert doc.xpath('//text()[lang("en")]') == ["hi", "x"]


def test_lang_is_false_with_no_lang_bearing_ancestor() -> None:
    doc = turbohtml.parse("<div><p>hi</p></div>")
    assert doc.xpath("//text()[lang('en')]") == []
