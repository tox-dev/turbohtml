from __future__ import annotations

import sys
from pathlib import Path

import pytest
from bench import __main__ as cli
from bench.orchestrator import SweepOptions


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    fake = _Recorder()
    monkeypatch.setattr(cli, "run", fake.run)
    monkeypatch.setattr(cli, "physical_cpus", lambda: [0, 1, 2, 3])
    return fake


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], SweepOptions]] = []

    def run(self, commands: tuple[str, ...], options: SweepOptions) -> None:
        self.calls.append((commands, options))


def test_jobs_and_pgo_are_kept_out_of_the_pyperf_arguments(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _invoke(monkeypatch, "--pgo", "--jobs", "2", "core", "--rigorous")
    assert recorder.calls == [
        (("core",), SweepOptions(pyperf_args=("--rigorous",), pgo=True, jobs=2, resume=None, targets=None)),
    ]


def _invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> None:
    monkeypatch.setattr(sys, "argv", ["bench", *arguments])
    cli.main()


def test_jobs_defaults_to_one(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _invoke(monkeypatch, "core")
    assert recorder.calls == [(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))]


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
    assert recorder.calls == [
        (("core",), SweepOptions(pyperf_args=("--affinity=3",), pgo=False, jobs=1, resume=None, targets=None)),
    ]


def test_operation_names_are_passed_in_order_ahead_of_pyperf_options(
    recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _invoke(monkeypatch, "startup", "node-equals", "--rigorous", "--affinity=3")
    assert recorder.calls == [
        (
            ("startup", "node-equals"),
            SweepOptions(pyperf_args=("--rigorous", "--affinity=3"), pgo=False, jobs=1, resume=None, targets=None),
        ),
    ]


def test_resume_and_targets_are_parsed_out_of_the_command(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    _invoke(monkeypatch, "--resume", "target/scratch/sweep", "--targets", "core,html5lib", "node-equals", "--rigorous")
    assert recorder.calls == [
        (
            ("node-equals",),
            SweepOptions(
                pyperf_args=("--rigorous",),
                pgo=False,
                jobs=1,
                resume=Path("target/scratch/sweep"),
                targets=frozenset({"core", "html5lib"}),
            ),
        ),
    ]


@pytest.mark.parametrize(
    "flag",
    [pytest.param("--resume", id="resume"), pytest.param("--targets", id="targets")],
)
def test_resume_and_targets_need_a_value(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    with pytest.raises(SystemExit, match=f"{flag} takes a value"):
        _invoke(monkeypatch, "node-equals", flag)
    assert recorder.calls == []


def test_missing_command_prints_usage_with_jobs(recorder: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(
        SystemExit,
        match=r"usage: python -m bench \[--table-json DIR\] \[--resume DIR\] \[--targets a,b\] \[--pgo\] \[--jobs N\]",
    ):
        _invoke(monkeypatch, "--jobs", "2")
    assert recorder.calls == []
