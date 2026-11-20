from __future__ import annotations

import copy
import re
from collections import OrderedDict
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Comment, Document, DocumentFragment, Element, Text, parse, parse_fragment
from turbohtml.build import E, ElementMaker, document

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from turbohtml import Node


@pytest.fixture
def maker() -> ElementMaker:
    # A private builder, independent of the shared ``E`` singleton.
    return ElementMaker()


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(E.div, "<div></div>", id="empty-element-keeps-end-tag"),
        pytest.param(E.br, "<br>", id="void-element-has-no-end-tag"),
        pytest.param(lambda: E.p("body"), "<p>body</p>", id="text-child"),
        pytest.param(lambda: E.div({"class": "card"}), '<div class="card"></div>', id="leading-mapping-is-attributes"),
        pytest.param(lambda: E.div({"class": ["a", "b"]}), '<div class="a b"></div>', id="list-attribute-joins"),
        pytest.param(lambda: E.input({"disabled": None}), '<input disabled="">', id="none-attribute-is-valueless"),
        pytest.param(
            lambda: E.div({"class": "card"}, E.h1("Title"), E.p("body")),
            '<div class="card"><h1>Title</h1><p>body</p></div>',
            id="nesting-builds-real-children",
        ),
        pytest.param(lambda: E.p("a", E.b("b"), "c"), "<p>a<b>b</b>c</p>", id="children-keep-order"),
        pytest.param(lambda: E.div(Comment("note")), "<div><!--note--></div>", id="non-string-node-passes-through"),
        pytest.param(
            lambda: E.div("before", E.span("mid"), "after"),
            "<div>before<span>mid</span>after</div>",
            id="mixed-text-and-element-children",
        ),
        pytest.param(lambda: E("div", "body"), "<div>body</div>", id="call-form-names-the-tag"),
        pytest.param(lambda: E("a", {"href": "/x"}, "link"), '<a href="/x">link</a>', id="call-form-leading-mapping"),
        pytest.param(lambda: E("my-widget"), "<my-widget></my-widget>", id="call-form-non-identifier-tag"),
        pytest.param(
            lambda: E.div(OrderedDict([("id", "x")]), "t"),
            '<div id="x">t</div>',
            id="leading-dict-subclass-is-attributes",
        ),
        pytest.param(
            lambda: E.div(MappingProxyType({"id": "x"}), "t"),
            '<div id="x">t</div>',
            id="leading-non-dict-mapping-is-attributes",
        ),
        pytest.param(lambda: E.div(E.span("s"), "t"), "<div><span>s</span>t</div>", id="leading-element-is-a-child"),
        pytest.param(lambda: E.p(Text("t"), "u"), "<p>tu</p>", id="leading-text-node-is-a-child"),
    ],
)
def test_serialize(build: Callable[[], Element], expected: str) -> None:
    assert build().serialize() == expected


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(
            lambda: E.section(E.h2("Heading"), E.p("text")),
            "<section><h2>Heading</h2><p>text</p></section>",
            id="section",
        ),
        pytest.param(lambda: E.ul(E.li("one"), E.li("two")), "<ul><li>one</li><li>two</li></ul>", id="list"),
    ],
)
def test_round_trips_through_parse(build: Callable[[], Element], expected: str) -> None:
    assert build().serialize() == expected
    assert parse_fragment(expected).inner_html == expected


@pytest.mark.parametrize(
    "child",
    [
        pytest.param("body", id="string-becomes-text-node"),
        pytest.param(Text("body"), id="prebuilt-text-node-is-not-rewrapped"),
    ],
)
def test_text_child_is_a_text_node(child: str | Text) -> None:
    (built,) = E.p(child).children
    assert isinstance(built, Text)
    assert built.data == "body"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(E.div, id="attribute-access"),
        pytest.param(lambda: E("p"), id="call-form"),
    ],
)
def test_builds_a_real_element(build: Callable[[], Node]) -> None:
    assert isinstance(build(), Element)


def test_attribute_access_returns_a_fresh_callable() -> None:
    assert E.div is not E.div  # each access builds its own factory; no shared mutable state


def test_a_separate_maker_builds_the_same_way(maker: ElementMaker) -> None:
    assert maker.span("x").serialize() == "<span>x</span>"


