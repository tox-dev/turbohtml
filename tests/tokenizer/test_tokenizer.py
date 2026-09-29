from __future__ import annotations

import gc
import json
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

import pytest

from turbohtml import Token, Tokenizer, TokenType, _html, tokenize

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from _pytest.mark.structures import ParameterSet


def _shape(token: Token) -> tuple[object, ...]:
    return (token.type, token.tag, token.target, token.data, token.attrs, token.self_closing)


def assert_streaming_matches_whole(document: str) -> None:
    """Feeding one character at a time must match one-shot tokenization."""
    tokenizer = Tokenizer()
    streamed = [token for char in document for token in tokenizer.feed(char)]
    streamed += list(tokenizer.close())
    whole = list(tokenize(document))
    assert [_shape(token) for token in streamed] == [_shape(token) for token in whole]
    assert [(token.line, token.col) for token in streamed] == [(token.line, token.col) for token in whole]


def test_tokenize_simple_document() -> None:
    head, start, body, end = list(tokenize('a<p class="x">b</p>'))
    assert _shape(head) == (TokenType.TEXT, None, None, "a", None, False)
    assert _shape(start) == (TokenType.START_TAG, "p", None, None, [("class", "x")], False)
    assert _shape(body) == (TokenType.TEXT, None, None, "b", None, False)
    assert _shape(end) == (TokenType.END_TAG, "p", None, None, [], False)


def test_tokenize_empty_input() -> None:
    assert list(tokenize("")) == []


def test_comment_token() -> None:
    (comment,) = list(tokenize("<!-- hi -->"))
    assert comment.type is TokenType.COMMENT
    assert comment.data == " hi "
    assert comment.tag is None
    assert comment.attrs is None


@pytest.mark.parametrize(
    ("document", "target", "data"),
    [
        pytest.param("<?pi>", "pi", "", id="greater-than-close"),
        pytest.param("<?pi?>", "pi", "", id="question-mark-close"),
        pytest.param("<?pi   value?>", "pi", "value", id="leading-data-space-collapses"),
        pytest.param("<?pi value?part>", "pi", "value?part", id="question-mark-data"),
        pytest.param("<?module-handler data>", "module-handler", "data", id="target-punctuation"),
        pytest.param("<?_private1 data>", "_private1", "data", id="underscore-and-digit-target"),
    ],
)
def test_processing_instruction_token(document: str, target: str, data: str, width_prefix: str) -> None:
    token = list(tokenize(width_prefix + document, capture_source=True))[-1]
    assert (token.type, token.target, token.data, token.source, token.tag) == (
        TokenType.PROCESSING_INSTRUCTION,
        target,
        data,
        document,
        None,
    )


@pytest.mark.parametrize(
    ("document", "data"),
    [
        pytest.param("<?xml?>", "?xml?", id="xml"),
        pytest.param("<?XML-stylesheet href=x?>", "?XML-stylesheet href=x?", id="xml-stylesheet-case-insensitive"),
        pytest.param("<?1bad>", "?1bad", id="bad-first-character"),
        pytest.param("<?bad.target>", "?bad.target", id="bad-target-character"),
    ],
)
def test_invalid_processing_instruction_is_a_comment(document: str, data: str, width_prefix: str) -> None:
    token = list(tokenize(width_prefix + document))[-1]
    assert (token.type, token.data, token.target) == (TokenType.COMMENT, data, None)


def test_processing_instruction_target_space_at_eof_is_dropped(width_prefix: str) -> None:
    assert [token.data for token in tokenize(width_prefix + "<?pi ")] == ([width_prefix] if width_prefix else [])


# each expected tuple is (type, name, public_id, system_id, force_quirks)
@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            "<!DOCTYPE HTML PUBLIC \"pub\" 'sys'>", (TokenType.DOCTYPE, "html", "pub", "sys", False), id="full"
        ),
        # a nameless DOCTYPE name is "missing", a distinct state from the empty string, so it is None
        pytest.param("<!DOCTYPE>", (TokenType.DOCTYPE, None, None, None, True), id="bare"),
        pytest.param("<!DOCTYPE >", (TokenType.DOCTYPE, None, None, None, True), id="bare-trailing-space"),
        # a non-doctype token still exposes the doctype fields, all empty
        pytest.param("<p>", (TokenType.START_TAG, None, None, None, False), id="non-doctype"),
    ],
)
def test_doctype_fields(document: str, expected: tuple[object, ...]) -> None:
    (token,) = list(tokenize(document))
    assert (token.type, token.name, token.public_id, token.system_id, token.force_quirks) == expected
    assert token.data is None


def test_self_closing_tag() -> None:
    (tag,) = list(tokenize("<br/>"))
    assert tag.self_closing is True


@pytest.mark.parametrize(
    ("document", "tag_name", "attrs"),
    [
        pytest.param("<a x=1 y x=2 z=''>", "a", [("x", "1"), ("y", ""), ("z", "")], id="duplicates-keep-first"),
        pytest.param("<a xy=1 xő=2>", "a", [("xy", "1"), ("xő", "2")], id="same-length-mixed-width-names"),
        pytest.param("<ab xyz=ő q=🎉>", "ab", [("xyz", "ő"), ("q", "🎉")], id="wide-buffer-after-narrow"),
    ],
)
def test_tag_attrs(document: str, tag_name: str, attrs: list[tuple[str, str | None]]) -> None:
    (tag,) = list(tokenize(document))
    assert tag.tag == tag_name
    assert tag.attrs == attrs


@pytest.mark.parametrize("mode", ["whole", "streaming"])
def test_capture_attributes_can_be_disabled(mode: str) -> None:
    document = '<a href="a&amp;b" download>text</a>'
    if mode == "streaming":
        tokenizer = Tokenizer(capture_attributes=False)
        tokens = [token for char in document for token in tokenizer.feed(char)]
        tokens += list(tokenizer.close())
    else:
        tokens = list(tokenize(document, capture_attributes=False))

    start, text, end = tokens
    assert (start.tag, start.attrs, start.attr("href")) == ("a", [], None)
    assert (text.data, end.tag) == ("text", "a")


