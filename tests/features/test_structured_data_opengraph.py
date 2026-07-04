from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml._structured_data import OpenGraph


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta property="og:title" content="Hello"><meta property="og:type" content="article">',
            {"title": "Hello", "type": "article"},
            id="opengraph-via-property",
        ),
        pytest.param(
            '<meta name="twitter:card" content="summary"><meta name="twitter:site" content="@x">',
            {},
            id="twitter-keys-dropped",
        ),
        pytest.param('<meta name="og:title" content="By name">', {"title": "By name"}, id="og-via-name"),
        pytest.param('<meta property="twitter:card" content="summary">', {}, id="twitter-via-property-dropped"),
        pytest.param(
            '<meta property="og:title" name="twitter:title" content="P">',
            {"title": "P"},
            id="property-wins-over-name",
        ),
        pytest.param(
            '<meta property="og:title" content="Hi"><meta name="twitter:card" content="c">',
            {"title": "Hi"},
            id="og-kept-twitter-dropped",
        ),
        pytest.param(
            '<meta property="description" name="og:title" content="N">',
            {"title": "N"},
            id="name-used-when-property-not-social",
        ),
        pytest.param(
            '<meta property name="og:title" content="N">',
            {"title": "N"},
            id="name-used-when-property-valueless",
        ),
        pytest.param('<meta property="og:title">', {"title": ""}, id="missing-content-empty-string"),
        pytest.param('<meta property="og:title" content>', {"title": ""}, id="valueless-content-empty-string"),
        pytest.param(
            '<meta property="og:image" content="a"><meta property="og:image:width" content="400">',
            {"image": "a", "image:width": "400"},
            id="structured-subkey-kept",
        ),
        pytest.param(
            '<meta property="og:title" content="first"><meta property="og:title" content="second">',
            {"title": "second"},
            id="last-occurrence-wins",
        ),
        pytest.param('<meta name="description" content="x">', {}, id="non-social-name"),
        pytest.param('<meta property="article:author" content="x">', {}, id="non-social-property"),
        pytest.param('<meta property="x" content="y">', {}, id="property-shorter-than-prefix"),
        pytest.param('<meta charset="utf-8">', {}, id="no-property-or-name"),
        pytest.param('<meta property name content="x">', {}, id="both-valueless"),
        pytest.param("<p>not a meta</p>", {}, id="no-meta"),
    ],
)
def test_opengraph(html: str, expected: dict[str, str]) -> None:
    assert parse(html).opengraph() == expected


def test_opengraph_returns_record() -> None:
    result = parse('<meta property="og:title" content="Hi">').opengraph()
    assert isinstance(result, OpenGraph)
    assert result["title"] == "Hi"


def test_opengraph_keeps_twitter_in_structured_data_but_drops_it_in_record() -> None:
    html = '<meta property="og:title" content="T"><meta name="twitter:card" content="summary">'
    assert parse(html).structured_data().opengraph == {"og:title": "T", "twitter:card": "summary"}
    assert parse(html).opengraph() == {"title": "T"}


_BASE = "http://ex.com/dir/"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("og:url", id="og-url"),
        pytest.param("og:image", id="og-image"),
        pytest.param("og:image:url", id="og-image-url"),
        pytest.param("og:image:secure_url", id="og-image-secure-url"),
        pytest.param("og:audio", id="og-audio"),
        pytest.param("og:audio:url", id="og-audio-url"),
        pytest.param("og:audio:secure_url", id="og-audio-secure-url"),
        pytest.param("og:video", id="og-video"),
        pytest.param("og:video:url", id="og-video-url"),
        pytest.param("og:video:secure_url", id="og-video-secure-url"),
    ],
)
def test_opengraph_resolves_url_valued_key(key: str) -> None:
    html = f'<meta property="{key}" content="/a.png">'
    assert parse(html).opengraph(base_url=_BASE) == {key[len("og:") :]: "http://ex.com/a.png"}


