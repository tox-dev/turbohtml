"""Reserve sibling costs before expansion so a leaf fallback can complete its input."""

from __future__ import annotations

import argparse
import hashlib
import random
from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Keep corpus files as materialized inputs rather than oracle mode strings."""
    parser: Final = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--budget", type=int, default=30)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--sweep", action="store_true")
    args: Final = parser.parse_args(argv)
    if args.count < 1:
        parser.error("count must be positive")
    try:
        cases = (
            generation_sweep(_HTML_GRAMMAR, args.count, args.budget)
            if args.sweep
            else tuple(
                generate(_HTML_GRAMMAR, random.Random(args.seed + index), args.budget) for index in range(args.count)
            )
        )
    except (GrammarError, BudgetError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    return 0


def compile_grammar(
    productions: Sequence[Production],
    root: str,
    *,
    identifiers: tuple[str, ...] = ("x", "y", "z"),
    materialize: Callable[[Generated], Generated] | None = None,
) -> Grammar:
    """Reject incomplete grammars before consuming a finite input budget."""
    if (
        not 1 <= len(identifiers) <= 64
        or len(set(identifiers)) != len(identifiers)
        or any(not name.isascii() or not name.isidentifier() for name in identifiers)
    ):
        msg = "identifier pools require 1 to 64 distinct ASCII identifiers"
        raise GrammarError(msg)
    rules: Final[dict[str, list[Production]]] = {}
    names: Final[set[str]] = set()
    for production in productions:
        if production.name in names or production.nodes < 0 or production.height < 0:
            msg = "production names must be unique, node costs nonnegative, and heights nonnegative"
            raise GrammarError(msg)
        names.add(production.name)
        rules.setdefault(production.symbol, []).append(production)
        if any(isinstance(part, Reference) and part.depth < 0 for part in production.parts):
            msg = "reference depths must be nonnegative"
            raise GrammarError(msg)
    if root not in rules:
        msg = f"unknown root: {root}"
        raise GrammarError(msg)
    symbols: Final = set(rules)
    references: Final = {
        part.symbol for production in productions for part in production.parts if isinstance(part, Reference)
    }
    if unknown := references - symbols:
        msg = f"unknown symbols: {sorted(unknown)}"
        raise GrammarError(msg)
    if unreachable := symbols - _reachable(root, rules):
        msg = f"unreachable symbols: {sorted(unreachable)}"
        raise GrammarError(msg)
    minimum: Final[dict[str, tuple[int, int]]] = {}
    while _relax(rules, minimum):
        pass
    if missing := symbols - minimum.keys():
        msg = f"nonproductive symbols: {sorted(missing)}"
        raise GrammarError(msg)
    return Grammar(
        tuple(productions),
        root,
        {name: tuple(items) for name, items in rules.items()},
        minimum,
        identifiers,
        _edges(productions, minimum),
        materialize,
    )


def _edges(
    productions: Sequence[Production], minimum: dict[str, tuple[int, int]]
) -> tuple[tuple[Production, int, str, int, int], ...]:
    """Price every reference once as its production's minimum cost less the referenced child's minimum."""
    edges: Final[list[tuple[Production, int, str, int, int]]] = []
    for production in productions:
        base: Final = _cost(production, minimum)
        for index, part in enumerate(production.parts):
            if isinstance(part, Reference):
                child: Final = minimum[part.symbol]
                edges.append((production, index, part.symbol, base[0] - child[0], base[1] - child[1]))
    return tuple(edges)


def _reachable(root: str, rules: dict[str, list[Production]]) -> set[str]:
    found: Final[set[str]] = set()
    pending: Final = [root]
    while pending:
        if (symbol := pending.pop()) not in found:
            found.add(symbol)
            pending.extend(
                part.symbol for production in rules[symbol] for part in production.parts if isinstance(part, Reference)
            )
    return found


def _relax(rules: dict[str, list[Production]], minimum: dict[str, tuple[int, int]]) -> bool:
    changed = False
    for symbol, productions in rules.items():
        for production in productions:
            references: Final = [part.symbol for part in production.parts if isinstance(part, Reference)]
            if all(part in minimum for part in references):
                cost: Final = _cost(production, minimum)
                if symbol not in minimum or cost < minimum[symbol]:
                    minimum[symbol] = cost
                    changed = True
    return changed


def _cost(production: Production, minimum: dict[str, tuple[int, int]]) -> tuple[int, int]:
    children: Final = [minimum[part.symbol] for part in production.parts if isinstance(part, Reference)]
    return production.nodes + sum(cost[0] for cost in children), 1 + sum(cost[1] for cost in children)


def _route(grammar: Grammar, name: str) -> tuple[tuple[Production, int], ...]:
    if (target := next((production for production in grammar.productions if production.name == name), None)) is None:
        msg = f"unknown production: {name}"
        raise GrammarError(msg)
    costs: Final = {target.symbol: _cost(target, grammar.minimum)}
    paths: Final[dict[str, tuple[tuple[Production, int], ...]]] = {target.symbol: ((target, -1),)}
    changed = True
    while changed:
        changed = False
        for production, index, symbol, nodes, steps in grammar.edges:
            if symbol in costs:
                cost: Final = nodes + costs[symbol][0], steps + costs[symbol][1]
                if production.symbol not in costs or cost < costs[production.symbol]:
                    costs[production.symbol] = cost
                    paths[production.symbol] = ((production, index), *paths[symbol])
                    changed = True
    return paths[grammar.root]


def _route_cost(grammar: Grammar, route: tuple[tuple[Production, int], ...]) -> tuple[int, int]:
    cost = (0, 0)
    for production, index in reversed(route):
        base: Final = _cost(production, grammar.minimum)
        if index < 0:
            cost = base
        else:
            child: Final = grammar.minimum[cast("Reference", production.parts[index]).symbol]
            cost = base[0] - child[0] + cost[0], base[1] - child[1] + cost[1]
    return cost


def generate(
    grammar: Grammar,
    rng: random.Random,
    budget: int | GenerationBudget = 30,
    *,
    leaf: bool = False,
    force: str | None = None,
) -> Generated:
    """Reserve the canonical minimum-node derivation and its expansion steps."""
    route: Final = _route(grammar, force) if force is not None else ()
    remaining = _initial_budget(grammar, budget, route)
    pending: Final[list[tuple[bytes | Reference | Identifier, int, bool, tuple[tuple[Production, int], ...]]]] = [
        (Reference(grammar.root, 0), 0, leaf, route)
    ]
    output: Final[list[bytes]] = []
    fired: Final[list[str]] = []
    bindings: Final = _Bindings({}, set(), set(), grammar.identifiers)
    depth = 0
    while pending:
        part, level, minimal, path = pending.pop()
        if isinstance(part, bytes):
            output.append(part)
        elif isinstance(part, Identifier):
            output.append(_identifier(part, bindings))
        else:
            chosen, index, minimal = _choose(
                grammar, rng, (part, level, minimal, path), _available(grammar, pending, remaining)
            )
            remaining = remaining[0] - chosen.nodes, remaining[1] - 1
            if chosen.nodes:
                depth = max(depth, level + chosen.height)
            fired.append(chosen.name)
            pending.extend(
                (
                    item,
                    level + item.depth if isinstance(item, Reference) else level,
                    minimal,
                    path[1:] if path and position == index else (),
                )
                for position, item in reversed(tuple(enumerate(chosen.parts)))
            )
    if missing := bindings.references - bindings.definitions:
        msg = f"undefined identifiers: {sorted(missing)}"
        raise GrammarError(msg)
    case: Final = Generated(
        b"".join(output),
        (budget.nodes if isinstance(budget, GenerationBudget) else budget) - remaining[0],
        depth,
        tuple(fired),
        tuple(sorted(bindings.values.items())),
    )
    return case if grammar.materialize is None else grammar.materialize(case)


def _initial_budget(
    grammar: Grammar, budget: int | GenerationBudget, route: tuple[tuple[Production, int], ...]
) -> tuple[int, int]:
    remaining: Final = (
        (budget.nodes, budget.steps)
        if isinstance(budget, GenerationBudget)
        else (budget, budget * max(cost[1] for cost in grammar.minimum.values()))
    )
    required: Final = _route_cost(grammar, route) if route else grammar.minimum[grammar.root]
    if any(available < needed for available, needed in zip(remaining, required, strict=True)):
        msg = "budget cannot complete the root"
        raise BudgetError(msg)
    return remaining


def _available(
    grammar: Grammar,
    pending: list[tuple[bytes | Reference | Identifier, int, bool, tuple[tuple[Production, int], ...]]],
    remaining: tuple[int, int],
) -> tuple[int, int]:
    reserved: Final = [
        _route_cost(grammar, route) if route else grammar.minimum[item.symbol]
        for item, _, _, route in pending
        if isinstance(item, Reference)
    ]
    return remaining[0] - sum(cost[0] for cost in reserved), remaining[1] - sum(cost[1] for cost in reserved)


def _identifier(part: Identifier, bindings: _Bindings) -> bytes:
    if part.name not in bindings.values:
        if len(bindings.values) == len(bindings.pool):
            msg = "identifier pool exhausted"
            raise GrammarError(msg)
        bindings.values[part.name] = bindings.pool[len(bindings.values)]
    if part.definition:
        if part.name in bindings.definitions:
            msg = f"duplicate identifier definition: {part.name}"
            raise GrammarError(msg)
        bindings.definitions.add(part.name)
    else:
        bindings.references.add(part.name)
    return bindings.values[part.name].encode("ascii")


def _choose(
    grammar: Grammar,
    rng: random.Random,
    frame: tuple[Reference, int, bool, tuple[tuple[Production, int], ...]],
    available: tuple[int, int],
) -> tuple[Production, int, bool]:
    part, level, minimal, path = frame
    if path:
        return path[0][0], path[0][1], True
    eligible: Final = [
        production
        for production in grammar.rules[part.symbol]
        if all(cost <= limit for cost, limit in zip(_cost(production, grammar.minimum), available, strict=True))
    ]
    if minimal or rng.random() >= 0.75 ** (level + 1):
        return min(eligible, key=lambda production: _cost(production, grammar.minimum)), -1, True
    return rng.choice(eligible), -1, False


def generation_sweep(grammar: Grammar, minimum: int = 1, budget: int | GenerationBudget = 30) -> tuple[Generated, ...]:
    """Force each declared production through a minimum-cost root path."""
    if minimum < 1:
        msg = "production floors must be positive"
        raise ProductionFloorError(msg)
    cases: Final = tuple(
        generate(grammar, random.Random(index), budget, force=production.name)
        for index, production in enumerate(production for production in grammar.productions for _ in range(minimum))
    )
    assert_production_floors(cases, (production.name for production in grammar.productions), minimum)
    return cases


def assert_production_floors(cases: Iterable[Generated], productions: Iterable[str], minimum: int = 1) -> Counter[str]:
    """Fail a generation lane when a declared production stops executing."""
    if minimum < 1:
        msg = "production floors must be positive"
        raise ProductionFloorError(msg)
    counts: Final = Counter(name for case in cases for name in case.productions)
    if missing := {name: counts[name] for name in productions if counts[name] < minimum}:
        msg = f"production floors missed: {missing}"
        raise ProductionFloorError(msg)
    return counts


def write_corpus(cases: Iterable[Generated], directory: Path) -> int:
    """Hash materialized bytes so equal inputs share one corpus entry."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: Final[set[Path]] = set()
    for case in cases:
        path: Final = directory / hashlib.sha256(case.data).hexdigest()
        path.write_bytes(case.data)
        paths.add(path)
    return len(paths)


