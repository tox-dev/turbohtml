"""Atheris's callback needs the preloaded native rejection bridge."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Final, Protocol, cast

from .atheris_registry import Target, validate_owners
from .atheris_runtime import failure_hook, rejecting_callback, rejection_hook

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["fuzz"]


def fuzz(
    targets: Sequence[Target],
    modules: Sequence[str],
    target_name: str,
    argv: Sequence[str],
    *,
    custom_mutator: Callable[[bytes, int, int], bytes] | None = None,
) -> None:
    """Require qualified ownership before loading the optional Linux runtime."""
    validate_owners(targets, modules)
    target: Final = {target.name: target for target in targets}[target_name]
    atheris: Final = import_module("atheris")
    instrument: Final = cast("Callable[[Callable[[bytes], None]], Callable[[bytes], None]]", atheris.instrument_func)
    callback: Final = instrument(
        rejecting_callback(
            target.run,
            target.exceptions,
            rejection_hook(),
            failure_hook(),
            lossy_on_allocation_failure=target.lossy_on_allocation_failure,
        )
    )
    cast("Callable[[], None]", atheris.instrument_all)()
    cast("_Setup", atheris.Setup)(list(argv), callback, custom_mutator=custom_mutator)
    cast("Callable[[], None]", atheris.Fuzz)()


class _Setup(Protocol):
    def __call__(
        self,
        argv: list[str],
        callback: Callable[[bytes], None],
        *,
        custom_mutator: Callable[[bytes, int, int], bytes] | None,
    ) -> None: ...
