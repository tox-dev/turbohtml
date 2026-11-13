from __future__ import annotations

from typing import Final

import pytest

from turbohtml.extract import normalize_url


def test_normalize_url_punycodes_a_unicode_host() -> None:
    # The public normalizer routes a Unicode host through the engine and falls back on an unencodable one.
    assert normalize_url("http://münchen.de/weg") == "http://xn--mnchen-3ya.de/weg"
    assert normalize_url("http://a\ud800b.de/") == "http://a\ud800b.de/"


_CASES: Final[list[pytest.ParameterSet]] = [
    pytest.param("münchen.example", "xn--mnchen-3ya.example", id="normalized"),
    pytest.param("a\u0301.example", "xn--1ca.example", id="decomposed"),
    pytest.param("q\u0301\u0323.example", "xn--q-xbb5h.example", id="reordered"),
    pytest.param("a\u0305\u0301.example", "xn--a-xbbl.example", id="blocked"),
    pytest.param("\u1100\u1161.example", "xn--o39a.example", id="hangul-lv"),
    pytest.param("\u1100\u1161\u11a8.example", "xn--p39a.example", id="hangul-lvt"),
    pytest.param("faß.example", "xn--fa-hia.example", id="nontransitional"),
    pytest.param("a\u03a31.example", "xn--a1-vbc.example", id="sigma-mapped-with-context"),
    pytest.param("a\u03c31.example", "xn--a1-vbc.example", id="ordinary-sigma"),
    pytest.param("a\u03c21.example", "xn--a1-sbc.example", id="final-sigma-distinct"),
    pytest.param("\uff26\uff2f\uff2f.日本", "foo.xn--wgv71a", id="mapped-label"),
    pytest.param("a\u00adb.日本", "ab.xn--wgv71a", id="ignored-character"),
    pytest.param("münchen。example", "xn--mnchen-3ya.example", id="mapped-separator"),
    pytest.param("münchen..example", "xn--mnchen-3ya..example", id="empty-interior-label"),
    pytest.param("münchen.example.", "xn--mnchen-3ya.example.", id="trailing-label"),
    pytest.param("xn--ascii-.日本", "xn--ascii-.xn--wgv71a", id="retained-ace"),
    pytest.param("xn--.日本", ".xn--wgv71a", id="empty-ace"),
    pytest.param("A\ufffdB.example", "a\ufffdb.example", id="disallowed-fallback"),
]


@pytest.mark.parametrize(("host", "expected"), _CASES)
def test_idna_shared_public_target(host: str, expected: str) -> None:
    assert normalize_url(f"https://{host}/item?q=1") == f"https://{expected}/item?q=1"


# A label whose punycode delta would overflow the 32-bit accumulator: a long run of one code point, then a distant one.
# The first case trips the pre-multiply guard, the second lands the accumulator just under the limit so the following
# increments trip the per-code-point guard. Both stay under the 16384-code-point input cap and use CJK code points UTS
# #46 keeps, so they reach the encoder rather than the disallowed-code-point or input-cap rejection.
_OVERFLOW_LABELS: Final[list[str]] = ["\u3400" * 16000 + "\U000323af", "\u3400" * 11159 + "\U000323ab"]


@pytest.mark.parametrize(
    "label",
    [
        pytest.param(_OVERFLOW_LABELS[0], id="pre-multiply-guard"),
        pytest.param(_OVERFLOW_LABELS[1], id="increment-guard"),
    ],
)
def test_punycode_delta_overflow_is_rejected(label: str) -> None:
    # A label whose encoding would overflow the punycode accumulator keeps its Unicode form, never a wrong host.
    url: Final = f"http://{label}.example/"
    assert normalize_url(url) == url


def test_host_at_the_input_cap_still_encodes() -> None:
    assert normalize_url("http://" + "\u3400" * 16384 + "/").startswith("http://xn--")


def test_oversize_host_keeps_its_unicode_form() -> None:
    # past the 16384-code-point cap the host skips the O(n^2) reorder and punycode passes and keeps its Unicode form;
    # U+0316 then U+0301 alternate combining classes 220 and 230, the input an insertion-sort reorder is O(n^2) on
    url: Final = "http://" + "\u0316\u0301" * 60000 + "/"
    assert normalize_url(url) == url


@pytest.mark.parametrize(
    "code_point",
    [
        pytest.param(0x0085, id="c1-control-NEL"),
        pytest.param(0x2028, id="line-separator"),
        pytest.param(0x202E, id="right-to-left-override"),
        pytest.param(0xE000, id="private-use"),
        pytest.param(0xFFFE, id="noncharacter"),
    ],
)
def test_disallowed_code_point_host_is_not_punycode_encoded(code_point: int) -> None:
    # UTS #46 validity criterion 6 rejects a disallowed code point, so the host keeps its Unicode form.
    url: Final = f"http://a{chr(code_point)}b.example/"
    assert normalize_url(url) == url
