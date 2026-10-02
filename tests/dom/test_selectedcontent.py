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
