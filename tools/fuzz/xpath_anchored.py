"""A selected node keeps generated predicates from collapsing to empty results."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Final, TypeAlias, cast

import lxml.etree

from turbohtml import Element, Node, parse_xml

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from lxml.etree import _Element as LxmlElement  # simplint: ignore[private-import-path]  # lxml's element type

_Value: TypeAlias = tuple[str, ...] | float | bool | str


def main(argv: Sequence[str] | None = None) -> int:
    # Keep automated batches finite and findings distinct from agreement.
    parser: Final[argparse.ArgumentParser] = argparse.ArgumentParser(
        description="Compare bounded anchored XPath queries with libxml2."
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--cases", type=int, default=32)
    parser.add_argument("--negative-control", action="store_true")
    arguments: Final[argparse.Namespace] = parser.parse_args(argv)
    if not 1 <= arguments.cases <= 256:
        parser.error("--cases must be between 1 and 256")
    findings = 0
    rows = 0
    for seed in range(arguments.seed, arguments.seed + arguments.cases):
        for verdict in compare(generate(seed), negative_control=arguments.negative_control):
            findings += verdict.ours != verdict.reference
            rows += 1
            print(json.dumps(asdict(verdict), sort_keys=True))
    print(json.dumps({"summary": {"cases": arguments.cases, "rows": rows, "findings": findings}}, sort_keys=True))
    return int(findings != 0)


def compare(case: Case, *, negative_control: bool = False) -> Iterator[Verdict]:
    # Rectify against the complete selection to retain position and size context.
    ours, reference = contexts(case.xml)
    query: Final[str] = _rectify(reference, case)
    case_hash: Final[str] = hashlib.sha256((f"{case.xml}\0{case.target}\0{case.predicate}").encode()).hexdigest()
    for expression in (
        query,
        f"{query}/@id",
        f"{query}/text()",
        f"count({query})",
        f"boolean({query})",
        f"string({query})",
    ):
        ours_expression: Final[str] = (
            f"not({expression})" if negative_control and expression.startswith("boolean(") else expression
        )
        yield Verdict(
            seed=case.seed,
            sha256=case_hash,
            target=case.target,
            expression=expression,
            ours=_evaluate(ours, ours_expression),
            reference=_evaluate(reference, expression),
        )


def contexts(xml: str) -> tuple[Element, LxmlElement]:
    # lxml has no document-node context and evaluates from the root element, where a relative expression selects one
    # node fewer than from the document. Both engines start at the root element, so their answers are comparable.
    parser: Final[lxml.etree.XMLParser[lxml.etree._Element]] = lxml.etree.XMLParser(
        resolve_entities=False, no_network=True
    )
    reference: Final[lxml.etree._Element] = lxml.etree.fromstring(xml.encode(), parser)
    ours: Final[Element | None] = parse_xml(xml).root
    assert ours is not None  # ruff: ignore[assert]  # the generator always writes a root element
    return ours, reference


def _rectify(reference: lxml.etree._Element, case: Case) -> str:
    target = reference.xpath(f"//*[@id='{case.target}']")[0]
    prefix: Final[str] = f"//{target.tag}"
    predicate: Final[str] = f"boolean({case.predicate})"
    initial: Final[str] = f"{prefix}[{predicate}]"
    try:
        selected = reference.xpath(initial)
    except lxml.etree.XPathError:
        return initial
    return initial if case.target in [node.get("id") for node in selected] else f"{prefix}[not({predicate})]"


def _evaluate(document: Node | lxml.etree._Element, expression: str) -> tuple[str, _Value]:
    try:
        result = document.xpath(expression, smart_strings=False)
    except (ValueError, lxml.etree.XPathError):
        return "error", "evaluation"
    if isinstance(result, list):
        return "nodes", tuple(
            str(item.attrs["id"])
            if isinstance(item, Element)
            else str(item)
            if isinstance(item, str)
            else cast("LxmlElement", item).attrib["id"]
            for item in result
        )
    if isinstance(result, bool):
        return "boolean", result
    if isinstance(result, float):
        return "number", result
    return "string", str(result)


def generate(seed: int) -> Case:
    # Restrict parent choices to existing nodes to bound depth and avoid cycles.
    generator: Final[random.Random] = random.Random(seed)
    elements: Final[list[lxml.etree._Element]] = [lxml.etree.Element("root", id="n0", score="0")]
    for position in range(1, generator.randint(8, 16)):
        element: Final[lxml.etree._Element] = lxml.etree.SubElement(
            generator.choice(elements),
            generator.choice(("item", "branch", "leaf")),
            id=f"n{position}",
            score=str(generator.randrange(8)),
        )
        element.text = generator.choice(("alpha", "beta", "gamma"))
        elements.append(element)
    return Case(
        xml=lxml.etree.tostring(elements[0], encoding="unicode"),
        target=generator.choice(elements).attrib["id"],
        predicate=generator.choice((
            "@score < 3",
            "@score >= 5",
            "@missing",
            "count(child::*)",
            "count(ancestor::*) > 2",
            "position() = 1",
            "position() = last()",
            "contains(string(.), 'alpha')",
            "string-length(@id) = 2",
            "preceding-sibling::*",
            "following-sibling::*",
            "@score < 4 and child::*",
            "@score > 4 or parent::item",
        )),
        seed=seed,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Case:
    # Keep replay inputs independent of engine-owned nodes.
    xml: str
    target: str
    predicate: str
    seed: int | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class Verdict:
    # Retain both answers instead of treating the reference as infallible.
    seed: int | None
    sha256: str
    target: str
    expression: str
    ours: tuple[str, _Value]
    reference: tuple[str, _Value]


__all__: Final[list[str]] = [
    "Case",
]


if __name__ == "__main__":
    raise SystemExit(main())
