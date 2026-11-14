"""Live bytes allocated from matching files, read through tracemalloc after a full collection."""

from __future__ import annotations

import gc
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from types import ModuleType

PeakBytes = Callable[[str, tuple[str, ...]], int]


@dataclass(frozen=True, kw_only=True, slots=True)
class RetainedBytes:
    tracing: ModuleType
    package: str

    def __call__(self, filename_pattern: str | None = None) -> int:
        # Full collection first, then the live blocks whose innermost Python frame matches the pattern: allocations
        # made by other threads have frames elsewhere, so they stay out of the count.
        gc.collect()
        pattern: Final = self.package if filename_pattern is None else filename_pattern
        snapshot: Final = self.tracing.take_snapshot().filter_traces([
            self.tracing.Filter(inclusive=True, filename_pattern=pattern)
        ])
        return sum(stat.size for stat in snapshot.statistics("filename"))
