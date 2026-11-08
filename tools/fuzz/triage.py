"""Keep stack signatures in private reports rather than public CI logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

_KIND: Final[re.Pattern[str]] = re.compile(
    r"(?#out of memory with dashes or spaces)out[- ]of[- ]memory"
    r"|(?#out of memory)out of memory|(?#Python MemoryError)MemoryError|(?#rss limit)rss limit"
    r"|(?#libFuzzer timeout)libFuzzer: timeout|(?#error line that mentions a timeout)ERROR:.*timeout"
    r"|(?#Python timeout)TimeoutError",
    re.IGNORECASE,
)
_NATIVE: Final[re.Pattern[str]] = re.compile(
    r"(?#line start)^\s*(?#hash and frame index)#(?P<index>\d+)(?#whitespace)\s+"
    r"(?#optional address)(?:0x[0-9a-f]+\s+)?(?#optional in keyword)(?:in\s+)?(?#the frame text)(?P<frame>.+)",
    re.IGNORECASE,
)
_PYTHON: Final[re.Pattern[str]] = re.compile(
    r'(?#line start)^\s*(?#File keyword)File "(?P<file>[^"]+)"(?#line keyword), line (?P<line>\d+)'
    r"(?#in keyword), in (?P<function>.+)(?#line end)$"
)
_LOCATION: Final[re.Pattern[str]] = re.compile(
    r"(?#optional drive letter)(?:[A-Za-z]:)?"
    r"(?#a path separator)[/\\]"
    r"(?#path characters up to a space or parenthesis)[^\s()]+"
)
_ADDRESS: Final[re.Pattern[str]] = re.compile(r"0x[0-9a-f]+", re.IGNORECASE)


def main(argv: Sequence[str] | None = None) -> int:
    # Write private group metadata; print counts without source frames.
    parser = argparse.ArgumentParser(description="Group saved fuzz reports without replaying inputs.")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="private JSON report")
    args = parser.parse_args(argv)
    try:
        groups = _groups(args.reports)
        args.output.write_text(f"{json.dumps(groups, indent=2, sort_keys=True)}\n", encoding="utf-8")
    except OSError as error:
        parser.exit(2, f"triage: {error}\n")
    print(f"triage: {len(args.reports)} reports, {len(groups)} groups")
    return 0


def _groups(paths: Sequence[Path]) -> dict[str, _Group]:
    groups: dict[str, _Group] = {}
    for path in paths:
        finding = classify(path.read_text(encoding="utf-8", errors="replace"))
        groups.setdefault(finding.digest, {"kind": finding.kind, "frames": finding.frames, "reports": []})[
            "reports"
        ].append(str(path))
    return groups


def classify(report: str) -> Finding:
    # Use five frames, falling back to report text when no stack is present.
    if match := _KIND.search(report):
        kind: Literal["crash", "timeout", "oom"] = "timeout" if "timeout" in match.group().lower() else "oom"
    else:
        kind = "crash"
    frames = _frames(report)
    identity = json.dumps((kind, frames or (_ADDRESS.sub("<address>", report.strip()),)), separators=(",", ":"))
    return Finding(kind=kind, frames=frames, digest=hashlib.sha256(identity.encode()).hexdigest())


def _frames(report: str) -> tuple[str, ...]:
    native: list[str] = []
    python: list[str] = []
    for line in report.splitlines():
        if match := _NATIVE.match(line):
            if match["index"] == "0" and native:
                break
            native.append(
                _LOCATION.sub(lambda item: item.group().replace("\\", "/").rsplit("/", 1)[-1], match["frame"])
            )
            if len(native) == 5:
                break
        elif match := _PYTHON.match(line):
            python.append(
                f"{match['function']} {match['file'].replace(chr(92), '/').rsplit('/', 1)[-1]}:{match['line']}"
            )
    return tuple(native or list(reversed(python))[:5])


@dataclass(frozen=True, kw_only=True, slots=True)
class Finding:
    # The report signature is independent of a crashing input digest.
    kind: Literal["crash", "timeout", "oom"]
    frames: tuple[str, ...]
    digest: str


class _Group(TypedDict):
    kind: str
    frames: tuple[str, ...]
    reports: list[str]


__all__ = [
    "Finding",
    "classify",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
