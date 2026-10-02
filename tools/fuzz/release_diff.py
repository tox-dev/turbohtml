"""
Previous-release differential: the ``--mode release-diff`` lane of ``tools/fuzz/fuzz.py``.

A performance or refactoring change can move output without any oracle noticing, so this feeds the same generated
inputs to HEAD and to the latest PyPI release, each in its own interpreter running ``release_worker.py``, and reports
every operation whose result moved (html5gum's ``FUZZ_OLD_HTML5GUM`` mode). A difference is a lead to triage, not
necessarily a bug: every fix merged since the release shows up too. ``--errors`` sets how two failures compare: by
type and message, by type only, or any failure equal to any other.

A negative control runs a HEAD worker told to perturb one operation and must report it, and a vacuity floor bounds the
share of cases both sides answered. Findings land in ``--crash-dir`` like every other lane's, logged by hash only.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

from fuzz.round_trip_oracles import MAX_INPUT, ORACLES, UNBOUNDED, Floor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from typing_extensions import Self

__all__ = ["Worker", "compare", "main"]

_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
_WORKER: Final[Path] = Path(__file__).with_name("release_worker.py")
# each operation reads inputs from the round-trip oracle whose generator writes its language
_SOURCES: Final[dict[str, str]] = {
    "serialize": "html-fixpoint",
    "fragment": "html-fixpoint",
    "xml": "html-fixpoint",
    "text": "html-fixpoint",
    "markdown": "markdown-fixpoint",
    "minify_html": "html-fixpoint",
    "sanitize": "html-fixpoint",
    "minify_css": "css-fixpoint",
    "minify_js": "js-fixpoint",
    "minify_js_plain": "js-fixpoint",
    "style": "style-fixpoint",
    "select": "selector-entry",
    "css_to_xpath": "selector-entry",
    "xpath": "xpath-entry",
}
_FLOOR: Final = Floor(200, 0.9)
_SEEDS_PER_OPERATION: Final = 40


def main(argv: Sequence[str] | None = None) -> int:
    """Return 0 when both sides agree, 1 on a difference, 2 when the control stayed silent or too little compared."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=1.0, help="generation budget after the seed pass")
    parser.add_argument(
        "--rng-seed",
        type=int,
        default=int(os.environ.get("FUZZ_RNG_SEED", "0")),
        help="seed of the generated cases (default: $FUZZ_RNG_SEED, else 0)",
    )
    parser.add_argument("--crash-dir", type=Path, default=_ROOT / ".fuzz-crashes", help="where differences land")
    parser.add_argument("--errors", choices=("message", "type", "any"), default="type", help="how two failures compare")
    parser.add_argument("--release", default="", help="the PyPI version to compare with (default: the latest)")
    parser.add_argument(
        "--release-python",
        type=Path,
        default=None,
        help="an interpreter whose installed turbohtml stands for the release, instead of installing one",
    )
    args = parser.parse_args(argv)

    release_python = args.release_python or _install(args.release)
    with Worker(Path(sys.executable)) as head, Worker(release_python) as release:
        if not _control_fires(head):
            print("BLIND ORACLE: the perturbed worker went unreported", file=sys.stderr)
            return 2
        run = _Run(head, release, args.errors)
        seed_rng = random.Random(0)
        for operation, source in _SOURCES.items():
            for _ in range(_SEEDS_PER_OPERATION):
                run.case(operation, ORACLES[source].generate(seed_rng))
        rng = random.Random(args.rng_seed)
        deadline = time.monotonic() + args.minutes * 60
        while time.monotonic() < deadline:
            operation = rng.choice(list(_SOURCES))
            run.case(operation, ORACLES[_SOURCES[operation]].generate(rng))
    print(f"release-diff: {dict(run.stats)}")
    args.crash_dir.mkdir(parents=True, exist_ok=True)
    findings = run.report(args.crash_dir)
    compared, skipped = run.stats["compared"], run.stats["missing"]
    if compared < _FLOOR.count or compared < _FLOOR.ratio * (compared + skipped):
        print("VACUOUS RUN: the release answered too few cases", file=sys.stderr)
        return 2
    print(f"{findings} difference(s) written to {args.crash_dir}")
    return int(findings > 0)


def _install(release: str) -> Path:
    """Install the release wheel into a cached venv of this interpreter's version and return its interpreter."""
    venv = (
        Path(tempfile.gettempdir())
        / f"turbohtml-release-{release or 'latest'}-{sys.version_info[0]}.{sys.version_info[1]}"
    )
    python = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    if not python.exists():
        subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, str(venv)], check=True)
    requirement = f"turbohtml=={release}" if release else "turbohtml"
    command = ["uv", "pip", "install", "--quiet", "--python", str(python), "--only-binary", ":all:", "--upgrade"]
    subprocess.run([*command, requirement], check=True)
    return python


