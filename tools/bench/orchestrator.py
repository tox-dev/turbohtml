"""
Provision an isolated venv per target, run the worker in each, and render the merged tables.

Competitors are discovered statically: this module never imports a ``bench.competitors`` submodule (that would pull in
its library), it parses each one's ``_REQUIREMENTS`` and ``OPERATIONS`` keys out of the source. So the orchestrator
imports only stdlib plus the pure-data ``operations`` and the ``report`` renderer, and runs in a minimal environment.
It builds the turbohtml wheel once for the ``core`` baseline venv; every competitor venv installs only that competitor.
"""

from __future__ import annotations

import ast
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Final, TypeAlias

import pgo_build

from .corpus import prefetch
from .operations import OPERATIONS
from .report import render

if TYPE_CHECKING:
    from collections.abc import Iterator
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
_Job: TypeAlias = tuple[str, str]


def _string_tuple(node: ast.Tuple) -> tuple[str, ...]:
    # Pull the string literals out of a tuple assignment's right-hand side.
    return tuple(el.value for el in node.elts if isinstance(el, ast.Constant) and isinstance(el.value, str))


def _bindings(path: Path) -> Iterator[tuple[str, ast.expr | None]]:
    # Yield each top-level name a competitor module binds, from a plain or an annotated assignment, with its value.
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            yield node.target.id, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            yield node.targets[0].id, node.value


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


_COMPETITORS: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]] = _discover()


def run(commands: tuple[str, ...], pyperf_args: tuple[str, ...], *, pgo: bool, jobs: int) -> None:
    # Dispatch the CLI commands to the matching reports, forwarding any extra pyperf options to every worker.
    #
    # One workdir spans the whole run, so the (profile-guided, when ``pgo``) core wheel is built once and every venv
    # provisioned once, however many operations reuse them.
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

            interpreters.build(workdir, pyperf_args)
        else:
            plan = _plan(commands)
            pythons = _provision(workdir, _competitors(plan), pgo=pgo)
            _Schedule(plan, pythons, workdir=workdir, pyperf_args=pyperf_args, jobs=jobs).execute()


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


