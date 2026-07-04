from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, LinkSpan


def _tuples(spans: list[LinkSpan]) -> list[tuple[int, int, str, str, bool]]:
    return [(span.start, span.end, span.text, span.url, span.is_email) for span in spans]


def test_registered_scheme_less_url_is_found() -> None:
    spans = LinkDetector(schemes=["tel"]).find("call tel:+1-800-555-0100 now")
    assert _tuples(spans) == [(5, 24, "tel:+1-800-555-0100", "tel:+1-800-555-0100", False)]


def test_scheme_registration_is_case_and_colon_insensitive() -> None:
    spans = LinkDetector(schemes=["TEL:"]).find("tel:12345")
    assert _tuples(spans) == [(0, 9, "tel:12345", "tel:12345", False)]


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
    assert _tuples(spans) == [(0, 18, "http://example.com", "http://example.com", False)]


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
    assert _tuples(LinkDetector().find(text)) == [(0, len(text), text, text, False)]


def test_registered_scheme_extends_authority_matching() -> None:
    spans = LinkDetector(schemes=["git"]).find("git://example.com")
    assert _tuples(spans) == [(0, 17, "git://example.com", "git://example.com", False)]
