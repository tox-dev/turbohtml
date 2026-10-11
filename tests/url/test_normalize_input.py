from __future__ import annotations

import pytest

from turbohtml.extract import normalize_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("https://example.org/a ", "https://example.org/a", id="trailing-space"),
        pytest.param("https://example.org/a\x00", "https://example.org/a", id="trailing-null"),
        pytest.param(" \x01https://example.org/a\x1f ", "https://example.org/a", id="both-edges"),
        pytest.param("https://example.org/a ?x=1", "https://example.org/a%20?x=1", id="interior-space"),
        pytest.param("https://example.org/a#f ", "https://example.org/a#f", id="fragment-edge"),
        pytest.param("foo:hello ", "foo:hello", id="opaque-path-edge"),
        pytest.param("https://example.org/a\x7f", "https://example.org/a%7F", id="del-not-c0"),
    ],
)
def test_normalize_url_trims_input_edges(url: str, expected: str) -> None:
    assert normalize_url(url) == expected
