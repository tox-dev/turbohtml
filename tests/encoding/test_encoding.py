"""parse(bytes): the WHATWG encoding sniffing order and decoding."""

from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml.detect import detect


def test_str_input_has_no_encoding() -> None:
    assert parse("<p>hi</p>").encoding is None


def test_undeclared_bytes_default_to_windows_1252() -> None:
    # no BOM, argument, or <meta>: the WHATWG default applies
    assert parse(b"<p>hi</p>").encoding == "windows-1252"


def test_meta_charset_is_decoded() -> None:
    doc = parse('<meta charset="utf-8"><p>café</p>'.encode())
    assert doc.encoding == "UTF-8"
    element = doc.find("p")
    assert element is not None
    assert element.text == "café"


@pytest.mark.parametrize(
    ("data", "encoding"),
    [
        pytest.param(b"\xef\xbb\xbf<p>x", "UTF-8", id="utf-8"),
        pytest.param(b"\xff\xfe<\x00p\x00>\x00", "UTF-16LE", id="utf-16le"),
        pytest.param(b"\xfe\xff\x00<\x00p\x00>", "UTF-16BE", id="utf-16be"),
    ],
)
def test_bom_is_detected_and_stripped(data: bytes, encoding: str) -> None:
    doc = parse(data)
    assert doc.encoding == encoding
    element = doc.find("p")
    assert element is not None  # the byte-order mark did not leak into the tree


@pytest.mark.parametrize(
    ("data", "encoding_arg", "expected"),
    [
        pytest.param(b"<p>x", "iso-8859-2", "ISO-8859-2", id="argument-used"),
        pytest.param(b"\xef\xbb\xbf<p>x", "iso-8859-2", "UTF-8", id="bom-overrides-argument"),
        # the argument outranks a <meta>; a <meta> is used only without an argument
        pytest.param(b'<meta charset="utf-8"><p>x', "iso-8859-2", "ISO-8859-2", id="argument-outranks-meta"),
        pytest.param(b"<p>x", "  ISO-8859-2  ", "ISO-8859-2", id="whitespace-and-case-insensitive"),
    ],
)
def test_encoding_argument(data: bytes, encoding_arg: str, expected: str) -> None:
    assert parse(data, encoding=encoding_arg).encoding == expected


@pytest.mark.parametrize(
    "encoding_arg",
    [
        pytest.param("not-a-real-encoding", id="unknown-label"),
        pytest.param("", id="empty-label"),
        pytest.param("   ", id="whitespace-label"),
        pytest.param("x" * 80, id="overlong-label"),
    ],
)
def test_unknown_encoding_argument_raises(encoding_arg: str) -> None:
    # an unknown label is an error, not a silent windows-1252 fallback, matching Document.encode
    with pytest.raises(LookupError, match="unknown encoding"):
        parse(b"<p>x", encoding=encoding_arg)


def test_unknown_encoding_argument_raises_even_with_bom() -> None:
    # the label is validated before a BOM can win, so a bogus one is never silently ignored
    with pytest.raises(LookupError, match="unknown encoding"):
        parse(b"\xef\xbb\xbf<p>x", encoding="not-a-real-encoding")


@pytest.mark.parametrize(
    "wrap",
    [
        pytest.param(bytes, id="bytes"),
        pytest.param(bytearray, id="bytearray"),
        pytest.param(memoryview, id="memoryview"),
    ],
)
def test_bytes_like_inputs(wrap: type) -> None:
    assert parse(wrap(b'<meta charset="iso-8859-2"><p>x')).encoding == "ISO-8859-2"


def test_invalid_bytes_become_replacement_characters() -> None:
    # 0xFF is not valid UTF-8; decoding replaces it instead of raising
    doc = parse(b'<meta charset="utf-8"><p>\xff</p>')
    element = doc.find("p")
    assert element is not None
    assert element.text == "�"


