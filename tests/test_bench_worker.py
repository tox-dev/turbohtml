from __future__ import annotations

import json
import os
import subprocess  # ruff:ignore[suspicious-subprocess-import]  # fixed Python executable and generated fixture script
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

import pyperf
import pytest
from bench.ci import benchmarks
from bench.core import OPERATIONS
from bench.operations import INPUTS
from bench.timing import Mutating
from bench.worker import main

from turbohtml import __version__

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPT: Final[str] = """\
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


@pytest.mark.parametrize(
    ("case", "expected"), [pytest.param(0, f"{__version__}\n", id="import"), pytest.param(1, "<p>a b", id="cli")]
)
def test_worker_startup_public_output(case: int, expected: str) -> None:
    operation: Final[Callable[[str], str]] = cast("Callable[[str], str]", OPERATIONS["startup"][0])
    assert operation(cast("str", INPUTS["startup"]()[case][1])) == expected


def test_worker_startup_uses_elapsed_time() -> None:
    assert ("startup" in OPERATIONS, "startup" in {name for name, _, _ in benchmarks()}) == (True, False)


def test_ci_gates_dispatch_only_on_staged_cases() -> None:
    gated: Final[set[str]] = {name for name, _, _ in benchmarks() if name.startswith("transform-dispatch")}
    assert gated == {"transform-dispatch-1", "transform-dispatch-4", "transform-dispatch-16"}


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
        (OPERATIONS[name][0], INPUTS[name]()[0][1]),
    ]


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param("read_file", id="plain"),
        pytest.param("Mutating(setup=read_file, run=str.upper)", id="mutating"),
    ],
)
@pytest.mark.parametrize("error_index", [pytest.param(0, id="error-first"), pytest.param(1, id="error-middle")])
def test_worker_skips_unselected_inputs(tmp_path: Path, error_index: int, *, operation: str) -> None:
    first: Final[Path] = tmp_path / "first.txt"
    first.write_text("first input", encoding="utf-8")
    second: Final[Path] = tmp_path / "second.txt"
    second.write_text("second input", encoding="utf-8")
    cases: Final[list[str]] = [str(first), str(second)]
    cases.insert(error_index, str(tmp_path / "missing.txt"))
    script: Final[Path] = tmp_path / "worker_case.py"
    script.write_text(
        _SCRIPT.format(cases=cases, operation=operation),
        encoding="utf-8",
    )
    output: Final[Path] = tmp_path / "stats.json"
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
    script: Final[Path] = tmp_path / "worker_case.py"
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


def test_worker_lists_the_case_names_of_each_operation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BENCH_LIST", json.dumps(["startup"]))
    main()
    assert json.loads(capsys.readouterr().out) == {"startup": ["fresh import and version", "fresh CLI minification"]}


def test_worker_refuses_a_case_name_repeated_within_an_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(INPUTS, "repeats", lambda: (("same", "a"), ("same", "b")))
    monkeypatch.setenv("BENCH_LIST", json.dumps(["repeats"]))
    with pytest.raises(SystemExit, match="'repeats' repeats the case name 'same'"):
        main()


def test_worker_measures_only_the_case_named_by_bench_case(tmp_path: Path) -> None:
    first: Final[Path] = tmp_path / "first.txt"
    first.write_text("first input", encoding="utf-8")
    second: Final[Path] = tmp_path / "second.txt"
    second.write_text("second input", encoding="utf-8")
    script: Final[Path] = tmp_path / "worker_case.py"
    script.write_text(_SCRIPT.format(cases=[str(first), str(second)], operation="read_file"), encoding="utf-8")
    output: Final[Path] = tmp_path / "stats.json"
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
            "BENCH_CASE": "1",
        },
    )
    assert (
        {name: sorted(entry) for name, entry in json.loads(output.read_text(encoding="utf-8")).items()},
        {tuple(json.loads(line)) for line in script.with_suffix(".jsonl").read_text(encoding="utf-8").splitlines()},
    ) == (
        {"audit-worker|second.txt|files": ["mean", "stdev"]},
        {("manager", "second.txt"), ("--worker-task=0", "second.txt")},
    )


@pytest.mark.parametrize(
    ("operation", "argv", "expected"),
    [
        pytest.param(Mutating(setup=str.upper, run=str.lower), [], 0.01, id="mutating-default"),
        pytest.param(str.lower, [], 0.1, id="plain-default"),
        pytest.param(Mutating(setup=str.upper, run=str.lower), ["--min-time=0.5"], 0.5, id="mutating-command-line"),
    ],
)
def test_worker_min_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    operation: Callable[[str], str] | Mutating[str, str, str],
    argv: list[str],
    expected: float,
) -> None:
    runners: Final[list[pyperf.Runner]] = []

    class RecordingRunner(pyperf.Runner):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(**kwargs)
            runners.append(self)

    # pyperf's one-runner-per-process guard keys on id(cls) in a set shared by every subclass, and a collected
    # RecordingRunner from an earlier case can leave its id for this one to reuse
    monkeypatch.setattr(RecordingRunner, "_created", set())
    monkeypatch.setattr(pyperf, "Runner", RecordingRunner)
    monkeypatch.setitem(OPERATIONS, "min-time", (operation, "files"))
    monkeypatch.setitem(INPUTS, "min-time", lambda: ())
    monkeypatch.setenv("BENCH_TARGET", "core")
    monkeypatch.setenv("BENCH_OPERATION", "min-time")
    monkeypatch.setenv("BENCH_OUT", str(tmp_path / "stats.json"))
    monkeypatch.setenv("BENCH_SKIP_CASES", "[]")
    monkeypatch.setattr(sys, "argv", ["worker", *argv])
    main()
    assert runners[0].parse_args().min_time == expected
