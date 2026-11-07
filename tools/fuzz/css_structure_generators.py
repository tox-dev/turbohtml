"""Charge complete CSS syntax trees so lexical helpers do not consume fictitious nodes."""

from __future__ import annotations

import argparse
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from fuzz.css_generation_tables import CSS_ROWS, SOURCE_URLS, CssRow
from fuzz.structure_generators import (
    BudgetError,
    Generated,
    GenerationBudget,
    Grammar,
    GrammarError,
    Identifier,
    Production,
    ProductionFloorError,
    Reference,
    compile_grammar,
    generate,
    generation_sweep,
    write_corpus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    # Preserve source bytes for the coverage-guided corpus.
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=512)
    parser.add_argument("--steps", type=int, default=128)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--seed", type=int, default=0)
    args: Final = parser.parse_args(argv)
    if args.count < 0:
        parser.error("count must be nonnegative")
    try:
        cases: Final = (
            tuple(
                css_generate(random.Random(args.seed + index), args.budget, steps=args.steps)
                for index in range(args.count)
            )
            if args.count
            else generation_sweep(_GRAMMAR, budget=GenerationBudget(nodes=args.budget, steps=args.steps))
        )
    except (BudgetError, GrammarError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    return 0


def css_generate(rng: random.Random, budget: int = 96, *, steps: int = 64) -> Generated:
    # Reserve node and expansion budgets across nested at-rules and sibling sheets.
    return generate(_GRAMMAR, rng, GenerationBudget(nodes=budget, steps=steps))


def css_grammar(rows: Sequence[CssRow] = CSS_ROWS) -> Grammar:
    # Retain source alternatives as distinct productions for forced coverage.
    standard: str = "https://www.w3.org/TR/2021/CRD-css-syntax-3-20211224/"
    return compile_grammar(
        (
            Production(
                name="css:sheet",
                symbol="root",
                parts=(*_PREFIX, Reference(symbol="body")),
                nodes=30,
                origin=standard,
                height=7,
            ),
            Production(name="css:leaf", symbol="body", parts=(b"a{color:red}",), nodes=8, origin=standard, height=4),
            Production(
                name="css:siblings",
                symbol="body",
                parts=(Reference(symbol="body", depth=0), Reference(symbol="body", depth=0)),
                nodes=0,
                origin=standard,
            ),
            *(
                Production(
                    name=f"css:nested:{name}",
                    symbol="body",
                    parts=(prefix, Reference(symbol="body", depth=2), b"}"),
                    nodes=nodes,
                    origin=standard,
                    height=height,
                )
                for name, prefix, nodes, height in (
                    ("media", b"@media all{", 5, 3),
                    ("supports", b"@supports(display:grid){", 8, 6),
                    ("container", b"@container(width>1px){", 7, 4),
                    ("layer", b"@layer x{", 5, 3),
                )
            ),
            *(_production(row) for row in rows),
        ),
        "root",
        identifiers=("x", "y", "z", "u", "v"),
    )


def _production(row: CssRow) -> Production:
    origin: Final = SOURCE_URLS[row[0]] + f"#L{row[1]}"
    name: Final = f"css:{'blink' if row[0] == 'blink-css.dict' else 'domato:' + row[0]}:{row[1]}"
    parts: Final = _references(row[4])
    if row[9] or row[10]:
        return Production(
            name=name,
            symbol="root",
            parts=(*parts, *_PREFIX) if row[10] else (*_PREFIX, *parts),
            nodes=row[5],
            origin=origin,
            height=row[6],
        )
    return Production(name=name, symbol="body", parts=parts, nodes=row[7] - 1, origin=origin, height=row[8] - 1)


def _references(source: str) -> Parts:
    parts: Final[list[bytes | Identifier]] = []
    position = 0
    for token in _BINDING.finditer(source):
        parts.append(source[position : token.start()].encode("utf-8"))
        if token[0].startswith("--"):
            parts.append(b"--")
        parts.append(Identifier(name=token[0].removeprefix("--"), definition=False))
        position = token.end()
    parts.append(source[position:].encode("utf-8"))
    return tuple(parts)


def css_source_generate(rng: random.Random) -> str:
    # Use whole sheets so at-rules reach minification.
    return css_generate(rng).data.decode("utf-8")


def css_source_seeds() -> list[str]:
    # Force pinned alternatives through their shortest emitting root paths.
    return [
        case.data.decode("utf-8") for case in generation_sweep(_GRAMMAR, budget=GenerationBudget(nodes=512, steps=128))
    ]


Parts: TypeAlias = tuple[bytes | Identifier, ...]
_BINDING: Final = re.compile(r"(?<![a-zA-Z0-9_-])(?:--cssvar[a-d]|anim)(?![a-zA-Z0-9_-])")
_PREFIX: Final[Parts] = (
    b":root{",
    *(
        part
        for name in ("cssvara", "cssvarb", "cssvarc", "cssvard")
        for part in (b"--", Identifier(name=name, definition=True), b":red;")
    ),
    b"}@keyframes ",
    Identifier(name="anim", definition=True),
    b"{from{opacity:0}}",
)
_GRAMMAR: Final = css_grammar()

__all__ = [
    "css_generate",
    "css_grammar",
    "css_source_generate",
    "css_source_seeds",
    "main",
]

if __name__ == "__main__":
    raise SystemExit(main())