@pytest.mark.parametrize(
    "late",
    [
        pytest.param({"id": "b"}, id="dict"),
        pytest.param(OrderedDict([("id", "b")]), id="dict-subclass"),
        pytest.param(MappingProxyType({"id": "b"}), id="non-dict-mapping"),
    ],
)
def test_non_leading_mapping_is_rejected(late: Mapping[str, str]) -> None:
    with pytest.raises(TypeError, match="must come first"):
        E.div("text", late)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("a=b", id="equals"),
        pytest.param("a b", id="space"),
        pytest.param("a/b", id="slash"),
        pytest.param("a>b", id="greater-than"),
    ],
)
def test_invalid_attribute_name_is_rejected(name: str) -> None:
    expected = rf"attribute name {re.escape(repr(name))} contains an invalid character: {re.escape(repr(name[1]))}"
    with pytest.raises(ValueError, match=expected):
        E.div({name: "x"}, "hi")


@pytest.mark.parametrize(
    ("build", "tag"),
    [
        pytest.param(lambda: E.br("x"), "br", id="br-text-child"),
        pytest.param(lambda: E.img("x", E.span("y")), "img", id="img-mixed-children"),
        pytest.param(lambda: E.hr(E.span("y")), "hr", id="hr-node-child"),
        pytest.param(lambda: E("input", "x"), "input", id="call-form-input"),
        pytest.param(lambda: E.frame(E.b()), "frame", id="frame-serializes-as-void"),
    ],
)
def test_void_element_rejects_children(build: Callable[[], Element], tag: str) -> None:
    with pytest.raises(ValueError, match=rf"void element '{tag}' cannot have children"):
        build()


def test_non_node_child_is_rejected() -> None:
    with pytest.raises(TypeError, match="child must be a node"):
        E.div(42)  # ty: ignore[invalid-argument-type]  # neither a node nor a string


def test_constructor_children_none_builds_empty() -> None:
    # the builder always passes a list, so None reaches the constructor only directly
    assert Element("div", None, None).serialize() == "<div></div>"


def test_constructor_accepts_a_children_tuple() -> None:
    # the builder always passes a list, so a tuple reaches the constructor only directly
    assert Element("div", None, (Text("a"), Text("b"))).serialize() == "<div>ab</div>"


def test_constructor_lowercases_a_custom_tag() -> None:
    assert Element("CUSTOM-TAG").tag == "custom-tag"


def test_constructor_children_must_be_iterable() -> None:
    with pytest.raises(TypeError, match="children must be iterable"):
        Element("div", None, 42)  # ty: ignore[invalid-argument-type]  # children must be an iterable of nodes


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("__deepcopy__", id="deepcopy"),
        pytest.param("__setstate__", id="setstate"),
        pytest.param("__wrapped__", id="wrapped"),
        pytest.param("__", id="bare-double-underscore"),
    ],
)
def test_dunder_lookup_falls_through(maker: ElementMaker, name: str) -> None:
    with pytest.raises(AttributeError):
        getattr(maker, name)


def test_deepcopy_is_not_hijacked_by_getattr(maker: ElementMaker) -> None:
    assert isinstance(copy.deepcopy(maker), ElementMaker)


def test_factory_names_its_tag() -> None:
    assert repr(E.div) == "E.div"


def test_factory_copies_by_its_tag() -> None:
    assert copy.copy(E.section)("x").serialize() == "<section>x</section>"


def test_factory_takes_no_keyword_arguments() -> None:
    with pytest.raises(TypeError, match=r"E\.div\(\) takes no keyword arguments"):
        E.div(id="x")  # ty: ignore[unknown-argument]  # the builder takes attributes as a leading mapping


def test_subclass_attribute_wins_over_a_tag() -> None:
    class Maker(ElementMaker):
        span = "not a tag"

    assert Maker().span == "not a tag"
    assert Maker().b("x").serialize() == "<b>x</b>"


def test_instance_attribute_wins_over_a_tag() -> None:
    class Maker(ElementMaker):
        def __init__(self) -> None:
            self.em = "set"

    assert Maker().em == "set"


