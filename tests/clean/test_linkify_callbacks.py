from __future__ import annotations

import pytest

from turbohtml.clean import LinkCandidate, nofollow, target_blank


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"rel": "nofollow"}, id="http"),
        pytest.param("https://x.com", {}, {"rel": "nofollow"}, id="https"),
        pytest.param("HTTPS://X.COM", {}, {"rel": "nofollow"}, id="upper-case-scheme"),
        pytest.param("http://x.com", {"rel": "noopener"}, {"rel": "noopener nofollow"}, id="keeps-an-existing-rel"),
        pytest.param("http://x.com", {"rel": "nofollow"}, {"rel": "nofollow"}, id="never-doubles"),
        pytest.param("http://x.com", {"rel": ""}, {"rel": "nofollow"}, id="empty-rel"),
        pytest.param("mailto:a@b.com", {}, {}, id="address-left-alone"),
        pytest.param("tel:+1", {}, {}, id="phone-left-alone"),
        pytest.param("ht:x", {}, {}, id="shorter-than-a-web-scheme"),
        pytest.param("httpx://x.com", {}, {}, id="longer-than-a-web-scheme"),
        pytest.param("http", {}, {}, id="scheme-with-no-colon"),
    ],
)
def test_nofollow(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    assert nofollow(LinkCandidate(url, "text", attrs)).attrs == expected


@pytest.mark.parametrize(
    ("url", "attrs", "expected"),
    [
        pytest.param("http://x.com", {}, {"target": "_blank"}, id="web-link-opens-a-tab"),
        pytest.param("https://x.com", {"rel": "a"}, {"rel": "a", "target": "_blank"}, id="other-attrs-kept"),
        pytest.param("mailto:a@b.com", {"target": "_blank"}, {}, id="stale-target-cleared"),
        pytest.param("tel:+1", {}, {}, id="non-web-without-a-target"),
    ],
)
def test_target_blank(url: str, attrs: dict[str, str], expected: dict[str, str]) -> None:
    assert target_blank(LinkCandidate(url, "text", attrs)).attrs == expected


def test_a_callback_answers_the_same_link() -> None:
    link = LinkCandidate("http://x.com", "text", {})
    assert nofollow(link) is link
    assert target_blank(link) is link


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
@pytest.mark.parametrize(
    "link",
    [
        pytest.param(object(), id="not-a-candidate"),
        pytest.param(SystemError, id="a-type"),
    ],
)
def test_a_callback_rejects_a_foreign_object(callback: object, link: object) -> None:
    with pytest.raises(TypeError, match="expects a LinkCandidate"):
        callback(link)  # ty: ignore[call-non-callable]  # the argument check is the point


class _UrlOnly:
    """A stand-in carrying a url but no attrs, the way a caller's own object might."""

    url = "http://x.com"


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
@pytest.mark.parametrize(
    "link",
    [
        pytest.param(LinkCandidate(1, "text", {}), id="url-is-not-a-string"),  # ty: ignore[invalid-argument-type]
        pytest.param(LinkCandidate("http://x.com", "text", "attrs"), id="attrs-is-not-a-dict"),  # ty: ignore[invalid-argument-type]
        pytest.param(_UrlOnly(), id="no-attrs-at-all"),
    ],
)
def test_a_callback_rejects_wrongly_typed_fields(callback: object, link: object) -> None:
    with pytest.raises(TypeError, match="expects a LinkCandidate"):
        callback(link)  # ty: ignore[call-non-callable]  # the field check is the point


@pytest.mark.parametrize("callback", [nofollow, target_blank], ids=["nofollow", "target_blank"])
def test_a_callback_takes_one_link(callback: object) -> None:
    with pytest.raises(TypeError):
        callback()  # ty: ignore[call-non-callable]  # the arity check is the point
