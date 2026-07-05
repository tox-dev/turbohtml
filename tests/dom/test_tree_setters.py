"""The live mutable attribute view, the data/text setters, and the HTML content setters."""

from __future__ import annotations

import pytest

from turbohtml import Comment, Element, Namespace, Text, parse


def _div(markup: str = '<div id="a" class="x y">') -> Element:
    element = parse(markup).find("div")
    assert element is not None
    return element


def _ctx(source: str, selector: str) -> Element:
    element = parse(source).find(selector)
    assert element is not None
    return element


def test_attrs_is_a_live_view() -> None:
    element = _div()
    element.attrs["data-z"] = "1"  # mutating the view rewrites the element
    assert element.html == '<div id="a" class="x y" data-z="1"></div>'


def test_attrs_set_replaces_an_existing_value() -> None:
    element = _div()
    element.attrs["class"] = ["p", "q"]  # a list joins like construction does
    assert element.html == '<div id="a" class="p q"></div>'
    assert element.attrs["class"] == ["p", "q"]


def test_attrs_set_valueless_and_empty() -> None:
    element = parse("<input>").find("input")
    assert element is not None
    element.attrs["disabled"] = None  # None sets an empty (valueless) attribute
    element.attrs["value"] = ""
    assert element.html == '<input disabled="" value="">'
    # a valueless or empty attribute reads back as the empty string, like getAttribute
    assert element.attrs["disabled"] == ""  # noqa: PLC1901  # exactly "", not None
    assert element.attrs["value"] == ""  # noqa: PLC1901  # exactly ""


def test_attrs_set_existing_to_empty() -> None:
    element = _div('<div id="a">')
    element.attrs["id"] = None  # None clears an existing value to empty
    assert element.attrs["id"] == ""  # noqa: PLC1901  # exactly "", not None
    assert element.html == '<div id=""></div>'


def test_attrs_set_lowercases_the_name() -> None:
    element = _div("<div>")
    element.attrs["DATA-X"] = "1"
    assert element.attrs["data-x"] == "1"  # stored lowercased, like the parser


def test_attrs_delete() -> None:
    element = _div()
    del element.attrs["id"]
    assert element.html == '<div class="x y"></div>'


@pytest.mark.parametrize(
    "name",
    # a dynamic name and a known atom hit different lookup tables, both must miss
    [pytest.param("never-seen-name", id="dynamic"), pytest.param("title", id="known-atom")],
)
def test_attrs_delete_missing_name_raises(name: str) -> None:
    with pytest.raises(KeyError):
        del _div().attrs[name]


def test_attrs_set_rejects_non_str_name() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        _div().attrs[5] = "x"  # ty: ignore[invalid-assignment]  # names must be str


def test_attrs_delete_rejects_non_str_name() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        del _div().attrs[5]  # ty: ignore[invalid-argument-type]  # names must be str


@pytest.mark.parametrize(
    "name",
    [pytest.param("", id="empty"), pytest.param("a b", id="space"), pytest.param("a=b", id="eq")],
)
def test_attrs_set_rejects_invalid_name(name: str) -> None:
    with pytest.raises(ValueError, match=r"empty|invalid character"):
        _div().attrs[name] = "x"


def test_attrs_set_rejects_bad_value() -> None:
    with pytest.raises(TypeError, match="attribute value"):
        _div().attrs["x"] = 1  # ty: ignore[invalid-assignment]  # value must be str/list/None


@pytest.mark.parametrize(
    "name",
    # "title" is a known atom that happens to be absent; "" can never be a stored name
    [pytest.param("title", id="known-atom-absent"), pytest.param("", id="empty")],
)
def test_attrs_getitem_missing_raises_keyerror(name: str) -> None:
    with pytest.raises(KeyError):
        _ = _div().attrs[name]


def test_attrs_getitem_rejects_non_str_name() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        _ = _div().attrs[5]  # ty: ignore[invalid-argument-type]  # names must be str


def test_attrs_len_and_iter_keep_source_order() -> None:
    element = _div('<div id="a" class="x" data-z="1">')
    assert len(element.attrs) == 3
    assert list(element.attrs) == ["id", "class", "data-z"]


