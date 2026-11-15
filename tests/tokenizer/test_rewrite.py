from __future__ import annotations

from bisect import bisect_left
from functools import partial
from typing import TYPE_CHECKING

import pytest

from turbohtml import Comment, Namespace, SelectorSyntaxError, parse, parse_fragment
from turbohtml import Element as ParsedElement
from turbohtml.rewrite import Element, rewrite

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from traced_bytes import PeakBytes
    from work_steps import WorkSteps
    from wpt_tree_corpus import WptHtmlTreeCorpus

    from turbohtml import Document, ShadowRoot, SourceLocation
from typing import Final

_Read = str | bool | None | tuple[tuple[str, str | None], ...]


def test_rewrite_noop_returns_input_unchanged() -> None:
    src = '<!DOCTYPE html><html><body><?pi data?><p class=x>Hi &amp; bye</p><img src="a.png"></body></html>'
    assert rewrite(src) == src


def test_rewrite_empty_input() -> None:
    assert not rewrite("")


def test_rewrite_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        rewrite(b"<p>")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


@pytest.mark.parametrize(
    ("source", "selector", "expected"),
    [
        pytest.param("<p>x</p>", "p", '<p k="v">x</p>', id="type"),
        pytest.param("<p>x</p><a>y</a>", "*", '<p k="v">x</p><a k="v">y</a>', id="universal"),
        pytest.param('<p class="a b">x</p><p>y</p>', ".b", '<p class="a b" k="v">x</p><p>y</p>', id="class"),
        pytest.param('<p id="one">x</p><p>y</p>', "#one", '<p id="one" k="v">x</p><p>y</p>', id="id"),
        pytest.param("<a href=/1>x</a><a>y</a>", "[href]", '<a href="/1" k="v">x</a><a>y</a>', id="attr-exists"),
        pytest.param(
            "<a href=/1>x</a><a href=/2>y</a>", '[href="/1"]', '<a href="/1" k="v">x</a><a href=/2>y</a>', id="attr-eq"
        ),
        pytest.param("<a data-x=1>x</a><a>y</a>", "[data-x]", '<a data-x="1" k="v">x</a><a>y</a>', id="attr-custom"),
        pytest.param(
            "<div><span>a</span></div><span>b</span>",
            "div span",
            '<div><span k="v">a</span></div><span>b</span>',
            id="descendant",
        ),
        pytest.param(
            "<ul><li>a</li></ul><ol><li>b</li></ol>",
            "ul > li",
            '<ul><li k="v">a</li></ul><ol><li>b</li></ol>',
            id="child",
        ),
        pytest.param(
            "<html><body><p>x</p></body></html>", ":root", '<html k="v"><body><p>x</p></body></html>', id="root"
        ),
        pytest.param(
            "<h1>a</h1><h2>b</h2><p>c</p>", ":is(h1, h2)", '<h1 k="v">a</h1><h2 k="v">b</h2><p>c</p>', id="is"
        ),
        pytest.param("<a class=x>1</a><a>2</a>", "a:not(.x)", '<a class=x>1</a><a k="v">2</a>', id="not"),
        pytest.param(
            "<input type=checkbox checked><input type=checkbox>",
            ":checked",
            '<input type="checkbox" checked k="v"><input type=checkbox>',
            id="checked",
        ),
        pytest.param("<a href=/1>x</a><a>y</a>", ":any-link", '<a href="/1" k="v">x</a><a>y</a>', id="any-link"),
        # a stream keeps no earlier siblings, so it cannot tell a fieldset's first legend from a later one and
        # every control below a disabled fieldset stays read-only
        pytest.param(
            "<fieldset disabled><legend><input></legend></fieldset><input>",
            "input:read-write",
            '<fieldset disabled><legend><input></legend></fieldset><input k="v">',
            id="read-write-disabled-fieldset-legend",
        ),
    ],
)
def test_rewrite_streamable_selectors_match(source: str, selector: str, expected: str) -> None:
    assert rewrite(source, elements=[(selector, _set("k", "v"))]) == expected


def _set(name: str, value: str) -> Callable[[Element], None]:
    def handler(element: Element) -> None:
        element.set_attribute(name, value)

    return handler


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param("a + p", id="adjacent-sibling"),
        pytest.param("a ~ p", id="general-sibling"),
        pytest.param("p:first-child", id="first-child"),
        pytest.param("p:last-child", id="last-child"),
        pytest.param("p:only-child", id="only-child"),
        pytest.param("p:nth-child(2)", id="nth-child"),
        pytest.param("p:first-of-type", id="first-of-type"),
        pytest.param("p:nth-of-type(1)", id="nth-of-type"),
        pytest.param("p:empty", id="empty"),
        pytest.param("div:has(p)", id="has"),
        pytest.param(":scope p", id="scope"),
        pytest.param("input:disabled", id="disabled"),
        pytest.param("p:not(:last-child)", id="not-nested-unstreamable"),
    ],
)
def test_rewrite_rejects_unstreamable_selector(selector: str) -> None:
    with pytest.raises(SelectorSyntaxError):
        rewrite("<p>x</p>", elements=[(selector, _set("k", "v"))])


def test_rewrite_rejects_malformed_selector() -> None:
    with pytest.raises(SelectorSyntaxError):
        rewrite("<p>x</p>", elements=[("p[", _set("k", "v"))])


def test_rewrite_set_attribute_adds_and_replaces() -> None:
    def handler(element: Element) -> None:
        element.set_attribute("data-new", "1")
        element.set_attribute("class", "changed")

    assert rewrite('<p class="old">x</p>', elements=[("p", handler)]) == '<p class="changed" data-new="1">x</p>'


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        pytest.param("<p>x</p>", '<p t="&quot;&amp;&lt;&gt;">x</p>', id="set-value"),
        pytest.param(
            '<p title="&lt;/style&gt;">x</p>',
            '<p title="&lt;/style&gt;" t="&quot;&amp;&lt;&gt;">x</p>',
            id="source-value",
        ),
    ],
)
def test_rewrite_edited_start_tag_escapes_attribute_values(src: str, expected: str) -> None:
    assert rewrite(src, elements=[("p", _set("t", '"&<>'))]) == expected


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        pytest.param("<img hidden/=q>", '<img hidden="" =q k="v">', id="equals-name-after-bare"),
        pytest.param("<img b/=m/=n>", '<img b="" =m="" =n k="v">', id="two-in-a-row"),
        pytest.param('<img c=""/=m>', '<img c="" =m k="v">', id="equals-name-after-empty-value"),
        pytest.param("<img =m c>", '<img =m c k="v">', id="equals-name-first"),
        pytest.param("<img a b>", '<img a b k="v">', id="ordinary-bare-names-stay-bare"),
    ],
)
def test_rewrite_edited_start_tag_keeps_equals_name_from_becoming_a_value(src: str, expected: str) -> None:
    assert rewrite(src, elements=[("img", _set("k", "v"))]) == expected


def test_rewrite_remove_attribute_keeps_equals_name_from_becoming_a_value() -> None:
    def handler(element: Element) -> None:
        element.remove_attribute("b")

    assert rewrite("<a rel b=1 =m>y</a>", elements=[("a", handler)]) == '<a rel="" =m>y</a>'


def test_rewrite_set_attribute_empty_name_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        rewrite("<p>x</p>", elements=[("p", _set("", "v"))])


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("data-x onclick", id="space"),
        pytest.param("a>", id="greater-than"),
        pytest.param("a/", id="slash"),
        pytest.param("x=y", id="equals"),
        pytest.param("a\0", id="nul"),
    ],
)
def test_rewrite_set_attribute_rejects_name_the_dom_rejects(name: str) -> None:
    with pytest.raises(ValueError, match="invalid character"):
        rewrite("<img>", elements=[("img", _set(name, "v"))])


def test_rewrite_set_attribute_non_str_name_raises() -> None:
    def handler(element: Element) -> None:
        element.set_attribute(1, "v")  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection

    with pytest.raises(TypeError):
        rewrite("<p>x</p>", elements=[("p", handler)])


def test_rewrite_remove_attribute_present_and_absent() -> None:
    def handler(element: Element) -> None:
        element.remove_attribute("data-x")
        element.remove_attribute("data-missing")
        element.remove_attribute("data-custom-gone")

    assert rewrite("<p data-x=1 keep=2>t</p>", elements=[("p", handler)]) == '<p keep="2">t</p>'


def test_rewrite_get_and_has_attribute() -> None:
    seen: dict[str, _Read] = {}

    def handler(element: Element) -> None:
        seen["value"] = element.get("href")
        seen["valueless"] = element.get("hidden")
        seen["missing"] = element.get("nope")
        seen["default"] = element.get("nope", "fallback")
        seen["has"] = element.has_attribute("href")
        seen["has_not"] = element.has_attribute("nope")
        seen["tag"] = element.tag
        seen["attrs"] = element.attrs
        seen["kind"] = element.kind
        seen["removed"] = element.removed

    rewrite("<a href=/x hidden>t</a>", elements=[("a", handler)])
    assert seen == {
        "value": "/x",
        "valueless": None,
        "missing": None,
        "default": "fallback",
        "has": True,
        "has_not": False,
        "tag": "a",
        "attrs": (("href", "/x"), ("hidden", None)),
        "kind": "element",
        "removed": False,
    }


def test_rewrite_before_and_after() -> None:
    def handler(element: Element) -> None:
        element.before("[")
        element.after("]")

    assert rewrite("<a>hi</a>", elements=[("a", handler)]) == "[<a>hi</a>]"


def test_rewrite_before_after_escape_and_raw() -> None:
    def handler(element: Element) -> None:
        element.before("<b>", html=True)
        element.after("<i>")

    assert rewrite("<a>hi</a>", elements=[("a", handler)]) == "<b><a>hi</a>&lt;i&gt;"


def test_rewrite_after_accumulates() -> None:
    def handler(element: Element) -> None:
        element.after("1", html=True)
        element.after("2", html=True)

    assert rewrite("<a>x</a>", elements=[("a", handler)]) == "<a>x</a>12"


def test_rewrite_prepend_and_append() -> None:
    def handler(element: Element) -> None:
        element.prepend("(", html=True)
        element.prepend("[", html=True)
        element.append(")", html=True)
        element.append("]", html=True)

    assert rewrite("<p>x</p>", elements=[("p", handler)]) == "<p>([x)]</p>"


def test_rewrite_set_content_replaces_inner() -> None:
    def handler(element: Element) -> None:
        element.set_content("<b>new</b>", html=True)

    assert rewrite("<div>old<span>y</span></div>", elements=[("div", handler)]) == "<div><b>new</b></div>"


def test_rewrite_set_content_escapes() -> None:
    def handler(element: Element) -> None:
        element.set_content("<b>")

    assert rewrite("<div>old</div>", elements=[("div", handler)]) == "<div>&lt;b&gt;</div>"


def test_rewrite_replace_drops_subtree() -> None:
    def handler(element: Element) -> None:
        element.replace("<x>Y</x>", html=True)

    assert rewrite("<b>a<i>b</i></b>C", elements=[("b", handler)]) == "<x>Y</x>C"


def test_rewrite_replace_escapes() -> None:
    def handler(element: Element) -> None:
        element.replace("<x>")

    assert rewrite("<b>a</b>", elements=[("b", handler)]) == "&lt;x&gt;"


def test_rewrite_remove_drops_subtree() -> None:
    def handler(element: Element) -> None:
        assert element.removed is False
        element.remove()
        assert element.removed is True

    assert rewrite("<b>a<i>x</i></b><keep>y</keep>", elements=[("b", handler)]) == "<keep>y</keep>"


def test_rewrite_remove_and_keep_content_unwraps() -> None:
    def handler(element: Element) -> None:
        element.remove_and_keep_content()

    assert rewrite("<div><b>keep</b></div>", elements=[("div", handler)]) == "<b>keep</b>"


