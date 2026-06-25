from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml._html import _linkify_scan
from turbohtml.linkify import Linkify, linkify

if TYPE_CHECKING:
    from turbohtml.linkify import Callback


def _no_callbacks() -> list[Callback]:
    return []


def test_extra_tlds_links_custom_bare_domain() -> None:
    out = linkify("visit foo.internal here", Linkify(callbacks=_no_callbacks(), extra_tlds=["internal"]))
    assert out == 'visit <a href="http://foo.internal">foo.internal</a> here'


def test_extra_tlds_is_case_insensitive() -> None:
    out = linkify("foo.LOCAL", Linkify(callbacks=_no_callbacks(), extra_tlds=["local"]))
    assert out == '<a href="http://foo.LOCAL">foo.LOCAL</a>'


def test_extra_tlds_absent_leaves_unknown_tld_plain() -> None:
    assert linkify("foo.internal here", Linkify(callbacks=_no_callbacks())) == "foo.internal here"


def test_extra_tlds_still_links_builtin_tld() -> None:
    out = linkify("example.com", Linkify(callbacks=_no_callbacks(), extra_tlds=["internal"]))
    assert out == '<a href="http://example.com">example.com</a>'


def test_schemes_blocks_unlisted_scheme() -> None:
    out = linkify("ftp://x.com and http://y.com", Linkify(callbacks=_no_callbacks(), schemes=["http"]))
    assert out == 'ftp://x.com and <a href="http://y.com">http://y.com</a>'


def test_schemes_is_case_insensitive() -> None:
    out = linkify("HTTP://x.com", Linkify(callbacks=_no_callbacks(), schemes=["http"]))
    assert out == '<a href="HTTP://x.com">HTTP://x.com</a>'


def test_schemes_does_not_gate_bare_domains() -> None:
    out = linkify("see example.com", Linkify(callbacks=_no_callbacks(), schemes=["https"]))
    assert out == 'see <a href="http://example.com">example.com</a>'


def test_schemes_none_allows_every_scheme() -> None:
    out = linkify("ftp://x.com", Linkify(callbacks=_no_callbacks()))
    assert out == '<a href="ftp://x.com">ftp://x.com</a>'


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
    parse_email: bool,  # noqa: FBT001  # a pytest parametrize value, not a boolean-trap call site
    extra_tlds: tuple[str, ...],
    spans: list[tuple[int, int, int]],
) -> None:
    assert _linkify_scan(text, parse_email, True, extra_tlds) == spans  # noqa: FBT003  # positional-only C binding under test


def test_scanner_extra_tlds_defaults_to_none() -> None:
    assert _linkify_scan("foo.internal", False, True) == []  # noqa: FBT003  # positional-only C binding under test
