from __future__ import annotations

import gc
import threading
from functools import partial
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest

from turbohtml import Document, IncrementalParser, Token, Tokenizer, TokenType, parse, tokenize

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from traced_bytes import PeakBytes, RetainedBytes
    from work_steps import WorkSteps


def test_tokenize_simple_document() -> None:
    head, start, body, end = list(tokenize('a<p class="x">b</p>'))
    assert _shape(head) == (TokenType.TEXT, None, None, "a", None, False)
    assert _shape(start) == (TokenType.START_TAG, "p", None, None, [("class", "x")], False)
    assert _shape(body) == (TokenType.TEXT, None, None, "b", None, False)
    assert _shape(end) == (TokenType.END_TAG, "p", None, None, [], False)


def _shape(token: Token) -> _Shape:
    return _Shape(token.type, token.tag, token.target, token.data, token.attrs, token.self_closing)


class _Shape(NamedTuple):
    token_type: TokenType
    tag: str | None
    target: str | None
    data: str | None
    attrs: list[tuple[str, str]] | None
    self_closing: bool


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
    assert [token.data for token in tokenize(f"{width_prefix}<?pi ")] == [
        token.data for token in tokenize(width_prefix)
    ]


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
def test_doctype_fields(document: str, expected: tuple[TokenType, str | None, str | None, str | None, bool]) -> None:
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
        # a start tag never writes the text and doctype buffers, which stay NULL and pack as empty
        pytest.param("<p>", "p", [], id="no-attributes"),
    ],
)
def test_tag_attrs(document: str, tag_name: str, attrs: list[tuple[str, str | None]]) -> None:
    (tag,) = list(tokenize(document))
    assert (tag.tag, tag.attrs) == (tag_name, attrs)


def _whole_tokens(document: str) -> list[Token]:
    return list(tokenize(document, capture_attributes=False))


def _streamed_tokens(document: str) -> list[Token]:
    tokenizer = Tokenizer(capture_attributes=False)
    return [token for char in document for token in tokenizer.feed(char)] + list(tokenizer.close())


@pytest.mark.parametrize(
    "tokens_of",
    [pytest.param(_whole_tokens, id="whole"), pytest.param(_streamed_tokens, id="streaming")],
)
def test_capture_attributes_can_be_disabled(tokens_of: Callable[[str], list[Token]]) -> None:
    start, text, end = tokens_of('<a href="a&amp;b" download>text</a>')
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
        tag.attr(b"href")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
    with pytest.raises(TypeError):
        tag.attr(123)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
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
    assert (TokenType.TEXT, TokenType.CHARACTER_REFERENCE, TokenType.PROCESSING_INSTRUCTION) == (0, 5, 6)
    assert isinstance(TokenType.START_TAG, int)


def test_token_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        Token()


def test_tokenizer_takes_no_arguments() -> None:
    # a streaming tokenizer starts empty; a constructor argument is an error, not a silent no-op
    with pytest.raises(TypeError):
        Tokenizer("<p>")  # ty: ignore[too-many-positional-arguments]  # passes an extra positional argument on purpose to check the rejection
    with pytest.raises(TypeError):
        Tokenizer(source="<p>")  # ty: ignore[unknown-argument]  # passes an unknown keyword on purpose to check the rejection


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
        pytest.param("<plaintext></plaintext><p>", ["</plaintext><p>"], id="plaintext"),
    ],
)
def test_content_model_switching(document: str, expected: list[str]) -> None:
    texts = [token.data for token in tokenize(document) if token.type is TokenType.TEXT]
    assert texts == expected


def _start_tags(tokens: Iterable[Token]) -> list[str | None]:
    return [token.tag for token in tokens if token.type is TokenType.START_TAG]


