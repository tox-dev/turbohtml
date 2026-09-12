from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS
from typing_extensions import assert_type

from turbohtml import Document, Element, Text, parse, parse_fragment
from turbohtml._html import _linkify_find, _linkify_fold, _linkify_scan, _phone_e164, _phone_regions
from turbohtml.clean import (
    DEFAULT_CALLBACKS,
    LinkCandidate,
    LinkDetector,
    Linker,
    Linkify,
    LinkSpan,
    PhoneNumber,
    PhoneNumbers,
    PhoneType,
    linkify,
    linkify_node,
    nofollow,
    target_blank,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml.clean import Callback


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
        pytest.param(
            "{scoped http://example.com/foo_bar}",
            '{scoped <a href="http://example.com/foo_bar">http://example.com/foo_bar</a>}',
            id="brace-scoped-url-trims-brace",
        ),
        pytest.param(
            "'quoted http://example.com/foo_bar'",
            "'quoted <a href=\"http://example.com/foo_bar\">http://example.com/foo_bar</a>'",
            id="single-quoted-url-trims-apostrophe",
        ),
        pytest.param(
            "https://cdn_1.example.org/x",
            '<a href="https://cdn_1.example.org/x">https://cdn_1.example.org/x</a>',
            id="underscore-in-host-label-kept",
        ),
        pytest.param(
            "http://_dmarc.example.com/",
            '<a href="http://_dmarc.example.com/">http://_dmarc.example.com/</a>',
            id="underscore-leading-host-label-kept",
        ),
        # Unicode whitespace bounds a URL the way an ASCII space does (issue #53)
        pytest.param(
            "http://example.com\xa0more",
            '<a href="http://example.com">http://example.com</a>&nbsp;more',
            id="nbsp-ends-url",
        ),
        pytest.param(
            "a　http://x.com　b",
            'a　<a href="http://x.com">http://x.com</a>　b',
            id="ideographic-space-ends-url",
        ),
        pytest.param(
            "http://example.com/p\xa0q",
            '<a href="http://example.com/p">http://example.com/p</a>&nbsp;q',
            id="nbsp-ends-url-path",
        ),
        # a non-whitespace non-ASCII code point keeps an internationalized domain intact
        pytest.param("münchen.de", '<a href="http://münchen.de">münchen.de</a>', id="idn-host-label-kept"),
        # zero-width format characters are not White_Space, so they stay in the URL
        pytest.param(
            "http://a.com/x\u200by",
            '<a href="http://a.com/x\u200by">http://a.com/x\u200by</a>',
            id="zero-width-space-stays-in-url",
        ),
    ],
)
def test_linkify_plain(text: str, expected: str) -> None:
    assert linkify(text, Linkify(callbacks=_no_callbacks())) == expected


@pytest.mark.parametrize(
    "cp",
    [
        0x85, 0xA0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
        0x2007, 0x2008, 0x2009, 0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
    ],
)  # fmt: skip
def test_linkify_each_unicode_space_ends_url(cp: int) -> None:
    # every Unicode White_Space code point bounds the host the way an ASCII space does
    out = linkify(f"http://x.com{chr(cp)}y", Linkify(callbacks=_no_callbacks()))
    assert out.startswith('<a href="http://x.com">http://x.com</a>')


def test_linkify_default_callback_adds_nofollow() -> None:
    assert linkify("see http://example.com") == 'see <a href="http://example.com" rel="nofollow">http://example.com</a>'


def test_linkify_single_url() -> None:
    assert linkify("x.com") == '<a href="http://x.com" rel="nofollow">x.com</a>'


def test_linkify_leaves_existing_anchor_untouched() -> None:
    html = '<a href="http://x.com">http://y.com</a> and http://z.com'
    assert linkify(html, Linkify(callbacks=_no_callbacks())) == (
        '<a href="http://x.com">http://y.com</a> and <a href="http://z.com">http://z.com</a>'
    )


@pytest.mark.parametrize("tag", ["script", "style"])
def test_linkify_skips_raw_text_elements(tag: str) -> None:
    html = f"<{tag}>http://x.com</{tag}>http://y.com"
    out = linkify(html, Linkify(callbacks=_no_callbacks()))
    assert f"<{tag}>http://x.com</{tag}>" in out
    assert '<a href="http://y.com">' in out


@pytest.mark.parametrize("skip_tag", ["code", "CODE"], ids=["lowercase", "uppercase"])
def test_linkify_skip_tags(skip_tag: str) -> None:
    html = "<code>http://x.com</code> http://y.com"
    out = linkify(html, Linkify(skip_tags=[skip_tag], callbacks=_no_callbacks()))
    assert out == '<code>http://x.com</code> <a href="http://y.com">http://y.com</a>'


def test_linkify_nested_skip_tag_stays_skipped() -> None:
    html = "<code><span>http://x.com</span></code>"
    assert linkify(html, Linkify(skip_tags=["code"], callbacks=_no_callbacks())) == html


def test_linkify_checks_each_skip_tag_by_exact_name() -> None:
    out = linkify("<code>http://x.com</code>", Linkify(skip_tags=["pre", "samp", "coder"], callbacks=_no_callbacks()))
    assert out == '<code><a href="http://x.com">http://x.com</a></code>'


def test_linkify_walk_does_not_depend_on_python_recursion_limit() -> None:
    html = "<div>" * 1_200 + "http://x.com" + "</div>" * 1_200
    out = linkify(html, Linkify(callbacks=_no_callbacks()))
    assert out.count('<a href="http://x.com">') == 1


def test_linkify_leaves_comment_nodes_untouched() -> None:
    html = "<!-- http://skip.com --> http://link.com"
    out = linkify(html, Linkify(callbacks=_no_callbacks()))
    assert out == '<!-- http://skip.com --> <a href="http://link.com">http://link.com</a>'


def test_linkify_email_off_by_default() -> None:
    assert linkify("reach bob@example.com now") == "reach bob@example.com now"


def test_linkify_email_when_enabled() -> None:
    out = linkify("reach bob@example.com now", Linkify(parse_email=True, callbacks=_no_callbacks()))
    assert out == 'reach <a href="mailto:bob@example.com">bob@example.com</a> now'


def test_linkify_default_callback_leaves_email_rel_absent() -> None:
    out = linkify("reach bob@example.com now", Linkify(parse_email=True))
    assert out == 'reach <a href="mailto:bob@example.com">bob@example.com</a> now'


def test_linkify_email_local_part_ends_at_unicode_space() -> None:
    # Unicode whitespace bounds the email local part the way an ASCII space does (issue #53)
    out = linkify("foo\xa0bar@example.com", Linkify(parse_email=True, callbacks=_no_callbacks()))
    assert out == 'foo&nbsp;<a href="mailto:bar@example.com">bar@example.com</a>'


def test_linkify_email_keeps_non_ascii_local_part() -> None:
    out = linkify("naïve@example.com", Linkify(parse_email=True, callbacks=_no_callbacks()))
    assert out == '<a href="mailto:naïve@example.com">naïve@example.com</a>'


def test_linkify_veto_callback_keeps_plain_text() -> None:
    assert linkify("http://x.com", Linkify(callbacks=[lambda link: None])) == "http://x.com"  # ruff:ignore[unused-lambda-argument]


def test_linkify_callback_can_change_text() -> None:
    def shorten(link: LinkCandidate) -> LinkCandidate:
        link.text = "link"
        return link

    assert linkify("http://x.com", Linkify(callbacks=[shorten])) == '<a href="http://x.com">link</a>'


def test_linkify_callback_can_clear_text() -> None:
    def clear(link: LinkCandidate) -> LinkCandidate:
        link.text = ""
        return link

    assert linkify("http://x.com", Linkify(callbacks=[clear])) == '<a href="http://x.com"></a>'


def test_linkify_callback_can_add_attribute() -> None:
    def add_class(link: LinkCandidate) -> LinkCandidate:
        link.attrs["class"] = "ext"
        return link

    out = linkify("http://x.com", Linkify(callbacks=[add_class]))
    assert out == '<a href="http://x.com" class="ext">http://x.com</a>'


def test_linkify_callback_chain_runs_in_order() -> None:
    out = linkify("http://x.com", Linkify(callbacks=[nofollow, target_blank]))
    assert out == '<a href="http://x.com" rel="nofollow" target="_blank">http://x.com</a>'


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("url", 1, id="url"),
        pytest.param("text", 1, id="text"),
        pytest.param("attrs", [], id="attrs"),
    ],
)
def test_linkify_rejects_invalid_callback_field(field: str, value: object) -> None:
    def invalidate(link: LinkCandidate) -> LinkCandidate:
        setattr(link, field, value)
        return link

    with pytest.raises(TypeError):
        linkify("http://x.com", Linkify(callbacks=[invalidate]))


@pytest.mark.parametrize(
    "attrs",
    [pytest.param({1: "value"}, id="name"), pytest.param({"name": 1}, id="value")],
)
def test_linkify_rejects_invalid_callback_attribute(attrs: dict[object, object]) -> None:
    def invalidate(link: LinkCandidate) -> LinkCandidate:
        link.attrs = attrs  # ty: ignore[invalid-assignment]  # callback validation requires an invalid attrs mapping
        return link

    with pytest.raises(TypeError):
        linkify("http://x.com", Linkify(callbacks=[invalidate]))


