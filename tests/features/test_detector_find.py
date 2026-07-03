from __future__ import annotations

import pytest

from turbohtml.clean import Detector, LinkSpan


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
    assert _tuples(Detector().find(text)) == expected


def test_find_offsets_slice_back_to_text() -> None:
    text = "reach me at bob@example.com or https://example.org/x"
    for span in Detector().find(text):
        assert text[span.start : span.end] == span.text


def test_find_respects_emails_disabled() -> None:
    assert Detector(emails=False).find("write bob@example.com") == []


def test_find_respects_bare_domains_disabled() -> None:
    assert Detector(bare_domains=False).find("go to example.com") == []


def test_find_scheme_url_still_found_without_bare_domains() -> None:
    spans = Detector(bare_domains=False).find("see https://example.com here")
    assert _tuples(spans) == [(4, 23, "https://example.com", "https://example.com", False)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("{Scoped like http://example.com/foo_bar}", id="trailing-brace"),
        pytest.param("'Quoted like http://example.com/foo_bar'", id="trailing-apostrophe"),
    ],
)
def test_find_trims_a_trailing_brace_or_apostrophe(text: str) -> None:
    assert _tuples(Detector().find(text)) == [
        (13, 39, "http://example.com/foo_bar", "http://example.com/foo_bar", False),
    ]


def test_find_keeps_a_balanced_brace_in_the_path() -> None:
    spans = Detector().find("http://example.com/{id}")
    assert _tuples(spans) == [(0, 23, "http://example.com/{id}", "http://example.com/{id}", False)]
