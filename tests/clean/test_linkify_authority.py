from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, linkify


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
