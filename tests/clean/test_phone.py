from __future__ import annotations

import copy
import pickle  # ruff:ignore[suspicious-pickle-import]  # round-tripping our own trusted payloads
import random
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Final, TypedDict

import pytest

from turbohtml.clean import (
    LinkDetector,
    Linker,
    Linkify,
    LinkSpan,
    PhoneGrouping,
    PhoneNumber,
    PhoneNumbers,
    PhoneType,
    linkify,
)

_US: Final[PhoneNumbers] = PhoneNumbers(regions=("US",))
_WORKERS: Final[int] = 8


def _fullwidth(text: str) -> str:
    # Built from ASCII so the source carries no look-alike fullwidth literals.
    return "".join(chr(0xFF10 + int(char)) if char.isdigit() else "\uff0b" if char == "+" else char for char in text)


class _Policy(TypedDict, total=False):
    emails: bool
    bare_domains: bool
    tlds: list[str]
    schemes: list[str]


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
        (span.text, span.url, span.phone and span.phone.e164)
        for span in LinkDetector(phones=_US).find("tel://+16502530000")
    ] == [("tel://+16502530000", "tel:+16502530000", "+16502530000")]


def test_no_phones_leaves_digits_alone() -> None:
    assert LinkDetector().find("Call 650-253-0000 or +44 20 7946 0958") == []


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        pytest.param("Call 650-253-0000 now", 5, 17, id="ucs1"),
        pytest.param(f"\u30b3\u30fc\u30eb {_fullwidth('650-253-0000')} now", 4, 16, id="ucs2-fullwidth"),
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
    assert LinkDetector(phones=_US).has_link(f"650-253-0000 {'tail ' * 45000}") is True


def test_extension_with_many_space_runs_keeps_the_number() -> None:
    gap = f"{' ' * 34}\t"
    text = f"44 20 7946 0958{gap * 140}x10958"
    assert [span.url for span in LinkDetector(phones=PhoneNumbers(regions=("GB",))).find(text)] == [
        "tel:+442079460958;ext=10958"
    ]


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


def _urls(text: str, policy: _Policy | None = None) -> list[str]:
    return [span.url for span in LinkDetector(phones=_US, **(policy or {})).find(text)]


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
    assert _urls(f"{' '.join(['1234'] * 30)} 650-253-0000") == ["tel:+16502530000"]


def test_a_group_over_twenty_digits_ends_the_run() -> None:
    assert _urls(f"{'1' * 21} 650-253-0000") == ["tel:+16502530000"]


def test_a_run_over_250_code_points_is_cut() -> None:
    assert _urls(f"{'1 ' * 130}650-253-0000") == ["tel:+16502530000"]


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


_PHONE_EXTENSIONS_US: Final[PhoneNumbers] = PhoneNumbers(regions=("US",))


@pytest.mark.parametrize(
    "tail",
    [
        pytest.param(";ext=123", id="rfc-3966"),
        pytest.param(" ext 123", id="ext"),
        pytest.param(" ext. 123", id="ext-dot"),
        pytest.param(" ext: 123", id="ext-colon"),
        pytest.param(" xt 123", id="xt"),
        pytest.param(" xtn 123", id="xtn"),
        pytest.param(" extn 123", id="extn"),
        pytest.param(" extension 123", id="extension"),
        pytest.param(" extensi\u00f3n 123", id="extension-spanish"),
        pytest.param(" extensio\u0301n 123", id="extension-decomposed-accent"),
        pytest.param(" anexo 123", id="anexo"),
        pytest.param(" \u0434\u043e\u0431 123", id="dob"),
        pytest.param(" EXT 123", id="upper"),
        pytest.param(" Ext 123", id="mixed"),
        pytest.param(" EXTENSI\u00d3N 123", id="upper-spanish"),
        pytest.param(" \uff45\uff58\uff54 123", id="fullwidth-ext"),
        pytest.param(" \uff45\uff58\uff54\uff4e 123", id="fullwidth-extn"),
        pytest.param(" x 123", id="x"),
        pytest.param(" x123", id="x-no-space"),
        pytest.param(" X123", id="upper-x"),
        pytest.param(" \uff58123", id="fullwidth-x"),
        pytest.param(" #123", id="hash"),
        pytest.param(" \uff03123", id="fullwidth-hash"),
        pytest.param(" ~123", id="tilde"),
        pytest.param(" int 123", id="int"),
        pytest.param(" \uff49\uff4e\uff54 123", id="fullwidth-int"),
        pytest.param(" ext \uff11\uff12\uff13", id="fullwidth-digits"),
        pytest.param(" ext \u0661\u0662\u0663", id="arabic-indic-digits"),
    ],
)
def test_extension_forms(tail: str) -> None:
    text = f"650-253-0000{tail}"
    assert _find(text) == [(text, "123")]


def _find(text: str, phones: PhoneNumbers = _PHONE_EXTENSIONS_US) -> list[tuple[str, str | None]]:
    return [
        (span.text, span.phone.extension if span.phone else None) for span in LinkDetector(phones=phones).find(text)
    ]


@pytest.mark.parametrize(
    ("tail", "extension"),
    [
        pytest.param(f";ext={'1' * 20}", "1" * 20, id="rfc-twenty"),
        pytest.param(f" ext {'1' * 20}", "1" * 20, id="explicit-twenty"),
        pytest.param(f" x{'1' * 9}", "1" * 9, id="ambiguous-nine"),
    ],
)
def test_extension_digit_caps(tail: str, extension: str) -> None:
    text = f"650-253-0000{tail}"
    assert _find(text) == [(text, extension)]


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param(f";ext={'1' * 21}", [(f"650-253-0000;ext={'1' * 20}", "1" * 20)], id="rfc-keeps-twenty"),
        pytest.param(f" ext {'1' * 21}", [(f"650-253-0000 ext {'1' * 20}", "1" * 20)], id="explicit-keeps-twenty"),
        pytest.param(" - 123#", [("650-253-0000", None)], id="american-hash-outside-the-candidate"),
        pytest.param("- 123#", [("650-253-0000", None)], id="american-without-a-space"),
        pytest.param(f" - {'1' * 7}#", [("650-253-0000", None)], id="american-over"),
        pytest.param(" 123#", [("650-253-0000", None)], id="hash-without-separator"),
    ],
)
def test_extension_shapes_the_matcher_cuts(tail: str, expected: list[tuple[str, str | None]]) -> None:
    assert _find(f"650-253-0000{tail}") == expected


def test_ambiguous_form_over_nine_digits_reads_as_a_second_number() -> None:
    assert _find(f"650-253-0000 x{'1' * 10}") == [("650-253-0000", None)]


def test_match_end_covers_the_extension() -> None:
    span = LinkDetector(phones=_PHONE_EXTENSIONS_US).find("ring 650-253-0000 ext 12 now")[0]
    assert (span.start, span.end, span.text, span.url) == (5, 24, "650-253-0000 ext 12", "tel:+16502530000;ext=12")


def test_marker_group_in_the_middle() -> None:
    assert _find("650 x 253 0000") == []
    assert _find("650 x 253 0000", PhoneNumbers(regions=("US",), require_valid=False)) == [("650 x 253 0000", None)]


