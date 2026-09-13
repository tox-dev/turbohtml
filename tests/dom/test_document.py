from __future__ import annotations

import gc
import pickle  # ruff:ignore[suspicious-pickle-import]  # round-tripping our own trusted payloads
import threading
from typing import Final

import pytest

from turbohtml import (
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    HTMLParseError,
    IncrementalParser,
    Node,
    ParseError,
    ProcessingInstruction,
    Text,
    Tokenizer,
    parse,
    parse_xml,
    tokenize,
)
from turbohtml._html import _reconstruct

# Each fixture exercises a different resumable corner of the tree builder: text and
# attributes, rawtext (script/style/title/textarea) whose content can split across a
# feed, table foster-parenting, select, template, foreign content, the adoption
# agency, comments, doctype, and CR normalization.
DOCUMENTS = [
    pytest.param("", id="empty"),
    pytest.param("<!DOCTYPE html><p class=x>Hello <b>wo</b>rld</p>", id="doctype-text-attr"),
    pytest.param("<title>A &amp; B</title><script>var x = 1 < 2;</script>", id="rawtext"),
    pytest.param("<textarea>\nkept</textarea><style>p{color:red}</style>", id="textarea-style"),
    pytest.param("<table><tr><td>cell</td></tr>stray<caption>c</caption></table>", id="table-foster"),
    pytest.param("<select><option>a<option>b</select><p>after", id="select"),
    pytest.param("<template><tr><td>x</td></tr></template>", id="template"),
    pytest.param("<svg><circle/><foreignObject><div>d</div></foreignObject></svg>", id="foreign"),
    pytest.param("<b>1<i>2</b>3</i>4", id="adoption-agency"),
    pytest.param("<!-- a comment --><p>after the comment</p>", id="comment"),
    pytest.param("<div><p>a<p>b<p>c</div>", id="implied-end-tags"),
    pytest.param("line one\r\nline two\rline three", id="cr-normalization"),
    pytest.param("<p>n\x00ul</p>", id="nul"),
    pytest.param("<p>\x00</p><p>名名名\x00名</p>", id="nul-before-two-byte-chunks"),
    pytest.param("<p>\x00</p><p>😀😀😀\x00😀</p>", id="nul-before-four-byte-chunks"),
    pytest.param("plain text with no tags at all", id="plain-text"),
]

CHUNK_SIZES = [pytest.param(size, id=f"chunk-{size}") for size in (1, 2, 3, 5, 8, 13)]


def _stream(document: str, chunk: int) -> str:
    parser = IncrementalParser()
    for start in range(0, len(document), chunk):
        parser.feed(document[start : start + chunk])
    return parser.close().html


@pytest.mark.parametrize("document", DOCUMENTS)
@pytest.mark.parametrize("chunk", CHUNK_SIZES)
def test_chunked_str_matches_whole(document: str, chunk: int) -> None:
    assert _stream(document, chunk) == parse(document).html


@pytest.mark.parametrize("document", DOCUMENTS)
def test_one_character_at_a_time_matches_whole(document: str) -> None:
    parser = IncrementalParser()
    for character in document:
        parser.feed(character)
    assert parser.close().html == parse(document).html


# A U+0000 makes the text builder filter NUL; the streaming path must flag it from
# whatever storage width the fed chunk happens to use, so feed each width whole.
WIDE_WIDTHS = [
    pytest.param("<p>café ☃ two-byte plain</p>", id="two-byte"),
    pytest.param("<p>café\x00☃ two-byte nul</p>", id="two-byte-nul"),
    pytest.param("<p>math 🦄 four-byte plain</p>", id="four-byte"),
    pytest.param("<p>🦄\x00 four-byte nul</p>", id="four-byte-nul"),
]


@pytest.mark.parametrize("document", WIDE_WIDTHS)
def test_wide_storage_widths_single_feed_match_whole(document: str) -> None:
    parser = IncrementalParser()
    parser.feed(document)  # one feed keeps the chunk at the string's natural width
    assert parser.close().html == parse(document).html


