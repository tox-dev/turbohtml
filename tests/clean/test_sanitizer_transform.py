"""``Policy.transform_tags`` (sanitize-html's ``transformTags``/``simpleTransform``): rename a tag mid-sanitize.

A transform rewrites an element's name -- and, with a :class:`Transform`, adds attributes -- before the allowlist runs,
so the renamed element is re-checked as if the author had written the target. The rename decides an element's name,
never its safety: the allowlist still governs the target tag, and any added attributes are scrubbed like the element's
own.
"""

from __future__ import annotations

import pytest

from turbohtml.clean import Policy, Sanitizer, Transform, sanitize, sanitize_report


@pytest.mark.parametrize(
    ("fragment", "transform", "tags", "expected"),
    [
        pytest.param("<b>hi</b>", {"b": "strong"}, {"strong"}, "<strong>hi</strong>", id="string-rename"),
        pytest.param("<i>hi</i>", {"i": Transform("em")}, {"em"}, "<em>hi</em>", id="transform-rename-only"),
        pytest.param(
            "<b>a</b><i>b</i>", {"b": "strong"}, {"strong", "i"}, "<strong>a</strong><i>b</i>", id="one-of-two"
        ),
        pytest.param("<b>x</b>", {"b": "strong"}, {"em"}, "&lt;strong&gt;x&lt;/strong&gt;", id="target-not-allowed"),
        pytest.param("<p>x</p>", {"b": "strong"}, {"p"}, "<p>x</p>", id="no-rule-untouched"),
    ],
)
def test_transform_renames(fragment: str, transform: dict[str, Transform | str], tags: set[str], expected: str) -> None:
    assert sanitize(fragment, Policy(tags=frozenset(tags), transform_tags=transform)) == expected


def test_transform_nests_and_recurses() -> None:
    policy = Policy(tags=frozenset({"strong", "em"}), transform_tags={"b": "strong", "i": "em"})
    assert sanitize("<b>a<i>b</i>c</b>", policy) == "<strong>a<em>b</em>c</strong>"


def test_transform_adds_allowlisted_attribute() -> None:
    policy = Policy(
        tags=frozenset({"div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"center": Transform("div", {"class": "center"})},
    )
    assert sanitize("<center>x</center>", policy) == '<div class="center">x</div>'


def test_transform_added_attribute_still_needs_allowlist() -> None:
    policy = Policy(tags=frozenset({"div"}), transform_tags={"center": Transform("div", {"class": "c"})})
    assert sanitize("<center>x</center>", policy) == "<div>x</div>"


def test_transform_added_attribute_overwrites_existing() -> None:
    policy = Policy(
        tags=frozenset({"div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"div": Transform("div", {"class": "safe"})},
    )
    assert sanitize('<div class="danger">x</div>', policy) == '<div class="safe">x</div>'


@pytest.mark.parametrize(
    ("target", "fragment", "expected"),
    [
        pytest.param("script", "<b>evil</b>", "&lt;script&gt;evil&lt;/script&gt;", id="script"),
        pytest.param("iframe", "<b>x</b>", "&lt;iframe&gt;x&lt;/iframe&gt;", id="iframe"),
    ],
)
def test_transform_cannot_smuggle_unsafe_tag(target: str, fragment: str, expected: str) -> None:
    policy = Policy(tags=frozenset({"strong", target}), transform_tags={"b": target})
    assert sanitize(fragment, policy) == expected


def test_transform_added_url_attribute_is_scheme_scrubbed() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href"})},
        transform_tags={"b": Transform("a", {"href": "javascript:alert(1)"})},
    )
    assert sanitize("<b>x</b>", policy) == "<a>x</a>"


def test_transform_target_never_bypasses_on_handler_scrub() -> None:
    policy = Policy(
        tags=frozenset({"div"}),
        attributes={"div": frozenset({"onclick"})},
        transform_tags={"b": Transform("div", {"onclick": "steal()"})},
    )
    assert sanitize("<b>x</b>", policy) == "<div>x</div>"


def test_transform_reports_target_name() -> None:
    policy = Policy(tags=frozenset({"strong"}), transform_tags={"b": "script"})
    html, removed = sanitize_report("<b>e</b>", policy)
    assert html == "&lt;script&gt;e&lt;/script&gt;"
    assert [(item.tag, item.attribute) for item in removed] == [("script", None)]


def test_transform_skips_foreign_elements() -> None:
    policy = Policy(tags=frozenset({"svg", "strong"}), transform_tags={"b": "strong"})
    assert sanitize("<svg></svg><b>x</b>", policy) == "<svg></svg><strong>x</strong>"


@pytest.mark.parametrize(
    ("target", "error", "message"),
    [
        pytest.param(5, TypeError, "must be a str or Transform, got int", id="wrong-type"),
        pytest.param("", ValueError, "must be a non-empty string", id="empty-string"),
        pytest.param(Transform(""), ValueError, "must be a non-empty string", id="empty-transform-tag"),
    ],
)
def test_transform_rejects_bad_rule(target: Transform | str | int, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        Sanitizer(Policy(transform_tags={"b": target}))  # ty: ignore[invalid-argument-type]
