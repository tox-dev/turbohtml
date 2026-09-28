from __future__ import annotations

from html import escape
from typing import Final

import pytest

from turbohtml import parse_fragment
from turbohtml.clean import Policy, sanitize
from turbohtml.migration.bleach import clean


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("http://[", None, id="open"),
        pytest.param("http://host]", None, id="close"),
        pytest.param("https://[host/path", None, id="before-path"),
        pytest.param("http://[host?query]", None, id="query-cannot-close"),
        pytest.param("http://[host#fragment]", None, id="fragment-cannot-close"),
        pytest.param("http://[host/path]", None, id="path-cannot-close"),
        pytest.param("//[", None, id="relative-authority-open"),
        pytest.param("//host]", None, id="relative-authority-close"),
        pytest.param("http://user[@host", None, id="userinfo-open"),
        pytest.param("http://user]@host", None, id="userinfo-close"),
        pytest.param(" \t\r\nHTTP://[", None, id="leading-whitespace"),
        pytest.param("\x01\x1fhttp://[", None, id="leading-controls"),
        pytest.param("h\tt\nt\rp://[", None, id="scheme-controls"),
        pytest.param("http:\t/\n/[", None, id="authority-controls"),
        pytest.param(" \t//[", None, id="relative-leading-whitespace"),
        pytest.param("http://[]", "http://[]", id="empty-literal"),
        pytest.param("http://[bad]", "http://[bad]", id="hostname-literal"),
        pytest.param("http://[::1]", "http://[::1]", id="ipv6"),
        pytest.param("http://x[y]", "http://x[y]", id="embedded-brackets"),
        pytest.param("http://]@[host", "http://]@[host", id="userinfo-pairs-host"),
        pytest.param("http://[user]@host", "http://[user]@host", id="userinfo-paired"),
        pytest.param("//[bad]", "//[bad]", id="relative-authority-paired"),
        pytest.param("http://host/path[", "http://host/path[", id="path"),
        pytest.param("http://host?query[", "http://host?query[", id="query"),
        pytest.param("http://host#fragment[", "http://host#fragment[", id="fragment"),
        pytest.param("/path[", "/path[", id="relative-path"),
        pytest.param("?query[", "?query[", id="relative-query"),
        pytest.param("#fragment[", "#fragment[", id="relative-fragment"),
        pytest.param("relative[", "relative[", id="plain-relative"),
        pytest.param("http:/[", "http:/[", id="single-slash"),
        pytest.param("http:", "http:", id="scheme-only"),
        pytest.param("http:/", "http:/", id="scheme-and-slash"),
        pytest.param("http://", "http://", id="empty-authority"),
        pytest.param("https[://host", "https[://host", id="invalid-scheme"),
        pytest.param("mailto:user[", "mailto:user[", id="opaque"),
    ],
)
def test_clean_url_authority(value: str, expected: str | None) -> None:
    result: Final = clean(f'<a href="{escape(value, quote=True)}">x</a>')
    assert dict(parse_fragment(result).select("a")[0].attrs) == ({} if expected is None else {"href": expected})


@pytest.mark.parametrize("attribute", ["href", "src", "srcset", "imagesrcset"])
@pytest.mark.parametrize("source", ["input", "rewrite", "forced"])
def test_sanitize_url_authority_after_attribute_changes(attribute: str, source: str) -> None:
    value: Final = "safe.png 1x, http://[ 2x" if attribute.endswith("srcset") else "http://["
    policy: Final = Policy(
        tags=frozenset({"x"}),
        attributes={"x": frozenset({attribute})},
        attribute_filter=(lambda _tag, _name, _value: value) if source == "rewrite" else None,
        set_attributes={"x": {attribute: value}} if source == "forced" else {},
    )
    original: Final = value if source == "input" else "https://safe.example/"
    result: Final = sanitize(f'<x {attribute}="{original}"></x>', policy)
    assert dict(parse_fragment(result).select("x")[0].attrs) == {}


def test_sanitize_data_url_brackets() -> None:
    value: Final = "data:text/plain," + "[" * 100_000
    policy: Final = Policy(
        tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}, url_schemes=frozenset({"data"})
    )
    result: Final = sanitize(f'<a href="{value}">x</a>', policy)
    assert dict(parse_fragment(result).select("a")[0].attrs) == {"href": value}