def _provision(workdir: Path, competitors: list[str], *, pgo: bool) -> dict[str, Path]:
    # Build every venv before the first worker runs, so no two runs ever provision a venv while another measures.
    #
    # Some competitors do not install on every toolchain -- newspaper3k pins long-unmaintained dependencies,
    # metadata_parser pins an older beautifulsoup4 -- so a *provisioning* failure drops just that competitor's column
    # with a note.
    pythons = {"core": baseline_python(workdir, pgo=pgo)}
    for competitor in competitors:
        requirements, _operation_names, pip_options = _COMPETITORS[competitor]
        try:
            pythons[competitor] = venv_python(workdir, competitor, requirements, pip_options)
        except subprocess.CalledProcessError:
            _report(f"skipping {competitor}: it did not install in its isolated venv")
    return pythons


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
        pythons: dict[str, Path],
        *,
        workdir: Path,
        pyperf_args: tuple[str, ...],
        jobs: int,
    ) -> None:
        self._pythons = pythons
        self._workdir = workdir
        self._pyperf_args = pyperf_args
        self._jobs = jobs
        self._cpus: queue.SimpleQueue[int] = queue.SimpleQueue()
        if jobs > 1:
            for cpu in physical_cpus():
                self._cpus.put(cpu)
        self._progress = _Progress(
            {
                operation: tuple(target for target in targets if target in pythons)
                for operation, targets in plan.items()
            },
            jobs,
        )
        self._failed = threading.Event()

    def execute(self) -> None:
        pool = ThreadPoolExecutor(max_workers=self._jobs)
        try:
            for future in as_completed([pool.submit(self._run, job) for job in self._progress.ordered()]):
                future.result()
        finally:
            pool.shutdown(cancel_futures=True)

    def _run(self, job: _Job) -> None:
        if self._failed.is_set():
            return
        self._progress.start(job)
        try:
            self._progress.finish(job, self._measure(job))
        except BaseException:
            # a worker thread would otherwise pick up the next queued run before the failure reaches the caller
            self._failed.set()
            raise

    def _measure(self, job: _Job) -> dict[str, dict[str, float | str]]:
        if self._jobs == 1:
            return self._dispatch(job, self._pyperf_args)
        cpu = self._cpus.get()
        try:
            return self._dispatch(job, (*self._pyperf_args, f"--affinity={cpu}"))
        finally:
            self._cpus.put(cpu)

    def _dispatch(self, job: _Job, pyperf_args: tuple[str, ...]) -> dict[str, dict[str, float | str]]:
        operation, target = job
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
        # A competitor that instead hangs -- runs past the per-operation timeout without finishing -- is killed and its
        # column dropped with a note, so one pathological library or input cannot stall the whole sweep.
        try:
            return run_worker(python, job, self._workdir, pyperf_args, capture=capture)
        except subprocess.TimeoutExpired:
            _report(f"skipping {target}: it did not finish {operation} within {_COMPETITOR_TIMEOUT:.0f}s")
            return {}
        except subprocess.CalledProcessError as error:
            if "importlib.import_module" not in (error.stderr or ""):
                raise
            _report(f"skipping {target}: it installed but failed to import in its isolated venv")
            return {}


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
    def __init__(self, plan: dict[str, tuple[str, ...]], jobs: int) -> None:
        self._plan = plan
        self._jobs = jobs
        try:
            loaded = json.loads(_DURATIONS_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            loaded = {}
        # a missing or corrupt cache counts as empty: the durations only steer the order and the ETA, never a result
        self._durations: dict[str, float] = loaded if _is_durations(loaded) else {}
        self._pending: list[_Job] = []
        for operation, targets in plan.items():
            self._pending.extend((operation, target) for target in targets)
        self._total = len(self._pending)
        self._running: dict[_Job, float] = {}
        self._results: dict[_Job, dict[str, dict[str, float | str]]] = {}
        self._seconds: list[float] = []
        self._unrendered: deque[str] = deque(plan)
        self._started_at = time.monotonic()

    def ordered(self) -> list[_Job]:
        if self._jobs == 1:
            return list(self._pending)
        # a run timed in an earlier sweep starts first, so the longest one is never left for the tail
        return sorted(self._pending, key=lambda job: -self._durations.get(_duration_key(job), -math.inf))

    def start(self, job: _Job) -> None:
        with _OUTPUT_LOCK:
            self._pending.remove(job)
            self._running[job] = time.monotonic()

    def finish(self, job: _Job, stats: dict[str, dict[str, float | str]]) -> None:
        operation, target = job
        with _OUTPUT_LOCK:
            now = time.monotonic()
            seconds = now - self._running.pop(job)
            self._durations[_duration_key(job)] = seconds
            _save_durations(self._durations)
            self._seconds.append(seconds)
            self._results[job] = stats
            self._render_ready()
            print(
                f"[{len(self._results)}/{self._total}] {target} {operation} took {_clock(seconds)}; "
                f"elapsed {_clock(now - self._started_at)}, eta {_clock(self._eta(now))}",
                file=sys.stderr,
            )

    def _render_ready(self) -> None:
        while self._unrendered:
            operation = self._unrendered[0]
            targets = self._plan[operation]
            if any((operation, target) not in self._results for target in targets):
                return
            stats: dict[str, dict[str, float | str]] = {}
            for target in targets:
                stats.update(self._results[operation, target])
            render(operation, stats)
            self._unrendered.popleft()

    def _eta(self, now: float) -> float:
        fallback = fmean(self._seconds)
        pending = sum(self._durations.get(_duration_key(job), fallback) for job in self._pending)
        remaining = sum(
            max(0.0, self._durations.get(_duration_key(job), fallback) - (now - started))
            for job, started in self._running.items()
        )
        return (pending + remaining) / self._jobs


def _is_durations(value: object) -> TypeIs[dict[str, float]]:
    return isinstance(value, dict) and all(
        isinstance(key, str) and isinstance(seconds, int | float) for key, seconds in value.items()
    )


def _duration_key(job: _Job) -> str:
    operation, target = job
    return f"{target}:{operation}"


def _save_durations(durations: dict[str, float]) -> None:
    # Write through a temporary file, so an interrupted sweep never leaves a half-written cache behind.
    _DURATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    staging = _DURATIONS_FILE.with_name(f"{_DURATIONS_FILE.name}.tmp")
    staging.write_text(json.dumps(durations, indent=2, sort_keys=True), encoding="utf-8")
    staging.replace(_DURATIONS_FILE)


def _clock(seconds: float) -> str:
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m{rest:02d}s"


def run_worker(
    python: Path,
    job: _Job,
    workdir: Path,
    pyperf_args: tuple[str, ...],
    *,
    capture: bool = False,
) -> dict[str, dict[str, float | str]]:
    # Run the worker for one (operation, target) in the given venv and return its per-case stats.
    #
    # A competitor is bounded by ``_COMPETITOR_TIMEOUT``: past it the worker is killed and
    # :class:`subprocess.TimeoutExpired` propagates, so a library that hangs on one input cannot freeze the sweep. The
    # core baseline runs untimed -- turbohtml is the thing under test, so its slow cases are measured, never dropped.
    operation, target = job
    timeout = None if target == "core" else _COMPETITOR_TIMEOUT
    out = workdir / f"{target}-{operation}.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(_TOOLS_DIR),
        "BENCH_TARGET": target,
        "BENCH_OPERATION": operation,
        "BENCH_OUT": str(out),
    }
    # stderr is captured so a failure carries the traceback (the caller reads it to tell an import-time environment
    # clash from a real measurement crash); stdout stays inherited so pyperf's per-case lines stream live, unless a
    # parallel job captures it to print as one block once the worker ends.
    try:
        completed = subprocess.run(
            [str(python), "-m", "bench.worker", *pyperf_args],
            check=True,
            env=env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        with _OUTPUT_LOCK:
            sys.stderr.write(error.stderr or "")
        raise
    if capture:
        with _OUTPUT_LOCK:
            sys.stdout.write(completed.stdout)
    return json.loads(out.read_text(encoding="utf-8"))


__all__ = [
    "REPO_ROOT",
    "baseline_python",
    "physical_cpus",
    "run",
    "run_worker",
    "venv_python",
]
