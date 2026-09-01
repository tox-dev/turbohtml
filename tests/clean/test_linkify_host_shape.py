from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector


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
