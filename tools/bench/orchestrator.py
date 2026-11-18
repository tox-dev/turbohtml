"""
Provision an isolated venv per target, run the worker in each, and render the merged tables.

Competitors are discovered statically: this module never imports a ``bench.competitors`` submodule (that would pull in
its library), it parses each one's ``_REQUIREMENTS`` and ``OPERATIONS`` keys out of the source. So the orchestrator
imports only stdlib plus the pure-data ``operations`` and the ``report`` renderer, and runs in a minimal environment.
It builds the turbohtml wheel once for the ``core`` baseline venv; every competitor venv installs only that competitor.
"""

from __future__ import annotations

import ast
import contextlib
import json
import math
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from itertools import chain, product
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Final, TypeAlias

import pgo_build

from .corpus import prefetch
from .operations import OPERATIONS
from .report import render

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from types import FrameType
    from typing import TypeIs

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_TOOLS_DIR: Final[Path] = Path(__file__).resolve().parents[1]
_COMPETITOR_DIR: Final[Path] = Path(__file__).resolve().parent / "competitors"
_COMPETITOR_TIMEOUT: Final[float] = (
    5400.0  # seconds for one competitor's whole operation; generous so a slow library that reparses
)
# the page every mutation iteration (BeautifulSoup) or walks multi-megabyte input (dominate) is measured, not dropped
_CPU_SYSFS: Final[Path] = Path("/sys/devices/system/cpu")
_DURATIONS_FILE: Final[Path] = _TOOLS_DIR / ".bench_data" / "durations.json"
# jobs print their output and update the shared progress from worker threads; one lock keeps each write whole
_OUTPUT_LOCK: Final[threading.Lock] = threading.Lock()
_Job: TypeAlias = tuple[str, str, int]
_Stats: TypeAlias = dict[str, dict[str, float | str]]


@dataclass(frozen=True, slots=True, kw_only=True)
class SweepOptions:
    # What the command line asks of one run: the pyperf passthrough, the baseline build, and how the sweep is kept.
    pyperf_args: tuple[str, ...]
    pgo: bool
    jobs: int
    resume: Path | None
    targets: frozenset[str] | None


def _discover() -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]:
    # Read each competitor's _REQUIREMENTS, OPERATIONS keys, and optional _PIP_OPTIONS from source, without importing.
    found: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    for path in sorted(_COMPETITOR_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        requirements: tuple[str, ...] = ()
        operation_names: tuple[str, ...] = ()
        pip_options: tuple[str, ...] = ()
        for name, value in _bindings(path):
            if name == "_REQUIREMENTS" and isinstance(value, ast.Tuple):
                requirements = _string_tuple(value)
            elif name == "_PIP_OPTIONS" and isinstance(value, ast.Tuple):
                pip_options = _string_tuple(value)
            elif name == "OPERATIONS" and isinstance(value, ast.Dict):
                operation_names = tuple(
                    key.value for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
        found[path.stem] = (requirements, operation_names, pip_options)
    return found


def _bindings(path: Path) -> Iterator[tuple[str, ast.expr | None]]:
    # Yield each top-level name a competitor module binds, from a plain or an annotated assignment, with its value.
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            yield node.targets[0].id, node.value


def _string_tuple(node: ast.Tuple) -> tuple[str, ...]:
    # Pull the string literals out of a tuple assignment's right-hand side.
    return tuple(el.value for el in node.elts if isinstance(el, ast.Constant) and isinstance(el.value, str))


_COMPETITORS: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]] = _discover()


def run(commands: tuple[str, ...], options: SweepOptions) -> None:
    # Dispatch the CLI commands to the matching reports, forwarding any extra pyperf options to every worker.
    #
    # One workdir spans the whole run, so the (profile-guided, when ``options.pgo``) core wheel is built once and every
    # venv provisioned once, however many operations reuse them.
    if commands == ("interpreters",) and (options.resume is not None or options.targets is not None):
        msg = "interpreters runs outside the sweep, so --resume and --targets do not apply to it"
        raise SystemExit(msg)
    print(
        "tip: for low-noise results run `pyperf system tune` first (and `sudo pyperf system reset` after); pass "
        "pyperf options like --rigorous or --affinity=<cpu> after the command to control sampling and CPU pinning.",
        file=sys.stderr,
    )
    prefetch()  # the worker venvs carry no HTTP client; fill the download cache here, where one is installed
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        if commands == ("interpreters",):
            from . import (  # ruff:ignore[import-outside-top-level]  # imports orchestrator, so bind it late
                interpreters,
            )

            interpreters.build(workdir, options.pyperf_args)
        else:
            plan = _select_targets(_plan(commands), options.targets)
            if options.resume is not None:
                _check_source(options.resume)
            baseline = baseline_python(workdir, pgo=options.pgo)
            cases = _list_cases(baseline, tuple(plan))
            pythons = _provision(workdir, _competitors(plan), baseline)
            _Schedule(plan, cases, pythons, workdir=workdir, options=options).execute()


