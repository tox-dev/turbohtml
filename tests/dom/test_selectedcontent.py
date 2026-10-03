from __future__ import annotations

from typing import Final

import pytest

from turbohtml import Element, IncrementalParser, parse, parse_fragment

_OPTION_HOLDS_SELECTEDCONTENT: Final = "<select><option><selectedcontent>"
_OPTION_HOLDS_SELECTEDCONTENT_TREE: Final = "<select><option><selectedcontent></selectedcontent></option></select>"


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(_OPTION_HOLDS_SELECTEDCONTENT, _OPTION_HOLDS_SELECTEDCONTENT_TREE, id="child-of-option"),
        pytest.param(
            "<select><option><div><selectedcontent>",
            "<select><option><div><selectedcontent></selectedcontent></div></option></select>",
            id="descendant-of-option",
        ),
        pytest.param(
            "<select><option selected><selectedcontent>",
            '<select><option selected=""><selectedcontent></selectedcontent></option></select>',
            id="selected-option",
        ),
        pytest.param(
            "<select><option>a</option><option selected>b<selectedcontent>",
            '<select><option>a</option><option selected="">b<selectedcontent></selectedcontent></option></select>',
            id="later-selected-option",
        ),
        pytest.param(
            "<select><option><selectedcontent><selectedcontent>",
            "<select><option><selectedcontent><selectedcontent></selectedcontent></selectedcontent></option></select>",
            id="nested-selectedcontent",
        ),
        pytest.param(
            "<select><option>one<selectedcontent></selectedcontent></option>"
            "<button><selectedcontent></selectedcontent></button><option selected>two</option></select>",
            "<select><option>one<selectedcontent></selectedcontent></option>"
            '<button><selectedcontent>two</selectedcontent></button><option selected="">two</option></select>',
            id="skips-earlier-disabled",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option>one<selectedcontent></selectedcontent></option></select>",
            "<select><button><selectedcontent>one<selectedcontent></selectedcontent></selectedcontent></button>"
            "<option>one<selectedcontent></selectedcontent></option></select>",
            id="copies-disabled-into-enabled",
        ),
        pytest.param(
            "<option><select><button><selectedcontent></button><option>x</option></select>",
            "<option><select><button><selectedcontent></selectedcontent></button><option>x</option></select></option>",
            id="option-above-select",
        ),
        pytest.param(
            "<select><svg><foreignObject><select><button><selectedcontent></button><option>x</option></select>",
            "<select><svg><foreignObject><select><button><selectedcontent></selectedcontent></button>"
            "<option>x</option></select></foreignObject></svg></select>",
            id="second-select-ancestor",
        ),
        pytest.param(
            "<select><button><selectedcontent><svg><foreignObject><select><button><selectedcontent></button>"
            "<option>x</option></select>",
            "<select><button><selectedcontent><svg><foreignObject><select><button><selectedcontent></selectedcontent>"
            "</button><option>x</option></select></foreignObject></svg></selectedcontent></button></select>",
            id="selectedcontent-above-select",
        ),
        pytest.param(
            "<select><svg><foreignObject><select><button><selectedcontent></button></select></foreignObject></svg>"
            "<option>x</option></select>",
            "<select><svg><foreignObject><select><button><selectedcontent></selectedcontent></button></select>"
            "</foreignObject></svg><option>x</option></select>",
            id="skips-nested-select",
        ),
        pytest.param(
            "<select><svg><foreignObject><select><option>in</option></select></foreignObject></svg>"
            "<button><selectedcontent></button><option>out</option></select>",
            "<select><svg><foreignObject><select><option>in</option></select></foreignObject></svg>"
            "<button><selectedcontent>out</selectedcontent></button><option>out</option></select>",
            id="first-option-skips-nested-select",
        ),
        pytest.param(
            "<select><svg><selectedcontent></selectedcontent></svg><button><selectedcontent></button><option>x</option>",
            "<select><svg><selectedcontent></selectedcontent></svg><button><selectedcontent>x</selectedcontent></button>"
            "<option>x</option></select>",
            id="skips-svg-namesake",
        ),
    ],
)
def test_parse_clones_option_only_into_enabled_selectedcontent(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


def test_parse_fragment_leaves_selectedcontent_in_option_empty() -> None:
    assert parse_fragment(_OPTION_HOLDS_SELECTEDCONTENT).inner_html == _OPTION_HOLDS_SELECTEDCONTENT_TREE


def test_incremental_parser_leaves_selectedcontent_in_option_empty() -> None:
    parser = IncrementalParser()
    parser.feed("<select><option>")
    parser.feed("<selectedcontent>")
    assert parser.close().html == f"<html><head></head><body>{_OPTION_HOLDS_SELECTEDCONTENT_TREE}</body></html>"


def test_set_inner_html_leaves_selectedcontent_in_option_empty() -> None:
    host = parse("<div></div>").find("div")
    assert isinstance(host, Element)
    host.set_inner_html(_OPTION_HOLDS_SELECTEDCONTENT)
    assert host.inner_html == _OPTION_HOLDS_SELECTEDCONTENT_TREE


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(
            "<select><button><selectedcontent></button><datalist><option selected>D</option></datalist>"
            "<option>B</option></select>",
            "<select><button><selectedcontent></selectedcontent></button>"
            '<datalist><option selected="">D</option></datalist><option>B</option></select>',
            id="datalist-option-has-no-nearest-select",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><optgroup><option selected>G</option></optgroup></select>",
            "<select><button><selectedcontent>G</selectedcontent></button>"
            '<optgroup><option selected="">G</option></optgroup></select>',
            id="single-optgroup-keeps-nearest-select",
        ),
        pytest.param(
            "<select><optgroup><optgroup><option selected>N</option></optgroup></optgroup>"
            "<button><selectedcontent></button></select>",
            '<select><optgroup></optgroup><optgroup><option selected="">N</option></optgroup>'
            "<button><selectedcontent></selectedcontent></button></select>",
            id="second-optgroup-drops-nearest-select",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option selected>o<div><option selected>p</div></option>"
            "<option>q</option></select>",
            '<select><button><selectedcontent>o<div><option selected="">p</option></div></selectedcontent></button>'
            '<option selected="">o<div><option selected="">p</option></div></option><option>q</option></select>',
            id="option-in-option-cloned-once",
        ),
    ],
)
def test_parse_clones_only_options_with_a_nearest_select(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


_DEEP_OPTION_NEST: Final = "<select><button><selectedcontent></button>" + "<option selected><div>" * 3 + "</select>"
_DEEP_OPTION_NEST_BODY: Final = (
    "<select><button><selectedcontent>"
    '<div><option selected=""><div><option selected=""><div></div></option></div></option></div>'
    "</selectedcontent></button>"
    '<option selected=""><div><option selected=""><div><option selected=""><div></div>'
    "</option></div></option></div></option></select>"
)


def test_parse_clones_nested_selected_options_once() -> None:
    assert parse(_DEEP_OPTION_NEST).html == f"<html><head></head><body>{_DEEP_OPTION_NEST_BODY}</body></html>"


def test_parse_of_deeply_nested_selected_options_stays_linear() -> None:
    document = parse("<select><button><selectedcontent></button>" + "<option selected><div>" * 2000 + "</select>")
    selectedcontent = document.find("selectedcontent")
    assert isinstance(selectedcontent, Element)
    assert len(document.html) < 8 * len("<option selected><div>" * 2000)


def test_set_inner_html_clones_the_selected_option() -> None:
    host = parse("<div></div>").find("div")
    assert isinstance(host, Element)
    host.set_inner_html("<select><button><selectedcontent></button><option selected>pick</option></select>")
    assert host.inner_html == (
        '<select><button><selectedcontent>pick</selectedcontent></button><option selected="">pick</option></select>'
    )


def test_adoption_agency_move_keeps_the_select_clone_correct() -> None:
    markup = "<select><button><selectedcontent></button><b><option selected>o</b><option>q</option></select>"
    assert parse(markup).html == (
        "<html><head></head><body><select><button><selectedcontent>o</selectedcontent></button>"
        '<b><option selected="">o</option></b><option>q</option></select></body></html>'
    )


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(
            "<select><button><selectedcontent></button><div><optgroup><div><optgroup><option selected>N</option>"
            "</select>",
            "<select><button><selectedcontent></selectedcontent></button>"
            '<div><optgroup><div><optgroup><option selected="">N</option></optgroup></div></optgroup></div></select>',
            id="two-optgroup-ancestors-drop-nearest-select",
        ),
        pytest.param(
            "<select><button><selectedcontent><option>I</option></selectedcontent></button>"
            "<option selected>O</option></select>",
            '<select><button><selectedcontent>O</selectedcontent></button><option selected="">O</option></select>',
            id="first-option-inside-and-outside-target",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option selected>o<b>bb<div>dd</b><option>q</option></select>",
            "<select><button><selectedcontent>o<b>bb</b><div><b>dd</b><option>q</option></div></selectedcontent></button>"
            '<option selected="">o<b>bb</b><div><b>dd</b><option>q</option></div></option></select>',
            id="adoption-agency-reparents-under-select",
        ),
    ],
)
def test_parse_selectedcontent_cache_edge_cases(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


def test_parse_many_selects_each_clone_their_option() -> None:
    unit = "<select><button><selectedcontent></button><option selected>x</option></select>"
    cloned = '<select><button><selectedcontent>x</selectedcontent></button><option selected="">x</option></select>'
    assert parse(unit * 9).html == f"<html><head></head><body>{cloned * 9}</body></html>"


def test_selectedcontent_inserted_after_first_option_refreshes_target() -> None:
    markup = "<select><option selected>a</option><button><selectedcontent></button><option selected>b</option></select>"
    assert parse(markup).html == (
        '<html><head></head><body><select><option selected="">a</option>'
        '<button><selectedcontent>b</selectedcontent></button><option selected="">b</option></select></body></html>'
    )


def test_adoption_agency_after_a_clone_refreshes_the_cache() -> None:
    markup = (
        "<select><button><selectedcontent></button><option selected>a</option><b><p>q</b>"
        "<option selected>c</option></select>"
    )
    assert parse(markup).html == (
        "<html><head></head><body><select><button><selectedcontent>c</selectedcontent></button>"
        '<option selected="">a</option><b></b><p><b>q</b></p><option selected="">c</option></select></body></html>'
    )


def test_default_first_option_without_selected_attribute_clones() -> None:
    markup = "<select><button><selectedcontent></button><div><option>x</option></div></select>"
    assert parse(markup).html == (
        "<html><head></head><body><select><button><selectedcontent>x</selectedcontent></button>"
        "<div><option>x</option></div></select></body></html>"
    )


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(
            "<select><option selected>a</option><option selected>b<selectedcontent></option></select>",
            '<select><option selected="">a</option>'
            '<option selected="">b<selectedcontent></selectedcontent></option></select>',
            id="selectedcontent-inside-option-after-cache",
        ),
        pytest.param(
            "<select><option selected>a</option></select><selectedcontent>",
            '<select><option selected="">a</option></select><selectedcontent></selectedcontent>',
            id="selectedcontent-outside-select-after-cache",
        ),
        pytest.param(
            "<select><b><p>q</b><option selected>x</option></select>",
            '<select><b></b><p><b>q</b></p><option selected="">x</option></select>',
            id="adoption-agency-before-any-cache",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option selected>x</option></select>"
            "<select><b><p>q</b><option selected>y</option></select>",
            '<select><button><selectedcontent>x</selectedcontent></button><option selected="">x</option></select>'
            '<select><b></b><p><b>q</b></p><option selected="">y</option></select>',
            id="adoption-agency-in-a-second-select",
        ),
        pytest.param(
            "<select><button><selectedcontent><b><i>t</i></b></selectedcontent></button><option>x</option></select>",
            "<select><button><selectedcontent>x</selectedcontent></button><option>x</option></select>",
            id="first-option-found-after-deep-backtrack",
        ),
    ],
)
def test_parse_selectedcontent_cache_invalidation(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"