def test_non_str_attribute_name_is_a_type_error(maker: ElementMaker) -> None:
    lookup = type(maker).__getattribute__  # getattr() checks the name itself; the slot reaches the C lookup unchecked
    with pytest.raises(TypeError, match=r"attribute name must be string|expected str"):
        lookup(maker, 5)  # ty: ignore[invalid-argument-type]  # the name check is the point


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("_x", id="one-leading-underscore"),
        pytest.param("__x", id="no-trailing-underscores"),
        pytest.param("__x_", id="one-trailing-underscore"),
        pytest.param("x__", id="no-leading-underscores"),
    ],
)
def test_underscore_names_that_are_not_dunders_name_tags(tag: str) -> None:
    assert getattr(E, tag)().tag == tag


def test_invalid_tag_is_rejected() -> None:
    with pytest.raises(ValueError, match="contains an invalid character"):
        E("a b")


def test_doctype_child_is_rejected() -> None:
    (doctype, *_) = parse("<!DOCTYPE html><p>").children
    with pytest.raises(ValueError, match="a doctype can only be a child of a Document"):
        E.p("a", doctype, "b")


def test_empty_string_child_is_an_empty_text_node() -> None:
    (child,) = E.p("").children
    assert isinstance(child, Text)
    assert not child.data


def test_document_child_is_rejected() -> None:
    with pytest.raises(TypeError, match="a Document cannot be inserted as a child"):
        E.div(parse("<p>x</p>"))


def test_bad_child_leaves_earlier_nodes_in_place() -> None:
    host = E.div(E.span("kept"))
    (span,) = host.children
    with pytest.raises(TypeError, match="child must be a node"):
        E.p(span, 42)  # ty: ignore[invalid-argument-type]  # neither a node nor a string
    assert host.serialize() == "<div><span>kept</span></div>"


def test_fragment_child_moves_its_children() -> None:
    fragment = DocumentFragment()
    fragment.append(E.b("1"))
    fragment.append(E.i("2"))
    assert E.p("a", fragment, "c").serialize() == "<p>a<b>1</b><i>2</i>c</p>"


_DATA_ATTRS: Final[str] = " ".join(f'data-{index}="{index}"' for index in range(40))


@pytest.mark.parametrize(
    ("attrs", "expected"),
    [
        pytest.param({"Data-X": "1", "data-x": "2"}, '<p data-x="1"></p>', id="folded-repeat-keeps-first"),
        pytest.param({"class": ["a", "b"]}, '<p class="a b"></p>', id="list-value-joins"),
        pytest.param({"title": "v" * 80}, f'<p title="{"v" * 80}"></p>', id="long-value"),
        pytest.param({"a" * 70: "x"}, f'<p {"a" * 70}="x"></p>', id="long-name"),
        pytest.param({"dätä": "x"}, '<p dätä="x"></p>', id="non-ascii-name"),
        pytest.param(
            {f"data-{index}": str(index) for index in range(40)},
            f"<p {_DATA_ATTRS}></p>",
            id="wide-dict",
        ),
    ],
)
def test_dict_attributes(attrs: Mapping[str, str | list[str]], expected: str) -> None:
    assert E.p(attrs).serialize() == expected
    assert Element("p", attrs).serialize() == expected