@pytest.mark.parametrize(
    "text",
    [pytest.param("x", id="ascii"), pytest.param("ő", id="ucs2"), pytest.param("🎉", id="ucs4")],
)
def test_capture_attributes_disabled_handles_every_value_form(text: str) -> None:
    document = f"<a =bad dq=\"a&ouml;\0\" sq='b&ouml;\0' uq=c&ouml;\0>{text}</a>"
    start, content, end = tokenize(document, capture_attributes=False)
    assert (start.attrs, content.data, end.tag) == ([], text, "a")


def test_non_latin1_tag_name() -> None:
    tokens = list(tokenize("<xmő>x</xmő>"))
    assert [token.tag for token in tokens if token.tag] == ["xmő", "xmő"]


def test_attr_lookup() -> None:
    (tag,) = list(tokenize("<a href='u' download>"))
    assert tag.attr("href") == "u"
    assert tag.attr("download") == ""  # ruff:ignore[compare-to-empty-string]  # exactly "" (valueless), distinct from None (missing)
    assert tag.attr("missing") is None  # a missing attribute yields the default
    assert tag.attr("missing", "fallback") == "fallback"
    assert tag.attr("href2", "fallback") == "fallback"
    assert tag.attr("hrex", "fallback") == "fallback"


def test_attr_on_non_tag_returns_default() -> None:
    (text,) = list(tokenize("plain"))
    assert text.attr("x", "fallback") == "fallback"


def test_attr_name_must_be_str() -> None:
    # a str-typed name rejects bytes (no silent latin-1 match) and other wrong types, naming str
    (tag,) = list(tokenize("<a href=x>"))
    with pytest.raises(TypeError):
        tag.attr(b"href")  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        tag.attr(123)  # ty: ignore[invalid-argument-type]
    with pytest.raises(UnicodeEncodeError):
        tag.attr("\udfff")  # a lone surrogate has no UTF-8 form


def test_token_repr() -> None:
    tokens = list(tokenize("a<p x=1></p><!--c--><!DOCTYPE html><?pi data?>"))
    assert [repr(token) for token in tokens] == [
        "Token(TEXT, data='a')",
        "Token(START_TAG, tag='p')",
        "Token(END_TAG, tag='p')",
        "Token(COMMENT, data='c')",
        "Token(DOCTYPE, name='html')",
        "Token(PROCESSING_INSTRUCTION, target='pi', data='data')",
    ]


def test_token_type_enum() -> None:
    assert [member.value for member in TokenType] == [0, 1, 2, 3, 4, 5, 6]
    assert TokenType.TEXT == 0
    assert TokenType.CHARACTER_REFERENCE == 5
    assert TokenType.PROCESSING_INSTRUCTION == 6
    assert isinstance(TokenType.START_TAG, int)


def test_token_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Token()


def test_tokenizer_takes_no_arguments() -> None:
    # a streaming tokenizer starts empty; a constructor argument is an error, not a silent no-op
    with pytest.raises(TypeError):
        Tokenizer("<p>")  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(TypeError):
        Tokenizer(source="<p>")  # ty: ignore[unknown-argument]


def test_tokens_stay_valid_after_iteration() -> None:
    tokens = list(tokenize("<a x=1><b y=2>"))
    assert [token.tag for token in tokens] == ["a", "b"]
    assert tokens[0].attrs == [("x", "1")]


def test_many_attributes() -> None:
    names = [f"a{index}" for index in range(9)]
    (tag,) = list(tokenize(f"<p {' '.join(f'{name}={index}' for index, name in enumerate(names))}>"))
    assert tag.attrs == [(name, str(index)) for index, name in enumerate(names)]


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param("<script>a<b && c</script>d", ["a<b && c", "d"], id="script"),
        pytest.param("<script><!--<script></script>--></script>", ["<!--<script></script>-->"], id="script-escaped"),
        pytest.param("<title>a<b &amp; c</title>d", ["a<b & c", "d"], id="title-rcdata"),
        pytest.param("<textarea></div></textarea>", ["</div>"], id="textarea-rcdata"),
        pytest.param("<style>a<b &amp; c</style>d", ["a<b &amp; c", "d"], id="style-rawtext"),
        pytest.param("<xmp><div></xmp>", ["<div>"], id="xmp"),
        pytest.param("<iframe><p></iframe>", ["<p>"], id="iframe"),
        pytest.param("<noembed><p></noembed>", ["<p>"], id="noembed"),
        pytest.param("<noframes><p></noframes>", ["<p>"], id="noframes"),
        pytest.param("<noscript><p></noscript>", ["<p>"], id="noscript"),
    ],
)
def test_content_model_switching(document: str, expected: list[str]) -> None:
    texts = [token.data for token in tokenize(document) if token.type is TokenType.TEXT]
    assert texts == expected


@pytest.mark.parametrize(
    ("document", "kinds"),
    [
        pytest.param(
            "<script>a</script >x", [TokenType.START_TAG, TokenType.TEXT, TokenType.END_TAG, TokenType.TEXT], id="space"
        ),
        pytest.param(
            "<script>a</script/>x",
            [TokenType.START_TAG, TokenType.TEXT, TokenType.END_TAG, TokenType.TEXT],
            id="self-closing",
        ),
        pytest.param(
            "<script><!--a</script >x",
            [TokenType.START_TAG, TokenType.TEXT, TokenType.END_TAG, TokenType.TEXT],
            id="escaped-space",
        ),
        pytest.param(
            "<script><!--a</script/>x",
            [TokenType.START_TAG, TokenType.TEXT, TokenType.END_TAG, TokenType.TEXT],
            id="escaped-self-closing",
        ),
        pytest.param(
            "<script><!--a</script>x",
            [TokenType.START_TAG, TokenType.TEXT, TokenType.END_TAG, TokenType.TEXT],
            id="escaped-close",
        ),
    ],
)
def test_script_end_tag_variants(document: str, kinds: list[TokenType], width_prefix: str) -> None:
    lead = [TokenType.TEXT] if width_prefix else []
    assert [token.type for token in tokenize(width_prefix + document)] == lead + kinds


