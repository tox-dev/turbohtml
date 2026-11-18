from __future__ import annotations

import io
import json
import os
import signal
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # fakes the worker process boundary; nothing is spawned
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, NamedTuple

import pytest
from bench import interpreters, orchestrator
from bench.operations import OPERATIONS
from bench.orchestrator import SweepOptions, physical_cpus, run

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
    monkeypatch.setattr(orchestrator, "baseline_python", fake.venvs.baseline_python)
    monkeypatch.setattr(orchestrator, "venv_python", fake.venvs.venv_python)
    monkeypatch.setattr(orchestrator, "render", fake.render)
    monkeypatch.setattr(orchestrator, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(os, "killpg", lambda pid, number: fake.processes.workers[pid].stop(number), raising=False)
    monkeypatch.setattr(subprocess, "run", fake.processes.run)
    monkeypatch.setattr(subprocess, "Popen", fake.processes.popen)
    return fake


class _Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.now = 0.0
        self.affinity = {0, 1, 2, 3}
        self.sysfs = tmp_path / "sysfs"
        self.durations = tmp_path / "durations.json"
        self.seconds: dict[tuple[str, str], float] = {}
        self.rendered: list[tuple[str, dict[str, dict[str, float | str]]]] = []
        self.venvs = _Venvs()
        self.processes = _Processes(self)

    def monotonic(self) -> float:
        return self.now

    def render(self, operation: str, stats: dict[str, dict[str, float | str]]) -> None:
        self.rendered.append((operation, stats))


class _Venvs:
    def __init__(self) -> None:
        self.broken_installs: set[str] = set()
        self.baseline_builds = 0
        self.provisioned: list[str] = []
        self.pip_options: dict[str, tuple[str, ...]] = {}

    def baseline_python(self, workdir: Path, *, pgo: bool) -> Path:
        assert not pgo
        self.baseline_builds += 1
        return workdir / "venv-core"

    def venv_python(
        self, workdir: Path, name: str, _requirements: tuple[str, ...], pip_options: tuple[str, ...]
    ) -> Path:
        self.pip_options[name] = pip_options
        self.provisioned.append(name)
        if name in self.broken_installs:
            raise subprocess.CalledProcessError(1, ["uv", "pip", "install"])
        return workdir / f"venv-{name}"


class _Processes:
    # The fake process layer: the case listing, the git stamp, and every worker, whose hook runs inside communicate.
    def __init__(self, harness: _Harness) -> None:
        self.harness = harness
        self.cases: dict[str, list[str]] = {}
        self.listing: str | None = None
        self.listing_error: str | None = None
        self.stamp: str | None = "tree-a"  # None stands for a machine without git
        self.calls: list[tuple[str, str, int | None, tuple[str, ...]]] = []
        self.hooks: dict[tuple[str, str, int | None], Callable[[], None]] = {}
        self.spawn_gates: dict[tuple[str, str, int | None], Callable[[], None]] = {}
        self.terminated: list[tuple[str, str, int | None]] = []
        self.killed = threading.Event()
        self.workers: dict[int, _Worker] = {}
        self._spawn_lock = threading.Lock()

    def run(
        self, args: list[str], *, env: dict[str, str] | None = None, **_options: object
    ) -> subprocess.CompletedProcess[str]:
        if env is None:  # the only call without a worker environment is the source stamp's git describe
            if self.stamp is None:
                raise FileNotFoundError(args[0])
            return subprocess.CompletedProcess(args, 0, stdout=f"{self.stamp}\n", stderr="")
        if self.listing_error is not None:
            raise subprocess.CalledProcessError(1, args, stderr=self.listing_error)
        stdout = self.listing or json.dumps({name: self.names(name) for name in json.loads(env["BENCH_LIST"])})
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    def names(self, operation: str) -> list[str]:
        return self.cases.get(operation, ["case"])

    def popen(self, args: list[str], *, env: dict[str, str], stdout: int | None, **_options: object) -> _Worker:
        if (gate := self.spawn_gates.get(_job_of(env))) is not None:
            gate()
        # workers spawn from pool threads, and without a GIL two of them could read the same length and share a pid
        with self._spawn_lock:
            worker = _Worker(self, args, env, pid=1000 + len(self.workers), captured=stdout is not None)
            self.workers[worker.pid] = worker
        return worker


def _job_of(env: dict[str, str]) -> _Job:
    return _Job(env["BENCH_OPERATION"], env["BENCH_TARGET"], int(env["BENCH_CASE"]) if "BENCH_CASE" in env else None)


class _Job(NamedTuple):
    operation: str
    target: str
    case: int | None


class _Worker:
    # Stands in for one worker process: the hook runs inside communicate, where a real worker would be running.
    def __init__(
        self, processes: _Processes, args: list[str], env: dict[str, str], *, pid: int, captured: bool
    ) -> None:
        self.job = _job_of(env)
        self.pid = pid
        self.args = args
        self.returncode: int | None = None
        self._processes = processes
        self._env = env
        self._captured = captured
        self._stdout: str | None = None
        self._stderr = ""
        self._communicated = False

    def stop(self, signal_number: int = signal.SIGTERM) -> None:
        self._processes.terminated.append(self.job)
        self.returncode = -signal_number
        self._processes.killed.set()

    kill = stop  # Windows stops the worker itself; POSIX signals its group through os.killpg

    def communicate(self, **_options: object) -> tuple[str | None, str]:
        # a reap after a timeout comes back with what the first communicate collected
        if not self._communicated:
            self._communicated = True
            if self.returncode is None:
                self._work()
        return self._stdout, self._stderr

    def _work(self) -> None:
        operation, target, case = self.job
        self._processes.calls.append((operation, target, case, tuple(self.args[3:])))
        try:
            self._processes.hooks.get(self.job, _nothing)()
        except subprocess.CalledProcessError as error:
            self.returncode, self._stderr = error.returncode, error.stderr
            return
        if self.returncode is not None:  # the harness terminated this worker while its hook ran
            return
        self._processes.harness.now += self._processes.harness.seconds.get((operation, target), 1.0)
        label = "turbohtml" if target == "core" else target
        names = self._processes.names(operation)
        selected = names if case is None else [names[case]]
        stats = {f"{operation}|{name}|{label}": {"mean": 1.0, "stdev": 0.0} for name in selected}
        Path(self._env["BENCH_OUT"]).write_text(json.dumps(stats), encoding="utf-8")
        output = f"{target} {operation} measured\n"
        if self._captured:
            self._stdout = output
        else:
            sys.stdout.write(output)
        self.returncode = 0


def _nothing() -> None:
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
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert _eta_lines(capsys.readouterr().err) == [
        "[1/2] core node-equals case took 1m02s; elapsed 1m02s, eta 1m02s",
        "[2/2] beautifulsoup4 node-equals case took 0m10s; elapsed 1m12s, eta 0m00s",
    ]


def _eta_lines(err: str) -> list[str]:
    return [line for line in err.splitlines() if line.startswith("[")]


def test_cached_duration_sets_the_eta_of_a_pending_run(harness: _Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.durations.write_text(
        json.dumps({"core:node-equals:0": 62.0, "beautifulsoup4:node-equals:0": 100.0}), encoding="utf-8"
    )
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert _eta_lines(capsys.readouterr().err)[0] == (
        "[1/2] core node-equals case took 1m02s; elapsed 1m02s, eta 1m40s"
    )


def test_finished_runs_are_written_to_the_duration_cache(harness: _Harness) -> None:
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert json.loads(harness.durations.read_text(encoding="utf-8")) == {
        "core:node-equals:0": 62.0,
        "beautifulsoup4:node-equals:0": 10.0,
    }


def test_missing_duration_cache_counts_as_empty(harness: _Harness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.seconds = {("node-equals", "core"): 62.0, ("node-equals", "beautifulsoup4"): 10.0}
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert _eta_lines(capsys.readouterr().err)[0] == (
        "[1/2] core node-equals case took 1m02s; elapsed 1m02s, eta 1m02s"
    )


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
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert _eta_lines(capsys.readouterr().err)[0] == (
        "[1/2] core node-equals case took 1m02s; elapsed 1m02s, eta 1m02s"
    )
    assert json.loads(harness.durations.read_text(encoding="utf-8")) == {
        "core:node-equals:0": 62.0,
        "beautifulsoup4:node-equals:0": 10.0,
    }


def test_each_case_is_its_own_job_in_listing_order_and_renders_in_that_order(harness: _Harness) -> None:
    harness.processes.cases = {"node-equals": ["first", "second", "third"]}
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == [
        ("node-equals", "core", 0, ()),
        ("node-equals", "core", 1, ()),
        ("node-equals", "core", 2, ()),
        ("node-equals", "beautifulsoup4", 0, ()),
        ("node-equals", "beautifulsoup4", 1, ()),
        ("node-equals", "beautifulsoup4", 2, ()),
    ]
    assert list(dict(harness.rendered)["node-equals"]) == [
        "node-equals|first|turbohtml",
        "node-equals|second|turbohtml",
        "node-equals|third|turbohtml",
        "node-equals|first|beautifulsoup4",
        "node-equals|second|beautifulsoup4",
        "node-equals|third|beautifulsoup4",
    ]


def test_a_listing_failure_stops_before_any_measurement(harness: _Harness) -> None:
    harness.processes.listing_error = "'node-equals' repeats the case name 'same'"
    with pytest.raises(SystemExit, match="could not list the cases of node-equals"):
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == []


def test_a_listing_that_is_not_case_names_is_rejected(harness: _Harness) -> None:
    harness.processes.listing = "[1, 2]"
    with pytest.raises(SystemExit, match="not case names per operation"):
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == []


def test_parallel_run_renders_in_plan_order_when_a_later_run_finishes_first(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    startup_finished = threading.Event()
    monkeypatch.setattr(sys, "stderr", _StderrWatch("startup", startup_finished))

    def hold() -> None:
        startup_finished.wait(timeout=10)

    harness.processes.hooks["node-equals", "core", 0] = hold
    run(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=None))
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

    def meet() -> None:
        barrier.wait()

    harness.processes.hooks["node-equals", "core", 0] = meet
    harness.processes.hooks["startup", "core", 0] = meet
    run(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=None))
    assert sorted(arg for _, _, _, args in harness.processes.calls for arg in args) == ["--affinity=0", "--affinity=1"]


@pytest.mark.usefixtures("harness")
def test_parallel_worker_output_is_printed_as_a_block(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    run(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=None))
    lines = capsys.readouterr().out.splitlines()
    assert sorted(line for line in lines if line.endswith(" measured")) == [
        "core node-equals measured",
        "core startup measured",
    ]


def test_serial_failure_cancels_the_remaining_runs(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})

    def fail() -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    harness.processes.hooks["node-equals", "core", 0] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == [("node-equals", "core", 0, ())]
    assert "boom" in capsys.readouterr().err


def test_parallel_failure_reraises(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})

    def fail() -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    harness.processes.hooks["node-equals", "core", 0] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run(("core",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=None))


