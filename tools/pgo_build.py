"""
Two-phase profile-guided build of the turbohtml C extension.

Profile-guided optimization compiles the extension twice: once instrumented to record which branches and calls the hot
paths take, then again with that profile so the compiler lays out the code the way the real workload runs it.
This driver runs both phases against one persistent meson build directory -- an instrumented ``-Db_pgo=generate`` build,
the representative training workload from :mod:`pgo_train`, then a ``-Db_pgo=use`` rebuild that reads the collected
counters. gcc writes ``.gcda`` files the second build reads directly; clang writes ``.profraw`` that must first be
merged into ``default.profdata``, which this driver does when it finds them, so one command works under either compiler.

The CodSpeed gate builds this way through the ``codspeed`` tox environment (``--phase full``, which finishes with the
profiled editable install the benchmarks then import). The release wheels build this way through cibuildwheel, whose
``before-build`` hook runs ``--phase profile`` to leave the profile in the shared build directory that the wheel build
then reuses with ``-Db_pgo=use``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

_ROOT: Final = Path(__file__).resolve().parent.parent


def _install(python: str, build_dir: Path, root: Path, phase: str, *, system: bool) -> None:
    """Reinstall the extension editable into ``python``'s environment at PGO ``phase``, reusing ``build_dir``."""
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            # cibuildwheel's manylinux interpreter is a system install, not a venv; uv refuses to touch it otherwise
            *(["--system"] if system else []),
            "--reinstall",
            "--no-deps",
            "--no-build-isolation",
            "--editable",
            str(root),
            f"--config-settings=build-dir={build_dir}",
            "--config-settings=setup-args=-Dbuildtype=release",
            f"--config-settings=setup-args=-Db_pgo={phase}",
        ],
        check=True,
    )


def _train(python: str, build_dir: Path, root: Path) -> None:
    """Drive the training workload against the instrumented build, pointing clang's counters into ``build_dir``."""
    subprocess.run(
        [python, str(root / "tools" / "pgo_train.py")],
        check=True,
        env={**os.environ, "LLVM_PROFILE_FILE": str(build_dir / "pgo-%p.profraw")},
    )


def _merge_clang_profile(build_dir: Path) -> None:
    """Merge clang's ``.profraw`` counters into the ``default.profdata`` the ``use`` build reads; gcc needs no merge."""
    if not (raw := sorted(build_dir.glob("*.profraw"))):
        return  # gcc left .gcda beside the objects, which -fprofile-use reads directly
    if (tool := shutil.which("llvm-profdata")) is None:
        located = subprocess.run(["xcrun", "--find", "llvm-profdata"], capture_output=True, text=True, check=False)
        if located.returncode != 0:
            msg = "clang produced .profraw counters but llvm-profdata is not available to merge them"
            raise RuntimeError(msg)
        tool = located.stdout.strip()
    subprocess.run([tool, "merge", "-output", str(build_dir / "default.profdata"), *map(str, raw)], check=True)


def build(python: str, build_dir: Path, root: Path, phase: str, *, system: bool) -> None:
    """Run the instrument-train-merge sequence, then optionally the profiled ``use`` install when ``phase`` is full."""
    build_dir.mkdir(parents=True, exist_ok=True)
    _install(python, build_dir, root, "generate", system=system)
    _train(python, build_dir, root)
    _merge_clang_profile(build_dir)
    if phase == "full":
        _install(python, build_dir, root, "use", system=system)


def main() -> None:
    """Parse the target interpreter, build directory, and phase, then run the two-phase build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="interpreter whose environment to install into")
    parser.add_argument("--build-dir", required=True, type=Path, help="persistent meson build directory to reuse")
    parser.add_argument("--project", default=_ROOT, type=Path, help="source tree to build (defaults to this repo)")
    parser.add_argument(
        "--phase",
        default="full",
        choices=("full", "profile"),
        help="full finishes with the profiled use install; profile stops after collecting it for a later use build",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        help="install into a non-virtual (system) interpreter, as cibuildwheel's manylinux image provides",
    )
    args = parser.parse_args()
    build(args.python, args.build_dir.resolve(), args.project.resolve(), args.phase, system=args.system)


if __name__ == "__main__":
    main()


__all__ = [
    "build",
]
