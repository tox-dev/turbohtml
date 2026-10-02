"""
Build and drive the turbohtml fuzz harnesses under AddressSanitizer + UndefinedBehaviorSanitizer.

Two mechanisms cover the untrusted-input entry points the security spike prioritizes (tox-dev/turbohtml#478):

* standalone, malloc-backed C harnesses for the surfaces whose core decouples from CPython -- the IDNA ToASCII engine
  (``idna_harness.c``, the highest memory-safety risk), the phone-number recognizer (``phone_harness.c``) and the JS
  minifier (``../js_minify_harness.c``). These compile with no interpreter, exactly the ``JM_STANDALONE`` pattern the
  JS minifier already ships.
* an in-process driver (``_targets.py``) for the surfaces that reach the live PyObject tree -- parse, serialize,
  sanitize, the URL parser, and the HTML/CSS minifiers -- run against an extension compiled with the sanitizers so a C
  fault aborts the interpreter with a stack trace. It calls the public API, so it survives the in-flight C refactors.

``smoke`` replays the past finds under ``tests/fuzz_regressions`` and a benign seed corpus once (fast, deterministic,
gates every PR). ``deep`` adds a mutation loop and structural probes for a per-target budget (the scheduled/manual run
that hunts for crashes). ``oracle`` runs the sanitizer wrong-output oracles (``sanitize_oracles.py``) instead,
because a sanitizer bug usually returns unsafe markup without crashing. ``round-trip`` runs the printer, minifier,
entry-point and source-span oracles (``round_trip_oracles.py``) and ``release-diff`` compares HEAD with the latest PyPI
release (``release_diff.py``), for the bugs that return wrong text. A crashing input lands in ``--crash-dir`` as
``crash-<sha256>``, and the log names it only by hash, length and harness, because CI logs on a public repository are
public. The in-process extension is expected to be pre-built by the tox env; ``--build`` builds it here for a local run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent
_FUZZ: Final[Path] = _ROOT / "tools" / "fuzz"
_CORPUS: Final[Path] = _FUZZ / "corpus"
_REGRESSIONS: Final[Path] = _ROOT / "tests" / "fuzz_regressions"
_JS_CORPUS: Final[Path] = _ROOT / "tests" / "js" / "_corpus"
_CC: Final[str] = os.environ.get("CC", "clang")
_JS_ENGINE: Final[tuple[str, ...]] = ("lexer", "ast", "parser", "printer", "fold", "mangle", "minify")
# pymalloc carves small objects out of pools, so an over-read that stays inside a pool never reaches ASan's redzones;
# PYTHONMALLOC=malloc hands every PyMem call to the intercepted system allocator (v1 FUZZ-2 was silent under pymalloc)
_ALLOCATORS: Final[tuple[str, ...]] = ("pymalloc", "malloc")
_WPT: Final = "tools/fuzz-data/wpt"


def main() -> int:
    """Return 0 when every harness stays clean, nonzero on the first sanitizer abort or soft finding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "deep", "oracle", "round-trip", "release-diff"), default="smoke")
    parser.add_argument(
        "--minutes", type=float, default=1.0, help="deep-mode budget per in-process target, split across the allocators"
    )
    parser.add_argument(
        "--rng-seed",
        type=int,
        default=int(os.environ.get("FUZZ_RNG_SEED", "0")),
        help="seed of the deep-mode mutation sequence (default: $FUZZ_RNG_SEED, else 0)",
    )
    parser.add_argument("--crash-dir", type=Path, default=_ROOT / ".fuzz-crashes", help="where crashing inputs land")
    parser.add_argument("--build", action="store_true", help="build the ASan extension here (tox builds it otherwise)")
    parser.add_argument("--extra-corpus", type=Path, default=None, help="a second seed directory (vendored test data)")
    parser.add_argument("--skip-inprocess", action="store_true", help="only run the standalone C harnesses")
    args, passthrough = parser.parse_known_args()
    if passthrough and args.mode not in {"round-trip", "release-diff"}:
        parser.error(f"unrecognized arguments: {' '.join(passthrough)}")

    if args.build:
        _build_extension(Path(tempfile.mkdtemp(prefix="th-fuzz-build-")))
    args.crash_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "oracle":
        return _run_oracles(args.minutes, args.rng_seed, args.crash_dir)
    if args.mode in {"round-trip", "release-diff"}:
        return _run_round_trip(args.mode, args.minutes, args.rng_seed, args.crash_dir, passthrough)
    if (code := _run_standalone(args.mode, args.extra_corpus, args.crash_dir)) != 0:
        return code
    return 0 if args.skip_inprocess else _run_inprocess(args.mode, args.minutes, args.rng_seed, args.crash_dir)


