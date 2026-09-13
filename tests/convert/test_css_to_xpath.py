from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

import turbohtml
from turbohtml import Document, Element, parse, query
from turbohtml.convert import (
    ExpressionError,
    GenericTranslator,
    HTMLTranslator,
    SelectorError,
    SelectorSyntaxError,
    css_specificity,
    css_to_xpath,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("div", "descendant-or-self::div", id="type"),
        pytest.param("DIV", "descendant-or-self::div", id="type-lowercased"),
        pytest.param("*", "descendant-or-self::*", id="universal"),
        pytest.param("div p", "descendant-or-self::div/descendant::p", id="descendant"),
        pytest.param("div > p", "descendant-or-self::div/p", id="child"),
        pytest.param("a + b", "descendant-or-self::a/following-sibling::*[1]/self::b", id="adjacent"),
        pytest.param("a ~ b", "descendant-or-self::a/following-sibling::b", id="sibling"),
        pytest.param("#id", "descendant-or-self::*[@id = 'id']", id="id"),
        pytest.param(
            ".cls",
            "descendant-or-self::*[@class and contains(concat(' ', normalize-space(@class), ' '), ' cls ')]",
            id="class",
        ),
        pytest.param("a[href]", "descendant-or-self::a[@href]", id="attr-exists"),
        pytest.param("a[HREF]", "descendant-or-self::a[@href]", id="attr-name-lowercased"),
        pytest.param("[data-x='a']", "descendant-or-self::*[@data-x = 'a']", id="attr-eq"),
        pytest.param('[data-x="it\'s"]', 'descendant-or-self::*[@data-x = "it\'s"]', id="literal-squote"),
        pytest.param("[data-x='say \"hi\"']", "descendant-or-self::*[@data-x = 'say \"hi\"']", id="literal-dquote"),
        pytest.param(
            r'[data-x="it\27 s\22 x"]',
            "descendant-or-self::*[@data-x = concat('it',\"'\",'s\"x')]",
            id="literal-mixed-quotes",
        ),
        pytest.param('[data-x=""]', "descendant-or-self::*[@data-x = '']", id="literal-empty"),
        pytest.param("div, p", "descendant-or-self::div | descendant-or-self::p", id="group-union"),
        pytest.param(":scope > div", "descendant-or-self::*[1]/div", id="scope-leading"),
        pytest.param("li:nth-child(n)", "descendant-or-self::li", id="nth-trivial"),
        pytest.param("li:nth-child(-n)", "descendant-or-self::li[false()]", id="nth-impossible"),
        pytest.param("li:nth-child(3)", "descendant-or-self::li[count(preceding-sibling::*) = 2]", id="nth-fixed"),
        pytest.param(
            "li:nth-child(2n+4)",
            "descendant-or-self::li[count(preceding-sibling::*) >= 3 and (count(preceding-sibling::*) + 1) mod 2 = 0]",
            id="nth-shifted",
        ),
        pytest.param(
            "li:nth-child(3n+1)",
            "descendant-or-self::li[count(preceding-sibling::*) mod 3 = 0]",
            id="nth-unshifted",
        ),
        pytest.param(
            "li:nth-last-child(-2n+4)",
            "descendant-or-self::li[count(following-sibling::*) <= 3 and (count(following-sibling::*) + 1) mod 2 = 0]",
            id="nth-last-negative-step",
        ),
        pytest.param(r"di\a0 v", "descendant-or-self::*[name() = 'di\xa0v']", id="unsafe-type-name"),
        pytest.param(r"[h\]ref]", "descendant-or-self::*[attribute::*[name() = 'h]ref']]", id="unsafe-attr-name"),
        pytest.param(":hover", "descendant-or-self::*[false()]", id="never-pseudo"),
        pytest.param(":dir(sideways)", "descendant-or-self::*[false()]", id="dir-unknown-direction"),
        pytest.param(":is(:unknown-pseudo)", "descendant-or-self::*[false()]", id="is-forgiving-empty"),
    ],
)
def test_translation(selector: str, expected: str) -> None:
    assert css_to_xpath(selector) == expected


