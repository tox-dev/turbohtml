from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Final, cast

import pytest
from fuzz.dom_lifecycle import (
    LifecycleStep,
    UnsupportedDomLifecycleError,
    dom_lifecycle_check,
    dom_lifecycle_controls,
    dom_lifecycle_generate,
    dom_lifecycle_race,
    dom_lifecycle_seeds,
    run_dom_lifecycle,
)
from fuzz.dom_program import (
    UnsupportedDomProgramError,
    dom_program_check,
    dom_program_controls,
    dom_program_generate,
    dom_program_seeds,
    run_dom_program,
)
from fuzz.html_foreign_oracles import (
    UnsupportedHtmlForeignCaseError,
    html_foreign_check,
    html_foreign_controls,
    html_foreign_generate,
    html_foreign_seeds,
)
from fuzz.html_grammar_oracles import (
    UnsupportedHtmlSiblingCaseError,
    html_sibling_check,
    html_sibling_controls,
    html_sibling_generate,
    html_sibling_seeds,
)
from fuzz.html_list_oracles import (
    UnsupportedHtmlListCaseError,
    html_list_check,
    html_list_controls,
    html_list_generate,
    html_list_seeds,
)
from fuzz.html_table_oracles import (
    UnsupportedHtmlTableCaseError,
    html_table_check,
    html_table_controls,
    html_table_generate,
    html_table_seeds,
)
from fuzz.iterator_oracles import (
    UnsupportedIteratorCaseError,
    iterator_sequence_check,
    iterator_sequence_controls,
    iterator_sequence_generate,
    iterator_sequence_seeds,
)
from fuzz.observer_oracles import (
    UnsupportedObserverCaseError,
    observer_sequence_check,
    observer_sequence_controls,
    observer_sequence_generate,
    observer_sequence_seeds,
)
from fuzz.round_trip_oracles import ORACLES, Floor, OutOfScopeError, main

from turbohtml import Element, NodeFilter, NodeIterator, Text, parse, parse_fragment
from turbohtml._html import _tree_verify

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

    from turbohtml import Node

_BROKEN: Final = ((LifecycleStep(0, "element", "ok", (0, 0, 1, 0)),), ())


def _steps(*instructions: tuple[int, int, int, int]) -> tuple[LifecycleStep, ...]:
    return run_dom_lifecycle(bytes(byte for instruction in instructions for byte in instruction))[0]


@pytest.mark.parametrize(
    "text", [pytest.param(seed, id=f"{index:03d}") for index, seed in enumerate(dom_lifecycle_seeds())]
)
def test_dom_lifecycle_seed_holds_and_reports_a_violation(text: str) -> None:
    assert (dom_lifecycle_check(text), dom_lifecycle_check(text, lambda _program: _BROKEN)) == (
        None,
        "tree invariant broken after element",
    )


def test_dom_lifecycle_generated_cases_hold() -> None:
    rng: Final = random.Random(1017)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - a fixed seed reproduces failing programs
    assert all(dom_lifecycle_check(dom_lifecycle_generate(rng)) is None for _ in range(100))


@pytest.mark.parametrize(
    ("report", "detail"),
    [
        pytest.param((0, 0, 0, 0), None, id="clean"),
        pytest.param((0, 0, 1, 0), "tree invariant broken after element", id="iterator"),
    ],
)
def test_dom_lifecycle_verifies_after_every_step(
    mocker: MockerFixture, report: tuple[int, ...], detail: str | None
) -> None:
    mocker.patch("fuzz.dom_lifecycle._tree_verify", return_value=report)
    assert dom_lifecycle_check(dom_lifecycle_seeds()[0]) == detail


def test_dom_lifecycle_negative_controls_fire() -> None:
    assert dom_lifecycle_controls() == {"missing violation": True, "replay drift": True}


@pytest.mark.parametrize(
    "text",
    [pytest.param("xx", id="not-hex"), pytest.param("0", id="odd-length"), pytest.param("00" * 1025, id="too-long")],
)
def test_dom_lifecycle_rejects_unsupported_encoding(text: str) -> None:
    with pytest.raises(UnsupportedDomLifecycleError):
        dom_lifecycle_check(text)
    with pytest.raises(OutOfScopeError):
        ORACLES["dom-lifecycle"].check(text)


def test_dom_lifecycle_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "dom-lifecycle", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "'compared': 240" in capsys.readouterr().out) == (0, [], True)


