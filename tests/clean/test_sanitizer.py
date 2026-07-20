from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from turbohtml.clean import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    OnDisallowed,
    Policy,
    Removed,
    Sanitizer,
    sanitize,
    sanitize_report,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


def _style_policy(
    *,
    css_properties: frozenset[str] = DEFAULT_CSS_PROPERTIES,
    attribute_filter: Callable[[str, str, str], str | None] | None = None,
) -> Policy:
    """A policy that allows <p style="...">, for exercising CSS scrubbing."""
    return Policy(
        tags=frozenset({"p"}),
        attributes={"p": frozenset({"style"})},
        css_properties=css_properties,
        attribute_filter=attribute_filter,
    )


# frame and frameset are absent: the parser drops them outside a frameset document, so a fragment never contains one.
# style is absent too: unlike the others, allowlisting it keeps the element with its CSS scrubbed (see the style-body
# tests), rather than neutralizing it.
_UNSAFE_TAGS = [
    "script", "iframe", "embed", "object", "noscript",
    "noembed", "noframes", "base", "basefont", "title", "xmp", "template",
]  # fmt: skip
# (tag, markup that yields it) -- col and area need a table/map context to parse as an element.
_VOID_CASES = [
    ("area", "<map><area></map>"), ("base", "<base>"), ("basefont", "<basefont>"), ("br", "<br>"),
    ("col", "<table><col></table>"), ("embed", "<embed>"), ("hr", "<hr>"), ("img", "<img>"),
    ("input", "<input>"), ("keygen", "<keygen>"), ("link", "<link>"), ("meta", "<meta>"),
    ("param", "<param>"), ("source", "<source>"), ("track", "<track>"), ("wbr", "<wbr>"),
]  # fmt: skip
_URL_ATTRS = [
    "href", "src", "cite", "data", "ping", "action", "poster", "longdesc", "formaction", "background", "xlink:href",
]  # fmt: skip


def _allow_all(tags: set[str], attrs: set[str]) -> Policy:
    """A permissive policy for exercising attribute and URL handling on otherwise-disallowed elements."""
    return Policy(tags=frozenset(tags), attributes={"*": frozenset(attrs)}, url_schemes=DEFAULT_SCHEMES)


def test_default_escapes_unknown_tags() -> None:
    assert sanitize("<p>hi</p>") == "&lt;p&gt;hi&lt;/p&gt;"


def test_default_keeps_allowlisted() -> None:
    assert sanitize('<a href="http://x.com" title="t">hi</a>') == '<a href="http://x.com" title="t">hi</a>'


def test_disallowed_attribute_dropped() -> None:
    assert sanitize('<a href="http://x" class="c" id="i">x</a>') == '<a href="http://x">x</a>'


def test_valueless_attribute_kept_when_allowed() -> None:
    policy = _allow_all({"input"}, {"disabled"})
    assert sanitize("<input disabled>", policy) == '<input disabled="">'


@pytest.mark.parametrize("tag", _UNSAFE_TAGS)
def test_unsafe_tag_never_survives_even_if_allowed(tag: str) -> None:
    # explicitly allow the unsafe tag; the baseline must still neutralize it
    policy = Policy(tags=DEFAULT_TAGS | {tag})
    out = sanitize(f"<{tag}>x</{tag}>", policy)
    assert f"<{tag}>" not in out
    assert f"&lt;{tag}&gt;" in out


@pytest.mark.parametrize(("tag", "html"), _VOID_CASES, ids=[tag for tag, _ in _VOID_CASES])
def test_void_tag_escaped_without_end_tag(tag: str, html: str) -> None:
    out = sanitize(html, Policy.strict())
    assert f"&lt;{tag}&gt;" in out
    assert f"&lt;/{tag}&gt;" not in out


def test_non_void_escape_has_end_tag() -> None:
    assert sanitize("<div>x</div>", Policy.strict()) == "&lt;div&gt;x&lt;/div&gt;"


def test_escape_keeps_allowed_children_live() -> None:
    assert sanitize("<unknown><b>x</b></unknown>") == "&lt;unknown&gt;<b>x</b>&lt;/unknown&gt;"


def test_escape_reconstructs_attributes() -> None:
    assert sanitize('<unknown id="a" hidden>x</unknown>') == '&lt;unknown id="a" hidden&gt;x&lt;/unknown&gt;'


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("Favorite movie: <name of movie>", "Favorite movie: &lt;name of movie&gt;", id="unclosed-empty"),
        pytest.param("I love <sarcasm> this", "I love &lt;sarcasm&gt; this", id="unclosed-wrapping-run"),
        pytest.param("<p>hi", "&lt;p&gt;hi", id="unclosed-known-at-eof"),
        pytest.param("<name of movie>x</name>", "&lt;name of movie&gt;x&lt;/name&gt;", id="source-closed-unknown"),
        pytest.param("<div><p>x</div>", "&lt;div&gt;&lt;p&gt;x&lt;/div&gt;", id="source-closed-outer-implied-inner"),
    ],
)
def test_escape_reproduces_only_source_end_tags(html: str, expected: str) -> None:
    # escape mode renders the author's markup as text: a disallowed element gets a `</tag>` only where the source wrote
    # one, never a fabricated close tag after an unclosed or void element
    assert sanitize(html, Policy.strict()) == expected


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        pytest.param(OnDisallowed.ESCAPE, "&lt;div&gt;<b>x</b>&lt;/div&gt;", id="escape"),
        pytest.param(OnDisallowed.STRIP, "<b>x</b>", id="strip"),
        pytest.param(OnDisallowed.REMOVE, "", id="remove"),
    ],
)
def test_dispositions(disposition: OnDisallowed, expected: str) -> None:
    assert sanitize("<div><b>x</b></div>", Policy(on_disallowed_tag=disposition)) == expected


@pytest.mark.parametrize("disposition", [OnDisallowed.ESCAPE, OnDisallowed.STRIP, OnDisallowed.REMOVE])
def test_foreign_content_never_unwrapped(disposition: OnDisallowed) -> None:
    # an SVG <a> shares the name of an allowed HTML <a> but must not be treated as one
    out = sanitize("<svg><a href='http://x'>t</a></svg>", Policy(on_disallowed_tag=disposition))
    assert "<a href" not in out  # never a live HTML anchor


def test_allowlisted_foreign_element_is_kept() -> None:
    # an SVG/MathML element on the allowlist is kept, matching bleach and nh3
    assert sanitize("<svg>text</svg>", Policy(tags=frozenset({"svg"}))) == "<svg>text</svg>"


def test_allowlisted_foreign_subtree_keeps_html_integration_child() -> None:
    policy = Policy(tags=frozenset({"svg", "foreignObject", "p"}))
    out = sanitize("<svg><foreignObject><p>hi</p></foreignObject></svg>", policy)
    assert out == "<svg><foreignObject><p>hi</p></foreignObject></svg>"