def test_class_name_with_both_quote_kinds_uses_concat() -> None:
    assert css_to_xpath(r".it\27 s\22 x") == (
        "descendant-or-self::*[@class and contains(concat(' ', normalize-space(@class), ' '),"
        " concat(' it',\"'\",'s\"x '))]"
    )


def test_prefix_replaces_the_default_scope() -> None:
    assert css_to_xpath("div p", prefix="descendant::") == "descendant::div/descendant::p"


def test_prefix_applies_to_every_union_arm() -> None:
    assert css_to_xpath("a, b", prefix="//") == "//a | //b"


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("", id="empty"),
        pytest.param("div >", id="dangling-combinator"),
        pytest.param(":unknown-pseudo", id="unknown-pseudo"),
        pytest.param("[href=", id="unclosed-attribute"),
        pytest.param("p::before", id="pseudo-element"),
        pytest.param(":not()", id="empty-not"),
    ],
)
def test_syntax_error(selector: str) -> None:
    with pytest.raises(SelectorSyntaxError, match="invalid CSS selector"):
        css_to_xpath(selector)


@pytest.mark.parametrize(
    ("selector", "match"),
    [
        pytest.param(":dir(ltr)", ":dir", id="dir"),
        pytest.param(":default", ":default", id="default"),
        pytest.param("*:first-of-type", "type selector", id="first-of-type-universal"),
        pytest.param(".c:last-of-type", "type selector", id="last-of-type-untyped"),
        pytest.param("*:only-of-type", "type selector", id="only-of-type-universal"),
        pytest.param("*:nth-of-type(2)", "type selector", id="nth-of-type-universal"),
        pytest.param("div:scope", ":scope", id="scope-compounded"),
        pytest.param("div :scope", ":scope", id="scope-not-leading"),
        pytest.param(":is(:scope)", ":scope", id="scope-nested"),
        pytest.param("a, :dir(rtl)", ":dir", id="error-in-later-arm"),
        pytest.param(":dir(rtl), a", ":dir", id="error-in-earlier-arm"),
    ],
)
def test_expression_error(selector: str, match: str) -> None:
    with pytest.raises(ExpressionError, match=match):
        css_to_xpath(selector)


def test_syntax_error_is_the_one_unified_selector_error() -> None:
    assert SelectorSyntaxError is turbohtml.SelectorSyntaxError is query.SelectorSyntaxError
    assert issubclass(SelectorSyntaxError, ValueError)


def test_expression_error_keeps_the_cssselect_shape() -> None:
    assert issubclass(ExpressionError, SelectorError)
    assert issubclass(ExpressionError, RuntimeError)


