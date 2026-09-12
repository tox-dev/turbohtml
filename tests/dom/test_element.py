from __future__ import annotations

import gc
import sys
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Final, cast

import pytest
from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS
from typing_extensions import assert_type

from turbohtml import Comment, Document, Element, Namespace, Node, Text, parse, parse_xml
from turbohtml.mutations import MutationObserver

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from bench.timing import Mutating


def first(html: str, tag: str) -> Element:
    element = parse(html).select_one(tag)
    assert element is not None
    return element


@pytest.mark.parametrize(
    ("html", "tag", "name", "expected"),
    [
        pytest.param('<a href="/x">', "a", "href", "/x", id="plain-value"),
        pytest.param('<a HREF="/x">', "a", "href", "/x", id="name-lowercased"),
        pytest.param('<div class="a b c">', "div", "class", "a b c", id="class-stays-raw-string"),
        pytest.param('<a rel="next prefetch">', "a", "rel", "next prefetch", id="token-list-stays-raw"),
        pytest.param("<input disabled>", "input", "disabled", "", id="valueless-is-empty-string"),
        pytest.param('<a href="">', "a", "href", "", id="empty-value"),
    ],
)
def test_attr_returns_raw_value(html: str, tag: str, name: str, expected: str) -> None:
    assert first(html, tag).attr(name) == expected


def test_attr_absent_defaults_to_none() -> None:
    assert first("<a href='/x'>", "a").attr("title") is None


def test_attr_absent_returns_given_default() -> None:
    assert first("<a href='/x'>", "a").attr("title", "fallback") == "fallback"


def test_attr_default_is_keyword() -> None:
    assert first("<a href='/x'>", "a").attr("title", default="kw") == "kw"


def test_attr_present_ignores_default() -> None:
    assert first('<a href="/x">', "a").attr("href", "fallback") == "/x"


def test_attr_name_must_be_str() -> None:
    with pytest.raises(TypeError, match="attribute name must be a str"):
        first('<a href="/x">', "a").attr(123)  # ty: ignore[invalid-argument-type]


def test_attr_requires_a_name() -> None:
    with pytest.raises(TypeError):
        first('<a href="/x">', "a").attr()  # ty: ignore[missing-argument]


def test_attr_getall_over_a_selection_is_a_comprehension() -> None:
    doc = parse('<a href="/x">home</a><a href="/y">about</a>')
    assert [anchor.attr("href") for anchor in doc.select("a")] == ["/x", "/y"]


@pytest.mark.parametrize(
    ("html", "tag", "attr", "expected"),
    [
        pytest.param('<div class="a b">', "div", "class", ["a", "b"], id="class"),
        pytest.param('<link sizes="16x16 32x32">', "link", "sizes", ["16x16", "32x32"], id="sizes"),
        pytest.param('<a rel="next prefetch">', "a", "rel", ["next", "prefetch"], id="rel"),
        pytest.param('<a rev="made up">', "a", "rev", ["made", "up"], id="rev"),
        pytest.param("<table><tr><td headers='h1 h2'>", "td", "headers", ["h1", "h2"], id="headers"),
        pytest.param(
            '<iframe sandbox="allow-forms allow-popups">',
            "iframe",
            "sandbox",
            ["allow-forms", "allow-popups"],
            id="sandbox",
        ),
        pytest.param('<object archive="a.jar b.jar">', "object", "archive", ["a.jar", "b.jar"], id="archive"),
        pytest.param('<div dropzone="copy link">', "div", "dropzone", ["copy", "link"], id="dropzone"),
        pytest.param('<button accesskey="s">', "button", "accesskey", ["s"], id="accesskey"),
        pytest.param(
            '<form accept-charset="utf-8 latin-1">',
            "form",
            "accept-charset",
            ["utf-8", "latin-1"],
            id="accept-charset",
        ),
    ],
)
def test_token_list_attributes_split(
    find: Callable[[str, str], Element], html: str, tag: str, attr: str, expected: list[str]
) -> None:
    assert find(html, tag).attrs[attr] == expected


@pytest.mark.parametrize(
    ("html", "tag", "attr"),
    [
        pytest.param('<div dir="ltr">', "div", "dir", id="len3"),
        pytest.param('<div title="t">', "div", "title", id="len5"),
        pytest.param('<div content="c">', "div", "content", id="len7"),
        pytest.param('<div tabindex="0">', "div", "tabindex", id="len8"),
        pytest.param('<div translate="no">', "div", "translate", id="len9"),
        pytest.param('<div data-foobar123="x">', "div", "data-foobar123", id="len14"),
        pytest.param('<div id="main">', "div", "id", id="default-length"),
    ],
)
def test_non_token_attributes_stay_string(find: Callable[[str, str], Element], html: str, tag: str, attr: str) -> None:
    assert isinstance(find(html, tag).attrs[attr], str)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a b", ["a", "b"], id="single-space"),
        pytest.param("  a   b  ", ["a", "b"], id="surrounding-and-internal"),
        pytest.param("solo", ["solo"], id="single-token"),
        pytest.param("   ", [], id="all-whitespace"),
        pytest.param("a\tb\nc\x0cd", ["a", "b", "c", "d"], id="every-ascii-whitespace"),
    ],
)
def test_class_whitespace_splitting(find: Callable[[str, str], Element], value: str, expected: list[str]) -> None:
    assert find(f'<div class="{value}">', "div").attrs["class"] == expected


@pytest.mark.parametrize(
    ("html", "tag", "attr", "expected"),
    [
        pytest.param("<div class>", "div", "class", [], id="valueless-token-list-name"),
        pytest.param('<div class="">', "div", "class", [], id="empty-value-token-list-name"),
        pytest.param("<input checked>", "input", "checked", "", id="valueless-plain-name"),
    ],
)
def test_valueless_attribute_is_empty(
    find: Callable[[str, str], Element], html: str, tag: str, attr: str, expected: str | list[str]
) -> None:
    # a valueless (or empty) attribute reads as the empty string, or the empty list for a token-list attribute
    assert find(html, tag).attrs[attr] == expected


def test_find_matches_token_attribute_by_whole_string() -> None:
    # find() compares the raw attribute value, so a token-list name is matched whole, not split.
    assert parse('<a rel="next">').find("a", rel="next") is not None
    assert parse('<a rel="next">').find("a", rel="nope") is None


def test_attribute_order_is_source_order(find: Callable[[str, str], Element]) -> None:
    assert list(find('<div id="a" class="x" data-z="1">', "div").attrs) == ["id", "class", "data-z"]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("café", id="latin1-two-byte"),
        pytest.param("x中", id="bmp-three-byte"),
        pytest.param("x😀", id="astral-four-byte"),
    ],
)
def test_non_ascii_attribute_name_round_trips(find: Callable[[str, str], Element], name: str) -> None:
    element = find(f'<div {name}="v">', "div")
    assert element.attrs[name] == "v"
    assert element.html == f'<div {name}="v"></div>'


def test_many_dynamic_attribute_names_grow_the_table(find: Callable[[str, str], Element]) -> None:
    names = [f"data-x{index}" for index in range(20)]
    element = find(f"<div {' '.join(names)}>", "div")
    assert sorted(element.attrs) == sorted(names)
    assert all(element.attrs[name] == "" for name in names)  # ruff:ignore[compare-to-empty-string]  # exactly "", not None


def test_repeated_dynamic_attribute_name_reuses_atom(find: Callable[[str, str], Element]) -> None:
    markup = '<div data-x="1"></div><p data-x="2"></p>'
    assert find(markup, "div").attrs["data-x"] == "1"
    assert find(markup, "p").attrs["data-x"] == "2"


def test_attrs_supports_mapping_protocol(find: Callable[[str, str], Element]) -> None:
    attrs = find('<div class="x y">', "div").attrs
    assert attrs.get("class") == ["x", "y"]
    assert attrs.get("missing") is None
    assert "class" in attrs
    assert "missing" not in attrs


@pytest.mark.parametrize(
    ("html", "tag", "attrs", "markup"),
    [
        pytest.param(
            '<a href="1" href="2" href="3">x</a>',
            "a",
            {"href": "1"},
            '<a href="1">x</a>',
            id="repeated-value-first-wins",
        ),
        pytest.param(
            "<p id=first id=second>z</p>", "p", {"id": "first"}, '<p id="first">z</p>', id="unquoted-first-wins"
        ),
        pytest.param(
            "<span data-x data-x>w</span>", "span", {"data-x": ""}, '<span data-x="">w</span>', id="valueless"
        ),
    ],
)
def test_duplicate_attributes_drop_to_first(
    find: Callable[[str, str], Element], html: str, tag: str, attrs: dict[str, str | None], markup: str
) -> None:
    # WHATWG discards a later duplicate attribute name during tokenization, so the tree keeps
    # only the first occurrence (in storage, not just a view) and serializes a single copy.
    element = find(html, tag)
    assert dict(element.attrs) == attrs
    assert element.html == markup


