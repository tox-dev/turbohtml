from __future__ import annotations

from typing import Final

import pytest

from turbohtml.clean import LinkDetector, PhoneNumbers

_US: Final = PhoneNumbers(regions=("US",))


def _find(text: str, phones: PhoneNumbers = _US) -> list[tuple[str, str | None]]:
    return [
        (span.text, span.phone.extension if span.phone else None) for span in LinkDetector(phones=phones).find(text)
    ]


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
    text = "650-253-0000" + tail
    assert _find(text) == [(text, "123")]


@pytest.mark.parametrize(
    ("tail", "extension"),
    [
        pytest.param(";ext=" + "1" * 20, "1" * 20, id="rfc-twenty"),
        pytest.param(" ext " + "1" * 20, "1" * 20, id="explicit-twenty"),
        pytest.param(" x" + "1" * 9, "1" * 9, id="ambiguous-nine"),
    ],
)
def test_extension_digit_caps(tail: str, extension: str) -> None:
    text = "650-253-0000" + tail
    assert _find(text) == [(text, extension)]


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param(";ext=" + "1" * 21, [("650-253-0000;ext=" + "1" * 20, "1" * 20)], id="rfc-keeps-twenty"),
        pytest.param(" ext " + "1" * 21, [("650-253-0000 ext " + "1" * 20, "1" * 20)], id="explicit-keeps-twenty"),
        pytest.param(" - 123#", [("650-253-0000", None)], id="american-hash-outside-the-candidate"),
        pytest.param("- 123#", [("650-253-0000", None)], id="american-without-a-space"),
        pytest.param(" - " + "1" * 7 + "#", [("650-253-0000", None)], id="american-over"),
        pytest.param(" 123#", [("650-253-0000", None)], id="hash-without-separator"),
    ],
)
def test_extension_shapes_the_matcher_cuts(tail: str, expected: list[tuple[str, str | None]]) -> None:
    assert _find("650-253-0000" + tail) == expected


def test_ambiguous_form_over_nine_digits_reads_as_a_second_number() -> None:
    assert _find("650-253-0000 x" + "1" * 10) == [("650-253-0000", None)]


def test_match_end_covers_the_extension() -> None:
    span = LinkDetector(phones=_US).find("ring 650-253-0000 ext 12 now")[0]
    assert (span.start, span.end, span.text, span.url) == (5, 24, "650-253-0000 ext 12", "tel:+16502530000;ext=12")


def test_marker_group_in_the_middle() -> None:
    assert _find("650 x 253 0000") == []
    assert _find("650 x 253 0000", PhoneNumbers(regions=("US",), require_valid=False)) == [("650 x 253 0000", None)]


def test_carrier_code_marker_needs_the_number_after_it() -> None:
    assert _find("xx 650-253-0000") == [("650-253-0000", None)]
    assert _find("650 xx 253-0000") == []


def test_extension_digits_are_folded_in_the_href() -> None:
    assert LinkDetector(phones=_US).find("650-253-0000 ext \uff11\uff12")[0].url == "tel:+16502530000;ext=12"
