"""Differential validation of the CSS-to-XPath translator against turbohtml's own CSS engine.

Each selector is run two ways over the same tree: directly through :meth:`Node.select` (the CSS
engine) and through :meth:`Node.xpath` over the translated expression. Both must select the same
element set in the same document order, so the translation is correct by construction. The XPath is
evaluated from the document root with the default ``descendant-or-self::`` prefix, which includes the
root itself; the root is dropped to match ``select``'s descendants-only semantics.
"""

from __future__ import annotations

from typing import cast

import pytest

import turbohtml
from turbohtml.convert import css_to_xpath

_DOC = turbohtml.parse(
    """<!doctype html><html><body>
    <header id="top"><nav><a href="/a" class="link">a</a><a class="link active">b</a><a>c</a></nav></header>
    <main>
      <section class="card" data-id="1" lang="EN-us"><h2>One</h2><p id="lead">p1</p><p class="note">p2</p>
        <p>p3</p></section>
      <section class="card wide" data-id="2"><h2>Two</h2><p>p4</p></section>
      <ul><li>i1</li><li class="x">i2</li><li>i3</li><li class="x">i4</li><li>i5</li></ul>
      <form><input type="Text" value="hi"><input type="checkbox" checked><textarea></textarea></form>
      <article><span></span><b>bold</b><i> </i><em><!--c--></em></article>
      <table><tr><td>r1c1</td><td>r1c2</td></tr><tr><td>r2c1</td></tr></table>
    </main>
    </body></html>"""
)
_ROOT = cast("turbohtml.Element", _DOC.root)


def _by_xpath(selector: str) -> list[turbohtml.Element]:
    """Return the elements the translated XPath selects, dropping the context root that select excludes."""
    return [
        node
        for node in _ROOT.xpath(css_to_xpath(selector))
        if isinstance(node, turbohtml.Element) and node != _ROOT
    ]


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("*", id="universal"),
        pytest.param("p", id="type"),
        pytest.param("DIV", id="type-uppercase"),
        pytest.param(".card", id="class"),
        pytest.param("#lead", id="id"),
        pytest.param("p.note", id="type-class"),
        pytest.param("section.card.wide", id="two-classes"),
        pytest.param("[data-id]", id="attr-exists"),
        pytest.param("[data-id=1]", id="attr-eq"),
        pytest.param("[data-id='2']", id="attr-eq-quoted"),
        pytest.param("[type=text]", id="attr-eq-ci-default"),
        pytest.param("[type=TEXT i]", id="attr-eq-explicit-ci"),
        pytest.param("[class~=active]", id="attr-include"),
        pytest.param("[lang|=en]", id="attr-dash"),
        pytest.param("[href^='/']", id="attr-prefix"),
        pytest.param("[value$=i]", id="attr-suffix"),
        pytest.param("[href*='/a']", id="attr-substring"),
        pytest.param("[missing^=x]", id="attr-prefix-empty-set"),
        pytest.param("section p", id="descendant"),
        pytest.param("section > p", id="child"),
        pytest.param("h2 + p", id="adjacent"),
        pytest.param("h2 ~ p", id="general-sibling"),
        pytest.param("nav a[href]", id="descendant-attr"),
        pytest.param("header > nav a + a", id="mixed-combinators"),
        pytest.param("p:first-child", id="first-child"),
        pytest.param("p:last-child", id="last-child"),
        pytest.param("span:only-child", id="only-child"),
        pytest.param("p:first-of-type", id="first-of-type"),
        pytest.param("p:last-of-type", id="last-of-type"),
        pytest.param("h2:only-of-type", id="only-of-type"),
        pytest.param("li:nth-child(2)", id="nth-const"),
        pytest.param("li:nth-child(2n)", id="nth-even"),
        pytest.param("li:nth-child(2n+1)", id="nth-odd"),
        pytest.param("li:nth-child(odd)", id="nth-keyword"),
        pytest.param("li:nth-child(-n+2)", id="nth-negative"),
        pytest.param("li:nth-child(3n-1)", id="nth-shift-negative"),
        pytest.param("li:nth-last-child(1)", id="nth-last"),
        pytest.param("p:nth-of-type(2)", id="nth-of-type"),
        pytest.param("p:nth-of-type(2n+1)", id="nth-of-type-step"),
        pytest.param("p:nth-of-type(-n+2)", id="nth-of-type-window"),
        pytest.param("p:nth-last-of-type(1)", id="nth-last-of-type"),
        pytest.param("li:not(.x)", id="not-class"),
        pytest.param("section:not(.wide)", id="not-class-keep"),
        pytest.param("main *:not(p):not(li)", id="not-multi"),
        pytest.param("li:not(:first-child)", id="not-pseudo"),
        pytest.param(":root", id="root"),
        pytest.param("span:empty", id="empty-element"),
        pytest.param("i:empty", id="empty-whitespace"),
        pytest.param("em:empty", id="empty-comment"),
        pytest.param("p, li", id="group"),
        pytest.param("section.card, ul > li.x", id="group-complex"),
        pytest.param("td", id="table-cell"),
    ],
)
def test_translation_matches_css_engine(selector: str) -> None:
    """The translated XPath selects exactly what the CSS engine selects, in the same order."""
    assert _ROOT.select(selector) == _by_xpath(selector)
