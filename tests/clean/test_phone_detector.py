from __future__ import annotations

from typing import Final, TypedDict

import pytest

from turbohtml.clean import LinkDetector, LinkSpan, PhoneNumber, PhoneNumbers, PhoneType

_US: Final = PhoneNumbers(regions=("US",))


def _fullwidth(text: str) -> str:
    """Built from ASCII so the source carries no look-alike fullwidth literals."""
    return "".join(chr(0xFF10 + int(char)) if char.isdigit() else "\uff0b" if char == "+" else char for char in text)


class _Policy(TypedDict, total=False):
    emails: bool
    bare_domains: bool
    tlds: list[str]
    schemes: list[str]


def _urls(text: str, policy: _Policy | None = None) -> list[str]:
    return [span.url for span in LinkDetector(phones=_US, **(policy or {})).find(text)]


def test_acceptance_span() -> None:
    assert LinkDetector(phones=_US).find("Call 650-253-0000")[0] == LinkSpan(
        5,
        17,
        "650-253-0000",
        "tel:+16502530000",
        False,  # ruff:ignore[boolean-positional-value-in-call]  # the span's positional contract
        phone=PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
    )


def test_phone_is_none_for_the_other_kinds() -> None:
    spans = LinkDetector(phones=_US).find("bob@example.com example.com https://x.org tel:+1-650-253-0000")
    assert [span.url for span in spans] == [
        "mailto:bob@example.com",
        "http://example.com",
        "https://x.org",
        "tel:+16502530000",
    ]
    assert [span.phone.international_number if span.phone else None for span in spans] == [
        None,
        None,
        None,
        "+16502530000",
    ]


def test_written_tel_uri_carries_its_number_through_tel_authority_form() -> None:
    assert [
        (span.text, span.url, span.phone.e164 if span.phone else None)
        for span in LinkDetector(phones=_US).find("tel://+16502530000")
    ] == [("tel://+16502530000", "tel:+16502530000", "+16502530000")]


def test_no_phones_leaves_digits_alone() -> None:
    assert LinkDetector().find("Call 650-253-0000 or +44 20 7946 0958") == []


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        pytest.param("Call 650-253-0000 now", 5, 17, id="ucs1"),
        pytest.param("\u30b3\u30fc\u30eb " + _fullwidth("650-253-0000") + " now", 4, 16, id="ucs2-fullwidth"),
        pytest.param("\U0001f600 call 650-253-0000", 7, 19, id="ucs4-emoji-prefix"),
    ],
)
def test_offsets_are_code_point_indexes(text: str, start: int, end: int) -> None:
    span = LinkDetector(phones=_US).find(text)[0]
    assert (span.start, span.end, span.text, span.url) == (start, end, text[start:end], "tel:+16502530000")


@pytest.mark.parametrize(
    ("phones", "text", "expected"),
    [
        pytest.param(_US, "650-253-0000", True, id="phones-on"),
        pytest.param(None, "650-253-0000", False, id="phones-off"),
        pytest.param(_US, "no number here", False, id="no-number"),
    ],
)
def test_has_link_sees_phones(phones: PhoneNumbers | None, text: str, *, expected: bool) -> None:
    assert LinkDetector(phones=phones).has_link(text) is expected


def test_has_link_exits_early_on_a_long_tail() -> None:
    assert LinkDetector(phones=_US).has_link("650-253-0000 " + "tail " * 45_000) is True


def test_span_repr_shows_the_phone() -> None:
    assert repr(LinkDetector(phones=_US).find("650-253-0000")[0]) == (
        "LinkSpan(start=0, end=12, text='650-253-0000', url='tel:+16502530000', phone=PhoneNumber(country_code=1, "
        "national_number='6502530000', extension=None, region='US', type=<PhoneType.FIXED_LINE_OR_MOBILE: "
        "'fixed_line_or_mobile'>))"
    )


def test_span_repr_and_equality_unchanged_without_a_phone() -> None:
    span = LinkSpan(0, 3, "abc", "http://abc", is_email=False)
    assert repr(span) == "LinkSpan(start=0, end=3, text='abc', url='http://abc')"
    assert span == LinkSpan(0, 3, "abc", "http://abc", False)  # ruff:ignore[boolean-positional-value-in-call]  # the positional contract
    assert span.phone is None


