from __future__ import annotations

import itertools
import random
import re
from functools import partial
from typing import TYPE_CHECKING

import pytest

from turbohtml import parse
from turbohtml._html import (
    _url_is_tracker,
    _url_join,
    _url_language_matches,
    _url_normalize_query,
    _url_percent_decode,
    _url_percent_encode,
    _url_remove_dot_segments,
)

if TYPE_CHECKING:
    from collections.abc import Callable
from urllib.parse import quote, unquote, urlsplit

from turbohtml._html import _url_scrub, _url_split, _url_variant_key


@pytest.mark.parametrize(
    ("html", "fallback", "expected"),
    [
        pytest.param('<base href="/sub/page">', "http://site.com/dir/", "http://site.com/sub/page", id="relative-base"),
        pytest.param('<base href="http://abs.com/x">', "http://site.com/", "http://abs.com/x", id="absolute-base"),
        pytest.param("<p>no base</p>", "http://site.com/", "http://site.com/", id="no-base"),
        pytest.param("<base>", "http://site.com/", "http://site.com/", id="valueless-href"),
        pytest.param('<base href="  ">', "http://site.com/", "http://site.com/", id="blank-href"),
    ],
)
def test_base_url(html: str, fallback: str, expected: str) -> None:
    assert parse(html).base_url(fallback) == expected


def test_base_url_default_fallback() -> None:
    assert parse('<base href="page">').base_url() == "page"


@pytest.mark.parametrize(
    ("href", "fallback", "expected"),
    [
        pytest.param(
            "http://example.org/sterling£", "https://example.org", "http://example.org/sterling%C2%A3", id="latin1-path"
        ),
        pytest.param(
            "http://x/p q?a=b c&e=f'#h i",
            "http://x",
            "http://x/p%20q?a=b%20c&e=f%27#h%20i",
            id="all-components-encoded",
        ),
        pytest.param("http://h?a=b c", "http://x", "http://h?a=b%20c", id="authority-then-query"),
        pytest.param("http://h#f g", "http://x", "http://h#f%20g", id="authority-then-fragment"),
        pytest.param("http://h/p#f g", "http://x", "http://h/p#f%20g", id="fragment-without-query"),
        pytest.param("http://h", "http://x", "http://h", id="authority-only"),
        pytest.param("ab/cd", "", "ab/cd", id="relative-without-scheme"),
        pytest.param("/x", "", "/x", id="protocol-relative-root"),
        pytest.param("http://x/a%20b?c=%7e", "http://x", "http://x/a%20b?c=%7e", id="existing-escapes-untouched"),
        pytest.param(
            "rel", "http://x/\U0001f389\udce9/", "http://x/%F0%9F%8E%89%EF%BF%BD/rel", id="high-char-and-surrogate"
        ),
    ],
)
def test_base_url_percent_encodes(href: str, fallback: str, expected: str) -> None:
    # the resolved base URL applies the WHATWG path/query/fragment percent-encode sets, so it is a valid URL string
    assert parse(f'<base href="{href}">').base_url(fallback) == expected


_SITE = "http://s.com/"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("5; url=next", (5.0, _SITE + "next"), id="delay-and-url"),
        pytest.param("10", (10.0, _SITE), id="delay-only"),
        pytest.param("2.5;url=x", (2.5, _SITE + "x"), id="float-delay"),
        pytest.param("2.5", (2.5, _SITE), id="float-delay-only"),
        pytest.param("5;url=", (5.0, _SITE), id="url-prefix-empty-value"),
        pytest.param("5;url=£x", (5.0, _SITE + "%C2%A3x"), id="two-byte-utf8-encoded"),
        pytest.param("5;url=€page", (5.0, _SITE + "%E2%82%ACpage"), id="three-byte-utf8-encoded"),
        pytest.param("5;url=\U0001f389", (5.0, _SITE + "%F0%9F%8E%89"), id="four-byte-utf8-encoded"),
        pytest.param("  7 ; url = y ", (7.0, _SITE + "y"), id="surrounding-whitespace"),
        pytest.param("3;url='q'", (3.0, _SITE + "q"), id="single-quoted-url"),
        pytest.param("8, z", (8.0, _SITE + "z"), id="comma-separator-no-prefix"),
        pytest.param("9;next", (9.0, _SITE + "next"), id="no-url-prefix"),
        pytest.param("1;urlx", (1.0, _SITE + "urlx"), id="url-prefix-without-equals"),
        pytest.param("5;uxxx", (5.0, _SITE + "uxxx"), id="u-without-rl-prefix"),
        pytest.param("5;urxx", (5.0, _SITE + "urxx"), id="ur-without-l-prefix"),
        pytest.param("5;url", (5.0, _SITE + "url"), id="bare-url-keyword-no-equals"),
        pytest.param("5;url='", (5.0, _SITE + "'"), id="lone-quote-after-url"),
        pytest.param("6;", (6.0, _SITE), id="separator-without-url"),
        pytest.param("2;ab", (2.0, _SITE + "ab"), id="short-url-after-separator"),
        pytest.param("0;url='unbalanced", (0.0, _SITE + "'unbalanced"), id="unbalanced-quote-kept"),
        pytest.param("0;url=a:b", (0.0, "a:b"), id="opaque-scheme-no-authority"),
    ],
)
def test_meta_refresh_content(content: str, expected: tuple[float, str]) -> None:
    assert parse(f'<meta http-equiv=refresh content="{content}">').meta_refresh(_SITE) == expected


def test_base_url_rejects_non_str_fallback() -> None:
    with pytest.raises(TypeError):
        parse("<base href=x>").base_url(123)  # ty: ignore[invalid-argument-type]  # non-str exercises the TypeError path


def test_meta_refresh_rejects_non_str_fallback() -> None:
    with pytest.raises(TypeError):
        parse("<meta http-equiv=refresh content='5'>").meta_refresh(123)  # ty: ignore[invalid-argument-type]  # non-str