@pytest.mark.parametrize(
    ("document", "public_id", "system_id"),
    [
        pytest.param("<!DOCTYPE html PUBLIC>", None, None, id="public-then-gt"),
        pytest.param("<!DOCTYPE html SYSTEM>", None, None, id="system-then-gt"),
        pytest.param("<!DOCTYPE html PUBLIC 'p'>", "p", None, id="public-only"),
        pytest.param("<!DOCTYPE html SYSTEM 's'>", None, "s", id="system-only"),
        pytest.param("<!DOCTYPE html PUBLIC >", None, None, id="public-space-gt"),
        pytest.param("<!DOCTYPE html PUBLIC x>", None, None, id="public-bogus"),
        pytest.param("<!DOCTYPE html PUBLIC  'p'>", "p", None, id="public-double-space"),
        pytest.param("<!DOCTYPE html PUBLIC 'p' >", "p", None, id="between-space-gt"),
        pytest.param("<!DOCTYPE html PUBLIC 'p'  's'>", "p", "s", id="between-double-space"),
        pytest.param("<!DOCTYPE html SYSTEM  's'>", None, "s", id="system-double-space"),
        pytest.param("<!DOCTYPE html SYSTEM >", None, None, id="system-space-gt"),
    ],
)
def test_doctype_identifier_edge_cases(
    document: str, public_id: str | None, system_id: str | None, width_prefix: str
) -> None:
    doctype = list(tokenize(width_prefix + document))[-1]
    assert doctype.public_id == public_id
    assert doctype.system_id == system_id


def test_large_text_runs_move_intact() -> None:
    first, second = "&amp;" + "a" * 600, "&gt;" + "b" * 700
    tokens = list(tokenize(f"{first}<p>{second}"))
    expected = ["&" + "a" * 600, ">" + "b" * 700]
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == expected


def test_tokens_outlive_the_source_string() -> None:
    text = "y" * 700 + "<p>"
    iterator = tokenize(text)
    del text
    gc.collect()
    tokens = list(iterator)
    del iterator
    gc.collect()
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == ["y" * 700]


def test_plaintext_consumes_rest() -> None:
    tokens = list(tokenize("<plaintext></plaintext><p>"))
    assert [token.type for token in tokens] == [TokenType.START_TAG, TokenType.TEXT]
    assert tokens[1].data == "</plaintext><p>"


def test_streaming_buffers_until_complete() -> None:
    tokenizer = Tokenizer()
    assert list(tokenizer.feed("a<di")) == []
    chunk = list(tokenizer.feed("v>b"))
    assert [token.type for token in chunk] == [TokenType.TEXT, TokenType.START_TAG]
    final = list(tokenizer.close())
    assert [token.data for token in final] == ["b"]


def test_streaming_charref_suspends() -> None:
    tokenizer = Tokenizer()
    assert list(tokenizer.feed("x&am")) == []
    assert list(tokenizer.feed("p;y")) == []
    assert [token.data for token in tokenizer.close()] == ["x&y"]


def test_streaming_crlf_across_feeds() -> None:
    tokenizer = Tokenizer()
    list(tokenizer.feed("a\r"))
    list(tokenizer.feed(""))
    list(tokenizer.feed("\nb"))
    assert [token.data for token in tokenizer.close()] == ["a\nb"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("\ra", ["\na"], id="lone-cr"),
        pytest.param("\r\na", ["\na"], id="crlf"),
        pytest.param("\r\U0001f600", ["\n\U0001f600"], id="ucs4-lone-cr"),
        pytest.param(
            '\r-><a hre="p?q=1&r=" 4itle="he said"&qot;hi&qot;">link</a>\n',
            ["\n->", "link", "\n"],
            id="fuzz-reproducer",
        ),
    ],
)
def test_leading_carriage_return_into_an_empty_input(text: str, expected: list[str]) -> None:
    # nothing precedes the '\r', so the first block appended has no code points and the input buffer is still
    # unallocated; the append must return before it forms a pointer into NULL (the deep-fuzz UBSan finding)
    assert [token.data for token in tokenize(text) if token.type is TokenType.TEXT] == expected


def test_feed_after_close_is_rejected() -> None:
    tokenizer = Tokenizer()
    list(tokenizer.feed("<p>"))
    list(tokenizer.close())
    with pytest.raises(ValueError, match="closed Tokenizer"):
        tokenizer.feed("<b>")