@pytest.mark.parametrize(
    ("name", "message"),
    [
        pytest.param("", "attribute name must not be empty", id="empty"),
        pytest.param(f"{'a' * 70} b", "contains an invalid character", id="long-ascii"),
        pytest.param("dä tä", "contains an invalid character", id="non-ascii"),
    ],
)
def test_dict_attribute_name_is_checked(name: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Element("p", {name: "x"})


def test_dict_attribute_name_must_be_a_str() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        Element("p", {5: "x"})  # ty: ignore[invalid-argument-type]  # the name check is the point


@pytest.mark.parametrize("tag", [pytest.param("br", id="void"), pytest.param("frame", id="frame")])
def test_constructor_rejects_children_of_a_void_element(tag: str) -> None:
    with pytest.raises(ValueError, match=f"void element '{tag}' cannot have children"):
        Element(tag, None, [Text("a")])


def test_constructor_rejects_a_lone_child_that_is_not_a_node() -> None:
    with pytest.raises(TypeError, match="child must be a node"):
        Element("p", None, [42])  # ty: ignore[invalid-argument-type]  # neither a node nor a string


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        pytest.param(lambda: Element(tag="p"), "<p></p>", id="keyword-tag"),
        pytest.param(lambda: Element("p", children=[Text("a")]), "<p>a</p>", id="keyword-children"),
        pytest.param(lambda: Element("p", {"id": "x"}, [Text("a")]), '<p id="x">a</p>', id="three-positional"),
        pytest.param(lambda: Element("p", None, [Text("a"), Text("b")]), "<p>ab</p>", id="several-children"),
        pytest.param(lambda: Element("br", None, []), "<br>", id="void-without-children"),
    ],
)
def test_constructor_argument_shapes(call: Callable[[], Element], expected: str) -> None:
    assert call().serialize() == expected


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(Element, id="none"),
        pytest.param(
            lambda: Element("p", None, None, None),  # ty: ignore[too-many-positional-arguments]  # the count is the point
            id="four",
        ),
    ],
)
def test_constructor_rejects_argument_counts(call: Callable[[], Element]) -> None:
    with pytest.raises(TypeError):
        call()


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        pytest.param(
            document,
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body></body></html>',
            id="empty-page-is-a-full-shell",
        ),
        pytest.param(
            lambda: document(title="Report"),
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Report</title></head><body></body></html>',
            id="title-follows-the-charset-meta",
        ),
        pytest.param(
            lambda: document(lang="en"),
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"></head><body></body></html>',
            id="lang-lands-on-html",
        ),
        pytest.param(
            lambda: document(charset="iso-8859-1"),
            '<!DOCTYPE html><html><head><meta charset="iso-8859-1"></head><body></body></html>',
            id="charset-is-configurable",
        ),
        pytest.param(
            lambda: document(charset=None),
            "<!DOCTYPE html><html><head></head><body></body></html>",
            id="none-charset-omits-the-meta",
        ),
        pytest.param(
            lambda: document(title=""),
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title></title></head><body></body></html>',
            id="empty-title-stays-childless",
        ),
        pytest.param(
            lambda: document(body=[E.h1("Sales"), E.p("Up 4%")]),
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><h1>Sales</h1><p>Up 4%</p></body></html>',
            id="body-content-fills-the-body",
        ),
        pytest.param(
            lambda: document(head=[E.link({"rel": "stylesheet", "href": "/x.css"})], title="T"),
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title>T</title>'
            '<link rel="stylesheet" href="/x.css"></head><body></body></html>',
            id="head-content-follows-title",
        ),
        pytest.param(
            lambda: document(body=["hello"]),
            '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>hello</body></html>',
            id="string-body-item-becomes-text",
        ),
        pytest.param(
            lambda: document(title="A & B", body=[E.p("x < y")]),
            '<!DOCTYPE html><html><head><meta charset="utf-8"><title>A &amp; B</title></head>'
            "<body><p>x &lt; y</p></body></html>",
            id="content-is-escaped-per-context",
        ),
    ],
)
def test_document_serialize(build: Callable[[], Document], expected: str) -> None:
    assert build().serialize() == expected


def test_document_returns_a_document() -> None:
    assert isinstance(document(), Document)


def test_document_round_trips_through_parse() -> None:
    page = document(title="Report", lang="en", body=[E.h1("Sales")])
    assert parse(page.serialize()).serialize() == page.serialize()


@pytest.mark.parametrize("field", [pytest.param("title", id="title"), pytest.param("lang", id="lang")])
def test_document_rejects_non_str_metadata(field: str) -> None:
    with pytest.raises(TypeError, match=f"{field} must be a str or None"):
        document(**{field: 42})  # ty: ignore[invalid-argument-type]  # exercises the runtime type guard


def test_document_rejects_non_str_charset() -> None:
    with pytest.raises(TypeError, match="charset must be a str or None"):
        document(charset=42)  # ty: ignore[invalid-argument-type]  # exercises the runtime type guard


@pytest.mark.parametrize(
    "section",
    [pytest.param("head", id="head"), pytest.param("body", id="body")],
)
def test_document_rejects_non_node_section_item(section: str) -> None:
    with pytest.raises(TypeError, match="child must be a node"):
        document(**{section: [42]})  # ty: ignore[invalid-argument-type]  # neither a node nor a string


def test_document_sections_take_any_iterable() -> None:
    page = document(body=(part for part in ("a", E.b("c"))))
    assert page.serialize().endswith("<body>a<b>c</b></body></html>")


def test_document_rejects_a_section_that_is_not_iterable() -> None:
    with pytest.raises(TypeError, match="iterable"):
        document(body=42)  # ty: ignore[invalid-argument-type]  # the argument check is the point