@dataclass(frozen=True)
class _Bindings:
    values: dict[str, str]
    definitions: set[str]
    references: set[str]
    pool: tuple[str, ...]


@dataclass(frozen=True)
class GenerationBudget:
    """Bound lexical recursion separately from materialized DOM nodes."""

    nodes: int
    steps: int


@dataclass(frozen=True)
class Production:
    """Charge the nodes emitted by fixed markup, including parser-created wrappers."""

    name: str
    symbol: str
    parts: tuple[bytes | Reference | Identifier, ...]
    nodes: int
    origin: str
    height: int = 0


@dataclass(frozen=True)
class Reference:
    """Place child productions at their materialized tree depth."""

    symbol: str
    depth: int = 1


@dataclass(frozen=True)
class Identifier:
    """Reuse allocated names so references bind to their generated definition."""

    name: str
    definition: bool


@dataclass(frozen=True)
class Grammar:
    """Resolve minimum derivations before consuming an input's shared node budget."""

    productions: tuple[Production, ...]
    root: str
    rules: dict[str, tuple[Production, ...]]
    minimum: dict[str, tuple[int, int]]
    identifiers: tuple[str, ...]
    edges: tuple[tuple[Production, int, str, int, int], ...]  # every reference, priced once for _route's relaxation
    materialize: Callable[[Generated], Generated] | None = None


