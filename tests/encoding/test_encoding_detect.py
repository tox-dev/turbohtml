"""Content-based encoding detection (issue #182), the opt-in detect_encoding step.

Detection is strictly subordinate to the WHATWG sniffing path: a BOM, the encoding
argument, and a ``<meta>`` charset always win. UTF-8 is resolved by structural
validation, which is a proof rather than a guess and so runs unconditionally; the
frequency-scored single-byte and CJK candidates are what ``detect_encoding=True``
adds.
"""

from __future__ import annotations

import pytest

from turbohtml import parse


def detected(raw: bytes) -> str | None:
    return parse(raw, detect_encoding=True).encoding


def test_utf8_is_resolved_without_the_opt_in() -> None:
    # structural validity is a proof, so undeclared UTF-8 never reaches the windows-1252 fallback
    raw = "<p>café résumé Москва 日本語</p>".encode()
    assert parse(raw).encoding == "UTF-8"
    assert detected(raw) == "UTF-8"
    paragraph = parse(raw).find("p")
    assert paragraph is not None
    assert paragraph.text == "café résumé Москва 日本語"


def test_the_frequency_model_still_needs_the_opt_in() -> None:
    # windows-1250 text is not valid UTF-8, so only the opt-in candidates can name it
    raw = "<p>Příliš žluťoučký kůň</p>".encode("windows-1250")
    assert parse(raw).encoding == "windows-1252"
    assert detected(raw) == "windows-1250"


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
    # wrap so the document is non-trivial; detection runs on the whole byte buffer.
    # invalid UTF-8 resolves to some single-byte encoding (Phase 2), never UTF-8
    result = detected(b"<p>" + raw + b"</p>")
    if is_utf8:
        assert result == "UTF-8"
    else:
        assert result != "UTF-8"


def test_truncated_sequence_at_eof_is_not_utf8() -> None:
    # an incomplete multi-byte sequence at the very end of the buffer disqualifies UTF-8; chardetng answers
    # windows-1250 here, and did so before this port did, once the single-byte candidates see EOF as a space
    assert detected(b"caf\xc3") == "windows-1250"


# Single-byte detection (Phase 2). The expected values are golden, captured from
# Firefox's chardetng (the reference this port follows) so the C detector is checked
# against it without a runtime cargo dependency. Each case is natural-language text
# re-encoded into a legacy single-byte encoding; the detector must recover its label.
@pytest.mark.parametrize(
    ("text", "source", "expected"),
    [
        pytest.param(
            "Précédemment, la créativité française était très développée près de Paris ici.",
            "cp1252",
            "windows-1252",
            id="french",
        ),
        pytest.param(
            "Müller schrieb über die Größe der schönen Häuser in Würzburg täglich neu.",
            "iso-8859-1",
            "windows-1252",
            id="german",
        ),
        pytest.param(
            "Программирование помогает понять структуру вычислительных систем сегодня здесь.",
            "cp1251",
            "windows-1251",
            id="russian-1251",
        ),
        pytest.param(
            "Москва это столица России и очень большой красивый город здесь сейчас опять.",
            "koi8-r",
            "KOI8-U",
            id="russian-koi8",
        ),
        pytest.param(
            "Η ελληνική γλώσσα είναι μία από τις αρχαιότερες γλώσσες στον κόσμο σήμερα εδώ.",  # noqa: RUF001
            "cp1253",
            "windows-1253",
            id="greek-1253",
        ),
        pytest.param(
            "Zażółć gęślą jaźń bardzo szybko aby sprawdzić wszystkie polskie znaki tutaj teraz.",
            "cp1250",
            "windows-1250",
            id="polish-1250",
        ),
        pytest.param(
            "Příliš žluťoučký kůň úpěl ďábelské ódy nad řekou ve městě každý letní večer tam.",
            "iso-8859-2",
            "ISO-8859-2",
            id="czech-iso2",
        ),
        pytest.param(
            "Çok güzel bir gün bugün İstanbul şehrinde yağmur yağıyor ve sıcaklık çok düşük.",  # noqa: RUF001
            "cp1254",
            "windows-1254",
            id="turkish-1254",
        ),
        pytest.param(
            "שלום לכולם היום יום יפה מאוד בעיר תל אביב והשמש זורחת בשמיים הכחולים שלנו כאן.",
            "cp1255",
            "windows-1255",
            id="hebrew-1255",
        ),
        pytest.param(
            "اللغة العربية لغة جميلة وغنية بالكلمات والتعابير المختلفة في العالم العربي كله.",
            "cp1256",
            "windows-1256",
            id="arabic-1256",
        ),
        pytest.param(
            "Lietuvių kalba turi daug specialių raidžių ir įdomią gramatikos struktūrą šiandien.",
            "cp1257",
            "windows-1257",
            id="baltic-1257",
        ),
    ],
)
def test_single_byte_detection(text: str, source: str, expected: str) -> None:
    assert detected(text.encode(source)) == expected


# CJK multi-byte detection (Phase 3). Each candidate drives a CPython incremental
# codec the way chardetng drives encoding_rs; the expected labels are golden, matching
# Firefox's chardetng on the same well-formed text.
@pytest.mark.parametrize(
    ("text", "source", "expected"),
    [
        pytest.param(
            "日本語のテキストをここに書きます。今日はとても良い天気ですね。",
            "shift_jis",
            "Shift_JIS",
            id="japanese-shift_jis",
        ),
        pytest.param(
            "日本語のテキストをここに書きます。今日はとても良い天気ですね。",
            "euc_jp",
            "EUC-JP",
            id="japanese-euc-jp",
        ),
        pytest.param(
            "コンピュータのプログラムはとても複雑な仕組みで動いています。",
            "shift_jis",
            "Shift_JIS",
            id="japanese-katakana",
        ),
        pytest.param(
            "한국어 텍스트를 여기에 작성합니다 오늘은 날씨가 정말 좋습니다 그렇죠.",
            "euc_kr",
            "EUC-KR",
            id="korean-euc-kr",
        ),
        pytest.param(
            "这是一段简体中文文本用来测试编码检测今天天气非常好我们去公园散步吧。",
            "gbk",
            "GBK",
            id="simplified-gbk",
        ),
        pytest.param(
            "這是一段繁體中文文本用來測試編碼偵測今天天氣非常好我們去公園散步吧。",
            "big5",
            "Big5",
            id="traditional-big5",
        ),
    ],
)
def test_cjk_detection(text: str, source: str, expected: str) -> None:
    assert detected(text.encode(source)) == expected


def test_iso_2022_jp_detected() -> None:
    # ISO-2022-JP is 7-bit and escape-driven: no high byte, an escape, decodes cleanly
    raw = "日本語のテキストをここに書きます。".encode("iso2022_jp")
    assert detected(raw) == "ISO-2022-JP"
