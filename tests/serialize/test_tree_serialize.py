"""Content accessors: text, data, name, namespace, and HTML serialization."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from xml.etree import (  # ruff:ignore[suspicious-xml-etree-import]  # parsing turbohtml's own output, not untrusted input
    ElementTree as ET,
)

import pytest

from turbohtml import (
    Canonical,
    Comment,
    Doctype,
    Element,
    Formatter,
    Html,
    Indent,
    Markdown,
    Minify,
    Namespace,
    Node,
    PlainText,
    ProcessingInstruction,
    Text,
    parse,
    parse_fragment,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from wpt_tree_corpus import WptHtmlTreeCorpus


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<body><p>a<b>b</b><i>c</i></p>d</body>", "body", "abcd", id="nested-elements"),
        pytest.param("<p>a&amp;b\U0001f600c</p>", "p", "a&b\U0001f600c", id="entities-and-astral-span"),
        pytest.param("<body></body>", "body", "", id="empty"),
    ],
)
def test_text_concatenates_descendant_character_data(
    find: Callable[[str, str], Element], html: str, selector: str, expected: str
) -> None:
    assert find(html, selector).text == expected


@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<p>x</p>", "p", "<p>x</p>", id="element"),
        pytest.param("<p class='a b'>x</p>", "p", '<p class="a b">x</p>', id="attribute-quoting"),
        pytest.param("<input disabled>", "input", '<input disabled="">', id="void-no-end-tag"),
        pytest.param("<br>", "br", "<br>", id="void-br"),
        pytest.param("<frameset><frame>", "frameset", "<frameset><frame></frameset>", id="void-frame"),
        pytest.param("<p>a<b>c</b>d</p>", "p", "<p>a<b>c</b>d</p>", id="nested"),
        pytest.param("<style>a > b { x }</style>", "style", "<style>a > b { x }</style>", id="rawtext-style"),
        pytest.param("<script>1 < 2 && 3</script>", "script", "<script>1 < 2 && 3</script>", id="rawtext-script"),
        pytest.param("<xmp><b></xmp>", "xmp", "<xmp><b></xmp>", id="rawtext-xmp"),
        pytest.param("<plaintext>a & <b>", "plaintext", "<plaintext>a & <b></plaintext>", id="rawtext-plaintext"),
        pytest.param(
            "<body><noscript>a&lt;b</noscript>", "noscript", "<noscript>a&lt;b</noscript>", id="noscript-escaped"
        ),
        pytest.param("<svg><circle r=5></circle></svg>", "svg", '<svg><circle r="5"></circle></svg>', id="foreign-svg"),
        pytest.param("<svg><title>a&b</title></svg>", "svg", "<svg><title>a&amp;b</title></svg>", id="foreign-text"),
    ],
)
def test_html_serialization(find: Callable[[str, str], Element], html: str, selector: str, expected: str) -> None:
    assert find(html, selector).html == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a&lt;b&gt;c", "a&lt;b&gt;c", id="angle-brackets"),
        pytest.param("a&amp;b", "a&amp;b", id="ampersand"),
        pytest.param("a&nbsp;b", "a&nbsp;b", id="nbsp"),
    ],
)
def test_text_is_escaped_in_html(find: Callable[[str, str], Element], source: str, expected: str) -> None:
    assert find(f"<p>{source}</p>", "p").html == f"<p>{expected}</p>"


@pytest.mark.parametrize(
    ("markup", "expected_value"),
    [
        pytest.param('"a&b"', "&amp;", id="amp-in-value"),
        pytest.param('"a\u00a0b"', "&nbsp;", id="nbsp-in-value"),
        pytest.param("'say \"hi\"'", "&quot;", id="quote-in-value"),
    ],
)
def test_attribute_values_are_escaped(find: Callable[[str, str], Element], markup: str, expected_value: str) -> None:
    assert expected_value in find(f"<p title={markup}>x</p>", "p").html


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        pytest.param("checked", "", id="valueless"),
        pytest.param("type", "text", id="valued"),
    ],
)
def test_attrs_mapping(find: Callable[[str, str], Element], attr: str, value: str) -> None:
    markup = f"{attr}={value}" if value else attr
    assert find(f"<input {markup}>", "input").attrs[attr] == value


@pytest.mark.parametrize(
    ("context", "selector", "expected"),
    [
        pytest.param("<div>", "div", Namespace.HTML, id="html"),
        pytest.param("<svg><circle></svg>", "circle", Namespace.SVG, id="svg"),
        pytest.param("<math><mi>x</mi></math>", "mi", Namespace.MATHML, id="mathml"),
    ],
)
def test_namespace(find: Callable[[str, str], Element], context: str, selector: str, expected: Namespace) -> None:
    assert find(f"<body>{context}</body>", selector).namespace is expected


@pytest.mark.parametrize(
    ("html", "node_type", "expected"),  # expected = (data, text, serialized)
    [
        pytest.param("<p>hello</p>", Text, ("hello", "hello", "hello"), id="text"),
        pytest.param("<!--c-->", Comment, ("c", "", "<!--c-->"), id="comment"),
        pytest.param("<!---->", Comment, ("", "", "<!---->"), id="empty-comment"),
    ],
)
def test_leaf_node_accessors(
    first_of_type: Callable[[str, type[Node]], Node],
    html: str,
    node_type: type[Node],
    expected: tuple[str, str, str],
) -> None:
    data, text, serialized = expected
    node = first_of_type(html, node_type)
    assert node.data == data  # ty: ignore[unresolved-attribute]  # Text and Comment both expose .data
    assert node.text == text  # .text counts Text descendants only, so a comment contributes nothing
    assert node.html == serialized


@pytest.mark.parametrize(
    ("doctype", "name"),
    [
        pytest.param("<!DOCTYPE html>", "html", id="bare"),
        pytest.param('<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN">', "html", id="with-public-id"),
    ],
)
def test_doctype_name(first_of_type: Callable[[str, type[Node]], Node], doctype: str, name: str) -> None:
    node = first_of_type(doctype, Doctype)
    assert node.name == name  # ty: ignore[unresolved-attribute]  # node is a Doctype here
    assert node.html == f"<!DOCTYPE {name}>"


def test_document_html_round_trips() -> None:
    source = "<!DOCTYPE html><html><head></head><body><p>hi</p></body></html>"
    assert parse(source).html == source


def test_document_text_skips_comments_and_doctype() -> None:
    doc = parse("<!-- c --><!DOCTYPE html><html><body>hello</body></html>")
    assert doc.text == "hello"


def test_template_content_serializes_and_collects_text(find: Callable[[str, str], Element]) -> None:
    template = find("<template>inner</template>", "template")
    assert template.html == "<template>inner</template>"
    assert template.text == "inner"


# the chunk stream must join back to exactly the one-shot output, for every document
# shape and every options object it supports (Minify, which needs the whole tree, is
# rejected instead and covered separately)
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "<!doctype html><html><head><title>t</title></head><body><p>a&amp;b</p></body></html>", id="document"
        ),
        pytest.param("<div id=x class='a b'><span>text</span><br><img src=y></div>", id="void-and-attrs"),
        pytest.param("<ul>\n  <li>one</li>\n  <li>two</li>\n</ul>", id="nested-whitespace"),
        pytest.param("<style>a > b { c: d }</style><script>1 < 2 && 3</script>", id="rawtext"),
        pytest.param("<pre>keep\n  this\nverbatim</pre>", id="pre-preserve"),
        pytest.param("<svg><circle r=5></circle></svg><!--note-->", id="foreign-and-comment"),
        pytest.param("plain text with a &amp; and \U0001f600 astral", id="text-only"),
        pytest.param("", id="empty"),
    ],
)
@pytest.mark.parametrize(
    "options",
    [
        pytest.param(None, id="defaults"),
        pytest.param(Html(), id="empty-config"),
        pytest.param(Html(layout=Indent(2)), id="indent"),
        pytest.param(Html(layout=Indent("\t")), id="indent-tab"),
        pytest.param(Html(sort_attributes=True), id="sort-attributes"),
        pytest.param(Html(meta_charset=True), id="meta-charset"),
        pytest.param(Html(layout=Indent(2), sort_attributes=True, meta_charset=True), id="indent-sort-meta"),
    ],
)
def test_serialize_iter_joins_to_serialize(source: str, options: Html | None) -> None:
    root = parse(source)
    assert "".join(root.serialize_iter(options)) == root.serialize(options)


def test_serialize_iter_matches_serialize_on_an_element_subtree(find: Callable[[str, str], Element]) -> None:
    node = find("<body><section><p z=1 a=2>hi</p><p>bye</p></section></body>", "section")
    assert "".join(node.serialize_iter()) == node.serialize()


def test_serialize_iter_empty_subtree_yields_no_chunks() -> None:
    # a subtree that serializes to "" finishes without ever yielding a chunk, so the
    # stream is empty rather than carrying a trailing "" (the terminal empty chunk)
    document = parse("")
    for child in list(document.children):
        child.decompose()
    assert not document.serialize()
    assert list(document.serialize_iter()) == []


@pytest.mark.parametrize("layout", [pytest.param(None, id="compact"), pytest.param(Indent(2), id="indent")])
def test_serialize_iter_bounds_each_chunk_and_joins(layout: Indent | None) -> None:
    # a document past the ~8 KiB chunk size must split into several bounded chunks
    # that still reconstruct the one-shot output exactly, under either walk
    source = "".join(f"<section id='s{index}'><p>row {index} &amp; more</p></section>" for index in range(400))
    root = parse(source)
    options = Html(layout=layout)
    chunks = list(root.serialize_iter(options))
    assert len(chunks) > 1
    assert max(len(chunk) for chunk in chunks[:-1]) < 32 * 1024  # a per-node boundary keeps chunks near the target
    assert "".join(chunks) == root.serialize(options)


def test_serialize_iter_emits_a_huge_text_node_as_one_chunk(find: Callable[[str, str], Element]) -> None:
    # a single text node larger than the chunk target cannot be split mid-node, so it
    # rides in one oversized chunk rather than being dropped or truncated
    body = find(f"<body>{'x' * 50000}</body>", "body")
    chunks = list(body.serialize_iter())
    assert "".join(chunks) == body.serialize()


def test_serialize_iter_streams_to_a_file_like_sink() -> None:
    root = parse("<html><body><p>chunked</p><ul><li>a</li><li>b</li></ul></body></html>")
    sink = io.StringIO()
    for chunk in root.serialize_iter():
        sink.write(chunk)
    assert sink.getvalue() == root.serialize()


def test_serialize_iter_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        parse("<p>x</p>").serialize_iter(Html(), Html())  # ty: ignore[too-many-positional-arguments]  # a second arg is rejected


def test_serialize_iter_rejects_a_minify_layout() -> None:
    with pytest.raises(ValueError, match="cannot stream a Minify layout"):
        list(parse("<p>x</p>").serialize_iter(Html(layout=Minify())))


def test_serialize_iter_rejects_a_non_layout() -> None:
    with pytest.raises(TypeError, match="layout must be an Indent"):
        parse("<p>x</p>").serialize_iter(Html(layout=True))  # ty: ignore[invalid-argument-type]  # non-layout rejected


def test_serialize_iter_rejects_a_non_formatter() -> None:
    with pytest.raises(TypeError, match="formatter must be a Formatter"):
        parse("<p>x</p>").serialize_iter(Html(formatter=object()))  # ty: ignore[invalid-argument-type]  # non-formatter rejected


def test_serialize_iter_rejects_another_renderers_config() -> None:
    with pytest.raises(TypeError, match="options must be a Html, not Markdown"):
        parse("<p>x</p>").serialize_iter(Markdown())  # ty: ignore[invalid-argument-type]  # the wrong config class is rejected


def test_serialize_iter_propagates_a_raising_truthiness() -> None:
    # the Html bool fields are read with PyObject_IsTrue, so a value whose __bool__
    # raises surfaces the error instead of being silently coerced
    class _Boom:
        def __bool__(self) -> bool:
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        parse("<p>x</p>").serialize_iter(Html(sort_attributes=_Boom()))  # ty: ignore[invalid-argument-type]  # a raising __bool__ on purpose


@pytest.mark.parametrize("layout", [pytest.param(None, id="compact"), pytest.param(Indent(2), id="indent")])
def test_serialize_iter_joins_to_serialize_over_corpus(
    wpt_html_tree_corpus: WptHtmlTreeCorpus, layout: Indent | None
) -> None:
    options = Html(layout=layout)
    mismatches = [
        f"{case['file']}: {data!r}"
        for case in wpt_html_tree_corpus["cases"]
        if case["context"] is None and case["scripting"] is not True
        for data in [case["data"]]
        if "".join((root := parse(data)).serialize_iter(options)) != root.serialize(options)
    ]
    assert not mismatches, f"{len(mismatches)} chunked outputs differ\n\n" + "\n\n".join(mismatches[:5])


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<!---->", "<!----><html><head></head><body></body></html>", id="empty-comment"),
        pytest.param(
            "<img sc<a.png alt=pic><br><hr><input>\n<!--",
            '<html><head></head><body><img sc<a.png="" alt="pic"><br><hr><input>\n<!----></body></html>',
            id="fuzz-empty-run",
        ),
    ],
)
def test_serialize_empty_run(html: str, expected: str) -> None:
    # An empty comment (or an unclosed <!-- the parser closes) serializes a zero-length run whose text pointer is
    # NULL; the run must be skipped rather than handed to memcpy, whose source is declared non-null even for length 0.
    assert parse(html).serialize() == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param('a<&>"', id="escaping"),
        pytest.param("\x7f\u0080\u07ff", id="two-byte-boundaries"),
        pytest.param("\u0800\ud7ff\ue000\uffff", id="three-byte-boundaries"),
        pytest.param("\U00010000\U0010ffff", id="four-byte-boundaries"),
    ],
)
@pytest.mark.parametrize("encoding", ["utf-8", "UTF8", "utf-16-le"])
@pytest.mark.parametrize(
    "options",
    [
        pytest.param(Html(), id="compact"),
        pytest.param(Html(layout=Indent("é😀")), id="indent"),
        pytest.param(Html(layout=Minify()), id="minify"),
        pytest.param(Html(xml=True), id="xml"),
    ],
)
@pytest.mark.parametrize("inner", [False, True], ids=["outer", "inner"])
def test_encode_unicode(text: str, encoding: str, options: Html, *, inner: bool) -> None:
    root: Final = Element("p", children=[Text(text)])
    assert root.encode(encoding, options, inner=inner) == root.serialize(options, inner=inner).encode(encoding)


@pytest.mark.parametrize("text", ["\ud800", "a\udfff", "é😀\ud800\udfff"])
@pytest.mark.parametrize("inner", [False, True], ids=["outer", "inner"])
def test_encode_surrogate_error(text: str, *, inner: bool) -> None:
    root: Final = Element("p", children=[Text(text)])
    with pytest.raises(UnicodeEncodeError) as expected:
        root.serialize(inner=inner).encode()
    with pytest.raises(UnicodeEncodeError) as actual:
        root.encode(inner=inner)
    assert actual.value.args == expected.value.args


@pytest.mark.parametrize(
    "unit",
    [
        pytest.param("", id="empty"),
        pytest.param(" ", id="space"),
        pytest.param("\t ", id="mixed"),
        pytest.param("é😀", id="unicode"),
        pytest.param("\t " * 1000, id="chunk-boundary"),
    ],
)
@pytest.mark.parametrize("inner", [False, True], ids=["outer", "inner"])
@pytest.mark.parametrize("method", ["serialize", "encode", "stream"])
def test_indent_repetition(unit: str, *, inner: bool, method: str) -> None:
    root: Final = parse_fragment("<section><p>x</p></section>")
    options: Final = Html(layout=Indent(unit))
    expected: Final = (
        f"<section>\n{unit}<p>\n{unit * 2}x\n{unit}</p>\n</section>"
        if inner
        else f"<div>\n{unit}<section>\n{unit * 2}<p>\n{unit * 3}x\n{unit * 2}</p>\n{unit}</section>\n</div>"
    )
    if method == "stream":
        output: Final = "".join(root.serialize_iter(options, inner=inner))
    elif method == "encode":
        output = root.encode(options=options, inner=inner).decode()
    else:
        output = root.serialize(options, inner=inner)
    assert output == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<p>a<b>b</b>c</p>", "a<b>b</b>c", id="mixed"),
        pytest.param("<p></p>", "", id="empty"),
        pytest.param("<script>a < b && c</script>", "a < b && c", id="raw-text"),
        pytest.param("<pre>\n\n a</pre>", "\n a", id="leading-newline"),
        pytest.param("<template><b>x</b></template>", "<b>x</b>", id="template"),
        pytest.param("<p>a&amp;b</p>", "a&amp;b", id="escaping"),
        pytest.param("<br>", "", id="void"),
    ],
)
def test_inner_compact(source: str, expected: str) -> None:
    root: Final = parse_fragment(source).children[0]
    assert root.serialize(inner=True) == expected


@pytest.mark.parametrize("layout", [None, Indent(), Minify()])
@pytest.mark.parametrize("node", [Text("x"), Comment("x"), Element("p")])
def test_inner_empty(layout: Indent | Minify | None, node: Node) -> None:
    assert not node.serialize(Html(layout=layout), inner=True)


@pytest.mark.parametrize("layout", [None, Indent(), Minify()])
def test_inner_document(layout: Indent | Minify | None) -> None:
    root: Final = parse("<p>a</p>")
    options: Final = Html(layout=layout)
    assert root.serialize(options, inner=True) == root.serialize(options)


@pytest.mark.parametrize("tag", ["pre", "textarea", "listing", "script", "style"])
@pytest.mark.parametrize("layout", [Indent(), Minify()])
def test_inner_preserve(tag: str, layout: Indent | Minify) -> None:
    root: Final = Element(tag, children=[Text("  a\n b  ")])
    assert root.serialize(Html(layout=layout), inner=True) == "  a\n b  "


def test_inner_indent() -> None:
    root: Final = parse_fragment("<div><p>a</p><p>b</p></div>").children[0]
    assert root.serialize(Html(layout=Indent()), inner=True) == "<p>\n  a\n</p>\n<p>\n  b\n</p>"


def test_inner_minify_state() -> None:
    root: Final = Element("div", children=[Text("a "), Comment("x"), Text(" b")])
    assert root.serialize(Html(layout=Minify()), inner=True) == "a b"


def test_inner_xml() -> None:
    root: Final = parse_fragment("<p>a<br>b</p>").children[0]
    assert root.serialize(Html(xml=True), inner=True) == "a<br/>b"


@pytest.mark.parametrize("layout", [None, Indent()])
@pytest.mark.parametrize("source", ["<p>a<b>b</b>c</p>", "<script>a < b</script>", "<pre> a\n b</pre>", "<br>"])
def test_inner_iterator(layout: Indent | None, source: str) -> None:
    root: Final = parse_fragment(source).children[0]
    options: Final = Html(layout=layout)
    assert "".join(root.serialize_iter(options, inner=True)) == root.serialize(options, inner=True)


def test_inner_iterator_chunks() -> None:
    root: Final = parse_fragment("<div>" + "<p>abcdefgh</p>" * 3000 + "</div>").children[0]
    chunks: Final = tuple(root.serialize_iter(inner=True))
    assert (len(chunks) > 1, "".join(chunks)) == (True, "<p>abcdefgh</p>" * 3000)


def test_inner_iterator_rejects_minify() -> None:
    with pytest.raises(ValueError, match="cannot stream"):
        Element("p").serialize_iter(Html(layout=Minify()), inner=True)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "iso-8859-1"])
def test_inner_encode(encoding: str) -> None:
    root: Final = Element("p", children=[Text("café")])
    assert root.encode(encoding, inner=True) == "café".encode(encoding)


def test_inner_encode_error() -> None:
    with pytest.raises(UnicodeEncodeError):
        Element("p", children=[Text("é")]).encode("ascii", inner=True)


def test_inner_default_unchanged() -> None:
    assert Element("p", children=[Text("x")]).serialize() == "<p>x</p>"


def test_inner_indent_nested_preserve() -> None:
    root: Final = Element("div", children=[Element("pre", children=[Element("b", children=[Text(" a ")])])])
    assert root.serialize(Html(layout=Indent()), inner=True) == "<pre><b> a </b></pre>"


@pytest.mark.parametrize(
    ("method", "arguments"),
    [("serialize", (None, True)), ("serialize_iter", (None, True)), ("encode", ("utf-8", None, True))],
)
def test_inner_keyword_only(method: str, arguments: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        getattr(Element("p"), method)(*arguments)


@pytest.mark.parametrize("layout", [None, Indent(), Minify()])
def test_inner_frame_ignores_children(layout: Indent | Minify | None) -> None:
    root: Final = Element("frame", children=[Text("not emitted")])
    assert not root.serialize(Html(layout=layout), inner=True)


@pytest.mark.parametrize("layout", [None, Indent(), Minify()])
@pytest.mark.parametrize("content", ["", "<title>x</title>", '<meta charset="utf-8">'])
def test_inner_meta_charset(layout: Indent | Minify | None, content: str) -> None:
    root: Final = parse(f"<head>{content}</head>").find_all("head")[0]
    result: Final = root.serialize(Html(layout=layout, meta_charset=True), inner=True)
    assert result.count("charset") == 1
    assert "<head>" not in result


def test_inner_xml_indent() -> None:
    root: Final = Element("p", children=[Element("br")])
    options: Final = Html(xml=True, layout=Indent())
    assert (root.serialize(options, inner=True), "".join(root.serialize_iter(options, inner=True))) == (
        "<br/>",
        "<br/>",
    )


@pytest.mark.parametrize("layout", [None, Indent(), Minify()])
def test_inner_foreign_context(layout: Indent | Minify | None) -> None:
    root: Final = parse_fragment("<svg><g></g></svg>").children[0]
    assert root.serialize(Html(layout=layout), inner=True) == "<g></g>"


def test_inner_metadata_on_other_context() -> None:
    root: Final = Element("div", children=[Text("x")])
    assert root.serialize(Html(layout=Indent(), meta_charset=True), inner=True) == "x"


def test_inner_minify_nested_raw_text() -> None:
    root: Final = parse_fragment("<div><script>a < b</script></div>").children[0]
    assert root.serialize(Html(layout=Minify()), inner=True) == "<script>a < b</script>"


def test_inner_shadow_root() -> None:
    root: Final = Element("div")
    shadow: Final = root.attach_shadow()
    shadow.set_inner_html("<b>  x  </b>")
    assert shadow.serialize(Html(layout=Minify()), inner=True) == "<b> x </b>"


@pytest.mark.parametrize("layout", [None, Indent()])
def test_inner_raw_context_only_applies_to_direct_text(layout: Indent | None) -> None:
    root: Final = Element("script", children=[Element("b", children=[Text("<&")])])
    assert root.serialize(Html(layout=layout), inner=True) == "<b>&lt;&amp;</b>"


_XML = Html(xml=True)


def _fragment(markup: str, selector: str) -> Element:
    node = parse(markup).select_one(selector)
    assert node is not None
    return node


@pytest.mark.parametrize(
    ("markup", "selector", "expected"),
    [
        pytest.param("<div></div>", "div", "<div/>", id="empty-self-closes"),
        pytest.param("<br>", "br", "<br/>", id="void-self-closes"),
        pytest.param("<input name=q value=1>", "input", '<input name="q" value="1"/>', id="void-with-attrs"),
        pytest.param("<p>x</p>", "p", "<p>x</p>", id="non-empty-keeps-end-tag"),
        pytest.param("<p><b>y</b></p>", "p", "<p><b>y</b></p>", id="nested-children"),
        pytest.param("<img src=a>", "img", '<img src="a"/>', id="void-img"),
    ],
)
def test_empty_elements_self_close(markup: str, selector: str, expected: str) -> None:
    assert _fragment(markup, selector).serialize(_XML) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("a&b", "a&amp;b", id="ampersand"),
        pytest.param("a<b", "a&lt;b", id="less-than"),
        pytest.param("a>b", "a&gt;b", id="greater-than"),
        pytest.param("a\rb", "a&#13;b", id="carriage-return"),
        pytest.param('a"b', 'a"b', id="quote-stays-literal"),
        pytest.param("a\tb\nc", "a\tb\nc", id="tab-newline-stay-literal"),
        pytest.param("a\xa0b", "a\xa0b", id="nbsp-stays-literal"),
    ],
)
def test_text_escaping(text: str, expected: str) -> None:
    assert Element("p", children=[Text(text)]).serialize(_XML) == f"<p>{expected}</p>"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a&b", "a&amp;b", id="ampersand"),
        pytest.param("a<b", "a&lt;b", id="less-than"),
        pytest.param("a>b", "a&gt;b", id="greater-than"),
        pytest.param('a"b', "a&quot;b", id="quote"),
        pytest.param("a\tb", "a&#9;b", id="tab"),
        pytest.param("a\nb", "a&#10;b", id="newline"),
        pytest.param("a\rb", "a&#13;b", id="carriage-return"),
        pytest.param("a\xa0b", "a\xa0b", id="nbsp-stays-literal"),
    ],
)
def test_attribute_escaping(value: str, expected: str) -> None:
    assert Element("p", {"title": value}).serialize(_XML) == f'<p title="{expected}"/>'


def test_script_content_is_escaped_not_raw() -> None:
    node = _fragment("<script>if(a<b){}</script>", "script")
    assert node.serialize(_XML) == "<script>if(a&lt;b){}</script>"


def test_style_content_is_escaped_not_raw() -> None:
    node = _fragment("<style>a>b{}</style>", "style")
    assert node.serialize(_XML) == "<style>a&gt;b{}</style>"


def test_svg_root_declares_default_namespace() -> None:
    node = _fragment("<svg><rect x=1><circle/></rect></svg>", "svg")
    assert node.serialize(_XML) == '<svg xmlns="http://www.w3.org/2000/svg"><rect x="1"><circle/></rect></svg>'


def test_mathml_root_declares_default_namespace() -> None:
    node = _fragment("<math><mi>x</mi></math>", "math")
    assert node.serialize(_XML) == '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'


def test_nested_foreign_element_does_not_redeclare_default() -> None:
    node = _fragment("<svg><g><rect/></g></svg>", "svg")
    assert "xmlns=" not in node.serialize(_XML).split("<g", 1)[1]


def test_detached_foreign_element_declares_namespace() -> None:
    svg = _fragment("<svg></svg>", "svg")
    svg.extract()
    assert svg.serialize(_XML) == '<svg xmlns="http://www.w3.org/2000/svg"/>'


def test_xlink_attribute_declares_prefix() -> None:
    node = _fragment("<svg xlink:href=x></svg>", "svg")
    assert node.serialize(_XML) == (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="x"/>'
    )


def test_foreign_element_without_xlink_omits_prefix() -> None:
    node = _fragment("<svg width=10></svg>", "svg")
    assert "xmlns:xlink" not in node.serialize(_XML)


def test_html_element_never_carries_namespace_decl() -> None:
    assert "xmlns" not in _fragment("<p id=a>x</p>", "p").serialize(_XML)


def test_sort_attributes_composes_with_xml() -> None:
    node = _fragment("<p z=1 a=2>x", "p")
    assert node.serialize(Html(xml=True, sort_attributes=True)) == '<p a="2" z="1">x</p>'


def test_formatter_is_ignored_under_xml() -> None:
    node = Element("p", children=[Text("a\xa0b")])
    assert node.serialize(Html(xml=True, formatter=Formatter.NAMED_ENTITIES)) == "<p>a\xa0b</p>"


def test_meta_charset_is_ignored_under_xml() -> None:
    out = parse("<p>x").serialize(Html(xml=True, meta_charset=True))
    assert "meta" not in out
    assert out == "<html><head/><body><p>x</p></body></html>"


def test_document_serialize_self_closes_empty_head() -> None:
    assert parse("<p>x").serialize(_XML) == "<html><head/><body><p>x</p></body></html>"


def test_comment_and_doctype_pass_through() -> None:
    out = parse("<!doctype html><!--note--><p>x").serialize(_XML)
    assert out == "<!DOCTYPE html><!--note--><html><head/><body><p>x</p></body></html>"


def test_indent_pretty_xml_self_closes_empties() -> None:
    node = _fragment("<div><p>hi</p><br></div>", "div")
    assert node.serialize(Html(xml=True, layout=Indent(2))) == ("<div>\n  <p>\n    hi\n  </p>\n  <br/>\n</div>")


def test_indent_pretty_xml_escapes_text() -> None:
    node = _fragment("<p>a&lt;b</p>", "p")
    assert node.serialize(Html(xml=True, layout=Indent(2))) == "<p>\n  a&lt;b\n</p>"


def test_indent_pretty_xml_empty_root_self_closes() -> None:
    assert _fragment("<div></div>", "div").serialize(Html(xml=True, layout=Indent(2))) == "<div/>"


def test_xml_processing_instruction_closes_with_question_mark() -> None:
    node = Element("doc", children=[ProcessingInstruction("t", "d")])
    assert node.serialize(_XML) == "<doc><?t d?></doc>"
    assert node.serialize(Html(xml=True, layout=Indent(2))) == "<doc>\n  <?t d?>\n</doc>"


def test_serialize_iter_streams_xml() -> None:
    node = _fragment("<div><br><p>x</p></div>", "div")
    assert "".join(node.serialize_iter(_XML)) == "<div><br/><p>x</p></div>"


def test_serialize_iter_streams_indented_xml() -> None:
    node = _fragment("<div><br></div>", "div")
    assert "".join(node.serialize_iter(Html(xml=True, layout=Indent(2)))) == "<div>\n  <br/>\n</div>"


def test_encode_emits_xml_bytes() -> None:
    assert _fragment("<br>", "br").encode(options=_XML) == b"<br/>"


def test_minify_layout_stays_html_under_xml() -> None:
    node = _fragment("<br>", "br")
    assert node.serialize(Html(xml=True, layout=Minify())) == "<br>"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<div><br><p class='a&b<c'>x&amp;<b>y</b></p></div>", id="mixed"),
        pytest.param("<svg xlink:href=x><a xlink:title=t><rect/></a></svg>", id="foreign-xlink"),
        pytest.param("<math><mi mathvariant=bold>x</mi></math>", id="mathml"),
        pytest.param("<p title='a\tb\nc'>t&lt;u</p>", id="whitespace-attrs"),
        pytest.param("<script>if(a<b&&c>d){}</script>", id="script"),
    ],
)
def test_output_is_well_formed_xml(markup: str) -> None:
    node = parse(markup).select_one("div,svg,math,script,p")
    assert node is not None
    ET.fromstring(node.serialize(_XML))  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own serialized output, parsed only to assert well-formedness


def test_round_trip_reparses_to_same_html() -> None:
    node = _fragment("<div><br><p>x&amp;y</p></div>", "div")
    reparsed = parse(node.serialize(_XML)).select_one("div")
    assert reparsed is not None
    assert reparsed.serialize() == node.serialize()


def _inner_xml(node: Node) -> str:
    """Serialize one node through the well-formed inner_xml path (Node.serialize keeps the raw XML syntax)."""
    return Element("root", children=[node]).inner_xml


def _parsed_inner_xml(markup: str) -> str:
    """Serialize parsed markup through inner_xml, for nodes (foreign, tag-soup names) the constructor rejects."""
    node = parse(f"<div>{markup}</div>").select_one("div")
    assert node is not None
    return node.inner_xml


def test_raw_xml_serialize_leaves_a_comment_untouched() -> None:
    from turbohtml import (  # ruff:ignore[import-outside-top-level]  # only this raw-vs-well-formed contrast needs it
        Comment,
    )

    node = Element("doc", children=[Comment("a--b")])
    assert node.serialize(_XML) == "<doc><!--a--b--></doc>"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("note", "<!--note-->", id="benign-unchanged"),
        pytest.param("a--b", "<!--a- -b-->", id="double-hyphen-split"),
        pytest.param("a---b", "<!--a- - -b-->", id="triple-hyphen-split"),
        pytest.param("end-", "<!--end- -->", id="trailing-hyphen-spaced"),
        pytest.param("--", "<!--- - -->", id="only-hyphens"),
        pytest.param("a\x0cb", "<!--ab-->", id="control-char-dropped"),
    ],
)
def test_inner_xml_comment_is_made_well_formed(body: str, expected: str) -> None:
    from turbohtml import (  # ruff:ignore[import-outside-top-level]  # only this comment-body suite needs the leaf type
        Comment,
    )

    out = _inner_xml(Comment(body))
    assert out == expected
    ET.fromstring(f"<doc>{out}</doc>")  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own output, parsed to assert the neutralized comment reparses


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("a\x0cb", "ab", id="form-feed-dropped"),
        pytest.param("a\x01b", "ab", id="control-dropped"),
        pytest.param("a\tb", "a\tb", id="tab-kept"),
        pytest.param("a\ud800b", "ab", id="surrogate-dropped"),
        pytest.param("a\ufffeb", "ab", id="noncharacter-fffe-dropped"),
        pytest.param("a\uffffb", "ab", id="noncharacter-ffff-dropped"),
        pytest.param("a\U0001f600b", "a\U0001f600b", id="astral-kept"),
    ],
)
def test_inner_xml_drops_characters_absent_from_xml(text: str, expected: str) -> None:
    assert _inner_xml(Element("p", children=[Text(text)])) == f"<p>{expected}</p>"


def test_inner_xml_drops_invalid_characters_in_attribute_values() -> None:
    assert _inner_xml(Element("p", {"title": "a\x0cb&c"})) == '<p title="ab&amp;c"/>'


def test_inner_xml_serializes_children_only() -> None:
    node = _fragment("<div><br><p>x&amp;y</p></div>", "div")
    assert node.inner_xml == "<br/><p>x&amp;y</p>"
    assert node.inner_html == "<br><p>x&amp;y</p>"


def test_inner_xml_does_not_duplicate_a_stored_xmlns() -> None:
    out = _parsed_inner_xml('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    assert out.count("xmlns=") == 1
    assert out == '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'


def test_inner_xml_does_not_duplicate_a_stored_xmlns_prefix() -> None:
    out = _parsed_inner_xml('<svg xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="x"></svg>')
    assert out.count("xmlns:xlink=") == 1
    ET.fromstring(out)  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own output, parsed to assert the single declaration is well-formed


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<b <script>x</b>", "<b>x</b>", id="invalid-first-char"),
        pytest.param('<p a"b=1>x', "<p>x</p>", id="invalid-later-char"),
    ],
)
def test_inner_xml_drops_attributes_with_invalid_names(markup: str, expected: str) -> None:
    out = _parsed_inner_xml(markup)
    assert out == expected
    ET.fromstring(out)  # ruff:ignore[suspicious-xml-element-tree-usage]  # our own output, parsed to assert dropping the bad name left it well-formed


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        pytest.param({"ü": "1"}, '<p ü="1"/>', id="non-ascii-start"),
        pytest.param({"data-ü": "1"}, '<p data-ü="1"/>', id="non-ascii-later"),
        pytest.param({"data-x": "1"}, '<p data-x="1"/>', id="ascii-name-kept"),
        pytest.param({"aria-hidden": "1"}, '<p aria-hidden="1"/>', id="eleven-char-name-not-a-declaration"),
        pytest.param({"xmlns": "urn:x"}, '<p xmlns="urn:x"/>', id="xmlns-kept-when-not-a-foreign-root"),
        pytest.param({"xmlns:xlink": "urn:x"}, '<p xmlns:xlink="urn:x"/>', id="xmlns-prefix-kept-without-xlink-attr"),
    ],
)
def test_inner_xml_keeps_valid_attribute_names(attributes: dict[str, str], expected: str) -> None:
    assert _inner_xml(Element("p", attributes)) == expected


def _one(html: str, selector: str) -> Node:
    node = parse(html).select_one(selector)
    assert node is not None
    return node


@pytest.mark.parametrize(
    ("html", "selector", "inner", "outer"),
    [
        pytest.param(
            "<div><p>hi</p><span>x</span></div>",
            "div",
            "<p>hi</p><span>x</span>",
            "<div><p>hi</p><span>x</span></div>",
            id="children-only",
        ),
        pytest.param("<p>x</p>", "p", "x", "<p>x</p>", id="leaf-text-child"),
        pytest.param("<br>", "br", "", "<br>", id="void-has-no-children"),
    ],
)
def test_inner_html(html: str, selector: str, inner: str, outer: str) -> None:
    node = _one(html, selector)
    assert node.inner_html == inner
    assert node.html == outer


def test_whatwg_attribute_leaves_angles_literal() -> None:
    anchor = _one('<a title="a&amp;b&lt;c&gt;d&quot;e&nbsp;f">t</a>', "a")
    assert anchor.html == '<a title="a&amp;b<c>d&quot;e&nbsp;f">t</a>'


def test_whatwg_text_keeps_a_literal_quote() -> None:
    assert _one("<p>say &quot;hi&quot;</p>", "p").inner_html == 'say "hi"'


def test_default_formatter_keeps_non_ascii_literal() -> None:
    assert _one("<p>café &amp; résumé&nbsp;x</p>", "p").inner_html == "café &amp; résumé&nbsp;x"


@pytest.mark.parametrize(
    ("html", "selector", "formatter", "expected"),
    [
        pytest.param(
            "<p>a&amp;b&lt;c&gt;d&nbsp;e</p>",
            "p",
            Formatter.MINIMAL,
            "<p>a&amp;b&lt;c&gt;d\xa0e</p>",
            id="minimal-text",
        ),
        pytest.param(
            '<a title="a&lt;b&quot;c&nbsp;d">t</a>',
            "a",
            Formatter.MINIMAL,
            '<a title="a&lt;b"c\xa0d">t</a>',
            id="minimal-attribute",
        ),
        pytest.param(
            "<p>café &amp; résumé&nbsp;x</p>",
            "p",
            Formatter.NAMED_ENTITIES,
            "<p>caf&eacute; &amp; r&eacute;sum&eacute;&nbsp;x</p>",
            id="named-uses-html-names",
        ),
        pytest.param("<p>ab12</p>", "p", Formatter.NAMED_ENTITIES, "<p>ab12</p>", id="named-keeps-unnamed"),
        pytest.param(
            "<p>é&amp;😀\u03b1a</p>",
            "p",
            Formatter.NAMED_ENTITIES,
            "<p>&eacute;&amp;😀&alpha;a</p>",
            id="named-mixed-text",
        ),
        pytest.param(
            '<p title="é&amp;😀\u03b1a">x</p>',
            "p",
            Formatter.NAMED_ENTITIES,
            '<p title="&eacute;&amp;😀&alpha;a">x</p>',
            id="named-mixed-attribute",
        ),
        pytest.param(
            "<p>a&lt;b&gt;c&quot;d&amp;e</p>",
            "p",
            Formatter.NAMED_ENTITIES,
            "<p>a&lt;b&gt;c&quot;d&amp;e</p>",
            id="named-escapes-structural-ascii",
        ),
        # a C1 control below the table and an astral emoji above it have no named
        # reference, so they stay literal (exercising both ends of the table search)
        pytest.param(
            "<p>\x85\U0001f600</p>",
            "p",
            Formatter.NAMED_ENTITIES,
            "<p>\x85\U0001f600</p>",
            id="named-passes-unnamed-non-ascii",
        ),
    ],
)
def test_formatter_serialize(html: str, selector: str, formatter: Formatter, expected: str) -> None:
    assert _one(html, selector).serialize(Html(formatter=formatter)) == expected


@pytest.mark.parametrize(
    ("html", "selector", "indent", "expected"),
    [
        pytest.param(
            "<ul><li>a</li><li>b</li></ul>",
            "ul",
            2,
            "<ul>\n  <li>\n    a\n  </li>\n  <li>\n    b\n  </li>\n</ul>",
            id="int-pretty-prints",
        ),
        pytest.param("<div><p>x</p></div>", "div", "\t", "<div>\n\t<p>\n\t\tx\n\t</p>\n</div>", id="str-unit-verbatim"),
        pytest.param("<div><p>x</p></div>", "div", 0, "<div>\n<p>\nx\n</p>\n</div>", id="zero-newlines-no-indent"),
        pytest.param("<div></div>", "div", 2, "<div></div>", id="empty-element-one-line"),
        pytest.param("<p><br></p>", "p", 2, "<p>\n  <br>\n</p>", id="void-element"),
        pytest.param("<frameset><frame>", "frameset", 2, "<frameset>\n  <frame>\n</frameset>", id="void-frame"),
        pytest.param("<div><pre>\n\nkeep</pre></div>", "div", 2, "<div>\n  <pre>\n\nkeep</pre>\n</div>", id="raw-pre"),
        pytest.param(
            "<div><script>a<b</script></div>", "div", 2, "<div>\n  <script>a<b</script>\n</div>", id="raw-script"
        ),
        pytest.param(
            "<template><p>x</p></template>",
            "template",
            2,
            "<template>\n  <p>\n    x\n  </p>\n</template>",
            id="template-content",
        ),
        # a foreign element is laid out like any non-void element, not preserved
        pytest.param(
            "<svg><circle r=1></circle></svg>", "svg", 2, '<svg>\n  <circle r="1"></circle>\n</svg>', id="foreign"
        ),
        # plain content (no leading newline) so the element-name test, not the
        # leading-newline rule, drives the decision to preserve
        pytest.param("<div><pre>plain</pre></div>", "div", 2, "<div>\n  <pre>plain</pre>\n</div>", id="preserve-pre"),
        pytest.param(
            "<div><textarea>x</textarea></div>",
            "div",
            2,
            "<div>\n  <textarea>x</textarea>\n</div>",
            id="preserve-textarea",
        ),
        pytest.param(
            "<div><listing>a b</listing></div>",
            "div",
            2,
            "<div>\n  <listing>a b</listing>\n</div>",
            id="preserve-listing",
        ),
        pytest.param("<div><p>x</p></div>", "div", None, "<div><p>x</p></div>", id="none-is-compact"),
    ],
)
def test_serialize_indent(html: str, selector: str, indent: int | str | None, expected: str) -> None:
    layout = None if indent is None else Indent(indent)
    assert _one(html, selector).serialize(Html(layout=layout)) == expected


# the parser drops one newline after <pre>; serialization restores it so a
# two-newline source round-trips, while plain content is left unchanged
@pytest.mark.parametrize(
    ("html", "selector", "expected"),
    [
        pytest.param("<pre>\n\nkeep</pre>", "pre", "<pre>\n\nkeep</pre>", id="pre-double-newline"),
        pytest.param(
            "<textarea>\n\nx</textarea>", "textarea", "<textarea>\n\nx</textarea>", id="textarea-double-newline"
        ),
        pytest.param("<listing>\n\ny</listing>", "listing", "<listing>\n\ny</listing>", id="listing-double-newline"),
        pytest.param("<pre>plain</pre>", "pre", "<pre>plain</pre>", id="no-leading-newline"),
        pytest.param("<pre><span>x</span></pre>", "pre", "<pre><span>x</span></pre>", id="non-text-first-child"),
        pytest.param("<pre></pre>", "pre", "<pre></pre>", id="empty"),
    ],
)
def test_pre_leading_newline_rule(html: str, selector: str, expected: str) -> None:
    assert _one(html, selector).html == expected


def test_pretty_document_includes_doctype_and_comment() -> None:
    pretty = parse("<!DOCTYPE html><!--c--><title>t").serialize(Html(layout=Indent(2)))
    assert pretty.startswith("<!DOCTYPE html>\n<!--c-->\n<html>\n  <head>")


def test_default_serialize_matches_html() -> None:
    div = _one("<div><p>hi</p></div>", "div")
    assert div.serialize() == div.html  # WHATWG, compact


def test_indent_defaults_to_two_spaces() -> None:
    assert Indent().unit == "  "


def test_indent_int_and_str_units() -> None:
    assert Indent(4).unit == "    "
    assert Indent("\t").unit == "\t"
    assert not Indent(0).unit


def test_indent_repr() -> None:
    assert repr(Indent(2)) == "Indent('  ')"


def test_indent_equality_and_hash() -> None:
    assert Indent(2) == Indent("  ")
    assert Indent(2) != Indent(4)
    assert Indent(2) != "Indent(2)"
    assert (Indent(2) == 3) is False
    assert hash(Indent(2)) == hash(Indent("  "))


def test_indent_unorderable() -> None:
    with pytest.raises(TypeError):
        _ = Indent(2) < Indent(4)  # ty: ignore[unsupported-operator]  # ordering is unsupported on purpose


def test_indent_not_equal_same_length() -> None:
    # same unit length, different content: forces the value comparison, not the length shortcut
    assert Indent(" ") != Indent("\t")


def test_indent_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        Indent(2, 4)  # ty: ignore[too-many-positional-arguments]  # takes one argument


@pytest.mark.parametrize(
    ("html", "selector", "call", "expected"),
    [
        pytest.param("<p>café</p>", "p", lambda node: node.encode(), "<p>café</p>".encode(), id="defaults-to-utf8"),
        pytest.param(
            "<p>café</p>",
            "p",
            lambda node: node.encode("ascii", Html(formatter=Formatter.NAMED_ENTITIES)),
            b"<p>caf&eacute;</p>",
            id="ascii-with-named-entities",
        ),
        pytest.param(
            "<div><p>x</p></div>",
            "div",
            lambda node: node.encode(options=Html(layout=Indent(2))),
            b"<div>\n  <p>\n    x\n  </p>\n</div>",
            id="honours-indent",
        ),
    ],
)
def test_encode(html: str, selector: str, call: Callable[[Node], bytes], expected: bytes) -> None:
    assert call(_one(html, selector)) == expected


@pytest.mark.parametrize(
    ("html", "kwargs", "exception"),
    [
        pytest.param("<p>x</p>", {"encoding": "not-a-codec"}, LookupError, id="unknown-encoding"),
        # the default WHATWG formatter keeps é literal, so ascii cannot represent it
        pytest.param("<p>café</p>", {"encoding": "ascii"}, UnicodeEncodeError, id="unencodable-character"),
    ],
)
def test_encode_raises(html: str, kwargs: dict[str, object], exception: type[Exception]) -> None:
    with pytest.raises(exception):
        _one(html, "p").encode(**kwargs)  # ty: ignore[invalid-argument-type]  # object-typed kwargs


@pytest.mark.parametrize(
    ("call", "exception", "match"),
    [
        pytest.param(
            lambda node: node.serialize(Html(formatter="whatwg")),  # ty: ignore[invalid-argument-type]  # pass a non-Formatter to test serialize rejects it
            TypeError,
            "Formatter",
            id="serialize-non-formatter",
        ),
        pytest.param(
            lambda node: node.serialize(Html(layout=Indent(1.5))),  # ty: ignore[invalid-argument-type]  # pass a non-int/str to test Indent rejects it
            TypeError,
            "indent",
            id="indent-bad-type",
        ),
        pytest.param(
            lambda node: node.serialize(Html(layout=Indent(-1))), ValueError, "negative", id="indent-negative"
        ),
        pytest.param(
            lambda node: node.serialize(Html(layout="whatwg")),  # ty: ignore[invalid-argument-type]  # pass a non-layout to test serialize rejects it
            TypeError,
            "layout",
            id="serialize-bad-layout",
        ),
        pytest.param(
            lambda node: node.encode(options=Html(formatter="whatwg")),  # ty: ignore[invalid-argument-type]  # pass a non-Formatter to test encode rejects it
            TypeError,
            "Formatter",
            id="encode-non-formatter",
        ),
    ],
)
def test_serialize_encode_argument_validation(
    call: Callable[[Node], object], exception: type[Exception], match: str | None
) -> None:
    with pytest.raises(exception, match=match):
        call(_one("<p>x</p>", "p"))


def test_explicit_none_is_the_default() -> None:
    node = _one("<div><p>hi</p></div>", "div")
    assert node.serialize(None) == node.serialize()
    assert node.encode("utf-8", None) == node.encode()


def test_serialize_options_must_be_an_html() -> None:
    with pytest.raises(TypeError, match="options must be a Html"):
        _one("<p>x</p>", "p").serialize(object())  # ty: ignore[invalid-argument-type]  # pass a non-Html to test the type error


def test_encode_options_must_be_an_html() -> None:
    with pytest.raises(TypeError, match="options must be a Html"):
        _one("<p>x</p>", "p").encode("utf-8", object())  # ty: ignore[invalid-argument-type]  # pass a non-Html to test the type error


def test_serialize_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        _one("<p>x</p>", "p").serialize(Html(), Html())  # ty: ignore[too-many-positional-arguments]  # a second arg is rejected


def test_encode_rejects_extra_positional() -> None:
    with pytest.raises(TypeError):
        _one("<p>x</p>", "p").encode("utf-8", Html(), "extra")  # ty: ignore[too-many-positional-arguments]  # a third arg is rejected


def test_serialize_propagates_a_raising_truthiness() -> None:
    # the Html bool fields are read with PyObject_IsTrue, so a value whose __bool__
    # raises surfaces the error instead of being silently coerced
    class _Boom:
        def __bool__(self) -> bool:
            msg = "boom"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        _one("<p>x</p>", "p").serialize(Html(sort_attributes=_Boom()))  # ty: ignore[invalid-argument-type]  # a raising __bool__ on purpose


@pytest.mark.parametrize(
    ("member", "value"),
    [
        pytest.param(Formatter.WHATWG, "whatwg", id="whatwg"),
        pytest.param(Formatter.MINIMAL, "minimal", id="minimal"),
        pytest.param(Formatter.NAMED_ENTITIES, "named", id="named"),
    ],
)
def test_formatter_members(member: Formatter, value: str) -> None:
    assert member.value == value


@pytest.mark.parametrize(
    ("markup", "selector", "expected"),
    [
        pytest.param("<p z=1 a=2 m=3>x", "p", '<p a="2" m="3" z="1">x</p>', id="three-attrs"),
        pytest.param("<p a=1>x", "p", '<p a="1">x</p>', id="single-attr-unchanged"),
        pytest.param("<p>x", "p", "<p>x</p>", id="no-attrs-unchanged"),
        pytest.param("<input name=q value=1>", "input", '<input name="q" value="1">', id="void-element"),
    ],
)
def test_sort_attributes_orders_by_name(markup: str, selector: str, expected: str) -> None:
    node: Final = parse(markup).select_one(selector)
    assert node is not None
    assert node.serialize(Html(sort_attributes=True)) == expected


def test_sort_attributes_off_keeps_source_order() -> None:
    node: Final = parse("<p z=1 a=2>x").select_one("p")
    assert node is not None
    assert node.serialize() == '<p z="1" a="2">x</p>'


@pytest.mark.parametrize("tag", ["pre", "textarea", "listing"])
@pytest.mark.parametrize("clear", [False, True], ids=["empty", "cleared"])
def test_serialize_empty_leading_text(tag: str, *, clear: bool) -> None:
    element: Final = Element(tag)
    text: Final = Text("x" if clear else "")
    element.append(text)
    if clear:
        text.data = ""
    assert element.serialize() == f"<{tag}></{tag}>"


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        pytest.param({"ab": "1", "a": "2"}, '<x a="2" ab="1"></x>', id="prefix-name-after-longer"),
        pytest.param({"a": "1", "ab": "2"}, '<x a="1" ab="2"></x>', id="prefix-name-before-longer"),
    ],
)
def test_sort_attributes_orders_prefix_names(attrs: dict[str, str], expected: str) -> None:
    assert Element("x", attrs).serialize(Html(sort_attributes=True)) == expected


@pytest.mark.parametrize("count", [8, 64, 65, 1024])
@pytest.mark.parametrize("reverse", [False, True], ids=["sorted", "reversed"])
@pytest.mark.parametrize("xml", [False, True], ids=["html", "xml"])
@pytest.mark.parametrize("encode", [False, True], ids=["serialize", "encode"])
def test_sort_attributes_by_width(count: int, *, reverse: bool, xml: bool, encode: bool) -> None:
    names: Final = sorted(f"a{index}" for index in range(count))
    element: Final = Element("x", dict.fromkeys(reversed(names) if reverse else names, ""), children=[Text("x")])
    options: Final = Html(xml=xml, sort_attributes=True)
    result: Final = element.encode(options=options).decode() if encode else element.serialize(options)
    assert result == "<x " + " ".join(f'{name}=""' for name in names) + ">x</x>"


def test_sort_attributes_composes_with_indent() -> None:
    node: Final = parse("<p z=1 a=2>x").select_one("p")
    assert node is not None
    assert node.serialize(Html(layout=Indent(2), sort_attributes=True)).splitlines()[0] == '<p a="2" z="1">'


def test_sort_attributes_composes_with_minify() -> None:
    node: Final = parse("<p z=1 a=2>x").select_one("p")
    assert node is not None
    assert node.serialize(Html(layout=Minify(), sort_attributes=True)) == "<p a=2 z=1>x</p>"


def test_meta_charset_injects_into_empty_head() -> None:
    out: Final = parse("<p>x").serialize(Html(meta_charset=True))
    assert out == '<html><head><meta charset="utf-8"></head><body><p>x</p></body></html>'


def test_meta_charset_injects_before_existing_head_content() -> None:
    out: Final = parse("<link rel=icon>").serialize(Html(meta_charset=True))
    assert out == '<html><head><meta charset="utf-8"><link rel="icon"></head><body></body></html>'


def test_meta_charset_normalizes_existing_charset_meta() -> None:
    out: Final = parse("<meta charset=ascii>").serialize(Html(meta_charset=True))
    assert out == '<html><head><meta charset="utf-8"></head><body></body></html>'


def test_meta_charset_keeps_other_attributes_on_charset_meta() -> None:
    out: Final = parse("<meta charset=ascii id=enc>").serialize(Html(meta_charset=True))
    assert out == '<html><head><meta charset="utf-8" id="enc"></head><body></body></html>'


def test_meta_charset_normalizes_http_equiv_content_type() -> None:
    out: Final = parse('<meta http-equiv=content-type content="text/html; charset=ascii">').serialize(
        Html(meta_charset=True)
    )
    expected: Final = '<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8">'
    assert out.startswith(expected)
    assert out.count("charset=") == 1


def test_meta_charset_matches_mixed_case_http_equiv_value() -> None:
    out: Final = parse('<meta http-equiv="Content-Type" content="text/html; charset=ascii">').serialize(
        Html(meta_charset=True)
    )
    expected: Final = '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">'
    assert out.startswith(expected)
    assert out.count("charset=") == 1


def test_meta_charset_ignores_unrelated_http_equiv() -> None:
    out: Final = parse('<meta http-equiv=refresh content="5">').serialize(Html(meta_charset=True))
    assert out.startswith('<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">')


def test_meta_charset_ignores_http_equiv_content_type_without_content() -> None:
    out: Final = parse("<meta http-equiv=content-type>").serialize(Html(meta_charset=True))
    assert out.startswith('<html><head><meta charset="utf-8"><meta http-equiv="content-type">')


@pytest.mark.parametrize(
    "equiv",
    [
        pytest.param("content", id="shorter-prefix"),
        pytest.param("content-type-x", id="longer-than-label"),
    ],
)
def test_meta_charset_http_equiv_label_mismatch_injects(equiv: str) -> None:
    out: Final = parse(f'<meta http-equiv="{equiv}" content="x">').serialize(Html(meta_charset=True))
    assert out.startswith('<html><head><meta charset="utf-8">')


def test_meta_charset_ignores_meta_without_charset_or_http_equiv() -> None:
    out: Final = parse('<meta name=viewport content="width=1">').serialize(Html(meta_charset=True))
    assert out.startswith('<html><head><meta charset="utf-8"><meta name="viewport" content="width=1">')


def test_meta_charset_keeps_seven_char_attr_on_charset_meta() -> None:
    out: Final = parse('<meta charset=ascii content="x">').serialize(Html(meta_charset=True))
    assert out.startswith('<html><head><meta charset="utf-8" content="x">')


def test_meta_charset_keeps_seven_char_attr_on_content_type_meta() -> None:
    out: Final = parse('<meta http-equiv=content-type content="text/html" enabled="x">').serialize(
        Html(meta_charset=True)
    )
    assert out.startswith('<html><head><meta http-equiv="content-type" content="text/html; charset=utf-8" enabled="x">')


def test_meta_charset_with_indent_leaves_foreign_element() -> None:
    out: Final = parse("<svg></svg>").serialize(Html(layout=Indent(2), meta_charset=True))
    assert '<meta charset="utf-8">' in out
    assert "<svg></svg>" in out


def test_meta_charset_skips_non_element_head_children() -> None:
    out: Final = parse("<head><!--note--><title>t").serialize(Html(meta_charset=True))
    assert out == ('<html><head><meta charset="utf-8"><!--note--><title>t</title></head><body></body></html>')


def test_meta_charset_off_injects_nothing() -> None:
    assert parse("<p>x").serialize() == "<html><head></head><body><p>x</p></body></html>"


def test_meta_charset_no_head_is_a_no_op() -> None:
    node: Final = parse("<p id=a>x").select_one("p")
    assert node is not None
    assert node.serialize(Html(meta_charset=True)) == '<p id="a">x</p>'


def test_meta_charset_leaves_foreign_elements_untouched() -> None:
    node: Final = parse("<svg><circle r=5></circle></svg>").select_one("svg")
    assert node is not None
    assert node.serialize(Html(meta_charset=True)) == '<svg><circle r="5"></circle></svg>'


def test_meta_charset_reparses_to_one_declaration() -> None:
    out: Final = parse("<title>t").serialize(Html(meta_charset=True))
    metas: Final = parse(out).select("meta")
    assert len(metas) == 1
    assert metas[0].attrs["charset"] == "utf-8"


def test_meta_charset_with_indent_indents_injected_meta() -> None:
    out: Final = parse("<p>x").serialize(Html(layout=Indent(2), meta_charset=True))
    assert out == (
        "<html>\n"
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        "  </head>\n"
        "  <body>\n"
        "    <p>\n"
        "      x\n"
        "    </p>\n"
        "  </body>\n"
        "</html>"
    )


def test_meta_charset_with_indent_non_empty_head() -> None:
    out: Final = parse("<link rel=icon>").serialize(Html(layout=Indent(2), meta_charset=True))
    assert out == (
        '<html>\n  <head>\n    <meta charset="utf-8">\n    <link rel="icon">\n  </head>\n  <body></body>\n</html>'
    )


def test_meta_charset_with_indent_normalizes_existing_meta() -> None:
    out: Final = parse("<meta charset=ascii>").serialize(Html(layout=Indent(2), meta_charset=True))
    assert out == ('<html>\n  <head>\n    <meta charset="utf-8">\n  </head>\n  <body></body>\n</html>')


def test_meta_charset_with_minify_injects() -> None:
    out: Final = parse("<title>t").serialize(Html(layout=Minify(), meta_charset=True))
    assert out.startswith('<meta charset="utf-8">')
    metas: Final = parse(out).select("meta")
    assert len(metas) == 1
    assert metas[0].attrs["charset"] == "utf-8"


def test_meta_charset_with_minify_normalizes_existing_meta() -> None:
    out: Final = parse("<meta charset=ascii><title>t").serialize(Html(layout=Minify(), meta_charset=True))
    metas: Final = parse(out).select("meta")
    assert len(metas) == 1
    assert metas[0].attrs["charset"] == "utf-8"
    assert 'charset="utf-8"' in out


def test_encode_meta_charset_uses_target_encoding() -> None:
    out: Final = parse("<p>x").encode("iso-8859-1", Html(meta_charset=True))
    assert out == ('<html><head><meta charset="iso-8859-1"></head><body><p>x</p></body></html>'.encode("latin-1"))


def test_encode_sort_attributes() -> None:
    node: Final = parse("<p z=1 a=2>x").select_one("p")
    assert node is not None
    assert node.encode(options=Html(sort_attributes=True)) == b'<p a="2" z="1">x</p>'


def test_serialize_rejects_another_renderers_config() -> None:
    with pytest.raises(TypeError, match="options must be a Html, not Markdown"):
        parse("<p>x</p>").serialize(Markdown())  # ty: ignore[invalid-argument-type]  # the wrong config class is rejected


@dataclass(frozen=True)
class _SortedHtml(Html):
    sort_attributes: bool = True


@dataclass(frozen=True)
class _ImageText(PlainText):
    images: bool = True


@dataclass(frozen=True)
class _CommentedCanonical(Canonical):
    with_comments: bool = True


@dataclass(frozen=True)
class _ExtendedHtml(_SortedHtml):
    extra: str = "value"


@dataclass(frozen=True)
class _ExtendedText(_ImageText):
    extra: str = "value"


@dataclass(frozen=True)
class _ExtendedCanonical(_CommentedCanonical):
    extra: str = "value"


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        pytest.param(Html(), '<p z="1" a="2"><img alt="picture"><!--note--></p>', id="html-default"),
        pytest.param(_SortedHtml(), '<p a="2" z="1"><img alt="picture"><!--note--></p>', id="html-subclass"),
        pytest.param(PlainText(), "", id="text-default"),
        pytest.param(_ImageText(), "picture", id="text-subclass"),
        pytest.param(Canonical(), b'<p a="2" z="1"><img alt="picture"></img></p>', id="canonical-default"),
        pytest.param(
            _CommentedCanonical(),
            b'<p a="2" z="1"><img alt="picture"></img><!--note--></p>',
            id="canonical-subclass",
        ),
    ],
)
def test_render_options_preserve_subclass_defaults(
    options: Html | PlainText | Canonical, expected: str | bytes
) -> None:
    assert _render(options) == expected


@pytest.mark.parametrize(
    "options",
    [
        pytest.param(_ExtendedHtml(), id="html"),
        pytest.param(_ExtendedText(), id="text"),
        pytest.param(_ExtendedCanonical(), id="canonical"),
    ],
)
def test_render_options_reject_subclass_extra_fields(options: Html | PlainText | Canonical) -> None:
    with pytest.raises(AttributeError, match="has no attribute 'extra'"):
        _render(options)


def _render(options: Html | PlainText | Canonical) -> str | bytes:
    document: Final = parse('<p z="1" a="2"><img alt="picture"><!--note--></p>').select("p")[0]
    if isinstance(options, Html):
        return document.serialize(options)
    if isinstance(options, PlainText):
        return document.to_text(options)
    return document.canonicalize(options)
