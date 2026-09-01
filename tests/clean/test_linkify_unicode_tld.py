from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("президент.рф", id="cyrillic"),
        pytest.param("ПРЕЗИДЕНТ.РФ", id="cyrillic-upper-case"),
        pytest.param("мвд.рф/news", id="cyrillic-with-a-path"),
        pytest.param("сайт.онлайн", id="longer-cyrillic-tld"),
        pytest.param("中国.中国", id="han"),
        pytest.param("x.ευ", id="greek"),
        pytest.param("x.ΕΛ", id="greek-upper-case"),
        pytest.param("example.vermögensberater", id="latin-tld-with-a-diacritic"),
    ],
)
def test_a_unicode_top_level_domain_makes_a_bare_domain(text: str) -> None:
    # IANA lists these only as punycode, but the U-label is what people write
    assert [span.text for span in LinkDetector().find(text)] == [text]


def test_the_punycode_spelling_still_matches() -> None:
    assert [span.text for span in LinkDetector().find("президент.xn--p1ai")] == ["президент.xn--p1ai"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("пример.испытание", id="a-tld-iana-does-not-list"),
        pytest.param("例子.中国国", id="longer-than-the-tld"),
        pytest.param("例子.中文", id="shorter-than-the-tld"),
        pytest.param("例子.串串", id="sorts-after-the-tld"),
        pytest.param("例子.丁丁", id="sorts-before-the-tld"),
    ],
)
def test_an_unlisted_unicode_label_is_not_a_top_level_domain(text: str) -> None:
    assert LinkDetector().find(text) == []


def test_a_unicode_tld_can_be_added_by_hand() -> None:
    assert [span.text for span in LinkDetector(tlds=["испытание"]).find("пример.испытание")] == ["пример.испытание"]
