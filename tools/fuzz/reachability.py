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

__all__ = ["main", "target_functions", "unreached"]


def main(argv: Sequence[str] | None = None) -> int:
    """Report each target's unreached functions by name only; the corpus stays in private storage."""
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-dir", type=Path, required=True, help="the build output holding coverage_d_*")
    arguments: Final = parser.parse_args(argv)
    names: Final = set(atheris_target_names())
    report: Final = {
        target.name: unreached(target, arguments.coverage_dir) for target in public_targets() if target.name in names
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(any(report.values()))


def unreached(target: Target, coverage_dir: Path) -> list[str]:
    """Name every target function the target's coverage data shows at 0%, or the data file when it is missing."""
    # base-runner's coverage script copies each fuzzer's translated data here before combining consumes the originals
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
            function.lines & set(data.lines(path) or ()) for path in measured if path.endswith("/" + function.path)
        )
    ]


def target_functions(target: Target) -> tuple[_Function, ...]:
    """Read the callback run by this target and every Python-implemented public export it owns."""
    callables: Final = [_unwrap(target.chunked if target.chunked is not None else target.callback)] + [
        _resolve(export) for export in target.exports
    ]
    # an alias export repeats a function, and a Protocol's stub methods hold no line a harness could execute
    return tuple(dict.fromkeys(function for item in callables for function in _functions(item) if function.lines))


def _unwrap(callback: object) -> object:
    return callback.func if isinstance(callback, functools.partial) else callback


def _resolve(export: str) -> object:
    module, _, name = export.rpartition(".")
    return getattr(importlib.import_module(module), name)


def _functions(item: object) -> Iterator[_Function]:
    if inspect.isfunction(item):
        yield _Function(item.__qualname__, _module_path(item.__module__), _lines(item.__code__))
    elif inspect.isclass(item) and (methods := list(_methods(item))):
        lines = frozenset(line for method in methods for line in _lines(method.__code__))
        yield _Function(item.__qualname__, _module_path(item.__module__), lines)


def _methods(cls: type) -> Iterator[FunctionType]:
    # only methods compiled from the class's own module count: dataclass and NamedTuple generate theirs from "<string>",
    # a C type's vars hold method descriptors, and PyPy's interpreter-level functions carry a code object without
    # co_filename
    source: Final = getattr(sys.modules[cls.__module__], "__file__", None)
    for member in vars(cls).values():
        function = member.__func__ if isinstance(member, (staticmethod, classmethod)) else member
        if inspect.isfunction(function) and getattr(function.__code__, "co_filename", None) == source:
            yield function


def _module_path(module: str) -> str:
    file: Final = sys.modules[module].__file__ or ""
    return module.replace(".", "/") + ("/__init__.py" if file.endswith("__init__.py") else ".py")


def _lines(code: CodeType) -> frozenset[int]:
    # the def line executes when the module loads, so it says nothing about whether the harness reached the body
    return frozenset(line for _, _, line in code.co_lines() if line is not None and line != code.co_firstlineno)


@dataclass(frozen=True)
class _Function:
    """One target function: where coverage records it and which of its lines can execute."""

    name: str
    path: str
    lines: frozenset[int]


if __name__ == "__main__":
    sys.exit(main())
