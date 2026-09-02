"""The sanitizer's policy compiler in C: the rel value, the style patterns and the transform rules it produces."""

from __future__ import annotations

import re

import pytest

from turbohtml._html import _sanitize_policy
from turbohtml.clean import Policy, Sanitizer, Transform

_EMPTY = ({}, frozenset(), {}, {}, {}, {}, Transform)


_Compiled = tuple[
    dict[str, frozenset[str]],
    str | None,
    dict[str, dict[str, str]],
    dict[str, dict[str, frozenset[str]]],
    dict[str, dict[str, tuple[re.Pattern[str], ...]]],
    dict[str, tuple[str, dict[str, str]]],
]


def _compile(**fields: object) -> _Compiled:
    """Compile a policy built from the given fields, returning the six compiled forms."""
    policy = Policy(**fields)  # ty: ignore[invalid-argument-type]  # each test passes a valid field
    return _sanitize_policy(
        policy.attributes,
        policy.add_link_rel,
        policy.set_attributes,
        policy.attribute_values,
        policy.allowed_styles,
        policy.transform_tags,
        Transform,
    )


def test_the_rel_value_is_sorted_and_space_joined() -> None:
    assert _compile(add_link_rel=frozenset({"noreferrer", "noopener"}))[1] == "noopener noreferrer"


def test_no_rel_tokens_is_none() -> None:
    assert _compile(add_link_rel=frozenset())[1] is None


def test_the_attributes_are_copied_into_a_dict() -> None:
    copied = _compile(attributes={"a": frozenset({"href"})})[0]
    assert copied == {"a": frozenset({"href"})}
    assert isinstance(copied, dict)


def test_value_sets_are_frozen_per_attribute() -> None:
    assert _compile(attribute_values={"a": {"rel": ["nofollow", "ugc"]}})[3] == {
        "a": {"rel": frozenset({"nofollow", "ugc"})}
    }


def test_set_attributes_are_copied_per_tag() -> None:
    assert _compile(set_attributes={"a": {"target": "_blank"}})[2] == {"a": {"target": "_blank"}}


def test_style_properties_are_lowercased_and_patterns_compiled() -> None:
    styles = _compile(allowed_styles={"*": {"Color": ["^red$"]}})[4]
    assert list(styles["*"]) == ["color"]
    (pattern,) = styles["*"]["color"]
    assert pattern.pattern == "^red$"


def test_a_precompiled_pattern_is_kept_as_it_is() -> None:
    ready = re.compile(r"^blue$")
    assert _compile(allowed_styles={"p": {"color": [ready]}})[4]["p"]["color"] == (ready,)


def test_a_bad_pattern_raises_the_regex_error() -> None:
    with pytest.raises(re.error):
        _compile(allowed_styles={"p": {"color": ["("]}})


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        pytest.param("em", ("em", {}), id="a-bare-rename"),
        pytest.param(Transform("div", {"class": "c"}), ("div", {"class": "c"}), id="a-transform-with-attributes"),
    ],
)
def test_transform_rules_normalize(target: str | Transform, expected: tuple[str, dict[str, str]]) -> None:
    assert _compile(transform_tags={"i": target})[5] == {"i": expected}


def test_a_transform_target_must_be_a_str_or_transform() -> None:
    with pytest.raises(TypeError, match="transform_tags\\['i'\\] must be a str or Transform, got int"):
        Sanitizer(Policy(transform_tags={"i": 5}))  # ty: ignore[invalid-argument-type]  # the check is the point


@pytest.mark.parametrize(
    "target", [pytest.param("", id="empty-str"), pytest.param(Transform(""), id="empty-transform")]
)
def test_a_transform_target_tag_must_be_non_empty(target: str | Transform) -> None:
    with pytest.raises(ValueError, match="target tag must be a non-empty string"):
        Sanitizer(Policy(transform_tags={"i": target}))


def test_the_compiler_rejects_a_non_mapping() -> None:
    with pytest.raises(TypeError):
        _sanitize_policy(5, *_EMPTY[1:])  # ty: ignore[invalid-argument-type]  # the argument check is the point


def test_the_compiler_rejects_a_non_mapping_nest() -> None:
    with pytest.raises(AttributeError, match="items"):
        _sanitize_policy({}, frozenset(), {"a": 5}, {}, {}, {}, Transform)  # ty: ignore[invalid-argument-type]


def test_the_compiler_rejects_a_non_mapping_table() -> None:
    with pytest.raises(AttributeError, match="items"):
        _sanitize_policy({}, frozenset(), 5, {}, {}, {}, Transform)  # ty: ignore[invalid-argument-type]


def test_the_compiler_rejects_a_non_iterable_rel() -> None:
    with pytest.raises(TypeError):
        _sanitize_policy({}, 5, {}, {}, {}, {}, Transform)  # ty: ignore[invalid-argument-type]


def test_rel_tokens_of_mixed_types_cannot_be_sorted() -> None:
    with pytest.raises(TypeError):
        _sanitize_policy({}, {"a", 1}, {}, {}, {}, {}, Transform)  # ty: ignore[invalid-argument-type]


def test_a_style_pattern_list_must_be_iterable() -> None:
    with pytest.raises(TypeError):
        _compile(allowed_styles={"p": {"color": 5}})


def test_a_style_property_must_be_a_str() -> None:
    with pytest.raises(AttributeError, match="lower"):
        _compile(allowed_styles={"p": {5: []}})


def test_a_transform_table_must_be_a_mapping() -> None:
    with pytest.raises(AttributeError, match="items"):
        _sanitize_policy({}, frozenset(), {}, {}, {}, 5, Transform)  # ty: ignore[invalid-argument-type]


class _TagOnly:
    """A transform-like object carrying a tag but no attributes, the way a caller's own record might."""

    tag = "em"


class _Bare:
    """A transform-like object carrying neither field."""


@pytest.mark.parametrize(
    ("target", "missing"),
    [pytest.param(_TagOnly(), "attributes", id="no-attributes"), pytest.param(_Bare(), "tag", id="no-tag")],
)
def test_a_transform_type_must_carry_both_fields(target: object, missing: str) -> None:
    with pytest.raises(AttributeError, match=missing):
        _sanitize_policy({}, frozenset(), {}, {}, {}, {"i": target}, type(target))  # ty: ignore[invalid-argument-type]


def test_a_transform_tag_that_is_not_a_str_is_rejected() -> None:
    with pytest.raises(ValueError, match="target tag must be a non-empty string"):
        Sanitizer(Policy(transform_tags={"i": Transform(5)}))  # ty: ignore[invalid-argument-type]


def test_the_compiler_rejects_too_few_arguments() -> None:
    with pytest.raises(TypeError):
        _sanitize_policy({}, frozenset())  # ty: ignore[missing-argument]  # the arity check is the point
