"""Lossless byte-preserving serialization: ``Node.to_source``."""

from __future__ import annotations

import pytest

from turbohtml import Element, Text, parse, parse_fragment, parse_xml

DOC = "<!DOCTYPE html><html><head></head><body>{}</body></html>"


def _doc(body: str) -> str:
    return DOC.format(body)


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(_doc('<p class="x">Hi &amp; bye</p>'), id="attr-and-entity"),
        pytest.param(_doc("<A HREF='u'>t</A>"), id="uppercase-single-quote"),
        pytest.param(_doc('<p  class = "x"  id=y >z</p>'), id="whitespace-in-tag"),
        pytest.param(_doc("<input disabled required>"), id="valueless-attrs"),
        pytest.param(_doc("<a href=plain>t</a>"), id="unquoted-attr"),
        pytest.param(_doc("<img src=x><br><hr>"), id="void-elements"),
        pytest.param(_doc("<ul><li>a<li>b</li></ul>"), id="implicitly-closed"),
        pytest.param(_doc("<!--a comment--><p>x</p>"), id="comment"),
        pytest.param(_doc('<svg width="1"><rect/></svg>'), id="foreign-svg"),
        pytest.param(_doc("<template><p>t</p></template>"), id="template-content"),
        pytest.param(_doc("<script>a<b && c</script>"), id="raw-text-element"),
        pytest.param(
            "<!DOCTYPE html><html><head><style>\na { color: red }\nb { color: blue }\n</style></head>"
            "<body></body></html>",
            id="raw-text-span",
        ),
        pytest.param(_doc("<pre>x\ny</pre>"), id="preformatted"),
        pytest.param(_doc("<pre>\n\nkept</pre>"), id="preformatted-leading-newline"),
        pytest.param(_doc("<table><tbody><tr><td>c</td></tr></tbody></table>"), id="explicit-table"),
        pytest.param(_doc("<div>plain unicode: \xa9 keep</div>"), id="latin1-text"),
        pytest.param(_doc("<div>bmp: €あ</div>"), id="bmp-text"),
        pytest.param(_doc("<div>astral: \U0001f600</div>"), id="astral-text"),
        pytest.param("<!DOCTYPE html><html><head></head><body></body></html>", id="empty-body"),
    ],
)
def test_unedited_round_trip_is_byte_identical(markup: str) -> None:
    assert parse(markup, source_locations=True).to_source() == markup


def test_changing_an_attribute_rewrites_only_that_start_tag() -> None:
    markup = _doc('<p id="a">one</p><p id="b">two</p>')
    doc = parse(markup, source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["id"] = "CHANGED"
    assert doc.to_source() == _doc('<p id="CHANGED">one</p><p id="b">two</p>')


def test_adding_an_attribute_rebuilds_the_start_tag() -> None:
    doc = parse(_doc('<p id="a">x</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["data-n"] = "1"
    assert doc.to_source() == _doc('<p id="a" data-n="1">x</p>')


def test_deleting_an_attribute_rebuilds_the_start_tag() -> None:
    doc = parse(_doc('<p id="a" data-n="1">x</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    del node.attrs["data-n"]
    assert doc.to_source() == _doc('<p id="a">x</p>')


def test_editing_text_re_escapes_only_that_run() -> None:
    doc = parse(_doc("<p>keep</p><p>edit</p>"), source_locations=True)
    node = doc.select("p")[1]
    node.text = "a < b & c"
    assert doc.to_source() == _doc("<p>keep</p><p>a &lt; b &amp; c</p>")


def test_clearing_text_emits_nothing_for_that_run() -> None:
    doc = parse(_doc("<p>gone</p>"), source_locations=True)
    node = doc.select_one("p")
    assert node is not None
    node.text = ""
    assert doc.to_source() == _doc("<p></p>")


def test_removing_a_node_drops_its_span_and_preserves_siblings() -> None:
    doc = parse(_doc('<p id="a">one</p><p id="b">two</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.extract()
    assert doc.to_source() == _doc('<p id="b">two</p>')


def test_inserting_an_element_serializes_it_canonically() -> None:
    doc = parse(_doc("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    section = Element("section", children=[Text("new")])
    body.append(section)
    assert doc.to_source() == _doc("<p>x</p><section>new</section>")


def test_inserting_a_void_element_serializes_it_canonically() -> None:
    doc = parse(_doc("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.append(Element("br"))
    assert doc.to_source() == _doc("<p>x</p><br>")


def test_inserting_a_raw_text_element_keeps_its_content_literal() -> None:
    doc = parse(_doc("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.append(Element("style", children=[Text("a < b")]))
    assert doc.to_source() == _doc("<p>x</p><style>a < b</style>")


def test_editing_raw_text_content_is_reflected() -> None:
    doc = parse(_doc("<style>old</style>"), source_locations=True)
    node = doc.select_one("style")
    assert node is not None
    node.text = "new & fresh"
    assert doc.to_source() == _doc("<style>new & fresh</style>")


def test_attribute_edit_on_a_synthetic_element_reserializes_it() -> None:
    doc = parse("<p>x</p>", source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.attrs["class"] = "c"
    assert '<body class="c">' in doc.to_source()


def test_to_source_on_a_subtree_root() -> None:
    doc = parse(_doc('<p id="a">deep &amp; verbatim</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    assert node.to_source() == '<p id="a">deep &amp; verbatim</p>'


def test_without_source_locations_matches_serialize() -> None:
    doc = parse(_doc('<p class="x">y</p>'))
    assert doc.to_source() == doc.serialize()


def test_attribute_edit_without_locations_is_reflected() -> None:
    doc = parse(_doc('<p id="a">y</p>'))
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["id"] = "z"
    assert '<p id="z">y</p>' in doc.to_source()


def test_empty_text_node_serializes_to_nothing() -> None:
    assert not Text("").to_source()


def test_xml_processing_instruction_and_cdata_serialize() -> None:
    doc = parse_xml("<r><?pi go?><![CDATA[<raw>]]></r>")
    assert doc.to_source() == "<r><?pi go><![CDATA[<raw>]]></r>"


def test_a_fragment_round_trips_through_its_children() -> None:
    fragment = '<p class="x">a</p><!--c--><span>b</span>'
    root = parse_fragment(fragment, "div", source_locations=True)
    assert "".join(child.to_source() for child in root.children) == fragment


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<p title="a&amp;b">x</p>', id="entity-in-attribute"),
        pytest.param("<div><span>1</span> <span>2</span></div>", id="inter-element-whitespace"),
        pytest.param("<DIV CLASS=Big>Mixed</DIV>", id="mixed-case"),
        pytest.param("<ol><li>one<li>two</ol>", id="implicit-close"),
        pytest.param("<p>plain</p>", id="plain"),
    ],
)
def test_fragment_round_trips_are_byte_identical(markup: str) -> None:
    root = parse_fragment(markup, "body", source_locations=True)
    assert "".join(child.to_source() for child in root.children) == markup


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(_doc("<p>&copy; 2020 &mdash; done</p>"), id="named-entities"),
        pytest.param(_doc("<pre>\nleading newline</pre>"), id="dropped-newline"),
        pytest.param("<!doctype html><html><head></head><body><p>x</p></body></html>", id="lowercase-doctype"),
    ],
)
def test_normalizing_inputs_are_idempotent_and_reparse_equal(markup: str) -> None:
    doc = parse(markup, source_locations=True)
    once = doc.to_source()
    twice = parse(once, source_locations=True).to_source()
    assert once == twice
    assert parse(once).serialize() == doc.serialize()
