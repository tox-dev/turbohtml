"""The super-linear lane end to end on the real thread clock: its negative control, both modes, and what it logs."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Final

import pytest
from fuzz.amplify import TARGETS, Bound, Shape, Target, main, score

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

# GetThreadTimes, behind time.thread_time on Windows, counts CPU time in 15.6 ms ticks, coarser than a 10 ms sample
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Windows thread clock advances in 15.6 ms ticks")

_SHAPE: Final = Shape("repeat", "ab")


def _quadratic(text: str) -> None:  # pragma: win32 no cover
    sum(1 for _ in range(len(text) // 32) for _ in range(len(text) // 32))


def _linear(text: str) -> None:  # pragma: win32 no cover
    sum(1 for _ in range(len(text) // 4))


def _tenfold(text: str) -> str:  # pragma: win32 no cover
    return text * 10


def _rows(tmp_path: Path, target: str, shape: dict[str, object]) -> Path:  # pragma: win32 no cover
    row = {"name": "row", "source": "s", "targets": [target], "shape": shape}
    (path := tmp_path / "rows.json").write_text(json.dumps([row]), encoding="utf-8")
    return path


def test_score_flags_a_quadratic_python_function() -> None:  # pragma: win32 no cover
    assert score(_quadratic, _SHAPE).super_linear is True


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
        pytest.param(Target(_quadratic, ()), ": super-linear", id="slope"),
        pytest.param(Target(_tenfold, (), Bound(2.0, 0)), ": linear, output over bound", id="output"),
    ],
)
def test_regressions_fail_a_row(
    tmp_path: Path, mocker: MockerFixture, capsys: pytest.CaptureFixture[str], target: Target, verdict: str
) -> None:  # pragma: win32 no cover
    mocker.patch.dict(TARGETS, {"probe": target})
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