@pytest.mark.parametrize(
    ("html", "process_existing"),
    [
        pytest.param("http://x.com", False, id="detected"),
        pytest.param('<a href="http://x.com">x</a>', True, id="existing"),
    ],
)
def test_linkify_propagates_callback_exception(
    html: str,
    *,
    process_existing: bool,
) -> None:
    def fail(_link: LinkCandidate) -> LinkCandidate:
        msg = "callback failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="callback failed"):
        linkify(html, Linkify(callbacks=[fail], process_existing=process_existing))


@pytest.mark.parametrize(
    ("html", "process_existing"),
    [
        pytest.param("http://x.com", False, id="detected"),
        pytest.param('<a href="http://x.com">x</a>', True, id="existing"),
    ],
)
def test_linkify_rejects_non_candidate_callback_result(
    html: str,
    *,
    process_existing: bool,
) -> None:
    def replace(_link: LinkCandidate) -> LinkCandidate:
        return object()  # ty: ignore[invalid-return-type]  # callback validation requires an invalid result type

    with pytest.raises(AttributeError):
        linkify(html, Linkify(callbacks=[replace], process_existing=process_existing))


@pytest.mark.parametrize("field", ["text", "attrs"])
@pytest.mark.parametrize(
    ("html", "process_existing"),
    [
        pytest.param("http://x.com", False, id="detected"),
        pytest.param('<a href="http://x.com">x</a>', True, id="existing"),
    ],
)
def test_linkify_rejects_callback_result_with_missing_field(
    field: str,
    html: str,
    *,
    process_existing: bool,
) -> None:
    def remove(link: LinkCandidate) -> LinkCandidate:
        delattr(link, field)
        return link

    with pytest.raises(AttributeError):
        linkify(html, Linkify(callbacks=[remove], process_existing=process_existing))


def test_linker_is_reusable() -> None:
    linker = Linker(Linkify(callbacks=_no_callbacks()))
    assert linker.linkify("http://a.example.com") == '<a href="http://a.example.com">http://a.example.com</a>'
    assert linker.linkify("http://b.example.com") == '<a href="http://b.example.com">http://b.example.com</a>'


def test_linker_is_reusable_across_concurrent_callbacks() -> None:
    callback_start = Barrier(4)

    def annotate(link: LinkCandidate) -> LinkCandidate:
        callback_start.wait()
        link.attrs["data-url"] = link.url
        link.text = link.text.upper()
        return link

    linker = Linker(Linkify(callbacks=[annotate]))
    urls = [f"http://x{index}.com" for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(linker.linkify, urls))
    assert results == [f'<a href="{url}" data-url="{url}">{url.upper()}</a>' for url in urls]


def test_default_callbacks_is_nofollow() -> None:
    assert (nofollow,) == DEFAULT_CALLBACKS


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"rel": "nofollow"}, id="adds-nofollow"),
        pytest.param("https://x.com", {}, {"rel": "nofollow"}, id="https-too"),
        pytest.param("http://x.com", {"rel": "noopener"}, {"rel": "noopener nofollow"}, id="appends-to-existing-rel"),
        pytest.param("http://x.com", {"rel": "nofollow"}, {"rel": "nofollow"}, id="idempotent"),
        pytest.param("HTTP://X.COM", {}, {"rel": "nofollow"}, id="uppercase-scheme"),
        pytest.param("mailto:a@b.com", {}, {}, id="skips-non-web"),
    ],
)
def test_nofollow(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    link = nofollow(LinkCandidate(url, "x", attrs))
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
    link = target_blank(LinkCandidate(url, "x", attrs))
    assert link is not None
    assert link.attrs == expected


def test_bare_domain_path_with_embedded_scheme_keeps_http_prefix() -> None:
    out = linkify("go example.com/r?u=http://evil.com end", Linkify(callbacks=_no_callbacks()))
    assert out == 'go <a href="http://example.com/r?u=http://evil.com">example.com/r?u=http://evil.com</a> end'


@pytest.mark.parametrize(
    ("text", "parse_email", "bare_domains", "spans"),
    [
        pytest.param("http://example.com", False, False, [(0, 18, 3)], id="scheme-url"),
        pytest.param("HTTP://EXAMPLE.COM", False, False, [(0, 18, 3)], id="uppercase-scheme-and-host"),
        pytest.param("ftp://example.com/file", False, False, [(0, 22, 3)], id="ftp-scheme"),
        pytest.param("svn+ssh://example.com", False, False, [(0, 21, 3)], id="scheme-with-plus"),
        pytest.param("view-source://example.com", False, False, [(0, 25, 3)], id="scheme-with-hyphen"),
        pytest.param("a.b://example.com", False, False, [(0, 17, 3)], id="scheme-with-dot"),
        pytest.param("nothttp//example.com", False, True, [(9, 20, 0)], id="no-colon-slash-slash-falls-to-bare"),
        pytest.param("foo/example.com", False, True, [(4, 15, 0)], id="single-slash-keeps-bare-domain"),
        pytest.param("mailto:bob", False, False, [], id="scheme-without-slashes-no-match"),
        pytest.param(":no scheme here", False, False, [], id="leading-colon-no-scheme"),
        pytest.param("xhttp://example.com", False, False, [(0, 19, 3)], id="arbitrary-scheme-matches-like-bleach"),
        pytest.param("日http://example.com", False, False, [], id="non-ascii-before-scheme-blocks"),
        pytest.param("http://.example.com", False, False, [], id="host-starts-with-dot"),
        pytest.param("http://-example.com", False, False, [], id="host-starts-with-hyphen"),
        pytest.param("http://localhost/x", False, False, [(0, 18, 3)], id="host-without-dot"),
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
        pytest.param("http://a.bc- x", False, False, [(0, 11, 3)], id="host-ends-before-a-trailing-hyphen"),
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
        pytest.param("word http://example.com", False, False, [(5, 23, 3)], id="space-then-url-not-blocked"),
        pytest.param("end. http://example.com", False, False, [(5, 23, 3)], id="not-blocked-after-dot-space"),
        pytest.param("http://example.com/a(b)c", False, False, [(0, 24, 3)], id="balanced-round-in-path"),
        pytest.param("http://example.com/a[b]c", False, False, [(0, 24, 3)], id="balanced-square-in-path"),
        pytest.param("http://example.com/a)b", False, False, [(0, 20, 3)], id="unbalanced-round-stops"),
        pytest.param("http://example.com/a]b", False, False, [(0, 20, 3)], id="unbalanced-square-stops"),
        pytest.param("http://example.com/a{b}c", False, False, [(0, 24, 3)], id="balanced-curly-in-path"),
        pytest.param("http://example.com/a}b", False, False, [(0, 20, 3)], id="unbalanced-curly-stops"),
        pytest.param(
            "{http://example.com/foo_bar}", False, False, [(1, 27, 3)], id="brace-scoped-trailing-brace-trimmed"
        ),
        pytest.param("http://example.com/foo'", False, False, [(0, 22, 3)], id="trailing-apostrophe-trimmed"),
        pytest.param("http://example.com/o'reilly", False, False, [(0, 27, 3)], id="apostrophe-mid-path-kept"),
        pytest.param("(http://example.com/path)", False, False, [(1, 24, 3)], id="link-in-parens"),
        pytest.param("http://example.com/path.", False, False, [(0, 23, 3)], id="path-trailing-dot-trimmed"),
        pytest.param("http://example.com/a,b,", False, False, [(0, 22, 3)], id="path-trailing-comma-trimmed"),
        pytest.param("http://example.com/p!?:;", False, False, [(0, 20, 3)], id="path-trailing-punct-run-trimmed"),
        # '*' is an RFC 3986 sub-delim that bleach and linkify_it keep, so a trailing one stays in the link
        pytest.param("http://example.com/path*", False, False, [(0, 24, 3)], id="path-trailing-star-kept"),
        pytest.param("http://example.com:notaport/x", False, False, [(0, 18, 3)], id="colon-not-a-port-ends-host"),
        pytest.param("http://example.com:8080", False, False, [(0, 23, 3)], id="port-at-end-of-string"),
        pytest.param("http://example.com?q=1", False, False, [(0, 22, 3)], id="query-led-tail"),
        pytest.param("http://example.com no tail", False, False, [(0, 18, 3)], id="host-only-no-tail"),
        pytest.param("see http://example.com.", False, False, [(4, 22, 3)], id="host-trailing-dot-trimmed"),
        pytest.param('http://example.com/a"b', False, False, [(0, 20, 3)], id="tail-stops-at-quote"),
        pytest.param("http://example.com/a`b", False, False, [(0, 20, 3)], id="tail-stops-at-backtick"),
        pytest.param("http://example.com/a b", False, False, [(0, 20, 3)], id="tail-stops-at-space"),
        pytest.param("http://example.com/a\x7fb", False, False, [(0, 20, 3)], id="tail-stops-at-del"),
        pytest.param("http://user:pass@host.com/x", True, False, [(0, 27, 3)], id="userinfo-url"),
        pytest.param("http://u@example.com", False, False, [(0, 20, 3)], id="userinfo-at-only"),
        pytest.param("http://a.b@example.com", False, False, [(0, 22, 3)], id="userinfo-is-a-valid-host"),
        pytest.param("http://1.2.3.4/path", False, False, [(0, 19, 3)], id="schemeful-ipv4"),
        pytest.param("at 1.2.3.4 here", False, True, [], id="bare-ipv4-needs-tld"),
        pytest.param("http://example.com#frag", False, False, [(0, 23, 3)], id="fragment-after-host"),
        pytest.param("http://example.com:8080?q=1", False, False, [(0, 27, 3)], id="port-then-query-no-userinfo"),
        pytest.param("http://example.com:8080#f", False, False, [(0, 25, 3)], id="port-then-fragment-no-userinfo"),
        pytest.param("http://example.com:8080 x", False, False, [(0, 23, 3)], id="port-then-space-no-userinfo"),
        pytest.param("see EXAMPLE.COM here", False, True, [(4, 15, 0)], id="bare-domain-uppercase-tld"),
        pytest.param("https://cdn_1.example.org/x", False, False, [(0, 27, 3)], id="underscore-in-scheme-host"),
        pytest.param("http://_dmarc.example.com/", False, False, [(0, 26, 3)], id="underscore-leading-scheme-host"),
        pytest.param("cdn_1.example.org/x", False, True, [(0, 19, 0)], id="underscore-in-bare-host"),
        pytest.param("_dmarc.example.com here", False, True, [(0, 18, 0)], id="underscore-leading-bare-host"),
    ],
)
def test_scanner_spans(
    text: str,
    parse_email: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    bare_domains: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    assert _linkify_scan(text, parse_email, bare_domains) == with_hrefs(text, spans)


@pytest.mark.parametrize(
    ("text", "url_schemes", "spans"),
    [
        pytest.param("http://example.com", ("http", "https", "ftp"), [(0, 18, 3)], id="allowed-scheme-matches"),
        pytest.param("ftp://example.com/file", ("http",), [], id="scheme-not-in-allowlist-skipped"),
        pytest.param("javascript://example.com", ("http", "https", "ftp"), [], id="javascript-scheme-excluded"),
        pytest.param("HTTP://example.com", ("http",), [(0, 18, 3)], id="allowlist-is-case-insensitive"),
        pytest.param("xhttp://example.com", (), [], id="empty-allowlist-matches-nothing"),
    ],
)
def test_scanner_url_schemes_restrict_authority(
    text: str,
    url_schemes: tuple[str, ...],
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    # a non-None url_schemes tuple restricts scheme://host matching to that allowlist; omitting it matches any scheme
    assert _linkify_scan(text, False, False, (), url_schemes) == with_hrefs(text, spans)  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding


def test_scanner_omitting_url_schemes_matches_any_scheme() -> None:
    assert _linkify_scan("xyzzy://example.com", False, False) == [(0, 19, 3, "xyzzy://example.com", None, False)]  # ruff:ignore[boolean-positional-value-in-call]  # positional C binding


def test_scanner_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_scan(123, False, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # the C arg check is the point


def test_scanner_rejects_non_tuple_url_schemes() -> None:
    with pytest.raises(TypeError):
        _linkify_scan("http://x.com", False, False, (), ["http"])  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]


def test_find_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_find(123, True, True, (), (), ("http",), None, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]


def test_find_rejects_non_tuple_tlds() -> None:
    with pytest.raises(TypeError):
        _linkify_find("text", True, True, ["corp"], (), ("http",), None, False)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]


def _tuples(spans: list[LinkSpan]) -> list[tuple[int, int, str, str, bool]]:
    return [(span.start, span.end, span.text, span.url, span.is_email) for span in spans]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", [], id="empty"),
        pytest.param("nothing to see here", [], id="no-links"),
        pytest.param(
            "see http://example.com now",
            [(4, 22, "http://example.com", "http://example.com", False)],
            id="scheme-url-keeps-its-scheme",
        ),
        pytest.param(
            "go to example.com today",
            [(6, 17, "example.com", "http://example.com", False)],
            id="bare-domain-gets-http",
        ),
        pytest.param(
            "visit www.example.com",
            [(6, 21, "www.example.com", "http://www.example.com", False)],
            id="www-bare-domain",
        ),
        pytest.param(
            "mail bob@example.com!",
            [(5, 20, "bob@example.com", "mailto:bob@example.com", True)],
            id="email-gets-mailto",
        ),
        pytest.param(
            "a.example.com and b.example.org",
            [
                (0, 13, "a.example.com", "http://a.example.com", False),
                (18, 31, "b.example.org", "http://b.example.org", False),
            ],
            id="two-bare-domains",
        ),
        pytest.param(
            "ftp://host.example.com/file",
            [(0, 27, "ftp://host.example.com/file", "ftp://host.example.com/file", False)],
            id="non-http-scheme-kept-verbatim",
        ),
    ],
)
def test_find_returns_spans(text: str, expected: list[tuple[int, int, str, str, bool]]) -> None:
    assert _tuples(LinkDetector().find(text)) == expected