def test_allowlisted_foreign_script_is_still_escaped() -> None:
    # the unsafe-tag set neutralizes scripting in any namespace even when allowlisted
    out = sanitize("<svg><script>alert(1)</script></svg>", Policy(tags=frozenset({"svg", "script"})))
    assert "<script>" not in out


def test_set_attributes_adds_absent_attributes() -> None:
    # set_attributes forces attributes onto kept elements even when the allowlist would not admit them
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href"})},
        set_attributes={"a": {"target": "_blank", "rel": "noopener"}},
    )
    out = sanitize('<a href="http://x">t</a>', policy)
    assert 'target="_blank"' in out
    assert 'rel="noopener"' in out


def test_set_attributes_overwrites_present_attribute() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href", "target"})},
        set_attributes={"a": {"target": "_blank"}},
    )
    out = sanitize('<a href="http://x" target="_self">t</a>', policy)
    assert 'target="_blank"' in out
    assert "_self" not in out


def test_set_attributes_only_touches_named_tag() -> None:
    # a kept element whose tag is not in set_attributes is left alone
    policy = Policy(
        tags=frozenset({"a", "b"}), attributes={"a": frozenset({"href"})}, set_attributes={"a": {"rel": "x"}}
    )
    out = sanitize('<a href="http://x">t</a><b>y</b>', policy)
    assert out.count("rel=") == 1


def test_set_attributes_skips_disallowed_elements() -> None:
    # a disallowed tag is escaped, so its set_attributes entry is never applied
    policy = Policy(tags=frozenset({"a"}), set_attributes={"script": {"rel": "x"}})
    assert "rel=" not in sanitize("<a>t</a><script>z</script>", policy)


def test_script_text_leaks_without_remove_with_content() -> None:
    # baseline: under the default ESCAPE mode a disallowed <script> is escaped, so its text stays visible
    out = sanitize("<b>ok</b><script>alert(1)</script>", Policy(tags=frozenset({"b"})))
    assert "alert(1)" in out


def test_remove_with_content_deletes_disallowed_subtree() -> None:
    # naming the tag in remove_with_content drops the tag and its whole subtree, so the text never leaks
    policy = Policy(tags=frozenset({"b"}), remove_with_content=frozenset({"script", "style"}))
    assert sanitize("<b>ok</b><script>alert(1)</script><style>x{}</style>", policy) == "<b>ok</b>"


def test_remove_with_content_does_not_drop_allowed_tags() -> None:
    # an allowlisted tag is kept even when it also appears in remove_with_content
    policy = Policy(tags=frozenset({"b"}), remove_with_content=frozenset({"b"}))
    assert sanitize("<b>kept</b>", policy) == "<b>kept</b>"


def test_remove_with_content_leaves_other_disallowed_tags_to_the_mode() -> None:
    # a disallowed tag that is not in the set still follows on_disallowed (escaped here)
    policy = Policy(tags=frozenset({"b"}), remove_with_content=frozenset({"script"}))
    assert "&lt;unknown&gt;" in sanitize("<b>ok</b><unknown>x</unknown>", policy)


@pytest.mark.parametrize("mode", [OnDisallowed.ESCAPE, OnDisallowed.STRIP, OnDisallowed.REMOVE])
def test_remove_with_content_overrides_every_disposition(mode: OnDisallowed) -> None:
    # content removal happens before the escape/strip/remove dispatch, so it wins in every mode
    policy = Policy(tags=frozenset({"b"}), on_disallowed_tag=mode, remove_with_content=frozenset({"script"}))
    out = sanitize("<b>ok</b><script>alert(1)</script>", policy)
    assert "alert(1)" not in out
    assert "<b>ok</b>" in out


def test_style_dropped_when_not_allowed() -> None:
    # style is not in the default allowed attributes, so it is dropped without CSS scrubbing
    assert sanitize('<a href="http://x" style="color: red">t</a>') == '<a href="http://x">t</a>'


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("color: red", 'style="color: red"', id="keep-allowed"),
        pytest.param("position: fixed", None, id="drop-disallowed"),
        pytest.param("COLOR: red", 'style="COLOR: red"', id="uppercase-name"),
        pytest.param("color: red; width: 5px", 'style="color: red; width: 5px"', id="two-kept-joined"),
        pytest.param("color: red; position: fixed", 'style="color: red"', id="keep-one-drop-one"),
        pytest.param("  color : red  ", 'style="color : red"', id="surrounding-whitespace-trimmed"),
        pytest.param("color: red; /* ; : x */ width: 1px", 'style="color: red', id="comment-holds-separators"),
        pytest.param("font-family: 'a;b:c'; color: red", "font-family: 'a;b:c'", id="string-holds-separators"),
        pytest.param("font-family: 'a\\'b'; color: red", "color: red", id="string-escaped-quote"),
        pytest.param("cursor: url(a;b:c); color: red", "cursor: url(a;b:c)", id="url-holds-separators"),
        pytest.param("cursor: url((nested)); color: red", "color: red", id="nested-parens"),
        pytest.param("color: red)", 'style="color: red)"', id="stray-close-paren"),
        pytest.param("color:", 'style="color:"', id="empty-value"),
        pytest.param("color: a:b", 'style="color: a:b"', id="value-has-second-colon"),
        pytest.param("no-colon-declaration", None, id="declaration-without-colon"),
        pytest.param(": red; color: blue", 'style="color: blue"', id="empty-property-name"),
        pytest.param("", None, id="empty-value"),
        pytest.param(";;; color: red ;;;", 'style="color: red"', id="extra-semicolons"),
        pytest.param("position: fixed; z-index: 9", None, id="all-dropped-removes-attribute"),
        pytest.param(("a" * 70) + ": red; color: blue", 'style="color: blue"', id="property-name-too-long"),
    ],
)
def test_style_scrubbing(style: str, expected: str | None) -> None:
    out = sanitize(f'<p style="{style}">x</p>', _style_policy())
    if expected is None:
        assert "style=" not in out
    else:
        assert expected in out


def test_style_unterminated_string_is_safe() -> None:
    # an unterminated string runs to the end; the declaration's property still gates it
    out = sanitize('<p style="color: red; font-family: \'unterminated">x</p>', _style_policy())
    assert "color: red" in out
    assert sanitize(out, _style_policy()) == out  # idempotent


def test_style_unterminated_comment_is_safe() -> None:
    out = sanitize('<p style="color: red; width: 1px /* unterminated">x</p>', _style_policy())
    assert "color: red" in out
    assert sanitize(out, _style_policy()) == out


def test_style_empty_property_set_drops_all_css() -> None:
    out = sanitize('<p style="color: red">x</p>', _style_policy(css_properties=frozenset()))
    assert "style=" not in out


