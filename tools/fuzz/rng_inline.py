"""Inline labels retain compiler errors alongside document verdicts."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Final

import lxml.etree

from turbohtml.validate import RelaxNG

from ._schema_inline import Case, reference_parser, run, verdicts

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from ._schema_inline import Verdict


def _main(argv: Sequence[str] | None = None) -> int:
    return run(
        argv,
        description="Compare bounded inline RELAX NG schemas with libxml2.",
        pool_seeds=(0, 1, 2, 3, 4, 5),
        generate=_generate,
        compare=_compare,
    )


def _compare(case: Case, *, negative_control: bool, reference_labels: bool) -> Iterator[Verdict]:
    # Compile first so invalid schemas remain part of the comparison.
    try:
        ours = RelaxNG(case.schema)
    except ValueError:
        ours = None
    try:
        reference = lxml.etree.RelaxNG(lxml.etree.fromstring(case.schema.encode(), reference_parser()))
    except (lxml.etree.RelaxNGParseError, lxml.etree.XMLSyntaxError):
        reference = None
    return verdicts(case, ours, reference, negative_control=negative_control, reference_labels=reference_labels)


def _generate(seed: int) -> Case:
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


__all__: Final[list[str]] = []


if __name__ == "__main__":
    raise SystemExit(_main())
