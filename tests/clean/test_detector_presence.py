from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("http://example.com followed by " + "tail " * 40_000, id="start-with-large-tail"),
        pytest.param("prefix " * 20 + "http://example.com" + " suffix " * 20, id="middle"),
        pytest.param("prefix " * 40 + "http://example.com", id="end"),
        pytest.param("bare example.com here", id="bare-domain"),
        pytest.param("mail bob@example.com", id="email"),
    ],
)
def test_has_link_detects_a_link(text: str) -> None:
    assert LinkDetector().has_link(text) is True


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("nothing here at all", id="no-link"),
        pytest.param("", id="empty"),
        pytest.param("hppt://example.invalid bob@localhost example.invalid", id="rejected-candidates"),
    ],
)
def test_has_link_is_false_without_a_link(text: str) -> None:
    assert LinkDetector().has_link(text) is False


def test_has_link_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        LinkDetector().has_link(123)  # ty: ignore[invalid-argument-type]  # Exercise the runtime type boundary.


def test_has_link_respects_configuration() -> None:
    detector = LinkDetector(emails=False, bare_domains=False)
    assert detector.has_link("write bob@example.com or visit example.com") is False
    assert detector.has_link("but https://example.com still counts") is True