def test_close_returns_a_document() -> None:
    parser = IncrementalParser()
    parser.feed("<p>hi</p>")
    document = parser.close()
    assert isinstance(document, Document)
    paragraph = document.find("p")
    assert paragraph is not None
    assert paragraph.text == "hi"


def test_str_parse_has_no_encoding() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    assert parser.close().encoding is None


def test_empty_close_builds_the_skeleton() -> None:
    assert IncrementalParser().close().html == parse("").html


MULTIBYTE = "<p>café — ☃ — 🦄</p>"  # 2-, 3-, and 4-byte UTF-8 sequences


def test_utf8_byte_at_a_time_matches_decoded_str() -> None:
    raw = MULTIBYTE.encode("utf-8")
    parser = IncrementalParser()
    for index in range(len(raw)):
        parser.feed(raw[index : index + 1])  # split inside every multi-byte sequence
    assert parser.close().html == parse(MULTIBYTE).html


@pytest.mark.parametrize("chunk", [1, 2, 3, 4, 7])
def test_utf8_chunked_matches_decoded_str(chunk: int) -> None:
    raw = MULTIBYTE.encode("utf-8")
    parser = IncrementalParser()
    for start in range(0, len(raw), chunk):
        parser.feed(raw[start : start + chunk])
    assert parser.close().html == parse(MULTIBYTE).html


def test_bytes_parse_reports_its_encoding() -> None:
    # an alias resolves to its WHATWG canonical name, the same one parse(bytes) reports
    parser = IncrementalParser(encoding="iso-8859-1")
    parser.feed("héllo".encode("latin-1"))
    document = parser.close()
    assert document.encoding == "windows-1252"
    paragraph = document.find("body")
    assert paragraph is not None
    assert "héllo" in paragraph.text


def test_explicit_encoding_decodes_each_chunk() -> None:
    text = "<p>naïve façade</p>"
    raw = text.encode("latin-1")
    parser = IncrementalParser(encoding="iso-8859-1")
    for byte in raw:
        parser.feed(bytes([byte]))
    assert parser.close().html == parse(text).html


def test_mixed_str_and_bytes_chunks() -> None:
    parser = IncrementalParser()
    parser.feed("<p>")
    parser.feed("café".encode())
    parser.feed("</p>")
    document = parser.close()
    paragraph = document.find("p")
    assert paragraph is not None
    assert paragraph.text == "café"
    assert document.encoding == "UTF-8"  # a bytes chunk resolved the label


def test_bytearray_and_memoryview_are_accepted() -> None:
    parser = IncrementalParser()
    parser.feed(bytearray(b"<p>a"))  # ty: ignore[invalid-argument-type]  # any buffer is accepted at runtime
    parser.feed(memoryview(b"b</p>"))  # ty: ignore[invalid-argument-type]  # any buffer is accepted at runtime
    paragraph = parser.close().find("p")
    assert paragraph is not None
    assert paragraph.text == "ab"


def test_unknown_encoding_raises_on_first_bytes_feed() -> None:
    parser = IncrementalParser(encoding="not-a-real-codec")
    with pytest.raises(LookupError):
        parser.feed(b"<p>x</p>")


def test_surrogate_encoding_name_raises_on_first_bytes_feed() -> None:
    parser = IncrementalParser(encoding="\udc80")  # no UTF-8 form for the codec lookup
    with pytest.raises(UnicodeEncodeError):
        parser.feed(b"<p>x</p>")


def _streamed(raw: bytes, encoding: str, chunk: int) -> str:
    parser = IncrementalParser(encoding=encoding)
    for start in range(0, len(raw), chunk):
        parser.feed(raw[start : start + chunk])
    return parser.close().text