def test_a_failure_stops_the_running_worker_at_once(harness: _Harness) -> None:
    harness.processes.cases = {"node-equals": ["slow", "failing"]}
    started = threading.Event()

    def hold() -> None:
        started.set()
        harness.processes.killed.wait(timeout=10)

    def fail() -> None:
        started.wait(timeout=10)
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    harness.processes.hooks["node-equals", "core", 0] = hold
    harness.processes.hooks["node-equals", "core", 1] = fail
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=frozenset({"core"})))
    assert (failure.value.stderr, harness.processes.terminated) == ("boom", [("node-equals", "core", 0)])


def test_a_worker_started_after_the_failure_is_stopped_on_arrival(harness: _Harness) -> None:
    harness.processes.cases = {"node-equals": ["slow", "failing", "late"]}
    started = threading.Event()
    reached = threading.Event()

    def hold() -> None:
        started.set()
        harness.processes.killed.wait(timeout=10)

    def fail() -> None:
        started.wait(timeout=10)
        reached.wait(timeout=10)
        raise subprocess.CalledProcessError(1, ["worker"], stderr="boom")

    def reach_and_wait_for_the_stop() -> None:
        reached.set()
        harness.processes.killed.wait(timeout=10)

    harness.processes.hooks["node-equals", "core", 0] = hold
    harness.processes.hooks["node-equals", "core", 1] = fail
    harness.processes.spawn_gates["node-equals", "core", 2] = reach_and_wait_for_the_stop
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=3, resume=None, targets=frozenset({"core"})))
    assert (
        failure.value.stderr,
        sorted(harness.processes.terminated),
        {case for _, _, case, _ in harness.processes.calls},
    ) == (
        "boom",
        [("node-equals", "core", 0), ("node-equals", "core", 2)],
        {0, 1},
    )


