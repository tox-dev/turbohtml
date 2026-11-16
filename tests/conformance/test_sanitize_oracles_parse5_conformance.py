from __future__ import annotations

import shutil
from typing import Final

from fuzz.sanitize_oracles import (
    _confirm_with_parse5,  # ruff:ignore[import-private-name]  # the parse5 verdict the oracle records on a finding
    _Finding,  # ruff:ignore[import-private-name]  # the finding record the verdict reads
    _parse5_dumps,  # ruff:ignore[import-private-name]  # the dump the committed parse5 runner returns
    _parse5_verdict,  # ruff:ignore[import-private-name]  # the confirmed or html5lib-only decision
)

from turbohtml import parse_fragment
from turbohtml.clean import Policy, sanitize_node

_TABLE_CELLS: Final[Policy] = Policy(tags=frozenset({"table", "td"}))


def test_parse5_runner_drops_an_omitted_wrapper_from_its_dump() -> None:
    [dump] = _parse5_dumps([("<table><tbody><tr><td>a</td></tr></tbody></table>", _TABLE_CELLS)], _node())
    assert dump == '| <table>\n|   <td>\n|     "a"'


def _node() -> str:
    node = shutil.which("node")
    assert node is not None, "the conformance env installs node and the parse5 oracle under tools/bench/node"
    return node


def test_parse5_verdict_is_html5lib_only_for_an_omitted_wrapper() -> None:
    finding = _Finding(
        oracle="mutation",
        signature="mutation: turbohtml <td> != html5lib <tbody>",
        markup="<table><td>a</td></table>",
        policy="Policy()",
        detail="turbohtml <td> != html5lib <tbody>",
    )
    [(confirmed, _)] = _confirm_with_parse5([(finding, _TABLE_CELLS)])
    assert confirmed.parse5 == "html5lib-only"


def test_parse5_verdict_is_confirmed_for_a_divergence_beside_an_omitted_wrapper() -> None:
    tree = sanitize_node(parse_fragment("<table><td>a</td></table>", "div", positions=False), _TABLE_CELLS)
    [dump] = _parse5_dumps([("<table><tbody><tr><td>b</td></tr></tbody></table>", _TABLE_CELLS)], _node())
    assert dump == '| <table>\n|   <td>\n|     "b"'
    assert _parse5_verdict(tree, _TABLE_CELLS, dump) == "confirmed"
