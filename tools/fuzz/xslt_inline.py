"""Compiler and application outcomes keep malformed trees in the differential."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml import HTMLParseError, parse_xml
from turbohtml.transform import Transform

from .schema_mutation import mutate

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    # Keep compiler, application and result findings in the exit status.
    parser: Final = argparse.ArgumentParser(description="Compare bounded inline XSLT trees with libxslt.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cases", type=int, default=12)
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--broken-references", action="store_true")
    parser.add_argument("--negative-control", action="store_true")
    arguments: Final = parser.parse_args(argv)
    if not 1 <= arguments.cases <= 256:
        parser.error("--cases must be between 1 and 256")
    if arguments.broken_references and not arguments.mutate:
        parser.error("--broken-references requires --mutate")
    findings = 0
    phases: Final[dict[str, int]] = {}
    pool: Final = tuple(generate(seed).stylesheet for seed in range(10))
    for seed in range(arguments.seed, arguments.seed + arguments.cases):
        case = generate(seed)
        if arguments.mutate:
            case = replace(
                case,
                stylesheet=mutate(generate(seed % 10).stylesheet, pool, seed, broken=arguments.broken_references),
                compiles=True,
                applies=not arguments.broken_references,
                output=None,
            )
        for row in compare(case, negative_control=arguments.negative_control):
            findings += row.expected != row.actual
            phases[row.phase] = phases.get(row.phase, 0) + 1
            print(json.dumps(asdict(row), sort_keys=True))
    print(json.dumps({"summary": {"cases": arguments.cases, "findings": findings, "phases": phases}}, sort_keys=True))
    return int(findings != 0)


def compare(case: Case, *, negative_control: bool = False) -> Iterator[Verdict]:
    # Known labels expose shared mistakes before engine agreement.
    ours: Final = _turbohtml(case)
    reference: Final = _libxslt(case)
    digest: Final = hashlib.sha256((f"{case.stylesheet}\\0{case.document}").encode()).hexdigest()
    for engine, outcome in (("turbohtml", ours), ("libxslt", reference)):
        yield Verdict(
            seed=case.seed, sha256=digest, engine=engine, phase="compilation", expected=case.compiles, actual=outcome[0]
        )
        yield Verdict(
            seed=case.seed,
            sha256=digest,
            engine=engine,
            phase="application",
            expected=case.applies if case.compiles else None,
            actual=outcome[1],
        )
        if outcome[1]:
            actual: Final = (
                _result("wrong" if case.method == "text" else "<wrong/>", case.method)
                if negative_control and engine == "turbohtml"
                else outcome[2]
            )
            yield Verdict(
                seed=case.seed,
                sha256=digest,
                engine=engine,
                phase="result",
                expected=reference[2] if case.output is None else _result(case.output, case.method),
                actual=actual,
            )


def generate(seed: int) -> Case:
    # Independent expected results distinguish a wrong reference verdict.
    number: Final = random.Random(seed).randrange(1, 100)
    word: Final = f"word{number}"
    variable: Final = f"label{number}"
    template: Final = f"render{number}"
    key: Final = f"entry{number}"
    family: Final = seed % 12
    method: Final = "xml" if family == 7 else "text"
    bodies: Final = (
        (f"<x:text> {word} &amp; beta </x:text>", f" {word} & beta "),
        ('<x:for-each select="/root/item"><x:value-of select="."/></x:for-each>', "BA"),
        ('<x:choose><x:when test="count(/root/item)=2">two</x:when><x:otherwise>other</x:otherwise></x:choose>', "two"),
        (f'<x:value-of select="${variable}"/>', word),
        (f'<x:call-template name="{template}"/>', "BA"),
        ('<x:apply-templates select="/root/item" mode="shown"/>', "BA"),
        (
            (
                '<x:for-each select="/root/item"><x:sort select="@n" data-type="number"/>'
                '<x:value-of select="."/></x:for-each>'
            ),
            "AB",
        ),
        (
            f'<out key="{word}"> alpha <child>A</child> beta </out>',
            f'<out key="{word}"> alpha <child>A</child> beta </out>',
        ),
        (f"<x:value-of select=\"key('{key}', 'a')\"/>", "A"),
        ('<x:value-of select="$parameter"/>', "parameter"),
        ('<x:value-of select="["/>', None),
        ('<x:call-template name="missing"/>', None),
    )
    body, output = bodies[family]
    stylesheet: Final = (
        '<x:stylesheet xmlns:x="http://www.w3.org/1999/XSL/Transform" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:rng="http://relaxng.org/ns/structure/1.0" version="1.0">'
        f'<x:output method="{method}"/><x:variable name="{variable}" select="\'{word}\'"/>'
        '<x:param name="parameter" select="\'parameter\'"/>'
        f'<x:key name="{key}" match="item" use="@id"/>'
        f'<x:template name="{template}"><x:value-of select="/root"/></x:template>'
        '<x:template match="item" mode="shown"><x:value-of select="."/></x:template>'
        f'<x:template match="/">{body}</x:template></x:stylesheet>'
    )
    return Case(
        seed,
        stylesheet,
        '<root><item id="b" n="2">B</item><item id="a" n="1">A</item></root>',
        method,
        family != 10,
        family < 10,
        output,
    )


def _turbohtml(case: Case) -> tuple[bool, bool | None, str | ResultTree | None]:
    try:
        compiled = Transform(parse_xml(case.stylesheet))
    except (HTMLParseError, ValueError):
        return False, None, None
    try:
        return True, True, _result(compiled(parse_xml(case.document)), case.method)
    except (HTMLParseError, ValueError, RuntimeError):
        return True, False, None


def _libxslt(case: Case) -> tuple[bool, bool | None, str | ResultTree | None]:
    parser: Final = lxml.etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        compiled = lxml.etree.XSLT(lxml.etree.fromstring(case.stylesheet.encode(), parser))
    except (lxml.etree.XSLTParseError, lxml.etree.XMLSyntaxError):
        return False, None, None
    try:
        return True, True, _result(str(compiled(lxml.etree.fromstring(case.document.encode(), parser))), case.method)
    except (lxml.etree.XSLTApplyError, lxml.etree.XMLSyntaxError):
        return True, False, None


def _result(output: str, method: str) -> str | ResultTree:
    return (
        output
        if method == "text"
        else _tree(
            lxml.etree.fromstring(output.encode(), lxml.etree.XMLParser(resolve_entities=False, no_network=True))
        )
    )


def _tree(node: lxml.etree._Element) -> ResultTree:
    return ResultTree(
        name=node.tag
        if isinstance(node.tag, str)
        else "#" + lxml.etree.tostring(node, encoding="unicode", with_tail=False),
        attributes=tuple(sorted(node.attrib.items())),
        text=node.text or "",
        tail=node.tail or "",
        children=tuple(_tree(child) for child in node),
    )


@dataclass(frozen=True, slots=True)
class Case:
    # Independent phase labels survive serialization and mutation.
    seed: int
    stylesheet: str
    document: str
    method: str
    compiles: bool
    applies: bool
    output: str | None


@dataclass(frozen=True, kw_only=True, slots=True)
class ResultTree:
    # Expanded names preserve content while allowing serializer variation.
    name: str
    attributes: tuple[tuple[str, str], ...]
    text: str
    tail: str
    children: tuple[ResultTree, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Unavailable applications remain visible after compiler failures.
    seed: int
    sha256: str
    engine: str
    phase: str
    expected: bool | str | ResultTree | None
    actual: bool | str | ResultTree | None


__all__: Final = []


if __name__ == "__main__":
    raise SystemExit(main())
