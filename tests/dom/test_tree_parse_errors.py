"""Collecting and reporting WHATWG parse errors from parse(), with optional strict mode."""

from __future__ import annotations

import gc

import pytest

from turbohtml import Document, HTMLParseError, IncrementalParser, ParseError, Tokenizer, parse, parse_xml, tokenize


@pytest.mark.parametrize(
    ("markup", "code", "line", "col"),
    [
        pytest.param("<div", "eof-in-tag", 1, 4, id="eof-in-tag"),
        pytest.param("<div foo", "eof-in-tag", 1, 8, id="eof-in-tag-attr"),
        pytest.param("<!--unclosed", "eof-in-comment", 1, 12, id="eof-in-comment"),
        pytest.param("<!DOCTYPE", "eof-in-doctype", 1, 9, id="eof-in-doctype"),
        pytest.param("<a b b>", "duplicate-attribute", 1, 6, id="duplicate-attribute"),
        pytest.param("<!-->", "abrupt-closing-of-empty-comment", 1, 4, id="abrupt-empty-comment"),
        pytest.param("<!--->", "abrupt-closing-of-empty-comment", 1, 5, id="abrupt-empty-comment-dash"),
        pytest.param("</>", "missing-end-tag-name", 1, 2, id="missing-end-tag-name"),
        pytest.param("<?php?>", "unexpected-question-mark-instead-of-tag-name", 1, 1, id="question-mark"),
        pytest.param("<html><!DOCTYPE html>", "unexpected-doctype", 1, 6, id="unexpected-doctype"),
    ],
)
def test_single_error(markup: str, code: str, line: int, col: int) -> None:
    errors = parse(markup).errors
    assert len(errors) == 1
    assert (errors[0].code, errors[0].line, errors[0].col) == (code, line, col)


@pytest.mark.parametrize(
    ("markup", "code", "line", "col"),
    [
        pytest.param("<!DOCTYPEhtml>", "missing-whitespace-before-doctype-name", 1, 9, id="doctype-no-space"),
        pytest.param("<!DOCTYPE>", "missing-doctype-name", 1, 9, id="doctype-no-name"),
        pytest.param('<!DOCTYPE a PUBLIC"x">', "missing-whitespace-after-doctype-public-keyword", 1, 18, id="public"),
        pytest.param('<!DOCTYPE a SYSTEM"x">', "missing-whitespace-after-doctype-system-keyword", 1, 18, id="system"),
        pytest.param("<!DOCTYPE a x>", "invalid-character-sequence-after-doctype-name", 1, 12, id="doctype-junk"),
        pytest.param("<0>", "invalid-first-character-of-tag-name", 1, 1, id="bad-tag-name"),
        pytest.param("<div/ id=1>", "unexpected-solidus-in-tag", 1, 5, id="stray-solidus"),
        pytest.param('<a b="c"d>', "missing-whitespace-between-attributes", 1, 8, id="no-space-between-attrs"),
        pytest.param("<a =b>", "unexpected-equals-sign-before-attribute-name", 1, 3, id="leading-equals"),
        pytest.param("<a b=>", "missing-attribute-value", 1, 5, id="empty-attribute-value"),
        pytest.param("<a b<c>", "unexpected-character-in-attribute-name", 1, 4, id="lt-in-attr-name"),
        pytest.param("<a b=c`>", "unexpected-character-in-unquoted-attribute-value", 1, 6, id="backtick-in-value"),
        pytest.param("<!x>", "incorrectly-opened-comment", 1, 2, id="bogus-comment"),
        pytest.param("<!--x--!>", "incorrectly-closed-comment", 1, 8, id="bang-before-close"),
        pytest.param("<![CDATA[x]]>", "cdata-in-html-content", 1, 8, id="cdata-outside-foreign"),
        pytest.param("&#;", "absence-of-digits-in-numeric-character-reference", 1, 2, id="no-digits"),
        pytest.param("&#xd800;", "surrogate-character-reference", 1, 8, id="surrogate-reference"),
        pytest.param("&#xfdd0;", "noncharacter-character-reference", 1, 8, id="noncharacter-reference"),
        pytest.param("&#x0000;", "null-character-reference", 1, 8, id="null-reference"),
        pytest.param("&#013;", "control-character-reference", 1, 6, id="control-reference"),
        pytest.param("&NotARealEntity;", "unknown-named-character-reference", 1, 15, id="unknown-entity"),
        pytest.param("a\x0bz", "control-character-in-input-stream", 1, 1, id="control-in-input"),
        pytest.param("a\ufdd0z", "noncharacter-in-input-stream", 1, 1, id="noncharacter-in-input"),
        pytest.param("a\ud800z", "surrogate-in-input-stream", 1, 1, id="surrogate-in-input"),
        pytest.param("a\x00b", "unexpected-null-character", 1, 1, id="null-in-data"),
    ],
)
def test_single_error_across_the_whatwg_codes(markup: str, code: str, line: int, col: int) -> None:
    errors = parse(markup).errors
    assert len(errors) == 1
    assert (errors[0].code, errors[0].line, errors[0].col) == (code, line, col)


