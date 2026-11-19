from __future__ import annotations

import unicodedata
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml.extract import normalize_url

if TYPE_CHECKING:
    from work_steps import WorkSteps


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
    pytest.param("xn--ascii-.日本", "xn--ascii-.日本", id="invalid-ace-keeps-host"),
    pytest.param("xn--.日本", "xn--.日本", id="empty-ace-keeps-host"),
    pytest.param("A\ufffdB.example", "a\ufffdb.example", id="disallowed-fallback"),
    pytest.param("\u1e9b\u0323.example", "xn--1ig.example", id="nfc-after-mapping"),
]


@pytest.mark.parametrize(("host", "expected"), _CASES)
def test_idna_shared_public_target(host: str, expected: str) -> None:
    assert normalize_url(f"https://{host}/item?q=1") == f"https://{expected}/item?q=1"


# Hosts UTS #46 validity rejects: an RFC 5893 bidi failure, a ContextJ joiner failure, a leading mark, or a bad xn--
# label in a non-ASCII host. WHATWG fails the host, so the URL is kept as written.
@pytest.mark.parametrize(
    "host",
    [
        pytest.param("\u0661.com", id="arabic-indic-digit-first"),
        pytest.param("a\u05dfb.com", id="latin-then-hebrew"),
        pytest.param("\u0661\u05d0.com", id="digit-then-hebrew"),
        pytest.param("\u0660\u0661.com", id="arabic-digits-only-and-leading"),
        pytest.param("a1\u0661.com", id="latin-digit-and-arabic-digit"),
        pytest.param("\u0300.com", id="leading-combining-mark"),
        pytest.param("xn--\u00e4.com", id="non-ascii-after-ace-prefix"),
        pytest.param("xn--zz.\u00fc.com", id="undecodable-ace-beside-unicode"),
        pytest.param("\u200c.com", id="zwnj-alone"),
        pytest.param("a\u200cb.com", id="zwnj-between-latin"),
        pytest.param("\u0915\u200db.com", id="zwj-without-virama"),
        pytest.param("\u0628\u200cb.com", id="zwnj-with-right-joining-context-missing"),
        pytest.param("xn--a-xbb.\u65e5\u672c", id="punycode-decodes-to-decomposed-label"),
        pytest.param("xn--q-xbb6h.\u65e5\u672c", id="punycode-decodes-to-unordered-marks"),
    ],
)
def test_idna_validity_keeps_an_invalid_host_as_written(host: str) -> None:
    assert normalize_url(f"https://{host}/item?q=1") == f"https://{host}/item?q=1"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        pytest.param("\u05dc\u05d0.com", "xn--4dbx.com", id="rtl-letters"),
        pytest.param("\u05dc1.com", "xn--1-oic.com", id="rtl-letter-then-digit"),
        pytest.param("\u05d0\u0661.com", "xn--4db40a.com", id="rtl-letter-then-arabic-digit"),
        pytest.param("a\u0300.com", "xn--0ca.com", id="combining-mark-after-letter"),
        pytest.param("\u0628\u200c\u0628.com", "xn--ngba799q.com", id="zwnj-between-joining-letters"),
        pytest.param("\u0915\u094d\u200db.com", "xn--b-tud2o090g.com", id="zwj-after-virama"),
        pytest.param("\u0915\u094d\u200d\u0915.com", "xn--11ba1o090g.com", id="zwj-between-letters"),
        pytest.param("\u05d0-\u05d1.com", "xn----zhce.com", id="rtl-hyphen-separator"),
        pytest.param("\u05d0,\u05d1.com", "xn--,-zhce.com", id="rtl-comma-separator"),
        pytest.param("\u05d0$\u05d1.com", "xn--$-zhce.com", id="rtl-currency-sign"),
        pytest.param("\u05d0!\u05d1.com", "xn--!-zhce.com", id="rtl-neutral-punctuation"),
        pytest.param("\ua872\u200c\u1820.com", "xn--26e961b7q8j.com", id="zwnj-after-left-joining-letter"),
    ],
)
def test_idna_validity_accepts_a_valid_bidi_or_joiner_host(host: str, expected: str) -> None:
    assert normalize_url(f"https://{host}/item?q=1") == f"https://{expected}/item?q=1"


# A label whose punycode delta would overflow the 32-bit accumulator: a long run of one code point, then a distant one.
# In the first case the jump between the two code points alone passes the limit, in the second it lands just under and
# the distant code point's position in the label carries it over. Both stay under the 16384-code-point input cap and
# use CJK code points UTS #46 keeps, so they reach the encoder rather than the disallowed-code-point or input-cap
# rejection.
_OVERFLOW_LABELS: Final[tuple[str, ...]] = ("\u3400" * 16000 + "\U000323af", "\u3400" * 11159 + "\U000323ab")


@pytest.mark.parametrize(
    "label",
    [
        pytest.param(_OVERFLOW_LABELS[0], id="code-point-jump"),
        pytest.param(_OVERFLOW_LABELS[1], id="jump-plus-position"),
    ],
)
def test_punycode_delta_overflow_is_rejected(label: str) -> None:
    # A label whose encoding would overflow the punycode accumulator keeps its Unicode form, never a wrong host.
    url: Final[str] = f"http://{label}.example/"
    assert normalize_url(url) == url


def test_host_at_the_input_cap_still_encodes() -> None:
    assert normalize_url("http://" + "\u3400" * 16384 + "/").startswith("http://xn--")