class _StartTags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # ruff:ignore[unused-method-argument]  # the callback signature is fixed by the protocol; this argument is unused
        self.tags.append(tag)

    def handle_data(self, data: str) -> None:
        self.data.append(data)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        # SVG/MathML content stays markup: title, style, xmp, ... are ordinary elements there
        pytest.param("<svg><title><i>", ["svg", "title", "i"], id="svg-title"),
        pytest.param("<svg><style><i></style>", ["svg", "style", "i"], id="svg-style"),
        pytest.param("<svg><script><i></script>", ["svg", "script", "i"], id="svg-script"),
        pytest.param("<svg><plaintext><i>", ["svg", "plaintext", "i"], id="svg-plaintext"),
        pytest.param("<math><xmp><i>", ["math", "xmp", "i"], id="math-xmp"),
        pytest.param("<svg><g><textarea><i>", ["svg", "g", "textarea", "i"], id="nested"),
        pytest.param("<svg><svg></svg><style><i>", ["svg", "svg", "style", "i"], id="inner-svg-closed"),
        pytest.param("<svg><input><style><i>", ["svg", "input", "style", "i"], id="void-name-stays-open"),
        # an integration point hands its start tags back to the HTML rules
        pytest.param("<svg><foreignObject><style><i>", ["svg", "foreignobject", "style"], id="foreign-object"),
        pytest.param("<svg><desc><xmp><i>", ["svg", "desc", "xmp"], id="desc"),
        pytest.param("<svg><title><textarea><i>", ["svg", "title", "textarea"], id="title-point"),
        pytest.param("<svg><title><title><i></title><i>", ["svg", "title", "title", "i"], id="title-in-title-point"),
        pytest.param("<math><mi><style><i>", ["math", "mi", "style"], id="mathml-text-point"),
        pytest.param("<math><mi><mglyph><style><i>", ["math", "mi", "mglyph", "style", "i"], id="mglyph"),
        pytest.param("<math><mo><malignmark><style><i>", ["math", "mo", "malignmark", "style", "i"], id="malignmark"),
        pytest.param("<math><mi><svg><style><i>", ["math", "mi", "svg", "style", "i"], id="svg-in-text-point"),
        pytest.param("<svg><desc><math><style><i>", ["svg", "desc", "math", "style", "i"], id="math-in-desc"),
        pytest.param("<math><annotation-xml><style><i>", ["math", "annotation-xml", "style", "i"], id="annotation-xml"),
        pytest.param(
            '<math><annotation-xml encoding="Text/HTML"><style><i>', ["math", "annotation-xml", "style"], id="html"
        ),
        pytest.param(
            "<math><annotation-xml encoding=application/xhtml+xml><style><i>",
            ["math", "annotation-xml", "style"],
            id="xhtml",
        ),
        pytest.param(
            "<math><annotation-xml id=a encoding=text/html><style><i>", ["math", "annotation-xml", "style"], id="second"
        ),
        pytest.param(
            "<math><annotation-xml encoding=text/xml><style><i>", ["math", "annotation-xml", "style", "i"], id="xml"
        ),
        pytest.param(
            "<math><annotation-xml encoding=text/htmł><style><i>",
            ["math", "annotation-xml", "style", "i"],
            id="wide-encoding",
        ),
        pytest.param(
            "<math><annotation-xml encoding><style><i>", ["math", "annotation-xml", "style", "i"], id="bare-encoding"
        ),
        pytest.param(
            "<math><annotation-xml encoding=x encoding=text/html><style><i>",
            ["math", "annotation-xml", "style", "i"],
            id="first-encoding-wins",
        ),
        pytest.param(
            "<math><annotation-xml><svg><style><i>",
            ["math", "annotation-xml", "svg", "style", "i"],
            id="svg-in-annotation",
        ),
        pytest.param(
            "<svg><annotation-xml encoding=text/html><style><i>",
            ["svg", "annotation-xml", "style", "i"],
            id="svg-annotation",
        ),
        # a breakout tag closes the foreign elements, so what follows is HTML again
        pytest.param("<svg><g><p><style><i>", ["svg", "g", "p", "style"], id="breakout"),
        pytest.param("<svg><img><style><i>", ["svg", "img", "style"], id="void-breakout"),
        pytest.param(
            "<svg><blzzzzzzze><style><i>", ["svg", "blzzzzzzze", "style", "i"], id="near-breakout-name"
        ),  # shares blockquote's hash slot and length
        pytest.param("<svg><font color=red><style><i>", ["svg", "font", "style"], id="font-color"),
        pytest.param("<svg><font x FACE=a><style><i>", ["svg", "font", "style"], id="font-face"),
        pytest.param("<svg><font size=1><style><i>", ["svg", "font", "style"], id="font-size"),
        pytest.param("<svg><font ā size=1><style><i>", ["svg", "font", "style"], id="font-wide-name"),
        pytest.param("<svg><font fāce=1><style><i>", ["svg", "font", "style", "i"], id="font-wide-same-length"),
        pytest.param("<svg><font sized><style><i>", ["svg", "font", "style", "i"], id="font-plain"),
        pytest.param("<svg><desc><svg><b><style><i>", ["svg", "desc", "svg", "b", "style"], id="breakout-to-point"),
        pytest.param("<svg></p><style><i>", ["svg", "style"], id="end-p"),
        pytest.param("<svg><g></br><style><i>", ["svg", "g", "style"], id="end-br"),
        pytest.param("<p><svg></p><style><i>", ["p", "svg", "style"], id="end-p-closes-open-p"),
        pytest.param("<svg><desc></p><style><i>", ["svg", "desc", "style"], id="end-p-at-point"),
        # end tags: a foreign element closes by name, an HTML end tag stops at a scoping foreign element
        pytest.param("<svg><g></svg><style><i>", ["svg", "g", "style"], id="end-svg"),
        pytest.param("<svg><g></g x=1><style><i>", ["svg", "g", "style", "i"], id="end-tag-attributes"),
        pytest.param(
            "<svg><desc><b><svg></svg><style><i>", ["svg", "desc", "b", "svg", "style"], id="inner-svg-end-to-html"
        ),
        pytest.param("<svg><title></svg><style><i>", ["svg", "title", "style"], id="end-svg-from-point"),
        pytest.param("<svg><g></x><style><i>", ["svg", "g", "style", "i"], id="stray-end"),
        pytest.param("<svg><x-y><g></x-y><style><i>", ["svg", "x-y", "g", "style", "i"], id="custom-end"),
        pytest.param("<x-y><svg><g></x-z></x-yy><style><i>", ["x-y", "svg", "g", "style", "i"], id="custom-stray-end"),
        pytest.param(
            "<div></dfn></span></dív><svg></div><style><i>", ["div", "svg", "style"], id="html-strays-keep-open"
        ),
        pytest.param("<x-ő><svg></x-ő><style><i>", ["x-ő", "svg", "style"], id="wide-custom-end"),
        pytest.param(
            "<x-abcdefgh><svg></x-abcdefgi><style><i>", ["x-abcdefgh", "svg", "style", "i"], id="long-custom-stray-end"
        ),
        pytest.param("<x-abcdefgh><svg></x-a><style><i>", ["x-abcdefgh", "svg", "style", "i"], id="short-end-of-long"),
        pytest.param("<x-a><svg></x-abcdefgh><style><i>", ["x-a", "svg", "style", "i"], id="long-end-of-short"),
        pytest.param("<x-abcdef><svg></x-abcdef><style><i>", ["x-abcdef", "svg", "style"], id="word-custom-end"),
        pytest.param(
            "<svg></svg></x><x-y></x-y><section><x-element></section><style><i>",
            ["svg", "x-y", "section", "x-element", "style"],
            id="html-after-foreign",
        ),
        pytest.param("<div><svg><g></div><style><i>", ["div", "svg", "g", "style"], id="html-end-closes-svg"),
        pytest.param("<svg><title><b></title><style><i>", ["svg", "title", "b", "style"], id="point-bounds-own-end"),
        pytest.param(
            "<div><svg><desc><b></div></b></desc><style><i>",
            ["div", "svg", "desc", "b", "style", "i"],
            id="point-bounds",
        ),
        pytest.param(
            "<div><math><annotation-xml><g></div><style><i>",
            ["div", "math", "annotation-xml", "g", "style", "i"],
            id="annotation-xml-bounds",
        ),
        pytest.param(
            "<div><svg><annotation-xml></div><style><i>",
            ["div", "svg", "annotation-xml", "style"],
            id="svg-annotation-open",
        ),
        pytest.param(
            "<table><td><svg><desc><b></td><style><i>", ["table", "td", "svg", "desc", "b", "style"], id="table-scope"
        ),
        pytest.param("<td><svg><td></td><style><i>", ["td", "svg", "td", "style", "i"], id="foreign-td"),
        # an HTML start tag a browser leaves no element open for has no end tag to close foreign content with
        pytest.param("<html><svg></html><style><i>", ["html", "svg", "style", "i"], id="html-not-opened"),
        pytest.param("<head><svg></head><style><i>", ["head", "svg", "style", "i"], id="head-not-opened"),
        pytest.param("<body><svg></body><style><i>", ["body", "svg", "style", "i"], id="body-not-opened"),
        pytest.param("<frame><svg></frame><style><i>", ["frame", "svg", "style", "i"], id="frame-not-opened"),
        pytest.param("x<frameset><svg></frameset><style><i>", ["frameset", "svg", "style", "i"], id="frameset-ignored"),
        pytest.param("<image><svg></image><style><i>", ["image", "svg", "style", "i"], id="image-is-img"),
        pytest.param("<td><svg></td><style><i>", ["td", "svg", "style", "i"], id="cell-outside-table"),
        pytest.param("<p><tr><svg></tr><style><i>", ["p", "tr", "svg", "style", "i"], id="row-outside-table"),
        pytest.param(
            "<svg><desc><th><math></th><style><i>", ["svg", "desc", "th", "math", "style", "i"], id="cell-in-point"
        ),
        pytest.param("<template><td><svg></td><style><i>", ["template", "td", "svg", "style"], id="cell-in-template"),
        pytest.param(
            "<svg><desc><body><![CDATA[><!--]]><i><!---->", ["svg", "desc", "body", "i"], id="cdata-after-body"
        ),
        # an HTML end tag looks for its element as far as a browser does
        pytest.param("<div><p><svg></div><style><i>", ["div", "p", "svg", "style"], id="scope-reaches-past-special"),
        pytest.param(
            "<div><table><td><svg></div><style><i>", ["div", "table", "td", "svg", "style", "i"], id="cell-bounds-scope"
        ),
        pytest.param(
            "<div><object><svg></div><style><i>", ["div", "object", "svg", "style", "i"], id="object-bounds-scope"
        ),
        pytest.param("<span><div><svg></span><style><i>", ["span", "div", "svg", "style", "i"], id="special-bounds"),
        pytest.param("<x-y><div><svg></x-y><style><i>", ["x-y", "div", "svg", "style", "i"], id="special-bounds-any"),
        pytest.param("<span><u><svg></span><style><i>", ["span", "u", "svg", "style"], id="phrasing-does-not-bound"),
        pytest.param("<b><div><svg></b><style><i>", ["b", "div", "svg", "style"], id="formatting-in-scope"),
        pytest.param("<dialog><div><svg></dialog><style><i>", ["dialog", "div", "svg", "style"], id="dialog-in-scope"),
        pytest.param("<h1><svg></h2><style><i>", ["h1", "svg", "style"], id="heading-closes-heading"),
        pytest.param("<h1><div><svg></h6><style><i>", ["h1", "div", "svg", "style"], id="heading-in-scope"),
        pytest.param("<p><svg></h1><style><i>", ["p", "svg", "style", "i"], id="heading-stray"),
        pytest.param("<form><svg></form><style><i>", ["form", "svg", "style", "i"], id="form-leaves-content-open"),
        pytest.param("<div><form><svg></form></div><style><i>", ["div", "form", "svg", "style"], id="form-removed"),
        pytest.param(
            "<form><x-y><svg></form></x-y><style><i>", ["form", "x-y", "svg", "style"], id="form-removed-under-custom"
        ),
        pytest.param("<svg><form><g></form><style><i>", ["svg", "form", "g", "style", "i"], id="foreign-form"),
        pytest.param(
            "<table><tr><td><div><svg></td><style><i>", ["table", "tr", "td", "div", "svg", "style"], id="cell-end"
        ),
        pytest.param("<table><td><svg></table><style><i>", ["table", "td", "svg", "style"], id="table-end"),
        pytest.param(
            "<table><tr><td><table><svg></tr><style><i>",
            ["table", "tr", "td", "table", "svg", "style", "i"],
            id="table-bounds-table-scope",
        ),
        pytest.param(
            "<table><tr><template><svg></tr><style><i>",
            ["table", "tr", "template", "svg", "style", "i"],
            id="template-bounds-table-scope",
        ),
        pytest.param(
            "<template><table><td><svg></template><style><i>",
            ["template", "table", "td", "svg", "style"],
            id="template-end-anywhere",
        ),
        pytest.param(
            "<template><svg><desc><b></template><style><i>",
            ["template", "svg", "desc", "b", "style"],
            id="template-end-through-point",
        ),
        # the trailing slash ends a foreign element only
        pytest.param("<svg><title/><style><i>", ["svg", "title", "style", "i"], id="self-closing-foreign"),
        pytest.param("<svg/><style><i>", ["svg", "style"], id="self-closing-svg"),
        pytest.param("<title/><i>", ["title"], id="self-closing-html-title"),
        pytest.param("<script/><i></script><b>", ["script", "b"], id="self-closing-html-script"),
        pytest.param("<b/><svg></b><style><i>", ["b", "svg", "style"], id="self-closing-html-stays-open"),
        # a CDATA section is text where the innermost open element is SVG/MathML, a bogus comment elsewhere
        pytest.param("<svg><![CDATA[><!--]]><i><!---->", ["svg", "i"], id="cdata-in-svg"),
        pytest.param("<svg><desc><![CDATA[><!--]]><i><!---->", ["svg", "desc", "i"], id="cdata-in-point"),
        pytest.param("<svg><desc><b><![CDATA[><!--]]><i><!---->", ["svg", "desc", "b"], id="cdata-in-html"),
        pytest.param("<svg></svg><![CDATA[><!--]]><i><!---->", ["svg"], id="cdata-after-svg"),
    ],
)
def test_foreign_content_tokenizes_like_a_browser(document: str, expected: list[str]) -> None:
    assert _start_tags(tokenize(document)) == expected
    # the rules that read a start tag's attributes still see them when the caller asked for none
    bare: Final[list[Token]] = list(tokenize(document, capture_attributes=False))
    assert _start_tags(bare) == expected
    assert not any(token.attrs for token in bare)
    tokenizer: Final[Tokenizer] = Tokenizer()
    streamed: Final[list[Token]] = [token for char in document for token in tokenizer.feed(char)]
    assert _start_tags([*streamed, *tokenizer.close()]) == expected
    handler: Final[_StartTags] = _StartTags()
    dispatcher: Final[Tokenizer] = Tokenizer(capture_attributes=False)
    dispatcher.feed(document)
    dispatcher.dispatch(handler)
    dispatcher.close()
    dispatcher.dispatch(handler)
    assert handler.tags == expected
    # the tree builder agrees on which <i> is an element and which is text
    assert len(parse(document).select("i")) == expected.count("i")


