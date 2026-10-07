"""Keep generation metadata outside byte files consumed by libFuzzer."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

from .atheris_targets import public_targets
from .css_structure_generators import css_grammar
from .encoding_structure_generators import encoding_check, encoding_grammar, encoding_profile
from .html_structure_generators import html_document, html_grammar_complete
from .markdown_structure_generators import markdown_grammar
from .structure_generators import GenerationBudget, generate, generation_sweep
from .xml_structure_generators import xml_grammar

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from types import FunctionType

    from .structure_generators import Generated, Grammar

__all__ = ["CorpusEntry", "CorpusManifest", "CorpusProfile", "RejectedEntry", "main", "write_corpora"]

CorpusProfile = Literal[
    "html",
    "xml",
    "css-stylesheet",
    "css-declaration",
    "css-selector",
    "javascript",
    "markdown-source",
    "markdown-html",
    "encoding",
]


def main(argv: Sequence[str] | None = None) -> int:
    """Admit domain bytes through the same callbacks used by coverage-guided consumers."""
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--budget", type=int, default=30)
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--steps", type=int, default=256)
    parser.add_argument(
        "--profile",
        choices=("html", "xml", "css-stylesheet", "markdown-source", "markdown-html", "encoding"),
        default="html",
    )
    arguments: Final = parser.parse_args(argv)
    grammar: Final = (
        html_grammar_complete()
        if arguments.profile == "html"
        else xml_grammar()
        if arguments.profile == "xml"
        else css_grammar()
        if arguments.profile == "css-stylesheet"
        else encoding_grammar()
        if arguments.profile == "encoding"
        else markdown_grammar(html=arguments.profile == "markdown-html")
    )
    generator: Final = random.Random(arguments.seed)
    budget: Final = GenerationBudget(arguments.budget, arguments.steps)
    cases: Final = (
        *(generation_sweep(grammar, budget=budget) if arguments.sweep else ()),
        *(generate(grammar, generator, budget) for _ in range(arguments.count)),
    )
    write_corpora(cases, grammar, arguments.output, arguments.profile)
    return 0


def write_corpora(
    cases: Iterable[Generated], grammar: Grammar, directory: Path, profile: CorpusProfile
) -> CorpusManifest:
    """Preflight the executable consumer before admitting exact bytes to its corpus."""
    targets: Final = {target.name: target for target in public_targets()}
    origins: Final = {production.name: production.origin for production in grammar.productions}
    entries: Final[list[CorpusEntry]] = []
    rejected: Final[list[RejectedEntry]] = []
    admitted: Final[set[tuple[str, str]]] = set()
    directory.mkdir(parents=True, exist_ok=True)
    for case in cases:
        digest: Final = hashlib.sha256(case.data).hexdigest()
        for name in _routes(case, profile):
            context: Final = encoding_profile(case) if profile == "encoding" else None
            target: Final = (
                next(target for target in public_targets(context.encoding, sniff=context.sniff) if target.name == name)
                if context
                else targets[name]
            )
            if context and (error := encoding_check(case)):
                raise AssertionError(error)
            try:
                target.callback(case.data)
            except target.exceptions as error:
                rejected.append(RejectedEntry(name, digest, type(error).__name__))
                continue
            subdirectory: Final = (
                f"{name}/{context.encoding}-{'sniff' if context.sniff else 'fixed'}" if context else name
            )
            destination: Final = directory / subdirectory / digest
            if (subdirectory, digest) not in admitted:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(case.data)
                admitted.add((subdirectory, digest))
            entries.append(
                CorpusEntry(
                    name,
                    target.exports,
                    f"{target.callback.__module__}.{cast('FunctionType', target.callback).__qualname__}",
                    str(destination.relative_to(directory)),
                    digest,
                    case.nodes,
                    case.depth,
                    tuple((production, origins[production]) for production in case.productions),
                    case.bindings,
                    context.encoding if context else None,
                    context.sniff if context else False,
                )
            )
    manifest: Final = CorpusManifest(tuple(entries), tuple(rejected))
    (directory / "manifest.json").write_text(json.dumps(asdict(manifest), indent=2) + "\n", encoding="utf-8")
    return manifest


def _routes(case: Generated, profile: CorpusProfile) -> tuple[str, ...]:
    if profile == "html":
        return (
            ("html-document", "html-incremental", "html-tokenizer")
            if html_document(case)
            else ("html-fragment", "html-tokenizer")
        )
    return {
        "javascript": ("javascript",),
        "markdown-source": ("markdown-source",),
        "markdown-html": ("markdown-html",),
        "encoding": ("encoding-bytes",),
        "xml": ("xml-schema",),
        "css-stylesheet": ("css-stylesheet",),
        "css-declaration": ("css-object-model",),
        "css-selector": ("css-translate",),
    }[profile]


@dataclass(frozen=True)
class CorpusEntry:
    """Link exact corpus bytes to their generating trace and executable owner."""

    target: str
    exports: tuple[str, ...]
    callback: str
    file: str
    sha256: str
    generation_nodes: int
    generation_depth: int
    productions: tuple[tuple[str, str], ...]
    bindings: tuple[tuple[str, str], ...]
    encoding: str | None = None
    sniff: bool = False


@dataclass(frozen=True)
class RejectedEntry:
    """Retain documented input rejection outside accepted target directories."""

    target: str
    sha256: str
    exception: str


@dataclass(frozen=True)
class CorpusManifest:
    """Keep accepted provenance and rejected-input evidence beside the corpus."""

    entries: tuple[CorpusEntry, ...]
    rejected: tuple[RejectedEntry, ...]


if __name__ == "__main__":
    raise SystemExit(main())