def test_rewrite_matched_but_unedited_stays_verbatim() -> None:
    calls: list[str] = []

    def handler(element: Element) -> None:
        calls.append(element.tag)

    assert rewrite("<p   class = 'x'  >hi</p>", elements=[("p", handler)]) == "<p   class = 'x'  >hi</p>"
    assert calls == ["p"]


def test_rewrite_multiple_selectors_run_in_order() -> None:
    order: list[str] = []

    def first(element: Element) -> None:
        order.append("first")
        element.set_attribute("a", "1")

    def second(element: Element) -> None:
        order.append("second")
        element.set_attribute("b", "2")

    assert rewrite("<p>x</p>", elements=[("p", first), ("p", second)]) == '<p a="1" b="2">x</p>'
    assert order == ["first", "second"]


def test_rewrite_void_element_keeps_no_slash() -> None:
    assert rewrite("<img src=a><br>", elements=[("img", _set("loading", "lazy"))]) == '<img src="a" loading="lazy"><br>'


def test_rewrite_self_closing_foreign_preserved() -> None:
    assert rewrite("<svg><rect/></svg>", elements=[("rect", _set("f", "1"))]) == '<svg><rect f="1"/></svg>'


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('<script>var a = "<p>";</script><p>real</p>', id="script"),
        pytest.param("<style>p {color: red}</style><p>real</p>", id="style"),
        pytest.param("<textarea><p>no</p></textarea><p>real</p>", id="textarea"),
        pytest.param("<title><p>no</p></title><p>real</p>", id="title"),
        pytest.param("<xmp><p>no</p></xmp><p>real</p>", id="xmp"),
    ],
)
def test_rewrite_rawtext_content_is_not_matched(source: str) -> None:
    result = rewrite(source, elements=[("p", _set("t", "1"))])
    assert '<p t="1">real</p>' in result
    assert result.count('t="1"') == 1


def test_rewrite_plaintext_swallows_rest() -> None:
    result = rewrite("<plaintext><p>x</p>", elements=[("p", _set("t", "1"))])
    assert 't="1"' not in result


def test_rewrite_removed_ancestor_suppresses_descendants() -> None:
    inner_calls: list[str] = []

    def drop(element: Element) -> None:
        element.remove()

    def mark(element: Element) -> None:
        inner_calls.append(element.tag)
        element.set_attribute("m", "1")

    result = rewrite("<div><p>gone</p></div><p>kept</p>", elements=[("div", drop), ("p", mark)])
    assert result == '<p m="1">kept</p>'
    assert inner_calls == ["p"]


def test_rewrite_set_content_ancestor_suppresses() -> None:
    def handler(element: Element) -> None:
        element.set_content("Z")

    assert rewrite("<div><span>x</span></div>", elements=[("div", handler)]) == "<div>Z</div>"


def test_rewrite_deeply_nested_beyond_depth_guard_is_safe() -> None:
    depth = 9000
    result = rewrite(f"{'<div>' * depth}x{'</div>' * depth}", elements=[("div", _set("k", "1"))])
    assert result.count('<div k="1">') >= 1
    assert "x" in result


def test_rewrite_misnested_end_tags() -> None:
    assert rewrite("<b><i>x</b>y</i>", elements=[("i", _set("m", "1"))]) == '<b><i m="1">x</b>y</i>'


def test_rewrite_stray_end_tag_emitted_verbatim() -> None:
    assert rewrite("</p>text", elements=[("p", _set("m", "1"))]) == "</p>text"


def test_rewrite_stray_end_tag_with_open_elements() -> None:
    # </span> matches nothing on the open stack though <div> is open; it streams verbatim
    assert rewrite("<div>a</span>b</div>", elements=[("div", _set("m", "1"))]) == '<div m="1">a</span>b</div>'


@pytest.mark.parametrize("stray", [pytest.param("x-bar", id="same-length"), pytest.param("x-fooo", id="longer")])
def test_rewrite_stray_custom_end_tag_name_mismatch(stray: str) -> None:
    # the stray end tag shares the unknown-atom bucket with the open <x-foo> but its name differs,
    # so it matches nothing and streams verbatim
    result = rewrite(f"<x-foo>a</{stray}>b</x-foo>", elements=[("x-foo", _set("m", "1"))])
    assert result == f'<x-foo m="1">a</{stray}>b</x-foo>'


def test_rewrite_unclosed_element_flushes_after() -> None:
    def handler(element: Element) -> None:
        element.after("!")
        element.append("+", html=True)

    assert rewrite("<p>hi", elements=[("p", handler)]) == "<p>hi+!"


def test_rewrite_text_handler_set_text() -> None:
    def handler(text: Element) -> None:
        assert text.kind == "text"
        text.set_text(text.text.upper())

    assert rewrite("<p>hello</p> world", text=handler) == "<p>HELLO</p> WORLD"


def test_rewrite_text_handler_escapes_set_text() -> None:
    def handler(text: Element) -> None:
        text.set_text("<x>")

    assert rewrite("<p>a</p>", text=handler) == "<p>&lt;x&gt;</p>"


@pytest.mark.parametrize(
    ("html", "value", "expected"),
    [
        # script and the other RAWTEXT elements decode no character references, so escaping would change the text
        pytest.param("<script>a</script>", "a < b && c", "<script>a < b && c</script>", id="script"),
        pytest.param("<style>a</style>", 'a > b{content:"&"}', '<style>a > b{content:"&"}</style>', id="style"),
        pytest.param("<xmp>a</xmp>", "<b>", "<xmp><b></xmp>", id="xmp"),
        pytest.param("<script>a</script>", "a<!-b", "<script>a<!-b</script>", id="script-near-comment-open"),
        pytest.param("<script>a</script>", "a<!bc", "<script>a<!bc</script>", id="script-bang-without-dash"),
        pytest.param("<script>a</script>", "a</s", "<script>a</s</script>", id="script-partial-end-tag-at-end"),
        pytest.param("<plaintext>a", "</plaintext><b>", "<plaintext></plaintext><b>", id="plaintext-never-ends"),
        # RCDATA decodes references, so its text stays escaped
        pytest.param("<textarea>a</textarea>", "a < b &", "<textarea>a &lt; b &amp;</textarea>", id="textarea"),
    ],
)
def test_rewrite_set_text_in_raw_text_is_literal(html: str, value: str, expected: str) -> None:
    assert rewrite(html, text=_set_text(value)) == expected


def _set_text(value: str) -> Callable[[Element], None]:
    def handler(text: Element) -> None:
        text.set_text(value)

    return handler


def test_rewrite_text_node_insertions_in_raw_text_are_literal() -> None:
    def handler(text: Element) -> None:
        text.before("a<")
        text.after(">b")

    assert rewrite("<script>x</script>", text=handler) == "<script>a<x>b</script>"


def test_rewrite_text_node_replace_in_raw_text_is_literal() -> None:
    assert rewrite("<style>x</style>", text=lambda text: text.replace("a>b")) == "<style>a>b</style>"


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param(lambda element: element.append("a<b"), id="append"),
        pytest.param(lambda element: element.prepend("a<b"), id="prepend"),
        pytest.param(lambda element: element.set_content("a<b"), id="set-content"),
    ],
)
def test_rewrite_raw_text_element_content_is_literal(edit: Callable[[Element], None]) -> None:
    assert "a<b" in rewrite("<script>x</script>", elements=[("script", edit)])


def test_rewrite_raw_text_element_outside_stays_escaped() -> None:
    def handler(element: Element) -> None:
        element.before("<i>")
        element.after("a<b")

    assert rewrite("<script>x</script>", elements=[("script", handler)]) == "&lt;i&gt;<script>x</script>a&lt;b"


@pytest.mark.parametrize(
    ("html", "value"),
    [
        pytest.param("<script>a</script>", "</script><b>", id="script-end-tag"),
        pytest.param("<script>a</script>", "x<!--y", id="script-comment-open"),
        pytest.param("<style>a</style>", "</STYLE ><b>", id="style-end-tag-any-case"),
    ],
)
def test_rewrite_raw_set_text_that_ends_the_element_raises(html: str, value: str) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        rewrite(html, text=_set_text(value))


def _append_split_end_tag(element: Element) -> None:
    element.append("</scr")
    element.append("ipt>")


@pytest.mark.parametrize(
    ("html", "edit"),
    [
        # each piece is harmless alone; the check runs on the content as written, so the joined end tag is caught
        pytest.param("<script>a</script>", _append_split_end_tag, id="split-across-appends"),
        pytest.param("<script>a<</script>", lambda element: element.append("/script>"), id="completes-source-text"),
    ],
)
def test_rewrite_raw_append_that_ends_the_element_raises(html: str, edit: Callable[[Element], None]) -> None:
    with pytest.raises(ValueError, match="must not contain"):
        rewrite(html, elements=[("script", edit)])


def test_rewrite_raw_text_html_content_is_not_checked() -> None:
    edit = [("script", lambda element: element.append("</script><b>", html=True))]
    assert rewrite("<script>a</script>", elements=edit) == "<script>a</script><b></script>"


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        # the content was text inside the element, and without the tags around it only escaping keeps it text
        pytest.param("<xmp><b>a</b></xmp>", "&lt;b&gt;a&lt;/b&gt;", id="xmp"),
        pytest.param("<script>if (a < b && c) {}</script>", "if (a &lt; b &amp;&amp; c) {}", id="script"),
        pytest.param("<style>a > b {}</style><p>x</p>", "a &gt; b {}<p>x</p>", id="style"),
        pytest.param("<noscript><b class=x>y</b></noscript>", "&lt;b class=x&gt;y&lt;/b&gt;", id="noscript"),
        pytest.param("<xmp>&amp;</xmp>", "&amp;amp;", id="rawtext-reference-is-text"),
        pytest.param("<xmp>&</xmp>lt;b>", "&amp;lt;b>", id="no-reference-across-the-end"),
        pytest.param("<xmp>a\x00<b>\r\n</xmp>", "a\ufffd&lt;b&gt;\n", id="normalized-run"),
        pytest.param("<xmp>\u20ac<\U0001f600></xmp>", "\u20ac&lt;\U0001f600&gt;", id="wide"),
        pytest.param("<plaintext>a<b></xmp>", "a&lt;b&gt;&lt;/xmp&gt;", id="plaintext"),
        pytest.param("<xmp>a<b>", "a&lt;b&gt;", id="unclosed"),
        # RCDATA decodes references, so each is written as its decoded text, escaped
        pytest.param("<textarea><b> &amp; &lt; & &#60;</textarea>", "&lt;b&gt; &amp; &lt; &amp; &lt;", id="textarea"),
        pytest.param("<title>a<b></title><b>k</b>", "a&lt;b&gt;<b>k</b>", id="title"),
        pytest.param("<title>&not</title>in;", "¬in;", id="reference-without-semicolon"),
    ],
)
def test_rewrite_unwrapped_raw_text_content_is_escaped(html: str, expected: str) -> None:
    selector = "xmp, script, style, noscript, plaintext, textarea, title"
    assert rewrite(html, elements=[(selector, _unwrap)]) == expected
    assert rewrite(html, elements=[(selector, _unwrap)], text=lambda _: None) == expected


def _unwrap(element: Element) -> None:
    element.remove_and_keep_content()


def test_rewrite_unwrapped_ancestor_leaves_raw_text_alone() -> None:
    assert rewrite("<div><xmp><b></xmp><b>k</b></div>", elements=[("div", _unwrap)]) == "<xmp><b></xmp><b>k</b>"


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(lambda text: text.set_text(f"{text.text}<i>"), "a&lt;b&gt;&lt;i&gt;", id="set-text"),
        pytest.param(lambda text: text.before("<i>"), "&lt;i&gt;a&lt;b&gt;", id="before"),
        pytest.param(lambda text: text.after("<i>"), "a&lt;b&gt;&lt;i&gt;", id="after"),
        pytest.param(lambda text: text.replace("<i>"), "&lt;i&gt;", id="replace"),
        pytest.param(lambda text: text.after("<i>", html=True), "a&lt;b&gt;<i>", id="html"),
    ],
)
def test_rewrite_text_edit_in_unwrapped_raw_text_is_escaped(edit: Callable[[Element], None], expected: str) -> None:
    assert rewrite("<xmp>a<b></xmp>", elements=[("xmp", _unwrap)], text=edit) == expected


