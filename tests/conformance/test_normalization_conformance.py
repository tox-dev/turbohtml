"""UAX #15 conformance: turbohtml.normalize against the Unicode Consortium's own NormalizationTest.txt.

Oracle: NormalizationTest.txt for Unicode 16.0.0, the exact version turbohtml's tables pin, taken from the
``unicodetools`` submodule pinned to tag ``final-16.0-20240912`` where the 16.0 release data lives under the ``dev``
directory. Every non-comment row is ``c1;c2;c3;c4;c5; # comment`` whose columns are source, NFC, NFD, NFKC, NFKD as
space-separated hex code points. The file header fixes the invariants asserted here for all ~20000 rows, plus the rule
that every code point absent from Part 1 is left unchanged by all four forms. A missing submodule is a setup error, not
a skip: loading the module raises so a dev running without the checkout is told to init it (CI inits submodules).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from turbohtml.detect import NormalizationForm, is_normalized, normalize

_FORMS: tuple[NormalizationForm, ...] = ("NFC", "NFD", "NFKC", "NFKD")

# Each form maps a canonical column to the input columns that must normalize into it, transcribed from the file header:
#   NFC   c2 == toNFC(c1..c3)         c4 == toNFC(c4..c5)
#   NFD   c3 == toNFD(c1..c3)         c5 == toNFD(c4..c5)
#   NFKC  c4 == toNFKC(c1..c5)
#   NFKD  c5 == toNFKD(c1..c5)
_INVARIANTS: dict[NormalizationForm, tuple[tuple[int, tuple[int, ...]], ...]] = {
    "NFC": ((1, (0, 1, 2)), (3, (3, 4))),
    "NFD": ((2, (0, 1, 2)), (4, (3, 4))),
    "NFKC": ((3, (0, 1, 2, 3, 4)),),
    "NFKD": ((4, (0, 1, 2, 3, 4)),),
}

_ORACLE = Path(__file__).parent / "unicodetools" / "unicodetools" / "data" / "ucd" / "dev" / "NormalizationTest.txt"
if not _ORACLE.exists():  # pragma: no cover
    _HINT = (
        "submodule tests/conformance/unicodetools not checked out; "
        "run: git submodule update --init tests/conformance/unicodetools"
    )
    raise RuntimeError(_HINT)


def _decode(field: str) -> str:
    return "".join(chr(int(code_point, 16)) for code_point in field.split())


def _load() -> tuple[list[tuple[int, int, tuple[str, str, str, str, str]]], frozenset[int]]:
    part = -1
    rows: list[tuple[int, int, tuple[str, str, str, str, str]]] = []
    for lineno, raw in enumerate(_ORACLE.read_text(encoding="utf-8").splitlines(), start=1):
        if raw.startswith("@Part"):
            part = int(raw[5])
        elif payload := raw.split("#", 1)[0].strip():
            columns = tuple(_decode(field) for field in payload.split(";")[:5])
            rows.append((part, lineno, columns))  # ty: ignore[invalid-argument-type]  # split yields exactly 5
    return rows, frozenset(ord(columns[0]) for part, _, columns in rows if part == 1)


_ROWS, _PART1_STARTERS = _load()


@pytest.mark.parametrize("form", _FORMS)
def test_column_invariants_hold_for_every_row(form: NormalizationForm) -> None:
    failures = [
        (lineno, part, ascii(columns[source]))
        for part, lineno, columns in _ROWS
        for target, sources in _INVARIANTS[form]
        for source in sources
        if normalize(form, columns[source]) != columns[target]
    ]
    assert failures == []


@pytest.mark.parametrize("form", _FORMS)
def test_is_normalized_agrees_with_normalize_on_every_column(form: NormalizationForm) -> None:
    inconsistent = [
        (lineno, index)
        for _, lineno, columns in _ROWS
        for index, value in enumerate(columns)
        if is_normalized(form, value) is not (normalize(form, value) == value)
    ]
    assert inconsistent == []


@pytest.mark.parametrize("form", _FORMS)
def test_code_points_absent_from_part1_are_unchanged(form: NormalizationForm) -> None:
    changed = [
        f"U+{cp:04X}"
        for cp in range(0x110000)
        if not 0xD800 <= cp <= 0xDFFF and cp not in _PART1_STARTERS and normalize(form, chr(cp)) != chr(cp)
    ]
    assert changed == []