def test_mathml_definitionurl_attribute_name_is_cased(find: Callable[[str, str], Element]) -> None:
    # WHATWG "adjust MathML attributes": definitionurl -> definitionURL, applied at construction
    # so the cased name lands in the tree and serialization, not only the #document debug format
    math = find("<math definitionURL='x'></math>", "math")
    assert dict(math.attrs) == {"definitionURL": "x"}
    assert math.html == '<math definitionURL="x"></math>'
    assert math.attrs["definitionURL"] == "x"
    assert math.attrs.get("definitionurl") == "x"  # foreign lookup stays case-insensitive
    assert "nope" not in math.attrs  # length mismatch in the foreign scan
    assert "abcdefghijklm" not in math.attrs  # same length as definitionURL, different name


def test_mathml_definitionurl_can_be_set_and_deleted_by_either_case(find: Callable[[str, str], Element]) -> None:
    math = find("<math definitionURL='x'></math>", "math")
    math.attrs["definitionURL"] = "y"  # updates the existing slot, no duplicate
    assert math.html == '<math definitionURL="y"></math>'
    del math.attrs["definitionurl"]
    assert math.html == "<math></math>"


def test_svg_definitionurl_attribute_stays_lowercase(find: Callable[[str, str], Element]) -> None:
    # definitionurl is only in the MathML adjust table, so on an SVG element it stays as written
    assert dict(find("<svg definitionURL='x'></svg>", "svg").attrs) == {"definitionurl": "x"}


def test_svg_attributes_are_cased(find: Callable[[str, str], Element]) -> None:
    # WHATWG "adjust SVG attributes": viewbox -> viewBox etc., applied at construction so the
    # cased name lands in the tree and serialization, not only the #document debug format
    svg = find("<svg viewBox='0' attributeName='y'></svg>", "svg")
    assert dict(svg.attrs) == {"viewBox": "0", "attributeName": "y"}
    assert svg.html == '<svg viewBox="0" attributeName="y"></svg>'
    assert svg.attrs["viewBox"] == "0"  # exact match
    assert svg.attrs.get("viewbox") == "0"  # foreign lookup stays case-insensitive
    assert "nope" not in svg.attrs  # length mismatch in the foreign scan
    assert "abcdefg" not in svg.attrs  # same length as viewBox, different name
    assert "class" not in svg.attrs  # interned atom, absent, falls through the foreign scan


def test_svg_attribute_can_be_set_and_deleted_by_either_case(find: Callable[[str, str], Element]) -> None:
    svg = find("<svg viewBox='0'></svg>", "svg")
    svg.attrs["viewbox"] = "9"  # updates the existing cased slot, no duplicate
    assert svg.html == '<svg viewBox="9"></svg>'
    del svg.attrs["viewbox"]
    assert svg.html == "<svg></svg>"


@pytest.mark.parametrize(
    ("html", "name", "expected"),
    [
        pytest.param('<div class="a b c">', "b", True, id="present-middle-token"),
        pytest.param('<div class="a b c">', "a", True, id="present-first-token"),
        pytest.param('<div class="a b c">', "c", True, id="present-last-token"),
        pytest.param('<div class="a b c">', "d", False, id="absent-same-length"),
        pytest.param('<div class="a b c">', "ab", False, id="absent-longer"),
        pytest.param('<div class="abc">', "ab", False, id="absent-shorter"),
        pytest.param("<div>", "a", False, id="no-class-attribute"),
        pytest.param("<div class>", "a", False, id="valueless-class"),
        pytest.param('<div class="">', "a", False, id="empty-class"),
        pytest.param('<div class="  a   b  ">', "a", True, id="ignores-surrounding-whitespace"),
        pytest.param('<div class="a b">', "", False, id="empty-name-never-matches"),
        pytest.param('<div class="a b">', "a b", False, id="whitespace-name-never-matches"),
    ],
)
def test_has_class(find: Callable[[str, str], Element], html: str, name: str, *, expected: bool) -> None:
    assert find(html, "div").has_class(name) is expected


@pytest.mark.parametrize(
    ("html", "ops", "expected"),
    [
        pytest.param("<div>", [("add_class", "active")], ["active"], id="add-to-bare-element"),
        pytest.param("<div class>", [("add_class", "active")], ["active"], id="add-to-valueless-class"),
        pytest.param('<div class="a b">', [("add_class", "c")], ["a", "b", "c"], id="add-appends-keeping-order"),
        pytest.param("<div>", [("add_class", "a"), ("add_class", "b")], ["a", "b"], id="add-chains-returning-self"),
        pytest.param('<div class="a b c">', [("remove_class", "b")], ["a", "c"], id="remove-present-token"),
        pytest.param('<div class="a b a c a">', [("remove_class", "a")], ["b", "c"], id="remove-every-occurrence"),
        pytest.param('<div class="a b">', [("remove_class", "a"), ("remove_class", "b")], [], id="remove-chains-empty"),
        pytest.param('<div class="a">', [("toggle_class", "b")], ["a", "b"], id="toggle-adds-when-absent"),
        pytest.param('<div class="a b">', [("toggle_class", "b")], ["a"], id="toggle-removes-when-present"),
        pytest.param("<div>", [("toggle_class", "open")], ["open"], id="toggle-on-bare-element-adds"),
        pytest.param(
            '<div class="a b">', [("toggle_class", "b"), ("toggle_class", "b")], ["a", "b"], id="toggle-twice-restores"
        ),
    ],
)
def test_class_mutation_result(
    find: Callable[[str, str], Element], html: str, ops: list[tuple[str, str]], expected: list[str]
) -> None:
    element = find(html, "div")
    result = element
    for op, name in ops:
        result = getattr(result, op)(name)  # each mutator returns the element, so the calls chain
    assert result is element
    assert element.attrs["class"] == expected


@pytest.mark.parametrize(
    ("html", "op", "name", "expected"),
    [
        # an existing or absent token is a no-op, so the raw value keeps its original whitespace
        pytest.param('<div class="a  b">', "add_class", "a", "a  b", id="add-existing-keeps-raw-value"),
        pytest.param('<div class="a  b">', "remove_class", "c", "a  b", id="remove-absent-keeps-raw-value"),
        # an actual change rewrites the value with single-space separators, collapsing redundant whitespace
        pytest.param('<div class="  a   b  ">', "add_class", "c", "a b c", id="add-collapses-whitespace-on-write"),
    ],
)
def test_class_mutation_raw_value(
    find: Callable[[str, str], Element], html: str, op: str, name: str, expected: str
) -> None:
    assert getattr(find(html, "div"), op)(name).attr("class") == expected


def test_remove_last_token_leaves_empty_class(find: Callable[[str, str], Element]) -> None:
    element = find('<div class="only">', "div")
    element.remove_class("only")
    assert "class" in element.attrs
    assert element.attrs["class"] == []
    assert element.html == '<div class=""></div>'


def test_remove_on_element_without_class_is_a_noop(find: Callable[[str, str], Element]) -> None:
    element = find("<div>", "div")
    element.remove_class("a")
    assert "class" not in element.attrs


