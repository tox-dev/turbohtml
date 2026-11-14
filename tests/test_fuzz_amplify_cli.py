from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.amplify import TARGETS, Bound, Shape, Target, main, score

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture

# GetThreadTimes, behind time.thread_time on Windows, counts CPU time in 15.6 ms ticks, coarser than a 10 ms sample
pytestmark: Final[pytest.MarkDecorator] = pytest.mark.skipif(
    sys.platform == "win32", reason="the Windows thread clock advances in 15.6 ms ticks"
)

_SHAPE: Final[Shape] = Shape(kind="repeat", left="ab")


@pytest.fixture
def clock(mocker: MockerFixture) -> list[float]:  # pragma: win32 no cover
    now = [0.0]
    mocker.patch("time.thread_time", autospec=True, side_effect=lambda: now[0])
    return now


@pytest.fixture
def controls(mocker: MockerFixture, clock: list[float]) -> None:  # pragma: win32 no cover
    # main's blind-scorer gate times these two before every run; a clock they do not advance would never end a sample
    mocker.patch("fuzz.amplify._quadratic_control", autospec=True, side_effect=_charging(clock, _quadratic_cost))
    mocker.patch("fuzz.amplify._linear_control", autospec=True, side_effect=_charging(clock, _linear_cost))


@pytest.fixture
def install_probe(
    mocker: MockerFixture, clock: list[float]
) -> Callable[[Callable[[list[float]], Target]], None]:  # pragma: win32 no cover
    return lambda build: mocker.patch.dict(TARGETS, {"probe": build(clock)})


def _charging(clock: list[float], cost: Callable[[int], float]) -> Callable[[str], None]:  # pragma: win32 no cover
    def run(text: str) -> None:
        clock[0] += cost(len(text))

    return run


def _quadratic_cost(size: int) -> float:  # pragma: win32 no cover
    # 5 ms at 1 KiB, growing fourfold per doubling and staying under the 0.5 s call cutoff at 8 KiB
    return (size / 1024) ** 2 * 0.005


def _linear_cost(size: int) -> float:  # pragma: win32 no cover
    return size / 1024 * 0.001


def _tenfold(clock: list[float]) -> Callable[[str], str]:  # pragma: win32 no cover
    def run(text: str) -> str:
        clock[0] += _linear_cost(len(text))
        return text * 10

    return run


def test_score_flags_a_quadratic_run(clock: list[float]) -> None:  # pragma: win32 no cover
    assert score(_charging(clock, _quadratic_cost), _SHAPE).super_linear is True


def test_score_passes_a_linear_python_function(clock: list[float]) -> None:  # pragma: win32 no cover
    assert score(_charging(clock, _linear_cost), _SHAPE).super_linear is False


@pytest.mark.usefixtures("controls")
def test_regressions_pass_a_linear_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], install_probe: Callable[[Callable[[list[float]], Target]], None]
) -> None:  # pragma: win32 no cover
    install_probe(lambda clock: Target(run=_charging(clock, _linear_cost), sources=()))
    code = main(["regressions", "--file", str(_rows(tmp_path, "probe", {"kind": "repeat", "left": "a<?"}))])
    assert (code, capsys.readouterr().out.splitlines()[-1]) == (0, "1 regression rows scored, 0 failed")


def _rows(tmp_path: Path, target: str, shape: dict[str, str]) -> Path:  # pragma: win32 no cover
    row = {"name": "row", "source": "s", "targets": [target], "shape": shape}
    (path := tmp_path / "rows.json").write_text(json.dumps([row]), encoding="utf-8")
    return path


@pytest.mark.usefixtures("controls")
@pytest.mark.parametrize(
    ("target", "verdict"),
    [
        pytest.param(
            lambda clock: Target(run=_charging(clock, _quadratic_cost), sources=()), ": super-linear", id="slope"
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
) -> None:  # pragma: win32 no cover
    install_probe(target)
    code = main(["regressions", "--file", str(_rows(tmp_path, "probe", {"kind": "repeat", "left": "ab"}))])
    first = capsys.readouterr().out.splitlines()[0]
    assert (code, first.startswith("row [probe] growth "), first.endswith(verdict)) == (1, True, True)


def test_search_keeps_the_seed_and_inputs_out_of_the_log(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:  # pragma: win32 no cover
    code = main([
        "search",
        "--minutes",
        "0.02",
        "--target",
        "url",
        "--rng-seed",
        "918273645",
        "--crash-dir",
        str(tmp_path),
    ])
    out, err = capfd.readouterr()
    summary = [line for line in out.splitlines() if line.startswith("search: ")]
    assert (code, len(summary), "918273645" in out + err, sorted(tmp_path.iterdir())) == (0, 1, False, [])