@pytest.mark.parametrize(
    ("html", "edit", "expected"),
    [
        pytest.param("<xmp>text</xmp>", lambda element: element.append("<x>"), "text&lt;x&gt;", id="append"),
        pytest.param("<xmp>text</xmp>", lambda element: element.set_content("<x>"), "&lt;x&gt;", id="set-content"),
        pytest.param("<xmp>text</xmp>", lambda element: element.append("<x>", html=True), "text<x>", id="html"),
        pytest.param("<textarea>a</textarea>", lambda element: element.append("<x>"), "a&lt;x&gt;", id="rcdata"),
        pytest.param("<div>a</div>", lambda element: element.append("<x>"), "a&lt;x&gt;", id="ordinary"),
    ],
)
def test_rewrite_unwrapped_raw_text_element_content_is_escaped(
    html: str, edit: Callable[[Element], None], expected: str
) -> None:
    # the handlers can come in either order: the text was appended unescaped only for between the tags
    assert rewrite(html, elements=[("*", edit), ("*", _unwrap)]) == expected
    assert rewrite(html, elements=[("*", _unwrap), ("*", edit)]) == expected


def test_rewrite_removed_raw_text_element_content_is_escaped() -> None:
    def handler(element: Element) -> None:
        element.append("<x>")
        element.remove()

    assert rewrite("<script>a</script>", elements=[("script", handler)]) == "&lt;x&gt;"


def test_rewrite_unwrapped_raw_text_append_is_not_checked() -> None:
    def handler(element: Element) -> None:
        element.append("a")
        element.append("</xmp>", html=True)
        element.remove_and_keep_content()

    assert rewrite("<xmp>x</xmp>", elements=[("xmp", handler)]) == "xa</xmp>"


def test_rewrite_unwrapped_raw_text_set_content_after_append_replaces_it() -> None:
    def handler(element: Element) -> None:
        element.append("<x>")
        element.set_content("<y>")
        element.remove_and_keep_content()

    assert rewrite("<xmp>text</xmp>", elements=[("xmp", handler)]) == "&lt;y&gt;"


def test_rewrite_unwrapped_raw_text_prepend_is_not_checked() -> None:
    def handler(element: Element) -> None:
        element.prepend("a")
        element.remove_and_keep_content()

    def note(text: Element) -> None:
        text.after("<!--x-->", html=True)

    # prepend marked the script as written into, but no <script> is left for the comment to hide an end tag in
    assert rewrite("<script>x</script>", elements=[("script", handler)], text=note) == "x<!--x-->"


def test_rewrite_text_normalized_run_verbatim() -> None:
    # a NUL in text becomes a buffered (non-slice) run; it must still round-trip
    assert rewrite("<p>a\x00b</p>") == "<p>a\x00b</p>"


def test_rewrite_text_before_after_and_remove() -> None:
    def before_after(text: Element) -> None:
        text.before("[", html=True)
        text.after("]", html=True)

    assert rewrite("<p>hi</p>", text=before_after) == "<p>[hi]</p>"

    def drop(text: Element) -> None:
        text.remove()

    assert rewrite("<p>gone</p>", elements=[], text=drop) == "<p></p>"


def test_rewrite_text_replace() -> None:
    def handler(text: Element) -> None:
        text.replace("<b>x</b>", html=True)

    assert rewrite("<p>t</p>", text=handler) == "<p><b>x</b></p>"


def test_rewrite_comment_handler() -> None:
    def handler(comment: Element) -> None:
        assert (comment.kind, comment.text) == ("comment", " keep ")
        comment.set_text(" edited ")

    assert rewrite("<p>a<!-- keep -->b</p>", comments=handler) == "<p>a<!-- edited -->b</p>"


@pytest.mark.parametrize(
    ("text", "found"),
    [
        pytest.param(">x", "'>' at index 0", id="starts-greater-than"),
        pytest.param("->x", "'->' at index 0", id="starts-dash-greater-than"),
        pytest.param("a-->b", "'-->' at index 1", id="close"),
        pytest.param("a--!>b", "'--!>' at index 1", id="bang-close"),
    ],
)
def test_rewrite_comment_set_text_rejects_early_close(text: str, found: str) -> None:
    # rewrite writes the comment back between `<!--` and `-->`, and each of these would close it early
    with pytest.raises(ValueError, match=f"^comment text has {found}, which ends an HTML comment early"):
        rewrite("<p><!--n--></p>", comments=lambda comment: comment.set_text(text))


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param("-", id="dash"),
        pytest.param("-x", id="starts-dash"),
        pytest.param("--", id="double-dash"),
        pytest.param("a--!", id="ends-bang"),
        pytest.param("a--!x", id="bang-not-closing"),
        pytest.param("a--x", id="dashes-not-closing"),
        # a nested `<!--` and a trailing `<!-` are parse errors inside a comment, but neither ends it
        pytest.param("a<!--", id="nested-open"),
        pytest.param("a<!-", id="ends-partial-open"),
    ],
)
def test_rewrite_comment_set_text_round_trips(text: str) -> None:
    out = rewrite("<p><!--n--></p>", comments=lambda comment: comment.set_text(text))
    assert [node.data for node in parse_fragment(out, "div").descendants if isinstance(node, Comment)] == [text]


def test_rewrite_comment_remove_and_before() -> None:
    def handler(comment: Element) -> None:
        comment.before("X", html=True)
        comment.remove()

    assert rewrite("<p>a<!--c-->b</p>", comments=handler) == "<p>aXb</p>"


def test_rewrite_doctype_handler_reads_fields() -> None:
    seen: dict[str, _Read] = {}

    def handler(doctype: Element) -> None:
        seen["kind"] = doctype.kind
        seen["name"] = doctype.name
        seen["public_id"] = doctype.public_id
        seen["system_id"] = doctype.system_id

    rewrite('<!DOCTYPE html PUBLIC "-//W3C//DTD//EN" "http://x.dtd"><p>x</p>', doctype=handler)
    assert seen == {
        "kind": "doctype",
        "name": "html",
        "public_id": "-//W3C//DTD//EN",
        "system_id": "http://x.dtd",
    }


def test_rewrite_doctype_absent_identifiers_are_none() -> None:
    seen: dict[str, _Read] = {}

    def handler(doctype: Element) -> None:
        seen["public_id"] = doctype.public_id
        seen["system_id"] = doctype.system_id

    rewrite("<!DOCTYPE html><p>x</p>", doctype=handler)
    assert seen == {"public_id": None, "system_id": None}


def test_rewrite_doctype_remove_and_after() -> None:
    def handler(doctype: Element) -> None:
        doctype.after("<!--after-->", html=True)
        doctype.remove()

    assert rewrite("<!DOCTYPE html>x", doctype=handler) == "<!--after-->x"


def test_rewrite_handle_raises_after_handler_returns() -> None:
    stash: list[Element] = []

    def handler(element: Element) -> None:
        stash.append(element)

    rewrite("<p>x</p>", elements=[("p", handler)])
    with pytest.raises(RuntimeError):
        stash[0].set_attribute("z", "1")
    with pytest.raises(RuntimeError):
        _ = stash[0].tag
    with pytest.raises(RuntimeError):
        stash[0].before("x")
    with pytest.raises(RuntimeError):
        stash[0].after("x")
    with pytest.raises(RuntimeError):
        stash[0].replace("x")
    with pytest.raises(RuntimeError):
        stash[0].remove()
    with pytest.raises(RuntimeError):
        stash[0].set_content("x")
    with pytest.raises(RuntimeError):
        _ = stash[0].text


def test_rewrite_get_requires_a_name_argument() -> None:
    def handler(element: Element) -> None:
        with pytest.raises(TypeError):
            element.get()  # ty: ignore[missing-argument]  # omits a required argument on purpose to check the error

    rewrite("<p>x</p>", elements=[("p", handler)])


def test_rewrite_wrong_kind_method_raises() -> None:
    def on_text(text: Element) -> None:
        with pytest.raises(TypeError):
            text.set_attribute("x", "1")
        with pytest.raises(TypeError):
            _ = text.tag
        with pytest.raises(TypeError):
            _ = text.attrs
        with pytest.raises(TypeError):
            _ = text.name
        with pytest.raises(TypeError):
            _ = text.public_id
        with pytest.raises(TypeError):
            _ = text.system_id

    rewrite("<p>hi</p>", text=on_text)

    def on_element(element: Element) -> None:
        with pytest.raises(TypeError):
            _ = element.text
        with pytest.raises(TypeError):
            element.set_text("x")

    rewrite("<p>hi</p>", elements=[("p", on_element)])


def test_rewrite_set_text_rejects_non_str() -> None:
    def handler(text: Element) -> None:
        with pytest.raises(TypeError):
            text.set_text(1)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection

    rewrite("<p>hi</p>", text=handler)


def test_rewrite_handler_exception_propagates() -> None:
    def boom(*_: object) -> None:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        rewrite("<p>x</p>", elements=[("p", boom)])


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<!DOCTYPE html><html><head><title>t</title></head><body><p>hi</p></body></html>", id="document"),
        pytest.param("<ul><li>a</li><li>b</li></ul>", id="list"),
        pytest.param('<a href="x?a=1&amp;b=2">link</a>', id="entities"),
        pytest.param("<p>text<!-- comment --><span>more</span></p>", id="mixed"),
    ],
)
def test_rewrite_noop_equals_full_parse_serialize(source: str) -> None:
    # a canonical document (round-tripped through the tree serializer) is reproduced
    # verbatim by a no-op streaming pass, so the two agree on it
    canonical = parse(source).serialize()
    assert rewrite(canonical) == canonical


def test_rewrite_streaming_matches_dom_edit() -> None:

    def add_rel(element: Element) -> None:
        element.set_attribute("rel", "noopener")

    source = "<div><a href=/1>one</a><a href=/2>two</a></div>"
    streamed = rewrite(source, elements=[("a[href]", add_rel)])

    for anchor in parse(source).select("a[href]"):
        anchor.attrs["rel"] = "noopener"
    assert streamed == '<div><a href="/1" rel="noopener">one</a><a href="/2" rel="noopener">two</a></div>'


def test_rewrite_wide_document_is_bounded() -> None:
    result = rewrite(f"<ul>{'<li>x</li>' * 20000}</ul>", elements=[("li", _set("k", "1"))])
    assert result.count('<li k="1">x</li>') == 20000


def test_rewrite_non_ascii_attribute_name_and_value_roundtrip() -> None:
    def handler(element: Element) -> None:
        element.set_attribute("data-é中\U0001f600", "vé")

    assert rewrite("<p>x</p>", elements=[("p", handler)]) == '<p data-é中\U0001f600="vé">x</p>'


def test_rewrite_non_ascii_source_attribute_name() -> None:
    assert rewrite("<p data-é=1>a</p>", elements=[("[data-é]", _set("m", "1"))]) == ('<p data-é="1" m="1">a</p>')


def test_rewrite_non_ascii_tag_name_is_unknown() -> None:
    # a tag name with a code point above the Latin-1 range is stored two-byte and never
    # resolves to a builtin atom; a bare type selector still matches it by name
    assert rewrite("<x中>a</x中>", elements=[("x中", _set("m", "1"))]) == '<x中 m="1">a</x中>'