@pytest.mark.parametrize(
    ("encoding", "raw"),
    [
        pytest.param("big5", b"<p>\x87\x40\x88\x62\x98\x40", id="big5-combination-and-astral"),
        pytest.param("euc-kr", b"<p>\x81\x41\xb0\xa1", id="euc-kr"),
        pytest.param("shift_jis", b"<p>\x81\x60\x80", id="shift_jis"),
        pytest.param("euc-jp", b"<p>\xa1\xc1\x8f\xa1\xa1", id="euc-jp-jis0212"),
        pytest.param("gb18030", b"<p>\x80\x81\x30\x81\x30\xa3\xa0", id="gb18030-four-byte"),
        pytest.param("iso-2022-jp", b"<p>\x1b(I\x21\x1b(B", id="iso-2022-jp-katakana"),
        pytest.param("x-user-defined", b"<p>\x80\xff", id="x-user-defined"),
        pytest.param("koi8-u", b"<p>\xae\xbe", id="koi8-u"),
        pytest.param("utf-8", "<p>日本語".encode(), id="utf-8"),
        pytest.param("utf-16le", "<p>日本語".encode("utf-16-le"), id="utf-16le"),
        pytest.param("utf-16be", "<p>日本語".encode("utf-16-be"), id="utf-16be"),
    ],
)
@pytest.mark.parametrize("chunk", [pytest.param(1, id="one-byte"), pytest.param(2, id="two-byte")])
def test_a_sequence_split_across_chunks_decodes_as_one_piece(encoding: str, raw: bytes, chunk: int) -> None:
    # the decoder holds back the trailing bytes of a sequence the next chunk may complete, and its ISO-2022-JP mode
    # survives the boundary even though its bytes do not
    assert _streamed(raw, encoding, chunk) == parse(raw, encoding=encoding).text


def test_a_label_alias_reports_its_canonical_name() -> None:
    parser = IncrementalParser(encoding="iso-8859-1")
    parser.feed(b"caf\xe9")
    assert parser.close().encoding == "windows-1252"


def test_an_unsupported_label_raises_on_the_first_bytes_chunk() -> None:
    parser = IncrementalParser(encoding="latin-1")  # a CPython alias the spec does not name
    with pytest.raises(LookupError, match="unknown encoding: latin-1"):
        parser.feed(b"x")


def test_the_replacement_encoding_yields_one_replacement_char_for_the_whole_stream() -> None:
    parser = IncrementalParser(encoding="hz-gb-2312")
    parser.feed(b"<p>abc")
    parser.feed(b"def")
    assert parser.close().text == "�"


def test_an_escape_that_ends_a_chunk_keeps_the_mode_it_interrupted() -> None:
    # 0x1B abandons the pair 0x24 opened, and lands the decoder in the escape state with nothing left in the chunk. The
    # end-of-stream probe that finds the escape incomplete falls back to the output state, so the next chunk has to
    # resume in the escape state rather than read 0x38 as a lead byte.
    raw = b"\x1b$B$\x1b8M"
    parser = IncrementalParser(encoding="iso-2022-jp")
    parser.feed(raw[:5])
    parser.feed(raw[5:])
    assert parser.close().text == "��戸"


def test_a_chunk_past_the_scratch_capacity_grows_it_geometrically() -> None:
    # the held-back tail makes each chunk a byte or two longer than the last, so the decode scratch grows by doubling
    # rather than reallocating on every feed; drive the first growth from nothing and a second from a live buffer
    parser = IncrementalParser(encoding="shift_jis")
    first, second = "あ" * 100, "い" * 500
    parser.feed(first.encode("shift_jis"))
    parser.feed(second.encode("shift_jis"))
    assert parser.close().text == first + second


def test_feed_after_close_raises() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    parser.close()
    with pytest.raises(ValueError, match="closed"):
        parser.feed("<p>more</p>")


def test_double_close_raises() -> None:
    parser = IncrementalParser()
    parser.feed("<p>x</p>")
    parser.close()
    with pytest.raises(ValueError, match="already closed"):
        parser.close()


def test_feed_rejects_other_types() -> None:
    parser = IncrementalParser()
    with pytest.raises(TypeError, match="str or a bytes-like object"):
        parser.feed(123)  # ty: ignore[invalid-argument-type]


def test_encoding_must_be_str() -> None:
    with pytest.raises(TypeError):
        IncrementalParser(encoding=b"utf-8")  # ty: ignore[invalid-argument-type]


