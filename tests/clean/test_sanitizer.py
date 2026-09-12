from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast

import pytest
from bench.operations import INPUTS
from typing_extensions import assert_type

from turbohtml import Document, Element, parse, parse_fragment, parse_xml
from turbohtml._html import _sanitize, _sanitize_policy
from turbohtml.clean import (
    DEFAULT_ATTRIBUTES,
    DEFAULT_CSS_PROPERTIES,
    DEFAULT_SCHEMES,
    DEFAULT_TAGS,
    OnDisallowed,
    Policy,
    Removed,
    Sanitizer,
    Transform,
    sanitize,
    sanitize_node,
    sanitize_report,
    sanitize_report_node,
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


def test_deep_input_is_sanitized_without_using_the_c_stack() -> None:
    markup = "<div>" * 1_200 + "bottom" + "</div>" * 1_200
    output = sanitize(markup, Policy(tags=frozenset({"div"})))
    assert (output.count("<div>"), "bottom" in output) == (1_200, True)


@pytest.mark.parametrize(
    ("mode", "expected_open_count"),
    [
        pytest.param(OnDisallowed.STRIP, 0, id="strip"),
        pytest.param(OnDisallowed.ESCAPE, 1_200, id="escape"),
    ],
)
def test_deep_disallowed_input_completes_postorder_actions(mode: OnDisallowed, expected_open_count: int) -> None:
    markup = "<x>" * 1_200 + "bottom" + "</x>" * 1_200
    output = sanitize(markup, Policy(on_disallowed_tag=mode))
    assert (output.count("&lt;x&gt;"), "bottom" in output) == (expected_open_count, True)


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


@pytest.mark.parametrize(
    "tag",
    [
        pytest.param("animate", id="animate"),
        pytest.param("animateColor", id="animate-color"),
        pytest.param("animateMotion", id="animate-motion"),
        pytest.param("animateTransform", id="animate-transform"),
        pytest.param("set", id="set"),
    ],
)
def test_svg_animation_is_blocked_by_an_allowlist(tag: str) -> None:
    policy = Policy(tags=frozenset({"svg", tag}), attributes={"*": frozenset({"*"})})
    assert f"<{tag}" not in sanitize(f'<svg><{tag} attributeName="xlink:href" to="javascript:x"></{tag}></svg>', policy)


def test_svg_animation_name_matching_is_case_adjusted() -> None:
    policy = Policy(tags=frozenset({"svg", "animate"}), attributes={"*": frozenset({"*"})})
    assert "<animate" not in sanitize(
        '<svg><ANIMATE ATTRIBUTENAME="xlink:href" TO="javascript:x"></ANIMATE></svg>', policy
    )


def test_html_element_named_animate_is_not_svg_animation() -> None:
    assert sanitize("<animate>text</animate>", Policy(tags=frozenset({"animate"}))) == "<animate>text</animate>"


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


def test_empty_set_attributes_rule_leaves_element_unchanged() -> None:
    policy = Policy(tags=frozenset({"a"}), set_attributes={"a": {}})
    assert sanitize("<a>x</a>", policy) == "<a>x</a>"


def test_set_attributes_skips_disallowed_elements() -> None:
    # a disallowed tag is escaped, so its set_attributes entry is never applied
    policy = Policy(tags=frozenset({"a"}), set_attributes={"script": {"rel": "x"}})
    assert "rel=" not in sanitize("<a>t</a><script>z</script>", policy)


@pytest.mark.parametrize(
    ("attribute", "value", "expected"),
    [
        pytest.param("href", "javascript:alert(1)", {}, id="url-scheme"),
        pytest.param("onclick", "alert(2)", {}, id="event-handler"),
        pytest.param("style", "color: url(javascript:alert(3))", {}, id="style"),
        pytest.param("srcset", "safe.png 1x, javascript:alert(4) 2x", {}, id="srcset"),
        pytest.param("title", "unsafe", {}, id="configured-value"),
        pytest.param("data-note", "{{secret}}", {"data-note": " "}, id="template-marker"),
        pytest.param("id", "location", {"id": "user-content-location"}, id="named-property"),
        pytest.param("x", "safe", {"x": "safe"}, id="safe-value"),
    ],
)
def test_set_attributes_pass_through_final_safety_checks(attribute: str, value: str, expected: dict[str, str]) -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attribute_values={"a": {"title": frozenset({"safe"})}},
        css_properties=frozenset({"color"}),
        strip_template_markers=True,
        isolate_named_props=True,
        set_attributes={"a": {attribute: value}},
    )
    element = parse_fragment(sanitize("<a>x</a>", policy)).find("a")
    assert isinstance(element, Element)
    assert dict(element.attrs) == expected


def test_set_attributes_canonicalizes_html_names_before_safety_checks() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        css_properties=frozenset({"color"}),
        set_attributes={
            "a": {
                "HREF": "javascript:alert(1)",
                "OnClick": "alert(2)",
                "STYLE": "color:url(javascript:alert(3))",
                "TITLE": "safe",
            }
        },
    )
    element = parse_fragment(sanitize("<a>x</a>", policy)).find("a")
    assert isinstance(element, Element)
    assert dict(element.attrs) == {"title": "safe"}


def test_set_attributes_preserves_foreign_attribute_case() -> None:
    policy = Policy(tags=frozenset({"svg"}), set_attributes={"svg": {"viewBox": "0 0 10 10"}})
    assert sanitize("<svg></svg>", policy) == '<svg viewBox="0 0 10 10"></svg>'


def test_set_attributes_pass_through_media_host_check() -> None:
    policy = Policy(
        tags=frozenset({"p", "video"}),
        media_hosts=frozenset({"media.example"}),
        set_attributes={
            "p": {"title": "text"},
            "video": {"alt": "clip", "src": "https://attacker.example/movie.mp4", "title": "movie"},
        },
    )
    root = parse_fragment(sanitize("<p></p><video></video>", policy))
    paragraph = root.find("p")
    video = root.find("video")
    assert isinstance(paragraph, Element)
    assert isinstance(video, Element)
    assert dict(paragraph.attrs) == {"title": "text"}
    assert dict(video.attrs) == {"alt": "clip", "title": "movie"}


def test_final_safety_pass_keeps_non_event_o_attribute() -> None:
    policy = Policy(tags=frozenset({"details"}), set_attributes={"details": {"open": ""}})
    assert sanitize("<details>x</details>", policy) == '<details open="">x</details>'