def test_style_scrubbed_before_attribute_filter_sees_it() -> None:
    seen: list[str] = []

    def record(_tag: str, name: str, value: str) -> str:
        seen.append(f"{name}={value}")
        return value

    sanitize('<p style="color: red; position: fixed">x</p>', _style_policy(attribute_filter=record))
    assert seen == ["style=color: red"]  # the filter sees the already-scrubbed value


def test_style_comment_with_lone_asterisk() -> None:
    # a '*' inside a comment that is not the closing '*/' must not end the comment early
    assert "color: red" in sanitize('<p style="color: red /* a*b */">x</p>', _style_policy())


def test_style_unterminated_comment_ending_in_asterisk() -> None:
    # a '*' as the final byte of an unterminated comment has no following '/'
    assert "color: red" in sanitize('<p style="color: red /* x *">x</p>', _style_policy())


def test_style_slash_not_starting_a_comment() -> None:
    # a '/' not followed by '*' is an ordinary character, including one at the very end
    assert "color: 1/2" in sanitize('<p style="color: 1/2">x</p>', _style_policy())
    assert "color: red/" in sanitize('<p style="color: red/">x</p>', _style_policy())


def test_style_double_quoted_css_string() -> None:
    # a double-quoted CSS string (in a single-quoted attribute) holds separators safely
    out = sanitize("<p style='font-family: \"a;b:c\"; color: red'>x</p>", _style_policy())
    assert "color: red" in out


@pytest.mark.parametrize(
    ("style", "kept"),
    [
        pytest.param("width: expression(alert(1))", False, id="expression"),
        pytest.param("width: EXPRESSION(alert(1))", False, id="expression-uppercase"),
        pytest.param("width: expression (alert(1))", False, id="expression-space-before-paren"),
        pytest.param("color: url(javascript:alert(1))", False, id="url-javascript"),
        pytest.param("color: url('javascript:alert(1)')", False, id="url-javascript-single-quote"),
        pytest.param("color: url( javascript:x )", False, id="url-javascript-spaced"),
        pytest.param("color: url/* c */(javascript:x)", False, id="url-comment-before-paren"),
        pytest.param("color: url(http://x/a.png)", True, id="url-http-allowed"),
        pytest.param("color: url(http://x y)", True, id="url-allowed-then-space"),
        pytest.param("color: url/* c */(http://x)", True, id="url-comment-then-allowed"),
        pytest.param("color: url(/rel.png)", True, id="url-relative-allowed"),
        pytest.param("color: url()", True, id="url-empty"),
        pytest.param("color: expression", True, id="expression-bare-ident"),
        pytest.param("color: expression z", True, id="expression-ident-then-word"),
        pytest.param("color: url", True, id="url-bare-ident"),
        pytest.param("color: url z", True, id="url-ident-then-word"),
        pytest.param("color: ur", True, id="url-prefix-truncated"),
        pytest.param("cursor: curl(x)", True, id="url-mid-identifier"),
        pytest.param("color: url/x", True, id="url-slash-not-comment"),
        pytest.param("color: url/", True, id="url-trailing-slash"),
        pytest.param("color: url(", True, id="url-open-paren-at-end"),
        pytest.param("color: url(http://x", True, id="url-unterminated-allowed"),
        pytest.param("color: url/* unterminated", True, id="url-unterminated-comment"),
        pytest.param("color: url/* x*", True, id="url-comment-trailing-asterisk"),
        pytest.param("color: url/* a*b */(http://x)", True, id="url-comment-lone-asterisk-then-allowed"),
        pytest.param("color: a-b_1c", True, id="value-identifier-punctuation"),
    ],
)
def test_style_value_rejects_expression_and_bad_url_scheme(style: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    # a kept property still has its value scrubbed: expression() and url(disallowed-scheme) drop the whole declaration
    out = sanitize(f'<p style="{style}">x</p>', _style_policy())
    assert ("style=" in out) is kept


def test_style_double_quoted_url_scheme_is_stripped() -> None:
    # a double-quoted url() (in a single-quoted attribute) cannot smuggle a disallowed scheme past the value scan
    assert "style=" not in sanitize("""<p style='color: url("javascript:x")'>y</p>""", _style_policy())


_COLOR_ALIGN = {"color": [r"^#[0-9a-f]{3,6}$", r"^rgb\("], "text-align": [r"^left$|^right$|^center$"]}


def _allowed_styles_policy(
    allowed_styles: Mapping[str, Mapping[str, Sequence[str | re.Pattern[str]]]],
    *,
    css_properties: frozenset[str] = frozenset({"color", "text-align", "width"}),
) -> Policy:
    """A <p style> / <span style> policy carrying a per-property value allowlist for exercising allowed_styles."""
    return Policy(
        tags=frozenset({"p", "span"}),
        attributes={"*": frozenset({"style"})},
        css_properties=css_properties,
        allowed_styles=allowed_styles,
    )


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("color: #fff", 'style="color: #fff"', id="value-matches-pattern"),
        pytest.param("color: red", None, id="value-fails-every-pattern"),
        pytest.param("text-align: center", 'style="text-align: center"', id="second-property-matches"),
        pytest.param("width: 5px", None, id="property-not-listed-dropped"),
        pytest.param("color: #fff; width: 5px", 'style="color: #fff"', id="listed-kept-unlisted-dropped"),
        pytest.param("color: rgb(1,2,3)", 'style="color: rgb(1,2,3)"', id="second-pattern-of-a-property"),
        pytest.param("color:", None, id="empty-value-no-pattern-matches"),
    ],
)
def test_allowed_styles_narrows_by_value(style: str, expected: str | None) -> None:
    out = sanitize(f'<p style="{style}">x</p>', _allowed_styles_policy({"*": _COLOR_ALIGN}))
    if expected is None:
        assert "style=" not in out
    else:
        assert expected in out


def test_allowed_styles_wildcard_applies_to_every_tag() -> None:
    # the "*" tag key matches any element, so a <span> is narrowed the same as a <p>
    policy = _allowed_styles_policy({"*": {"color": [r"^blue$"]}})
    assert sanitize('<span style="color: blue">x</span>', policy) == '<span style="color: blue">x</span>'
    assert "style=" not in sanitize('<span style="color: green">x</span>', policy)


def test_allowed_styles_tag_specific_leaves_other_tags_to_name_allowlist() -> None:
    # a rule keyed only by "span" narrows <span>; a <p> keeps the css_properties baseline (no value narrowing)
    policy = _allowed_styles_policy({"span": {"color": [r"^blue$"]}})
    assert "style=" not in sanitize('<span style="color: red; width: 5px">x</span>', policy)
    assert sanitize('<p style="color: red; width: 5px">x</p>', policy) == '<p style="color: red; width: 5px">x</p>'


