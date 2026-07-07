"""Validate ``parse_xml`` against the W3C XML Conformance Test Suite.

The suite (the OASIS/NIST/Sun/IBM/James-Clark ``xmlconf`` collection) is vendored
as the pinned ``tests/conformance/xml-conformance-suite`` submodule. Its catalog
files classify each case by ``TYPE``:

* ``not-wf`` -- not well-formed; a conformant processor MUST reject it.
* ``valid`` / ``invalid`` -- well-formed (``invalid`` additionally violates a DTD
  validity constraint, which a non-validating processor need not detect).
* ``error`` -- reporting is optional; the suite lets a processor accept or reject.

``parse_xml`` is a non-validating XML 1.0 well-formedness checker for the document
instance and prolog. It deliberately does not parse the DTD internal subset grammar,
resolve DTD-declared or external entities, validate against a DTD, or implement
XML 1.1. The oracle here is therefore the well-formedness verdict: every in-scope
``not-wf`` case must raise :class:`HTMLParseError`; every in-scope well-formed case
must parse. Cases that exercise a deliberately omitted feature are ``xfail``-ed with
a spec-grounded reason (see ``_DEVIATIONS`` and ``_classify``); the module prints the
pass / xfail breakdown at import so the counts are visible in the test log.

The normal test matrix ``--ignore``\\ s ``tests/conformance``; this suite runs only in the
dedicated ``conformance`` tox env (the "🔬 conformance" CI job), which inits the vendored
oracle submodules first. A missing submodule is a hard error there, not a skip -- an
absent oracle is a misconfiguration, not a reason to pass silently. The module stays in
``[tool.coverage] run.omit`` since it never runs under the coverage matrix.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET  # noqa: S405 -- the catalogs are trusted, pinned vendored fixtures

import pytest

from turbohtml import HTMLParseError, parse_xml

_XMLNS = "{http://www.w3.org/XML/1998/namespace}base"
_DATA = Path(__file__).parent / "xml-conformance-suite" / "packages" / "test-data" / "xmlconf"

# The leaf catalogs the top-level xmlconf.xml pulls in as external-entity includes. Listing
# them directly sidesteps resolving those entities, and the set is stable under the pin.
_CATALOGS = (
    "xmltest/xmltest.xml",
    "japanese/japanese.xml",
    "sun/sun-valid.xml",
    "sun/sun-invalid.xml",
    "sun/sun-not-wf.xml",
    "sun/sun-error.xml",
    "oasis/oasis.xml",
    "ibm/ibm_oasis_invalid.xml",
    "ibm/ibm_oasis_not-wf.xml",
    "ibm/ibm_oasis_valid.xml",
    "ibm/xml-1.1/ibm_invalid.xml",
    "ibm/xml-1.1/ibm_not-wf.xml",
    "ibm/xml-1.1/ibm_valid.xml",
    "eduni/errata-2e/errata2e.xml",
    "eduni/errata-3e/errata3e.xml",
    "eduni/errata-4e/errata4e.xml",
    "eduni/misc/ht-bh.xml",
    "eduni/namespaces/1.0/rmt-ns10.xml",
    "eduni/namespaces/1.1/rmt-ns11.xml",
    "eduni/namespaces/errata-1e/errata1e.xml",
    "eduni/xml-1.1/xml11.xml",
)

_ENCODING = (
    "parse_xml takes already-decoded text; byte-level encoding errors and declared-versus-actual encoding "
    "mismatches belong to the decoding layer, not to well-formedness"
)
_NS_COLON = (
    "parse_xml is namespace-aware, so a ':' is the prefix separator, not an ordinary XML 1.0 Name character; a "
    "name that uses it as one reads as an undeclared prefix, and a processing-instruction target that does reads "
    "as a non-NCName -- both rejected where plain XML 1.0 accepts them"
)
_NS_11 = "namespace prefix undeclaration is a Namespaces 1.1 feature; parse_xml targets XML 1.0"

# Cases that exercise a documented parse_xml limitation rather than a bug. Every entry is a
# deliberate spec deviation argued in the reason; all other in-scope cases are hard assertions.
_DEVIATIONS = {
    "not-wf-sa-170": _ENCODING,
    "rmt-e2e-61": _ENCODING,
    "hst-lhs-007": _ENCODING,
    "hst-lhs-008": _ENCODING,
    "o-p04pass1": _NS_COLON,
    "o-p05pass1": _NS_COLON,
    "x-ibm-1-0.5-valid-P04-ibm04v01.xml": _NS_COLON,
    "x-ibm-1-0.5-valid-P05-ibm05v01.xml": _NS_COLON,
    "x-ibm-1-0.5-valid-P05-ibm05v02.xml": _NS_COLON,
    "x-ibm-1-0.5-valid-P05-ibm05v03.xml": _NS_COLON,
    "valid-sa-012": "the namespace declaration is supplied by a DTD ATTLIST default, which parse_xml does not apply",
    "rmt-ns10-023": _NS_11,
}


def _clean(text: str) -> str:
    """Drop a leading XML/text declaration and any DOCTYPE so a catalog fragment (several
    top-level TEST elements, as the sun files ship) parses once wrapped in a single root."""
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
    start = text.find("<!DOCTYPE")
    if start < 0:
        return text
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == ">" and depth == 0:
            return text[:start] + text[index + 1 :]
    return text


def _catalog_tests(catalog: str) -> list[tuple[dict[str, str], Path]]:
    path = _DATA / catalog
    root = ET.fromstring(f"<WRAP>{_clean(path.read_text(encoding='utf-8', errors='replace'))}</WRAP>")  # noqa: S314
    out: list[tuple[dict[str, str], Path]] = []
    for test in root.iter("TEST"):
        base = path.parent / xml_base if (xml_base := test.get(_XMLNS)) else path.parent
        out.append((test.attrib, (base / test.attrib["URI"]).resolve()))
    return out


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    head = raw[:200].decode("latin-1", "replace")
    match = re.search(r"""encoding\s*=\s*["']([\w.\-]+)["']""", head)
    return raw.decode(match.group(1) if match else "utf-8")


def _is_xml_11(attrib: dict[str, str]) -> bool:
    return attrib.get("VERSION") == "1.1" or (attrib.get("RECOMMENDATION") or "").startswith(("XML1.1", "NS1.1"))


def _deviation_reason(attrib: dict[str, str], kind: str, source: str) -> str | None:
    """The reason a well-formed/not-wf case is out of scope, or None when it is a hard assertion."""
    if (reason := _DEVIATIONS.get(attrib["ID"])) is not None:
        return reason
    if _is_xml_11(attrib):
        return "parse_xml targets XML 1.0; XML 1.1 documents are out of scope"
    if attrib.get("ENTITIES", "none") != "none":
        return "requires DTD-declared or external entity processing, which parse_xml omits"
    if kind != "not-wf" and "<!ENTITY" in source:
        return "references a DTD-declared entity from the internal subset, which parse_xml does not resolve"
    if kind == "not-wf" and "<!DOCTYPE" in source:
        return "the case's non-well-formedness lies in the DTD subset grammar, which parse_xml does not parse"
    return None


def _classify(attrib: dict[str, str], source: str) -> tuple[bool, bool, str | None]:
    """Return (skip, expect_raise, xfail_reason) for a case given its metadata and text."""
    kind = attrib["TYPE"]
    if kind == "error":
        return True, False, None  # the spec leaves reporting optional; assert nothing
    return False, kind == "not-wf", _deviation_reason(attrib, kind, source)


def _load() -> list[object]:
    if not _DATA.is_dir():  # pragma: no cover -- the dedicated conformance job always inits the submodule
        msg = (
            "submodule tests/conformance/xml-conformance-suite not checked out; run: "
            "git submodule update --init tests/conformance/xml-conformance-suite"
        )
        raise RuntimeError(msg)
    params: list[object] = []
    counts = {"assert": 0, "xfail": 0, "skip": 0}
    for catalog in _CATALOGS:
        for attrib, file_path in _catalog_tests(catalog):
            try:
                source = _decode(file_path.read_bytes())
            except (LookupError, UnicodeDecodeError):
                skip, expect_raise, reason = False, attrib["TYPE"] == "not-wf", _ENCODING
                source = ""
            else:
                skip, expect_raise, reason = _classify(attrib, source)
            marks = [pytest.mark.skip(reason="error-type case: reporting is optional")] if skip else []
            if reason is not None:
                marks.append(pytest.mark.xfail(reason=reason, strict=False))
            counts["skip" if skip else "xfail" if reason else "assert"] += 1
            params.append(pytest.param(source, expect_raise, id=attrib["ID"], marks=marks))
    print(  # noqa: T201 -- surface the breakdown in the test log
        f"\nxmlconf: {len(params)} cases -- {counts['assert']} hard assertions, "
        f"{counts['xfail']} xfail (documented deviations), {counts['skip']} skipped (optional)"
    )
    return params


_CASES = _load()


@pytest.mark.parametrize(("source", "expect_raise"), _CASES)
def test_xml_conformance(source: str, *, expect_raise: bool) -> None:
    if expect_raise:
        with pytest.raises(HTMLParseError):
            parse_xml(source)
    else:
        parse_xml(source)  # a well-formed document must parse without a well-formedness error