def test_final_safety_pass_keeps_safe_style() -> None:
    policy = Policy(
        tags=frozenset({"p"}),
        css_properties=frozenset({"color"}),
        set_attributes={"p": {"style": "color: red"}},
    )
    assert sanitize("<p>x</p>", policy) == '<p style="color: red">x</p>'


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
        pytest.param("é: red", None, id="non-ascii-property-name"),
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
        pytest.param("color: url(http://x y)", False, id="url-unquoted-whitespace"),
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
        pytest.param("color: url(", False, id="url-open-paren-at-end"),
        pytest.param("color: url(http://x", False, id="url-unterminated"),
        pytest.param("color: url/* unterminated", True, id="url-unterminated-comment"),
        pytest.param("color: url/* x*", True, id="url-comment-trailing-asterisk"),
        pytest.param("color: url/* a*b */(http://x)", True, id="url-comment-lone-asterisk-then-allowed"),
        pytest.param("color: a-b_1c", True, id="value-identifier-punctuation"),
        pytest.param("cursor: identifierlong(javascript:alert(1))", True, id="long-identifier"),
        pytest.param("cursor: url(#fragment)", True, id="fragment-url"),
        pytest.param("cursor: url(java\u200bscript:alert(1))", False, id="ignorable-format-character"),
        pytest.param("cursor: url(:relative)", True, id="colon-before-scheme"),
        pytest.param("cursor: url(h1:opaque)", False, id="scheme-with-digit"),
        pytest.param(f"cursor: url({'h' * 41}:opaque)", False, id="overlong-scheme"),
        pytest.param("cursor: url(https://example.com/(x)", False, id="open-paren-in-unquoted-url"),
        pytest.param("cursor: url(https://example.com/'x)", False, id="single-quote-in-unquoted-url"),
        pytest.param("cursor: url(https://example.com/\x7fx)", False, id="delete-in-unquoted-url"),
        pytest.param("cursor: url(https://example.com/\x01x)", False, id="control-in-unquoted-url"),
        pytest.param("cursor: url('https://example.com/\fpath')", False, id="form-feed-in-quoted-url"),
    ],
)
def test_style_value_rejects_expression_and_bad_url_scheme(style: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    # a kept property still has its value scrubbed: expression() and url(disallowed-scheme) drop the whole declaration
    out = sanitize(f'<p style="{style}">x</p>', _style_policy())
    assert ("style=" in out) is kept


@pytest.mark.parametrize(
    ("style", "kept"),
    [
        pytest.param(r"cursor: u\72l(javascript:alert(1))", False, id="escaped-function-hex"),
        pytest.param(r"cursor: u\72 l(javascript:alert(1))", False, id="escaped-function-hex-terminator"),
        pytest.param(r"cursor: u\rl(javascript:alert(1))", False, id="escaped-function-simple"),
        pytest.param(r"cursor: U\000052L(JaVaScRiPt:alert(1))", False, id="escaped-function-mixed-case"),
        pytest.param(r"cursor: url(jav\61script:alert(1))", False, id="escaped-scheme"),
        pytest.param(r'cursor: u\72l("jav\61script:alert(1)")', False, id="escaped-quoted-url"),
        pytest.param("cursor: u\\\nrl(javascript:alert(1))", False, id="escaped-newline"),
        pytest.param("cursor: \\", False, id="trailing-escape"),
        pytest.param(r"width: expr\65ssion(alert(1))", False, id="escaped-expression"),
        pytest.param(r"cursor: url(https://example.com/a), u\72l(jav\61script:alert(1))", False, id="multiple-urls"),
        pytest.param(r"cursor: url(jav\110000-script:x)", True, id="invalid-code-point-is-not-script"),
        pytest.param(r"cursor: url(jav\0 -script:x)", True, id="null-escape-is-not-script"),
        pytest.param(r"cursor: url(jav\d800 -script:x)", True, id="surrogate-escape-is-not-script"),
        pytest.param(r"cursor: url(jav\1f642 -script:x)", True, id="non-bmp-code-point-is-not-script"),
        pytest.param(r"cursor: abcdefghijk\0000612", True, id="six-digit-escape-before-hex"),
        pytest.param("cursor: url(jav\\\nascript:alert(1))", False, id="escaped-newline-in-url"),
        pytest.param(r'cursor: url("https://example.com" trailing)', False, id="garbage-after-quoted-url"),
        pytest.param(r'cursor: url("https://example.com)', False, id="unterminated-quoted-url"),
        pytest.param('cursor: url("https://example.com\npath")', False, id="newline-in-quoted-url"),
        pytest.param(r'cursor: url(https://example.com/"x)', False, id="quote-in-unquoted-url"),
    ],
)
def test_style_value_decodes_css_escapes(style: str, *, kept: bool) -> None:
    assert ("style=" in sanitize(f"<p style='{style}'>x</p>", _style_policy())) is kept


@pytest.mark.parametrize(
    "prefix_length",
    [pytest.param(40, id="inline-buffer"), pytest.param(96, id="grown-buffer")],
)
def test_style_url_scheme_lookup_does_not_truncate_long_names(prefix_length: int) -> None:
    allowed_prefix = "h" * prefix_length
    policy = Policy(
        tags=frozenset({"p"}),
        attributes={"p": frozenset({"style"})},
        css_properties=frozenset({"cursor"}),
        url_schemes=frozenset({allowed_prefix}),
    )
    assert "style=" not in sanitize(f'<p style="cursor: url({allowed_prefix}h:opaque)">x</p>', policy)


@pytest.mark.parametrize(
    ("style", "kept"),
    [
        pytest.param("content: 'a\\\rb'", True, id="carriage-return"),
        pytest.param("content: 'a\\\r\nb'", True, id="crlf"),
        pytest.param("content: 'a\\", False, id="trailing-string-escape"),
        pytest.param("content: 'a\\zb'", True, id="simple-string-escape"),
        pytest.param("content: \\61", True, id="hex-escape-at-end"),
        pytest.param("content: \\61\r", True, id="hex-escape-carriage-return-at-end"),
        pytest.param("content: \\61\rblue", True, id="hex-escape-carriage-return-terminator"),
        pytest.param("content: \\61\r\nblue", True, id="hex-escape-crlf-terminator"),
    ],
)
def test_set_style_scanner_handles_escape_boundaries(style: str, *, kept: bool) -> None:
    policy = Policy(
        tags=frozenset({"p"}),
        css_properties=frozenset({"content", "cursor"}),
        set_attributes={"p": {"style": style}},
    )
    assert ("style=" in sanitize("<p>x</p>", policy)) is kept


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("behavior: url(#x)", id="behavior"),
        pytest.param("BEHAVIOR: url(#x)", id="behavior-case"),
        pytest.param(r"beha\76ior: url(#x)", id="behavior-escape"),
        pytest.param("beha\\\nvior: url(#x)", id="invalid-property-escape"),
        pytest.param("-moz-binding: url(#x)", id="moz-binding"),
        pytest.param(r"-moz-b\69nding: url(#x)", id="moz-binding-escape"),
    ],
)
def test_legacy_executable_css_properties_cannot_be_allowlisted(style: str) -> None:
    policy = _style_policy(
        css_properties=frozenset({"behavior", "BEHAVIOR", r"beha\76ior", "-moz-binding", r"-moz-b\69nding"})
    )
    assert "style=" not in sanitize(f'<p style="{style}">x</p>', policy)


@pytest.mark.parametrize(
    "style",
    [
        pytest.param("content: 'url(javascript:alert(1))'", id="string"),
        pytest.param("color: /* url(javascript:alert(1)) */ red", id="comment"),
        pytest.param(r"cursor: safe\ url(javascript:alert(1))", id="escaped-identifier-space"),
        pytest.param("cursor: urlish(javascript:alert(1))", id="longer-identifier"),
    ],
)
def test_css_safety_scanner_ignores_inert_tokens(style: str) -> None:
    policy = _style_policy(css_properties=frozenset({"color", "content", "cursor"}))
    assert "style=" in sanitize(f'<p style="{style}">x</p>', policy)


def test_css_safety_scanner_keeps_url_text_inside_non_ascii_identifier() -> None:
    policy = _style_policy(css_properties=frozenset({"cursor"}))
    assert 'style="cursor: \u00e9url(javascript:alert(1))"' in sanitize(
        '<p style="cursor: \u00e9url(javascript:alert(1))">x</p>', policy
    )


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


@pytest.mark.parametrize(
    "style",
    [
        pytest.param(r"cursor:u\72l(javascript:alert(1))", id="escaped-function"),
        pytest.param(r"cursor:url(jav\61script:alert(1))", id="escaped-scheme"),
    ],
)
def test_style_element_body_decodes_css_escapes(style: str) -> None:
    policy = _style_element_policy(css_properties=frozenset({"cursor"}))
    once = sanitize(f"<style>p{{{style}}}</style>", policy)
    assert (once, sanitize(once, policy)) == ("<style>p{}</style>", once)


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