def test_meta_refresh_double_quoted_url() -> None:
    # a single-quoted attribute lets the url use double quotes
    html = "<meta http-equiv=refresh content='4;url=\"d\"'>"
    assert parse(html).meta_refresh(_SITE) == (4.0, _SITE + "d")


def test_meta_refresh_unresolvable_url_raises() -> None:
    html = '<meta http-equiv=refresh content="0;url=//[bad">'
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        parse(html).meta_refresh(_SITE)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<meta http-equiv=Refresh content="5;url=x">', (5.0, "x"), id="mixed-case-equiv"),
        pytest.param('<meta http-equiv="  refresh  " content="5;url=x">', (5.0, "x"), id="padded-equiv"),
        pytest.param(
            '<meta charset=utf-8><meta http-equiv=refresh content="7;url=z">', (7.0, "z"), id="skips-other-meta"
        ),
        pytest.param('<meta http-equiv=other content="8;url=z">', None, id="non-refresh-equiv"),
        pytest.param('<meta http-equiv=refresx content="9;url=z">', None, id="seven-char-non-refresh-equiv"),
        pytest.param('<meta content="5;url=z">', None, id="meta-without-http-equiv"),
        pytest.param("<meta http-equiv=refresh>", None, id="refresh-without-content"),
        pytest.param('<meta http-equiv=refresh content="abc">', None, id="no-leading-number"),
        pytest.param('<meta http-equiv=refresh content="   ">', None, id="all-whitespace-content"),
        pytest.param('<meta http-equiv="   " content="5;url=x">', None, id="all-whitespace-equiv"),
        pytest.param("<p>nothing</p>", None, id="no-meta"),
        pytest.param(
            '<noscript><meta http-equiv=refresh content="5;url=x"></noscript>', None, id="ignored-in-noscript"
        ),
    ],
)
def test_meta_refresh_selection(html: str, expected: tuple[float, str] | None) -> None:
    assert parse(html).meta_refresh() == expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        pytest.param("/a/b", "/a/b", id="no-dot-segments"),
        pytest.param("no-percent-no-dot", "no-percent-no-dot", id="fast-path-unchanged"),
        pytest.param("a.b/c", "a.b/c", id="dot-inside-segment-kept"),
        pytest.param("/a/./b", "/a/b", id="single-dot-dropped"),
        pytest.param("/a/../b", "/b", id="double-dot-pops-parent"),
        pytest.param("/a/%2e/b", "/a/b", id="percent-2e-lower-is-dot"),
        pytest.param("/a/%2E/b", "/a/b", id="percent-2e-upper-is-dot"),
        pytest.param("/a/%2E./b", "/b", id="mixed-escape-and-literal-is-dotdot"),
        pytest.param("/%2e%2e/b", "/b", id="escaped-dotdot-pops"),
        pytest.param("/a/.../b", "/a/.../b", id="three-dots-not-a-dot-segment"),
        pytest.param("/a/%2f/b", "/a/%2f/b", id="escape-not-2e-third-char"),
        pytest.param("/a/%3e/b", "/a/%3e/b", id="escape-not-2e-second-char"),
        pytest.param("/a/%2/b", "/a/%2/b", id="escape-too-short-for-2e"),
        pytest.param("/a/.", "/a/", id="trailing-single-dot"),
        pytest.param("/a/..", "/", id="trailing-double-dot"),
        pytest.param("%2E", "", id="lone-escaped-dot"),
    ],
)
def test_remove_dot_segments_cases(path: str, expected: str) -> None:
    assert _url_remove_dot_segments(path) == expected


