"""Literal observer traces detect missed edits and reentrant delivery mistakes."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Final, TypeAlias, cast

from turbohtml import Element, Node
from turbohtml.mutations import MutationObserver, MutationRecord

if TYPE_CHECKING:
    import random
    from collections.abc import Callable


def observer_sequence_generate(rng: random.Random) -> str:
    # Bound retained references and edits before invoking callbacks.
    return f"{rng.choice(_PROGRAMS)}:g{rng.randrange(10000)}"


def observer_sequence_seeds() -> list[str]:
    # Exercise each bounded program before random generation.
    return [f"{program}:g{index}" for program in _PROGRAMS for index in range(30)]


def observer_sequence_controls() -> dict[str, bool]:
    # Require exact edits and callback outcomes before accepting random programs.
    added: Final[_Record] = ("childList", 0, (1,), (), None, None, None, None)
    return {
        "missing edit": observer_sequence_check(
            "reuse1:a", lambda _program, _name: json.dumps(("<div></div>", True, [], [], [], None))
        )
        is not None,
        "missing callback": observer_sequence_check(
            "nested:a", lambda _program, _name: json.dumps(("<div><a></a></div>", True, [[added]], [], [], None))
        )
        is not None,
        "swallowed callback error": observer_sequence_check(
            "error:a", lambda _program, _name: json.dumps(("<div><a></a></div>", True, [[added]], [[added]], [], None))
        )
        is not None,
    }


def observer_sequence_check(case: str, run: Callable[[str, str], str] | None = None) -> str | None:
    # Retain detached wrappers and pin callback batches against an independent model.
    program, separator, name = case.partition(":")
    if not separator or program not in _PROGRAMS or _OBSERVER_SEQUENCE_CHECK_PATTERN.fullmatch(name) is None:
        raise UnsupportedObserverCaseError(case)
    return None if (run or _run)(program, name) == _expected(program, name) else "observer sequence differs from model"


class UnsupportedObserverCaseError(ValueError):
    # Keep malformed programs separate from failures during valid DOM operations.
    ...


def _run(program: str, name: str) -> str:
    root: Final = Element("div")
    first: Final = Element(name)
    second: Final = Element("b")
    registers: Final = (root, first, second)
    batches: Final[list[list[_Record]]] = []
    callbacks: Final[list[list[_Record]]] = []

    def callback(records: list[MutationRecord], observer: MutationObserver) -> None:
        callbacks.append(_records(records, registers))
        if program == "error":
            message: Final = "observer callback failed"
            raise _ObserverCallbackError(message)
        if len(callbacks) == 1:
            root.append(second)
            observer.deliver()

    observer: Final = MutationObserver(callback if program in {"nested", "error"} else None)
    observer.observe(root, child_list=True)
    root.append(first)
    alive = first.tag == name and first.parent == root
    error: str | None = None
    if program.startswith("reuse"):
        batches.append(_records(observer.take_records(), registers))
        for _ in range(int(program[-1])):
            first.extract()
            alive = alive and first.tag == name and first.parent is None
            batches.append(_records(observer.take_records(), registers))
            root.append(first)
            alive = alive and first.parent == root
            batches.append(_records(observer.take_records(), registers))
    elif program == "replace":
        batches.append(_records(observer.take_records(), registers))
        first.replace_with(second)
        alive = alive and first.tag == name and first.parent is None and second.parent == root
        batches.append(_records(observer.take_records(), registers))
    else:
        try:
            batches.append(_records(observer.deliver(), registers))
        except _ObserverCallbackError as caught:
            error = str(caught)
    return json.dumps((root.html, alive, batches, callbacks, _records(observer.take_records(), registers), error))


def _records(records: list[MutationRecord], registers: tuple[Element, Element, Element]) -> list[_Record]:
    return [
        (
            record.type,
            _register(record.target, registers),
            tuple(_register(node, registers) for node in record.added_nodes),
            tuple(_register(node, registers) for node in record.removed_nodes),
            _register(record.previous_sibling, registers),
            _register(record.next_sibling, registers),
            record.attribute_name,
            record.old_value,
        )
        for record in records
    ]


def _register(node: Node | None, registers: tuple[Element, Element, Element]) -> int | None:
    return None if node is None else registers.index(cast("Element", node))


class _ObserverCallbackError(RuntimeError):
    # Distinguish the expected callback error from an unrelated DOM failure.
    ...


def _expected(program: str, name: str) -> str:
    added: Final[_Record] = ("childList", 0, (1,), (), None, None, None, None)
    if program.startswith("reuse"):
        removed: Final[_Record] = ("childList", 0, (), (1,), None, None, None, None)
        return json.dumps((
            f"<div><{name}></{name}></div>",
            True,
            [[added], *([[removed], [added]] * int(program[-1]))],
            [],
            [],
            None,
        ))
    if program == "replace":
        inserted: Final[_Record] = ("childList", 0, (2,), (), None, 1, None, None)
        removed_old: Final[_Record] = ("childList", 0, (), (1,), 2, None, None, None)
        return json.dumps(("<div><b></b></div>", True, [[added], [inserted, removed_old]], [], [], None))
    if program == "nested":
        nested: Final[_Record] = ("childList", 0, (2,), (), 1, None, None, None)
        return json.dumps((f"<div><{name}></{name}><b></b></div>", True, [[added]], [[added], [nested]], [], None))
    return json.dumps((f"<div><{name}></{name}></div>", True, [], [[added]], [], "observer callback failed"))


_OBSERVER_SEQUENCE_CHECK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?#a, b, div, or a g followed by up to 15 letters or digits)(?:[ab]|div|g[a-z0-9]{0,15})"
)


_Record: TypeAlias = tuple[
    str, int | None, tuple[int | None, ...], tuple[int | None, ...], int | None, int | None, str | None, str | None
]
_PROGRAMS: Final[tuple[str, ...]] = ("reuse1", "reuse2", "reuse3", "replace", "nested", "error")


__all__ = [
    "UnsupportedObserverCaseError",
    "observer_sequence_check",
    "observer_sequence_controls",
    "observer_sequence_generate",
    "observer_sequence_seeds",
]