@pytest.mark.parametrize(
    ("tag", "html", "policy"),
    [
        pytest.param(
            "a",
            '<a href="/safe">x</a>',
            Policy(
                tags=frozenset({"a"}),
                attributes={"a": frozenset({"href"})},
                attribute_filter=lambda _tag, _name, _value: "javascript:alert(1)",
            ),
            id="url",
        ),
        pytest.param(
            "img",
            '<img srcset="safe.png 1x">',
            Policy(
                tags=frozenset({"img"}),
                attributes={"img": frozenset({"srcset"})},
                attribute_filter=lambda _tag, _name, _value: "safe.png 1x, javascript:alert(1) 2x",
            ),
            id="srcset",
        ),
        pytest.param(
            "p",
            '<p style="color: red">x</p>',
            Policy(
                tags=frozenset({"p"}),
                attributes={"p": frozenset({"style"})},
                css_properties=frozenset({"color"}),
                attribute_filter=lambda _tag, _name, _value: "color: url(javascript:alert(1))",
            ),
            id="style",
        ),
        pytest.param(
            "p",
            '<p style="cursor: auto">x</p>',
            Policy(
                tags=frozenset({"p"}),
                attributes={"p": frozenset({"style"})},
                css_properties=frozenset({"cursor"}),
                attribute_filter=lambda _tag, _name, _value: "cursor: u\\72\r\nl(javascript:alert(1))",
            ),
            id="style-escaped-crlf-terminator",
        ),
        pytest.param(
            "a",
            '<a title="safe">x</a>',
            Policy(
                tags=frozenset({"a"}),
                attributes={"a": frozenset({"title"})},
                attribute_values={"a": {"title": frozenset({"safe"})}},
                attribute_filter=lambda _tag, _name, _value: "unsafe",
            ),
            id="configured-value",
        ),
        pytest.param(
            "video",
            '<video src="https://media.example/movie.mp4"></video>',
            Policy(
                tags=frozenset({"video"}),
                attributes={"video": frozenset({"src"})},
                media_hosts=frozenset({"media.example"}),
                attribute_filter=lambda _tag, _name, _value: "https://attacker.example/movie.mp4",
            ),
            id="media-host",
        ),
    ],
)
def test_attribute_filter_replacement_passes_through_final_safety_checks(tag: str, html: str, policy: Policy) -> None:
    element = parse_fragment(sanitize(html, policy)).find(tag)
    assert isinstance(element, Element)
    assert dict(element.attrs) == {}


def test_attribute_filter_replacement_passes_through_final_rewrites() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"id", "title"})},
        strip_template_markers=True,
        isolate_named_props=True,
        attribute_filter=lambda _tag, name, _value: "location" if name == "id" else "{{secret}}",
    )
    element = parse_fragment(sanitize('<a id="safe" title="safe">x</a>', policy)).find("a")
    assert isinstance(element, Element)
    assert dict(element.attrs) == {"id": "user-content-location", "title": " "}


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


@pytest.fixture
def policy() -> Policy:
    return Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href", "title", "id", "style"})},
        attribute_prefixes=frozenset({"data-"}),
        css_properties=frozenset({"color"}),
    )


@pytest.mark.parametrize("count", [0, 31, 32, 1_024])
@pytest.mark.parametrize("prefix", ["bad-", "data-"], ids=["rejected", "allowed"])
def test_attribute_compaction_count(policy: Policy, count: int, prefix: str) -> None:
    attributes: Final = "".join(f' {prefix}{index}="x"' for index in range(count))
    assert Sanitizer(policy).sanitize(f"<a{attributes}>x</a>") == (
        f"<a{attributes}>x</a>" if prefix == "data-" else "<a>x</a>"
    )


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        pytest.param('href="javascript:alert(1)"', "", id="unsafe-url"),
        pytest.param('onclick="alert(1)"', "", id="event-handler"),
        pytest.param('style="color: red; position: fixed"', ' style="color: red"', id="style-safety"),
        pytest.param('href="/safe"', ' href="/safe"', id="safe-url"),
    ],
)
def test_attribute_compaction_safety(policy: Policy, attribute: str, expected: str) -> None:
    rejected: Final = "".join(f' bad-{index}="x"' for index in range(32))
    assert Sanitizer(policy).sanitize(f"<a{rejected} {attribute}>x</a>") == f"<a{expected}>x</a>"


def test_attribute_compaction_retains_order(policy: Policy) -> None:
    attributes: Final = "".join(f' bad-{index}="x" data-{index}="{index}"' for index in range(32))
    expected: Final = "".join(f' data-{index}="{index}"' for index in range(32))
    assert Sanitizer(policy).sanitize(f"<a{attributes}>x</a>") == f"<a{expected}>x</a>"


def test_attribute_compaction_report_order(policy: Policy) -> None:
    rejected: Final = "".join(f' bad-{index}="x"' for index in range(32))
    assert Sanitizer(policy).sanitize_report(f'<a first="x" href="javascript:x" onclick="x"{rejected}>x</a>') == (
        "<a>x</a>",
        [Removed("a", name) for name in ("first", "href", "onclick", *(f"bad-{index}" for index in range(32)))],
    )


def test_attribute_compaction_filter_order(policy: Policy) -> None:
    names: Final[list[str]] = []

    def record(_tag: str, name: str, value: str) -> str:
        names.append(name)
        return value

    attributes: Final = "".join(f' bad-{index}="x" data-{index}="x"' for index in range(32))
    Sanitizer(replace(policy, attribute_filter=record)).sanitize(f"<a{attributes}>x</a>")
    assert names == [f"data-{index}" for index in range(32)]


@pytest.mark.parametrize("prefix", ["bad-", "data-"], ids=["custom-attributes", "custom-element"])
def test_attribute_compaction_custom_checks(policy: Policy, prefix: str) -> None:
    attributes: Final = "".join(f' {prefix}{index}="x"' for index in range(32))
    sanitizer: Final = Sanitizer(
        replace(
            policy,
            custom_element_check=lambda tag: tag == "x-card",
            custom_attribute_check=(lambda _tag, name: name.startswith("bad-")) if prefix == "bad-" else None,
        )
    )
    assert sanitizer.sanitize(f"<x-card{attributes}>x</x-card>") == f"<x-card{attributes}>x</x-card>"


def test_attribute_compaction_late_writes(policy: Policy) -> None:
    rejected: Final = "".join(f' bad-{index}="x"' for index in range(32))
    sanitizer: Final = Sanitizer(
        replace(policy, set_attributes={"a": {"href": "javascript:x", "title": "kept", "onclick": "x"}})
    )
    assert sanitizer.sanitize(f"<a{rejected}>x</a>") == '<a title="kept">x</a>'


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        pytest.param('id="heading"', 'id="user-content-heading"', id="named-property"),
        pytest.param('title="{{value}}"', 'title=" "', id="template-marker"),
    ],
)
def test_attribute_compaction_value_rewrites(policy: Policy, attribute: str, expected: str) -> None:
    rejected: Final = "".join(f' bad-{index}="x"' for index in range(32))
    sanitizer: Final = Sanitizer(replace(policy, isolate_named_props=True, strip_template_markers=True))
    assert sanitizer.sanitize(f"<a{rejected} {attribute}>x</a>") == f"<a {expected}>x</a>"


def test_attribute_compaction_copies_source(policy: Policy) -> None:
    html: Final = "<a" + "".join(f' bad-{index}="x"' for index in range(32)) + ' href="/safe">x</a>'
    source: Final = parse_fragment(html)
    sanitized: Final = Sanitizer(policy).sanitize_node(source)
    assert (source.inner_html, sanitized.inner_html) == (html, '<a href="/safe">x</a>')


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        pytest.param(0, "<p>x</p>", id="rejected"),
        pytest.param(1, "<p" + "".join(f' data-{index}="x"' for index in range(1_024)) + ">x</p>", id="allowed"),
        pytest.param(2, '<p data-a="x" data-b="x" data-c="x" data-d="x">x</p>', id="tiny"),
    ],
)
def test_sanitize_attribute_benchmark(case: int, expected: str) -> None:
    sanitizer: Final = Sanitizer(Policy(tags=frozenset({"p"}), attribute_prefixes=frozenset({"data-"})))
    assert sanitizer.sanitize(cast("str", INPUTS["sanitize-attributes"]()[case][1])) == expected


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