def test_rewrite_set_attribute_lowercases_name() -> None:
    assert rewrite("<p>x</p>", elements=[("p", _set("DATA-Upper", "v"))]) == '<p data-upper="v">x</p>'


def test_rewrite_content_escapes_ampersand() -> None:
    def handler(element: Element) -> None:
        element.before("a & b")

    assert rewrite("<p>x</p>", elements=[("p", handler)]) == "a &amp; b<p>x</p>"


def test_rewrite_get_known_atom_absent_returns_default() -> None:
    seen: dict[str, _Read] = {}

    def handler(element: Element) -> None:
        seen["id"] = element.get("id", "none")
        seen["has_class"] = element.has_attribute("class")

    rewrite("<p>x</p>", elements=[("p", handler)])
    assert seen == {"id": "none", "has_class": False}


def test_rewrite_void_element_after_and_append() -> None:
    def handler(element: Element) -> None:
        element.after("!", html=True)
        element.append("+", html=True)

    assert rewrite("<img src=a>rest", elements=[("img", handler)]) == "<img src=a>+!rest"


def test_rewrite_intermediate_dropped_element_on_misnest() -> None:
    def drop(element: Element) -> None:
        element.remove()

    # </a> implicitly closes the still-open, content-suppressed <b>
    assert rewrite("<a><b>x</a>y", elements=[("b", drop)]) == "<a></a>y"


def test_rewrite_eof_unclosed_dropped_element() -> None:
    def handler(element: Element) -> None:
        element.set_content("Z")

    assert rewrite("<div>tail", elements=[("div", handler)]) == "<div>Z"


def test_rewrite_stray_end_tag_inside_removed_region() -> None:
    def drop(element: Element) -> None:
        element.remove()

    assert rewrite("<div>a</span>b</div>c", elements=[("div", drop)]) == "c"


def test_rewrite_text_get_after_set_text() -> None:
    seen: list[str] = []

    def handler(text: Element) -> None:
        text.set_text("new")
        seen.append(text.text)

    rewrite("<p>old</p>", text=handler)
    assert seen == ["new"]


def test_rewrite_doctype_empty_name_is_none() -> None:
    seen: list[str | None] = []

    def handler(doctype: Element) -> None:
        seen.append(doctype.name)

    rewrite("<!DOCTYPE>x", doctype=handler)
    assert seen == [None]


@pytest.mark.parametrize(
    "method",
    [
        pytest.param(lambda handle: handle.set_content("x"), id="set_content"),
        pytest.param(lambda handle: handle.append("x"), id="append"),
        pytest.param(lambda handle: handle.prepend("x"), id="prepend"),
        pytest.param(lambda handle: handle.remove_and_keep_content(), id="remove_and_keep_content"),
        pytest.param(lambda handle: handle.get("x"), id="get"),
        pytest.param(lambda handle: handle.has_attribute("x"), id="has_attribute"),
        pytest.param(lambda handle: handle.remove_attribute("x"), id="remove_attribute"),
    ],
)
def test_rewrite_element_only_method_on_text_raises(method: Callable[[Element], object]) -> None:
    def handler(text: Element) -> None:
        with pytest.raises(TypeError):
            method(text)

    rewrite("<p>hi</p>", text=handler)


@pytest.mark.parametrize(
    "method",
    [
        pytest.param(lambda handle: handle.before(123), id="before"),
        pytest.param(lambda handle: handle.after(123), id="after"),
        pytest.param(lambda handle: handle.replace(123), id="replace"),
        pytest.param(lambda handle: handle.set_content(123), id="set_content"),
        pytest.param(lambda handle: handle.append(123), id="append"),
        pytest.param(lambda handle: handle.prepend(123), id="prepend"),
    ],
)
def test_rewrite_content_method_non_str_raises(method: Callable[[Element], object]) -> None:
    def handler(element: Element) -> None:
        with pytest.raises(TypeError):
            method(element)

    rewrite("<p>x</p>", elements=[("p", handler)])


def test_rewrite_attribute_methods_non_str_argument_raise() -> None:
    def handler(element: Element) -> None:
        with pytest.raises(TypeError):
            element.get(123)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
        with pytest.raises(TypeError):
            element.has_attribute(123)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
        with pytest.raises(TypeError):
            element.remove_attribute(123)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection
        with pytest.raises(TypeError):
            element.set_attribute("ok", 123)  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection

    rewrite("<p>x</p>", elements=[("p", handler)])


def test_rewrite_set_text_after_handler_raises() -> None:
    stash: list[Element] = []

    def handler(text: Element) -> None:
        stash.append(text)

    rewrite("<p>x</p>", text=handler)
    with pytest.raises(RuntimeError):
        stash[0].set_text("y")


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"text": None}, id="text"),
        pytest.param({"comments": None}, id="comments"),
        pytest.param({"doctype": None}, id="doctype"),
    ],
)
def test_rewrite_leaf_handler_exception_propagates(kwargs: dict[str, None]) -> None:
    def boom(*_: object) -> None:
        msg = "leaf-boom"
        raise ValueError(msg)

    key = next(iter(kwargs))
    source = {"text": "<p>t</p>", "comments": "<!--c-->", "doctype": "<!DOCTYPE html>x"}[key]
    with pytest.raises(ValueError, match="leaf-boom"):
        rewrite(source, **{key: boom})  # ty: ignore[invalid-argument-type]  # passes a wrong type on purpose to check the runtime rejection


def test_rewrite_handler_exception_frees_open_ancestors() -> None:
    def boom(*_: object) -> None:
        msg = "nested-boom"
        raise ValueError(msg)

    # the enclosing <div> is open on the spine when the <p> handler raises
    with pytest.raises(ValueError, match="nested-boom"):
        rewrite("<div><p>x</p></div>", elements=[("p", boom)])


def test_rewrite_remove_after_replace_drops_the_replacement() -> None:
    def handler(element: Element) -> None:
        element.replace("X", html=True)
        element.remove()

    assert rewrite("<p>a</p>b", elements=[("p", handler)]) == "b"


def test_rewrite_set_content_after_append_replaces_it() -> None:
    def handler(element: Element) -> None:
        element.append("X", html=True)
        element.set_content("Y", html=True)

    assert rewrite("<p>a</p>", elements=[("p", handler)]) == "<p>Y</p>"


def test_rewrite_set_empty_attribute_value() -> None:
    def handler(element: Element) -> None:
        element.set_attribute("data-x", "")

    assert rewrite("<p>a</p>", elements=[("p", handler)]) == '<p data-x="">a</p>'


def test_rewrite_matched_element_keeps_empty_source_attribute() -> None:
    def handler(element: Element) -> None:
        element.set_attribute("id", "z")

    assert rewrite('<p class="">a</p>', elements=[("p", handler)]) == '<p class="" id="z">a</p>'


def test_rewrite_remove_known_attribute() -> None:
    def handler(element: Element) -> None:
        element.remove_attribute("class")

    assert rewrite('<p class="c" id=k>a</p>', elements=[("p", handler)]) == '<p id="k">a</p>'


def test_rewrite_non_ascii_tag_name() -> None:
    assert rewrite("<café>x</café>") == "<café>x</café>"


def test_rewrite_void_element_inside_dropped_content() -> None:
    def handler(element: Element) -> None:
        element.set_content("Z")

    # the <br> falls inside the <div> whose content the handler drops, so it is suppressed
    assert rewrite("<div><br>x</div>", elements=[("div", handler)]) == "<div>Z</div>"


def test_rewrite_overlong_attribute_name_is_truncated() -> None:
    def handler(element: Element) -> None:
        element.set_attribute("id", "k")

    # a name past the 256-byte encode buffer exercises the truncation guard without a crash
    html = f'<p data-{"z" * 300}="v">t</p>'
    assert rewrite(html, elements=[("p", handler)]).endswith('id="k">t</p>')


def test_rewrite_second_rule_skipped_after_first_raises() -> None:
    def boom(_: Element) -> None:
        msg = "two-rule-boom"
        raise ValueError(msg)

    # the <p> matches both rules; the first raising exits the match loop before the second runs
    with pytest.raises(ValueError, match="two-rule-boom"):
        rewrite("<p class=x>t</p>", elements=[("p", boom), (".x", boom)])


@pytest.mark.parametrize(
    ("initial", "count"),
    [pytest.param(0, 1, id="single"), pytest.param(0, 1000, id="many"), pytest.param(3, 17, id="existing-array")],
)
def test_rewrite_attribute_growth_order(initial: int, count: int) -> None:
    def add(element: Element) -> None:
        for index in range(count):
            element.set_attribute(f"a{index}", "x")

    before: Final = "".join(f' b{index}="before"' for index in range(initial))
    after: Final = "".join(f' a{index}="x"' for index in range(count))
    assert rewrite(f"<x{before}>body</x>", elements=(("x", add),)) == f"<x{before}{after}>body</x>"


def test_rewrite_attribute_growth_remove_and_append() -> None:
    def change(element: Element) -> None:
        for index in range(17):
            element.set_attribute(f"a{index}", "before")
        for index in range(17):
            element.remove_attribute(f"a{index}")
        element.set_attribute("a0", "after")
        element.set_attribute("a1", "second")

    assert rewrite("<x>body</x>", elements=(("x", change),)) == '<x a0="after" a1="second">body</x>'


