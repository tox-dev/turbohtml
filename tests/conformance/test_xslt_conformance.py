"""Validate ``turbohtml.transform`` against libxslt's own XSLT 1.0 Recommendation test corpus.

libxslt (the reference C implementation behind ``lxml.etree.XSLT``) ships, under ``tests/REC``
and ``tests/REC2``, the worked examples from the XSLT 1.0 Recommendation as
stylesheet/source/expected-output triples: ``foo.xsl`` transforms ``foo.xml`` into ``foo.out``.
This harness runs turbohtml over every such triple and asserts its output equals libxslt's, so it
proves spec-correctness against an authoritative oracle rather than against turbohtml's own tests.

The corpus is the pinned ``tests/conformance/libxslt`` submodule. It is a pure data checkout with no
optional runtime dependency, so an absent submodule is a setup error, not a reason to skip: collection
raises rather than silently passing. The dedicated ``conformance`` CI job initializes the submodule and
runs this suite; the coverage matrix ignores ``tests/conformance`` (and the file stays in
``[tool.coverage] run.omit``).

Comparison normalizes only insignificant differences per output method: the ``xml`` and ``html``
methods drop the XML declaration and collapse whitespace between tags; ``text`` output is compared
verbatim. libxslt's ``runtest`` passes ``test``/``test2`` string parameters to every case, so this
harness does too.

Cases that exercise XSLT features turbohtml deliberately does not model are ``xfail`` with a
spec-cited reason (see ``_XFAIL``); every other case is a strict correctness assertion.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import turbohtml
from turbohtml.transform import transform

_CORPUS = Path(__file__).parent / "libxslt" / "tests"

if not _CORPUS.exists():  # pragma: no cover
    msg = (
        "submodule tests/conformance/libxslt not checked out; "
        "run: git submodule update --init tests/conformance/libxslt"
    )
    raise RuntimeError(msg)

# Features outside turbohtml's XSLT 1.0 subset, each keyed by "<dir>/<stem>" with the spec reason.
_XFAIL: dict[str, str] = {
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
