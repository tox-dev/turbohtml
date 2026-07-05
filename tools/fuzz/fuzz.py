"""
Build and drive the turbohtml fuzz harnesses under AddressSanitizer + UndefinedBehaviorSanitizer.

Two mechanisms cover the untrusted-input entry points the security spike prioritizes (tox-dev/turbohtml#478):

* standalone, malloc-backed C harnesses for the surfaces whose core decouples from CPython -- the IDNA ToASCII engine
  (``idna_harness.c``, the highest memory-safety risk) and the JS minifier (``../js_minify_harness.c``). These compile
  with no interpreter, exactly the ``JM_STANDALONE`` pattern the JS minifier already ships.
* an in-process driver (``_targets.py``) for the surfaces that reach the live PyObject tree -- parse, serialize,
  sanitize, the URL parser, and the HTML/CSS minifiers -- run against an extension compiled with the sanitizers so a C
  fault aborts the interpreter with a stack trace. It calls the public API, so it survives the in-flight C refactors.

``smoke`` replays a benign seed corpus once (fast, deterministic, gates every PR). ``deep`` adds a mutation loop and
structural probes for a per-target budget (the scheduled/manual run that hunts for crashes). The in-process extension is
expected to be pre-built by the tox env; ``--build`` builds it here for a local run.
"""

from __future__ import annotations

import argparse
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
_JS_CORPUS: Final[Path] = _ROOT / "tests" / "serialize" / "js" / "_corpus"
_CC: Final[str] = os.environ.get("CC", "clang")
_JS_ENGINE: Final[tuple[str, ...]] = ("lexer", "ast", "parser", "printer", "fold", "mangle", "minify")


def _asan_preload() -> dict[str, str]:
    """Build the env preloading the ASan runtime ahead of the interpreter so the instrumented .so's interceptors arm."""
    on_linux = platform.system() == "Linux"
    flag = f"libclang_rt.asan-{platform.machine()}.so" if on_linux else "libclang_rt.asan_osx_dynamic.dylib"
    runtime = subprocess.run(
        [_CC, f"-print-file-name={flag}"], capture_output=True, text=True, check=True
    ).stdout.strip()
    env = dict(os.environ)
    env["LD_PRELOAD" if on_linux else "DYLD_INSERT_LIBRARIES"] = runtime
    env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1:abort_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    return env


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
    files = sorted(str(path) for path in (_CORPUS / target).glob("*") if path.is_file())
    if extra is not None and (extra / target).is_dir():
        files += sorted(str(path) for path in (extra / target).glob("*") if path.is_file())
    return files


def _run_standalone(mode: str, extra: Path | None) -> int:
    """Compile and replay the malloc-backed C harnesses; a sanitizer abort returns its nonzero code."""
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = f"detect_leaks={1 if platform.system() == 'Linux' else 0}:halt_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    work = Path(tempfile.mkdtemp(prefix="th-fuzz-"))
    idna = work / "idna_harness"
    js = work / "js_harness"
    _compile(_FUZZ / "idna_harness.c", [], "-DTH_IDNA_STANDALONE", idna)
    _compile(
        _ROOT / "tools" / "js_minify_harness.c",
        [_ROOT / "src" / "turbohtml" / "_c" / "serialize" / "js" / f"{name}.c" for name in _JS_ENGINE],
        "-DJM_STANDALONE",
        js,
    )
    js_seeds = sorted(str(path) for path in _JS_CORPUS.glob("*")) if mode == "deep" else []
    for binary, seeds in ((idna, _seed_files("idna", extra)), (js, js_seeds)):
        if (result := subprocess.run([str(binary), *seeds], env=env, check=False)).returncode != 0:
            print(f"SANITIZER ABORT in {binary.name} (exit {result.returncode})", file=sys.stderr)
            return result.returncode
    return 0


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


def _run_inprocess(mode: str, minutes: float) -> int:
    """Re-exec the interpreter under the ASan preload to drive the coupled targets through the public API."""
    repro = Path(tempfile.mkdtemp(prefix="th-fuzz-repro-")) / "current_input.bin"
    cmd = [
        sys.executable,
        str(_FUZZ / "_targets.py"),
        "--mode",
        mode,
        "--corpus-dir",
        str(_CORPUS),
        "--repro",
        str(repro),
        "--minutes",
        str(minutes),
    ]
    result = subprocess.run(cmd, env=_asan_preload(), check=False)
    if result.returncode not in {0, 1}:
        crasher = repro.read_bytes() if repro.exists() else b""
        print(f"SANITIZER ABORT in-process (exit {result.returncode})", file=sys.stderr)
        print(f"crashing input ({len(crasher)} bytes): {crasher[:400]!r}", file=sys.stderr)
    return result.returncode


def main() -> int:
    """Return 0 when every harness stays clean, nonzero on the first sanitizer abort or soft finding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "deep"), default="smoke")
    parser.add_argument("--minutes", type=float, default=1.0, help="deep-mode wall-clock budget per in-process target")
    parser.add_argument("--build", action="store_true", help="build the ASan extension here (tox builds it otherwise)")
    parser.add_argument("--extra-corpus", type=Path, default=None, help="a second seed directory (vendored test data)")
    parser.add_argument("--skip-inprocess", action="store_true", help="only run the standalone C harnesses")
    args = parser.parse_args()

    if args.build:
        _build_extension(Path(tempfile.mkdtemp(prefix="th-fuzz-build-")))
    if (code := _run_standalone(args.mode, args.extra_corpus)) != 0:
        return code
    if args.skip_inprocess:
        return 0
    return _run_inprocess(args.mode, args.minutes)


if __name__ == "__main__":
    raise SystemExit(main())
