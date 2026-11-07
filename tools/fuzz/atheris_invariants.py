"""Atheris targets assert the #1010 and #1013 round-trip oracles on the inputs they already decode."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from .round_trip_oracles import OutOfScopeError

if TYPE_CHECKING:
    from collections.abc import Callable


def assert_invariant(check: Callable[[str], str | None], text: str) -> None:
    # Skip only this oracle on an out-of-scope case, so the input keeps its other checks and its corpus entry.
    with contextlib.suppress(OutOfScopeError):
        if failure := check(text):
            raise AssertionError(failure)


__all__ = [
    "assert_invariant",
]