def test_markup_inside_svg_title_keeps_its_attributes() -> None:
    document: Final[str] = "<svg><g><title><textarea>v</textarea></title><style><a href=1 class=k>"
    assert _start_tags(tokenize(document)) == ["svg", "g", "title", "textarea", "style", "a"]
    (anchor,) = [token for token in tokenize(document) if token.tag == "a"]
    assert anchor.attrs == [("href", "1"), ("class", "k")]


def test_cdata_section_in_foreign_content_is_text() -> None:
    tokens: Final[list[Token]] = list(tokenize("<svg><![CDATA[a<b>]]></svg><![CDATA[c]]>"))
    assert [(token.type, token.tag or token.data) for token in tokens] == [
        (TokenType.START_TAG, "svg"),
        (TokenType.TEXT, "a<b>"),
        (TokenType.END_TAG, "svg"),
        (TokenType.COMMENT, "[CDATA[c]]"),
    ]
    handler: Final[_StartTags] = _StartTags()
    tokenizer: Final[Tokenizer] = Tokenizer()
    tokenizer.feed("<math><![CDATA[<i>]]>")
    tokenizer.close()
    tokenizer.dispatch(handler)
    assert (handler.tags, handler.data) == (["math"], ["<i>"])


def test_reset_forgets_open_foreign_elements() -> None:
    tokenizer: Final[Tokenizer] = Tokenizer(capture_attributes=False)
    assert _start_tags([*tokenizer.feed("<svg><font color=red><svg><style><i>"), *tokenizer.close()]) == [
        "svg",
        "font",
        "svg",
        "style",
        "i",
    ]
    tokenizer.reset()
    tokens: Final[list[Token]] = [*tokenizer.feed("<style><i></style><![CDATA[x]]><p id=a>"), *tokenizer.close()]
    assert [(token.type, token.tag or token.data, token.attrs) for token in tokens] == [
        (TokenType.START_TAG, "style", []),
        (TokenType.TEXT, "<i>", None),
        (TokenType.END_TAG, "style", []),
        (TokenType.COMMENT, "[CDATA[x]]", None),
        (TokenType.START_TAG, "p", []),
    ]


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("<p>" * 1500, id="omitted-end-tags"),
        pytest.param("".join(f"<x-{index}>" for index in range(1500)), id="custom-names"),
        pytest.param("".join(f"<x-element-{index}>" for index in range(1500)), id="long-custom-names"),
        pytest.param(f"<svg>{'<g>' * 1500}", id="foreign"),
    ],
)
def test_open_elements_past_the_depth_cap_keep_the_innermost(prefix: str) -> None:
    assert _start_tags(tokenize(f"{prefix}<svg><title><textarea>x</textarea></title><style><i>"))[-5:] == [
        "svg",
        "title",
        "textarea",
        "style",
        "i",
    ]