def test_dom_lifecycle_cli_emits_violation(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["dom-lifecycle"],
        check=partial(dom_lifecycle_check, run=lambda _program: _BROKEN),
        seeds=lambda: [dom_lifecycle_seeds()[0]],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"dom-lifecycle": oracle}, clear=True)
    status: Final = main(["--oracle", "dom-lifecycle", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, len(list(tmp_path.glob("*.json")))) == (1, 1)


def test_dom_lifecycle_empty_program_keeps_empty_registers() -> None:
    assert run_dom_lifecycle(b"") == ((), ("",) * 8)


def test_dom_lifecycle_partial_instruction_is_ignored() -> None:
    assert run_dom_lifecycle(bytes((0, 0, 0, 0, 1, 2))) == run_dom_lifecycle(bytes((0, 0, 0, 0)))


def test_dom_lifecycle_limits_executed_steps() -> None:
    assert len(_steps(*[(1, 0, 0, 0)] * 300)) == 256


def test_dom_lifecycle_limits_created_nodes() -> None:
    assert [step.outcome for step in _steps(*[(0, 0, 0, 0)] * 50)][47:] == ["ok", "cap", "cap"]


def test_dom_lifecycle_bounds_callback_nesting() -> None:
    steps: Final = _steps((18, 0, 0, 3), (18, 0, 0, 2), (18, 0, 0, 1), (0, 0, 0, 0))
    assert max(step.depth for step in steps) == 2


@pytest.mark.parametrize(
    ("instructions", "outcome"),
    [
        pytest.param([(27, 0, 0, 0)], "abort", id="top-level"),
        pytest.param([(18, 0, 0, 1), (27, 0, 0, 0)], "_UnwindError", id="from-callback"),
    ],
)
def test_dom_lifecycle_raise_unwinds_to_the_enclosing_operation(
    instructions: list[tuple[int, int, int, int]], outcome: str
) -> None:
    assert _steps(*instructions)[-1].outcome == outcome


@pytest.mark.parametrize(
    ("instructions", "outcome"),
    [
        pytest.param([(0, 0, 0, 0), (0, 1, 0, 0), (4, 0, 1, 0), (6, 1, 0, 0)], "ok", id="extract-retained"),
        pytest.param([(0, 0, 0, 0), (0, 1, 0, 0), (4, 0, 1, 0), (8, 0, 0, 0)], "ValueError", id="unwrap-root"),
        pytest.param([(1, 0, 0, 0), (4, 0, 0, 0)], "empty", id="append-to-text"),
        pytest.param([(19, 0, 0, 0), (21, 0, 0, 0), (14, 0, 0, 0)], "ok", id="token-iterator-step"),
    ],
)
def test_dom_lifecycle_operation_outcome(instructions: list[tuple[int, int, int, int]], outcome: str) -> None:
    assert _steps(*instructions)[-1].outcome == outcome


@pytest.mark.parametrize(
    ("removal", "operation"),
    [
        pytest.param((28, 0, 0, 0), "normalize", id="normalize"),
        pytest.param((29, 0, 5, 0), "range-extract", id="range"),
    ],
)
def test_dom_lifecycle_moves_an_iterator_out_of_removed_text(
    removal: tuple[int, int, int, int], operation: str
) -> None:
    # p holds the texts "alpha" and "beta gamma"; an iterator over p steps to the second text, which the removal drops
    steps: Final = _steps(
        (0, 0, 0, 0),
        (1, 1, 1, 0),
        (1, 2, 2, 0),
        (4, 0, 1, 0),
        (4, 0, 2, 0),
        (12, 3, 0, 0),
        *[(14, 3, 1, 4)] * 3,
        removal,
    )
    assert steps[-1] == LifecycleStep(0, operation, "ok", (0, 0, 0, 0))


def test_tree_verify_keeps_an_iterator_reference_inside_its_root_across_normalize() -> None:
    root: Final = Element("div", children=[Text("x"), Text("y")])
    iterator: Final = NodeIterator(root)
    for _ in range(3):
        iterator.next_node()
    root.normalize()  # merging removes the reference, so the removal steps move it back to the merged text
    assert (_tree_verify(root), iterator.reference_node) == ((0, 0, 0, 0), root.children[0])


def test_tree_verify_keeps_a_filter_candidate_inside_its_root_across_normalize() -> None:
    merged: Final = Text("y")
    root: Final = Element("div", children=[Text("x"), merged])
    reports: Final[list[tuple[int, int, int, int]]] = []

    def record(node: Node) -> int:
        if node == merged:
            # merging removes the candidate, so the removal steps move it back to the merged text
            root.normalize()
            reports.append(_tree_verify(root))
        return NodeFilter.FILTER_SKIP

    assert (list(NodeIterator(root, filter=record)), reports) == ([], [(0, 0, 0, 0)])


def test_tree_verify_finds_nodes_in_every_arena_block() -> None:
    document: Final = parse("<p>" * 3_000)  # enough nodes to fill several arena blocks
    assert _tree_verify(document) == (0, 0, 0, 0)


def test_tree_verify_accepts_a_current_css_path_id_map() -> None:
    document: Final = parse('<p id="a"><b></b></p>')
    document.select("b")[0].css_path()
    assert _tree_verify(document) == (0, 0, 0, 0)


def test_tree_verify_rejects_a_non_node() -> None:
    with pytest.raises(TypeError, match="expected a turbohtml element"):
        _tree_verify(object())  # ty: ignore[invalid-argument-type] - the hook validates its argument at runtime


_SEEDS: Final = dom_lifecycle_seeds()[::8]  # the seeds run each opcode over 8 slots


def test_dom_lifecycle_race_matches_the_serial_run() -> None:
    assert dom_lifecycle_race(_SEEDS) == [run_dom_lifecycle(bytes.fromhex(seed))[0] for seed in _SEEDS]


def test_dom_lifecycle_race_on_shared_trees_keeps_every_tree_intact() -> None:
    traces: Final = dom_lifecycle_race(_SEEDS, shared=True)
    assert {step.violations for trace in traces for step in trace} == {(0, 0, 0, 0)}


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        pytest.param(bytes((0, 1, 0, 0)), "<main><p></p><span></span></main>", id="create"),
        pytest.param(bytes((1, 0, 0, 1)), "<main><span></span></main>", id="tag"),
        pytest.param(bytes((2, 0, 1, 0)), "<main><p>alpha</p></main>", id="text"),
        pytest.param(bytes((3, 0, 2, 0)), '<main><p data-x="beta"></p></main>', id="attribute"),
        pytest.param(bytes((3, 0, 2, 0, 4, 0, 0, 0)), "<main><p></p></main>", id="remove-attribute"),
        pytest.param(bytes((0, 1, 0, 0, 5, 0, 0, 0)), "<main><span></span><p></p></main>", id="append-reorder"),
        pytest.param(bytes((0, 1, 0, 0, 6, 1, 0, 0)), "<main><span></span><p></p></main>", id="insert-reorder"),
        pytest.param(bytes((7, 0, 0, 0)), "<main><p></p></main>", id="observe"),
        pytest.param(bytes((8, 0, 25, 2, 2, 0, 0, 0)), "<main><p>zz</p></main>", id="string-register"),
        pytest.param(bytes((9, 0, 3, 0, 1, 0, 0, 0)), "<main><div></div></main>", id="integer-register"),
    ],
)
def test_dom_program_public_opcode_result(suffix: bytes, expected: str) -> None:
    program: Final = bytes((0, 0, 0, 0)) + suffix
    assert json.loads(run_dom_program(program)[-1])[0] == expected