def test_carrier_code_marker_needs_the_number_after_it() -> None:
    assert _find("xx 650-253-0000") == [("650-253-0000", None)]
    assert _find("650 xx 253-0000") == []


def test_extension_digits_are_folded_in_the_href() -> None:
    assert (
        LinkDetector(phones=_PHONE_EXTENSIONS_US).find("650-253-0000 ext \uff11\uff12")[0].url
        == "tel:+16502530000;ext=12"
    )


_ALL: Final[tuple[PhoneGrouping, ...]] = (PhoneGrouping.ANY, PhoneGrouping.STRICT, PhoneGrouping.EXACT)
_LOOSE: Final[tuple[PhoneGrouping, ...]] = (PhoneGrouping.ANY, PhoneGrouping.STRICT)
_NONE: Final[tuple[PhoneGrouping, ...]] = (PhoneGrouping.ANY,)
_UNBROKEN: Final[tuple[PhoneGrouping, ...]] = (PhoneGrouping.ANY, PhoneGrouping.EXACT)


@pytest.mark.parametrize(
    ("region", "text", "accepting"),
    [
        pytest.param("US", "(415) 666-7777", _ALL, id="us-national-format"),
        pytest.param("US", "415-666-7777", _ALL, id="us-hyphens"),
        pytest.param("US", "415.666.7777", _ALL, id="us-dots"),
        pytest.param("US", "4156667777", _ALL, id="us-unbroken"),
        pytest.param("US", "1 415 666 7777", _ALL, id="us-own-code"),
        pytest.param("US", "1415 666 7777", _ALL, id="us-code-glued-to-the-area-code"),
        pytest.param("US", "+1 (415) 666-7777", _ALL, id="us-plus"),
        pytest.param("US", "+14156667777", _ALL, id="us-e164"),
        pytest.param("US", "011 1 415 666 7777", _ALL, id="us-idd"),
        pytest.param("US", "(415) 666-7777 x 12", _ALL, id="us-extension"),
        pytest.param("US", "+1/415/666-7777", _ALL, id="us-slash-after-the-code-is-not-a-date"),
        pytest.param("US", "415 6667777", _LOOSE, id="us-last-groups-merged"),
        pytest.param("US", "415-6667777", _LOOSE, id="us-last-groups-merged-with-hyphen"),
        pytest.param("US", "415666 7777", _NONE, id="us-first-groups-merged"),
        pytest.param("US", "41 566 67777", _NONE, id="us-shifted-groups"),
        pytest.param("US", "4 15 666 7777", _NONE, id="us-split-area-code"),
        pytest.param("US", "415/666/7777", _NONE, id="us-two-slashes"),
        pytest.param("GB", "020 7946 0958", _ALL, id="gb-national-format"),
        pytest.param("GB", "0207 946 0958", _ALL, id="gb-alternate-format"),
        pytest.param("GB", "+44 (0)20 7946 0958", _ALL, id="gb-optional-prefix-in-brackets"),
        pytest.param("GB", "0 20 7946 0958", _ALL, id="gb-prefix-apart"),
        pytest.param("GB", "020 79460958", _LOOSE, id="gb-last-groups-merged"),
        pytest.param("DE", "030 12345678", _ALL, id="de-national-format"),
        pytest.param("DE", "030 1234 5678", _ALL, id="de-alternate-format"),
        pytest.param("DE", "030 123 456 78", _ALL, id="de-alternate-format-with-more-groups"),
        pytest.param("DE", "030/12345678", _ALL, id="de-one-slash"),
        pytest.param("DE", "030/1234/5678", _NONE, id="de-two-slashes"),
        pytest.param("DE", "0151 2345 6789", _NONE, id="de-mobile-grouped-wrongly"),
        pytest.param("DE", "01512 3456789", _ALL, id="de-mobile-grouped-right"),
        pytest.param("FR", "01 23 45 67 89", _ALL, id="fr-pairs"),
        pytest.param("FR", "01 2345 6789", _LOOSE, id="fr-pairs-merged"),
        pytest.param("FR", "+33 123 456 789", _NONE, id="fr-triples"),
        pytest.param("AR", "011 15-2345-6789", _NONE, id="ar-mobile-token-only-any"),
        pytest.param("AR", "+54 9 11 2345-6789", _ALL, id="ar-mobile-international"),
        pytest.param("US", "1/415/666-7777", _ALL, id="us-own-code-then-one-slash"),
        pytest.param("US", "1/415/666/7777", _NONE, id="us-own-code-then-two-slashes"),
        pytest.param("US", "+1 415/666/7777", _NONE, id="us-plus-and-area-code-before-two-slashes"),
        pytest.param("AG", "268 460-1234", _ALL, id="ag-area-code-written"),
        pytest.param("AG", "460 1234", _NONE, id="ag-transformed-prefix-groups-absent"),
        pytest.param("AG", "4601234", _UNBROKEN, id="ag-transformed-unbroken-run"),
        pytest.param("ES", "612345678", _ALL, id="es-no-national-prefix-unbroken"),
        pytest.param("ES", "61234 5678", _LOOSE, id="es-no-national-prefix-merged"),
        pytest.param("AC", "62889", _ALL, id="ac-region-without-formats"),
        pytest.param("SM", "0549 912345", _ALL, id="sm-area-code-written"),
        pytest.param("SM", "91 23 45", _NONE, id="sm-transformed-prefix-groups-regrouped"),
        pytest.param("SM", "912345", _UNBROKEN, id="sm-transformed-unbroken-run"),
        pytest.param("NF", "2 2123", _NONE, id="nf-one-digit-transform"),
        pytest.param("DE", "(02) 3234 5678", _NONE, id="de-bracketed-prefix-shorter-than-the-number"),
    ],
)
def test_grouping_leniencies_follow_the_matcher(region: str, text: str, accepting: tuple[PhoneGrouping, ...]) -> None:
    assert {
        grouping: [
            span.text for span in LinkDetector(phones=PhoneNumbers(regions=(region,), grouping=grouping)).find(text)
        ]
        for grouping in PhoneGrouping
    } == {grouping: [text] if grouping in accepting else [] for grouping in PhoneGrouping}


def test_grouping_applies_inside_prose() -> None:
    assert [
        span.text
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT)).find(
            "call (415) 666-7777 or 415666 7777 now"
        )
    ] == ["(415) 666-7777"]