@pytest.mark.parametrize("op", ["add_class", "remove_class", "toggle_class"])
@pytest.mark.parametrize(
    ("name", "match"),
    [
        pytest.param("", "class name must not be empty", id="empty"),
        pytest.param("a b", "class name must not contain whitespace", id="whitespace"),
    ],
)
def test_class_mutation_rejects_invalid_name(
    find: Callable[[str, str], Element], op: str, name: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        getattr(find("<div>", "div"), op)(name)


@pytest.mark.parametrize("op", ["has_class", "add_class", "remove_class", "toggle_class"])
def test_class_method_rejects_non_str_name(find: Callable[[str, str], Element], op: str) -> None:
    with pytest.raises(TypeError, match="class name must be a str"):
        getattr(find('<div class="a">', "div"), op)(1)


def _control(markup: str, selector: str) -> Element:
    element = parse(markup).find(selector)
    assert element is not None
    return element


def test_checked_reads_the_attribute() -> None:
    assert _control("<input type=checkbox checked>", "input").checked is True


def test_unchecked_reads_false() -> None:
    assert _control("<input type=checkbox>", "input").checked is False


def test_non_control_reads_false() -> None:
    assert _control("<div></div>", "div").checked is False


def test_set_checked_true_adds_the_attribute() -> None:
    field = _control("<input type=checkbox>", "input")
    field.checked = True
    assert field.checked is True
    assert field.html == '<input type="checkbox" checked="">'


def test_set_checked_false_removes_the_attribute() -> None:
    field = _control("<input type=checkbox checked>", "input")
    field.checked = False
    assert field.checked is False
    assert field.html == '<input type="checkbox">'


def test_set_checked_accepts_truthy_values() -> None:
    field: Any = _control("<input type=radio>", "input")
    field.checked = 1
    assert field.checked is True


def test_set_checked_propagates_a_failing_bool() -> None:
    class Boom:
        def __bool__(self) -> bool:
            msg = "nope"
            raise ValueError(msg)

    field: Any = _control("<input type=checkbox>", "input")
    with pytest.raises(ValueError, match="nope"):
        field.checked = Boom()


def test_set_checked_on_a_text_input_raises() -> None:
    field = _control("<input type=text>", "input")
    with pytest.raises(TypeError, match="checkbox or radio"):
        field.checked = True


def test_set_checked_on_a_typeless_input_raises() -> None:
    field = _control("<input>", "input")
    with pytest.raises(TypeError, match="checkbox or radio"):
        field.checked = True


def test_set_checked_on_a_valueless_type_input_raises() -> None:
    field = _control("<input type>", "input")
    with pytest.raises(TypeError, match="checkbox or radio"):
        field.checked = True


def test_set_checked_on_a_non_input_raises() -> None:
    element = _control("<div></div>", "div")
    with pytest.raises(TypeError, match="checkbox or radio"):
        element.checked = True


def test_delete_checked_raises() -> None:
    field: Any = _control("<input type=checkbox checked>", "input")
    with pytest.raises(TypeError, match="cannot delete checked"):
        del field.checked


def test_checking_a_radio_clears_the_group() -> None:
    form = _control(
        "<form><input type=radio name=size value=s checked>"
        "<input type=radio name=size value=m>"
        "<input type=radio name=size value=l></form>",
        "form",
    )
    radios = form.find_all("input")
    radios[1].checked = True
    assert [radio.checked for radio in radios] == [False, True, False]


def test_checking_a_radio_leaves_other_names_alone() -> None:
    form = _control(
        "<form><input type=radio name=a value=1 checked>"
        "<input type=radio name=b value=2 checked>"
        "<input type=radio name=size value=3 checked></form>",
        "form",
    )
    radios = form.find_all("input")
    radios[0].checked = True  # name "a" matches neither the same-length "b" nor the longer "size"
    assert [radio.checked for radio in radios] == [True, True, True]


def test_checking_a_nameless_radio_clears_nothing() -> None:
    form = _control(
        "<form><input type=radio value=1 checked><input type=radio value=2></form>",
        "form",
    )
    radios = form.find_all("input")
    radios[1].checked = True
    assert [radio.checked for radio in radios] == [True, True]


def test_checking_a_valueless_named_radio_clears_nothing() -> None:
    form = _control(
        "<form><input type=radio name value=1 checked><input type=radio name value=2></form>",
        "form",
    )
    radios = form.find_all("input")
    radios[1].checked = True
    assert [radio.checked for radio in radios] == [True, True]


def test_checking_a_blank_named_radio_clears_nothing() -> None:
    form = parse("<form></form>").find("form")
    assert form is not None
    first = Element("input", {"type": "radio", "name": "", "value": "1", "checked": None})
    second = Element("input", {"type": "radio", "name": "", "value": "2"})
    form.extend([first, second])  # an empty string name is a real but blank name, distinct from a valueless one
    second.checked = True
    assert [first.checked, second.checked] == [True, True]


def test_radio_group_skips_unrelated_controls_in_scope() -> None:
    form = _control(
        "<form>"
        "<input type=radio name=s value=a checked>"  # target
        "<input type=radio name=s value=b checked>"  # same name -> cleared
        "<input type=radio name=x value=c checked>"  # same-length name -> kept
        "<input type=radio name=size value=d checked>"  # different-length name -> kept
        "<input type=radio value=e checked>"  # no name -> kept
        "<input type=radio name value=f checked>"  # valueless name -> kept
        "<input type=checkbox name=s value=g checked>"  # not a radio -> kept
        "<input type=text name=s value=h>"  # not a radio -> untouched
        "<label>pick</label>"  # not an input -> untouched
        "</form>",
        "form",
    )
    radios = form.find_all("input")
    radios[0].checked = True
    assert [field.checked for field in radios] == [True, False, True, True, True, True, True, False]


def test_radio_group_falls_back_to_the_document() -> None:
    document = parse("<input type=radio name=g value=1 checked><input type=radio name=g value=2>")
    radios = document.find_all("input")
    radios[1].checked = True
    assert [radio.checked for radio in radios] == [False, True]


def test_radio_group_is_scoped_to_the_owning_form() -> None:
    document = parse(
        "<input type=radio name=g value=outside checked><form><input type=radio name=g value=inside></form>"
    )
    form = document.find("form")
    assert form is not None
    inside = form.find("input")
    assert inside is not None
    inside.checked = True
    outside = document.find_all("input")[0]
    assert outside.checked is True
    assert inside.checked is True


def test_unchecking_a_radio_leaves_the_group() -> None:
    form = _control(
        "<form><input type=radio name=s value=1 checked><input type=radio name=s value=2 checked></form>",
        "form",
    )
    radios = form.find_all("input")
    radios[0].checked = False
    assert [radio.checked for radio in radios] == [False, True]


def _tree_field_value_control(markup: str, selector: str) -> Element:
    element = parse(markup).find(selector)
    assert element is not None
    return element


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<input name=q value=hello>", "hello", id="text-value"),
        pytest.param("<input name=q>", "", id="text-missing-value"),
        pytest.param("<input type=hidden value=tok>", "tok", id="hidden"),
        pytest.param("<input type=checkbox>", "on", id="checkbox-default-on"),
        pytest.param("<input type=checkbox value=yes>", "yes", id="checkbox-explicit"),
        pytest.param("<input type=radio>", "on", id="radio-default-on"),
        pytest.param("<input type=Checkbox value=x>", "x", id="checkbox-case-insensitive"),
        pytest.param("<input type=checkbox value>", "", id="checkbox-empty-valueless"),
    ],
)
def test_input_field_value(markup: str, expected: str) -> None:
    assert _tree_field_value_control(markup, "input").field_value == expected


def test_textarea_field_value_is_its_text() -> None:
    assert _tree_field_value_control("<textarea> hi there </textarea>", "textarea").field_value == " hi there "


def test_button_field_value() -> None:
    assert _tree_field_value_control("<button value=go>Go</button>", "button").field_value == "go"


def test_button_field_value_defaults_empty() -> None:
    assert _tree_field_value_control("<button>Go</button>", "button").field_value == ""  # ruff:ignore[compare-to-empty-string]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<option value=r>Red</option>", "r", id="value-attribute"),
        pytest.param("<option> Red </option>", "Red", id="text-stripped"),
        pytest.param("<option value>Red</option>", "", id="empty-valueless"),
    ],
)
def test_option_field_value(markup: str, expected: str) -> None:
    assert _tree_field_value_control(markup, "option").field_value == expected


def test_single_select_returns_the_selected_option() -> None:
    select = _tree_field_value_control("<select><option value=r>Red<option value=g selected>Green</select>", "select")
    assert select.field_value == "g"


def test_single_select_defaults_to_the_first_option() -> None:
    select = _tree_field_value_control("<select><option value=r>Red<option value=g>Green</select>", "select")
    assert select.field_value == "r"


def test_single_select_last_selected_wins() -> None:
    select = _tree_field_value_control(
        "<select><option value=r selected>Red<option value=g selected>Green</select>", "select"
    )
    assert select.field_value == "g"


def test_empty_single_select_is_none() -> None:
    assert _tree_field_value_control("<select></select>", "select").field_value is None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param("1", "a", id="display-size-1-defaults-to-first"),
        pytest.param("4", None, id="display-size-4-has-no-default"),
    ],
)
def test_single_select_default_only_when_display_size_is_one(size: str, expected: str | None) -> None:
    select = _tree_field_value_control(f"<select size={size}><option value=a><option value=b></select>", "select")
    assert select.field_value == expected


def test_single_select_default_skips_an_optgroup_disabled_option() -> None:
    select = _tree_field_value_control(
        "<select><optgroup disabled><option value=a>A</optgroup><option value=b>B</select>", "select"
    )
    assert select.field_value == "b"


def test_single_select_keeps_a_disabled_selected_option() -> None:
    select = _tree_field_value_control("<select><option value=a disabled selected><option value=b></select>", "select")
    assert select.field_value == "a"


def test_multiple_select_returns_the_selected_list() -> None:
    select = _tree_field_value_control(
        "<select multiple><option value=a selected>A<option value=b><option value=c selected>C</select>", "select"
    )
    assert select.field_value == ["a", "c"]


def test_multiple_select_with_no_selection_is_empty() -> None:
    select = _tree_field_value_control("<select multiple><option value=a>A<option value=b>B</select>", "select")
    assert select.field_value == []


def test_non_control_field_value_is_none() -> None:
    assert _tree_field_value_control("<div>hi</div>", "div").field_value is None


def test_set_input_value_writes_the_attribute() -> None:
    field = _tree_field_value_control("<input name=q value=old>", "input")
    field.field_value = "new"
    assert field.field_value == "new"
    assert field.html == '<input name="q" value="new">'


def test_set_input_value_to_none_removes_the_attribute() -> None:
    field = _tree_field_value_control("<input name=q value=old>", "input")
    field.field_value = None
    assert field.html == '<input name="q">'


