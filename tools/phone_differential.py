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
from phone_model import Config, Match, Recognizer
from phone_oracle import CONTEXTS, Found, classify, oracle_matches, renderings
from phonenumbers import Leniency

if TYPE_CHECKING:
    from generate_phone import Region


@dataclass
class _RunState:
    """The counts and printed-example budget threaded through every region's cases."""

    counts: Counter[str] = field(default_factory=Counter)
    shown: int = 0
    cases: int = 0


def main() -> None:
    """Compare every region's example numbers against the phonenumbers oracle and print the disagreement summary."""
    arguments = _parse_args()
    tables = compile_tables(fetch_sources(arguments.sources))
    leniency = Leniency.VALID if arguments.mode == "valid" else Leniency.POSSIBLE
    wanted = set(arguments.regions.split(",")) if arguments.regions else None
    state = _RunState()
    for region_tables in tables.regions:
        region = region_tables.region
        if region.code == "001" or (wanted and region.code not in wanted):
            continue
        _check_region(
            Recognizer(tables, Config(regions=(region.code,), require_valid=arguments.mode == "valid")),
            region,
            leniency,
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


def _check_region(
    recognizer: Recognizer, region: Region, leniency: int, arguments: argparse.Namespace, state: _RunState
) -> None:
    for kind, example in region.examples.items():
        try:
            number = phonenumbers.parse(example, region.code)
        except phonenumbers.NumberParseException:
            continue
        for form, rendered in renderings(number, region.code):
            for context in CONTEXTS:
                text = context.format(rendered)
                state.cases += 1
                ours = _model_matches(recognizer.find_all(text))
                theirs = oracle_matches(text, region.code, leniency)
                category = classify(text, ours, theirs)
                state.counts[category] += 1
                if category == "agree":
                    continue
                if state.shown < arguments.show and (not arguments.category or category == arguments.category):
                    state.shown += 1
                    print(
                        f"[{category}] {region.code} {kind} {form}: {text!r}\n"
                        f"    ours={sorted(ours)}\n    oracle={sorted(theirs)}"
                    )
        if arguments.limit and state.cases >= arguments.limit:
            break


def _model_matches(matches: list[Match]) -> Found:
    """Reduce ``matches`` to the comparable shape ``oracle_matches`` also produces."""
    return {(match.start, match.end, match.international_number, match.extension or "") for match in matches}


if __name__ == "__main__":
    main()
