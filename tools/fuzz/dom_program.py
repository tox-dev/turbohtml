"""Byte programs preserve live typed registers without lifecycle opcodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from turbohtml import Element

if TYPE_CHECKING:
    import random
    from collections.abc import Callable

__all__: Final = [
    "UnsupportedDomProgramError",
    "dom_program_check",
    "dom_program_controls",
    "dom_program_generate",
    "dom_program_seeds",
    "run_dom_program",
]

_MAX_BYTES: Final = 1024
_MAX_STEPS: Final = 64
_MAX_NODES: Final = 16
_TAGS: Final = ("p", "span", "section", "div")


class UnsupportedDomProgramError(ValueError):
    """Reject unsupported encodings before allocating register state."""


def dom_program_check(text: str, run: Callable[[bytes], tuple[str, ...]] | None = None) -> str | None:
    """Keep the expected state independent of DOM mutation methods."""
    if len(text) > _MAX_BYTES * 2:
        msg: Final = "DOM program exceeds the input limit"
        raise UnsupportedDomProgramError(msg)
    try:
        program: Final = bytes.fromhex(text)
    except ValueError as error:
        msg: Final = "DOM program must contain hexadecimal bytes"
        raise UnsupportedDomProgramError(msg) from error
    return (
        None if (run or run_dom_program)(program) == _expected(program) else "DOM program differs from its value model"
    )


def dom_program_generate(rng: random.Random) -> str:
    """Seed occupied registers before random mutation opcodes."""
    prefix: Final = bytes((0, 0, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0))
    return (prefix + bytes(rng.randrange(256) for _ in range(rng.randrange(1, 49) * 4))).hex()


def dom_program_seeds() -> list[str]:
    """Ensure every supported opcode executes with live operands."""
    prefix: Final = bytes((0, 0, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0))
    return [
        (prefix + bytes((opcode, operand, operand + 1, operand + 2))).hex()
        for opcode in range(10)
        for operand in range(10)
    ]


def dom_program_controls() -> dict[str, bool]:
    """Require a wrong-result callback to fail the registered checker."""
    return {"missing operation": dom_program_check(dom_program_seeds()[1], lambda _program: ()) is not None}


def run_dom_program(program: bytes) -> tuple[str, ...]:
    """Bound state growth while preserving public live-node operands."""
    root: Final = Element("main")
    nodes: Final[list[Element]] = []
    strings: Final = ["", "alpha", "beta", "gamma"]
    integers: Final = [0, 1, 2, 3]
    trace: Final[list[str]] = [_observe(root, nodes)]
    for opcode, first, second, third in _instructions(program):
        if opcode == 8:
            strings[first % 4] = chr(97 + second % 26) * (third % 9)
        elif opcode == 9:
            integers[first % 4] = second
        elif opcode == 0:
            if len(nodes) < _MAX_NODES:
                node: Final = Element(_TAGS[first % len(_TAGS)])
                root.append(node)
                nodes.append(node)
        elif nodes:
            _apply(root, nodes[first % len(nodes)], opcode, strings[second % 4], integers[third % 4])
        trace.append(_observe(root, nodes))
    return tuple(trace)


def _instructions(program: bytes) -> list[tuple[int, int, int, int]]:
    bounded: Final = program[:_MAX_BYTES]
    return [
        (bounded[index] % 10, bounded[index + 1], bounded[index + 2], bounded[index + 3])
        for index in range(0, min(len(bounded) - len(bounded) % 4, _MAX_STEPS * 4), 4)
    ]


def _apply(root: Element, node: Element, opcode: int, text: str, integer: int) -> None:
    if opcode == 1:
        node.tag = _TAGS[integer % len(_TAGS)]
    elif opcode == 2:
        node.text = text
    elif opcode == 3:
        node.attrs["data-x"] = text
    elif opcode == 4:
        if "data-x" in node.attrs:
            del node.attrs["data-x"]
    elif opcode == 5:
        root.append(node)
    elif opcode == 6:
        root.insert(integer % (len(root.children) + 1), node)


def _observe(root: Element, nodes: list[Element]) -> str:
    children: Final = cast("list[Element]", list(root.children))
    return json.dumps((
        root.html,
        [
            (
                node.tag,
                node.text,
                node.attrs.get("data-x"),
                len(node.attrs),
                node.parent == root,
                node.previous_sibling == (children[index - 1] if index else None),
                node.next_sibling == (children[index + 1] if index + 1 < len(children) else None),
                nodes.index(node),
            )
            for index, node in enumerate(children)
        ],
    ))


def _expected(program: bytes) -> tuple[str, ...]:
    nodes: Final[list[_Item]] = []
    order: Final[list[_Item]] = []
    strings: Final = ["", "alpha", "beta", "gamma"]
    integers: Final = [0, 1, 2, 3]
    trace: Final = [_model_snapshot(order, nodes)]
    for opcode, first, second, third in _instructions(program):
        if opcode == 8:
            strings[first % 4] = chr(97 + second % 26) * (third % 9)
        elif opcode == 9:
            integers[first % 4] = second
        elif opcode == 0:
            if len(nodes) < _MAX_NODES:
                item: Final = _Item(_TAGS[first % len(_TAGS)])
                nodes.append(item)
                order.append(item)
        elif nodes:
            _model_apply(order, nodes[first % len(nodes)], opcode, strings[second % 4], integers[third % 4])
        trace.append(_model_snapshot(order, nodes))
    return tuple(trace)


def _model_apply(order: list[_Item], item: _Item, opcode: int, text: str, integer: int) -> None:
    if opcode == 1:
        item.tag = _TAGS[integer % len(_TAGS)]
    elif opcode == 2:
        item.text = text
    elif opcode == 3:
        item.attribute = text
    elif opcode == 4:
        item.attribute = None
    elif opcode == 5:
        order.remove(item)
        order.append(item)
    elif opcode == 6:
        position: Final = integer % (len(order) + 1)
        old: Final = order.index(item)
        order.remove(item)
        order.insert(position - int(old < position), item)


def _model_snapshot(order: list[_Item], nodes: list[_Item]) -> str:
    markup: Final = "".join(
        f"<{item.tag}"
        + (f' data-x="{item.attribute}"' if item.attribute is not None else "")
        + f">{item.text}</{item.tag}>"
        for item in order
    )
    return json.dumps((
        f"<main>{markup}</main>",
        [
            (item.tag, item.text, item.attribute, int(item.attribute is not None), True, True, True, nodes.index(item))
            for item in order
        ],
    ))


@dataclass(eq=False)
class _Item:
    tag: str
    text: str = ""
    attribute: str | None = None