def _build_extension(build_dir: Path) -> None:
    cmd = [
        "uv",
        "pip",
        "install",
        "--reinstall",
        "--no-deps",
        "--no-build-isolation",
        "--editable",
        str(_ROOT),
        f"--config-settings=build-dir={build_dir}",
        "--config-settings=setup-args=-Dc_args=-fsanitize=address,undefined",
        "--config-settings=setup-args=-Dc_link_args=-fsanitize=address,undefined",
        "--config-settings=setup-args=-Dbuildtype=debugoptimized",
    ]
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, env={**os.environ, "CC": _CC})


def _run_oracles(minutes: float, rng_seed: int, crash_dir: Path) -> int:
    """Run the sanitizer oracles under the ASan preload, so a C fault on the way still aborts with a stack trace."""
    _checkout_sparse_wpt()
    env = _asan_preload()
    if minutes > 0:
        env = _private_reports(env, crash_dir)
    result = subprocess.run(
        [
            sys.executable,
            str(_FUZZ / "sanitize_oracles.py"),
            "--minutes",
            str(minutes),
            "--rng-seed",
            str(rng_seed),
            "--repro",
            str(crash_dir / "current_input.html"),
            "--report",
            str(crash_dir / "crash-findings.json"),
        ],
        env=env,
        check=False,
    )
    if result.returncode not in {0, 1, 2}:
        print(f"SANITIZER ABORT in the oracle run (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def _run_round_trip(mode: str, minutes: float, rng_seed: int, crash_dir: Path, passthrough: list[str]) -> int:
    """
    Run the round-trip oracles under the ASan preload, or the release differential without it.

    The seed reaches the child only through ``FUZZ_RNG_SEED``, never its command line, because it regenerates every
    finding from the public code. The release differential runs a PyPI wheel built without the sanitizers, so
    preloading the runtime would only slow it. Both run as modules of ``tools/`` so the differential can reuse the
    oracles' generators; arguments ``fuzz.py`` does not know (``--oracle``, ``--errors``) pass through to them.
    """
    module = "fuzz.round_trip_oracles" if mode == "round-trip" else "fuzz.release_diff"
    env = {
        **(_asan_preload() if mode == "round-trip" else os.environ),
        "FUZZ_RNG_SEED": str(rng_seed),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(_ROOT / "tools"), os.environ.get("PYTHONPATH")])),
    }
    if minutes > 0 and mode == "round-trip":
        env = _private_reports(env, crash_dir)
    command = [sys.executable, "-m", module, "--minutes", str(minutes), "--crash-dir", str(crash_dir), *passthrough]
    result = subprocess.run(command, env=env, check=False)
    if result.returncode not in {0, 1, 2}:
        print(f"SANITIZER ABORT in the {mode} run (exit {result.returncode})", file=sys.stderr)
    return result.returncode


