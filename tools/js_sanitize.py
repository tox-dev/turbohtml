"""
Compile the JavaScript-minifier engine standalone and run it under the sanitizers.

The engine builds with ``-DJM_STANDALONE`` against the system allocator (no CPython
runtime), so ``tools/js_minify_harness.c`` can push the committed corpus through it
under AddressSanitizer + UndefinedBehaviorSanitizer everywhere, plus LeakSanitizer on
Linux (Apple clang has no LSan). A clean run exits 0; any sanitizer abort fails the
gate. Corpus inputs come from the vendored ``tests/serialize/js/_corpus`` fixtures, so
the check needs no network and is fully reproducible.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_ENGINE: Final[tuple[str, ...]] = (
    "lexer.c",
    "ast.c",
    "parser.c",
    "printer.c",
    "fold.c",
    "mangle.c",
    "minify.c",
)


def main() -> int:
    """Return the harness's exit code: 0 for a clean run, the sanitizer's abort code otherwise."""
    corpus_dir = Path(tempfile.mkdtemp(prefix="jsmin-corpus-"))
    count = 0
    for fixture in sorted((_ROOT / "tests" / "serialize" / "js" / "_corpus").glob("*.json")):
        for row in json.loads(fixture.read_text(encoding="utf-8")):
            (corpus_dir / f"{count}.js").write_text(row["input"], encoding="utf-8")
            count += 1

    binary = corpus_dir / "jsmin_harness"
    cmd = [
        os.environ.get("CC", "clang"),
        "-DJM_STANDALONE",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-g",
        "-O1",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-I",
        str(_ROOT / "src" / "turbohtml" / "_c"),
        str(_ROOT / "tools" / "js_minify_harness.c"),
        *[str(_ROOT / "src" / "turbohtml" / "_c" / "serialize" / "js" / name) for name in _ENGINE],
        "-o",
        str(binary),
    ]
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)

    on_linux = platform.system() == "Linux"
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = f"detect_leaks={1 if on_linux else 0}:halt_on_error=1"
    env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    files = sorted(str(path) for path in corpus_dir.glob("*.js"))
    print(f"running harness over {len(files)} corpus inputs (leak detection {'on' if on_linux else 'off (macOS)'})")
    if (result := subprocess.run([str(binary), *files], env=env, check=False)).returncode != 0:
        print(f"SANITIZER ABORT (exit {result.returncode})", file=sys.stderr)
        return result.returncode
    print("sanitizer run clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