# The set of tags the reachability check reasons over, allowed together so only the namespace relationship can drop one.
_FOREIGN_TAGS = frozenset({"svg", "math", "foreignObject", "annotation-xml", "mtext", "circle", "p", "b", "i"})
_FOREIGN_POLICY = Policy(tags=_FOREIGN_TAGS, attributes={"annotation-xml": frozenset({"encoding"})})


def _find(root: Element, tag: str, namespace: str | None = None) -> Element:
    """Return the first element named ``tag`` (optionally in ``namespace``) in document order."""
    queue: list[Element] = list(getattr(root, "children", ()))
    while queue:
        node = queue.pop(0)
        if isinstance(node, Element) and node.tag == tag and (namespace is None or node.namespace.value == namespace):
            return node
        queue[0:0] = list(getattr(node, "children", ()))
    msg = f"no {tag!r} element found"
    raise AssertionError(msg)


def _sanitize_tree(root: Element, tags: frozenset[str]) -> str:
    # named to keep the boolean positional arguments off the FBT003 lint, not to document them
    allow_relative = strip_comments = True
    strip_templates = isolate_named_props = allow_customized_builtins = False
    allow_html = allow_svg = allow_mathml = True
    empty: frozenset[str] = frozenset()
    schemes = frozenset({"http", "https", "mailto"})
    original = root.inner_html
    sanitized = _sanitize(
        root, tags, {}, schemes, allow_relative, OnDisallowed.REMOVE.value, strip_comments, None, None, {}, empty,
        empty, empty, {}, empty, strip_templates, None, {}, {}, isolate_named_props, None, None,
        allow_customized_builtins, allow_html, allow_svg, allow_mathml,
    )  # fmt: skip
    assert root.inner_html == original
    return sanitized.inner_html


def test_find_helper_reports_a_missing_element() -> None:
    """The navigation helper fails loudly rather than returning None when a case names a tag that is not present."""
    with pytest.raises(AssertionError, match="no 'span' element"):
        _find(parse_fragment("<div></div>"), "span")


@pytest.mark.parametrize(
    ("html", "policy"),
    [
        pytest.param("<svg>x</svg>", _FOREIGN_POLICY, id="svg-enters-from-html"),
        pytest.param("<math>x</math>", _FOREIGN_POLICY, id="math-enters-from-html"),
        pytest.param("<svg><circle></circle></svg>", _FOREIGN_POLICY, id="svg-child-of-svg"),
        pytest.param("<svg><foreignObject><p>hi</p></foreignObject></svg>", _FOREIGN_POLICY, id="html-under-svg-point"),
        pytest.param("<math><mtext><b>hi</b></mtext></math>", _FOREIGN_POLICY, id="html-under-mathml-text-point"),
        pytest.param(
            '<math><annotation-xml encoding="text/html"><b>hi</b></annotation-xml></math>',
            _FOREIGN_POLICY,
            id="html-under-annotation-xml",
        ),
        pytest.param(
            '<math><annotation-xml encoding="application/xhtml+xml"><i>hi</i></annotation-xml></math>',
            _FOREIGN_POLICY,
            id="html-under-annotation-xml-xhtml",
        ),
        pytest.param("<svg><foreignObject><math>y</math></foreignObject></svg>", _FOREIGN_POLICY, id="math-under-svg"),
        pytest.param("<math><mtext><svg>z</svg></mtext></math>", _FOREIGN_POLICY, id="svg-under-mathml-text-point"),
        pytest.param(
            "<math><annotation-xml><svg>z</svg></annotation-xml></math>", _FOREIGN_POLICY, id="svg-under-annotation-xml"
        ),
    ],
)
def test_namespace_reachable_foreign_content_untouched(html: str, policy: Policy) -> None:
    """Every namespace transition the parser produces is reachable, so the check leaves valid foreign content as-is."""
    assert sanitize(html, policy) == parse_fragment(html).inner_html


# Each case parses valid markup, then moves a real foreign/HTML node under a parent the parser would never give it,
# producing a namespace-confused node the reachability check must drop. move is (source, source-namespace, target):
# source-namespace disambiguates a name that exists in more than one namespace. marker is the node's start tag, absent
# once it is dropped and present while correctly nested.
_CONFUSION_CASES = [
    pytest.param(
        "<div></div><svg><circle></circle></svg>", ("circle", "svg", "div"), {"div", "svg", "circle"}, "<circle",
        id="svg-element-reparented-under-html",
    ),
    pytest.param(
        "<math><mrow></mrow></math><svg></svg>", ("svg", "svg", "mrow"), {"math", "mrow", "svg"}, "<svg",
        id="svg-under-non-integration-mathml",
    ),
    pytest.param(
        "<div></div><math><mrow></mrow></math>", ("mrow", "math", "div"), {"div", "math", "mrow"}, "<mrow",
        id="mathml-element-reparented-under-html",
    ),
    pytest.param(
        "<math></math><svg><g></g></svg>", ("math", "math", "g"), {"math", "svg", "g"}, "<math",
        id="math-under-non-integration-svg",
    ),
    pytest.param(
        "<svg><g></g></svg><p></p>", ("p", "html", "g"), {"svg", "g", "p"}, "<p",
        id="html-under-non-integration-svg",
    ),
    pytest.param(
        "<math><mrow></mrow></math><p></p>", ("p", "html", "mrow"), {"math", "mrow", "p"}, "<p",
        id="html-under-non-integration-mathml",
    ),
]  # fmt: skip


@pytest.mark.parametrize(("html", "move", "tags", "marker"), _CONFUSION_CASES)
def test_namespace_confusion_is_dropped(
    html: str, move: tuple[str, str, str], tags: frozenset[str], marker: str
) -> None:
    """A node reparented into an unreachable namespace is dropped, while the same node correctly nested is kept."""
    assert marker in _sanitize_tree(parse_fragment(html), tags)  # correctly nested: the policy keeps it
    source, source_ns, target = move
    root = parse_fragment(html)
    _find(root, target).append(_find(root, source, source_ns))
    assert marker not in _sanitize_tree(root, tags)  # confused by the move: the reachability check drops it


_POST = "<p onclick='x'>Hi <b>there</b> <script>evil()</script></p>"


def test_node_form_returns_a_sanitized_copy_of_the_same_kind() -> None:
    root = parse_fragment(_POST)
    clean = assert_type(sanitize_node(root, Policy.relaxed()), Element)
    assert isinstance(clean, Element)
    assert clean.inner_html == sanitize(_POST, Policy.relaxed())


def test_the_source_is_left_untouched() -> None:
    root = parse_fragment(_POST)
    before = root.inner_html
    sanitize_node(root, Policy.relaxed())
    assert root.inner_html == before


def test_the_node_itself_is_the_kept_context() -> None:
    # the policy never judges the node passed in, only its descendants, like the fragment root of the string form
    script = parse_fragment("<script>evil()</script>").select_one("script")
    assert script is not None
    clean = sanitize_node(script, Policy.relaxed())
    assert isinstance(clean, Element)
    assert clean.tag == "script"


def test_a_document_has_its_html_element_judged() -> None:
    document = parse("<p onclick='x'>hi</p>")
    clean = assert_type(sanitize_node(document, Policy(tags=frozenset({"html", "head", "body", "p"}))), Document)
    assert isinstance(clean, Document)
    assert clean.serialize() == "<html><head></head><body><p>hi</p></body></html>"


def test_a_document_under_a_fragment_policy_loses_its_shell() -> None:
    clean = sanitize_node(parse("<p>hi</p>"), Policy(tags=frozenset({"p"}), on_disallowed_tag=OnDisallowed.STRIP))
    assert clean.serialize() == "<p>hi</p>"