def test_remove_dot_segments_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_remove_dot_segments(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.")
_C0_OR_SPACE = "".join(map(chr, range(0x21)))
_USES_RELATIVE = frozenset({
    *("", "ftp", "http", "gopher", "nntp", "imap", "wais", "file", "https", "shttp"),
    *("mms", "prospero", "rtsp", "rtsps", "rtspu", "sftp", "svn", "svn+ssh", "ws", "wss"),
})


def _split5(url: str) -> tuple[str | None, str | None, str, str | None, str | None]:
    """CPython 3.14's ``_urlsplit`` reduced to a ``str``: leading strip, scheme, authority, query, and fragment."""
    url = url.lstrip(_C0_OR_SPACE)
    for unsafe in "\t\r\n":
        url = url.replace(unsafe, "")
    scheme = netloc = query = fragment = None
    colon = url.find(":")
    if colon > 0 and url[0].isascii() and url[0].isalpha() and all(char in _SCHEME_CHARS for char in url[:colon]):
        scheme, url = url[:colon].lower(), url[colon + 1 :]
    if url[:2] == "//":
        delimiter = min([len(url), *(offset for char in "/?#" if (offset := url.find(char, 2)) >= 0)])
        netloc, url = url[2:delimiter], url[delimiter:]
        if ("[" in netloc) != ("]" in netloc):
            message = "Invalid IPv6 URL"
            raise ValueError(message)
    if "#" in url:
        url, fragment = url.split("#", 1)
    if "?" in url:
        url, query = url.split("?", 1)
    return scheme, netloc, url, query, fragment


def _unsplit(scheme: str | None, netloc: str | None, path: str, query: str | None, fragment: str | None) -> str:
    if netloc is not None:
        if path and path[:1] != "/":
            path = "/" + path
        path = "//" + netloc + path
    prefix = f"{scheme}:" if scheme else ""
    suffix = ("?" + query if query is not None else "") + ("#" + fragment if fragment is not None else "")
    return f"{prefix}{path}{suffix}"


def _merge(bpath: str, path: str) -> str:
    """The RFC 3986 5.2.3 merge and 5.2.4 dot removal, in urljoin's segment-list form; ``path`` is never empty here."""
    base_parts = bpath.split("/")
    if base_parts[-1]:
        del base_parts[-1]  # the base's last segment is a file, so the reference replaces it
    if path[:1] == "/":
        segments = path.split("/")
    else:
        segments = base_parts + path.split("/")
        segments[1:-1] = filter(None, segments[1:-1])  # drop the empty interior segments a splice would rejoin
    resolved: list[str] = []
    for segment in segments:
        if segment == "..":
            if resolved:
                resolved.pop()
        elif segment != ".":
            resolved.append(segment)
    if segments[-1] in {".", ".."}:
        resolved.append("")
    return "/".join(resolved) or "/"


def _reference_join(base: str, url: str) -> str:
    """CPython 3.14's ``urljoin`` frozen against a ``str`` pair, the version-stable oracle the corpus differs from."""
    if not base or not url:
        return url or base
    bscheme, bnetloc, bpath, bquery, bfragment = _split5(base)
    scheme, netloc, path, query, fragment = _split5(url)
    if scheme is None:
        scheme = bscheme
    if scheme != bscheme or (scheme and scheme not in _USES_RELATIVE):
        return url  # a foreign or opaque scheme leaves the reference verbatim
    if netloc:
        return _unsplit(scheme, netloc, path, query, fragment)
    if not path:
        if query is None:
            query = bquery
            if fragment is None:
                fragment = bfragment
        return _unsplit(scheme, bnetloc, bpath, query, fragment)
    return _unsplit(scheme, bnetloc, _merge(bpath, path), query, fragment)


_BASES = (
    "http://a/b/c/d;p?q",
    "http://a/b/c/d",
    "http://a/b/c/",
    "http://a/b",
    "http://a/",
    "http://a",
    "https://h.com/dir/page.html?x=1#frag",
    "http://user:pass@h:8080/p/q?a=b#c",
    "http://a//b//c",
    "https://ex.com",
    "http://[::1]/p",
    "ftp://f/x/y",
    "file:///a/b",
    "//host/a",
    "http:opaque",
    "mailto:x@y",
    "tel:123",
    "HTTP://Up.Case/Dir/",
    "",
)
_REFERENCES = (
    "g",
    "./g",
    "g/",
    "/g",
    "//g",
    "?y",
    "g?y",
    "#s",
    "g#s",
    "g?y#s",
    ";x",
    "g;x",
    "",
    ".",
    "./",
    "..",
    "../",
    "../g",
    "../..",
    "../../",
    "../../g",
    "../../../../g",
    "/./g",
    "/../g",
    "g.",
    ".g",
    "g..",
    "..g",
    "./../g",
    "./g/.",
    "g/./h",
    "g/../h",
    "g;x=1/./y",
    "g;x=1/../y",
    "g?y/./x",
    "g#s/../x",
    "http:g",
    "http://other/x",
    "mailto:a@b",
    "javascript:void(0)",
    "//h2/p",
    "?",
    "#",
    "a//b",
    "c//d",
    "/a//b",
    "a/b/",
    "../a//b",
    "x/..",
    "x/../",
    "x/../y",
    " ",
    "   ",
    "a/b/c/../../d",
    "//",
    "///x",
    "/a/b/../../../c",
    "?q=1&r=2",
    "#a#b",
    "/",
    ".//g",
    "a\tb",
    "a\nb\rc",
    "~a:b",
    "_a:b",
    "1a:b",
    "@a:b",
)


def _corpus() -> list[tuple[str, str]]:
    pairs = [(base, reference) for base in _BASES for reference in _REFERENCES]
    alphabet = "abc/.?#=&%:@;+"
    generator = random.Random(20260704)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # fuzz corpus, not for security
    for _ in range(3000):
        reference = "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 10)))
        pairs.append((generator.choice(_BASES), reference))
    pairs += [("http://[bad", "g"), ("http://a/b", "//[bad")]  # both sides must agree the split fails
    return pairs


def _join(base: str, reference: str) -> tuple[str | None, bool]:
    """The joined URL, or ``(None, True)`` when the join raised, so the oracle and C agree on the failure too."""
    try:
        return _url_join(base, reference), False
    except ValueError:
        return None, True


def _oracle(base: str, reference: str) -> tuple[str | None, bool]:
    try:
        return _reference_join(base, reference), False
    except ValueError:
        return None, True


def test_url_join_matches_reference_over_corpus() -> None:
    for base, reference in _corpus():
        assert _join(base, reference) == _oracle(base, reference), (base, reference)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        pytest.param("g:h", "g:h", id="different-scheme-is-verbatim"),
        pytest.param("g", "http://a/b/c/g", id="relative-segment"),
        pytest.param("./g", "http://a/b/c/g", id="same-directory"),
        pytest.param("g/", "http://a/b/c/g/", id="trailing-slash"),
        pytest.param("/g", "http://a/g", id="absolute-path"),
        pytest.param("//g", "http://g", id="scheme-relative"),
        pytest.param("?y", "http://a/b/c/d;p?y", id="query-only"),
        pytest.param("g?y", "http://a/b/c/g?y", id="segment-with-query"),
        pytest.param("#s", "http://a/b/c/d;p?q#s", id="fragment-only"),
        pytest.param("g#s", "http://a/b/c/g#s", id="segment-with-fragment"),
        pytest.param(";x", "http://a/b/c/;x", id="params-segment"),
        pytest.param(".", "http://a/b/c/", id="dot"),
        pytest.param("./", "http://a/b/c/", id="dot-slash"),
        pytest.param("..", "http://a/b/", id="dot-dot"),
        pytest.param("../", "http://a/b/", id="dot-dot-slash"),
        pytest.param("../g", "http://a/b/g", id="parent-segment"),
        pytest.param("../..", "http://a/", id="grandparent"),
        pytest.param("../../g", "http://a/g", id="grandparent-segment"),
        pytest.param("../../../../g", "http://a/g", id="over-popped-clamps-to-root"),
        pytest.param("/./g", "http://a/g", id="absolute-with-dot"),
        pytest.param("/../g", "http://a/g", id="absolute-with-dot-dot"),
        pytest.param("g.", "http://a/b/c/g.", id="trailing-dot-in-name"),
        pytest.param(".g", "http://a/b/c/.g", id="leading-dot-in-name"),
        pytest.param("g/./h", "http://a/b/c/g/h", id="interior-dot"),
        pytest.param("g/../h", "http://a/b/c/h", id="interior-dot-dot"),
        pytest.param("g;x=1/../y", "http://a/b/c/y", id="params-then-parent"),
        pytest.param("g?y/./x", "http://a/b/c/g?y/./x", id="dots-inside-query-are-literal"),
        pytest.param("g#s/../x", "http://a/b/c/g#s/../x", id="dots-inside-fragment-are-literal"),
        pytest.param("http:g", "http://a/b/c/g", id="same-scheme-prefix-is-relative"),
        pytest.param("", "http://a/b/c/d;p?q", id="empty-reference-keeps-base"),
    ],
)
def test_url_join_resolves_rfc3986_examples(reference: str, expected: str) -> None:
    assert _url_join("http://a/b/c/d;p?q", reference) == expected


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("", "g/h", "g/h", id="empty-base-returns-reference"),
        pytest.param("http://a/b", "", "http://a/b", id="empty-reference-returns-base"),
        pytest.param("http://a/b?x#y", "   ", "http://a/b?x#y", id="blank-reference-keeps-base-query-and-fragment"),
        pytest.param("//host/a/b", "c", "//host/a/c", id="scheme-less-base-keeps-authority"),
        pytest.param("http:opaque", "rel", "http:rel", id="rootless-base-path-merges-without-authority"),
        pytest.param("http://h", "g", "http://h/g", id="empty-base-path-roots-the-reference"),
        pytest.param("http://a/b/c/d", "//h2/p/q", "http://h2/p/q", id="scheme-relative-replaces-authority"),
    ],
)
def test_url_join_edge_operands(base: str, reference: str, expected: str) -> None:
    assert _url_join(base, reference) == expected


