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
import tempfile
from pathlib import Path

from bench import operations, report

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = Path(__file__).resolve().parents[1]
_COMPETITOR_DIR = Path(__file__).resolve().parent / "competitors"


def _discover() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Read each competitor module's REQUIREMENTS and OPERATIONS keys from source, without importing it."""
    found: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for path in sorted(_COMPETITOR_DIR.glob("*.py")):
        if path.stem == "__init__":
            continue
        requirements: tuple[str, ...] = ()
        operation_names: tuple[str, ...] = ()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                continue
            if node.targets[0].id == "REQUIREMENTS" and isinstance(node.value, ast.Tuple):
                requirements = tuple(
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                )
            elif node.targets[0].id == "OPERATIONS" and isinstance(node.value, ast.Dict):
                operation_names = tuple(
                    key.value for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
        found[path.stem] = (requirements, operation_names)
    return found


COMPETITORS = _discover()


def _packages_for(operation: str) -> list[str]:
    """Every competitor module that implements the operation, in discovery order."""
    return [name for name, (_, operation_names) in COMPETITORS.items() if operation in operation_names]


def _uv(*args: str) -> None:
    """Run a uv subcommand, surfacing its output only on failure."""
    subprocess.run(["uv", *args], check=True, capture_output=True, text=True)


def _build_wheel(workdir: Path) -> Path:
    """Build the turbohtml wheel once; only the core baseline venv installs it."""
    _uv("build", "--wheel", "--out-dir", str(workdir), str(_REPO_ROOT))
    return next(workdir.glob("*.whl"))


def _venv_python(workdir: Path, name: str, reqs: tuple[str, ...]) -> Path:
    """Provision an isolated venv holding pyperf and the given requirements; return its interpreter."""
    venv = workdir / f"venv-{name}"
    _uv("venv", str(venv))
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    _uv("pip", "install", "--python", str(python), "pyperf>=2.10", *reqs)
    return python


def _run_worker(python: Path, target: str, operation: str, workdir: Path) -> dict[str, float]:
    """Run the worker for one (target, operation) in the given venv and return its means."""
    out = workdir / f"{target}-{operation}.json"
    env = {
        **os.environ,
        "PYTHONPATH": str(_TOOLS_DIR),
        "BENCH_TARGET": target,
        "BENCH_OPERATION": operation,
        "BENCH_OUT": str(out),
    }
    subprocess.run([str(python), "-m", "bench.worker"], check=True, env=env)
    return json.loads(out.read_text(encoding="utf-8"))


def report_operation(operation: str) -> None:
    """Render one operation: the turbohtml baseline against every competitor that implements it, each isolated."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        wheel = _build_wheel(workdir)
        means = _run_worker(_venv_python(workdir, "core", (str(wheel),)), "core", operation, workdir)
        for competitor in _packages_for(operation):
            python = _venv_python(workdir, competitor, COMPETITORS[competitor][0])
            means.update(_run_worker(python, competitor, operation, workdir))
        report.render(operation, means)


def report_package(competitor: str) -> None:
    """Render one competitor's report: it against the turbohtml baseline across every operation it implements."""
    reqs, operation_names = COMPETITORS[competitor]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        core_python = _venv_python(workdir, "core", (str(_build_wheel(workdir)),))
        competitor_python = _venv_python(workdir, competitor, reqs)
        for operation in operation_names:
            means = _run_worker(core_python, "core", operation, workdir)
            means.update(_run_worker(competitor_python, competitor, operation, workdir))
            report.render(operation, means)


def report_core() -> None:
    """Render turbohtml's own baseline for every operation in a turbohtml-only venv."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        python = _venv_python(workdir, "core", (str(_build_wheel(workdir)),))
        for operation in operations.OPERATIONS:
            report.render(operation, _run_worker(python, "core", operation, workdir))


def run(command: str) -> None:
    """Dispatch a CLI command to the matching report."""
    if command == "core":
        report_core()
    elif command == "all":
        for operation in operations.OPERATIONS:
            report_operation(operation)
    elif command in COMPETITORS:
        report_package(command)
    elif command in operations.OPERATIONS:
        report_operation(command)
    else:
        choices = ", ".join(["core", "all", *operations.OPERATIONS, *COMPETITORS])
        msg = f"unknown command {command!r}; choose one of: {choices}"
        raise SystemExit(msg)
