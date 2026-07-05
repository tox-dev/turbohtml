"""Differential validation of turbohtml.query against soupsieve, BeautifulSoup's CSS engine.

The selector corpus is harvested from soupsieve's own test suite (the ``assert_selector`` calls without ``namespaces``
or ``custom`` arguments), so it covers the selector shapes soupsieve users actually write: escapes, case flags,
``:has()``/``:is()``/``:where()``, ``:nth-child(An+B of S)``, forgiving lists, and the UI pseudo-classes. Both engines
run each selector over the same fixture documents and must pick the same elements, compared by document-order index
(the fixtures are explicit enough that html.parser and the WHATWG parser build identical structures).

Excluded from the corpus, with the reasons on record:

- soupsieve's proprietary extensions (``[attr!=value]``, ``:-soup-contains``, ``:-soup-contains-own``) and the
  pseudo-classes turbohtml rejects as unknown (``:current``, ``:host``, ``:open``, ...) -- both engines fail loudly,
  one at compile and one at zero matches, so there is nothing to diff.
- ``:nth-child(An+B)`` on an element with no sibling nodes at all and ``input[type=hidden]`` under ``:enabled``, where
  soupsieve 2.8 disagrees with Selectors-4/HTML (an only child sits at position 1, and the current HTML spec no longer
  excludes hidden inputs), so the oracle is the one off spec.
"""

from __future__ import annotations

import pytest

from turbohtml import Element, parse
from turbohtml import query as turbo_match

soupsieve = pytest.importorskip("soupsieve")
bs4 = pytest.importorskip("bs4")

DOCS = {
    "article": (
        '<!doctype html><html lang="en"><head><title>T</title></head><body>'
        '<div id="div" class="foo bar">'
        '<p id="0" class="test1 test2 test3">Some text <span id="1" class="foo"> in a paragraph</span>.</p>'
        '<a id="2" href="http://google.com">Link</a>'
        '<span id="3" class="foo bar">Direct child</span>'
        '<pre id="pre" class="test-a test-b">'
        '<span id="4">Child 1</span>'
        '<span id="5" class="test2">Child 2</span>'
        '<span id="6">Child 3</span>'
        "</pre>"
        "</div>"
        '<main><article><h2>One</h2><p lang="en-US">p1</p><p class="lead">p2</p><p></p></article>'
        '<article dir="rtl"><h2>Two</h2><p>p3</p><em>em</em><strong>st</strong></article></main>'
        '<nav><ul><li><a href="/x">x</a></li><li class="on"><a href="/y">y</a></li><li>plain</li></ul></nav>'
        "</body></html>"
    ),
    "form": (
        "<!doctype html><html><head><title>F</title></head><body>"
        '<form action="/s"><fieldset><legend>L</legend>'
        '<input type="text" name="q" value="v" required>'
        '<input type="checkbox" name="c" checked>'
        '<input type="radio" name="r" disabled>'
        '<input type="number" min="0" max="9" value="5">'
        '<select><option value="a" selected>a</option><option value="b">b</option></select>'
        '<optgroup label="g"><option>c</option></optgroup>'
        '<textarea name="t" readonly>text</textarea>'
        '<button type="submit" disabled>go</button>'
        "</fieldset></form>"
        "<table><thead><tr><th>H1</th><th>H2</th></tr></thead><tbody>"
        "<tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr>"
        "</tbody></table>"
        "<div><span>only</span> tail</div>"
        "</body></html>"
    ),
}