@pytest.mark.parametrize(
    ("base", "reference"),
    [
        pytest.param("mailto:x@y", "g", id="opaque-base-scheme"),
        pytest.param("http://a/b", "ftp://x/y", id="foreign-scheme"),
        pytest.param("tel:1", "tel:2", id="same-opaque-scheme"),
        pytest.param("http://a/b", "mailto:a@b", id="mailto-reference"),
    ],
)
def test_url_join_returns_reference_verbatim(base: str, reference: str) -> None:
    assert _url_join(base, reference) == reference


def test_url_join_rejects_non_str_operand() -> None:
    with pytest.raises(TypeError):
        _url_join(b"http://a", "g")  # ty: ignore[invalid-argument-type]  # both operands must be str


@pytest.mark.parametrize(
    ("base", "reference"),
    [
        pytest.param("http://[::1/x", "g", id="base-open-bracket-only"),
        pytest.param("http://a]/x", "g", id="base-close-bracket-only"),
        pytest.param("http://a/b", "//[bad", id="reference-open-bracket-only"),
        pytest.param("http://a/b", "//bad]", id="reference-close-bracket-only"),
    ],
)
def test_url_join_rejects_unbalanced_brackets(base: str, reference: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        _url_join(base, reference)


@pytest.mark.parametrize(
    ("base", "reference", "expected"),
    [
        pytest.param("http://a/", "g\th", "http://a/gh", id="reference-drops-tab"),
        pytest.param("http://a/", "g\nh\rj", "http://a/ghj", id="reference-drops-newline-and-return"),
        pytest.param("\x01\x02http://a/x", "g", "http://a/g", id="base-strips-leading-control"),
        pytest.param("  http://a/x  ", "g", "http://a/g", id="base-strips-leading-space"),
    ],
)
def test_url_join_preprocesses_operands(base: str, reference: str, expected: str) -> None:
    assert _url_join(base, reference) == expected


_LANGUAGE_PARAMS = frozenset({"lang", "language"})
_ISO = frozenset({"de", "en", "fr", "es", "ru", "nl"})
_SEGMENT = re.compile(r"([a-z]{2})(?:[-_][a-z]{2,3})?$")


def _language_oracle(query: str, path: str, hostname: str, language: str, *, strict: bool) -> bool:
    for pair in query.split("&"):
        key, separator, value = pair.partition("=")
        if separator and _url_percent_decode(key).lower() in _LANGUAGE_PARAMS:
            code = _url_percent_decode(value).lower()
            if code and not code.startswith(language):
                return False
    leading = next((segment for segment in path.lower().split("/") if segment), "")
    if (match := _SEGMENT.fullmatch(leading)) and match[1] in _ISO and match[1] != language:
        return False
    if strict:
        label = hostname.partition(".")[0]
        if len(label) == 2 and label in _ISO and label != language:
            return False
    return True


_PATHS = (
    "",
    "/",
    "/de/beitrag",
    "/en-us/page",
    "/en_us/page",
    "/fr-fra/x",
    "/de",
    "//de//x",
    "de/x",
    "/DE/UPPER",
    "/e1/x",
    "/abc/x",
    "/abcd/x",
    "/en-u1/x",
    "/en-u/x",
    "/xy-ab/x",
    "/ru/y",
    "/zz/y",
    "/en-abcd/x",
    "/%64%65/x",
    "/enxab/x",
    "/z{/x",
)
_QUERIES = (
    "",
    "x=1",
    "lang=de",
    "language=fr",
    "lang=en-US",
    "lang=",
    "lang=de&language=en",
    "LANG=fr",
    "%6c%61%6e%67=de",
)
_HOSTS = ("example.org", "de.example.org", "en.example.org", "zz.example.org", "e.org", "fr.a.b", "de", "localhost")
_LANGS = ("de", "en", "fr")


def test_language_matches_matches_oracle_over_corpus() -> None:
    for path, query, host, lang, strict in itertools.product(_PATHS, _QUERIES, _HOSTS, _LANGS, (False, True)):
        got = _url_language_matches(query, path, host, lang, strict, _LANGUAGE_PARAMS, _ISO)
        want = _language_oracle(query, path, host, lang, strict=strict)
        assert got is want, (query, path, host, lang, strict)


@pytest.mark.parametrize(
    ("markers", "strict", "expected"),
    [
        pytest.param(("", "/de/x", "h.org", "en"), False, False, id="path-segment-other-language"),
        pytest.param(("", "/en/x", "h.org", "en"), False, True, id="path-segment-same-language"),
        pytest.param(("", "/en-us/x", "h.org", "en"), False, True, id="path-subtag-two-letters"),
        pytest.param(("", "/fr-fra/x", "h.org", "en"), False, False, id="path-subtag-three-letters"),
        pytest.param(("", "/abc/x", "h.org", "en"), False, True, id="path-three-letters-not-marker"),
        pytest.param(("", "/en-u1/x", "h.org", "en"), False, True, id="path-subtag-not-letters"),
        pytest.param(("", "/e1/x", "h.org", "en"), False, True, id="path-second-char-not-letter"),
        pytest.param(("lang=de", "/x", "h.org", "en"), False, False, id="query-lang-other-language"),
        pytest.param(("lang=en-gb", "/x", "h.org", "en"), False, True, id="query-lang-prefix-matches"),
        pytest.param(("lang=", "/x", "h.org", "en"), False, True, id="query-lang-empty-value"),
        pytest.param(("", "/x", "de.example.org", "en"), True, False, id="strict-host-other-language"),
        pytest.param(("", "/x", "de.example.org", "en"), False, True, id="non-strict-ignores-host"),
        pytest.param(("", "/x", "en.example.org", "en"), True, True, id="strict-host-same-language"),
        pytest.param(("", "/x", "abc.example.org", "en"), True, True, id="strict-host-label-not-two-letters"),
        pytest.param(("", "/x", "de", "en"), True, False, id="strict-dotless-host-label"),
        pytest.param(("", "/enxab/x", "h.org", "en"), False, True, id="path-five-chars-no-separator"),
    ],
)
def test_language_matches_cases(markers: tuple[str, str, str, str], *, strict: bool, expected: bool) -> None:
    query, path, hostname, language = markers
    assert _url_language_matches(query, path, hostname, language, strict, _LANGUAGE_PARAMS, _ISO) is expected


def test_language_matches_rejects_missing_arguments() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_language_matches("", "/x", "h.org", "en", lax)  # ty: ignore[missing-argument]


_QUERY_SET = 1
_CONTENT = frozenset({"id", "page", "post", "article_id"})
_LANGUAGE = frozenset({"lang", "language"})


def _query_oracle(query: str, allow: frozenset[str] | None, deny: frozenset[str], *, strict: bool) -> str:
    kept: list[tuple[str, str]] = []
    for pair in query.split("&"):
        if not pair:
            continue
        key = _url_percent_decode(pair.partition("=")[0]).lower()
        if key in deny:
            continue
        if allow is not None:
            dropped = key not in allow
        elif strict:
            dropped = key not in _CONTENT and key not in _LANGUAGE
        else:
            dropped = _url_is_tracker(key)
        if dropped:
            continue
        try:
            encoded = _url_percent_encode(pair, _QUERY_SET)
        except UnicodeEncodeError as exc:
            # the retired shim converted the encoder's failure to the public ValueError; the C does the same inline
            msg = f"URL component {pair!r} has a character that cannot be percent-encoded: {exc.reason}"
            raise ValueError(msg) from exc
        kept.append((key, encoded))
    return "&".join(pair for _key, pair in sorted(kept))


def _outcome(call: Callable[[], str]) -> tuple[object, ...]:
    try:
        return ("ok", call())
    except ValueError as exc:  # UnicodeEncodeError subclasses ValueError, so a lone-surrogate failure lands here too
        return ("err", type(exc).__name__, str(exc))


_NORMALIZED_QUERIES = (
    "",
    "a=1",
    "ok=1&bad=\udcff",  # a lone surrogate cannot percent-encode, so both sides must fail identically
    "a=1&b=2",
    "b=2&a=1",
    "id=1&utm_source=x",
    "utm_campaign=z&fbclid=y&keep=1",
    "a=1&a=2&a=3",
    "=empty&x=",
    "café=1&CAFÉ=2",
    "%41=%42&ref=1",
    "lang=de&page=3",
    "session=1&content=2",
    "aff_id=1&article_id=9",
    "x=%ZZ&y=1",
    "a=1&&b=2&",
    "utm_medium=e&Id=7",
    "A=1&a=2&B=3",
    "a&b&c",
)
_ALLOWS = (None, frozenset({"id", "page"}), frozenset({"UTM_SOURCE"}), frozenset())
_DENYS = (frozenset(), frozenset({"id"}), frozenset({"a", "b"}))


def test_normalize_query_matches_oracle_over_corpus() -> None:
    for query, allow, deny, strict in itertools.product(_NORMALIZED_QUERIES, _ALLOWS, _DENYS, (False, True)):
        if strict and allow is not None:
            continue
        got = _outcome(partial(_url_normalize_query, query, allow, deny, strict, _CONTENT, _LANGUAGE))
        want = _outcome(partial(_query_oracle, query, allow, deny, strict=strict))
        assert got == want, (query, allow, deny, strict)


@pytest.mark.parametrize(
    ("query", "allow", "deny", "strict", "expected"),
    [
        pytest.param("b=2&a=1", None, frozenset(), False, "a=1&b=2", id="sort-non-trackers"),
        pytest.param("utm_source=x&id=1", None, frozenset(), False, "id=1", id="drop-tracker"),
        pytest.param("id=1&page=2&x=3", None, frozenset(), True, "id=1&page=2", id="strict-keeps-content"),
        pytest.param("lang=de&x=3", None, frozenset(), True, "lang=de", id="strict-keeps-language"),
        pytest.param("id=1&page=2", frozenset({"id"}), frozenset(), False, "id=1", id="allow-keeps-only-listed"),
        pytest.param("id=1&keep=2", None, frozenset({"id"}), False, "keep=2", id="deny-drops-listed"),
        pytest.param("Id=1&PAGE=2", None, frozenset(), True, "Id=1&PAGE=2", id="key-lowercased-for-filter"),
        pytest.param("", None, frozenset(), False, "", id="empty-query"),
        pytest.param("a=1&&b=2&", None, frozenset(), False, "a=1&b=2", id="empty-pairs-dropped"),
    ],
)
def test_normalize_query_cases(
    query: str, allow: frozenset[str] | None, deny: frozenset[str], expected: str, *, strict: bool
) -> None:
    assert _url_normalize_query(query, allow, deny, strict, _CONTENT, _LANGUAGE) == expected


def test_normalize_query_surrogate_key_raises() -> None:
    # a lone-surrogate key has no UTF-8 form, so the tracker test cannot classify it and the error propagates
    lax = False
    with pytest.raises(UnicodeEncodeError):
        _url_normalize_query("\ud800=1", None, frozenset(), lax, _CONTENT, _LANGUAGE)


def test_normalize_query_surrogate_value_raises() -> None:
    # the key survives, then encoding the pair fails on the surrogate value, rewrapped as the shim's ValueError
    lax = False
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        _url_normalize_query("ok=\ud800", None, frozenset(), lax, _CONTENT, _LANGUAGE)


def test_normalize_query_rejects_non_str_query() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_normalize_query(123, None, frozenset(), lax, _CONTENT, _LANGUAGE)  # ty: ignore[invalid-argument-type]


def test_normalize_query_rejects_missing_arguments() -> None:
    lax = False
    with pytest.raises(TypeError):
        _url_normalize_query("a=1", None, frozenset(), lax)  # ty: ignore[missing-argument]


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


_C0_AND_SPACE = "".join(map(chr, range(0x21)))
_MARKUP_DELIMITER = re.compile(r'[<>"]')


# CPython's str whitespace (Py_UNICODE_ISSPACE), pinned because PyPy's str.split() disagrees on the C0 separators
# 0x1C-0x1F, and the C scrub follows CPython's predicate on every interpreter
_PY_WHITESPACE = frozenset(
    map(
        chr,
        (
            *range(0x09, 0x0E),
            *range(0x1C, 0x21),
            0x85,
            0xA0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        ),
    )
)


def _oracle_scrub(url: str) -> str:
    remainder = "".join(char for char in url.strip(_C0_AND_SPACE) if char not in _PY_WHITESPACE)
    if remainder.startswith("<![CDATA["):
        remainder = remainder.removeprefix("<![CDATA[").removesuffix("]]>")
    remainder = _MARKUP_DELIMITER.split(remainder, maxsplit=1)[0].replace("&amp;", "&")
    return remainder.removesuffix("&") if remainder.endswith("/&") else remainder


# Pieces that reach every scrub branch: whitespace str.split() removes but the edge C0 strip alone would not (NBSP, NEL,
# a separator), a non-whitespace C0 control that survives in the middle, and the CDATA, markup, and escape shapes.
_PIECES = (
    "https://x.example/p",
    " ",
    "\t",
    "\n",
    "\xa0",
    "\x85",
    "\x1c",
    "\x01",
    "<![CDATA[",
    "]]>",
    "<",
    ">",
    '"',
    "&amp;",
    "/&",
    "&",
    "a",
    "//double",
    "?q=1",
    "#f",
)
_EDGE_CASES = (
    "",
    " ",
    "  https://x.example/p  ",
    "\t\nhttps://x.example/p\r\n",
    "\x01\x02https://x.example/p\x03",
    "a\xa0b\x85c",
    "a\x1cb\x1fc",
    "line break",
    "mid\x01control",
    "<![CDATA[https://x.example/p]]>",
    "<![CDATA[https://x.example/p",
    "<![CDATA[a>b",
    "<![CDATA[]]>",
    "<![CDAT",
    "https://x.example/a<script>",
    "https://x.example/a>b",
    'https://x.example/p"junk',
    "https://x.example/a&amp;b&amp;c",
    "https://x.example/path/&amp;",
    "https://x.example/path/&",
    "https://x.example/&amp;",
    "trailing&",
)


def _scrub_corpus() -> list[str]:
    seen = set(_EDGE_CASES)
    for length in range(4):
        seen.update("".join(combo) for combo in itertools.product(_PIECES, repeat=length))
    return sorted(seen)


def test_scrub_matches_oracle_over_corpus() -> None:
    for url in _scrub_corpus():
        assert _url_scrub(url) == _oracle_scrub(url), repr(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("  https://x/p  ", "https://x/p", id="edge-space-stripped"),
        pytest.param("\x01https://x/p\x02", "https://x/p", id="edge-c0-stripped"),
        pytest.param("mid\x01control", "mid\x01control", id="mid-non-ws-c0-kept"),
        pytest.param("a\xa0b", "ab", id="nbsp-whitespace-removed"),
        pytest.param("a b\tc\nd", "abcd", id="internal-whitespace-removed"),
        pytest.param("<![CDATA[https://x/p]]>", "https://x/p", id="cdata-unwrapped"),
        pytest.param("<![CDATA[https://x/p", "https://x/p", id="cdata-open-only"),
        pytest.param("<![CDATA[ab", "ab", id="cdata-content-too-short-for-suffix"),
        pytest.param("<![CDATA[a]x]", "a]x]", id="cdata-suffix-second-char-mismatch"),
        pytest.param("<![CDATA[a]]x", "a]]x", id="cdata-suffix-third-char-mismatch"),
        pytest.param("<![CDAT", "", id="cdata-too-short-truncated-at-lt"),
        pytest.param("https://x/a<b", "https://x/a", id="truncate-at-lt"),
        pytest.param("https://x/a>b", "https://x/a", id="truncate-at-gt"),
        pytest.param('https://x/a"b', "https://x/a", id="truncate-at-quote"),
        pytest.param("https://x/a&amp;b", "https://x/a&b", id="amp-unescaped"),
        pytest.param("https://x/&amp;a&amp;b", "https://x/&a&b", id="amp-unescaped-multiple"),
        pytest.param("https://x/p/&amp;", "https://x/p/", id="trailing-slash-amp-dropped"),
        pytest.param("keep&", "keep&", id="trailing-amp-not-after-slash-kept"),
        pytest.param("a&xzzzz", "a&xzzzz", id="amp-first-char-mismatch"),
        pytest.param("a&abbbb", "a&abbbb", id="amp-second-char-mismatch"),
        pytest.param("a&amxxx", "a&amxxx", id="amp-third-char-mismatch"),
        pytest.param("a&ampxy", "a&ampxy", id="amp-fourth-char-mismatch"),
    ],
)
def test_scrub_cases(url: str, expected: str) -> None:
    assert _url_scrub(url) == expected