@pytest.mark.parametrize(
    "key",
    [
        pytest.param("twitter:image", id="twitter-image"),
        pytest.param("twitter:image:src", id="twitter-image-src"),
        pytest.param("twitter:player", id="twitter-player"),
        pytest.param("twitter:player:stream", id="twitter-player-stream"),
    ],
)
def test_opengraph_drops_twitter_url_valued_key(key: str) -> None:
    html = f'<meta property="{key}" content="/a.png"><meta property="og:title" content="T">'
    assert parse(html).opengraph(base_url=_BASE) == {"title": "T"}


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta property="og:image" content="a.png"><meta property="og:title" content="a.png">',
            {"image": "http://ex.com/dir/a.png", "title": "a.png"},
            id="only-url-key-resolved-not-same-length-string-key",
        ),
        pytest.param(
            '<meta property="og:image" content="http://cdn.com/x.png">',
            {"image": "http://cdn.com/x.png"},
            id="absolute-url-kept",
        ),
        pytest.param('<meta property="og:image" content>', {"image": ""}, id="empty-url-stays-empty"),
        pytest.param(
            '<meta property="og:Image" content="/a.png">',
            {"Image": "http://ex.com/a.png"},
            id="key-case-insensitive",
        ),
        pytest.param(
            '<meta property="og:image" content="http://[bad">',
            {"image": "http://[bad"},
            id="malformed-value-kept-verbatim",
        ),
    ],
)
def test_opengraph_resolution_cases(html: str, expected: dict[str, str]) -> None:
    assert parse(html).opengraph(base_url=_BASE) == expected


def test_opengraph_base_href_refines_base_url() -> None:
    html = '<base href="http://b.com/sub/"><meta property="og:image" content="a.png">'
    assert parse(html).opengraph(base_url="http://ex.com/") == {"image": "http://b.com/sub/a.png"}


def test_opengraph_malformed_base_href_falls_back_to_base_url() -> None:
    html = '<base href="http://[bad"><meta property="og:image" content="a.png">'
    assert parse(html).opengraph(base_url="http://ex.com/d/") == {"image": "http://ex.com/d/a.png"}


def test_opengraph_base_url_none_is_verbatim() -> None:
    html = '<meta property="og:image" content="/a.png">'
    assert parse(html).opengraph(base_url=None) == {"image": "/a.png"}


def test_opengraph_malformed_base_url_raises() -> None:
    with pytest.raises(ValueError, match="not a valid absolute URL"):
        parse('<meta property="og:image" content="/a.png">').opengraph(base_url="http://[bad")


def test_opengraph_base_url_wrong_type_raises() -> None:
    with pytest.raises(TypeError, match="base_url must be a str or None"):
        parse("<p>x</p>").opengraph(base_url=123)  # ty: ignore[invalid-argument-type]  # non-str exercises TypeError


def test_opengraph_rejects_unexpected_argument() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").opengraph(nope=1)  # ty: ignore[unknown-argument]  # exercises the argument-parse failure


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            '<meta name="dc.title" content="Hi"><meta name="dc.creator" content="Ada">',
            {"dc.title": "Hi", "dc.creator": "Ada"},
            id="dc-names",
        ),
        pytest.param('<meta name="dcterms.created" content="2020">', {"dcterms.created": "2020"}, id="dcterms-name"),
        pytest.param('<meta name="DC.Title" content="Hi">', {"dc.title": "Hi"}, id="name-lower-cased"),
        pytest.param(
            '<meta name="dc.title" content="first"><meta name="dc.title" content="second">',
            {"dc.title": "second"},
            id="last-occurrence-wins",
        ),
        pytest.param('<meta name="dc.title">', {"dc.title": ""}, id="missing-content-empty-string"),
        pytest.param('<meta name="dc.title" content>', {"dc.title": ""}, id="valueless-content-empty-string"),
        pytest.param('<meta name="keywords" content="x">', {}, id="non-dc-name-ignored"),
        pytest.param('<meta name="dc" content="x">', {}, id="name-shorter-than-prefix-ignored"),
        pytest.param('<meta name="dcx.title" content="x">', {}, id="near-miss-prefix-ignored"),
        pytest.param('<meta property="dc.title" content="x">', {}, id="property-not-name-ignored"),
        pytest.param('<meta name content="x">', {}, id="valueless-name-ignored"),
        pytest.param('<meta content="x">', {}, id="no-name-ignored"),
        pytest.param("<p>not a meta</p>", {}, id="no-meta"),
    ],
)
def test_dublin_core(html: str, expected: dict[str, str]) -> None:
    assert parse(html).dublin_core() == expected