@pytest.mark.parametrize(
    ("text", "regions", "expected"),
    [
        pytest.param("460-1234", ("AG",), ["tel:+12684601234"], id="ag-local-number-rule-anchored-at-the-end"),
        pytest.param("91123456789", ("AR",), ["tel:+5491123456789"], id="ar-prefix-rule-matches-nothing"),
        pytest.param("800 123 4567", ("RU",), ["tel:+78001234567"], id="ru-prefix-guard-keeps-the-toll-free-8"),
        pytest.param("3788762", ("DE",), [], id="de-general-pattern-without-a-type"),
        pytest.param("(02) 3234 5678", ("PH",), ["tel:+63232345678"], id="ph-format-with-a-class-pattern"),
        pytest.param("20 7946 0958", ("GB",), [], id="gb-national-prefix-missing"),
        pytest.param("1 506 234 5678", ("CA",), ["tel:+15062345678"], id="own-code-from-a-non-main-region"),
        pytest.param("1-253-0000", ("US",), [], id="stripped-length-is-local-only"),
        pytest.param("1-2530-0000", ("US",), [], id="stripped-length-is-invalid"),
        pytest.param("268 555 1234", ("US",), [], id="routed-region-rejects"),
        pytest.param("+0 650 253 0000", ("US",), [], id="country-code-cannot-start-with-zero"),
        pytest.param("+999 123 4567", ("US",), [], id="unassigned-country-code"),
        pytest.param("+44 1", ("US",), [], id="too-short-after-the-country-code"),
        pytest.param("011 0 650 253 0000", ("US",), [], id="idd-followed-by-zero"),
        pytest.param("011 12", ("US",), [], id="idd-with-too-little-after-it"),
        pytest.param("+ 650 253 0000", ("US",), [], id="plus-then-space-reads-nothing"),
        pytest.param("(+1 650 253 0000)", ("US",), ["tel:+16502530000"], id="bracket-then-plus"),
        pytest.param("+(650) 253-0000", ("US",), ["tel:+16502530000"], id="plus-then-bracket"),
        pytest.param("(( 650 253 0000", ("US",), ["tel:+16502530000"], id="two-openers-with-space"),
        pytest.param("\uff08650\uff09 253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-round-brackets"),
        pytest.param("\uff3b650\uff3d 253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-square-brackets"),
        pytest.param("[650] 253-0000", ("US",), ["tel:+16502530000"], id="square-brackets"),
        pytest.param("650\uff0f253\uff0f0000", ("US",), ["tel:+16502530000"], id="fullwidth-slash"),
        pytest.param("650-253-0000 (1) (2) (3)", ("US",), ["tel:+16502530000"], id="three-bracket-pairs"),
        pytest.param("650-253-0000 (1) (2) (3) (4)", ("US",), ["tel:+16502530000"], id="fourth-pair-splits"),
        pytest.param("650-253-0000 )1( 2", ("US",), ["tel:+16502530000"], id="closer-before-opener-splits"),
        pytest.param("650-253-0000 () 1", ("US",), ["tel:+16502530000"], id="empty-pair-splits"),
        pytest.param("650-253-0000 (1 2", ("US",), ["tel:+16502530000"], id="unclosed-pair-splits"),
        pytest.param(
            "(530) 583-6985 x302/ x2303", ("US",), ["tel:+15305836985;ext=302"], id="second-number-after-space"
        ),
        pytest.param("(530) 583-6985 x302/", ("US",), ["tel:+15305836985;ext=302"], id="slash-at-the-end"),
        pytest.param(
            "(530) 583-6985 x302 - 5", ("US",), ["tel:+15305836985;ext=302"], id="marker-extension-inside-a-split"
        ),
        pytest.param(
            "650-253-0000 x - 5", ("US",), ["tel:+16502530000;ext=5"], id="marker-then-spaced-hyphen-extension"
        ),
        pytest.param("650-253-0000 \uff58123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-x-marker"),
        pytest.param("650-253-0000 \uff03123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-hash-marker"),
        pytest.param("650-253-0000 ~123", ("US",), ["tel:+16502530000;ext=123"], id="tilde-marker"),
        pytest.param("650-253-0000 \uff5e123", ("US",), ["tel:+16502530000;ext=123"], id="fullwidth-tilde-marker"),
        pytest.param("650-253-0000 ext 12 34", ("US",), ["tel:+16502530000;ext=12"], id="extension-digits-then-more"),
        pytest.param(
            f"650-253-0000 ext {'1' * 25}",
            ("US",),
            [f"tel:+16502530000;ext={'1' * 20}"],
            id="extension-cap-inside-a-run",
        ),
        pytest.param("650\u00ad253\u00ad0000", ("US",), ["tel:+16502530000"], id="soft-hyphen-separator"),
        pytest.param("650\u2060253\u20600000", ("US",), ["tel:+16502530000"], id="word-joiner-separator"),
        pytest.param("650\u2053253\u20530000", ("US",), ["tel:+16502530000"], id="swung-dash-separator"),
        pytest.param("650\u223c253\u223c0000", ("US",), ["tel:+16502530000"], id="tilde-operator-separator"),
        pytest.param("1\uff08650\uff09253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-parens-inside"),
        pytest.param("1\uff3b650\uff3d253-0000", ("US",), ["tel:+16502530000"], id="fullwidth-brackets-inside"),
        pytest.param("1[650]253-0000", ("US",), ["tel:+16502530000"], id="square-brackets-inside"),
        pytest.param("650-253-0000 (1(2) 3", ("US",), ["tel:+16502530000"], id="opener-inside-a-pair-splits"),
        pytest.param(
            "650-253-0000 x1234567890 - 5", ("US",), ["tel:+16502530000"], id="marker-group-too-long-for-an-extension"
        ),
        pytest.param("0212345678", ("AU",), ["tel:+61212345678"], id="routerless-shared-code"),
        pytest.param("0549 886377", ("SM",), ["tel:+3780549886377"], id="sm-leading-zero-kept"),
        pytest.param(
            "references 650-253-0000", ("US",), ["tel:+16502530000"], id="word-longer-than-a-label-with-its-prefix"
        ),
        pytest.param("{650-253-0000", ("US",), ["tel:+16502530000"], id="brace-before"),
        pytest.param(
            "pages 1-5     (3 pages) 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-five-spaces"
        ),
        pytest.param("1-5 (\u0663 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-a-non-ascii-count"),
        pytest.param("1-\u0665 (3 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-with-a-non-ascii-page"),
        pytest.param("1-5 (x 650-253-0000", ("US",), ["tel:+16502530000"], id="page-range-without-a-count"),
        pytest.param(
            "2012-01-02 08:-1 650-253-0000",
            ("US",),
            ["tel:+12012010208", "tel:+16502530000"],
            id="minutes-tens-below-zero",
        ),
        pytest.param(
            "2012-01-02 08:0- 650-253-0000",
            ("US",),
            ["tel:+12012010208", "tel:+16502530000"],
            id="minutes-units-below-zero",
        ),
        pytest.param("01234567890", ("CH",), [], id="stripped-length-in-a-gap"),
        pytest.param("08 123 456", ("SE",), ["tel:+468123456"], id="format-longer-than-the-number"),
        pytest.param("1 650 253 0000 12345", ("US",), [], id="own-code-then-too-many-digits"),
        pytest.param(
            "(530) 583-6985 x302/x", ("US",), ["tel:+15305836985;ext=302"], id="second-number-marker-at-the-end"
        ),
        pytest.param(
            "650-253-0000 -x 123", ("US",), ["tel:+16502530000"], id="marker-after-a-hyphen-is-not-an-extension"
        ),
        pytest.param(
            "(530) 583-6985 -x302 - 5", ("US",), ["tel:+15305836985"], id="marker-after-a-hyphen-inside-a-split"
        ),
        pytest.param("101-02 08:00", ("US",), [], id="seven-digits-before-the-hour"),
        pytest.param(
            "1999-01-02 08:00 650-253-0000", ("US",), ["tel:+16502530000"], id="timestamp-in-the-previous-century"
        ),
        pytest.param("{ref 650-253-0000", ("US",), [], id="label-after-a-brace"),
        pytest.param("1-5 (\u0663) 650-253-0000", ("US",), ["tel:+16502530000"], id="page-count-not-ascii"),
        pytest.param("1-5 (x)650-253-0000", ("US",), [], id="page-count-not-a-digit"),
        pytest.param("594 10 12 34", ("GF",), [], id="own-code-number-valid-as-a-whole"),
        pytest.param(
            "(530) 583-6985 x302/x2303 and more", ("US",), ["tel:+15305836985;ext=302"], id="second-number-then-text"
        ),
    ],
)
def test_readings_reach_each_rule(text: str, regions: tuple[str, ...], expected: list[str]) -> None:
    assert _phone_paths_urls(text, regions) == expected


