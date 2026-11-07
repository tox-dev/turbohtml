"""Retained iterator cursors must follow edits without replaying stale nodes."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element, Node, NodeFilter, NodeIterator

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def iterator_sequence_generate(rng: random.Random) -> str:
    # Keep five wrappers and one removal per generated program.
    return f"{rng.choice(tuple(_PROGRAMS))}:g{rng.randrange(10000)}"


def iterator_sequence_seeds() -> list[str]:
    # Cover cursor directions and ancestor removal before generation.
    return [f"{program}:g{index}" for program in _PROGRAMS for index in range(30)]


def iterator_sequence_controls() -> dict[str, bool]:
    # Require exact reference states and traversal order before fuzzing.
    return {
        "missing extraction": iterator_sequence_check("after:a", lambda _program, _name: "[]") is not None,
        "stale reference": iterator_sequence_check(
            "after:a",
            lambda _program, _name: json.dumps((
                [0, 1, 2, 3],
                [],
                (3, False),
                4,
                4,
                ("c", True),
                [4, 3],
                "<a><a><b></b></a><d></d><c></c></a>",
            )),
        )
        is not None,
        "wrong traversal order": iterator_sequence_check(
            "before:a",
            lambda _program, _name: json.dumps((
                [0, 1, 2, 3],
                [3],
                (4, True),
                4,
                4,
                ("c", True),
                [3, 4],
                "<a><a><b></b></a><d></d><c></c></a>",
            )),
        )
        is not None,
    }


def iterator_sequence_check(case: str, run: Callable[[str, str], str] | None = None) -> str | None:
    # Pin cursor adjustment and reinsertion against literal traversal traces.
    program, separator, name = case.partition(":")
    if not separator or program not in _PROGRAMS or re.fullmatch(r"(?:[ab]|div|g[a-z0-9]{0,15})", name) is None:
        raise UnsupportedIteratorCaseError(case)
    return None if (run or _run)(program, name) == _expected(program, name) else "iterator sequence differs from model"


class UnsupportedIteratorCaseError(ValueError):
    # Separate malformed programs from failures during valid traversal.
    ...


def _run(program: str, name: str) -> str:
    registers: Final = (Element(name), Element("a"), Element("b"), Element("c"), Element("d"))
    root: Final = registers[0]
    registers[1].append(registers[2])
    for child in (registers[1], *registers[3:]):
        root.append(child)
    iterator: Final = NodeIterator(root, what_to_show=NodeFilter.SHOW_ELEMENT)
    forward, back, removed_index = _PROGRAMS[program]
    visited: Final = [_identity(iterator.next_node(), registers) for _ in range(forward)]
    rewind: Final = [_identity(iterator.previous_node(), registers) for _ in range(back)]
    removed: Final = registers[removed_index]
    removed.extract()
    adjusted: Final = (_identity(iterator.reference_node, registers), iterator.pointer_before_reference_node)
    next_node: Final = _identity(iterator.next_node(), registers)
    previous_node: Final = _identity(iterator.previous_node(), registers)
    detached: Final = (removed.tag, removed.parent is None)
    root.append(removed)
    remaining: Final = [_identity(node, registers) for node in iterator]
    return json.dumps((visited, rewind, adjusted, next_node, previous_node, detached, remaining, root.html))


def _identity(node: Node | None, registers: tuple[Element, ...]) -> int | None:
    return None if node is None else registers.index(cast("Element", node))


def _expected(program: str, name: str) -> str:
    traces: Final = {
        "after": ([0, 1, 2, 3], [], (2, False), 4, 4, ("c", True), [4, 3], "<a><b></b></a><d></d><c></c>"),
        "before": ([0, 1, 2, 3], [3], (4, True), 4, 4, ("c", True), [4, 3], "<a><b></b></a><d></d><c></c>"),
        "last": ([0, 1, 2, 3, 4], [4], (3, False), None, 3, ("d", True), [3, 4], "<a><b></b></a><c></c><d></d>"),
        "ancestor": ([0, 1, 2], [], (0, False), 3, 3, ("a", True), [3, 4, 1, 2], "<c></c><d></d><a><b></b></a>"),
    }
    *trace, children = traces[program]
    return json.dumps((*trace, f"<{name}>{children}</{name}>"))


_PROGRAMS: Final[dict[str, tuple[int, int, int]]] = {
    "after": (4, 0, 3),
    "before": (4, 1, 3),
    "last": (5, 1, 4),
    "ancestor": (3, 0, 1),
}

__all__ = [
    "UnsupportedIteratorCaseError",
    "iterator_sequence_check",
    "iterator_sequence_controls",
    "iterator_sequence_generate",
    "iterator_sequence_seeds",
]