def _iterated(tokenizer: Tokenizer, chunks: list[str]) -> list[str | None]:
    return _start_tags(token for chunk in chunks for token in tokenizer.feed(chunk))


def _dispatched(tokenizer: Tokenizer, chunks: list[str]) -> list[str | None]:
    handler: Final[_StartTags] = _StartTags()
    for chunk in chunks:
        tokenizer.feed(chunk)
        tokenizer.dispatch(handler)
    return [*handler.tags]


@pytest.mark.parametrize("read", [pytest.param(_iterated, id="iterate"), pytest.param(_dispatched, id="dispatch")])
def test_long_stream_still_follows_open_elements(read: Callable[[Tokenizer, list[str]], list[str | None]]) -> None:
    # the open elements are followed chunk by chunk, however much input came before the first <svg>
    chunks: Final[list[str]] = ["<div>", *["<p>words</p>"] * 10_000, "<svg><style><i></style></div><style><b>"]
    tags: Final[list[str | None]] = read(Tokenizer(), chunks)
    assert (tags[0], tags[-4:]) == ("div", ["svg", "style", "i", "style"])


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
    assert [token.type for token in tokenize(width_prefix + document)] == [
        token.type for token in tokenize(width_prefix)
    ] + kinds


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
    assert (doctype.public_id, doctype.system_id) == (public_id, system_id)


