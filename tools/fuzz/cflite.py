"""
Run ClusterFuzzLite with its output kept off the public CI log.

The stock GitHub actions print each crash report, which carries the crashing input for short inputs, and upload the
input as a workflow artifact anyone signed in can download. This driver runs the same pinned images on the standalone
platform with a filesystem filestore instead: the build and run logs, crashes, corpus and coverage land in files under
``--workspace``, and the console gets only exit codes, crash hashes and, when the build fails, the build log. The
workflow then commits the corpus, crashes and logs to a public state branch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from zipfile import ZipFile

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["BUILD_IMAGE", "RUN_IMAGE", "main"]

# the v1 tags the google/clusterfuzzlite actions use, pinned so a retag cannot change what runs
_IMAGES: Final = "gcr.io/oss-fuzz-base/clusterfuzzlite-"
BUILD_IMAGE: Final = _IMAGES + "build-fuzzers@sha256:831044bb06844a77e6b435d6476da4917386119c83a76aa9c0c0b02b3d1a2bb5"
RUN_IMAGE: Final = _IMAGES + "run-fuzzers@sha256:35b5e685193f1f920f6627dd8f0bd9009fbb338eab795254d48bd073b45235cf"
_BUILD_CHECK: Final = "Build check: stdout:"
_BUILD_CHECK_VERDICT: Final = re.compile(r"BAD BUILD: |\d{4}-\d\d-\d\d [\d:,]+ - \S+ - [A-Z]+ - ")
_BUILD_LOG_LINES: Final = 200


def main(argv: Sequence[str] | None = None) -> int:
    """Return the run's exit code: nonzero when the build breaks or a fuzzer crashes."""
    parser: Final = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("code-change", "batch", "prune", "coverage"), required=True)
    parser.add_argument("--sanitizer", choices=("address", "undefined", "coverage"), required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--source", type=Path, required=True, help="the checkout, with its full history")
    parser.add_argument("--workspace", type=Path, required=True, help="holds storage/, logs/ and the CFLite outputs")
    parser.add_argument("--base-commit", help="the commit a code-change run diffs against and finds its base build at")
    parser.add_argument("--upload-build", action="store_true", help="keep this build as the next run's base build")
    arguments: Final = parser.parse_args(argv)
    return _run(
        _Job(
            arguments.mode,
            arguments.sanitizer,
            arguments.seconds,
            arguments.source.resolve(),
            arguments.workspace.resolve(),
            arguments.base_commit,
            arguments.upload_build,
        )
    )


def _run(job: _Job) -> int:
    """Build, then fuzz, prune or measure; the filestore under workspace/storage keeps what the run produced."""
    workspace: Final = job.workspace
    storage: Final = workspace / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    logs: Final = workspace / "logs"
    logs.mkdir(exist_ok=True)
    environment: Final = {
        "CFL_PLATFORM": "standalone",
        "WORKSPACE": str(workspace),
        "PROJECT_SRC_PATH": str(job.source),
        "REPOSITORY": "turbohtml",
        "FILESTORE": "filesystem",
        "FILESTORE_ROOT_DIR": str(storage),
        "LANGUAGE": "python",
        "SANITIZER": job.sanitizer,
        "LOW_DISK_SPACE": "True",
        **({} if job.base_commit is None else {"GIT_BASE_COMMIT": job.base_commit}),
        **({"UPLOAD_BUILD": "True"} if job.upload_build else {}),
    }
    status: Final = {
        "build": _docker(
            BUILD_IMAGE, environment, (workspace, job.source), build_log := logs / f"build-{job.sanitizer}.log"
        )
    }
    if status["build"] != 0:
        print(_public_build_log(build_log), file=sys.stderr)
    else:
        if job.mode in {"batch", "coverage"}:
            _add_seeds(workspace / "build-out", storage / "corpus")
        status[job.mode] = _docker(
            RUN_IMAGE,
            {**environment, "MODE": job.mode, "FUZZ_SECONDS": str(job.seconds), "OUTPUT_SARIF": "True"},
            (workspace, job.source),
            logs / f"{job.mode}-{job.sanitizer}.log",
        )
    print(json.dumps({"status": status, "crashes": _crashes(workspace)}, indent=2, sort_keys=True))
    return max(status.values())


def _add_seeds(build: Path, corpus: Path) -> None:
    for archive_path in sorted(build.glob("fuzz_*_seed_corpus.zip")):
        target = corpus / archive_path.name.removesuffix("_seed_corpus.zip")
        target.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                (target / Path(name).name).write_bytes(archive.read(name))


def _docker(image: str, environment: dict[str, str], mounts: Sequence[Path], log: Path) -> int:
    # CFLite starts the builder and runner as sibling containers through the socket and hands them its own volumes, so
    # each mount keeps its host path; SELinux labels on the outer container block the nested builder's writes
    command: Final = [
        "docker",
        "run",
        "--rm",
        "--security-opt",
        "label=disable",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        *(argument for mount in mounts for argument in ("-v", f"{mount}:{mount}")),
        *(argument for key, value in environment.items() for argument in ("-e", f"{key}={value}")),
        image,
    ]
    with log.open("ab") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False).returncode


def _public_build_log(log: Path) -> str:
    # docker build, pip, meson and compiler output name files and flags, never a fuzz input, so a failed build can show
    # its log on the public console; the bad build check then runs each fuzzer on its first inputs and prints the run
    # of one that dies, so from there on only the verdict lines and CIFuzz's own log lines stay
    build, _, check = log.read_text(encoding="utf-8", errors="replace").partition(_BUILD_CHECK)
    lines: Final = build.splitlines() + [line for line in check.splitlines() if _BUILD_CHECK_VERDICT.match(line)]
    return "\n".join(lines[-_BUILD_LOG_LINES:])


def _crashes(workspace: Path) -> list[dict[str, str | int]]:
    # the hash names a crash for triage without exposing the input, as fuzz.py logs its crashers. CIFuzz saves each
    # input as out/artifacts/<target>/<sanitizer>/<input> next to an <input>.summary stack trace (_target_artifact_path
    # and _save_crash in infra/cifuzz/fuzz_target.py, google/oss-fuzz@868bfcbd1db0c8eb4dc6029598c90705d6c88e74)
    artifacts: Final = workspace / "out" / "artifacts"
    return [
        {
            "fuzzer": path.relative_to(artifacts).parts[0],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in sorted(artifacts.rglob("*"))
        if path.is_file() and path.suffix != ".summary"
    ]


@dataclass(frozen=True)
class _Job:
    """One ClusterFuzzLite run: what it does, for how long, over which checkout, into which workspace."""

    mode: str
    sanitizer: str
    seconds: int
    source: Path
    workspace: Path
    base_commit: str | None
    upload_build: bool


if __name__ == "__main__":
    sys.exit(main())