def test_reset_reopens_a_closed_tokenizer() -> None:
    tokenizer = Tokenizer()
    list(tokenizer.close())
    tokenizer.reset()
    assert [token.type for token in tokenizer.feed("<b>")] == [TokenType.START_TAG]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("a\rb", "a\nb", id="lone-cr"),
        pytest.param("a\r\nb", "a\nb", id="crlf"),
        pytest.param("a\r\r\nb", "a\n\nb", id="cr-then-crlf"),
        pytest.param("ő\r\nő\rz", "ő\nő\nz", id="crlf-ucs2"),
        pytest.param("\U0001f600\r\n\U0001f600\rz", "\U0001f600\n\U0001f600\nz", id="crlf-ucs4"),
        pytest.param("ő\nő", "ő\nő", id="bare-lf-ucs2"),
        pytest.param("\U0001f600\n\U0001f600", "\U0001f600\n\U0001f600", id="bare-lf-ucs4"),
        pytest.param("a<div", "a", id="dropped-tag-at-eof-flushes-text"),
        pytest.param("a<", "a<", id="lt-at-eof-is-text"),
    ],
)
def test_single_text_token(text: str, expected: str) -> None:
    assert [token.data for token in tokenize(text)] == [expected]


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("<!-- comment --><!-", id="comment-partial"),
        pytest.param("<!doctype html public 'p' 's'><!doctype html system 'x'>", id="doctype-keywords"),
        pytest.param("&amp; &am &#x41; &#65; &#xZ &# &cent", id="charrefs"),
        pytest.param(f"&{'a' * 40};", id="charref-name-over-cap"),
        pytest.param("<div class='a' id=b checked>x</div>", id="attributes"),
        pytest.param("<a x='&amp;' y=\"&gt;\" z=&lt; w=&cent>", id="attribute-charrefs"),
        pytest.param("<a x=&notit y=&ampz> &0 &#", id="charref-prefix-and-digit"),
        pytest.param("<script>a<b</script>c", id="script"),
        pytest.param("<title>&notin;</title>", id="rcdata-charref"),
        pytest.param("a\r\nb\rc", id="newlines"),
        pytest.param("a<p>ő x=🎉>b", id="widening-midstream"),
        pytest.param("<![CDATA[x]]>", id="cdata-bogus"),
        pytest.param("</> <?bogus> text", id="bogus-comments"),
        pytest.param("<?pi data?more>", id="processing-instruction"),
    ],
)
def test_feed_char_by_char_matches_whole(document: str, width_prefix: str) -> None:
    assert_streaming_matches_whole(width_prefix + document)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("</", id="end-tag-open"),
        pytest.param("<title>x</title", id="rcdata-end-name"),
        pytest.param("<title>x</", id="rcdata-end-open"),
        pytest.param("<style>x</", id="rawtext-end-open"),
        pytest.param("<textarea>x<", id="rcdata-lt"),
        pytest.param("<style>x<", id="rawtext-lt"),
        pytest.param("<style>x</sty", id="rawtext-end-name"),
        pytest.param("<script>x<", id="script-lt"),
        pytest.param("<script>x</", id="script-end-open"),
        pytest.param("<script>x</scri", id="script-end-name"),
        pytest.param("<script>x<!", id="script-bang"),
        pytest.param("<script><!-", id="escape-start"),
        pytest.param("<script><!--", id="escape-start-dash"),
        pytest.param("<script><!--x-", id="escaped-dash"),
        pytest.param("<script><!--<", id="escaped-lt"),
        pytest.param("<script><!--</", id="escaped-end-open"),
        pytest.param("<script><!--</scri", id="escaped-end-name"),
        pytest.param("<script><!--<scr", id="double-escape-start"),
        pytest.param("<script><!--<script>x<", id="double-escaped-lt"),
        pytest.param("<script><!--<script>x-", id="double-escaped-dash"),
        pytest.param("<script><!--<script></scr", id="double-escape-end"),
        pytest.param("<!--x<!", id="comment-lt-bang"),
        pytest.param("<!--x<!-", id="comment-lt-bang-dash"),
        pytest.param("<a x=&amp", id="attr-charref"),
        pytest.param("<?", id="processing-instruction-open"),
        pytest.param("<?pi", id="processing-instruction-target"),
        pytest.param("<?pi data", id="processing-instruction-data"),
        pytest.param("<?pi data?", id="processing-instruction-questionable"),
        pytest.param("<script><!--</scrip >x", id="escaped-end-inappropriate-space"),
        pytest.param("<script><!--</scrip/>x", id="escaped-end-inappropriate-slash"),
        pytest.param("<script><!--<scr ipt>x", id="double-escape-start-space"),
        pytest.param("<script><!--<script/>x-->", id="double-escape-start-slash"),
        pytest.param("<script><!--<script></script ->x", id="double-escape-end-space"),
        pytest.param("<script><!--<script></script/->x", id="double-escape-end-slash"),
        # a six-character buffer that is not "script" takes the other side of each comparison
        pytest.param("<script><!--<scripx >x", id="double-escape-start-near-miss"),
        pytest.param("<script><!--<script></scripx >x", id="double-escape-end-near-miss"),
    ],
)
def test_eof_mid_construct_matches_streaming(document: str, width_prefix: str) -> None:
    assert_streaming_matches_whole(width_prefix + document)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("x]", "x]", id="bracket-eof"),
        pytest.param("x]]", "x]]", id="double-bracket-eof"),
        pytest.param("x]y", "x]y", id="bracket-text"),
        pytest.param("x]]]>y", "x]y", id="bracket-run-close"),
        pytest.param("x]]y", "x]]y", id="double-bracket-text"),
    ],
)
def test_cdata_section_edges(text: str, expected: str, storage_kind: int) -> None:
    tokens, _ = _html._tokenize_states(text, "CDATA section state", None, storage_kind)
    assert tokens == [("Character", expected)]


@pytest.mark.parametrize(
    ("state", "text", "expected"),
    [
        pytest.param("PLAINTEXT state", "a\nb", "a\nb", id="plaintext-newline"),
        pytest.param("PLAINTEXT state", "a\x00b", "a�b", id="plaintext-nul"),
        pytest.param("RCDATA state", "a\x00b", "a�b", id="rcdata-nul"),
        pytest.param("RCDATA state", "a\nb", "a\nb", id="rcdata-newline"),
        pytest.param("RAWTEXT state", "a\x00b", "a�b", id="rawtext-nul"),
        pytest.param("RAWTEXT state", "a\nb", "a\nb", id="rawtext-newline"),
        pytest.param("Script data state", "a\x00b", "a�b", id="script-nul"),
        pytest.param("Script data state", "a\nb", "a\nb", id="script-newline"),
        pytest.param("CDATA section state", "a\nb]", "a\nb]", id="cdata-newline"),
    ],
)
def test_text_run_breaks_on_special_characters(state: str, text: str, expected: str, storage_kind: int) -> None:
    tokens, _ = _html._tokenize_states(text, state, None, storage_kind)
    assert tokens == [("Character", expected)]


def test_reset_discards_state() -> None:
    tokenizer = Tokenizer()
    list(tokenizer.feed("<div><scr"))
    tokenizer.reset()
    tokens = list(tokenizer.feed("<p>"))
    list(tokenizer.close())
    assert [token.tag for token in tokens] == ["p"]


def test_context_manager_signals_eof() -> None:
    with Tokenizer() as tokenizer:
        assert isinstance(tokenizer, Tokenizer)
        assert [token.tag for token in tokenizer.feed("<p>x")] == ["p"]
    assert [token.data for token in tokenizer] == ["x"]


def test_context_manager_closes_on_error() -> None:
    tokenizer = Tokenizer()

    def explode() -> None:
        with tokenizer:
            list(tokenizer.feed("<p>x"))
            msg = "boom"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        explode()
    assert [token.tag or token.data for token in tokenizer] == ["x"]


def test_tokenizer_is_iterable_mid_stream() -> None:
    tokenizer = Tokenizer()
    tokenizer.feed("<p>x<b")
    assert [token.tag for token in tokenizer] == ["p"]
    assert [token.tag or token.data for token in tokenizer.feed(">")] == ["x", "b"]


def test_close_is_terminal() -> None:
    tokenizer = Tokenizer()
    assert [token.tag for token in tokenizer.feed("<p>x")] == ["p"]
    iterator = tokenizer.close()
    assert [token.data for token in iterator] == ["x"]
    assert list(iterator) == []