@pytest.mark.parametrize(
    ("pad", "expected"),
    [
        # <meta charset=utf-8> is 20 bytes; the prescan examines only the first 1024
        pytest.param(1004, "UTF-8", id="meta-ends-at-1023-honored"),
        pytest.param(1005, "windows-1252", id="meta-ends-at-1024-ignored"),
    ],
)
def test_meta_prescan_stops_at_1024_bytes(pad: int, expected: str) -> None:
    # detect() is the byte sniffer alone, so it is where the prescan's window stays visible;
    # parse() reaches a later <meta> through the reparse below.
    assert detect(b"a" * pad + b"<meta charset=utf-8>").encoding == expected


@pytest.mark.parametrize(
    ("pad", "expected"),
    [
        pytest.param(1004, "UTF-8", id="prescan-reaches-it"),
        pytest.param(1005, "UTF-8", id="reparse-reaches-it"),
        pytest.param(10240, "UTF-8", id="reparse-reaches-it-far-out"),
    ],
)
def test_parse_honors_a_meta_past_the_prescan_window(pad: int, expected: str) -> None:
    # WHATWG "changing the encoding while parsing": a <meta> the prescan could not reach
    # still redoes the parse, so where it sits in the document does not change the answer.
    assert parse(b"a" * pad + b"<meta charset=utf-8>").encoding == expected


def test_reparse_decodes_text_before_the_late_meta() -> None:
    # the whole document is re-decoded, not just the bytes after the declaration
    doc = parse(b"<p>\xe8\xed\xf8</p><!-- " + b"x" * 1100 + b" --><meta charset=iso-8859-2>")
    paragraph = doc.find("p")
    assert paragraph is not None
    assert paragraph.text == "číř"


def test_reparse_reads_a_late_http_equiv_content_type() -> None:
    late = b"<!-- " + b"x" * 1100 + b' --><meta http-equiv="Content-Type" content="text/html; charset=gbk">'
    paragraph = parse(late + b"<p>\xd6\xd0\xce\xc4</p>").find("p")
    assert paragraph is not None
    assert paragraph.text == "中文"


def test_reparse_skips_an_unresolvable_label_for_the_next_meta() -> None:
    # an unsupported label falls through, exactly as the prescan's lookup does
    late = b"<!-- " + b"x" * 1100 + b' --><meta charset="bogus"><meta charset="iso8859-2">'
    assert parse(late + b"<p>\xe8</p>").encoding == "ISO-8859-2"


def test_reparse_ignores_a_meta_written_inside_a_script() -> None:
    # only a real <meta> element counts; a byte-level scan past the window would be fooled
    script = b"<!-- " + b"x" * 1100 + b' --><script>var s = "<meta charset=iso-8859-2>";</script>'
    assert parse(script + b"<p>caf\xc3\xa9</p>").encoding == "UTF-8"


def test_reparse_ignores_a_non_ascii_charset_label() -> None:
    # \xfc is not valid UTF-8, so the sniff falls back to windows-1252 and decodes the label
    # as "utf-8" with a leading U+00FC, which names no encoding: the parse is not redone
    late = b"<!-- " + b"x" * 1100 + b" --><meta charset=\xfctf-8>"
    assert parse(late + b"<p>x</p>").encoding == "windows-1252"


@pytest.mark.parametrize(
    ("markup", "encoding", "expected"),
    [
        pytest.param(b"\xef\xbb\xbf" + b"a" * 1100 + b"<meta charset=iso-8859-2>", None, "UTF-8", id="bom-is-certain"),
        pytest.param(b"a" * 1100 + b"<meta charset=iso-8859-2>", "utf-8", "UTF-8", id="argument-is-certain"),
    ],
)
def test_a_certain_encoding_blocks_the_reparse(markup: bytes, encoding: str | None, expected: str) -> None:
    assert parse(markup, encoding=encoding).encoding == expected