@dataclass(frozen=True)
class Generated:
    """Keep source bytes beside their node costs and executed production trace."""

    data: bytes
    nodes: int
    depth: int
    productions: tuple[str, ...]
    bindings: tuple[tuple[str, str], ...]


class GrammarError(ValueError):
    """Keep invalid grammar definitions outside the input corpus."""


class BudgetError(ValueError):
    """Reject budgets that cannot complete a minimum derivation."""


class ProductionFloorError(ValueError):
    """Treat unused productions as failed generation coverage."""


def html_generate(rng: random.Random, budget: int = 30) -> Generated:
    """Keep the general HTML oracle inside the shared node budget."""
    return generate(_HTML_GRAMMAR, rng, budget)


def html_grammar() -> Grammar:
    """Separate child containers so adjacent text nodes cannot merge."""
    productions: Final = [
        Production(
            "html:root",
            "root",
            (
                b'<div id="',
                Identifier("target", definition=True),
                b'">',
                Reference("node", 2),
                b'<a href="#',
                Identifier("target", definition=False),
                b'">x</a></div>',
            ),
            4,
            _HTML_STANDARD,
            3,
        ),
        Production(
            "html:nested",
            "node",
            (b"<div><span>", Reference("node", 2), b"</span><span>", Reference("node", 2), b"</span></div>"),
            3,
            _HTML_STANDARD,
            1,
        ),
    ]
    productions.extend(
        Production(f"html:text:{index}", "node", (escape(text, quote=False).encode("utf-8"),), 1, _LOCAL_TABLES)
        for index, text in enumerate(_HTML_TEXT)
    )
    for tag in _HTML_TAGS:
        source, nodes, height = _element(tag)
        productions.append(
            Production(
                f"html:tag:{tag}",
                "node",
                (source,),
                nodes,
                _DOMATO_TAGS if tag not in {"svg", "math", "script"} else _HTML_STANDARD,
                height,
            )
        )
    for name in _HTML_ATTRIBUTES:
        for index, value in enumerate(_HTML_VALUES):
            parts, nodes, height = _attribute(name, value, index)
            productions.append(
                Production(f"html:attribute:{name}:{index}", "node", parts, nodes, _LOCAL_TABLES, height)
            )
    return compile_grammar(productions, "root")


