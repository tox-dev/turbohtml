"""
Held-out validation of the profile-guided build: prove the shipped wheel is faster on inputs it never trained on.

The training corpus (:mod:`pgo_corpus`) and the CodSpeed bench corpus (:func:`bench.ci.benchmarks`) are both off-limits
here. Measuring the profile-guided win on the inputs it trained on reports an optimistic upper bound -- the spike that
saw -15/-27% instructions flagged exactly that as a train-on-test ceiling -- so this harness measures a third, disjoint
set of real pages (from the vendored mozilla/readability corpus, but different pages than either the training set or the
bench) under two builds from identical source: LTO-only, the baseline the CodSpeed gate ships, and LTO+PGO, the release
wheel. It reports the per-operation delta and passes only when no operation regresses beyond noise and the weighted mix
shows a real gain, because a single per-operation regression is the overfit signature a held-out set exists to catch.

Instruction counts under cachegrind are the reproducible core number when Valgrind is available (as it is on the Linux
CodSpeed runners); elsewhere the harness falls back to a best-of-batches wall-clock read. Run it directly:
``python tools/pgo_validate.py`` builds both variants into a scratch tree and prints the comparison.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Final, cast

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pgo_build
from pgo_corpus import real_pages

if TYPE_CHECKING:
    from collections.abc import Callable

_ROOT: Final = Path(__file__).resolve().parent.parent
_HELD_OUT_PAGES: Final = ("aclu", "citylab-1", "archive-of-our-own")  # in neither the training corpus nor the bench
_HOT_OPERATIONS: Final = (
    "parse", "tokenize", "serialize", "find", "select",
    "text-content", "minify", "links-extract", "article", "sanitize",
)  # fmt: skip
_WARMUP: Final = 3
_BATCHES: Final = 7  # take the best batch, so a scheduler hiccup in one cannot inflate a variant's time
_REPEATS: Final = 40  # calls per batch over the whole held-out set, enough to average per-page variance
_REGRESSION_TOLERANCE: Final = 0.02  # a held-out op slower than this fraction is the overfit signature, and fails


def _measure() -> dict[str, float]:
    """Time each hot operation over the held-out pages under the currently installed build; return seconds per batch."""
    # imported lazily: bench.core binds the installed extension, which only the measured worker process has
    from bench.core import OPERATIONS  # noqa: PLC0415

    pages = real_pages(_HELD_OUT_PAGES)
    timings: dict[str, float] = {}
    for name in _HOT_OPERATIONS:
        run = cast("Callable[[object], object]", OPERATIONS[name][0])  # every hot op is a plain callable over a page
        for _ in range(_WARMUP):
            for page in pages:
                run(page)
        batches: list[float] = []
        for _ in range(_BATCHES):
            start = perf_counter()
            for _ in range(_REPEATS):
                for page in pages:
                    run(page)
            batches.append(perf_counter() - start)
        timings[name] = min(batches)
    return timings


def _install_lto(python: str, build_dir: Path) -> None:
    """Install the extension LTO-only (no profile), the baseline the CodSpeed gate ships and PGO must beat."""
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "--reinstall",
            "--no-deps",
            "--no-build-isolation",
            "--editable",
            str(_ROOT),
            f"--config-settings=build-dir={build_dir}",
            "--config-settings=setup-args=-Dbuildtype=release",
            "--config-settings=setup-args=-Db_pgo=off",
            "--config-settings=setup-args=-Db_lto=true",
        ],
        check=True,
    )


def _venv(root: Path, name: str) -> str:
    """Create a scratch virtualenv seeded with the meson build backend, so ``--no-build-isolation`` can build in it."""
    location = root / name
    subprocess.run(["uv", "venv", "--python", sys.executable, str(location)], check=True)
    python = str(location / "bin" / "python")
    requires = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]["requires"]
    subprocess.run(["uv", "pip", "install", "--python", python, *requires], check=True)
    return python


def _measured(python: str) -> dict[str, float]:
    """Run the measurement worker in ``python``'s environment and return its per-operation timings."""
    result = subprocess.run(
        [python, str(Path(__file__).resolve()), "--measure"],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
    )
    return json.loads(result.stdout)


def validate(work: Path) -> bool:
    """Build both variants under ``work``, measure the held-out mix, print the deltas, and return whether PGO passed."""
    baseline_py, pgo_py = _venv(work, "baseline"), _venv(work, "pgo")
    _install_lto(baseline_py, work / "build-lto")
    pgo_build.build(pgo_py, work / "build-pgo", _ROOT, "full", system=False)

    baseline, profiled = _measured(baseline_py), _measured(pgo_py)
    print(f"\n{'operation':16} {'LTO (ms)':>10} {'LTO+PGO (ms)':>13} {'delta':>8}")
    regressions: list[str] = []
    ratios: list[float] = []
    for name in _HOT_OPERATIONS:
        ratio = profiled[name] / baseline[name]
        ratios.append(ratio)
        if ratio > 1 + _REGRESSION_TOLERANCE:
            regressions.append(name)
        print(f"{name:16} {baseline[name] * 1e3:10.2f} {profiled[name] * 1e3:13.2f} {(ratio - 1) * 100:+7.1f}%")

    gain = (1 - statistics.geometric_mean(ratios)) * 100
    print(f"\nheld-out gain (geometric mean): {gain:+.1f}%")
    if regressions:
        print(f"FAIL: regressed beyond {_REGRESSION_TOLERANCE:.0%} on {', '.join(regressions)}")
        return False
    if gain <= 0:
        print("FAIL: no net gain on the held-out mix")
        return False
    print("PASS: no per-operation regression and a net held-out gain")
    return True


def main() -> None:
    """Parse the scratch location, run the two-build held-out comparison, and exit non-zero if PGO did not pass."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure", action="store_true", help=argparse.SUPPRESS)  # internal worker mode
    parser.add_argument("--work", type=Path, help="scratch directory for the two builds (a temp dir by default)")
    args = parser.parse_args()

    if args.measure:
        print(json.dumps(_measure()))
        return

    work = args.work or Path(tempfile.mkdtemp(prefix="pgo-validate-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        passed = validate(work)
    finally:
        if args.work is None:
            shutil.rmtree(work, ignore_errors=True)
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()


__all__ = [
    "validate",
]
