from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest

import turbohtml
from turbohtml.transform import transform

_CORPUS = Path(__file__).parent / "libxslt" / "tests"

if not _CORPUS.exists():  # pragma: no cover
    _MSG = (
        "submodule tests/conformance/libxslt not checked out; "
        "run: git submodule update --init tests/conformance/libxslt"
    )
    raise RuntimeError(_MSG)

# Features outside turbohtml's XSLT 1.0 subset, each keyed by "<dir>/<stem>" with the spec reason.
_XFAIL: Final[dict[str, str]] = {
    "REC/test-10-3": "not yet supported: locale-aware xsl:sort collation (lang='de') needs an ICU/locale layer "
    "turbohtml does not carry; the default is Unicode-codepoint collation",
    "REC/test-12.4-1": "not yet supported: generate-id over the namespace:: axis needs the XPath namespace axis",
    "REC/test-5.2-10": "not yet supported: id() over DTD-declared ID attributes needs a DTD layer the parser "
    "does not have",
}


def _iter_cases() -> list[tuple[str, Path]]:
    return [
        (f"{group}/{xsl.stem}", xsl)
        for group in ("REC", "REC2")
        for xsl in sorted((_CORPUS / group).glob("*.xsl"))
        if xsl.with_suffix(".xml").exists() and xsl.with_suffix(".out").exists()
    ]


_CASES = _iter_cases()


def _output_method(stylesheet: str) -> str:
    match = re.search(r"<xsl:output[^>]*\bmethod\s*=\s*[\"'](\w+)", stylesheet)
    return match.group(1) if match else "xml"


def _canonical(text: str) -> str:
    text = re.sub(r"^﻿?<\?xml\s[^>]*\?>\s*", "", text)
    return re.sub(r">\s+<", "><", text.strip())


@pytest.mark.parametrize(("case_id", "xsl"), [pytest.param(cid, path, id=cid) for cid, path in _CASES])
def test_xslt_conformance(case_id: str, xsl: Path, request: pytest.FixtureRequest) -> None:
    if case_id in _XFAIL:
        request.node.add_marker(pytest.mark.xfail(reason=_XFAIL[case_id], strict=True))
    stylesheet = xsl.read_text(encoding="utf-8", errors="replace")
    source = xsl.with_suffix(".xml").read_text(encoding="utf-8", errors="replace")
    expected = xsl.with_suffix(".out").read_text(encoding="utf-8", errors="replace")
    result = transform(
        turbohtml.parse_xml(stylesheet),
        turbohtml.parse_xml(source),
        base_url=str(xsl),
        test="'passed_value'",
        test2="'passed_value2'",
    )
    if _output_method(stylesheet) == "text":
        assert result == expected
    else:
        assert _canonical(result) == _canonical(expected)
