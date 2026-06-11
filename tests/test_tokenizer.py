"""Behavioral tests for the public tokenizer API.

The html5lib conformance suite (test_tokenizer_conformance.py) pins the state
machine to the WHATWG algorithm; these tests cover what the suite cannot: the
Python-facing Token/Tokenizer/tokenize surface, the tag-driven content-model
switching the suite bypasses, incremental feeding (the suite feeds whole
strings), and source positions.
"""

from __future__ import annotations

import gc
from html.parser import HTMLParser

import pytest

from turbohtml import Token, Tokenizer, TokenType, _html, tokenize


def _shape(token: Token) -> tuple[object, ...]:
    return (token.type, token.tag, token.data, token.attrs, token.self_closing)


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
    assert _shape(head) == (TokenType.TEXT, None, "a", None, False)
    assert _shape(start) == (TokenType.START_TAG, "p", None, [("class", "x")], False)
    assert _shape(body) == (TokenType.TEXT, None, "b", None, False)
    assert _shape(end) == (TokenType.END_TAG, "p", None, [], False)


def test_tokenize_empty_input() -> None:
    assert list(tokenize("")) == []


def test_tokenize_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        tokenize(123)  # ty: ignore[invalid-argument-type]  # non-str on purpose to exercise the TypeError path


def test_feed_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        Tokenizer().feed(b"<p>")  # ty: ignore[invalid-argument-type]  # non-str on purpose


def test_comment_token() -> None:
    (comment,) = list(tokenize("<!-- hi -->"))
    assert comment.type is TokenType.COMMENT
    assert comment.data == " hi "
    assert comment.tag is None
    assert comment.attrs is None


def test_doctype_token_full() -> None:
    (doctype,) = list(tokenize("<!DOCTYPE HTML PUBLIC \"pub\" 'sys'>"))
    assert doctype.type is TokenType.DOCTYPE
    assert doctype.name == "html"
    assert doctype.public_id == "pub"
    assert doctype.system_id == "sys"
    assert doctype.force_quirks is False
    assert doctype.data is None


def test_doctype_token_bare() -> None:
    (doctype,) = list(tokenize("<!DOCTYPE>"))
    assert doctype.name == ""  # noqa: PLC1901  # must be exactly the empty string, None would also be falsey
    assert doctype.public_id is None
    assert doctype.system_id is None
    assert doctype.force_quirks is True


def test_non_doctype_has_no_doctype_fields() -> None:
    (tag,) = list(tokenize("<p>"))
    assert tag.name is None
    assert tag.public_id is None
    assert tag.system_id is None
    assert tag.force_quirks is False


def test_self_closing_tag() -> None:
    (tag,) = list(tokenize("<br/>"))
    assert tag.self_closing is True


def test_duplicate_attributes_keep_first() -> None:
    (tag,) = list(tokenize("<a x=1 y x=2 z=''>"))
    assert tag.attrs == [("x", "1"), ("y", None), ("z", "")]


def test_non_latin1_tag_name() -> None:
    tokens = list(tokenize("<xmő>x</xmő>"))
    assert [token.tag for token in tokens if token.tag] == ["xmő", "xmő"]


def test_wide_buffers_after_odd_length_narrow_ones() -> None:
    (tag,) = list(tokenize("<ab xyz=ő q=🎉>"))
    assert tag.tag == "ab"
    assert tag.attrs == [("xyz", "ő"), ("q", "🎉")]


def test_same_length_attribute_names_of_different_widths() -> None:
    (tag,) = list(tokenize("<a xy=1 xő=2>"))
    assert tag.attrs == [("xy", "1"), ("xő", "2")]


def test_attr_lookup() -> None:
    (tag,) = list(tokenize("<a href='u' download>"))
    assert tag.attr("href") == "u"
    assert tag.attr("download") is None
    assert tag.attr("missing") is None
    assert tag.attr("missing", "fallback") == "fallback"
    assert tag.attr("href2", "fallback") == "fallback"
    assert tag.attr("hrex", "fallback") == "fallback"


def test_attr_on_non_tag_returns_default() -> None:
    (text,) = list(tokenize("plain"))
    assert text.attr("x", "fallback") == "fallback"


def test_attr_rejects_non_str_name() -> None:
    (tag,) = list(tokenize("<p>"))
    with pytest.raises(TypeError):
        tag.attr(123)  # ty: ignore[invalid-argument-type]  # non-str on purpose


