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
that hunts for crashes). A crashing input lands in ``--crash-dir`` as ``crash-<sha256>``, and the log names it only by
hash, length, harness and seeds, because CI logs on a public repository are public. The in-process extension is
expected to be pre-built by the tox env; ``--build`` builds it here for a local run.
"""

from __future__ import annotations

import argparse
import hashlib
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
_JS_CORPUS: Final[Path] = _ROOT / "tests" / "serialize" / "js" / "_corpus"
_CC: Final[str] = os.environ.get("CC", "clang")
_JS_ENGINE: Final[tuple[str, ...]] = ("lexer", "ast", "parser", "printer", "fold", "mangle", "minify")
# pymalloc carves small objects out of pools, so an over-read that stays inside a pool never reaches ASan's redzones;
# PYTHONMALLOC=malloc hands every PyMem call to the intercepted system allocator (v1 FUZZ-2 was silent under pymalloc)
_ALLOCATORS: Final[tuple[str, ...]] = ("pymalloc", "malloc")


def main() -> int:
    """Return 0 when every harness stays clean, nonzero on the first sanitizer abort or soft finding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "deep"), default="smoke")
    parser.add_argument(
        "--minutes", type=float, default=1.0, help="deep-mode budget per in-process target, split across the allocators"
    )
    parser.add_argument("--rng-seed", type=int, default=0, help="seed of the deep-mode mutation sequence")
    parser.add_argument("--crash-dir", type=Path, default=_ROOT / ".fuzz-crashes", help="where crashing inputs land")
    parser.add_argument("--build", action="store_true", help="build the ASan extension here (tox builds it otherwise)")
    parser.add_argument("--extra-corpus", type=Path, default=None, help="a second seed directory (vendored test data)")
    parser.add_argument("--skip-inprocess", action="store_true", help="only run the standalone C harnesses")
    args = parser.parse_args()

    if args.build:
        _build_extension(Path(tempfile.mkdtemp(prefix="th-fuzz-build-")))
    if (code := _run_standalone(args.mode, args.extra_corpus)) != 0:
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


def _run_standalone(mode: str, extra: Path | None) -> int:
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
    js_seeds = _files(_REGRESSIONS) + (_files(_JS_CORPUS) if mode == "deep" else [])
    env = {
        **os.environ,
        "ASAN_OPTIONS": f"detect_leaks={1 if platform.system() == 'Linux' else 0}:halt_on_error=1",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
    }
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


def _seed_files(target: str, extra: Path | None) -> list[str]:
    return _files(_REGRESSIONS) + _files(_CORPUS / target) + ([] if extra is None else _files(extra / target))


def _files(directory: Path) -> list[str]:
    return sorted(str(path) for path in directory.glob("*") if path.is_file())


def _run_inprocess(mode: str, minutes: float, rng_seed: int, crash_dir: Path) -> int:
    env = {"PYTHONHASHSEED": "0", **_asan_preload()}
    crash_dir.mkdir(parents=True, exist_ok=True)
    status = 0
    for allocator in _ALLOCATORS:
        repro_dir = Path(tempfile.mkdtemp(prefix="th-fuzz-repro-"))
        context = f"PYTHONMALLOC={allocator} PYTHONHASHSEED={env['PYTHONHASHSEED']} rng-seed={rng_seed}"
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


if __name__ == "__main__":
    raise SystemExit(main())
