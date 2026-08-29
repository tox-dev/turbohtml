from __future__ import annotations

import copy
import pickle  # ruff:ignore[suspicious-pickle-import]  # this test pickles its own values
from typing import Final

import pytest

from turbohtml.clean import LinkDetector, Linkify, PhoneGrouping, PhoneNumber, PhoneNumbers, PhoneType, linkify

_US: Final = PhoneNumbers(regions=("US",))
_US_POSSIBLE: Final = PhoneNumbers(regions=("US",), require_valid=False)


@pytest.mark.parametrize(
    ("text", "phones", "expected"),
    [
        pytest.param(
            "030 1234 5678 9012 3456 7890 1234 5678 9012 3456", PhoneNumbers(regions=("DE",)), [], id="overlong-run"
        ),
        pytest.param(
            "+011 44 20 7031 3000", _US, [("+011 44 20 7031 3000", "tel:+442070313000")], id="plus-then-idd-retries"
        ),
        pytest.param("+999 20 7031 3000", _US, [], id="plus-with-no-code-and-no-idd"),
        pytest.param(
            "192.168.0.1 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="ipv4-keeps-its-last-octet"
        ),
        pytest.param(
            "1.2.3.4.5 650-253-0000",
            _US,
            [("650-253-0000", "tel:+16502530000")],
            id="five-dotted-groups-are-no-address",
        ),
        pytest.param("Order 650-253-0000", _US_POSSIBLE, [], id="label-covers-the-hyphenated-run"),
        pytest.param(
            "Order 12345, 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="label-stops-at-a-comma"
        ),
        pytest.param(
            "Order 12345 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="label-stops-at-a-space"
        ),
        pytest.param(
            "Order 12345\t650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="label-stops-at-a-tab"
        ),
        pytest.param(
            "\u00e9ref 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="label-glued-to-a-latin-letter"
        ),
        pytest.param("\uff34ref 650-253-0000", _US, [], id="label-after-a-letter-outside-the-latin-class"),
        pytest.param("xref 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="label-inside-a-longer-word"),
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
            "[::1]:8080 then 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="ipv6-bracketed-with-port"
        ),
        pytest.param(
            "open at 12:30, call 650-253-0000", _US, [("650-253-0000", "tel:+16502530000")], id="one-colon-is-a-time"
        ),
        pytest.param("tel:not-a-number", _US, [], id="tel-uri-without-a-number"),
        pytest.param(
            "tel://evil.example call 650-253-0000",
            _US,
            [("650-253-0000", "tel:+16502530000")],
            id="tel-authority-is-not-a-url",
        ),
        pytest.param(
            "-".join(["1" * 20] * 11 + ["1" * 18, "1" * 20]) + " call 650-253-0000 now",
            _US,
            [("650-253-0000", "tel:+16502530000")],
            id="run-holding-258-digits-then-a-phone",
        ),
        pytest.param("tel:1;phone-context=+" + "1" * 500, _US, [], id="tel-uri-with-an-overlong-context"),
        pytest.param(
            "tel:+1-650-253-0000;ext=12",
            _US,
            [("tel:+1-650-253-0000;ext=12", "tel:+16502530000;ext=12")],
            id="tel-uri-with-a-number",
        ),
        pytest.param("TEL:650-253-0000", _US, [("TEL:650-253-0000", "tel:+16502530000")], id="tel-uri-any-case"),
        pytest.param(
            "tel:+1-650-253-0000;isub=123",
            _US,
            [("tel:+1-650-253-0000;isub=123", "tel:+16502530000")],
            id="tel-uri-isub",
        ),
        pytest.param(
            "+44 20 7946 0958 (1234)",
            _US,
            [("+44 20 7946 0958", "tel:+442079460958")],
            id="bracketed-group-after-a-plus",
        ),
        pytest.param(
            "+1 650 253 0000 (1234", _US, [("+1 650 253 0000", "tel:+16502530000")], id="unclosed-bracket-after-a-plus"
        ),
        pytest.param("(650 253 0000", _US, [("(650 253 0000", "tel:+16502530000")], id="unclosed-lead-bracket"),
        pytest.param("+ +1 650 253 0000", _US, [], id="plus-after-a-gap"),
        pytest.param("+ 1 650 253 0000", _US, [("+ 1 650 253 0000", "tel:+16502530000")], id="plus-then-a-space"),
        pytest.param("++1 650 253 0000", _US, [("++1 650 253 0000", "tel:+16502530000")], id="doubled-plus"),
        pytest.param(
            "650 253 0000 x1234#",
            _US,
            [("650 253 0000 x1234", "tel:+16502530000;ext=1234")],
            id="hash-after-an-x-group",
        ),
        pytest.param(
            "650 253 0000 #1234#",
            _US,
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
            _US,
            [("6502530000", "tel:+16502530000"), ("6502530000", "tel:+16502530000")],
            id="colon-chain-that-is-no-address",
        ),
        pytest.param("2001:db8::6502530000", _US, [("6502530000", "tel:+16502530000")], id="hextet-too-long"),
        pytest.param("[2001:db8::1]:6502530000", _US, [("6502530000", "tel:+16502530000")], id="port-too-long"),
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
        pytest.param("()650 253 0000", _US, [], id="empty-brackets-before-the-digits"),
        pytest.param("1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3", _US, [], id="run-longer-than-any-address"),
    ],
)
def test_runs_around_a_number(text: str, phones: PhoneNumbers, expected: list[tuple[str, str]]) -> None:
    assert [(span.text, span.url) for span in LinkDetector(phones=phones).find(text)] == expected


def test_rewrite_leaves_a_tel_uri_without_a_number_alone() -> None:
    assert linkify("see tel:not-a-number or tel:650-253-0000", Linkify(phones=_US)) == (
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
    assert len(LinkDetector(phones=_US).find(text)) == count


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
    assert [span.url for span in LinkDetector(schemes=("tel",), phones=_US).find("tel://evil.example")] == [
        "tel://evil.example"
    ]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(
            PhoneNumbers(regions=("US", "GB"), grouping=PhoneGrouping.STRICT, types=(PhoneType.MOBILE,)), id="settings"
        ),
        pytest.param(Linkify(phones=_US), id="linkify"),
    ],
)
def test_settings_pickle_and_copy(value: object) -> None:
    assert pickle.loads(pickle.dumps(value)) == value  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
    assert copy.deepcopy(value) == value


def test_detector_pickles_with_its_settings() -> None:
    assert [
        span.url
        for span in pickle.loads(  # ruff:ignore[suspicious-pickle-usage]  # this test's own bytes
            pickle.dumps(LinkDetector(phones=_US, schemes=("bitcoin",), tlds=("test",)))
        ).find("call 650-253-0000 or bitcoin:1abc at host.test")
    ] == ["tel:+16502530000", "bitcoin:1abc", "http://host.test"]


def test_other_schemes_keep_their_payloads() -> None:
    assert [
        (span.text, span.url)
        for span in LinkDetector(schemes=("bitcoin", "sms"), phones=_US).find("bitcoin:1abc sms:123 tel:junk")
    ] == [("bitcoin:1abc", "bitcoin:1abc"), ("sms:123", "sms:123")]