def test_large_text_runs_move_intact() -> None:
    first, second = f"&amp;{'a' * 600}", f"&gt;{'b' * 700}"
    tokens = list(tokenize(f"{first}<p>{second}"))
    expected = [f"&{'a' * 600}", f">{'b' * 700}"]
    assert [token.data for token in tokens if token.type is TokenType.TEXT] == expected


def test_tokens_outlive_the_source_string() -> None:
    text = f"{'y' * 700}<p>"
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


def test_reset_forgets_a_suspended_charref_run() -> None:
    tokenizer = Tokenizer()
    assert list(tokenizer.feed("&#x4141")) == []  # suspended in the digits, with its scan position remembered
    tokenizer.reset()
    assert list(tokenizer.feed("&#x42")) == []
    assert [token.data for token in tokenizer.close()] == ["B"]


@pytest.mark.parametrize(
    ("prefix", "run"),
    [
        pytest.param("&#x", "c", id="hex-digits"),
        pytest.param("&#", "9", id="decimal-digits"),
        pytest.param("&", "a", id="name-over-cap"),
        pytest.param("<a x='&#x", "c", id="attribute-hex-digits"),
        pytest.param("<a x=&", "a", id="attribute-name-over-cap"),
    ],
)
def test_unterminated_charref_run_streams_in_linear_time(prefix: str, run: str, work_steps: WorkSteps) -> None:
    # a reference still open at the end of a feed resumes its scan there instead of restarting at its '&'
    document = prefix + run * 20_000
    assert _stream_fed_in_chunks(document) == [_shape(token) for token in tokenize(document)]
    work_steps.assert_linear(lambda n: partial(_stream_fed_in_chunks, prefix + run * n), (5_000, 20_000))


def _stream_fed_in_chunks(document: str) -> list[_Shape]:
    tokenizer = Tokenizer()
    tokens: list[Token] = []
    for offset in range(0, len(document), 7):
        tokens.extend(tokenizer.feed(document[offset : offset + 7]))
    tokens.extend(tokenizer.close())
    return [_shape(token) for token in tokens]


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("&#", id="decimal-text"),
        pytest.param("&#x", id="hex-text"),
        pytest.param("<title>&#", id="rcdata"),
        pytest.param('<a x="&#', id="attribute-decimal"),
        pytest.param('<a x="&#x', id="attribute-hex"),
    ],
)
def test_unterminated_numeric_reference_holds_no_digits(prefix: str, peak_bytes: PeakBytes) -> None:
    # the digits of a numeric reference are never output, so a suspended run keeps only its prefix in the buffer
    peak: Final[int] = peak_bytes(
        "import tracemalloc\n"
        "from turbohtml import Tokenizer\n"
        f"prefix = {prefix!r}\n"
        "block = '9' * 65536\n"
        "tokenizer = Tokenizer()\n"
        "list(tokenizer.feed(prefix))\n"
        "tracemalloc.start()\n"
        "for _ in range(64):\n"
        "    list(tokenizer.feed(block))\n"
        "print(tracemalloc.get_traced_memory()[1])\n",
        (),
    )
    assert peak < 256 * 1024, peak


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("&#", id="decimal-text"),
        pytest.param("&#x", id="hex-text"),
        pytest.param("<title>&#", id="rcdata"),
        pytest.param('<a x="&#', id="attribute-decimal"),
        pytest.param('<a x="&#x', id="attribute-hex"),
        pytest.param("&#X", id="upper-hex-text"),
    ],
)
@pytest.mark.parametrize("ending", ["", ";", " ", "x;"])
def test_streamed_numeric_reference_matches_one_shot(prefix: str, ending: str, width_prefix: str) -> None:
    # the streamed scan drops the digits it has passed, so the reference must still end, report and advance as one-shot
    document: Final[str] = f"{width_prefix}{prefix}{'9' * 3000}{ending}tail"
    whole: Final[Document] = parse(document)
    streamed: Final[IncrementalParser] = IncrementalParser()
    for offset in range(0, len(document), 7):
        streamed.feed(document[offset : offset + 7])
    split: Final[Document] = streamed.close()
    assert _stream_fed_in_chunks(document) == [_shape(token) for token in tokenize(document)]
    assert [(error.code, error.line, error.col) for error in split.errors] == [
        (error.code, error.line, error.col) for error in whole.errors
    ]
    assert split.html == whole.html