def test_allowed_styles_merges_tag_and_wildcard() -> None:
    # a property listed by the tag and by "*" unions both pattern lists; distinct properties from each both apply
    policy = _allowed_styles_policy({
        "p": {"color": [r"^red$"]},
        "*": {"color": [r"^blue$"], "text-align": [r"^left$"]},
    })
    assert sanitize('<p style="color: red">x</p>', policy) == '<p style="color: red">x</p>'
    assert sanitize('<p style="color: blue">x</p>', policy) == '<p style="color: blue">x</p>'
    assert sanitize('<p style="text-align: left">x</p>', policy) == '<p style="text-align: left">x</p>'
    assert "style=" not in sanitize('<p style="color: green">x</p>', policy)


def test_allowed_styles_accepts_precompiled_patterns() -> None:
    policy = _allowed_styles_policy({"*": {"color": [re.compile(r"^teal$")]}})
    assert sanitize('<p style="color: teal">x</p>', policy) == '<p style="color: teal">x</p>'


def test_allowed_styles_patterns_search_unanchored() -> None:
    # a pattern is applied with re.search, so an unanchored one matches anywhere in the value, like sanitize-html
    policy = _allowed_styles_policy({"*": {"color": [r"e"]}})
    assert sanitize('<p style="color: red">x</p>', policy) == '<p style="color: red">x</p>'


def test_allowed_styles_still_requires_the_name_allowlist() -> None:
    # allowed_styles narrows on top of css_properties; a property it lists but css_properties omits stays dropped
    policy = _allowed_styles_policy({"*": {"color": [r"^red$"]}}, css_properties=frozenset({"width"}))
    assert "style=" not in sanitize('<p style="color: red">x</p>', policy)


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("color: expression(alert(1))", id="expression"),
        pytest.param("color: url(javascript:alert(1))", id="javascript-url"),
    ],
)
def test_allowed_styles_cannot_admit_dangerous_values(style: str) -> None:
    # the safety baseline runs before the value patterns, so a permissive pattern cannot re-admit dangerous CSS
    policy = _allowed_styles_policy({"*": {"color": [r".*"]}})
    assert "style=" not in sanitize(f'<p style="{style}">x</p>', policy)


def test_allowed_styles_empty_is_a_noop() -> None:
    # the default empty mapping leaves css_properties as the only style filter
    policy = _allowed_styles_policy({})
    assert sanitize('<p style="color: red; width: 5px">x</p>', policy) == '<p style="color: red; width: 5px">x</p>'


def _style_element_policy(*, css_properties: frozenset[str] = DEFAULT_CSS_PROPERTIES) -> Policy:
    """A policy that allowlists the <style> element, so its stylesheet body is scrubbed rather than dropped."""
    return Policy(tags=frozenset({"style"}), attributes={}, css_properties=css_properties)


@pytest.mark.parametrize(
    ("css", "expected_body"),
    [
        pytest.param("p{color:red;position:fixed}", "p{color:red;}", id="keep-one-drop-one"),
        pytest.param("p{position:fixed}", "p{}", id="drop-all-keeps-empty-rule"),
        pytest.param("a{color:red}b{width:5px}", "a{color:red;}b{width:5px;}", id="two-rules"),
        pytest.param("@media screen{p{color:red;position:fixed}}", "@media screen{p{color:red;}}", id="nested-at-rule"),
        pytest.param('@import "evil";p{color:red}', "p{color:red;}", id="at-statement-dropped"),
        pytest.param("p{color:expression(a)}", "p{}", id="expression-value-dropped"),
        pytest.param("p{color:url(javascript:x)}", "p{}", id="url-bad-scheme-dropped"),
        pytest.param("p{color:url(http://ok)}", "p{color:url(http://ok);}", id="url-allowed-scheme-kept"),
        pytest.param("p{color:rgb(1,2,3)}", "p{color:rgb(1,2,3);}", id="parenthesised-value-kept"),
        pytest.param("p{color:red", "p{color:red;}", id="unclosed-block-balanced"),
        pytest.param("}p{color:red}", "p{color:red;}", id="stray-close-brace-dropped"),
        pytest.param('p{content:"a{b};c";color:red}', "p{color:red;}", id="string-holds-separators"),
        pytest.param(
            "p{color:red/* ; : */;width:5px}", "p{color:red/* ; : */;width:5px;}", id="comment-holds-separators"
        ),
        pytest.param(r'p{font-family:"a\";b";color:red}', r'p{font-family:"a\";b";color:red;}', id="string-escape"),
        pytest.param("p{color:red}/", "p{color:red;}", id="trailing-slash-not-comment"),
        pytest.param("p{color:red}/* unterminated", "p{color:red;}", id="unterminated-comment"),
        pytest.param("p{color:red}/* x*", "p{color:red;}", id="comment-trailing-asterisk"),
        pytest.param("p{color:red}/* a*b */", "p{color:red;}", id="comment-lone-asterisk"),
        pytest.param("p{color:1/2}", "p{color:1/2;}", id="slash-not-comment-in-value"),
        pytest.param("p{color:red)}", "p{color:red);}", id="stray-close-paren"),
        pytest.param("p{color:a:b}", "p{color:a:b;}", id="value-has-second-colon"),
        pytest.param("  p  {color:red}", "p{color:red;}", id="prelude-whitespace-trimmed"),
        pytest.param("   {color:red}", "{color:red;}", id="whitespace-only-prelude"),
        pytest.param("p{content:'a;b';color:red}", "p{color:red;}", id="single-quoted-string"),
        pytest.param("p{novalue;color:red}", "p{color:red;}", id="declaration-without-colon-dropped"),
    ],
)
def test_style_element_body_scrubbed(css: str, expected_body: str) -> None:
    # an allowlisted <style> keeps its element and structure; each declaration is vetted like a style attribute
    assert sanitize(f"<style>{css}</style>", _style_element_policy()) == f"<style>{expected_body}</style>"


def test_allowed_styles_does_not_narrow_style_element_body() -> None:
    # allowed_styles targets inline style attributes; a <style> body stays governed by css_properties alone
    policy = Policy(
        tags=frozenset({"style"}),
        attributes={},
        css_properties=frozenset({"color"}),
        allowed_styles={"*": {"color": [r"^blue$"]}},
    )
    assert sanitize("<style>p{color:red}</style>", policy) == "<style>p{color:red;}</style>"


@pytest.mark.parametrize("css", ["", "  "], ids=["no-text-child", "whitespace-only"])
def test_style_element_kept_when_body_empty(css: str) -> None:
    # keeping the element is the point: an empty body leaves <style></style> standing, never escaped or dropped
    assert sanitize(f"<style>{css}</style>", _style_element_policy()) == "<style></style>"


def test_style_element_body_is_idempotent() -> None:
    # re-sanitizing the scrubbed output is a fixpoint, the correctness gate for a value-safe transform
    once = sanitize(
        "<style>a{color:red;position:fixed}@media(min-width:1px){p{width:5px;top:0}}</style>", _style_element_policy()
    )
    assert sanitize(once, _style_element_policy()) == once