def test_the_copy_inherits_the_xml_flag() -> None:
    root = parse_xml("<r><b/><script>x</script></r>").root
    assert root is not None
    clean = sanitize_node(root, Policy(tags=frozenset({"b"}), on_disallowed_tag=OnDisallowed.STRIP))
    assert clean.inner_xml == "<b/>x"


def test_the_string_forms_accept_a_node() -> None:
    root = parse_fragment(_POST)
    assert sanitize(root, Policy.relaxed()) == sanitize(_POST, Policy.relaxed())
    assert sanitize_report(root, Policy.relaxed()) == sanitize_report(_POST, Policy.relaxed())


def test_the_report_form_pairs_the_copy_with_the_drops() -> None:
    clean, removed = assert_type(
        sanitize_report_node(parse_fragment(_POST), Policy.relaxed()), tuple[Element, list[Removed]]
    )
    assert clean.inner_html == "<p>Hi <b>there</b> &lt;script&gt;evil()&lt;/script&gt;</p>"
    assert removed == [Removed(tag="p", attribute="onclick"), Removed(tag="script", attribute=None)]


def test_a_reusable_sanitizer_offers_both_node_forms() -> None:
    sanitizer = Sanitizer(Policy.relaxed())
    root = parse_fragment(_POST)
    assert assert_type(sanitizer.sanitize_node(root), Element).inner_html == sanitizer.sanitize(_POST)
    assert (
        assert_type(sanitizer.sanitize_report_node(root), tuple[Element, list[Removed]])[1]
        == sanitizer.sanitize_report(_POST)[1]
    )


@pytest.mark.parametrize("entry", [sanitize_node, sanitize_report_node], ids=["sanitize_node", "sanitize_report_node"])
def test_the_node_forms_refuse_a_str(entry: object) -> None:
    with pytest.raises(TypeError, match="pass a str to sanitize instead"):
        entry(_POST)  # ty: ignore[call-non-callable]  # the argument check is the point


@pytest.mark.parametrize("entry", [sanitize, sanitize_node], ids=["sanitize", "sanitize_node"])
def test_a_foreign_object_is_rejected(entry: object) -> None:
    with pytest.raises(TypeError):
        entry(42)  # ty: ignore[call-non-callable]  # the argument check is the point


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


XSS_CORPUS = [
    pytest.param("<script>alert(1)</script>", id="script"),
    pytest.param("<scr<script>ipt>alert(1)</scr</script>ipt>", id="nested-script"),
    pytest.param("<img src=x onerror=alert(1)>", id="img-onerror"),
    pytest.param("<a href='javascript:alert(1)'>x</a>", id="js-url"),
    pytest.param("<a href='java\tscript:alert(1)'>x</a>", id="js-url-tab"),
    pytest.param("<a href='java\nscript:alert(1)'>x</a>", id="js-url-newline"),
    pytest.param("<a href='&#106;avascript:alert(1)'>x</a>", id="js-url-entity"),
    pytest.param("<a href='ja&#x09;vascript:alert(1)'>x</a>", id="js-url-hex-entity"),
    pytest.param("<a href='JaVaScRiPt:alert(1)'>x</a>", id="js-url-case"),
    pytest.param("<a href='  javascript:alert(1)'>x</a>", id="js-url-leading-space"),
    pytest.param("<a href='\x01javascript:alert(1)'>x</a>", id="js-url-control"),
    pytest.param("<a href='data:text/html,<script>alert(1)</script>'>x</a>", id="data-url"),
    pytest.param("<a href='vbscript:msgbox(1)'>x</a>", id="vbscript-url"),
    pytest.param(
        "<svg><iframe><a title='</a><img src=x onerror=alert(1)>'>x</a></iframe></svg>", id="svg-ns-confusion"
    ),
    pytest.param(
        "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)></style></table></mtext></math>",
        id="mathml-mglyph",
    ),
    pytest.param("<svg><a><circle/></a></svg>", id="svg-a-not-html-a"),
    pytest.param("<noscript><p title='</noscript><img src=x onerror=alert(1)>'>", id="noscript-context"),
    pytest.param("<style><img src=x onerror=alert(1)></style>", id="style-rawtext"),
    pytest.param("<title><img src=x onerror=alert(1)></title>", id="title-rawtext"),
    pytest.param("<textarea><img src=x onerror=alert(1)></textarea>", id="textarea-rcdata"),
    pytest.param("<xmp><img src=x onerror=alert(1)></xmp>", id="xmp-rawtext"),
    pytest.param("<!-- --><img src=x onerror=alert(1)>", id="comment-then-img"),
    pytest.param("<!--<img src=x onerror=alert(1)>-->", id="img-in-comment"),
    pytest.param("<![CDATA[<img src=x onerror=alert(1)>]]>", id="cdata"),
    pytest.param("<svg></p><style><a id=</style><img src=x onerror=alert(1)>", id="svg-style-attr-breakout"),
    pytest.param(
        "<form><math><mtext></form><form><mglyph><style></math><img src onerror=alert(1)>", id="form-math-mutation"
    ),
    pytest.param("<select><noscript><svg><style></select><img src onerror=alert(1)>", id="select-noscript-svg"),
    pytest.param("<b><i></b></i><img src=x onerror=alert(1)>", id="misnested-adoption"),
    pytest.param('<a href="javascript:alert(1)" onmouseover=alert(2)>x</a>', id="js-url-plus-handler"),
    pytest.param("<p title='\"><img src=x onerror=alert(1)>'>safe</p>", id="attr-value-breakout"),
    pytest.param("<a href='http://ok' href='javascript:alert(1)'>x</a>", id="dup-href-js-second"),
    pytest.param("<a href='javascript:alert(1)' href='http://ok'>x</a>", id="dup-href-js-first"),
    pytest.param("<a href='http://ok' href='data:text/html,<script>alert(1)</script>'>x</a>", id="dup-href-data"),
    pytest.param('<a href="http://ok" href="vbscript:msgbox(1)">x</a>', id="dup-href-vbscript"),
]

_MODES = [
    pytest.param(Policy(), id="escape"),
    pytest.param(Policy(on_disallowed_tag=OnDisallowed.STRIP), id="strip"),
    pytest.param(Policy(on_disallowed_tag=OnDisallowed.REMOVE), id="remove"),
    pytest.param(Policy.relaxed(), id="relaxed"),
    pytest.param(Policy.strict(), id="strict"),
]


@pytest.mark.parametrize("payload", XSS_CORPUS)
@pytest.mark.parametrize("policy", _MODES)
def test_no_live_danger(payload: str, policy: Policy) -> None:
    danger = _live_danger(sanitize(payload, policy))
    assert danger == [], f"live XSS survived: {danger}"


@pytest.mark.parametrize("payload", XSS_CORPUS)
@pytest.mark.parametrize("policy", _MODES)
def test_round_trip_invariant(payload: str, policy: Policy) -> None:
    once = sanitize(payload, policy)
    assert sanitize(once, policy) == once, "sanitizing is not idempotent: a live mutation-XSS"


def test_oracle_detects_a_real_handler() -> None:
    # guard the guard: _live_danger must flag an event handler that is genuinely live
    assert _live_danger('<img src=x onerror="alert(1)">') == ["@onerror"]


def test_oracle_detects_a_real_script() -> None:
    assert _live_danger("<script>alert(1)</script>") == ["<script>"]


def test_oracle_detects_a_dangerous_url() -> None:
    assert _live_danger('<a href="javascript:alert(1)">x</a>') == ["href=javascript:alert(1)"]