def test_spans_differing_only_in_phone_are_unequal() -> None:
    assert LinkSpan(
        0,
        12,
        "650-253-0000",
        "tel:+16502530000",
        is_email=False,
        phone=PhoneNumber(1, "6502530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE),
    ) != LinkSpan(0, 12, "650-253-0000", "tel:+16502530000", is_email=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("650-253-0000", ["tel:+16502530000"], id="hyphens"),
        pytest.param("650.253.0000", ["tel:+16502530000"], id="dots"),
        pytest.param("650 253 0000", ["tel:+16502530000"], id="spaces"),
        pytest.param("(650) 253-0000", ["tel:+16502530000"], id="parenthesized-area-code"),
        pytest.param("6502530000", ["tel:+16502530000"], id="bare"),
        pytest.param("1-650-253-0000", ["tel:+16502530000"], id="own-country-code"),
        pytest.param("+1 650-253-0000", ["tel:+16502530000"], id="plus"),
        pytest.param("+1 (650) 253-0000", ["tel:+16502530000"], id="plus-parenthesized"),
        pytest.param("011 44 20 7946 0958", ["tel:+442079460958"], id="idd"),
        pytest.param("650\u00a0253\u00a00000", ["tel:+16502530000"], id="nbsp"),
        pytest.param("650\u2013253\u20130000", ["tel:+16502530000"], id="en-dash"),
        pytest.param("650-253-0000.", ["tel:+16502530000"], id="sentence-end"),
        pytest.param("(650-253-0000)", ["tel:+16502530000"], id="parenthesized-whole"),
        pytest.param("650-253-0000:", ["tel:+16502530000"], id="colon-after"),
        pytest.param("650-253-0000 and 650-253-0001", ["tel:+16502530000", "tel:+16502530001"], id="two-numbers"),
        pytest.param("650-253-0000/650-253-0001", ["tel:+16502530000", "tel:+16502530001"], id="slash-separated-pair"),
        pytest.param("651-234-2345/332-445-1234", ["tel:+16512342345", "tel:+13324451234"], id="resume-after-match"),
        pytest.param("12345 650-253-0000", ["tel:+16502530000"], id="retry-from-second-group"),
        pytest.param("650-253-0000 12345", ["tel:+16502530000"], id="trailing-group-not-a-number"),
        pytest.param("650-253-0000 x", ["tel:+16502530000"], id="dangling-marker"),
        pytest.param("++1 650 253 0000", ["tel:+16502530000"], id="double-plus"),
        pytest.param("2024 650253", ["tel:+12024650253"], id="short-groups-joined"),
        pytest.param("(650-253-0000", ["tel:+16502530000"], id="unbalanced-open"),
        pytest.param("650-253-0000)", ["tel:+16502530000"], id="close-after-run"),
    ],
)
def test_written_forms(text: str, expected: list[str]) -> None:
    assert _urls(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("650-253", id="too-short"),
        pytest.param("650-253-000", id="nine-digits"),
        pytest.param("555-123-4567", id="invalid-area-code"),
        pytest.param("123-456-7890", id="invalid-exchange"),
        pytest.param("3/10/2011", id="slash-date"),
        pytest.param("2012-01-02 08:00", id="timestamp"),
        pytest.param("12:30", id="time"),
        pytest.param("127.0.0.1", id="ipv4"),
        pytest.param("10.0.0.1:8080", id="ipv4-port"),
        pytest.param("255.255.255.255", id="ipv4-max"),
        pytest.param("212.234.56.78", id="ipv4-phone-like"),
        pytest.param("4111 1111 1111 1111", id="card"),
        pytest.param("1-800-FLOWERS", id="letters"),
        pytest.param("0000000000", id="zeros"),
        pytest.param("650-253-0000-1234", id="hyphenated-tail-without-a-space"),
        pytest.param("1.2.3.4.5.6.7.8.9.0", id="dotted-digits"),
        pytest.param("00 0 650 253 0000", id="idd-then-zero"),
        pytest.param("650-253-00001", id="eleven-digits-run"),
    ],
)
def test_shapes_that_are_not_numbers(text: str) -> None:
    assert _urls(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("3/10/2011 650-253-0000", ["tel:+16502530000"], id="leading-date"),
        pytest.param("650-253-0000 3/10/2011", ["tel:+16502530000"], id="trailing-date"),
        pytest.param("1234/5/67 650-253-0000", ["tel:+16502530000"], id="not-a-date"),
        pytest.param("2012-01-02 08 650-253-0000", ["tel:+16502530000"], id="timestamp-shape-without-minutes"),
        pytest.param("12:30 650-253-0000", ["tel:+16502530000"], id="time-before"),
        pytest.param("650-253-0000 12:30", ["tel:+16502530000"], id="time-after"),
        pytest.param("256.1.1.1 650-253-0000", ["tel:+16502530000"], id="not-an-ipv4"),
        pytest.param("(530) 583-6985 x302/x2303", ["tel:+15305836985;ext=302"], id="second-number-start"),
        pytest.param("(530) 583-698 x302/x2303", [], id="second-number-start-invalid-main"),
        pytest.param("+1 650-253-0000 - 5", ["tel:+16502530000"], id="trailing-dash-digit"),
        pytest.param("1 (650) 253-0000", ["tel:+16502530000"], id="code-then-parenthesized"),
        pytest.param("(1 (650) 253-0000)", ["tel:+16502530000"], id="nested-brackets"),
        pytest.param("((650) 253-0000)", ["tel:+16502530000"], id="double-opener"),
        pytest.param("text) 650-253-0000", ["tel:+16502530000"], id="closer-before"),
        pytest.param("(650) 253-0000 (3", ["tel:+16502530000"], id="page-range-shape-splits-at-the-bracket"),
        pytest.param("pages 253-0000 (754) 223-3321", ["tel:+17542233321"], id="page-range-rejects-its-chunk"),
    ],
)
def test_poisoned_groups_do_not_block_the_rest(text: str, expected: list[str]) -> None:
    assert _urls(text) == expected


