"""Qualified aliases need separate ownership even when they share an implementation."""

from __future__ import annotations

import importlib
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from .atheris_header import Header

__all__ = ["Target", "validate_owners"]


def validate_owners(targets: Sequence[Target], modules: Iterable[str]) -> dict[str, Target]:
    """Fail before fuzzing when a public export has no declared executable owner."""
    owners: Final = {export: target for target in targets for export in target.exports}
    duplicates: Final = sorted(
        export
        for export, count in Counter(export for target in targets for export in target.exports).items()
        if count > 1
    )
    if duplicates:
        duplicate_message: Final = f"Duplicate export owners: {', '.join(duplicates)}"
        raise ValueError(duplicate_message)
    names: Final = Counter(target.name for target in targets)
    if duplicated_names := sorted(name for name, count in names.items() if count > 1):
        message: Final = f"Duplicate target names: {duplicated_names!r}"
        raise ValueError(message)
    declared: Final = {
        f"{module_name}.{export}" for module_name in modules for export in importlib.import_module(module_name).__all__
    }
    missing: Final = sorted(declared - owners.keys())
    unknown: Final = sorted(owners.keys() - declared)
    if missing or unknown:
        mismatch_message: Final = f"Export ownership mismatch: missing={missing!r}, unknown={unknown!r}"
        raise ValueError(mismatch_message)
    return owners


@dataclass(frozen=True)
class Target:
    """One callback owns the public contracts it exercises per input."""

    name: str
    callback: Callable[[bytes], None]
    exports: tuple[str, ...]
    exceptions: tuple[type[Exception], ...] = ()
    chunked: Callable[[bytes, Header], None] | None = None
    lossy_on_allocation_failure: bool = False

    def run(self, payload: bytes, header: Header) -> None:
        """Feed a target with an incremental API in the header's chunks instead of one piece."""
        if self.chunked is None:
            self.callback(payload)
        else:
            self.chunked(payload, header)
