from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Final, cast

import turbohtml
from turbohtml import Comment, Doctype, Document, Element, Namespace, Node, ProcessingInstruction, Text

if TYPE_CHECKING:
    from wpt_tree_corpus import WptHtmlTreeCorpus


# The .dat format loses embedded doctype quotes; compare these cases with the native dump (issue #478).
_DAT_UNREPRESENTABLE: Final[frozenset[tuple[str, str, str | None]]] = frozenset({
    ("doctype01.dat", "<!DOCTYPE potato SYSTEM 'taco\"'>Hello", None),
})


def test_public_tree_matches_spec(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    cases: Final = [
        case
        for case in wpt_html_tree_corpus["cases"]
        if case["scripting"] is not True and (case["file"], case["data"], case["context"]) not in _DAT_UNREPRESENTABLE
    ]
    failures: Final = [
        f"{case['file']}: #data {case['data']!r} (context={case['context']!r})\nexpected:\n{expected}\ngot:\n{got}"
        for case in cases
        for expected in [case["document"]]
        for got in [_public_dump(case["data"], case["context"])]
        if got != expected
    ]
    assert not failures, f"{len(failures)}/{len(cases)} public/spec divergences\n\n" + "\n\n".join(failures[:5])


def _public_dump(data: str, context: str | None) -> str:
    root: Final = turbohtml.parse_fragment(data, context) if context is not None else turbohtml.parse(data)
    out: Final[list[str]] = []
    for child in root.children:
        _dump_node(child, 0, out)
    return "\n".join(out)


def _dump_node(node: Node, depth: int, out: list[str]) -> None:
    pad: Final = f"| {'  ' * depth}"
    if isinstance(node, Element):
        _dump_element(node, depth, out)
    elif isinstance(node, Text):
        out.append(f'{pad}"{node.data}"')
    elif isinstance(node, Comment):
        out.append(f"{pad}<!-- {node.data} -->")
    elif isinstance(node, ProcessingInstruction):
        out.append(f"{pad}<?{node.target} {node.data}?>")
    else:
        _dump_doctype(cast("Doctype", node), depth, out)


def _dump_element(node: Element, depth: int, out: list[str]) -> None:
    pad: Final = f"| {'  ' * depth}"
    foreign: Final = node.namespace is not Namespace.HTML
    prefix: Final = f"{node.namespace.value} " if foreign else ""
    out.append(f"{pad}<{prefix}{node.tag}>")
    out.extend(
        sorted(
            f"| {'  ' * (depth + 1)}{_attr_line(name, value or '', foreign=foreign)}"
            for name, value in (node.attrs or {}).items()
        )
    )
    if not foreign and node.tag == "template":
        out.append(f"| {'  ' * (depth + 1)}content")
        for child in node.children[0].children:
            _dump_node(child, depth + 2, out)
        return
    for child in node.children:
        _dump_node(child, depth + 1, out)


def _attr_line(name: str, value: str | list[str], *, foreign: bool) -> str:
    if foreign and name in frozenset({
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
    }):
        name = name.replace(":", " ")
    if isinstance(value, list):
        value = " ".join(value)
    return f'{name}="{value}"'


def _dump_doctype(node: Doctype, depth: int, out: list[str]) -> None:
    pad: Final = f"| {'  ' * depth}"
    name: Final = node.name or ""
    if node.public_id is not None or node.system_id is not None:
        out.append(f'{pad}<!DOCTYPE {name} "{node.public_id or ""}" "{node.system_id or ""}">')
    else:
        out.append(f"{pad}<!DOCTYPE {name}>")


def test_corpus_exercises_sanitizer_relevant_nodes(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    # Keep the corpus sensitive to sanitizer-relevant parser differences.
    unsafe_tags: Final = {
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
    url_attrs: Final = {"href", "src", "action", "formaction", "poster", "cite", "xlink:href", "background"}

    def is_relevant(node: Node) -> bool:
        if not isinstance(node, Element):
            return isinstance(node, Document) and any(is_relevant(child) for child in node.children)
        if node.namespace is not Namespace.HTML or (node.tag in unsafe_tags):
            return True
        attrs: Final = node.attrs or {}
        if any(name.startswith("on") or name in url_attrs for name in attrs):
            return True
        return any(is_relevant(child) for child in node.children)

    cases: Final = [case for case in wpt_html_tree_corpus["cases"] if case["scripting"] is not True]
    relevant: Final = sum(
        is_relevant(parsed)
        for parsed in chain(
            (turbohtml.parse_fragment(case["data"], case["context"]) for case in cases if case["context"] is not None),
            (turbohtml.parse(case["data"]) for case in cases if case["context"] is None),
        )
    )
    assert relevant >= 400