def test_context_manager_closes_inside_block() -> None:
    with IncrementalParser() as parser:
        assert isinstance(parser, IncrementalParser)
        parser.feed("<p>hi</p>")
        document = parser.close()  # exit then sees a spent parser and does nothing
    paragraph = document.find("p")
    assert paragraph is not None
    assert paragraph.text == "hi"


def test_context_manager_discards_abandoned_parse() -> None:
    with IncrementalParser() as parser:
        parser.feed("<p>partial")
        # leaving the block without close() releases the in-progress C stream
    with pytest.raises(ValueError, match="closed"):
        parser.feed("<p>x</p>")


def test_parser_discarded_without_close() -> None:
    parser = IncrementalParser()
    parser.feed("<div><p>unfinished")
    del parser  # dealloc must free the in-progress stream without a close()


def test_default_encoding_is_utf8() -> None:
    parser = IncrementalParser()
    parser.feed("é".encode())  # decoded via the default utf-8 label
    assert parser.close().encoding == "UTF-8"


def _roundtrip(node: Node) -> Node:
    return pickle.loads(pickle.dumps(node))  # ruff:ignore[suspicious-pickle-usage]  # our own trusted payload


@pytest.mark.parametrize(
    "node",
    [
        Text("a & b <ok>"),
        Comment("a note"),
        CData("x < y & z"),
        ProcessingInstruction("xml-stylesheet", 'href="a.css"'),
        ProcessingInstruction("php", ""),
    ],
    ids=["text", "comment", "cdata", "pi", "pi-empty"],
)
def test_leaf_nodes_round_trip(node: Text | Comment | CData | ProcessingInstruction) -> None:
    clone = _roundtrip(node)
    assert clone is not node
    assert type(clone) is type(node)
    assert clone.html == node.html


def test_element_with_attributes_and_children_round_trips() -> None:
    element = Element("div", {"class": ["card", "lg"], "id": "x", "hidden": None})
    heading = Element("h2")
    heading.text = "Title"
    element.append(heading)
    element.append(Text("tail"))
    clone = _roundtrip(element)
    assert clone.html == '<div class="card lg" id="x" hidden=""><h2>Title</h2>tail</div>'


def test_parser_produced_invalid_tag_name_round_trips() -> None:
    # the tokenizer keeps a '<' in a tag name ("a<b" from "<a<b>"), a name Element() rejects; pickle
    # reconstruction must rebuild it through the trusted builder, not the validating constructor (issue #83).
    element = next(node for node in parse("<a<b>x").descendants if isinstance(node, Element) and "<" in node.tag)
    assert element.tag == "a<b"
    clone = _roundtrip(element)
    assert isinstance(clone, Element)
    assert clone.tag == "a<b"
    assert clone.html == element.html


def test_nested_pi_and_cdata_survive_pickling() -> None:
    # serialize-and-reparse would fold these; pickling carries the child list
    host = Element("host")
    host.append(ProcessingInstruction("t", "d"))
    host.append(CData("raw"))
    assert _roundtrip(host).html == "<host><?t d><![CDATA[raw]]></host>"


@pytest.mark.parametrize("tag", ["style", "xmp", "iframe"])
def test_rawtext_element_keeps_literal_text_across_pickle(tag: str) -> None:
    # a raw-text element serializes its text verbatim; the round-trip must not start escaping it (issue #86)
    element = parse(f"<{tag}>x < y</{tag}>").find(tag)
    assert isinstance(element, Element)
    assert element.html == f"<{tag}>x < y</{tag}>"
    assert _roundtrip(element).html == element.html


def test_document_round_trips() -> None:
    doc = parse("<!DOCTYPE html><title>Hi</title><p id=a>x</p><!--c-->")
    clone = _roundtrip(doc)
    assert isinstance(clone, Document)
    assert clone.html == doc.html


def test_xml_document_round_trips_case_sensitively() -> None:
    doc = parse_xml('<Root Attr="v"><Child X="1"/><![CDATA[r]]><?pi d?></Root>')
    clone = _roundtrip(doc)
    assert isinstance(clone, Document)
    root = clone.root
    assert isinstance(root, Element)
    assert root.attrs["Attr"] == "v"
    assert "attr" not in root.attrs
    assert root.html == '<Root Attr="v"><Child X="1"></Child><![CDATA[r]]><?pi d></Root>'