SELECTORS = (
    "a:active",
    ".foo",
    "a.bar",
    "a.\\0 bar",
    "span.foo\\",
    "a.foo.bar",
    "div span",
    ".foo\\:bar\\3a foobar",
    "#\\31",
    "a#\\32",
    "span, a",
    "span",
    "tag",
    "Tag",
    "TAG",
    "a:visited",
    "[href]",
    "[   href   ]",
    "span[id].test[data-test=test]",
    "[id=\\35]",
    "[id='5']",
    '[id="5"]',
    '[  id  =  "5"  ]',
    '[ID="5"]',
    '[id="\x00pre"]',
    '[id="\\0 pre"]',
    '[type="test"]',
    "[lang|=en]",
    "[class~=test2]",
    "[class~=test-a]",
    "[class~=test-b]",
    '[class~="test1 test2"]',
    '[class~=""]',
    '[class~="test1\\ test2"]',
    "div > span",
    "div>span",
    "span:first-child",
    "input:focus",
    "input:not(:focus)",
    "a:hover",
    "p:lang(de)",
    "p:lang(en)",
    "span + span",
    "span+span",
    "span#\\34 + span#\\35",
    "body *",
    "[class^=here]",
    "[class$=words]",
    "[class*=words]",
    "span[title*='bar']",
    "span[title^='foo']",
    "span[title$='bar']",
    "span[title|='fo']",
    "span[title~='baz']",
    ":checked",
    ":disabled",
    "body :empty",
    ":enabled",
    "p:first-of-type",
    "span:first-of-type",
    "body :first-of-type",
    "span:last-child",
    "span:LAST-CHILD",
    "p:last-of-type",
    "span:last-of-type",
    "body :last-of-type",
    "ns1|el, ns2|el",
    'div :not([id="1"])',
    'span:not([id="1"])',
    'div :NOT([id="1"])',
    "p:nth-child(-2)",
    "p:nth-child(2)",
    "p:NTH-CHILD(2)",
    "p:NT\\H-CH\\ILD(2)",
    "p:nth-child(odd)",
    "p:nth-child(ODD)",
    "p:nth-child(even)",
    "p:nth-child(EVEN)",
    "p:nth-child(2n-5)",
    "p:nth-child(2N-5)",
    "p:nth-child(-2n+20)",
    "p:nth-child(50n-20)",
    "p:nth-child(-2n-2)",
    "p:nth-child(9n - 1)",
    "p:nth-child(2n + 1)",
    "p:nth-child(-n+3)",
    "span:nth-child(-n+3)",
    "p:nth-last-child(2)",
    "p:nth-last-child(2n + 1)",
    "p:nth-last-of-type(3)",
    "p:nth-last-of-type(2n + 1)",
    "p:nth-of-type(3)",
    "p:nth-of-type(2n + 1)",
    "span:nth-of-type(2n + 1)",
    "span:only-child",
    "p:only-of-type",
    ":root",
    ":root > body > div",
    ":root div",
    "p ~ span",
    "#head-2:target",
    "#head-2:not(:target)",
    "[class*=WORDS]",
    "[class*=WORDS i]",
    "[class*=WORDSi]",
    "[class*='WORDS'i]",
    '[type="test" i]',
    '[type="test" s]',
    ":default",
    ":default:default",
    "div:dir(rtl)",
    "div:dir(ltr)",
    "div:dir(ltr):dir(rtl)",
    "span:dir(rtl)",
    "span:dir(ltr)",
    ":is(input, textarea):dir(ltr)",
    "html:dir(ltr)",
    "math:dir(rtl)",
    "form:focus-visible",
    "form:not(:focus-visible)",
    "form:focus-within",
    "form:not(:focus-within)",
    "div:not(.aaaa):has(.kkkk > p.llll)",
    "p:has(+ .dddd:has(+ div .jjjj))",
    "p:has(~ .jjjj)",
    "div:has(> .bbbb)",
    "div:NOT(.aaaa):HAS(.kkkk > p.llll)",
    "div:has(> .bbbb, .ffff, .jjjj)",
    "div:has(.ffff, > .bbbb, .jjjj)",
    "div:has(> :not(.bbbb, .ffff, .jjjj))",
    "div:not(:has(> .bbbb, .ffff, .jjjj))",
    ":is(span, a)",
    ":is(span, , a)",
    ":is(, span, a)",
    ":is(span, a, )",
    ":is()",
    ":is(span, a:is(#\\32))",
    ":is(span):not(span)",
    ":is(span):is(div)",
    ":is(a):is(#\\32)",
    "p:lang(de-DE)",
    "p:lang(de--DE)",
    "p:lang(de-\\*-DE)",
    "p:lang('*-de-DE')",
    "p:lang('*-*-*-DE')",
    "p:lang(\\*-DE)",
    "p:lang('de-DE')",
    "p:lang('de-\\\nDE')",
    "p:lang('*-DE')",
    "p[lang]:lang(de-DE)",
    "div:lang('')",
    "mtext:lang(en)",
    "div :not(p, :not([id=\\35]))",
    ":nth-child(-n+3 of p)",
    ":nth-child(2n + 1 of :is(p, span).test)",
    ":nth-child(2n + 1 OF :is(p, span).test)",
    ":nth-last-child(2n + 1 of p[id], span[id])",
    ":optional",
    "input:optional",
    "body :read-only",
    ":read-write",
    ":required",
    "input:required",
    "article:target-within",
    "article:not(:target-within)",
    ":where(span, a)",
    ":where(span, a:where(#\\32))",
    # :link/:any-link reduce to :is(a, area)[href] on a parsed tree (issue #349)
    ":any-link",
    ":link",
    "a:any-link",
    ":not(a:any-link)",
    # wildcard :lang() ranges by RFC 4647 extended filtering (issue #350)
    ":lang('*')",
    ":lang('*-US')",
    "p:lang('*-US')",
    ":lang(\\*-DE)",
    # document-scoped :scope resolves to the document element (issue #351)
    ":scope",
    ":scope > body",
    ":scope > body > div",
    ":scope a",
)


def _hits(document_html: str, selector: str) -> tuple[list[int], list[int]]:
    """Both engines' matches as document-order indexes over the identical element sequence."""
    turbo_doc = parse(document_html)
    soup = bs4.BeautifulSoup(document_html, "html.parser")
    turbo_index = {
        element: position
        for position, element in enumerate(node for node in turbo_doc.descendants if isinstance(node, Element))
    }
    soup_index = {id(element): position for position, element in enumerate(soup.find_all(name=True))}
    ours = sorted(turbo_index[element] for element in turbo_match.select(selector, turbo_doc))
    theirs = sorted(soup_index[id(element)] for element in soupsieve.select(selector, soup))
    return ours, theirs


@pytest.mark.parametrize("selector", SELECTORS, ids=repr)
@pytest.mark.parametrize("doc_name", list(DOCS), ids=list(DOCS))
def test_matches_soupsieve(doc_name: str, selector: str) -> None:
    ours, theirs = _hits(DOCS[doc_name], selector)
    assert ours == theirs


def test_corpus_exercises_the_fixtures() -> None:
    # a fixture edit that silences the corpus (every selector matching nothing) must fail loudly
    matching = sum(1 for selector in SELECTORS if any(_hits(html, selector)[0] for html in DOCS.values()))
    assert matching >= 90