@pytest.mark.parametrize("element", ["", "<title>"])
def test_unterminated_named_reference_holds_no_run(element: str, peak_bytes: PeakBytes) -> None:
    # past the longest entity name the rest of the run is text, so it leaves the input buffer as it arrives and costs
    # what the same text without a reference costs, rather than that plus the held input
    script: Final[str] = (
        "import sys, tracemalloc\n"
        "from turbohtml import Tokenizer\n"
        "block = 'a' * 65536\n"
        "tokenizer = Tokenizer()\n"
        "list(tokenizer.feed(sys.argv[1]))\n"
        "tracemalloc.start()\n"
        "for _ in range(64):\n"
        "    list(tokenizer.feed(block))\n"
        "print(tracemalloc.get_traced_memory()[1])\n"
    )
    plain: Final[int] = peak_bytes(script, (f"{element}a",))
    assert peak_bytes(script, (f"{element}&",)) < plain + 256 * 1024, plain


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("x&", id="text"),
        pytest.param("<title>x&", id="rcdata"),
        pytest.param('<a x="x&', id="attribute-quoted"),
        pytest.param("<a x=x&", id="attribute-unquoted"),
        pytest.param('<a x="x&notin', id="attribute-legacy"),
    ],
)
@pytest.mark.parametrize("stem", ["notin", "aaaaaa"])
@pytest.mark.parametrize(
    "ending",
    [
        pytest.param("", id="end"),
        pytest.param(";", id="semicolon"),
        pytest.param(" ", id="space"),
        pytest.param("x;", id="letter-semicolon"),
        pytest.param("<", id="lt"),
        pytest.param("=", id="equals"),
        pytest.param("&", id="ampersand"),
        pytest.param("é", id="non-ascii"),
        pytest.param(":", id="colon"),
        pytest.param("\n", id="newline"),
        pytest.param("9", id="digit"),
    ],
)
def test_streamed_named_reference_matches_one_shot(prefix: str, stem: str, ending: str, width_prefix: str) -> None:
    # a run past the longest entity name is emitted as text as it streams, and must still read as one-shot does
    document: Final[str] = f"{width_prefix}{prefix}{stem}{'x' * 60}{ending}tail"
    whole: Final[Document] = parse(document)
    streamed: Final[IncrementalParser] = IncrementalParser()
    for offset in range(0, len(document), 7):
        streamed.feed(document[offset : offset + 7])
    split: Final[Document] = streamed.close()
    assert split.html == whole.html
    assert [(error.code, error.line, error.col) for error in split.errors] == [
        (error.code, error.line, error.col) for error in whole.errors
    ]
    assert _stream_fed_in_chunks(document) == [_shape(token) for token in tokenize(document)]


@pytest.mark.parametrize(
    ("document", "codes"),
    [
        pytest.param(f"<p>&{'a' * 40};x</p>", [("unknown-named-character-reference", 1, 44)], id="text-unknown"),
        pytest.param(
            f"<p>&notin{'x' * 40};</p>",
            [("missing-semicolon-after-character-reference", 1, 7)],
            id="text-prefix-match",
        ),
        pytest.param(
            f"<title>&{'a' * 40};</title>", [("unknown-named-character-reference", 1, 48)], id="rcdata-unknown"
        ),
        pytest.param(f'<a x="&notin{"x" * 40}">z</a>', [], id="attribute-legacy"),
    ],
)
def test_named_reference_past_longest_name_reports_as_one_shot(
    document: str, codes: list[tuple[str, int, int]], width_prefix: str
) -> None:
    streamed: Final[IncrementalParser] = IncrementalParser()
    prefixed: Final[str] = width_prefix + document
    for offset in range(0, len(prefixed), 5):
        streamed.feed(prefixed[offset : offset + 5])
    shift: Final[int] = len(width_prefix)
    expected: Final[list[tuple[str, int, int]]] = [(code, line, col + shift) for code, line, col in codes]
    assert [(error.code, error.line, error.col) for error in parse(prefixed).errors] == expected
    assert [(error.code, error.line, error.col) for error in streamed.close().errors] == expected


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("<p>&acE;x</p>", id="two-code-points"),
        pytest.param("<p>&acE</p>", id="two-code-points-no-semicolon"),
        pytest.param('<a x="&notin=1">z</a>', id="legacy-equals"),
        pytest.param('<a x="&notin1">z</a>', id="legacy-digit"),
        pytest.param("<p>x&ampy</p>", id="prefix-then-letter"),
        pytest.param("<p>x&amp y</p>", id="exact-no-semicolon"),
        pytest.param("<p>&notit;</p>", id="prefix-unknown-semicolon"),
        pytest.param('<a x="&notin ">z</a>', id="attribute-space-after"),
        pytest.param('<a x="&notin&">z</a>', id="attribute-ampersand-after"),
        pytest.param('<a x="&amp=1">z</a>', id="attribute-legacy-equals"),
        pytest.param('<a x="&copy ">z</a>', id="attribute-legacy-space"),
        pytest.param('<a x="&amp:">z</a>', id="attribute-legacy-colon"),
        pytest.param('<a x="&amp&">z</a>', id="attribute-legacy-ampersand"),
        pytest.param('<a x="&amp1">z</a>', id="attribute-legacy-digit"),
    ],
)
def test_named_reference_shapes_stream_as_one_shot(document: str, width_prefix: str) -> None:
    assert_streaming_matches_whole(width_prefix + document)


def assert_streaming_matches_whole(document: str) -> None:
    # Feeding one character at a time must match one-shot tokenization.
    tokenizer = Tokenizer()
    streamed = [token for char in document for token in tokenizer.feed(char)]
    streamed += list(tokenizer.close())
    whole = list(tokenize(document))
    assert [_shape(token) for token in streamed] == [_shape(token) for token in whole]
    assert [(token.line, token.col) for token in streamed] == [(token.line, token.col) for token in whole]


