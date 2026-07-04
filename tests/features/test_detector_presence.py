from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("see http://example.com", id="scheme-url"),
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
    ],
)
def test_has_link_is_false_without_a_link(text: str) -> None:
    assert LinkDetector().has_link(text) is False


def test_has_link_respects_configuration() -> None:
    detector = LinkDetector(emails=False, bare_domains=False)
    assert detector.has_link("write bob@example.com or visit example.com") is False
    assert detector.has_link("but https://example.com still counts") is True