def test_scrub_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_scrub(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


_REGNAME, _IPV4, _IPV6 = 0, 1, 2

_SCHEMES = ("http", "https", "ftp", "HTTP", "mailto", "", "javascript", "ws", "wss", "file")
_AUTHORITIES = (
    "",
    "example.com",
    "Example.COM",
    "user@host",
    "user:pass@host",
    "host:8080",
    "host:",
    "host:080",
    "host:abc",
    "1.2.3.4",
    "1.2.3.4:9",
    "[::1]",
    "[::1]:443",
    "[::1]:",
    "[2001:db8::1]:8080",
    "user@[::1]:80",
    "münchen.de",
    "sub.example.co.uk",
    "a",
    "a:b:c",
    "@host",
    "user@@host",
)
_SPLIT_PATHS = ("", "/", "/a/b", "/a/../b")
_SPLIT_QUERIES = ("", "?a=1", "?a=1&b=2")
_FRAGMENTS = ("", "#f")


def _split_corpus() -> list[str]:
    urls = [
        f"{scheme}:" * bool(scheme) + (f"//{auth}" * bool(auth or path.startswith("//"))) + path + query + fragment
        for scheme in _SCHEMES
        for auth in _AUTHORITIES
        for path in _SPLIT_PATHS
        for query in _SPLIT_QUERIES
        for fragment in _FRAGMENTS
    ]
    urls += ["  http://x.com/y  ", "http://h\tost/p", "\x01\x02http://a.com", "HTTP://X", "//netonly/p", "a\nb\rc://x"]
    urls += [f"http://h.com/x{query}{fragment}" for query in ("", "?", "?a=1") for fragment in ("", "#", "#a=1")]
    return sorted(set(urls))


def test_url_split_matches_urllib_over_corpus() -> None:
    for url in _split_corpus():
        reference = urlsplit(url)
        scheme, netloc, path, query, fragment, _userinfo, host, *_rest = _url_split(url)
        assert (scheme, netloc, path, query, fragment) == (
            reference.scheme,
            reference.netloc,
            reference.path,
            reference.query,
            reference.fragment,
        ), url
        assert host.lower() == (reference.hostname or ""), url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            "http://user:pass@Example.COM:8080/p?a=1#f",
            (
                "http",
                "user:pass@Example.COM:8080",
                "/p",
                "a=1",
                "f",
                "user:pass",
                "Example.COM",
                "8080",
                True,
                _REGNAME,
            ),
            id="full-regname",
        ),
        pytest.param(
            "https://[2001:DB8::1]:443/x",
            ("https", "[2001:DB8::1]:443", "/x", "", "", "", "2001:DB8::1", "443", True, _IPV6),
            id="ipv6-with-port",
        ),
        pytest.param(
            "http://1.2.3.4/x",
            ("http", "1.2.3.4", "/x", "", "", "", "1.2.3.4", "", False, _IPV4),
            id="ipv4-no-port",
        ),
        pytest.param(
            "mailto:a@b.com",
            ("mailto", "", "a@b.com", "", "", "", "", "", False, _REGNAME),
            id="opaque-no-authority",
        ),
        pytest.param(
            "",
            ("", "", "", "", "", "", "", "", False, _REGNAME),
            id="empty",
        ),
    ],
)
def test_url_split_reports_components(url: str, expected: tuple[object, ...]) -> None:
    assert _url_split(url) == expected


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        pytest.param("http://example.com/", _REGNAME, id="regname"),
        pytest.param("http://münchen.de/", _REGNAME, id="unicode-regname"),
        pytest.param("http://1.2.3.4/", _IPV4, id="ipv4"),
        pytest.param("http://[::1]/", _IPV6, id="ipv6"),
    ],
)
def test_url_split_classifies_host(url: str, kind: int) -> None:
    assert _url_split(url)[9] == kind


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("http://[::1/x", id="open-only"),
        pytest.param("http://a]b/x", id="close-only"),
    ],
)
def test_url_split_rejects_unbalanced_brackets(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid IPv6 URL"):
        _url_split(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("\x00\x1f http://x/p", ("http", "x", "/p"), id="strip-leading-control-and-space"),
        pytest.param("http://münchen.de/p", ("http", "münchen.de", "/p"), id="two-byte-input"),
        pytest.param("http://x/\U0001f600", ("http", "x", "/\U0001f600"), id="four-byte-input"),
        pytest.param("ht\ttp://x/p", ("http", "x", "/p"), id="drop-tab"),
        pytest.param("ht\ntp://x/p", ("http", "x", "/p"), id="drop-newline"),
        pytest.param("ht\rtp://x/p", ("http", "x", "/p"), id="drop-carriage-return"),
    ],
)
def test_url_split_preprocesses_input(url: str, expected: tuple[str, str, str]) -> None:
    assert _url_split(url)[:3] == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("ht!p://x", ("", "", "ht!p://x"), id="non-scheme-char-before-colon-is-no-scheme"),
        pytest.param("1http://x", ("", "", "1http://x"), id="digit-lead-is-no-scheme"),
        pytest.param("~http://x", ("", "", "~http://x"), id="above-z-lead-is-no-scheme"),
        pytest.param("_http://x", ("", "", "_http://x"), id="between-Z-and-a-lead-is-no-scheme"),
        pytest.param("abc", ("", "", "abc"), id="bare-scheme-chars-without-colon-is-path"),
        pytest.param("a+b-1.c://x", ("a+b-1.c", "x", ""), id="scheme-uses-plus-minus-dot-digit"),
        pytest.param("http://", ("http", "", ""), id="empty-authority"),
        pytest.param("http://:80/x", ("http", ":80", "/x"), id="empty-host-with-port"),
        pytest.param("http://[::1]x/p", ("http", "[::1]x", "/p"), id="ipv6-trailing-non-colon"),
        pytest.param("http://]@[foo/p", ("http", "]@[foo", "/p"), id="bracket-open-without-close-in-hostinfo"),
    ],
)
def test_url_split_edge_authorities(url: str, expected: tuple[str, str, str]) -> None:
    assert _url_split(url)[:3] == expected