def _select_targets(plan: dict[str, tuple[str, ...]], targets: frozenset[str] | None) -> dict[str, tuple[str, ...]]:
    # turbohtml is every table's baseline, so each operation keeps it; the chosen competitors join it.
    if targets is None:
        return plan
    if unknown := targets - {"core", *_COMPETITORS}:
        msg = f"unknown target {', '.join(sorted(unknown))}; choose any of: core, {', '.join(_COMPETITORS)}"
        raise SystemExit(msg)
    return {
        operation: ("core", *(name for name in names if name != "core" and name in targets))
        for operation, names in plan.items()
    }


def _plan(commands: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    # Map each operation to the targets whose runs fill its table: turbohtml first, then competitors in discovery order.
    if len(commands) > 1:
        # One run covers them all: one baseline build and one schedule, rendered in the order given.
        for command in commands:
            if command in _COMPETITORS or command in {"core", "all", "interpreters"}:
                msg = f"{command!r} runs alone; several at once take operation names only"
                raise SystemExit(msg)
            if command not in OPERATIONS:
                raise _unknown(command)
        return {operation: ("core", *_packages_for(operation)) for operation in commands}
    (command,) = commands
    if command in _COMPETITORS:
        return dict.fromkeys(_COMPETITORS[command][1], ("core", command))
    if command == "core":
        return dict.fromkeys(OPERATIONS, ("core",))
    if command == "all":
        return {operation: ("core", *_packages_for(operation)) for operation in OPERATIONS}
    if command in OPERATIONS:
        return {command: ("core", *_packages_for(command))}
    raise _unknown(command)


def _unknown(command: str) -> SystemExit:
    choices = ", ".join(["core", "all", "interpreters", *OPERATIONS, *_COMPETITORS])
    return SystemExit(f"unknown command {command!r}; choose one of: {choices}")


def _packages_for(operation: str) -> list[str]:
    # Every competitor module that implements the operation, in discovery order.
    return [name for name, (_, operation_names, _pip) in _COMPETITORS.items() if operation in operation_names]


def _check_source(resume: Path) -> None:
    # A directory keeps the tree it was first filled from, so results from two code states never share one table.
    # ``--dirty`` stamps uncommitted edits too, so changed code never passes for the tree the results came from.
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "describe", "--always", "--dirty", "--abbrev=40"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        msg = "--resume needs a git checkout to stamp where the results came from"
        raise SystemExit(msg) from error
    stamp = completed.stdout.strip()
    recorded = resume / "source.txt"
    if not recorded.exists():
        _write_atomic(recorded, f"{stamp}\n")
        return
    if (previous := recorded.read_text(encoding="utf-8").strip()) != stamp:
        msg = f"results come from {previous}, this tree is {stamp}; use a fresh directory"
        raise SystemExit(msg)


def _write_atomic(path: Path, text: str) -> None:
    # Write through a temporary file, so an interrupted sweep never leaves a half-written file behind.
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f"{path.name}.tmp")
    staging.write_text(text, encoding="utf-8")
    staging.replace(path)


def baseline_python(workdir: Path, *, pgo: bool) -> Path:
    # Provision the turbohtml baseline venv, plain by default or with the shipped PGO+LTO release recipe when ``pgo``.
    #
    # The plain path builds the turbohtml wheel and installs it -- fast, good for iterating. The ``pgo`` path
    # instead reproduces what ships: the venv gets the build backend, then :func:`pgo_build.build` drives the two-phase
    # profile-guided, link-time-optimized editable install (``--no-build-isolation``, so the backend has to live in the
    # venv), leaving the baseline measured against a release-representative binary.
    if not pgo:
        _uv("build", "--wheel", "--out-dir", str(workdir), str(REPO_ROOT))
        return venv_python(workdir, "core", (str(next(workdir.glob("*.whl"))),))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    python = venv_python(workdir, "core", tuple(pyproject["build-system"]["requires"]))
    pgo_build.build(str(python), workdir / "core-pgo-build", REPO_ROOT, "full", system=False)
    return python


