#!/usr/bin/env python3
"""
Run clang-tidy over the C extension for the pre-commit hook.

clang-tidy needs a compile database, which Meson produces at configure time. We
keep a throwaway build directory under ``build/`` (git-ignored) and configure it
once; later runs reuse it. Only the ``.c`` translation units are analyzed; the
``.h`` files (including the implementation fragments some ``.c`` files #include)
are pulled in by those and checked through the header filter in ``.clang-tidy``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
BUILD = _ROOT / "build" / "clang-tidy"
# meson compiles these units only with -Dfuzzing=true, so only that build's compile database lists them
_FUZZING_SOURCES = frozenset({"src/turbohtml/_c/core/fuzzing.c"})


def main(argv: list[str]) -> int:
    # Run clang-tidy over the ``.c`` paths in ``argv``; return nonzero when any run fails.
    sources = [arg for arg in argv if arg.endswith(".c")]
    if not sources:
        return 0
    command = ["clang-tidy"]
    if sys.platform == "darwin":
        # the bundled clang-tidy resolves system headers against the macOS SDK only
        # when told where it lives; Linux finds them on the default search path
        sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True, check=True)
        command += ["--extra-arg=-isysroot", f"--extra-arg={sdk.stdout.strip()}"]
    status = 0
    for build, options, selected in (
        (BUILD, [], [source for source in sources if source not in _FUZZING_SOURCES]),
        (
            _ROOT / "build" / "clang-tidy-fuzzing",
            ["-Dfuzzing=true"],
            [source for source in sources if source in _FUZZING_SOURCES],
        ),
    ):
        if selected:
            database = f"-p={compile_database(build, options)}"
            status = subprocess.run([*command, database, *selected], cwd=_ROOT, check=False).returncode or status
    return status


def compile_database(build: Path, options: list[str]) -> Path:
    # Configure the Meson build directory once and return it.
    if not (build / "compile_commands.json").exists():
        subprocess.run(["meson", "setup", str(build), "--buildtype=plain", *options], cwd=_ROOT, check=True)
    return build


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
