"""Duplicate-attribute detection stays O(n) so a tag with many distinct attribute
names cannot drive the tokenizer quadratic.

Leaving the attribute-name state used to scan every earlier attribute, so a single
tag carrying n distinct names cost O(n^2): ~200 kB of ``a0 a1 a2 ...`` took most of
a second and ~2 MB took tens of seconds, a small-input DoS. The tokenizer now dedupes
through a per-tag hash index; these tests pin the linear cost and the exact
first-occurrence-wins semantics the hash path must preserve.
"""

from __future__ import annotations

import time

from turbohtml import tokenize

# Large enough that the old O(n^2) scan would take tens of seconds even on an
# instrumented build, so a regression cannot slip under the wall-clock ceiling; the
# linear path parses it in well under a second.
_MANY = 60000
# Headroom is ~30x over the linear cost and ~5x under the quadratic one, so ordinary
# CI scheduling jitter cannot flip the result either way.
_BUDGET_SECONDS = 6.0


def _attrs_of(pairs: str) -> list[tuple[str, str]]:
    (tag,) = list(tokenize(f"<a{pairs}>"))
    assert tag.attrs is not None  # a start tag always exposes its attribute list
    return tag.attrs


def test_many_distinct_attributes_parse_in_linear_time() -> None:
    pairs = "".join(f" a{index}" for index in range(_MANY))
    start = time.perf_counter()
    attrs = _attrs_of(pairs)
    assert time.perf_counter() - start < _BUDGET_SECONDS
    assert len(attrs) == _MANY  # every distinct name is kept, none dropped


def test_many_distinct_attributes_keep_their_names_and_order() -> None:
    attrs = _attrs_of("".join(f" a{index}" for index in range(2000)))
    assert [name for name, _ in attrs] == [f"a{index}" for index in range(2000)]


def test_many_duplicate_attributes_collapse_to_the_first() -> None:
    assert _attrs_of(" x" * _MANY) == [("x", "")]  # duplicates dropped early, count never grows


def test_interleaved_duplicates_keep_first_occurrence_order() -> None:
    attrs = _attrs_of(" a b a c b d a")  # repeats reuse the dropped slot, first wins
    assert [name for name, _ in attrs] == ["a", "b", "c", "d"]


def test_hash_index_resets_between_consecutive_large_tags() -> None:
    # Two big tags back to back: the second must not see the first tag's names, so the
    # per-tag epoch reset and the grow that re-seats live entries both have to be right.
    first = "<a" + "".join(f" p{index}" for index in range(1500)) + ">"
    second = "<b" + "".join(f" p{index}" for index in range(1500)) + ">"
    counts = [len(token.attrs) for token in tokenize(first + second) if token.attrs is not None]
    assert counts == [1500, 1500]  # neither tag drops a name