def test_delete_input_value_removes_the_attribute() -> None:
    field: Any = _tree_field_value_control("<input name=q value=old>", "input")
    del field.field_value
    assert field.html == '<input name="q">'


def test_set_input_value_rejects_non_str() -> None:
    field: Any = _tree_field_value_control("<input name=q>", "input")
    with pytest.raises(TypeError, match="must be a str or None"):
        field.field_value = 5


def test_set_textarea_value_replaces_text() -> None:
    field = _tree_field_value_control("<textarea>old</textarea>", "textarea")
    field.field_value = "new"
    assert field.field_value == "new"
    assert field.html == "<textarea>new</textarea>"


def test_set_textarea_value_to_none_clears_it() -> None:
    field = _tree_field_value_control("<textarea>old</textarea>", "textarea")
    field.field_value = None
    assert field.field_value == ""  # ruff:ignore[compare-to-empty-string]
    assert field.html == "<textarea></textarea>"


def test_set_textarea_value_rejects_a_list() -> None:
    field = _tree_field_value_control("<textarea></textarea>", "textarea")
    with pytest.raises(TypeError, match="must be a str or None"):
        field.field_value = ["x"]


def test_set_single_select_selects_the_match() -> None:
    select = _tree_field_value_control("<select><option value=r selected>Red<option value=g>Green</select>", "select")
    select.field_value = "g"
    assert select.field_value == "g"
    assert select.html == '<select><option value="r">Red</option><option value="g" selected="">Green</option></select>'


def test_set_single_select_only_selects_the_first_match() -> None:
    select = _tree_field_value_control("<select><option value=x>One<option value=x>Two</select>", "select")
    select.field_value = "x"
    options = select.find_all("option")
    assert [option.checked for option in options] == [False, False]
    assert "selected" in options[0].attrs
    assert "selected" not in options[1].attrs


def test_set_single_select_to_none_clears_all() -> None:
    select = _tree_field_value_control("<select><option value=r selected>Red<option value=g>Green</select>", "select")
    select.field_value = None
    assert "selected" not in select.find_all("option")[0].attrs


def test_set_single_select_rejects_a_list() -> None:
    select = _tree_field_value_control("<select><option value=r>Red</select>", "select")
    with pytest.raises(TypeError, match="single select must be a str or None"):
        select.field_value = ["r"]


def test_set_multiple_select_selects_each_match() -> None:
    select = _tree_field_value_control(
        "<select multiple><option value=a>A<option value=b>B<option value=c>C</select>", "select"
    )
    select.field_value = ["a", "c"]
    assert select.field_value == ["a", "c"]


def test_set_multiple_select_deselects_the_rest() -> None:
    select = _tree_field_value_control(
        "<select multiple><option value=a selected>A<option value=b selected>B</select>", "select"
    )
    select.field_value = ["a"]
    assert select.field_value == ["a"]


def test_set_multiple_select_rejects_a_bare_str() -> None:
    select = _tree_field_value_control("<select multiple><option value=a>A</select>", "select")
    with pytest.raises(TypeError, match="multiple select must be a list of str or None"):
        select.field_value = "a"


def test_set_multiple_select_rejects_non_str_member() -> None:
    select: Any = _tree_field_value_control("<select multiple><option value=a>A</select>", "select")
    with pytest.raises(TypeError, match="multiple select must be a list of str or None"):
        select.field_value = [1]


def test_set_field_value_on_non_control_raises() -> None:
    element = _tree_field_value_control("<div></div>", "div")
    with pytest.raises(TypeError, match="can only be set on a form control"):
        element.field_value = "x"


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<input type=che value=x>", "x", id="type-shorter-than-keyword"),
        pytest.param("<input type=checkboxx value=x>", "x", id="type-longer-than-keyword"),
        pytest.param("<input type value=x>", "x", id="valueless-type"),
    ],
)
def test_unusual_input_type_is_text_like(markup: str, expected: str) -> None:
    assert _tree_field_value_control(markup, "input").field_value == expected


def test_option_value_strips_whitespace_only_text() -> None:
    select = _tree_field_value_control("<select><option selected>   </option></select>", "select")
    assert select.field_value == ""  # ruff:ignore[compare-to-empty-string]


def test_select_skips_an_optgroup_wrapper() -> None:
    select = _tree_field_value_control(
        "<select><optgroup label=g><option value=a selected>A</optgroup></select>", "select"
    )
    assert select.field_value == "a"


def test_delete_textarea_value_clears_it() -> None:
    field: Any = _tree_field_value_control("<textarea>old</textarea>", "textarea")
    del field.field_value
    assert field.html == "<textarea></textarea>"


def test_delete_single_select_value_clears_all() -> None:
    select: Any = _tree_field_value_control(
        "<select><option value=r selected>Red<option value=g>Green</select>", "select"
    )
    del select.field_value
    assert "selected" not in select.find_all("option")[0].attrs


def _form(markup: str) -> Element:
    form = parse(markup).find("form")
    assert form is not None
    return form


def test_a_blank_string_named_control_is_skipped() -> None:
    form = _form("<form></form>")
    form.append(Element("input", {"name": "", "value": "y"}))  # an empty-string name is still no name
    assert form.form_data() == []


def test_text_and_hidden_inputs_are_submitted() -> None:
    form = _form("<form><input name=q value=hello><input name=tok type=hidden value=abc></form>")
    assert form.form_data() == [("q", "hello"), ("tok", "abc")]


def test_a_missing_value_submits_empty() -> None:
    assert _form("<form><input name=q></form>").form_data() == [("q", "")]


def test_unnamed_controls_are_skipped() -> None:
    assert _form("<form><input value=x><input name='' value=y><input name value=z></form>").form_data() == []


def test_controls_nested_in_plain_wrappers_are_found() -> None:
    form = _form("<form><div><p><input name=q value=ok></p></div></form>")
    assert form.form_data() == [("q", "ok")]


def test_checkbox_only_submits_when_checked() -> None:
    form = _form("<form><input name=a type=checkbox value=1 checked><input name=b type=checkbox value=2></form>")
    assert form.form_data() == [("a", "1")]


def test_checkbox_without_value_submits_on() -> None:
    assert _form("<form><input name=a type=checkbox checked></form>").form_data() == [("a", "on")]


def test_radio_only_submits_the_checked_one() -> None:
    form = _form(
        "<form><input name=s type=radio value=s><input name=s type=radio value=m checked>"
        "<input name=s type=radio value=l></form>"
    )
    assert form.form_data() == [("s", "m")]


@pytest.mark.parametrize("button_type", ["submit", "reset", "button", "image", "file"])
def test_buttons_and_files_are_skipped(button_type: str) -> None:
    form = _form(f"<form><input name=b type={button_type} value=x><input name=q value=ok></form>")
    assert form.form_data() == [("q", "ok")]


def test_button_element_is_skipped() -> None:
    assert _form("<form><button name=b value=x>Go</button></form>").form_data() == []


def test_textarea_submits_its_text() -> None:
    assert _form("<form><textarea name=bio>hi there</textarea></form>").form_data() == [("bio", "hi there")]


def test_disabled_controls_are_skipped() -> None:
    assert _form("<form><input name=a value=1 disabled><input name=b value=2></form>").form_data() == [("b", "2")]


def test_a_disabled_fieldset_skips_its_controls() -> None:
    form = _form("<form><fieldset disabled><input name=a value=1></fieldset><input name=b value=2></form>")
    assert form.form_data() == [("b", "2")]


def test_a_disabled_fieldset_keeps_controls_inside_its_first_legend() -> None:
    form = _form(
        "<form><fieldset disabled><legend><input name=a value=1></legend><input name=b value=2></fieldset></form>"
    )
    assert form.form_data() == [("a", "1")]


def test_a_disabled_fieldset_skips_controls_inside_later_legends() -> None:
    form = _form(
        "<form><fieldset disabled><legend>x</legend><legend><input name=a value=1></legend>"
        "<input name=b value=2></fieldset></form>"
    )
    assert form.form_data() == []


def test_an_enabled_fieldset_keeps_its_controls() -> None:
    form = _form("<form><fieldset><input name=a value=1></fieldset></form>")
    assert form.form_data() == [("a", "1")]


def test_single_select_submits_the_selected_option() -> None:
    form = _form("<form><select name=c><option value=r>Red<option value=g selected>Green</select></form>")
    assert form.form_data() == [("c", "g")]


def test_tree_form_data_single_select_defaults_to_the_first_option() -> None:
    form = _form("<form><select name=c><option value=r>Red<option value=g>Green</select></form>")
    assert form.form_data() == [("c", "r")]


def test_single_select_skips_a_disabled_default() -> None:
    form = _form("<form><select name=c><option value=r disabled>Red<option value=g>Green</select></form>")
    assert form.form_data() == [("c", "g")]


def test_an_empty_select_submits_nothing() -> None:
    assert _form("<form><select name=c></select></form>").form_data() == []


