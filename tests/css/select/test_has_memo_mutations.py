from __future__ import annotations

import contextlib
import copy
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import Element, Range, parse
from turbohtml.cssom import computed_style
from turbohtml.etree import ElementView

if TYPE_CHECKING:
    from collections.abc import Callable

    from turbohtml import Document

_QUERIES: Final[tuple[str, ...]] = ("div:has(p)", "div:has(> span)", "b:has(i)", "span:has(b)")
_TAGS: Final[tuple[str, ...]] = ("div", "span", "b", "i")
_SNIPPETS: Final[tuple[str, ...]] = ("<p>x</p>", "<b><i>y</i></b>", "<span>z</span>", "x")
_CANDIDATES: Final[str] = "body div, body span, body b, body i"
_STEPS: Final[int] = 90


def _pick(walk: _Walk) -> Element:
    return walk.rng.choice(list(walk.doc.select(_CANDIDATES)))


def _tag(walk: _Walk) -> Element:
    return Element(walk.rng.choice(_TAGS))


def _deepen(walk: _Walk) -> None:
    child: Final = _tag(walk)
    walk.last.append(child)
    walk.last = child


def _append(walk: _Walk) -> None:
    _pick(walk).append(_tag(walk))


def _insert(walk: _Walk) -> None:
    target: Final = _pick(walk)
    target.insert(walk.rng.randrange(len(target.children) + 1), _tag(walk))


def _extract_reattach(walk: _Walk) -> None:
    _pick(walk).append(_pick(walk).extract())


def _set_inner_html(walk: _Walk) -> None:
    _pick(walk).set_inner_html(walk.rng.choice(_SNIPPETS))


def _insert_before(walk: _Walk) -> None:
    _pick(walk).insert_before(_tag(walk))


def _replace_with(walk: _Walk) -> None:
    _pick(walk).replace_with(_tag(walk))


def _wrap(walk: _Walk) -> None:
    _pick(walk).wrap(_tag(walk))


def _unwrap(walk: _Walk) -> None:
    _pick(walk).unwrap()


def _detach(walk: _Walk) -> None:
    walk.detached = _pick(walk).extract()


def _copy_attach(walk: _Walk) -> None:
    _pick(walk).append(copy.copy(_pick(walk)))


def _range_insert(walk: _Walk) -> None:
    Range(_pick(walk)).insert_node(_tag(walk))


def _range_extract(walk: _Walk) -> None:
    _pick(walk).append(Range(_pick(walk)).extract_contents())


def _range_delete(walk: _Walk) -> None:
    Range(_pick(walk)).delete_contents()


def _range_surround(walk: _Walk) -> None:
    Range(_pick(walk)).surround_contents(_tag(walk))


def _view_mutate(walk: _Walk) -> None:
    view: Final = ElementView(_pick(walk))
    view.append(ElementView(_tag(walk)))
    view.insert(0, ElementView(_tag(walk)))
    view.remove(view[-1])
    view.replace(view[0], ElementView(_tag(walk)))


_OPERATIONS: Final[tuple[Callable[[_Walk], None], ...]] = (
    _deepen,
    _deepen,
    _append,
    _insert,
    _extract_reattach,
    _detach,
    _set_inner_html,
    _insert_before,
    _replace_with,
    _wrap,
    _unwrap,
    _copy_attach,
    _range_insert,
    _range_extract,
    _range_delete,
    _range_surround,
    _view_mutate,
)


@pytest.mark.parametrize("seed", range(12))
def test_has_memo_results_match_reparse_after_every_mutation(seed: int) -> None:
    assert _mismatches(seed) == []


def _mismatches(seed: int) -> list[str]:
    rng = random.Random(seed)  # ruff:ignore[suspicious-non-cryptographic-random-usage]  # a fixed mutation corpus
    doc: Final = parse("<body><div><span>x</span></div></body>")
    first: Final = doc.select_one("body div")
    assert first is not None
    walk: Final = _Walk(rng=rng, doc=doc, last=first)
    found: list[str] = []
    for step in range(_STEPS):
        if not list(doc.select(_CANDIDATES)):
            body: Final = doc.select_one("body")
            assert body is not None
            body.append(Element("div"))
        operation: Final = rng.choice(_OPERATIONS)
        with contextlib.suppress(IndexError, ValueError, TypeError):
            operation(walk)
        found.extend(
            f"step {step} {label}: live={live} reparsed={expected}"
            for label, live, expected in _comparisons(walk)
            if live != expected
        )
    return found


@dataclass(slots=True, kw_only=True)
class _Walk:
    rng: random.Random
    doc: Document
    last: Element
    detached: Element | None = None


def _comparisons(walk: _Walk) -> list[tuple[str, list[str], list[str]]]:
    reparsed: Final = parse(walk.doc.html)
    body: Final = walk.doc.select_one("body")
    reparsed_body: Final = reparsed.select_one("body")
    assert body is not None
    assert reparsed_body is not None
    detached: Final = walk.detached
    pairs: list[tuple[str, list[str], list[str]]] = []
    for query in _QUERIES:
        pairs.extend((
            (query, [node.html for node in walk.doc.select(query)], [node.html for node in reparsed.select(query)]),
            (
                f"body {query}",
                [node.html for node in body.select(query)],
                [node.html for node in reparsed_body.select(query)],
            ),
        ))
        if detached is not None and detached.parent is None:
            reference_root: Final = parse(f"<body>{detached.html}</body>").select_one("body > *")
            assert reference_root is not None
            pairs.append((
                f"detached {query}",
                [node.html for node in detached.select(query)],
                [node.html for node in reference_root.select(query)],
            ))
    return pairs


def test_computed_style_two_has_rules_on_one_element_later_wins() -> None:
    doc: Final = parse(
        "<html><head><style>div:has(b){color:red}div:has(i){color:blue}</style></head>"
        "<body><div><b></b><i></i></div></body></html>"
    )
    element: Final = doc.select_one("div")
    assert isinstance(element, Element)
    assert computed_style(element)["color"] == "blue"