def test_preprocessing_errors_interleave_with_tokenizer_errors() -> None:
    # the control character precedes the tag error at the same position, as the spec reads
    # the input stream before the tokenizer consumes the character
    assert [error.code for error in parse("<\x01").errors] == [
        "control-character-in-input-stream",
        "invalid-first-character-of-tag-name",
    ]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            "<\x01",
            ["control-character-in-input-stream", "invalid-first-character-of-tag-name"],
            id="same-position-preprocessing-first",
        ),
        pytest.param(
            "<0>\x01",
            ["invalid-first-character-of-tag-name", "control-character-in-input-stream"],
            id="tokenizer-error-comes-first",
        ),
        pytest.param(
            "\x01\n<0>",
            ["control-character-in-input-stream", "invalid-first-character-of-tag-name"],
            id="preprocessing-error-on-the-earlier-line",
        ),
        pytest.param(
            "<0>\n\x01",
            ["invalid-first-character-of-tag-name", "control-character-in-input-stream"],
            id="tokenizer-error-on-the-earlier-line",
        ),
    ],
)
def test_preprocessing_errors_interleave_by_position(markup: str, expected: list[str]) -> None:
    # the spec reads the input stream before the tokenizer consumes the character, so a
    # preprocessing error precedes a tokenizer error raised at the same position
    assert [error.code for error in parse(markup).errors] == expected


# the tokenizer core is stamped once per PyUnicode storage width, so each width has to run
_WIDTHS = [pytest.param("", id="ucs1"), pytest.param("\u20ac", id="ucs2"), pytest.param("\U0001f600", id="ucs4")]


@pytest.mark.parametrize("prefix", _WIDTHS)
@pytest.mark.parametrize(
    "reference",
    [
        pytest.param("&#9;", id="tab"),
        pytest.param("&#10;", id="line-feed"),
        pytest.param("&#12;", id="form-feed"),
        pytest.param("&#32;", id="space"),
    ],
)
def test_ascii_whitespace_references_are_not_control_references(prefix: str, reference: str) -> None:
    assert parse(prefix + reference).errors == []


@pytest.mark.parametrize("prefix", _WIDTHS)
def test_a_name_longer_than_the_table_still_reports_the_semicolon(prefix: str) -> None:
    errors = parse(prefix + "&" + "a1" * 40 + ";").errors
    assert [error.code for error in errors] == ["unknown-named-character-reference"]


@pytest.mark.parametrize("prefix", _WIDTHS)
@pytest.mark.parametrize(
    "tail",
    [pytest.param(" ", id="followed-by-text"), pytest.param("", id="ends-the-input")],
)
def test_a_name_longer_than_the_table_without_a_semicolon_is_literal(prefix: str, tail: str) -> None:
    assert parse(prefix + "&" + "a1" * 40 + tail).errors == []