def test_xml_element_subtree_round_trips_case_sensitively() -> None:
    root = parse_xml('<Root><Child X="1"><Gc Y="2"/></Child></Root>').root
    assert isinstance(root, Element)
    clone = _roundtrip(root.children[0])
    assert isinstance(clone, Element)
    assert clone.tag == "Child"
    assert clone.attrs["X"] == "1"
    assert "x" not in clone.attrs
    grandchild = clone.children[0]
    assert isinstance(grandchild, Element)
    assert grandchild.tag == "Gc"
    assert grandchild.attrs["Y"] == "2"


@pytest.mark.parametrize(
    ("markup", "public_id", "system_id"),
    [
        pytest.param("<!DOCTYPE html>", None, None, id="name-only"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">',
            "-//W3C//DTD HTML 4.01//EN",
            "http://www.w3.org/TR/html4/strict.dtd",
            id="public-and-system",
        ),
        # a missing sibling identifier must stay None across the round-trip, not collapse to ""
        pytest.param('<!DOCTYPE html PUBLIC "p">', "p", None, id="public-only-missing-system"),
        pytest.param('<!DOCTYPE html SYSTEM "s">', None, "s", id="system-only-missing-public"),
        pytest.param('<!DOCTYPE html PUBLIC "p" "">', "p", "", id="public-and-empty-system"),
        # an embedded quote in a single-quoted identifier must survive the round-trip (part of #478)
        pytest.param("<!DOCTYPE html PUBLIC 'pub\"lic' 'sys\"tem'>", 'pub"lic', 'sys"tem', id="embedded-quotes"),
    ],
)
def test_doctype_round_trips(markup: str, public_id: str | None, system_id: str | None) -> None:
    doctype = parse(markup).children[0]
    assert isinstance(doctype, Doctype)
    clone = _roundtrip(doctype)
    assert isinstance(clone, Doctype)
    assert clone.name == "html"
    assert clone.public_id == public_id
    assert clone.system_id == system_id


def test_pickled_element_is_independent() -> None:
    element = Element("p")
    element.append(Text("x"))
    clone = _roundtrip(element)
    assert isinstance(clone, Element)
    clone.append(Element("b"))  # editing the clone must not touch the original
    assert element.html == "<p>x</p>"
    assert clone.html == "<p>x<b></b></p>"


@pytest.mark.parametrize(
    ("html", "container"),
    [
        pytest.param("<svg><rect></rect></svg>", "svg", id="svg"),
        pytest.param("<math><mi>x</mi></math>", "math", id="mathml"),
    ],
)
def test_foreign_element_round_trip_keeps_namespace(html: str, container: str) -> None:
    parent = parse(html).find(container)
    assert parent is not None
    child = parent.children[0]
    assert isinstance(child, Element)
    clone = _roundtrip(child)
    assert isinstance(clone, Element)
    assert clone.namespace == child.namespace  # not reset to Namespace.HTML on the round-trip (issue #85)


@pytest.mark.parametrize("ns", [-1, 99], ids=["below", "above"])
def test_reconstruct_rejects_out_of_range_namespace(ns: int) -> None:
    reduced = Element("div").__reduce__()
    assert isinstance(reduced, tuple)
    kind = reduced[1][0]  # the element kind from the (kind, data, children) payload
    # a crafted payload must not index the namespaces table out of bounds
    with pytest.raises(ValueError, match="namespace out of range"):
        _reconstruct(kind, ("div", {}, ns), [])


def test_reconstruct_rejects_malformed_arguments() -> None:
    with pytest.raises(TypeError):
        _reconstruct("not a triple")  # ty: ignore[missing-argument, invalid-argument-type]  # deliberately malformed


def test_reconstruct_accepts_names_the_constructor_rejects() -> None:
    reduced = Element("div").__reduce__()
    assert isinstance(reduced, tuple)
    kind = reduced[1][0]  # the (kind, data, children) payload pickle would store
    # _reconstruct is the trusted pickle hook, so it rebuilds whatever the parser stored,
    # including a tag name like "a<b" that the validating public Element() rejects (issue #83)
    node = _reconstruct(kind, ("a<b", {}), [])
    assert isinstance(node, Element)
    assert node.tag == "a<b"


