from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

import pytest

from turbohtml import (
    Canonical,
    CData,
    Comment,
    Doctype,
    Document,
    Element,
    Html,
    Indent,
    Minify,
    Node,
    ProcessingInstruction,
    Text,
    parse,
    parse_xml,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def test_text_carries_its_data() -> None:
    text = Text("a & b")
    assert text.data == "a & b"
    assert text.parent is None  # a freshly built node has no parent yet


def test_text_serializes_escaped() -> None:
    assert Text("Tom & Jerry <ok>").html == "Tom &amp; Jerry &lt;ok&gt;"


def test_comment_carries_its_data_and_serializes() -> None:
    comment = Comment("a note")
    assert comment.data == "a note"
    assert comment.html == "<!--a note-->"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # data built through the DOM can hold what a parsed comment cannot; the serialization keeps it one comment
        pytest.param("a--><b>x</b>", "<!--a-- ><b>x</b>-->", id="close"),
        pytest.param("a--!><b>x</b>", "<!--a-- !><b>x</b>-->", id="close-bang"),
        pytest.param("><b>x</b>", "<!-- ><b>x</b>-->", id="leading-gt"),
        pytest.param("-><b>x</b>", "<!-- -><b>x</b>-->", id="leading-dash-gt"),
        pytest.param("a --- b -", "<!--a --- b --->", id="dashes-kept"),
        pytest.param("a--", "<!--a---->", id="trailing-dashes-kept"),
        pytest.param("a--!", "<!--a--!-->", id="trailing-bang-kept"),
    ],
)
def test_comment_data_cannot_end_the_comment_early(data: str, expected: str) -> None:
    assert Comment(data).html == expected


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda node: node.to_source(), id="to-source"),
        pytest.param(lambda node: node.serialize(Html(layout=Indent())), id="indent"),
        pytest.param(lambda node: node.canonicalize(Canonical(with_comments=True)).decode(), id="canonical"),
    ],
)
def test_comment_data_stays_one_comment_in_every_serializer(render: Callable[[Node], str]) -> None:
    root = parse_xml("<r/>").select_one("r")
    assert root is not None
    root.append(Comment("a--><b>x</b>"))
    assert parse(render(root)).select("b") == []


@pytest.mark.parametrize(
    ("data", "xml", "canonical"),
    [
        # XML forbids `--` and a trailing `-` in a comment, so both outputs space them apart and stay well-formed
        pytest.param("a--><b>x</b>", "<!--a- -><b>x</b>-->", "<!--a- -><b>x</b>-->", id="close"),
        pytest.param("a--b", "<!--a- -b-->", "<!--a- -b-->", id="double-dash"),
        pytest.param("a-", "<!--a- -->", "<!--a- -->", id="trailing-dash"),
        # XHTML output also reaches HTML parsers, which end a comment at a leading `>` or `->`; canonical XML keeps
        # the data an XML parser reads back
        pytest.param("><b>x</b>", "<!-- ><b>x</b>-->", "<!--><b>x</b>-->", id="leading-gt"),
        pytest.param("-><b>x</b>", "<!-- -><b>x</b>-->", "<!---><b>x</b>-->", id="leading-dash-gt"),
    ],
)
def test_xml_comment_data_stays_well_formed(data: str, xml: str, canonical: str) -> None:
    root = parse_xml("<r/>").select_one("r")
    assert root is not None
    root.append(Comment(data))
    assert (root.serialize(Html(xml=True)), root.canonicalize(Canonical(with_comments=True)).decode()) == (
        f"<r>{xml}</r>",
        f"<r>{canonical}</r>",
    )


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda node: node.serialize(Html(xml=True)), id="xml"),
        pytest.param(lambda node: node.canonicalize(Canonical(with_comments=True)).decode(), id="canonical"),
    ],
)
def test_xml_comment_output_reparses_as_xml(render: Callable[[Node], str]) -> None:
    root = parse_xml("<r/>").select_one("r")
    assert root is not None
    root.append(Comment("a--b-"))
    reparsed = parse_xml(render(root)).select_one("r")
    assert reparsed is not None
    assert [cast("Comment", child).data for child in reparsed.children] == ["a- -b- "]


def test_canonical_keeps_parsed_comment_data() -> None:
    assert parse_xml("<r><!-->x--></r>").canonicalize(Canonical(with_comments=True)) == b"<r><!-->x--></r>"


