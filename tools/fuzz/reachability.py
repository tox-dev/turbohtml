"""A harness whose target functions never execute fuzzes nothing, so the coverage job fails on it instead of passing."""

from __future__ import annotations

import argparse
import functools
import importlib
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from coverage import CoverageData

from .atheris_targets import public_targets
from .oss_fuzz import atheris_target_names, fuzzer_name

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from types import CodeType, FunctionType

    from .atheris_registry import Target


def main(argv: Sequence[str] | None = None) -> int:
    # Report each target's unreached functions by name only; the corpus stays in private storage.
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-dir", type=Path, required=True, help="the directory holding coverage_d_*")
    arguments: Final = parser.parse_args(argv)
    names: Final = set(atheris_target_names())
    report: Final = {
        target.name: unreached(target, arguments.coverage_dir) for target in public_targets() if target.name in names
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(any(report.values()))


def unreached(target: Target, coverage_dir: Path) -> list[str]:
    # Name every target function the target's coverage data shows at 0%, or the data file when it is missing.
    # base-runner's coverage script copies each fuzzer's translated data to build-out before combining consumes the
    # originals, and cflite.py gathers those copies from the per-target runs into one directory
    data_file: Final = coverage_dir / f"coverage_d_{fuzzer_name(target.name)}"
    if not data_file.is_file():
        return [f"no coverage data at {data_file.name}"]
    data: Final = CoverageData(basename=str(data_file))
    data.read()
    measured: Final = data.measured_files()
    return [
        function.name
        for function in target_functions(target)
        if not any(
            function.lines & set(data.lines(path) or ()) for path in measured if path.endswith(f"/{function.path}")
        )
    ]


def target_functions(target: Target) -> tuple[_Function, ...]:
    # Read the callback run by this target and every Python-implemented public export it owns.
    callback: Final = target.chunked if target.chunked is not None else target.callback
    callables: Final = [callback.func if isinstance(callback, functools.partial) else callback] + [
        _resolve(export) for export in target.exports
    ]
    # an alias export repeats a function, and a Protocol's stub methods hold no line a harness could execute
    return tuple(dict.fromkeys(function for item in callables for function in _functions(item) if function.lines))


def _resolve(
    export: str,
) -> object:  # simplint: ignore[imprecise-annotation]  # getattr returns whatever the module exports
    module, _, name = export.rpartition(".")
    return getattr(importlib.import_module(module), name)


def _functions(
    item: object,  # simplint: ignore[imprecise-annotation]  # exports include non-callable values
) -> Iterator[_Function]:
    if inspect.isfunction(item):
        yield _Function(name=item.__qualname__, path=_module_path(item.__code__), lines=_lines(item.__code__))
    elif inspect.isclass(item) and not getattr(item, "_is_protocol", False) and (methods := list(_methods(item))):
        lines = frozenset(line for method in methods for line in _lines(method.__code__))
        yield _Function(name=item.__qualname__, path=_module_path(methods[0].__code__), lines=lines)


def _methods(cls: type) -> Iterator[FunctionType]:
    # only methods compiled from the class's own module count: dataclass and NamedTuple generate theirs from "<string>",
    # a C type's vars hold method descriptors, and PyPy's interpreter-level functions carry a code object without
    # co_filename
    source: Final = getattr(sys.modules[cls.__module__], "__file__", None)
    for member in cls.__dict__.values():
        function = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
        if inspect.isfunction(function) and getattr(function.__code__, "co_filename", None) == source:
            yield function


def _module_path(code: CodeType) -> str:
    path: Final = Path(code.co_filename).resolve().relative_to(Path(__file__).resolve().parents[2]).as_posix()
    return path.removeprefix("src/").removeprefix("tools/")


def _lines(code: CodeType) -> frozenset[int]:
    # the def line executes when the module loads, so it says nothing about whether the harness reached the body
    return frozenset(line for _, _, line in code.co_lines() if line is not None and line != code.co_firstlineno)


@dataclass(frozen=True, kw_only=True, slots=True)
class _Function:
    # One target function: where coverage records it and which of its lines can execute.
    name: str
    path: str
    lines: frozenset[int]


__all__ = [
    "main",
    "target_functions",
    "unreached",
]


if __name__ == "__main__":
    sys.exit(main())