def _phone_paths_urls(text: str, regions: tuple[str, ...] = ("US",), *, valid: bool = True) -> list[str]:
    return [span.url for span in LinkDetector(phones=PhoneNumbers(regions=regions, require_valid=valid)).find(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("3/10/11 650-253-0000", ["tel:+16502530000"], id="two-digit-year"),
        pytest.param("31/8/2011 650-253-0000", ["tel:+16502530000"], id="day-first"),
        pytest.param("10/12/82 650-253-0000", ["tel:+16502530000"], id="short-year"),
        pytest.param("40/40/2011 650-253-0000", ["tel:+16502530000"], id="middle-part-over-39-is-not-a-date"),
        pytest.param("3/10/3011 650-253-0000", ["tel:+16502530000"], id="any-two-digit-year-part"),
        pytest.param("45/10/2011 650-253-0000", ["tel:+16502530000"], id="first-part-is-the-last-digit"),
        pytest.param("3/10/2 650-253-0000", ["tel:+16502530000"], id="one-digit-year-is-not-a-date"),
        pytest.param("3//10/2011 650-253-0000", ["tel:+13102011", "tel:+16502530000"], id="double-slash-is-not-a-date"),
        pytest.param("123/10/2011 650-253-0000", ["tel:+16502530000"], id="three-digit-first-part"),
        pytest.param("2012/01/02 08:00 650-253-0000", ["tel:+16502530000"], id="slash-timestamp"),
        pytest.param("20120102 08:00 650-253-0000", ["tel:+16502530000"], id="compact-timestamp"),
        pytest.param("2012-01/02 08:00 650-253-0000", ["tel:+16502530000"], id="mixed-date-separators"),
        pytest.param("2012-0102 08:00 650-253-0000", ["tel:+16502530000"], id="one-separator"),
        pytest.param("12012-01-02 08:00 650-253-0000", ["tel:+16502530000"], id="year-is-the-tail-of-a-longer-group"),
        pytest.param("2012-01-02  08:00 650-253-0000", ["tel:+16502530000"], id="hour-after-two-spaces"),
        pytest.param(
            "2012-01-02 08:60 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="minutes-out-of-range"
        ),
        pytest.param(
            "2012-01-02 08:0x 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="minutes-not-digits"
        ),
        pytest.param("2012-01-02 08:", ["tel:+12012010208"], id="colon-without-minutes"),
        pytest.param("2012-01-02 08:0", ["tel:+12012010208"], id="one-digit-minutes"),
        pytest.param("2012-01-02 08:\u0660\u0660", ["tel:+12012010208"], id="minutes-not-ascii"),
        pytest.param(
            "3012-01-02 08:00 650-253-0000", ["tel:+13012010208", "tel:+16502530000"], id="year-outside-the-century"
        ),
        pytest.param(
            "2012-21-02 08:00 650-253-0000", ["tel:+12012210208", "tel:+16502530000"], id="month-out-of-range"
        ),
        pytest.param("2012-01-42 08:00 650-253-0000", ["tel:+12012014208", "tel:+16502530000"], id="day-out-of-range"),
        pytest.param("2012-01-02 38:00 650-253-0000", ["tel:+12012010238", "tel:+16502530000"], id="hour-out-of-range"),
        pytest.param("2012-01-02 8:00 650-253-0000", ["tel:+16502530000"], id="one-digit-hour"),
        pytest.param("2012.01.02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="dotted-date"),
        pytest.param("2012--01-02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="double-hyphen"),
        pytest.param(
            "201-201-02 08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="separator-off-the-boundary"
        ),
        pytest.param(
            "2012-01-02-08:00 650-253-0000", ["tel:+12012010208", "tel:+16502530000"], id="hyphen-before-the-hour"
        ),
        pytest.param(
            "2012-01-02 08\u00a0\u00a0:00 650-253-0000",
            ["tel:+12012010208", "tel:+16502530000"],
            id="hour-after-non-ascii-space",
        ),
        pytest.param("01-02 08:00", [], id="too-few-digits-for-a-stamp"),
        pytest.param("12.34.56.7 650-253-0000", ["tel:+16502530000"], id="ipv4-short-last-octet"),
        pytest.param("1.2.3 650-253-0000", ["tel:+16502530000"], id="three-dotted-groups-are-not-ipv4"),
        pytest.param("+1.2.3.4", [], id="plus-before-dotted-groups"),
        pytest.param("Order#12345 650-253-0000", ["tel:+16502530000"], id="label-through-hash"),
        pytest.param("Ref\t650-253-0000", [], id="label-through-tab"),
        pytest.param("ref-650-253-0000", [], id="label-through-hyphen"),
        pytest.param("abcdefghijklmnop 650-253-0000", ["tel:+16502530000"], id="word-longer-than-a-label"),
        pytest.param("ref2 650-253-0000", ["tel:+16502530000"], id="label-followed-by-a-digit"),
    ],
)
def test_poison_shapes(text: str, expected: list[str]) -> None:
    assert _phone_paths_urls(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("4111 1111 1111 1111 110", [], id="four-by-four-plus-three"),
        pytest.param("3782 822463 10005", [], id="amex-shape"),
        pytest.param("3056 930902 5904", [], id="four-six-four"),
        pytest.param("4111111111111111", [], id="single-run"),
        pytest.param("4111 1111 1111 1112", ["tel:+494111", "tel:+49111111111112"], id="luhn-fails"),
        pytest.param("4111 1111 1111 11111", ["tel:+494111", "tel:+491111111111111"], id="not-a-card-shape"),
    ],
)
def test_card_shapes(text: str, expected: list[str]) -> None:
    assert _phone_paths_urls(text, ("DE",), valid=False) == expected


def test_a_single_group_past_the_card_length() -> None:
    assert _phone_paths_urls("12345678901234567890", ("DE",), valid=False) == []


def test_a_run_of_full_groups_stops_at_the_length_cap() -> None:
    assert _phone_paths_urls(f"{' '.join(['12345678901234567890'] * 14)} 650-253-0000") == ["tel:+16502530000"]


def test_a_group_over_twenty_digits_at_the_end() -> None:
    assert _phone_paths_urls(f"650-253-0000 {'1' * 21}") == ["tel:+16502530000"]


@pytest.mark.parametrize(
    ("text", "starts", "valid"),
    [
        pytest.param("(+1 650 253 0000)", [0], True, id="two-lead-groups"),
        pytest.param("(+(650) 253-0000", [2], True, id="three-lead-characters-keep-the-last-two"),
        pytest.param("(++650 253 0000", [], True, id="three-lead-characters-without-punctuation"),
        pytest.param("(--- 650-253-0000", [0], True, id="four-punctuation-after-the-lead"),
        pytest.param("(---- 650-253-0000", [6], True, id="five-punctuation-drop-the-lead"),
        pytest.param("+a650-253-0000", [2], False, id="letter-after-the-lead"),
    ],
)
def test_lead_groups(text: str, starts: list[int], valid: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # the parametrized flag selects the expected outcome
    assert [
        span.start for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=valid)).find(text)
    ] == starts


