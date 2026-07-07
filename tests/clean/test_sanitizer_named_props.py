"""``Policy.isolate_named_props`` (DOMPurify's ``SANITIZE_NAMED_PROPS``): namespace id/name to stop DOM clobbering.

An attacker-set ``id`` or ``name`` whose value matches a built-in ``document`` or form property shadows it through
named access -- ``<input name="attributes">`` makes ``form.attributes`` resolve to the input, ``<img name="body">``
hides ``document.body``. With the flag on, every kept id/name value is prefixed with ``user-content-``, moving it out
of the property namespace so no value can collide; an already-prefixed value is left alone, so re-sanitizing is a
fixpoint. The flag is off by default and never weakens the safety baseline.
"""

from __future__ import annotations

import pytest

from turbohtml.clean import Policy, Removed, sanitize, sanitize_report

_ALLOW = {
    "a": frozenset({"id", "href", "hx"}),
    "form": frozenset({"name"}),
    "input": frozenset({"name", "id"}),
    "img": frozenset({"name", "src"}),
    "p": frozenset({"id", "class"}),
}
_TAGS = frozenset({"a", "form", "input", "img", "p"})
_ON = Policy(tags=_TAGS, attributes=_ALLOW, isolate_named_props=True)
_OFF = Policy(tags=_TAGS, attributes=_ALLOW)


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        pytest.param(
            '<a id="location" href="http://x/">x</a>',
            '<a id="user-content-location" href="http://x/">x</a>',
            id="id-collision-prefixed",
        ),
        pytest.param(
            '<input name="attributes">', '<input name="user-content-attributes">', id="name-collision-prefixed"
        ),
        pytest.param(
            '<img name="body" src="http://x/i.png">',
            '<img name="user-content-body" src="http://x/i.png">',
            id="name-prefixed-url-kept",
        ),
        pytest.param(
            '<a id="user-content-foo">y</a>', '<a id="user-content-foo">y</a>', id="already-prefixed-untouched"
        ),
        pytest.param(
            '<a id="user-shortmismatch">y</a>',
            '<a id="user-content-user-shortmismatch">y</a>',
            id="shares-lead-then-differs",
        ),
        pytest.param('<a id="x">y</a>', '<a id="user-content-x">y</a>', id="short-value-prefixed"),
        pytest.param('<a id="">y</a>', '<a id="user-content-">y</a>', id="empty-value-prefixed"),
        pytest.param("<a id>y</a>", '<a id="user-content-">y</a>', id="bare-attribute-prefixed"),
        pytest.param('<a href="http://x/">y</a>', '<a href="http://x/">y</a>', id="href-not-a-named-prop"),
        pytest.param('<a hx="q">y</a>', '<a hx="q">y</a>', id="two-char-non-id-untouched"),
        pytest.param(
            '<p class="c" id="menu">t</p>', '<p class="c" id="user-content-menu">t</p>', id="only-id-among-siblings"
        ),
    ],
)
def test_isolate_named_props_on(fragment: str, expected: str) -> None:
    assert sanitize(fragment, _ON) == expected


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param('<a id="location" href="http://x/">x</a>', id="id"),
        pytest.param('<input name="attributes">', id="name"),
        pytest.param('<a hx="q">y</a>', id="other-attribute"),
    ],
)
def test_isolate_named_props_off_by_default(fragment: str) -> None:
    assert sanitize(fragment, _OFF) == fragment


def test_isolate_named_props_is_a_fixpoint() -> None:
    once = sanitize('<input name="attributes"><a id="location">x</a>', _ON)
    assert sanitize(once, _ON) == once


def test_isolate_named_props_runs_after_attribute_filter() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"id"})},
        attribute_filter=lambda _tag, name, value: value.upper() if name == "id" else value,
        isolate_named_props=True,
    )
    assert sanitize('<a id="menu">x</a>', policy) == '<a id="user-content-MENU">x</a>'


def test_isolate_named_props_keeps_safety_baseline() -> None:
    html, removed = sanitize_report('<a id="x" onclick="e()">t</a><script>bad()</script>', _ON)
    assert html == '<a id="user-content-x">t</a>&lt;script&gt;bad()&lt;/script&gt;'
    assert removed == [Removed("a", "onclick"), Removed("script")]
