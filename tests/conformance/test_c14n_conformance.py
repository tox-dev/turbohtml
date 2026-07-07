"""turbohtml's Canonical XML output validated byte-for-byte against libxml2's own c14n corpus.

libxml2's ``test/c14n`` is the de-facto Canonical XML conformance suite -- the W3C c14n/c14n11
example documents and the xmldsig "merlin" interop vectors -- that libxml2 and lxml gate on. Each
case pairs an input ``.xml`` with the byte-exact canonical form libxml2 emits under one algorithm,
under ``result/c14n/<mode>/``.

The corpus is a pinned, shallow submodule (``tests/conformance/libxml2``) that the dedicated
``conformance`` tox env (the "conformance" CI job) checks out recursively and runs; the normal
matrix deselects ``tests/conformance``. A vendored-oracle suite must never silently no-op, so an
absent corpus raises rather than skips. Check it out with
``git submodule update --init tests/conformance/libxml2``.

turbohtml canonicalizes a complete document or subtree of its own infoset, not an arbitrary XPath
node-set, and models the HTML infoset -- HTML carries no namespace, SVG/MathML their default, xlink
its prefix -- rather than a general XML namespace or DTD processor. Cases that need what turbohtml
scopes out are xfailed with a categorized reason:

- ``xpath-subset``: an XPath document-subset filter (the sibling ``.xpath`` file) selects the
  node-set; turbohtml's ``Canonical`` API has no node-set canonicalization.
- ``arbitrary-namespaces``: ``xmlns:*`` declarations on prefixes outside HTML/SVG/MathML/xlink,
  which turbohtml neither propagates onto a subtree apex nor minimizes.
- ``dtd-attribute-typing``: an ID/NMTOKEN-typed attribute (declared by ``ATTLIST``) drives
  non-CDATA attribute-value normalization; turbohtml does not read the internal DTD subset.
- ``dtd-entities``: a reference to a ``ENTITY``-declared general entity turbohtml does not resolve.

9 of the 73 cases live in the HTML infoset -- example-1/2/6 across the 1.0, with-comments and 1.1
modes -- and are asserted byte-exact; exclusive c14n's corpus is entirely node-set cases, so it
contributes none.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from turbohtml import Canonical, parse_xml

_ROOT = Path(__file__).parent / "libxml2"
_CORPUS = _ROOT / "test" / "c14n"
_RESULTS = _ROOT / "result" / "c14n"

if not _CORPUS.is_dir():  # pragma: no cover
    msg = (
        "submodule tests/conformance/libxml2 not checked out; "
        "run: git submodule update --init tests/conformance/libxml2"
    )
    raise RuntimeError(msg)

_MODES = {
    "without-comments": Canonical(),
    "with-comments": Canonical(with_comments=True),
    "exc-without-comments": Canonical(exclusive=True),
    "1-1-without-comments": Canonical(version="1.1"),
}


def _out_of_scope(case_dir: Path, prefix: str, source: str) -> str | None:
    """The categorized reason this case falls outside turbohtml's infoset c14n, or None if in scope."""
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
