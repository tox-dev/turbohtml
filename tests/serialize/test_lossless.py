from __future__ import annotations

from typing import Final

import pytest

from turbohtml import (
    CData,
    Element,
    Formatter,
    Html,
    IncrementalParser,
    ProcessingInstruction,
    Text,
    parse,
    parse_fragment,
    parse_xml,
)

_DOC: Final[str] = "<!DOCTYPE html><html><head></head><body>{}</body></html>"


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(_DOC.format('<p class="x">Hi &amp; bye</p>'), id="attr-and-entity"),
        pytest.param(_DOC.format("<A HREF='u'>t</A>"), id="uppercase-single-quote"),
        pytest.param(_DOC.format('<p  class = "x"  id=y >z</p>'), id="whitespace-in-tag"),
        pytest.param(_DOC.format("<input disabled required>"), id="valueless-attrs"),
        pytest.param(_DOC.format("<a href=plain>t</a>"), id="unquoted-attr"),
        pytest.param(_DOC.format("<img src=x><br><hr>"), id="void-elements"),
        pytest.param(_DOC.format("<ul><li>a<li>b</li></ul>"), id="implicitly-closed"),
        pytest.param(
            _DOC.format("<dl><dt>a<dd>b</dl><table><tbody><tr><td>c<td>d<tr><td>e</table><p>f<p>g"),
            id="optional-end-tags",
        ),
        pytest.param(_DOC.format("<!--a comment--><p>x</p>"), id="comment"),
        pytest.param(_DOC.format('<svg width="1"><rect/></svg>'), id="foreign-svg"),
        pytest.param(_DOC.format("<template><p>t</p></template>"), id="template-content"),
        pytest.param(_DOC.format("<script>a<b && c</script>"), id="raw-text-element"),
        pytest.param(_DOC.format("<p>Ωé and 😀</p>"), id="wide-text"),
        pytest.param(
            "<!DOCTYPE html><html><head><style>\na { color: red }\nb { color: blue }\n</style></head>"
            "<body></body></html>",
            id="raw-text-span",
        ),
        pytest.param(_DOC.format("<pre>x\ny</pre>"), id="preformatted"),
        pytest.param(_DOC.format("<pre>\n\nkept</pre>"), id="preformatted-leading-newline"),
        pytest.param(_DOC.format("<table><tbody><tr><td>c</td></tr></tbody></table>"), id="explicit-table"),
        pytest.param(_DOC.format("<div>plain unicode: \xa9 keep</div>"), id="latin1-text"),
        pytest.param(_DOC.format("<div>bmp: €あ</div>"), id="bmp-text"),
        pytest.param(_DOC.format("<div>astral: \U0001f600</div>"), id="astral-text"),
        pytest.param("<!DOCTYPE html><html><head></head><body></body></html>", id="empty-body"),
    ],
)
def test_unedited_round_trip_is_byte_identical(markup: str) -> None:
    assert parse(markup, source_locations=True).to_source() == markup


@pytest.mark.parametrize(
    ("doctype", "expected"),
    [
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 3.2//EN">',
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 3.2//EN">',
            id="quirky-public",
        ),
        pytest.param(
            "<!doctype html system 'http://www.ibm.com/data/dtd/v11/ibmxhtml1-transitional.dtd'>",
            '<!DOCTYPE html SYSTEM "http://www.ibm.com/data/dtd/v11/ibmxhtml1-transitional.dtd">',
            id="quirky-system",
        ),
        pytest.param("<!DOCTYPE html bogus>", "<!DOCTYPE html x>", id="forced-quirks-name"),
        pytest.param('<!DOCTYPE html PUBLIC "a" "b>', '<!DOCTYPE html PUBLIC "a" "b>', id="forced-quirks-system"),
    ],
)
def test_doctype_keeps_the_document_mode(doctype: str, expected: str) -> None:
    # the <table> nests in the <p> only in quirks mode, so the output must reparse in the source's mode
    rest = "<html><head></head><body><p>x<table></table></body></html>"
    doc = parse(doctype + rest, source_locations=True)
    assert doc.to_source() == expected + rest
    assert parse(doc.to_source()).html == doc.html


def test_doctype_identifiers_round_trip() -> None:
    markup = '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">'
    markup += "<html><head></head><body></body></html>"
    assert parse(markup, source_locations=True).to_source() == markup