@pytest.mark.parametrize("prefix", _WIDTHS[1:])
@pytest.mark.parametrize(
    ("newline", "expected_line"),
    [
        pytest.param("\r\n", 2, id="crlf"),
        pytest.param("\r", 2, id="lone-cr"),
        pytest.param("", 1, id="no-newline"),
    ],
)
def test_wide_input_normalizes_newlines_when_locating_errors(prefix: str, newline: str, expected_line: int) -> None:
    # the preprocessing walk counts lines over the newline-normalized stream the tokenizer reads
    errors = parse(prefix + "\x01" + newline + "\x02").errors
    assert [(error.code, error.line) for error in errors] == [
        ("control-character-in-input-stream", 1),
        ("control-character-in-input-stream", expected_line),
    ]


@pytest.mark.parametrize("prefix", _WIDTHS[1:])
def test_wide_input_ending_in_a_carriage_return(prefix: str) -> None:
    # the CR has no character after it to pair with, so the lookahead must not read past the end
    assert [error.code for error in parse(prefix + "\x01\r").errors] == ["control-character-in-input-stream"]


def test_a_long_text_run_still_reports_a_null() -> None:
    # the run scanners are vectorized, so the NUL must stop a block, not only a scalar tail
    assert [error.code for error in parse("a" * 64 + "\x00").errors] == ["unexpected-null-character"]


@pytest.mark.parametrize("prefix", _WIDTHS)
def test_an_overlong_reference_suspends_until_the_input_ends(prefix: str) -> None:
    # the reference helper waits for more input rather than deciding on a partial name
    tokenizer = Tokenizer()
    assert list(tokenizer.feed(prefix + "&" + "a" * 40)) == []
    assert [token.data for token in tokenizer.close()] == [prefix + "&" + "a" * 40]


def test_reading_errors_twice_reports_the_same_list() -> None:
    # the preprocessing errors are folded in on the first read, and once only
    document = parse("a\x01b")
    first = [error.code for error in document.errors]
    assert first == ["control-character-in-input-stream"]
    assert [error.code for error in document.errors] == first


def _streamed(markup: str, chunk: int = 1) -> list[tuple[str, int, int]]:
    parser = IncrementalParser()
    for start in range(0, len(markup), chunk):
        parser.feed(markup[start : start + chunk])
    return [(error.code, error.line, error.col) for error in parser.close().errors]


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<div", id="tokenizer-eof-in-tag"),
        pytest.param("<a b b>", id="tokenizer-duplicate-attribute"),
        pytest.param("a\x01b", id="preprocessing-control"),
        pytest.param("<p>￾</p>", id="preprocessing-noncharacter"),
        pytest.param("<p>\ud800</p>", id="preprocessing-surrogate"),
        pytest.param("<p a=1 a=2>\x02", id="both-kinds-interleaved"),
        pytest.param("a\r\n\x01b", id="control-after-a-crlf"),
        pytest.param("a\r\x01b", id="control-after-a-lone-cr"),
    ],
)
def test_a_streamed_document_reports_the_errors_parse_reports(markup: str) -> None:
    # the streaming tokenizer collects into the tree's sink, and each chunk is swept for the preprocessing errors the
    # states never see, so one byte at a time yields exactly what the whole buffer does, positions included
    expected = [(error.code, error.line, error.col) for error in parse(markup).errors]
    assert _streamed(markup) == expected
    assert expected != []


def test_a_crlf_split_across_a_feed_counts_one_line_break() -> None:
    # the tokenizer reads a newline-normalized stream: the U+000A that opens the second chunk finishes the break the
    # first chunk's U+000D opened, so the control character that follows is on line 2, not line 3
    parser = IncrementalParser()
    parser.feed("a\r")
    parser.feed("\n\x01b")
    assert [(error.code, error.line, error.col) for error in parser.close().errors] == [
        ("control-character-in-input-stream", 2, 0),
    ]