def test_oversize_host_keeps_its_unicode_form() -> None:
    # past the 16384-code-point cap the host skips the O(n^2) reorder and punycode passes and keeps its Unicode form;
    # U+0316 then U+0301 alternate combining classes 220 and 230, the input an insertion-sort reorder is O(n^2) on
    url: Final[str] = "http://" + "\u0316\u0301" * 60000 + "/"
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
    url: Final[str] = f"http://a{chr(code_point)}b.example/"
    assert normalize_url(url) == url


_ACUTE: Final[str] = "\u0301"
_GRAVE_BELOW: Final[str] = "\u0316"
_DIALYTIKA: Final[str] = "\u0344"
_TIBETAN_II: Final[str] = "\u0f73"
_FFI: Final[str] = "\ufb03"
_INPUT_CAP: Final[int] = 16384
_FFI_PREFIX: Final[str] = "\u4e00\u4e8c\u4e09\u56db"


def test_host_mapped_to_the_input_cap_still_encodes() -> None:
    # U+FB03 maps to "ffi", so the host is exactly at the cap after the mapping
    host: Final[str] = f"{_FFI_PREFIX}{_FFI * ((_INPUT_CAP - len(_FFI_PREFIX)) // 3)}"
    assert normalize_url(f"http://{host}/").startswith("http://xn--ffiffi")


def test_misordered_marks_at_the_input_cap_reorder_in_linear_steps(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(normalize_url, f"http://{_ACUTE * n}{_GRAVE_BELOW * n}/"), (1500, 6000))


@pytest.mark.parametrize("length", [*range(56, 72), 300])
def test_punycode_matches_the_rfc_3492_sample_codec_on_both_sides_of_the_sorted_switch(length: int) -> None:
    # Labels from 64 code points (and payloads from 64 digits) up take the sorted encoder and decoder. The stdlib codec
    # is the RFC 3492 sample, so both paths must agree with it, and decoding then re-encoding must give the ACE label
    # back. The trailing non-ASCII label sends the host through ToASCII, which an all-ASCII host skips. The label mixes
    # out-of-order and repeated code points from three scripts with letters, digits and hyphens.
    pool: Final[str] = "ab7-\u03b1\u03c3\u03c2\u03c9\uac00\ud7a3\u4e00\u9fa5\u5c71"
    label: Final[str] = f"{''.join(pool[(index * 7 + index // 3) % len(pool)] for index in range(length - 1))}\u03b2"
    ace: Final[str] = f"xn--{label.encode('punycode').decode('ascii')}"
    assert normalize_url(f"http://{label}.\u00e9/") == f"http://{ace}.xn--9ca/"
    assert normalize_url(f"http://{ace}.\u00e9/") == f"http://{ace}.xn--9ca/"


def test_label_of_distinct_code_points_encodes_in_linear_steps(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda n: partial(normalize_url, f"http://{''.join(chr(0x4E00 + offset) for offset in range(n))}/"),
        (1500, 6000),
    )


def test_punycode_label_decodes_in_linear_steps(work_steps: WorkSteps) -> None:
    # descending code points: the decoder inserts each one in front of all it decoded before
    def host(count: int) -> str:
        label = "".join(chr(0x4E00 + count - offset) for offset in range(count))
        return f"http://xn--{label.encode('punycode').decode('ascii')}.\u00e9/"

    work_steps.assert_linear(lambda n: partial(normalize_url, host(n)), (1000, 4000))


@pytest.mark.parametrize(
    ("host", "ordered"),
    [
        pytest.param(f"a{(_ACUTE + _GRAVE_BELOW) * 20}", f"a{_GRAVE_BELOW * 20}{_ACUTE * 20}", id="one-long-run"),
        pytest.param(
            f"a{_ACUTE * 40}{_GRAVE_BELOW}b{_ACUTE}{_GRAVE_BELOW}",
            f"a{_GRAVE_BELOW}{_ACUTE * 40}b{_GRAVE_BELOW}{_ACUTE}",
            id="long-run-then-short-run",
        ),
        pytest.param(f"a{_ACUTE * 33}{_GRAVE_BELOW}", f"a{_GRAVE_BELOW}{_ACUTE * 33}", id="run-after-one-letter"),
        pytest.param(f"a{_ACUTE * 31}{_GRAVE_BELOW}", f"a{_GRAVE_BELOW}{_ACUTE * 31}", id="run-just-under-the-switch"),
    ],
)
def test_long_mark_runs_reorder_like_short_ones(host: str, ordered: str) -> None:
    assert normalize_url(f"http://{host}/") == normalize_url(f"http://{ordered}/")
    assert unicodedata.normalize("NFC", host) == unicodedata.normalize("NFC", ordered)


@pytest.mark.parametrize(
    "host",
    [
        pytest.param(f"{_FFI_PREFIX}{_FFI * ((_INPUT_CAP - len(_FFI_PREFIX)) // 3 + 1)}", id="mapping-ligature"),
        # U+3300 maps to four code points, so the host passes the input check and exceeds the cap once mapped
        pytest.param("\u3300" * (_INPUT_CAP // 4 + 5), id="mapping-expansion"),
        # U+0344 and U+0F73 each map to two combining marks, so this host is past the cap once mapped
        pytest.param(f"a{_DIALYTIKA * 6000}{_TIBETAN_II * 5000}", id="mapping-reorder"),
        # U+01D8 is u + U+0308 + U+0301; U+0323 sorts before both and composes with the u, leaving three code points
        pytest.param("\u01d8\u0323" * (_INPUT_CAP // 3 + 11), id="normalization"),
    ],
)
def test_host_lengthened_past_the_input_cap_keeps_its_unicode_form(host: str) -> None:
    url: Final[str] = f"http://{host}/"
    assert normalize_url(url) == url