def _uv(*args: str) -> None:
    # Run a uv subcommand, surfacing its output only on failure.
    subprocess.run(["uv", *args], check=True, capture_output=True, text=True)


def venv_python(
    workdir: Path,
    name: str,
    reqs: tuple[str, ...],
    pip_options: tuple[str, ...] = (),
    interpreter: str | None = None,
) -> Path:
    # Provision an isolated venv holding pyperf and the given requirements; return its interpreter.
    #
    # ``interpreter`` names the uv Python spec to build the venv on, for the interpreter comparison that runs the same
    # turbohtml under several of them; the default takes whichever Python uv resolves.
    #
    # Idempotent within one workdir: a target that already has its venv (a competitor appearing in several operations of
    # an ``all`` sweep, or the core baseline shared across every operation) is provisioned once and reused, so the whole
    # matrix pays each install -- and the one-time profile-guided core build -- exactly once instead of per operation.
    venv = workdir / f"venv-{name}"
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    if python.exists():
        return python
    _uv("venv", *(("--python", interpreter) if interpreter else ()), str(venv))
    _uv("pip", "install", "--python", str(python), "pyperf>=2.10", *pip_options, *reqs)
    return python


def _list_cases(baseline: Path, operations: tuple[str, ...]) -> dict[str, list[str]]:
    # Read every operation's case names from the core venv, so each case becomes its own job before anything measures.
    try:
        completed = subprocess.run(
            [str(baseline), "-m", "bench.worker"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(_TOOLS_DIR), "BENCH_LIST": json.dumps(operations)},
        )
    except subprocess.CalledProcessError as error:
        msg = f"could not list the cases of {', '.join(operations)}: {error.stderr.strip()}"
        raise SystemExit(msg) from error
    listing = json.loads(completed.stdout)
    if not _is_listing(listing):
        msg = f"the core venv listed {completed.stdout.strip()!r}, not case names per operation"
        raise SystemExit(msg)
    return listing


def _is_listing(value: object) -> TypeIs[dict[str, list[str]]]:
    return isinstance(value, dict) and all(isinstance(names, list) for names in value.values())


def _provision(workdir: Path, competitors: list[str], baseline: Path) -> dict[str, Path]:
    # Build every venv before the first worker runs, so no two runs ever provision a venv while another measures.
    #
    # Some competitors do not install on every toolchain -- newspaper3k pins long-unmaintained dependencies,
    # metadata_parser pins an older beautifulsoup4 -- so a *provisioning* failure drops just that competitor's column
    # with a note.
    pythons = {"core": baseline}
    for competitor in competitors:
        requirements, _operation_names, pip_options = _COMPETITORS[competitor]
        try:
            pythons[competitor] = venv_python(workdir, competitor, requirements, pip_options)
        except subprocess.CalledProcessError:
            _report(f"skipping {competitor}: it did not install in its isolated venv")
    return pythons


def _report(message: str) -> None:
    # Print a note under the output lock, so it never lands inside another job's block.
    with _OUTPUT_LOCK:
        print(message, file=sys.stderr)


def _competitors(plan: dict[str, tuple[str, ...]]) -> list[str]:
    # Every competitor the plan runs, each once, in the order the plan first meets it.
    found: dict[str, None] = {}
    for targets in plan.values():
        found.update(dict.fromkeys(target for target in targets if target != "core"))
    return list(found)


