"""Conformance tests for the C percent-coders behind :mod:`turbohtml.extract._urls`.

``_url_percent_encode`` and ``_url_percent_decode`` apply the WHATWG component percent-encode sets (URL standard 1.3)
and their inverse. They coincide with ``urllib.parse.quote``/``unquote`` for every input the spec pins, so the bulk of
the coverage is a differential against urllib over a generated corpus; the encoder additionally uppercases an existing
``%XX`` escape the way the canonical URL form does (RFC 3986 6.2.2.1), so the oracle applies that same sweep first.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

import pytest

from turbohtml._html import _url_percent_decode, _url_percent_encode

_PATH, _QUERY, _FRAGMENT = 0, 1, 2

# The safe run each set keeps raw, mirroring URL_PATH_KEEP / URL_QUERY_KEEP / URL_FRAGMENT_KEEP in _c/url/url.c; the
# unreserved characters quote() always keeps are implicit, so only the set-specific additions are listed.
_SAFE: dict[int, str] = {
    _PATH: "!$%&'()*+,-./:;=@[\\]^_|~",
    _QUERY: "!$%&()*+,-./:;=?@[\\]^`{|}~",
    _FRAGMENT: "!#$%&'()*+,-./:;=?@[\\]^_{|}~",
}
_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


def _oracle_encode(text: str, url_set: int) -> str:
    uppercased = _ESCAPE.sub(lambda match: match[0].upper(), text) if "%" in text else text
    return quote(uppercased, safe=_SAFE[url_set])


# A spread that reaches every encode/decode branch: kept and out-of-set bytes, lowercase and already-uppercase escapes,
# both malformed-escape shapes, a truncated escape at the very end, a '%' before a non-ASCII byte, and multi-byte input.
_CORPUS = (
    "",
    "a",
    "abc-._~",
    " ",
    "a b/c",
    '<>"`',
    "%2f",
    "%2F",
    "%c3%a9",
    "abc%2edef",
    "%2g",
    "%g0",
    "%",
    "a%",
    "%2",
    "100%done",
    "a%%41b",
    "é",
    "%é",
    "münchen",
    "日本語",
    "\U0001f600",
    "!$&'()*+,;=:@[]^|{}?#`~",
    "a=1&b=2",
    "\x00\x01\x1f",
)


@pytest.mark.parametrize(
    "url_set",
    [pytest.param(_PATH, id="path"), pytest.param(_QUERY, id="query"), pytest.param(_FRAGMENT, id="fragment")],
)
def test_percent_encode_matches_urllib_over_corpus(url_set: int) -> None:
    for text in _CORPUS:
        assert _url_percent_encode(text, url_set) == _oracle_encode(text, url_set), text


def test_percent_decode_matches_urllib_over_corpus() -> None:
    for text in _CORPUS:
        assert _url_percent_decode(text) == unquote(text), text


@pytest.mark.parametrize(
    ("text", "url_set", "expected"),
    [
        pytest.param("", _PATH, "", id="empty"),
        pytest.param("abc", _PATH, "abc", id="all-kept"),
        pytest.param("a b", _PATH, "a%20b", id="space-encoded"),
        pytest.param("%2f", _PATH, "%2F", id="lowercase-escape-uppercased"),
        pytest.param("%2F", _PATH, "%2F", id="uppercase-escape-kept"),
        pytest.param("%2g", _PATH, "%2g", id="second-non-hex-is-literal"),
        pytest.param("%g0", _PATH, "%g0", id="first-non-hex-is-literal"),
        pytest.param("a%", _PATH, "a%", id="trailing-percent-no-room"),
        pytest.param("%2", _PATH, "%2", id="truncated-escape"),
        pytest.param("é", _PATH, "%C3%A9", id="non-ascii-utf8"),
        pytest.param("%é", _PATH, "%%C3%A9", id="percent-before-non-ascii"),
        pytest.param("a'b", _QUERY, "a%27b", id="query-drops-apostrophe"),
        pytest.param("a'b", _FRAGMENT, "a'b", id="fragment-keeps-apostrophe"),
        pytest.param("a`b", _QUERY, "a`b", id="query-keeps-backtick"),
        pytest.param("a`b", _FRAGMENT, "a%60b", id="fragment-drops-backtick"),
    ],
)
def test_percent_encode_cases(text: str, url_set: int, expected: str) -> None:
    assert _url_percent_encode(text, url_set) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("abc", "abc", id="no-escape"),
        pytest.param("%2F", "/", id="valid-escape"),
        pytest.param("%2f", "/", id="lowercase-escape"),
        pytest.param("%c3%a9", "é", id="utf8-two-byte"),
        pytest.param("%zz", "%zz", id="invalid-escape-literal"),
        pytest.param("%g0", "%g0", id="first-non-hex-literal"),
        pytest.param("a%", "a%", id="trailing-percent"),
        pytest.param("%2", "%2", id="truncated-escape"),
        pytest.param("%80", "�", id="lone-continuation-replaced"),
        pytest.param("é%41", "éA", id="non-ascii-then-escape"),
        pytest.param("%41é", "Aé", id="escape-then-non-ascii"),
        pytest.param("%é", "%é", id="percent-before-non-ascii"),
    ],
)
def test_percent_decode_cases(text: str, expected: str) -> None:
    assert _url_percent_decode(text) == expected


def test_percent_encode_lone_surrogate_raises() -> None:
    # a lone surrogate has no UTF-8 form, so it cannot be percent-encoded; the shim rewraps this as ValueError
    with pytest.raises(UnicodeEncodeError):
        _url_percent_encode("a\udc00b", _PATH)


def test_percent_decode_lone_surrogate_passes_through() -> None:
    # unquote leaves a raw non-ASCII code point untouched, splitting the ASCII runs around it, and so does this coder
    assert _url_percent_decode("a\udc00%41b") == "a\udc00Ab"


def test_percent_encode_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _url_percent_encode(123, _PATH)  # ty: ignore[invalid-argument-type]  # text must be a str
