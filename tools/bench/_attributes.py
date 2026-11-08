"""Attribute maps for the node-equality benchmark, filled the same way for every tree builder."""

from __future__ import annotations

from typing import Final, Protocol


class _Attrs(Protocol):
    def __setitem__(self, key: str, value: str, /) -> None: ...
    def __delitem__(self, key: str, /) -> None: ...


def fill_equality_pair(left: _Attrs, right: _Attrs, count: int, variant: str) -> None:
    right["data-seed"] = ""
    del right["data-seed"]
    names: Final = [f"data-{index}" for index in range(count)]
    for name in names:
        left[name] = "é水😀"
    order: Final = names[count // 2 :] + names[: count // 2] if variant == "rotated" else names
    for name in reversed(order) if variant == "reversed" else order:
        right[name] = "é水😀"
    if variant == "early-value":
        right[names[0]] = "different"
    elif variant == "late-value":
        right[names[-1]] = "different"
    elif variant == "disjoint":
        del right[names[-1]]
        right["data-missing"] = "é水😀"


__all__: Final[list[str]] = [
    "fill_equality_pair",
]