class _Schedule:
    def __init__(
        self,
        plan: dict[str, tuple[str, ...]],
        cases: dict[str, list[str]],
        pythons: dict[str, Path],
        *,
        workdir: Path,
        options: SweepOptions,
    ) -> None:
        self._pythons = pythons
        self._workdir = workdir
        self._pyperf_args = options.pyperf_args
        self._jobs = options.jobs
        self._cpus: queue.SimpleQueue[int] = queue.SimpleQueue()
        if self._jobs > 1:
            for cpu in physical_cpus():
                self._cpus.put(cpu)
        self._progress = _Progress(_operation_jobs(plan, cases, pythons), cases, options.resume, self._jobs)
        self._failed = threading.Event()

    def execute(self) -> None:
        with _stop_on_signals() as received:
            pool = ThreadPoolExecutor(max_workers=self._jobs)
            try:
                self._wait_for_futures(pool, received)
            except BaseException:
                # the first failure is the one reported; the workers stopped here fail too, and their errors go unread
                self._failed.set()
                raise
            finally:
                _WORKERS.stop()
                pool.shutdown(cancel_futures=True)
                _WORKERS.reopen()

    def _wait_for_futures(self, pool: ThreadPoolExecutor, received: list[int]) -> None:
        pending = {pool.submit(self._run, job) for job in self._progress.ordered()}
        while pending:
            # a timed wait returns to the interpreter now and then, so a recorded signal is seen even where a blocking
            # wait ignores Ctrl-C (Windows)
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            if received:
                _WORKERS.stop()
                pool.shutdown(cancel_futures=True)
                if received[0] == signal.SIGINT:
                    raise KeyboardInterrupt
                raise SystemExit(128 + received[0])
            for future in done:
                future.result()

    def _run(self, job: _Job) -> None:
        if self._failed.is_set() or not self._progress.start(job):
            return
        try:
            stats = self._measure(job)
        except _SkipError as skipped:
            self._progress.drop(job, str(skipped))
            return
        except BaseException:
            # a worker thread would otherwise pick up the next queued run before the failure reaches the caller
            self._failed.set()
            raise
        self._progress.finish(job, stats)

    def _measure(self, job: _Job) -> _Stats:
        if self._jobs == 1:
            return self._dispatch(job, self._pyperf_args)
        cpu = self._cpus.get()
        try:
            return self._dispatch(job, (*self._pyperf_args, f"--affinity={cpu}"))
        finally:
            self._cpus.put(cpu)

    def _dispatch(self, job: _Job, pyperf_args: tuple[str, ...]) -> _Stats:
        operation, target, _case = job
        python = self._pythons[target]
        capture = self._jobs > 1
        if target == "core":
            return run_worker(python, job, self._workdir, pyperf_args, capture=capture)
        # A measurement failure propagates and fails the benchmark, except the two a competitor can be blamed for.
        #
        # A competitor can install cleanly yet fail to *import*, when its bundled native library clashes with another
        # wheel's ABI in the shared venv (html5-parser returns an lxml tree, so the two must share one libxml2, which
        # its ``_PIP_OPTIONS`` secures by building lxml from source); that is the same environment quirk one import
        # boundary later, so it drops the column with a note too.
        #
        # Only a crash *after* the library imported -- during measurement, where the fault is the benchmark and not the
        # toolchain -- is left to propagate rather than silently blanking the column. The two are told apart by whether
        # the worker died inside ``importlib.import_module``.
        #
        # A competitor that instead hangs -- runs past the per-case timeout without finishing -- is killed and its
        # column dropped with a note, so one pathological library or input cannot stall the whole sweep.
        try:
            return run_worker(python, job, self._workdir, pyperf_args, capture=capture)
        except subprocess.TimeoutExpired as error:
            msg = f"skipping {target}: it did not finish {operation} within {_COMPETITOR_TIMEOUT:.0f}s"
            raise _SkipError(msg) from error
        except subprocess.CalledProcessError as error:
            if "importlib.import_module" not in (error.stderr or ""):
                raise
            msg = f"skipping {target}: it installed but failed to import in its isolated venv"
            raise _SkipError(msg) from error


def physical_cpus() -> list[int]:
    # Two hardware threads of one core share its execution units, so one CPU per core keeps concurrent jobs from
    # measuring each other; the lowest usable sibling stands for each core.
    usable = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set(range(os.cpu_count() or 1))
    return [cpu for cpu in sorted(usable) if _is_first_sibling(cpu, usable)]


