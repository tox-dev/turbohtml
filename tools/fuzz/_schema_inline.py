"""Shared harness for the inline schema comparisons against libxml2."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Final, Protocol

import lxml.etree

from turbohtml import parse_xml

from .schema_mutation import mutate

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from turbohtml.validate import RelaxNG, XMLSchema


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    # Expected labels retain shared compiler errors.
    seed: int
    schema: str
    compiles: bool
    documents: tuple[tuple[str, bool], ...]


def verdicts(
    case: Case,
    ours: RelaxNG | XMLSchema | None,
    reference: lxml.etree.RelaxNG | lxml.etree.XMLSchema | None,
    *,
    negative_control: bool,
    reference_labels: bool,
) -> Iterator[Verdict]:
    schema_hash: Final = hashlib.sha256(case.schema.encode()).hexdigest()
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
            None
            if reference is None
            else reference.validate(lxml.etree.fromstring(document.encode(), reference_parser()))
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


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Unavailable document results keep compilation failures visible.
    seed: int
    sha256: str
    engine: str
    phase: str
    expected: bool
    actual: bool | None


def reference_parser() -> lxml.etree.XMLParser:
    return lxml.etree.XMLParser(resolve_entities=False, no_network=True)


class _Compare(Protocol):
    def __call__(self, case: Case, *, negative_control: bool, reference_labels: bool) -> Iterator[Verdict]: ...


def run(
    argv: Sequence[str] | None,
    *,
    description: str,
    pool_seeds: tuple[int, ...],
    generate: Callable[[int], Case],
    compare: _Compare,
) -> int:
    # Keep compiler and validation findings in the exit status.
    parser: Final = argparse.ArgumentParser(description=description)
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


__all__: Final[list[str]] = [
    "Case",
    "Verdict",
    "reference_parser",
    "run",
    "verdicts",
]