def test_token_repr() -> None:
    tokens = list(tokenize("a<p x=1></p><!--c--><!DOCTYPE html>"))
    assert repr(tokens[0]) == "Token(TEXT, data='a')"
    assert repr(tokens[1]) == "Token(START_TAG, tag='p')"
    assert repr(tokens[2]) == "Token(END_TAG, tag='p')"
    assert repr(tokens[3]) == "Token(COMMENT, data='c')"
    assert repr(tokens[4]) == "Token(DOCTYPE, name='html')"


def test_token_type_enum() -> None:
    assert [member.value for member in TokenType] == [0, 1, 2, 3, 4]
    assert TokenType.TEXT == 0
    assert isinstance(TokenType.START_TAG, int)


def test_token_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Token()


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
        pytest.param("a\rb", "a\nb", id="lone-cr"),
        pytest.param("a\r\nb", "a\nb", id="crlf"),
        pytest.param("a\r\r\nb", "a\n\nb", id="cr-then-crlf"),
        pytest.param("ő\r\nő\rz", "ő\nő\nz", id="crlf-ucs2"),
        pytest.param("\U0001f600\r\n\U0001f600\rz", "\U0001f600\n\U0001f600\nz", id="crlf-ucs4"),
        pytest.param("ő\nő", "ő\nő", id="bare-lf-ucs2"),
        pytest.param("\U0001f600\n\U0001f600", "\U0001f600\n\U0001f600", id="bare-lf-ucs4"),
    ],
)
def test_newline_normalization(text: str, expected: str) -> None:
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
        pytest.param("<script><!--</scrip >x", id="escaped-end-inappropriate-space"),
        pytest.param("<script><!--</scrip/>x", id="escaped-end-inappropriate-slash"),
        pytest.param("<script><!--<scr ipt>x", id="double-escape-start-space"),
        pytest.param("<script><!--<script/>x-->", id="double-escape-start-slash"),
        pytest.param("<script><!--<script></script ->x", id="double-escape-end-space"),
        pytest.param("<script><!--<script></script/->x", id="double-escape-end-slash"),
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
    assert _html._tokenize_states(text, "CDATA section state", None, storage_kind) == [("Character", expected)]


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
    assert _html._tokenize_states(text, state, None, storage_kind) == [("Character", expected)]


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

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
            self.positions.append(self.getpos())

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # noqa: ARG002
            self.positions.append(self.getpos())

        def handle_data(self, data: str) -> None:  # noqa: ARG002
            self.positions.append(self.getpos())

        def handle_comment(self, data: str) -> None:  # noqa: ARG002
            self.positions.append(self.getpos())

    recorder = Recorder()
    recorder.feed(document)
    recorder.close()
    assert [(token.line, token.col) for token in tokenize(document)] == recorder.positions


def test_dropped_tag_at_eof_flushes_text() -> None:
    assert [token.data for token in tokenize("a<div")] == ["a"]


def test_lt_at_eof_is_text() -> None:
    assert [token.data for token in tokenize("a<")] == ["a<"]


def test_garbage_collection_traversal() -> None:
    tokenizer = Tokenizer()
    iterator = tokenizer.feed("<p><b>")
    token = next(iterator)
    gc.collect()
    del tokenizer
    assert token.tag == "p"
    assert [token.tag for token in iterator] == ["b"]


def test_tokenize_states_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _html._tokenize_states(123, "Data state")  # ty: ignore[invalid-argument-type]  # non-str on purpose


def test_tokenize_states_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="unknown initial state"):
        _html._tokenize_states("x", "Bogus state", None)


def test_tokenize_states_rejects_non_str_last_tag() -> None:
    with pytest.raises(TypeError, match="last_start_tag"):
        _html._tokenize_states("x", "Data state", 5)  # ty: ignore[invalid-argument-type]  # non-str on purpose


def test_tokenize_states_rejects_bad_storage_kind() -> None:
    with pytest.raises(ValueError, match="storage_kind"):
        _html._tokenize_states("x", "Data state", None, 3)


def test_tokenize_states_default_last_tag() -> None:
    assert _html._tokenize_states("x", "Data state") == [("Character", "x")]


def test_tokenize_states_empty_last_tag() -> None:
    assert _html._tokenize_states("x", "RCDATA state", "") == [("Character", "x")]


def test_rcdata_end_tag_name_width_mismatch() -> None:
    assert _html._tokenize_states("</xy>", "RCDATA state", "xő") == [("Character", "</xy>")]


def test_tag_names_are_lowercased() -> None:
    assert [next(iter(tokenize(document))).tag for document in ("<DIV>", "<SpAn>")] == ["div", "span"]