def _element(tag: str) -> tuple[bytes, int, int]:
    if source := _CONTEXTS.get(tag):
        return source
    if tag == "template":
        return b"<template></template>", 2, 0
    if tag in _VOID:
        return f"<{tag}>".encode("ascii"), 1, 0
    return f"<{tag}>x</{tag}>".encode("ascii"), 2, 1


def _attribute(name: str, value: str, index: int) -> tuple[tuple[bytes | Reference | Identifier, ...], int, int]:
    tag: Final = {"href": "a", "src": "img", "alt": "img", "name": "input", "type": "input", "value": "input"}.get(
        name, "span"
    )
    encoded: Final = escape(
        _IDENTIFIERS[1 + index % 2] if name == "id" else ("a", "b", "c")[index % 3] if name == "class" else value,
        quote=True,
    ).encode("utf-8")
    parts: Final[tuple[bytes | Reference | Identifier, ...]] = (
        (b"#", Identifier("target", definition=False)) if value == "#f" and name not in {"id", "class"} else (encoded,)
    )
    ending: Final = b">" if tag in _VOID else f">x</{tag}>".encode("ascii")
    return (
        (f'<{tag} {name}="'.encode("ascii"), *parts, b'"' + ending),
        1 if tag in _VOID else 2,
        0 if tag in _VOID else 1,
    )


