from __future__ import annotations

from collections.abc import Mapping

import pytest

from turbohtml import Element
from turbohtml._html import TagFactory, build_document, build_element


def test_build_document_requires_head_and_body() -> None:
    with pytest.raises(TypeError, match="argument"):
        build_document()  # ty: ignore[missing-argument]  # exercises the argument-parsing guard


def test_build_element_rejects_a_shape_test_that_is_not_a_type() -> None:
    with pytest.raises(TypeError):
        build_element("p", 5, {})  # ty: ignore[invalid-argument-type]  # the argument check is the point


class _Picky(type):
    def __instancecheck__(cls, instance: object) -> bool:
        if instance == 5:
            msg = "cannot judge 5"
            raise RuntimeError(msg)
        return False


class _Shape(metaclass=_Picky): ...


def test_build_element_propagates_a_late_shape_test_error() -> None:
    with pytest.raises(RuntimeError, match="cannot judge 5"):
        build_element("p", _Shape, "a", 5)


class _EveryShape(type):
    def __instancecheck__(cls, instance: object) -> bool:
        return True


class _Anything(metaclass=_EveryShape): ...


def test_build_element_asks_the_shape_test_for_a_dict_that_is_not_mapping_abc() -> None:
    assert build_element("p", _Anything, {"a": "1"}).html == '<p a="1"></p>'
    with pytest.raises(TypeError, match="child must be a node"):
        build_element("p", _Shape, {"a": "1"})


def test_build_element_asks_the_shape_test_for_an_element_argument() -> None:
    with pytest.raises(TypeError, match="attrs must be a mapping"):
        build_element("p", _Anything, Element("b"))


def test_build_element_needs_a_tag_and_a_shape_test() -> None:
    with pytest.raises(TypeError, match=r"build_element\(\) takes at least 2 arguments \(1 given\)"):
        build_element("p")  # ty: ignore[missing-argument]  # the argument check is the point


def test_build_element_rejects_a_tag_that_is_not_a_str() -> None:
    with pytest.raises(TypeError, match="tag must be a str, not int"):
        build_element(5, Mapping)  # ty: ignore[invalid-argument-type]  # the argument check is the point


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param((), r"TagFactory\(\) takes exactly 1 argument \(0 given\)", id="no-tag"),
        pytest.param((5,), "argument 1 must be str, not int", id="non-str-tag"),
    ],
)
def test_tag_factory_checks_its_tag(args: tuple[int, ...], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        TagFactory(*args)  # ty: ignore[invalid-argument-type, missing-argument]  # the argument check is the point


def test_tag_factory_takes_no_keywords() -> None:
    with pytest.raises(TypeError, match=r"TagFactory\(\) takes no keyword arguments"):
        TagFactory(tag="p")  # ty: ignore[unknown-argument, missing-argument]  # the argument check is the point


def test_tag_factory_builds_its_tag() -> None:
    assert TagFactory("p")("x").html == "<p>x</p>"


def test_tag_factory_takes_empty_keywords() -> None:
    # the slot wrappers hand an empty keyword dict on, where a plain call passes none
    keywords: dict[str, str] = {}
    factory = TagFactory.__new__(TagFactory, "p", **keywords)  # see above
    assert factory.__call__("x", **keywords).html == "<p>x</p>"  # see above