# Scheme-evasion parity: routing the sanitizer's scheme scan onto the shared grammar predicates keeps the exact
# allow/block decision. Every obfuscated dangerous scheme stays blocked; the benign counterparts stay allowed (a
# regression there would be over-blocking, not a hole, but the allowlist is only useful if normal URLs survive).
_SCHEME_PARITY = [
    pytest.param("javascript:alert(1)", False, id="javascript"),
    pytest.param("JaVaScRiPt:alert(1)", False, id="javascript-mixed-case"),
    pytest.param("DATA:text/html,x", False, id="data-upper"),
    pytest.param("vbscript:msgbox(1)", False, id="vbscript"),
    pytest.param("VBScript:msgbox(1)", False, id="vbscript-mixed-case"),
    pytest.param("about:blank", False, id="about"),
    pytest.param("blob:https://example.com/u", False, id="blob"),
    pytest.param("  javascript:alert(1)", False, id="leading-spaces"),
    pytest.param("\tjavascript:alert(1)", False, id="leading-tab"),
    pytest.param("\njavascript:alert(1)", False, id="leading-newline"),
    pytest.param("\rjavascript:alert(1)", False, id="leading-cr"),
    pytest.param("\x01javascript:alert(1)", False, id="leading-control"),
    pytest.param("\x1fjavascript:alert(1)", False, id="leading-unit-separator"),
    pytest.param("java\tscript:alert(1)", False, id="embedded-tab"),
    pytest.param("java\nscript:alert(1)", False, id="embedded-newline"),
    pytest.param("java\x7fscript:alert(1)", False, id="embedded-del"),
    pytest.param("java­script:alert(1)", False, id="embedded-soft-hyphen"),
    pytest.param("java\u200bscript:alert(1)", False, id="embedded-zero-width-space"),
    pytest.param("java‌script:alert(1)", False, id="embedded-zero-width-non-joiner"),
    pytest.param("java‍script:alert(1)", False, id="embedded-zero-width-joiner"),
    pytest.param("java⁠script:alert(1)", False, id="embedded-word-joiner"),
    pytest.param("java﻿script:alert(1)", False, id="embedded-bom"),
    pytest.param("javascript\t:alert(1)", False, id="tab-before-colon"),
    pytest.param("javascript­:alert(1)", False, id="soft-hyphen-before-colon"),
    pytest.param("­javascript:alert(1)", False, id="leading-soft-hyphen"),
    pytest.param("﻿javascript:alert(1)", False, id="leading-bom"),
    pytest.param("tel:+15551234", False, id="tel-not-allowlisted"),
    pytest.param("ftp://x.com", False, id="ftp-not-allowlisted"),
    pytest.param("http://example.com", True, id="http"),
    pytest.param("https://example.com/a?b#c", True, id="https-with-query-fragment"),
    pytest.param("HtTp://ok.com", True, id="http-mixed-case"),
    pytest.param("  https://ok.com", True, id="https-leading-space"),
    pytest.param("mailto:a@b.com", True, id="mailto"),
    pytest.param("MAILTO:a@b.com", True, id="mailto-upper"),
    pytest.param("/relative/path", True, id="rooted-relative"),
    pytest.param("relative/path", True, id="bare-relative"),
    pytest.param("#fragment", True, id="fragment-only"),
    pytest.param(":no-scheme", True, id="leading-colon-relative"),
    pytest.param("//other.example/x", True, id="protocol-relative"),
    pytest.param("1javascript:alert(1)", True, id="digit-prefixed-scheme-is-relative"),
]


@pytest.mark.parametrize(("url", "kept"), _SCHEME_PARITY)
def test_scheme_allowlist_parity(url: str, kept: bool) -> None:  # ruff:ignore[boolean-type-hint-positional-argument]
    assert ("href=" in sanitize(f'<a href="{url}">x</a>', Policy())) is kept


@pytest.mark.parametrize(
    "payload",
    [
        # foreign-content start/end asymmetry: </li> synthesizes a sibling on the second parse.
        pytest.param("<li><math><mtext><li>", id="li-math-mtext-nesting"),
        # a raw carriage return normalizes to a newline when the sanitized text is reparsed.
        pytest.param("a&#xd;b", id="cr-normalization"),
        pytest.param("<div>&#xd;</div>", id="cr-in-element"),
    ],
)
def test_inert_even_when_not_string_idempotent(payload: str) -> None:
    # These are benign inputs whose sanitized form is *not* byte-identical on a second pass
    # (foreign-content nesting shifts, CR->LF normalization). Sanitization is single-pass, so the
    # guarantee is inertness, not string idempotence: no executable construct survives either pass,
    # even though sanitize(sanitize(x)) != sanitize(x). Consumers must trust the first pass, not reparse.
    once = sanitize(payload)
    twice = sanitize(once)
    assert once != twice, "expected a non-idempotent case; move it to the round-trip corpus if it stabilizes"
    assert _live_danger(once) == []
    assert _live_danger(twice) == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("<select><plaintext></select><img src=x onerror=alert(1)>", id="plaintext-in-select"),
        pytest.param("<plaintext><img src=x onerror=alert(1)>", id="bare-plaintext"),
    ],
)
def test_allowlisted_plaintext_is_neutralized(payload: str) -> None:
    # a custom policy may allowlist <plaintext>, but its content is raw text that cannot
    # be escaped once the element is kept, so a </select><img onerror> tail would reparse
    # into a live image. Like <xmp>, <plaintext> must always be neutralized (#72).
    policy = Policy(tags=frozenset({"plaintext", "select", "img"}), attributes={"img": frozenset({"src"})})
    out = sanitize(payload, policy)
    assert _live_danger(out) == [], f"live XSS survived: {out!r}"
    assert sanitize(out, policy) == out, "sanitizing is not idempotent"


_SANITIZER_TEMPLATES_ON = Policy(
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
    assert sanitize(fragment, _SANITIZER_TEMPLATES_ON) == expected


def test_templates_attribute_value_with_marker_collapses() -> None:
    assert sanitize('<a href="/x" title="{{t}}">k</a>', _SANITIZER_TEMPLATES_ON) == '<a href="/x" title=" ">k</a>'


def test_templates_attribute_value_without_marker_unchanged() -> None:
    assert sanitize('<a href="/x" title="plain">k</a>', _SANITIZER_TEMPLATES_ON) == '<a href="/x" title="plain">k</a>'


def test_templates_valueless_attribute_survives() -> None:
    assert sanitize("<input disabled>", _SANITIZER_TEMPLATES_ON) == '<input disabled="">'


def test_templates_off_by_default_keeps_markers() -> None:
    keep = Policy(tags=frozenset({"p"}))
    assert sanitize("<p>a{{x}}b</p>", keep) == "<p>a{{x}}b</p>"


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


def test_transform_canonicalizes_html_names_before_safety_checks() -> None:
    policy = Policy(
        tags=frozenset({"a"}),
        attributes={"a": frozenset({"href", "onclick", "title"})},
        transform_tags={"b": Transform("A", {"HREF": "javascript:x", "OnClick": "steal()", "TITLE": "safe"})},
    )
    assert sanitize("<b>x</b>", policy) == '<a title="safe">x</a>'


def test_transform_canonicalizes_unsafe_html_target() -> None:
    policy = Policy(tags=frozenset({"script"}), transform_tags={"b": "SCRIPT"})
    assert sanitize("<b>x</b>", policy) == "&lt;script&gt;x&lt;/script&gt;"


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


_XML = Policy(
    tags=frozenset({"p", "br", "a", "strong", "em", "img", "svg", "rect", "math", "mi"}),
    attributes={"a": frozenset({"href"}), "img": frozenset({"src"})},
    xml=True,
)


def _well_formed(fragment: str) -> None:
    """Assert a sanitized fragment reparses as XML once wrapped in a single root, so it is well-formed."""
    parse_xml(f"<root>{fragment}</root>")


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        pytest.param("<p>a<br>b</p>", "<p>a<br/>b</p>", id="void-self-closes"),
        pytest.param("<img src=a>", '<img src="a"/>', id="void-with-attr"),
        pytest.param("<strong>x & y < z</strong>", "<strong>x &amp; y &lt; z</strong>", id="text-escaped"),
        pytest.param('<a href="?a=1&b=2">l</a>', '<a href="?a=1&amp;b=2">l</a>', id="attr-escaped"),
        pytest.param("<svg><rect></svg>", '<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>', id="svg-namespace"),
        pytest.param(
            "<math><mi>x</mi></math>",
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
            id="mathml-namespace",
        ),
    ],
)
def test_xml_output_is_well_formed(html: str, expected: str) -> None:
    out = sanitize(html, _XML)
    assert out == expected
    _well_formed(out)