def test_closer_after_the_leading_part_splits_the_run() -> None:
    assert _phone_paths_urls("(650) 253)-0000", valid=False) == ["tel:+12530000"]


@pytest.mark.parametrize(
    ("country_code", "national_number", "region"),
    [
        pytest.param(1, "2685551234", "AG", id="routed-region-without-a-type"),
        pytest.param(1, "7218478877", "SX", id="routed-region-outside-its-general-pattern"),
    ],
)
def test_value_check_rejects_a_routed_region_whose_plan_rejects(
    country_code: int, national_number: str, region: str
) -> None:
    with pytest.raises(ValueError, match="no number"):
        PhoneNumber(country_code, national_number, None, region, PhoneType.FIXED_LINE)


def test_leading_zeros_are_capped_at_ten() -> None:
    assert [
        span.phone.national_number
        for span in LinkDetector(phones=PhoneNumbers(require_valid=False)).find(f"+62 {'0' * 12}12345")
        if span.phone
    ] == [f"{'0' * 10}12345"]


def test_all_zero_number_in_possible_mode() -> None:
    assert [
        span.phone.national_number
        for span in LinkDetector(phones=PhoneNumbers(regions=("US",), require_valid=False)).find("000-000-0000")
        if span.phone
    ] == ["0000000000"]


def test_general_only_number_in_possible_mode_reports_its_region() -> None:
    assert [
        (span.phone.region, span.phone.type)
        for span in LinkDetector(phones=PhoneNumbers(regions=("DE",), require_valid=False)).find("3788762")
        if span.phone
    ] == [("DE", PhoneType.UNKNOWN)]


def test_value_check_rejects_leading_zeros_past_the_cap() -> None:
    with pytest.raises(ValueError, match="no number"):
        PhoneNumber(62, f"{'0' * 12}12345", None, "ID", PhoneType.UNKNOWN)


def test_value_check_rejects_a_char_below_zero() -> None:
    with pytest.raises(ValueError, match="ASCII digits"):
        PhoneNumber(1, "650-530000", None, "US", PhoneType.FIXED_LINE_OR_MOBILE)


_PHONE_RUNS_US: Final[PhoneNumbers] = PhoneNumbers(regions=("US",))


_US_POSSIBLE: Final[PhoneNumbers] = PhoneNumbers(regions=("US",), require_valid=False)


