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


_DOCUMENT_OVERRIDES: dict[tuple[str, str, str | None], str] = {
    # WHATWG added HTML processing instructions after the pinned html5lib-tests revision.
    (
        "html5test-com.dat",
        '<?import namespace="foo" implementation="#bar">',
        None,
    ): ('| <?import namespace="foo" implementation="#bar"?>\n| <html>\n|   <head>\n|   <body>'),
    ("tests1.dat", "<?", None): "| <html>\n|   <head>\n|   <body>",
    ("tests1.dat", "<?COMMENT?>", None): "| <?COMMENT ?>\n| <html>\n|   <head>\n|   <body>",
    ("tests1.dat", "<?COM--MENT?>", None): "| <?COM--MENT ?>\n| <html>\n|   <head>\n|   <body>",
}


@pytest.mark.parametrize("filename", sorted({name for name, _, _, _, _ in _CASES}))
def test_tree_construction(filename: str) -> None:
    cases = [
        (d, _DOCUMENT_OVERRIDES.get((filename, d, ctx), doc), ctx, script_on)
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