def test_a_matching_late_meta_does_not_reparse() -> None:
    # the declaration agrees with the sniff, so the encoding is merely confirmed
    assert parse(b"<p>caf\xc3\xa9</p><!-- " + b"x" * 1100 + b" --><meta charset=utf-8>").encoding == "UTF-8"


@pytest.mark.parametrize(
    ("markup", "encoding", "expected"),
    [
        pytest.param(b"\xef\xbb\xbf<p>x", None, "certain", id="byte-order-mark"),
        pytest.param(b"<p>x", "utf-8", "certain", id="encoding-argument"),
        pytest.param(b"<meta charset=utf-8><p>x", None, "certain", id="meta-inside-the-window"),
        pytest.param(b"<!-- " + b"x" * 1100 + b" --><meta charset=iso-8859-2>", None, "certain", id="meta-past-window"),
        pytest.param(b"<p>caf\xc3\xa9", None, "tentative", id="structural-utf8"),
        pytest.param(b"<p>caf\xe9", None, "tentative", id="windows-1252-fallback"),
    ],
)
def test_encoding_confidence(markup: bytes, encoding: str | None, expected: str) -> None:
    # the spec's certain/tentative split: a declaration decides, a sniff only guesses
    assert parse(markup, encoding=encoding).encoding_confidence == expected


def test_the_opt_in_detector_stays_tentative() -> None:
    assert parse(b"<p>caf\xe9", detect_encoding=True).encoding_confidence == "tentative"


def test_str_input_has_no_encoding_confidence() -> None:
    document = parse("<p>x</p>")
    assert document.encoding is None
    assert document.encoding_confidence is None


def _past_the_window(meta: bytes) -> bytes:
    # \xe8 keeps the document out of the structural UTF-8 check, so only the <meta> can decide
    return b"<p>\xe8</p><!-- " + b"x" * 1100 + b" -->" + meta


@pytest.mark.parametrize(
    "meta",
    [
        pytest.param(b"<meta charset>", id="valueless-charset"),
        pytest.param(b'<meta http-equiv content="text/html; charset=gbk">', id="valueless-http-equiv"),
        pytest.param(b'<meta http-equiv="refresh" content="0; url=/x">', id="pragma-is-not-content-type"),
        pytest.param(b'<meta http-equiv="content-type">', id="content-type-without-content"),
        pytest.param(b'<meta http-equiv="content-type" content>', id="valueless-content"),
        pytest.param(b'<meta name="description" content="text/html; charset=gbk">', id="name-is-not-a-pragma"),
    ],
)
def test_a_meta_declaring_no_encoding_does_not_reparse(meta: bytes) -> None:
    assert parse(_past_the_window(meta)).encoding == "windows-1252"