@pytest.mark.parametrize("opcode", range(1, 8))
def test_dom_program_unoccupied_element_registers(opcode: int) -> None:
    program: Final = bytes((opcode, 0, 0, 0))
    assert json.loads(run_dom_program(program)[-1]) == ["<main></main>", []]


@pytest.mark.parametrize("extra", [b"\x01", b"\x01\x02", b"\x01\x02\x03"])
def test_dom_program_partial_instruction_is_ignored(extra: bytes) -> None:
    program: Final = bytes((0, 0, 0, 0))
    assert run_dom_program(program + extra) == run_dom_program(program)


def test_dom_program_limits_created_nodes() -> None:
    result: Final = json.loads(run_dom_program(bytes((0, 0, 0, 0)) * 64)[-1])
    assert len(result[1]) == 16


def test_dom_program_limits_executed_steps() -> None:
    assert len(run_dom_program(bytes((0, 0, 0, 0)) * 300)) == 65


@pytest.mark.parametrize("text", ["xx", "0", "00" * 1025])
def test_dom_program_rejects_unsupported_encoding(text: str) -> None:
    with pytest.raises(UnsupportedDomProgramError):
        dom_program_check(text)
    with pytest.raises(OutOfScopeError):
        ORACLES["dom-program"].check(text)


def test_dom_program_empty_program_preserves_root() -> None:
    assert run_dom_program(b"") == ('["<main></main>", []]',)


def test_dom_program_generated_cases_match_model() -> None:
    rng: Final = random.Random(1017)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - a fixed seed reproduces failing byte programs
    assert all(dom_program_check(dom_program_generate(rng)) is None for _ in range(100))


@pytest.mark.parametrize("text", dom_program_seeds())
def test_dom_program_seed_and_wrong_result_control(text: str) -> None:
    assert (dom_program_check(text), dom_program_check(text, lambda _program: ())) == (
        None,
        "DOM program differs from its value model",
    )


def test_dom_program_negative_control_fires() -> None:
    assert dom_program_controls() == {"missing operation": True}


def test_dom_program_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "dom-program", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "100" in capsys.readouterr().out) == (0, [], True)


def test_dom_program_cli_emits_wrong_result(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["dom-program"],
        check=partial(dom_program_check, run=lambda _program: ()),
        seeds=lambda: [dom_program_seeds()[1]],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"dom-program": oracle}, clear=True)
    status: Final = main(["--oracle", "dom-program", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "DOM program differs from its value model",
        1,
    )


def test_dom_program_cli_rejects_vacuous_input(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        ORACLES,
        {"dom-program": replace(ORACLES["dom-program"], seeds=lambda: ["xx"], floor=Floor(1, 1))},
        clear=True,
    )
    assert main(["--oracle", "dom-program", "--minutes", "0", "--crash-dir", str(tmp_path)]) == 2


def test_dom_program_checker_observes_node_cap() -> None:
    assert dom_program_check((bytes((0, 0, 0, 0)) * 64).hex()) is None


def test_dom_program_checker_preserves_empty_state() -> None:
    assert dom_program_check("") is None


@pytest.mark.parametrize("opcode", range(1, 8))
def test_dom_program_checker_preserves_unoccupied_registers(opcode: int) -> None:
    assert dom_program_check(bytes((opcode, 0, 0, 0)).hex()) is None


@pytest.mark.parametrize("opcode", [5, 6], ids=["append", "insert"])
def test_dom_program_reorders_identical_nodes(opcode: int) -> None:
    program: Final = bytes((0, 0, 0, 0, 0, 0, 0, 0, opcode, 0, 0, 2))
    result: Final = json.loads(run_dom_program(program)[-1])
    assert (result[0], [row[-1] for row in result[1]]) == ("<main><p></p><p></p></main>", [1, 0])


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param(
            "td:implied:1:plain",
            '<table id="z"><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></table>',
            [
                ("table", "z", 0),
                ("tbody", "", 1),
                ("tr", "x", 2),
                ("td", "x", 3),
                ("#text", "MiXeD", 4),
                ("td", "y", 3),
                ("#text", "MiXeD", 6),
            ],
            id="inserted-row-group",
        ),
        pytest.param(
            "th:explicit:1:numeric",
            '<table id="z"><tbody><tr id="x"><th id="x">&#65;&#x42;&#67;</th>'
            '<th id="y">&#65;&#x42;&#67;</th></tr></tbody></table>',
            [
                ("table", "z", 0),
                ("tbody", "", 1),
                ("tr", "x", 2),
                ("th", "x", 3),
                ("#text", "ABC", 4),
                ("th", "y", 3),
                ("#text", "ABC", 6),
            ],
            id="explicit-numeric-cells",
        ),
        pytest.param(
            "td:explicit:2:named",
            '<table id="z"><tbody><tr id="x"><td id="x">&amp;&lt;&gt;</td><td id="y">&amp;&lt;&gt;</td></tr>'
            '<tr id="y"><td id="x">&amp;&lt;&gt;</td><td id="y">&amp;&lt;&gt;</td></tr></tbody></table>',
            [
                ("table", "z", 0),
                ("tbody", "", 1),
                ("tr", "x", 2),
                ("td", "x", 3),
                ("#text", "&<>", 4),
                ("td", "y", 3),
                ("#text", "&<>", 6),
                ("tr", "y", 2),
                ("td", "x", 8),
                ("#text", "&<>", 9),
                ("td", "y", 8),
                ("#text", "&<>", 11),
            ],
            id="explicit-two-rows-named",
        ),
        pytest.param(
            "th:implied:2:plain",
            '<table id="z"><tr id="x"><th id="x">MiXeD</th><th id="y">MiXeD</th></tr>'
            '<tr id="y"><th id="x">MiXeD</th><th id="y">MiXeD</th></tr></table>',
            [
                ("table", "z", 0),
                ("tbody", "", 1),
                ("tr", "x", 2),
                ("th", "x", 3),
                ("#text", "MiXeD", 4),
                ("th", "y", 3),
                ("#text", "MiXeD", 6),
                ("tr", "y", 2),
                ("th", "x", 8),
                ("#text", "MiXeD", 9),
                ("th", "y", 8),
                ("#text", "MiXeD", 11),
            ],
            id="inserted-group-two-rows",
        ),
    ],
)
def test_html_table_literal_trees(case: str, source: str, expected: list[tuple[str, str, int]]) -> None:
    root: Final = parse_fragment(source)
    nodes: Final = [root, *root.descendants]
    assert (
        [
            (
                node.tag if isinstance(node, Element) else "#text",
                cast("str", node.attrs.get("id", "")) if isinstance(node, Element) else cast("Text", node).data,
                nodes.index(cast("Element", node.parent)),
            )
            for node in nodes[1:]
        ],
        {node.namespace.value for node in root.iter_elements()},
        html_table_check(case),
        html_table_check(case, lambda _node: source),
        html_table_check(case, lambda _node: ""),
    ) == (expected, {"html"}, None, None, "serialized table tree differs from literals")