@pytest.mark.parametrize(
    ("chunks", "line", "col"),
    [
        pytest.param(["a\r", "", "\n\x01b"], 2, 0, id="empty-chunk-between-cr-and-lf"),
        pytest.param(["a\r", "日\x01"], 2, 1, id="wide-chunk-after-a-cr"),
        pytest.param(["a\r", "\x01b"], 2, 0, id="chunk-after-a-lone-cr"),
    ],
)
def test_a_pending_cr_survives_the_chunk_that_follows(chunks: list[str], line: int, col: int) -> None:
    # an empty chunk decides nothing, so the U+000D still awaits a U+000A; a chunk of wider code points resumes the
    # same way the byte-sized one does
    parser = IncrementalParser()
    for chunk in chunks:
        parser.feed(chunk)
    assert [(error.code, error.line, error.col) for error in parser.close().errors] == [
        ("control-character-in-input-stream", line, col),
    ]


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("\t\x08" + "A" * 6, id="tab-then-control"),
        pytest.param("\x00\x01" + "A" * 6, id="null-then-control"),
        pytest.param("\x0c\x1f" + "A" * 6, id="form-feed-then-control"),
    ],
)
def test_a_control_beside_an_ordinary_low_byte_is_still_reported(markup: str) -> None:
    # the eight-byte skip masks the tab, form feed and NUL out of the "below space" test, and the mask it built from
    # them used to erase the neighboring control character's bit along with them
    codes = [error.code for error in parse(markup).errors]
    assert "control-character-in-input-stream" in codes


def test_a_streamed_preprocessing_error_precedes_a_tokenizer_error_at_one_position() -> None:
    # th_error_sink_merge orders a preprocessing error before a tokenizer error at the same position, because the spec
    # raises it as the character is read rather than as a state consumes it
    assert _streamed("<a\x01") == [("control-character-in-input-stream", 1, 2), ("eof-in-tag", 1, 3)]


def test_a_streamed_byte_document_reports_the_errors_parse_reports() -> None:
    # the chunk the decoder hands the tokenizer is what gets swept, so a sequence split across a feed still lands its
    # error at the position the whole-buffer parse gives it
    raw = "<p>\x01日</p>".encode("cp932")
    parser = IncrementalParser(encoding="shift_jis")
    for start in range(0, len(raw), 2):
        parser.feed(raw[start : start + 2])
    streamed = [(error.code, error.line, error.col) for error in parser.close().errors]
    assert streamed == [(error.code, error.line, error.col) for error in parse(raw, encoding="shift_jis").errors]
    assert streamed == [("control-character-in-input-stream", 1, 3)]


def test_xml_reports_its_own_error_set() -> None:
    # the XML parser names a control character its own way, and never runs the HTML
    # preprocessing step; a well-formed document leaves the list empty
    assert parse_xml("<r>ab</r>").errors == []
    with pytest.raises(HTMLParseError) as raised:
        parse_xml("<r>a\x01b</r>")
    assert raised.value.error.code == "xml-invalid-char"


def test_well_formed_document_has_no_errors() -> None:
    assert parse("<!DOCTYPE html><html><body><p>hi</p></body></html>").errors == []


def test_errors_is_a_fresh_list_each_call() -> None:
    document = parse("<div")
    first = document.errors
    second = document.errors
    assert first == second
    assert first is not second  # a new list is materialized per access


def test_error_field_types() -> None:
    error = parse("<div").errors[0]
    assert isinstance(error.code, str)
    assert isinstance(error.line, int)
    assert isinstance(error.col, int)


def test_error_position_tracks_newlines() -> None:
    error = parse("\n\n<div").errors[0]  # the open tag starts on the third line
    assert error.code == "eof-in-tag"
    assert error.line == 3


def test_multiple_errors_in_document_order() -> None:
    codes = [error.code for error in parse("<a b b><c d d>").errors]
    assert codes == ["duplicate-attribute", "duplicate-attribute"]