def test_competitor_that_does_not_install_drops_its_column(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.venvs.broken_installs = {"beautifulsoup4"}
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
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
    def fail() -> None:
        raise error

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = fail
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert note in capsys.readouterr().err.splitlines()
    assert harness.rendered == [("node-equals", {"node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0}})]


def test_timeout_drops_the_competitors_remaining_cases_with_one_note(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b", "c"]}

    def time_out() -> None:
        raise subprocess.TimeoutExpired(["worker"], 5400)

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = time_out
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert [call for call in harness.processes.calls if call[1] == "beautifulsoup4"] == [
        ("node-equals", "beautifulsoup4", 0, ()),
    ]
    assert [line for line in capsys.readouterr().err.splitlines() if line.startswith("skipping")] == [
        "skipping beautifulsoup4: it did not finish node-equals within 5400s",
    ]
    assert list(dict(harness.rendered)["node-equals"]) == [
        "node-equals|a|turbohtml",
        "node-equals|b|turbohtml",
        "node-equals|c|turbohtml",
    ]


def test_cases_timing_out_side_by_side_print_the_note_once(
    harness: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    barrier = threading.Barrier(2, timeout=10)

    def time_out() -> None:
        barrier.wait()
        raise subprocess.TimeoutExpired(["worker"], 5400)

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = time_out
    harness.processes.hooks["node-equals", "beautifulsoup4", 1] = time_out
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=None, targets=None))
    assert [line for line in capsys.readouterr().err.splitlines() if line.startswith("skipping")] == [
        "skipping beautifulsoup4: it did not finish node-equals within 5400s",
    ]


def test_competitor_crash_after_import_propagates(harness: _Harness) -> None:
    def fail() -> None:
        raise subprocess.CalledProcessError(1, ["worker"], stderr="ValueError: bad measurement")

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = fail
    with pytest.raises(subprocess.CalledProcessError):
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))


def test_package_run_renders_each_operation_for_turbohtml_and_the_package(harness: _Harness) -> None:
    run(("beautifulsoup4",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0},
        "node-equals|case|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
    }


def test_all_renders_every_operation_with_its_competitors(harness: _Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    run(("all",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert [operation for operation, _ in harness.rendered] == ["node-equals", "startup"]
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|case|turbohtml": {"mean": 1.0, "stdev": 0.0},
        "node-equals|case|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
    }


def test_package_run_installs_with_the_competitor_pip_options(harness: _Harness) -> None:
    run(("html5_parser",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.venvs.pip_options["html5_parser"] == ("--no-binary", "lxml")


@pytest.mark.usefixtures("harness")
def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit, match="unknown command 'nope'"):
        run(("nope",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))


def test_several_operations_share_one_baseline_and_render_in_the_order_given(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orchestrator, "OPERATIONS", {name: OPERATIONS[name] for name in _TWO_NAMES})
    run(("startup", "node-equals"), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert [operation for operation, _ in harness.rendered] == ["startup", "node-equals"]
    assert (harness.venvs.baseline_builds, harness.venvs.provisioned) == (1, ["beautifulsoup4"])


@pytest.mark.parametrize(
    ("commands", "rejected"),
    [
        pytest.param(("all", "node-equals"), "all", id="all-first"),
        pytest.param(("node-equals", "all"), "all", id="all-last"),
        pytest.param(("core", "node-equals"), "core", id="core"),
        pytest.param(("interpreters", "node-equals"), "interpreters", id="interpreters"),
        pytest.param(("node-equals", "beautifulsoup4"), "beautifulsoup4", id="package"),
    ],
)
def test_single_command_cannot_be_mixed_with_operations(
    harness: _Harness, commands: tuple[str, ...], rejected: str
) -> None:
    with pytest.raises(SystemExit, match=f"{rejected!r} runs alone"):
        run(commands, SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == []


def test_unknown_name_among_several_operations_is_rejected(harness: _Harness) -> None:
    with pytest.raises(SystemExit, match="unknown command 'nope'"):
        run(("node-equals", "nope"), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=None))
    assert harness.processes.calls == []


def test_targets_limit_the_run_to_the_chosen_targets(harness: _Harness) -> None:
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=frozenset({"core"})))
    assert {target for _, target, _, _ in harness.processes.calls} == {"core"}
    assert list(dict(harness.rendered)["node-equals"]) == ["node-equals|case|turbohtml"]


def test_turbohtml_stays_in_every_run_as_the_baseline(harness: _Harness) -> None:
    run(
        ("startup", "node-equals"),
        SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=frozenset({"beautifulsoup4"})),
    )
    assert harness.processes.calls == [
        ("startup", "core", 0, ()),
        ("node-equals", "core", 0, ()),
        ("node-equals", "beautifulsoup4", 0, ()),
    ]
    assert [operation for operation, _ in harness.rendered] == ["startup", "node-equals"]


def test_targets_reuse_the_finished_turbohtml_runs_from_resume(harness: _Harness, tmp_path: Path) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"
    for index, case in enumerate(("a", "b")):
        finished = resume / "core" / "node-equals" / f"{index}.json"
        finished.parent.mkdir(parents=True, exist_ok=True)
        finished.write_text(
            json.dumps({f"node-equals|{case}|turbohtml": {"mean": 2.0, "stdev": 0.0}}), encoding="utf-8"
        )
    run(
        ("node-equals",),
        SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"beautifulsoup4"})),
    )
    assert harness.processes.calls == [
        ("node-equals", "beautifulsoup4", 0, ()),
        ("node-equals", "beautifulsoup4", 1, ()),
    ]
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|a|turbohtml": {"mean": 2.0, "stdev": 0.0},
        "node-equals|b|turbohtml": {"mean": 2.0, "stdev": 0.0},
        "node-equals|a|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
        "node-equals|b|beautifulsoup4": {"mean": 1.0, "stdev": 0.0},
    }


