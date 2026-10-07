"""Use a separate JavaScript parser for exact-source minifier invariants."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from turbohtml.clean import JSMinify, minify_js

from .atheris_registry import Target
from .round_trip_oracles import JsNames, OutOfScopeError, fixpoint_check, js_names_check

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["initialize_javascript", "javascript_observation", "javascript_targets"]

_NAMES: Final = JsNames()
_STRICT: Final = JSMinify(mangle=False, fold=False)


def javascript_targets() -> tuple[Target, ...]:
    """Give exact JavaScript bytes a separate executable owner."""
    return (Target("javascript", _javascript, ("turbohtml.clean.minify_js",), (UnicodeDecodeError, OutOfScopeError)),)


def _javascript(data: bytes) -> None:
    javascript_observation(data)


def javascript_observation(data: bytes, printer: Callable[[str, JSMinify], str] = minify_js) -> str:
    """Keep independent source rejection ahead of minifier findings."""
    source: Final = data.decode("utf-8")
    if _NAMES.read(source) is None:
        raise OutOfScopeError

    def strict(source: str) -> str:
        return printer(source, _STRICT)

    if failure := fixpoint_check(source, strict, numeric=False):
        raise AssertionError(failure)
    if failure := js_names_check(source, _NAMES, printer):
        raise AssertionError(failure)
    return strict(source)


def initialize_javascript() -> None:
    """Resolve reader dependencies before libFuzzer accepts input."""
    _NAMES.read("0")