def test_rewrite_attribute_growth_replace_and_escape() -> None:
    def change(element: Element) -> None:
        for index in range(17):
            element.set_attribute(f"a{index}", "before")
        element.set_attribute("a0", '"&')

    rest: Final = "".join(f' a{index}="before"' for index in range(1, 17))
    assert rewrite("<x>body</x>", elements=(("x", change),)) == f'<x a0="&quot;&amp;"{rest}>body</x>'


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # SVG/MathML content stays markup: title, style, xmp, ... are ordinary elements there
        pytest.param("<svg><title><i>", ["svg", "title", "i"], id="svg-title"),
        pytest.param("<svg><style><i></style>", ["svg", "style", "i"], id="svg-style"),
        pytest.param("<svg><script><i></script>", ["svg", "script", "i"], id="svg-script"),
        pytest.param("<svg><plaintext><i>", ["svg", "plaintext", "i"], id="svg-plaintext"),
        pytest.param("<math><xmp><i>", ["math", "xmp", "i"], id="math-xmp"),
        pytest.param("<svg><g><textarea><i>", ["svg", "g", "textarea", "i"], id="nested"),
        pytest.param("<svg><svg></svg><style><i>", ["svg", "svg", "style", "i"], id="inner-svg-closed"),
        pytest.param(
            "<div><svg></svg><b><svg><title><i>", ["div", "svg", "b", "svg", "title", "i"], id="second-svg-under-html"
        ),
        # an integration point hands its start tags back to the HTML rules
        pytest.param("<svg><foreignObject><style><i>", ["svg", "foreignobject", "style"], id="foreign-object"),
        pytest.param("<svg><desc><xmp><i>", ["svg", "desc", "xmp"], id="desc"),
        pytest.param("<svg><title><textarea><i>", ["svg", "title", "textarea"], id="title-point"),
        pytest.param("<math><mi><style><i>", ["math", "mi", "style"], id="mathml-text-point"),
        pytest.param("<math><mi><mglyph><style><i>", ["math", "mi", "mglyph", "style", "i"], id="mglyph"),
        pytest.param("<math><mo><malignmark><style><i>", ["math", "mo", "malignmark", "style", "i"], id="malignmark"),
        pytest.param("<math><mi><svg><style><i>", ["math", "mi", "svg", "style", "i"], id="svg-in-text-point"),
        pytest.param("<svg><desc><math><style><i>", ["svg", "desc", "math", "style", "i"], id="math-in-desc"),
        pytest.param("<math><annotation-xml><style><i>", ["math", "annotation-xml", "style", "i"], id="annotation-xml"),
        pytest.param(
            '<math><annotation-xml encoding="Text/HTML"><style><i>', ["math", "annotation-xml", "style"], id="html"
        ),
        pytest.param(
            "<math><annotation-xml encoding=application/xhtml+xml><style><i>",
            ["math", "annotation-xml", "style"],
            id="xhtml",
        ),
        pytest.param(
            "<math><annotation-xml id=a encoding=text/html><style><i>", ["math", "annotation-xml", "style"], id="second"
        ),
        pytest.param(
            "<math><annotation-xml encoding=text/xml><style><i>", ["math", "annotation-xml", "style", "i"], id="xml"
        ),
        pytest.param(
            "<math><annotation-xml encoding><style><i>", ["math", "annotation-xml", "style", "i"], id="bare-encoding"
        ),
        pytest.param(
            "<math><annotation-xml encoding=x encoding=text/html><style><i>",
            ["math", "annotation-xml", "style", "i"],
            id="first-encoding-wins",
        ),
        pytest.param(
            "<math><annotation-xml><svg><style><i>",
            ["math", "annotation-xml", "svg", "style", "i"],
            id="svg-in-annotation",
        ),
        pytest.param(
            "<svg><annotation-xml encoding=text/html><style><i>",
            ["svg", "annotation-xml", "style", "i"],
            id="svg-annotation",
        ),
        # a breakout tag closes the foreign elements, so what follows is HTML again
        pytest.param("<svg><g><p><style><i>", ["svg", "g", "p", "style"], id="breakout"),
        pytest.param("<svg><img><style><i>", ["svg", "img", "style"], id="void-breakout"),
        pytest.param("<svg><font color=red><style><i>", ["svg", "font", "style"], id="font-color"),
        pytest.param("<svg><font x FACE=a><style><i>", ["svg", "font", "style"], id="font-face"),
        pytest.param("<svg><font size=1><style><i>", ["svg", "font", "style"], id="font-size"),
        pytest.param("<svg><font \u0101 size=1><style><i>", ["svg", "font", "style"], id="font-wide-name"),
        pytest.param("<svg><font sized><style><i>", ["svg", "font", "style", "i"], id="font-plain"),
        pytest.param("<svg><desc><svg><b><style><i>", ["svg", "desc", "svg", "b", "style"], id="breakout-to-point"),
        pytest.param("<svg></p><style><i>", ["svg", "style"], id="end-p"),
        pytest.param("<svg><g></br><style><i>", ["svg", "g", "style"], id="end-br"),
        pytest.param("<p><svg></p><style><i>", ["p", "svg", "style"], id="end-p-closes-open-p"),
        pytest.param("<svg><desc></p><style><i>", ["svg", "desc", "style"], id="end-p-at-point"),
        # end tags: a foreign element closes by name, an HTML end tag stops at a scoping foreign element
        pytest.param("<svg><g></svg><style><i>", ["svg", "g", "style"], id="end-svg"),
        pytest.param("<svg><title></svg><style><i>", ["svg", "title", "style"], id="end-svg-from-point"),
        pytest.param("<svg><g></x><style><i>", ["svg", "g", "style", "i"], id="stray-end"),
        pytest.param(
            "<svg><desc><div><body></body><title><i>", ["svg", "desc", "div", "body", "title"], id="end-unopened-body"
        ),
        pytest.param("<svg><x-y><g></x-y><style><i>", ["svg", "x-y", "g", "style", "i"], id="custom-end"),
        pytest.param("<div><svg><g></div><style><i>", ["div", "svg", "g", "style"], id="html-end-closes-svg"),
        pytest.param("<svg><title><b></title><style><i>", ["svg", "title", "b", "style"], id="point-bounds-own-end"),
        pytest.param(
            "<div><svg><desc><b></div></b></desc><style><i>",
            ["div", "svg", "desc", "b", "style", "i"],
            id="point-bounds",
        ),
        pytest.param(
            "<div><math><annotation-xml><g></div><style><i>",
            ["div", "math", "annotation-xml", "g", "style", "i"],
            id="annotation-xml-bounds",
        ),
        pytest.param(
            "<div><svg><annotation-xml></div><style><i>",
            ["div", "svg", "annotation-xml", "style"],
            id="svg-annotation-open",
        ),
        pytest.param(
            "<table><td><svg><desc><b></td><style><i>", ["table", "td", "svg", "desc", "b", "style"], id="table-scope"
        ),
        pytest.param("<td><svg><td></td><style><i>", ["td", "svg", "td", "style", "i"], id="foreign-td"),
        # the trailing slash ends a foreign element only
        pytest.param("<svg><title/><style><i>", ["svg", "title", "style", "i"], id="self-closing-foreign"),
        pytest.param("<svg/><style><i>", ["svg", "style"], id="self-closing-svg"),
        pytest.param("<title/><i>", ["title"], id="self-closing-html-title"),
        pytest.param("<script/><i></script><b>", ["script", "b"], id="self-closing-html-script"),
        pytest.param("<b/><svg></b><style><i>", ["b", "svg", "style"], id="self-closing-html-stays-open"),
    ],
)
def test_rewrite_foreign_content_tokenizes_like_a_browser(source: str, expected: list[str]) -> None:
    assert _seen(source) == expected
    # the tree builder agrees on which <i> is an element and which is text
    assert len(parse(source).select("i")) == expected.count("i")


def _seen(source: str) -> list[str]:
    tags: list[str] = []
    assert rewrite(source, elements=[("*", lambda element: tags.append(element.tag))]) == source
    return tags


def test_rewrite_removes_script_inside_svg_title() -> None:
    def drop(element: Element) -> None:
        element.remove()

    source = "<svg><title><b><script>go()</script></b></title><style><script>x</script></style></svg>"
    assert rewrite(source, elements=[("script", drop)]) == "<svg><title><b></b></title><style></style></svg>"


def test_rewrite_text_in_foreign_script_is_escaped() -> None:
    def fill(element: Element) -> None:
        element.set_content("a<b")

    # an HTML script holds raw text; an SVG one is parsed as markup, so text written into it is escaped
    source = "<script>x</script><svg><script>x</script></svg>"
    assert rewrite(source, elements=[("script", fill)]) == "<script>a<b</script><svg><script>a&lt;b</script></svg>"


def test_rewrite_breakout_flushes_trailing_edits_of_closed_foreign_elements() -> None:
    def edit(element: Element) -> None:
        element.append("+")
        element.after("!")

    assert rewrite("<svg><g>a<p>b</p></svg>", elements=[("g", edit)]) == "<svg><g>a+!<p>b</p></svg>"
    assert rewrite("<svg><g>a</p>b</svg>", elements=[("g", edit)]) == "<svg><g>a+!</p>b</svg>"


def test_rewrite_breakout_ends_removed_foreign_element() -> None:
    def drop(element: Element) -> None:
        element.remove()

    def swap(element: Element) -> None:
        element.set_content("Q")
        element.after("!")

    # <p> is not inside the <svg> a browser builds, so it is not part of what is removed or replaced
    assert rewrite("<svg><g>a<p>b</p></svg>c", elements=[("svg", drop)]) == "<p>b</p></svg>c"
    assert rewrite("<svg><g>a<p>b</p></svg>c", elements=[("g", swap)]) == "<svg><g>Q!<p>b</p></svg>c"
    assert rewrite("<svg><g><a>x<p>b</p>", elements=[("g", drop), ("a", swap)]) == "<svg><p>b</p>"


def _remove(element: Element) -> None:
    element.remove()


def _replace_with_text(element: Element) -> None:
    element.replace("img src=x")


def _strip_font(element: Element) -> None:
    for name in ("color", "face", "size"):
        element.remove_attribute(name)


_TITLE: Final[str] = "<title><script>run()</script></title>"


@pytest.mark.parametrize(
    ("source", "selector", "handler", "expected"),
    [
        # a breakout start tag ends the open <svg>/<math>; dropped, it leaves their end tags in its place
        pytest.param(f"<svg><font color=red>{_TITLE}</font>", "font", _unwrap, f"<svg></svg>{_TITLE}", id="unwrap"),
        pytest.param(f"<svg><img src=a>{_TITLE}", "img", _remove, f"<svg></svg>{_TITLE}", id="remove-void"),
        pytest.param(
            f"<math><b></b>{_TITLE}", "b", _replace_with_text, f"<math></math>img src=x{_TITLE}", id="replace"
        ),
        pytest.param(
            f"<svg><g><svg><a><p>{_TITLE}", "p", _unwrap, f"<svg><g><svg><a></a></svg></g></svg>{_TITLE}", id="nested"
        ),
        pytest.param(
            f"<svg><desc><svg><g><p>{_TITLE}", "p", _unwrap, f"<svg><desc><svg><g></g></svg>{_TITLE}", id="to-point"
        ),
        # a <font> breaks out only while it keeps color, face or size
        pytest.param(
            f"<svg><font color=a id=b size=1>{_TITLE}",
            "font",
            _strip_font,
            f'<svg></svg><font id="b">{_TITLE}',
            id="font-stripped",
        ),
        pytest.param(
            f"<svg><font size=1>{_TITLE}",
            "font",
            _set("color", "a"),
            f'<svg><font size="1" color="a">{_TITLE}',
            id="font-kept",
        ),
        pytest.param(f"<svg><b>{_TITLE}", "b", _set("id", "a"), f'<svg><b id="a">{_TITLE}', id="edited"),
        # a dropped end tag leaves the end tags of the foreign elements it closed
        pytest.param(f"<p><svg><g></p>{_TITLE}", "p", _unwrap, f"<svg><g></g></svg>{_TITLE}", id="end-p"),
        pytest.param(
            f"<div><math><mrow></div>{_TITLE}", "div", _unwrap, f"<math><mrow></mrow></math>{_TITLE}", id="end-html"
        ),
        pytest.param(
            f"<table><td><i><svg><desc><b></td></b></desc>{_TITLE}",
            "td",
            _unwrap,
            f"<table><i><svg><desc><b></b></desc></svg></i></b></desc>{_TITLE}",
            id="end-through-point",
        ),
        pytest.param(f"<div/><svg></div>{_TITLE}", "div", _unwrap, f"<svg></svg>{_TITLE}", id="end-html-slash"),
        pytest.param(f"<div><svg></div>{_TITLE}", "div", _remove, _TITLE, id="end-of-removed"),
    ],
)
def test_rewrite_dropped_tag_still_ends_foreign_content(
    source: str, selector: str, handler: Callable[[Element], None], expected: str
) -> None:
    result = rewrite(source, elements=[(selector, handler), ("script", _remove)])
    assert result == expected
    # the <title> text was no script in the input, so no handler saw it, and it is none in the output
    assert not parse(source, scripting=True).select("script")
    assert not parse(result, scripting=True).select("script")


def test_rewrite_dropped_breakout_ends_foreign_elements_in_place() -> None:
    def mark(element: Element) -> None:
        element.append(f"[{element.tag}")
        element.after(f"{element.tag}]")

    def drop(element: Element) -> None:
        element.before("^")
        element.remove()

    def fill(element: Element) -> None:
        element.set_content("Q")

    # each end tag goes where its element ended: after its append(), ahead of its after() and the dropped tag's before()
    source = "<svg><g>a<p>b</p>c"
    assert rewrite(source, elements=[("svg, g", mark), ("p", drop)]) == "<svg><g>a[g</g>g][svg</svg>svg]^c"
    marked = "<svg><g>a[g</g>g][svg</svg>svg]c"
    assert rewrite("<p><svg><g>a</p>c", elements=[("svg, g", mark), ("p", _unwrap)]) == marked
    # an element whose own start tag is not in the output gets no end tag
    assert rewrite(source, elements=[("g", _unwrap), ("p", drop)]) == "<svg>a</svg>^c"
    assert rewrite(source, elements=[("svg", _remove), ("p", drop)]) == "^c"
    assert rewrite(source, elements=[("svg", fill), ("p", drop)]) == "<svg>Q</svg>^c"
    assert rewrite(f"<div>{source}</div>d", elements=[("div", fill), ("p", drop)]) == "<div>Q</div>d"
    assert rewrite("<div><svg><g><a>x</div>c", elements=[("g", fill), ("div", _unwrap)]) == "<svg><g>Q</g></svg>c"
    assert rewrite("<div><svg><g>x</div>c", elements=[("g", _unwrap), ("div", _unwrap)]) == "<svg>x</svg>c"
    # without a foreign element left open, a dropped end tag closes nothing, as before
    assert rewrite("<div><b><i>x</div>c", elements=[("div", _unwrap)]) == "<b><i>xc"