def test_a_skipped_target_contributes_nothing_even_when_a_sibling_case_succeeded(
    harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"
    # the timed-out case waits until its sibling has finished and been written, so the drop must clean up after it
    sibling_finished = threading.Event()
    monkeypatch.setattr(sys, "stderr", _StderrWatch("beautifulsoup4 node-equals b took", sibling_finished))

    def time_out() -> None:
        sibling_finished.wait(timeout=10)
        raise subprocess.TimeoutExpired(["worker"], 5400)

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = time_out
    run(
        ("node-equals",),
        SweepOptions(pyperf_args=(), pgo=False, jobs=2, resume=resume, targets=None),
    )
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|a|turbohtml": {"mean": 1.0, "stdev": 0.0},
        "node-equals|b|turbohtml": {"mean": 1.0, "stdev": 0.0},
    }
    assert sorted(path.relative_to(resume).as_posix() for path in resume.rglob("*.json")) == [
        "core/node-equals/0.json",
        "core/node-equals/1.json",
    ]


@pytest.mark.parametrize(
    ("signal_number", "error"),
    [
        pytest.param(signal.SIGTERM, SystemExit, id="sigterm"),
        pytest.param(signal.SIGINT, KeyboardInterrupt, id="sigint"),
    ],
)
def test_a_signal_stops_every_running_worker_and_gives_back_the_handler(
    harness: _Harness, signal_number: int, error: type[BaseException]
) -> None:
    previous = signal.getsignal(signal_number)

    def hold() -> None:
        signal.raise_signal(signal_number)
        harness.processes.killed.wait(timeout=10)

    harness.processes.hooks["node-equals", "core", 0] = hold
    with pytest.raises(error):
        run(
            ("node-equals",),
            SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=frozenset({"core"})),
        )
    assert (harness.processes.terminated, signal.getsignal(signal_number)) == ([("node-equals", "core", 0)], previous)


