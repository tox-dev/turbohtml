"""``Policy.strip_template_markers`` (DOMPurify's ``SAFE_FOR_TEMPLATES``): collapse ``{{ }}``/``${ }``/``<% %>`` runs.

A template engine (Angular, Vue, Mustache, EJS, ERB) evaluates these markers in the strings it renders, so a sanitized
value that still carries one can re-inject once the output is fed through that engine. With the flag on, every run --
its opening delimiter through the nearest matching close, or through the end when unclosed -- collapses to a single
space, in kept text and in kept attribute values alike.
"""

from __future__ import annotations

import pytest

from turbohtml.clean import Policy, sanitize

_ON = Policy(
    tags=frozenset({"p", "a", "input"}),
    attributes={"a": frozenset({"href", "title"}), "input": frozenset({"disabled"})},
    strip_template_markers=True,
)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        pytest.param("<p>a{{x}}b</p>", "<p>a b</p>", id="mustache-closed"),
        pytest.param("<p>a${x}b</p>", "<p>a b</p>", id="tmplit-closed"),
        pytest.param("<p>a<%x%>b</p>", "<p>a b</p>", id="erb-closed"),
        pytest.param("<p>a{{x</p>", "<p>a </p>", id="mustache-unclosed"),
        pytest.param("<p>a${x</p>", "<p>a </p>", id="tmplit-unclosed"),
        pytest.param("<p>a<%x</p>", "<p>a </p>", id="erb-unclosed"),
        pytest.param("<p>x{{a}b}}y</p>", "<p>x y</p>", id="inner-close-lead-then-full-close"),
        pytest.param("<p>{{a}</p>", "<p> </p>", id="close-lead-at-last-char-stays-unclosed"),
        pytest.param("<p>a{b}c</p>", "<p>a{b}c</p>", id="brace-without-second-brace-kept"),
        pytest.param("<p>a$b c</p>", "<p>a$b c</p>", id="dollar-without-brace-kept"),
        pytest.param("<p>3 &lt; 5</p>", "<p>3 &lt; 5</p>", id="lt-without-percent-kept"),
        pytest.param("<p>a{</p>", "<p>a{</p>", id="brace-at-end-of-text-kept"),
        pytest.param("<p>a$</p>", "<p>a$</p>", id="dollar-at-end-of-text-kept"),
        pytest.param("<p>a&lt;</p>", "<p>a&lt;</p>", id="lt-at-end-of-text-kept"),
        pytest.param("<p>{{a}}{{b}}</p>", "<p>  </p>", id="two-runs-collapse-independently"),
    ],
)
def test_templates_text_run_collapses(fragment: str, expected: str) -> None:
    assert sanitize(fragment, _ON) == expected


def test_templates_attribute_value_with_marker_collapses() -> None:
    assert sanitize('<a href="/x" title="{{t}}">k</a>', _ON) == '<a href="/x" title=" ">k</a>'


def test_templates_attribute_value_without_marker_unchanged() -> None:
    assert sanitize('<a href="/x" title="plain">k</a>', _ON) == '<a href="/x" title="plain">k</a>'


def test_templates_valueless_attribute_survives() -> None:
    assert sanitize("<input disabled>", _ON) == '<input disabled="">'


def test_templates_off_by_default_keeps_markers() -> None:
    keep = Policy(tags=frozenset({"p"}))
    assert sanitize("<p>a{{x}}b</p>", keep) == "<p>a{{x}}b</p>"
