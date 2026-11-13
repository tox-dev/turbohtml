"""
Differential check of the phone recognizer model against the ``phonenumbers`` oracle.

For every region and example number the pinned metadata carries, render the number the ways people write it, embed
each in prose, and compare ``phone_model.Recognizer`` with ``phonenumbers.PhoneNumberMatcher`` at the matching
leniency: the spans, the E.164 string and the extension must agree. Differences are sorted into named categories; an
uncategorized difference is a defect in the model.

Usage:  python tools/phone_differential.py --sources DIR [--mode valid|possible] [--regions US,GB] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import phonenumbers
from generate_phone import compile_tables, fetch_sources
from phone_model import Config, Recognizer
from phone_oracle import CONTEXTS, classify, oracle_matches, renderings
from phonenumbers import Leniency

if TYPE_CHECKING:
    from generate_phone import Region


@dataclass(  # simplint: ignore[dataclass-frozen]  # mutable counters; slots trip class-as-data-structure
    slots=True, kw_only=True
)
class _RunState:
    # The settings every case is compared under, and the counts and printed-example budget across all regions.
    leniency: int
    show: int
    category: str
    counts: Counter[str] = field(default_factory=Counter)
    shown: int = 0
    cases: int = 0


def main() -> None:
    # Compare every region's example numbers against the phonenumbers oracle and print the disagreement summary.
    arguments = _parse_args()
    tables = compile_tables(fetch_sources(arguments.sources))
    wanted = set(arguments.regions.split(",")) if arguments.regions else None
    state = _RunState(
        leniency=Leniency.VALID if arguments.mode == "valid" else Leniency.POSSIBLE,
        show=arguments.show,
        category=arguments.category,
    )
    for region_tables in tables.regions:
        region = region_tables.region
        if region.code == "001" or (wanted and region.code not in wanted):
            continue
        _check_region(
            Recognizer(tables, Config(regions=(region.code,), require_valid=arguments.mode == "valid")),
            region,
            arguments,
            state,
        )
        if arguments.limit and state.cases >= arguments.limit:
            break
    print(f"{state.cases} cases: {dict(state.counts)}")
    sys.exit(0 if state.counts.keys() <= {"agree"} else 1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--mode", choices=("valid", "possible"), default="valid")
    parser.add_argument("--regions", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show", type=int, default=25)
    parser.add_argument("--category", default="")
    return parser.parse_args()


def _check_region(recognizer: Recognizer, region: Region, arguments: argparse.Namespace, state: _RunState) -> None:
    for kind, example in region.examples.items():
        try:
            number = phonenumbers.parse(example, region.code)
        except phonenumbers.NumberParseException:
            continue
        for form, rendered in renderings(number, region.code):
            for context in CONTEXTS:
                _check_text(recognizer, region.code, f"{kind} {form}", context.format(rendered), state)
        if arguments.limit and state.cases >= arguments.limit:
            break


def _check_text(recognizer: Recognizer, code: str, label: str, text: str, state: _RunState) -> None:
    state.cases += 1
    ours = {
        (match.start, match.end, match.international_number, match.extension or "")
        for match in recognizer.find_all(text)
    }
    theirs = oracle_matches(text, code, state.leniency)
    category = classify(text, ours, theirs)
    state.counts[category] += 1
    if category == "agree":
        return
    if state.shown < state.show and (not state.category or category == state.category):
        state.shown += 1
        print(f"[{category}] {code} {label}: {text!r}\n    ours={sorted(ours)}\n    oracle={sorted(theirs)}")


if __name__ == "__main__":
    main()