def test_style_element_empty_property_set_drops_all_css() -> None:
    # an empty css_properties set means no declaration is allowlisted, so every rule scrubs to an empty block
    assert (
        sanitize("<style>p{color:red}</style>", _style_element_policy(css_properties=frozenset()))
        == "<style>p{}</style>"
    )


def test_style_element_attributes_still_scrubbed() -> None:
    # a kept <style> is a normal allowed element for attributes: a disallowed one is dropped, the body still scrubbed
    out = sanitize('<style bad="x">p{color:red;position:fixed}</style>', _style_element_policy())
    assert out == "<style>p{color:red;}</style>"


def test_style_element_attribute_filter_error_propagates() -> None:
    # a kept <style> runs its surviving attributes through the filter like any element, so a filter error surfaces
    def boom(_tag: str, name: str, _value: str) -> str:
        raise ValueError(name)

    policy = Policy(tags=frozenset({"style"}), attributes={"style": frozenset({"foo"})}, attribute_filter=boom)
    with pytest.raises(ValueError, match="foo"):
        sanitize('<style foo="x">p{color:red}</style>', policy)


def test_style_element_dropped_when_not_allowed() -> None:
    # the baseline is unchanged: a <style> the policy does not allowlist is escaped, never kept as live CSS
    out = sanitize("<style>p{color:red}</style>")
    assert "<style>" not in out
    assert "&lt;style&gt;" in out


def test_style_element_removed_with_content_when_named() -> None:
    # naming style in remove_with_content still drops the whole element, even though the tag is otherwise unsafe-exempt
    policy = Policy(tags=frozenset({"b"}), remove_with_content=frozenset({"style"}))
    assert sanitize("<b>ok</b><style>p{color:red}</style>", policy) == "<b>ok</b>"


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        pytest.param("http://x.com", True, id="http"),
        pytest.param("https://x.com", True, id="https"),
        pytest.param("mailto:a@b.com", True, id="mailto"),
        pytest.param("/relative/path", True, id="relative"),
        pytest.param("#fragment", True, id="fragment"),
        pytest.param("javascript:alert(1)", False, id="javascript"),
        pytest.param("JAVASCRIPT:alert(1)", False, id="javascript-upper"),
        pytest.param("data:text/html,x", False, id="data"),
        pytest.param("ftp://x.com", False, id="ftp-not-allowed"),
        pytest.param("a+very-long.scheme-that-overflows-the-buffer-aaaaaaaaaaaaaaaaaaa:x", False, id="overflow"),
        pytest.param(":no-scheme", True, id="leading-colon-is-relative"),
        pytest.param("h2+t-p.x:y", False, id="scheme-with-digit-plus-dash-dot"),
        pytest.param("java\x7fscript:alert(1)", False, id="del-byte-in-scheme"),
    ],
)
def test_url_scheme_allowlist(url: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]  # kept is the pytest expectation, not a flag
    out = sanitize(f'<a href="{url}">x</a>')
    assert ("href=" in out) is kept


@pytest.mark.parametrize("attr", _URL_ATTRS)
def test_every_url_attribute_is_scheme_checked(attr: str) -> None:
    policy = _allow_all({"x"}, {attr})
    assert sanitize(f'<x {attr}="javascript:alert(1)">t</x>', policy) == "<x>t</x>"


@pytest.mark.parametrize("attr", ["srcset", "imagesrcset"])
@pytest.mark.parametrize(
    ("value", "kept"),
    [
        pytest.param("a.jpg 1x, b.jpg 2x", True, id="relative-candidates"),
        pytest.param("https://ok/a.jpg 1x, https://ok/b.jpg 2x", True, id="allowed-scheme"),
        pytest.param("a.jpg", True, id="single-no-descriptor"),
        pytest.param("", True, id="empty"),
        pytest.param("a.jpg,", True, id="trailing-comma"),
        pytest.param("  ,  a.jpg 1x", True, id="leading-separators"),
        pytest.param("a.jpg\t1x,\nb.jpg\x0c2x", True, id="ascii-whitespace-separators"),
        pytest.param("javascript:alert(1) 1x", False, id="first-candidate-script"),
        pytest.param("a.jpg 1x, javascript:alert(1) 2x", False, id="later-candidate-script"),
        pytest.param("https://ok/a.jpg 1x, vbscript:msgbox(1) 2x", False, id="later-candidate-vbscript"),
    ],
)
def test_srcset_candidate_schemes_are_checked(attr: str, value: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    # a srcset is a comma-separated list of "URL descriptor" candidates; every candidate's scheme is
    # checked and the whole attribute is dropped if any candidate carries a disallowed scheme.
    out = sanitize(f'<img {attr}="{value}">', _allow_all({"img"}, {attr}))
    assert (attr in out) is kept


def test_eleven_char_non_srcset_attribute_is_not_url_checked() -> None:
    # placeholder is eleven characters but is not imagesrcset, so its value is never scheme-checked.
    policy = _allow_all({"x"}, {"placeholder"})
    assert (
        sanitize('<x placeholder="javascript:not-a-url">t</x>', policy) == '<x placeholder="javascript:not-a-url">t</x>'
    )


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param(
            "<a href='http://ok' href='javascript:alert(1)'>x</a>",
            '<a href="http://ok" rel="noopener noreferrer">x</a>',
            id="safe-first-survives",
        ),
        pytest.param("<a href='javascript:alert(1)' href='http://ok'>x</a>", "<a>x</a>", id="unsafe-first-dropped"),
    ],
)
def test_duplicate_url_attribute_collapses_to_first(html: str, expected: str) -> None:
    # the tokenizer drops a duplicate attribute name, keeping the first occurrence (WHATWG), so the
    # sanitizer only ever sees one href: a safe first value survives the scheme check, an unsafe one
    # is dropped, and no disallowed scheme can reach the output.
    assert sanitize(html, Policy.relaxed()) == expected


def test_duplicate_url_attribute_keeps_only_the_first() -> None:
    out = sanitize("<a href='http://a' href='http://b'>x</a>", Policy.relaxed())
    assert out == '<a href="http://a" rel="noopener noreferrer">x</a>'


def test_non_url_attribute_value_is_not_scheme_checked() -> None:
    policy = _allow_all({"x"}, {"title"})
    assert sanitize('<x title="javascript:not-a-url">t</x>', policy) == '<x title="javascript:not-a-url">t</x>'


def test_four_char_non_url_attribute_is_not_scheme_checked() -> None:
    # type is four characters but is none of href/cite/data/ping, so its value is never scheme-checked.
    policy = _allow_all({"x"}, {"type"})
    assert sanitize('<x type="javascript:not-a-url">t</x>', policy) == '<x type="javascript:not-a-url">t</x>'


