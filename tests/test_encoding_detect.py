"""Content-based encoding detection (issue #182), the opt-in detect_encoding step.

Detection is strictly subordinate to the WHATWG sniffing path: a BOM, the encoding
argument, and a ``<meta>`` charset always win, and the step runs only when the
caller passes ``detect_encoding=True``. Phase 1 resolves UTF-8 by structural
validation; the single-byte and CJK candidates follow in later phases.
"""

from __future__ import annotations

import pytest

from turbohtml import parse


def detected(raw: bytes) -> str | None:
    return parse(raw, detect_encoding=True).encoding


def test_utf8_detected_only_with_opt_in() -> None:
    raw = "<p>café résumé Москва 日本語</p>".encode()
    assert parse(raw).encoding == "windows-1252"  # default stays spec-conformant
    assert detected(raw) == "UTF-8"
    paragraph = parse(raw, detect_encoding=True).find("p")
    assert paragraph is not None
    assert paragraph.text == "café résumé Москва 日本語"


def test_pure_ascii_is_not_detected() -> None:
    # ASCII decodes identically under windows-1252, so there is nothing to detect
    assert detected(b"<p>hello world</p>") == "windows-1252"


def test_meta_charset_wins_over_detection() -> None:
    # the bytes are valid UTF-8, but the <meta> declaration takes precedence
    raw = b"<meta charset=iso-8859-2><p>caf\xc3\xa9</p>"
    assert parse(raw, detect_encoding=True).encoding == "ISO-8859-2"


def test_bom_wins_over_detection() -> None:
    # a UTF-8 BOM resolves through the spec BOM step, before detection would run
    assert parse(b"\xef\xbb\xbf<p>x</p>", detect_encoding=True).encoding == "UTF-8"


def test_encoding_argument_wins_over_detection() -> None:
    raw = "café".encode()  # valid UTF-8
    assert parse(raw, encoding="iso-8859-5", detect_encoding=True).encoding == "ISO-8859-5"


@pytest.mark.parametrize(
    ("raw", "is_utf8"),
    [
        pytest.param(b"caf\xc3\xa9", True, id="two-byte"),  # é U+00E9
        pytest.param(b"\xe4\xb8\xad", True, id="three-byte-e1-ec"),  # 中 U+4E2D
        pytest.param(b"\xe0\xa0\x80", True, id="three-byte-e0-min"),  # U+0800
        pytest.param(b"\xed\x80\x80", True, id="three-byte-ed-prefix"),  # U+D000
        pytest.param(b"\xee\x80\x80", True, id="three-byte-ee-ef"),  # U+E000
        pytest.param(b"\xf0\x9f\x98\x80", True, id="four-byte-f0"),  # 😀 U+1F600
        pytest.param(b"\xf1\x80\x80\x80", True, id="four-byte-f1-f3"),  # U+40000
        pytest.param(b"\xf4\x80\x80\x80", True, id="four-byte-f4-max"),  # U+100000
        pytest.param(b"\xc0\x80", False, id="overlong-c0-lead"),
        pytest.param(b"\xe0\x80\x80", False, id="overlong-e0"),
        pytest.param(b"\xed\xa0\x80", False, id="surrogate-ed"),
        pytest.param(b"\xf0\x80\x80\x80", False, id="overlong-f0"),
        pytest.param(b"\xf4\x90\x80\x80", False, id="above-max-f4"),
        pytest.param(b"\xff", False, id="invalid-lead-ff"),
        pytest.param(b"\x80", False, id="stray-continuation"),
        pytest.param(b"\xc3", False, id="truncated-two-byte"),
        pytest.param(b"\xc3\x28", False, id="bad-first-continuation"),
        pytest.param(b"\xe1\x80\x28", False, id="bad-later-continuation-low"),
        pytest.param(b"\xe1\x80\xc0", False, id="bad-later-continuation-high"),
    ],
)
def test_utf8_validator(raw: bytes, is_utf8: bool) -> None:  # noqa: FBT001  # parametrized expectation flag
    # wrap so the document is non-trivial; detection runs on the whole byte buffer
    assert detected(b"<p>" + raw + b"</p>") == ("UTF-8" if is_utf8 else "windows-1252")


def test_truncated_sequence_at_eof_is_not_utf8() -> None:
    # an incomplete multi-byte sequence at the very end of the buffer disqualifies UTF-8
    assert detected(b"caf\xc3") == "windows-1252"
