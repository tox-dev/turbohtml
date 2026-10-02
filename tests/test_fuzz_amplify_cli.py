"""
The super-linear lane end to end on the real thread clock: its negative control, both modes, and what it logs.

A probe that must score super-linear adds synthetic seconds on top of the real clock, so its slope stands above the
noise of a shared runner while the lane's own controls still time real work.
"""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.amplify import TARGETS, Bound, Shape, Target, main, score

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pytest_mock import MockerFixture

# GetThreadTimes, behind time.thread_time on Windows, counts CPU time in 15.6 ms ticks, coarser than a 10 ms sample
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Windows thread clock advances in 15.6 ms ticks")

_SHAPE: Final = Shape("repeat", "ab")


@pytest.fixture
def synthetic(mocker: MockerFixture) -> list[float]:  # pragma: win32 no cover
    real = time.thread_time
    extra = [0.0]
    mocker.patch("time.thread_time", side_effect=lambda: real() + extra[0])
    return extra


@pytest.fixture
def install_probe(
    mocker: MockerFixture, synthetic: list[float]
) -> Callable[[Callable[[list[float]], Target]], None]:  # pragma: win32 no cover
    return lambda build: mocker.patch.dict(TARGETS, {"probe": build(synthetic)})


def _quadratic(extra: list[float]) -> Callable[[str], None]:  # pragma: win32 no cover
    # 5 ms at 1 KiB, growing fourfold per doubling and staying under the 0.5 s call cutoff at 8 KiB
    def run(text: str) -> None:
        extra[0] += (len(text) / 1024) ** 2 * 0.005

    return run


def _linear(text: str) -> None:  # pragma: win32 no cover
    sum(1 for _ in range(len(text) // 4))


def _tenfold(text: str) -> str:  # pragma: win32 no cover
    return text * 10


def _rows(tmp_path: Path, target: str, shape: dict[str, object]) -> Path:  # pragma: win32 no cover
    row = {"name": "row", "source": "s", "targets": [target], "shape": shape}
    (path := tmp_path / "rows.json").write_text(json.dumps([row]), encoding="utf-8")
    return path


def test_score_flags_a_quadratic_run(synthetic: list[float]) -> None:  # pragma: win32 no cover
    assert score(_quadratic(synthetic), _SHAPE).super_linear is True


def test_score_passes_a_linear_python_function() -> None:  # pragma: win32 no cover
    assert score(_linear, _SHAPE).super_linear is False


def test_regressions_pass_a_linear_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # pragma: win32 no cover
    code = main(["regressions", "--file", str(_rows(tmp_path, "parse", {"kind": "repeat", "left": "a<?"}))])
    assert (code, capsys.readouterr().out.splitlines()[-1]) == (0, "1 regression rows scored, 0 failed")


@pytest.mark.parametrize(
    ("target", "verdict"),
    [
        pytest.param(lambda extra: Target(_quadratic(extra), ()), ": super-linear", id="slope"),
        pytest.param(lambda _extra: Target(_tenfold, (), Bound(2.0, 0)), ": linear, output over bound", id="output"),
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
