"""Differential validation of the CSS-to-XPath translator against lxml's cssselect + libxml2 XPath.

cssselect is the de-facto CSS-to-XPath translator (lxml/parsel/pyquery wrap it), and libxml2 is the XPath 1.0
oracle. For each selector, the elements lxml selects via its own cssselect must equal the elements libxml2 selects
when handed turbohtml's translated XPath, so the translation agrees with the reference stack node-for-node. lxml ships
no wheels for some interpreters and cssselect is a bench-only dependency, so both are imported defensively; this module
is excluded from the coverage gate (see ``tool.coverage.run.omit``) because it skips where they are absent.
"""

from __future__ import annotations

import pytest

from turbohtml.convert import css_to_xpath

pytest.importorskip("cssselect")
lxml_html = pytest.importorskip("lxml.html")

_HTML = (
    "<html><body>"
    "<div class='a'><p id='x'>1</p><p class='note'>2</p><p>3</p></div>"
    "<div class='b'><a href='/y'>y</a><a>z</a></div>"
    "<ul><li>1</li><li>2</li><li>3</li><li>4</li></ul>"
    "</body></html>"
)


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("div.a p", id="descendant"),
        pytest.param("div > p", id="child"),
        pytest.param("p#x", id="id"),
        pytest.param("a[href]", id="attr-exists"),
        pytest.param("div.a p:first-child", id="first-child"),
        pytest.param("li:nth-child(2n+1)", id="nth-odd"),
        pytest.param("p:not(.note)", id="not-class"),
        pytest.param("p + p", id="adjacent"),
        pytest.param("div.a, div.b a", id="group"),
    ],
)
def test_translation_matches_lxml(selector: str) -> None:
    """libxml2 over the translated XPath selects the same elements lxml's cssselect does."""
    tree = lxml_html.fromstring(_HTML)
    by_cssselect = tree.cssselect(selector)
    by_translated = tree.xpath(css_to_xpath(selector))
    assert by_translated == by_cssselect
