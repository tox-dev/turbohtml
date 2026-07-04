"""opengraph: the extract.opengraph entry point and the OpenGraph mapping with its is_valid check."""

from __future__ import annotations

import pytest

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


def test_opengraph_resolves_url_against_base() -> None:
    html = '<meta property="og:image" content="/rock.jpg"><meta property="og:title" content="R">'
    assert opengraph(html, "http://x.com/dir/") == {"image": "http://x.com/rock.jpg", "title": "R"}


def test_opengraph_base_url_omitted_is_verbatim() -> None:
    assert opengraph('<meta property="og:image" content="/rock.jpg">') == {"image": "/rock.jpg"}