def _checkout_sparse_wpt() -> None:
    """
    Check out only ``sanitizer-api/`` of the pinned WPT submodule.

    A depth-1 WPT clone is about 1 GB, so ``.gitmodules`` marks the submodule ``update = none`` (plain and recursive
    ``git submodule update`` skip it) and this fetches the recorded commit blob-less with a sparse checkout instead.
    """
    if (target := _ROOT / _WPT / "sanitizer-api").is_dir():
        return
    commit = _git("rev-parse", f"HEAD:{_WPT}")
    url = _git("config", "--file", ".gitmodules", f"submodule.{_WPT}.url")
    _git("init", "--quiet", _WPT)
    _git("-C", _WPT, "fetch", "--quiet", "--depth", "1", "--filter=blob:none", url, commit)
    _git("-C", _WPT, "sparse-checkout", "set", "--no-cone", "/sanitizer-api/", "/LICENSE.md")
    _git("-C", _WPT, "checkout", "--quiet", "FETCH_HEAD")
    print(f"checked out {target.relative_to(_ROOT)} at {commit[:12]}")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _run_standalone(mode: str, extra: Path | None, crash_dir: Path) -> int:
    work = Path(tempfile.mkdtemp(prefix="th-fuzz-"))
    idna = work / "idna_harness"
    js = work / "js_harness"
    phone = work / "phone_harness"
    _compile(_FUZZ / "idna_harness.c", [], "-DTH_IDNA_STANDALONE", idna)
    _compile(_FUZZ / "phone_harness.c", [], "-DTH_PHONE_STANDALONE", phone)
    _compile(
        _ROOT / "tools" / "js_minify_harness.c",
        [_ROOT / "src" / "turbohtml" / "_c" / "js" / f"{name}.c" for name in _JS_ENGINE],
        "-DJM_STANDALONE",
        js,
    )
    js_seeds = _files(_REGRESSIONS) + (_js_corpus(work) if mode == "deep" else [])
    env = {
        **os.environ,
        "ASAN_OPTIONS": f"detect_leaks={1 if platform.system() == 'Linux' else 0}:halt_on_error=1",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
    }
    if mode == "deep":
        env = _private_reports(env, crash_dir)
    for binary, seeds in ((idna, _seed_files("idna", extra)), (phone, _seed_files("phone", extra)), (js, js_seeds)):
        if (result := subprocess.run([str(binary), *seeds], env=env, check=False)).returncode != 0:
            print(f"SANITIZER ABORT in {binary.name} (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    return 0


def _compile(harness: Path, sources: list[Path], macro: str, binary: Path) -> None:
    cmd = [
        _CC,
        macro,
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-g",
        "-O1",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(_ROOT / "src" / "turbohtml" / "_c"),
        str(harness),
        *[str(path) for path in sources],
        "-o",
        str(binary),
    ]
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _js_corpus(work: Path) -> list[str]:
    # the fixtures are JSON rows, so each input becomes its own file for the harness, as tools/js_sanitize.py does
    files = []
    for index, row in enumerate(
        row for fixture in sorted(_JS_CORPUS.glob("*.json")) for row in json.loads(fixture.read_text(encoding="utf-8"))
    ):
        (path := work / f"corpus-{index}.js").write_text(row["input"], encoding="utf-8")
        files.append(str(path))
    return files


def _seed_files(target: str, extra: Path | None) -> list[str]:
    return _files(_REGRESSIONS) + _files(_CORPUS / target) + ([] if extra is None else _files(extra / target))


def _files(directory: Path) -> list[str]:
    return sorted(str(path) for path in directory.glob("*") if path.is_file())


def _run_inprocess(mode: str, minutes: float, rng_seed: int, crash_dir: Path) -> int:
    env = {"PYTHONHASHSEED": "0", **_asan_preload()}
    if mode == "deep":
        env = _private_reports(env, crash_dir)
    status = 0
    for allocator in _ALLOCATORS:
        repro_dir = Path(tempfile.mkdtemp(prefix="th-fuzz-repro-"))
        context = f"PYTHONMALLOC={allocator} PYTHONHASHSEED={env['PYTHONHASHSEED']}"
        print(f"in-process pass: {context}, crashers kept in {crash_dir}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                str(_FUZZ / "_targets.py"),
                "--mode",
                mode,
                "--corpus-dir",
                str(_CORPUS),
                "--regression-dir",
                str(_REGRESSIONS),
                "--repro-dir",
                str(repro_dir),
                "--crash-dir",
                str(crash_dir),
                "--minutes",
                str(minutes / len(_ALLOCATORS)),
                "--rng-seed",
                str(rng_seed),
            ],
            env={**env, "PYTHONMALLOC": allocator},
            check=False,
        )
        if result.returncode not in {0, 1}:
            print(f"SANITIZER ABORT in-process (exit {result.returncode}) {context}", file=sys.stderr)
            for repro in repro_dir.iterdir():  # the worker leaves only the input it died on, named after its target
                data = repro.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                (crash_dir / f"crash-{digest}").write_bytes(data)
                (crash_dir / f"crash-{digest}.replay").write_text(f"{context} rng-seed={rng_seed}\n")
                print(f"crashing input: [{repro.name}] sha256={digest} bytes={len(data)}", file=sys.stderr)
            return result.returncode
        status |= result.returncode
    return status


def _asan_preload() -> dict[str, str]:
    """Build the env preloading the ASan runtime ahead of the interpreter so the instrumented .so's interceptors arm."""
    on_linux = platform.system() == "Linux"
    flag = f"libclang_rt.asan-{platform.machine()}.so" if on_linux else "libclang_rt.asan_osx_dynamic.dylib"
    runtime = subprocess.run(
        [_CC, f"-print-file-name={flag}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        **os.environ,
        "LD_PRELOAD" if on_linux else "DYLD_INSERT_LIBRARIES": runtime,
        "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1:abort_on_error=1",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
    }


def _private_reports(env: dict[str, str], crash_dir: Path) -> dict[str, str]:
    # a sanitizer report names the faulting function and line, which discloses an unfixed bug in a public CI log, so a
    # deep run writes reports beside the crashers, where the workflow encrypts them
    log = f":log_path={crash_dir / 'crash-sanitizer'}"
    return {**env, "ASAN_OPTIONS": env["ASAN_OPTIONS"] + log, "UBSAN_OPTIONS": env["UBSAN_OPTIONS"] + log}


if __name__ == "__main__":
    raise SystemExit(main())
