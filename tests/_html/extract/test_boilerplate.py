from __future__ import annotations

import pytest

from turbohtml import parse
from turbohtml._html import boilerplate as _boilerplate


def test_a_unit_outside_the_content_body_is_boilerplate() -> None:
    root = parse("<p>orphan</p>").root
    assert root is not None
    assert _boilerplate(root, None, 0, 1.0, True)[0][1] is True  # ruff:ignore[boolean-positional-value-in-call]  # positional C binding


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(("notanelement", None, 0, 1.0, True), id="root-is-not-an-element"),
        pytest.param((None, 0, 1.0, True), id="too-few-arguments"),
    ],
)
def test_the_binding_rejects_bad_arguments(args: tuple[str | int | float | bool | None, ...]) -> None:
    with pytest.raises(TypeError):
        _boilerplate(*args)  # ty: ignore[invalid-argument-type]  # tests the C argument guard


def test_the_binding_rejects_a_content_that_is_not_an_element() -> None:
    root = parse("<p>x</p>").root
    assert root is not None
    with pytest.raises(TypeError):
        _boilerplate(root, "notanelement", 0, 1.0, True)  # ruff:ignore[boolean-positional-value-in-call]  # ty: ignore[invalid-argument-type]  # positional C binding
