from __future__ import annotations

import sys
import sysconfig
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeVar

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))

from work_steps import WorkSteps
from wpt_tree_corpus import WptHtmlTreeCorpus, load_wpt_html_tree

from turbohtml import Element, Node, _html, parse  # ruff:ignore[import-private-name]  # work-step build hook

if TYPE_CHECKING:
    from collections.abc import Callable

_N = TypeVar("_N", bound=Node)

_FREE_THREADED: Final[bool] = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
_GIL_ENABLED_AT_START: Final[bool] = (
    sys._is_gil_enabled() if _FREE_THREADED else True  # ruff:ignore[private-member-access]  # CPython GIL API
)


@pytest.fixture(scope="session")
def wpt_html_tree_corpus() -> WptHtmlTreeCorpus:
    # Return the pinned WPT tree-construction corpus.
    return load_wpt_html_tree()


def pytest_sessionfinish(session: pytest.Session) -> None:
    # Fail the run if a C extension silently re-enabled the GIL on a free-threaded build.
    #
    # ``_html`` declares ``Py_MOD_GIL_NOT_USED``; importing any extension that forgets that slot
    # flips the GIL back on and quietly masks every free-threaded race we test for (issue #84).
    # untestable without building a deliberately GIL-re-enabling extension to import mid-run
    if not _GIL_ENABLED_AT_START and (  # pragma: no cover
        sys._is_gil_enabled()  # ruff:ignore[private-member-access]  # CPython GIL API
    ):
        session.exitstatus = 1


@pytest.fixture(params=[1, 2, 4], ids=["ucs1", "ucs2", "ucs4"])
def storage_kind(request: pytest.FixtureRequest) -> int:
    # Forced input-buffer width for the private _tokenize_states hook.
    return request.param


@pytest.fixture(params=["", "ő", "🎉"], ids=["ucs1", "ucs2", "ucs4"])
def width_prefix(request: pytest.FixtureRequest) -> str:
    # Leading text that forces public-API input into each storage width.
    return request.param


@pytest.fixture
def with_hrefs() -> Callable[[str, list[tuple[int, int, int]]], list[tuple[int, int, int, str, None, bool]]]:
    # Widen (start, end, kind) rows to the scanner's span shape: its href per kind, no phone, its address answer.
    def widen(text: str, spans: list[tuple[int, int, int]]) -> list[tuple[int, int, int, str, None, bool]]:
        prefixes = {0: "http://", 1: "mailto:"}
        emails = {1, 5}
        return [
            (start, end, kind, prefixes.get(kind, "") + text[start:end], None, kind in emails)
            for start, end, kind in spans
        ]

    return widen


@pytest.fixture
def find() -> Callable[[str, str], Element]:
    # Parse html and return the first element matching selector (never None).
    def _find(html: str, selector: str) -> Element:
        element = parse(html).find(selector)
        assert element is not None, f"no <{selector}> in {html!r}"
        return element

    return _find


@pytest.fixture
def first_of_type() -> Callable[[str, type[_N]], _N]:
    # Parse html and return its first descendant of the given node type.
    def _first(html: str, node_type: type[_N]) -> _N:
        return next(node for node in parse(html).descendants if isinstance(node, node_type))

    return _first


@pytest.fixture
def work_steps() -> WorkSteps:
    # Count C loop steps through the work-step build's private counter.
    read = getattr(_html, "_work_steps", None)
    if read is None:  # pragma: no cover  # only release-build legs (scalar) lack it, and they run without coverage
        pytest.skip("needs the work-step build: meson setup -Dwork_steps=true")
    return WorkSteps(read=read)
