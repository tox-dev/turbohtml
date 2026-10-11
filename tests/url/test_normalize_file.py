from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import UrlCleaning, normalize_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:", "file:///", id="empty"),
        pytest.param("file://", "file:///", id="empty-authority"),
        pytest.param("file:\\", "file:///", id="single-backslash"),
        pytest.param("file:\\\\", "file:///", id="double-backslash"),
        pytest.param("file:\\\\\\", "file:///", id="three-backslashes"),
        pytest.param("file:\\\\\\\\", "file:////", id="four-backslashes"),
        pytest.param("file:/\\", "file:///", id="mixed-opener"),
        pytest.param("file:\\/", "file:///", id="reversed-opener"),
        pytest.param("FILE://SERVER/share\\a?x=1#part", "file://server/share/a?x=1#part", id="unc-path"),
        pytest.param("file:\\\\SERVER\\share\\a?x=1#part", "file://server/share/a?x=1#part", id="unc-backslash-opener"),
        pytest.param("file:///C:/a\\b?x=1#part", "file:///C:/a/b?x=1#part", id="absolute-drive"),
        pytest.param("FILE:///a/%5C/b?x=1#part", "file:///a/%5C/b?x=1#part", id="encoded-backslash"),
        pytest.param("FILE:///a/b?x=1#part", "file:///a/b?x=1#part", id="forward-slashes"),
        pytest.param("file:///a\\\u0100", "file:///a/%C4%80", id="two-byte-unicode"),
        pytest.param("file:///a\\\U0001f600", "file:///a/%F0%9F%98%80", id="four-byte-unicode"),
        pytest.param("file:///a\\\x00b", "file:///a/%00b", id="embedded-null"),
        pytest.param("FIZZ://EXAMPLE.ORG/a\\b?x=1#part", "fizz://example.org/a\\b?x=1#part", id="f-prefixed-non-file"),
        pytest.param("HTTPS://EXAMPLE.ORG/a\\b?x=1#part", "https://example.org/a\\b?x=1#part", id="web-path-policy"),
        pytest.param(
            "CUSTOM://EXAMPLE.ORG/a\\b?x=1#part", "custom://example.org/a\\b?x=1#part", id="custom-path-policy"
        ),
    ],
)
def test_normalize_file_path(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:///C|/foo", "file:///C:/foo", id="absolute-drive"),
        pytest.param("file:///c|", "file:///c:", id="lowercase-drive"),
        pytest.param("file:/C|/foo", "file:///C:/foo", id="single-slash"),
        pytest.param("file:///C|\\foo", "file:///C:/foo", id="backslash-separator"),
        pytest.param("file:///C|?x=1", "file:///C:?x=1", id="query-after-drive"),
        pytest.param("file:///C|/\u0100", "file:///C:/%C4%80", id="two-byte-unicode"),
        pytest.param("file:///C|/\U0001f600", "file:///C:/%F0%9F%98%80", id="four-byte-unicode"),
        pytest.param("file:///C|foo", "file:///C|foo", id="longer-segment"),
        pytest.param("file:///1|/foo", "file:///1|/foo", id="non-letter"),
        pytest.param("FILE:ab|\\foo", "file:ab|/foo", id="relative-file-path"),
    ],
)
def test_normalize_file_drive_separator(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


def test_normalize_file_root_survives_trailing_slash_option() -> None:
    assert normalize_url("FILE:", UrlCleaning(trailing_slash=False)) == "file:///"


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file:///a\udce9", id="unchanged-path-surrogate"),
        pytest.param("file:///a\\\udce9", id="rewritten-path-surrogate"),
    ],
)
def test_normalize_file_rejects_surrogate(url: str) -> None:
    with pytest.raises(ValueError, match="cannot be percent-encoded"):
        normalize_url(url)
