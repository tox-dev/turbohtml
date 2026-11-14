from __future__ import annotations

import os
from contextlib import nullcontext
from multiprocessing.context import SpawnProcess
from typing import TYPE_CHECKING, Literal

import pytest
from fuzz.amplify import TARGETS, Target

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from fuzz.amplify import _Budget  # simplint: ignore[private-import-path]  # the worker's budget type


class SeamProcess(SpawnProcess):
    # A spawned worker starts with none of the parent's patches, so it installs the fake clock and its probe itself
    def __init__(
        self,
        *,
        kind: Literal["quadratic", "crash-once"],
        marker: Path,
        target: Callable[[Sequence[str], _Budget, str, Path], None],
        args: tuple[object, ...],
    ) -> None:
        super().__init__(target=target, args=args)
        self.kind = kind
        self.marker = marker

    def run(self) -> None:
        clock = [0.0]
        with pytest.MonkeyPatch.context() as patch:
            patch.setitem(TARGETS, "probe", build_probe(self.kind, clock, self.marker))
            patch.setattr("time.thread_time", lambda: clock[0])
            patch.setattr("fuzz.amplify._watchdog", nullcontext)
            super().run()


def build_probe(kind: Literal["quadratic", "crash-once"], clock: list[float], marker: Path) -> Target:
    if kind == "quadratic":
        return Target(run=charging(clock, quadratic_cost), sources=TARGETS["url"].sources)

    def crash_once(text: str) -> None:
        # the first call of the first worker ends its process, so the search has to restart it
        if not marker.exists():
            marker.touch()
            os._exit(1)
        clock[0] += linear_cost(len(text))

    return Target(run=crash_once, sources=TARGETS["url"].sources)


def charging(clock: list[float], cost: Callable[[int], float]) -> Callable[[str], None]:
    def run(text: str) -> None:
        clock[0] += cost(len(text))

    return run


def quadratic_cost(size: int) -> float:
    # 5 ms at 1 KiB, growing fourfold per doubling and staying under the 0.5 s call cutoff at 8 KiB
    return (size / 1024) ** 2 * 0.005


def linear_cost(size: int) -> float:
    return size / 1024 * 0.001
