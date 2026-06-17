from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml._html import _linkify_scan
from turbohtml.linkify import DEFAULT_CALLBACKS, Link, Linker, linkify, nofollow, target_blank

if TYPE_CHECKING:
    from turbohtml.linkify import Callback


def _no_callbacks() -> list[Callback]:
    return []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", "", id="empty"),
        pytest.param("nothing to see here", "nothing to see here", id="no-links"),
        pytest.param(
            "go http://example.com end",
            'go <a href="http://example.com">http://example.com</a> end',
            id="scheme-url",
        ),
        pytest.param(
            "https://example.com/a/b?c=d#e",
            '<a href="https://example.com/a/b?c=d#e">https://example.com/a/b?c=d#e</a>',
            id="scheme-url-path-query-fragment",
        ),
        pytest.param(
            "visit www.example.com today",
            'visit <a href="http://www.example.com">www.example.com</a> today',
            id="www-gets-http-prefix",
        ),
        pytest.param(
            "a bare example.com link",
            'a bare <a href="http://example.com">example.com</a> link',
            id="bare-domain",
        ),
        pytest.param(
            "sub.domain.example.co.uk works",
            '<a href="http://sub.domain.example.co.uk">sub.domain.example.co.uk</a> works',
            id="bare-domain-multi-label",
        ),
        pytest.param(
            "host with port http://example.com:8080/x",
            'host with port <a href="http://example.com:8080/x">http://example.com:8080/x</a>',
            id="port-and-path",
        ),
        pytest.param(
            "two http://a.example.com and http://b.example.com here",
            'two <a href="http://a.example.com">http://a.example.com</a> and '
            '<a href="http://b.example.com">http://b.example.com</a> here',
            id="two-links-in-one-text-run",
        ),
        pytest.param(
            "wrap (http://en.wikipedia.org/wiki/Foo_(bar)) done",
            'wrap (<a href="http://en.wikipedia.org/wiki/Foo_(bar)">http://en.wikipedia.org/wiki/Foo_(bar)</a>) done',
            id="balanced-parens-kept-trailing-trimmed",
        ),
        pytest.param(
            "trailing dot http://example.com. end",
            'trailing dot <a href="http://example.com">http://example.com</a>. end',
            id="trailing-dot-trimmed",
        ),
    ],
)
def test_linkify_plain(text: str, expected: str) -> None:
    assert linkify(text, callbacks=_no_callbacks()) == expected


def test_linkify_default_callback_adds_nofollow() -> None:
    assert linkify("see http://example.com") == 'see <a href="http://example.com" rel="nofollow">http://example.com</a>'


def test_linkify_leaves_existing_anchor_untouched() -> None:
    html = '<a href="http://x.com">http://y.com</a> and http://z.com'
    assert linkify(html, callbacks=_no_callbacks()) == (
        '<a href="http://x.com">http://y.com</a> and <a href="http://z.com">http://z.com</a>'
    )


@pytest.mark.parametrize("tag", ["script", "style"])
def test_linkify_skips_raw_text_elements(tag: str) -> None:
    html = f"<{tag}>http://x.com</{tag}>http://y.com"
    out = linkify(html, callbacks=_no_callbacks())
    assert f"<{tag}>http://x.com</{tag}>" in out
    assert '<a href="http://y.com">' in out


def test_linkify_skip_tags() -> None:
    html = "<code>http://x.com</code> http://y.com"
    out = linkify(html, skip_tags=["code"], callbacks=_no_callbacks())
    assert out == '<code>http://x.com</code> <a href="http://y.com">http://y.com</a>'


def test_linkify_nested_skip_tag_stays_skipped() -> None:
    html = "<code><span>http://x.com</span></code>"
    assert linkify(html, skip_tags=["code"], callbacks=_no_callbacks()) == html


def test_linkify_leaves_comment_nodes_untouched() -> None:
    html = "<!-- http://skip.com --> http://link.com"
    out = linkify(html, callbacks=_no_callbacks())
    assert out == '<!-- http://skip.com --> <a href="http://link.com">http://link.com</a>'


def test_linkify_email_off_by_default() -> None:
    assert linkify("reach bob@example.com now") == "reach bob@example.com now"


def test_linkify_email_when_enabled() -> None:
    out = linkify("reach bob@example.com now", parse_email=True, callbacks=_no_callbacks())
    assert out == 'reach <a href="mailto:bob@example.com">bob@example.com</a> now'


def test_linkify_veto_callback_keeps_plain_text() -> None:
    assert linkify("http://x.com", callbacks=[lambda link: None]) == "http://x.com"  # noqa: ARG005


def test_linkify_callback_can_change_text() -> None:
    def shorten(link: Link) -> Link:
        link.text = "link"
        return link

    assert linkify("http://x.com", callbacks=[shorten]) == '<a href="http://x.com">link</a>'