def test_find_offsets_slice_back_to_text() -> None:
    text = "reach me at bob@example.com or https://example.org/x"
    for span in LinkDetector().find(text):
        assert text[span.start : span.end] == span.text


def test_find_respects_emails_disabled() -> None:
    assert LinkDetector(emails=False).find("write bob@example.com") == []


def test_find_respects_bare_domains_disabled() -> None:
    assert LinkDetector(bare_domains=False).find("go to example.com") == []


def test_find_scheme_url_still_found_without_bare_domains() -> None:
    spans = LinkDetector(bare_domains=False).find("see https://example.com here")
    assert _tuples(spans) == [(4, 23, "https://example.com", "https://example.com", False)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("{Scoped like http://example.com/foo_bar}", id="trailing-brace"),
        pytest.param("'Quoted like http://example.com/foo_bar'", id="trailing-apostrophe"),
    ],
)
def test_find_trims_a_trailing_brace_or_apostrophe(text: str) -> None:
    assert _tuples(LinkDetector().find(text)) == [
        (13, 39, "http://example.com/foo_bar", "http://example.com/foo_bar", False),
    ]


def test_find_keeps_a_balanced_brace_in_the_path() -> None:
    spans = LinkDetector().find("http://example.com/{id}")
    assert _tuples(spans) == [(0, 23, "http://example.com/{id}", "http://example.com/{id}", False)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("http://example.com followed by " + "tail " * 40_000, id="start-with-large-tail"),
        pytest.param("prefix " * 20 + "http://example.com" + " suffix " * 20, id="middle"),
        pytest.param("prefix " * 40 + "http://example.com", id="end"),
        pytest.param("bare example.com here", id="bare-domain"),
        pytest.param("mail bob@example.com", id="email"),
    ],
)
def test_has_link_detects_a_link(text: str) -> None:
    assert LinkDetector().has_link(text) is True


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("nothing here at all", id="no-link"),
        pytest.param("", id="empty"),
        pytest.param("hppt://example.invalid bob@localhost example.invalid", id="rejected-candidates"),
    ],
)
def test_has_link_is_false_without_a_link(text: str) -> None:
    assert LinkDetector().has_link(text) is False


def test_has_link_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        LinkDetector().has_link(123)  # ty: ignore[invalid-argument-type]  # Exercise the runtime type boundary.


def test_has_link_respects_configuration() -> None:
    detector = LinkDetector(emails=False, bare_domains=False)
    assert detector.has_link("write bob@example.com or visit example.com") is False
    assert detector.has_link("but https://example.com still counts") is True


def _detector_schemes_tuples(spans: list[LinkSpan]) -> list[tuple[int, int, str, str, bool]]:
    return [(span.start, span.end, span.text, span.url, span.is_email) for span in spans]


def test_registered_scheme_less_url_is_found() -> None:
    spans = LinkDetector(schemes=["tel"]).find("call tel:+1-800-555-0100 now")
    assert _detector_schemes_tuples(spans) == [(5, 24, "tel:+1-800-555-0100", "tel:+1-800-555-0100", False)]


def test_scheme_registration_is_case_and_colon_insensitive() -> None:
    spans = LinkDetector(schemes=["TEL:"]).find("tel:12345")
    assert _detector_schemes_tuples(spans) == [(0, 9, "tel:12345", "tel:12345", False)]


def test_unregistered_scheme_is_not_matched() -> None:
    assert LinkDetector().find("tel:12345") == []


def test_scheme_less_skipped_when_scheme_not_registered() -> None:
    assert LinkDetector(schemes=["tel"]).find("time: 5 minutes") == []


def test_scheme_with_no_leading_scheme_chars_is_skipped() -> None:
    assert LinkDetector(schemes=["tel"]).find("a :b") == []


def test_scheme_blocked_by_preceding_label_char() -> None:
    # the underscore is a host-label character, so it is not part of the scheme yet blocks a link there
    assert LinkDetector(schemes=["tel"]).find("_tel:y") == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("tel:", id="nothing-after-colon"),
        pytest.param("tel: spaced", id="space-after-colon"),
    ],
)
def test_scheme_with_empty_opaque_part_is_skipped(text: str) -> None:
    assert LinkDetector(schemes=["tel"]).find(text) == []


