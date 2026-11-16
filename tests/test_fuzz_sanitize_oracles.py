from __future__ import annotations

from typing import Final

import pytest
from fuzz.sanitize_oracles import (
    _mutation_detail,  # ruff:ignore[import-private-name]  # the html5lib comparison the mutation oracle runs
    _parse5_requests,  # ruff:ignore[import-private-name]  # the parse5 request each output is sent as
    _parse5_verdict,  # ruff:ignore[import-private-name]  # the confirmed or html5lib-only decision
)

from turbohtml import parse_fragment
from turbohtml.clean import Policy, sanitize_node

_TABLE_ROWS: Final[Policy] = Policy(tags=frozenset({"table", "tr", "td"}))
_TABLE_CELLS: Final[Policy] = Policy(tags=frozenset({"table", "td"}))
_SELECT_ROW: Final[Policy] = Policy(tags=frozenset({"table", "tr", "select", "option"}))


@pytest.mark.parametrize(
    ("markup", "policy"),
    [
        pytest.param("&#13", Policy(), id="carriage-return-reference"),
        pytest.param("a&#13;b", Policy(), id="carriage-return-in-text"),
        pytest.param("<table><tr>", Policy(tags=frozenset({"table", "tr"})), id="omitted-tbody"),
        pytest.param("<table><td>", _TABLE_CELLS, id="omitted-tbody-and-tr"),
        pytest.param("<table><tr><td>a</td></tr><tr><td>b</td></tr></table>", _TABLE_ROWS, id="adjacent-rows-merge"),
        pytest.param("<table><td>a</td> <td>b</td></table>", _TABLE_ROWS, id="text-after-omitted-cell"),
        pytest.param("<table><tr><select><option>x</option></select></tr></table>", _SELECT_ROW, id="select-branch"),
        pytest.param("<template><p>x</p></template>", Policy(tags=frozenset({"template", "p"})), id="template"),
    ],
)
def test_reparse_matches_the_sanitized_tree(markup: str, policy: Policy) -> None:
    tree = sanitize_node(parse_fragment(markup, "div", positions=False), policy)
    assert _mutation_detail(tree, tree.inner_html, policy) is None


@pytest.mark.parametrize(
    ("markup", "serialized", "policy"),
    [
        pytest.param("<table><td>a</td></table>", "<table><td>b</td></table>", _TABLE_CELLS, id="altered-text"),
        pytest.param(
            "<table><tr><td>a</td></tr></table>",
            '<table><tbody id="x"><tr><td>a</td></tr></tbody></table>',
            _TABLE_ROWS,
            id="attributed-wrapper",
        ),
        pytest.param("&#13;", "\r", Policy(), id="raw-carriage-return"),
    ],
)
def test_divergence_from_the_sanitized_tree_is_reported(markup: str, serialized: str, policy: Policy) -> None:
    tree = sanitize_node(parse_fragment(markup, "div", positions=False), policy)
    assert _mutation_detail(tree, serialized, policy) is not None


@pytest.mark.parametrize(
    ("policy", "omit"),
    [
        pytest.param(_TABLE_CELLS, ["colgroup", "tbody", "tr"], id="every-wrapper-omitted"),
        pytest.param(Policy(tags=frozenset({"table", "tbody", "tr", "td"})), ["colgroup"], id="allowed-wrappers-kept"),
        pytest.param(
            Policy(tags=frozenset({"table", "tbody", "tr", "td", "colgroup"}), allow_html=False),
            ["colgroup", "tbody", "tr"],
            id="html-disallowed",
        ),
    ],
)
def test_parse5_request_names_the_omitted_wrappers(policy: Policy, omit: list[str]) -> None:
    assert _parse5_requests([("<table></table>", policy)]) == [{"markup": "<table></table>", "omit": omit}]


@pytest.mark.parametrize(
    ("dump", "verdict"),
    [
        # the runner's dump of the output with its omitted wrapper removed, as the mutation comparison reads it
        pytest.param('| <table>\n|   <td>\n|     "a"', "html5lib-only", id="omitted-wrapper-only"),
        # the same shape with the cell text changed: a divergence beside the omitted wrapper
        pytest.param('| <table>\n|   <td>\n|     "b"', "confirmed", id="divergence-beside-omitted-wrapper"),
        pytest.param("| <table>\n|   <td>", "confirmed", id="dropped-text"),
    ],
)
def test_parse5_verdict_compares_the_unwrapped_dump(dump: str, verdict: str) -> None:
    tree = sanitize_node(parse_fragment("<table><td>a</td></table>", "div", positions=False), _TABLE_CELLS)
    assert _parse5_verdict(tree, _TABLE_CELLS, dump) == verdict