def test_control_characters_are_dropped_from_xml() -> None:
    out = sanitize("<p>bad\x0cchar\x01here</p>", _XML)
    assert out == "<p>badcharhere</p>"
    _well_formed(out)


def test_kept_comment_is_neutralized_for_xml() -> None:
    policy = Policy(tags=frozenset({"p"}), strip_comments=False, xml=True)
    out = sanitize("<p>a</p><!-- c--d- -->", policy)
    assert out == "<p>a</p><!-- c- -d- -->"
    _well_formed(out)


def test_default_policy_still_emits_html() -> None:
    assert sanitize("<p>a<br>b") == "&lt;p&gt;a&lt;br&gt;b"
    assert sanitize("<a href='http://x'>l</a>") == '<a href="http://x">l</a>'


def test_disallowed_tag_escaped_stays_well_formed() -> None:
    out = sanitize("<p>ok</p><script>evil()</script>", Policy(tags=frozenset({"p"}), xml=True))
    assert "<script" not in out
    _well_formed(out)


def test_report_reports_drops_and_emits_xml() -> None:
    out, removed = sanitize_report("<p>keep<br><span>drop</span></p>", _XML)
    assert out == "<p>keep<br/>&lt;span&gt;drop&lt;/span&gt;</p>"
    assert [item.tag for item in removed] == ["span"]
    _well_formed(out)


def test_sanitizer_instance_reuses_xml_policy() -> None:
    cleaner = Sanitizer(_XML)
    assert cleaner.sanitize("<br>") == "<br/>"
    assert cleaner.sanitize("<img src=x>") == '<img src="x"/>'


_XML_ORACLE_POLICY: Final = Policy(
    tags=frozenset({"p", "br", "a", "strong", "em", "img", "ul", "li", "svg", "rect", "circle", "math", "mi"}),
    attributes={"a": frozenset({"href"}), "img": frozenset({"src", "alt"})},
    strip_comments=False,
    xml=True,
)


@pytest.mark.parametrize(
    ("html", "tags", "text"),
    [
        pytest.param("<p>a<br>b<br>c</p>", ["p"], "abc", id="voids"),
        pytest.param("<ul><li>one<li>two</ul>", ["ul"], "onetwo", id="implied-end-tags"),
        pytest.param('<a href="?x=1&y=2&z">link & more</a>', ["a"], "link & more", id="entities"),
        pytest.param("<p>a<!-- c--d- -->b</p>", ["p"], "ab", id="comment"),
        pytest.param("<svg><rect/><circle/></svg>", ["svg"], "", id="svg"),
        pytest.param("<math><mi>x</mi></math>", ["math"], "x", id="mathml"),
        pytest.param("<p>ctrl\x0c\x01chars</p>", ["p"], "ctrlchars", id="control-chars"),
        pytest.param("<img src=x alt='a<b\"c'>", ["img"], "", id="attr-specials"),
        pytest.param(
            "<p>text</p><script>evil()</script><em>more</em>",
            ["p", "em"],
            "text<script>evil()</script>more",
            id="escaped-script",
        ),
    ],
)
@pytest.mark.oracle
def test_output_parses_under_lxml(html: str, tags: list[str], text: str) -> None:
    lxml_etree: Final = pytest.importorskip("lxml.etree")
    fragment = sanitize(html, _XML_ORACLE_POLICY)
    root = lxml_etree.fromstring(f"<root>{fragment}</root>".encode())
    assert ([lxml_etree.QName(child).localname for child in root], root.xpath("string(.)")) == (tags, text)


_DOMPURIFY_FIXTURE = Path(__file__).parent / "data" / "dompurify_expect.mjs"


@dataclass(frozen=True)
class _DompurifyCase:
    payload: str
    accepted: frozenset[str]
    title: str


def _load_cases() -> list[_DompurifyCase]:
    """Extract the ``{payload, expected}`` entries from the ESM fixture by decoding each object with the JSON reader."""
    body = _DOMPURIFY_FIXTURE.read_text(encoding="utf-8").split("export default", 1)[1]
    decoder = json.JSONDecoder()
    index = body.index("[") + 1
    cases: list[_DompurifyCase] = []
    while True:
        while body[index] in " \t\r\n,":  # whitespace and the array/element separators between entries
            index += 1
        if body[index] == "]":
            return cases
        entry, index = decoder.raw_decode(body, index)
        expected = entry["expected"]
        accepted = frozenset(expected if isinstance(expected, list) else [expected])
        cases.append(_DompurifyCase(entry["payload"], accepted, entry.get("title", "")))


_DOMPURIFY_CASES = _load_cases()
_DOMPURIFY_IDS = [
    f"{position:03d}-{re.sub(r'[^a-z0-9]+', '-', case.title.lower()).strip('-')[:48] or 'untitled'}"
    for position, case in enumerate(_DOMPURIFY_CASES)
]

# A max-permissive adversarial policy: keep the whole HTML/SVG/MathML structural surface and every attribute name so the
# only thing removing danger is the non-configurable C baseline (unsafe tags, on* handlers, non-allowlisted schemes,
# CSS scrubbing). This is the strongest bypass surface -- a restrictive policy would escape most payloads to inert text
# and never exercise a kept, attack-capable element.
_DOMPURIFY_PERMISSIVE_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "button", "caption", "cite", "code", "col", "colgroup", "dd", "del", "div",
    "dl", "dt", "em", "figcaption", "figure", "form", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input",
    "ins", "label", "li", "mark", "ol", "option", "p", "pre", "q", "s", "script", "select", "small", "span",
    "strong", "style",
    "sub", "sup", "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "tr", "u", "ul",
    "svg", "g", "rect", "circle", "path", "defs", "filter", "fegaussianblur", "image", "title", "desc",
    "foreignobject", "text", "use", "animate", "animateColor", "animateMotion", "animateTransform", "set",
    "math", "mi", "mo", "mn", "ms", "mtext", "mrow", "mglyph", "annotation-xml",
})  # fmt: skip
_DOMPURIFY_PERMISSIVE = Policy(
    tags=_DOMPURIFY_PERMISSIVE_TAGS,
    attributes=MappingProxyType({"*": frozenset({"*"})}),
    url_schemes=DEFAULT_SCHEMES,
    css_properties=DEFAULT_CSS_PROPERTIES,
)
_DOMPURIFY_POLICIES = [
    pytest.param(Policy(), id="default"),
    pytest.param(Policy.relaxed(), id="relaxed"),
    pytest.param(_DOMPURIFY_PERMISSIVE, id="permissive"),
]

# Scriptable elements that execute or load code if they survive in the HTML namespace; scheme prefixes that run script.
_DOMPURIFY_DANGER_TAGS = frozenset({"script", "iframe", "object", "embed", "frame", "style", "noscript", "base"})
_DOMPURIFY_DANGER_SCHEMES = ("javascript:", "data:", "vbscript:")
_DOMPURIFY_URL_ATTRS = frozenset({
    "href",
    "src",
    "action",
    "xlink:href",
    "formaction",
    "poster",
    "background",
    "cite",
    "ping",
})
_DOMPURIFY_SVG_ANIMATION_TAGS = frozenset({"animate", "animateColor", "animateMotion", "animateTransform", "set"})
# CSS constructs that execute or fetch when a kept <style> body or style attribute survives the property scrub.
_DOMPURIFY_CSS_DANGER = ("javascript:", "vbscript:", "expression(", "@import", "behavior:", "-moz-binding")


def _attr_value(raw: str | list[str] | None) -> str:
    """The lowercased attribute value, joining a duplicate-attribute list and reading a boolean attr as empty."""
    if raw is None:
        return ""
    joined = " ".join(raw) if isinstance(raw, list) else raw
    return joined.lower()