def test_ten_char_non_url_attribute_is_not_scheme_checked() -> None:
    # data-thing is ten characters, same length as the URL attrs, but is not one: it must not be scheme-checked
    policy = _allow_all({"x"}, {"*"})
    assert sanitize('<x data-thing="javascript:ok">t</x>', policy) == '<x data-thing="javascript:ok">t</x>'


@pytest.mark.parametrize("url", ["ab{cd:x", "a=b:c", "a~b:c", "1http://x"])
def test_unusual_scheme_characters_are_treated_as_relative(url: str) -> None:
    # a byte that is not a scheme character before the colon makes the value a relative URL, which is kept
    assert sanitize(f'<a href="{url}">t</a>') == f'<a href="{url}">t</a>'


@pytest.mark.parametrize(
    "char",
    [chr(0x00AD), chr(0x200B), chr(0x200C), chr(0x200D), chr(0x2060), chr(0xFEFF)],
    ids=["soft-hyphen", "zwsp", "zwnj", "zwj", "word-joiner", "bom"],
)
def test_zero_width_characters_cannot_obfuscate_a_scheme(char: str) -> None:
    # soft hyphen, zero-width, and BOM characters are stripped before the scheme is read, like browsers ignore them
    assert sanitize(f'<a href="java{char}script:alert(1)">x</a>') == "<a>x</a>"


def test_relative_url_dropped_when_disallowed() -> None:
    policy = Policy(allow_relative_urls=False)
    assert sanitize('<a href="/path">x</a>', policy) == "<a>x</a>"


def test_event_handler_attribute_always_dropped() -> None:
    policy = _allow_all({"x"}, {"*"})
    assert sanitize('<x onclick="evil()" title="t">y</x>', policy) == '<x title="t">y</x>'


def test_wildcard_name_allows_any_attribute() -> None:
    policy = _allow_all({"x"}, {"*"})
    assert sanitize('<x data-z="1" title="t">y</x>', policy) == '<x data-z="1" title="t">y</x>'


def test_wildcard_tag_attributes() -> None:
    policy = Policy(tags=frozenset({"a", "span"}), attributes={"*": frozenset({"title"})})
    assert sanitize('<span title="t" id="i">x</span>', policy) == '<span title="t">x</span>'


def test_attribute_filter_rewrites_value() -> None:
    policy = _allow_all({"a"}, {"href"})
    policy = Policy(tags=policy.tags, attributes=policy.attributes, attribute_filter=lambda _t, _n, v: v.upper())
    assert sanitize('<a href="http://x">y</a>', policy) == '<a href="HTTP://X">y</a>'


def test_attribute_filter_drops_with_none() -> None:
    policy = Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href", "title"})},
                    attribute_filter=lambda _t, n, v: None if n == "title" else v)  # fmt: skip
    assert sanitize('<a href="http://x" title="t">y</a>', policy) == '<a href="http://x">y</a>'


def test_attribute_filter_keeps_unchanged_value() -> None:
    policy = Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}, attribute_filter=lambda _t, _n, v: v)
    assert sanitize('<a href="http://x">y</a>', policy) == '<a href="http://x">y</a>'


def test_attribute_filter_must_return_str_or_none() -> None:
    def bad_filter(_t: str, _n: str, _v: str) -> str | None:
        return 42  # ty: ignore[invalid-return-type]  # a deliberately wrong return type, to exercise the runtime check

    policy = Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}, attribute_filter=bad_filter)
    with pytest.raises(TypeError):
        sanitize('<a href="http://x">y</a>', policy)


def test_attribute_filter_exception_propagates() -> None:
    def boom(_tag: str, name: str, _value: str) -> str:
        raise ValueError(name)

    policy = Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}, attribute_filter=boom)
    with pytest.raises(ValueError, match="href"):
        sanitize('<a href="http://x">y</a>', policy)


def test_add_link_rel_added_to_anchor_with_href() -> None:
    assert (
        sanitize('<a href="http://x">y</a>', Policy.relaxed()) == '<a href="http://x" rel="noopener noreferrer">y</a>'
    )


def test_add_link_rel_skipped_without_href() -> None:
    assert sanitize("<a>y</a>", Policy.relaxed()) == "<a>y</a>"


def test_add_link_rel_scans_other_attributes_for_href() -> None:
    # name (same length as href, different bytes) and title (different length) exercise both has_attr comparisons
    assert sanitize('<a name="n" title="t">y</a>', Policy.relaxed()) == '<a name="n" title="t">y</a>'


def test_short_and_o_prefixed_attributes_are_not_event_handlers() -> None:
    policy = _allow_all({"x"}, {"*"})
    # a (one byte: too short to be on*), ox (starts with o but not on), title (does not start with o)
    assert sanitize('<x a="1" ox="2" title="t">y</x>', policy) == '<x a="1" ox="2" title="t">y</x>'


def test_comments_stripped_by_default() -> None:
    assert sanitize("a<!-- c -->b") == "ab"


def test_comments_kept_when_requested() -> None:
    assert sanitize("a<!-- c -->b", Policy(strip_comments=False)) == "a<!-- c -->b"


def test_policy_basic_is_the_default_allowlist() -> None:
    assert Policy.basic().tags == DEFAULT_TAGS


def test_strict_escapes_everything() -> None:
    assert sanitize("<b>x</b>", Policy.strict()) == "&lt;b&gt;x&lt;/b&gt;"


def test_relaxed_allows_rich_content() -> None:
    assert sanitize("<h1>T</h1><table><tr><td>c</td></tr></table>", Policy.relaxed()) == (
        "<h1>T</h1><table><tbody><tr><td>c</td></tr></tbody></table>"
    )


def test_sanitizer_is_reusable() -> None:
    sanitizer = Sanitizer(Policy.relaxed())
    assert sanitizer.sanitize("<h1>a</h1>") == "<h1>a</h1>"
    assert sanitizer.sanitize("<h2>b</h2>") == "<h2>b</h2>"


@pytest.mark.parametrize(
    ("field", "value", "type_name"),
    [
        pytest.param("tags", ["b"], "list", id="tags"),
        pytest.param("url_schemes", ["http"], "list", id="url_schemes"),
        pytest.param("remove_with_content", ["x"], "list", id="remove_with_content"),
        pytest.param("css_properties", "color", "str", id="css_properties"),
        pytest.param("attribute_prefixes", ["data-"], "list", id="attribute_prefixes"),
        pytest.param("media_hosts", ["x.com"], "list", id="media_hosts"),
    ],
)
def test_non_set_policy_field_raises_typeerror(field: str, value: list[str] | str, type_name: str) -> None:
    # a set-typed field given a non-set once reached a bare C SystemError deep in the walk; it now fails with a clear
    # TypeError naming the offending field and the type it got
    policy = Policy(**{field: value})  # ty: ignore[invalid-argument-type]  # the wrong type is what the guard rejects
    with pytest.raises(TypeError, match=rf"Policy\.{field} must be a set or frozenset, got {type_name}"):
        sanitize("<b>x", policy)