@pytest.mark.parametrize(
    "document",
    [
        pytest.param(f"<p>&{'a' * 40};x</p>", id="text"),
        pytest.param(f"<p>&notin{'x' * 40}</p>", id="prefix-match"),
        pytest.param(f'<a x="&notin{"x" * 40}">z</a>', id="attribute"),
        pytest.param(f"<p>&#{'9' * 40};x</p>", id="numeric"),
        pytest.param(f"<p>&#X{'9' * 40}x</p>", id="numeric-upper-hex-run"),
    ],
)
def test_named_reference_with_source_locations_streams_as_one_shot(document: str, width_prefix: str) -> None:
    prefixed: Final[str] = width_prefix + document
    streamed: Final[IncrementalParser] = IncrementalParser(source_locations=True)
    for offset in range(0, len(prefixed), 7):
        streamed.feed(prefixed[offset : offset + 7])
    whole: Final[Document] = parse(prefixed, source_locations=True)
    split: Final[Document] = streamed.close()
    assert split.html == whole.html
    assert [(error.code, error.line, error.col) for error in split.errors] == [
        (error.code, error.line, error.col) for error in whole.errors
    ]


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"resolve_references": False}, id="split"),
        pytest.param({"resolve_references": False, "capture_source": True}, id="split-captured"),
        pytest.param({"capture_source": True}, id="captured"),
        pytest.param({"capture_attributes": False}, id="attributes-uncaptured"),
    ],
)
@pytest.mark.parametrize(
    ("prefix", "filler"),
    [
        pytest.param("x&", "notin", id="named-text"),
        pytest.param('<a x="x&', "notin", id="named-attribute"),
        pytest.param("<title>x&", "notin", id="named-rcdata"),
        pytest.param("x&#", "9", id="numeric-text"),
        pytest.param("x&#X", "9", id="numeric-upper-hex"),
    ],
)
def test_streamed_named_reference_matches_one_shot_in_each_mode(
    options: dict[str, bool], prefix: str, filler: str, width_prefix: str
) -> None:
    document: Final[str] = f"{width_prefix}{prefix}{filler}{'x' * 60};tail"
    tokenizer = Tokenizer(**options)
    streamed = [_shape(token) for char in document for token in tokenizer.feed(char)]
    streamed += [_shape(token) for token in tokenizer.close()]
    assert streamed == [_shape(token) for token in tokenize(document, **options)]


@pytest.mark.parametrize("prefix", ["", "<title>"])
@pytest.mark.parametrize("reference", ["&", "&quot"])
def test_long_named_reference_run_streams_as_one_text_token(prefix: str, reference: str, width_prefix: str) -> None:
    # a run past the longest name leaves the input as it arrives, yet stays one text token as in a one-shot tokenize
    document: Final[str] = f"{width_prefix}{prefix}{reference}{'a' * 9_000};tail"
    tokenizer = Tokenizer()
    streamed: list[_Shape] = []
    for offset in range(0, len(document), 98):
        streamed.extend(_shape(token) for token in tokenizer.feed(document[offset : offset + 98]))
    streamed.extend(_shape(token) for token in tokenizer.close())
    assert [shape.data[-9:] for shape in streamed if shape.data and "aaaa" in shape.data] == ["aaaa;tail"]
    assert streamed == [_shape(token) for token in tokenize(document)]


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
        pytest.param(f"&#x{'0' * 40}41;&#{'0' * 40}66;&#x{'f' * 40};&#{'9' * 40}", id="charref-long-digit-runs"),
        pytest.param(f"&{'a' * 40}&#x41;&#{'0' * 40}66&{'b' * 40};&#67;", id="charref-runs-back-to-back"),
        pytest.param(f"<a x='&#x{'0' * 40}41;' y=&{'a' * 40}; z=\"&#{'0' * 40}66\">", id="attribute-charref-runs"),
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

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # ruff:ignore[unused-method-argument]  # the callback signature is fixed by the protocol; this argument is unused
            self.positions.append(self.getpos())

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:  # ruff:ignore[unused-method-argument]  # the callback signature is fixed by the protocol; this argument is unused
            self.positions.append(self.getpos())

        def handle_data(self, data: str) -> None:  # ruff:ignore[unused-method-argument]  # the callback signature is fixed by the protocol; this argument is unused
            self.positions.append(self.getpos())

        def handle_comment(self, data: str) -> None:  # ruff:ignore[unused-method-argument]  # the callback signature is fixed by the protocol; this argument is unused
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
    ],
)
def test_api_rejects_bad_arguments(call: Callable[[], object], exc: type[Exception], match: str | None) -> None:
    with pytest.raises(exc, match=match):
        call()


def test_tag_names_are_lowercased() -> None:
    assert [next(iter(tokenize(document))).tag for document in ("<DIV>", "<SpAn>")] == ["div", "span"]


def test_streaming_tokenizer_reclaims_consumed_input(retained_bytes: RetainedBytes) -> None:
    # feed() compacts the consumed prefix, so a long-lived streaming tokenizer stays memory
    # bounded instead of growing its input buffer with every chunk (issue #80).
    tokenizer = Tokenizer()
    for _ in range(200):  # warm up so one-time allocations settle out of the measurement
        list(tokenizer.feed("<p>hello world</p>"))
    for _ in range(10_000):
        list(tokenizer.feed("<p>hello world</p>"))
    retained = retained_bytes(__file__)
    for _ in range(10_000):
        list(tokenizer.feed("<p>hello world</p>"))
    growth = retained_bytes(__file__) - retained
    # without compaction the input buffer grows by about 180 KB in the second batch
    assert growth < 100_000, f"streaming feed retained {growth} more bytes"


def test_feed_keeps_input_while_a_token_is_queued() -> None:
    # a completed token not yet pulled from the iterator still spans the input buffer, so the
    # next feed must skip compaction; the full stream stays correct either way.
    tokenizer = Tokenizer()
    stream = tokenizer.feed("x<a>")
    first = next(stream)  # drives the state machine, leaving a record queued behind the one returned
    tokenizer.feed("z")
    tokens = [first, *tokenizer, *tokenizer.close()]
    assert [_shape(token) for token in tokens] == [_shape(token) for token in tokenize("x<a>z")]


