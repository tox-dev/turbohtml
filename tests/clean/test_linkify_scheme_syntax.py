from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, Linkify, linkify


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("https:nonsense.com", id="colon-no-slash"),
        pytest.param("https:/nonsense.com", id="colon-one-slash"),
        pytest.param("hppt://nonsense.com", id="typo-scheme"),
        pytest.param("http:///nonsense.com", id="colon-three-slashes"),
        pytest.param("http:////nonsense.com", id="colon-four-slashes"),
        pytest.param("http://-nonsense.com", id="colon-slashes-then-hyphen"),
        pytest.param("///nonsense.com", id="three-slashes-no-colon"),
        pytest.param("path:to:nonsense.com", id="colon-inside-a-path"),
    ],
)
def test_declined_scheme_leaves_its_host_plain(text: str) -> None:
    # the host of a URL whose scheme syntax the scanner declined belongs to that URL, so neither half links
    assert LinkDetector().find(text) == []


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("//example.com", "example.com", id="protocol-relative"),
        pytest.param("nothttp//example.com", "example.com", id="two-slashes-after-a-word"),
        pytest.param("foo/example.com", "example.com", id="one-slash-after-a-word"),
        pytest.param("-example.com", "example.com", id="leading-hyphen"),
    ],
)
def test_slashes_without_a_scheme_colon_still_link(text: str, matched: str) -> None:
    assert [span.text for span in LinkDetector().find(text)] == [matched]


def test_declined_scheme_is_not_rewritten() -> None:
    assert linkify("https:/nonsense.com") == "https:/nonsense.com"


@pytest.mark.parametrize(
    ("text", "matched"),
    [
        pytest.param("mailto:a@b.com", "mailto:a@b.com", id="bare"),
        pytest.param("MAILTO:A@B.COM", "MAILTO:A@B.COM", id="uppercase-scheme"),
        pytest.param("write to mailto:a@b.com now", "mailto:a@b.com", id="in-prose"),
        pytest.param("mailto:foo.bar+baz@example.co.uk", "mailto:foo.bar+baz@example.co.uk", id="dotted-local-part"),
    ],
)
def test_mailto_uri_spans_its_own_scheme(text: str, matched: str) -> None:
    # GFM's protocol autolink: the URI is one link, not a stranded "mailto:" beside one
    span = LinkDetector().find(text)[0]
    assert (span.text, span.url, span.is_email) == (matched, matched, True)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("mailto:garbage", id="no-address"),
        pytest.param("mailto:@example.com", id="empty-local-part"),
        pytest.param("mailto:a@b", id="host-without-a-tld"),
        pytest.param("mailto: a@b.com", id="space-before-the-address"),
        pytest.param("mailto:.a@b.com", id="address-starts-past-the-colon"),
        pytest.param("xmailto:a@b.com", id="scheme-is-a-longer-word"),
        pytest.param("mailtp:a@b.com", id="scheme-is-a-typo"),
        pytest.param("a.mailto:a@b.com", id="scheme-blocked-on-its-left"),
        pytest.param(":a@b.com", id="colon-with-no-scheme"),
    ],
)
def test_mailto_without_a_whole_address_is_not_a_scheme_link(text: str) -> None:
    assert [span.text for span in LinkDetector().find(text) if span.text.lower().startswith("mailto")] == []


def test_mailto_uri_rewrites_to_one_anchor() -> None:
    assert linkify("mailto:a@b.com", Linkify(parse_email=True)) == '<a href="mailto:a@b.com">mailto:a@b.com</a>'


def test_mailto_uri_stays_plain_without_email_detection() -> None:
    assert linkify("mailto:a@b.com") == "mailto:a@b.com"


def test_bare_address_still_gets_the_mailto_prefix() -> None:
    span = LinkDetector().find("a@b.com")[0]
    assert (span.text, span.url) == ("a@b.com", "mailto:a@b.com")