def test_scheme_url_with_authority_takes_priority() -> None:
    spans = LinkDetector(schemes=["http"]).find("http://example.com")
    assert _detector_schemes_tuples(spans) == [(0, 18, "http://example.com", "http://example.com", False)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("hppt://example.com", id="typo-scheme"),
        pytest.param("javascript://example.com", id="javascript-scheme"),
        pytest.param("xyzzy://foo.com", id="unregistered-scheme"),
    ],
)
def test_unknown_authority_scheme_is_not_detected(text: str) -> None:
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize("scheme", ["http", "https", "ftp"])
def test_builtin_authority_scheme_is_detected(scheme: str) -> None:
    text = f"{scheme}://example.com"
    assert _detector_schemes_tuples(LinkDetector().find(text)) == [(0, len(text), text, text, False)]


def test_registered_scheme_extends_authority_matching() -> None:
    spans = LinkDetector(schemes=["git"]).find("git://example.com")
    assert _detector_schemes_tuples(spans) == [(0, 17, "git://example.com", "git://example.com", False)]


def test_repr_shows_offsets_and_url() -> None:
    span = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    assert repr(span) == "LinkSpan(start=0, end=3, text='abc', url='http://abc')"


def test_equal_spans_compare_equal() -> None:
    left = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    right = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    assert left == right


def test_spans_differing_in_a_field_are_unequal() -> None:
    left = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    right = LinkSpan(0, 3, "abc", "https://abc", is_email=False)
    assert left != right


def test_span_compared_to_other_type_is_not_equal() -> None:
    span = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    assert span != "abc"
    assert (span == "abc") is False


def test_span_is_unhashable() -> None:
    with pytest.raises(TypeError):
        hash(LinkSpan(0, 3, "abc", "http://abc", is_email=False))


def _detector_tlds_tuples(spans: list[LinkSpan]) -> list[tuple[int, int, str, str, bool]]:
    return [(span.start, span.end, span.text, span.url, span.is_email) for span in spans]


def test_custom_tld_makes_a_bare_domain_match() -> None:
    spans = LinkDetector(tlds=["corp"]).find("visit intranet.corp today")
    assert _detector_tlds_tuples(spans) == [(6, 19, "intranet.corp", "http://intranet.corp", False)]


def test_custom_tld_is_normalized() -> None:
    spans = LinkDetector(tlds=[".CORP"]).find("intranet.corp")
    assert _detector_tlds_tuples(spans) == [(0, 13, "intranet.corp", "http://intranet.corp", False)]


def test_custom_tld_applies_to_email() -> None:
    spans = LinkDetector(tlds=["corp"]).find("bob@mail.corp")
    assert _detector_tlds_tuples(spans) == [(0, 13, "bob@mail.corp", "mailto:bob@mail.corp", True)]


def test_non_ascii_custom_tld_matches() -> None:
    spans = LinkDetector(tlds=["рф"]).find("сайт.рф")
    assert _detector_tlds_tuples(spans) == [(0, 7, "сайт.рф", "http://сайт.рф", False)]


def test_unknown_tld_without_registration_is_not_a_link() -> None:
    assert LinkDetector().find("file.zzunknown") == []


def test_single_letter_last_label_is_not_a_tld() -> None:
    assert LinkDetector().find("go a.b here") == []


@pytest.mark.parametrize(
    ("tlds", "text"),
    [
        pytest.param(["xyz", "corp"], "foo.corp", id="length-mismatch-then-hit"),
        pytest.param(["corq"], "foo.corp", id="same-length-near-miss"),
    ],
)
def test_custom_tld_candidate_scanning(tlds: list[str], text: str) -> None:
    spans = LinkDetector(tlds=tlds).find(text)
    matched = [span.text for span in spans]
    assert matched == (["foo.corp"] if "corp" in tlds else [])


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("http://localhost", id="localhost"),
        pytest.param("http://localhost:8000/path", id="localhost-with-port-and-path"),
        pytest.param("https://localhost/", id="localhost-over-https"),
        pytest.param("http://intranet/x", id="intranet-name"),
        pytest.param("http://host-name", id="name-with-a-hyphen"),
        pytest.param("http://inrgess2", id="name-ending-in-a-digit"),
        pytest.param("http://999", id="all-digit-name"),
    ],
)
def test_a_scheme_carries_a_single_label_host(text: str) -> None:
    # a dot tells a bare domain apart from a word; an authority the author wrote a scheme for needs no such proof
    assert [span.text for span in LinkDetector().find(text)] == [text]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("http://[::1]", id="loopback"),
        pytest.param("http://[::1]:8080/path?query=1", id="port-and-query"),
        pytest.param("https://[2001:db8::1]/abc", id="documentation-prefix"),
        pytest.param("ftp://[::ffff:192.168.1.1]/", id="ipv4-mapped"),
        pytest.param("http://[fe80::1%25eth0]/", id="zone-id"),
        pytest.param("http://user@[::1]/x", id="behind-userinfo"),
    ],
)
def test_a_scheme_carries_an_ip_literal_host(text: str) -> None:
    assert [span.text for span in LinkDetector().find(text)] == [text]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("http://", id="nothing-after-the-slashes"),
        pytest.param("http://.", id="a-lone-dot"),
        pytest.param("http://..", id="two-dots"),
        pytest.param("http://#", id="straight-to-a-fragment"),
        pytest.param("http://[]/", id="empty-brackets"),
        pytest.param("http://[zz]/", id="not-hex-in-brackets"),
        pytest.param("http://[::1/", id="unclosed-bracket-then-a-path"),
        pytest.param("http://[::1", id="unclosed-bracket-at-the-end"),
        pytest.param("http://[fe80::1%25a%25b]/", id="two-zone-markers"),
    ],
)
def test_a_scheme_without_a_host_is_not_a_link(text: str) -> None:
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("localhost:8000", id="port-only"),
        pytest.param("localhost", id="bare"),
        pytest.param("intranet/x", id="with-a-path"),
    ],
)
def test_a_single_label_host_needs_its_scheme_written(text: str) -> None:
    # without a scheme there is nothing to tell the name apart from an ordinary word
    assert LinkDetector().find(text) == []