def test_empty_feed_preserves_pending_text_after_malformed_tag() -> None:
    tokenizer = Tokenizer(capture_source=True)
    tokens = [*tokenizer.feed("<b/bod"), *tokenizer.feed("y></>\n"), *tokenizer.feed(""), *tokenizer.close()]
    assert [_shape(token) for token in tokens] == [_shape(token) for token in tokenize("<b/body></>\n")]


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


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(0, id="empty"),
        pytest.param(1, id="one"),
        pytest.param(10, id="ten"),
        pytest.param(100, id="hundred"),
    ],
)
def test_token_attrs_order(count: int) -> None:
    expected: Final[list[tuple[str, str]]] = [(f"a{index}", str(index)) for index in range(count)]
    (token,) = tokenize(f"<p {' '.join(f'{name}={value}' for name, value in expected)}>")
    assert token.attrs == expected


@pytest.mark.parametrize("document", [pytest.param("<p>", id="empty"), pytest.param("<p a=1 b=2>", id="populated")])
def test_token_attrs_returns_fresh_list(document: str) -> None:
    (token,) = tokenize(document)
    expected: Final[list[tuple[str, str]] | None] = token.attrs
    attrs: Final[list[tuple[str, str]] | None] = token.attrs
    assert attrs is not None
    attrs[:] = [("replacement", "changed")]
    assert token.attrs == expected


def test_many_distinct_attributes_parse_in_linear_time(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(lambda n: partial(_attrs_of, "".join(f" a{i}" for i in range(n))), (2000, 8000))
    assert len(_attrs_of("".join(f" a{index}" for index in range(8000)))) == 8000  # every distinct name is kept


def _attrs_of(pairs: str) -> list[tuple[str, str]]:
    (tag,) = list(tokenize(f"<a{pairs}>"))
    assert tag.attrs is not None  # a start tag always exposes its attribute list
    return tag.attrs


def test_many_distinct_attributes_keep_their_names_and_order() -> None:
    attrs = _attrs_of("".join(f" a{index}" for index in range(2000)))
    assert [name for name, _ in attrs] == [f"a{index}" for index in range(2000)]


def test_many_duplicate_attributes_collapse_to_the_first() -> None:
    assert _attrs_of(" x" * 60_000) == [("x", "")]  # duplicates dropped early, count never grows


def test_interleaved_duplicates_keep_first_occurrence_order() -> None:
    attrs = _attrs_of(" a b a c b d a")  # repeats reuse the dropped slot, first wins
    assert [name for name, _ in attrs] == ["a", "b", "c", "d"]


def test_hash_index_resets_between_consecutive_large_tags() -> None:
    # Two big tags back to back: the second must not see the first tag's names, so the
    # per-tag epoch reset and the grow that re-seats live entries both have to be right.
    first = f"<a{''.join(f' p{index}' for index in range(1500))}>"
    second = f"<b{''.join(f' p{index}' for index in range(1500))}>"
    counts = [len(token.attrs) for token in tokenize(first + second) if token.attrs is not None]
    assert counts == [1500, 1500]  # neither tag drops a name


def test_default_resolves_references_into_text() -> None:
    head, tag = list(tokenize("a&amp;b<p>"))
    assert _reference_shape(head) == (TokenType.TEXT, None, "a&b", None, None, False)
    assert tag.type is TokenType.START_TAG


def _reference_shape(token: Token) -> _ReferenceShape:
    return _ReferenceShape(token.type, token.tag, token.data, token.source, token.attrs, token.self_closing)


class _ReferenceShape(NamedTuple):
    token_type: TokenType
    tag: str | None
    data: str | None
    source: str | None
    attrs: list[tuple[str, str]] | None
    self_closing: bool


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
    assert (reference.data, reference.source) == (data, source)
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


def _refs(tokens: Iterable[Token]) -> list[tuple[str | None, str | None]]:
    return [(token.data, token.source) for token in tokens if token.type is TokenType.CHARACTER_REFERENCE]


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
    assert (token.tag, token.source) == (tag, source)


def test_capture_source_leaves_text_without_source() -> None:
    start, text, end = list(tokenize("<p>hi</p>", capture_source=True))
    assert start.source == "<p>"
    assert text.type is TokenType.TEXT
    assert text.source is None
    assert end.source == "</p>"


def test_capture_source_preserves_original_casing_and_whitespace() -> None:
    # the resolved tag name is lowercased, but the verbatim source keeps the original spelling
    (tag,) = list(tokenize("<DIV  Class = 'x' >", capture_source=True))
    assert (tag.tag, tag.source) == ("div", "<DIV  Class = 'x' >")


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


def _stream_char_by_char(document: str, *, resolve_references: bool, capture_source: bool) -> list[Token]:
    tokenizer = Tokenizer(resolve_references=resolve_references, capture_source=capture_source)
    streamed = [token for char in document for token in tokenizer.feed(char)]
    streamed += list(tokenizer.close())
    return streamed


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


@pytest.mark.parametrize(
    ("source", "data"),
    [
        pytest.param("<svg><![CDATA[a]]></svg>", "a", id="section"),
        pytest.param("<svg>y<![CDATA[z]]>w</svg>", "yzw", id="text-around-section"),
        pytest.param("<svg><![CDATA[a", "a", id="eof"),
    ],
)
def test_tokenize_cdata_section_text_in_each_storage_width(width_prefix: str, source: str, data: str) -> None:
    # the prefix sets the input's storage width, so each width's copy of the tokenizer reads the section
    texts = [token.data for token in tokenize(width_prefix + source) if token.type is TokenType.TEXT]
    assert texts == [text for text in (width_prefix, data) if text]
