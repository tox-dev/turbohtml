from __future__ import annotations

import pytest

from turbohtml.extract import clean_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(
            "  https://Example.COM:443/a//b/?utm_source=x&id=7&ref=feed#sec  ",
            "https://example.com/a/b/?id=7#sec",
            id="scrub-and-normalize",
        ),
        pytest.param("<![CDATA[http://x.com/a]]>", "http://x.com/a", id="cdata-wrapper"),
        pytest.param("http://x.com/a</p>", "http://x.com/a", id="markup-remnant"),
        pytest.param("http://x.com/a?b=1&amp;c=2", "http://x.com/a?b=1&c=2", id="escaped-ampersand"),
        pytest.param("http://x.com/a/&", "http://x.com/a", id="trailing-amp"),
        pytest.param('http://x.com/a"junk', "http://x.com/a", id="trailing-quote"),
        pytest.param("http://x.com/", "http://x.com", id="bare-host-trailing-slash"),
        pytest.param(
            "https://web.archive.org/save/http://x.com/",
            "https://web.archive.org/save/http:/x.com",
            id="double-scheme-rstrip",
        ),
    ],
)
def test_clean_url_scrubs_then_normalizes(raw: str, expected: str) -> None:
    assert clean_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("   ", id="whitespace-only"),
        pytest.param("<p>", id="markup-only"),
        pytest.param("\x00\x01", id="control-only"),
    ],
)
def test_clean_url_returns_none_when_nothing_usable_remains(raw: str) -> None:
    assert clean_url(raw) is None
