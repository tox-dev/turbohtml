"""Shared fixtures: the tokenizer core is stamped once per PyUnicode storage
width, so width-sensitive tests run under all three kinds.
"""

from __future__ import annotations

import sys
import sysconfig
from typing import TYPE_CHECKING, TypeVar

import pytest

from turbohtml import Element, Node, parse

if TYPE_CHECKING:
    from collections.abc import Callable

_N = TypeVar("_N", bound=Node)

_FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
_gil_enabled_at_start = sys._is_gil_enabled() if _FREE_THREADED else True


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Fail the run if a C extension silently re-enabled the GIL on a free-threaded build.

    ``_html`` declares ``Py_MOD_GIL_NOT_USED``; importing any extension that forgets that slot
    flips the GIL back on and quietly masks every free-threaded race we test for (issue #84).
    """
    # untestable without building a deliberately GIL-re-enabling extension to import mid-run
    if _FREE_THREADED and not _gil_enabled_at_start and sys._is_gil_enabled():  # pragma: no cover
        session.exitstatus = 1


@pytest.fixture(params=[1, 2, 4], ids=["ucs1", "ucs2", "ucs4"])
def storage_kind(request: pytest.FixtureRequest) -> int:
    """Forced input-buffer width for the private _tokenize_states hook."""
    return request.param


@pytest.fixture(params=["", "ő", "🎉"], ids=["ucs1", "ucs2", "ucs4"])
def width_prefix(request: pytest.FixtureRequest) -> str:
    """Leading text that forces public-API input into each storage width."""
    return request.param


@pytest.fixture
def find() -> Callable[[str, str], Element]:
    """Parse html and return the first element matching selector (never None)."""

    def _find(html: str, selector: str) -> Element:
        element = parse(html).find(selector)
        assert element is not None, f"no <{selector}> in {html!r}"
        return element

    return _find


@pytest.fixture
def first_of_type() -> Callable[[str, type[_N]], _N]:
    """Parse html and return its first descendant of the given node type."""

    def _first(html: str, node_type: type[_N]) -> _N:
        return next(node for node in parse(html).descendants if isinstance(node, node_type))

    return _first