def test_multiple_select_submits_each_selected_option() -> None:
    form = _form(
        "<form><select name=t multiple><option value=a selected>A<option value=b>"
        "<option value=c selected>C</select></form>"
    )
    assert form.form_data() == [("t", "a"), ("t", "c")]


def test_multiple_select_skips_disabled_options() -> None:
    form = _form(
        "<form><select name=t multiple><option value=a selected disabled>A<option value=b selected>B</select></form>"
    )
    assert form.form_data() == [("t", "b")]


def test_tree_form_data_single_select_default_skips_an_optgroup_disabled_option() -> None:
    form = _form(
        "<form><select name=s><optgroup disabled><option value=a>A</optgroup><option value=b>B</select></form>"
    )
    assert form.form_data() == [("s", "b")]


def test_multiple_select_skips_optgroup_disabled_options() -> None:
    form = _form(
        "<form><select name=t multiple><optgroup disabled><option value=a selected>A</optgroup>"
        "<option value=b selected>B</select></form>"
    )
    assert form.form_data() == [("t", "b")]


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        pytest.param("size=1", [("s", "a")], id="display-size-1"),
        pytest.param("size=4", [], id="display-size-4"),
        pytest.param('size=" 4"', [], id="leading-whitespace-parsed"),
        pytest.param('size="  "', [("s", "a")], id="all-whitespace-falls-to-default"),
        pytest.param('size="x"', [("s", "a")], id="non-numeric-falls-to-default"),
        pytest.param('size="-1"', [("s", "a")], id="leading-non-digit-falls-to-default"),
        pytest.param('size="99999"', [], id="huge-size-clamped-not-one"),
        pytest.param("size", [("s", "a")], id="valueless-falls-to-default"),
    ],
)
def test_select_default_first_only_when_display_size_is_one(attr: str, expected: list[tuple[str, str]]) -> None:
    form = _form(f"<form><select name=s {attr}><option value=a><option value=b></select></form>")
    assert form.form_data() == expected


def test_select_with_only_a_disabled_selected_option_submits_nothing() -> None:
    form = _form("<form><select name=s><option value=a disabled selected><option value=b></select></form>")
    assert form.form_data() == []


def test_form_data_ignores_controls_inside_a_template() -> None:
    form = _form("<form><template><input name=t value=1></template><input name=a value=2></form>")
    assert form.form_data() == [("a", "2")]


def test_form_data_ignores_controls_nested_deep_inside_a_template() -> None:
    form = _form("<form><template><div><input name=t value=1></div></template><input name=a value=2></form>")
    assert form.form_data() == [("a", "2")]


def test_form_data_skips_a_trailing_template() -> None:
    form = _form("<form><input name=a value=2><template><input name=t value=1></template></form>")
    assert form.form_data() == [("a", "2")]


def test_option_value_falls_back_to_text() -> None:
    form = _form("<form><select name=c><option selected> Green </select></form>")
    assert form.form_data() == [("c", "Green")]


def test_pairs_keep_document_order() -> None:
    form = _form("<form><input name=z value=1><input name=a value=2></form>")
    assert form.form_data() == [("z", "1"), ("a", "2")]


def test_form_data_on_a_non_form_raises() -> None:
    element = parse("<div></div>").find("div")
    assert element is not None
    with pytest.raises(TypeError, match="can only be called on a form element"):
        element.form_data()


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
    assert element.attrs["disabled"] == ""  # ruff:ignore[compare-to-empty-string]  # exactly "", not None
    assert element.attrs["value"] == ""  # ruff:ignore[compare-to-empty-string]  # exactly ""


def test_attrs_set_existing_to_empty() -> None:
    element = _div('<div id="a">')
    element.attrs["id"] = None  # None clears an existing value to empty
    assert element.attrs["id"] == ""  # ruff:ignore[compare-to-empty-string]  # exactly "", not None
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


def _found(root: Element | Document, selector: str) -> Element:
    """First match of selector in an already-edited tree, asserted present."""
    node = root.find(selector)
    assert node is not None, selector
    return node


def test_append_constructed_child() -> None:
    div = Element("div")
    div.append(Element("span"))
    div.append(Text("hi"))
    assert div.html == "<div><span></span>hi</div>"


def test_append_moves_within_same_tree() -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    ul = _found(doc, "ul")
    ul.append(_found(doc, "li"))  # a single-parent node relocates rather than duplicating
    assert ul.html == "<ul><li>b</li><li>a</li></ul>"


def test_append_adopts_subtree_from_another_tree() -> None:
    doc = parse('<section id=s class="box wide"><b>x</b><i>y</i></section>')
    box = Element("div")
    box.append(_found(doc, "section"))
    assert box.html == '<div><section id="s" class="box wide"><b>x</b><i>y</i></section></div>'
    assert doc.find("section") is None  # the source tree no longer holds it


def test_append_preserves_node_hash() -> None:
    node = _found(parse("<span>x</span>"), "span")
    held = {node}
    Element("div").append(node)
    second_destination = Element("main")
    second_destination.append(node)
    assert node in held
    assert _found(second_destination, "span") in held


def test_append_preserves_hashes_for_multiple_adoptions() -> None:
    nodes = [Element(f"x-{index}") for index in range(9)]
    held = set(nodes)
    destination = Element("div")
    for node in nodes:
        destination.append(node)
    assert all(node in held for node in destination.children)


def test_append_adopts_every_attribute_shape() -> None:
    # data-custom-xyz is no known atom, so adoption must re-intern its name across
    # trees; the valueless and empty values (both "") must survive the copy too
    source = Element("x", {"data-custom-xyz": "1", "disabled": None, "value": ""})
    box = Element("div")
    box.append(source)
    held = _found(box, "x")
    assert held.attrs["data-custom-xyz"] == "1"
    assert held.attrs["disabled"] == ""  # ruff:ignore[compare-to-empty-string]  # exactly "" (valueless reads empty), not None
    assert held.attrs["value"] == ""  # ruff:ignore[compare-to-empty-string]  # exactly ""


def test_append_adopts_an_empty_data_node() -> None:
    box = Element("div")
    box.append(Comment(""))  # an empty-data node copies with no character buffer
    assert box.html == "<div><!----></div>"


def test_append_rejects_non_node() -> None:
    with pytest.raises(TypeError, match="must be a node"):
        Element("p").append("x")  # ty: ignore[invalid-argument-type]  # child must be a node


def test_append_rejects_document() -> None:
    with pytest.raises(TypeError, match="Document cannot be inserted"):
        Element("p").append(parse("<p></p>"))


def test_append_rejects_cycle() -> None:
    parent = Element("p")
    child = Element("q")
    parent.append(child)
    with pytest.raises(ValueError, match="own subtree"):
        child.append(parent)


def test_extend_appends_each_in_order() -> None:
    ul = Element("ul")
    ul.extend([Element("li"), Text("x"), Comment("c")])
    assert ul.html == "<ul><li></li>x<!--c--></ul>"


def test_extend_rejects_non_iterable() -> None:
    with pytest.raises(TypeError):
        Element("ul").extend(5)  # ty: ignore[invalid-argument-type]  # children must be iterable


def test_extend_rejects_non_node_member() -> None:
    with pytest.raises(TypeError, match="must be a node"):
        Element("ul").extend([Element("li"), 1])  # ty: ignore[invalid-argument-type]  # members must be nodes


def test_extend_propagates_iterator_error() -> None:
    def boom() -> Iterator[Element]:
        yield Element("li")
        msg = "stop"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="stop"):
        Element("ul").extend(boom())


@pytest.mark.parametrize(
    "index",
    # like list.insert, index -1 lands before the last child, not after it
    [pytest.param(1, id="positive"), pytest.param(-1, id="negative-counts-from-end")],
)
def test_insert_places_a_child_between_two_siblings(index: int) -> None:
    paragraph = Element("p")
    paragraph.append(Element("a"))
    paragraph.append(Element("c"))
    paragraph.insert(index, Element("b"))
    assert paragraph.html == "<p><a></a><b></b><c></c></p>"


@pytest.mark.parametrize(
    ("index", "text", "expected"),
    [
        pytest.param(-100, "S", "<p>S<a></a></p>", id="negative-clamps-to-start"),
        pytest.param(100, "E", "<p><a></a>E</p>", id="past-end-appends"),
    ],
)
def test_insert_clamps_out_of_range_index(index: int, text: str, expected: str) -> None:
    paragraph = Element("p")
    paragraph.append(Element("a"))
    paragraph.insert(index, Text(text))
    assert paragraph.html == expected


def test_insert_moving_a_node_onto_its_own_slot_appends() -> None:
    doc = parse("<p><a></a><b></b><c></c></p>")
    paragraph = _found(doc, "p")
    paragraph.insert(1, _found(doc, "b"))  # the reference slot is the moved node itself, so it tails
    assert paragraph.html == "<p><a></a><c></c><b></b></p>"


def test_insert_rejects_bad_arguments() -> None:
    with pytest.raises(TypeError):
        Element("p").insert("x", Text("y"))  # ty: ignore[invalid-argument-type]  # index must be an int


