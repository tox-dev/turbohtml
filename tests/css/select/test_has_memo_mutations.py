from __future__ import annotations

import contextlib
import copy
import random
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
_STEPS: Final[int] = 90


_CANDIDATES: Final[str] = "body div, body span, body b, body i"


def _pick(rng: random.Random, doc: Document) -> Element:
    return rng.choice(list(doc.select(_CANDIDATES)))


def _deepen(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    child: Final = Element(rng.choice(_TAGS))
    state["last"].append(child)
    state["last"] = child


def _append(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    _pick(rng, doc).append(Element(rng.choice(_TAGS)))


def _insert(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    target: Final = _pick(rng, doc)
    target.insert(rng.randrange(len(target.children) + 1), Element(rng.choice(_TAGS)))


def _extract_reattach(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        _pick(rng, doc).append(node.extract())


def _set_inner_html(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    _pick(rng, doc).set_inner_html(rng.choice(_SNIPPETS))


def _insert_before(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        node.insert_before(Element(rng.choice(_TAGS)))


def _replace_with(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        node.replace_with(Element(rng.choice(_TAGS)))


def _wrap(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        node.wrap(Element(rng.choice(_TAGS)))


def _unwrap(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        node.unwrap()


def _detach(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    node: Final = _pick(rng, doc)
    if node.parent is not None:
        state["detached"] = node.extract()


def _copy_attach(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    _pick(rng, doc).append(copy.copy(_pick(rng, doc)))


def _range_insert(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    Range(_pick(rng, doc)).insert_node(Element(rng.choice(_TAGS)))


def _range_extract(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    _pick(rng, doc).append(Range(_pick(rng, doc)).extract_contents())


def _range_delete(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    Range(_pick(rng, doc)).delete_contents()


def _range_surround(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    Range(_pick(rng, doc)).surround_contents(Element(rng.choice(_TAGS)))


def _view_mutate(rng: random.Random, doc: Document, state: dict[str, Element]) -> None:
    view: Final = ElementView(_pick(rng, doc))
    view.append(ElementView(Element(rng.choice(_TAGS))))
    view.insert(0, ElementView(Element(rng.choice(_TAGS))))
    if len(view) > 1:
        view.remove(view[-1])
        view.replace(view[0], ElementView(Element(rng.choice(_TAGS))))


_OPERATIONS: Final[tuple[Callable[[random.Random, Document, dict[str, Element]], None], ...]] = (
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
    rng = random.Random(seed)
    doc = parse("<body><div><span>x</span></div></body>")
    last: Final = doc.select_one("body div")
    assert last is not None
    state: dict[str, Element] = {"last": last}
    found: list[str] = []
    for step in range(_STEPS):
        if not list(doc.select(_CANDIDATES)):
            body: Final = doc.select_one("body")
            assert body is not None
            body.append(Element("div"))
        operation = rng.choice(_OPERATIONS)
        with contextlib.suppress(IndexError, ValueError, TypeError):
            operation(rng, doc, state)
        found.extend(
            f"step {step} {label}: live={live} reparsed={expected}"
            for label, live, expected in _comparisons(doc, state)
            if live != expected
        )
    return found


def _comparisons(doc: Document, state: dict[str, Element]) -> list[tuple[str, list[str], list[str]]]:
    reparsed: Final = parse(doc.html)
    body: Final = doc.select_one("body")
    reparsed_body: Final = reparsed.select_one("body")
    assert body is not None and reparsed_body is not None
    detached: Final = state.get("detached")
    pairs: list[tuple[str, list[str], list[str]]] = []
    for query in _QUERIES:
        pairs.append((query, [node.html for node in doc.select(query)], [node.html for node in reparsed.select(query)]))
        pairs.append(
            (f"body {query}", [node.html for node in body.select(query)], [node.html for node in reparsed_body.select(query)])
        )
        if detached is not None and detached.parent is None:
            reference_root: Final = parse(f"<body>{detached.html}</body>").select_one("body > *")
            assert reference_root is not None
            pairs.append(
                (
                    f"detached {query}",
                    [node.html for node in detached.select(query)],
                    [node.html for node in reference_root.select(query)],
                )
            )
    return pairs


def test_computed_style_two_has_rules_on_one_element_later_wins() -> None:
    doc: Final = parse(
        "<html><head><style>div:has(b){color:red}div:has(i){color:blue}</style></head>"
        "<body><div><b></b><i></i></div></body></html>"
    )
    element: Final = doc.select_one("div")
    assert isinstance(element, Element)
    assert computed_style(element)["color"] == "blue"
