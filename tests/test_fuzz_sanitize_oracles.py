from __future__ import annotations

import shutil
from typing import Final

import pytest
from fuzz.sanitize_oracles import (
    _NODE_DIR,  # ruff:ignore[import-private-name]  # the parse5 install the confirming re-parse runs from
    _confirm_with_parse5,  # ruff:ignore[import-private-name]  # the parse5 verdict on a finding
    _Finding,  # ruff:ignore[import-private-name]  # the finding record the verdict reads
    _mutation_detail,  # ruff:ignore[import-private-name]  # the html5lib comparison the mutation oracle runs
    _parse5_dumps,  # ruff:ignore[import-private-name]  # the parse5 dump the verdict compares
    _parse5_verdict,  # ruff:ignore[import-private-name]  # the confirmed or html5lib-only decision
)

from turbohtml import parse_fragment
from turbohtml.clean import Policy, sanitize_node

_TABLE_ROWS: Final[Policy] = Policy(tags=frozenset({"table", "tr", "td"}))
_TABLE_CELLS: Final[Policy] = Policy(tags=frozenset({"table", "td"}))
_SELECT_ROW: Final[Policy] = Policy(tags=frozenset({"table", "tr", "select", "option"}))
_NODE: Final[str] = shutil.which("node") or ""
_PARSE5_AVAILABLE: Final[bool] = bool(_NODE) and (_NODE_DIR / "node_modules" / "parse5").is_dir()


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


@pytest.mark.skipif(not _PARSE5_AVAILABLE, reason="node or the parse5 install under tools/bench/node is missing")
def test_parse5_confirmation_ignores_an_omitted_wrapper() -> None:
    finding = _Finding(
        oracle="mutation",
        signature="mutation: turbohtml <td> != html5lib <tbody>",
        markup="<table><td>a</td></table>",
        policy="Policy()",
        detail="turbohtml <td> != html5lib <tbody>",
    )
    [(confirmed, _)] = _confirm_with_parse5([(finding, _TABLE_CELLS)])
    assert confirmed.parse5 == "html5lib-only"


@pytest.mark.skipif(not _PARSE5_AVAILABLE, reason="node or the parse5 install under tools/bench/node is missing")
def test_parse5_verdict_stays_confirmed_beside_an_omitted_wrapper() -> None:
    tree = sanitize_node(parse_fragment("<table><td>a</td></table>", "div", positions=False), _TABLE_CELLS)
    [dump] = _parse5_dumps(
        [("<table><tbody><tr><td>b</td></tr></tbody></table>", _TABLE_CELLS)],
        _NODE,
    )
    assert _parse5_verdict(tree, _TABLE_CELLS, dump) == "confirmed"