def test_a_localhost_url_rewrites() -> None:
    out = linkify("open http://localhost:8000/admin now")
    assert out == 'open <a href="http://localhost:8000/admin" rel="nofollow">http://localhost:8000/admin</a> now'


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"rel": "nofollow"}, id="http"),
        pytest.param("https://x.com", {}, {"rel": "nofollow"}, id="https"),
        pytest.param("HTTPS://X.COM", {}, {"rel": "nofollow"}, id="upper-case-scheme"),
        pytest.param("http://x.com", {"rel": "noopener"}, {"rel": "noopener nofollow"}, id="keeps-an-existing-rel"),
        pytest.param("http://x.com", {"rel": "nofollow"}, {"rel": "nofollow"}, id="never-doubles"),
        pytest.param("http://x.com", {"rel": ""}, {"rel": "nofollow"}, id="empty-rel"),
        pytest.param("mailto:a@b.com", {}, {}, id="address-left-alone"),
        pytest.param("tel:+1", {}, {}, id="phone-left-alone"),
        pytest.param("ht:x", {}, {}, id="shorter-than-a-web-scheme"),
        pytest.param("httpx://x.com", {}, {}, id="longer-than-a-web-scheme"),
        pytest.param("http", {}, {}, id="scheme-with-no-colon"),
    ],
)
def test_linkify_callbacks_nofollow(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    assert nofollow(LinkCandidate(url, "text", attrs)).attrs == expected


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"target": "_blank"}, id="web-link-opens-a-tab"),
        pytest.param("https://x.com", {"rel": "a"}, {"rel": "a", "target": "_blank"}, id="other-attrs-kept"),
        pytest.param("mailto:a@b.com", {"target": "_blank"}, {}, id="stale-target-cleared"),
        pytest.param("tel:+1", {}, {}, id="non-web-without-a-target"),
    ],
)
def test_linkify_callbacks_target_blank(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    assert target_blank(LinkCandidate(url, "text", attrs)).attrs == expected


def test_a_callback_answers_the_same_link() -> None:
    link = LinkCandidate("http://x.com", "text", {})
    assert nofollow(link) is link
    assert target_blank(link) is link


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
@pytest.mark.parametrize(
    "link",
    [
        pytest.param(object(), id="not-a-candidate"),
        pytest.param(SystemError, id="a-type"),
    ],
)
def test_a_callback_rejects_a_foreign_object(callback: object, link: object) -> None:
    with pytest.raises(TypeError, match="expects a LinkCandidate"):
        callback(link)  # ty: ignore[call-non-callable]  # the argument check is the point


class _UrlOnly:
    """A stand-in carrying a url but no attrs, the way a caller's own object might."""

    url = "http://x.com"


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
@pytest.mark.parametrize(
    "link",
    [
        pytest.param(LinkCandidate(1, "text", {}), id="url-is-not-a-string"),  # ty: ignore[invalid-argument-type]
        pytest.param(LinkCandidate("http://x.com", "text", "attrs"), id="attrs-is-not-a-dict"),  # ty: ignore[invalid-argument-type]
        pytest.param(_UrlOnly(), id="no-attrs-at-all"),
    ],
)
def test_a_callback_rejects_wrongly_typed_fields(callback: object, link: object) -> None:
    with pytest.raises(TypeError, match="expects a LinkCandidate"):
        callback(link)  # ty: ignore[call-non-callable]  # the field check is the point


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
def test_a_callback_takes_one_link(callback: object) -> None:
    with pytest.raises(TypeError):
        callback()  # ty: ignore[call-non-callable]  # the arity check is the point


@pytest.mark.parametrize(
    ("values", "kind", "expected"),
    [
        pytest.param(["Pre", "code", "pre"], 0, ("code", "pre"), id="lowercased-deduplicated-sorted"),
        pytest.param([".Dev", "dev", "app"], 1, ("app", "dev"), id="a-leading-dot-falls-off-a-tld"),
        pytest.param(["Git:", "ssh::", "git"], 2, ("git", "ssh"), id="trailing-colons-fall-off-a-scheme"),
        pytest.param([" Fax ", "tel", "fax"], 3, ("fax", "tel"), id="words-are-stripped"),
        pytest.param([], 0, (), id="nothing"),
        pytest.param(["."], 1, ("",), id="a-lone-dot-is-an-empty-tld"),
        pytest.param([""], 1, ("",), id="an-empty-tld"),
        pytest.param([":", "::"], 2, ("",), id="lone-colons-are-an-empty-scheme"),
    ],
)
def test_fold(values: list[str], kind: int, expected: tuple[str, ...]) -> None:
    assert _linkify_fold(values, "field", kind) == expected


def test_fold_names_the_field_in_its_error() -> None:
    with pytest.raises(TypeError, match="skip_tags entries must be str"):
        _linkify_fold(["pre", 5], "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_fold_takes_three_arguments() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(["a"], "field")  # ty: ignore[missing-argument]  # the arity check is the point


def test_fold_needs_an_iterable() -> None:
    with pytest.raises(TypeError):
        _linkify_fold(5, "skip_tags", 0)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_strip_uppercase_and_keep_first_seen_order() -> None:
    assert _phone_regions([" gb", "US", "gb", "de "]) == ("GB", "US", "DE")


def test_a_non_ascii_region_is_left_alone() -> None:
    # `ß` would otherwise fold to South Sudan's `SS`
    assert _phone_regions(["ß", "us"]) == ("ß", "US")


def test_regions_entries_must_be_str() -> None:
    with pytest.raises(TypeError, match="regions entries must be str"):
        _phone_regions(["US", 1])  # ty: ignore[invalid-argument-type]  # the check is the point


def test_regions_need_an_iterable() -> None:
    with pytest.raises(TypeError):
        _phone_regions(5)  # ty: ignore[invalid-argument-type]  # the check is the point


@pytest.mark.parametrize(
    ("country_code", "national", "expected"),
    [
        pytest.param(44, "2079460958", "+442079460958", id="fits"),
        pytest.param(1, "2" * 14, "+1" + "2" * 14, id="exactly-fifteen-digits"),
        pytest.param(1, "2" * 15, None, id="one-digit-too-many"),
    ],
)
def test_e164(country_code: int, national: str, expected: str | None) -> None:
    assert _phone_e164(country_code, national) == expected


def test_e164_rejects_a_non_str_number() -> None:
    with pytest.raises(TypeError):
        _phone_e164(1, 5)  # ty: ignore[invalid-argument-type]  # the check is the point


def test_the_public_e164_reads_the_same() -> None:
    number = PhoneNumber.parse("+44 20 7946 0958")
    assert number.e164 == "+442079460958"


def test_the_type_mask_restricts_the_detector() -> None:
    settings = PhoneNumbers(regions=("GB",), types=frozenset({PhoneType.MOBILE}))
    found = LinkDetector(phones=settings).find("call 020 7946 0958 or 07400 123456")
    assert [span.text for span in found] == ["07400 123456"]


def _linkify_detection_no_callbacks() -> list[Callback]:
    return []


def test_extra_tlds_links_custom_bare_domain() -> None:
    out = linkify(
        "visit foo.internal here", Linkify(callbacks=_linkify_detection_no_callbacks(), extra_tlds=["internal"])
    )
    assert out == 'visit <a href="http://foo.internal">foo.internal</a> here'


def test_extra_tlds_is_case_insensitive() -> None:
    out = linkify("foo.LOCAL", Linkify(callbacks=_linkify_detection_no_callbacks(), extra_tlds=["local"]))
    assert out == '<a href="http://foo.LOCAL">foo.LOCAL</a>'


def test_extra_tlds_absent_leaves_unknown_tld_plain() -> None:
    assert linkify("foo.internal here", Linkify(callbacks=_linkify_detection_no_callbacks())) == "foo.internal here"


def test_extra_tlds_still_links_builtin_tld() -> None:
    out = linkify("example.com", Linkify(callbacks=_linkify_detection_no_callbacks(), extra_tlds=["internal"]))
    assert out == '<a href="http://example.com">example.com</a>'


def test_schemes_blocks_unlisted_scheme() -> None:
    out = linkify(
        "ftp://x.com and http://y.com", Linkify(callbacks=_linkify_detection_no_callbacks(), schemes=["http"])
    )
    assert out == 'ftp://x.com and <a href="http://y.com">http://y.com</a>'


def test_schemes_is_case_insensitive() -> None:
    out = linkify("HTTP://x.com", Linkify(callbacks=_linkify_detection_no_callbacks(), schemes=["http"]))
    assert out == '<a href="HTTP://x.com">HTTP://x.com</a>'


def test_schemes_does_not_gate_bare_domains() -> None:
    out = linkify("see example.com", Linkify(callbacks=_linkify_detection_no_callbacks(), schemes=["https"]))
    assert out == 'see <a href="http://example.com">example.com</a>'


def test_schemes_none_allows_the_builtin_default_set() -> None:
    out = linkify("ftp://x.com", Linkify(callbacks=_linkify_detection_no_callbacks()))
    assert out == '<a href="ftp://x.com">ftp://x.com</a>'


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("javascript://example.com", id="javascript-scheme"),
        pytest.param("xyzzy://foo.com", id="typo-scheme"),
    ],
)
def test_schemes_none_rejects_an_unknown_scheme(text: str) -> None:
    assert linkify(text, Linkify(callbacks=_linkify_detection_no_callbacks())) == text


def test_schemes_registers_a_custom_scheme() -> None:
    out = linkify(
        "git://example.com and http://x.com", Linkify(callbacks=_linkify_detection_no_callbacks(), schemes=["git"])
    )
    assert out == '<a href="git://example.com">git://example.com</a> and http://x.com'


@pytest.mark.parametrize(
    ("text", "parse_email", "extra_tlds", "spans"),
    [
        pytest.param("foo.internal x", False, ("internal",), [(0, 12, 0)], id="custom-ascii-tld"),
        pytest.param("foo.internal x", False, (), [], id="absent-without-extra"),
        pytest.param("a.zzz here", False, ("foo",), [], id="custom-tld-no-match"),
        pytest.param("пример.дом ok", False, ("дом",), [(0, 10, 0)], id="custom-non-ascii-tld"),
        pytest.param("m@foo.internal", True, ("internal",), [(0, 14, 1)], id="custom-tld-email"),
        pytest.param("example.com", False, ("internal",), [(0, 11, 0)], id="builtin-tld-still-wins"),
    ],
)
def test_scanner_extra_tlds(
    text: str,
    parse_email: bool,  # ruff:ignore[boolean-type-hint-positional-argument]  # a pytest parametrize value, not a boolean-trap call site
    extra_tlds: tuple[str, ...],
    spans: list[tuple[int, int, int]],
    with_hrefs: Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None]]],
) -> None:
    assert _linkify_scan(text, parse_email, True, extra_tlds) == with_hrefs(text, spans)  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding under test


def test_scanner_extra_tlds_defaults_to_none() -> None:
    assert _linkify_scan("foo.internal", False, True) == []  # ruff:ignore[boolean-positional-value-in-call]  # positional-only C binding under test


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("http://example.com-", "http://example.com", id="scheme-url"),
        pytest.param("wrap (example.com-)", "example.com", id="bare-domain-in-parens"),
        pytest.param("example.com- and more", "example.com", id="bare-domain-in-prose"),
        pytest.param("example.com--", "example.com", id="two-trailing-hyphens"),
    ],
)
def test_a_trailing_hyphen_ends_the_host(text: str, matched: str) -> None:
    # the hyphen is punctuation the host stops before, not a reason to drop the whole link
    assert [span.text for span in LinkDetector().find(text)] == [matched]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("_example.com", id="leading-label"),
        pytest.param("under_score.com", id="second-to-last-label"),
        pytest.param("example.co_m", id="top-level-label"),
    ],
)
def test_an_underscore_in_the_last_two_labels_is_not_a_bare_domain(text: str) -> None:
    # a domain name may hold an underscore, a host name may not
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("_dmarc.example.com", id="dns-record-name"),
        pytest.param("cdn_1.example.org/x", id="third-to-last-label"),
    ],
)
def test_an_underscore_further_left_still_links(text: str) -> None:
    assert [span.text for span in LinkDetector().find(text)] == [text]


