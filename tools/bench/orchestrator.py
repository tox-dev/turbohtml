"""
Provision an isolated venv per target, run the worker in each, and render the merged tables.

Competitors are discovered statically: this module never imports a ``bench.competitors`` submodule (that would pull in
its library), it parses each one's ``REQUIREMENTS`` and ``OPERATIONS`` keys out of the source. So the orchestrator
imports only stdlib plus the pure-data ``operations`` and the ``report`` renderer, and runs in a minimal environment.
It builds the turbohtml wheel once for the ``core`` baseline venv; every competitor venv installs only that competitor.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pgo_build
import tomllib

from bench import corpus, operations, report

REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = Path(__file__).resolve().parents[1]
_COMPETITOR_DIR = Path(__file__).resolve().parent / "competitors"
_COMPETITOR_TIMEOUT = 5400.0  # seconds for one competitor's whole operation; generous so a slow library that reparses
# the page every mutation iteration (BeautifulSoup) or walks multi-megabyte input (dominate) is measured, not dropped


def _string_tuple(node: ast.Tuple) -> tuple[str, ...]:
    """Pull the string literals out of a tuple assignment's right-hand side."""
    return tuple(el.value for el in node.elts if isinstance(el, ast.Constant) and isinstance(el.value, str))


def _discover() -> dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]]:
    """Read each competitor's REQUIREMENTS, OPERATIONS keys, and optional PIP_OPTIONS from source, without importing."""
    found: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {}
    for path in sorted(_COMPETITOR_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        requirements: tuple[str, ...] = ()
        operation_names: tuple[str, ...] = ()
        pip_options: tuple[str, ...] = ()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            if node.targets[0].id == "REQUIREMENTS" and isinstance(node.value, ast.Tuple):
                requirements = _string_tuple(node.value)
            elif node.targets[0].id == "PIP_OPTIONS" and isinstance(node.value, ast.Tuple):
                pip_options = _string_tuple(node.value)
            elif node.targets[0].id == "OPERATIONS" and isinstance(node.value, ast.Dict):
                operation_names = tuple(
                    key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
        found[path.stem] = (requirements, operation_names, pip_options)
    return found


COMPETITORS = _discover()


def _packages_for(operation: str) -> list[str]:
    """Every competitor module that implements the operation, in discovery order."""
    return [name for name, (_, operation_names, _pip) in COMPETITORS.items() if operation in operation_names]


def _uv(*args: str) -> None:
    """Run a uv subcommand, surfacing its output only on failure."""
    subprocess.run(["uv", *args], check=True, capture_output=True, text=True)


def _build_wheel(workdir: Path) -> Path:
    """Build the turbohtml wheel once; only the core baseline venv installs it."""
    _uv("build", "--wheel", "--out-dir", str(workdir), str(REPO_ROOT))
    return next(workdir.glob("*.whl"))


def venv_python(
    workdir: Path,
    name: str,
    reqs: tuple[str, ...],
    pip_options: tuple[str, ...] = (),
    interpreter: str | None = None,
) -> Path:
    """
    Provision an isolated venv holding pyperf and the given requirements; return its interpreter.

    ``interpreter`` names the uv Python spec to build the venv on, for the interpreter comparison that runs the same
    turbohtml under several of them; the default takes whichever Python uv resolves.

    Idempotent within one workdir: a target that already has its venv (a competitor appearing in several operations of
    an ``all`` sweep, or the core baseline shared across every operation) is provisioned once and reused, so the whole
    matrix pays each install -- and the one-time profile-guided core build -- exactly once instead of per operation.
    """
    venv = workdir / f"venv-{name}"
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    if python.exists():
        return python
    _uv("venv", *(("--python", interpreter) if interpreter else ()), str(venv))
    _uv("pip", "install", "--python", str(python), "pyperf>=2.10", *pip_options, *reqs)
    return python


def _core_python(workdir: Path, *, pgo: bool) -> Path:
    """
    Provision the turbohtml baseline venv, plain by default or with the shipped PGO+LTO release recipe when ``pgo``.

    The plain path installs the wheel :func:`_build_wheel` builds -- fast, good for iterating. The ``pgo`` path instead
    reproduces what ships: the venv gets the build backend, then :func:`pgo_build.build` drives the two-phase
    profile-guided, link-time-optimized editable install (``--no-build-isolation``, so the backend has to live in the
    venv), leaving the baseline measured against a release-representative binary.
    """
    if not pgo:
        return venv_python(workdir, "core", (str(_build_wheel(workdir)),))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build_backend = tuple(pyproject["build-system"]["requires"])
    python = venv_python(workdir, "core", build_backend)
    pgo_build.build(str(python), workdir / "core-pgo-build", REPO_ROOT, "full", system=False)
    return python


def run_worker(
    python: Path, target: str, operation: str, workdir: Path, pyperf_args: tuple[str, ...]
) -> dict[str, dict[str, float | str]]:
    """
    Run the worker for one (target, operation) in the given venv and return its per-case stats.

    A competitor is bounded by ``_COMPETITOR_TIMEOUT``: past it the worker is killed and
    :class:`subprocess.TimeoutExpired` propagates, so a library that hangs on one input cannot freeze the sweep. The
    core baseline runs untimed -- turbohtml is the thing under test, so its slow cases are measured, never dropped.
    """
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
    # clash from a real measurement crash); stdout stays inherited so pyperf's per-case lines stream live.
    try:
        subprocess.run(
            [str(python), "-m", "bench.worker", *pyperf_args],
            check=True,
            env=env,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        sys.stderr.write(error.stderr or "")
        raise
    return json.loads(out.read_text(encoding="utf-8"))


def _try_competitor(
    workdir: Path, competitor: str, operation: str, pyperf_args: tuple[str, ...]
) -> dict[str, dict[str, float | str]]:
    """
    Provision the competitor's venv then run it; a measurement failure propagates and fails the benchmark.

    Some competitors do not install on every toolchain -- newspaper3k pins long-unmaintained dependencies,
    metadata_parser pins an older beautifulsoup4 -- so a *provisioning* failure drops just that competitor's column with
    a note. A competitor can also install cleanly yet fail to *import*, when its bundled native library clashes with
    another wheel's ABI in the shared venv (html5-parser returns an lxml tree, so the two must share one libxml2, which
    its ``PIP_OPTIONS`` secures by building lxml from source); that is the same environment quirk one import boundary
    later, so it drops the column with a note too. Only a crash *after* the library imported -- during measurement,
    where the fault is the benchmark and not the toolchain -- is left to propagate rather than silently blanking the
    column. The two are told
    apart by whether the worker died inside ``importlib.import_module``. A competitor that instead hangs -- runs past
    the per-operation timeout without finishing -- is killed and its column dropped with a note, so one pathological
    library or input cannot stall the whole sweep.
    """
    try:
        python = venv_python(workdir, competitor, COMPETITORS[competitor][0], COMPETITORS[competitor][2])
    except subprocess.CalledProcessError:
        print(f"skipping {competitor}: it did not install in its isolated venv", file=sys.stderr)
        return {}
    try:
        return run_worker(python, competitor, operation, workdir, pyperf_args)
    except subprocess.TimeoutExpired:
        print(
            f"skipping {competitor}: it did not finish {operation} within {_COMPETITOR_TIMEOUT:.0f}s", file=sys.stderr
        )
        return {}
    except subprocess.CalledProcessError as error:
        if "importlib.import_module" not in (error.stderr or ""):
            raise
        print(f"skipping {competitor}: it installed but failed to import in its isolated venv", file=sys.stderr)
        return {}


def report_operation(operation: str, pyperf_args: tuple[str, ...], *, workdir: Path, core_python: Path) -> None:
    """Render one operation: the turbohtml baseline against every competitor that implements it, each isolated."""
    stats = run_worker(core_python, "core", operation, workdir, pyperf_args)
    for competitor in _packages_for(operation):
        stats.update(_try_competitor(workdir, competitor, operation, pyperf_args))
    report.render(operation, stats)


def report_package(competitor: str, pyperf_args: tuple[str, ...], *, workdir: Path, core_python: Path) -> None:
    """Render one competitor's report: it against the turbohtml baseline across every operation it implements."""
    reqs, operation_names, pip_options = COMPETITORS[competitor]
    competitor_python = venv_python(workdir, competitor, reqs, pip_options)
    for operation in operation_names:
        stats = run_worker(core_python, "core", operation, workdir, pyperf_args)
        stats.update(run_worker(competitor_python, competitor, operation, workdir, pyperf_args))
        report.render(operation, stats)


def report_core(pyperf_args: tuple[str, ...], *, workdir: Path, core_python: Path) -> None:
    """Render turbohtml's own baseline for every operation in a turbohtml-only venv."""
    for operation in operations.OPERATIONS:
        report.render(operation, run_worker(core_python, "core", operation, workdir, pyperf_args))


def run(command: str, pyperf_args: tuple[str, ...] = (), *, pgo: bool = False) -> None:
    """
    Dispatch a CLI command to the matching report, forwarding any extra pyperf options to every worker.

    One workdir spans the whole command, so the (profile-guided, when ``pgo``) core wheel is built once and every venv
    provisioned once, however many operations reuse them.
    """
    print(
        "tip: for low-noise results run `pyperf system tune` first (and `sudo pyperf system reset` after); pass "
        "pyperf options like --rigorous or --affinity=<cpu> after the command to control sampling and CPU pinning.",
        file=sys.stderr,
    )
    corpus.prefetch()  # the worker venvs carry no HTTP client; fill the download cache here, where one is installed
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        if command in COMPETITORS:
            report_package(command, pyperf_args, workdir=workdir, core_python=_core_python(workdir, pgo=pgo))
        elif command == "core":
            report_core(pyperf_args, workdir=workdir, core_python=_core_python(workdir, pgo=pgo))
        elif command == "interpreters":
            from bench import (  # ruff:ignore[import-outside-top-level]  # imports orchestrator, so bind it late
                interpreters,
            )

            interpreters.build(workdir, pyperf_args)
        elif command == "all":
            core_python = _core_python(workdir, pgo=pgo)
            for operation in operations.OPERATIONS:
                report_operation(operation, pyperf_args, workdir=workdir, core_python=core_python)
        elif command in operations.OPERATIONS:
            report_operation(command, pyperf_args, workdir=workdir, core_python=_core_python(workdir, pgo=pgo))
        else:
            choices = ", ".join(["core", "all", "interpreters", *operations.OPERATIONS, *COMPETITORS])
            msg = f"unknown command {command!r}; choose one of: {choices}"
            raise SystemExit(msg)