def test_html_table_checks_first_parse() -> None:
    source: Final = '<table id="z"><thead><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></thead></table>'
    assert (
        html_table_check("td:implied:1:plain", read=lambda _source: parse_fragment(source))
        == "parsed table tree differs from literals"
    )


def test_html_table_checks_repeat_formatting() -> None:
    source: Final = '<table id="z"><tbody><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></tbody></table>'
    outputs: Final = iter([source, source.replace('"', "'")])
    assert (
        html_table_check("td:implied:1:plain", lambda _node: next(outputs)) == "table serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            '<table id="z"><thead><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr>'
            '<tr id="y"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></thead></table>',
            id="changed-row-group",
        ),
        pytest.param(
            '<table id="z"><tbody id="x"><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr>'
            '<tr id="y"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></tbody></table>',
            id="unexpected-group-attribute",
        ),
        pytest.param(
            '<table id="z"><tbody><tr id="x"><td id="x">MiXeD</td></tr>'
            '<tr id="y"><td id="y">MiXeD</td><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></tbody></table>',
            id="cell-moved-to-following-row",
        ),
        pytest.param(
            '<table id="z"><tbody><tr id="x"><th id="x">MiXeD</th><th id="y">MiXeD</th></tr>'
            '<tr id="y"><th id="x">MiXeD</th><th id="y">MiXeD</th></tr></tbody></table>',
            id="changed-cell-type",
        ),
        pytest.param(
            '<table id="z"><tbody><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></tbody></table>',
            id="dropped-row",
        ),
        pytest.param(
            '<table id="z"><tbody><tr id="x"><td id="x">WRONG</td><td id="y">MiXeD</td></tr>'
            '<tr id="y"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></tbody></table>',
            id="changed-text",
        ),
        pytest.param(
            '<table id="z"><tbody><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr>'
            '<tr id="y"><td id="x">MiXeD</td><td id="x">MiXeD</td></tr></tbody></table>',
            id="changed-cell-id",
        ),
        pytest.param("<!--unexpected-->", id="unexpected-comment"),
        pytest.param("", id="lost-tree"),
    ],
)
def test_html_table_rejects_changed_tree(source: str) -> None:
    assert (
        html_table_check("td:explicit:2:plain", lambda _node: source) == "serialized table tree differs from literals"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("div:explicit:1:plain", id="cell-pool"),
        pytest.param("td:raw:1:plain", id="group-pool"),
        pytest.param("td:explicit:0:plain", id="empty-table"),
        pytest.param("td:explicit:3:plain", id="row-budget"),
        pytest.param("td:explicit:1:raw", id="terminal-pool"),
    ],
)
def test_html_table_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedHtmlTableCaseError):
        html_table_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["html-table-grammar"].check(case)


def test_html_table_production_sweep_and_bounds() -> None:
    seeds: Final = html_table_seeds()
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    generated: Final = [html_table_generate(rng) for _ in range(100)]
    assert (
        len(seeds),
        len(set(seeds)),
        Counter(case.split(":")[0] for case in seeds),
        Counter(case.split(":")[1] for case in seeds),
        Counter(case.split(":")[2] for case in seeds),
        Counter(case.split(":")[3] for case in seeds),
        set(generated) <= set(seeds),
        all(html_table_check(case) is None for case in [*seeds, *generated]),
        html_table_controls(),
    ) == (
        24,
        24,
        {"td": 12, "th": 12},
        {"explicit": 12, "implied": 12},
        {"1": 12, "2": 12},
        {"plain": 8, "named": 8, "numeric": 8},
        True,
        True,
        {"changed row group": True, "dropped row": True, "encoded text": True},
    )


def test_html_table_generated_trees_obey_bounds() -> None:
    bounds: Final[set[tuple[int, int, int]]] = set()
    identifiers: Final[set[str]] = set()

    def capture(source: str) -> Element:
        root: Final = parse_fragment(source)
        elements: Final = [node for node in root.descendants if isinstance(node, Element)]
        bounds.add((
            len(elements),
            sum(isinstance(node, Text) for node in root.descendants),
            max(sum(isinstance(ancestor, Element) for ancestor in element.ancestors) for element in elements),
        ))
        identifiers.update(
            cast("str", identifier) for element in elements if (identifier := element.attrs.get("id")) is not None
        )
        return root

    assert (all(html_table_check(case, read=capture) is None for case in html_table_seeds()), bounds, identifiers) == (
        True,
        {(5, 2, 4), (8, 4, 4)},
        {"x", "y", "z"},
    )


def test_html_table_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "html-table-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "24" in capsys.readouterr().out) == (0, [], True)