def test_an_underscore_behind_an_explicit_scheme_is_the_author_s_call() -> None:
    assert [span.text for span in LinkDetector().find("http://exa_mple.com/")] == ["http://exa_mple.com/"]


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("example.com:8080/x", "example.com:8080/x", id="in-range"),
        pytest.param("example.com:65535/x", "example.com:65535/x", id="highest-port"),
        pytest.param("example.com:65536/x", "example.com", id="one-past-the-highest"),
        pytest.param("google.com:500000", "google.com", id="far-out-of-range"),
        pytest.param("example.com:00000000080", "example.com:00000000080", id="leading-zeros"),
        pytest.param("example.com:99999999999999999999", "example.com", id="wider-than-the-range"),
    ],
)
def test_only_a_port_in_range_joins_the_host(text: str, matched: str) -> None:
    assert [span.text for span in LinkDetector().find(text)] == [matched]


_TEXT = "<p>See https://example.com and <a href='/x'>kept</a></p>"


def test_node_form_links_in_place_and_returns_the_node() -> None:
    root = parse_fragment(_TEXT)
    assert assert_type(linkify_node(root), Element) is root
    assert root.inner_html == linkify(_TEXT)


def test_a_document_links_its_body_text() -> None:
    document = parse(_TEXT)
    linked = assert_type(linkify_node(document), Document)
    assert isinstance(linked, Document)
    assert linked.serialize().count("<a ") == 2


def test_the_string_form_over_a_node_leaves_it_untouched() -> None:
    root = parse_fragment(_TEXT)
    before = root.inner_html
    assert linkify(root) == linkify(_TEXT)
    assert root.inner_html == before


def test_options_apply_to_the_node_form() -> None:
    root = parse_fragment("<p>mail bob@example.com</p>")
    linkify_node(root, Linkify(parse_email=True))
    assert 'href="mailto:bob@example.com"' in root.inner_html


def test_a_reusable_linker_offers_the_node_form() -> None:
    linker = Linker()
    root = parse_fragment(_TEXT)
    assert assert_type(linker.linkify_node(root), Element).inner_html == linker.linkify(_TEXT)
    document: Final = parse(_TEXT)
    assert assert_type(linker.linkify_node(document), Document) is document
    assert document.serialize().count("<a ") == 2


def test_the_node_form_refuses_a_str() -> None:
    with pytest.raises(TypeError, match="pass a str to linkify instead"):
        linkify_node(_TEXT)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize("entry", [linkify, linkify_node], ids=["linkify", "linkify_node"])
def test_a_foreign_object_is_rejected(entry: object) -> None:
    with pytest.raises(TypeError):
        entry(42)  # ty: ignore[call-non-callable]  # the argument check is the point


def _linkify_process_existing_no_callbacks() -> list[Callback]:
    return []


def test_link_existing_defaults_to_false() -> None:
    assert LinkCandidate("http://x.com", "x").existing is False


def test_link_existing_can_be_set() -> None:
    assert LinkCandidate("http://x.com", "x", existing=True).existing is True


def test_existing_anchor_untouched_without_flag() -> None:
    html = '<a href="http://x.com">x</a>'
    assert linkify(html, Linkify(callbacks=[nofollow])) == html


def test_process_existing_runs_callbacks_over_anchor() -> None:
    html = '<a href="http://x.com">x</a>'
    assert linkify(html, Linkify(process_existing=True)) == '<a href="http://x.com" rel="nofollow">x</a>'


def test_process_existing_veto_unwraps_anchor() -> None:
    html = '<a href="http://x.com">click</a>'
    assert linkify(html, Linkify(callbacks=[lambda _link: None], process_existing=True)) == "click"


def test_process_existing_can_change_text() -> None:
    def relabel(link: LinkCandidate) -> LinkCandidate:
        link.text = "link"
        return link

    html = '<a href="http://x.com">original</a>'
    assert linkify(html, Linkify(callbacks=[relabel], process_existing=True)) == '<a href="http://x.com">link</a>'


def test_process_existing_propagates_text_comparison_error() -> None:
    class UncomparableText(str):  # ruff:ignore[subclass-builtin]  # native comparison accepts str subclasses
        __slots__ = ()

        def __eq__(self, _other: object) -> bool:
            msg = "comparison failed"
            raise RuntimeError(msg)

        __hash__ = str.__hash__

    def replace(link: LinkCandidate) -> LinkCandidate:
        link.text = UncomparableText(link.text)
        return link

    with pytest.raises(RuntimeError, match="comparison failed"):
        linkify('<a href="http://x.com">x</a>', Linkify(callbacks=[replace], process_existing=True))


def test_process_existing_keeps_inner_markup_when_text_unchanged() -> None:
    def add_rel(link: LinkCandidate) -> LinkCandidate:
        link.attrs["rel"] = "ext"
        return link

    html = '<a href="http://x.com"><b>x</b></a>'
    out = linkify(html, Linkify(callbacks=[add_rel], process_existing=True))
    assert out == '<a href="http://x.com" rel="ext"><b>x</b></a>'


def test_process_existing_callback_can_remove_attr() -> None:
    html = '<a href="mailto:a@b.com" target="_blank">mail</a>'
    out = linkify(html, Linkify(callbacks=[target_blank], process_existing=True))
    assert out == '<a href="mailto:a@b.com">mail</a>'


def test_process_existing_preserves_token_list_attr() -> None:
    html = '<a href="http://x.com" class="a b">x</a>'
    out = linkify(html, Linkify(callbacks=_linkify_process_existing_no_callbacks(), process_existing=True))
    assert out == '<a href="http://x.com" class="a b">x</a>'


def test_process_existing_flattens_valueless_attr() -> None:
    html = "<a download>x</a>"
    out = linkify(html, Linkify(callbacks=_linkify_process_existing_no_callbacks(), process_existing=True))
    assert out == '<a download="">x</a>'


def test_process_existing_preserves_empty_text_and_four_character_attr() -> None:
    html = '<a href="http://x.com" data="x"></a>'
    assert linkify(html, Linkify(callbacks=_linkify_process_existing_no_callbacks(), process_existing=True)) == html


def test_process_existing_anchor_without_href_stays_bare() -> None:
    html = "<a>plain</a>"
    assert linkify(html, Linkify(callbacks=[nofollow], process_existing=True)) == "<a>plain</a>"


def test_existing_flag_distinguishes_existing_from_detected() -> None:
    def mark(link: LinkCandidate) -> LinkCandidate:
        link.attrs["data-kind"] = "existing" if link.existing else "new"
        return link

    html = '<a href="http://x.com">x</a> see http://y.com'
    out = linkify(html, Linkify(callbacks=[mark], process_existing=True))
    assert '<a href="http://x.com" data-kind="existing">x</a>' in out
    assert 'data-kind="new"' in out


def test_callbacks_follow_document_order_across_existing_and_detected_links() -> None:
    seen: list[tuple[str, bool]] = []

    def record(link: LinkCandidate) -> LinkCandidate:
        seen.append((link.url, link.existing))
        return link

    linkify(
        'first.com <a href="https://second.example">second</a> third.com',
        Linkify(callbacks=[record], process_existing=True),
    )
    assert seen == [
        ("http://first.com", False),
        ("https://second.example", True),
        ("http://third.com", False),
    ]


def test_process_existing_still_detects_links_in_other_tags() -> None:
    html = "<p>see http://x.com</p>"
    out = linkify(html, Linkify(callbacks=_linkify_process_existing_no_callbacks(), process_existing=True))
    assert out == '<p>see <a href="http://x.com">http://x.com</a></p>'


def test_process_existing_leaves_anchor_in_skip_tag_alone() -> None:
    html = '<code><a href="http://x.com">x</a></code>'
    assert linkify(html, Linkify(callbacks=[nofollow], skip_tags=["code"], process_existing=True)) == html


def test_how_to_doctest_scenario() -> None:
    def annotate(link: LinkCandidate) -> LinkCandidate:
        link.attrs["data-seen"] = "author" if link.existing else "auto"
        return link

    html = '<a href="https://docs.example">docs</a>, ping app.internal, skip ftp://x.example'
    out = linkify(
        html, Linkify(callbacks=[annotate], process_existing=True, extra_tlds=["internal"], schemes=["https"])
    )
    assert out == (
        '<a href="https://docs.example" data-seen="author">docs</a>, '
        'ping <a href="http://app.internal" data-seen="auto">app.internal</a>, skip ftp://x.example'
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("https:nonsense.com", id="colon-no-slash"),
        pytest.param("https:/nonsense.com", id="colon-one-slash"),
        pytest.param("hppt://nonsense.com", id="typo-scheme"),
        pytest.param("http:///nonsense.com", id="colon-three-slashes"),
        pytest.param("http:////nonsense.com", id="colon-four-slashes"),
        pytest.param("http://-nonsense.com", id="colon-slashes-then-hyphen"),
        pytest.param("///nonsense.com", id="three-slashes-no-colon"),
        pytest.param("path:to:nonsense.com", id="colon-inside-a-path"),
    ],
)
def test_declined_scheme_leaves_its_host_plain(text: str) -> None:
    # the host of a URL whose scheme syntax the scanner declined belongs to that URL, so neither half links
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("//example.com", "example.com", id="protocol-relative"),
        pytest.param("nothttp//example.com", "example.com", id="two-slashes-after-a-word"),
        pytest.param("foo/example.com", "example.com", id="one-slash-after-a-word"),
        pytest.param("-example.com", "example.com", id="leading-hyphen"),
    ],
)
def test_slashes_without_a_scheme_colon_still_link(text: str, matched: str) -> None:
    assert [span.text for span in LinkDetector().find(text)] == [matched]