@pytest.mark.parametrize(
    ("text", "phones", "expected"),
    [
        pytest.param(
            "030 1234 5678 9012 3456 7890 1234 5678 9012 3456", PhoneNumbers(regions=("DE",)), [], id="overlong-run"
        ),
        pytest.param(
            "+011 44 20 7031 3000",
            _PHONE_RUNS_US,
            [("+011 44 20 7031 3000", "tel:+442070313000")],
            id="plus-then-idd-retries",
        ),
        pytest.param("+999 20 7031 3000", _PHONE_RUNS_US, [], id="plus-with-no-code-and-no-idd"),
        pytest.param(
            "192.168.0.1 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="ipv4-keeps-its-last-octet",
        ),
        pytest.param(
            "1.2.3.4.5 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="five-dotted-groups-are-no-address",
        ),
        pytest.param("Order 650-253-0000", _US_POSSIBLE, [], id="label-covers-the-hyphenated-run"),
        pytest.param(
            "Order 12345, 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="label-stops-at-a-comma",
        ),
        pytest.param(
            "Order 12345 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="label-stops-at-a-space",
        ),
        pytest.param(
            "Order 12345\t650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="label-stops-at-a-tab",
        ),
        pytest.param(
            "\u00e9ref 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="label-glued-to-a-latin-letter",
        ),
        pytest.param("\uff34ref 650-253-0000", _PHONE_RUNS_US, [], id="label-after-a-letter-outside-the-latin-class"),
        pytest.param(
            "xref 650-253-0000", _PHONE_RUNS_US, [("650-253-0000", "tel:+16502530000")], id="label-inside-a-longer-word"
        ),
        pytest.param(
            "+49 200000000000000",
            _US_POSSIBLE,
            [("+49 200000000000000", "tel:200000000000000;phone-context=+49")],
            id="beyond-e164-gets-the-local-form",
        ),
        pytest.param(
            "+49 200000000000000 ext. 12",
            _US_POSSIBLE,
            [("+49 200000000000000 ext. 12", "tel:200000000000000;ext=12;phone-context=+49")],
            id="beyond-e164-extension-first",
        ),
        pytest.param(
            "tel:200000000000000;ext=12;phone-context=+49",
            _US_POSSIBLE,
            [("tel:200000000000000;ext=12;phone-context=+49", "tel:200000000000000;ext=12;phone-context=+49")],
            id="local-form-uri-links-as-itself",
        ),
        pytest.param(
            "4111111111111111 021 5550123",
            PhoneNumbers(regions=("ID",), require_valid=False),
            [("021 5550123", "tel:+62215550123")],
            id="card-run-then-a-phone",
        ),
        pytest.param(
            "4111 1111 1111 1111 021 5550123",
            PhoneNumbers(regions=("ID",), require_valid=False),
            [("5550123", "tel:+625550123")],
            id="spaced-card-then-a-phone-reads-as-the-matcher-does",
        ),
        pytest.param(
            "4111 1111 1111 1112 021 5550123",
            PhoneNumbers(regions=("ID",), require_valid=False),
            [("5550123", "tel:+625550123")],
            id="card-shape-failing-luhn-is-digits",
        ),
        pytest.param("2001:4860:4860::8888", PhoneNumbers(regions=("TA",)), [], id="ipv6-hextet"),
        pytest.param("2001:db8::1 or ::1", PhoneNumbers(regions=("TA",)), [], id="ipv6-short"),
        pytest.param(
            "[::1]:8080 then 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="ipv6-bracketed-with-port",
        ),
        pytest.param(
            "open at 12:30, call 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="one-colon-is-a-time",
        ),
        pytest.param("tel:not-a-number", _PHONE_RUNS_US, [], id="tel-uri-without-a-number"),
        pytest.param(
            "tel://evil.example call 650-253-0000",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="tel-authority-is-not-a-url",
        ),
        pytest.param(
            f"{'-'.join(['1' * 20] * 11 + ['1' * 18, '1' * 20])} call 650-253-0000 now",
            _PHONE_RUNS_US,
            [("650-253-0000", "tel:+16502530000")],
            id="run-holding-258-digits-then-a-phone",
        ),
        pytest.param(f"tel:1;phone-context=+{'1' * 500}", _PHONE_RUNS_US, [], id="tel-uri-with-an-overlong-context"),
        pytest.param(
            "tel:+1-650-253-0000;ext=12",
            _PHONE_RUNS_US,
            [("tel:+1-650-253-0000;ext=12", "tel:+16502530000;ext=12")],
            id="tel-uri-with-a-number",
        ),
        pytest.param(
            "TEL:650-253-0000", _PHONE_RUNS_US, [("TEL:650-253-0000", "tel:+16502530000")], id="tel-uri-any-case"
        ),
        pytest.param(
            "tel:+1-650-253-0000;isub=123",
            _PHONE_RUNS_US,
            [("tel:+1-650-253-0000;isub=123", "tel:+16502530000")],
            id="tel-uri-isub",
        ),
        pytest.param(
            "+44 20 7946 0958 (1234)",
            _PHONE_RUNS_US,
            [("+44 20 7946 0958", "tel:+442079460958")],
            id="bracketed-group-after-a-plus",
        ),
        pytest.param(
            "+1 650 253 0000 (1234",
            _PHONE_RUNS_US,
            [("+1 650 253 0000", "tel:+16502530000")],
            id="unclosed-bracket-after-a-plus",
        ),
        pytest.param(
            "(650 253 0000", _PHONE_RUNS_US, [("(650 253 0000", "tel:+16502530000")], id="unclosed-lead-bracket"
        ),
        pytest.param("+ +1 650 253 0000", _PHONE_RUNS_US, [], id="plus-after-a-gap"),
        pytest.param(
            "+ 1 650 253 0000", _PHONE_RUNS_US, [("+ 1 650 253 0000", "tel:+16502530000")], id="plus-then-a-space"
        ),
        pytest.param("++1 650 253 0000", _PHONE_RUNS_US, [("++1 650 253 0000", "tel:+16502530000")], id="doubled-plus"),
        pytest.param(
            "650 253 0000 x1234#",
            _PHONE_RUNS_US,
            [("650 253 0000 x1234", "tel:+16502530000;ext=1234")],
            id="hash-after-an-x-group",
        ),
        pytest.param(
            "650 253 0000 #1234#",
            _PHONE_RUNS_US,
            [("650 253 0000 #1234#", "tel:+16502530000;ext=1234")],
            id="hash-marked-extension",
        ),
        pytest.param(
            "0xx11 2345 6789",
            PhoneNumbers(regions=("BR",)),
            [("0xx11 2345 6789", "tel:+551123456789")],
            id="carrier-code",
        ),
        pytest.param(
            "0xx11 2345 6789 x12",
            PhoneNumbers(regions=("BR",)),
            [("0xx11 2345 6789 x12", "tel:+551123456789;ext=12")],
            id="carrier-code-then-extension",
        ),
        pytest.param(
            "6502530000:6502530000:1",
            _PHONE_RUNS_US,
            [("6502530000", "tel:+16502530000"), ("6502530000", "tel:+16502530000")],
            id="colon-chain-that-is-no-address",
        ),
        pytest.param(
            "2001:db8::6502530000", _PHONE_RUNS_US, [("6502530000", "tel:+16502530000")], id="hextet-too-long"
        ),
        pytest.param(
            "[2001:db8::1]:6502530000", _PHONE_RUNS_US, [("6502530000", "tel:+16502530000")], id="port-too-long"
        ),
        pytest.param(
            "2001:db8:1:2:3:4:5:6:8888", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="nine-hextets"
        ),
        pytest.param("2001:db8::1::8888", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="two-gaps"),
        pytest.param(":8888:1", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="lone-leading-colon"),
        pytest.param("[::8888", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="bracket-never-closed"),
        pytest.param("1:2:3:4:5:6:7:8888", PhoneNumbers(regions=("TA",)), [], id="eight-hextets"),
        pytest.param("2001:db8:::8888", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="triple-colon"),
        pytest.param(
            "2001:db8::8888:", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="lone-trailing-colon"
        ),
        pytest.param("[::8888]:", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="port-without-digits"),
        pytest.param("[::8888]", PhoneNumbers(regions=("TA",)), [], id="bracketed-without-a-port"),
        pytest.param("[::8888]x", PhoneNumbers(regions=("TA",)), [], id="bracketed-address-then-a-letter"),
        pytest.param("[::8888[", PhoneNumbers(regions=("TA",)), [("8888", "tel:+2908888")], id="bracket-reopened"),
        pytest.param(
            "[::1]a8888",
            PhoneNumbers(regions=("TA",), require_valid=False),
            [("8888", "tel:+2908888")],
            id="hex-letter-after-the-bracket",
        ),
        pytest.param(
            "[::1]:80a8888",
            PhoneNumbers(regions=("TA",), require_valid=False),
            [("8888", "tel:+2908888")],
            id="hex-letter-after-the-port",
        ),
        pytest.param(
            "1:2:3:4:5:6:7::8888",
            PhoneNumbers(regions=("TA",)),
            [("8888", "tel:+2908888")],
            id="gap-with-eight-hextets",
        ),
        pytest.param("()650 253 0000", _PHONE_RUNS_US, [], id="empty-brackets-before-the-digits"),
        pytest.param(
            "1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3", _PHONE_RUNS_US, [], id="run-longer-than-any-address"
        ),
    ],
)
def test_runs_around_a_number(text: str, phones: PhoneNumbers, expected: list[tuple[str, str]]) -> None:
    assert [(span.text, span.url) for span in LinkDetector(phones=phones).find(text)] == expected


def test_rewrite_leaves_a_tel_uri_without_a_number_alone() -> None:
    assert linkify("see tel:not-a-number or tel:650-253-0000", Linkify(phones=_PHONE_RUNS_US)) == (
        'see tel:not-a-number or <a href="tel:+16502530000">tel:650-253-0000</a>'
    )


@pytest.mark.parametrize(
    ("text", "count"),
    [
        pytest.param("abcdef0123456789" * 2000, 0, id="hex-chain"),
        pytest.param("6502530000:" * 2000, 2000, id="colon-chain"),
    ],
)
def test_long_address_chains_scan_in_bounded_time(text: str, count: int) -> None:
    assert len(LinkDetector(phones=_PHONE_RUNS_US).find(text)) == count


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("+49 200000000000000 ext. 12", id="beyond-e164"),
        pytest.param("+44 20 7946 0958 x12", id="global"),
    ],
)
def test_href_round_trips_through_parse(text: str) -> None:
    span = LinkDetector(phones=_US_POSSIBLE).find(text)[0]
    assert span.phone is not None
    parsed = PhoneNumber.parse(span.url, require_valid=False)
    assert (parsed.country_code, parsed.national_number, parsed.extension) == (
        span.phone.country_code,
        span.phone.national_number,
        span.phone.extension,
    )


