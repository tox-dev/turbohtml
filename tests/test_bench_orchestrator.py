from __future__ import annotations

import io
import json
import os
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # fakes the worker process boundary; nothing is spawned
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final

import pytest
from bench import interpreters, orchestrator
from bench.operations import OPERATIONS
from bench.orchestrator import physical_cpus, run

if TYPE_CHECKING:
    from collections.abc import Callable

_TWO_NAMES: Final[tuple[str, str]] = ("node-equals", "startup")


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    fake = _Harness(tmp_path)
    monkeypatch.setattr(orchestrator, "_CPU_SYSFS", fake.sysfs)
    monkeypatch.setattr(orchestrator, "_DURATIONS_FILE", fake.durations)
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: fake.affinity, raising=False)
    monkeypatch.setattr(orchestrator, "prefetch", lambda: None)
    monkeypatch.setattr(orchestrator, "baseline_python", fake.baseline_python)
    monkeypatch.setattr(orchestrator, "venv_python", fake.venv_python)
    monkeypatch.setattr(orchestrator, "render", fake.render)
    monkeypatch.setattr(orchestrator, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(subprocess, "run", fake.worker)
    return fake


class _Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.now = 0.0
        self.affinity = {0, 1, 2, 3}
        self.sysfs = tmp_path / "sysfs"
        self.durations = tmp_path / "durations.json"
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.hooks: dict[tuple[str, str], Callable[[tuple[str, ...]], None]] = {}
        self.seconds: dict[tuple[str, str], float] = {}
        self.broken_installs: set[str] = set()
        self.pip_options: dict[str, tuple[str, ...]] = {}
        self.rendered: list[tuple[str, dict[str, dict[str, float | str]]]] = []

    def monotonic(self) -> float:
        return self.now

    @staticmethod
    def baseline_python(workdir: Path, *, pgo: bool) -> Path:
        assert not pgo
        return workdir / "venv-core"

    def venv_python(
        self, workdir: Path, name: str, _requirements: tuple[str, ...], pip_options: tuple[str, ...]
    ) -> Path:
        self.pip_options[name] = pip_options
        if name in self.broken_installs:
            raise subprocess.CalledProcessError(1, ["uv", "pip", "install"])
        return workdir / f"venv-{name}"

    def render(self, operation: str, stats: dict[str, dict[str, float | str]]) -> None:
        self.rendered.append((operation, stats))

    def worker(
        self, args: list[str], *, env: dict[str, str], stdout: int | None, **_options: object
    ) -> subprocess.CompletedProcess[str]:
        operation, target = env["BENCH_OPERATION"], env["BENCH_TARGET"]
        pyperf_args = tuple(args[3:])
        self.calls.append((operation, target, pyperf_args))
        self.hooks.get((operation, target), _nothing)(pyperf_args)
        self.now += self.seconds.get((operation, target), 1.0)
        label = "turbohtml" if target == "core" else target
        Path(env["BENCH_OUT"]).write_text(
            json.dumps({f"{operation}|case|{label}": {"mean": 1.0, "stdev": 0.0}}), encoding="utf-8"
        )
        output = f"{target} {operation} measured\n"
        if stdout is None:
            sys.stdout.write(output)
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")


def _nothing(_args: tuple[str, ...]) -> None:
    return None


@pytest.mark.parametrize(
    ("siblings", "affinity", "expected"),
    [
        pytest.param({0: "0,2", 1: "1,3", 2: "0,2", 3: "1,3"}, {0, 1, 2, 3}, [0, 1], id="comma-siblings"),
        pytest.param({0: "0-1", 1: "0-1", 2: "2-3", 3: "2-3"}, {0, 1, 2, 3}, [0, 2], id="range-siblings"),
        pytest.param({1: "0,1", 3: "2,3"}, {1, 3}, [1, 3], id="sibling-outside-affinity"),
        pytest.param({}, {0, 1, 2, 3}, [0, 1, 2, 3], id="no-topology"),
    ],
)
def test_physical_cpus_keep_one_cpu_per_core(
    harness: _Harness, siblings: dict[int, str], affinity: set[int], expected: list[int]
) -> None:
    harness.affinity = affinity
    for cpu, text in siblings.items():
        path = harness.sysfs / f"cpu{cpu}" / "topology" / "thread_siblings_list"
        path.parent.mkdir(parents=True)
        path.write_text(f"{text}\n", encoding="utf-8")
    assert physical_cpus() == expected


def test_serial_run_prints_an_eta_after_each_run(harness: _Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run("node-equals", (), pgo=False, jobs=1)
    assert _eta_lines(capsys.readouterr().err) == [
        "[1/2] core node-equals took 1m02s; elapsed 1m02s, eta 1m02s",
        "[2/2] beautifulsoup4 node-equals took 0m10s; elapsed 1m12s, eta 0m00s",
    ]


def _eta_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("[")]


def test_cached_duration_sets_the_eta_of_a_pending_run(harness: _Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.durations.write_text(
        json.dumps({"core:node-equals": 62.0, "beautifulsoup4:node-equals": 100.0}), encoding="utf-8"
    )
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run("node-equals", (), pgo=False, jobs=1)
    assert _eta_lines(capsys.readouterr().err)[0] == "[1/2] core node-equals took 1m02s; elapsed 1m02s, eta 1m40s"


def test_finished_runs_are_written_to_the_duration_cache(harness: _Harness) -> None:
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run("node-equals", (), pgo=False, jobs=1)
    assert json.loads(harness.durations.read_text(encoding="utf-8")) == {
        "core:node-equals": 62.0,
        "beautifulsoup4:node-equals": 10.0,
    }


def test_missing_duration_cache_counts_as_empty(harness: _Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run("node-equals", (), pgo=False, jobs=1)
    assert _eta_lines(capsys.readouterr().err)[0] == "[1/2] core node-equals took 1m02s; elapsed 1m02s, eta 1m02s"


@pytest.mark.parametrize(
    "cache",
    [
        pytest.param("{not json", id="corrupt"),
        pytest.param('["not", "a", "mapping"]', id="wrong-shape"),
    ],
)
def test_corrupt_duration_cache_counts_as_empty(
    harness: _Harness, capsys: pytest.CaptureFixture[str], cache: str
) -> None:
    harness.durations.write_text(cache, encoding="utf-8")
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run("node-equals", (), pgo=False, jobs=1)
    assert _eta_lines(capsys.readouterr().err)[0] == "[1/2] core node-equals took 1m02s; elapsed 1m02s, eta 1m02s"
    assert json.loads(harness.durations.read_text(encoding="utf-8")) == {
        "core:node-equals": 62.0,
        "beautifulsoup4:node-equals": 10.0,
    }


def test_parallel_run_renders_in_plan_order_when_a_later_run_finishes_first(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    startup_finished = threading.Event()
    monkeypatch.setattr(sys, "stderr", _StderrWatch("startup", startup_finished))

    def hold(_args: tuple[str, ...]) -> None:
        startup_finished.wait(timeout=10)

    harness.hooks["node-equals", "core"] = hold
    run("core", (), pgo=False, jobs=2)
    assert [operation for operation, _ in harness.rendered] == ["node-equals", "startup"]


class _StderrWatch(io.StringIO):
    def __init__(self, marker: str, seen: threading.Event) -> None:
        super().__init__()
        self._marker = marker
        self._seen = seen

    def write(self, text: str) -> int:
        if self._marker in text:
            self._seen.set()
        return super().write(text)


def test_parallel_runs_are_pinned_to_distinct_cpus(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    barrier = threading.Barrier(2, timeout=10)

    def meet(_args: tuple[str, ...]) -> None:
        barrier.wait()

    harness.hooks["node-equals", "core"] = meet
    harness.hooks["startup", "core"] = meet
    run("core", (), pgo=False, jobs=2)
    assert sorted(arg for _, _, args in harness.calls for arg in args) == ["--affinity=0", "--affinity=1"]


@pytest.mark.usefixtures("harness")
def test_parallel_worker_output_is_printed_as_a_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    run("core", (), pgo=False, jobs=2)
    lines = capsys.readouterr().out.splitlines()
    assert sorted(line for line in lines if line.endswith(" measured")) == [
        "core node-equals measured",
        "core startup measured",
    ]


def test_serial_failure_cancels_the_remaining_runs(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})

    def fail(_args: tuple[str, ...]) -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    harness.hooks["node-equals", "core"] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run("core", (), pgo=False, jobs=1)
    assert harness.calls == [("node-equals", "core", ())]
    assert "boom" in capsys.readouterr().err


def test_parallel_failure_reraises(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})

    def fail(_args: tuple[str, ...]) -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    harness.hooks["node-equals", "core"] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run("core", (), pgo=False, jobs=2)


def test_competitor_that_does_not_install_drops_its_column(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.broken_installs = {"beautifulsoup4"}
    run("node-equals", (), pgo=False, jobs=1)
    assert "skipping beautifulsoup4: it did not install in its isolated venv" in capsys.readouterr().err.splitlines()
    assert harness.rendered == [("node-equals", {"node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0}})]


@pytest.mark.parametrize(
    ("error", "note"),
    [
        pytest.param(
            subprocess.TimeoutExpired(["worker"], 5400),
            "skipping beautifulsoup4: it did not finish node-equals within 5400s",
            id="timeout",
        ),
        pytest.param(
            subprocess.CalledProcessError(1, ["worker"], stderr="File importlib.import_module failed\n"),
            "skipping beautifulsoup4: it installed but failed to import in its isolated venv",
            id="import",
        ),
    ],
)
def test_competitor_that_cannot_be_measured_drops_its_column(
    harness: _Harness,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    note: str,
) -> None:
    def fail(_args: tuple[str, ...]) -> None:
        raise error

    harness.hooks["node-equals", "beautifulsoup4"] = fail
    run("node-equals", (), pgo=False, jobs=1)
    assert note in capsys.readouterr().err.splitlines()
    assert harness.rendered == [("node-equals", {"node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0}})]


def test_competitor_crash_after_import_propagates(harness: _Harness) -> None:
    def fail(_args: tuple[str, ...]) -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="ValueError: bad measurement")

    harness.hooks["node-equals", "beautifulsoup4"] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run("node-equals", (), pgo=False, jobs=1)


def test_package_run_renders_each_operation_for_turbohtml_and_the_package(harness: _Harness) -> None:
    run("beautifulsoup4", (), pgo=False, jobs=1)
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0},
        "node-equals|case|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
    }


def test_all_renders_every_operation_with_its_competitors(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    run("all", (), pgo=False, jobs=1)
    assert [operation for operation, _ in harness.rendered] == ["node-equals", "startup"]
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0},
        "node-equals|case|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
    }


def test_package_run_installs_with_the_competitor_pip_options(harness: _Harness) -> None:
    run("html5_parser", (), pgo=False, jobs=1)
    assert harness.pip_options["html5_parser"] == ("--no-binary", "lxml")


@pytest.mark.usefixtures("harness")
def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit, match="unknown command 'nope'"):
        run("nope", (), pgo=False, jobs=1)


def test_interpreters_command_builds_its_feed_outside_the_scheduler(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[tuple[Path, tuple[str, ...]]] = []

    def build(workdir: Path, pyperf_args: tuple[str, ...]) -> None:
        built.append((workdir, pyperf_args))

    monkeypatch.setattr(interpreters, "build", build)
    run("interpreters", ("--rigorous",), pgo=False, jobs=1)
    assert [args for _, args in built] == [("--rigorous",)]
    assert harness.calls == []