class Worker:
    """One ``release_worker.py`` process, answering one operation per line."""

    def __init__(self, python: Path, *, perturb: str | None = None) -> None:
        """Start the worker under ``python``; ``perturb`` names an operation whose results it marks."""
        self._command = [str(python), str(_WORKER), *(["--perturb", perturb] if perturb else [])]
        self._process = self._start()

    def _start(self) -> subprocess.Popen[str]:
        # a worker for an installed wheel must import that wheel, not the checkout this driver runs from
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        return subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="surrogatepass",
            env=env,
        )

    def __enter__(self) -> Self:
        """Use the worker for the length of a ``with`` block."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the worker's input, which ends its loop, and reap it."""
        self._reap()

    def _reap(self) -> int:
        assert self._process.stdin is not None  # ruff: ignore[assert]  # Popen with stdin=PIPE always sets it
        assert self._process.stdout is not None  # ruff: ignore[assert]  # Popen with stdout=PIPE always sets it
        # a worker that already exited cannot take the buffered tail: EPIPE on POSIX, EINVAL on Windows
        with contextlib.suppress(OSError):
            self._process.stdin.close()
        code = self._process.wait()
        self._process.stdout.close()
        return code

    def answer(self, operation: str, text: str) -> dict[str, object]:
        """Return the worker's answer for one operation on one input; a worker that dies answers ``crashed``."""
        assert self._process.stdin is not None  # ruff: ignore[assert]  # Popen with stdin=PIPE always sets it
        assert self._process.stdout is not None  # ruff: ignore[assert]  # Popen with stdout=PIPE always sets it
        try:
            self._process.stdin.write(json.dumps([operation, text]) + "\n")
            self._process.stdin.flush()
        except OSError:
            line = ""  # the worker exited before it read the case
        else:
            line = self._process.stdout.readline()
        if line:
            return json.loads(line)
        # stderr stays closed: a dying worker's traceback or sanitizer report would print the input's location
        code = self._reap()
        self._process = self._start()
        return {"crashed": code}


def compare(head: dict[str, object], release: dict[str, object], errors: str) -> str | None:
    """
    Name how two answers differ, or return None when they agree.

    ``errors`` is the normalization knob: ``message`` compares a failure's type and message, ``type`` its type only,
    ``any`` treats every failure as equal to every other.
    """
    if "crashed" in head or "crashed" in release:
        return "HEAD crashed" if "crashed" in head else "release crashed"
    if "ok" in head and "ok" in release:
        return None if head["ok"] == release["ok"] else "output differs"
    if "error" in head and "error" in release:
        head_error, release_error = head["error"], release["error"]
        assert isinstance(head_error, list)  # ruff: ignore[assert]  # the worker always reports [type, message]
        assert isinstance(release_error, list)  # ruff: ignore[assert]  # the worker always reports [type, message]
        agree = {"message": head_error == release_error, "type": head_error[0] == release_error[0], "any": True}
        return None if agree[errors] else "error differs"
    return "HEAD raises" if "error" in head else "release raises"


def _control_fires(head: Worker) -> bool:
    with Worker(Path(sys.executable), perturb="minify_css") as perturbed:
        answer = perturbed.answer("minify_css", "a{color:red}")
    return compare(head.answer("minify_css", "a{color:red}"), answer, "any") == "output differs"


@dataclass
class _Run:
    head: Worker
    release: Worker
    errors: str
    stats: Counter[str] = field(default_factory=Counter)
    found: dict[str, tuple[str, str, int]] = field(default_factory=dict)

    def case(self, operation: str, text: str) -> None:
        if len(text) > MAX_INPUT or UNBOUNDED.search(text):
            self.stats["skipped-input"] += 1
            return
        if "missing" in (answer := self.release.answer(operation, text)):
            self.stats["missing"] += 1
            return
        self.stats["compared"] += 1
        if (difference := compare(self.head.answer(operation, text), answer, self.errors)) is not None:
            key = f"{operation}: {difference}"
            hits = self.found[key][2] + 1 if key in self.found else 1
            self.found[key] = (operation, self.found[key][1] if key in self.found else text, hits)

    def report(self, crash_dir: Path) -> int:
        for key, (operation, text, hits) in self.found.items():
            data = json.dumps({"operation": operation, "input": text}).encode()
            digest = hashlib.sha256(data).hexdigest()
            (crash_dir / f"crash-{digest}").write_bytes(data)
            sidecar = json.dumps({"oracle": "release-diff", "detail": key, "hits": hits}, indent=2) + "\n"
            (crash_dir / f"crash-{digest}.json").write_text(sidecar, encoding="utf-8")
            # only the oracle and a hash reach the log: the input may be an unreported bug (DESIGN-v2 decision D5)
            print(f"FINDING release-diff sha256={digest}", file=sys.stderr, flush=True)
        return len(self.found)


if __name__ == "__main__":
    raise SystemExit(main())
