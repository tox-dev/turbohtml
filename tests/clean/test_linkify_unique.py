from __future__ import annotations

from turbohtml.clean import LinkDetector


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