def test_comment_data_stays_one_comment_when_minified() -> None:
    doc = parse("<p>y</p>")
    paragraph = doc.select_one("p")
    assert paragraph is not None
    paragraph.append(Comment("a--><b>x</b>"))
    assert parse(doc.serialize(Html(layout=Minify(strip_comments=False)))).select("b") == []


def test_xml_cdata_splits_its_end_marker() -> None:
    root = parse_xml("<r/>").select_one("r")
    assert root is not None
    root.append(CData("a]]x]]><b>x</b>"))
    out = root.serialize(Html(xml=True))
    assert out == "<r><![CDATA[a]]x]]]]><![CDATA[><b>x</b>]]></r>"
    reparsed = parse_xml(out).select_one("r")
    assert reparsed is not None
    assert "".join(cast("CData", section).data for section in reparsed.children) == "a]]x]]><b>x</b>"


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda node: node.serialize(Html(xml=True)), id="xml"),
        pytest.param(lambda node: node.canonicalize().decode(), id="canonical"),
    ],
)
def test_xml_processing_instruction_data_cannot_end_it_early(render: Callable[[Node], str]) -> None:
    root = parse_xml("<r/>").select_one("r")
    assert root is not None
    root.append(ProcessingInstruction("t", "d?x?><i>y</i>"))
    assert parse_xml(render(root)).select("i") == []


def test_empty_nodes() -> None:
    assert not Text("").data  # empty data round-trips as the empty string
    assert not Text("").html
    assert Comment("").html == "<!---->"


def test_data_is_keyword() -> None:
    assert Text(data="x").data == "x"
    assert Comment(data="x").data == "x"


def test_constructed_node_matches_structurally() -> None:
    match Text("hi"):
        case Text(data):
            assert data == "hi"
        case _:  # pragma: no cover - a Text always matches Text
            pytest.fail("did not match")


@pytest.mark.parametrize(
    "node_type",
    [pytest.param(Text, id="text"), pytest.param(Comment, id="comment")],
)
def test_data_must_be_a_str(node_type: type[Text | Comment]) -> None:
    with pytest.raises(TypeError):
        node_type(123)  # ty: ignore[invalid-argument-type]  # data must be a str


def test_element_bare() -> None:
    div = Element("div")
    assert div.tag == "div"
    assert div.html == "<div></div>"
    assert div.parent is None


def test_element_tag_is_lowercased() -> None:
    assert Element("DIV").tag == "div"  # matches what the parser stores


def test_element_with_attributes() -> None:
    anchor = Element("a", {"href": "/x", "class": "btn lg"})
    assert anchor.html == '<a href="/x" class="btn lg"></a>'  # attributes in insertion order
    assert anchor.attrs["href"] == "/x"
    assert anchor.attrs["class"] == ["btn", "lg"]  # the class token list reads back as a list


def test_element_list_valued_attribute_joins_on_space() -> None:
    assert Element("p", {"class": ["a", "b"]}).html == '<p class="a b"></p>'


def test_element_valueless_attribute() -> None:
    # a None value is a valueless attribute, which serializes empty per the spec
    assert Element("input", {"disabled": None}).html == '<input disabled="">'


def test_element_empty_attribute_value() -> None:
    # an empty string value is present but empty, distinct from a valueless None
    value = Element("input", {"value": ""}).attrs["value"]
    assert value is not None  # present, unlike a valueless attribute
    assert not value  # but empty


def test_element_attrs_none_is_no_attributes() -> None:
    assert Element("div", None).html == "<div></div>"


def test_element_void_has_no_end_tag() -> None:
    assert Element("br").html == "<br>"


def test_element_rawtext_serializes_text_literally() -> None:
    # a constructed raw-text element carries the atom's flags, so its text is not escaped (issue #86)
    style = Element("style")
    style.text = "a < b"
    assert style.html == "<style>a < b</style>"


def test_element_unknown_tag_constructs() -> None:
    assert Element("my-widget").html == "<my-widget></my-widget>"
    assert Element("x" * 70).tag == "x" * 70  # a tag too long for the atom table
    assert Element("\ud800").tag == "\ud800"  # a tag that cannot encode to UTF-8


def test_element_attribute_name_is_lowercased() -> None:
    assert Element("div", {"DATA-X": "1"}).attrs["data-x"] == "1"