def test_registered_tel_scheme_links_the_authority_form() -> None:
    assert [span.url for span in LinkDetector(schemes=("tel",), phones=_PHONE_RUNS_US).find("tel://evil.example")] == [
        "tel://evil.example"
    ]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            PhoneNumbers(regions=("US", "GB"), grouping=PhoneGrouping.STRICT, types=(PhoneType.MOBILE,)), id="settings"
        ),
        pytest.param(Linkify(phones=_PHONE_RUNS_US), id="linkify"),
    ],
)
def test_settings_pickle_and_copy(value: PhoneNumbers | Linkify) -> None:
    assert pickle.loads(pickle.dumps(value)) == value  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
    assert copy.deepcopy(value) == value


def test_detector_pickles_with_its_settings() -> None:
    assert [
        span.url
        for span in pickle.loads(  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
            pickle.dumps(LinkDetector(phones=_PHONE_RUNS_US, schemes=("bitcoin",), tlds=("test",)))
        ).find("call 650-253-0000 or bitcoin:1abc at host.test")
    ] == ["tel:+16502530000", "bitcoin:1abc", "http://host.test"]


def test_other_schemes_keep_their_payloads() -> None:
    assert [
        (span.text, span.url)
        for span in LinkDetector(schemes=("bitcoin", "sms"), phones=_PHONE_RUNS_US).find(
            "bitcoin:1abc sms:123 tel:junk"
        )
    ] == [("bitcoin:1abc", "bitcoin:1abc"), ("sms:123", "sms:123")]


_PHONE_UNICODE_US: Final[PhoneNumbers] = PhoneNumbers(regions=("US",))


def _in_script(zero: int, text: str = "650-253-0000") -> str:
    return "".join(chr(zero + int(char)) if char.isdigit() else char for char in text)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(_in_script(0x0660), id="arabic-indic"),
        pytest.param(_in_script(0x0966), id="devanagari"),
        pytest.param(_in_script(0xFF10), id="fullwidth"),
        pytest.param(_in_script(0x1D7CE), id="mathematical-bold-astral"),
        pytest.param(f"650-{_in_script(1632, '253')}-{_in_script(65296, '0000')}", id="mixed-scripts"),
        pytest.param(_in_script(0x11F50), id="kawi-added-in-unicode-15"),
    ],
)
def test_digits_of_every_script(text: str) -> None:
    assert _phone_unicode_urls(text) == ["tel:+16502530000"]


def _phone_unicode_urls(text: str, phones: PhoneNumbers = _PHONE_UNICODE_US) -> list[str]:
    return [span.url for span in LinkDetector(phones=phones).find(text)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("650-253-0000 ext \u0661\u0662", id="arabic-indic-extension"),
        pytest.param("650-253-0000 x\uff11\uff12", id="fullwidth-extension"),
    ],
)
def test_extension_digits_of_other_scripts(text: str) -> None:
    assert _phone_unicode_urls(text) == ["tel:+16502530000;ext=12"]


@pytest.mark.parametrize(
    ("text", "valid", "possible"),
    [
        pytest.param("a650-253-0000", [], ["tel:+16502530000"], id="latin-letter-before"),
        pytest.param("650-253-0000a", [], ["tel:+16502530000"], id="latin-letter-after"),
        pytest.param("\u00c9650-253-0000", [], ["tel:+16502530000"], id="accented-letter-before"),
        pytest.param("650-253-0000\u0301", [], ["tel:+16502530000"], id="combining-accent-after"),
        pytest.param("$650-253-0000", [], ["tel:+16502530000"], id="dollar-before"),
        pytest.param("650-253-0000\u00a3", [], ["tel:+16502530000"], id="pound-after"),
        pytest.param("650-253-0000%", [], ["tel:+16502530000"], id="percent-after"),
        pytest.param("\u00a5650-253-0000", [], ["tel:+16502530000"], id="yen-before"),
        pytest.param("a+1 650-253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="lead-plus-after-letter"),
        pytest.param("a(650) 253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="lead-bracket-after-letter"),
        pytest.param(
            "\u6211\u7684\u7535\u8bdd650-253-0000\u3002",
            ["tel:+16502530000"],
            ["tel:+16502530000"],
            id="chinese-context",
        ),
        pytest.param("\u03b1650-253-0000", ["tel:+16502530000"], ["tel:+16502530000"], id="greek-letter-before"),
    ],
)
def test_neighboring_letters_and_currency(text: str, valid: list[str], possible: list[str]) -> None:
    assert _phone_unicode_urls(text) == valid
    assert _phone_unicode_urls(text, PhoneNumbers(regions=("US",), require_valid=False)) == possible


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("650\u00a0253\u00a00000", id="nbsp"),
        pytest.param("650\u3000253\u30000000", id="ideographic-space"),
        pytest.param("650\u2010253\u20100000", id="hyphen-u2010"),
        pytest.param("650\u2212253\u22120000", id="minus-sign"),
        pytest.param("650\uff0d253\uff0d0000", id="fullwidth-hyphen"),
        pytest.param("650\uff0e253\uff0e0000", id="fullwidth-full-stop"),
        pytest.param("\uff08650\uff09 253-0000", id="fullwidth-brackets"),
        pytest.param("650\u30fc253\u30fc0000", id="katakana-prolonged-sound-mark"),
        pytest.param("650\u200b253\u200b0000", id="zero-width-space"),
    ],
)
def test_separators_of_every_kind(text: str) -> None:
    assert _phone_unicode_urls(text) == ["tel:+16502530000"]


def test_offsets_count_code_points_not_bytes() -> None:
    span = LinkDetector(phones=_PHONE_UNICODE_US).find("\U0001f600\U0001f600 650-253-0000")[0]
    assert (span.start, span.end) == (3, 15)


_CH: Final[PhoneNumbers] = PhoneNumbers(regions=("CH",))


_CH_COLLAPSED: Final[PhoneNumbers] = PhoneNumbers(regions=("CH",), collapse_whitespace=True)


_PHONE_WHITESPACE_US: Final[PhoneNumbers] = PhoneNumbers(regions=("US",))


_US_COLLAPSED: Final[PhoneNumbers] = PhoneNumbers(regions=("US",), collapse_whitespace=True)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("+41     79434     3254", id="five-spaces"),
        pytest.param("+41\n79434\n3254", id="newline"),
        pytest.param("+41\t79434\t3254", id="tab"),
        pytest.param("+41\r\n79434\r\n3254", id="carriage-return"),
        pytest.param("+41\f79434\f3254", id="form-feed"),
        pytest.param("+41 \n 79434 \n 3254", id="mixed-run"),
        pytest.param("079\n434\n32\n54", id="national"),
    ],
)
def test_collapse_whitespace_reads_a_run_as_one_space(text: str) -> None:
    assert _found(_CH_COLLAPSED, text) == ["tel:+41794343254"]


def _found(phones: PhoneNumbers, text: str) -> list[str]:
    return [span.url for span in LinkDetector(phones=phones).find(text)]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("+41     79434     3254", id="five-spaces"),
        pytest.param("+41\n79434\n3254", id="newline"),
        pytest.param("+41\t79434\t3254", id="tab"),
        pytest.param("079\n434\n32\n54", id="national"),
    ],
)
def test_default_stops_at_the_separator_limit(text: str) -> None:
    assert _found(_CH, text) == []


