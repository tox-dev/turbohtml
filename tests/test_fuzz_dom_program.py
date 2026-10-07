from __future__ import annotations

import json
import random
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.dom_program import (
    UnsupportedDomProgramError,
    dom_program_check,
    dom_program_controls,
    dom_program_generate,
    dom_program_seeds,
    run_dom_program,
)
from fuzz.round_trip_oracles import ORACLES, Floor, OutOfScopeError, main

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