def test_unknown_target_is_rejected(harness: _Harness) -> None:
    with pytest.raises(SystemExit, match="unknown target nope; choose any of: core, "):
        run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=None, targets=frozenset({"nope"})))
    assert harness.processes.calls == []


def test_resume_skips_finished_runs_and_reads_them_back(harness: _Harness, tmp_path: Path) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"
    finished = resume / "core" / "node-equals" / "0.json"
    finished.parent.mkdir(parents=True)
    finished.write_text(json.dumps({"node-equals|a|turbohtml": {"mean": 2.0, "stdev": 0.0}}), encoding="utf-8")
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"core"})))
    assert harness.processes.calls == [("node-equals", "core", 1, ())]
    assert dict(harness.rendered)["node-equals"] == {
        "node-equals|a|turbohtml": {"mean": 2.0, "stdev": 0.0},
        "node-equals|b|turbohtml": {"mean": 1.0, "stdev": 0.0},
    }


def test_resume_writes_each_finished_run_and_stamps_the_source(harness: _Harness, tmp_path: Path) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"core"})))
    assert (resume / "source.txt").read_text(encoding="utf-8") == "tree-a\n"
    assert json.loads((resume / "core" / "node-equals" / "1.json").read_text(encoding="utf-8")) == {
        "node-equals|b|turbohtml": {"mean": 1.0, "stdev": 0.0},
    }


