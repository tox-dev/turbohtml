"""Differential validation of css_to_xpath() against the native CSS engine.

Every selector in the corpus is translated and evaluated with turbohtml's XPath engine,
and the resulting node-set must equal what the CSS selector engine selects for the same
selector on the same documents. The corpus harvests the selectors cssselect exercises in
its own suite (tests/test_cssselect.py, the ids and Shakespeare documents) and adds the
turbohtml-specific surface: the HTML case-insensitive attribute set, Selectors 4 input
pseudo-classes, :nth-child(... of S), and relative :has() arms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import Document, Element, parse
from turbohtml.convert import css_to_xpath

if TYPE_CHECKING:
    from collections.abc import Iterable

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