class _TagSet(set[str]):
    pass


class _TagFrozenSet(frozenset[str]):
    pass


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param({"b"}, id="set"),
        pytest.param(frozenset({"b"}), id="frozenset"),
        pytest.param(_TagSet({"b"}), id="set-subclass"),
        pytest.param(_TagFrozenSet({"b"}), id="frozenset-subclass"),
    ],
)
def test_set_and_frozenset_tags_are_accepted(tags: frozenset[str]) -> None:
    # every set-like type (including subclasses) passes the type guard and sanitizes normally
    assert sanitize("<b>x</b>", Policy(tags=tags)) == "<b>x</b>"


def test_escape_propagates_a_child_filter_error() -> None:
    def boom(_tag: str, name: str, _value: str) -> str:
        raise ValueError(name)

    # default policy escapes <unknown> and allows the <a> inside it, whose attribute filter raises
    with pytest.raises(ValueError, match="href"):
        sanitize('<unknown><a href="http://x">y</a></unknown>', Policy(attribute_filter=boom))


def test_strip_propagates_a_child_filter_error() -> None:
    def boom(_tag: str, name: str, _value: str) -> str:
        raise ValueError(name)

    policy = Policy(on_disallowed_tag=OnDisallowed.STRIP, attribute_filter=boom)
    with pytest.raises(ValueError, match="href"):
        sanitize('<div><a href="http://x">y</a></div>', policy)


def test_sanitize_rejects_non_element() -> None:
    from turbohtml._html import (  # ruff:ignore[import-outside-top-level]  # exercising the C argument guard directly
        _sanitize,
    )

    # the policy arguments after the element; only the non-element first argument matters to this guard
    policy_args = (
        frozenset(), {}, frozenset(), True, 0, True, None, None, {}, frozenset(), frozenset(), frozenset(), {},
        frozenset(), False, None, {}, {}, False, None, None, False, True, True, True,
    )  # fmt: skip
    with pytest.raises(TypeError):
        _sanitize("not an element", *policy_args)  # ty: ignore[invalid-argument-type]


def test_sanitize_rejects_wrong_arguments() -> None:
    from turbohtml._html import (  # ruff:ignore[import-outside-top-level]  # exercising the C argument parsing directly
        _sanitize,
    )

    with pytest.raises(TypeError):
        _sanitize()  # ty: ignore[missing-argument]  # too few arguments


def test_module_constants_match_bleach_defaults() -> None:
    assert (
        frozenset({
            "a",
            "abbr",
            "acronym",
            "b",
            "blockquote",
            "code",
            "em",
            "i",
            "li",
            "ol",
            "strong",
            "ul",
        })
        == DEFAULT_TAGS
    )
    assert dict(DEFAULT_ATTRIBUTES) == {
        "a": frozenset({"href", "title"}),
        "abbr": frozenset({"title"}),
        "acronym": frozenset({"title"}),
    }
    assert frozenset({"http", "https", "mailto"}) == DEFAULT_SCHEMES


def _prefix_policy(prefixes: frozenset[str]) -> Policy:
    """Allow <a href> plus any attribute matching one of the given name prefixes."""
    return Policy(tags=frozenset({"a"}), attributes={"a": frozenset({"href"})}, attribute_prefixes=prefixes)


def test_attribute_prefix_allows_matching_family() -> None:
    out = sanitize(
        '<a href="http://x" data-id="1" data-role="nav" class="c">y</a>', _prefix_policy(frozenset({"data-"}))
    )
    assert out == '<a href="http://x" data-id="1" data-role="nav">y</a>'


def test_attribute_prefix_multiple_prefixes_each_allow() -> None:
    policy = _prefix_policy(frozenset({"data-", "aria-"}))
    assert sanitize('<a href="http://x" aria-label="l" data-x="1">y</a>', policy) == (
        '<a href="http://x" aria-label="l" data-x="1">y</a>'
    )


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("dat", id="shorter-than-prefix"),
        pytest.param("datax", id="same-length-mismatch"),
        pytest.param("role", id="unrelated"),
    ],
)
def test_attribute_prefix_non_matching_name_dropped(name: str) -> None:
    # a name shorter than the prefix, or the prefix length but a different byte, is not a prefix match
    assert sanitize(f'<a href="http://x" {name}="1">y</a>', _prefix_policy(frozenset({"data-"}))) == (
        '<a href="http://x">y</a>'
    )


def test_attribute_prefix_default_policy_drops_data_attributes() -> None:
    # without a configured prefix set, prefix matching pays nothing and data-* is not admitted
    assert sanitize('<a href="http://x" data-id="1">y</a>') == '<a href="http://x">y</a>'


def test_attribute_prefix_empty_string_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="attribute_prefixes must not contain an empty prefix"):
        sanitize("<a>y</a>", _prefix_policy(frozenset({""})))


def test_attribute_prefix_non_string_raises_typeerror() -> None:
    policy = _prefix_policy(frozenset({123}))  # ty: ignore[invalid-argument-type]  # a non-str prefix is rejected
    with pytest.raises(TypeError, match="attribute_prefixes must contain only str, got int"):
        sanitize("<a>y</a>", policy)


def _value_policy() -> Policy:
    """Allow <a href target> and <b>, restricting <a target> to two literal values."""
    return Policy(
        tags=frozenset({"a", "b"}),
        attributes={"a": frozenset({"href", "target"}), "b": frozenset({"target"})},
        attribute_values={"a": {"target": frozenset({"_blank", "_self"})}},
    )


@pytest.mark.parametrize(
    ("value", "kept"),
    [pytest.param("_blank", True, id="allowed"), pytest.param("_top", False, id="disallowed")],
)
def test_attribute_value_allowlist_restricts_target(value: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    out = sanitize(f'<a href="http://x" target="{value}">y</a>', _value_policy())
    assert (f'target="{value}"' in out) is kept


def test_attribute_value_allowlist_leaves_unrestricted_attribute() -> None:
    # href has no value entry, so its value is not constrained
    assert sanitize('<a href="http://weird" target="_self">y</a>', _value_policy()) == (
        '<a href="http://weird" target="_self">y</a>'
    )


def test_attribute_value_allowlist_leaves_unrestricted_tag() -> None:
    # <b> is not keyed in attribute_values, so its target is unrestricted
    assert sanitize('<b target="_top">y</b>', _value_policy()) == '<b target="_top">y</b>'


def test_attribute_value_allowlist_only_narrows_never_allows() -> None:
    # target is absent from <a>'s attribute allowlist, so a value entry cannot resurrect it
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href"})},
        attribute_values={"a": {"target": frozenset({"_blank"})}},
    )
    assert sanitize('<a href="http://x" target="_blank">y</a>', policy) == '<a href="http://x">y</a>'