@pytest.mark.parametrize(
    "attrs",
    [
        pytest.param({"ID": "a", "id": "b"}, id="upper-first"),
        pytest.param({"id": "a", "Id": "b", "ID": "c"}, id="three-spellings"),
    ],
)
def test_element_attribute_names_folding_together_keep_the_first(attrs: dict[str, str]) -> None:
    assert Element("div", {**attrs, "title": "t"}).html == '<div id="a" title="t"></div>'


def test_element_attribute_names_folding_together_count_once() -> None:
    assert len(Element("div", {"ID": "a", "id": "b"}).attrs) == 1


@pytest.mark.parametrize(
    "tag",
    # an empty name has nothing to write; the others could not round-trip if written
    [
        pytest.param("", id="empty"),
        pytest.param("a b", id="space"),
        pytest.param("a/b", id="slash"),
        pytest.param("a>b", id="gt"),
        pytest.param("a<b", id="lt"),
        pytest.param("a=b", id="eq"),
        pytest.param('a"b', id="dquote"),
        pytest.param("a'b", id="squote"),
    ],
)
def test_element_tag_is_rejected(tag: str) -> None:
    with pytest.raises(ValueError, match=r"empty|invalid character"):
        Element(tag)


def test_element_tag_must_be_a_str() -> None:
    with pytest.raises(TypeError, match="tag must be a str, not int"):
        Element(5)  # ty: ignore[invalid-argument-type]  # tag must be a str


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("a b", id="space"),
        pytest.param("a/b", id="slash"),
        pytest.param("a>b", id="gt"),
        pytest.param("a=b", id="eq"),
        pytest.param("=a", id="leading-eq"),
        pytest.param("a\x00b", id="null"),
        pytest.param("a\x0cb", id="form-feed"),
    ],
)
def test_element_attribute_name_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match=r"empty|invalid character"):
        Element("div", {name: "x"})


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<p zeitmaschine":=1>', id="quote"),
        pytest.param("<p a'b=1>", id="apostrophe"),
        pytest.param("<p a<b=1>", id="less-than"),
        pytest.param("<p a\x01b=1>", id="control"),
    ],
)
def test_attribute_name_the_parser_produces_can_be_set_again(markup: str) -> None:
    paragraph = parse(markup).select_one("p")
    assert paragraph is not None
    name = next(iter(paragraph.attrs))
    paragraph.attrs[name] = "v"
    assert paragraph.attrs[name] == "v"


@pytest.mark.parametrize("name", [pytest.param('a"b', id="quote"), pytest.param("a<b", id="less-than")])
def test_element_accepts_a_parser_producible_attribute_name(name: str) -> None:
    element = Element("div", {name: "x"})
    reparsed = parse(element.html).select_one("div")
    assert reparsed is not None
    assert dict(reparsed.attrs) == {name: "x"}


