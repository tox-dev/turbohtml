"""Count the C core's loop steps around a call and check how they grow or compare to a control."""

from __future__ import annotations

import gc
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True, kw_only=True, slots=True)
class WorkSteps:
    read: Callable[[], int]

    def __call__(self, call: Callable[[], object]) -> int:
        # Steps the C core counted on this thread while call ran; GC is held off so no finalizer runs inside.
        gc.collect()
        gc.disable()
        try:
            before = self.read()
            call()
            return self.read() - before
        finally:
            gc.enable()

    def assert_linear(
        self, build: Callable[[int], Callable[[], object]], sizes: tuple[int, int], *, exponent: float = 1.25
    ) -> None:
        small, large = (self(build(size)) for size in sizes)
        if small <= 0:
            msg = "no counted loop ran: the tick is not on this input's path"
            raise AssertionError(msg)
        if (growth := math.log(large / small) / math.log(sizes[1] / sizes[0])) > exponent:
            msg = f"steps {small} -> {large} grow as n^{growth:.2f}"
            raise AssertionError(msg)

    def assert_within(self, call: Callable[[], object], control: Callable[[], object], *, factor: float) -> None:
        spent, baseline = self(call), self(control)
        if baseline <= 0:
            msg = "the control ran no counted loop"
            raise AssertionError(msg)
        if spent > factor * baseline:
            msg = f"{spent} steps against {baseline} for the linear control"
            raise AssertionError(msg)
