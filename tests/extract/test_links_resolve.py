from __future__ import annotations

import pytest

from turbohtml import parse, parse_fragment

_BASE = "https://example.com/dir/page.html"


def _resolved(html: str, base: str = _BASE) -> str:
    root = parse_fragment(html)
    root.resolve_links(base)
    return root.inner_html


def test_relative_href_becomes_absolute() -> None:
    assert _resolved('<a href="a/b.html">x</a>') == '<a href="https://example.com/dir/a/b.html">x</a>'


def test_root_relative_href_resolves_against_origin() -> None:
    assert _resolved('<a href="/top.html">x</a>') == '<a href="https://example.com/top.html">x</a>'


def test_absolute_href_is_left_unchanged() -> None:
    assert _resolved('<a href="https://other.test/x">y</a>') == '<a href="https://other.test/x">y</a>'


def test_fragment_only_href_resolves_against_the_page() -> None:
    assert _resolved('<a href="#sec">y</a>') == '<a href="https://example.com/dir/page.html#sec">y</a>'


def test_srcset_candidates_are_each_resolved() -> None:
    out = _resolved('<img srcset="a.png 1x, b.png 2x">')
    assert out == '<img srcset="https://example.com/dir/a.png 1x, https://example.com/dir/b.png 2x">'


def test_ping_list_entries_are_each_resolved() -> None:
    out = _resolved('<a href="h" ping="p1 p2">x</a>')
    assert 'ping="https://example.com/dir/p1 https://example.com/dir/p2"' in out


def test_css_url_in_style_attribute_is_resolved() -> None:
    out = _resolved('<p style="background:url(bg.png)"></p>')
    assert out == '<p style="background:url(https://example.com/dir/bg.png)"></p>'


def test_css_url_in_style_element_is_resolved() -> None:
    root = parse_fragment("<style>a{background:url(bg.png)} @import 'theme.css';</style>")
    root.resolve_links(_BASE)
    assert root.inner_html == (
        "<style>a{background:url(https://example.com/dir/bg.png)} @import 'https://example.com/dir/theme.css';</style>"
    )


def test_meta_refresh_url_is_resolved() -> None:
    out = _resolved('<meta http-equiv="refresh" content="5; url=next.html">')
    assert out == '<meta http-equiv="refresh" content="5; url=https://example.com/dir/next.html">'


def test_resolve_on_a_whole_document_returns_none() -> None:
    doc = parse('<html><body><a href="a.html">x</a></body></html>')
    assert doc.resolve_links(_BASE) is None
    (link,) = doc.links()
    assert link.url == "https://example.com/dir/a.html"


def test_a_longer_replacement_grows_the_value() -> None:
    out = _resolved('<a href="x">t</a>', "https://example.com/very/deep/path/")
    assert out == '<a href="https://example.com/very/deep/path/x">t</a>'


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<a href="">x</a>', id="empty-href"),
        pytest.param("<style></style>", id="empty-style"),
        pytest.param('<meta http-equiv="refresh" content="5">', id="refresh-no-url"),
    ],
)
def test_nothing_to_resolve_is_a_no_op(html: str) -> None:
    assert _resolved(html) == html


def test_resolve_links_requires_a_string_base() -> None:
    with pytest.raises(TypeError, match="base URL string"):
        parse_fragment('<a href="a">x</a>').resolve_links(None)  # ty: ignore[invalid-argument-type]
