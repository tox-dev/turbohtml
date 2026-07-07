"""Predicate-based custom-element allowance and split SVG/MathML content profiles.

These port DOMPurify's ``CUSTOM_ELEMENT_HANDLING`` (a ``tagNameCheck``/``attributeNameCheck`` predicate that keeps an
unlisted custom element and its attributes) and ``USE_PROFILES`` (per-namespace HTML/SVG/MathML gates). Both run inside
the single C sanitize walk, so the security baseline -- ``on*`` handlers, ``javascript:`` URLs, unsafe tags -- still
applies to whatever a predicate keeps; the last group asserts exactly that.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from turbohtml.clean import Policy, sanitize

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("html", "check", "expected"),
    [
        pytest.param(
            "<my-widget>hi</my-widget>",
            lambda tag: tag.startswith("my-"),
            "<my-widget>hi</my-widget>",
            id="predicate-keeps-matching",
        ),
        pytest.param(
            "<other-el>hi</other-el>",
            lambda tag: tag.startswith("my-"),
            "&lt;other-el&gt;hi&lt;/other-el&gt;",
            id="predicate-escapes-non-matching",
        ),
        pytest.param(
            "<x-a>a</x-a><x-b>b</x-b>",
            lambda tag: bool(re.compile(r"^x-a$").search(tag)),
            "<x-a>a</x-a>&lt;x-b&gt;b&lt;/x-b&gt;",
            id="regex-search-drives-the-predicate",
        ),
        pytest.param(
            "<font-face>x</font-face>",
            lambda _tag: True,
            "&lt;font-face&gt;x&lt;/font-face&gt;",
            id="reserved-name-is-never-a-custom-element",
        ),
        pytest.param(
            "<foobar>x</foobar>",
            lambda _tag: True,
            "&lt;foobar&gt;x&lt;/foobar&gt;",
            id="a-name-without-a-dash-is-not-a-custom-element",
        ),
        pytest.param(
            "<xy>x</xy>",
            lambda _tag: True,
            "&lt;xy&gt;x&lt;/xy&gt;",
            id="a-short-name-is-not-a-custom-element",
        ),
    ],
)
def test_custom_element_predicate(html: str, check: Callable[[str], bool], expected: str) -> None:
    """An unlisted basic custom element is kept when the matcher admits its name, and escaped otherwise."""
    assert sanitize(html, Policy(tags=frozenset(), custom_element_check=check)) == expected


def _admit_all() -> Policy:
    """A policy whose only allowance is a matcher that keeps every custom-element name it is asked about."""
    return Policy(tags=frozenset(), custom_element_check=lambda _tag: True)


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("a1-b", id="digit-continues-a-name"),
        pytest.param("a_-b", id="underscore-continues-a-name"),
        pytest.param("a.-b", id="dot-continues-a-name"),
    ],
)
def test_custom_element_name_accepts_name_characters(tag: str) -> None:
    """The grammar admits ``[.\\w]`` characters between and after dashes, so each of these is a custom element."""
    html = f"<{tag}>x</{tag}>"
    assert sanitize(html, _admit_all()) == html


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("a~-b", id="a-non-name-character-rejects"),
        pytest.param("a--b", id="a-doubled-dash-rejects"),
        pytest.param("x-y-", id="a-trailing-dash-rejects"),
    ],
)
def test_custom_element_name_rejects_malformed_names(tag: str) -> None:
    """A non-name character, a doubled dash, or a trailing dash disqualifies a name, so the matcher never sees it."""
    html = f"<{tag}>x</{tag}>"
    assert sanitize(html, _admit_all()) != html


def test_custom_attribute_check_keeps_matching_and_drops_others() -> None:
    """On a kept custom element, only attributes the attribute matcher admits survive; the rest are stripped."""
    policy = Policy(
        tags=frozenset(),
        custom_element_check=lambda _tag: True,
        custom_attribute_check=lambda _tag, name: name.startswith("data-x"),
    )
    assert sanitize('<my-el data-x-id="1" foo="2">x</my-el>', policy) == '<my-el data-x-id="1">x</my-el>'


def test_custom_attribute_check_default_keeps_only_allowlisted() -> None:
    """Without an attribute matcher, a kept custom element keeps only the attributes ``attributes`` allowlists."""
    policy = Policy(
        tags=frozenset(),
        attributes={"my-el": frozenset({"title"})},
        custom_element_check=lambda _tag: True,
    )
    assert sanitize('<my-el title="t" role="x">y</my-el>', policy) == '<my-el title="t">y</my-el>'


def test_custom_attribute_check_applies_to_an_allowlisted_custom_element() -> None:
    """A custom element admitted by ``tags`` still routes its unlisted attributes through the attribute matcher."""
    policy = Policy(
        tags=frozenset({"my-el"}),
        custom_element_check=lambda _tag: True,
        custom_attribute_check=lambda _tag, name: name == "role",
    )
    assert sanitize('<my-el role="button" foo="x">y</my-el>', policy) == '<my-el role="button">y</my-el>'


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<button is="my-button">x</button>', '<button is="my-button">x</button>', id="is-value-matches"),
        pytest.param('<button is="evil">x</button>', "<button>x</button>", id="is-value-rejected"),
        pytest.param("<button is>x</button>", "<button>x</button>", id="valueless-is-dropped"),
    ],
)
def test_allow_customized_builtins(html: str, expected: str) -> None:
    """With ``allow_customized_builtins``, an ``is`` attribute survives only when its value names a custom element."""
    policy = Policy(
        tags=frozenset({"button"}),
        custom_element_check=lambda tag: tag == "my-button",
        allow_customized_builtins=True,
    )
    assert sanitize(html, policy) == expected


def test_is_attribute_is_dropped_without_allow_customized_builtins() -> None:
    """The ``is`` attribute is not special unless ``allow_customized_builtins`` is on, so it is stripped by default."""
    policy = Policy(tags=frozenset({"button"}), custom_element_check=lambda _tag: True)
    assert sanitize('<button is="my-button">x</button>', policy) == "<button>x</button>"


def test_the_is_rule_only_touches_a_valued_is_on_a_configured_policy() -> None:
    """Only a two-character ``is`` with a value is special: every other unlisted attribute drops as usual, and the
    rule stays inert without a custom-element matcher to test the value against."""
    configured = Policy(
        tags=frozenset({"button"}),
        custom_element_check=lambda tag: tag == "my-button",
        allow_customized_builtins=True,
    )
    # data-x (length != 2), ab (not "i..."), and id ("i" but not "is") all miss the is-rule and are stripped
    assert sanitize('<button is="my-button" data-x="1" ab="2" id="3">y</button>', configured) == (
        '<button is="my-button">y</button>'
    )
    # allow_customized_builtins on but no matcher: the is-rule has nothing to test the value against, so is drops
    uncheckable = Policy(tags=frozenset({"button"}), allow_customized_builtins=True)
    assert sanitize('<button is="my-button">y</button>', uncheckable) == "<button>y</button>"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param('<my-el onclick="steal()">x</my-el>', "<my-el>x</my-el>", id="event-handler-stripped"),
        pytest.param(
            '<my-el><a href="javascript:alert(1)">l</a></my-el>',
            "<my-el><a>l</a></my-el>",
            id="dangerous-url-scrubbed",
        ),
        pytest.param("<my-el><script>evil()</script></my-el>", "<my-el></my-el>", id="unsafe-child-removed"),
    ],
)
def test_baseline_holds_on_custom_elements(html: str, expected: str) -> None:
    """The non-configurable safety baseline still scrubs a kept custom element and its subtree."""
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href"})},
        custom_element_check=lambda _tag: True,
        custom_attribute_check=lambda _tag, _name: True,
        remove_with_content=frozenset({"script"}),
    )
    assert sanitize(html, policy) == expected


def test_a_custom_matcher_never_keeps_an_unsafe_tag() -> None:
    """An unsafe raw-text tag is escaped regardless of the custom-element matcher, since it never reaches it."""
    policy = Policy(tags=frozenset(), custom_element_check=lambda _tag: True)
    assert sanitize("<my-script>x</my-script>", policy) == "<my-script>x</my-script>"
    assert "&lt;script&gt;" in sanitize("<script>x</script>", policy)


_PROFILE_HTML = "<b>h</b><svg><circle></circle></svg><math><mi>m</mi></math>"
_PROFILE_TAGS = frozenset({"b", "svg", "circle", "math", "mi"})


@pytest.mark.parametrize(
    ("policy", "keeps", "drops"),
    [
        pytest.param(
            Policy(tags=_PROFILE_TAGS, allow_mathml=False), "<circle>", "<mi>", id="svg-only-keeps-svg-drops-mathml"
        ),
        pytest.param(
            Policy(tags=_PROFILE_TAGS, allow_svg=False), "<mi>", "<circle>", id="mathml-only-keeps-mathml-drops-svg"
        ),
        pytest.param(
            Policy(tags=_PROFILE_TAGS, allow_html=False, allow_svg=False), "<mi>", "<b>", id="no-html-drops-html"
        ),
    ],
)
def test_content_profiles(policy: Policy, keeps: str, drops: str) -> None:
    """Each namespace gate keeps its own content and drops the disabled namespace even when its tags are allowlisted."""
    result = sanitize(_PROFILE_HTML, policy)
    assert keeps in result
    assert drops not in result


def test_all_profiles_on_by_default_keep_every_namespace() -> None:
    """The default gates keep HTML, SVG, and MathML together, so an allowlist governs each namespace as before."""
    result = sanitize(_PROFILE_HTML, Policy(tags=_PROFILE_TAGS))
    assert "<circle>" in result
    assert "<mi>" in result


def test_a_disabled_namespace_ignores_a_matching_custom_element() -> None:
    """A foreign element never reaches the custom-element matcher, and a disabled HTML namespace short-circuits it."""
    svg = Policy(tags=frozenset({"svg", "circle"}), custom_element_check=lambda _tag: True)
    assert sanitize("<svg><circle></circle></svg>", svg) == "<svg><circle></circle></svg>"
    no_html = Policy(tags=frozenset(), allow_html=False, custom_element_check=lambda _tag: True)
    assert sanitize("<my-el>x</my-el>", no_html) == "&lt;my-el&gt;x&lt;/my-el&gt;"


def test_a_raising_element_matcher_propagates() -> None:
    """An exception from the custom-element matcher surfaces to the caller rather than being swallowed."""

    def boom(_tag: str) -> bool:
        msg = "element"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="element"):
        sanitize("<my-el>x</my-el>", Policy(tags=frozenset(), custom_element_check=boom))


def test_a_raising_attribute_matcher_propagates() -> None:
    """An exception from the attribute matcher surfaces to the caller."""

    def boom(_tag: str, _name: str) -> bool:
        msg = "attribute"
        raise ValueError(msg)

    policy = Policy(
        tags=frozenset(),
        custom_element_check=lambda _tag: True,
        custom_attribute_check=boom,
    )
    with pytest.raises(ValueError, match="attribute"):
        sanitize('<my-el foo="1">x</my-el>', policy)


def test_a_raising_is_matcher_propagates() -> None:
    """An exception from the matcher while checking an ``is`` value surfaces to the caller."""

    def boom(_tag: str) -> bool:
        msg = "is-value"
        raise ValueError(msg)

    # button is allowlisted, so the matcher runs only against the is value "custom-name", where it raises
    policy = Policy(tags=frozenset({"button"}), custom_element_check=boom, allow_customized_builtins=True)
    with pytest.raises(ValueError, match="is-value"):
        sanitize('<button is="custom-name">x</button>', policy)