def test_reparse_looks_past_the_first_few_unresolvable_metas() -> None:
    # more declarations than the label array's initial room, none of the first five usable
    metas = b"".join(b'<meta charset="bogus-%d">' % index for index in range(5))
    assert parse(_past_the_window(metas + b'<meta charset="iso8859-2">')).encoding == "ISO-8859-2"


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        pytest.param("iso-8859-3", "ISO-8859-3", id="iso-8859-3"),
        pytest.param("latin4", "ISO-8859-4", id="iso-8859-4-alias"),
        pytest.param("arabic", "ISO-8859-6", id="iso-8859-6-alias"),
        pytest.param("iso-8859-8", "ISO-8859-8", id="iso-8859-8"),
        pytest.param("hebrew", "ISO-8859-8", id="iso-8859-8-alias"),
        pytest.param("iso-8859-8-i", "ISO-8859-8-I", id="iso-8859-8-i"),
        pytest.param("iso-8859-10", "ISO-8859-10", id="iso-8859-10"),
        pytest.param("iso-8859-13", "ISO-8859-13", id="iso-8859-13"),
        pytest.param("iso-8859-14", "ISO-8859-14", id="iso-8859-14"),
        pytest.param("iso-8859-16", "ISO-8859-16", id="iso-8859-16"),
        pytest.param("866", "IBM866", id="ibm866-alias"),
        pytest.param("csiso2022jp", "ISO-2022-JP", id="iso-2022-jp-alias"),
        pytest.param("x-mac-ukrainian", "x-mac-cyrillic", id="x-mac-cyrillic-alias"),
        pytest.param("x-user-defined", "x-user-defined", id="x-user-defined"),
        pytest.param("gbk", "GBK", id="gbk-name-kept"),  # GBK shares gb18030's decoder but keeps its own name
        # labels that previously fell through to windows-1252 (see #424)
        pytest.param("iso-8859-9", "windows-1254", id="iso-8859-9-turkish"),
        pytest.param("latin5", "windows-1254", id="latin5-alias"),
        pytest.param("tis-620", "windows-874", id="tis-620-thai"),
        pytest.param("iso-8859-11", "windows-874", id="iso-8859-11-thai"),
        pytest.param("shift-jis", "Shift_JIS", id="shift-jis-hyphen"),
        pytest.param("windows-31j", "Shift_JIS", id="windows-31j"),
        pytest.param("ms932", "Shift_JIS", id="ms932"),
        pytest.param("x-gbk", "GBK", id="x-gbk"),
        pytest.param("chinese", "GBK", id="chinese-alias"),
        pytest.param("windows-949", "EUC-KR", id="windows-949"),
        pytest.param("korean", "EUC-KR", id="korean-alias"),
        pytest.param("ksc5601", "EUC-KR", id="ksc5601"),
        pytest.param("koi8", "KOI8-R", id="koi8"),
        pytest.param("koi8_r", "KOI8-R", id="koi8_r-underscore"),
        pytest.param("x-mac-roman", "macintosh", id="x-mac-roman"),
        pytest.param("mac", "macintosh", id="mac-alias"),
        pytest.param("x-cp1251", "windows-1251", id="x-cp1251"),
        pytest.param("cp1253", "windows-1253", id="cp1253"),
        pytest.param("cp1258", "windows-1258", id="cp1258"),
    ],
)
def test_whatwg_label_resolves(label: str, expected: str) -> None:
    assert parse(b"<p>x", encoding=label).encoding == expected


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        # the five bytes CPython's cp1252 leaves undefined map to the C1 controls, not
        # U+FFFD (WHATWG windows-1252 index, #423); the codepoint equals the byte
        pytest.param(b"\x81\x8d\x8f\x90\x9d", "\x81\x8d\x8f\x90\x9d", id="undefined-bytes-are-c1-controls"),
        # a defined high byte (0x80 euro) beside an undefined one keeps the string wide
        pytest.param(b"a\x80\x81b", "a€\x81b", id="mixed-with-defined-high-byte"),
        # 0x80 and 0x9F stay their WHATWG glyphs, unchanged by the C1 restore
        pytest.param(b"\x80\x9f", "€Ÿ", id="defined-c1-bytes-unaffected"),
    ],
)
def test_windows_1252_undefined_bytes_decode_to_c1_controls(raw: bytes, text: str) -> None:
    element = parse(b"<p>" + raw, encoding="windows-1252").find("p")
    assert element is not None
    assert element.text == text