@pytest.mark.parametrize("separator", [pytest.param("\u00a0", id="nbsp"), pytest.param("\u3000", id="ideographic")])
def test_collapse_whitespace_leaves_the_spaces_html_keeps(separator: str) -> None:
    assert _found(_CH_COLLAPSED, f"+41{separator * 5}79434{separator * 5}3254") == []
    assert _found(_CH_COLLAPSED, f"+41{separator * 4}79434{separator * 4}3254") == ["tel:+41794343254"]


def test_collapse_whitespace_leaves_a_vertical_tab() -> None:
    assert _found(_CH_COLLAPSED, "+41\v79434 3254") == []


def test_collapse_whitespace_reaches_a_lead_over_a_run() -> None:
    text = f"+{' ' * 300}41 79434 3254"
    assert [(span.text, span.url) for span in LinkDetector(phones=_CH_COLLAPSED).find(text)] == [
        (text, "tel:+41794343254")
    ]


def test_collapse_whitespace_measures_a_run_as_it_renders() -> None:
    text = f"+41{' ' * 300}79434 3254"
    assert [(span.text, span.url) for span in LinkDetector(phones=_CH_COLLAPSED).find(text)] == [
        (text, "tel:+41794343254")
    ]
    assert _found(_CH, text) == []


def test_grouping_rejects_oversized_candidate() -> None:
    text = f"\uff10 \u0666\u0665   \u0662\u0665  \u0662\u0665\u0663{' ' * 389}x   \u0661\u0662"
    phones = PhoneNumbers(
        regions=("GB", "DE"),
        require_valid=True,
        require_separators=True,
        grouping=PhoneGrouping.STRICT,
        ignore_numbers_after=(),
    )
    assert LinkDetector(phones=phones).find(text) == []


def test_collapse_whitespace_reads_a_broken_extension() -> None:
    assert _found(_US_COLLAPSED, "650-253-0000\next. 1234") == ["tel:+16502530000;ext=1234"]
    assert _found(_PHONE_WHITESPACE_US, "650-253-0000\next. 1234") == ["tel:+16502530000"]


def test_collapse_whitespace_reaches_an_identifier_label_over_a_run() -> None:
    assert _found(_US_COLLAPSED, "Order\f650-253-0000") == []
    assert _found(_PHONE_WHITESPACE_US, "Order\f650-253-0000") == ["tel:+16502530000"]


def test_collapse_whitespace_keeps_a_timestamp_out() -> None:
    assert _found(_US_COLLAPSED, "20250901\n10:30") == []
    assert _found(_US_COLLAPSED, "20250901\n10") == ["tel:+12025090110"]


def test_collapse_whitespace_groups_across_a_run() -> None:
    strict = PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT, collapse_whitespace=True)
    assert _found(strict, "650\n253 0000") == ["tel:+16502530000"]
    assert _found(PhoneNumbers(regions=("US",), grouping=PhoneGrouping.EXACT), "650\n253 0000") == []


def test_collapse_whitespace_links_a_number_a_formatter_wrapped() -> None:
    assert linkify("<p>Call\n+41 79\n434 32 54\ntoday</p>", Linkify(phones=_CH_COLLAPSED)) == (
        '<p>Call\n<a href="tel:+41794343254">+41 79\n434 32 54</a>\ntoday</p>'
    )


def test_collapse_whitespace_survives_a_pickle_and_a_copy() -> None:
    restored = pickle.loads(pickle.dumps(_CH_COLLAPSED))  # ruff:ignore[suspicious-pickle-usage]  # this test's bytes
    assert restored == _CH_COLLAPSED
    assert copy.deepcopy(_CH_COLLAPSED) == _CH_COLLAPSED


# numbers, dates, an address, an extension and prose, the pieces a run of whitespace can end up gluing together
_PIECES: Final[tuple[str, ...]] = (
    "+41", "79434", "3254", "079", "434", "32", "54", "650", "253", "0000", "(650)", "-", ".", "/", "x12", "Order",
    "Tel:", "abc", "+1", "20250901", "10:30", "4111", "1111", "pages", "1-5", "(3", "pages)", "2001:db8::8888", "020",
    "7946", "0958", "#", ",", "]", "ext.", "~", "[", "\u00a0", "\u3000",
)  # fmt: skip


_RUNS: Final[re.Pattern[str]] = re.compile(r"[ \t\n\f\r]+")


def test_collapse_whitespace_matches_the_text_as_it_renders() -> None:
    # The rule the knob promises: a run reads as the one space HTML paints, whatever it holds.
    regions = ("CH", "US", "GB")
    collapsed = LinkDetector(phones=PhoneNumbers(regions=regions, collapse_whitespace=True))
    rendered = LinkDetector(phones=PhoneNumbers(regions=regions))
    for text in _corpus():
        assert [(span.url, _RUNS.sub(" ", span.text)) for span in collapsed.find(text)] == [
            (span.url, span.text) for span in rendered.find(_RUNS.sub(" ", text))
        ], text


def _corpus() -> list[str]:
    # Glue the pieces with every run of whitespace the knob has to read as one space.
    rng = random.Random(20260901)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a fixed corpus
    separators = (" ", "  ", "     ", "\n", "\t", "\r\n", " \n ", "\f", "")
    return [
        "".join(rng.choice(_PIECES) + rng.choice(separators) for _ in range(rng.randint(2, 7))) for _ in range(20_000)
    ]


def test_one_linker_and_one_detector_shared_across_threads() -> None:
    phones: Final = PhoneNumbers(regions=("US", "GB"))
    linker: Final = Linker(Linkify(phones=phones, parse_email=True))
    detector: Final = LinkDetector(phones=phones)
    barrier: Final = Barrier(_WORKERS)
    text: Final = "mail a@b.com, call 650-253-0000 or +44 20 7946 0958 x12, see example.com"

    def work(_index: int) -> tuple[str, list[str]]:
        barrier.wait()
        return linker.linkify(text), [span.url for span in detector.find(text)]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        assert (
            list(pool.map(work, range(_WORKERS)))
            == [
                (
                    (
                        'mail <a href="mailto:a@b.com">a@b.com</a>, call '
                        '<a href="tel:+16502530000">650-253-0000</a> or '
                        '<a href="tel:+442079460958;ext=12">+44 20 7946 0958 x12</a>, '
                        'see <a href="http://example.com" rel="nofollow">example.com</a>'
                    ),
                    ["mailto:a@b.com", "tel:+16502530000", "tel:+442079460958;ext=12", "http://example.com"],
                )
            ]
            * _WORKERS
        )


def test_detectors_with_different_policies_keep_their_own_answers() -> None:
    phones: Final = PhoneNumbers(regions=("US",))
    domains: Final = LinkDetector(phones=phones, tlds=["corp"])
    numbers: Final = LinkDetector(phones=phones, bare_domains=False, emails=False)
    barrier: Final = Barrier(_WORKERS)
    text: Final = "6502530000.corp 6502530000@example.com"

    def work(index: int) -> list[str]:
        barrier.wait()
        return [span.url for span in (domains if index % 2 == 0 else numbers).find(text)]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        assert list(pool.map(work, range(_WORKERS))) == [
            ["http://6502530000.corp", "mailto:6502530000@example.com"],
            ["tel:+16502530000"],
        ] * (_WORKERS // 2)
