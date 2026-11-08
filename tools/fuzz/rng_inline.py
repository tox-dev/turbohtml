"""Inline labels retain compiler errors alongside document verdicts."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml import parse_xml
from turbohtml.validate import RelaxNG

from .schema_mutation import mutate

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    # Keep compiler and validation findings in the exit status.
    parser: Final = argparse.ArgumentParser(description="Compare bounded inline RELAX NG schemas with libxml2.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cases", type=int, default=32)
    parser.add_argument("--negative-control", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--broken-references", action="store_true")
    arguments: Final = parser.parse_args(argv)
    if not 1 <= arguments.cases <= 256:
        parser.error("--cases must be between 1 and 256")
    if arguments.broken_references and not arguments.mutate:
        parser.error("--broken-references requires --mutate")
    pool_seeds: Final = (0, 1, 2, 3, 4, 5)
    pool: Final = tuple(generate(seed).schema for seed in pool_seeds)
    findings = 0
    rows = 0
    for seed in range(arguments.seed, arguments.seed + arguments.cases):
        case = generate(pool_seeds[seed % len(pool_seeds)] if arguments.mutate else seed)
        if arguments.mutate:
            case = replace(
                case,
                schema=mutate(case.schema, pool, seed, broken=arguments.broken_references),
                compiles=not arguments.broken_references,
                documents=() if arguments.broken_references else case.documents,
            )
        for verdict in compare(case, negative_control=arguments.negative_control, reference_labels=arguments.mutate):
            findings += verdict.actual != verdict.expected
            rows += 1
            print(json.dumps(asdict(verdict), sort_keys=True))
    print(json.dumps({"summary": {"cases": arguments.cases, "rows": rows, "findings": findings}}, sort_keys=True))
    return int(findings != 0)


def compare(case: Case, *, negative_control: bool = False, reference_labels: bool = False) -> Iterator[Verdict]:
    # Compile first so invalid schemas remain part of the comparison.
    schema_hash: Final = hashlib.sha256(case.schema.encode()).hexdigest()
    ours: Final = _compile_turbohtml(case.schema)
    reference: Final = _compile_lxml(case.schema)
    yield Verdict(
        seed=case.seed,
        sha256=schema_hash,
        engine="turbohtml",
        phase="compilation",
        expected=case.compiles,
        actual=ours is not None,
    )
    yield Verdict(
        seed=case.seed,
        sha256=schema_hash,
        engine="libxml2",
        phase="compilation",
        expected=case.compiles,
        actual=reference is not None,
    )
    for document, expected in case.documents:
        document_hash: Final = hashlib.sha256((f"{case.schema}\\0{document}").encode()).hexdigest()
        actual: Final = None if ours is None else ours.is_valid(parse_xml(document))
        reference_actual: Final = (
            None if reference is None else reference.validate(lxml.etree.fromstring(document.encode(), _parser()))
        )
        expected_result: Final = reference_actual if reference_labels and reference_actual is not None else expected
        yield Verdict(
            seed=case.seed,
            sha256=document_hash,
            engine="turbohtml",
            phase="validation",
            expected=expected_result,
            actual=not actual if negative_control and actual is not None else actual,
        )
        yield Verdict(
            seed=case.seed,
            sha256=document_hash,
            engine="libxml2",
            phase="validation",
            expected=expected_result,
            actual=reference_actual,
        )


def generate(seed: int) -> Case:
    # Explicit value types keep whitespace semantics independent of defaulting.
    number: Final = random.Random(seed).randrange(1, 100)
    word: Final = f"word{number}"
    family: Final = seed % 8
    rng: str = 'xmlns:rng="http://relaxng.org/ns/structure/1.0"'
    if family == 0:
        pattern: Final = "<rng:text/>"
        documents: Final = ((f"<v>{word}</v>", True), ("<v/>", True), ("<other/>", False))
    elif family == 1:
        pattern = '<rng:data type="integer"/>'
        documents = ((f"<v>{number}</v>", True), ("<v>wrong</v>", False), ("<other/>", False))
    elif family == 2:
        pattern = f'<rng:value type="string">{word}</rng:value>'
        documents = ((f"<v>{word}</v>", True), ("<v>wrong</v>", False), ("<other/>", False))
    elif family == 3:
        pattern = (
            '<rng:choice><rng:value type="string">a</rng:value><rng:value type="string">b</rng:value></rng:choice>'
        )
        documents = (("<v>a</v>", True), ("<v>b</v>", True), ("<v>wrong</v>", False))
    elif family == 4:
        pattern = (
            '<rng:group><rng:element name="child"><rng:data type="integer"/></rng:element>'
            '<rng:element name="tail"><rng:empty/></rng:element></rng:group>'
        )
        documents = (
            (f"<v><child>{number}</child><tail/></v>", True),
            (f"<v><tail/><child>{number}</child></v>", False),
            ("<v/>", False),
        )
    elif family == 5:
        pattern = '<rng:attribute name="key"><rng:data type="integer"/></rng:attribute><rng:empty/>'
        documents = ((f'<v key="{number}"/>', True), ('<v key="wrong"/>', False), ("<v/>", False))
    else:
        schema: Final = (
            f'<rng:grammar {rng}><rng:start><rng:ref name="missing"/></rng:start></rng:grammar>'
            if family == 6
            else "<foreign/>"
        )
        return Case(seed=seed, schema=schema, compiles=False, documents=())
    name: Final = f"entry{number}"
    return Case(
        seed=seed,
        schema=f'<rng:grammar {rng} datatypeLibrary="http://www.w3.org/2001/XMLSchema-datatypes">'
        f'<rng:start><rng:ref name="{name}"/></rng:start>'
        f'<rng:define name="{name}"><rng:element name="v">{pattern}</rng:element></rng:define></rng:grammar>',
        compiles=True,
        documents=documents,
    )


def _compile_turbohtml(schema: str) -> RelaxNG | None:
    try:
        return RelaxNG(schema)
    except ValueError:
        return None


def _compile_lxml(schema: str) -> lxml.etree.RelaxNG | None:
    try:
        return lxml.etree.RelaxNG(lxml.etree.fromstring(schema.encode(), _parser()))
    except (lxml.etree.RelaxNGParseError, lxml.etree.XMLSyntaxError):
        return None


def _parser() -> lxml.etree.XMLParser:
    return lxml.etree.XMLParser(resolve_entities=False, no_network=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    # Expected labels retain shared compiler errors.
    seed: int
    schema: str
    compiles: bool
    documents: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Unavailable document results keep compilation failures visible.
    seed: int
    sha256: str
    engine: str
    phase: str
    expected: bool
    actual: bool | None


__all__: Final[list[str]] = []


if __name__ == "__main__":
    raise SystemExit(main())
