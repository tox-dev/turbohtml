"""
Format and lint the Python snippets embedded in the reStructuredText docs with Ruff.

Ruff has no native reStructuredText support, so this hook extracts every ``testcode`` and
``code-block:: python`` body, dedents it, runs ``ruff check --fix`` and ``ruff format`` over
it under the project configuration, then splices the result back at the original indentation.
Files are rewritten only when their snippets change, and the hook exits non-zero on any
rewrite or residual lint finding so the commit fails until the docs match the code style.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "pyproject.toml"
DIRECTIVE = re.compile(r"^(?P<indent>[ \t]*)\.\. (?:testcode::|code-block:: (?:python|py|python3))\s*$")

# Rules that fire on every well-formed doctest example, so enforcing them would only push noqa
# noise into the rendered docs (and let --fix corrupt the migration before/after blocks).
SNIPPET_IGNORE = (
    "T201",  # examples print to produce the testoutput block
    "INP001",  # a doc snippet is not an importable package
    "D",  # examples carry no docstrings
    "ANN",  # examples skip annotations for brevity
    "I001",  # before/after blocks keep two import groups isort would merge
    "I002",  # snippets do not carry the from __future__ import boilerplate
    "F821",  # turbohtml and parse come from conf.py's doctest_global_setup
    "F401",  # migration blocks show the old import without using it
    "F811",  # ... and redefine the same name on the turbohtml side
    "E401",  # comparison imports are grouped on one line
    "E402",  # imports follow prose-driven setup statements
    "ARG001",  # illustrative callbacks accept arguments they ignore
)


@dataclass(slots=True)
class Snippet:
    """One extracted Python block: where it lives and how it was indented."""

    source: Path
    line_no: int
    body_start: int
    body_end: int
    content_indent: int
    code: str


def indent_of(line: str) -> int:
    """Return the count of leading whitespace characters in ``line``."""
    return len(line) - len(line.lstrip())


def snippets_of(source: Path, lines: list[str]) -> list[Snippet]:
    """Return every ``testcode``/``code-block:: python`` block found in ``lines``."""
    blocks: list[Snippet] = []
    cursor = 0
    while cursor < len(lines):
        if not (directive := DIRECTIVE.match(lines[cursor])):
            cursor += 1
            continue
        base = len(directive.group("indent"))
        line_no = cursor + 1
        cursor += 1
        while cursor < len(lines) and (
            not lines[cursor].strip() or (lines[cursor].lstrip().startswith(":") and indent_of(lines[cursor]) > base)
        ):
            cursor += 1
        body_start = cursor
        while cursor < len(lines) and (not lines[cursor].strip() or indent_of(lines[cursor]) > base):
            cursor += 1
        body_end = cursor
        while body_end > body_start and not lines[body_end - 1].strip():
            body_end -= 1
        if body := [line for line in lines[body_start:body_end] if line.strip()]:
            content_indent = min(indent_of(line) for line in body)
            code = "\n".join(line[content_indent:] if line.strip() else "" for line in lines[body_start:body_end])
            blocks.append(Snippet(source, line_no, body_start, body_end, content_indent, code + "\n"))
    return blocks


def run_ruff(staging: Path) -> dict[Path, str]:
    """Fix and format every staged snippet file in ``staging`` and return their new contents."""
    ignore = ["--extend-ignore", ",".join(SNIPPET_IGNORE)]
    common = ["--config", str(CONFIG), str(staging)]
    subprocess.run(["ruff", "check", "--fix", "--quiet", *ignore, *common], check=False)
    subprocess.run(["ruff", "format", "--quiet", *common], check=False)
    return {path: path.read_text() for path in staging.glob("*.py")}


def reindent(code: str, content_indent: int) -> list[str]:
    """Re-apply ``content_indent`` to a dedented snippet, leaving blank lines empty."""
    pad = " " * content_indent
    return [pad + line if line.strip() else "" for line in code.rstrip("\n").split("\n")]


def report_residuals(staging: Path, owners: dict[Path, Snippet]) -> bool:
    """Print any lint finding Ruff could not auto-fix, mapped to its docs location."""
    result = subprocess.run(
        [
            "ruff",
            "check",
            "--no-fix",
            "--quiet",
            "--output-format=concise",
            "--extend-ignore",
            ",".join(SNIPPET_IGNORE),
            "--config",
            str(CONFIG),
            str(staging),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    findings = result.stdout.strip()
    if not findings:
        return False
    for line in findings.splitlines():
        if match := re.match(r"^(?P<file>.*\.py):(?P<row>\d+):(?P<col>\d+): (?P<rest>.*)$", line):
            owner = owners[Path(match.group("file"))]
            print(f"{owner.source}: snippet at line {owner.line_no}: {match.group('rest')}")
        else:
            print(line)
    return True


def main(argv: list[str]) -> int:
    """Format and lint the snippets in the ``.rst`` paths in ``argv``; return the hook exit code."""
    paths = [Path(arg) for arg in argv if arg.endswith(".rst")] or sorted(ROOT.joinpath("docs").rglob("*.rst"))
    documents = {path: path.read_text().splitlines() for path in paths}
    blocks = [block for path, lines in documents.items() for block in snippets_of(path, lines)]
    if not blocks:
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        owners = {staging / f"s{index}.py": block for index, block in enumerate(blocks)}
        for path, block in owners.items():
            path.write_text(block.code)
        formatted = run_ruff(staging)
        has_residuals = report_residuals(staging, owners)

    changed: set[Path] = set()
    for path, block in sorted(owners.items(), key=lambda item: item[1].body_start, reverse=True):
        new_body = reindent(formatted[path], block.content_indent)
        if documents[block.source][block.body_start : block.body_end] != new_body:
            documents[block.source][block.body_start : block.body_end] = new_body
            changed.add(block.source)

    for source in changed:
        source.write_text("\n".join(documents[source]) + "\n")
        print(f"reformatted snippets in {source}")

    return 1 if changed or has_residuals else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