def test_html_table_cli_emits_group_change(mocker: MockerFixture, tmp_path: Path) -> None:
    source: Final = '<table id="z"><thead><tr id="x"><td id="x">MiXeD</td><td id="y">MiXeD</td></tr></thead></table>'
    oracle: Final = replace(
        ORACLES["html-table-grammar"],
        check=partial(html_table_check, serialize=lambda _node: source),
        seeds=lambda: ["td:implied:1:plain"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"html-table-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-table-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized table tree differs from literals",
        1,
    )


def test_html_table_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["html-table-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"html-table-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-table-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param(
            "ul:flat:explicit:plain",
            '<ul id="z"><li id="x">MiXeD</li><li id="y">MiXeD</li></ul>',
            [("ul", "z", 0), ("li", "x", 1), ("#text", "MiXeD", 2), ("li", "y", 1), ("#text", "MiXeD", 4)],
            id="flat-explicit",
        ),
        pytest.param(
            "ol:flat:implied:numeric",
            '<ol id="z"><li id="x">&#65;&#x42;&#67;<li id="y">&#65;&#x42;&#67;</ol>',
            [("ol", "z", 0), ("li", "x", 1), ("#text", "ABC", 2), ("li", "y", 1), ("#text", "ABC", 4)],
            id="flat-implied-numeric",
        ),
        pytest.param(
            "ul:nested:explicit:named",
            '<ul id="z"><li id="x">&amp;&lt;&gt;<ul id="z"><li id="x">&amp;&lt;&gt;</li>'
            '<li id="y">&amp;&lt;&gt;</li></ul></li><li id="y">&amp;&lt;&gt;</li></ul>',
            [
                ("ul", "z", 0),
                ("li", "x", 1),
                ("#text", "&<>", 2),
                ("ul", "z", 2),
                ("li", "x", 4),
                ("#text", "&<>", 5),
                ("li", "y", 4),
                ("#text", "&<>", 7),
                ("li", "y", 1),
                ("#text", "&<>", 9),
            ],
            id="nested-explicit-named",
        ),
        pytest.param(
            "ol:nested:implied:plain",
            '<ol id="z"><li id="x">MiXeD<ol id="z"><li id="x">MiXeD<li id="y">MiXeD</ol><li id="y">MiXeD</ol>',
            [
                ("ol", "z", 0),
                ("li", "x", 1),
                ("#text", "MiXeD", 2),
                ("ol", "z", 2),
                ("li", "x", 4),
                ("#text", "MiXeD", 5),
                ("li", "y", 4),
                ("#text", "MiXeD", 7),
                ("li", "y", 1),
                ("#text", "MiXeD", 9),
            ],
            id="nested-implied",
        ),
    ],
)
def test_html_list_literal_trees(case: str, source: str, expected: list[tuple[str, str, int]]) -> None:
    root: Final = parse_fragment(source)
    nodes: Final = [root, *root.descendants]
    assert (
        [
            (
                node.tag if isinstance(node, Element) else "#text",
                node.attrs["id"] if isinstance(node, Element) else cast("Text", node).data,
                nodes.index(cast("Element", node.parent)),
            )
            for node in nodes[1:]
        ],
        {node.namespace.value for node in root.iter_elements()},
        html_list_check(case),
        html_list_check(case, lambda _node: source),
        html_list_check(case, lambda _node: ""),
    ) == (expected, {"html"}, None, None, "serialized list tree differs from literals")


def test_html_list_checks_first_parse() -> None:
    assert (
        html_list_check("ul:nested:implied:plain", read=lambda _source: parse_fragment("<ul></ul>"))
        == "parsed list tree differs from literals"
    )


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            '<ul id="z"><li id="x">MiXeD<ul id="z"><li id="x">MiXeD</li></ul></li>'
            '<li id="y">MiXeD</li><li id="y">MiXeD</li></ul>',
            id="inner-item-moved-to-outer-list",
        ),
        pytest.param(
            '<ul id="z"><li id="x">MiXeD<ul id="z"><li id="x">MiXeD</li>'
            '<li id="y">MiXeD</li><li id="y">MiXeD</li></ul></li></ul>',
            id="outer-item-moved-to-inner-list",
        ),
        pytest.param('<ul id="z"><li id="x">MiXeD</li></ul>', id="lost-nested-siblings"),
        pytest.param(
            '<ul id="z"><li id="x">WRONG<ul id="z"><li id="x">MiXeD</li>'
            '<li id="y">MiXeD</li></ul></li><li id="y">MiXeD</li></ul>',
            id="changed-text",
        ),
        pytest.param(
            '<ul id="z"><li id="x">MiXeD<ul id="z"><li id="x">MiXeD</li>'
            '<li id="y">MiXeD</li></ul></li><li id="x">MiXeD</li></ul>',
            id="changed-id",
        ),
        pytest.param('<svg><ul id="z"></ul></svg>', id="changed-namespace"),
        pytest.param("<!--unexpected-->", id="unexpected-comment"),
        pytest.param("", id="lost-tree"),
    ],
)
def test_html_list_rejects_changed_tree(source: str) -> None:
    assert (
        html_list_check("ul:nested:explicit:plain", lambda _node: source)
        == "serialized list tree differs from literals"
    )


def test_html_list_checks_repeat_formatting() -> None:
    source: Final = '<ul id="z"><li id="x">MiXeD</li><li id="y">MiXeD</li></ul>'
    outputs: Final = iter([source, source.replace('"', "'")])
    assert (
        html_list_check("ul:flat:explicit:plain", lambda _node: next(outputs)) == "list serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("menu:flat:explicit:plain", id="tag-pool"),
        pytest.param("ul:deep:explicit:plain", id="depth-bound"),
        pytest.param("ul:flat:missing:plain", id="ending-pool"),
        pytest.param("ul:flat:explicit:raw", id="terminal-pool"),
        pytest.param("ul:flat:explicit:plain:extra", id="trailing-program"),
    ],
)
def test_html_list_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedHtmlListCaseError):
        html_list_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["html-list-grammar"].check(case)