def test_insert_rejects_non_node_child() -> None:
    with pytest.raises(TypeError, match="must be a node"):
        Element("p").insert(0, "x")  # ty: ignore[invalid-argument-type]  # child must be a node


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        pytest.param(lambda: _found(parse("<ul><li>a</li><li>b</li></ul>"), "ul"), "<ul></ul>", id="with-children"),
        pytest.param(lambda: Element("div"), "<div></div>", id="already-empty"),
    ],
)
def test_clear_detaches_every_child(make: Callable[[], Element], expected: str) -> None:
    element = make()
    element.clear()
    assert element.html == expected


@pytest.mark.parametrize(
    ("html", "method", "nodes", "expected"),
    [
        pytest.param(
            "<ul><li>a</li></ul>",
            "insert_before",
            [Comment("note")],
            "<ul><!--note--><li>a</li></ul>",
            id="before-single",
        ),
        pytest.param(
            "<ul><li>a</li><li>c</li></ul>",
            "insert_after",
            [Element("b")],
            "<ul><li>a</li><b></b><li>c</li></ul>",
            id="after-middle",
        ),
        pytest.param(
            "<ul><li>a</li></ul>",
            "insert_after",
            [Element("b")],
            "<ul><li>a</li><b></b></ul>",
            id="after-last-appends",
        ),
        pytest.param(
            "<ul><li>b</li></ul>",
            "insert_before",
            [Text("A"), Comment("c")],
            "<ul>A<!--c--><li>b</li></ul>",
            id="before-multiple-keeps-order",
        ),
        pytest.param(
            "<ul><li>a</li></ul>",
            "insert_after",
            [Element("x"), Element("y")],
            "<ul><li>a</li><x></x><y></y></ul>",
            id="after-multiple-keeps-order",
        ),
    ],
)
def test_sibling_insertion_relative_to_first_li(html: str, method: str, nodes: list[Node], expected: str) -> None:
    doc = parse(html)
    getattr(_found(doc, "li"), method)(*nodes)
    assert _found(doc, "ul").html == expected


_SIBLING_METHODS = [
    pytest.param("insert_before", id="insert_before"),
    pytest.param("insert_after", id="insert_after"),
    pytest.param("replace_with", id="replace_with"),
]


@pytest.mark.parametrize("method", _SIBLING_METHODS)
def test_sibling_edit_needs_a_parent(method: str) -> None:
    with pytest.raises(ValueError, match="no parent"):
        getattr(Text("x"), method)(Text("y"))


@pytest.mark.parametrize("method", _SIBLING_METHODS)
def test_sibling_edit_with_self_is_a_noop(method: str) -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    getattr(_found(doc, "li"), method)(_found(doc, "li"))  # editing a node relative to itself changes nothing
    assert _found(doc, "ul").html == "<ul><li>a</li><li>b</li></ul>"


@pytest.mark.parametrize("method", _SIBLING_METHODS)
def test_sibling_edit_rejects_non_node(method: str) -> None:
    li = _found(parse("<ul><li>a</li></ul>"), "li")
    with pytest.raises(TypeError, match="must be a node"):
        getattr(li, method)("x")


@pytest.mark.parametrize(
    ("html", "replacements", "expected"),
    [
        pytest.param("<p><b>x</b></p>", [Text("plain")], "<p>plain</p>", id="single"),
        pytest.param("<p><b>x</b></p>", [Text("1"), Text("2")], "<p>12</p>", id="multiple-in-order"),
        pytest.param("<p><b>x</b>y</p>", [], "<p>y</p>", id="none-just-removes"),
    ],
)
def test_replace_with(html: str, replacements: list[Node], expected: str) -> None:
    doc = parse(html)
    bold = _found(doc, "b")
    bold.replace_with(*replacements)
    assert _found(doc, "p").html == expected
    assert bold.parent is None  # the replaced node is detached, still usable


def test_extract_detaches_and_returns_self() -> None:
    doc = parse("<div><span>s</span></div>")
    span = _found(doc, "span")
    assert assert_type(span.extract(), Element) is span
    assert span.parent is None
    assert _found(doc, "div").html == "<div></div>"


def test_extract_an_extracted_node_reinserts() -> None:
    span = _found(parse("<div><span>s</span></div>"), "span")
    span.extract()
    box = Element("section")
    box.append(span)
    assert box.html == "<section><span>s</span></section>"


def test_extract_a_standalone_node_is_a_noop() -> None:
    node = Element("div")
    assert node.extract() is node  # a node with no parent extracts to itself


def test_wrap_puts_a_node_inside_an_element() -> None:
    doc = parse("<div><span>s</span></div>")
    wrapper = _found(doc, "span").wrap(Element("a"))
    assert wrapper.tag == "a"  # wrap returns the wrapper
    assert _found(doc, "div").html == "<div><a><span>s</span></a></div>"


def test_wrap_a_standalone_node() -> None:
    assert Text("hi").wrap(Element("em")).html == "<em>hi</em>"


@pytest.mark.parametrize(
    "wrapper",
    # a Text cannot hold children, and a bare string is not a node at all
    [pytest.param(Text("y"), id="text"), pytest.param("y", id="non-node")],
)
def test_wrap_rejects_a_non_element_wrapper(wrapper: object) -> None:
    with pytest.raises(TypeError, match="must be an element"):
        Text("x").wrap(wrapper)  # ty: ignore[invalid-argument-type]  # wrapper must be an element node


def test_wrap_in_an_ancestor_is_a_cycle() -> None:
    doc = parse("<div><p><span></span></p></div>")
    with pytest.raises(ValueError, match="own subtree"):
        _found(doc, "span").wrap(_found(doc, "div"))


def test_wrap_a_document_is_rejected() -> None:
    with pytest.raises(TypeError, match="Document cannot be inserted"):
        parse("<p></p>").wrap(Element("div"))


def test_wrap_siblings_groups_a_run_up_to_until() -> None:
    doc = parse("<ul><li>a</li><li>b</li><li>c</li><li>d</li></ul>")
    items = list(_found(doc, "ul").children)
    wrapper = items[1].wrap_siblings(Element("div"), until=items[2])
    assert wrapper.tag == "div"  # wrap_siblings returns the wrapper
    assert _found(doc, "ul").html == "<ul><li>a</li><div><li>b</li><li>c</li></div><li>d</li></ul>"


def test_wrap_siblings_with_no_until_reaches_the_last_sibling() -> None:
    doc = parse("<ul><li>a</li><li>b</li><li>c</li></ul>")
    second = list(_found(doc, "ul").children)[1]
    second.wrap_siblings(Element("div"))
    assert _found(doc, "ul").html == "<ul><li>a</li><div><li>b</li><li>c</li></div></ul>"


def test_wrap_siblings_of_a_single_node_until_self() -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    first = next(iter(_found(doc, "ul").children))
    first.wrap_siblings(Element("div"), until=first)
    assert _found(doc, "ul").html == "<ul><div><li>a</li></div><li>b</li></ul>"


def test_wrap_siblings_adopts_a_wrapper_from_another_tree() -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    first = next(iter(_found(doc, "ul").children))
    wrapper = first.wrap_siblings(_found(parse("<section></section>"), "section"))
    assert _found(doc, "ul").html == "<ul><section><li>a</li><li>b</li></section></ul>"
    assert wrapper.parent == _found(doc, "ul")  # the returned wrapper is the adopted copy in the live tree


@pytest.mark.parametrize(
    "wrapper",
    [pytest.param(Text("y"), id="text"), pytest.param("y", id="non-node")],
)
def test_wrap_siblings_rejects_a_non_element_wrapper(wrapper: object) -> None:
    li = _found(parse("<ul><li>a</li></ul>"), "li")
    with pytest.raises(TypeError, match="must be an element"):
        li.wrap_siblings(wrapper)  # ty: ignore[invalid-argument-type]  # wrapper must be an element node


def test_wrap_siblings_rejects_a_non_node_until() -> None:
    li = _found(parse("<ul><li>a</li></ul>"), "li")
    with pytest.raises(TypeError, match="until must be a node"):
        li.wrap_siblings(Element("div"), until="a")  # ty: ignore[invalid-argument-type]  # until is a node or None


def test_wrap_siblings_needs_a_parent() -> None:
    with pytest.raises(ValueError, match="no parent"):
        Element("div").wrap_siblings(Element("section"))


def test_wrap_siblings_rejects_an_until_in_another_parent() -> None:
    doc = parse("<ul><li>a</li></ul><ol><li>b</li></ol>")
    first = next(iter(_found(doc, "ul").children))
    outsider = next(iter(_found(doc, "ol").children))
    with pytest.raises(ValueError, match="following siblings"):
        first.wrap_siblings(Element("div"), until=outsider)


def test_wrap_siblings_rejects_an_until_before_the_node() -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    items = list(_found(doc, "ul").children)
    with pytest.raises(ValueError, match="following siblings"):
        items[1].wrap_siblings(Element("div"), until=items[0])


