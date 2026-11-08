"""bleach: the html5lib-based clean."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import bleach

if TYPE_CHECKING:
    from bench.operations import Timed

_REQUIREMENTS: Final[tuple[str, ...]] = ("bleach>=6.4",)


def sanitize(text: str) -> None:
    bleach.clean(text)


def linkify(text: str) -> None:
    # Auto-link URLs and emails in HTML with bleach's html5lib-based filter.
    bleach.linkify(text)


def _sanitize_disallowed(case: tuple[str, str]) -> str:
    mode, text = case
    return _DISALLOWED_CLEANERS[mode].clean(text)


def _sanitize_attributes(text: str) -> str:
    return _ATTRIBUTE_CLEANER.clean(text)


def _allow_data_attribute(_tag: str, name: str, _value: str) -> bool:
    return name.startswith("data-")


_DISALLOWED_CLEANERS: Final[dict[str, bleach.Cleaner]] = {
    mode: bleach.Cleaner(tags=frozenset(), strip=mode == "strip") for mode in ("escape", "strip")
}
_ATTRIBUTE_CLEANER: Final[bleach.Cleaner] = bleach.Cleaner(tags={"p"}, attributes=_allow_data_attribute)

OPERATIONS: Final[dict[str, tuple[Timed, str]]] = {
    "sanitize": (sanitize, "bleach"),
    "sanitize-disallowed": (_sanitize_disallowed, "bleach"),
    "linkify": (linkify, "bleach"),
    "sanitize-attributes": (_sanitize_attributes, "bleach"),
}
