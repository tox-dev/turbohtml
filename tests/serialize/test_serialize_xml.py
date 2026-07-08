"""XML/XHTML serialization: the ``Html(xml=True)`` output syntax."""

from __future__ import annotations

from xml.etree import ElementTree as ET  # noqa: S405  # parsing turbohtml's own serialized output, not untrusted input

import pytest

from turbohtml import Element, Formatter, Html, Indent, Minify, Node, ProcessingInstruction, Text, parse

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
    ET.fromstring(node.serialize(_XML))  # noqa: S314  # our own serialized output, parsed only to assert well-formedness


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
    from turbohtml import Comment  # noqa: PLC0415  # local: only this raw-vs-well-formed contrast needs the leaf

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
    from turbohtml import Comment  # noqa: PLC0415  # local: only this comment-body suite needs the leaf type

    out = _inner_xml(Comment(body))
    assert out == expected
    ET.fromstring(f"<doc>{out}</doc>")  # noqa: S314  # our own output, parsed to assert the neutralized comment reparses


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
    ET.fromstring(out)  # noqa: S314  # our own output, parsed to assert the single declaration is well-formed


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
    ET.fromstring(out)  # noqa: S314  # our own output, parsed to assert dropping the bad name left it well-formed


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