def test_non_str_selector_raises_type_error() -> None:
    with pytest.raises(TypeError):
        css_to_xpath(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C argument guard


def test_generic_translator_is_a_method_like_cssselect() -> None:
    assert GenericTranslator().css_to_xpath("div p") == css_to_xpath("div p")


def test_generic_translator_passes_the_prefix_positionally() -> None:
    assert GenericTranslator().css_to_xpath("div", "//") == "//div"


def test_html_translator_records_the_xhtml_flag() -> None:
    assert HTMLTranslator().xhtml is False
    assert HTMLTranslator(xhtml=True).xhtml is True


def test_html_translator_translates_like_the_function() -> None:
    assert HTMLTranslator().css_to_xpath("A[HREF]") == css_to_xpath("a[href]")


IDS_DOC = """<!doctype html>
<html id="html" lang="en"><head>
  <link id="link-href" href="foo">
  <link id="link-nohref">
</head><body>
<div id="outer-div">
 <a id="name-anchor" name="foo"></a>
 <a id="tag-anchor" rel="tag" href="http://localhost/foo">link</a>
 <a id="nofollow-anchor" rel="nofollow" href="https://example.org">link</a>
 <ol id="first-ol" class="a b c">
   <li id="first-li">content</li>
   <li id="second-li" lang="En-us"><div id="li-div"></div></li>
   <li id="third-li" class="ab c"></li>
   <li id="fourth-li" class="ab
c"></li>
   <li id="fifth-li"></li>
   <li id="sixth-li"></li>
   <li id="seventh-li">  </li>
 </ol>
 <p id="paragraph" lang="fr">
   <b id="p-b">hi</b> <em id="p-em">there</em>
   <b id="p-b2" title="it's">guy</b>
   <custom-tag id="custom-one"></custom-tag>
   <custom-tag id="custom-two" data-x='say "hi"'></custom-tag>
 </p>
 <ol id="second-ol"></ol>
 <map name="dummymap">
   <area shape="circle" coords="200,250,25" href="foo.html" id="area-href">
   <area shape="default" id="area-nohref">
 </map>
</div>
<div id="foobar-div" foobar="ab bc
cde"><span id="foobar-span"></span></div>
</body></html>
"""

FORMS_DOC = """<!doctype html>
<html><body>
<form id="form">
 <input type="checkbox" id="checkbox-unchecked">
 <input type="CHECKBOX" id="checkbox-shouty" checked>
 <input type="checkbox" id="checkbox-disabled" disabled>
 <input type="text" id="text-checked" checked="checked">
 <input type="hidden" id="hidden-plain">
 <input type="hidden" id="hidden-disabled" disabled>
 <input type="checkbox" id="checkbox-checked" checked>
 <input id="typeless" required>
 <input type="color" id="color-input">
 <textarea id="area-rw"></textarea>
 <textarea id="area-ro" readonly></textarea>
 <div id="editable" contenteditable></div>
 <div id="editable-true" contenteditable="TRUE"></div>
 <div id="editable-false" contenteditable="false"></div>
 <select id="select" required>
   <option id="option-selected" selected>a</option>
   <optgroup id="optgroup-disabled" disabled><option id="option-in-disabled">b</option></optgroup>
   <optgroup id="optgroup"><option id="option-plain">c</option></optgroup>
 </select>
 <fieldset id="f1" disabled>
   <legend id="l1"><input id="in-first-legend"></legend>
   <legend id="l2"><input id="in-second-legend"></legend>
   <input id="in-fieldset-body">
   <fieldset id="f2"><legend id="l3"><input id="nested-legend-input"></legend></fieldset>
 </fieldset>
 <fieldset id="f3" disabled><fieldset id="f4" disabled>
   <legend id="l4"><input id="doubly-fenced"></legend>
 </fieldset></fieldset>
 <button id="button" disabled></button>
</form>
<p id="lang-none">x</p>
<p id="lang-empty" lang="">x</p>
<div lang="en-US"><p id="lang-inherited">x</p><p id="lang-own" lang="fr-CA">y</p></div>
<span id="dir-none">abc</span>
</body></html>
"""

DOCS = {"ids": IDS_DOC, "forms": FORMS_DOC}

SELECTORS = (
    # type, universal, and (ignored) namespace prefixes
    "div",
    "DIV",
    "*",
    "x|div",
    "*|div",
    "|div",
    "custom-tag",
    "nonexistent",
    r"di\a0 v",
    r"div\[",
    r"\31 div",
    r"_x\.y-z",
    "h1",
    r"a\21 b",
    "a_b",
    # id and class, including escaped names XPath cannot spell
    "#first-li",
    "li#first-li",
    "*#first-li",
    ".a",
    ".c",
    "ol.a.b.c",
    r".a\20 b",
    r".it\27 s",
    r".sa\22 y",
    r".it\27 s\22 x",
    r"#it\27 s",
    # attribute operators, quoting, and the HTML case-insensitive set
    "a[name]",
    "a[NAme]",
    r"[h\]ref]",
    r"[h\]ref=x]",
    "a[rel]",
    'a[rel="tag"]',
    "a[rel=TAG]",
    "a[rel=TAG i]",
    "a[rel=tag s]",
    '[title="it\'s"]',
    "[data-x='say \"hi\"']",
    'a[href*="localhost"]',
    'a[href*=""]',
    'a[href^="http"]',
    'a[href^="http:"]',
    'a[href^=""]',
    'a[href$="org"]',
    'a[href$=""]',
    'div[foobar~="bc"]',
    'div[foobar~="cde"]',
    '[foobar~="ab bc"]',
    '[foobar~=""]',
    '*[lang|="En"]',
    '[lang|="en"]',
    "[data-x|=y]",
    "[type=CHECKBOX]",
    "[type=checkbox s]",
    # combinators
    "div div",
    "div > div",
    "li + li",
    "li ~ li",
    "ol#first-ol li + li:nth-child(4)",
    "li + li:nth-child(1)",
    "li ~ li:nth-child(2n+1)",
    "p > b + em",
    "p b ~ em",
    # structural pseudo-classes
    ":root",
    "html:root",
    "li:root",
    "a:empty",
    "li:empty",
    "p:empty",
    "li:first-child",
    "li:last-child",
    "#outer-div:first-child",
    "#outer-div :first-child",
    "span:only-child",
    "div *:only-child",
    "ol:first-of-type",
    "ol:last-of-type",
    "p:only-of-type",
    "custom-tag:first-of-type",
    "custom-tag:last-of-type",
    "custom-tag:only-of-type",
    r"\31 a:nth-of-type(1)",
    # the nth-* An+B algebra, every branch
    "li:nth-child(1)",
    "li:nth-child(3)",
    "li:nth-child(0)",
    "li:nth-child(10)",
    "li:nth-child(n)",
    "li:nth-child(n+3)",
    "li:nth-child(n-5)",
    "li:nth-child(-n)",
    "li:nth-child(-n+3)",
    "li:nth-child(-2n+4)",
    "li:nth-child(2n)",
    "li:nth-child(even)",
    "li:nth-child(2n+1)",
    "li:nth-child(odd)",
    "li:nth-child(+2n+1)",
    "li:nth-child(2n+4)",
    "li:nth-child(3n+1)",
    "li:nth-child(3n-1)",
    "li:nth-last-child(0)",
    "li:nth-last-child(1)",
    "li:nth-last-child(2n)",
    "li:nth-last-child(even)",
    "li:nth-last-child(2n+2)",
    "li:nth-last-child(3n+1)",
    "ol:nth-child(1)",
    "ol:nth-of-type(2)",
    "ol:nth-last-of-type(1)",
    "li:nth-child(2n of .c)",
    "li:nth-child(n of .c)",
    "li:nth-last-child(1 of li[class])",
    # logical pseudo-classes and :has()
    ":is(#first-li, #second-li)",
    "a:is(#name-anchor, #tag-anchor)",
    ":is(.c)",
    ":is(*)",
    ":where(.c)",
    ":is(:unknown-pseudo)",
    ":where(:unknown-pseudo)",
    ":is(ol li)",
    ":is(ol > li)",
    ":is(b + em)",
    ":is(* + em)",
    ":is(b ~ em)",
    ":is(ol li div)",
    ":is(* div)",
    ":is(ol *)",
    "ol.a.b.c > li.c:nth-child(3)",
    ":not(*)",
    "a:not([href])",
    "ol :not(li[class])",
    "div:not(.a, #outer-div)",
    "li:not(ol > li)",
    "link:has(*)",
    "ol:has(div)",
    "div:has(> ol)",
    "a:has(+ a)",
    "a:has(~ ol)",
    "p:has(b + em)",
    "p:has(> b ~ em)",
    "body:has(ol .c)",
    "div:has(> a + a)",
    "div:has(ol, map)",
    ":is(ol li.c div)",
    # input pseudo-classes over the static tree
    ":checked",
    ":disabled",
    ":enabled",
    ":required",
    ":optional",
    ":read-only",
    ":read-write",
    "input:read-write",
    # :lang(), :dir(), and the never-matching UA-state pseudo-classes
    ":lang(en)",
    ":lang(EN)",
    ":lang(en-US)",
    ":lang(e)",
    ":lang(en , fr)",
    ':lang("fr")',
    ":lang('en)",
    ":lang('en-us')",
    ":lang(fr, en)",
    ":lang(,)",
    ":dir(sideways)",
    "a:hover",
    ":link",
    ":visited",
    ":focus",
    ":active",
    ":any-link",
    ":target",
    # selector groups
    "div, p",
    "div, div div",
    "a, :is(b)",
    # a value long enough to grow the emitter buffer past its first allocation
    f"[data-x='{'v' * 600}']",
)


def _ids(nodes: Iterable[Element | str]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        assert isinstance(node, Element)
        identifier = node.attrs.get("id")
        out.append(identifier if isinstance(identifier, str) else "nil")
    return out


@pytest.fixture(scope="module")
def documents() -> dict[str, Document]:
    return {name: parse(source) for name, source in DOCS.items()}


@pytest.mark.parametrize("selector", [pytest.param(selector, id=selector) for selector in SELECTORS])
def test_xpath_selects_the_same_nodes_from_the_document(selector: str, documents: dict[str, Document]) -> None:
    expression = css_to_xpath(selector)
    for name, document in documents.items():
        assert _ids(document.xpath(expression)) == _ids(document.select(selector)), (name, expression)


@pytest.mark.parametrize("selector", [pytest.param(selector, id=selector) for selector in SELECTORS])
def test_xpath_selects_the_same_nodes_from_an_element(selector: str, documents: dict[str, Document]) -> None:
    # select() walks descendants only, which is exactly the descendant:: prefix
    expression = css_to_xpath(selector, prefix="descendant::")
    for name, document in documents.items():
        body = document.select_one("body")
        assert body is not None
        assert _ids(body.xpath(expression)) == _ids(body.select(selector)), (name, expression)


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(":scope > div", id="scope-child"),
        pytest.param(":scope ol", id="scope-descendant"),
        pytest.param(":scope > div > ol", id="scope-chain"),
        pytest.param(":scope li, :scope span", id="scope-group"),
    ],
)
def test_scope_matches_the_query_root(selector: str, documents: dict[str, Document]) -> None:
    # the default prefix's descendant-or-self::*[1] is the context element itself
    body = documents["ids"].select_one("body")
    assert body is not None
    assert _ids(body.xpath(css_to_xpath(selector))) == _ids(body.select(selector))