def _style_body(element: Element) -> str:
    """The lowercased text content of a ``<style>`` element (rawtext, so its children are text nodes)."""
    return "".join(getattr(child, "data", "") for child in element.children).lower()


def _bad_scheme(value: str) -> bool:
    """Whether a URL value resolves to a script-capable scheme once the control/whitespace obfuscation is stripped."""
    return "".join(char for char in value if ord(char) > 0x20).startswith(_DOMPURIFY_DANGER_SCHEMES)


def _dangerous_element(node: Element) -> str | None:
    namespace = node.namespace.value
    if namespace == "svg" and node.tag in _DOMPURIFY_SVG_ANIMATION_TAGS:
        attrs = {name.lower(): _attr_value(value) for name, value in node.attrs.items()}
        target = attrs.get("attributename", "")
        if target.startswith("on"):
            return f"<{node.tag}>->{target}"
        if target in _DOMPURIFY_URL_ATTRS:
            for name in ("from", "to", "values"):
                if any(_bad_scheme(value) for value in attrs.get(name, "").split(";")):
                    return f"<{node.tag}>->{target}"
    if node.tag == "script" and namespace in {"html", "svg"}:
        return "<script>"
    if node.tag == "style" and namespace in {"html", "svg"}:
        return "style-body" if any(token in _style_body(node) for token in _DOMPURIFY_CSS_DANGER) else None
    if node.tag not in _DOMPURIFY_DANGER_TAGS or namespace != "html":
        return None
    return f"<{node.tag}>"


def _dangerous_attribute(name: str, value: str) -> str | None:
    """A label for an executable attribute: an ``on*`` handler, dangerous CSS on ``style``, or a scriptable URL."""
    if name.startswith("on"):
        return f"@{name}"
    if name == "style":
        return "@style" if any(token in value for token in _DOMPURIFY_CSS_DANGER) else None
    return f"{name}={value[:32]}" if name in _DOMPURIFY_URL_ATTRS and _bad_scheme(value) else None


def _live_danger(html: str) -> list[str]:
    """Reparse sanitized HTML and list every executable construct that survived; an empty list means inert."""
    survived: list[str] = []
    stack = list(parse_fragment(html).children)
    while stack:
        node = stack.pop()
        if isinstance(node, Element):
            if (element_hit := _dangerous_element(node)) is not None:
                survived.append(element_hit)
            survived.extend(
                hit
                for name, raw in node.attrs.items()
                if (hit := _dangerous_attribute(name, _attr_value(raw))) is not None
            )
            stack.extend(node.children)
    return survived


@pytest.mark.parametrize("policy", _DOMPURIFY_POLICIES)
@pytest.mark.parametrize("case", _DOMPURIFY_CASES, ids=_DOMPURIFY_IDS)
def test_dompurify_payload_sanitizes_to_inert(case: _DompurifyCase, policy: Policy) -> None:
    survived = _live_danger(sanitize(case.payload, policy))
    assert survived == [], (
        f"sanitizer bypass -- executable markup survived DOMPurify payload {case.title!r}: {survived}"
    )


@pytest.mark.parametrize("case", _DOMPURIFY_CASES, ids=_DOMPURIFY_IDS)
def test_turbohtml_is_never_less_safe_than_dompurify(case: _DompurifyCase) -> None:
    # "output in the accepted set" is the wrong metric on its own: DOMPurify keeps data: image URIs and does not scrub
    # CSS, so its accepted outputs carry constructs turbohtml strips by design, and the two allowlists differ. The
    # security-equivalence claim is the one that holds every time -- turbohtml's output is byte-identical to an accepted
    # DOMPurify output, or, where the allowlists diverge, still provably inert. It is never a downgrade.
    out = sanitize(case.payload, _DOMPURIFY_PERMISSIVE)
    assert out in case.accepted or _live_danger(out) == [], f"downgrade vs DOMPurify on {case.title!r}: {out!r}"


def test_attr_value_normalizes_every_shape() -> None:
    assert (_attr_value(None), _attr_value(["A", "B"]), _attr_value("HrEf")) == ("", "a b", "href")


# Guard the oracle: each row pins the exact label _live_danger yields (or [] for the inert counterpart), so a checker
# that stopped detecting a class would fail here rather than silently green-light a bypass in the corpus run above.
@pytest.mark.parametrize(
    ("html", "survived"),
    [
        pytest.param("<script>alert(1)</script>", ["<script>"], id="scriptable-element"),
        pytest.param("<svg><script>alert(1)</script></svg>", ["<script>"], id="svg-script"),
        pytest.param("<style>a{background:url(javascript:alert(1))}</style>", ["style-body"], id="style-body-danger"),
        pytest.param("<style>a{color:red}</style>", [], id="style-body-benign"),
        pytest.param("<svg><style>a{behavior:url(#x)}</style></svg>", ["style-body"], id="svg-style-danger"),
        pytest.param("<svg><style>a{fill:red}</style></svg>", [], id="svg-style-benign"),
        pytest.param('<img src=x onerror="alert(1)">', ["@onerror"], id="event-handler"),
        pytest.param('<p style="behavior:url(#x)">x</p>', ["@style"], id="style-attr-danger"),
        pytest.param('<p style="color:red">x</p>', [], id="style-attr-benign"),
        pytest.param('<a href="javascript:alert(1)">x</a>', ["href=javascript:alert(1)"], id="url-danger"),
        pytest.param('<a href="https://example.com">x</a>', [], id="url-benign"),
        pytest.param(
            '<svg><animate attributeName="xlink:href" from="javascript:x"></animate></svg>',
            ["<animate>->xlink:href"],
            id="svg-animation-from",
        ),
        pytest.param(
            '<svg><set attributeName="XLINK:HREF" to="JAVASCRIPT:x"></set></svg>',
            ["<set>->xlink:href"],
            id="svg-animation-to-case",
        ),
        pytest.param(
            '<svg><animateTransform attributeName="xlink:href" values="https://safe;javascript:x"></animateTransform></svg>',
            ["<animateTransform>->xlink:href"],
            id="svg-animation-values",
        ),
        pytest.param(
            '<svg><animate attributeName="onload" to="alert(1)"></animate></svg>',
            ["<animate>->onload"],
            id="svg-animation-handler",
        ),
        pytest.param(
            '<svg><animate attributeName="fill" to="red"></animate></svg>',
            [],
            id="svg-animation-non-url-target",
        ),
        pytest.param(
            '<svg><animate attributeName="xlink:href" from="https://example.com"></animate></svg>',
            [],
            id="svg-animation-safe-url",
        ),
        pytest.param(
            '<animate attributeName="xlink:href" from="javascript:x"></animate>',
            [],
            id="html-animation-name-inert",
        ),
        pytest.param("<iframe></iframe>", ["<iframe>"], id="dangerous-html-element"),
    ],
)
def test_live_danger_labels_every_executable_construct(html: str, survived: list[str]) -> None:
    assert _live_danger(html) == survived


@pytest.mark.parametrize("method", [False, True], ids=["function", "sanitizer"])
def test_document_node_return_types(*, method: bool) -> None:
    document: Final = parse("<p onclick='x'>hi</p>")
    policy: Final = Policy(tags=frozenset({"html", "head", "body", "p"}))
    sanitizer: Final = Sanitizer(policy)
    cleaned: Final = assert_type(sanitizer.sanitize_node(document), Document)
    result: Final = assert_type(
        sanitizer.sanitize_report_node(document) if method else sanitize_report_node(document, policy),
        tuple[Document, list[Removed]],
    )
    assert (cleaned.serialize(), result[0].serialize(), result[1]) == (
        "<html><head></head><body><p>hi</p></body></html>",
        "<html><head></head><body><p>hi</p></body></html>",
        [Removed("p", "onclick")],
    )