@pytest.mark.parametrize(
    ("doctype", "expected"),
    [
        pytest.param('<!DOCTYPE r SYSTEM "r.dtd">', '<!DOCTYPE r SYSTEM "r.dtd">', id="spellable"),
        pytest.param('<!DOCTYPE r SYSTEM "a><img>">', "<!DOCTYPE r x>", id="system-ends-the-doctype"),
        pytest.param("<!DOCTYPE r PUBLIC \"p\" 'a><img>'>", "<!DOCTYPE r x>", id="public-and-system"),
    ],
)
def test_xml_doctype_literal_is_not_written_as_markup(doctype: str, expected: str) -> None:
    assert parse_xml(f"{doctype}<r/>").to_source() == f"{expected}<r></r>"


def test_carriage_returns_use_retained_normalized_source() -> None:
    markup = _DOC.format("<p>\r</p>" * 4096)
    assert parse(markup, source_locations=True).to_source() == markup.replace("\r", "\n")


def test_changing_an_attribute_rewrites_only_that_start_tag() -> None:
    doc = parse(_DOC.format('<p id="a">one</p><p id="b">two</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["id"] = "CHANGED"
    assert doc.to_source() == _DOC.format('<p id="CHANGED">one</p><p id="b">two</p>')


def test_adding_an_attribute_rebuilds_the_start_tag() -> None:
    doc = parse(_DOC.format('<p id="a">x</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["data-n"] = "1"
    assert doc.to_source() == _DOC.format('<p id="a" data-n="1">x</p>')


def test_deleting_an_attribute_rebuilds_the_start_tag() -> None:
    doc = parse(_DOC.format('<p id="a" data-n="1">x</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    del node.attrs["data-n"]
    assert doc.to_source() == _DOC.format('<p id="a">x</p>')


def test_editing_text_re_escapes_only_that_run() -> None:
    doc = parse(_DOC.format("<p>keep</p><p>edit</p>"), source_locations=True)
    doc.select("p")[1].text = "a < b & c"
    assert doc.to_source() == _DOC.format("<p>keep</p><p>a &lt; b &amp; c</p>")


def test_clearing_text_emits_nothing_for_that_run() -> None:
    doc = parse(_DOC.format("<p>gone</p>"), source_locations=True)
    node = doc.select_one("p")
    assert node is not None
    node.text = ""
    assert doc.to_source() == _DOC.format("<p></p>")


def test_removing_a_node_drops_its_span_and_preserves_siblings() -> None:
    doc = parse(_DOC.format('<p id="a">one</p><p id="b">two</p>'), source_locations=True)
    node = doc.select_one("#a")
    assert node is not None
    node.extract()
    assert doc.to_source() == _DOC.format('<p id="b">two</p>')


def test_inserting_an_element_serializes_it_canonically() -> None:
    doc = parse(_DOC.format("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.append(Element("section", children=[Text("new")]))
    assert doc.to_source() == _DOC.format("<p>x</p><section>new</section>")


def test_inserting_a_void_element_serializes_it_canonically() -> None:
    doc = parse(_DOC.format("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.append(Element("br"))
    assert doc.to_source() == _DOC.format("<p>x</p><br>")


def test_inserting_a_raw_text_element_keeps_its_content_literal() -> None:
    doc = parse(_DOC.format("<p>x</p>"), source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.append(Element("style", children=[Text("a < b")]))
    assert doc.to_source() == _DOC.format("<p>x</p><style>a < b</style>")


def test_editing_raw_text_content_is_reflected() -> None:
    doc = parse(_DOC.format("<style>old</style>"), source_locations=True)
    node = doc.select_one("style")
    assert node is not None
    node.text = "new & fresh"
    assert doc.to_source() == _DOC.format("<style>new & fresh</style>")


def test_attribute_edit_on_a_synthetic_element_reserializes_it() -> None:
    doc = parse("<p>x</p>", source_locations=True)
    body = doc.select_one("body")
    assert body is not None
    body.attrs["class"] = "c"
    assert '<body class="c">' in doc.to_source()


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<svg><body><style><img src=x onerror=alert(1)></style>", id="svg-closed-by-a-dropped-body"),
        pytest.param("<math><head><xmp><img src=x onerror=alert(1)></xmp>", id="math-closed-by-a-dropped-head"),
        pytest.param("<svg><g><body><style><img src=x></style>", id="nested-foreign"),
        pytest.param("<table><td><svg><body><td><style><img src=x></style></table>", id="foreign-last-in-a-cell"),
        pytest.param(
            "<select><select><style></select><img src=x onerror=alert(1)></style>", id="select-closed-by-a-select"
        ),
        pytest.param("<div><select><select></div><style><img src=x></style>", id="select-last-in-its-parent"),
        pytest.param("<table><div>x<tr><td>y</table>", id="foster-parented"),
        pytest.param("<table><li><dd>a<tr><li>b</table>", id="fostered-item-ending-an-item"),
        pytest.param("<table><option><rt>a<tr><option>b</table>", id="fostered-rt-ending-an-option"),
        pytest.param("<table><optgroup><optgroup>a</table>", id="fostered-optgroup-ending-an-optgroup"),
        pytest.param("<table><math><mi><option>a</table>", id="fostered-option-ending-a-foreign-element"),
        pytest.param("<b><i>x</b>y", id="reconstructed-formatting"),
        pytest.param("<i><dd><li><dd></i>", id="inside-formatting"),
        pytest.param("<form><div><p>x</form>y", id="p-before-text"),
        pytest.param("<textarea>x", id="rcdata-at-eof"),
        pytest.param("<script>x", id="raw-text-at-eof"),
        pytest.param("<div><plaintext>x</div>", id="plaintext"),
        pytest.param("<svg><path><path/><g/></path><g></g></svg>", id="foreign-self-closing"),
        pytest.param("<svg><path d=x/><g/></svg>", id="foreign-slash-in-an-unquoted-value"),
    ],
)
def test_an_element_the_source_did_not_close_keeps_its_extent(markup: str) -> None:
    doc = parse(markup, source_locations=True)
    assert parse(doc.to_source()).serialize() == doc.serialize()


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            "<svg><body><style><a></style>",
            "<html><head></head><body><svg></svg><style><a></style></body></html>",
            id="foreign",
        ),
        pytest.param(
            "<select><select><style><a></style>",
            "<html><head></head><body><select></select><style><a></style></body></html>",
            id="no-optional-end-tag",
        ),
        pytest.param(
            _DOC.format("<div><span>x</div>"), _DOC.format("<div><span>x</span></div>"), id="closed-by-its-parent"
        ),
        pytest.param(_DOC.format("<p>a<p>b<div>c</div>"), _DOC.format("<p>a<p>b<div>c</div>"), id="optional-end-tag"),
        pytest.param(_DOC.format("<svg><path/><g/></svg>"), _DOC.format("<svg><path/><g/></svg>"), id="self-closing"),
        pytest.param(
            "<div><plaintext>x</div>",
            "<html><head></head><body><div><plaintext>x</div>",
            id="nothing-after-plaintext",
        ),
    ],
)
def test_a_missing_end_tag_is_written_unless_a_reparse_implies_it(markup: str, expected: str) -> None:
    assert parse(markup, source_locations=True).to_source() == expected


def test_removing_the_sibling_that_closed_an_element_keeps_it_closed() -> None:
    doc = parse(_DOC.format("<svg><p></p><style><a></style>"), source_locations=True)
    node = doc.select_one("p")
    assert node is not None
    node.extract()
    assert doc.to_source() == _DOC.format("<svg></svg><style><a></style>")


def test_editing_a_self_closed_foreign_element_closes_it() -> None:
    doc = parse(_DOC.format("<svg><path/><g/></svg>"), source_locations=True)
    node = doc.select_one("path")
    assert node is not None
    node.attrs["d"] = "1"
    assert doc.to_source() == _DOC.format('<svg><path d="1"></path><g/></svg>')


def test_a_self_closed_foreign_element_given_a_child_opens_around_it() -> None:
    doc = parse(_DOC.format("<svg><g/><g/></svg>"), source_locations=True)
    node = doc.select_one("g")
    assert node is not None
    node.append(Text("x"))
    assert doc.to_source() == _DOC.format("<svg><g>x</g><g/></svg>")


def test_a_detached_element_the_source_left_open_is_closed() -> None:
    node = parse(_DOC.format("<ul><li>a<li>b</ul>"), source_locations=True).select_one("li")
    assert node is not None
    assert node.to_source() == "<li>a"
    node.extract()
    assert node.to_source() == "<li>a</li>"


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("<ul><li><a href=x>y</a><li>b</ul>", id="item-ending-in-a-link"),
        pytest.param("<p>a <b>b</b><p>c", id="paragraph-ending-in-bold"),
        pytest.param(
            "<table><tbody><tr><td><i>a</i><td><a href=x>b</a><tr><td>c</table>", id="cell-ending-in-formatting"
        ),
        pytest.param("<dl><dt><em>a</em><dd><strong>b</strong></dl>", id="description-ending-in-formatting"),
        pytest.param("<a href=x><ul><li>a<li>b</ul></a><ul><li>c<li>d</ul>", id="list-inside-a-link"),
    ],
)
def test_optional_end_tag_stays_out_around_formatting_the_source_closed(body: str) -> None:
    # a formatting element written back with its own source end tag leaves nothing for a reparse to reconstruct
    doc = parse(_DOC.format(body), source_locations=True)
    assert doc.to_source() == _DOC.format(body)
    assert parse(doc.to_source()).serialize() == doc.serialize()


def test_a_subtree_closes_what_the_document_walk_closes() -> None:
    # the unclosed <b> around the first list blocks omission there, for the whole document and for one item alike
    doc = parse(_DOC.format("<b><ul><li>a<li>b</ul><ul><li>c<li>d</ul>"), source_locations=True)
    assert doc.to_source() == _DOC.format("<b><ul><li>a</li><li>b</li></ul><ul><li>c</li><li>d</li></ul></b>")
    assert [item.to_source() for item in doc.select("li")] == ["<li>a</li>", "<li>b</li>", "<li>c</li>", "<li>d</li>"]


def test_a_renamed_formatting_element_blocks_omission_inside_it() -> None:
    doc = parse(_DOC.format("<i><ul><li>a<li>b</ul></i>"), source_locations=True)
    node = doc.select_one("i")
    assert node is not None
    node.tag = "b"
    assert doc.to_source() == _DOC.format("<b><ul><li>a</li><li>b</li></ul></b>")


def test_to_source_on_a_subtree_root() -> None:
    node = parse(_DOC.format('<p id="a">deep &amp; verbatim</p>'), source_locations=True).select_one("#a")
    assert node is not None
    assert node.to_source() == '<p id="a">deep &amp; verbatim</p>'


def test_without_source_locations_matches_serialize() -> None:
    doc = parse(_DOC.format('<p class="x">y</p>'))
    assert doc.to_source() == doc.serialize()


def test_attribute_edit_without_locations_is_reflected() -> None:
    doc = parse(_DOC.format('<p id="a">y</p>'))
    node = doc.select_one("#a")
    assert node is not None
    node.attrs["id"] = "z"
    assert '<p id="z">y</p>' in doc.to_source()


def test_empty_text_node_serializes_to_nothing() -> None:
    assert not Text("").to_source()


def test_xml_processing_instruction_and_cdata_serialize() -> None:
    doc = parse_xml("<r><?pi go?><![CDATA[x < y]]><![CDATA[<raw>]]></r>")
    assert doc.to_source() == "<r><?pi go><![CDATA[x < y]]>&lt;raw&gt;</r>"


def test_inserted_processing_instruction_and_cdata_stay_inert() -> None:
    doc = parse(_DOC.format("<p>x</p>"), source_locations=True)
    paragraph = doc.select_one("p")
    assert paragraph is not None
    paragraph.append(ProcessingInstruction("a", "b><i>y</i>"))
    paragraph.append(CData("c><b>z</b>"))
    assert parse(doc.to_source()).select("b, i") == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            "<math><mi><![CDATA[<img src=x title=text>]]></mi></math>",
            "<math><mi>&lt;img src=x title=text&gt;</mi></math>",
            id="cdata-at-mathml-text-integration-point",
        ),
        pytest.param(
            "<svg><desc><![CDATA[&lt;b&gt;]]></desc></svg>",
            "<svg><desc>&amp;lt;b&amp;gt;</desc></svg>",
            id="cdata-at-html-integration-point",
        ),
        pytest.param(
            "<p><<b>x</b>img src=x title=text></p>",
            "<p>&lt;<b>x</b>img src=x title=text></p>",
            id="less-than-ahead-of-a-tag",
        ),
        pytest.param(
            "<math><mi><![CDATA[<i title=text>Ωé]]></mi></math>",
            "<math><mi>&lt;i title=text&gt;Ωé</mi></math>",
            id="wide-cdata-at-mathml-text-integration-point",
        ),
        pytest.param(
            "<svg><desc><![CDATA[&lt;Ω&gt;]]></desc></svg>",
            "<svg><desc>&amp;lt;Ω&amp;gt;</desc></svg>",
            id="wide-cdata-with-reference-at-html-integration-point",
        ),
    ],
)
def test_source_text_holding_markup_characters_is_escaped(body: str, expected: str) -> None:
    doc = parse(_DOC.format(body), source_locations=True)
    assert doc.to_source() == _DOC.format(expected)
    assert parse(doc.to_source()).serialize() == doc.serialize()


def test_text_left_adjacent_by_a_removal_stays_text() -> None:
    doc = parse(_DOC.format("<p><<b></b>img src=x title=text></p>"), source_locations=True)
    node = doc.select_one("b")
    assert node is not None
    node.decompose()
    assert doc.to_source() == _DOC.format("<p>&lt;img src=x title=text></p>")
    assert parse(doc.to_source()).select("img") == []


def test_raw_text_of_a_renamed_element_is_escaped() -> None:
    node = parse("<body><plaintext><img src=x title=text> &amp;", source_locations=True).select_one("plaintext")
    assert node is not None
    node.tag = "div"
    assert node.to_source() == "<div>&lt;img src=x title=text&gt; &amp;amp;</div>"


def test_raw_text_after_an_inserted_element_stays_literal() -> None:
    doc = parse(_DOC.format("<script>a && b</script>"), source_locations=True)
    node = doc.select_one("script")
    assert node is not None
    node.insert(0, Element("i"))
    assert doc.to_source() == _DOC.format("<script><i></i>a && b</script>")


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
        pytest.param(_DOC.format("<p>&copy; 2020 &mdash; done</p>"), id="named-entities"),
        pytest.param(_DOC.format("<pre>\nleading newline</pre>"), id="dropped-newline"),
        pytest.param(_DOC.format("<p>a&#xd;b</p>"), id="carriage-return-reference"),
        pytest.param("<!doctype html><html><head></head><body><p>x</p></body></html>", id="lowercase-doctype"),
    ],
)
def test_normalizing_inputs_are_idempotent_and_reparse_equal(markup: str) -> None:
    doc = parse(markup, source_locations=True)
    once = doc.to_source()
    twice = parse(once, source_locations=True).to_source()
    assert once == twice
    assert parse(once).serialize() == doc.serialize()