def test_most_selectors_match_something(documents: dict[str, Document]) -> None:
    # guard the corpus against rotting into vacuous empty-vs-empty comparisons
    matching = sum(1 for selector in SELECTORS if any(document.select(selector) for document in documents.values()))
    assert matching >= 2 * len(SELECTORS) // 3


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        pytest.param("*", [(0, 0, 0)], id="universal-zero"),
        pytest.param("li", [(0, 0, 1)], id="type"),
        pytest.param("ul li", [(0, 0, 2)], id="descendant-two-types"),
        pytest.param("ul li a", [(0, 0, 3)], id="descendant-three-types"),
        pytest.param("#id", [(1, 0, 0)], id="id"),
        pytest.param(".c", [(0, 1, 0)], id="class"),
        pytest.param("a[href]", [(0, 1, 1)], id="type-and-attribute"),
        pytest.param("a.b#c", [(1, 1, 1)], id="type-class-id-compound"),
        pytest.param("div.x.y", [(0, 2, 1)], id="two-classes"),
        pytest.param("p > a.link", [(0, 1, 2)], id="child-combinator"),
        pytest.param("a:hover", [(0, 1, 1)], id="pseudo-class-counts-b"),
        pytest.param("li:nth-child(2)", [(0, 1, 1)], id="nth-child-counts-b"),
        pytest.param(":nth-last-child(1)", [(0, 1, 0)], id="nth-last-child-without-filter"),
        pytest.param(":nth-child(1 of #x)", [(1, 1, 0)], id="nth-child-adds-id-filter"),
        pytest.param(":nth-last-child(2 of .c)", [(0, 2, 0)], id="nth-last-child-adds-class-filter"),
        pytest.param("li:nth-child(2n of #x, .c)", [(1, 1, 1)], id="nth-child-max-filter"),
        pytest.param(":nth-last-child(odd of div, .a.b)", [(0, 3, 0)], id="nth-last-child-max-filter"),
        pytest.param(":nth-child(1 of :where(#x))", [(0, 1, 0)], id="nth-child-zero-filter"),
        pytest.param(":where(:nth-child(1 of #x))", [(0, 0, 0)], id="where-zeroes-nth-filter"),
        pytest.param(":nth-child(1 of :nth-child(1 of #x))", [(1, 2, 0)], id="nested-nth-filters"),
        pytest.param(":where(#x)", [(0, 0, 0)], id="where-is-zero"),
        pytest.param(":is(#a, .b)", [(1, 0, 0)], id="is-takes-most-specific-id"),
        pytest.param(":is(.a, .b.c)", [(0, 2, 0)], id="is-takes-most-specific-class-count"),
        pytest.param(":is(.x, .x y)", [(0, 1, 1)], id="is-tiebreak-on-c"),
        pytest.param(":is(li, div)", [(0, 0, 1)], id="is-equal-alternatives"),
        pytest.param(":not(.a.b)", [(0, 2, 0)], id="not-takes-argument"),
        pytest.param(":has(.x)", [(0, 1, 0)], id="has-takes-argument"),
        pytest.param("h1, .foo, #bar", [(0, 0, 1), (0, 1, 0), (1, 0, 0)], id="comma-list-one-triple-each"),
        pytest.param("*, li", [(0, 0, 0), (0, 0, 1)], id="universal-then-type"),
    ],
)
def test_specificity_matches_the_spec(selector: str, expected: list[tuple[int, int, int]]) -> None:
    assert css_specificity(selector) == expected


