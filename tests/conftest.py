"""Shared fixtures: the tokenizer core is stamped once per PyUnicode storage
width, so width-sensitive tests run under all three kinds.
"""

from __future__ import annotations

import pytest


@pytest.fixture(params=[1, 2, 4], ids=["ucs1", "ucs2", "ucs4"])
def storage_kind(request: pytest.FixtureRequest) -> int:
    """Forced input-buffer width for the private _tokenize_states hook."""
    return request.param


@pytest.fixture(params=["", "ő", "🎉"], ids=["ucs1", "ucs2", "ucs4"])
def width_prefix(request: pytest.FixtureRequest) -> str:
    """Leading text that forces public-API input into each storage width."""
    return request.param
