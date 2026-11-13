from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml._html import _parse_fragment, _parse_only, _parse_tree

if TYPE_CHECKING:
    from collections.abc import Callable


def test_parse_only_returns_none() -> None:
    assert _parse_only("<html><body><p>hi & bye</p>") is None


@pytest.mark.parametrize(
    ("call"),
    [
        # bytes on purpose: the stub types these str-only (correct for real use), so ty flags the deliberate
        # misuse; suppress rather than widen the stub, which would drop the very rejection this test asserts.
        pytest.param(lambda: _parse_tree(b"x"), id="parse_tree"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: _parse_only(b"x"), id="parse_only"),  # ty: ignore[invalid-argument-type]  # non-str on purpose
        pytest.param(lambda: _parse_fragment(b"x", "div"), id="parse_fragment"),  # ty: ignore[invalid-argument-type]  # non-str
    ],
)
def test_hooks_reject_non_str(call: Callable[[], object]) -> None:
    with pytest.raises(TypeError):
        call()