def test_specificity_rejects_an_invalid_selector() -> None:
    with pytest.raises(SelectorSyntaxError):
        css_specificity("a >> b")


def test_specificity_rejects_a_non_string() -> None:
    with pytest.raises(TypeError):
        css_specificity(123)  # ty: ignore[invalid-argument-type]  # intentional non-str exercises the C argument guard


_ORACLE_DOC: Final = """<!doctype html>
<html id="html"><head>
  <link id="link-href" href="foo">
  <link id="link-nohref">
</head><body>
<div id="outer-div">
 <a id="name-anchor" name="foo"></a>
 <a id="tag-anchor" rel="tag" href="http://localhost/foo">link</a>
 <a id="nofollow-anchor" rel="nofollow" href="https://example.org">link</a>
 <ol id="first-ol" class="a b c">
   <li id="first-li">content</li>
   <li id="second-li" lang="En-us"><div id="li-div"></div></li>
   <li id="third-li" class="ab c"></li>
   <li id="fourth-li" class="ab
c"></li>
   <li id="fifth-li"></li>
   <li id="sixth-li"></li>
   <li id="seventh-li">  </li>
 </ol>
 <p id="paragraph">
   <b id="p-b">hi</b> <em id="p-em">there</em>
   <b id="p-b2">guy</b>
   <input type="checkbox" id="checkbox-unchecked">
   <input type="checkbox" id="checkbox-disabled" disabled>
   <input type="text" id="text-checked" checked="checked">
   <input type="checkbox" id="checkbox-checked" checked>
   <input type="checkbox" id="checkbox-disabled-checked" disabled checked>
 </p>
 <ol id="second-ol"></ol>
</div>
<div id="foobar-div" foobar="ab bc
cde"><span id="foobar-span"></span></div>
</body></html>
"""

