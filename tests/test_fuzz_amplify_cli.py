from __future__ import annotations

import json
from contextlib import nullcontext
from functools import partial
from typing import TYPE_CHECKING, Final

import pytest
from amplify_seam import SeamProcess, build_probe, charging, linear_cost, quadratic_cost
from fuzz.amplify import TARGETS, Bound, Shape, Target, main, score

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture

_SHAPE: Final[Shape] = Shape(kind="repeat", left="ab")


@pytest.fixture
def clock(mocker: MockerFixture) -> list[float]:
    now = [0.0]
    mocker.patch("time.thread_time", autospec=True, side_effect=lambda: now[0])
    mocker.patch("fuzz.amplify._watchdog", autospec=True, return_value=nullcontext())
    return now


@pytest.fixture
def controls(mocker: MockerFixture, clock: list[float]) -> None:
    # main's blind-scorer gate times these two before every run; a clock they do not advance would never end a sample
    mocker.patch("fuzz.amplify._quadratic_control", autospec=True, side_effect=charging(clock, quadratic_cost))
    mocker.patch("fuzz.amplify._linear_control", autospec=True, side_effect=charging(clock, linear_cost))


@pytest.fixture
def install_probe(mocker: MockerFixture, clock: list[float]) -> Callable[[Callable[[list[float]], Target]], None]:
    return lambda build: mocker.patch.dict(TARGETS, {"probe": build(clock)})


def _tenfold(clock: list[float]) -> Callable[[str], str]:
    def run(text: str) -> str:
        clock[0] += linear_cost(len(text))
        return text * 10

    return run


def test_score_flags_a_quadratic_run(clock: list[float]) -> None:
    assert score(charging(clock, quadratic_cost), _SHAPE).super_linear is True


@pytest.mark.usefixtures("controls")
def test_regressions_pass_a_linear_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], install_probe: Callable[[Callable[[list[float]], Target]], None]
) -> None:
    install_probe(lambda clock: Target(run=charging(clock, linear_cost), sources=()))
    code = main(["regressions", "--file", str(_rows(tmp_path, "probe", {"kind": "repeat", "left": "a<?"}))])
    assert (code, capsys.readouterr().out.splitlines()[-1]) == (0, "1 regression rows scored, 0 failed")


def _rows(tmp_path: Path, target: str, shape: dict[str, str]) -> Path:
    row = {"name": "row", "source": "s", "targets": [target], "shape": shape}
    (path := tmp_path / "rows.json").write_text(json.dumps([row]), encoding="utf-8")
    return path


@pytest.mark.usefixtures("controls")
@pytest.mark.parametrize(
    ("target", "verdict"),
    [
        pytest.param(
            lambda clock: Target(run=charging(clock, quadratic_cost), sources=()),
            ": super-linear",
            id="slope",
        ),
        pytest.param(
            lambda clock: Target(run=_tenfold(clock), sources=(), bound=Bound(slope=2.0, offset=0)),
            ": linear, output over bound",
            id="output",
        ),
    ],
)
def test_regressions_fail_a_row(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    install_probe: Callable[[Callable[[list[float]], Target]], None],
    target: Callable[[list[float]], Target],
    verdict: str,
) -> None:
    install_probe(target)
    code = main(["regressions", "--file", str(_rows(tmp_path, "probe", {"kind": "repeat", "left": "ab"}))])
    first = capsys.readouterr().out.splitlines()[0]
    assert (code, first.startswith("row [probe] growth "), first.endswith(verdict)) == (1, True, True)


@pytest.mark.usefixtures("controls")
def test_search_keeps_the_seed_and_inputs_out_of_the_log(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], install_probe: Callable[[Callable[[list[float]], Target]], None]
) -> None:
    marker = tmp_path / "marker"
    install_probe(partial(build_probe, "quadratic", marker=marker))
    code = main(
        ["search", "--shapes", "2", "--target", "probe", "--rng-seed", "918273645", "--crash-dir", str(tmp_path)],
        process=partial(SeamProcess, kind="quadratic", marker=marker),
    )
    out, err = capfd.readouterr()
    summary = [line.split(",")[0] for line in out.splitlines() if line.startswith("search: ")]
    findings = [line for line in out.splitlines() if line.startswith("FINDING ")]
    crashes = list(tmp_path.glob("crash-*.json"))
    assert (code, summary, len(findings), len(crashes), "918273645" in out + err) == (
        1,
        ["search: 2 shapes over 1 targets"],
        2,
        2,
        False,
    )


@pytest.mark.usefixtures("controls")
def test_search_restarts_a_dead_worker_and_stops_at_the_cap(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], install_probe: Callable[[Callable[[list[float]], Target]], None]
) -> None:
    marker = tmp_path / "marker"
    install_probe(partial(build_probe, "crash-once", marker=marker))
    code = main(
        ["search", "--shapes", "2", "--target", "probe", "--rng-seed", "918273645", "--crash-dir", str(tmp_path)],
        process=partial(SeamProcess, kind="crash-once", marker=marker),
    )
    summary = [line.split(",")[0] for line in capfd.readouterr().out.splitlines() if line.startswith("search: ")]
    records = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("crash-*.json")]
    aborts = [(record["finding"], record["exitcode"], record["log"]) for record in records]
    assert (code, summary, aborts) == (
        1,
        ["search: 1 shapes over 1 targets"],
        [("abort", 1, "crash-worker-0.log")],
    )


@pytest.mark.usefixtures("controls")
def test_search_stops_when_its_budget_is_spent(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code = main(["search", "--minutes", "0", "--target", "url", "--crash-dir", str(tmp_path)])
    summary = [line for line in capfd.readouterr().out.splitlines() if line.startswith("search: ")]
    assert (code, summary) == (0, ["search: 0 shapes over 0 targets, 0 capped slope(s), 0 finding(s)"])


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        pytest.param("0", "must be at least 1", id="zero"),
        pytest.param("-3", "must be at least 1", id="negative"),
        pytest.param("x", "invalid _shape_count value", id="text"),
    ],
)
def test_search_rejects_a_bad_shape_count(value: str, reason: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["search", "--shapes", value])
    assert (error.value.code, reason in capsys.readouterr().err) == (2, True)