@pytest.mark.parametrize(
    ("source", "selector", "expected"),
    [
        pytest.param('<xmp/><a title="</xmp><script>x</script>">', "script", '<xmp/><a title="</xmp>">', id="raw-text"),
        pytest.param("<title/><b></title><i>", "i", "<title/><b></title>", id="rcdata"),
        pytest.param("<script/><b></script><i>", "i", "<script/><b></script>", id="script"),
        pytest.param("<div/>x</div>y", "div", "y", id="html-stays-open"),
        pytest.param('<div class="ad"/><img src=x></div>ok', ".ad", "ok", id="html-keeps-its-content"),
        pytest.param("<svg><input/>x</input>y</svg>", "input", "<svg>x</input>y</svg>", id="foreign-closes"),
        pytest.param("<svg><input>x</input>y</svg>", "input", "<svg>y</svg>", id="foreign-is-not-void"),
    ],
)
def test_rewrite_self_closing_slash_only_closes_foreign(source: str, selector: str, expected: str) -> None:
    assert rewrite(source, elements=[(selector, lambda element: element.remove())]) == expected


@pytest.mark.parametrize(
    ("source", "tags"),
    [
        pytest.param("<svg><title><script>x</script></title></svg>", ["svg", "title", "script"], id="svg-title"),
        pytest.param("<svg><style><b></style></svg>", ["svg", "style", "b"], id="svg-style-breakout"),
        pytest.param("<svg><g><style><a></a></style></g></svg>", ["svg", "g", "style", "a"], id="svg-style"),
        pytest.param("<math><xmp><mi></mi></xmp></math>", ["math", "xmp", "mi"], id="math-xmp"),
        pytest.param("<svg><desc><title><b></title></desc></svg>", ["svg", "desc", "title"], id="html-in-desc"),
        pytest.param(
            "<svg><foreignObject><xmp><b></xmp></foreignObject>",
            ["svg", "foreignobject", "xmp"],
            id="html-in-foreign-object",
        ),
        pytest.param("<math><mi><xmp><b></xmp></mi>", ["math", "mi", "xmp"], id="math-text"),
        pytest.param("<math><mi><mglyph><xmp><a>", ["math", "mi", "mglyph", "xmp", "a"], id="mglyph-stays-foreign"),
        pytest.param("<math><mi><malignmark><title><b>", ["math", "mi", "malignmark", "title", "b"], id="malignmark"),
        pytest.param("<math><annotation-xml><xmp><a>", ["math", "annotation-xml", "xmp", "a"], id="annotation"),
        pytest.param("<math><annotation-xml><svg><title>", ["math", "annotation-xml", "svg", "title"], id="svg-in"),
        pytest.param(
            '<math><annotation-xml encoding="Text/HTML"><xmp><a>',
            ["math", "annotation-xml", "xmp"],
            id="annotation-html",
        ),
        pytest.param(
            '<math><annotation-xml encoding="application/xhtml+xml"><xmp><a>',
            ["math", "annotation-xml", "xmp"],
            id="annotation-xhtml",
        ),
        pytest.param(
            "<math><annotation-xml x encoding=text/html><xmp><a>",
            ["math", "annotation-xml", "xmp"],
            id="annotation-second-attribute",
        ),
        pytest.param(
            "<math><annotation-xml encoding=text/htmx><xmp><a>",
            ["math", "annotation-xml", "xmp", "a"],
            id="annotation-other-type",
        ),
        pytest.param(
            "<math><annotation-xml encoding><xmp><a>",
            ["math", "annotation-xml", "xmp", "a"],
            id="annotation-bare-encoding",
        ),
    ],
)
def test_rewrite_raw_text_models_are_html_only(source: str, tags: list[str]) -> None:
    assert _seen(source) == tags


def test_rewrite_removes_a_script_in_an_svg_title() -> None:
    strip = [("script", lambda element: element.remove())]
    assert rewrite("<svg><title><script>x</script></title></svg>", elements=strip) == "<svg><title></title></svg>"


@pytest.mark.parametrize(
    ("source", "tags"),
    [
        pytest.param("<svg><p><title><b></title>", ["svg", "p", "title"], id="breakout-start"),
        pytest.param("<svg><font color=red><title><b></title>", ["svg", "font", "title"], id="breakout-font"),
        pytest.param("<svg><font face=x><title><b></title>", ["svg", "font", "title"], id="breakout-font-face"),
        pytest.param("<svg><font size=1><title><b></title>", ["svg", "font", "title"], id="breakout-font-size"),
        pytest.param("<svg><font><title><b></title>", ["svg", "font", "title", "b"], id="font-stays"),
        pytest.param("<svg><font data=1><title><b>", ["svg", "font", "title", "b"], id="font-other-attribute"),
        pytest.param("<svg><font \u015dize=1><title><b>", ["svg", "font", "title", "b"], id="font-wide-attribute"),
        pytest.param("<svg><g></p><title><b></title>", ["svg", "g", "title"], id="breakout-end-p"),
        pytest.param("<svg></br><title><b></title>", ["svg", "title"], id="breakout-end-br"),
        pytest.param(
            "<svg><desc><svg><g><p></p></desc><title><b>",
            ["svg", "desc", "svg", "g", "p", "title", "b"],
            id="to-integration-point",
        ),
        pytest.param(
            "<math><mi><mglyph><p></p><mglyph><title><b>",
            ["math", "mi", "mglyph", "p", "mglyph", "title", "b"],
            id="to-text-integration-point",
        ),
    ],
)
def test_rewrite_breaks_out_of_foreign_content(source: str, tags: list[str]) -> None:
    assert _seen(source) == tags


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("a<svg><g>b<p>c</p>d", "a<svg><g>b-+<p>c</p>d", id="start-tag"),
        pytest.param("a<svg><g>b</p>c", "a<svg><g>b-+</p>c", id="end-tag"),
    ],
)
def test_rewrite_breakout_writes_the_trailing_edits_of_the_element_it_ends(source: str, expected: str) -> None:
    def edit(element: Element) -> None:
        element.append("-")
        element.after("+")

    assert rewrite(source, elements=[("svg", edit)]) == expected


def test_rewrite_breakout_ends_a_removed_foreign_element() -> None:
    strip = [("svg", lambda element: element.remove())]
    assert rewrite("a<svg><g>b<p>c</p>d", elements=strip) == "a<p>c</p>d"


@pytest.mark.parametrize(
    ("source", "tags"),
    [
        pytest.param("<svg><g></svg><title><b></title>", ["svg", "g", "title"], id="foreign"),
        pytest.param("<svg><desc><b></desc><title><i>", ["svg", "desc", "b", "title"], id="foreign-under-html"),
        pytest.param("<div><svg><g></div><title><b></title>", ["div", "svg", "g", "title"], id="html"),
        pytest.param("<svg><desc><div><svg></div><title><b>", ["svg", "desc", "div", "svg", "title"], id="html-inner"),
        pytest.param("<div><svg><desc><svg></div><title><b>", ["div", "svg", "desc", "svg", "title", "b"], id="scoped"),
        pytest.param(
            "<div><math><mi></div><mglyph><title><b>",
            ["div", "math", "mi", "mglyph", "title", "b"],
            id="scoped-by-text-integration-point",
        ),
        pytest.param(
            "<div><math><annotation-xml></div><title><b>",
            ["div", "math", "annotation-xml", "title", "b"],
            id="scoped-by-annotation",
        ),
        pytest.param("<span><div><svg></span><title><b>", ["span", "div", "svg", "title", "b"], id="special-above"),
        pytest.param("<b><div><svg></b><title><i>", ["b", "div", "svg", "title"], id="adoption-agency"),
        pytest.param(
            f"<b>{'<ul>' * 7}<svg></b><title><i>",
            ["b", *["ul"] * 7, "svg", "title"],
            id="adoption-agency-reaches-the-top",
        ),
        pytest.param(
            f"<b>{'<ul>' * 8}<svg></b><title><i>",
            ["b", *["ul"] * 8, "svg", "title", "i"],
            id="adoption-agency-gives-up",
        ),
        pytest.param("<h1><svg></h2><title><b>", ["h1", "svg", "title"], id="any-heading"),
        pytest.param("<h1><div><svg></h2><title><b>", ["h1", "div", "svg", "title"], id="heading-below"),
        pytest.param("<li><ul><math></li><xmp><b>", ["li", "ul", "math", "xmp", "b"], id="list-item-scope-ul"),
        pytest.param("<li><ol><math></li><xmp><b>", ["li", "ol", "math", "xmp", "b"], id="list-item-scope-ol"),
        pytest.param("<li><div><svg></li><title><b>", ["li", "div", "svg", "title"], id="list-item-in-scope"),
        pytest.param("<form><svg></form><title><b>", ["form", "svg", "title", "b"], id="form-leaves-alone"),
        pytest.param(
            "<template><form><svg></form><title><b>",
            ["template", "form", "svg", "title"],
            id="form-in-template",
        ),
        pytest.param("<template><svg><g></template><title><b>", ["template", "svg", "g", "title"], id="template"),
        pytest.param("<table><svg><g></table><title><b>", ["table", "svg", "g", "title"], id="table"),
        pytest.param("<table><tr><td><svg></tr><title><b>", ["table", "tr", "td", "svg", "title"], id="row"),
        pytest.param("<table><td><svg><desc></td><title><b>", ["table", "td", "svg", "desc", "title"], id="cell"),
        pytest.param("<template><td><svg></td><title><b>", ["template", "td", "svg", "title"], id="template-cell"),
        pytest.param(
            "<table><td><svg><desc><table></td></table></desc><title><b>",
            ["table", "td", "svg", "desc", "table", "title", "b"],
            id="nested-table-bounds-table-scope",
        ),
        pytest.param(
            "<table><template><svg></table><title><b>",
            ["table", "template", "svg", "title", "b"],
            id="template-bounds-table-scope",
        ),
        pytest.param(
            "<table><td><span><td><svg></span><title><b>",
            ["table", "td", "span", "td", "svg", "title", "b"],
            id="scoped-by-cell",
        ),
    ],
)
def test_rewrite_end_tag_closes_the_foreign_elements_the_tree_builder_closes(source: str, tags: list[str]) -> None:
    assert _seen(source) == tags


