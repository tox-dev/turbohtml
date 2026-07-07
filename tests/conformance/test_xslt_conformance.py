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
    "REC/stand-2.7-1": "xsl:strip-space/preserve-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-3.4-1": "xsl:strip-space/preserve-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-3.4-2": "xsl:strip-space/preserve-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-3.4-3": "xsl:strip-space/preserve-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-9.1-1": "xsl:strip-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-9.1-2": "xsl:strip-space whitespace stripping (section 3.4) is not modeled",
    "REC/test-2.3-1": "simplified (literal-result-element) stylesheets (section 2.3) are not modeled",
    "REC/test-2.6.2-1": "xsl:import of external stylesheets (section 2.6.2) is not modeled",
    "REC/test-7.1.1": "xsl:namespace-alias (section 7.1.1) is not modeled",
    "REC/test-7.1.3": "xsl:namespace-alias (section 7.1.1) is not modeled",
    "REC/test-7.1.4": "use-attribute-sets / xsl:attribute-set (section 7.1.4) is not modeled",
    "REC/test-7.7-3": "multi-level xsl:number (level='multiple', count/from) is not modeled",
    "REC/test-7.7-5": "multi-level xsl:number (level='multiple', count/from) is not modeled",
    "REC/test-7.7-6": "xsl:number grouping-separator/grouping-size and lang are not modeled",
    "REC/test-10-3": "locale-aware xsl:sort collation (lang='de') is not modeled",
    "REC/test-12.4-1": "generate-id over the namespace:: axis is not modeled",
    "REC/test-5.2-10": "id() patterns need DTD-declared ID attributes, which the parser does not track",
    "REC/test-15-1": "extension elements (extension-element-prefixes) and xsl:fallback are not modeled",
    "REC/test-16.1-1": "the cdata-section-elements output option (section 16.1) is not modeled",
    "REC/test-16.1-2": "the cdata-section-elements output option (section 16.1) is not modeled",
    "REC/test-2.5-1": "html output method auto-selection and meta content-type injection are not modeled",
    "REC/test-8-1": "html output method auto-selection and meta content-type injection are not modeled",
    "REC2/html": "html output method auto-selection and meta content-type injection are not modeled",
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
        test="'passed_value'",
        test2="'passed_value2'",
    )
    if _output_method(stylesheet) == "text":
        assert result == expected
    else:
        assert _canonical(result) == _canonical(expected)