def test_linkify_callback_can_add_attribute() -> None:
    def add_class(link: Link) -> Link:
        link.attrs["class"] = "ext"
        return link

    assert linkify("http://x.com", callbacks=[add_class]) == '<a href="http://x.com" class="ext">http://x.com</a>'


def test_linkify_callback_chain_runs_in_order() -> None:
    out = linkify("http://x.com", callbacks=[nofollow, target_blank])
    assert out == '<a href="http://x.com" rel="nofollow" target="_blank">http://x.com</a>'


def test_linker_is_reusable() -> None:
    linker = Linker(callbacks=_no_callbacks())
    assert linker.linkify("http://a.example.com") == '<a href="http://a.example.com">http://a.example.com</a>'
    assert linker.linkify("http://b.example.com") == '<a href="http://b.example.com">http://b.example.com</a>'


def test_default_callbacks_is_nofollow() -> None:
    assert (nofollow,) == DEFAULT_CALLBACKS


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"rel": "nofollow"}, id="adds-nofollow"),
        pytest.param("https://x.com", {}, {"rel": "nofollow"}, id="https-too"),
        pytest.param("http://x.com", {"rel": "noopener"}, {"rel": "noopener nofollow"}, id="appends-to-existing-rel"),
        pytest.param("http://x.com", {"rel": "nofollow"}, {"rel": "nofollow"}, id="idempotent"),
        pytest.param("mailto:a@b.com", {}, {}, id="skips-non-web"),
    ],
)
def test_nofollow(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    link = nofollow(Link(url, "x", attrs))
    assert link is not None
    assert link.attrs == expected


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"target": "_blank"}, id="adds-target"),
        pytest.param("https://x.com", {}, {"target": "_blank"}, id="https-too"),
        pytest.param("mailto:a@b.com", {"target": "_blank"}, {}, id="strips-target-from-non-web"),
    ],
)
def test_target_blank(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    link = target_blank(Link(url, "x", attrs))
    assert link is not None
    assert link.attrs == expected


def test_bare_domain_path_with_embedded_scheme_keeps_http_prefix() -> None:
    out = linkify("go example.com/r?u=http://evil.com end", callbacks=_no_callbacks())
    assert out == 'go <a href="http://example.com/r?u=http://evil.com">example.com/r?u=http://evil.com</a> end'


@pytest.mark.parametrize(
    ("text", "parse_email", "bare_domains", "spans"),
    [
        pytest.param("http://example.com", False, False, [(0, 18, 0)], id="scheme-url"),
        pytest.param("HTTP://EXAMPLE.COM", False, False, [(0, 18, 0)], id="uppercase-scheme-and-host"),
        pytest.param("ftp://example.com/file", False, False, [(0, 22, 0)], id="ftp-scheme"),
        pytest.param("svn+ssh://example.com", False, False, [(0, 21, 0)], id="scheme-with-plus"),
        pytest.param("view-source://example.com", False, False, [(0, 25, 0)], id="scheme-with-hyphen"),
        pytest.param("a.b://example.com", False, False, [(0, 17, 0)], id="scheme-with-dot"),
        pytest.param("nothttp//example.com", False, True, [(9, 20, 0)], id="no-colon-slash-slash-falls-to-bare"),
        pytest.param("mailto:bob", False, False, [], id="scheme-without-slashes-no-match"),
        pytest.param(":no scheme here", False, False, [], id="leading-colon-no-scheme"),
        pytest.param("xhttp://example.com", False, False, [(0, 19, 0)], id="arbitrary-scheme-matches-like-bleach"),
        pytest.param("日http://example.com", False, False, [], id="non-ascii-before-scheme-blocks"),
        pytest.param("http://.example.com", False, False, [], id="host-starts-with-dot"),
        pytest.param("http://-example.com", False, False, [], id="host-starts-with-hyphen"),
        pytest.param("http://localhost/x", False, False, [], id="host-without-dot"),
        pytest.param("x@example.com", False, True, [], id="bare-blocked-by-at-on-left"),
        pytest.param(".example.com", False, True, [], id="bare-blocked-by-dot-on-left"),
        pytest.param("see example.com here", False, True, [(4, 15, 0)], id="bare-domain"),
        pytest.param("see example.com here", False, False, [], id="bare-domain-disabled"),
        pytest.param("example.unknowntld nope", False, True, [], id="bare-domain-unknown-tld"),
        pytest.param("a.co", False, True, [(0, 4, 0)], id="bare-two-char-tld"),
        pytest.param("x.y nope", False, True, [], id="bare-one-char-tld-rejected"),
        pytest.param("example.123 here", False, True, [], id="numeric-tld-rejected"),
        pytest.param("foo.дом nope", False, True, [], id="non-ascii-tld-rejected"),
        pytest.param("://example.com", False, False, [], id="scheme-has-no-letters"),
        pytest.param("1http://example.com", False, False, [], id="scheme-starts-with-digit"),
        pytest.param("ends in colon foo:", False, False, [], id="colon-at-end-no-room"),
        pytest.param("http:/example.com", False, False, [], id="single-slash-scheme"),
        pytest.param("http://a.bc- x", False, False, [], id="host-last-label-ends-with-hyphen"),
        pytest.param("trailing-.com", False, True, [], id="label-ends-with-hyphen"),
        pytest.param("-leading.com here", False, True, [(1, 12, 0)], id="leading-hyphen-trimmed"),
        pytest.param("a--b.com here", False, True, [(0, 8, 0)], id="double-hyphen-inside-label"),
        pytest.param("food.example.com", False, True, [(0, 16, 0)], id="multi-label-host"),
        pytest.param("дом.example.com", False, True, [(0, 15, 0)], id="non-ascii-label"),
        pytest.param("xn--p1ai.example.com", False, True, [(0, 20, 0)], id="punycode-label-not-tld"),
        pytest.param("ru.xn--p1ai stuff", False, True, [(0, 11, 0)], id="punycode-tld"),
        pytest.param("dot at end example. nope", False, True, [], id="trailing-dot-no-following-label"),
        pytest.param("a@example.com", True, False, [(0, 13, 1)], id="email"),
        pytest.param("a@example.com", False, False, [], id="email-disabled"),
        pytest.param("user.name+tag@example.com", True, False, [(0, 25, 1)], id="email-dotted-local-part"),
        pytest.param("!#$%&'*+-/=?^_`{|}~a@example.com", True, False, [(0, 32, 1)], id="email-special-local-chars"),
        pytest.param("ü2@example.com", True, False, [(0, 14, 1)], id="email-non-ascii-and-digit-local"),
        pytest.param(".bad@example.com", True, False, [(1, 16, 1)], id="email-local-cannot-start-with-dot"),
        pytest.param("x .name@example.com", True, False, [(3, 19, 1)], id="email-dot-after-non-local-stops"),
        pytest.param("@example.com", True, False, [], id="email-no-local-part"),
        pytest.param("a@@example.com", True, False, [], id="email-double-at"),
        pytest.param("ab@cd@example.com", True, False, [], id="email-second-at-blocks-on-left"),
        pytest.param("a@b nope", True, False, [], id="email-no-dotted-host"),
        pytest.param("word http://example.com", False, False, [(5, 23, 0)], id="space-then-url-not-blocked"),
        pytest.param("end. http://example.com", False, False, [(5, 23, 0)], id="not-blocked-after-dot-space"),
        pytest.param("http://example.com/a(b)c", False, False, [(0, 24, 0)], id="balanced-round-in-path"),
        pytest.param("http://example.com/a[b]c", False, False, [(0, 24, 0)], id="balanced-square-in-path"),
        pytest.param("http://example.com/a)b", False, False, [(0, 20, 0)], id="unbalanced-round-stops"),
        pytest.param("http://example.com/a]b", False, False, [(0, 20, 0)], id="unbalanced-square-stops"),
        pytest.param("(http://example.com/path)", False, False, [(1, 24, 0)], id="link-in-parens"),
        pytest.param("http://example.com/path.", False, False, [(0, 23, 0)], id="path-trailing-dot-trimmed"),
        pytest.param("http://example.com/a,b,", False, False, [(0, 22, 0)], id="path-trailing-comma-trimmed"),
        pytest.param("http://example.com/p!?:;*", False, False, [(0, 20, 0)], id="path-trailing-punct-run-trimmed"),
        pytest.param("http://example.com:notaport/x", False, False, [(0, 18, 0)], id="colon-not-a-port-ends-host"),
        pytest.param("http://example.com:8080", False, False, [(0, 23, 0)], id="port-at-end-of-string"),
        pytest.param("http://example.com?q=1", False, False, [(0, 22, 0)], id="query-led-tail"),
        pytest.param("http://example.com no tail", False, False, [(0, 18, 0)], id="host-only-no-tail"),
        pytest.param("see http://example.com.", False, False, [(4, 22, 0)], id="host-trailing-dot-trimmed"),
        pytest.param('http://example.com/a"b', False, False, [(0, 20, 0)], id="tail-stops-at-quote"),
        pytest.param("http://example.com/a`b", False, False, [(0, 20, 0)], id="tail-stops-at-backtick"),
        pytest.param("http://example.com/a b", False, False, [(0, 20, 0)], id="tail-stops-at-space"),
        pytest.param("http://example.com/a\x7fb", False, False, [(0, 20, 0)], id="tail-stops-at-del"),
    ],
)
def test_scanner_spans(
    text: str,
    parse_email: bool,  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    bare_domains: bool,  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    spans: list[tuple[int, int, int]],
) -> None:
    assert _linkify_scan(text, parse_email, bare_domains) == spans


def test_scanner_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_scan(123, False, False)  # noqa: FBT003  # ty: ignore[invalid-argument-type]  # the C arg check is the point
