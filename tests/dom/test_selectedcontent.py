from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, IncrementalParser, parse, parse_fragment

if TYPE_CHECKING:
    from work_steps import WorkSteps

_OPTION_HOLDS_SELECTEDCONTENT: Final[str] = "<select><option><selectedcontent>"
_OPTION_HOLDS_SELECTEDCONTENT_TREE: Final[str] = "<select><option><selectedcontent></selectedcontent></option></select>"


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
            "<select><button><selectedcontent>B</selectedcontent></button>"
            '<datalist><option selected="">D</option></datalist><option>B</option></select>',
            id="datalist-option-has-no-nearest-select",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><datalist><option>D</option></datalist>"
            "<option>B</option></select>",
            "<select><button><selectedcontent>B</selectedcontent></button>"
            "<datalist><option>D</option></datalist><option>B</option></select>",
            id="default-selected-skips-datalist-option",
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
            "<button><selectedcontent>N</selectedcontent></button></select>",
            id="sibling-optgroups-keep-nearest-select",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option disabled selected>A</option><option>B</option></select>",
            '<select><button><selectedcontent>A</selectedcontent></button><option disabled="" selected="">A</option>'
            "<option>B</option></select>",
            id="selected-disabled-option-stays-selected",
        ),
        pytest.param(
            "<select><button><selectedcontent><option>I</option></selectedcontent></button><option>B</option></select>",
            "<select><button><selectedcontent>B</selectedcontent></button><option>B</option></select>",
            id="default-moves-past-an-option-the-clone-removed",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option selected>o<div><option selected>p</div></option>"
            "<option>q</option></select>",
            '<select><button><selectedcontent>o<div><option selected="">p</option></div></selectedcontent></button>'
            '<option selected="">o<div><option selected="">p</option></div></option><option>q</option></select>',
            id="option-in-option-cloned-once",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><template><option selected>T</option></template>"
            "<option>x</option></select>",
            "<select><button><selectedcontent>x</selectedcontent></button>"
            '<template><option selected="">T</option></template><option>x</option></select>',
            id="option-in-template-has-no-nearest-select",
        ),
        pytest.param(
            "<select><template><selectedcontent></selectedcontent></template>"
            "<button><selectedcontent></button><option>x</option></select>",
            "<select><template><selectedcontent></selectedcontent></template>"
            "<button><selectedcontent>x</selectedcontent></button><option>x</option></select>",
            id="templated-selectedcontent-is-not-a-target",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option disabled>A</option><option>B</option></select>",
            '<select><button><selectedcontent>B</selectedcontent></button><option disabled="">A</option>'
            "<option>B</option></select>",
            id="default-selected-skips-disabled-option",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><optgroup disabled><option>A</option></optgroup>"
            "<option>B</option></select>",
            '<select><button><selectedcontent>B</selectedcontent></button><optgroup disabled=""><option>A</option>'
            "</optgroup><option>B</option></select>",
            id="default-selected-skips-disabled-optgroup",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><optgroup><option>A</option></optgroup>"
            "<option>B</option></select>",
            "<select><button><selectedcontent>A</selectedcontent></button><optgroup><option>A</option></optgroup>"
            "<option>B</option></select>",
            id="default-selected-enters-enabled-optgroup",
        ),
        pytest.param(
            "<select><button><selectedcontent></selectedcontent><selectedcontent></selectedcontent></button>"
            "<option>x</option></select>",
            "<select><button><selectedcontent>x</selectedcontent><selectedcontent>x</selectedcontent></button>"
            "<option>x</option></select>",
            id="every-enabled-selectedcontent-updated",
        ),
        pytest.param(
            "<select><button><selectedcontent></button><option disabled>A</option></select>",
            '<select><button><selectedcontent></selectedcontent></button><option disabled="">A</option></select>',
            id="no-enabled-option-leaves-selectedcontent-empty",
        ),
    ],
)
def test_parse_clones_only_options_with_a_nearest_select(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


def test_parse_clones_nested_selected_options_once() -> None:
    deep_option_nest_body: str = (
        "<select><button><selectedcontent>"
        '<div><option selected=""><div><option selected=""><div></div></option></div></option></div>'
        "</selectedcontent></button>"
        '<option selected=""><div><option selected=""><div><option selected=""><div></div>'
        "</option></div></option></div></option></select>"
    )
    assert (
        parse(f"<select><button><selectedcontent></button>{'<option selected><div>' * 3}</select>").html
        == f"<html><head></head><body>{deep_option_nest_body}</body></html>"
    )


def test_parse_of_deeply_nested_selected_options_stays_linear() -> None:
    document = parse(f"<select><button><selectedcontent></button>{'<option selected><div>' * 2000}</select>")
    selectedcontent = document.find("selectedcontent")
    assert isinstance(selectedcontent, Element)
    assert len(document.html) < 8 * len("<option selected><div>" * 2000)


@pytest.mark.parametrize(
    ("options", "shown"),
    [
        pytest.param(
            "<optgroup disabled><option>a<option>b</optgroup><optgroup><option>c<option>d</optgroup>",
            "c",
            id="disabled-group-then-enabled-group",
        ),
        pytest.param(
            "<optgroup><option disabled>a</optgroup><optgroup disabled><option>b<option>c</optgroup><option>d",
            "d",
            id="enabled-group-then-disabled-group",
        ),
        pytest.param(
            "<optgroup disabled><option>a</optgroup><optgroup disabled><option>b<option selected>c</optgroup>",
            "c",
            id="selected-option-of-a-disabled-group",
        ),
        pytest.param("<optgroup disabled><option>a<option>b</optgroup>", "", id="only-a-disabled-group"),
    ],
)
def test_parse_skips_the_options_of_each_disabled_optgroup(options: str, shown: str) -> None:
    selectedcontent = parse(f"<select><button><selectedcontent></button>{options}</select>").find("selectedcontent")
    assert isinstance(selectedcontent, Element)
    assert selectedcontent.inner_html == shown


@pytest.mark.parametrize("disabled", [pytest.param("", id="enabled"), pytest.param(" disabled", id="disabled-last")])
def test_parse_of_options_in_an_optgroup_of_many_attributes_stays_linear(disabled: str, work_steps: WorkSteps) -> None:
    assert len(parse(_grouped_options_markup(4_000, disabled)).find_all("option")) == 4_000
    work_steps.assert_linear(lambda n: partial(parse, _grouped_options_markup(n, disabled)), (1000, 4000))


def _grouped_options_markup(count: int, disabled: str) -> str:
    attributes = " ".join(f"a{index}" for index in range(count))
    return f"<select><optgroup {attributes}{disabled}>{'<option>' * count}"


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


_CAPPED_TARGETS: Final[str] = "<selectedcontent></selectedcontent>" * 20
_CAPPED_MIRRORS: Final[str] = "<selectedcontent>a</selectedcontent>" * 16 + "<selectedcontent></selectedcontent>" * 4


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(
            f"<select>{_CAPPED_TARGETS}<option>a</option></select>",
            f"<select>{_CAPPED_MIRRORS}<option>a</option></select>",
            id="before-the-option",
        ),
        pytest.param(
            f"<select><option>a</option>{_CAPPED_TARGETS}</select>",
            f"<select><option>a</option>{_CAPPED_MIRRORS}</select>",
            id="after-the-option",
        ),
        pytest.param(
            f"<select><option>a</option><b><p>q</b></p>{_CAPPED_TARGETS}</select>",
            f"<select><option>a</option><b></b><p><b>q</b></p>{_CAPPED_MIRRORS}</select>",
            id="after-an-adoption-agency-move",
        ),
        pytest.param(
            f"<select>{_CAPPED_TARGETS}<option>b</option><option selected>a</option></select>",
            f'<select>{_CAPPED_MIRRORS}<option>b</option><option selected="">a</option></select>',
            id="later-selected-option",
        ),
        pytest.param(
            f"<select>{'<selectedcontent></selectedcontent>' * 15}<option>a</option>"
            "<selectedcontent></selectedcontent><selectedcontent>kept</selectedcontent></select>",
            f"<select>{'<selectedcontent>a</selectedcontent>' * 15}<option>a</option>"
            "<selectedcontent>a</selectedcontent><selectedcontent>kept</selectedcontent></select>",
            id="one-past-the-cap-keeps-its-own-content",
        ),
    ],
)
def test_parse_mirrors_the_option_into_sixteen_selectedcontent_at_most(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(
            f"<select>{'<selectedcontent></selectedcontent>' * 400}<option>{'<b>x</b>' * 100}",
            id="selectedcontent-before-the-option",
        ),
        pytest.param(
            f"<select><option>{'<b>x</b>' * 100}</option>{'<selectedcontent></selectedcontent>' * 400}",
            id="selectedcontent-after-the-option",
        ),
    ],
)
def test_parse_of_many_selectedcontent_stays_linear(page: str) -> None:
    assert len(parse(page).html) < 8 * len(page)


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


