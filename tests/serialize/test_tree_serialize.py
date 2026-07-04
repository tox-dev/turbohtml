"""Content accessors: text, data, name, namespace, and HTML serialization."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Doctype, Html, Indent, Markdown, Minify, Namespace, Text, parse

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Element, Node


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
    first: Callable[[str, type[Node]], Node], html: str, node_type: type[Node], expected: tuple[str, str, str]
) -> None:
    data, text, serialized = expected
    node = first(html, node_type)
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
def test_doctype_name(first: Callable[[str, type[Node]], Node], doctype: str, name: str) -> None:
    node = first(doctype, Doctype)
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


_TREE_DIR = Path(__file__).parents[1] / "html5lib-tests" / "tree-construction"


def _corpus_sources(path: Path) -> list[str]:
    sources: list[str] = []
    for raw in path.read_text(encoding="utf-8").split("\n#data\n"):
        block = raw.removeprefix("#data\n")
        data, _, rest = block.partition("\n#errors")
        if "#document-fragment\n" in rest or "#script-on" in rest or "\n#document\n" not in rest:
            continue
        sources.append(data)
    return sources


@pytest.mark.parametrize("layout", [pytest.param(None, id="compact"), pytest.param(Indent(2), id="indent")])
@pytest.mark.parametrize("filename", sorted(path.name for path in _TREE_DIR.glob("*.dat")))
def test_serialize_iter_joins_to_serialize_over_corpus(filename: str, layout: Indent | None) -> None:
    options = Html(layout=layout)
    mismatches = [
        data
        for data in _corpus_sources(_TREE_DIR / filename)
        if "".join((root := parse(data)).serialize_iter(options)) != root.serialize(options)
    ]
    assert not mismatches, f"{filename}: {len(mismatches)} chunked outputs differ\n\n" + "\n\n".join(
        repr(source) for source in mismatches[:5]
    )