def test_a_long_run_of_groups_is_bounded() -> None:
    assert _urls(" ".join(["1234"] * 30) + " 650-253-0000") == ["tel:+16502530000"]


def test_a_group_over_twenty_digits_ends_the_run() -> None:
    assert _urls("1" * 21 + " 650-253-0000") == ["tel:+16502530000"]


def test_a_run_over_250_code_points_is_cut() -> None:
    assert _urls("1 " * 130 + "650-253-0000") == ["tel:+16502530000"]


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        pytest.param("123@example.com", {}, ["mailto:123@example.com"], id="numeric-local-part-is-an-email"),
        pytest.param("123@example.com", {"emails": False}, [], id="run-touching-at-is-never-a-phone"),
        pytest.param("6502530000@example.com", {"emails": False}, [], id="phone-local-part-with-emails-off"),
        pytest.param("@6502530000", {}, [], id="at-before"),
        pytest.param("6502530000@", {}, [], id="at-after"),
        pytest.param("bob+6502530000@example.com", {}, ["mailto:bob+6502530000@example.com"], id="plus-tag-email"),
        pytest.param("1password.com", {}, ["http://1password.com"], id="domain-starting-with-a-digit"),
        pytest.param("6502530000.com", {}, ["http://6502530000.com"], id="numeric-label-domain"),
        pytest.param(
            "6502530000.com", {"bare_domains": False}, ["tel:+16502530000"], id="numeric-label-with-domains-off"
        ),
        pytest.param(
            "650.253.0000.example.com", {}, ["http://650.253.0000.example.com"], id="dotted-number-inside-a-domain"
        ),
        pytest.param(
            "650.253.0000.example.com",
            {"bare_domains": False},
            ["tel:+16502530000"],
            id="dotted-number-with-domains-off",
        ),
        pytest.param(
            "650-253-0000.example.com", {}, ["http://650-253-0000.example.com"], id="hyphenated-number-inside-a-domain"
        ),
        pytest.param("650-253-0000.invalidtld", {}, ["tel:+16502530000"], id="not-a-tld"),
        pytest.param("650-253-0000.2024", {}, ["tel:+16502530000"], id="numeric-suffix"),
        pytest.param("650-253-0000.corp", {}, ["tel:+16502530000"], id="private-suffix-unregistered"),
        pytest.param(
            "650-253-0000.corp", {"tlds": ["corp"]}, ["http://650-253-0000.corp"], id="private-suffix-registered"
        ),
        pytest.param("2024.example.com", {}, ["http://2024.example.com"], id="year-label-domain"),
        pytest.param(
            "bob@example.com 650-253-0000", {}, ["mailto:bob@example.com", "tel:+16502530000"], id="email-then-phone"
        ),
        pytest.param(
            "650-253-0000 bob@example.com", {}, ["tel:+16502530000", "mailto:bob@example.com"], id="phone-then-email"
        ),
        pytest.param("http://127.0.0.1:8080/x", {}, ["http://127.0.0.1:8080/x"], id="url-with-ipv4"),
        pytest.param("https://x.org/650-253-0000", {}, ["https://x.org/650-253-0000"], id="number-inside-a-url"),
        pytest.param(
            "see example.com/650-253-0000", {}, ["http://example.com/650-253-0000"], id="number-inside-a-domain-path"
        ),
    ],
)
def test_emails_and_domains_keep_winning(text: str, kwargs: _Policy, expected: list[str]) -> None:
    assert _urls(text, kwargs) == expected


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        pytest.param("tel:+1-650-253-0000", {}, ["tel:+16502530000"], id="tel-uri-links-to-its-number"),
        pytest.param("callto:+1-650-253-0000", {}, ["tel:+16502530000"], id="unregistered-scheme-digits-link"),
        pytest.param(
            "callto:+1-650-253-0000", {"schemes": ["callto"]}, ["callto:+1-650-253-0000"], id="registered-scheme-wins"
        ),
        pytest.param("sip:6502530000@x.example", {"emails": False}, [], id="sip-address-touches-at"),
        pytest.param(
            "sip:6502530000@x.example", {"schemes": ["sip"]}, ["sip:6502530000@x.example"], id="sip-registered"
        ),
        pytest.param("hppt://6502530000", {}, ["tel:+16502530000"], id="typo-scheme-digits-link"),
    ],
)
def test_scheme_precedence(text: str, kwargs: _Policy, expected: list[str]) -> None:
    assert _urls(text, kwargs) == expected


