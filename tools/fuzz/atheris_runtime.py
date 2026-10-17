"""Atheris's Python callback discards return values; libFuzzer needs native rejection."""

from __future__ import annotations

import argparse
import ctypes
import importlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from .atheris_header import split_header

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .atheris_header import Header

__all__ = ["build_runtime", "failure_hook", "main", "rejecting_callback", "rejection_hook"]


def main(argv: Sequence[str] | None = None) -> int:
    """Build one ELF so libFuzzer and Atheris share coverage symbols."""
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--sanitizer", type=Path, help="a static sanitizer runtime to merge in, as atheris does")
    arguments: Final = parser.parse_args(argv)
    build_runtime(arguments.archive, arguments.output, coverage=arguments.coverage, sanitizer=arguments.sanitizer)
    return 0


def build_runtime(archive: Path, output: Path, *, coverage: bool = False, sanitizer: Path | None = None) -> Path:
    """Keep the original wheel archive intact for provenance checks."""
    output.mkdir(parents=True, exist_ok=True)
    runtime: Final = output / "runtime.a"
    shutil.copyfile(archive, runtime)
    subprocess.run(
        [
            os.environ.get("OBJCOPY", "objcopy"),
            "--redefine-sym",
            "LLVMFuzzerRunDriver=turbohtml_real_fuzzer_driver",
            str(runtime),
        ],
        check=True,
    )
    bridge: Final = output / "bridge.o"
    subprocess.run(
        [
            os.environ.get("CC", "gcc"),
            "-c",
            "-fPIC",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            *(["--coverage", "-DATHERIS_BRIDGE_COVERAGE"] if coverage else []),
            str(Path(__file__).with_name("atheris_bridge.c")),
            "-o",
            str(bridge),
        ],
        check=True,
    )
    library: Final = output / "libatheris_fuzzer.so"
    subprocess.run(
        [
            os.environ.get("CXX", "g++"),
            "-shared",
            "-o",
            str(library),
            str(bridge),
            "-Wl,--whole-archive",
            str(runtime),
            *([] if sanitizer is None else [str(_shared_sanitizer(sanitizer, output))]),
            "-Wl,--no-whole-archive",
            "-ldl",
            "-pthread",
            *(["--coverage"] if coverage else []),
        ],
        check=True,
    )
    return library


def _shared_sanitizer(sanitizer: Path, output: Path) -> Path:
    # a preinit object registers the runtime through .preinit_array, which a shared object cannot carry; atheris drops
    # it the same way when it merges libFuzzer with a sanitizer (setup_utils/merge_libfuzzer_sanitizer.sh)
    merged: Final = output / "sanitizer.a"
    shutil.copyfile(sanitizer, merged)
    archiver: Final = os.environ.get("AR", "ar")
    members: Final = subprocess.run([archiver, "t", str(merged)], check=True, capture_output=True, text=True).stdout
    if preinit := [member for member in members.split() if "preinit" in member]:
        subprocess.run([archiver, "d", str(merged), *preinit], check=True)
    return merged


def rejecting_callback(
    target: Callable[[bytes, Header], None],
    exceptions: tuple[type[Exception], ...],
    reject: Callable[[], None],
    inject: Callable[[int], tuple[int, bool]],
    *,
    lossy_on_allocation_failure: bool = False,
) -> Callable[[bytes], None]:
    """
    Fail the header's allocation, then reject only exceptions the selected public API documents.

    A ``MemoryError`` without an injected failure and another exception after one are findings. Python can recover
    from failed internal allocations, so a successful result remains valid. The ``finally`` block closes the window
    before any handler here allocates. Mutation observers may drop records on allocation failure; only that target
    accepts a resulting assertion. libxml2 checks that each API reports the failure it injected
    (https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/xml.c#L72-L83).
    """

    def run(data: bytes) -> None:
        header, payload = split_header(data)
        inject(header.failure_pos)
        try:
            try:
                target(payload, header)
            finally:
                failed = inject(0)[1]
        except MemoryError:
            if not failed:
                raise
            return
        except AssertionError:
            if not (failed and lossy_on_allocation_failure):
                raise
            return
        except exceptions as error:
            if failed:
                message = f"{type(error).__name__} for an injected allocation failure"
                raise AssertionError(message) from error
            reject()
            return

    return run


def rejection_hook() -> Callable[[], None]:
    """Resolve the preloaded bridge before starting the callback loop."""
    return ctypes.CFUNCTYPE(None)(("turbohtml_fuzz_reject_input", ctypes.CDLL(None)))


def failure_hook() -> Callable[[int], tuple[int, bool]]:
    """Resolve the PyMem failure hook, which only the fuzz-only build (``meson -Dfuzzing=true``) defines."""
    return cast(
        "Callable[[int], tuple[int, bool]]", vars(importlib.import_module("turbohtml._html"))["_fuzz_inject_failure"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