_IDENTIFIERS: Final = ("x", "y", "z")
_HTML_STANDARD: Final = "https://html.spec.whatwg.org/multipage/parsing.html"
_DOMATO_TAGS: Final = (
    "https://github.com/googleprojectzero/domato/blob/fadff396cc45d521cc594d3e2396e27e887b1963/html_tags.py"
)
_LOCAL_TABLES: Final = "https://github.com/tox-dev/turbohtml/blob/6a8722d7614a69df4b6ca6c8563f62d00f0837c1/tools/fuzz/round_trip_oracles.py"
_CONTEXTS: Final = {
    "table": (b"<table><tbody><tr><td>x</td></tr></tbody></table>", 5, 4),
    "tr": (b"<table><tbody><tr><td>x</td></tr></tbody></table>", 5, 4),
    "td": (b"<table><tbody><tr><td>x</td></tr></tbody></table>", 5, 4),
    "th": (b"<table><tbody><tr><th>x</th></tr></tbody></table>", 5, 4),
    "caption": (b"<table><caption>x</caption></table>", 3, 2),
    "ul": (b"<ul><li>x</li></ul>", 3, 2),
    "ol": (b"<ol><li>x</li></ol>", 3, 2),
    "li": (b"<ul><li>x</li></ul>", 3, 2),
    "dl": (b"<dl><dt>x</dt><dd>x</dd></dl>", 5, 2),
    "dt": (b"<dl><dt>x</dt></dl>", 3, 2),
    "dd": (b"<dl><dd>x</dd></dl>", 3, 2),
    "select": (b"<select><option>x</option></select>", 3, 2),
    "option": (b"<select><option>x</option></select>", 3, 2),
}

_HTML_TAGS: Final = (
    "div", "p", "span", "a", "b", "i", "em", "strong", "code", "pre", "ul", "ol", "li", "table", "tr", "td", "th",
    "h1", "h2", "blockquote", "section", "img", "br", "hr", "input", "label", "select", "option", "textarea", "svg",
    "math", "template", "del", "sup", "script", "style", "title", "dl", "dt", "dd", "caption", "button", "form",
)  # fmt: skip

_VOID: Final = frozenset({"img", "br", "hr", "input"})

_HTML_TEXT: Final = (
    "x", "hello world", "*", "_", "#", "|", "[", "]", "(", ")", "\\", "`", "&amp;", "&lt;", "<", ">", "1.", "-", "+",
    "  ", "\n", "\xa0", "é", "&#0;", "&#x1F600;", "\r\n", "~", "!", "=", "'", '"', "&nbsp;", "---", "```", "> q",
    "1) x", "a|b", "<!--c-->", "]]>", "--", "&#13;", "\t", "**b**", "[l](u)", "<?pi x?>", "\x0c", "&gt", "\U0001f600",
)  # fmt: skip

_HTML_ATTRIBUTES: Final = (
    "id",
    "class",
    "href",
    "src",
    "title",
    "lang",
    "data-x",
    "style",
    "alt",
    "name",
    "type",
    "value",
)

_HTML_VALUES: Final = ("a", "b c", "", "x y z", "&amp;", '"', "'", "1", "#f", "javascript:x", "\n", "a b", "é", "<>")


_HTML_GRAMMAR: Final = html_grammar()

__all__ = [
    "BudgetError",
    "Generated",
    "GenerationBudget",
    "Grammar",
    "GrammarError",
    "Identifier",
    "Production",
    "ProductionFloorError",
    "Reference",
    "assert_production_floors",
    "compile_grammar",
    "generate",
    "generation_sweep",
    "html_generate",
    "html_grammar",
    "main",
    "write_corpus",
]

if __name__ == "__main__":
    raise SystemExit(main())