def test_iterator_is_reusable_across_feeds() -> None:
    tokenizer = Tokenizer()
    iterator = tokenizer.feed("<p>")
    assert iter(iterator) is iterator
    assert next(iterator).tag == "p"
    list(tokenizer.feed("<b>"))
    list(tokenizer.close())


@pytest.mark.parametrize(
    ("document", "positions"),
    [
        pytest.param("ab<p>\ncd</p>", [(1, 0), (1, 2), (1, 5), (2, 2)], id="tags-and-text"),
        pytest.param("<3 x", [(1, 0)], id="lt-as-text"),
        pytest.param("ab<!--c-->", [(1, 0), (1, 2)], id="comment"),
        pytest.param("x\n<!doctype html>", [(1, 0), (2, 0)], id="doctype"),
        pytest.param("<?bogus>x", [(1, 0), (1, 8)], id="bogus-comment"),
        pytest.param("</>x", [(1, 3)], id="dropped-end-tag"),
        pytest.param("<title>a</titl>b</title>", [(1, 0), (1, 7), (1, 16)], id="rcdata-fallback"),
    ],
)
def test_positions(document: str, positions: list[tuple[int, int]]) -> None:
    assert [(token.line, token.col) for token in tokenize(document)] == positions


def test_positions_match_html_parser() -> None:
    document = "head\n<p\nclass='x'>text<!--c-->\n<br/>tail"

    class Recorder(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.positions: list[tuple[int, int]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # ruff:ignore[unused-method-argument]
            self.positions.append(self.getpos())

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # ruff:ignore[unused-method-argument]
            self.positions.append(self.getpos())

        def handle_data(self, data: str) -> None:  # ruff:ignore[unused-method-argument]
            self.positions.append(self.getpos())

        def handle_comment(self, data: str) -> None:  # ruff:ignore[unused-method-argument]
            self.positions.append(self.getpos())

    recorder = Recorder()
    recorder.feed(document)
    recorder.close()
    assert [(token.line, token.col) for token in tokenize(document)] == recorder.positions


def test_garbage_collection_traversal() -> None:
    tokenizer = Tokenizer()
    iterator = tokenizer.feed("<p><b>")
    token = next(iterator)
    gc.collect()
    del tokenizer
    assert token.tag == "p"
    assert [token.tag for token in iterator] == ["b"]


@pytest.mark.parametrize(
    ("call", "exc", "match"),
    [
        pytest.param(lambda: tokenize(123), TypeError, None, id="tokenize-non-str"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: Tokenizer().feed(b"<p>"), TypeError, None, id="feed-non-str"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: next(iter(tokenize("<p>"))).attr(123), TypeError, None, id="attr-non-str-name"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: _html._tokenize_states(123, "Data state"), TypeError, None, id="states-non-str-text"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(
            lambda: _html._tokenize_states("x", "Bogus state", None),
            ValueError,
            "unknown initial state",
            id="states-unknown-state",
        ),
        pytest.param(
            lambda: _html._tokenize_states("x", "Data state", 5),  # ty: ignore[invalid-argument-type]  # non-str on purpose
            TypeError,
            "last_start_tag",
            id="states-non-str-last-tag",
        ),
        pytest.param(
            lambda: _html._tokenize_states("x", "Data state", None, 3),
            ValueError,
            "storage_kind",
            id="states-bad-storage-kind",
        ),
    ],
)
def test_api_rejects_bad_arguments(call: Callable[[], object], exc: type[Exception], match: str | None) -> None:
    with pytest.raises(exc, match=match):
        call()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(("x", "Data state"), "x", id="default-last-tag"),
        pytest.param(("x", "RCDATA state", ""), "x", id="empty-last-tag"),
        pytest.param(("</xy>", "RCDATA state", "xő"), "</xy>", id="rcdata-end-tag-name-width-mismatch"),
    ],
)
def test_tokenize_states_returns_characters(args: tuple[str, ...], expected: str) -> None:
    tokens, _ = _html._tokenize_states(*args)  # ty: ignore[invalid-argument-type]  # variadic str args
    assert tokens == [("Character", expected)]


def test_tag_names_are_lowercased() -> None:
    assert [next(iter(tokenize(document))).tag for document in ("<DIV>", "<SpAn>")] == ["div", "span"]


def test_streaming_tokenizer_reclaims_consumed_input() -> None:
    # feed() compacts the consumed prefix, so a long-lived streaming tokenizer stays memory
    # bounded instead of growing its input buffer with every chunk (issue #80).
    # PyPy ships no _tracemalloc, and its GC frees the buffer on its own schedule, so there is
    # no peak to assert there; the compaction itself is C and covered by the CPython run.
    tracemalloc = pytest.importorskip("tracemalloc")
    tokenizer = Tokenizer()
    for _ in range(200):  # warm up so one-time allocations settle out of the measurement
        list(tokenizer.feed("<p>hello world</p>"))
    tracemalloc.start()
    for _ in range(10_000):
        list(tokenizer.feed("<p>hello world</p>"))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # without compaction the buffer grows to well over 100 KB across this run; with it, it stays tiny
    assert peak < 100_000, f"streaming feed leaked the input buffer: {peak} bytes"


def test_feed_keeps_input_while_a_token_is_queued() -> None:
    # a completed token not yet pulled from the iterator still spans the input buffer, so the
    # next feed must skip compaction; the full stream stays correct either way.
    tokenizer = Tokenizer()
    stream = tokenizer.feed("x<a>")
    first = next(stream)  # drives the state machine, leaving a record queued behind the one returned
    tokenizer.feed("z")
    tokens = [first, *tokenizer, *tokenizer.close()]
    assert [_shape(token) for token in tokens] == [_shape(token) for token in tokenize("x<a>z")]


def test_concurrent_feed_on_shared_tokenizer_is_memory_safe() -> None:
    # the shared state machine, its record buffers, and its free list are guarded by a
    # per-tokenizer critical section, so concurrent feed()/iteration on the free-threaded
    # interpreter must not corrupt the heap (it segfaulted before); under the GIL this just
    # exercises the locked paths
    tokenizer = Tokenizer()
    chunks = ["<div class='a'>hi</div>", "<p>x</p>", "<a href='x'>l</a>", "<!-- c -->", "<span>partial"]

    def work() -> None:
        for _ in range(200):
            for chunk in chunks:
                for _token in tokenizer.feed(chunk):
                    pass

    threads = [threading.Thread(target=work) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    list(tokenizer.close())


def test_dispatch_requires_every_handler() -> None:
    # dispatch binds the handlers once per call, so an object missing one fails before any token is delivered
    tokenizer = Tokenizer()
    tokenizer.feed("<p>text</p>")
    with pytest.raises(AttributeError, match="handle_"):
        tokenizer.dispatch(object())


@pytest.mark.parametrize("count", [0, 1, 10, 100], ids=["empty", "one", "ten", "hundred"])
def test_token_attrs_order(count: int) -> None:
    expected: Final = [(f"a{index}", str(index)) for index in range(count)]
    (token,) = tokenize(f"<p {' '.join(f'{name}={value}' for name, value in expected)}>")
    assert token.attrs == expected


@pytest.mark.parametrize("document", ["<p>", "<p a=1 b=2>"], ids=["empty", "populated"])
def test_token_attrs_returns_fresh_list(document: str) -> None:
    (token,) = tokenize(document)
    expected: Final = token.attrs
    attrs: Final = token.attrs
    assert attrs is not None
    attrs[:] = [("replacement", "changed")]
    assert token.attrs == expected


# Large enough that the old O(n^2) scan would take tens of seconds even on an
# instrumented build, so a regression cannot slip under the wall-clock ceiling; the
# linear path parses it in well under a second.
_MANY = 60000
# Headroom is ~30x over the linear cost and ~5x under the quadratic one, so ordinary
# CI scheduling jitter cannot flip the result either way.
_BUDGET_SECONDS = 6.0


def _attrs_of(pairs: str) -> list[tuple[str, str]]:
    (tag,) = list(tokenize(f"<a{pairs}>"))
    assert tag.attrs is not None  # a start tag always exposes its attribute list
    return tag.attrs


def test_many_distinct_attributes_parse_in_linear_time() -> None:
    pairs = "".join(f" a{index}" for index in range(_MANY))
    start = time.perf_counter()
    attrs = _attrs_of(pairs)
    assert time.perf_counter() - start < _BUDGET_SECONDS
    assert len(attrs) == _MANY  # every distinct name is kept, none dropped


def test_many_distinct_attributes_keep_their_names_and_order() -> None:
    attrs = _attrs_of("".join(f" a{index}" for index in range(2000)))
    assert [name for name, _ in attrs] == [f"a{index}" for index in range(2000)]


def test_many_duplicate_attributes_collapse_to_the_first() -> None:
    assert _attrs_of(" x" * _MANY) == [("x", "")]  # duplicates dropped early, count never grows


def test_interleaved_duplicates_keep_first_occurrence_order() -> None:
    attrs = _attrs_of(" a b a c b d a")  # repeats reuse the dropped slot, first wins
    assert [name for name, _ in attrs] == ["a", "b", "c", "d"]


def test_hash_index_resets_between_consecutive_large_tags() -> None:
    # Two big tags back to back: the second must not see the first tag's names, so the
    # per-tag epoch reset and the grow that re-seats live entries both have to be right.
    first = "<a" + "".join(f" p{index}" for index in range(1500)) + ">"
    second = "<b" + "".join(f" p{index}" for index in range(1500)) + ">"
    counts = [len(token.attrs) for token in tokenize(first + second) if token.attrs is not None]
    assert counts == [1500, 1500]  # neither tag drops a name


def _reference_shape(token: Token) -> tuple[object, ...]:
    return (token.type, token.tag, token.data, token.source, token.attrs, token.self_closing)


def _stream_char_by_char(document: str, *, resolve_references: bool, capture_source: bool) -> list[Token]:
    tokenizer = Tokenizer(resolve_references=resolve_references, capture_source=capture_source)
    streamed = [token for char in document for token in tokenizer.feed(char)]
    streamed += list(tokenizer.close())
    return streamed


def _refs(tokens: Iterable[Token]) -> list[tuple[str | None, str | None]]:
    return [(token.data, token.source) for token in tokens if token.type is TokenType.CHARACTER_REFERENCE]


def test_default_resolves_references_into_text() -> None:
    head, tag = list(tokenize("a&amp;b<p>"))
    assert _reference_shape(head) == (TokenType.TEXT, None, "a&b", None, None, False)
    assert tag.type is TokenType.START_TAG


def test_default_source_is_none() -> None:
    assert all(token.source is None for token in tokenize('<a href="x">hi</a>&amp;'))


@pytest.mark.parametrize(
    ("document", "data", "source"),
    [
        pytest.param("&amp;", "&", "&amp;", id="named"),
        pytest.param("&#x41;", "A", "&#x41;", id="numeric-hex"),
        pytest.param("&#65;", "A", "&#65;", id="numeric-dec"),
        pytest.param("&cent;", "¢", "&cent;", id="named-two-byte"),
        pytest.param("&notin;", "∉", "&notin;", id="named-astral-name"),
        pytest.param("&amp", "&", "&amp", id="named-no-semicolon"),
        pytest.param("&gt;", ">", "&gt;", id="named-gt"),
    ],
)
def test_reference_becomes_its_own_token(document: str, data: str, source: str) -> None:
    (reference,) = list(tokenize(document, resolve_references=False))
    assert reference.type is TokenType.CHARACTER_REFERENCE
    assert reference.data == data
    assert reference.source == source
    assert reference.tag is None
    assert reference.attrs is None
    assert reference.self_closing is False


def test_reference_splits_surrounding_text() -> None:
    tokens = list(tokenize("a&amp;b", resolve_references=False))
    assert [(token.type, token.data) for token in tokens] == [
        (TokenType.TEXT, "a"),
        (TokenType.CHARACTER_REFERENCE, "&"),
        (TokenType.TEXT, "b"),
    ]


@pytest.mark.parametrize(
    ("document", "text"),
    [
        pytest.param("a & b", "a & b", id="bare-ampersand-space"),
        pytest.param("&xyz", "&xyz", id="unknown-name-no-semicolon"),
        pytest.param("&#", "&#", id="numeric-no-digits"),
        pytest.param("&#x", "&#x", id="hex-no-digits"),
        pytest.param("x&", "x&", id="trailing-ampersand-at-eof"),
    ],
)
def test_non_reference_ampersand_stays_text(document: str, text: str) -> None:
    tokens = list(tokenize(document, resolve_references=False))
    assert all(token.type is TokenType.TEXT for token in tokens)
    assert "".join(token.data or "" for token in tokens) == text


def test_consecutive_references() -> None:
    assert _refs(tokenize("&amp;&lt;&gt;", resolve_references=False)) == [("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")]


def test_named_vs_numeric_is_readable_from_source() -> None:
    named, numeric = (token.source for token in tokenize("&amp;&#65;", resolve_references=False))
    assert named is not None
    assert numeric is not None
    # the verbatim source distinguishes the two: a numeric reference's second character is '#'
    assert (named[1], numeric[1]) == ("a", "#")


def test_references_split_in_rcdata() -> None:
    tokens = list(tokenize("<title>x&amp;y</title>", resolve_references=False))
    assert _refs(tokens) == [("&", "&amp;")]
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == ["x", "y"]


def test_non_reference_ampersand_stays_text_in_rcdata() -> None:
    tokens = list(tokenize("<title>a & b</title>", resolve_references=False))
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == ["a & b"]
    assert not any(token.type is TokenType.CHARACTER_REFERENCE for token in tokens)


def test_attribute_references_always_resolved() -> None:
    # convert_charrefs-style splitting applies to text only; the attribute list stays decoded
    (tag,) = list(tokenize('<a href="a&amp;b">', resolve_references=False))
    assert tag.type is TokenType.START_TAG
    assert tag.attrs == [("href", "a&b")]


def test_reference_repr() -> None:
    (reference,) = list(tokenize("&amp;", resolve_references=False))
    assert repr(reference) == "Token(CHARACTER_REFERENCE, data='&')"


@pytest.mark.parametrize(
    ("document", "tag", "source"),
    [
        pytest.param('<a href="x">', "a", '<a href="x">', id="start-tag"),
        pytest.param("</a>", "a", "</a>", id="end-tag"),
        pytest.param("<br/>", "br", "<br/>", id="self-closing"),
        pytest.param("<!--c-->", None, "<!--c-->", id="comment"),
        pytest.param("<!DOCTYPE html>", None, "<!DOCTYPE html>", id="doctype"),
    ],
)
def test_capture_source_records_verbatim_markup(document: str, tag: str | None, source: str) -> None:
    (token,) = list(tokenize(document, capture_source=True))
    assert token.tag == tag
    assert token.source == source


def test_capture_source_leaves_text_without_source() -> None:
    start, text, end = list(tokenize("<p>hi</p>", capture_source=True))
    assert start.source == "<p>"
    assert text.type is TokenType.TEXT
    assert text.source is None
    assert end.source == "</p>"


def test_capture_source_preserves_original_casing_and_whitespace() -> None:
    # the resolved tag name is lowercased, but the verbatim source keeps the original spelling
    (tag,) = list(tokenize("<DIV  Class = 'x' >", capture_source=True))
    assert tag.tag == "div"
    assert tag.source == "<DIV  Class = 'x' >"


def test_capture_source_and_split_references_combine() -> None:
    tokens = list(tokenize("<p>a&amp;b</p>", resolve_references=False, capture_source=True))
    assert [(token.type, token.source) for token in tokens] == [
        (TokenType.START_TAG, "<p>"),
        (TokenType.TEXT, None),
        (TokenType.CHARACTER_REFERENCE, "&amp;"),
        (TokenType.TEXT, None),
        (TokenType.END_TAG, "</p>"),
    ]


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("a&amp;b&#x41;c&#65;d&cent;&notin;&unknown;e", id="mixed-references"),
        pytest.param("x & y &amp z &# w", id="literal-ampersands"),
        pytest.param("<title>r&amp;c</title>&gt;", id="rcdata-and-data"),
        pytest.param("<title>a & b &x</title>", id="rcdata-literal-ampersand"),
        pytest.param("&amp;&lt;&gt;", id="adjacent"),
    ],
)
def test_split_streaming_matches_whole(document: str, width_prefix: str) -> None:
    text = width_prefix + document
    streamed = _stream_char_by_char(text, resolve_references=False, capture_source=False)
    whole = list(tokenize(text, resolve_references=False))
    assert [_reference_shape(token) for token in streamed] == [_reference_shape(token) for token in whole]
    assert [(token.line, token.col) for token in streamed] == [(token.line, token.col) for token in whole]