def test_reconstruct_propagates_a_construction_failure() -> None:
    reduced = Element("div").__reduce__()
    kind = reduced[1][0]  # the (kind, data, children) payload pickle would store
    # a genuinely broken payload still fails: a non-mapping attrs value cannot build an element,
    # and reconstruction surfaces that TypeError instead of returning a half-built node
    with pytest.raises(TypeError):
        _reconstruct(kind, ("div", 123), [])  # ty: ignore[invalid-argument-type]  # malformed attrs payload


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


def _tree_parse_errors_streamed(markup: str, chunk: int = 1) -> list[tuple[str, int, int]]:
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
        pytest.param("<p>\x00</p>名\r\n\x01😀\ud800<a b b>", id="errors-after-nul-with-width-changes"),
    ],
)
def test_a_streamed_document_reports_the_errors_parse_reports(markup: str) -> None:
    # the streaming tokenizer collects into the tree's sink, and each chunk is swept for the preprocessing errors the
    # states never see, so one byte at a time yields exactly what the whole buffer does, positions included
    expected = [(error.code, error.line, error.col) for error in parse(markup).errors]
    assert _tree_parse_errors_streamed(markup) == expected
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
    assert _tree_parse_errors_streamed("<a\x01") == [("control-character-in-input-stream", 1, 2), ("eof-in-tag", 1, 3)]


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


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("a", id="ascii"),
        pytest.param("é", id="latin1"),
        pytest.param("名", id="ucs2"),
        pytest.param("😀", id="ucs4"),
    ],
)
@pytest.mark.parametrize("locations", [pytest.param(False, id="no-locations"), pytest.param(True, id="locations")])
def test_parser_text_coalescing_keeps_one_node_across_feeds(text: str, *, locations: bool) -> None:
    parser: Final = IncrementalParser(source_locations=locations)
    parser.feed("<p>")
    for _ in range(129):
        parser.feed(text)
    parser.feed("</p>")
    paragraph: Final = parser.close().find("p")
    assert paragraph is not None
    assert [node.text for node in paragraph.children] == [text * 129]


def test_parser_text_coalescing_switches_between_nodes() -> None:
    source: Final = "<p>" + "x</missing>" * 40 + "<i>" + "y</missing>" * 40 + "</i>" + "z</missing>" * 40 + "</p>"
    paragraph: Final = parse(source).find("p")
    assert paragraph is not None
    assert [node.text for node in paragraph.children] == ["x" * 40, "y" * 40, "z" * 40]


def test_parser_text_coalescing_keeps_fostered_text_before_table() -> None:
    root: Final = parse("<div><table>" + "x<tr><td>cell</td></tr>" * 40 + "</table></div>").find("div")
    assert root is not None
    assert [node.text for node in root.children] == ["x" * 40, "cell" * 40]


def test_independent_parsers_in_parallel_each_build_correctly() -> None:
    document: Final = "<html><body>" + "".join(f"<div><p>x{index}</p></div>" for index in range(200)) + "</body></html>"
    expected: Final = parse(document).html
    results: Final[list[str]] = []
    lock: Final = threading.Lock()
    start: Final = threading.Barrier(4)

    def worker() -> None:
        start.wait()
        parser: Final = IncrementalParser()
        for position in range(0, len(document), 4):
            parser.feed(document[position : position + 4])
        with lock:
            results.append(parser.close().html)

    threads: Final = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [expected, expected, expected, expected]


def test_concurrent_feeds_on_one_parser_are_memory_safe() -> None:
    parser: Final = IncrementalParser()
    start: Final = threading.Barrier(2)

    def feeder(tag: str) -> None:
        start.wait()
        for index in range(200):
            parser.feed(f"<{tag}>{index}</{tag}>")

    threads: Final = [threading.Thread(target=feeder, args=(tag,)) for tag in ("p", "span")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert isinstance(parser.close().html, str)  # interleaving is undefined, but never a crash