@pytest.mark.parametrize(
    ("attrs", "message"),
    [
        pytest.param({"x": 1}, "attribute value", id="non-str-value"),
        pytest.param({1: "x"}, "attribute name", id="non-str-name"),
    ],
)
def test_element_rejects_non_str_attribute(attrs: dict[object, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        Element("div", attrs)  # ty: ignore[invalid-argument-type]  # name and value must be str/list/None


def test_element_list_member_must_be_str() -> None:
    with pytest.raises(TypeError):
        Element("div", {"class": [1, 2]})  # ty: ignore[invalid-argument-type]  # members must be str


@pytest.mark.parametrize("attrs", [5, [("a", "b")]], ids=["int", "list"])
def test_element_attrs_must_be_a_mapping(attrs: object) -> None:
    # a value without keys() is not a mapping; report that as a TypeError, not the raw AttributeError
    with pytest.raises(TypeError, match="attrs must be a mapping"):
        Element("div", attrs)  # ty: ignore[invalid-argument-type]  # attrs must be a mapping


def test_element_mapping_getitem_failure_propagates() -> None:
    class BadMapping:
        def keys(self) -> list[str]:  # ruff:ignore[no-self-use]  # a mapping protocol method must be an instance method
            return ["x"]

        def __getitem__(self, key: str) -> str:
            raise KeyError(key)

    with pytest.raises(KeyError):
        Element("div", BadMapping())  # ty: ignore[invalid-argument-type]  # a deliberately broken mapping


def test_element_mapping_keys_failure_is_not_masked() -> None:
    class RaisingKeys:
        def keys(self) -> list[str]:  # ruff:ignore[no-self-use]  # a mapping protocol method must be an instance method
            msg = "boom"
            raise RuntimeError(msg)

    # keys() exists but raises: surface that error rather than mislabeling it a non-mapping TypeError
    with pytest.raises(RuntimeError):
        Element("div", RaisingKeys())  # ty: ignore[invalid-argument-type]  # keys() raises


@pytest.mark.parametrize(
    "duplicate",
    [pytest.param(copy.copy, id="shallow"), pytest.param(copy.deepcopy, id="deep")],
)
def test_duplicates_a_standalone_subtree(duplicate: Callable[[Element], Element]) -> None:
    div = parse('<div id="a"><b>x</b>y</div>').find("div")
    assert div is not None
    clone = duplicate(div)
    assert clone is not div
    assert clone.html == '<div id="a"><b>x</b>y</div>'
    assert clone.parent is None  # both shallow and deep yield a detached root, not a view


@pytest.mark.parametrize(
    "duplicate",
    [pytest.param(copy.copy, id="shallow"), pytest.param(copy.deepcopy, id="deep")],
)
def test_xml_duplicate_keeps_names_case_sensitive(duplicate: Callable[[Element], Element]) -> None:
    root = parse_xml('<Root Attr="v"><Child X="1"/></Root>').root
    assert isinstance(root, Element)
    clone = duplicate(root)
    assert clone.tag == "Root"
    assert clone.attrs["Attr"] == "v"
    assert "attr" not in clone.attrs
    child = clone.children[0]
    assert isinstance(child, Element)
    assert child.attrs["X"] == "1"


def test_copy_is_independent_of_the_original() -> None:
    div = parse("<div><b>x</b></div>").find("div")
    assert div is not None
    clone = copy.copy(div)
    clone.append(Element("z"))  # editing the copy must not touch the source
    assert div.html == "<div><b>x</b></div>"
    assert clone.html == "<div><b>x</b><z></z></div>"


def test_copy_of_a_constructed_node() -> None:
    element = Element("p", {"class": ["a", "b"]})
    element.append(Text("hi"))
    assert copy.copy(element).html == '<p class="a b">hi</p>'


@pytest.mark.parametrize(
    "duplicate",
    [pytest.param(copy.copy, id="shallow"), pytest.param(copy.deepcopy, id="deep")],
)
def test_copy_preserves_doctype_identifiers(duplicate: Callable[[Doctype], Doctype]) -> None:
    # the clone must carry the public-id split point so an embedded quote survives (part of #478)
    doctype = parse("<!DOCTYPE html PUBLIC 'pub\"lic' 'sys\"tem'>").children[0]
    assert isinstance(doctype, Doctype)
    clone = duplicate(doctype)
    assert (clone.public_id, clone.system_id) == ('pub"lic', 'sys"tem')


def _doctype(markup: str) -> Doctype:
    node = parse(markup).children[0]  # the doctype is the document's first child
    assert isinstance(node, Doctype)
    return node


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
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN">',
            "-//W3C//DTD HTML 4.01//EN",
            None,  # no system identifier was supplied, so it is missing, not empty
            id="public-only-missing-system",
        ),
        pytest.param(
            '<!DOCTYPE html SYSTEM "about:legacy-compat">',
            None,  # SYSTEM supplies no public identifier, so it is missing, not empty
            "about:legacy-compat",
            id="system-only-missing-public",
        ),
        pytest.param(
            '<!DOCTYPE html PUBLIC "p" "">',
            "p",
            "",  # a system identifier given as "" is present but empty, distinct from missing
            id="public-and-empty-system",
        ),
        pytest.param(
            "<!DOCTYPE html SYSTEM 'taco\"quote'>",
            None,
            'taco"quote',  # a quote embedded in the single-quoted identifier survives (part of #478)
            id="system-embedded-quote",
        ),
        pytest.param(
            "<!DOCTYPE html PUBLIC 'pub\"lic' 'sys\"tem'>",
            'pub"lic',
            'sys"tem',  # both identifiers keep their embedded quotes
            id="public-and-system-embedded-quotes",
        ),
    ],
)
def test_doctype_identifiers(markup: str, public_id: str | None, system_id: str | None) -> None:
    doctype = _doctype(markup)
    assert doctype.name == "html"
    assert doctype.public_id == public_id
    assert doctype.system_id == system_id


