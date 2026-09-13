"""Run pyperf's process protocol against filesystem operations with rejected inputs."""

from __future__ import annotations

import json
import os
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # fixed Python executable and generated fixture script
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench import core
from bench.ci import benchmarks
from bench.operations import INPUTS

from turbohtml import __version__

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPT: Final = """\
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Final

from bench import core, operations, worker
from bench.timing import Mutating


def read_file(source: str) -> str:
    with Path(__file__).with_suffix(".jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps([
            next((arg for arg in sys.argv if arg.startswith("--worker-task=")), "manager"),
            Path(source).name,
        ]) + "\\n")
    return Path(source).read_text(encoding="utf-8")


CASES: Final = {cases!r}
core.OPERATIONS["audit-worker"] = ({operation}, "files")
operations.INPUTS["audit-worker"] = lambda: tuple((Path(source).name, source) for source in CASES)
worker.main()
"""


@pytest.mark.parametrize(("case", "expected"), [(0, __version__ + "\n"), (1, "<p>a b")], ids=["import", "cli"])
def test_worker_startup_public_output(case: int, expected: str) -> None:
    operation: Final = cast("Callable[[object], str]", core.OPERATIONS["startup"][0])
    assert operation(INPUTS["startup"]()[case][1]) == expected


def test_worker_startup_uses_elapsed_time() -> None:
    assert ("startup" in core.OPERATIONS, "startup" in {name for name, _, _ in benchmarks()}) == (True, False)


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("select-relative", id="relative-selector"),
        pytest.param("find-text-exact", id="exact-text"),
        pytest.param("find-attr-presence", id="attribute-presence"),
    ],
)
def test_worker_explicit_case_registered_once(name: str) -> None:
    assert [(run, load()) for identity, run, load in benchmarks() if identity == name] == [
        (core.OPERATIONS[name][0], INPUTS[name]()[0][1]),
    ]


@pytest.mark.parametrize("mutating", [False, True], ids=["plain", "mutating"])
@pytest.mark.parametrize("error_index", [0, 1], ids=["error-first", "error-middle"])
def test_worker_skips_unselected_inputs(tmp_path: Path, error_index: int, *, mutating: bool) -> None:
    first: Final = tmp_path / "first.txt"
    first.write_text("first input", encoding="utf-8")
    second: Final = tmp_path / "second.txt"
    second.write_text("second input", encoding="utf-8")
    cases: Final = [str(first), str(second)]
    cases.insert(error_index, str(tmp_path / "missing.txt"))
    script: Final = tmp_path / "worker_case.py"
    script.write_text(
        _SCRIPT.format(cases=cases, operation="Mutating(read_file, str.upper)" if mutating else "read_file"),
        encoding="utf-8",
    )
    output: Final = tmp_path / "stats.json"
    subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv, generated fixture script
        [sys.executable, str(script), "--copy-env", "--processes=1", "--values=2", "--warmups=0", "--loops=1"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(str(Path(path).absolute()) for path in sys.path),
            "BENCH_TARGET": "core",
            "BENCH_OPERATION": "audit-worker",
            "BENCH_OUT": str(output),
            "BENCH_SKIP_CASES": "[0, 1, 2]",
        },
    )
    assert (
        {name: sorted(entry) for name, entry in json.loads(output.read_text(encoding="utf-8")).items()},
        {tuple(json.loads(line)) for line in script.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()},
    ) == (
        {
            "audit-worker|first.txt|files": ["mean", "stdev"],
            "audit-worker|second.txt|files": ["mean", "stdev"],
            "audit-worker|missing.txt|files": ["error"],
        },
        {
            ("manager", "first.txt"),
            ("manager", "second.txt"),
            ("manager", "missing.txt"),
            ("--worker-task=0", "first.txt"),
            ("--worker-task=1", "second.txt"),
        },
    )


def test_worker_propagates_selected_input_error(tmp_path: Path) -> None:
    script: Final = tmp_path / "worker_case.py"
    script.write_text(_SCRIPT.format(cases=[str(tmp_path / "missing.txt")], operation="read_file"), encoding="utf-8")
    with pytest.raises(subprocess.CalledProcessError) as failure:
        subprocess.run(  # ruff:ignore[subprocess-without-shell-equals-true]  # fixed argv and generated fixture script
            [
                sys.executable,
                str(script),
                "--worker",
                "--worker-task=0",
                "--loops=1",
                "--values=1",
                "--warmups=0",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(str(Path(path).absolute()) for path in sys.path),
                "BENCH_TARGET": "core",
                "BENCH_OPERATION": "audit-worker",
                "BENCH_OUT": str(tmp_path / "stats.json"),
                "BENCH_SKIP_CASES": "[]",
            },
        )
    assert "FileNotFoundError" in failure.value.stderr
