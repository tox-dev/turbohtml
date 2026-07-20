"""Element.attrs: token-list attributes split to list[str], plus order and valueless rules."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import parse

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Element


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