@pytest.mark.parametrize(
    ("text", "regions", "url", "region", "number_type"),
    [
        pytest.param(
            "+44 20 7946 0958", (), "tel:+442079460958", "GB", PhoneType.FIXED_LINE, id="plus-without-regions"
        ),
        pytest.param(
            _fullwidth("+44 20 7946 0958"), (), "tel:+442079460958", "GB", PhoneType.FIXED_LINE, id="fullwidth-plus"
        ),
        pytest.param("+800 1234 5678", (), "tel:+80012345678", "001", PhoneType.TOLL_FREE, id="non-geographic"),
        pytest.param("+1 268 464 1234", (), "tel:+12684641234", "AG", PhoneType.MOBILE, id="routed-shared-code"),
        pytest.param("268 464 1234", ("US",), "tel:+12684641234", "AG", PhoneType.MOBILE, id="routed-from-us"),
        pytest.param("01624 756789", ("GB",), "tel:+441624756789", "IM", PhoneType.FIXED_LINE, id="routed-from-gb"),
        pytest.param("07400 123456", ("GB",), "tel:+447400123456", "GB", PhoneType.MOBILE, id="gb-mobile"),
        pytest.param("800-234-5678", ("US",), "tel:+18002345678", "US", PhoneType.TOLL_FREE, id="us-toll-free"),
        pytest.param("900-234-5678", ("US",), "tel:+19002345678", "US", PhoneType.PREMIUM_RATE, id="us-premium"),
        pytest.param(
            "+41 (0) 78 927 2696", (), "tel:+41789272696", "CH", PhoneType.MOBILE, id="prefix-after-country-code"
        ),
        pytest.param("0 11 15 1234-5678", ("AR",), "tel:+5491112345678", "AR", PhoneType.MOBILE, id="ar-transform"),
        pytest.param(
            "06 12345678", ("IT",), "tel:+390612345678", "IT", PhoneType.FIXED_LINE, id="italian-leading-zero"
        ),
        pytest.param("030 12345678", ("DE",), "tel:+493012345678", "DE", PhoneType.FIXED_LINE, id="de-fixed"),
        pytest.param("0011 44 20 7946 0958", ("AU",), "tel:+442079460958", "GB", PhoneType.FIXED_LINE, id="au-idd"),
        pytest.param("00 44 20 7946 0958", ("DE",), "tel:+442079460958", "GB", PhoneType.FIXED_LINE, id="00-idd"),
        pytest.param("8999", ("TA",), "tel:+2908999", "TA", PhoneType.FIXED_LINE, id="short-plan"),
        pytest.param("+290 8999", ("SG",), "tel:+2908999", "TA", PhoneType.FIXED_LINE, id="short-plan-international"),
    ],
)
def test_readings(text: str, regions: tuple[str, ...], url: str, region: str, number_type: PhoneType) -> None:
    assert [
        (span.url, span.phone.region, span.phone.type)
        for span in LinkDetector(phones=PhoneNumbers(regions=regions)).find(text)
        if span.phone
    ] == [(url, region, number_type)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("00 999 1234567", id="idd-without-a-country-code"),
        pytest.param("011", id="idd-alone"),
    ],
)
def test_idd_commitment(text: str) -> None:
    assert _urls(text) == []


def test_plus_only_mode_ignores_national_text() -> None:
    assert [span.url for span in LinkDetector(phones=PhoneNumbers()).find("650-253-0000 but +1 650-253-0001")] == [
        "tel:+16502530001"
    ]


@pytest.mark.parametrize(
    ("text", "url", "region"),
    [
        pytest.param("555-123-4567", "tel:+15551234567", None, id="possible-unrouted-without-a-type"),
        pytest.param("650-253-0000", "tel:+16502530000", "US", id="valid-number-still-unknown-type"),
        pytest.param("268 555 1234", "tel:+12685551234", "AG", id="routed-region-reported"),
    ],
)
def test_possible_mode(text: str, url: str, region: str | None) -> None:
    assert [
        (span.url, span.phone.region, span.phone.type)
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False)).find(text)
        if span.phone
    ] == [(url, region, PhoneType.UNKNOWN)]


def test_possible_mode_rejects_impossible_lengths() -> None:
    assert LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False)).find("555-1234-56789") == []


def test_possible_mode_accepts_local_only_lengths() -> None:
    assert [
        span.url for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False)).find("253-0000")
    ] == ["tel:+12530000"]
    assert LinkDetector(phones=_US).find("253-0000") == []