def test_doctype_matches_on_name() -> None:
    match _doctype("<!DOCTYPE html>"):
        case Doctype(name):
            assert name == "html"
        case _:  # pragma: no cover - the doctype always matches
            pytest.fail("doctype did not match")


def test_document_matches_on_root() -> None:
    match parse("<title>t</title>"):
        case Document(Element(tag)):
            assert tag == "html"
        case _:  # pragma: no cover - a parsed document always has an html root
            pytest.fail("document did not match")


def test_pi_carries_target_and_data() -> None:
    pi = ProcessingInstruction("xml-stylesheet", 'href="a.css"')
    assert pi.target == "xml-stylesheet"
    assert pi.data == 'href="a.css"'
    assert pi.html == '<?xml-stylesheet href="a.css">'
    assert pi.parent is None


def test_pi_with_empty_data() -> None:
    pi = ProcessingInstruction("php", "")
    assert pi.target == "php"
    assert not pi.data
    assert pi.html == "<?php >"


def test_pi_data_is_keyword() -> None:
    assert ProcessingInstruction(target="t", data="d").data == "d"


def test_pi_repr() -> None:
    assert repr(ProcessingInstruction("t", "d")) == "ProcessingInstruction('t', 'd')"


def test_pi_matches_structurally() -> None:
    match ProcessingInstruction("xml", "v=1"):
        case ProcessingInstruction(target, data):
            assert target == "xml"
            assert data == "v=1"
        case _:  # pragma: no cover - a PI always matches
            pytest.fail("did not match")


@pytest.mark.parametrize(
    "target",
    [pytest.param("", id="empty"), pytest.param("a b", id="space"), pytest.param("a>b", id="gt")],
)
def test_pi_rejects_bad_target(target: str) -> None:
    with pytest.raises(ValueError, match=r"empty|invalid character"):
        ProcessingInstruction(target, "d")


@pytest.mark.parametrize(
    ("target", "data"),
    [pytest.param(1, "d", id="target"), pytest.param("t", 1, id="data")],
)
def test_pi_rejects_non_str_field(target: object, data: object) -> None:
    with pytest.raises(TypeError):
        ProcessingInstruction(target, data)  # ty: ignore[invalid-argument-type]  # target and data must be str


def test_cdata_carries_data() -> None:
    cdata = CData("x < y & z")
    assert cdata.data == "x < y & z"
    assert cdata.html == "<![CDATA[x < y & z]]>"  # content is verbatim, not escaped
    assert cdata.parent is None


def test_cdata_empty() -> None:
    assert CData("").html == "<![CDATA[]]>"


def test_cdata_repr() -> None:
    assert repr(CData("hi")) == "CData('hi')"


def test_cdata_matches_structurally() -> None:
    match CData("payload"):
        case CData(data):
            assert data == "payload"
        case _:  # pragma: no cover - a CData always matches
            pytest.fail("did not match")


def test_cdata_data_is_settable() -> None:
    cdata = CData("old")
    cdata.data = "new"
    assert cdata.html == "<![CDATA[new]]>"


def test_cdata_data_must_be_str() -> None:
    with pytest.raises(TypeError):
        CData(1)  # ty: ignore[invalid-argument-type]  # data must be a str


def test_pi_and_cdata_embed_in_a_tree() -> None:
    div = Element("div")
    div.extend([Text("a"), ProcessingInstruction("t", "d"), CData("c"), Comment("k")])
    assert div.html == "<div>a<?t d><![CDATA[c]]><!--k--></div>"


def test_pi_adopts_across_trees_keeping_both_halves() -> None:
    box = Element("section")
    box.append(ProcessingInstruction("xml", 'version="1.0"'))  # adopt must preserve the packed target/data split
    held = box.children[0]
    assert isinstance(held, ProcessingInstruction)
    assert held.target == "xml"
    assert held.data == 'version="1.0"'


def test_pi_and_cdata_serialize_pretty() -> None:
    root = Element("root")
    root.extend([CData("d"), ProcessingInstruction("t", "x")])
    assert root.serialize(Html(layout=Indent(2))) == "<root><![CDATA[d]]><?t x></root>"


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
