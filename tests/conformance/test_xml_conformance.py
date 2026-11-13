from __future__ import annotations

import re
from contextlib import nullcontext
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple
from xml.etree import (  # ruff:ignore[suspicious-xml-etree-import]  # the catalogs are trusted, pinned vendored fixtures
    ElementTree as ET,
)

import pytest

from turbohtml import HTMLParseError, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

_DATA: Final[Path] = Path(__file__).parent / "xml-conformance-suite" / "packages" / "test-data" / "xmlconf"

# The leaf catalogs the top-level xmlconf.xml pulls in as external-entity includes. Listing
# them directly sidesteps resolving those entities, and the set is stable under the pin.
_CATALOGS: Final[tuple[str, ...]] = (
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

_ENCODING: Final[str] = (
    "parse_xml takes already-decoded text; byte-level encoding errors and declared-versus-actual encoding "
    "mismatches belong to the decoding layer, not to well-formedness"
)
_NS_COLON: Final[str] = (
    "parse_xml is namespace-aware, so a ':' is the prefix separator, not an ordinary XML 1.0 Name character; a "
    "name that uses it as one reads as an undeclared prefix, and a processing-instruction target that does reads "
    "as a non-NCName -- both rejected where plain XML 1.0 accepts them"
)

# Cases that exercise a documented parse_xml limitation rather than a bug. Every entry is a
# deliberate spec deviation argued in the reason; all other in-scope cases are hard assertions.
_DEVIATIONS: Final[dict[str, str]] = {
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
}


_TEXT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*<\?xml[^>]*\?>")


def _clean(text: str) -> str:
    # Drop a leading XML/text declaration and any DOCTYPE so a catalog fragment (several
    # top-level TEST elements, as the sun files ship) parses once wrapped in a single root.
    text = _TEXT_PATTERN.sub("", text, count=1)
    start = text.find("<!DOCTYPE")
    if start < 0:
        return text
    depth = 0
    for index in range(start, len(text)):
        if (char := text[index]) == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == ">" and depth == 0:
            return text[:start] + text[index + 1 :]
    return text


def _catalog_tests(catalog: str) -> list[tuple[dict[str, str], Path]]:
    path = _DATA / catalog
    root = ET.fromstring(f"<WRAP>{_clean(path.read_text(encoding='utf-8', errors='replace'))}</WRAP>")  # ruff:ignore[suspicious-xml-element-tree-usage]  # parses fixed markup from the test corpus, not untrusted input
    out: list[tuple[dict[str, str], Path]] = []
    xmlns: str = "{http://www.w3.org/XML/1998/namespace}base"
    for test in root.iter("TEST"):
        base = path.parent / xml_base if (xml_base := test.get(xmlns)) else path.parent
        out.append((test.attrib, (base / test.attrib["URI"]).resolve()))
    return out


_MATCH_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"""(?#XML declaration keyword)encoding(?#equals sign)\s*=\s*(?#opening quote)["']"""
    r"""(?P<encoding>[\w.\-]+)(?#closing quote)["']"""
)


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    match = _MATCH_PATTERN.search(raw[:200].decode("latin-1", "replace"))
    return raw.decode(match["encoding"] if match else "utf-8")


def _deviation_reason(attrib: dict[str, str], kind: str, source: str) -> str | None:
    # The reason a well-formed/not-wf case is out of scope, or None when it is a hard assertion.
    if (reason := _DEVIATIONS.get(attrib["ID"])) is not None:
        return reason
    if attrib.get("VERSION") == "1.1" or (attrib.get("RECOMMENDATION") or "").startswith(("XML1.1", "NS1.1")):
        return "parse_xml targets XML 1.0; XML 1.1 documents are out of scope"
    if attrib.get("ENTITIES", "none") != "none":
        return "requires DTD-declared or external entity processing, which parse_xml omits"
    if kind != "not-wf" and "<!ENTITY" in source:
        return "references a DTD-declared entity from the internal subset, which parse_xml does not resolve"
    if kind == "not-wf" and "<!DOCTYPE" in source:
        return "the case's non-well-formedness lies in the DTD subset grammar, which parse_xml does not parse"
    return None


class _Classification(NamedTuple):
    skip: bool
    expect_raise: bool
    xfail_reason: str | None


def _classify(attrib: dict[str, str], source: str) -> _Classification:
    # Return (skip, expect_raise, xfail_reason) for a case given its metadata and text.
    kind = attrib["TYPE"]
    if kind == "error":
        return _Classification(
            skip=True, expect_raise=False, xfail_reason=None
        )  # the spec leaves reporting optional; assert nothing
    return _Classification(
        skip=False, expect_raise=kind == "not-wf", xfail_reason=_deviation_reason(attrib, kind, source)
    )


def _case(attrib: dict[str, str], file_path: Path) -> tuple[pytest.ParameterSet, str]:
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
    outcome = partial(pytest.raises, HTMLParseError) if expect_raise else nullcontext
    kind = "skip" if skip else "xfail" if reason else "assert"
    return pytest.param(source, outcome, id=attrib["ID"], marks=marks), kind


def _load() -> list[pytest.ParameterSet]:
    if not _DATA.is_dir():  # pragma: no cover -- the dedicated conformance job always inits the submodule
        msg = (
            "submodule tests/conformance/xml-conformance-suite not checked out; run: "
            "git submodule update --init tests/conformance/xml-conformance-suite"
        )
        raise RuntimeError(msg)
    params: list[pytest.ParameterSet] = []
    counts = {"assert": 0, "xfail": 0, "skip": 0}
    for catalog in _CATALOGS:
        for attrib, file_path in _catalog_tests(catalog):
            param, kind = _case(attrib, file_path)
            params.append(param)
            counts[kind] += 1
    print(  # ruff:ignore[print] -- surface the breakdown in the test log
        f"\nxmlconf: {len(params)} cases -- {counts['assert']} hard assertions, "
        f"{counts['xfail']} xfail (documented deviations), {counts['skip']} skipped (optional)"
    )
    return params


_CASES: Final[list[pytest.ParameterSet]] = _load()


@pytest.mark.parametrize(("source", "outcome"), _CASES)
def test_xml_conformance(
    source: str, outcome: Callable[[], AbstractContextManager[pytest.ExceptionInfo[HTMLParseError] | None]]
) -> None:
    with outcome():
        parse_xml(source)  # a well-formed document must parse without a well-formedness error