@pytest.mark.parametrize(
    ("markup", "body"),
    [
        pytest.param(
            "<select><option selected>a</option><button><selectedcontent></button></select>",
            '<select><option selected="">a</option><button><selectedcontent>a</selectedcontent></button></select>',
            id="selected-option",
        ),
        pytest.param(
            "<select><option>a</option><button><selectedcontent></button></select>",
            "<select><option>a</option><button><selectedcontent>a</selectedcontent></button></select>",
            id="default-option",
        ),
        pytest.param(
            "<select><option>a</option><option selected>b</option><button><selectedcontent></button></select>",
            '<select><option>a</option><option selected="">b</option>'
            "<button><selectedcontent>b</selectedcontent></button></select>",
            id="later-selected-option",
        ),
        pytest.param(
            "<select><option selected>a</option><option>b</option><button><selectedcontent></button></select>",
            '<select><option selected="">a</option><option>b</option>'
            "<button><selectedcontent>a</selectedcontent></button></select>",
            id="earlier-selected-option",
        ),
        pytest.param(
            "<select><option disabled selected>a</option><option>b</option><button><selectedcontent></button></select>",
            '<select><option disabled="" selected="">a</option><option>b</option>'
            "<button><selectedcontent>a</selectedcontent></button></select>",
            id="selected-disabled-option",
        ),
        pytest.param(
            "<select><datalist><option selected>D</option></datalist><option>B</option>"
            "<button><selectedcontent></button></select>",
            '<select><datalist><option selected="">D</option></datalist><option>B</option>'
            "<button><selectedcontent>B</selectedcontent></button></select>",
            id="past-datalist-option",
        ),
        pytest.param(
            "<select><option>a</option><button><selectedcontent>z</selectedcontent></button></select>",
            "<select><option>a</option><button><selectedcontent>az</selectedcontent></button></select>",
            id="parsed-content-follows-clone",
        ),
        pytest.param(
            "<select><option>a</option><button><selectedcontent></button><selectedcontent></selectedcontent></select>",
            "<select><option>a</option><button><selectedcontent>a</selectedcontent></button>"
            "<selectedcontent>a</selectedcontent></select>",
            id="every-late-selectedcontent",
        ),
        pytest.param(
            "<select><option>a</option><svg><foreignObject><button><selectedcontent></button></foreignObject></svg>"
            "</select>",
            "<select><option>a</option><svg><foreignObject><button><selectedcontent>a</selectedcontent></button>"
            "</foreignObject></svg></select>",
            id="through-foreign-content",
        ),
        pytest.param(
            "<select><option selected>a</option><b><p>q</b><button><selectedcontent></button></select>",
            '<select><option selected="">a</option><b></b><p><b>q</b>'
            "<button><selectedcontent>a</selectedcontent></button></p></select>",
            id="after-adoption-agency-move",
        ),
        pytest.param(
            "<select><option>a</option><button><selectedcontent><selectedcontent></selectedcontent></selectedcontent>"
            "</button></select>",
            "<select><option>a</option><button><selectedcontent>a<selectedcontent></selectedcontent></selectedcontent>"
            "</button></select>",
            id="inner-selectedcontent-disabled",
        ),
        pytest.param(
            "<select><option>a</option><template><button><selectedcontent></button></template></select>",
            "<select><option>a</option><template><button><selectedcontent></selectedcontent></button></template>"
            "</select>",
            id="template-content-is-not-the-select",
        ),
        pytest.param(
            "<select><svg><foreignObject><select><option>i</option><button><selectedcontent></button></select>"
            "</foreignObject></svg></select>",
            "<select><svg><foreignObject><select><option>i</option><button><selectedcontent></selectedcontent>"
            "</button></select></foreignObject></svg></select>",
            id="second-select-ancestor",
        ),
        pytest.param(
            "<select><button><selectedcontent></button></select>",
            "<select><button><selectedcontent></selectedcontent></button></select>",
            id="no-option",
        ),
        pytest.param(
            "<select><option disabled>a</option><button><selectedcontent></button></select>",
            '<select><option disabled="">a</option><button><selectedcontent></selectedcontent></button></select>',
            id="no-enabled-option",
        ),
        pytest.param(
            "<select multiple><option selected>a</option><button><selectedcontent></button></select>",
            '<select multiple=""><option selected="">a</option><button><selectedcontent></selectedcontent></button>'
            "</select>",
            id="multiple-select",
        ),
        pytest.param(
            "<select><option selected>a</option><button><selectedcontent disabled></button></select>",
            '<select><option selected="">a</option><button><selectedcontent disabled=""></selectedcontent></button>'
            "</select>",
            id="disabled-attribute",
        ),
    ],
)
def test_parse_fills_selectedcontent_inserted_after_the_option(markup: str, body: str) -> None:
    assert parse(markup).html == f"<html><head></head><body>{body}</body></html>"


def test_set_inner_html_fills_selectedcontent_inserted_after_the_option() -> None:
    host = parse("<div></div>").find("div")
    assert isinstance(host, Element)
    host.set_inner_html("<select><option>pick</option><button><selectedcontent></button></select>")
    assert host.inner_html == (
        "<select><option>pick</option><button><selectedcontent>pick</selectedcontent></button></select>"
    )