def test_attrs_get_returns_value_or_default() -> None:
    attrs = _div().attrs
    assert attrs.get("id") == "a"
    assert attrs.get("missing") is None
    assert attrs.get("missing", "d") == "d"
    assert attrs.get(5, "d") == "d"  # a non-str key falls back to the default


def test_attrs_get_needs_a_key() -> None:
    with pytest.raises(TypeError):
        _div().attrs.get()  # ty: ignore[no-matching-overload]  # get requires a key


def test_attrs_contains() -> None:
    attrs = _div().attrs
    assert "id" in attrs
    assert "missing" not in attrs
    assert 5 not in attrs  # a non-str key is never present


def test_attrs_keys_values_items() -> None:
    attrs = _div('<div id="a" class="x y">').attrs
    assert attrs.keys() == ["id", "class"]
    assert attrs.values() == ["a", ["x", "y"]]
    assert attrs.items() == [("id", "a"), ("class", ["x", "y"])]


def test_attrs_repr_reads_like_a_dict() -> None:
    assert repr(_div('<div id="a">').attrs) == "{'id': 'a'}"


def test_attrs_round_trips_through_dict() -> None:
    assert dict(_div('<div id="a" class="x">').attrs) == {"id": "a", "class": ["x"]}


def test_data_setter_replaces_text() -> None:
    text = Text("old")
    text.data = "new & shiny"
    assert text.data == "new & shiny"
    assert text.html == "new &amp; shiny"


def test_data_setter_on_comment() -> None:
    comment = Comment("a")
    comment.data = ""
    assert comment.html == "<!---->"


def test_data_setter_on_a_parsed_text_node() -> None:
    text = next(child for child in _div("<div>hi</div>").children if isinstance(child, Text))
    text.data = "bye"
    assert text.data == "bye"


def test_data_setter_rejects_non_str() -> None:
    with pytest.raises(TypeError, match="data must be a str"):
        Text("x").data = 5  # ty: ignore[invalid-assignment]  # data must be a str


def test_data_cannot_be_deleted() -> None:
    text = Text("x")
    with pytest.raises(TypeError, match="cannot delete data"):
        del text.data  # ty: ignore[invalid-assignment]  # data has no deleter


def test_text_setter_replaces_children() -> None:
    element = parse("<p><b>x</b>y</p>").find("p")
    assert element is not None
    element.text = "Tom & Jerry"
    assert element.html == "<p>Tom &amp; Jerry</p>"
    assert len(element) == 1


def test_text_setter_empty_clears() -> None:
    element = parse("<p><b>x</b>y</p>").find("p")
    assert element is not None
    element.text = ""
    assert element.html == "<p></p>"
    assert len(element) == 0


def test_text_setter_rejects_non_str() -> None:
    element = _div()
    with pytest.raises(TypeError, match="text must be a str"):
        element.text = 5  # ty: ignore[invalid-assignment]  # text must be a str


def test_text_cannot_be_deleted() -> None:
    element = _div()
    with pytest.raises(TypeError, match="cannot delete text"):
        del element.text  # ty: ignore[invalid-assignment]  # text has no deleter


@pytest.mark.parametrize(
    ("source", "selector", "fragment", "expected"),
    [
        pytest.param("<div><b>old</b>text</div>", "div", "<i>new</i>", "<div><i>new</i></div>", id="replaces-children"),
        pytest.param("<div><b>old</b>text</div>", "div", "", "<div></div>", id="empty-clears"),
        pytest.param("<div></div>", "div", "<p>a<p>b", "<div><p>a</p><p>b</p></div>", id="repairs-malformed"),
        pytest.param(
            "<table><tbody></tbody></table>",
            "tbody",
            "<tr><td>cell</td></tr>",
            "<tbody><tr><td>cell</td></tr></tbody>",
            id="table-context",
        ),
    ],
)
def test_set_inner_html_replaces_with_parsed_fragment(source: str, selector: str, fragment: str, expected: str) -> None:
    element = _ctx(source, selector)
    element.set_inner_html(fragment)
    assert element.html == expected