@pytest.mark.parametrize(
    "formatter",
    [
        pytest.param(Formatter.WHATWG, id="whatwg"),
        pytest.param(Formatter.MINIMAL, id="minimal"),
        pytest.param(Formatter.NAMED_ENTITIES, id="named"),
    ],
)
@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<p>a&#xd;b</p>", id="text"),
        pytest.param('<p title="a&#xd;b">x</p>', id="attribute"),
        pytest.param("<pre>&#xd;x</pre>", id="preformatted"),
    ],
)
def test_carriage_return_serialization_is_idempotent_and_reparse_equal(markup: str, formatter: Formatter) -> None:
    # a raw CR reparses as LF, so the serializer must write it as a reference for the output to read back unchanged
    html = Html(formatter=formatter)
    once = parse(markup).serialize(html)
    assert parse(once).serialize(html) == once
    assert parse(once).text == parse(markup).text


@pytest.mark.parametrize(
    "chunks",
    [
        pytest.param([_DOC.format("<p id=a>x</p><b>y</b>")], id="one-chunk"),
        pytest.param(
            ["<!DOCTYPE html><html><head></head><body><p id=", "a>x</p><b>y</b></body></html>"], id="two-chunks"
        ),
        pytest.param(list(_DOC.format("<p id=a>x\r\n</p><b>y</b>")), id="per-character"),
    ],
)
def test_incremental_parser_round_trip_is_byte_identical(chunks: list[str]) -> None:
    parser = IncrementalParser(source_locations=True)
    for chunk in chunks:
        parser.feed(chunk)
    assert parser.close().to_source() == "".join(chunks).replace("\r\n", "\n")
