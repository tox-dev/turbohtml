from __future__ import annotations

import re
from pathlib import Path

import pytest

_C_ROOT = Path(__file__).parents[2] / "src" / "turbohtml" / "_c"
_COMPAT_HEADER = _C_ROOT / "core" / "pycompat.h"

# the wrapper each guarded spelling must be reached through, and the pattern finding a direct use
_WRAPPERS = {
    "TH_SEALED": re.compile(r"\bPy_TPFLAGS_DISALLOW_INSTANTIATION\b"),
    "th_copy_characters": re.compile(r"\bPyUnicode_CopyCharacters\s*\("),
    "th_str_format": re.compile(r"\bPyUnicode_FromFormatV?\s*\("),
    # the 4-byte literal is the one safe kind, so it stays a direct call
    "th_str_from_kind": re.compile(r"\bPyUnicode_FromKindAndData\s*\((?!PyUnicode_4BYTE_KIND)"),
}


def _sources() -> list[Path]:
    return sorted(path for path in _C_ROOT.rglob("*") if path.suffix in {".c", ".h"} and path != _COMPAT_HEADER)


def test_the_c_sources_are_discovered() -> None:
    # a broken glob would make every guard below vacuously pass
    assert len(_sources()) > 50


@pytest.mark.parametrize("wrapper", sorted(_WRAPPERS), ids=sorted(_WRAPPERS))
def test_no_direct_use_outside_the_compat_header(wrapper: str) -> None:
    pattern = _WRAPPERS[wrapper]
    offenders = [
        f"{path.relative_to(_C_ROOT)}:{number}"
        for path in _sources()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if pattern.search(line)
    ]
    assert offenders == [], f"reach {wrapper} instead of the wrapped spelling at: {', '.join(offenders)}"


@pytest.mark.parametrize("wrapper", sorted(_WRAPPERS), ids=sorted(_WRAPPERS))
def test_the_wrapper_is_defined_and_used(wrapper: str) -> None:
    # deleting a wrapper together with its call sites would satisfy the guard above on its own
    assert re.search(rf"^#define {wrapper}\b", _COMPAT_HEADER.read_text(encoding="utf-8"), re.MULTILINE)
    assert any(re.search(rf"\b{wrapper}\b", path.read_text(encoding="utf-8")) for path in _sources())