@pytest.mark.parametrize(
    ("fragment", "length"),
    [pytest.param("<i>new</i>", 1, id="one-child"), pytest.param("", 0, id="empty")],
)
def test_set_inner_html_sets_the_child_count(fragment: str, length: int) -> None:
    element = _ctx("<div><b>old</b>text</div>", "div")
    element.set_inner_html(fragment)
    assert len(element) == length


def test_set_inner_html_parses_markup_into_a_subtree() -> None:
    element = _ctx("<div></div>", "div")
    element.set_inner_html("<ul><li>a</li><li>b</li></ul>")
    assert [item.text for item in element.find_all("li")] == ["a", "b"]


def test_set_inner_html_round_trips_escaped_text() -> None:
    element = _ctx("<div></div>", "div")
    element.set_inner_html("a &amp; b &lt; c")
    assert element.text == "a & b < c"


@pytest.mark.parametrize(
    ("source", "selector", "fragment", "child", "namespace"),
    [
        pytest.param("<svg></svg>", "svg", "<rect></rect>", "rect", Namespace.SVG, id="svg"),
        pytest.param("<math></math>", "math", "<mi>x</mi>", "mi", Namespace.MATHML, id="math"),
    ],
)
def test_set_inner_html_parses_in_a_foreign_context(
    source: str, selector: str, fragment: str, child: str, namespace: Namespace
) -> None:
    element = _ctx(source, selector)
    element.set_inner_html(fragment)
    found = element.find(child)
    assert found is not None
    assert found.namespace is namespace


def test_set_inner_html_on_a_constructed_element() -> None:
    element = Element("section")
    element.set_inner_html("<h1>Title</h1>")
    assert element.html == "<section><h1>Title</h1></section>"


@pytest.mark.parametrize(
    ("tag", "fragment", "exception", "match"),
    [
        # the type check rejects a non-str before the parse; an int reaches it via parametrize
        pytest.param("div", 5, TypeError, "html must be a str", id="non-str"),
        # the tag name is the fragment context, which must encode to UTF-8
        pytest.param("\ud800", "<b>x</b>", UnicodeEncodeError, None, id="lone-surrogate-context"),
    ],
)
def test_set_inner_html_rejects(tag: str, fragment: str, exception: type[Exception], match: str | None) -> None:
    with pytest.raises(exception, match=match):
        Element(tag).set_inner_html(fragment)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("Tom & Jerry", "<p>Tom &amp; Jerry</p>", id="escapes-ampersand"),
        pytest.param("", "<p></p>", id="empty-clears"),
    ],
)
def test_set_text_replaces_children(text: str, expected: str) -> None:
    element = _ctx("<p><b>x</b>y</p>", "p")
    element.set_text(text)
    assert element.html == expected


@pytest.mark.parametrize(
    ("text", "length"),
    [pytest.param("Tom & Jerry", 1, id="one-text-node"), pytest.param("", 0, id="empty")],
)
def test_set_text_sets_the_child_count(text: str, length: int) -> None:
    element = _ctx("<p><b>x</b>y</p>", "p")
    element.set_text(text)
    assert len(element) == length


def test_set_text_does_not_parse_markup() -> None:
    element = _ctx("<p></p>", "p")
    element.set_text("<b>not bold</b>")
    assert element.text == "<b>not bold</b>"  # the angle brackets are text, not an element
    assert element.find("b") is None


def test_set_text_matches_the_text_setter() -> None:
    one = _ctx("<p><b>x</b>y</p>", "p")
    other = _ctx("<p><b>x</b>y</p>", "p")
    one.set_text("same")
    other.text = "same"
    assert one.html == other.html


def test_set_text_on_a_constructed_element() -> None:
    element = Element("span")
    element.set_text("hi")
    assert element.html == "<span>hi</span>"


@pytest.mark.parametrize("value", [pytest.param(5, id="int")])
def test_set_text_rejects_non_str(value: str) -> None:
    with pytest.raises(TypeError, match="text must be a str"):
        Element("p").set_text(value)