@pytest.mark.parametrize(
    ("label", "raw", "char"),
    [
        pytest.param("iso-8859-8", b"\xe0", "א", id="iso-8859-8-hebrew-alef"),
        pytest.param("ibm866", b"\x80", "\u0410", id="ibm866-cyrillic"),
        # GBK's decoder is gb18030's decoder: the four-byte sequence decodes instead of yielding U+FFFD
        pytest.param("gbk", bytes([0x81, 0x30, 0x81, 0x30]), "\x80", id="gbk-four-byte"),
        pytest.param("gbk", bytes([0xD2, 0xBB]), "一", id="gbk-two-byte-legacy"),
        # iso-8859-9 is the Turkish windows-1254 label: 0xFE is the dotless s-cedilla (#424)
        pytest.param("iso-8859-9", b"\xfe", "ş", id="iso-8859-9-decodes-windows-1254"),
        pytest.param("tis-620", b"\x80", "€", id="tis-620-decodes-windows-874"),
    ],
)
def test_whatwg_label_decodes(label: str, raw: bytes, char: str) -> None:
    doc = parse(b"<meta charset=" + label.encode() + b"><p>" + raw + b"</p>")
    element = doc.find("p")
    assert element is not None
    assert element.text == char


@pytest.mark.parametrize(
    ("raw", "char"),
    [
        pytest.param(b"A", "A", id="ascii-kept"),
        pytest.param(b"\x80", "", id="low-to-private-use"),
        pytest.param(b"\xff", "", id="high-to-private-use"),
    ],
)
def test_x_user_defined_argument_decodes(raw: bytes, char: str) -> None:
    # x-user-defined maps 0x80-0xFF into the U+F780-U+F7FF private-use block; it is
    # honored only via the encoding argument, since a <meta> declaring it is forced to
    # windows-1252 (see test_sniffing_edge_cases[meta-x-user-defined-to-1252])
    element = parse(b"<p>" + raw, encoding="x-user-defined").find("p")
    assert element is not None
    assert element.text == char


@pytest.mark.parametrize(
    "label",
    ["replacement", "iso-2022-kr", "csiso2022kr", "iso-2022-cn", "iso-2022-cn-ext", "hz-gb-2312"],
)
def test_replacement_encoding_collapses_to_one_fffd(label: str) -> None:
    # the stateful ISO-2022/HZ encodings are refused: a non-empty input is one U+FFFD
    doc = parse(b"<meta charset=" + label.encode() + b">\x80\x95")
    assert doc.encoding == "replacement"
    body = doc.find("body")
    assert body is not None
    assert body.text == "�"