@pytest.mark.parametrize(
    "document",
    [
        pytest.param('<a href="x" id=y>hi</a><!--c--><br/>', id="tags-and-comment"),
        pytest.param("<DIV Class='X'>t</DIV>", id="casing"),
        pytest.param("<!DOCTYPE html><p>x", id="doctype"),
        pytest.param("<a\n href='x'\n>y", id="multiline-tag"),
    ],
)
def test_capture_source_streaming_matches_whole(document: str, width_prefix: str) -> None:
    text = width_prefix + document
    streamed = _stream_char_by_char(text, resolve_references=True, capture_source=True)
    whole = list(tokenize(text, capture_source=True))
    assert [_reference_shape(token) for token in streamed] == [_reference_shape(token) for token in whole]


def test_capture_source_spans_a_feed_boundary() -> None:
    # the opening '<' is in an earlier chunk than the closing '>', so input compaction
    # between feeds must keep the tag's source prefix alive (issue #213); here a pending
    # text run ("a") already pins the prefix
    tokenizer = Tokenizer(capture_source=True)
    tokens = list(tokenizer.feed("a<a "))
    tokens += list(tokenizer.feed('href="x">b'))
    tokens += list(tokenizer.close())
    start = next(token for token in tokens if token.type is TokenType.START_TAG)
    assert start.source == '<a href="x">'


