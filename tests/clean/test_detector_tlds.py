from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, LinkSpan


def _tuples(spans: list[LinkSpan]) -> list[tuple[int, int, str, str, bool]]:
    return [(span.start, span.end, span.text, span.url, span.is_email) for span in spans]


def test_custom_tld_makes_a_bare_domain_match() -> None:
    spans = LinkDetector(tlds=["corp"]).find("visit intranet.corp today")
    assert _tuples(spans) == [(6, 19, "intranet.corp", "http://intranet.corp", False)]


def test_custom_tld_is_normalized() -> None:
    spans = LinkDetector(tlds=[".CORP"]).find("intranet.corp")
    assert _tuples(spans) == [(0, 13, "intranet.corp", "http://intranet.corp", False)]


def test_custom_tld_applies_to_email() -> None:
    spans = LinkDetector(tlds=["corp"]).find("bob@mail.corp")
    assert _tuples(spans) == [(0, 13, "bob@mail.corp", "mailto:bob@mail.corp", True)]


def test_non_ascii_custom_tld_matches() -> None:
    spans = LinkDetector(tlds=["рф"]).find("сайт.рф")
    assert _tuples(spans) == [(0, 7, "сайт.рф", "http://сайт.рф", False)]


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
