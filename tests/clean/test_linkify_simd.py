from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, PhoneNumbers

_LANES = 16


@pytest.mark.parametrize("lane", [pytest.param(lane, id=f"lane-{lane}") for lane in range(_LANES)])
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
    text = ("p" * (lane - 1) + " " if lane else "") + token + " q" * 5
    detector = LinkDetector(phones=PhoneNumbers(regions=("US",)))
    assert [(span.start, span.url) for span in detector.find(text)] == [(lane, url)]


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
    detector = LinkDetector(phones=PhoneNumbers(regions=("US",)), emails=False, bare_domains=False)
    assert [span.start for span in detector.find(prefix + " 650-253-0000")] == [len(prefix) + 1]


@pytest.mark.parametrize(
    "length",
    [pytest.param(15, id="short-tail"), pytest.param(16, id="one-block"), pytest.param(17, id="block-and-one")],
)
def test_plain_text_of_block_sizes_has_no_links(length: int) -> None:
    detector = LinkDetector(phones=PhoneNumbers(regions=("US",)))
    assert detector.find("p" * length) == []
    assert detector.has_link("p" * length) is False


def test_digits_are_not_triggers_with_phones_off() -> None:
    text = "p" * 7 + "650-253-0000 a@b.com"
    assert [span.url for span in LinkDetector().find(text)] == ["mailto:a@b.com"]


def test_a_digit_in_the_last_lane_of_a_block() -> None:
    text = "p" * 14 + " 6" + "50-253-0000"
    assert [span.start for span in LinkDetector(phones=PhoneNumbers(regions=("US",))).find(text)] == [15]


@pytest.mark.parametrize("kind", [pytest.param("ucs2", id="ucs2"), pytest.param("ucs4", id="ucs4")])
def test_wide_text_without_digits_scans_clean(kind: str) -> None:
    filler = "\u4e2d\u6587" if kind == "ucs2" else "\U0001f600\u4e2d"
    text = filler * 50_000 + " 650-253-0000"
    detector = LinkDetector(phones=PhoneNumbers(regions=("US",)))
    assert [span.url for span in detector.find(text)] == ["tel:+16502530000"]
    assert LinkDetector().find(text) == []
