"""
Refresh and check the living WPT HTML tree-construction corpus.

Usage: python tools/generate_wpt_tree_corpus.py WPT_CHECKOUT OUTPUT_JSON
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Final, TypedDict

from turbohtml import _html, parse  # ruff:ignore[import-private-name]  # native WPT-format dumper


class _Error(TypedDict):
    code: str
    line: int
    col: int
    end_line: int | None
    end_col: int | None


class _Case(TypedDict):
    file: str
    data: str
    document: str
    context: str | None
    scripting: bool | None
    errors: list[_Error] | None
    spec_errors: list[_Error] | None


class _Decision(TypedDict):
    file: str
    data: str
    context: str | None
    scripting: bool | None
    reason: str
    spec: str
    fixture: str


class _Exclusion(_Decision):
    document: str


_Key = tuple[str, str, str | None, bool | None]
_PI_SPEC: Final[str] = "https://html.spec.whatwg.org/multipage/parsing.html#processing-instruction-open-state"
_SCRIPT_SPEC: Final[str] = "https://html.spec.whatwg.org/multipage/scripting.html#script-processing-model"
_WPT_BASE: Final[str] = (
    "https://github.com/web-platform-tests/wpt/blob/4830edb033cb486fd0cd6f85b5e937cfc718704d/"
    "html/syntax/parsing/resources"
)
_NEW_ERROR: Final[re.Pattern[str]] = re.compile(r"\((\d+):(\d+)(?:-(\d+):(\d+))?\) ([a-z0-9-]+)")
_SECTION: Final[re.Pattern[str]] = re.compile(
    r"(?m)^#(errors|new-errors|document-fragment|script-on|script-off)(?:\n|$)"
)

_SCRIPT_EXECUTION_EXCLUSIONS: Final[dict[_Key, tuple[str, str]]] = {
    (
        "scripted_adoption01.dat",
        '<p><b id="A"><script>document.getElementById("A").id = "B"</script></p>TEXT</b>',
        None,
        True,
    ): (
        (
            "| <html>\n"
            "|   <head>\n"
            "|   <body>\n"
            "|     <p>\n"
            "|       <b>\n"
            '|         id="A"\n'
            "|         <script>\n"
            '|           "document.getElementById("A").id = "B""\n'
            "|     <b>\n"
            '|       id="A"\n'
            '|       "TEXT"'
        ),
        f"{_WPT_BASE}/scripted_adoption01.dat#L1-L16",
    ),
    (
        "scripted_ark.dat",
        (
            '<p><font size=4><font size=4><font size=4><script>document.getElementsByTagName("font")[2]'
            '.setAttribute("size", "5");</script><font size=4><p>X'
        ),
        None,
        True,
    ): (
        (
            "| <html>\n"
            "|   <head>\n"
            "|   <body>\n"
            "|     <p>\n"
            "|       <font>\n"
            '|         size="4"\n'
            "|         <font>\n"
            '|           size="4"\n'
            "|           <font>\n"
            '|             size="4"\n'
            "|             <script>\n"
            '|               "document.getElementsByTagName("font")[2].setAttribute("size", "5");"\n'
            "|             <font>\n"
            '|               size="4"\n'
            "|     <p>\n"
            "|       <font>\n"
            '|         size="4"\n'
            "|         <font>\n"
            '|           size="4"\n'
            "|           <font>\n"
            '|             size="4"\n'
            '|             "X"'
        ),
        f"{_WPT_BASE}/scripted_ark.dat#L1-L27",
    ),
    (
        "scripted_webkit01.dat",
        '1<script>document.write("2")</script>3',
        None,
        True,
    ): (
        '| <html>\n|   <head>\n|   <body>\n|     "1"\n|     <script>\n|       "document.write("2")"\n|     "3"',
        f"{_WPT_BASE}/scripted_webkit01.dat#L1-L12",
    ),
    (
        "scripted_webkit01.dat",
        (
            "1<script>document.write(\"<script>document.write('2')</scr\"+ \"ipt><script>document.write('3')</scr\" "
            '+ "ipt>")</script>4'
        ),
        None,
        True,
    ): (
        (
            "| <html>\n"
            "|   <head>\n"
            "|   <body>\n"
            '|     "1"\n'
            "|     <script>\n"
            '|       "document.write("<script>document.write(\'2\')</scr"+ '
            '"ipt><script>document.write(\'3\')</scr" + "ipt>")"\n'
            '|     "4"'
        ),
        f"{_WPT_BASE}/scripted_webkit01.dat#L14-L30",
    ),
}

_ERROR_OVERRIDES: Final[dict[_Key, tuple[list[_Error], str]]] = {
    ("comments01.dat", '<?xml version="1.0">Hi', None, None): (
        [{"code": "disallowed-processing-instruction-target", "line": 1, "col": 6, "end_line": None, "end_col": None}],
        f"{_WPT_BASE}/comments01.dat#L155-L195",
    ),
    ("comments01.dat", '<?xml version="1.0">', None, None): (
        [{"code": "disallowed-processing-instruction-target", "line": 1, "col": 6, "end_line": None, "end_col": None}],
        f"{_WPT_BASE}/comments01.dat#L155-L195",
    ),
    ("comments01.dat", "<?xml version", None, None): (
        [{"code": "disallowed-processing-instruction-target", "line": 1, "col": 6, "end_line": None, "end_col": None}],
        f"{_WPT_BASE}/comments01.dat#L155-L195",
    ),
    ("html5test-com.dat", '<?import namespace="foo" implementation="#bar">', None, None): (
        [],
        f"{_WPT_BASE}/html5test-com.dat#L129-L139",
    ),
    ("tests1.dat", "<?", None, None): (
        [{"code": "eof-in-processing-instruction", "line": 1, "col": 3, "end_line": None, "end_col": None}],
        f"{_WPT_BASE}/tests1.dat#L551-L570",
    ),
    ("tests1.dat", "<?#", None, None): (
        [
            {
                "code": "invalid-first-character-of-processing-instruction-target",
                "line": 1,
                "col": 3,
                "end_line": None,
                "end_col": None,
            }
        ],
        f"{_WPT_BASE}/tests1.dat#L551-L570",
    ),
    ("tests1.dat", "<?COMMENT?>", None, None): ([], f"{_WPT_BASE}/tests1.dat#L602-L614"),
    ("tests1.dat", "<?COM--MENT?>", None, None): ([], f"{_WPT_BASE}/tests1.dat#L641-L653"),
}


def generate(checkout: Path, out_path: Path) -> None:
    """Pin WPT data so upstream changes cannot alter CI without a reviewed diff."""
    resources = checkout / "html" / "syntax" / "parsing" / "resources"
    files = sorted(resources.glob("*.dat"))
    if not files:
        msg = f"no HTML tree-construction .dat files found under {resources}"
        raise SystemExit(msg)
    cases = [case for path in files for case in _parse_file(path)]
    revision = _revision(checkout)
    exclusions, error_adjustments = _decisions()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "source": f"https://github.com/web-platform-tests/wpt/tree/{revision}/html/syntax/parsing/resources",
                "revision": revision,
                "files": [path.name for path in files],
                "fixture_counts": {path.name: sum(case["file"] == path.name for case in cases) for path in files},
                "applicable_fixture_counts": {
                    path.name: sum(
                        case["file"] == path.name
                        and (case["file"], case["data"], case["context"], case["scripting"])
                        not in _SCRIPT_EXECUTION_EXCLUSIONS
                        for case in cases
                    )
                    for path in files
                },
                "error_adjustments": error_adjustments,
                "exclusions": exclusions,
                "cases": cases,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out_path}: {len(cases)} cases from {len(files)} files at {revision[:12]}")
    _report(cases)


def _parse_file(path: Path) -> list[_Case]:
    with path.open(encoding="utf-8", newline="") as handle:
        source = handle.read()
    cases: list[_Case] = []
    for block in source.split("#data\n")[1:]:
        before_document, marker, document = block.partition("\n#document\n")
        if not marker:
            msg = f"{path}: #data block has no #document section"
            raise ValueError(msg)
        data, sections = _sections(before_document)
        context = None
        if fragment := sections.get("document-fragment"):
            context = fragment.splitlines()[0].strip()
        scripting = True if "script-on" in sections else False if "script-off" in sections else None
        key = (path.name, data, context, scripting)
        expected = document.rstrip("\n")
        errors = _new_errors(sections.get("new-errors"), path)
        cases.append({
            "file": path.name,
            "data": data,
            "document": expected,
            "context": context,
            "scripting": scripting,
            "errors": errors,
            "spec_errors": _ERROR_OVERRIDES.get(key, (errors, ""))[0],
        })
    return cases


def _sections(text: str) -> tuple[str, dict[str, str]]:
    matches = list(_SECTION.finditer(text))
    if not matches:
        return text, {}
    sections = {
        match[1]: text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else None].removesuffix(
            "\n"
        )
        for index, match in enumerate(matches)
    }
    return text[: matches[0].start()].removesuffix("\n"), sections


def _new_errors(value: str | None, path: Path) -> list[_Error] | None:
    if value is None:
        return None
    errors: list[_Error] = []
    for line in value.splitlines():
        if not line:
            continue
        if (match := _NEW_ERROR.fullmatch(line)) is None:
            msg = f"{path}: invalid #new-errors entry: {line!r}"
            raise ValueError(msg)
        errors.append({
            "code": match[5],
            "line": int(match[1]),
            "col": int(match[2]),
            "end_line": int(match[3]) if match[3] is not None else None,
            "end_col": int(match[4]) if match[4] is not None else None,
        })
    return errors


def _revision(checkout: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _decisions() -> tuple[list[_Exclusion], list[_Decision]]:
    exclusion_reason = "The expected tree requires JavaScript execution; turbohtml has no JavaScript runtime."
    exclusions: list[_Exclusion] = [
        {
            "file": file,
            "data": data,
            "context": context,
            "scripting": scripting,
            "reason": exclusion_reason,
            "spec": _SCRIPT_SPEC,
            "fixture": fixture,
            "document": document,
        }
        for (file, data, context, scripting), (document, fixture) in _SCRIPT_EXECUTION_EXCLUSIONS.items()
    ]
    error_reason = "These fixtures retain parse errors from the bogus-comment rules that preceded HTML instructions."
    error_adjustments: list[_Decision] = [
        {
            "file": file,
            "data": data,
            "context": context,
            "scripting": scripting,
            "reason": error_reason,
            "spec": _PI_SPEC,
            "fixture": fixture,
        }
        for (file, data, context, scripting), (_, fixture) in _ERROR_OVERRIDES.items()
    ]
    return exclusions, error_adjustments


def _report(cases: list[_Case]) -> None:
    applicable = [
        case
        for case in cases
        if (case["file"], case["data"], case["context"], case["scripting"]) not in _SCRIPT_EXECUTION_EXCLUSIONS
    ]
    results = [(case, _build(case)) for case in applicable]
    failures = [(case, result) for case, result in results if result != case["document"]]
    error_cases = [
        (case, expected)
        for case in applicable
        if case["context"] is None and (expected := case["spec_errors"]) is not None
    ]
    error_failures = [
        (case, actual) for case, expected in error_cases if not _errors_match(expected, actual := _errors(case))
    ]
    totals = Counter(case["file"] for case in applicable)
    failure_counts = Counter(case["file"] for case, _ in failures)
    for filename, total in sorted(totals.items()):
        print(f"{filename}: {total - failure_counts[filename]}/{total}")
    count = len(applicable)
    print(f"trees: {count - len(failures)}/{count} ({(count - len(failures)) / count:.2%})")
    print(f"unsupported script-execution cases: {len(_SCRIPT_EXECUTION_EXCLUSIONS)}")
    checked_errors = len(error_cases)
    print(f"exact parse errors: {checked_errors - len(error_failures)}/{checked_errors}")
    messages = [
        *(
            f"{case['file']}: tree for {case['data']!r}\nexpected:\n{case['document']}\ngot:\n{result}"
            for case, result in failures
        ),
        *(
            f"{case['file']}: errors for {case['data']!r}\nexpected: {case['spec_errors']}\ngot: {actual}"
            for case, actual in error_failures
        ),
    ]
    if messages:
        print("\n\n".join(messages))
        msg = f"{len(messages)} applicable WPT cases failed"
        raise SystemExit(msg)


def _build(case: _Case) -> str:
    if (context := case["context"]) is not None:
        result = _html._parse_fragment(  # ruff:ignore[private-member-access]  # native WPT-format dumper
            case["data"], context, bool(case["scripting"])
        )
    else:
        result = _html._parse_tree(  # ruff:ignore[private-member-access]  # native WPT-format dumper
            case["data"], bool(case["scripting"])
        )
    return result.rstrip("\n")


def _errors(case: _Case) -> list[_Error]:
    return [
        {"code": error.code, "line": error.line, "col": error.col + 1, "end_line": None, "end_col": None}
        for error in parse(case["data"], scripting=bool(case["scripting"])).errors
    ]


def _errors_match(expected: list[_Error], actual: list[_Error]) -> bool:
    if len(expected) != len(actual):
        return False
    for wanted, raised in zip(expected, actual, strict=True):
        if wanted["code"] != raised["code"]:
            return False
        position = raised["line"], raised["col"]
        start = wanted["line"], wanted["col"]
        if (end_line := wanted["end_line"]) is None:
            if position != start:
                return False
        elif (end_col := wanted["end_col"]) is None or not start <= position <= (end_line, end_col):
            return False
    return True


__all__ = ["generate"]


if __name__ == "__main__":
    if len(sys.argv) != 3:
        msg = "usage: generate_wpt_tree_corpus.py WPT_CHECKOUT OUTPUT_JSON"
        raise SystemExit(msg)
    generate(Path(sys.argv[1]), Path(sys.argv[2]))