def test_capture_source_spans_feed_boundary_without_leading_text() -> None:
    # a tag opens a chunk with nothing buffered before it, so only the marked '<' itself
    # keeps the prefix alive across the next feed's compaction
    tokenizer = Tokenizer(capture_source=True)
    list(tokenizer.feed("<a>"))  # emits and drains, leaving no pending text
    list(tokenizer.feed("<b "))  # opens a tag with nothing buffered ahead of it
    tokens = [*tokenizer.feed('x="y">'), *tokenizer.close()]
    start = next(token for token in tokens if token.tag == "b")
    assert start.source == '<b x="y">'


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param("<p>abc</p><p", " class=x>", id="shorter-prefix"),
        pytest.param("<p>abcdefghij</p><p cl", "ass=x>", id="longer-prefix"),
    ],
)
def test_capture_source_spans_feed_boundary_after_consumed_markup(first: str, second: str) -> None:
    tokenizer = Tokenizer(capture_source=True)
    list(tokenizer.feed(first))
    assert [token.source for token in tokenizer.feed(second)] == ["<p class=x>"]


def test_tokenize_rejects_unknown_keyword() -> None:
    with pytest.raises(TypeError):
        tokenize("x", flavor=1)  # ty: ignore[unknown-argument]  # unexpected keyword on purpose


