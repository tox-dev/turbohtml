from __future__ import annotations

import pytest

from turbohtml._html import _linkify_find


def test_find_rejects_non_str_text() -> None:
    with pytest.raises(TypeError):
        _linkify_find(123, True, True, (), ())  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]


def test_find_rejects_non_tuple_tlds() -> None:
    with pytest.raises(TypeError):
        _linkify_find("text", True, True, ["corp"], ())  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]
