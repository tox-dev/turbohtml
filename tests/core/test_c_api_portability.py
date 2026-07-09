"""Guard the C-API spellings whose meaning differs between CPython and PyPy's cpyext.

``core/pycompat.h`` wraps each of them behind a ``th_`` or ``TH_`` name that is an identity macro on
CPython and an adaptation on PyPy. The wrappers only help where they are used, and reaching for the
wrapped spelling directly compiles and passes every CPython test while being silently wrong on PyPy:
a dropped BOM, a byte-swapped string, a one-past length, or a type that constructs without a tree and
segfaults on first use. Nothing in the compiler catches that, so pin it here. The C sources may name
each wrapped spelling only inside ``pycompat.h`` itself, and must reach it through the wrapper
everywhere else.

``PyUnicode_FromKindAndData`` is exempt when its kind argument is the ``PyUnicode_4BYTE_KIND``
literal: cpyext's 4-byte path is exact, and the wrapper exists only to widen a 2-byte buffer.

Upstream: https://github.com/pypy/pypy/issues/5524 and https://github.com/pypy/pypy/issues/5525.
"""

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