def _is_first_sibling(cpu: int, usable: set[int]) -> bool:
    try:
        siblings = _cpu_list(
            (_CPU_SYSFS / f"cpu{cpu}" / "topology" / "thread_siblings_list").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return True
    return cpu == min(siblings & usable)


def _cpu_list(text: str) -> set[int]:
    # Expand a sysfs CPU list such as ``0,16`` or ``0-3,8``.
    cpus: set[int] = set()
    for part in text.split(","):
        low, _, high = part.partition("-")
        cpus.update(range(int(low), int(high or low) + 1))
    return cpus


class _Progress:
    def __init__(
        self,
        jobs_by_operation: dict[str, list[_Job]],
        cases: dict[str, list[str]],
        resume: Path | None,
        jobs: int,
    ) -> None:
        self._jobs_by_operation = jobs_by_operation
        self._cases = cases
        self._jobs = jobs
        self._resume = resume
        try:
            loaded = json.loads(_DURATIONS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            loaded = {}
        # a missing or corrupt cache counts as empty: the durations only steer the order and the ETA, never a result
        self._durations: dict[str, float] = loaded if _is_durations(loaded) else {}
        all_jobs = list(chain.from_iterable(jobs_by_operation.values()))
        self._results: dict[_Job, _Stats] = _load_results(resume, all_jobs) if resume is not None else {}
        if self._results:
            _report(f"resumed {len(self._results)} of {len(all_jobs)} runs from {resume}")
        self._pending: dict[_Job, None] = dict.fromkeys(job for job in all_jobs if job not in self._results)
        self._running: dict[_Job, float] = {}
        self._dropped: set[tuple[str, str]] = set()
        self._seconds: list[float] = []
        self._unrendered: deque[str] = deque(jobs_by_operation)
        self._started_at = time.monotonic()
        self._render_ready()

    def _render_ready(self) -> None:
        while self._unrendered:
            operation = self._unrendered[0]
            jobs = self._jobs_by_operation[operation]
            if any(job not in self._results for job in jobs):
                return
            stats: _Stats = {}
            for job in jobs:
                if (job[0], job[1]) not in self._dropped:
                    stats.update(self._results[job])
            render(operation, stats)
            self._unrendered.popleft()

    def ordered(self) -> list[_Job]:
        if self._jobs == 1:
            return list(self._pending)
        # a run timed in an earlier sweep starts first, so the longest one is never left for the tail
        return sorted(self._pending, key=lambda job: -self._durations.get(_duration_key(job), -math.inf))

    def start(self, job: _Job) -> bool:
        operation, target, _case = job
        with _OUTPUT_LOCK:
            if (operation, target) in self._dropped:
                return False
            del self._pending[job]
            self._running[job] = time.monotonic()
            return True

    def finish(self, job: _Job, stats: _Stats) -> None:
        operation, target, case = job
        with _OUTPUT_LOCK:
            now = time.monotonic()
            seconds = now - self._running.pop(job)
            self._durations[_duration_key(job)] = seconds
            _write_atomic(_DURATIONS_FILE, json.dumps(self._durations, indent=2, sort_keys=True))
            self._seconds.append(seconds)
            self._results[job] = stats
            if self._resume is not None and (operation, target) not in self._dropped:
                _write_atomic(_result_path(self._resume, job), json.dumps(stats, indent=2, sort_keys=True))
            self._render_ready()
            print(
                f"[{len(self._results)}/{sum(map(len, self._jobs_by_operation.values()))}] "
                f"{target} {operation} {self._cases[operation][case]} "
                f"took {_clock(seconds)}; elapsed {_clock(now - self._started_at)}, eta {_clock(self._eta(now))}",
                file=sys.stderr,
            )

    def _eta(self, now: float) -> float:
        fallback = fmean(self._seconds)
        pending = sum(self._durations.get(_duration_key(job), fallback) for job in self._pending)
        remaining = sum(
            max(0.0, self._durations.get(_duration_key(job), fallback) - (now - started))
            for job, started in self._running.items()
        )
        return (pending + remaining) / self._jobs

    def drop(self, job: _Job, note: str) -> None:
        # A skipped competitor contributes nothing to its operation: its runs are neither rendered nor kept on disk.
        operation, target, _case = job
        with _OUTPUT_LOCK:
            del self._running[job]
            self._results[job] = {}
            if (operation, target) not in self._dropped:
                self._dropped.add((operation, target))
                print(note, file=sys.stderr)
                for waiting in [waiting for waiting in self._pending if waiting[:2] == (operation, target)]:
                    del self._pending[waiting]
                    self._results[waiting] = {}
                if self._resume is not None:
                    for index in range(len(self._cases[operation])):
                        _result_path(self._resume, (operation, target, index)).unlink(missing_ok=True)
            self._render_ready()


def _is_durations(value: object) -> TypeIs[dict[str, float]]:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(seconds, int | float) for key, seconds in value.items()
    )


def _load_results(resume: Path, jobs: list[_Job]) -> dict[_Job, _Stats]:
    # A run an earlier sweep finished is read back instead of measured again; a run that never finished has no file.
    loaded: dict[_Job, _Stats] = {}
    for job in jobs:
        path = _result_path(resume, job)
        if path.exists():
            stats = json.loads(path.read_text(encoding="utf-8"))
            if not _is_stats(stats):
                msg = f"{path} is not a benchmark result; remove it to measure that run again"
                raise SystemExit(msg)
            loaded[job] = stats
    return loaded


def _is_stats(value: object) -> TypeIs[_Stats]:
    return isinstance(value, dict) and all(isinstance(entry, dict) for entry in value.values())


def _duration_key(job: _Job) -> str:
    operation, target, case = job
    return f"{target}:{operation}:{case}"


def _result_path(resume: Path, job: _Job) -> Path:
    operation, target, case = job
    return resume / target / operation / f"{case}.json"


def _clock(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m{rest:02d}s"


def _operation_jobs(
    plan: dict[str, tuple[str, ...]], cases: dict[str, list[str]], pythons: dict[str, Path]
) -> dict[str, list[_Job]]:
    # Every (operation, target, case) run, targets outermost, so the jobs of one operation come in table order.
    jobs: dict[str, list[_Job]] = {}
    for operation, targets in plan.items():
        count = len(cases[operation])
        jobs[operation] = [
            (operation, target, index) for target, index in product(targets, range(count)) if target in pythons
        ]
    return jobs


@contextlib.contextmanager
def _stop_on_signals() -> Generator[list[int], None, None]:
    # Workers lead their own sessions, so a terminal's Ctrl-C never reaches them; the sweep's wait loop turns a
    # recorded signal into the cleanup that stops every worker group. The handler only appends: Python runs it between
    # any two bytecodes of the main thread, so raising there could land inside a lock the sweep holds, and an Event's
    # set() takes a lock of its own.
    received: list[int] = []

    def record(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)

    previous = {number: signal.signal(number, record) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield received
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class _SkipError(Exception):
    # A competitor run that could not be measured; its message is the note saying why the column is dropped.
    pass


def run_worker(
    python: Path,
    job: tuple[str, str, int | None],
    workdir: Path,
    pyperf_args: tuple[str, ...],
    *,
    capture: bool = False,
) -> _Stats:
    # Run the worker for one (operation, target, case) in the given venv and return its stats; a ``None`` case measures
    # every case of the operation.
    #
    # A competitor is bounded by ``_COMPETITOR_TIMEOUT``: past it the worker's process group is killed and
    # :class:`subprocess.TimeoutExpired` propagates, so a library that hangs on one input cannot freeze the sweep. The
    # core baseline runs untimed -- turbohtml is the thing under test, so its slow cases are measured, never dropped.
    operation, target, case = job
    timeout = None if target == "core" else _COMPETITOR_TIMEOUT
    out = workdir / f"{target}-{operation}-{'all' if case is None else case}.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(_TOOLS_DIR),
        "BENCH_TARGET": target,
        "BENCH_OPERATION": operation,
        "BENCH_OUT": str(out),
    }
    env.pop("BENCH_CASE", None)  # an inherited selection would silently narrow an operation's sweep to one case
    if case is not None:
        env["BENCH_CASE"] = str(case)
    # stderr is captured so a failure carries the traceback (the caller reads it to tell an import-time environment
    # clash from a real measurement crash); stdout stays inherited so pyperf's per-case lines stream live, unless a
    # parallel job captures it to print as one block once the worker ends.
    process = subprocess.Popen(
        [str(python), "-m", "bench.worker", *pyperf_args],
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _WORKERS.add(process)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate(process)
        process.communicate()
        raise
    finally:
        _WORKERS.discard(process)
    if process.returncode:
        with _OUTPUT_LOCK:
            sys.stderr.write(stderr or "")
        raise subprocess.CalledProcessError(process.returncode, process.args, output=stdout, stderr=stderr)
    if capture:
        with _OUTPUT_LOCK:
            sys.stdout.write(stdout)
    return json.loads(out.read_text(encoding="utf-8"))


def _terminate(process: subprocess.Popen[str]) -> None:
    # Windows has no process groups, so a stop can only signal the worker itself there.
    with contextlib.suppress(ProcessLookupError):  # the group may exit between the check and the signal
        process.kill() if os.name == "nt" else os.killpg(process.pid, signal.SIGTERM)


class _Workers:
    # Workers lead their own sessions, so a terminal's Ctrl-C never reaches them: a failure stops each one from here.
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[subprocess.Popen[str]] = set()
        self._stopped = False

    def reopen(self) -> None:
        # once a sweep's pool has shut down nothing of it can start again, so later runs in this process start clean
        with self._lock:
            self._stopped = False

    def add(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._running.add(process)
            if self._stopped:
                _terminate(process)

    def discard(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._running.discard(process)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            for process in self._running:
                _terminate(process)


_WORKERS: Final[_Workers] = _Workers()


__all__ = [
    "REPO_ROOT",
    "SweepOptions",
    "baseline_python",
    "physical_cpus",
    "run",
    "run_worker",
    "venv_python",
]
