from __future__ import annotations

import pytest

from turbohtml._html import _registrable_domain


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
        pytest.param("www.maple-trade.org..", "www.maple-trade.org..", id="second-dot-is-an-empty-label"),
        pytest.param("..", "..", id="only-dots"),
        pytest.param("zz..", "zz..", id="empty-label-before-the-root"),
        # long enough that the host copy leaves the small-object allocator, where a read past its end is detected
        pytest.param(f"{'b.' * 100}maple-trade.com.au.", "maple-trade.com.au.", id="long-host"),
    ],
)
def test_registrable_domain_matches_the_suffix_without_the_trailing_dot(host: str, expected: str) -> None:
    # the trailing dot names the DNS root: the suffix rules match the host without it and the answer keeps it; a label
    # that is still empty once that dot is set aside is no TLD, so such a host has no known suffix and stays whole
    assert _registrable_domain(host) == expected