def test_declined_scheme_is_not_rewritten() -> None:
    assert linkify("https:/nonsense.com") == "https:/nonsense.com"


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("mailto:a@b.com", "mailto:a@b.com", id="bare"),
        pytest.param("MAILTO:A@B.COM", "MAILTO:A@B.COM", id="uppercase-scheme"),
        pytest.param("write to mailto:a@b.com now", "mailto:a@b.com", id="in-prose"),
        pytest.param("mailto:foo.bar+baz@example.co.uk", "mailto:foo.bar+baz@example.co.uk", id="dotted-local-part"),
    ],
)
def test_mailto_uri_spans_its_own_scheme(text: str, matched: str) -> None:
    # GFM's protocol autolink: the URI is one link, not a stranded "mailto:" beside one
    span = LinkDetector().find(text)[0]
    assert (span.text, span.url, span.is_email) == (matched, matched, True)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("mailto:garbage", id="no-address"),
        pytest.param("mailto:@example.com", id="empty-local-part"),
        pytest.param("mailto:a@b", id="host-without-a-tld"),
        pytest.param("mailto: a@b.com", id="space-before-the-address"),
        pytest.param("mailto:.a@b.com", id="address-starts-past-the-colon"),
        pytest.param("xmailto:a@b.com", id="scheme-is-a-longer-word"),
        pytest.param("mailtp:a@b.com", id="scheme-is-a-typo"),
        pytest.param("a.mailto:a@b.com", id="scheme-blocked-on-its-left"),
        pytest.param(":a@b.com", id="colon-with-no-scheme"),
    ],
)
def test_mailto_without_a_whole_address_is_not_a_scheme_link(text: str) -> None:
    assert [span.text for span in LinkDetector().find(text) if span.text.lower().startswith("mailto")] == []


def test_mailto_uri_rewrites_to_one_anchor() -> None:
    assert linkify("mailto:a@b.com", Linkify(parse_email=True)) == '<a href="mailto:a@b.com">mailto:a@b.com</a>'


def test_mailto_uri_stays_plain_without_email_detection() -> None:
    assert linkify("mailto:a@b.com") == "mailto:a@b.com"


def test_bare_address_still_gets_the_mailto_prefix() -> None:
    span = LinkDetector().find("a@b.com")[0]
    assert (span.text, span.url) == ("a@b.com", "mailto:a@b.com")


_US_DETECTOR: Final = LinkDetector(phones=PhoneNumbers(regions=("US",)))


@pytest.mark.parametrize("lane", [pytest.param(lane, id=f"lane-{lane}") for lane in range(16)])
@pytest.mark.parametrize(
    ("token", "url"),
    [
        pytest.param("a@b.com", "mailto:a@b.com", id="email"),
        pytest.param("x.com", "http://x.com", id="domain"),
        pytest.param("https://x.org", "https://x.org", id="url"),
        pytest.param("650-253-0000", "tel:+16502530000", id="phone"),
    ],
)
def test_trigger_at_every_lane(lane: int, token: str, url: str) -> None:
    assert [
        (span.start, span.url)
        for span in _US_DETECTOR.find(("p" * (lane - 1) + " " if lane else "") + token + " q" * 5)
    ] == [(lane, url)]


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("p" * 15, id="across-a-block-boundary"),
        pytest.param("p" * 16, id="after-one-block"),
        pytest.param("p" * 17, id="one-past-a-block"),
        pytest.param("p" * 100, id="deep-in-plain-text"),
    ],
)
def test_trigger_after_plain_blocks(prefix: str) -> None:
    assert [
        span.start
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",)), emails=False, bare_domains=False).find(
            prefix + " 650-253-0000"
        )
    ] == [len(prefix) + 1]


@pytest.mark.parametrize(
    "length",
    [pytest.param(15, id="short-tail"), pytest.param(16, id="one-block"), pytest.param(17, id="block-and-one")],
)
def test_plain_text_of_block_sizes_has_no_links(length: int) -> None:
    assert _US_DETECTOR.find("p" * length) == []
    assert _US_DETECTOR.has_link("p" * length) is False


def test_digits_are_not_triggers_with_phones_off() -> None:
    assert [span.url for span in LinkDetector().find("p" * 7 + "650-253-0000 a@b.com")] == ["mailto:a@b.com"]


def test_a_digit_in_the_last_lane_of_a_block() -> None:
    assert [span.start for span in _US_DETECTOR.find("p" * 14 + " 6" + "50-253-0000")] == [15]


@pytest.mark.parametrize("kind", [pytest.param("ucs2", id="ucs2"), pytest.param("ucs4", id="ucs4")])
def test_wide_text_without_digits_scans_clean(kind: str) -> None:
    text = ("\u4e2d\u6587" if kind == "ucs2" else "\U0001f600\u4e2d") * 50_000 + " 650-253-0000"
    assert [span.url for span in _US_DETECTOR.find(text)] == ["tel:+16502530000"]
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize("prefix", ["plain ", "café ", "中文 ", "😀 "], ids=["ascii", "latin1", "ucs2", "ucs4"])
def test_linkify_snapshot_width(prefix: str) -> None:
    assert Linker().linkify(prefix + "https://example.com tail") == (
        prefix + '<a href="https://example.com" rel="nofollow">https://example.com</a> tail'
    )


@pytest.mark.parametrize("prefix", ["plain ", "😀 "], ids=["ascii", "ucs4"])
def test_linkify_snapshot_survives_callback_mutation(prefix: str) -> None:
    root: Final = parse_fragment(prefix + "https://example.com tail")
    text: Final = root.children[0]
    assert isinstance(text, Text)

    def mutate(link: LinkCandidate) -> LinkCandidate:
        text.data = "replaced by callback"
        return link

    assert Linker(Linkify(callbacks=(mutate,))).linkify_node(root).inner_html == (
        prefix + '<a href="https://example.com">https://example.com</a> tail'
    )


def test_linkify_wide_snapshot_callback_veto() -> None:
    def veto(_link: LinkCandidate) -> None:
        return None

    assert Linker(Linkify(callbacks=(veto,))).linkify("😀 https://example.com tail") == "😀 https://example.com tail"


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(
            1,
            "<p>" + "😀 " * 32_768 + '<a href="https://example.com" rel="nofollow">https://example.com</a></p>',
            id="wide-one-link",
        ),
        pytest.param(
            2,
            "<p>" + "a " * 32_768 + '<a href="https://example.com" rel="nofollow">https://example.com</a></p>',
            id="ascii-one-link",
        ),
        pytest.param(3, "<p>" + "😀 " * 32_768 + "</p>", id="wide-no-links"),
        pytest.param(
            4,
            "<p>" + '😀 <a href="https://example.com" rel="nofollow">https://example.com</a> ' * 1_024 + "</p>",
            id="wide-many-links",
        ),
    ],
)
def test_linkify_snapshot_benchmark(case: int, expected: str) -> None:
    root: Final = parse_fragment(cast("str", INPUTS["linkify-node"]()[case][1]))
    assert Linker().linkify_node(root).inner_html == expected


def test_linkify_snapshot_callback_benchmark() -> None:
    case: Final = cast("tuple[str, str]", INPUTS["linkify-traversal"]()[5][1])
    assert Linker(Linkify(callbacks=(nofollow, target_blank), process_existing=True)).linkify(case[1]) == (
        "<p>"
        + "😀 " * 32_768
        + '<a href="https://example.com" rel="nofollow" target="_blank">https://example.com</a></p>'
    )


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("президент.рф", id="cyrillic"),
        pytest.param("ПРЕЗИДЕНТ.РФ", id="cyrillic-upper-case"),
        pytest.param("мвд.рф/news", id="cyrillic-with-a-path"),
        pytest.param("сайт.онлайн", id="longer-cyrillic-tld"),
        pytest.param("中国.中国", id="han"),
        pytest.param("x.ευ", id="greek"),
        pytest.param("x.ΕΛ", id="greek-upper-case"),
        pytest.param("example.vermögensberater", id="latin-tld-with-a-diacritic"),
    ],
)
def test_a_unicode_top_level_domain_makes_a_bare_domain(text: str) -> None:
    # IANA lists these only as punycode, but the U-label is what people write
    assert [span.text for span in LinkDetector().find(text)] == [text]