def test_replacement_encoding_empty_input_is_empty() -> None:
    assert parse(b"", encoding="replacement").encoding == "replacement"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # byte-order-mark near misses fall through to the default
        pytest.param(b"a", "windows-1252", id="one-byte"),
        pytest.param(b"ab", "windows-1252", id="two-byte-no-bom"),
        pytest.param(b"\xefab", "windows-1252", id="ef-not-bb"),
        pytest.param(b"\xef\xbba", "windows-1252", id="ef-bb-not-bf"),
        pytest.param(b"\xfea", "windows-1252", id="fe-not-ff"),
        pytest.param(b"\xffa", "windows-1252", id="ff-not-fe"),
        # not actually a meta element
        pytest.param(b"<meto charset=utf-8>", "windows-1252", id="not-meta-name"),
        pytest.param(b"<metax>", "windows-1252", id="meta-no-space"),
        # WHATWG accepts 0x2F as the separator after "meta", like whitespace
        pytest.param(b"<meta/charset=utf-8>", "UTF-8", id="meta-slash-separator"),
        pytest.param(
            b'<meta/http-equiv="content-type" content="text/html;charset=utf-8">', "UTF-8", id="meta-slash-pragma"
        ),
        # attribute parsing edges
        pytest.param(b"<meta charset", "windows-1252", id="name-at-eof"),
        pytest.param(b"<meta charset=>", "windows-1252", id="empty-value"),
        pytest.param(b'<meta foo charset="utf-8">', "UTF-8", id="bare-attribute"),
        pytest.param(b"<meta " + b"a" * 40 + b'=x charset="utf-8">', "UTF-8", id="long-name"),
        pytest.param(b'<meta charset="' + b"z" * 200 + b'">', "windows-1252", id="long-quoted-value"),
        pytest.param(b"<meta charset=" + b"z" * 200 + b">", "windows-1252", id="long-unquoted-value"),
        # http-equiv / content
        pytest.param(b'<meta http-equiv="content-type" content="charset">', "windows-1252", id="content-no-eq"),
        pytest.param(
            b'<meta http-equiv="content-type" content="text/html; charset=utf-8; x">', "UTF-8", id="content-semicolon"
        ),
        pytest.param(
            b'<meta http-equiv="content-type" content="text/html; charset=\'utf-8\'">', "UTF-8", id="content-quoted"
        ),
        pytest.param(b'<meta http-equiv="refresh" content="0; charset=utf-8">', "windows-1252", id="not-pragma"),
        # tag skipping
        pytest.param(b'</p><meta charset="utf-8">', "UTF-8", id="end-tag-then-meta"),
        pytest.param(b'<?xml?><meta charset="utf-8">', "UTF-8", id="pi-then-meta"),
        pytest.param(b"<!-- unterminated", "windows-1252", id="unterminated-comment"),
        # a quoted attribute hides a later meta until its closing quote
        pytest.param(b'<p title="x><meta charset=utf-8"><p>', "windows-1252", id="meta-inside-quote"),
        # attribute-name and value lexing corners
        pytest.param(b'<meta =x charset="utf-8">', "UTF-8", id="equals-first"),
        pytest.param(b"<meta charset =utf-8>", "UTF-8", id="space-before-equals"),
        pytest.param(b"<meta charset ", "windows-1252", id="name-then-space-eof"),
        pytest.param(b'<meta CHARSET="utf-8">', "UTF-8", id="uppercase-name"),
        pytest.param(b"<meta charset=UTF-8>", "UTF-8", id="uppercase-value"),
        # content-attribute quote and truncation corners
        pytest.param(
            b'<meta http-equiv="content-type" content=\'text/html; charset="utf-8"\'>', "UTF-8", id="content-dquote"
        ),
        pytest.param(
            b'<meta http-equiv="content-type" content="charset=\'' + b"z" * 70 + b"'\">",
            "windows-1252",
            id="content-quoted-long",
        ),
        pytest.param(
            b'<meta http-equiv="content-type" content="charset=' + b"z" * 70 + b'">',
            "windows-1252",
            id="content-unquoted-long",
        ),
        pytest.param(b'<meta http-equiv="content-type" content="charset=">', "windows-1252", id="content-empty"),
        # an earlier charset wins, so the content attribute is not consulted
        pytest.param(
            b'<meta charset=utf-8 content="text/html; charset=iso-8859-2">', "UTF-8", id="charset-before-content"
        ),
        # meta-name near misses are skipped as ordinary tags
        pytest.param(b'<mq><meta charset="utf-8">', "UTF-8", id="m-not-me"),
        pytest.param(b'<meq><meta charset="utf-8">', "UTF-8", id="me-not-met"),
        # tag-skip combinator corners
        pytest.param(b'</1><meta charset="utf-8">', "UTF-8", id="end-tag-non-alpha"),
        pytest.param(b"</", "windows-1252", id="bare-end-tag"),
        pytest.param(b'< <meta charset="utf-8">', "UTF-8", id="lone-angle"),
        pytest.param(b'<br/><meta charset="utf-8">', "UTF-8", id="self-closing-tag-name"),
        # a <meta> declaring x-user-defined is forced to windows-1252 (HTML §13.2.3.2, #424),
        # unlike the encoding argument, which keeps x-user-defined
        pytest.param(b"<meta charset=x-user-defined><p>x", "windows-1252", id="meta-x-user-defined-to-1252"),
    ],
)
def test_sniffing_edge_cases(data: bytes, expected: str) -> None:
    assert parse(data).encoding == expected


def test_parse_requires_an_argument() -> None:
    with pytest.raises(TypeError):
        parse()  # ty: ignore[missing-argument]  # the missing markup is rejected at runtime