def test_wrap_siblings_rejects_a_wrapper_inside_the_run() -> None:
    doc = parse("<ul><li>a</li><li>b</li></ul>")
    items = _found(doc, "ul").find_all("li")
    with pytest.raises(ValueError, match="one of the wrapped nodes"):
        items[0].wrap_siblings(items[1])


def test_wrap_siblings_in_an_ancestor_is_a_cycle() -> None:
    doc = parse("<section><ul><li>a</li></ul></section>")
    first = _found(doc, "li")
    with pytest.raises(ValueError, match="own subtree"):
        first.wrap_siblings(_found(doc, "section"))


def test_wrap_siblings_requires_a_wrapper_argument() -> None:
    li = _found(parse("<ul><li>a</li></ul>"), "li")
    with pytest.raises(TypeError):
        li.wrap_siblings()  # ty: ignore[missing-argument]  # wrapper is required


def test_wrap_children_boxes_every_child() -> None:
    doc = parse("<div>a<b>x</b>c</div>")
    wrapper = _found(doc, "div").wrap_children(Element("section"))
    assert wrapper.tag == "section"  # wrap_children returns the wrapper
    assert _found(doc, "div").html == "<div><section>a<b>x</b>c</section></div>"


def test_wrap_children_of_an_empty_element_nests_an_empty_wrapper() -> None:
    div = Element("div")
    div.wrap_children(Element("section"))
    assert div.html == "<div><section></section></div>"


@pytest.mark.parametrize(
    "wrapper",
    [pytest.param(Text("y"), id="text"), pytest.param("y", id="non-node")],
)
def test_wrap_children_rejects_a_non_element_wrapper(wrapper: object) -> None:
    div = _found(parse("<div>x</div>"), "div")
    with pytest.raises(TypeError, match="must be an element"):
        div.wrap_children(wrapper)  # ty: ignore[invalid-argument-type]  # wrapper must be an element node


def test_wrap_children_into_self_is_a_cycle() -> None:
    div = _found(parse("<div>x</div>"), "div")
    with pytest.raises(ValueError, match="own subtree"):
        div.wrap_children(div)


def test_unwrap_replaces_an_element_with_its_children() -> None:
    doc = parse("<div><b>x<i>y</i></b></div>")
    bold = _found(doc, "b")
    assert assert_type(bold.unwrap(), Element) is bold
    assert _found(doc, "div").html == "<div>x<i>y</i></div>"


def test_unwrap_a_childless_node_just_removes_it() -> None:
    doc = parse("<div><span></span>t</div>")
    _found(doc, "span").unwrap()
    assert _found(doc, "div").html == "<div>t</div>"


def test_unwrap_needs_a_parent() -> None:
    with pytest.raises(ValueError, match="no parent"):
        Element("div").unwrap()


def test_decompose_detaches_and_returns_none() -> None:
    doc = parse("<div><span>s</span>t</div>")
    assert _found(doc, "span").decompose() is None
    assert _found(doc, "div").html == "<div>t</div>"


def test_normalize_merges_adjacent_text_and_drops_empties() -> None:
    paragraph = Element("p")
    # the Comment is neither text nor element, so it breaks a run without merging
    paragraph.extend([Text("a"), Text(""), Text("b"), Comment("c"), Element("br"), Text("d")])
    paragraph.normalize()
    assert paragraph.html == "<p>ab<!--c--><br>d</p>"
    assert len(paragraph) == 4  # the run a/""/b collapsed into one Text node


def test_normalize_drops_a_leading_empty_text_node() -> None:
    paragraph = Element("p")
    paragraph.extend([Text(""), Text("x")])
    paragraph.normalize()
    assert [type(child).__name__ for child in paragraph] == ["Text"]
    assert paragraph.html == "<p>x</p>"


def test_normalize_recurses_into_child_elements() -> None:
    paragraph = Element("p")
    inner = Element("b")
    inner.extend([Text("x"), Text("y")])
    paragraph.append(inner)
    paragraph.normalize()
    bold = _found(paragraph, "b")
    assert len(bold) == 1  # the nested adjacent text merged too
    assert bold.html == "<b>xy</b>"


@pytest.mark.parametrize("count", [pytest.param(3, id="small"), pytest.param(100, id="wide")])
def test_attribute_growth_preserves_values_and_order(count: int) -> None:
    root: Final = Element("p")
    for index in range(count):
        root.attrs[f"data-{index}"] = str(index)
    root.attrs["data-0"] = "changed"
    del root.attrs["data-1"]
    root.attrs["data-last"] = "last"
    assert list(root.attrs.items()) == [
        ("data-0", "changed"),
        *((f"data-{index}", str(index)) for index in range(2, count)),
        ("data-last", "last"),
    ]


def test_attribute_growth_keeps_copies_independent() -> None:
    root: Final = Element("p")
    for name in ("a", "b", "c"):
        root.attrs[f"data-{name}"] = name
    copied: Final = deepcopy(root)
    root.attrs["data-d"] = "original"
    copied.attrs["data-d"] = "copy"
    assert (root.attrs["data-d"], copied.attrs["data-d"]) == ("original", "copy")


def test_attribute_growth_preserves_parsed_attributes() -> None:
    root: Final = parse('<p title="original" hidden></p>').find("p")
    assert root is not None
    for index in range(100):
        root.attrs[f"data-{index}"] = str(index)
    assert list(root.attrs.items()) == [
        ("title", "original"),
        ("hidden", ""),
        *((f"data-{index}", str(index)) for index in range(100)),
    ]


def test_attribute_growth_records_changes_across_capacity_boundaries() -> None:
    root: Final = Element("p")
    observer: Final = MutationObserver()
    observer.observe(root, attributes=True, attribute_old_value=True)
    for index in range(17):
        root.attrs[f"data-{index}"] = str(index)
    root.attrs["data-0"] = "changed"
    del root.attrs["data-1"]
    root.attrs["data-1"] = "restored"
    assert [(record.attribute_name, record.old_value) for record in observer.take_records()] == [
        *((f"data-{index}", None) for index in range(17)),
        ("data-0", "0"),
        ("data-1", "1"),
        ("data-1", None),
    ]


@pytest.mark.parametrize("count", [3, 100], ids=["small", "wide"])
def test_attribute_growth_preserves_xml_names_and_values(count: int) -> None:
    attributes: Final = " ".join(f'名{index}="é😀{index}"' for index in range(count))
    root: Final = parse_xml(f"<root {attributes}/>").find("root")
    assert root is not None
    root.attrs["追加"] = "追加値"
    assert list(root.attrs.items()) == [
        *((f"名{index}", f"é😀{index}") for index in range(count)),
        ("追加", "追加値"),
    ]


@pytest.mark.parametrize("count", [pytest.param(2, id="pair"), pytest.param(100, id="long-run")])
@pytest.mark.parametrize("text", [pytest.param("word", id="ascii"), pytest.param("é水😀", id="unicode")])
def test_normalize_keeps_first_text_and_detaches_merged_nodes(count: int, text: str) -> None:
    root: Final = Element("p")
    root.extend(Text(text) for _ in range(count))
    children: Final = tuple(root)
    root.normalize()
    assert (tuple(root), children[0].text, [(child.parent, child.text) for child in children[1:]]) == (
        (children[0],),
        text * count,
        [(None, text)] * (count - 1),
    )


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        pytest.param(("", "", ""), [], id="empty"),
        pytest.param(("a", "", ""), ["a"], id="empty-tail"),
        pytest.param(("a", "", "b"), ["ab"], id="empty-middle"),
        pytest.param(("", "a", "b"), ["ab"], id="empty-prefix"),
    ],
)
def test_normalize_removes_empty_nodes(texts: tuple[str, ...], expected: list[str]) -> None:
    root: Final = Element("p")
    root.extend(Text(text) for text in texts)
    root.normalize()
    assert [child.text for child in root] == expected


def test_normalize_preserves_separate_runs() -> None:
    root: Final = Element("p")
    root.extend([Text("a"), Text("b"), Comment("boundary"), Text("c"), Text("d")])
    root.normalize()
    assert [(type(child), child.html) for child in root] == [(Text, "ab"), (Comment, "<!--boundary-->"), (Text, "cd")]


@pytest.mark.parametrize("existing", [False, True], ids=["new", "replacement"])
def test_attribute_benchmark_rebuilds_input(*, existing: bool) -> None:
    operation: Final = cast("Mutating", OPERATIONS["attribute-grow"][0])
    case: Final = cast("tuple[Element, tuple[str, ...]]", operation.setup((3, existing)))
    untouched: Final = cast("tuple[Element, tuple[str, ...]]", operation.setup((3, existing)))
    operation.run(case)
    assert (dict(case[0].attrs), dict(untouched[0].attrs)) == (
        {f"data-{index}": "after" for index in range(3)},
        {f"data-{index}": "before" for index in range(3)} if existing else {},
    )