def test_html_list_production_sweep_and_bounds() -> None:
    seeds: Final = html_list_seeds()
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    generated: Final = [html_list_generate(rng) for _ in range(100)]
    assert (
        len(seeds),
        len(set(seeds)),
        Counter(case.split(":")[0] for case in seeds),
        Counter(case.split(":")[1] for case in seeds),
        Counter(case.split(":")[2] for case in seeds),
        Counter(case.split(":")[3] for case in seeds),
        set(generated) <= set(seeds),
        all(html_list_check(case) is None for case in [*seeds, *generated]),
        html_list_controls(),
    ) == (
        24,
        24,
        {"ul": 12, "ol": 12},
        {"flat": 12, "nested": 12},
        {"explicit": 12, "implied": 12},
        {"plain": 8, "named": 8, "numeric": 8},
        True,
        True,
        {"inner item became outer sibling": True, "dropped sibling": True, "encoded text": True},
    )


def test_html_list_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "html-list-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "24" in capsys.readouterr().out) == (0, [], True)


def test_html_list_cli_emits_attachment_loss(mocker: MockerFixture, tmp_path: Path) -> None:
    source: Final = (
        '<ul id="z"><li id="x">MiXeD<ul id="z"><li id="x">MiXeD</li></ul></li>'
        '<li id="y">MiXeD</li><li id="y">MiXeD</li></ul>'
    )
    oracle: Final = replace(
        ORACLES["html-list-grammar"],
        check=partial(html_list_check, serialize=lambda _node: source),
        seeds=lambda: ["ul:nested:explicit:plain"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"html-list-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-list-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized list tree differs from literals",
        1,
    )


def test_html_list_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["html-list-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"html-list-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-list-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


def test_html_list_generated_trees_obey_bounds() -> None:
    bounds: Final[set[tuple[int, int, int]]] = set()
    identifiers: Final[set[str]] = set()

    def capture(source: str) -> Element:
        root: Final = parse_fragment(source)
        elements: Final = [node for node in root.descendants if isinstance(node, Element)]
        bounds.add((
            len(elements),
            sum(isinstance(node, Text) for node in root.descendants),
            max(sum(isinstance(ancestor, Element) for ancestor in element.ancestors) for element in elements),
        ))
        identifiers.update(cast("str", element.attrs["id"]) for element in elements)
        return root

    assert (all(html_list_check(case, read=capture) is None for case in html_list_seeds()), bounds, identifiers) == (
        True,
        {(3, 2, 2), (6, 4, 4)},
        {"x", "y", "z"},
    )


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "boundary", "decoded"),
    [
        pytest.param(
            "foreignObject:x:double:plain",
            '<svg><foreignObject><a id="x">MiXeD</a></foreignObject><g id="y"></g></svg>',
            "foreignObject",
            "MiXeD",
            id="foreign-object",
        ),
        pytest.param(
            "desc:y:single:named",
            "<svg><desc><a id='y'>&amp;&lt;&gt;</a></desc><g id='x'></g></svg>",
            "desc",
            "&<>",
            id="description",
        ),
        pytest.param(
            "title:x:double:numeric",
            '<svg><title><a id="x">&#65;&#x42;&#67;</a></title><g id="y"></g></svg>',
            "title",
            "ABC",
            id="title",
        ),
    ],
)
def test_html_foreign_literal_tree(case: str, source: str, boundary: str, decoded: str) -> None:
    root: Final = parse_fragment(source)
    assert (
        [
            (node.namespace.value, node.tag, cast("Element", node.parent).tag)
            for node in root.iter_elements()
            if node is not root
        ],
        next(root.iter_elements("a")).text,
        html_foreign_check(case),
        ORACLES["html-foreign-grammar"].check(case),
    ) == (
        [("svg", "svg", "div"), ("svg", boundary, "svg"), ("html", "a", boundary), ("svg", "g", "svg")],
        decoded,
        None,
        None,
    )


def test_html_foreign_first_parse() -> None:
    assert (
        html_foreign_check(
            "foreignObject:x:double:plain", read=lambda _text: parse_fragment("<svg><g><a>MiXeD</a></g></svg>")
        )
        == "parsed SVG integration tree differs from literals"
    )


@pytest.mark.parametrize(
    "serialized",
    [
        pytest.param('<svg><g><a id="x">MiXeD</a></g><g id="y"></g></svg>', id="namespace"),
        pytest.param('<svg><desc><a id="x">MiXeD</a><g id="y"></g></desc></svg>', id="parent"),
        pytest.param('<svg><desc><a id="y">MiXeD</a></desc><g id="y"></g></svg>', id="identifier"),
        pytest.param('<svg><desc><a id="x">changed</a></desc><g id="y"></g></svg>', id="text"),
        pytest.param("<!--unexpected-->", id="comment"),
        pytest.param("", id="empty"),
    ],
)
def test_html_foreign_serialized_corruption(serialized: str) -> None:
    assert html_foreign_check("desc:x:double:plain", lambda _node: serialized) == (
        "serialized SVG integration tree differs from literals"
    )


def test_html_foreign_repeat_formatting() -> None:
    outputs: Final = iter([
        '<svg><desc><a id="x">MiXeD</a></desc><g id="y"></g></svg>',
        "<svg><desc><a id='x'>MiXeD</a></desc><g id='y'></g></svg>",
    ])
    assert html_foreign_check("desc:x:double:plain", lambda _node: next(outputs)) == (
        "SVG integration serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing"),
        pytest.param("g:x:double:plain", id="boundary"),
        pytest.param("desc:z:double:plain", id="identifier"),
        pytest.param("desc:x:bare:plain", id="quote"),
        pytest.param("desc:x:double:raw", id="leaf"),
    ],
)
def test_html_foreign_unsupported_program(case: str) -> None:
    with pytest.raises(UnsupportedHtmlForeignCaseError):
        html_foreign_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["html-foreign-grammar"].check(case)


def test_html_foreign_finite_productions() -> None:
    seeds: Final = html_foreign_seeds()
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    generated: Final = [html_foreign_generate(rng) for _ in range(100)]
    assert (
        len(seeds),
        Counter(case.split(":")[0] for case in seeds),
        Counter(case.rsplit(":", 1)[1] for case in seeds),
        set(generated) <= set(seeds),
        all(html_foreign_check(case) is None for case in [*seeds, *generated]),
        html_foreign_controls(),
    ) == (
        36,
        {"foreignObject": 12, "desc": 12, "title": 12},
        {"plain": 12, "named": 12, "numeric": 12},
        True,
        True,
        {
            "HTML child became SVG": True,
            "SVG sibling moved inside integration point": True,
            "changed identifier": True,
            "encoded text": True,
        },
    )


