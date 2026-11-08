"""XML lexical choices consume expansion steps without inventing DOM nodes."""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    Grammar,
    Identifier,
    Production,
    ProductionFloorError,
    Reference,
    compile_grammar,
    generate,
    generation_sweep,
    write_corpus,
)

from turbohtml import CData, Comment, Doctype, Element, Html, ProcessingInstruction, Text, parse_xml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from turbohtml import Document, Node

XmlRecord = tuple[str, str, str, tuple[tuple[str, str], ...], int]


def main(argv: Sequence[str] | None = None) -> int:
    # Keep corpus files independent of parser acceptance.
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=30)
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--byte-budget", type=int, default=64, help="maximum random raw input length")
    args: Final = parser.parse_args(argv)
    if args.count < 0:
        parser.error("count must be nonnegative")
    try:
        cases: Final = (
            tuple(xml_raw_generate(random.Random(args.seed + index), args.byte_budget) for index in range(args.count))
            if args.raw and args.count
            else xml_raw_inputs()
            if args.raw
            else tuple(
                xml_generate(random.Random(args.seed + index), args.budget, steps=args.steps)
                for index in range(args.count)
            )
            if args.count
            else generation_sweep(_GRAMMAR, budget=GenerationBudget(nodes=args.budget, steps=args.steps))
        )
    except (BudgetError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    return 0


def xml_generate(rng: random.Random, budget: int = 30, *, steps: int = 120) -> Generated:
    # Keep well-formed XML and raw-byte rejection seeds in distinct profiles.
    return generate(_GRAMMAR, rng, GenerationBudget(nodes=budget, steps=steps))


def xml_grammar() -> Grammar:
    # Retain production provenance for corpus audits.
    return _GRAMMAR


def xml_structure_check(
    case: Generated,
    *,
    read: Callable[[str], Document] = parse_xml,
    serialize: Callable[[Node], str] | None = None,
) -> str | None:
    # Require literal names, content, parents and bindings before comparing serialization.
    expected: Final = xml_expected(case)
    root: Final = read(case.data.decode("utf-8"))
    if xml_snapshot(root) != expected:
        return "parsed XML differs from production literals"
    printed: Final = root.serialize(Html(xml=True)) if serialize is None else serialize(root)
    if xml_snapshot(read(printed)) != expected:
        return "serialized XML differs from production literals"
    return None


def xml_expected(case: Generated) -> tuple[XmlRecord, ...]:
    # Use literal production semantics independently of either parse.
    records: Final[list[XmlRecord]] = []
    _expect(iter(case.productions), -1, records)
    return tuple(records)


def _expect(trace: Iterator[str], parent: int, records: list[XmlRecord]) -> None:
    positions: Final[dict[str, int]] = {}
    for item in _RULES[next(trace)].expected:
        owner: Final = parent if not item.parent else positions[item.parent]
        if isinstance(item, _Child):
            _expect(trace, owner, records)
        else:
            positions[item.label] = len(records)
            records.append((item.kind, item.name, item.data, item.attrs, owner))


def xml_snapshot(root: Document) -> tuple[XmlRecord, ...]:
    # Keep parent ordinals stable when wrapper identities differ.
    nodes: Final = [root, *root.descendants]
    records: Final[list[XmlRecord]] = []
    for node in nodes:
        parent: Final = -1 if node.parent is None else nodes.index(node.parent)
        if isinstance(node, Element):
            records.append((
                f"element:{node.namespace.value}",
                node.tag,
                "",
                tuple(sorted((name, str(value)) for name, value in node.attrs.items())),
                parent,
            ))
        elif isinstance(node, Doctype):
            records.append((
                "doctype",
                node.name,
                "",
                tuple(
                    (name, value)
                    for name, value in (("public", node.public_id), ("system", node.system_id))
                    if value is not None
                ),
                parent,
            ))
        elif isinstance(node, ProcessingInstruction):
            records.append(("pi", node.target, node.data, (), parent))
        elif isinstance(node, (Text, Comment, CData)):
            records.append((type(node).__name__.lower(), "", node.data, (), parent))
        else:
            records.append(("document", "", "", (), parent))
    return tuple(records)


def xml_source_generate(rng: random.Random) -> str:
    # Use XML documents so grammar cases reach the XML parser.
    return xml_generate(rng).data.decode("utf-8")


def xml_source_seeds() -> list[str]:
    # Force production coverage so random choices cannot leave alternatives untested.
    return [
        case.data.decode("utf-8") for case in generation_sweep(_GRAMMAR, budget=GenerationBudget(nodes=30, steps=120))
    ]


def xml_source_controls() -> dict[str, bool]:
    # Make oracle no-ops fail on data loss and reparenting.
    return {
        "dropped attribute": xml_document_check(
            '<Root><Leaf key="value"/></Root>',
            lambda node: node.serialize(Html(xml=True)).replace(' key="value"', ""),
        )
        is not None,
        "changed parent": xml_document_check(
            "<Root><Group><Leaf/></Group></Root>",
            lambda _node: "<Root><Group/><Leaf/></Root>",
        )
        is not None,
    }


def xml_document_check(markup: str, serialize: Callable[[Node], str] | None = None) -> str | None:
    # Compare XML input semantics rather than reparsing an HTML interpretation.
    root: Final = parse_xml(markup)
    expected: Final = xml_snapshot(root)
    printed: Final = root.serialize(Html(xml=True)) if serialize is None else serialize(root)
    return None if xml_snapshot(parse_xml(printed)) == expected else "XML serialization changes the document"


def xml_raw_generate(rng: random.Random, budget: int) -> Generated:
    # Bound raw input length without claiming a DOM node cost.
    if budget < 0:
        msg = "byte budget must be nonnegative"
        raise BudgetError(msg)
    return Generated(
        data=rng.randbytes(rng.randint(0, budget)), nodes=0, depth=0, productions=("xml:raw:bytes",), bindings=()
    )


def xml_raw_inputs() -> tuple[Generated, ...]:
    # Preserve invalid UTF-8 and XML delimiters as bounded byte inputs.
    return tuple(
        Generated(data=source, nodes=0, depth=0, productions=(f"xml:raw:{index}",), bindings=())
        for index, source in enumerate(_RAW)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Literal:
    kind: str
    name: str = ""
    data: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    parent: str = ""
    label: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Child:
    parent: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class _Rule:
    production: Production
    expected: tuple[_Literal | _Child, ...]


def _rules() -> dict[str, _Rule]:
    standard = "https://www.w3.org/TR/2008/REC-xml-20081126/"
    rules: Final = [
        _Rule(
            production=Production(
                name="xml:document",
                symbol="document",
                parts=(
                    Reference(symbol="declaration", depth=0),
                    Reference(symbol="misc", depth=1),
                    Reference(symbol="doctype", depth=1),
                    b'<Root id="',
                    Identifier(name="target", definition=True),
                    b'">',
                    Reference(symbol="node", depth=2),
                    b'<Ref target="#',
                    Identifier(name="target", definition=False),
                    b'"/></Root>',
                    Reference(symbol="misc", depth=1),
                ),
                nodes=3,
                origin=standard,
                height=2,
            ),
            expected=(
                _Literal(kind="document", label="document"),
                _Child(parent="document"),
                _Child(parent="document"),
                _Child(parent="document"),
                _Literal(kind="element:html", name="Root", attrs=(("id", "x"),), parent="document", label="root"),
                _Child(parent="root"),
                _Literal(kind="element:html", name="Ref", attrs=(("target", "#x"),), parent="root"),
                _Child(parent="document"),
            ),
        ),
        _Rule(
            production=Production(
                name="xml:nested",
                symbol="node",
                parts=(b"<Group>", Reference(symbol="node"), Reference(symbol="node"), b"</Group>"),
                nodes=1,
                origin=standard,
            ),
            expected=(
                _Literal(kind="element:html", name="Group", label="group"),
                _Child(parent="group"),
                _Child(parent="group"),
            ),
        ),
    ]
    declarations = (
        b"",
        b'<?xml version="1.0"?>',
        b"<?xml version='1.0'?>",
        b'<?xml version="1.0" encoding="UTF-8"?>',
        b"<?xml version='1.0' encoding='UTF-8'?>",
        b'<?xml version="1.0" standalone="yes"?>',
        b'<?xml version="1.0" standalone="no"?>',
    )
    for index, declaration in enumerate(declarations):
        rules.append(
            _Rule(
                production=Production(
                    name=f"xml:declaration:{index}",
                    symbol="declaration",
                    parts=(declaration,),
                    nodes=0,
                    origin=standard,
                ),
                expected=(),
            )
        )
    for index, (source, expected) in enumerate(_MISC):
        rules.append(
            _Rule(
                production=Production(
                    name=f"xml:misc:{index}", symbol="misc", parts=(source,), nodes=len(expected), origin=standard
                ),
                expected=expected,
            )
        )
    competitor: str = "https://github.com/GNOME/libxml2/blob/c43dc98d27ac315a48d93dbd399c6c22cf7125b1/fuzz/xml.dict"
    doctypes = (
        (b"", ()),
        (b"<!DOCTYPE Root>", ()),
        (b'<!DOCTYPE Root SYSTEM "root.dtd">', (("system", "root.dtd"),)),
        (b'<!DOCTYPE Root PUBLIC "public-id" "root.dtd">', (("public", "public-id"), ("system", "root.dtd"))),
        (b"<!DOCTYPE Root [<!ELEMENT Root ANY>]>", ()),
    )
    for index, (source, attrs) in enumerate(doctypes):
        rules.append(
            _Rule(
                production=Production(
                    name=f"xml:doctype:{index}",
                    symbol="doctype",
                    parts=(source,),
                    nodes=int(bool(source)),
                    origin=competitor,
                ),
                expected=(_Literal(kind="doctype", name="Root", attrs=attrs),) if source else (),
            )
        )
    for index, (source, expected) in enumerate(_LEAVES):
        rules.append(
            _Rule(
                production=Production(
                    name=f"xml:leaf:{index}",
                    symbol="node",
                    parts=(source,),
                    nodes=len(expected),
                    origin=standard,
                    height=int(len(expected) > 1),
                ),
                expected=expected,
            )
        )
    return {rule.production.name: rule for rule in rules}


_MISC: Final[
    tuple[
        tuple[bytes, tuple[()]],
        tuple[bytes, tuple[()]],
        tuple[bytes, tuple[_Literal]],
        tuple[bytes, tuple[_Literal]],
        tuple[bytes, tuple[_Literal, _Literal]],
    ]
] = (
    (b"", ()),
    (b" \t\r\n", ()),
    (b"<!--before-->", (_Literal(kind="comment", data="before"),)),
    (b"<?probe data?>", (_Literal(kind="pi", name="probe", data="data"),)),
    (
        b"<!--before--><?probe data?>",
        (_Literal(kind="comment", data="before"), _Literal(kind="pi", name="probe", data="data")),
    ),
)
_LEAVES: Final[tuple[tuple[bytes, tuple[_Literal, ...]], ...]] = (
    (b"<Leaf/>", (_Literal(kind="element:html", name="Leaf"),)),
    (b"<Leaf></Leaf>", (_Literal(kind="element:html", name="Leaf"),)),
    *[
        (
            f"<{name}>x</{name}>".encode(),
            (_Literal(kind="element:html", name=name, label="leaf"), _Literal(kind="text", data="x", parent="leaf")),
        )
        for name in ("Leaf", "leaf", "_name", "name-1", "name.1", "é", "水")
    ],
    *[
        (
            f"<Leaf>{encoded}</Leaf>".encode(),
            (
                _Literal(kind="element:html", name="Leaf", label="leaf"),
                _Literal(kind="text", data=decoded, parent="leaf"),
            ),
        )
        for encoded, decoded in (
            ("x", "x"),
            ("a\tb\r\nc\rd", "a\tb\nc\nd"),
            ("&amp;&lt;&gt;&quot;&apos;", "&<>\"'"),
            ("&#9;&#10;&#13;", "\t\n\r"),
            ("&#65;&#x1F600;", "A😀"),
            ("é水😀", "é水😀"),
        )
    ],
    *[
        (
            f"<Leaf value={quote}{encoded}{quote}/>".encode(),
            (_Literal(kind="element:html", name="Leaf", attrs=(("value", decoded),)),),
        )
        for quote in ("'", '"')
        for encoded, decoded in (
            ("", ""),
            ("a b", "a b"),
            ("&quot;&apos;", "\"'"),
            ("a\tb\r\nc\rd", "a b c d"),
            ("&amp;&lt;", "&<"),
            ("&#9;&#10;&#13;", "\t\n\r"),
            ("é水😀", "é水😀"),
        )
    ],
    (
        b"<Leaf>a<Inner/>b</Leaf>",
        (
            _Literal(kind="element:html", name="Leaf", label="leaf"),
            _Literal(kind="text", data="a", parent="leaf"),
            _Literal(kind="element:html", name="Inner", parent="leaf"),
            _Literal(kind="text", data="b", parent="leaf"),
        ),
    ),
    (
        b"<Leaf><!--inside--></Leaf>",
        (
            _Literal(kind="element:html", name="Leaf", label="leaf"),
            _Literal(kind="comment", data="inside", parent="leaf"),
        ),
    ),
    (
        b"<Leaf><?probe data?></Leaf>",
        (
            _Literal(kind="element:html", name="Leaf", label="leaf"),
            _Literal(kind="pi", name="probe", data="data", parent="leaf"),
        ),
    ),
    *[
        (
            f"<Leaf><![CDATA[{text}]]></Leaf>".encode(),
            (
                _Literal(kind="element:html", name="Leaf", label="leaf"),
                _Literal(kind="cdata", data=text, parent="leaf"),
            ),
        )
        for text in ("", "x", "<&", "é水😀")
    ],
    (
        b'<p:Leaf xmlns:p="urn:p" p:key="value"/>',
        (_Literal(kind="element:html", name="p:Leaf", attrs=(("p:key", "value"), ("xmlns:p", "urn:p"))),),
    ),
    (b'<Leaf xmlns="urn:d"/>', (_Literal(kind="element:html", name="Leaf", attrs=(("xmlns", "urn:d"),)),)),
    (b'<Leaf xml:lang="en"/>', (_Literal(kind="element:html", name="Leaf", attrs=(("xml:lang", "en"),)),)),
    (
        b'<Leaf xmlns="urn:d"><Inner xmlns=""/></Leaf>',
        (
            _Literal(kind="element:html", name="Leaf", attrs=(("xmlns", "urn:d"),), label="leaf"),
            _Literal(kind="element:html", name="Inner", attrs=(("xmlns", ""),), parent="leaf"),
        ),
    ),
    (
        b'<p:Leaf xmlns:p="urn:a"><p:Inner xmlns:p="urn:b"/></p:Leaf>',
        (
            _Literal(kind="element:html", name="p:Leaf", attrs=(("xmlns:p", "urn:a"),), label="leaf"),
            _Literal(kind="element:html", name="p:Inner", attrs=(("xmlns:p", "urn:b"),), parent="leaf"),
        ),
    ),
)
_RAW: Final[tuple[bytes, ...]] = (
    b"",
    b"\xff",
    b"\xc0\xaf",
    b"<",
    b"<Root>",
    b"<Root/>tail",
    b"<Root>&unknown;</Root>",
    b"<Root>&#0;</Root>",
    b"<Root a='x' a='y'/>",
    b"<Root><!--a--b--></Root>",
)
_RULES: Final[dict[str, _Rule]] = _rules()
_GRAMMAR: Final[Grammar] = compile_grammar(tuple(rule.production for rule in _RULES.values()), "document")

__all__ = [
    "XmlRecord",
    "main",
    "xml_document_check",
    "xml_expected",
    "xml_generate",
    "xml_grammar",
    "xml_raw_generate",
    "xml_raw_inputs",
    "xml_snapshot",
    "xml_source_controls",
    "xml_source_generate",
    "xml_source_seeds",
    "xml_structure_check",
]

if __name__ == "__main__":
    raise SystemExit(main())
