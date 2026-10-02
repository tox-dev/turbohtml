"""The round-trip oracle driver end to end: findings on disk, the log it keeps clean, and the runs it refuses."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.round_trip_oracles import ORACLES, Floor, Oracle, OutOfScopeError, main

if TYPE_CHECKING:
    import random
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture


def _flag_x(text: str) -> str | None:
    return "broken" if "x" in text else None


def _out_of_scope(text: str) -> str | None:
    raise OutOfScopeError(text)


def _documented_depth(text: str) -> str | None:
    msg = f"{text} does not support trees nested 1024 levels or deeper"
    raise RecursionError(msg)


def _other_recursion(text: str) -> str | None:
    raise RecursionError(text)


def _key_error(text: str) -> str | None:
    raise KeyError(text)


def _seeds() -> list[str]:
    return ["axb", "a"]


def _generate(_rng: random.Random) -> str:
    return "zxz"


def _fired() -> dict[str, bool]:
    return {"control": True}


def _silent() -> dict[str, bool]:
    return {"control": False}


_ONE_CASE: Final = Floor(1, 0.5)


def _probe(
    check: Callable[[str], str | None],
    controls: Callable[[], dict[str, bool]] = _fired,
    floor: Floor = _ONE_CASE,
) -> dict[str, Oracle]:
    return {"probe": Oracle(check, _generate, _seeds, controls, floor)}


@pytest.fixture
def crash_dir(tmp_path: Path) -> Path:
    return tmp_path / "crashes"


def test_round_trip_cli_writes_a_minimized_finding(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    digest = hashlib.sha256(b"x").hexdigest()
    sidecar = json.loads((crash_dir / f"crash-{digest}.json").read_text(encoding="utf-8"))
    assert (code, (crash_dir / f"crash-{digest}").read_text(encoding="utf-8"), sidecar, capsys.readouterr().err) == (
        1,
        "x",
        {"oracle": "probe", "detail": "broken", "hits": 1, "rng_seed": 0},
        f"FINDING probe sha256={digest}\n",
    )


def test_round_trip_cli_keeps_the_input_and_seed_out_of_the_log(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    main(["--minutes", "0.001", "--rng-seed", "918273645", "--crash-dir", str(crash_dir)])
    out, err = capsys.readouterr()
    log = out + err
    assert (err.count("FINDING probe sha256="), "axb" in log, "zxz" in log, "918273645" in log) == (
        1,
        False,
        False,
        False,
    )


def test_round_trip_cli_runs_generated_cases_after_the_seeds(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x), clear=True)
    main(["--minutes", "0.001", "--crash-dir", str(crash_dir)])
    stats = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("probe: "))
    assert int(stats.split("'compared': ")[1].rstrip("}")) > len(_seeds())


def test_round_trip_cli_passes_a_clean_run(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(lambda _text: None), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().out.splitlines()) == (
        0,
        ["probe: {'compared': 2}", f"0 finding(s) written to {crash_dir}"],
    )


def test_round_trip_cli_fails_a_blind_oracle(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(ORACLES, _probe(_flag_x, controls=_silent), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (
        2,
        "BLIND ORACLE: the negative control did not fire for probe/control\n",
    )


@pytest.mark.parametrize(
    "check",
    [
        pytest.param(_out_of_scope, id="out-of-scope"),
        pytest.param(_documented_depth, id="documented-depth-error"),
    ],
)
def test_round_trip_cli_fails_a_vacuous_run(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str], check: Callable[[str], str | None]
) -> None:
    mocker.patch.dict(ORACLES, _probe(check), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (2, "VACUOUS RUN: probe compared below the floor\n")


def test_round_trip_cli_fails_below_the_ratio_floor(
    mocker: MockerFixture, crash_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mocker.patch.dict(
        ORACLES, _probe(lambda text: None if "x" in text else _out_of_scope(text), floor=Floor(1, 0.6)), clear=True
    )
    code = main(["--minutes", "0", "--oracle", "probe", "--crash-dir", str(crash_dir)])
    assert (code, capsys.readouterr().err) == (2, "VACUOUS RUN: probe compared below the floor\n")


@pytest.mark.parametrize(
    ("check", "detail"),
    [
        pytest.param(_other_recursion, "raised RecursionError", id="undocumented-recursion"),
        pytest.param(_key_error, "raised KeyError", id="unexpected-exception"),
    ],
)
def test_round_trip_cli_reports_an_escaping_exception(
    mocker: MockerFixture, crash_dir: Path, check: Callable[[str], str | None], detail: str
) -> None:
    mocker.patch.dict(ORACLES, _probe(check), clear=True)
    code = main(["--minutes", "0", "--crash-dir", str(crash_dir)])
    details = [json.loads(path.read_text(encoding="utf-8"))["detail"] for path in crash_dir.glob("crash-*.json")]
    assert (code, details) == (1, [detail])
