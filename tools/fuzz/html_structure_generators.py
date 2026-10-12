"""Keep table alternatives inside complete elements so attributes consume no fictitious DOM nodes."""

from __future__ import annotations

import argparse
import random
import re
from collections import deque
from dataclasses import dataclass
from functools import cache
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeAlias

from fuzz.html_generation_tables import (
    ATTRIBUTE_VALUE_RULES,
    COMMON_RULES,
    HTML_RULES,
    HTML_TAGS,
    SOURCE_URLS,
    TAG_ATTRIBUTE_RULES,
    TableRule,
)
from fuzz.structure_generators import (
    BudgetError,
    Generated,
    Grammar,
    GrammarError,
    Identifier,
    Production,
    ProductionFloorError,
    compile_grammar,
    generate,
    generation_sweep,
    html_grammar,
    write_corpus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Export bytes that the coverage-guided lane can consume directly."""
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
        cases: Final = (
            generation_sweep(_GRAMMAR, args.count, args.budget)
            if args.sweep
            else tuple(html_generate(random.Random(args.seed + index), args.budget) for index in range(args.count))
        )
    except (GrammarError, BudgetError, ProductionFloorError) as error:
        parser.error(str(error))
    write_corpus(cases, args.output)
    return 0


def html_generate(rng: random.Random, budget: int = 30) -> Generated:
    """Use one budget across tags, attributes and parser-created wrappers."""
    return generate(_GRAMMAR, rng, budget)


def html_grammar_complete(vocabulary: HtmlVocabulary | None = None) -> Grammar:
    """Retain table alternatives separately so finite sweeps expose omitted rows."""
    vocabulary = _VOCABULARY if vocabulary is None else vocabulary
    productions: Final = list(html_grammar().productions)
    values: Final = _Values((*vocabulary.common, *vocabulary.html, *vocabulary.values, *vocabulary.tag_attributes))
    attributes: Final = tuple(rule for rule in vocabulary.html if _ATTRIBUTE.fullmatch(rule[2]))
    for tag in vocabulary.tags:
        productions.append(
            _element_production(
                f"html:domato:tag:{tag}",
                tag,
                (),
                _TAG_ORIGINS[tag] if vocabulary is _VOCABULARY else vocabulary.origin,
            )
        )
    for rule in attributes:
        productions.append(
            _element_production(
                f"html:domato:attribute:{rule[0]}",
                "span",
                values.expand(rule[2]),
                _origin(vocabulary, "html.txt", rule[0]),
            )
        )
    for rule in vocabulary.values:
        owner, route = values.owner(attributes, rule)
        productions.append(
            _element_production(
                f"html:domato:value:{rule[0]}",
                "span",
                values.expand(owner[2], route),
                _origin(vocabulary, "attributevalues.txt", rule[0]),
            )
        )
    for rule in vocabulary.tag_attributes:
        tag: Final = rule[1].removesuffix("_attributes").removesuffix("_attribute")
        if tag not in vocabulary.tags:
            msg = f"tag attribute has no tag materialization: {tag}"
            raise GrammarError(msg)
        productions.append(
            _element_production(
                f"html:domato:tag-attribute:{rule[0]}",
                tag,
                values.expand(rule[2]),
                _origin(vocabulary, "tagattributes.txt", rule[0]),
            )
        )
    productions.extend(_hostile_productions())
    return compile_grammar(productions, "root")


def _origin(vocabulary: HtmlVocabulary, filename: str, line: int) -> str:
    return f"{SOURCE_URLS[filename]}#L{line}" if vocabulary is _VOCABULARY else vocabulary.origin


def html_document(case: Generated) -> bool:
    """Use document parsing for profiles whose tags are ignored in a div fragment."""
    return any(name.startswith("html:document:") for name in case.productions)


def _element_production(name: str, tag: str, attributes: Parts, origin: str) -> Production:
    if tag in {"html", "head", "body"}:
        parts: Final = (
            b'<html id="',
            Identifier("target", definition=True),
            b'"',
            *(attributes if tag == "html" else ()),
            b"><head",
            *(attributes if tag == "head" else ()),
            b"><title>x</title></head><body",
            *(attributes if tag == "body" else ()),
            b'><a href="#',
            Identifier("target", definition=False),
            b'">x</a></body></html>',
        )
        return Production("html:document:" + name, "root", parts, 8, origin, 4)
    if tag in {"frame", "frameset"}:
        parts = (
            b'<frameset id="',
            Identifier("target", definition=True),
            b'"',
            *(attributes if tag == "frameset" else ()),
            b"><frame",
            *(attributes if tag == "frame" else ()),
            b' src="#',
            Identifier("target", definition=False),
            b'"></frameset>',
        )
        return Production("html:document:" + name, "root", parts, 5, origin, 3)
    if tag == "plaintext":
        return Production(
            name,
            "root",
            (
                b'<div id="',
                Identifier("target", definition=True),
                b'"><a href="#',
                Identifier("target", definition=False),
                b'">x</a><plaintext',
                *attributes,
                b">x",
            ),
            6,
            origin,
            3,
        )
    prefix, suffix, nodes, height = _element_shape(tag)
    return Production(name, "node", (prefix, *attributes, suffix), nodes, origin, height)


def _element_shape(tag: str) -> tuple[bytes, bytes, int, int]:
    if shape := _SHAPES.get(tag):
        return shape
    if tag in _TABLE_SECTIONS:
        return b"<table><" + tag.encode("ascii"), b"><tr><td>x</td></tr></" + tag.encode("ascii") + b"></table>", 5, 4
    if tag in {"td", "th"}:
        return (
            b"<table><tbody><tr><" + tag.encode("ascii"),
            b">x</" + tag.encode("ascii") + b"></tr></tbody></table>",
            5,
            4,
        )
    if tag in _VOID:
        return b"<" + tag.encode("ascii"), b">", 1, 0
    return b"<" + tag.encode("ascii"), b">x</" + tag.encode("ascii") + b">", 2, 1


def _hostile_productions() -> tuple[Production, ...]:
    return (
        Production(
            "html:hostile:formatting", "node", (b"<b><i>x</b>y</i>",), 5, _STANDARD + "#adoption-agency-algorithm", 2
        ),
        Production(
            "html:hostile:near-miss",
            "node",
            (b"<span>x</spna><b>y</b></span>",),
            4,
            _STANDARD + "#parsing-main-inbody",
            2,
        ),
        *(
            Production(
                f"html:foreign:svg:{tag}",
                "node",
                (f"<svg><{tag}><p>x</p></{tag}><circle/></svg>".encode("ascii"),),
                5,
                _STANDARD + "#html-integration-point",
                3,
            )
            for tag in ("foreignObject", "desc", "title")
        ),
    )


class _Values:
    def __init__(self, rules: Sequence[TableRule]) -> None:
        self.rules: Final[dict[str, list[TableRule]]] = {}
        for rule in rules:
            self.rules.setdefault(rule[1], []).append(rule)
        self.minimum: Final[dict[str, TableRule]] = {}
        costs: Final[dict[str, int]] = {}
        changed = True
        while changed:
            changed = False
            for symbol, alternatives in self.rules.items():
                if symbol in _PRIMITIVES:
                    continue
                for rule in alternatives:
                    children: Final = [item for item in _symbols(rule[2]) if item not in _PRIMITIVES]
                    if all(child in costs for child in children):
                        cost: Final = 1 + sum(costs[child] for child in children)
                        if symbol not in costs or cost < costs[symbol]:
                            costs[symbol] = cost
                            self.minimum[symbol] = rule
                            changed = True

    def owner(self, attributes: Sequence[TableRule], target: TableRule) -> tuple[TableRule, tuple[TableRule, ...]]:
        for attribute in attributes:
            pending: Final[deque[tuple[str, tuple[TableRule, ...]]]] = deque(
                (symbol, ()) for symbol in _symbols(attribute[2])
            )
            visited: Final[set[str]] = set()
            while pending:
                symbol, path = pending.popleft()
                if symbol == target[1]:
                    return attribute, (*path, target)
                if symbol not in visited and symbol not in _PRIMITIVES:
                    visited.add(symbol)
                    pending.extend(
                        (child, (*path, rule)) for rule in self.rules.get(symbol, ()) for child in _symbols(rule[2])
                    )
        msg = f"value alternative has no emitting attribute owner: {target[1]} at line {target[0]}"
        raise GrammarError(msg)

    def expand(self, source: str, route: tuple[TableRule, ...] = ()) -> Parts:
        if attribute := _ATTRIBUTE.fullmatch(source):
            return (
                f' {attribute[1]}="'.encode("ascii"),
                *(
                    escape(part.decode("utf-8"), quote=True).encode("utf-8") if isinstance(part, bytes) else part
                    for part in self.expand(attribute[2], route)
                ),
                b'"',
            )
        parts: Final[list[bytes | Identifier]] = []
        position = 0
        for token in _TOKEN.finditer(source):
            parts.extend(_literal(source[position : token.start()]))
            symbol, *parameters = token[1].split()
            if symbol in _PRIMITIVES:
                parts.extend(_primitive(symbol, parameters))
            else:
                forced: Final = bool(route and route[0][1] == symbol)
                if (chosen := route[0] if forced else self.minimum.get(symbol)) is None:
                    msg = f"unproductive value symbol: {symbol}"
                    raise GrammarError(msg)
                parts.extend(self.expand(chosen[2], route[1:] if forced else ()))
            position = token.end()
        parts.extend(_literal(source[position:]))
        return tuple(parts)


@cache  # owner() re-reads the same value sources for every one of the ~1000 value alternatives
def _symbols(source: str) -> tuple[str, ...]:
    return tuple(token[1].split()[0] for token in _TOKEN.finditer(source))


def _literal(source: str) -> Parts:
    return (source.encode("utf-8"),) if source else ()


def _primitive(symbol: str, parameters: Sequence[str]) -> Parts:
    if symbol == "elementid":
        return (Identifier("target", definition=False),)
    if symbol == "class":
        return (b"a",)
    if symbol == "import":
        if tuple(parameters) != ("from=cssgrammar", "symbol=declaration5"):
            msg = f"unsupported grammar import: {parameters}"
            raise GrammarError(msg)
        return (b"color: red; color: red; color: red; color: red; color: red",)
    if symbol in {"int", "float"}:
        return (
            next((item.removeprefix("min=") for item in parameters if item.startswith("min=")), "0").encode("ascii"),
        )
    if symbol == "char":
        return (
            chr(int(next((item.removeprefix("min=") for item in parameters if item.startswith("min=")), "120"))).encode(
                "utf-8"
            ),
        )
    return (_PRIMITIVES[symbol],)


@dataclass(frozen=True)
class HtmlVocabulary:
    """Keep source alternatives explicit when a pinned table is updated."""

    tags: tuple[str, ...]
    html: tuple[TableRule, ...]
    values: tuple[TableRule, ...]
    tag_attributes: tuple[TableRule, ...]
    common: tuple[TableRule, ...] = ()
    origin: str = "supplied HTML vocabulary"


Parts: TypeAlias = tuple[bytes | Identifier, ...]

_STANDARD: Final = "https://html.spec.whatwg.org/multipage/parsing.html"
_TOKEN: Final = re.compile(r"<([^<>]+)>")
_ATTRIBUTE: Final = re.compile(r'([a-zA-Z][a-zA-Z0-9:_-]*)="([^"]*)"')
_TABLE_SECTIONS: Final = frozenset({"tbody", "thead", "tfoot"})
_SHAPES: Final = {
    "table": (b"<table", b"><tbody><tr><td>x</td></tr></tbody></table>", 5, 4),
    "tr": (b"<table><tbody><tr", b"><td>x</td></tr></tbody></table>", 5, 4),
    "col": (b"<table><colgroup><col", b"></colgroup></table>", 3, 2),
    "colgroup": (b"<table><colgroup", b"><col></colgroup></table>", 3, 2),
    "caption": (b"<table><caption", b">x</caption></table>", 3, 2),
    "option": (b"<select><option", b">x</option></select>", 3, 2),
    "optgroup": (b"<select><optgroup", b"><option>x</option></optgroup></select>", 4, 3),
    "template": (b"<template", b"></template>", 2, 0),
}
_VOID: Final = frozenset({
    "area",
    "base",
    "basefont",
    "bgsound",
    "br",
    "col",
    "embed",
    "hr",
    "image",
    "img",
    "input",
    "keygen",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
})
_PRIMITIVES: Final = {
    "elementid": b"x",
    "class": b"a",
    "import": b"",
    "int": b"0",
    "float": b"0",
    "char": b"x",
    "htmlsafestring": b"x",
    "string": b"x",
    "hex": b"0",
    "lt": b"<",
    "gt": b">",
    "hash": b"#",
    "space": b" ",
    "cr": b"\r",
    "lf": b"\n",
}

_TAG_ORIGINS: Final = {
    tag: f"{SOURCE_URLS['common.txt']}#L{line}" for line, symbol, tag in COMMON_RULES if symbol == "tagname"
}
_VOCABULARY: Final = HtmlVocabulary(HTML_TAGS, HTML_RULES, ATTRIBUTE_VALUE_RULES, TAG_ATTRIBUTE_RULES, COMMON_RULES)
_GRAMMAR: Final = html_grammar_complete()

__all__ = ["HtmlVocabulary", "html_document", "html_generate", "html_grammar_complete", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