def _media_policy(hosts: frozenset[str], *, extra_attrs: frozenset[str] = frozenset()) -> Policy:
    """Allow the embedded-media elements plus <img>, gating their src by host allowlist."""
    return Policy(
        tags=frozenset({"video", "audio", "source", "track", "img"}),
        attributes={"*": frozenset({"src"}) | extra_attrs},
        media_hosts=hosts,
    )


@pytest.mark.parametrize("tag", ["video", "audio", "source", "track"])
def test_media_host_allowlist_keeps_allowed_host(tag: str) -> None:
    policy = _media_policy(frozenset({"youtube.com"}))
    assert f'<{tag} src="https://youtube.com/e/x">' in sanitize(f'<{tag} src="https://youtube.com/e/x">', policy)


@pytest.mark.parametrize(
    ("src", "kept"),
    [
        pytest.param("https://youtube.com/e/x", True, id="scheme-host-path"),
        pytest.param("https://youtube.com", True, id="host-no-path"),
        pytest.param("https://youtube.com?q=1", True, id="host-query"),
        pytest.param("https://youtube.com#f", True, id="host-fragment"),
        pytest.param("https://youtube.com:8080/x", True, id="host-port"),
        pytest.param("https://user@youtube.com/x", True, id="host-userinfo"),
        pytest.param("//youtube.com/x", True, id="protocol-relative"),
        pytest.param("https://evil.com/x", False, id="disallowed-host"),
        pytest.param("https:///x", False, id="empty-host"),
        pytest.param("local.mp4", False, id="relative-no-authority"),
        pytest.param("/local.mp4", False, id="rooted-relative"),
        pytest.param("x", False, id="single-char"),
        pytest.param("https:ab", False, id="allowed-scheme-opaque"),
        pytest.param("https:/x", False, id="allowed-scheme-one-slash"),
    ],
)
def test_media_host_allowlist_gates_src(src: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    out = sanitize(f'<video src="{src}">', _media_policy(frozenset({"youtube.com"})))
    assert ("src=" in out) is kept


def test_media_host_allowlist_matches_host_case_insensitively() -> None:
    assert 'src="https://YouTube.COM/x"' in sanitize(
        '<video src="https://YouTube.COM/x">', _media_policy(frozenset({"youtube.com"}))
    )


def test_media_host_allowlist_rejects_overlong_host() -> None:
    host = "a" * 300 + ".com"
    assert "src=" not in sanitize(f'<video src="https://{host}/x">', _media_policy(frozenset({"youtube.com"})))


def test_media_host_allowlist_does_not_touch_non_media_src() -> None:
    # <img> is not an embedded-media element, so its src is not host-gated
    assert sanitize('<img src="https://evil.com/x">', _media_policy(frozenset({"youtube.com"}))) == (
        '<img src="https://evil.com/x">'
    )


def test_media_host_allowlist_skips_non_src_media_attributes() -> None:
    # only src is host-gated; a sibling attribute on the same media element is untouched
    policy = _media_policy(frozenset({"youtube.com"}), extra_attrs=frozenset({"alt", "width"}))
    out = sanitize('<video src="https://youtube.com/x" alt="a" width="10">', policy)
    assert 'src="https://youtube.com/x"' in out
    assert 'alt="a"' in out
    assert 'width="10"' in out


def test_media_host_default_policy_leaves_src_unrestricted() -> None:
    # with no media_hosts configured, the host check pays nothing and any allowed-scheme src survives
    policy = Policy(tags=frozenset({"video"}), attributes={"*": frozenset({"src"})})
    assert 'src="https://evil.com/x"' in sanitize('<video src="https://evil.com/x">', policy)


@pytest.mark.parametrize(
    ("src", "kept"),
    [
        pytest.param("https://evil.com@youtube.com/x", True, id="userinfo-evil-host-allowed"),
        pytest.param("https://youtube.com@evil.com/x", False, id="userinfo-allowed-host-evil"),
        pytest.param("https://good.com@evil.com/x", False, id="userinfo-and-host-evil"),
        pytest.param("https://user@name@youtube.com/x", True, id="double-at-last-wins"),
        pytest.param("http://youtube.com:80@evil.com/x", False, id="port-shaped-userinfo-host-evil"),
        pytest.param("https://youtube.com.evil.com/x", False, id="suffix-confusion"),
        pytest.param("https://youtube.com.evil.com", False, id="suffix-confusion-no-path"),
        pytest.param("https://youtube.com./x", True, id="trailing-dot-host-listed"),
        pytest.param("https://youtu\tbe.com/x", False, id="embedded-tab-blocked"),
        pytest.param("https://\tyoutube.com/x", False, id="leading-tab-blocked"),
        pytest.param("https://[::1]/x", False, id="ipv6-literal-blocked"),
        pytest.param("https://[::1]:8080/x", False, id="ipv6-literal-with-port-blocked"),
        pytest.param("https://user@[dead::beef]/x", False, id="ipv6-with-userinfo-blocked"),
    ],
)
def test_media_host_allowlist_host_confusion(src: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    # host-confusion parity: userinfo tricks resolve to the real host (the last '@' wins), a suffixed or
    # whitespace-obfuscated host never masquerades as the listed one, and an IPv6 literal is always rejected
    policy = _media_policy(frozenset({"youtube.com", "youtube.com."}))
    out = sanitize(f'<video src="{src}">', policy)
    assert ("src=" in out) is kept


def test_media_host_ipv6_literal_rejected_even_when_listed() -> None:
    # the shared authority parser extracts ::1 from [::1], but the sanitizer still rejects an IPv6 literal so a
    # bracketed host admits no media src -- the pre-unification behavior, kept so routing never weakens the allowlist
    policy = _media_policy(frozenset({"youtube.com", "::1"}))
    assert "src=" not in sanitize('<video src="https://[::1]/x">', policy)


def test_report_records_a_removed_element() -> None:
    out, removed = sanitize_report("<p>ok <script>evil()</script> done</p>")
    assert Removed("script", None) in removed
    assert "evil" in out
    assert "<script>" not in out


def test_report_records_a_dropped_attribute() -> None:
    _, removed = sanitize_report('<a href="http://x" onclick="bad()">k</a>')
    assert removed == [Removed("a", "onclick")]


def test_report_records_a_disallowed_url_attribute() -> None:
    _, removed = sanitize_report('<a href="javascript:alert(1)">x</a>')
    assert removed == [Removed("a", "href")]


def test_report_orders_records_as_the_walk_reaches_them() -> None:
    _, removed = sanitize_report('<a class="c" title="t" href="ftp://x">k</a>')
    assert removed == [Removed("a", "class"), Removed("a", "href")]


def test_report_is_empty_when_nothing_is_dropped() -> None:
    out, removed = sanitize_report('<a href="http://x" title="t">ok</a>')
    assert removed == []
    assert out == '<a href="http://x" title="t">ok</a>'


def test_report_default_attribute_is_none() -> None:
    assert Removed("div") == Removed("div", None)