_ORACLE_SELECTORS: Final = (
    "*",
    "div",
    "div div",
    "div, div div",
    "a[name]",
    "a[rel]",
    'a[rel="tag"]',
    'a[href*="localhost"]',
    'a[href*=""]',
    'a[href^="http"]',
    'a[href^="http:"]',
    'a[href$="org"]',
    'div[foobar~="bc"]',
    'div[foobar~="cde"]',
    '[foobar~="ab bc"]',
    '[foobar~=""]',
    "li:nth-child(3)",
    "li:nth-child(10)",
    "li:nth-child(2n)",
    "li:nth-child(even)",
    "li:nth-child(2n+1)",
    "li:nth-child(odd)",
    "li:nth-child(2n+4)",
    "li:nth-child(3n+1)",
    "li:nth-child(-n+3)",
    "li:nth-child(-2n+4)",
    "li:nth-child(n)",
    "li:nth-child(-n)",
    "li:nth-last-child(1)",
    "li:nth-last-child(2n)",
    "li:nth-last-child(2n+2)",
    "ol:first-of-type",
    "ol:nth-child(1)",
    "ol:nth-of-type(2)",
    "ol:nth-last-of-type(1)",
    "span:only-child",
    "li div:only-child",
    "div *:only-child",
    "p:only-of-type",
    "a:empty",
    ":root",
    "html:root",
    "li:root",
    ".a",
    ".c",
    "ol *.c",
    "ol li.c",
    "li ~ li.c",
    "ol > li.c",
    "#first-li",
    "li#first-li",
    "*#first-li",
    "li div",
    "div > div",
    "div>.c",
    "div + div",
    "a ~ a",
    'a[rel="tag"] ~ a',
    "ol#first-ol li:last-child",
    "ol#first-ol *:last-child",
    "#outer-div:first-child",
    "#outer-div :first-child",
    "a[href]",
    ":not(*)",
    "a:not([href])",
    "ol :not(li[class])",
    "link:has(*)",
    "ol:has(div)",
    ":is(#first-li, #second-li)",
    "a:is(#name-anchor, #tag-anchor)",
    ":is(.c)",
    "ol.a.b.c > li.c:nth-child(3)",
    ":checked",
    ":lang(en)",
    ":visited",
    r"di\a0 v",
    r"div\[",
    r"[h\a0 ref]",
    r"[h\]ref]",
)