@pytest.mark.parametrize(
    ("source", "tags"),
    [
        pytest.param("<svg><desc><td></desc><title><b>", ["svg", "desc", "td", "title", "b"], id="cell"),
        pytest.param("<svg><desc><body></desc><title><b>", ["svg", "desc", "body", "title", "b"], id="body"),
        pytest.param("<svg><desc><head></desc><title><b>", ["svg", "desc", "head", "title", "b"], id="head"),
        pytest.param("<svg><desc><frame></desc><title><b>", ["svg", "desc", "frame", "title", "b"], id="frame"),
        pytest.param(
            "<svg><desc><colgroup></desc><title><b>",
            ["svg", "desc", "colgroup", "title", "b"],
            id="colgroup",
        ),
        pytest.param("<svg><desc><image></desc><title><b>", ["svg", "desc", "image", "title", "b"], id="image"),
        pytest.param("<math><mi><html><mglyph><title><b>", ["math", "mi", "html", "mglyph", "title", "b"], id="html"),
        pytest.param("<svg><desc><form></desc><title><b>", ["svg", "desc", "form", "title"], id="form-is-opened"),
        pytest.param(
            "<form><svg><desc><form></desc><title><b>",
            ["form", "svg", "desc", "form", "title", "b"],
            id="nested-form",
        ),
        pytest.param(
            "<template><form><svg><desc><form></desc><title><b>",
            ["template", "form", "svg", "desc", "form", "title"],
            id="nested-form-in-template-is-opened",
        ),
        pytest.param(
            "<table><td><svg><desc><td></desc><title><b>",
            ["table", "td", "svg", "desc", "td", "title"],
            id="cell-in-table-is-opened",
        ),
        pytest.param("<td><svg></td><title><b>", ["td", "svg", "title", "b"], id="end-tag"),
        pytest.param("<html><svg></html><title><b>", ["html", "svg", "title", "b"], id="end-tag-html"),
        pytest.param("<span><td><svg></span><title><b>", ["span", "td", "svg", "title"], id="end-tag-below"),
        pytest.param(
            "<td><svg><desc><div></td></div></desc><title><b>",
            ["td", "svg", "desc", "div", "title", "b"],
            id="end-tag-under-html",
        ),
        pytest.param(
            "<svg><desc><td><svg></td><title><b>",
            ["svg", "desc", "td", "svg", "title", "b"],
            id="end-tag-over-foreign",
        ),
        pytest.param(
            "<svg><desc><body><div></body></desc><title><b>",
            ["svg", "desc", "body", "div", "title"],
            id="end-tag-leaves-the-elements-above-open",
        ),
        pytest.param(
            "<svg><td><desc><td></td><title><b>",
            ["svg", "td", "desc", "td", "title", "b"],
            id="end-tag-closes-the-foreign-element",
        ),
    ],
)
def test_rewrite_looks_past_an_element_the_tree_builder_does_not_open(source: str, tags: list[str]) -> None:
    assert _seen(source) == tags


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("<td>x</td>y", "<td>x</td>+y", id="outermost"),
        pytest.param("<div><td>x</td>y", "<div><td>x</td>+y", id="in-html"),
        pytest.param("<svg><desc><td>x</td>y", "<svg><desc><td>x</td>+y", id="in-integration-point"),
        pytest.param("<svg><desc><td><td>x</td>y", "<svg><desc><td><td>x</td>+y+", id="innermost"),
    ],
)
def test_rewrite_end_tag_closes_an_element_the_tree_builder_does_not_open(source: str, expected: str) -> None:
    assert rewrite(source, elements=[("td", lambda element: element.after("+"))]) == expected


@pytest.mark.parametrize(
    ("source", "tags", "texts"),
    [
        pytest.param("a<svg>b<![CDATA[<i>x]]>c</svg>", ["svg"], ["a", "b", "<i>x", "c"], id="section"),
        pytest.param("<svg><![CDATA[]]><g>", ["svg", "g"], [""], id="empty"),
        pytest.param("<svg><![CDATA[a]]b]]]><g>", ["svg", "g"], ["a]]b]"], id="brackets"),
        pytest.param("<svg><![CDATA[<i>", ["svg"], ["<i>"], id="eof"),
        pytest.param("<svg><![CDATA[", ["svg"], [""], id="eof-empty"),
        pytest.param("<svg><desc><![CDATA[<i>]]>", ["svg", "desc"], ["<i>"], id="integration-point"),
        pytest.param("<svg><desc><body><![CDATA[<i>]]>", ["svg", "desc", "body"], ["<i>"], id="past-unopened"),
        pytest.param("<svg><desc><p><![CDATA[<i>]]>", ["svg", "desc", "p"], ["]]>"], id="html-inside"),
        pytest.param("<p><![CDATA[<i>]]>", ["p"], ["]]>"], id="html"),
        pytest.param(
            "<svg><![CDATA[><desc><xmp>]]><script>x</script>",
            ["svg", "script"],
            ["><desc><xmp>", "x"],
            id="markup-in-section",
        ),
        pytest.param(
            "<svg><![CDATA[Ā<i>\U0001f600]]></svg>",
            ["svg"],
            ["Ā<i>\U0001f600"],
            id="wide-characters",
        ),
    ],
)
def test_rewrite_cdata_section_in_foreign_content(source: str, tags: list[str], texts: list[str]) -> None:
    seen: list[str] = []
    assert rewrite(source, text=lambda text: seen.append(text.text)) == source
    assert seen == texts
    assert _seen(source) == tags


def test_rewrite_cdata_section_text_is_escaped_when_rewritten() -> None:
    result = rewrite("<svg><![CDATA[<B>&]]></svg>", text=lambda text: text.set_text(text.text.lower()))
    assert result == "<svg>&lt;b&gt;&amp;</svg>"


@pytest.mark.parametrize(
    ("source", "selector", "expected"),
    [
        # a start tag that closes an open <p> closes it on the spine too, so the foreign end tags after it are seen
        pytest.param(
            "<svg><foreignObject><p>a<p>b</p></foreignObject></svg><p>after</p>", "svg", "<p>after</p>", id="p-p"
        ),
        pytest.param(
            "<svg><foreignObject><p>a<div>b</div></foreignObject><rect/><circle/></svg>c",
            "rect",
            "<svg><foreignObject><p>a<div>b</div></foreignObject><circle/></svg>c",
            id="p-div",
        ),
        pytest.param("<math><mtext><p>a<p>b</p></mtext></math>ok", "math", "ok", id="mathml"),
        pytest.param("<svg><desc><p><span>a<ul></ul></desc></svg>ok", "svg", "ok", id="p-with-open-child"),
        # a <p> that is still open keeps the integration point open, as in a browser
        pytest.param("<svg><foreignObject><p>a</foreignObject></svg>b", "svg", "", id="p-left-open"),
        pytest.param("<svg><desc><p><button>a<div></div></button></desc></svg>b", "svg", "", id="button-scope"),
    ],
)
def test_rewrite_implied_paragraph_end_inside_integration_point(source: str, selector: str, expected: str) -> None:
    assert rewrite(source, elements=[(selector, _remove)]) == expected


def test_rewrite_implied_paragraph_end_outside_foreign_content_keeps_the_spine() -> None:
    assert rewrite("<p>a<p>b</p>c", elements=[("p p", _set("x", "1"))]) == '<p>a<p x="1">b</p>c'


def test_rewrite_dropped_tag_that_closed_a_paragraph_leaves_its_end_tag() -> None:
    source = f"<svg><foreignObject><p><b>a<div>b</div></foreignObject>{_TITLE}</svg>"
    expected = f"<svg><foreignObject><p><b>a</b></p></foreignObject>{_TITLE}</svg>"
    assert rewrite(source, elements=[("div", _remove)]) == expected
    assert _seen(expected) == _seen(source.replace("<div>b</div>", "</b></p>"))


def test_rewrite_cell_start_tag_closes_the_cell_and_the_foreign_content_in_it() -> None:
    source = "<table><tr><td><svg><desc><td></td></desc><title><!--</title><img src=x onerror=a()>-->"
    assert _seen(source) == ["table", "tr", "td", "svg", "desc", "td", "title", "img"]
    assert len(parse(source).select("img")) == 1
    # a foreign <th> is an SVG element and closes nothing
    assert _seen("<table><tr><td><svg><g><th>x</th><title><i>") == ["table", "tr", "td", "svg", "g", "th", "title", "i"]
    cells: list[Element] = []

    def drop_second(element: Element) -> None:
        cells.append(element)
        if len(cells) == 2:
            element.remove()

    # the dropped <td> ended the first cell and the <svg> in it, so their end tags stand in for it
    dropped = rewrite("<table><tr><td><svg><desc><td>x", elements=[("td", drop_second)])
    assert dropped == "<table><tr><td><svg><desc></desc></svg></td>"


def test_rewrite_set_content_on_paragraph_closed_by_block_in_integration_point() -> None:
    assert rewrite("<svg><foreignObject><p>a<div>b", elements=[("p", lambda element: element.set_content("Z"))]) == (
        "<svg><foreignObject><p>Z<div>b"
    )


def test_rewrite_unwrapped_paragraph_closed_by_block_in_integration_point() -> None:
    assert rewrite("<svg><foreignObject><p>a<div>b</div>", elements=[("p", _unwrap)]) == (
        "<svg><foreignObject>a<div>b</div>"
    )


def test_rewrite_set_content_on_element_an_end_tag_leaves_unclosed() -> None:
    assert rewrite("<b><div><svg><g>x</b>c", elements=[("g", lambda element: element.set_content("Z"))]) == (
        "<b><div><svg><g>Z</b>c"
    )


def test_rewrite_nested_form_in_integration_point_is_written_unchanged() -> None:
    source = "<form><svg><foreignObject><p><form>x"
    assert rewrite(source, elements=[("p", lambda _: None)]) == source


def test_rewrite_th_start_tag_closes_the_cell_and_the_foreign_content_in_it() -> None:
    cells: list[Element] = []

    def drop_second(element: Element) -> None:
        cells.append(element)
        if len(cells) == 2:
            element.remove()

    assert rewrite("<table><tr><th><svg><desc><th>x", elements=[("th", drop_second)]) == (
        "<table><tr><th><svg><desc></desc></svg></th>"
    )


def test_parse_unclosed_cdata_section_in_foreign_content_is_text() -> None:
    assert [element.text for element in parse("<svg><![CDATA[<i>").select("svg")] == ["<i>"]


_RAW_TEXT: Final[frozenset[str]] = frozenset({
    "iframe",
    "noembed",
    "noframes",
    "noscript",
    "plaintext",
    "script",
    "style",
    "textarea",
    "title",
    "xmp",
})


def test_rewrite_calls_handlers_on_the_elements_a_parse_builds(wpt_html_tree_corpus: WptHtmlTreeCorpus) -> None:
    failures = [
        f"{case['file']}: {case['data']!r}: {problem}"
        for case in [*wpt_html_tree_corpus["cases"], *wpt_html_tree_corpus["exclusions"]]
        if case["context"] is None
        for problem in _misread(case["data"])
    ]
    assert not failures, f"{len(failures)} misread\n" + "\n".join(failures[:10])


def _misread(source: str) -> list[str]:
    mark = "\ue000"
    marked = rewrite(source, elements=[("*", lambda element: element.before(mark))])
    starts = [at - before for before, at in enumerate(at for at, char in enumerate(marked) if char == mark)]
    seen = set(starts)
    located = [
        (element, location)
        for element in _parsed_elements(parse(source, source_locations=True, scripting=True))
        if (location := element.source_location) is not None
    ]
    return [
        f"no handler call for <{element.tag}> at {location.start_tag.start_offset}"
        for element, location in located
        if location.start_tag.start_offset not in seen
    ] + [
        f"handler call at {starts[inside]} inside the text of <{element.tag}>"
        for element, location in located
        if (inside := _call_inside_raw_text(element, location, starts, len(source))) is not None
    ]


def _parsed_elements(node: Document | ParsedElement | ShadowRoot) -> Iterator[ParsedElement]:
    for child in node.descendants:
        if isinstance(child, ParsedElement):
            yield child
            if child.shadow_root is not None:
                yield from _parsed_elements(child.shadow_root)


def _call_inside_raw_text(element: ParsedElement, location: SourceLocation, starts: list[int], end: int) -> int | None:
    if element.namespace is not Namespace.HTML or element.tag not in _RAW_TEXT:
        return None
    inside = bisect_left(starts, location.start_tag.end_offset)
    text_end = end if location.end_tag is None else location.end_tag.start_offset
    return inside if inside < bisect_left(starts, text_end) else None


