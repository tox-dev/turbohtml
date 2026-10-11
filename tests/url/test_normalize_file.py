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


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file://localhost/C:/foo", "file:///C:/foo", id="localhost"),
        pytest.param("file://LOCALHOST/x", "file:///x", id="uppercase"),
        pytest.param("file://%6cocalhost/x", "file:///x", id="encoded"),
        pytest.param("file://localhost./x", "file://localhost./x", id="trailing-dot"),
        pytest.param("file://localhosx/x", "file://localhosx/x", id="same-length-host"),
    ],
)
def test_normalize_file_localhost_host(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


def test_normalize_file_root_survives_trailing_slash_option() -> None:
    assert normalize_url("FILE:", UrlCleaning(trailing_slash=False)) == "file:///"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param("file:///C:/../x", "file:///C:/x", id="drive-root-parent"),
        pytest.param("file:///C:/a/../../x", "file:///C:/x", id="drive-root-nested-parent"),
        pytest.param("file:///C:/../C:/x", "file:///C:/C:/x", id="repeated-drive-segment"),
        pytest.param("file:///C:/%2e%2e/x", "file:///C:/x", id="drive-root-escaped-parent"),
        pytest.param("file:///C:/..", "file:///C:/", id="drive-root-trailing-parent"),
        pytest.param("file:///C:", "file:///C:", id="drive-root-without-child"),
        pytest.param("file:///", "file:///", id="short-file-root"),
        pytest.param("file:///..", "file:///", id="short-parent-path"),
        pytest.param("file:///C:foo", "file:///C:foo", id="long-first-segment-without-dot"),
        pytest.param("file:///C:foo/../x", "file:///x", id="long-first-segment-with-parent"),
        pytest.param("file://server/C:/../x", "file://server/C:/x", id="unc-drive-root-parent"),
        pytest.param("file:///CC:/../x", "file:///x", id="long-segment-is-not-drive"),
        pytest.param("file:///4:/../x", "file:///x", id="digit-is-not-drive"),
        pytest.param("file:///Ca/../x", "file:///x", id="missing-drive-colon"),
        pytest.param("foo:/C:/../x", "foo:/x", id="other-scheme-does-not-protect-drive"),
        pytest.param("file:ab:c/foo", "file:ab:c/foo", id="colon-without-leading-slash"),
    ],
)
def test_normalize_file_keeps_drive_root(url: str, expected: str) -> None:
    normalized: Final = normalize_url(url)
    assert (normalized, normalize_url(normalized)) == (expected, expected)


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("file://user@server/share", id="username"),
        pytest.param("file://user:pass@server/share", id="password"),
        pytest.param("file://@server/share", id="empty-username"),
        pytest.param("file://user@localhost/share", id="local-host"),
    ],
)
def test_normalize_file_rejects_userinfo(url: str) -> None:
    with pytest.raises(ValueError, match="file URL cannot contain userinfo"):
        normalize_url(url)


def test_normalize_file_keeps_at_in_path() -> None:
    assert normalize_url("file://server/share@name") == "file://server/share@name"


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
