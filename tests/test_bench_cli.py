from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from bench import __main__ as cli

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recorder]:
    fake = _Recorder()
    monkeypatch.setattr(cli, "run", fake.run)
    monkeypatch.setattr(cli, "physical_cpus", lambda: [0, 1, 2, 3])
    yield fake


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], bool, int]] = []

    def run(self, command: str, pyperf_args: tuple[str, ...], *, pgo: bool, jobs: int) -> None:
        self.calls.append((command, pyperf_args, pgo, jobs))


def test_jobs_and_pgo_are_kept_out_of_the_pyperf_arguments(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _invoke(monkeypatch, "--pgo", "--jobs", "2", "core", "--rigorous")
    assert recorder.calls == [("core", ("--rigorous",), True, 2)]


def _invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> None:
    monkeypatch.setattr(sys, "argv", ["bench", *arguments])
    cli.main()


def test_jobs_defaults_to_one(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _invoke(monkeypatch, "core")
    assert recorder.calls == [("core", (), False, 1)]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        pytest.param(("--jobs", "0", "core"), "must be between 1 and 4", id="zero"),
        pytest.param(("--jobs", "5", "core"), "must be between 1 and 4", id="above-physical-cores"),
        pytest.param(("--jobs", "many", "core"), "takes the number of runs", id="not-a-number"),
        pytest.param(("core", "--jobs"), "takes the number of runs", id="missing-value"),
        pytest.param(("--jobs", "2", "core", "--affinity=3"), "cannot be combined", id="affinity-joined"),
        pytest.param(("--jobs", "2", "core", "--affinity", "3"), "cannot be combined", id="affinity-separate"),
    ],
)
def test_invalid_jobs_are_rejected(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, arguments: tuple[str, ...], message: str
) -> None:
    with pytest.raises(SystemExit, match=message):
        _invoke(monkeypatch, *arguments)
    assert recorder.calls == []


def test_affinity_is_allowed_without_parallel_jobs(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _invoke(monkeypatch, "core", "--affinity=3")
    assert recorder.calls == [("core", ("--affinity=3",), False, 1)]


def test_missing_command_prints_usage_with_jobs(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit, match=r"usage: python -m bench \[--table-json DIR\] \[--pgo\] \[--jobs N\]"):
        _invoke(monkeypatch, "--jobs", "2")
    assert recorder.calls == []