_BREAKOUT_RUN: Final[str] = "<svg><g><p></b><i></g>"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("<template><col><textarea></template><script>a()</script>", id="template-column"),
        pytest.param("<frameset><title><b></title><noframes><i></noframes>", id="frameset"),
        pytest.param("<a><svg><desc><a></desc></a></desc><style><i></style>", id="adoption-agency-in-foreign"),
        pytest.param("<b><svg><desc><b></desc><i></b><style><i></style>", id="formatting-reopened-in-foreign"),
        pytest.param(
            "<svg><foreignObject><div><style><i></style></div></foreignObject><style><i>", id="foreign-object"
        ),
        pytest.param("<math><annotation-xml encoding=text/html><style><i></style></annotation-xml>", id="annotation"),
        pytest.param("<math><annotation-xml><svg><style><i></style></svg></annotation-xml>", id="annotation-svg"),
        pytest.param("<math><mi><mglyph><style><i></style><malignmark></mi><style><i>", id="text-integration"),
        pytest.param("<svg><g></p><style><i></style>", id="paragraph-end-breaks-out"),
        pytest.param("<svg><g></br><style><i></style>", id="br-end-breaks-out"),
        pytest.param("<svg><font color=red><style><i></style>", id="font-breakout"),
        pytest.param("<svg><font><style><i></style>", id="plain-font"),
        pytest.param("<table><tr><td><svg><desc><td></td></desc><title><i></title>", id="cell-in-integration-point"),
        pytest.param("<select><svg><style><i></style></select><style><i>", id="select"),
        pytest.param("<svg><desc><p><svg><p><style><i></style>", id="nested-integration"),
        pytest.param("<div><template shadowrootmode=open><svg><p><style><i>", id="shadow-root"),
        pytest.param("<svg><desc><table><col><style><i></style>", id="column-group-implied"),
        pytest.param("<template></template>&amp;<frameset><title><b></title>", id="reference-ends-the-head"),
        pytest.param("<template>&amp;<col><textarea><i></textarea></template>", id="reference-in-template"),
        pytest.param("<svg><p\nid=a></p>\n<b>\n<svg><g\n/><p>x</b><style><i></style>", id="resumed-after-lines"),
        pytest.param("<svg><p><plaintext></p><style><i>", id="plaintext"),
        pytest.param("<div><div>\n<div><div>\n<svg><p><style><i></style>", id="multi-line"),
        pytest.param("<frameset><img><noframes><i></noframes>", id="ignored-void"),
        pytest.param(_BREAKOUT_RUN * 3, id="repeated-breakout"),
    ],
)
def test_rewrite_calls_handlers_on_the_elements_a_parse_builds_around_foreign_content(source: str) -> None:
    assert _misread(source) == []


def test_rewrite_removes_a_script_the_adoption_agency_moves_out_of_foreign_content() -> None:
    source = "<a><svg><desc><a></desc></a></desc><style><script>run()</script></style>"
    assert "run()" not in rewrite(source, elements=[("script", lambda element: element.remove())])


@pytest.mark.parametrize(
    ("handlers", "expected"),
    [
        pytest.param([("g", _unwrap)], "<svg><circle><path></path></circle><title>", id="kept"),
        pytest.param([("g", _unwrap), ("circle", _remove)], "<svg><title>", id="removed"),
    ],
)
def test_rewrite_unwrapped_foreign_end_tag_leaves_end_tags_for_the_elements_it_closed(
    handlers: list[tuple[str, Callable[[Element], None]]], expected: str
) -> None:
    assert rewrite("<svg><g><circle><path></g><title>", elements=handlers) == expected


def test_rewrite_follows_the_tree_builder_in_linear_time(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda count: partial(rewrite, _BREAKOUT_RUN * count, elements=[("*", lambda _: None)]),
        (500, 2000),
    )


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("", id="parked-between-blocks"),
        pytest.param("<template>", id="inside-template"),
        pytest.param("<svg><foreignObject>", id="inside-integration-point"),
        pytest.param("<math><mi><b>", id="inside-text-integration-point"),
        pytest.param("<form><div><template shadowrootmode=open>", id="inside-form-and-shadow-root"),
        pytest.param(f"<html title={'x' * 300_000}><template>", id="before-the-head"),
    ],
)
def test_rewrite_follows_the_tree_builder_through_a_long_document(prefix: str) -> None:
    mixed = (
        "<b>a<i>b</b>c</i><table><tr><td>x<td><svg><desc><p>y</desc></svg></table><p><a href=#>z<a>w</a>"
        "<style><i></style><svg><g><path/></g><title><b></title></svg><template><col><textarea><i></textarea></template>\n"
    )
    assert _misread(prefix + mixed * 3000) == []


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("<template>", id="inside-template"),
        pytest.param("<svg><foreignObject>", id="inside-integration-point"),
        pytest.param("<b><svg><p>", id="after-breakout"),
    ],
)
def test_rewrite_following_the_tree_builder_keeps_memory_to_the_open_elements(
    prefix: str, peak_bytes: PeakBytes
) -> None:
    script = (
        "import sys, tracemalloc\n"
        "from turbohtml.rewrite import rewrite\n"
        "source = sys.argv[1] + '<div><p>x <b>y</b></p></div>' * 40000\n"
        "tracemalloc.start()\n"
        "rewrite(source)\n"
        "print(tracemalloc.get_traced_memory()[1])\n"
    )
    assert peak_bytes(script, (prefix,)) - peak_bytes(script, ("",)) < 2 * 1024 * 1024


def test_rewrite_removed_attribute_is_gone_for_the_rest_of_the_handler() -> None:
    seen: list[_Read] = []

    def handler(element: Element) -> None:
        element.remove_attribute("b")
        seen.extend((element.attrs, element.get("b", "gone"), element.has_attribute("b"), element.get("c")))
        element.set_attribute("b", "again")
        seen.append(element.attrs)

    assert rewrite("<p a=1 b=2 c=3>t</p>", elements=[("p", handler)]) == '<p a="1" c="3" b="again">t</p>'
    assert seen == [(("a", "1"), ("c", "3")), "gone", False, "3", (("a", "1"), ("c", "3"), ("b", "again"))]


def test_rewrite_removed_attribute_is_gone_for_later_rules_and_descendants() -> None:
    matched: list[tuple[str, tuple[tuple[str, str | None], ...]]] = []

    def drop(element: Element) -> None:
        element.remove_attribute("a")

    def record(element: Element) -> None:
        matched.append((element.tag, element.attrs))

    rules: Final = [("p", drop), ("p[a]", record), ("p[c]", record), ("p[a] i", record), ("p[b] > i", record)]
    assert rewrite("<p a b c><i>t</i></p>", elements=rules) == "<p b c><i>t</i></p>"
    assert matched == [("p", (("b", None), ("c", None))), ("i", ())]


def test_rewrite_handler_raising_after_removing_attributes() -> None:
    def handler(element: Element) -> None:
        for name, _ in element.attrs[:20]:
            element.remove_attribute(name)
        msg = "after-removal-boom"
        raise ValueError(msg)

    names: Final = "".join(f" a{index}=v" for index in range(40))
    with pytest.raises(ValueError, match="after-removal-boom"):
        rewrite(f"<p{names}>t</p>", elements=[("p", handler)])


@pytest.mark.parametrize("count", [3, 16, 17, 40, 1000])
def test_rewrite_attribute_edits_by_name_on_a_long_tag(count: int) -> None:
    def handler(element: Element) -> None:
        for index in range(0, count, 2):
            element.remove_attribute(f"a{index}")
        for index in range(count):
            assert element.has_attribute(f"a{index}") is bool(index % 2)
            assert element.get(f"a{index}") == (f"v{index}" if index % 2 else None)
        assert element.get("never-seen") is None
        assert element.attrs == tuple((f"a{index}", f"v{index}") for index in range(1, count, 2))
        for index in range(1, count, 4):
            element.set_attribute(f"a{index}", "set")
        for index in range(count):
            element.set_attribute(f"b{index}", "new")
        assert element.get(f"b{count - 1}") == "new"
        element.remove_attribute("b0")
        assert not element.has_attribute("b0")

    values: Final = {index: f"v{index}" for index in range(1, count, 2)}
    values.update((index, "set") for index in range(1, count, 4))
    source: Final = "".join(f" a{index}=v{index}" for index in range(count))
    kept: Final = "".join(f' a{index}="{value}"' for index, value in values.items())
    added: Final = "".join(f' b{index}="new"' for index in range(1, count))
    assert rewrite(f"<p{source}>t</p>", elements=[("p", handler)]) == f"<p{kept}{added}>t</p>"


def test_rewrite_attribute_names_past_the_interning_key_stay_distinct() -> None:
    seen: list[_Read] = []

    def handler(element: Element) -> None:
        seen.append(element.get(first))
        seen.append(element.get(second))
        element.set_attribute(second, "set")
        seen.append(element.get(second))
        element.remove_attribute(first)
        seen.append(element.has_attribute(first))
        seen.append(element.get(second))

    first: Final = f"data-{'z' * 300}"
    second: Final = f"{first}y"
    assert rewrite(f"<p {first}=1 {second}=2>t</p>", elements=[("p", handler)]) == f'<p {second}="set">t</p>'
    assert seen == ["1", "2", "set", False, "set"]


def test_rewrite_edited_start_tag_writes_attribute_names_past_the_interning_key_whole() -> None:
    first: Final = f"data-{'z' * 300}"
    second: Final = f"{first}y"

    def add_marker(element: Element) -> None:
        element.set_attribute("marker", "1")

    assert rewrite(f"<p {first}=1 {second}=2>t</p>", elements=[("p", add_marker)]) == (
        f'<p {first}="1" {second}="2" marker="1">t</p>'
    )


def test_rewrite_selector_past_the_interning_key_matches_only_its_own_name() -> None:
    first: Final = f"data-{'z' * 300}"
    second: Final = f"{first}y"
    seen: list[str] = []

    def record(element: Element) -> None:
        seen.append(element.tag)

    source: Final = f"<p {first}=1><i {second}=2>t</i></p>"
    assert rewrite(source, elements=[(f"[{second}]", record)]) == source
    assert seen == ["i"]


def _strip_each(element: Element) -> None:
    for name, _ in element.attrs:
        element.remove_attribute(name)


def _strip_each_from_the_end(element: Element) -> None:
    for name, _ in reversed(element.attrs):
        element.remove_attribute(name)


def _read_each(element: Element) -> None:
    for name, _ in element.attrs:
        assert element.get(name) is None
        assert element.has_attribute(name)


def _set_each(element: Element) -> None:
    for name, _ in element.attrs:
        element.set_attribute(name, "")


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("a", id="short-names"),
        pytest.param("x" * 260, id="names-past-the-interning-key"),
    ],
)
@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(_strip_each, id="remove"),
        pytest.param(_strip_each_from_the_end, id="remove-reversed"),
        pytest.param(_read_each, id="get-has"),
        pytest.param(_set_each, id="set"),
    ],
)
def test_one_call_per_name_on_a_long_tag_scans_in_linear_time(
    handler: Callable[[Element], None], prefix: str, work_steps: WorkSteps
) -> None:
    work_steps.assert_linear(
        lambda count: partial(rewrite, _long_tag(count, prefix), elements=[("p", handler)]), (500, 2000)
    )


def _long_tag(count: int, prefix: str) -> str:
    names: Final = "".join(f" {prefix}{index}" for index in range(count))
    return f"<p{names}>"


def test_removing_and_setting_one_name_repeatedly_on_a_long_tag_scans_in_linear_time(work_steps: WorkSteps) -> None:
    work_steps.assert_linear(
        lambda count: partial(rewrite, _long_tag(count, "a"), elements=[("p", _toggler(count))]), (500, 2000)
    )


def _toggler(count: int) -> Callable[[Element], None]:
    def toggle(element: Element) -> None:
        for _ in range(count):
            element.remove_attribute("a0")
            element.set_attribute("a0", "v")

    return toggle