def test_capture_source_survives_many_streamed_tags() -> None:
    # drive enough feeds that the input buffer compacts repeatedly while sources resolve
    tokenizer = Tokenizer(capture_source=True)
    sources: list[str | None] = []
    for index in range(50):
        sources.extend(
            token.source for token in tokenizer.feed(f"<p id={index}>x</p>") if token.type is TokenType.START_TAG
        )
    sources += [token.source for token in tokenizer.close() if token.type is TokenType.START_TAG]
    assert sources == [f"<p id={index}>" for index in range(50)]


def test_split_reference_survives_a_queued_feed() -> None:
    # a reference token left queued behind a flushed text run must keep a valid source
    # across the next feed's compaction
    tokenizer = Tokenizer(resolve_references=False)
    stream = tokenizer.feed("x&amp;")
    first = next(stream)
    tokenizer.feed("y")
    tokens = [first, *tokenizer, *tokenizer.close()]
    assert _refs(tokens) == [("&", "&amp;")]
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == ["x", "y"]


_TOKENIZER_DIR: Final[Path] = Path(__file__).parents[1] / "html5lib-tests" / "tokenizer"

# CI always checks out the submodule (actions/checkout submodules: true); this guard fires only locally
if not _TOKENIZER_DIR.is_dir() or not any(_TOKENIZER_DIR.glob("*.test")):  # pragma: no cover
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)

_DOUBLE_ESCAPE = re.compile(r"\\u([0-9A-Fa-f]{4})")
_TokenPart: TypeAlias = str | bool | dict[str, str] | None
_ExpectedToken: TypeAlias = list[_TokenPart]
_ParseError: TypeAlias = tuple[str, int, int]
_TokenizerCase: TypeAlias = tuple[str, str, str | None, list[_ExpectedToken], list[_ParseError], str]
_PI_TOKEN_OVERRIDES: Final[dict[str, list[_ExpectedToken]]] = {
    "<?namespace>": [["ProcessingInstruction", "namespace", ""]],
    "<?foo-->": [["ProcessingInstruction", "foo--", ""]],
}


def _decode_double(text: str) -> str:
    return _DOUBLE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _decode_token(token: _ExpectedToken) -> _ExpectedToken:
    return [_decode_double(item) if isinstance(item, str) else item for item in token]


def _living_pi_expectation(
    text: str, state: str, expected: list[_ExpectedToken], errors: list[_ParseError]
) -> tuple[list[_ExpectedToken], list[_ParseError]]:
    if state != "Data state" or not text.startswith("<?"):
        return expected, errors
    if not (first := text[2:3]):
        return [], [("eof-in-processing-instruction", 1, 3)]
    if first.isascii() and (first.isalpha() or first == "_"):
        if text.endswith(">"):
            return _PI_TOKEN_OVERRIDES.get(text, expected), []
        return [], [("eof-in-processing-instruction", 1, len(text) + 1)]
    retained = [
        error
        for error in errors
        if error[0] not in {"control-character-in-input-stream", "unexpected-question-mark-instead-of-tag-name"}
    ]
    preprocessing = [error for error in errors if error[0] == "control-character-in-input-stream"]
    return expected, [*preprocessing, ("invalid-first-character-of-processing-instruction-target", 1, 3), *retained]


def _load_cases() -> list[_TokenizerCase]:
    cases: list[_TokenizerCase] = []
    for path in sorted(_TOKENIZER_DIR.glob("*.test")):
        document = json.loads(path.read_text(encoding="utf-8"))
        for index, test in enumerate(document.get("tests", [])):
            double_escaped = test.get("doubleEscaped", False)
            text = _decode_double(test["input"]) if double_escaped else test["input"]
            expected = [
                _decode_token(token) if double_escaped else list(token)
                for token in test["output"]
                if token != "ParseError"  # ruff:ignore[hardcoded-password-string]  # a token-stream marker, not a password
            ]
            errors = [(error["code"], error["line"], error["col"]) for error in test.get("errors", [])]
            last_start_tag = test.get("lastStartTag")
            for state in test.get("initialStates", ["Data state"]):
                expected, errors = _living_pi_expectation(text, state, expected, errors)
                identifier = f"{path.stem}-{index}-{state.replace(' ', '_')}"
                cases.append((text, state, last_start_tag, expected, errors, identifier))
    return cases


def _token_cases() -> list[ParameterSet]:
    return [pytest.param(text, state, tag, tokens, id=name) for text, state, tag, tokens, _, name in _load_cases()]


def _error_cases() -> list[ParameterSet]:
    return [pytest.param(text, state, tag, errors, id=name) for text, state, tag, _, errors, name in _load_cases()]


@pytest.mark.parametrize(("text", "state", "last_start_tag", "expected"), _token_cases())
def test_tokenizer_conformance(
    text: str, state: str, last_start_tag: str | None, expected: list[_ExpectedToken], storage_kind: int
) -> None:
    actual, _ = _html._tokenize_states(text, state, last_start_tag, storage_kind)
    assert [list(token) for token in actual] == expected


@pytest.mark.parametrize(("text", "state", "last_start_tag", "errors"), _error_cases())
def test_tokenizer_parse_errors(
    text: str, state: str, last_start_tag: str | None, errors: list[_ParseError], storage_kind: int
) -> None:
    _, raised = _html._tokenize_states(text, state, last_start_tag, storage_kind)
    assert [code for code, _, _ in raised] == [code for code, _, _ in errors]
    if all(ord(character) <= 0xFFFF for character in text):
        assert [(code, line, col + 1) for code, line, col in raised] == errors
