from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

from turbohtml import Canonical, parse_xml

_ROOT: Final[Path] = Path(__file__).parent / "libxml2"
_CORPUS: Final[Path] = _ROOT / "test" / "c14n"
_RESULTS: Final[Path] = _ROOT / "result" / "c14n"

if not _CORPUS.is_dir():  # pragma: no cover
    _MSG = (
        "submodule tests/conformance/libxml2 not checked out; "
        "run: git submodule update --init tests/conformance/libxml2"
    )
    raise RuntimeError(_MSG)

_MODES: Final[dict[str, Canonical]] = {
    "without-comments": Canonical(),
    "with-comments": Canonical(with_comments=True),
    "exc-without-comments": Canonical(exclusive=True),
    "1-1-without-comments": Canonical(version="1.1"),
}


def _out_of_scope(case_dir: Path, prefix: str, source: str) -> str | None:
    # The categorized reason this case falls outside turbohtml's infoset c14n, or None if in scope.
    if (case_dir / f"{prefix}.xpath").exists():
        return "xpath-subset: turbohtml canonicalizes a whole document or subtree, not an XPath node-set"
    if re.search(r"xmlns(:[\w.-]+)?\s*=", source):
        return "arbitrary-namespaces: xmlns:* prefixes outside turbohtml's HTML/SVG/MathML/xlink infoset"
    if "<!ENTITY" in source:
        return "dtd-entities: turbohtml does not resolve a DTD-declared general entity"
    if "<!ATTLIST" in source:
        return "dtd-attribute-typing: turbohtml does not apply the DTD attribute typing this case relies on"
    return None


def _cases() -> list:
    cases = []
    for mode_dir, options in _MODES.items():
        result_dir = _RESULTS / mode_dir
        for source in sorted((_CORPUS / mode_dir).glob("*.xml")):
            expected = result_dir / source.stem
            if not expected.exists():
                continue
            reason = _out_of_scope(source.parent, source.stem, source.read_text(encoding="utf-8"))
            marks = (pytest.mark.xfail(reason=reason, strict=False),) if reason else ()
            cases.append(pytest.param(source, expected, options, marks=marks, id=f"{mode_dir}/{source.stem}"))
    return cases


@pytest.mark.parametrize(("source", "expected", "options"), _cases())
def test_canonical_form_matches_libxml2(source: Path, expected: Path, options: Canonical) -> None:
    assert parse_xml(source.read_text(encoding="utf-8")).canonicalize(options) == expected.read_bytes()