@pytest.mark.parametrize("count", [2, 10], ids=["pair", "run"])
def test_normalize_benchmark_rebuilds_input(count: int) -> None:
    operation: Final = cast("Mutating", OPERATIONS["normalize-dom"][0])
    root: Final = cast("Element", operation.setup((count, "é😀")))
    untouched: Final = cast("Element", operation.setup((count, "é😀")))
    operation.run(root)
    assert ([child.text for child in root], [child.text for child in untouched]) == (["é😀" * count], ["é😀"] * count)


def test_empty_tail_benchmark_preserves_first_node() -> None:
    _, run, load = next(benchmark for benchmark in benchmarks() if benchmark[0] == "normalize-dom-empty-tail")
    operation: Final = cast("Mutating", run)
    root: Final = cast("Element", operation.setup(load()))
    before: Final = tuple(root)
    operation.run(root)
    assert (tuple(root), tuple(node.text for node in before), [node.parent for node in before[1:]]) == (
        (before[0],),
        ("word", *("",) * 1000),
        [None] * 1000,
    )


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            '<legend><input name="keep"></legend>',
            [("keep", "")],
            id="legend-outside-fieldset",
        ),
        pytest.param(
            '<fieldset disabled><input name="before"><legend><input name="keep"></legend>'
            '<legend><input name="second"></legend><input name="after"></fieldset>',
            [("keep", "")],
            id="first-legend-after-control",
        ),
        pytest.param(
            '<fieldset disabled><div><legend><input name="nested"></legend></div></fieldset>',
            [],
            id="legend-must-be-direct",
        ),
        pytest.param(
            '<fieldset disabled><legend><fieldset disabled><legend><input name="keep"></legend>'
            '<input name="inner"></fieldset></legend><input name="outer"></fieldset>',
            [("keep", "")],
            id="nested-exemptions",
        ),
        pytest.param(
            '<fieldset disabled><legend><fieldset disabled><input name="inner"></fieldset>'
            '<input name="keep"></legend></fieldset>',
            [("keep", "")],
            id="nested-disabled-without-legend",
        ),
        pytest.param(
            '<fieldset disabled><legend><input name="own" disabled><input name="keep"></legend></fieldset>',
            [("keep", "")],
            id="control-disabled-inside-exemption",
        ),
        pytest.param(
            '<fieldset disabled><legend><template><input name="inert"></template>'
            '<textarea name="keep">text</textarea></legend></fieldset>',
            [("keep", "text")],
            id="template-in-legend",
        ),
        pytest.param(
            "<fieldset disabled><legend></legend></fieldset><fieldset>"
            '<legend><input name="keep"></legend><input name="after"></fieldset>',
            [("keep", ""), ("after", "")],
            id="enabled-following-fieldset",
        ),
    ],
)
def test_form_data_fieldset_boundaries(markup: str, expected: list[tuple[str, str]]) -> None:
    form: Final = parse(f'<form>{markup}<input name="tail" value="ok"></form>').select("form")[0]
    assert form.form_data() == [*expected, ("tail", "ok")]


@pytest.mark.parametrize("case_index", [0, 1, 2, 3], ids=["disabled", "legend", "enabled", "plain"])
def test_form_data_fieldset_shared_inputs(case_index: int) -> None:
    source: Final = cast("str", INPUTS["form-data-fieldsets"]()[case_index][1])
    operation: Final = cast("Callable[[str], list[tuple[str, str]]]", OPERATIONS["form-data-fieldsets"][0])
    expected: Final = (
        []
        if case_index == 0
        else [("allowed", "yes")]
        if case_index == 1
        else [(f"n{index}", f"v{index}") for index in range(2_048 if case_index == 2 else 4)]
    )
    assert operation(source) == [*expected, ("tail", "ok")]


def test_form_data_fieldset_refreshes_after_enabling() -> None:
    form: Final = parse(
        '<form><fieldset disabled><legend><input name="first"></legend><input name="second"></fieldset></form>'
    ).select("form")[0]
    form.form_data()
    del form.select("fieldset")[0].attrs["disabled"]
    assert form.form_data() == [("first", ""), ("second", "")]


@pytest.mark.skipif(sys.implementation.name != "cpython", reason="CPython allocation-triggered collection")
@pytest.mark.parametrize("mutation", ["detach", "remove", "replace"])
def test_form_data_handles_legend_changes_during_collection(mutation: str) -> None:
    controls: Final = "".join(f'<input name="n{index}" value="v{index}">' for index in range(256))
    content: Final = (
        f"<legend></legend><div>{controls}</div>" if mutation == "remove" else f"<legend>{controls}</legend>"
    )
    disabled: Final = "" if mutation == "remove" else " disabled"
    form: Final = parse(
        f'<form><fieldset{disabled}>{content}<input name="blocked"></fieldset><input name="tail" value="ok"></form>'
    ).select("form")[0]
    fieldset: Final = form.select("fieldset")[0]
    legend: Final = form.select("legend")[0]
    collect: Final = form.form_data
    thresholds: Final = gc.get_threshold()
    restore_gc: Final = gc.enable if gc.isenabled() else gc.disable
    changed = False

    def change_legend(phase: str, _info: dict[str, int]) -> None:
        nonlocal changed
        if phase == "start" and not changed:
            changed = True
            if mutation == "replace":
                legend.insert_before(Element("legend"))
            else:
                legend.extract()
                if mutation == "remove":
                    fieldset.attrs["disabled"] = ""

    gc.disable()
    gc.collect()
    gc.callbacks.append(change_legend)
    try:
        gc.set_threshold(gc.get_count()[0] + 64, thresholds[1], thresholds[2])
        gc.enable()
        pairs: Final = collect()
        gc.collect()
    finally:
        gc.disable()
        gc.callbacks.remove(change_legend)
        gc.set_threshold(*thresholds)
        restore_gc()
    count: Final = sum(name.startswith("n") for name, _value in pairs)
    expected: Final = [(f"n{index}", f"v{index}") for index in range(count)]
    tail: Final = [] if sys.version_info < (3, 12) and mutation == "detach" else [("tail", "ok")]
    blocked: Final = [("blocked", "")] if sys.version_info >= (3, 12) and mutation == "remove" else []
    assert count == 256 if sys.version_info >= (3, 12) else 0 < count < 256
    assert (changed, legend.parent, pairs) == (
        True,
        fieldset if mutation == "replace" else None,
        [*expected, *blocked, *tail],
    )


@pytest.mark.parametrize("edit", ["none", "rename", "type", "append", "detach"])
def test_radio_group_index_tracks_scope_and_edits(edit: str) -> None:
    document: Final = parse(
        '<form><input type=radio name="é水😀" checked><input type=radio name="é水😀"></form>'
        '<form><input type=radio name="é水😀" checked></form>'
    )
    form: Final = document.select("form")[0]
    first, selected, outside = document.select("input")
    if edit == "rename":
        first.attrs["name"] = "other"
    elif edit == "type":
        first.attrs["type"] = "checkbox"
    elif edit == "append":
        form.append(Element("input", {"type": "radio", "name": "é水😀", "checked": ""}))
        document.select("input")
    elif edit == "detach":
        form.extract()
        document.select("input")
    selected.checked = True
    assert ([node.checked for node in form.children if isinstance(node, Element)], outside.checked) == (
        [edit in {"rename", "type"}, True, *([False] if edit == "append" else [])],
        True,
    )


def test_radio_group_index_preserves_attribute_observer_order() -> None:
    document: Final = parse("<form><input type=radio name=a checked><input type=radio name=a></form>")
    form: Final = document.select("form")[0]
    first, selected = document.select("input")
    observer: Final = MutationObserver()
    observer.observe(form, attributes=True, subtree=True, attribute_old_value=True)
    selected.checked = True
    assert [(record.target, record.attribute_name, record.old_value) for record in observer.take_records()] == [
        (selected, "checked", None),
        (first, "checked", ""),
    ]


@pytest.mark.parametrize(
    ("case", "selected"),
    [
        pytest.param(0, 15, id="document-radios"),
        pytest.param(1, 0, id="unindexed-change"),
        pytest.param(2, 15, id="many-forms"),
        pytest.param(3, 7, id="invalidation"),
    ],
)
def test_radio_group_benchmark_output(case: int, selected: int) -> None:
    operation: Final = cast("Mutating", OPERATIONS["radio-group"][0])
    data: Final = INPUTS["radio-group"]()[case][1]
    assert operation.run(operation.setup(data)) == tuple(index == selected for index in range(16))


@pytest.mark.parametrize("indexed", [pytest.param(False, id="unindexed"), pytest.param(True, id="indexed")])
def test_radio_group_document_scope_keeps_other_names_and_types(*, indexed: bool) -> None:
    document: Final = parse(
        '<input type=radio name="é水😀" checked><input type=radio name="é水😀">'
        '<input type=radio name=other checked><input type=checkbox name="é水😀" checked>'
    )
    radios: Final = [node for node in document.children[0].children[-1].children if isinstance(node, Element)]
    if indexed:
        document.select("input")
    radios[1].checked = True
    assert [node.checked for node in radios] == [False, True, True, True]