def test_url_split_empty_host_with_port_has_no_host() -> None:
    _scheme, _netloc, _path, _query, _fragment, _userinfo, host, port, has_port, kind = _url_split("http://:80/x")
    assert (host, port, has_port, kind) == ("", "80", True, _REGNAME)


def test_url_split_ipv6_open_without_close_keeps_tail_as_host() -> None:
    assert _url_split("http://]@[foo/p")[6] == "foo"


_TRACKER_NAMES = frozenset({
    "clickid",
    "dclid",
    "efid",
    "epik",
    "fb_ref",
    "fb_source",
    "fbclid",
    "gbraid",
    "gclid",
    "gclsrc",
    "igsh",
    "igshid",
    "mkt_tok",
    "msclkid",
    "partnerid",
    "s_cid",
    "sc_cid",
    "ttclid",
    "twclid",
    "wbraid",
    "wickedid",
    "yclid",
    "ysclid",
})
_TRACKER_PREFIXES = ("ad_", "ads_", "ga_", "gs_", "hsa_", "itm_", "mc_", "mtm_", "oly_", "pk_", "utm_", "vero_")
_TRACKER_WORDS = re.compile(
    r"(?:^|_)(?:aff(?:i(?:liate)?)?|campaign|cl?id|keyword|kwd|medium|refer(?:r?er)?|ref|session|source|uid|xtor)(?:_|$)"
)


