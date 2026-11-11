from __future__ import annotations

from collections.abc import Mapping

import pytest

from turbohtml._html import build_document, build_element


def test_build_document_requires_head_and_body() -> None:
    with pytest.raises(TypeError, match="argument"):
        build_document()  # ty: ignore[missing-argument]  # exercises the argument-parsing guard


def test_build_element_rejects_a_shape_test_that_is_not_a_type() -> None:
    with pytest.raises(TypeError):
        build_element("p", ({},), 5)  # ty: ignore[invalid-argument-type]  # the argument check is the point


class _Picky(type):
    def __instancecheck__(cls, instance: object) -> bool:
        if instance == 5:
            msg = "cannot judge 5"
            raise RuntimeError(msg)
        return False


class _Shape(metaclass=_Picky): ...


def test_build_element_propagates_a_late_shape_test_error() -> None:
    with pytest.raises(RuntimeError, match="cannot judge 5"):
        build_element("p", ("a", 5), _Shape)


def test_build_element_takes_a_tuple_of_arguments() -> None:
    with pytest.raises(TypeError):
        build_element("p", ["a"], Mapping)  # ty: ignore[invalid-argument-type]  # the argument check is the point
