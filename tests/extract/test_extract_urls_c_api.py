"""The URL cleaner's C entry points: the argument contract and the paths the public options do not reach."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import parse
from turbohtml._html import _url_clean, _url_normalize
from turbohtml.extract import UrlCleaning, clean_url, extract_links, normalize_url

if TYPE_CHECKING:
    from collections.abc import Callable

_KNOBS: Final = (False, True, False, None, frozenset(), frozenset({"id"}), frozenset({"lang"}))


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:-1]), id="normalize-too-few"),
        pytest.param(_url_clean, ("http://x.com", *_KNOBS), id="clean-too-few"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:3], 5, *_KNOBS[4:]), id="allow-not-iterable"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:]), id="allow-holds-an-int"),
        pytest.param(_url_normalize, ("http://x.com", *_KNOBS[:4], {1}, *_KNOBS[5:]), id="deny-holds-an-int"),
        pytest.param(
            _url_clean,
            ("http://x.com", *_KNOBS[:3], {1}, *_KNOBS[4:], None, frozenset()),
            id="clean-allow-holds-an-int",
        ),
    ],
)
def test_the_entry_points_reject_bad_arguments(entry: Callable[..., object], args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        entry(*args)


def test_extract_links_rejects_a_parameter_name_that_is_not_a_str() -> None:
    with pytest.raises(TypeError, match="query parameter names must be str"):
        extract_links("", "https://a.com/", UrlCleaning(query_deny=frozenset({1})))  # ty: ignore[invalid-argument-type]


def test_the_document_entry_point_rejects_too_few_arguments() -> None:
    external_only = False
    with pytest.raises(TypeError):
        parse("")._extract_links(None, external_only)  # ty: ignore[missing-argument]  # the arity check is the point


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://x.com:443/", "http://x.com:443/", id="another-scheme-default-is-kept"),
        pytest.param(
            "http://x.com:99999999999999999999/", "http://x.com:99999999999999999999/", id="an-overflowing-port"
        ),
        pytest.param("http://x.com:0080/", "http://x.com/", id="leading-zeros-read-as-the-default"),
        pytest.param("http://x.com:0081/", "http://x.com:81/", id="leading-zeros-fall-away"),
        pytest.param("ftp://x.com", "ftp://x.com", id="a-non-web-scheme-keeps-its-empty-path"),
        pytest.param("http:/x", "http:///x", id="a-netloc-scheme-without-an-authority-gets-the-marker"),
        pytest.param("http:x", "http:x", id="a-netloc-scheme-with-a-relative-path-keeps-its-shape"),
        pytest.param("http:", "http://", id="a-netloc-scheme-alone-gets-the-marker"),
        pytest.param("mailto:x@y.com", "mailto:x@y.com", id="an-opaque-scheme"),
        pytest.param("/a/b?utm_source=x#top", "/a/b#top", id="a-relative-reference"),
        pytest.param("////x", "////x", id="an-empty-authority-before-a-double-slash-path"),
    ],
)
def test_normalize_reassembles_every_shape(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("http://x.com//", "http://x.com", id="a-slash-run-is-trimmed-whole"),
        pytest.param("http://x.com/", "http://x.com/", id="the-root-stays"),
        pytest.param("http://x.com/a", "http://x.com/a", id="no-slash-to-trim"),
    ],
)
def test_trailing_slash_off(url: str, expected: str) -> None:
    assert normalize_url(url, UrlCleaning(trailing_slash=False)) == expected


def test_a_fragment_key_with_a_lone_surrogate_cannot_be_encoded() -> None:
    with pytest.raises(UnicodeEncodeError):
        normalize_url("http://x.com/#\ud800=1")


def test_clean_drops_a_fragment_key_with_a_lone_surrogate() -> None:
    assert clean_url("http://x.com/#\ud800=1") is None


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<a href=" https://test.com/a\t">x</a>', {"https://test.com/a"}, id="href-is-trimmed"),
        pytest.param(
            '<a href="https://test.com/a" rel="noopener">x</a>', {"https://test.com/a"}, id="an-eight-letter-rel"
        ),
        pytest.param(
            '<a href="https://test.com/a" rel="\xa0nofollow\xa0\xfcgc ">x</a>', set(), id="unicode-spaced-nofollow"
        ),
        pytest.param('<a href="https://test.com/a" rel="ugc\tNOFOLLOW">x</a>', set(), id="tab-separated-nofollow"),
        pytest.param('<a href="https://test.com/a" rel="ugc ">x</a>', {"https://test.com/a"}, id="rel-ends-in-a-space"),
    ],
)
def test_anchor_attributes_are_read_the_way_html_defines_them(html: str, expected: set[str]) -> None:
    assert extract_links(html, "https://test.com/") == expected


def test_a_longer_hreflang_subtag_is_another_language() -> None:
    html = '<a href="https://test.com/a" hreflang="deu">x</a>'
    assert extract_links(html, "https://test.com/", UrlCleaning(language="de")) == set()


def test_an_unsplittable_base_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<a href="https://a.example/">x</a>', "http://[::1", external_only=True)


def test_a_base_element_that_cannot_resolve_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<base href="http://[::1"><a href="p">x</a>', "https://a.com/")


def test_a_relative_href_against_an_unsplittable_base_element_raises() -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        extract_links('<base href="http://[::1"><a href="p">x</a>')