def test_bytes_input_collects_errors() -> None:
    assert parse(b"<div").errors[0].code == "eof-in-tag"


def test_collects_more_errors_than_the_initial_capacity() -> None:
    # one unique attribute then eleven duplicates, growing the sink past its
    # initial capacity so every duplicate is still recorded
    document = parse("<a" + " b" * 12 + ">")
    assert len(document.errors) == 11
    assert {error.code for error in document.errors} == {"duplicate-attribute"}


def test_repr_round_trips_fields() -> None:
    error = parse("<div").errors[0]
    assert repr(error) == f"ParseError(code='eof-in-tag', line={error.line}, col={error.col})"


def test_equality_and_hash() -> None:
    one = parse("<div").errors[0]
    same = parse("<div").errors[0]
    other = parse("<!--x").errors[0]
    assert one == same
    assert hash(one) == hash(same)
    assert one != other
    assert one != "not a parse error"  # a foreign type compares unequal, never raising


def test_equality_compares_code_and_position() -> None:
    eof_tag = parse("<div").errors[0]  # eof-in-tag at (1, 4)
    abrupt = parse("<!-->").errors[0]  # abrupt-closing-of-empty-comment, also at (1, 4)
    next_line = parse("\n<div").errors[0]  # eof-in-tag at (2, 4): same code and column, later line
    assert eof_tag != abrupt  # same position, different code
    assert eof_tag != next_line  # same code and column, different line
    assert eof_tag == parse("<div").errors[0]  # every field matches


def test_ordering_is_unsupported() -> None:
    one = parse("<div").errors[0]
    other = parse("<!--x").errors[0]
    with pytest.raises(TypeError):
        _ = one < other  # ty: ignore[unsupported-operator]  # only equality is defined, never an ordering


def test_cannot_instantiate_parse_error() -> None:
    with pytest.raises(TypeError):
        ParseError()  # type: ignore[call-arg]


def test_strict_raises_on_first_error() -> None:
    with pytest.raises(HTMLParseError) as raised:
        parse("<div", strict=True)
    error = raised.value.error
    assert isinstance(error, ParseError)
    assert error.code == "eof-in-tag"
    assert str(raised.value) == f"eof-in-tag at line {error.line}, column {error.col}"


def test_strict_reports_only_the_first_error() -> None:
    with pytest.raises(HTMLParseError) as raised:
        parse("<a b b><c d d>", strict=True)
    assert raised.value.error.code == "duplicate-attribute"
    assert raised.value.error.col == 6  # the first duplicate, not the second


def test_strict_on_bytes_input() -> None:
    with pytest.raises(HTMLParseError) as raised:
        parse(b"<!DOCTYPE", strict=True)
    assert raised.value.error.code == "eof-in-doctype"


def test_strict_on_well_formed_input_returns_document() -> None:
    document = parse("<!DOCTYPE html><p>ok</p>", strict=True)
    assert isinstance(document, Document)
    assert document.errors == []


def test_standalone_tokenizer_ignores_errors() -> None:
    # tokenize() runs the state machine with no error sink, so a malformed run
    # still tokenizes (the duplicate attribute is dropped) without collecting.
    tokens = list(tokenize("<a b b>"))
    start = next(token for token in tokens if token.tag == "a")
    assert start.attrs == [("b", "")]  # the second b is dropped, first wins


def test_html_parse_error_is_an_exception() -> None:
    assert issubclass(HTMLParseError, Exception)


def test_parse_error_outlives_its_document() -> None:
    # a ParseError holds no reference into the tree (its code is a static string),
    # so it keeps working after the Document and its arena are collected
    error = parse("<div").errors[0]
    gc.collect()
    assert (error.code, error.line, error.col) == ("eof-in-tag", 1, 4)
    assert repr(error) == "ParseError(code='eof-in-tag', line=1, col=4)"
