"""Whole stylesheet inputs must reach the public minifier without an HTML text boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from turbohtml.clean import minify_css

from .atheris_registry import Target
from .round_trip_oracles import OutOfScopeError, css_semantics_check, fixpoint_check

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["stylesheet_observation", "stylesheet_targets"]


def stylesheet_targets() -> tuple[Target, ...]:
    """Own the public minifier through an exact stylesheet input."""
    return (
        Target("css-stylesheet", _stylesheet, ("turbohtml.clean.minify_css",), (UnicodeDecodeError, OutOfScopeError)),
    )


def _stylesheet(data: bytes) -> None:
    stylesheet_observation(data)


def stylesheet_observation(data: bytes, printer: Callable[[str], str] = minify_css) -> str:
    """Check supported values and selectors against a fixed DOM context."""
    source: Final = data.decode("utf-8")
    if failure := fixpoint_check(source, printer, numeric=True):
        raise AssertionError(failure)
    if failure := css_semantics_check(
        '<style></style><p class="x">text<span id="target">child</span></p>',
        printer,
        stylesheet=source,
    ):
        raise AssertionError(failure)
    return printer(source)
