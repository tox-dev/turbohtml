"""Verify the tree builder against the html5lib tree-construction suite.

The ``.dat`` files under ``tests/html5lib-tests/tree-construction`` give, for
each input, the document tree a conformant parser must build, serialized in the
``| ``-indented "#document" format. This harness parses each ``#data`` block and
compares ``turbohtml``'s serialization against the ``#document`` expectation.
Every case must pass. A ``#script-on`` block asserts the tree a scripting host
builds (``<noscript>`` as raw text); those run through the ``scripting=True``
parse path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from turbohtml import _html

_TREE_DIR = Path(__file__).parents[1] / "html5lib-tests" / "tree-construction"

# CI always checks out the submodule (actions/checkout submodules: true); this guard fires only locally
if not _TREE_DIR.is_dir() or not any(_TREE_DIR.glob("*.dat")):  # pragma: no cover
    msg = "submodule tests/html5lib-tests not checked out; run: git submodule update --init tests/html5lib-tests"
    raise RuntimeError(msg)


def _parse_dat(path: Path) -> list[tuple[str, str, bool, str | None]]:
    """Return (input, expected-document, script-on, fragment-context) per block."""
    cases: list[tuple[str, str, bool, str | None]] = []
    with path.open(encoding="utf-8", newline="") as handle:  # a literal \r in a case is data
        raw_text = handle.read()
    for raw in raw_text.split("\n#data\n"):
        block = raw.removeprefix("#data\n")
        data, _, rest = block.partition("\n#errors")
        document_marker = "\n#document\n"
        if document_marker not in rest:
            continue
        before, _, document = rest.partition(document_marker)
        script_on = "#script-on" in before
        context: str | None = None
        if "#document-fragment\n" in before:
            context = before.partition("#document-fragment\n")[2].splitlines()[0].strip()
        cases.append((data, document.rstrip("\n"), script_on, context))
    return cases


def _iter_cases() -> list[tuple[str, str, str, str | None, bool]]:
    cases: list[tuple[str, str, str, str | None, bool]] = []
    for path in sorted(_TREE_DIR.glob("*.dat")):
        for data, document, script_on, context in _parse_dat(path):
            cases.append((path.name, data, document, context, script_on))
    return cases


_CASES = _iter_cases()


def _build(data: str, context: str | None, *, scripting: bool = False) -> str:
    if context is not None:
        return _html._parse_fragment(data, context, scripting).rstrip("\n")
    return _html._parse_tree(data, scripting).rstrip("\n")


# A few html5lib-tests cases encode the pre-errata "</p> and </br> in a foreign
# namespace" behavior added in html5lib-tests 9b4a29c (2021) and never revised. They
# contradict the WHATWG tree-construction algorithm (§ 13.2.6.5, the "Any other end
# tag" rule for foreign content): nothing is popped, the token is reprocessed in the
# current insertion mode with the foreign element still current, so the implied
# <p>/<br> lands *inside* the foreign root. In a foreign fragment context the root is
# the topmost (and only) stack element, so the end tag returns immediately and is
# ignored. lexbor and html5lib's own library agree with the spec here; the pinned
# .dat does not (and html5lib's library does not pass these cases either). We assert
# the spec-correct trees instead. See https://github.com/tox-dev/turbohtml/issues/32
# and https://github.com/tox-dev/turbohtml/issues/63.
_SPEC_OVERRIDES: dict[tuple[str, str, str | None], str] = {
    ("tests26.dat", "<svg></p><foo>", None): (
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|       <p>\n|       <svg foo>"
    ),
    ("tests26.dat", "<math></p><foo>", None): (
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|       <p>\n|       <math foo>"
    ),
    ("foreign-fragment.dat", "<svg></p><foo>", "div"): "| <svg svg>\n|   <p>\n|   <svg foo>",
    ("foreign-fragment.dat", "</p><foo>", "svg svg"): "| <svg foo>",
    ("tests26.dat", "<svg></br><foo>", None): (
        "| <html>\n|   <head>\n|   <body>\n|     <svg svg>\n|       <br>\n|       <svg foo>"
    ),
    ("tests26.dat", "<math></br><foo>", None): (
        "| <html>\n|   <head>\n|   <body>\n|     <math math>\n|       <br>\n|       <math foo>"
    ),
    ("foreign-fragment.dat", "<svg></br><foo>", "div"): "| <svg svg>\n|   <br>\n|   <svg foo>",
    ("foreign-fragment.dat", "</br><foo>", "svg svg"): "| <svg foo>",
    # The "in select" mode pops an open select for an input/keygen/textarea start tag (and ignores
    # it in a select-context fragment). The pinned .dat predates the keygen rule and still nests it;
    # html5lib's own library pops it (sibling) for a document and ignores it in a select fragment,
    # matching the WHATWG algorithm. See https://github.com/tox-dev/turbohtml/issues/93
    ("tests7.dat", "<select><keygen>", None): "| <html>\n|   <head>\n|   <body>\n|     <select>\n|     <keygen>",
    ("tests_innerHTML_1.dat", "<keygen><option>", "select"): "| <option>",
}


@pytest.mark.parametrize("filename", sorted({name for name, _, _, _, _ in _CASES}))
def test_tree_construction(filename: str) -> None:
    cases = [
        (d, _SPEC_OVERRIDES.get((filename, d, ctx), doc), ctx, script_on)
        for name, d, doc, ctx, script_on in _CASES
        if name == filename
    ]
    assert cases, f"no cases parsed from {filename}"
    failures = [
        f"#data {data!r} (context={context!r}, scripting={script_on})\n"
        f"expected:\n{document}\ngot:\n{_build(data, context, scripting=script_on)}"
        for data, document, context, script_on in cases
        if _build(data, context, scripting=script_on) != document
    ]
    assert not failures, f"{filename}: {len(failures)}/{len(cases)} failing\n\n" + "\n\n".join(failures[:5])
