from __future__ import annotations

from turbohtml import parse


def test_opengraph_duplicate_url_prefers_first_with_base() -> None:
    html = '<meta property="og:image" content="/one.png"><meta property="og:image" content="/two.png">'
    assert parse(html).opengraph(base_url="https://example.org/page")["image"] == "https://example.org/one.png"


def test_opengraph_duplicate_empty_first_value_is_preserved() -> None:
    html = '<meta property="og:title" content=""><meta property="og:title" content="fallback">'
    assert parse(html).opengraph() == {"title": ""}


def test_structured_data_duplicate_twitter_key_keeps_last() -> None:
    html = '<meta name="twitter:card" content="summary"><meta name="twitter:card" content="summary_large_image">'
    assert parse(html).structured_data().opengraph["twitter:card"] == "summary_large_image"
