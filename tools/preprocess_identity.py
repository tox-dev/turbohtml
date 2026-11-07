"""
Check that the fuzz-only branches add nothing to a production build's preprocessed C.

``-Dfuzzing=true`` code lives in ``#ifdef FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION`` branches inside production
sources and headers. A header change that alters the production token stream shifts LTO inlining and the CodSpeed
instruction gate, so this preprocesses every translation unit of the CodSpeed build (release, LTO) twice: once as
written and once from a mirror of ``src`` that ``unifdef -b`` stripped of those branches. ``-b`` blanks the removed
lines instead of deleting them, so ``__LINE__`` and the line markers stay comparable and the two outputs must match
byte for byte. Run it from the repository root: ``python tools/preprocess_identity.py``.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_BUILD: Final[Path] = _ROOT / "build" / "preprocess-identity"


def main() -> int:
    # Return 0 when every translation unit preprocesses identically with the fuzz-only branches stripped.
    if not (_BUILD / "compile_commands.json").is_file():
        subprocess.run(["meson", "setup", str(_BUILD), "--buildtype=release", "-Db_lto=true"], cwd=_ROOT, check=True)
    entries = json.loads((_BUILD / "compile_commands.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="th-preprocess-") as scratch:
        mirror = Path(scratch)
        _strip(mirror)
        differing = [
            entry["file"]
            for entry in entries
            if _preprocess(entry, Path(entry["directory"]))
            != _preprocess(entry, mirror / Path(entry["directory"]).relative_to(_ROOT))
        ]
    for name in differing:
        print(f"production preprocessing changes with the fuzz-only branches stripped: {name}", file=sys.stderr)
    print(f"{len(entries) - len(differing)} of {len(entries)} translation units preprocess identically")
    return 1 if differing else 0


def _strip(mirror: Path) -> None:
    # Mirror ``src`` with the fuzz-only branches blanked and the build directory's include paths created.
    for source in sorted((_ROOT / "src").rglob("*")):
        target = mirror / source.relative_to(_ROOT)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.suffix in {".c", ".h"}:
            result = subprocess.run(
                ["unifdef", "-b", "-UFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION", "-o", str(target), str(source)],
                check=False,
                capture_output=True,
            )
            # unifdef exits 0 when the output equals the input, 1 when it removed a branch, 2 on an error
            if result.returncode not in {0, 1}:
                msg = f"unifdef failed on {source}: {result.stderr.decode()}"
                raise SystemExit(msg)
    for directory in (_BUILD, *(path for path in _BUILD.rglob("*") if path.is_dir())):
        (mirror / directory.relative_to(_ROOT)).mkdir(parents=True, exist_ok=True)


def _preprocess(entry: dict[str, str], directory: Path) -> bytes:
    arguments = shlex.split(entry["command"])
    if Path(arguments[0]).name in {"ccache", "sccache"}:
        arguments = arguments[1:]
    kept: list[str] = []
    skip = False
    for argument in arguments:
        if skip:
            skip = False
        elif argument in {"-MQ", "-MF", "-o"}:
            skip = True
        elif argument not in {"-MD", "-c"}:
            kept.append(argument)
    return subprocess.run([*kept, "-E"], cwd=directory, capture_output=True, check=True).stdout


if __name__ == "__main__":
    raise SystemExit(main())
