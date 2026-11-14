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
        assert small > 0, "no counted loop ran: the tick is not on this input's path"
        growth = math.log(large / small) / math.log(sizes[1] / sizes[0])
        assert growth <= exponent, f"steps {small} -> {large} grow as n^{growth:.2f}"

    def assert_within(self, call: Callable[[], object], control: Callable[[], object], *, factor: float) -> None:
        spent, baseline = self(call), self(control)
        assert baseline > 0, "the control ran no counted loop"
        assert spent <= factor * baseline, f"{spent} steps against {baseline} for the linear control"
