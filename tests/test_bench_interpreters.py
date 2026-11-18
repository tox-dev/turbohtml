from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bench import interpreters

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_build_measures_every_operation_under_each_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    feed = tmp_path / "interpreters.json"
    specs: list[str] = []
    jobs: list[tuple[str, tuple[str, str, None], tuple[str, ...]]] = []

    def venv(workdir: Path, name: str, _requirements: tuple[str, ...], *, interpreter: str) -> Path:
        specs.append(interpreter)
        return workdir / name

    def worker(
        python: Path, job: tuple[str, str, None], _workdir: Path, pyperf_args: tuple[str, ...]
    ) -> dict[str, dict[str, float | str]]:
        jobs.append((python.name, job, pyperf_args))
        return {f"{job[0]}|case|core": {"mean": 1.0, "stdev": 0.0}}

    monkeypatch.setattr(interpreters, "_FEED", feed)
    monkeypatch.setattr(interpreters, "venv_python", venv)
    monkeypatch.setattr(interpreters, "run_worker", worker)
    interpreters.build(tmp_path, ("--fast",))

    rows = json.loads(feed.read_text(encoding="utf-8"))["rows"]
    assert specs == ["3.14", "pypy3.11"]
    assert len(jobs) == 2 * len(rows)
    assert {python for python, _, _ in jobs} == {"3-14", "pypy3-11"}
    assert {(job[1], pyperf_args) for _, job, pyperf_args in jobs} == {("core", ("--fast",))}
    assert [row[1:] for row in rows] == [[1.0, 1.0]] * len(rows)
