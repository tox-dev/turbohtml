from __future__ import annotations

import pytest

from turbohtml import Link, parse
from turbohtml._html import _registrable_domain
from turbohtml.extract._native import register_links


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("россия.рф", "россия.рф", id="cyrillic-unknown-suffix"),
        pytest.param("例え.jp", "例え.jp", id="cjk-label-ascii-suffix"),
        pytest.param("рф", "рф", id="bare-cyrillic-label"),
    ],
)
def test_registrable_domain_handles_a_raw_non_ascii_host(host: str, expected: str) -> None:
    # The raw binding skips the public URL normalizer's IDNA conversion.
    assert _registrable_domain(host) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("mail.maple-trade.org.", "maple-trade.org.", id="tld"),
        pytest.param("a.b.shop.maple-trade.com.au.", "maple-trade.com.au.", id="multi-label-suffix"),
        pytest.param("x.bar.pages.dev.", "bar.pages.dev.", id="private-suffix"),
        pytest.param("a.b.other.kawasaki.jp.", "b.other.kawasaki.jp.", id="wildcard-suffix"),
        pytest.param("a.city.kawasaki.jp.", "city.kawasaki.jp.", id="exception-suffix"),
        pytest.param("maple-trade.org.", "maple-trade.org.", id="registrable-domain-itself"),
        pytest.param("com.au.", "com.au.", id="public-suffix-itself"),
        pytest.param("a.b.unlisted-suffix.", "a.b.unlisted-suffix.", id="unknown-suffix"),
        pytest.param("www.maple-trade.org..", "maple-trade.org..", id="repeated-trailing-dots"),
        pytest.param("x.bar.pages.dev...", "bar.pages.dev...", id="three-trailing-dots"),
        pytest.param("a.city.kawasaki.jp..", "city.kawasaki.jp..", id="repeated-trailing-dots-exception-suffix"),
        pytest.param("com.au..", "com.au..", id="public-suffix-itself-repeated-dots"),
        pytest.param("a.b.unlisted-suffix..", "a.b.unlisted-suffix..", id="unknown-suffix-repeated-dots"),
        pytest.param("..", "..", id="only-dots"),
        pytest.param("zz..", "zz..", id="empty-label-before-the-root"),
        pytest.param("a..com.au", "a..com.au", id="empty-label-left-of-the-suffix"),
        pytest.param("a.b..com.au.", "a.b..com.au.", id="empty-label-left-of-the-suffix-with-root-dot"),
        pytest.param("www..maple-trade.org", "maple-trade.org", id="empty-label-outside-the-registrable-domain"),
        # long enough that the host copy leaves the small-object allocator, where a read past its end is detected
        pytest.param(f"{'b.' * 100}maple-trade.com.au..", "maple-trade.com.au..", id="long-host"),
    ],
)
def test_registrable_domain_matches_the_suffix_without_the_trailing_dots(host: str, expected: str) -> None:
    # the trailing dots name the DNS root: the suffix rules match the host without them and the answer keeps them; an
    # empty label left of the registrable domain names nothing, so such a host has no known site and stays whole
    assert _registrable_domain(host) == expected


@pytest.mark.parametrize("record", [dict, "Link"])
def test_register_links_rejects_a_non_tuple_record(record: type | str) -> None:
    with pytest.raises(TypeError, match="register_links expected a tuple subclass"):
        register_links(record)  # ty: ignore[invalid-argument-type]  # rejected at runtime
    assert type(parse("<a href=u>x</a>").links()[0]) is Link
