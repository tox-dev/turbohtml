"""Constructing standalone Text and Comment nodes."""

from __future__ import annotations

import pytest

from turbohtml import Comment, Element, Text


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


# --- Element ---


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
        pytest.param("a<b", id="lt"),
        pytest.param("a=b", id="eq"),
        pytest.param('a"b', id="dquote"),
        pytest.param("a'b", id="squote"),
    ],
)
def test_element_attribute_name_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match=r"empty|invalid character"):
        Element("div", {name: "x"})


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
        def keys(self) -> list[str]:  # noqa: PLR6301  # a mapping protocol method must be an instance method
            return ["x"]

        def __getitem__(self, key: str) -> str:
            raise KeyError(key)

    with pytest.raises(KeyError):
        Element("div", BadMapping())  # ty: ignore[invalid-argument-type]  # a deliberately broken mapping


def test_element_mapping_keys_failure_is_not_masked() -> None:
    class RaisingKeys:
        def keys(self) -> list[str]:  # noqa: PLR6301  # a mapping protocol method must be an instance method
            msg = "boom"
            raise RuntimeError(msg)

    # keys() exists but raises: surface that error rather than mislabeling it a non-mapping TypeError
    with pytest.raises(RuntimeError):
        Element("div", RaisingKeys())  # ty: ignore[invalid-argument-type]  # keys() raises