def test_html_foreign_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "html-foreign-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "36" in capsys.readouterr().out) == (0, [], True)


def test_html_foreign_cli_emits_loss(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["html-foreign-grammar"],
        check=partial(html_foreign_check, serialize=lambda _node: ""),
        seeds=lambda: ["desc:x:double:plain"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"html-foreign-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-foreign-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized SVG integration tree differs from literals",
        1,
    )


def test_html_foreign_cli_rejection(mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    oracle: Final = replace(ORACLES["html-foreign-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"html-foreign-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-foreign-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("case", "source", "expected"),
    [
        pytest.param(
            "span:title:bare:2:empty",
            "<span title=Token></span>" * 2,
            [("span", {"title": "Token"}, "")] * 2,
            id="identical-empty-siblings",
        ),
        pytest.param(
            "em:data-x:single:1:plain",
            "<em data-x='Token'>MiXeD</em>",
            [("em", {"data-x": "Token"}, "MiXeD")],
            id="single-quote",
        ),
        pytest.param(
            "code:title:double:1:named",
            '<code title="&quot;&apos;&amp;">&amp;&lt;&gt;</code>',
            [("code", {"title": "\"'&"}, "&<>")],
            id="named-references",
        ),
        pytest.param(
            "span:data-x:bare:1:numeric",
            "<span data-x=&#x26;&#34;>&#65;&#x42;&#67;</span>",
            [("span", {"data-x": '&"'}, "ABC")],
            id="numeric-references",
        ),
    ],
)
def test_html_sibling_literal_trees(case: str, source: str, expected: list[tuple[str, dict[str, str], str]]) -> None:
    root: Final = parse_fragment(source)
    assert (
        [(node.tag, dict(node.attrs), node.text) for node in root.iter_elements() if node is not root],
        html_sibling_check(case),
        html_sibling_check(case, lambda _node: source[: len(source) // int(case.split(":")[3])]),
        html_sibling_check(case, lambda _node: ""),
    ) == (expected, None, None, "serialized sibling tree differs from literals")


def test_html_sibling_checks_the_first_parse() -> None:
    assert html_sibling_check("span:title:bare:2:empty", read=lambda _text: parse_fragment("<span></span>")) == (
        "parsed sibling tree differs from literals"
    )


def test_html_sibling_rejects_unexpected_comment() -> None:
    assert html_sibling_check("span:title:bare:1:empty", lambda _node: "<!--unexpected-->") == (
        "serialized sibling tree differs from literals"
    )


def test_html_sibling_checks_repeat_formatting() -> None:
    outputs: Final = iter(['<span title="Token"></span>', "<span title='Token'></span>"])
    assert html_sibling_check("span:title:bare:1:empty", lambda _node: next(outputs)) == (
        "sibling serialization changes on repeat"
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("", id="missing-program"),
        pytest.param("div:title:bare:1:empty", id="tag-pool"),
        pytest.param("span:href:bare:1:empty", id="attribute-pool"),
        pytest.param("span:title:raw:1:empty", id="quote-pool"),
        pytest.param("span:title:bare:0:empty", id="zero-siblings"),
        pytest.param("span:title:bare:5:empty", id="node-budget"),
        pytest.param("span:title:bare:1:raw", id="terminal-pool"),
    ],
)
def test_html_sibling_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedHtmlSiblingCaseError):
        html_sibling_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["html-sibling-grammar"].check(case)


def test_html_sibling_production_sweep_and_bounds() -> None:
    seeds: Final = html_sibling_seeds()
    rng: Final = random.Random(1018)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - Reproducible grammar samples.
    generated: Final = [html_sibling_generate(rng) for _ in range(100)]
    leaves: Final = Counter(case.rsplit(":", 1)[1] for case in seeds)
    assert (
        len(seeds),
        leaves,
        all(html_sibling_check(case) is None for case in [*seeds, *generated]),
        max(int(case.split(":")[3]) for case in [*seeds, *generated]),
        html_sibling_controls(),
    ) == (
        288,
        {"empty": 72, "plain": 72, "named": 72, "numeric": 72},
        True,
        4,
        {"dropped identical sibling": True, "changed attribute": True, "encoded text": True},
    )


def test_html_sibling_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "html-sibling-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "288" in capsys.readouterr().out) == (0, [], True)


def test_html_sibling_cli_emits_multiplicity_loss(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["html-sibling-grammar"],
        check=partial(html_sibling_check, serialize=lambda _node: ""),
        seeds=lambda: ["span:title:bare:2:empty"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"html-sibling-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-sibling-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "serialized sibling tree differs from literals",
        1,
    )


def test_html_sibling_cli_rejection_is_out_of_scope(
    mocker: MockerFixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    oracle: Final = replace(ORACLES["html-sibling-grammar"], seeds=lambda: ["invalid"], floor=Floor(1, 1))
    mocker.patch.dict(ORACLES, {"html-sibling-grammar": oracle}, clear=True)
    status: Final = main(["--oracle", "html-sibling-grammar", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("*.json")), "VACUOUS RUN" in capsys.readouterr().err) == (2, [], True)


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize("program", ["after", "before", "last", "ancestor"])
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("g", id="distinct-tags"),
        pytest.param("b", id="same-first-tag"),
        pytest.param("div", id="ordinary-root"),
    ],
)
def test_iterator_sequence_pins_reference_and_traversal(program: str, name: str) -> None:
    case: Final = f"{program}:{name}"
    assert (
        iterator_sequence_check(case),
        iterator_sequence_check(case, lambda _program, _name: "[]"),
    ) == (None, "iterator sequence differs from model")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("after", id="missing-separator"),
        pytest.param("unknown:g", id="unknown-program"),
        pytest.param("after0:g", id="unsupported-count"),
        pytest.param("after:", id="empty-name"),
        pytest.param("after:é", id="non-ascii-name"),
        pytest.param("after:g<p>", id="markup-name"),
        pytest.param("after:br", id="outside-nonvoid-fixtures"),
        pytest.param("after:g" + "1" * 16, id="overlong-name"),
    ],
)
def test_iterator_sequence_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedIteratorCaseError):
        iterator_sequence_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["iterator-sequence"].check(case)