def _tracker_oracle(key: str) -> bool:
    return (
        key in _TRACKER_NAMES
        or key.startswith(_TRACKER_PREFIXES)
        or key.endswith("clid")
        or _TRACKER_WORDS.search(key) is not None
    )


def _tracker_corpus() -> list[str]:
    seen = (
        set(_TRACKER_NAMES)
        | set(_TRACKER_PREFIXES)
        | {
            "",
            "id",
            "page",
            "myclid",
            "reference",
            "x_ref_y",
            "campaign",
            "affiliate",
            "xtor",
            "referer",
            "referrer",
            "utm_source",
            "ad_id",
            "a_uid_b",
            "keyword",
            "kwd",
            "medium",
            "session",
            "cid",
            "clid",
            "not_a_ref",
        }
    )
    fragments = ("utm", "ref", "id", "clid", "aff", "source", "_", "campaign", "uid")
    for length in range(4):
        seen.update("".join(combo) for combo in itertools.product(fragments, repeat=length))
    return sorted(seen)


def test_is_tracker_matches_oracle_over_corpus() -> None:
    for key in _tracker_corpus():
        assert _url_is_tracker(key) == _tracker_oracle(key), repr(key)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        pytest.param("gclid", True, id="exact-name"),
        pytest.param("utm_source", True, id="known-prefix"),
        pytest.param("myclid", True, id="clid-suffix-not-exact"),
        pytest.param("x_ref_y", True, id="tracking-word-between-underscores"),
        pytest.param("campaign", True, id="tracking-word-whole"),
        pytest.param("reference", False, id="longer-word-not-matched"),
        pytest.param("id", False, id="content-name"),
        pytest.param("", False, id="empty"),
    ],
)
def test_is_tracker_cases(key: str, *, expected: bool) -> None:
    assert _url_is_tracker(key) is expected