def test_resume_reports_how_many_runs_it_skipped(
    harness: _Harness, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"
    finished = resume / "core" / "node-equals" / "0.json"
    finished.parent.mkdir(parents=True)
    finished.write_text(json.dumps({"node-equals|a|turbohtml": {"mean": 2.0, "stdev": 0.0}}), encoding="utf-8")
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"core"})))
    assert f"resumed 1 of 2 runs from {resume}" in capsys.readouterr().err.splitlines()


def test_resume_leaves_a_skipped_competitor_unwritten_so_it_is_measured_again(
    harness: _Harness, tmp_path: Path
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    resume = tmp_path / "results"

    def time_out() -> None:
        raise subprocess.TimeoutExpired(["worker"], 5400)

    harness.processes.hooks["node-equals", "beautifulsoup4", 0] = time_out
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=None))
    assert sorted(path.relative_to(resume).as_posix() for path in resume.rglob("*.json")) == [
        "core/node-equals/0.json",
        "core/node-equals/1.json",
    ]
    harness.processes.calls.clear()
    harness.processes.hooks.clear()
    run(("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=None))
    assert sorted(call[:3] for call in harness.processes.calls) == [
        ("node-equals", "beautifulsoup4", 0),
        ("node-equals", "beautifulsoup4", 1),
    ]


def test_resume_rejects_results_from_another_tree(harness: _Harness, tmp_path: Path) -> None:
    resume = tmp_path / "results"
    resume.mkdir()
    (resume / "source.txt").write_text("tree-a\n", encoding="utf-8")
    harness.processes.stamp = "tree-b"
    with pytest.raises(SystemExit, match="results come from tree-a, this tree is tree-b"):
        run(
            ("node-equals",),
            SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"core"})),
        )
    assert harness.processes.calls == []


def test_resume_refuses_a_file_that_is_not_a_benchmark_result(harness: _Harness, tmp_path: Path) -> None:
    resume = tmp_path / "results"
    corrupt = resume / "core" / "node-equals" / "0.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(SystemExit, match="is not a benchmark result"):
        run(
            ("node-equals",),
            SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=frozenset({"core"})),
        )
    assert harness.processes.calls == []


def test_resume_needs_a_git_checkout_to_stamp_the_source(harness: _Harness, tmp_path: Path) -> None:
    harness.processes.stamp = None
    with pytest.raises(SystemExit, match="needs a git checkout"):
        run(
            ("node-equals",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=tmp_path / "results", targets=None)
        )
    assert harness.processes.calls == []


@pytest.mark.parametrize(
    ("resume", "targets"),
    [
        pytest.param(Path("results"), None, id="resume"),
        pytest.param(None, frozenset({"core"}), id="targets"),
    ],
)
def test_interpreters_rejects_resume_and_targets(
    harness: _Harness, resume: Path | None, targets: frozenset[str] | None
) -> None:
    with pytest.raises(SystemExit, match="do not apply to it"):
        run(("interpreters",), SweepOptions(pyperf_args=(), pgo=False, jobs=1, resume=resume, targets=targets))
    assert harness.processes.calls == []


def test_interpreters_command_builds_its_feed_outside_the_scheduler(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[tuple[Path, tuple[str, ...]]] = []

    def build(workdir: Path, pyperf_args: tuple[str, ...]) -> None:
        built.append((workdir, pyperf_args))

    monkeypatch.setattr(interpreters, "build", build)
    run(("interpreters",), SweepOptions(pyperf_args=("--rigorous",), pgo=False, jobs=1, resume=None, targets=None))
    assert [args for _, args in built] == [("--rigorous",)]
    assert harness.processes.calls == []


def test_run_worker_without_a_case_measures_every_case_and_ignores_an_inherited_selection(
    harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.processes.cases = {"node-equals": ["a", "b"]}
    monkeypatch.setenv("BENCH_CASE", "1")
    stats = orchestrator.run_worker(tmp_path / "venv-core", ("node-equals", "core", None), tmp_path, ())
    assert (stats, harness.processes.calls) == (
        {
            "node-equals|a|turbohtml": {"mean": 1.0, "stdev": 0.0},
            "node-equals|b|turbohtml": {"mean": 1.0, "stdev": 0.0},
        },
        [("node-equals", "core", None, ())],
    )
