"""Differential security oracle: the public tree equals the spec tree a browser builds.

A sanitizer keeps or drops a node by reading its tag, namespace, and attributes off the
*public* ``Element`` API (``.tag``, ``.namespace``, ``.attrs``, ``.children``). If that
public tree ever diverged from the tree a conformant browser builds for the same bytes, the
sanitizer would vet one structure while the browser renders another -- a parser-differential
bypass that no memory sanitizer can see.

``test_treebuilder_conformance`` already pins turbohtml's internal ``#document`` dump against
the html5lib-tests tree-construction corpus (the canonical spec oracle). This suite closes the
remaining gap: it rebuilds the same ``#document`` dump purely from the public Element API and
asserts it matches the spec tree for every non-scripting case, document and fragment. Agreement
over the full corpus -- 570-odd cases carry a sanitizer-relevant element (script, style, foreign
content, event handlers, URL attributes) -- is the evidence that the tree a sanitizer walks is
the tree the browser resolves.

Two divergence classes are documented rather than silently skipped:

- Spec-lag: a handful of pinned ``.dat`` cases encode pre-errata trees for ``</p>``/``</br>`` in
  foreign content and ``<select><keygen>``. turbohtml follows the modern WHATWG algorithm (as do
  lexbor and html5lib's own library); the pinned data does not. For these the public tree is
  asserted against turbohtml's spec-validated parse, which the conformance suite pins to the
  corrected tree. See issues #32, #63, #93.
- A ``.dat`` text-format limit: the html5lib ``#document`` dump wraps doctype identifiers in unescaped
  quotes, so it cannot represent a quote embedded in one (``taco"`` reads back as ``taco``). turbohtml
  keeps the quote, matching html5lib-python and the WHATWG tokenizer, so the public tree is asserted
  against turbohtml's conformance-validated parse. See issue #478.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import turbohtml
from turbohtml import Comment, Doctype, Document, Element, Namespace, Node, Text, _html

_TREE_DIR = Path(__file__).parents[1] / "html5lib-tests" / "tree-construction"

# "adjust foreign attributes" (WHATWG 13.2.6.5): only these prefixed names on an SVG/MathML
# element serialize with a namespace-separating space; any other xml:/xlink: name keeps its colon.
# Bare ``xmlns`` is in the spec table but never appears on a foreign element in the pinned corpus.
_NAMESPACED_ATTRS = frozenset({
    "xlink:actuate",
    "xlink:arcrole",
    "xlink:href",
    "xlink:role",
    "xlink:show",
    "xlink:title",
    "xlink:type",
    "xml:lang",
    "xml:space",
    "xmlns:xlink",
})


def _attr_line(name: str, value: str | list[str], *, foreign: bool) -> str:
    if foreign and name in _NAMESPACED_ATTRS:
        name = name.replace(":", " ")
    if isinstance(value, list):  # the public API tokenizes class/rel into a list
        value = " ".join(value)
    return f'{name}="{value}"'


def _dump_node(node: Node, depth: int, out: list[str]) -> None:
    pad = "| " + "  " * depth
    if isinstance(node, Element):
        foreign = node.namespace is not Namespace.HTML
        prefix = f"{node.namespace.value} " if foreign else ""
        out.append(f"{pad}<{prefix}{node.tag}>")
        out.extend(
            sorted(
                "| " + "  " * (depth + 1) + _attr_line(name, value or "", foreign=foreign)
                for name, value in (node.attrs or {}).items()
            )
        )
        if not foreign and node.tag == "template":  # children hang off a "content" pseudo-node
            out.append("| " + "  " * (depth + 1) + "content")
            for child in node.children[0].children:
                _dump_node(child, depth + 2, out)
            return
        for child in node.children:
            _dump_node(child, depth + 1, out)
    elif isinstance(node, Text):
        out.append(f'{pad}"{node.data}"')
    elif isinstance(node, Comment):
        out.append(f"{pad}<!-- {node.data} -->")
    else:
        doctype = cast("Doctype", node)  # the corpus dumps only element/text/comment/doctype nodes
        name = doctype.name or ""
        if doctype.public_id is not None or doctype.system_id is not None:
            out.append(f'{pad}<!DOCTYPE {name} "{doctype.public_id or ""}" "{doctype.system_id or ""}">')
        else:
            out.append(f"{pad}<!DOCTYPE {name}>")


def _public_dump(data: str, context: str | None) -> str:
    root = turbohtml.parse_fragment(data, context) if context is not None else turbohtml.parse(data)
    out: list[str] = []
    for child in root.children:
        _dump_node(child, 0, out)
    return "\n".join(out)


def _internal_dump(data: str, context: str | None) -> str:
    if context is not None:
        return _html._parse_fragment(data, context).rstrip("\n")
    return _html._parse_tree(data).rstrip("\n")


def _parse_dat(path: Path) -> list[tuple[str, str, str | None]]:
    cases: list[tuple[str, str, str | None]] = []
    with path.open(encoding="utf-8", newline="") as handle:  # a literal \r in a case is data
        raw_text = handle.read()
    for raw in raw_text.split("\n#data\n"):
        block = raw.removeprefix("#data\n")
        data, _, rest = block.partition("\n#errors")
        if "\n#document\n" not in rest:
            continue
        before, _, document = rest.partition("\n#document\n")
        if "#script-on" in before:  # scripting-enabled cases need a script-executing host
            continue
        context = (
            before.partition("#document-fragment\n")[2].splitlines()[0].strip()
            if "#document-fragment\n" in before
            else None
        )
        cases.append((data, document.rstrip("\n"), context))
    return cases


_CASES = [(path.name, *case) for path in sorted(_TREE_DIR.glob("*.dat")) for case in _parse_dat(path)]

# The pinned .dat predates modern-spec errata for these cases; turbohtml is spec-correct (lexbor and
# html5lib's own library agree). The public tree is checked against turbohtml's conformance-validated
# parse, which test_treebuilder_conformance pins to the corrected tree. See issues #32, #63, #93.
_SPEC_LAG: frozenset[tuple[str, str, str | None]] = frozenset({
    ("tests26.dat", "<svg></p><foo>", None),
    ("tests26.dat", "<math></p><foo>", None),
    ("tests26.dat", "<svg></br><foo>", None),
    ("tests26.dat", "<math></br><foo>", None),
    ("foreign-fragment.dat", "<svg></p><foo>", "div"),
    ("foreign-fragment.dat", "</p><foo>", "svg svg"),
    ("foreign-fragment.dat", "<svg></br><foo>", "div"),
    ("foreign-fragment.dat", "</br><foo>", "svg svg"),
    ("tests7.dat", "<select><keygen>", None),
    ("tests_innerHTML_1.dat", "<keygen><option>", "select"),
})

# The html5lib `#document` text format wraps each doctype identifier in unescaped quotes, so it cannot
# represent a quote embedded in one: `taco"` serializes to a .dat that reads back as `taco`. turbohtml
# keeps the embedded quote (matching html5lib-python and the WHATWG tokenizer), so the public tree is
# checked against turbohtml's conformance-validated parse, not the lossy .dat text. See issue #478.
_DAT_UNREPRESENTABLE: frozenset[tuple[str, str, str | None]] = frozenset({
    ("doctype01.dat", "<!DOCTYPE potato SYSTEM 'taco\"'>Hello", None),
})


def _expected(filename: str, data: str, document: str, context: str | None) -> str:
    key = (filename, data, context)
    if key in _SPEC_LAG or key in _DAT_UNREPRESENTABLE:
        return _internal_dump(data, context)
    return document


@pytest.mark.parametrize("filename", sorted({name for name, _, _, _ in _CASES}))
def test_public_tree_matches_spec(filename: str) -> None:
    cases = [(data, document, context) for name, data, document, context in _CASES if name == filename]
    assert cases, f"no cases parsed from {filename}"
    failures = [
        f"#data {data!r} (context={context!r})\nexpected:\n{expected}\ngot:\n{got}"
        for data, document, context in cases
        for expected in [_expected(filename, data, document, context)]
        for got in [_public_dump(data, context)]
        if got != expected
    ]
    assert not failures, f"{filename}: {len(failures)}/{len(cases)} public/spec divergences\n\n" + "\n\n".join(
        failures[:5]
    )


def test_corpus_exercises_sanitizer_relevant_nodes() -> None:
    # a corpus edit that stops exercising the security surface (foreign content, script/style, handlers,
    # URL attributes) must fail loudly rather than let the oracle pass vacuously
    unsafe_tags = {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "noscript",
        "noembed",
        "noframes",
        "base",
        "title",
        "template",
        "xmp",
        "plaintext",
    }
    url_attrs = {"href", "src", "action", "formaction", "poster", "cite", "xlink:href", "background"}

    def is_relevant(node: object) -> bool:
        if not isinstance(node, Element):
            return isinstance(node, Document) and any(is_relevant(child) for child in node.children)
        if node.namespace is not Namespace.HTML or (node.tag in unsafe_tags):
            return True
        attrs = node.attrs or {}
        if any(name.startswith("on") or name in url_attrs for name in attrs):
            return True
        return any(is_relevant(child) for child in node.children)

    relevant = sum(
        1
        for _, data, _, context in _CASES
        if is_relevant(turbohtml.parse_fragment(data, context) if context is not None else turbohtml.parse(data))
    )
    assert relevant >= 400
