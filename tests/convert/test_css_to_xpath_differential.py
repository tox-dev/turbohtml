"""Differential validation of css_to_xpath() against the cssselect + lxml oracle.

cssselect's HTMLTranslator over libxml2 is the translation the ecosystem (lxml, parsel,
pyquery) runs, so on the shared semantic subset our expression must select the same
elements. Both translations are evaluated with lxml on the same tree, which also proves
the emitted expression is XPath 1.0 libxml2 accepts, independent of turbohtml's engine.
Where turbohtml follows the current specs and cssselect approximates (Selectors 4
``:empty``, the WHATWG case-insensitive attribute set, ``:disabled`` on hidden inputs
and inside a fieldset's first legend), the divergence is pinned explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import turbohtml
from turbohtml.convert import css_to_xpath

if TYPE_CHECKING:
    from collections.abc import Iterable

lxml_html = pytest.importorskip("lxml.html")
cssselect = pytest.importorskip("cssselect")

# the ids document from cssselect's own suite, restructured only where the WHATWG and
# libxml2 parsers would disagree on the tree (the fieldset no longer sits inside a p)
DOC = """<!doctype html>
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

# the subset of the corpus whose semantics cssselect and turbohtml share
SELECTORS = (
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


def _ids(nodes: Iterable[object]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if isinstance(node, turbohtml.Element):
            identifier = node.attrs.get("id")
        else:
            identifier = getattr(node, "get", None)  # lxml elements read attributes through .get
            assert callable(identifier)
            identifier = identifier("id")
        out.append(identifier if isinstance(identifier, str) else "nil")
    return out


@pytest.mark.parametrize("selector", [pytest.param(selector, id=selector) for selector in SELECTORS])
def test_matches_the_cssselect_oracle(selector: str) -> None:
    ours = turbohtml.parse(DOC)
    theirs = lxml_html.document_fromstring(DOC)
    our_expression = css_to_xpath(selector)
    oracle_ids = _ids(theirs.xpath(cssselect.HTMLTranslator().css_to_xpath(selector)))
    # our expression evaluated by turbohtml's XPath engine selects the oracle's nodes
    assert _ids(ours.xpath(our_expression)) == oracle_ids
    # and libxml2 accepts our expression and agrees, so the output is portable XPath 1.0
    assert _ids(theirs.xpath(our_expression)) == oracle_ids


@pytest.mark.parametrize(
    ("selector", "ours_only", "theirs_only"),
    [
        pytest.param("li:empty", ["seventh-li"], [], id="empty-allows-whitespace-per-selectors-4"),
        pytest.param('*[lang|="en"]', ["second-li"], [], id="lang-attr-is-case-insensitive-in-html"),
        pytest.param("input[type=CHECKBOX]", ["checkbox-unchecked"], [], id="type-attr-is-case-insensitive-in-html"),
    ],
)
def test_documented_divergences_from_cssselect(
    selector: str,
    ours_only: list[str],
    theirs_only: list[str],
) -> None:
    ours = turbohtml.parse(DOC)
    our_ids = set(_ids(ours.xpath(css_to_xpath(selector))))
    oracle_expression = cssselect.HTMLTranslator().css_to_xpath(selector)
    oracle_ids = set(_ids(lxml_html.document_fromstring(DOC).xpath(oracle_expression)))
    assert our_ids - oracle_ids >= set(ours_only)
    assert oracle_ids - our_ids == set(theirs_only)
    # the divergence follows the native CSS engine, the semantics the translation promises
    assert our_ids == set(_ids(ours.select(selector)))