@pytest.mark.parametrize(
    ("source", "position", "fragment", "expected"),
    [
        pytest.param(
            "<ul><li><span>kept</span></li></ul>",
            "afterbegin",
            "<em>new</em>",
            "<li><em>new</em><span>kept</span></li>",
            id="afterbegin-first",
        ),
        pytest.param(
            "<ul><li></li></ul>", "afterbegin", "<em>new</em>", "<li><em>new</em></li>", id="afterbegin-empty"
        ),
        pytest.param(
            "<ul><li><span>kept</span></li></ul>",
            "beforeend",
            "<em>new</em>",
            "<li><span>kept</span><em>new</em></li>",
            id="beforeend-last",
        ),
        pytest.param(
            "<ul><li>one</li></ul>", "BeforeEnd", "<em>x</em>", "<li>one<em>x</em></li>", id="case-insensitive"
        ),
    ],
)
def test_insert_adjacent_html_on_self(source: str, position: str, fragment: str, expected: str) -> None:
    element = _ctx(source, "li")
    element.insert_adjacent_html(position, fragment)
    assert element.html == expected


@pytest.mark.parametrize(
    ("source", "selector", "position", "fragment", "expected"),
    [
        pytest.param(
            "<ul><li>one</li></ul>",
            "li",
            "beforebegin",
            "<li>zero</li>",
            "<ul><li>zero</li><li>one</li></ul>",
            id="beforebegin",
        ),
        pytest.param(
            "<ul><li>one</li></ul>",
            "li",
            "afterend",
            "<li>two</li>",
            "<ul><li>one</li><li>two</li></ul>",
            id="afterend",
        ),
        pytest.param(
            "<ul><li>one</li></ul>",
            "li",
            "beforebegin",
            "<a></a><b></b>",
            "<ul><a></a><b></b><li>one</li></ul>",
            id="beforebegin-keeps-order",
        ),
        pytest.param(
            "<ul><li>one</li></ul>",
            "li",
            "afterend",
            "<a></a><b></b>",
            "<ul><li>one</li><a></a><b></b></ul>",
            id="afterend-keeps-order",
        ),
        pytest.param(
            "<table><tr><td>a</td></tr></table>",
            "td",
            "afterend",
            "<td>b</td>",
            "<tr><td>a</td><td>b</td></tr>",
            id="parent-context",
        ),
    ],
)
def test_insert_adjacent_html_among_siblings(
    source: str, selector: str, position: str, fragment: str, expected: str
) -> None:
    element = _ctx(source, selector)
    element.insert_adjacent_html(position, fragment)
    parent = element.parent
    assert parent is not None
    assert parent.html == expected


def test_insert_adjacent_html_beforebegin_needs_an_element_parent() -> None:
    with pytest.raises(ValueError, match="need an element parent"):
        Element("div").insert_adjacent_html("beforebegin", "<b>x</b>")


def test_insert_adjacent_html_afterend_on_the_root_needs_an_element_parent() -> None:
    html = parse("<p>x</p>").find("html")
    assert html is not None  # its parent is the document, not an element
    with pytest.raises(ValueError, match="need an element parent"):
        html.insert_adjacent_html("afterend", "<b>x</b>")


@pytest.mark.parametrize(
    ("position", "fragment", "exception", "match"),
    [
        pytest.param("middle", "<b>x</b>", ValueError, "position must be", id="unknown-position"),
        pytest.param(5, "<b>x</b>", TypeError, None, id="non-str-position"),
        pytest.param("beforeend", 5, TypeError, None, id="non-str-html"),
    ],
)
def test_insert_adjacent_html_rejects(
    position: str, fragment: str, exception: type[Exception], match: str | None
) -> None:
    element = _ctx("<ul><li>one</li></ul>", "li")
    with pytest.raises(exception, match=match):
        element.insert_adjacent_html(position, fragment)


def test_insert_adjacent_html_rejects_a_lone_surrogate_context() -> None:
    # 'beforeend' parses in the element's own context, whose tag must encode to UTF-8
    with pytest.raises(UnicodeEncodeError):
        Element("\ud800").insert_adjacent_html("beforeend", "<b>x</b>")
