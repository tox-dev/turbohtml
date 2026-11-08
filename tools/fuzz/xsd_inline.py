"""Inline labels retain compiler errors alongside document verdicts."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml.validate import XMLSchema

from ._schema_inline import Case, reference_parser, run, verdicts

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ._schema_inline import Verdict


def main(argv: Sequence[str] | None = None) -> int:
    return run(
        argv,
        description="Compare bounded inline XSD schemas with libxml2.",
        pool_seeds=(0, 1, 2, 3, 4, 5, 8),
        generate=generate,
        compare=compare,
    )


def compare(case: Case, *, negative_control: bool = False, reference_labels: bool = False) -> Iterator[Verdict]:
    # Compile first so invalid schemas remain part of the comparison.
    try:
        ours = XMLSchema(case.schema)
    except ValueError:
        ours = None
    try:
        reference = lxml.etree.XMLSchema(lxml.etree.fromstring(case.schema.encode(), reference_parser()))
    except (lxml.etree.XMLSchemaParseError, lxml.etree.XMLSyntaxError):
        reference = None
    return verdicts(case, ours, reference, negative_control=negative_control, reference_labels=reference_labels)


def generate(seed: int) -> Case:
    # Known labels distinguish shared mistakes from agreement.
    rng: Final = random.Random(seed)
    number: Final = rng.randrange(1, 100)
    word: Final = f"word{number}"
    family: Final = seed % 9
    if family == 0:
        declarations: Final = '<xs:element name="v" type="xs:string"/>'
        documents: Final = ((f"<v>{word}</v>", True), ("<v/>", True), ("<other/>", False))
    elif family == 1:
        declarations = '<xs:element name="v" type="xs:integer"/>'
        documents = ((f"<v>{number}</v>", True), ("<v>wrong</v>", False), ("<other/>", False))
    elif family == 2:
        declarations = (
            '<xs:element name="v"><xs:simpleType><xs:restriction base="xs:token">'
            f'<xs:enumeration value="{word}"/></xs:restriction></xs:simpleType></xs:element>'
        )
        documents = ((f"<v>  {word}  </v>", True), ("<v>wrong</v>", False), ("<other/>", False))
    elif family == 3:
        declarations = (
            '<xs:element name="v"><xs:complexType><xs:sequence><xs:element name="child" type="xs:integer"/>'
            "</xs:sequence></xs:complexType></xs:element>"
        )
        documents = ((f"<v><child>{number}</child></v>", True), ("<v/>", False), ("<other/>", False))
    elif family == 4:
        declarations = (
            '<xs:element name="v"><xs:complexType><xs:attribute name="key" type="xs:integer" use="required"/>'
            "</xs:complexType></xs:element>"
        )
        documents = ((f'<v key="{number}"/>', True), ('<v key="wrong"/>', False), ("<v/>", False))
    elif family == 5:
        declarations = (
            '<xs:element name="v"><xs:simpleType><xs:restriction base="xs:integer">'
            f'<xs:minInclusive value="{number}"/><xs:maxInclusive value="{number + 2}"/>'
            "</xs:restriction></xs:simpleType></xs:element>"
        )
        documents = ((f"<v>{number}</v>", True), (f"<v>{number + 3}</v>", False), ("<v>wrong</v>", False))
    elif family == 8:
        declarations = (
            f'<xs:simpleType name="number{number}"><xs:restriction base="xs:integer"/></xs:simpleType>'
            f'<xs:element name="v" type="number{number}"/>'
        )
        documents = ((f"<v>{number}</v>", True), ("<v>wrong</v>", False), ("<other/>", False))
    else:
        declarations = '<xs:element name="v" type="xs:notAType"/>' if family == 6 else '<xs:element type="xs:string"/>'
        documents = ()
    xs: str = 'xmlns:xs="http://www.w3.org/2001/XMLSchema"'
    return Case(
        seed=seed,
        schema=f"<xs:schema {xs}>{declarations}</xs:schema>",
        compiles=family < 6 or family == 8,
        documents=documents,
    )


__all__: Final[list[str]] = []


if __name__ == "__main__":
    raise SystemExit(main())
