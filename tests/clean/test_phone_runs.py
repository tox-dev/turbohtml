from __future__ import annotations

import pytest

from turbohtml.clean import LinkDetector, Linkify, PhoneNumbers, linkify

_US = PhoneNumbers(regions=("US",))
_US_POSSIBLE = PhoneNumbers(regions=("US",), require_valid=False)


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
        pytest.param("tel:not-a-number", _US, [], id="tel-uri-without-a-number"),
        pytest.param(
            "tel:+1-650-253-0000;ext=12",
            _US,
            [("tel:+1-650-253-0000;ext=12", "tel:+1-650-253-0000;ext=12")],
            id="tel-uri-with-a-number",
        ),
        pytest.param("TEL:650-253-0000", _US, [("TEL:650-253-0000", "TEL:650-253-0000")], id="tel-uri-any-case"),
    ],
)
def test_runs_around_a_number(text: str, phones: PhoneNumbers, expected: list[tuple[str, str]]) -> None:
    assert [(span.text, span.url) for span in LinkDetector(phones=phones).find(text)] == expected


def test_rewrite_leaves_a_tel_uri_without_a_number_alone() -> None:
    assert linkify("see tel:not-a-number or tel:650-253-0000", Linkify(phones=_US)) == (
        'see tel:not-a-number or <a href="tel:650-253-0000">tel:650-253-0000</a>'
    )


def test_other_schemes_keep_their_payloads() -> None:
    detector = LinkDetector(schemes=("bitcoin", "sms"), phones=_US)
    assert [(span.text, span.url) for span in detector.find("bitcoin:1abc sms:123 tel:junk")] == [
        ("bitcoin:1abc", "bitcoin:1abc"),
        ("sms:123", "sms:123"),
    ]