def test_the_punycode_spelling_still_matches() -> None:
    assert [span.text for span in LinkDetector().find("президент.xn--p1ai")] == ["президент.xn--p1ai"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("пример.испытание", id="a-tld-iana-does-not-list"),
        pytest.param("例子.中国国", id="longer-than-the-tld"),
        pytest.param("例子.中文", id="shorter-than-the-tld"),
        pytest.param("例子.串串", id="sorts-after-the-tld"),
        pytest.param("例子.丁丁", id="sorts-before-the-tld"),
    ],
)
def test_an_unlisted_unicode_label_is_not_a_top_level_domain(text: str) -> None:
    assert LinkDetector().find(text) == []


def test_a_unicode_tld_can_be_added_by_hand() -> None:
    assert [span.text for span in LinkDetector(tlds=["испытание"]).find("пример.испытание")] == ["пример.испытание"]


def test_unique_drops_a_repeated_url() -> None:
    text = "a.com then b.com then a.com"
    assert [span.url for span in LinkDetector().find(text, unique=True)] == ["http://a.com", "http://b.com"]


def test_unique_keeps_the_first_occurrence_offsets() -> None:
    span = LinkDetector().find("see a.com and a.com", unique=True)[0]
    assert (span.start, span.end) == (4, 9)


def test_unique_collapses_spellings_that_share_a_url() -> None:
    # the normalized url is the identity, so a bare domain and its written http:// form are one link
    found = LinkDetector().find("http://a.com and http://a.com", unique=True)
    assert [span.url for span in found] == ["http://a.com"]


def test_unique_keeps_distinct_paths_apart() -> None:
    found = LinkDetector().find("a.com/x and a.com/y", unique=True)
    assert [span.url for span in found] == ["http://a.com/x", "http://a.com/y"]


def test_find_repeats_every_match_by_default() -> None:
    assert len(LinkDetector().find("a.com then a.com")) == 2


def test_unique_on_text_without_links_is_empty() -> None:
    assert LinkDetector().find("nothing here", unique=True) == []


_US: Final = PhoneNumbers(regions=("US",))


def test_acceptance_case() -> None:
    assert linkify("Call 650-253-0000", Linkify(phones=_US)) == 'Call <a href="tel:+16502530000">650-253-0000</a>'
    assert LinkDetector(phones=_US).find("Call 650-253-0000")[0].phone is not None


def test_linkify_matches_a_within_text_phone_number() -> None:
    assert linkify("Please call +1 (650) 253-0000 now", Linkify(phones=_US)) == (
        'Please call <a href="tel:+16502530000">+1 (650) 253-0000</a> now'
    )


def test_a_reused_linker_and_one_without_phones() -> None:
    with_phones = Linker(Linkify(phones=_US))
    plain = Linker()
    for _ in range(2):
        assert with_phones.linkify("650-253-0000") == '<a href="tel:+16502530000">650-253-0000</a>'
        assert plain.linkify("650-253-0000") == "650-253-0000"


@pytest.mark.parametrize(
    ("text", "regions", "href"),
    [
        pytest.param("650-253-0000", ("US",), "tel:+16502530000", id="national"),
        pytest.param("011 44 20 7946 0958", ("US",), "tel:+442079460958", id="idd"),
        pytest.param(
            "\uff0b\uff14\uff14 \uff12\uff10 \uff17\uff19\uff14\uff16 \uff10\uff19\uff15\uff18",
            (),
            "tel:+442079460958",
            id="fullwidth",
        ),
        pytest.param("06 12345678", ("IT",), "tel:+390612345678", id="leading-zero-kept"),
        pytest.param("+800 1234 5678", (), "tel:+80012345678", id="non-geographic"),
        pytest.param("+49 800 1234567890", (), "tel:+498001234567890", id="over-fifteen-digits"),
        pytest.param("650-253-0000 ext. 1234", ("US",), "tel:+16502530000;ext=1234", id="extension"),
        pytest.param("650-253-0000 x1", ("US",), "tel:+16502530000;ext=1", id="one-digit-extension"),
    ],
)
def test_href_is_the_international_number(text: str, regions: tuple[str, ...], href: str) -> None:
    assert linkify(text, Linkify(phones=PhoneNumbers(regions=regions))) == f'<a href="{href}">{text}</a>'
    span = LinkDetector(phones=PhoneNumbers(regions=regions)).find(text)[0]
    assert span.phone is not None
    expected = "tel:" + span.phone.international_number
    if span.phone.extension is not None:
        expected += ";ext=" + span.phone.extension
    assert href == expected


def _collect(seen: list[LinkCandidate]) -> Callable[[LinkCandidate], LinkCandidate]:
    def callback(link: LinkCandidate) -> LinkCandidate:
        seen.append(link)
        return link

    return callback


@pytest.mark.parametrize("entry", [pytest.param("function", id="linkify"), pytest.param("linker", id="Linker")])
def test_callback_sees_the_phone(entry: str) -> None:
    seen: list[LinkCandidate] = []
    config = Linkify(callbacks=[_collect(seen)], phones=_US, parse_email=True)
    text = "mail a@b.com, see example.com, call 650-253-0000"
    assert (linkify(text, config) if entry == "function" else Linker(config).linkify(text)) == (
        'mail <a href="mailto:a@b.com">a@b.com</a>, see <a href="http://example.com">example.com</a>, '
        'call <a href="tel:+16502530000">650-253-0000</a>'
    )
    assert [link.phone for link in seen] == [
        None,
        None,
        PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
    ]


def test_callback_can_route_mobiles_to_sms() -> None:
    def sms(link: LinkCandidate) -> LinkCandidate:
        if link.phone is not None and link.phone.type in {PhoneType.MOBILE, PhoneType.FIXED_LINE_OR_MOBILE}:
            link.url = "sms:" + link.phone.international_number
        return link

    assert linkify(
        "a@b.com or 07400 123456 or 020 7946 0958",
        Linkify(callbacks=[sms], phones=PhoneNumbers(regions=("GB",)), parse_email=True),
    ) == (
        '<a href="mailto:a@b.com">a@b.com</a> or <a href="sms:+447400123456">07400 123456</a> or '
        '<a href="tel:+442079460958">020 7946 0958</a>'
    )


def test_veto_leaves_the_text_bare() -> None:
    assert linkify("call 650-253-0000", Linkify(callbacks=[lambda _link: None], phones=_US)) == "call 650-253-0000"


def test_skip_tags_and_existing_anchors_are_untouched() -> None:
    assert linkify(
        '<code>650-253-0000</code> <a href="/x">650-253-0000</a> <script>650-253-0000</script> 650-253-0000',
        Linkify(phones=_US, skip_tags=["code"]),
    ) == (
        '<code>650-253-0000</code> <a href="/x">650-253-0000</a> <script>650-253-0000</script> '
        '<a href="tel:+16502530000">650-253-0000</a>'
    )


def test_a_number_split_across_elements_is_not_joined() -> None:
    assert linkify("<b>650-253</b>-0000", Linkify(phones=_US)) == "<b>650-253</b>-0000"


def test_nbsp_inside_a_number() -> None:
    assert linkify("650&nbsp;253&nbsp;0000", Linkify(phones=_US)) == (
        '<a href="tel:+16502530000">650&nbsp;253&nbsp;0000</a>'
    )


def test_written_tel_uri_links_to_its_number() -> None:
    assert linkify("tel:+1-650-253-0000", Linkify(phones=_US)) == '<a href="tel:+16502530000">tel:+1-650-253-0000</a>'


@pytest.mark.parametrize(
    "html",
    [
        pytest.param('<a href="https://x.org">650-253-0000</a>', id="web-link"),
        pytest.param('<a href="tel:+16502530000;ext=12">650-253-0000 x12</a>', id="tel-link"),
        pytest.param('<a href="tel:not-a-number">call</a>', id="malformed-tel-link"),
    ],
)
def test_existing_anchors_reach_the_callback_without_a_phone(html: str) -> None:
    seen: list[LinkCandidate] = []
    assert linkify(html, Linkify(callbacks=[_collect(seen)], phones=_US, process_existing=True)) == html
    assert [(link.existing, link.phone) for link in seen] == [(True, None)]


def test_candidate_phone_defaults_to_none() -> None:
    assert LinkCandidate("http://x", "x").phone is None
    assert LinkCandidate("tel:+1", "1", phone=None).phone is None


@pytest.mark.parametrize("case", [1, 2, 3, 4], ids=["wide", "ascii", "no-links", "many-links"])
@pytest.mark.oracle
def test_linkify_snapshot_outputs(case: int) -> None:
    html: Final = pytest.importorskip("lxml.html")
    clean: Final = pytest.importorskip("lxml_html_clean")
    source: Final = cast("str", INPUTS["linkify-node"]()[case][1])
    root: Final = html.fragment_fromstring(source, create_parent="div")
    clean.autolink(root, avoid_hosts=())
    assert (
        Linker().linkify_node(parse_fragment(source)).inner_html,
        "".join(html.tostring(child, encoding="unicode") for child in root),
    ) == (
        source.replace("https://example.com", '<a href="https://example.com" rel="nofollow">https://example.com</a>'),
        source.replace("https://example.com", '<a href="https://example.com">https://example.com</a>'),
    )
