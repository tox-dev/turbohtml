"""Conformance tests for the C ``_url_join`` primitive behind :mod:`turbohtml.extract._urls`.

``_url_join`` resolves a relative reference against a base URL, the RFC 3986 section 5.3 reference transform
``urllib.parse.urljoin`` runs. It reproduces the transform ``urljoin`` runs on Python 3.14, where the standard library
adopted the WHATWG rule that a bare ``?`` or ``#`` keeps its empty query or fragment rather than inheriting the base's;
3.13 and earlier fall back to the base, so the differential oracle is a frozen port of the 3.14 algorithm rather than
the running interpreter's ``urljoin``, keeping the corpus stable across versions. The RFC 3986 section 5.4 examples are
pinned on their own, and the degenerate operands (empty base or reference, a foreign or opaque scheme, an unbalanced
bracket) are asserted directly.
"""

from __future__ import annotations

import random

import pytest

from turbohtml._html import _url_join

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
    generator = random.Random(20260704)  # noqa: S311  # fuzz corpus, not for security
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