def test_iterator_sequence_seed_programs_and_controls() -> None:
    rng: Final = random.SystemRandom()
    seeds: Final = iterator_sequence_seeds()
    generated: Final = [iterator_sequence_generate(rng) for _ in range(100)]
    assert (
        len(seeds),
        all(iterator_sequence_check(case) is None for case in [*seeds, *generated]),
        iterator_sequence_controls(),
    ) == (
        120,
        True,
        {"missing extraction": True, "stale reference": True, "wrong traversal order": True},
    )


def test_iterator_sequence_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "iterator-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "120" in capsys.readouterr().out) == (0, [], True)


def test_iterator_sequence_cli_emits_missed_edit(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["iterator-sequence"],
        check=partial(iterator_sequence_check, run=lambda _program, _name: "[]"),
        seeds=lambda: ["after:a"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"iterator-sequence": oracle}, clear=True)
    status: Final = main(["--oracle", "iterator-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "iterator sequence differs from model",
        1,
    )


def test_iterator_sequence_cli_emits_valid_program_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["iterator-sequence"],
        check=partial(iterator_sequence_check, run=_reject_program),
        seeds=lambda: ["after:a"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"iterator-sequence": oracle}, clear=True)
    status: Final = main(["--oracle", "iterator-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (1, 1, "raised RuntimeError", 1)


def _reject_program(_program: str, _name: str) -> str:
    message: Final = "valid DOM operation failed"
    raise RuntimeError(message)


def test_iterator_sequence_cli_rejects_vacuous_run(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        ORACLES,
        {"iterator-sequence": replace(ORACLES["iterator-sequence"], seeds=lambda: ["unknown:g"], floor=Floor(1, 1))},
        clear=True,
    )
    assert main(["--oracle", "iterator-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)]) == 2


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize("program", ["reuse1", "reuse2", "reuse3", "replace", "nested", "error"])
@pytest.mark.parametrize(
    "name",
    [
        pytest.param("g", id="distinct-tags"),
        pytest.param("b", id="same-child-tags"),
        pytest.param("div", id="same-root-tag"),
    ],
)
def test_observer_sequence_pins_edits_records_and_callbacks(program: str, name: str) -> None:
    case: Final = f"{program}:{name}"
    assert (
        observer_sequence_check(case),
        observer_sequence_check(case, lambda _program, _name: "[]"),
    ) == (None, "observer sequence differs from model")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("reuse1", id="missing-separator"),
        pytest.param("unknown:g", id="unknown-program"),
        pytest.param("reuse0:g", id="unsupported-count"),
        pytest.param("reuse1:", id="empty-name"),
        pytest.param("reuse1:é", id="non-ascii-name"),
        pytest.param("reuse1:g<p>", id="markup-name"),
        pytest.param("reuse1:br", id="outside-nonvoid-fixtures"),
        pytest.param("reuse1:g" + "1" * 16, id="overlong-name"),
    ],
)
def test_observer_sequence_rejects_unsupported_grammar(case: str) -> None:
    with pytest.raises(UnsupportedObserverCaseError):
        observer_sequence_check(case)
    with pytest.raises(OutOfScopeError):
        ORACLES["observer-sequence"].check(case)


def test_observer_sequence_seed_programs_and_controls() -> None:
    rng: Final = random.SystemRandom()
    seeds: Final = observer_sequence_seeds()
    generated: Final = [observer_sequence_generate(rng) for _ in range(100)]
    assert (
        len(seeds),
        all(observer_sequence_check(case) is None for case in [*seeds, *generated]),
        observer_sequence_controls(),
    ) == (
        180,
        True,
        {"missing edit": True, "missing callback": True, "swallowed callback error": True},
    )


def test_observer_sequence_cli_seed_floor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    status: Final = main(["--oracle", "observer-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    assert (status, list(tmp_path.glob("crash-*")), "180" in capsys.readouterr().out) == (0, [], True)


def test_observer_sequence_cli_emits_missed_edit(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["observer-sequence"],
        check=partial(observer_sequence_check, run=lambda _program, _name: "[]"),
        seeds=lambda: ["reuse1:a"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"observer-sequence": oracle}, clear=True)
    status: Final = main(["--oracle", "observer-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (
        1,
        1,
        "observer sequence differs from model",
        1,
    )


def test_observer_sequence_cli_emits_valid_program_failure(mocker: MockerFixture, tmp_path: Path) -> None:
    oracle: Final = replace(
        ORACLES["observer-sequence"],
        check=partial(observer_sequence_check, run=_fuzz_observer_invariants_reject_program),
        seeds=lambda: ["reuse1:a"],
        floor=Floor(1, 1),
    )
    mocker.patch.dict(ORACLES, {"observer-sequence": oracle}, clear=True)
    status: Final = main(["--oracle", "observer-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)])
    findings: Final = list(tmp_path.glob("*.json"))
    payload: Final = json.loads(findings[0].read_text(encoding="utf-8"))
    assert (status, len(findings), payload["detail"], payload["hits"]) == (1, 1, "raised RuntimeError", 1)


def _fuzz_observer_invariants_reject_program(_program: str, _name: str) -> str:
    message: Final = "valid DOM operation failed"
    raise RuntimeError(message)


def test_observer_sequence_cli_rejects_vacuous_run(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch.dict(
        ORACLES,
        {"observer-sequence": replace(ORACLES["observer-sequence"], seeds=lambda: ["unknown:g"], floor=Floor(1, 1))},
        clear=True,
    )
    assert main(["--oracle", "observer-sequence", "--minutes", "0", "--crash-dir", str(tmp_path)]) == 2
