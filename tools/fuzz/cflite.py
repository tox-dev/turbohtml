"""
Run ClusterFuzzLite with its output kept off the public CI log.

The stock GitHub actions print each crash report, which carries the crashing input for short inputs, and upload the
input as a workflow artifact anyone signed in can download. This driver runs the same pinned images on the standalone
platform with a filesystem filestore instead: the build and run logs, crashes, corpus and coverage land in files under
``--workspace``, and the console gets only exit codes, crash hashes and, when the build fails, the build log. The
workflow then commits the corpus, crashes and logs to a private storage repository.

Pruning and coverage run each fuzz target in its own CIFuzz run, ``--jobs`` at a time, all stopping at the deadline
``--seconds`` sets; a target still running then is stopped and reported with exit code 124, and so is a target that
only reaches the deadline without a free job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Final
from zipfile import ZipFile

if TYPE_CHECKING:
    from collections.abc import Sequence

# the v1 tags the google/clusterfuzzlite actions use, pinned so a retag cannot change what runs
_IMAGES: Final[str] = "gcr.io/oss-fuzz-base/clusterfuzzlite-"
BUILD_IMAGE: Final[str] = (
    f"{_IMAGES}build-fuzzers@sha256:831044bb06844a77e6b435d6476da4917386119c83a76aa9c0c0b02b3d1a2bb5"
)
RUN_IMAGE: Final[str] = f"{_IMAGES}run-fuzzers@sha256:35b5e685193f1f920f6627dd8f0bd9009fbb338eab795254d48bd073b45235cf"
_BUILD_CHECK_VERDICT: Final[re.Pattern[str]] = re.compile(r"BAD BUILD: |\d{4}-\d\d-\d\d [\d:,]+ - \S+ - [A-Z]+ - ")
# a CIFuzz run uploads the corpus and its report after its own deadline, so it gets this much less than the time left
_UPLOAD_SECONDS: Final[int] = 120
_TIMED_OUT: Final[int] = 124


def main(argv: Sequence[str] | None = None) -> int:
    # Return the run's exit code: nonzero when the build breaks or a fuzzer crashes.
    parser: Final = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("code-change", "batch", "prune", "coverage"), required=True)
    parser.add_argument("--sanitizer", choices=("address", "undefined", "coverage"), required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--source", type=Path, required=True, help="the checkout, with its full history")
    parser.add_argument("--workspace", type=Path, required=True, help="holds storage/, logs/ and the CFLite outputs")
    parser.add_argument("--base-commit", help="the commit a code-change run diffs against and finds its base build at")
    parser.add_argument("--upload-build", action="store_true", help="keep this build as the next run's base build")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="targets pruned or measured at once")
    arguments: Final = parser.parse_args(argv)
    return _run(
        _Job(
            mode=arguments.mode,
            sanitizer=arguments.sanitizer,
            seconds=arguments.seconds,
            source=arguments.source.resolve(),
            workspace=arguments.workspace.resolve(),
            base_commit=arguments.base_commit,
            upload_build=arguments.upload_build,
            jobs=arguments.jobs,
        )
    )


def _run(job: _Job) -> int:
    # Build, then fuzz, prune or measure; the filestore under workspace/storage keeps what the run produced.
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
            _add_seeds(
                workspace / "build-out",
                workspace / "coverage-corpus" if job.mode == "coverage" else storage / "corpus",
            )
        run_environment: Final = {**environment, "MODE": job.mode, "OUTPUT_SARIF": "True"}
        if job.mode in {"prune", "coverage"}:
            status.update(_run_targets(job, run_environment))
        else:
            status[job.mode] = _docker(
                RUN_IMAGE,
                {**run_environment, "FUZZ_SECONDS": str(job.seconds)},
                (workspace, job.source),
                logs / f"{job.mode}-{job.sanitizer}.log",
            )
    print(json.dumps({"status": status, "crashes": _crashes(workspace)}, indent=2, sort_keys=True))
    return max(status.values())


def _run_targets(job: _Job, environment: dict[str, str]) -> dict[str, int]:
    # one CIFuzz run prunes its targets in series and gives each a share of FUZZ_SECONDS as its merge timeout; the
    # first merge to time out raises out of the loop and leaves every later target unpruned. Its coverage mode never
    # reads FUZZ_SECONDS, and base-runner's coverage script replays each Python target's whole corpus in series. So
    # each target gets a run of its own, the largest corpus first, and all of them stop at the same deadline
    deadline: Final = monotonic() + job.seconds
    build: Final = job.workspace / "build-out"
    corpus: Final = (
        job.workspace / "coverage-corpus" if job.mode == "coverage" else job.workspace / "storage" / "corpus"
    )
    targets: Final = sorted(
        (path for path in build.iterdir() if _is_fuzz_target(path)),
        key=lambda path: (-len(list((corpus / path.name).glob("*"))), path.name),
    )
    names: Final = frozenset(target.name for target in targets)
    with ThreadPoolExecutor(max_workers=job.jobs) as pool:
        codes: Final = list(
            pool.map(lambda target: _run_target(job, environment, target, names - {target.name}, deadline), targets)
        )
    return {f"{job.mode} {target.name}": code for target, code in zip(targets, codes, strict=True)}


def _is_fuzz_target(path: Path) -> bool:
    # CIFuzz's own test (is_fuzz_target_local in infra/utils.py): an executable file with no extension that names the
    # libFuzzer entry point; llvm-symbolizer is executable but carries no such name
    return (
        path.is_file()
        and not path.suffix
        and os.access(path, os.X_OK)
        and b"LLVMFuzzerTestOneInput" in path.read_bytes()
    )


def _run_target(job: _Job, environment: dict[str, str], target: Path, others: frozenset[str], deadline: float) -> int:
    seconds: Final = int(deadline - monotonic()) - _UPLOAD_SECONDS
    if seconds < 1:
        return _TIMED_OUT
    workspace: Final = job.workspace / "targets" / target.name
    build: Final = workspace / "build-out"
    build.mkdir(parents=True, exist_ok=True)
    # CIFuzz runs every fuzz target it finds in build-out, and base-runner's coverage script finds them with
    # find -type f, which skips links: the target is copied, everything else it needs is linked, and the files of
    # the other targets stay out
    for path in target.parent.iterdir():
        if path.name.split(".")[0].removesuffix("_seed_corpus") not in others:
            (build / path.name).unlink(missing_ok=True)
            if path == target:
                shutil.copy2(path, build / path.name)
            else:
                (build / path.name).symlink_to(path)
    # The build seeds reach every public function; replaying the full fuzz corpus exceeds the coverage deadline.
    # Crashes stay in the shared filestore, while each coverage report stays in its own workspace.
    storage: Final = workspace / "storage"
    storage.mkdir(exist_ok=True)
    for name in ("corpus", "crashes"):
        shared = (
            job.workspace / "coverage-corpus"
            if job.mode == "coverage" and name == "corpus"
            else job.workspace / "storage" / name
        )
        shared.mkdir(exist_ok=True)
        (storage / name).unlink(missing_ok=True)
        (storage / name).symlink_to(shared)
    logs: Final = job.workspace / "logs" / f"{job.mode}-{job.sanitizer}"
    logs.mkdir(exist_ok=True)
    code: Final = _docker(
        RUN_IMAGE,
        {
            **environment,
            "WORKSPACE": str(workspace),
            "FILESTORE_ROOT_DIR": str(storage),
            "FUZZ_SECONDS": str(seconds),
        },
        (job.workspace, job.source),
        logs / f"{target.name}.log",
        seconds + _UPLOAD_SECONDS,
    )
    # the reachability check reads every target's coverage data from one directory
    if (data := build / f"coverage_d_{target.name}").is_file():
        collected = job.workspace / "coverage-data"
        collected.mkdir(exist_ok=True)
        shutil.copy2(data, collected / data.name)
    return code


def _add_seeds(build: Path, corpus: Path) -> None:
    for archive_path in sorted(build.glob("fuzz_*_seed_corpus.zip")):
        target = corpus / archive_path.name.removesuffix("_seed_corpus.zip")
        target.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive_path) as archive:
            for name in archive.namelist():
                (target / Path(name).name).write_bytes(archive.read(name))


def _docker(
    image: str, environment: dict[str, str], mounts: Sequence[Path], log: Path, seconds: int | None = None
) -> int:
    # CFLite starts the builder and runner as sibling containers through the socket and hands them its own volumes, so
    # each mount keeps its host path; SELinux labels on the outer container block the nested builder's writes
    name: Final = f"cflite-{uuid.uuid4().hex}"
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
        *(() if seconds is None else ("--name", name)),
        image,
    ]
    with log.open("ab") as output:
        try:
            return subprocess.run(
                command, stdout=output, stderr=subprocess.STDOUT, check=False, timeout=seconds
            ).returncode
        except subprocess.TimeoutExpired:
            # killing the docker client leaves its container running
            subprocess.run(["docker", "kill", name], stdout=output, stderr=subprocess.STDOUT, check=False)
            return _TIMED_OUT


def _public_build_log(log: Path) -> str:
    # docker build, pip, meson and compiler output name files and flags, never a fuzz input, so a failed build can show
    # its log on the public console; the bad build check then runs each fuzzer on its first inputs and prints the run
    # of one that dies, so from there on only the verdict lines and CIFuzz's own log lines stay
    build, _, check = log.read_text(encoding="utf-8", errors="replace").partition("Build check: stdout:")
    lines: Final = build.splitlines() + [line for line in check.splitlines() if _BUILD_CHECK_VERDICT.match(line)]
    return "\n".join(lines[-200:])


def _crashes(workspace: Path) -> list[dict[str, str | int]]:
    # the hash names a crash for triage without exposing the input, as fuzz.py logs its crashers. CIFuzz saves each
    # input as out/artifacts/<target>/<sanitizer>/<input> next to an <input>.summary stack trace (_target_artifact_path
    # and _save_crash in infra/cifuzz/fuzz_target.py, google/oss-fuzz@868bfcbd1db0c8eb4dc6029598c90705d6c88e74)
    # and the per-target runs of pruning and coverage keep theirs under targets/<target>/
    return [
        {
            "fuzzer": path.relative_to(artifacts).parts[0],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for artifacts in (run / "out" / "artifacts" for run in (workspace, *sorted((workspace / "targets").glob("*"))))
        for path in sorted(artifacts.rglob("*"))
        if path.is_file() and path.suffix != ".summary"
    ]


@dataclass(frozen=True, kw_only=True, slots=True)
class _Job:
    # One ClusterFuzzLite run: what it does, for how long, over which checkout, into which workspace.
    mode: str
    sanitizer: str
    seconds: int
    source: Path
    workspace: Path
    base_commit: str | None
    upload_build: bool
    jobs: int


__all__ = [
    "BUILD_IMAGE",
    "RUN_IMAGE",
    "main",
]


if __name__ == "__main__":
    sys.exit(main())
