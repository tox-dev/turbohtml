"""opengraph: extract.opengraph / Node.opengraph(), the OpenGraph mapping, is_valid and URL resolution.

The function entry point and the DOM method feed the same reader; the function cases pin the mapping
protocol (getitem, repr, is_valid) while the parametrized method cases pin the tag-selection and
base-URL rules.
"""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml.extract import OpenGraph, opengraph

_FULL = (
    '<meta property="og:title" content="The Rock">'
    '<meta property="og:type" content="video.movie">'
    '<meta property="og:image" content="http://x/rock.jpg">'
    '<meta property="og:url" content="http://x/tt0117500/">'
)


def test_opengraph_strips_prefix() -> None:
    assert opengraph('<meta property="og:title" content="Hello">') == {"title": "Hello"}


def test_opengraph_drops_twitter_tags() -> None:
    html = '<meta property="og:title" content="Hi"><meta name="twitter:card" content="summary">'
    assert dict(opengraph(html)) == {"title": "Hi"}


def test_opengraph_keeps_structured_subkeys() -> None:
    html = '<meta property="og:image" content="http://x/a.png"><meta property="og:image:width" content="400">'
    assert opengraph(html) == {"image": "http://x/a.png", "image:width": "400"}


def test_opengraph_empty_without_tags() -> None:
    assert dict(opengraph("<p>plain</p>")) == {}


def test_getitem_reads_stripped_key() -> None:
    assert opengraph('<meta property="og:title" content="Hello">')["title"] == "Hello"


def test_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        _ = opengraph('<meta property="og:title" content="Hello">')["type"]


def test_membership_and_get() -> None:
    og = opengraph('<meta property="og:title" content="Hello">')
    assert "title" in og
    assert og.get("missing") is None


def test_repr_wraps_properties() -> None:
    assert repr(opengraph('<meta property="og:title" content="Hi">')) == "OpenGraph({'title': 'Hi'})"


def test_is_valid_with_all_four_required() -> None:
    assert opengraph(_FULL).is_valid() is True


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<meta property="og:title" content="only">', id="one-property"),
        pytest.param("<p>no meta</p>", id="no-properties"),
    ],
)
def test_is_valid_false_when_required_missing(html: str) -> None:
    assert opengraph(html).is_valid() is False


def test_is_valid_false_when_required_empty() -> None:
    html = _FULL + '<meta property="og:url" content="">'
    assert opengraph(html).is_valid() is False


def test_direct_construction() -> None:
    og = OpenGraph({"title": "Hi", "type": "article", "image": "i", "url": "u"})
    assert og.is_valid() is True
    assert len(og) == 4


def test_opengraph_function_resolves_url_against_base() -> None:
    html = '<meta property="og:image" content="/rock.jpg"><meta property="og:title" content="R">'
    assert opengraph(html, "http://x.com/dir/") == {"image": "http://x.com/rock.jpg", "title": "R"}


def test_opengraph_function_base_url_omitted_is_verbatim() -> None:
    assert opengraph('<meta property="og:image" content="/rock.jpg">') == {"image": "/rock.jpg"}


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
def test_opengraph_document_method(html: str, expected: dict[str, str]) -> None:
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