@pytest.mark.parametrize("selector", [pytest.param(selector, id=selector) for selector in _ORACLE_SELECTORS])
@pytest.mark.oracle
def test_matches_the_cssselect_oracle(selector: str) -> None:
    lxml_html: Final = pytest.importorskip("lxml.html")
    cssselect: Final = pytest.importorskip("cssselect")
    ours = turbohtml.parse(_ORACLE_DOC)
    theirs = lxml_html.document_fromstring(_ORACLE_DOC)
    our_expression = css_to_xpath(selector)
    oracle_ids = [node.get("id", "nil") for node in theirs.xpath(cssselect.HTMLTranslator().css_to_xpath(selector))]
    assert _ids(ours.xpath(our_expression)) == oracle_ids
    assert [node.get("id", "nil") for node in theirs.xpath(our_expression)] == oracle_ids


@pytest.mark.parametrize(
    ("selector", "ours_only", "theirs_only"),
    [
        pytest.param("li:empty", ["seventh-li"], [], id="empty-allows-whitespace-per-selectors-4"),
        pytest.param('*[lang|="en"]', ["second-li"], [], id="lang-attr-is-case-insensitive-in-html"),
        pytest.param("input[type=CHECKBOX]", ["checkbox-unchecked"], [], id="type-attr-is-case-insensitive-in-html"),
    ],
)
@pytest.mark.oracle
def test_documented_divergences_from_cssselect(
    selector: str,
    ours_only: list[str],
    theirs_only: list[str],
) -> None:
    lxml_html: Final = pytest.importorskip("lxml.html")
    cssselect: Final = pytest.importorskip("cssselect")
    ours = turbohtml.parse(_ORACLE_DOC)
    our_ids = set(_ids(ours.xpath(css_to_xpath(selector))))
    oracle_expression = cssselect.HTMLTranslator().css_to_xpath(selector)
    oracle_ids = {node.get("id", "nil") for node in lxml_html.document_fromstring(_ORACLE_DOC).xpath(oracle_expression)}
    assert our_ids - oracle_ids >= set(ours_only)
    assert oracle_ids - our_ids == set(theirs_only)
    assert our_ids == set(_ids(ours.select(selector)))