def test_is_tracker_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_is_tracker(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard


def test_is_tracker_lone_surrogate_raises() -> None:
    # a lone-surrogate key has no UTF-8 form, so it cannot be classified and the UnicodeEncodeError propagates
    with pytest.raises(UnicodeEncodeError):
        _url_is_tracker("\ud800clid")


def _oracle_variant(url: str) -> str:
    remainder = url.partition("://")[2]
    return remainder if "?" in remainder or "#" in remainder else remainder.rstrip("/")


_VARIANT_SCHEMES = ("https://", "http://", "ftp://", "", "://", "a://b://")
_TAILS = (
    "",
    "/",
    "//",
    "x.example/p",
    "x.example/p/",
    "x.example/p//",
    "x.example/p?q=1",
    "x.example/p/#f",
    "x.example/a/b/",
    "x.example/?only",
    "x.example/#only",
    "mailto:x@y",
    "a:/b/c/",
)


def _variant_corpus() -> list[str]:
    combined = {"".join(combo) for combo in itertools.product(_VARIANT_SCHEMES, _TAILS)}
    return sorted(combined | {"", "no-scheme-path/", "://leading/", "a://b://c/d/"})


def test_variant_key_matches_oracle_over_corpus() -> None:
    for url in _variant_corpus():
        assert _url_variant_key(url) == _oracle_variant(url), repr(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("no-scheme-path/", "", id="no-scheme-empty-key"),
        pytest.param("https://x/p/", "x/p", id="trailing-slash-trimmed"),
        pytest.param("https://x/p///", "x/p", id="trailing-slashes-trimmed"),
        pytest.param("https://x/p?q=1/", "x/p?q=1/", id="query-keeps-slash"),
        pytest.param("https://x/p/#f", "x/p/#f", id="fragment-keeps-slash"),
        pytest.param("https://x/", "x", id="root-slash-trimmed"),
        pytest.param("a://b://c/", "b://c", id="first-marker-wins"),
        pytest.param("mailto:x@y", "", id="colon-not-followed-by-slash"),
        pytest.param("a:/b/c/", "", id="colon-single-slash-no-marker"),
    ],
)
def test_variant_key_cases(url: str, expected: str) -> None:
    assert _url_variant_key(url) == expected


def test_variant_key_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="must be str"):
        _url_variant_key(123)  # ty: ignore[invalid-argument-type]  # a non-str exercises the TypeError guard
