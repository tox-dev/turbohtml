"""Behavioral tests for the DOM-less streaming rewriter (turbohtml.rewrite)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from turbohtml import parse
from turbohtml._internal._selectors import SelectorSyntaxError
from turbohtml.rewrite import Element, rewrite

if TYPE_CHECKING:
    from collections.abc import Callable


def _set(name: str, value: str) -> Callable[[Element], None]:
    def handler(element: Element) -> None:
        element.set_attribute(name, value)

    return handler


def test_rewrite_noop_returns_input_unchanged() -> None:
    src = '<!DOCTYPE html><html><body><p class=x>Hi &amp; bye</p><img src="a.png"></body></html>'
    assert rewrite(src) == src


def test_rewrite_empty_input() -> None:
    assert not rewrite("")


def test_rewrite_rejects_non_str() -> None:
    with pytest.raises(TypeError):
        rewrite(b"<p>")  # ty: ignore[invalid-argument-type]


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
    ],
)
def test_rewrite_streamable_selectors_match(source: str, selector: str, expected: str) -> None:
    assert rewrite(source, elements=[(selector, _set("k", "v"))]) == expected


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


def test_rewrite_set_attribute_escapes_value() -> None:
    assert rewrite("<p>x</p>", elements=[("p", _set("t", '"&<'))]) == '<p t="&quot;&amp;<">x</p>'


def test_rewrite_set_attribute_empty_name_raises() -> None:
    with pytest.raises(ValueError, match="empty"):
        rewrite("<p>x</p>", elements=[("p", _set("", "v"))])


def test_rewrite_set_attribute_non_str_name_raises() -> None:
    def handler(element: Element) -> None:
        element.set_attribute(1, "v")  # ty: ignore[invalid-argument-type]

    with pytest.raises(TypeError):
        rewrite("<p>x</p>", elements=[("p", handler)])


def test_rewrite_remove_attribute_present_and_absent() -> None:
    def handler(element: Element) -> None:
        element.remove_attribute("data-x")
        element.remove_attribute("data-missing")
        element.remove_attribute("data-custom-gone")

    assert rewrite("<p data-x=1 keep=2>t</p>", elements=[("p", handler)]) == '<p keep="2">t</p>'


def test_rewrite_get_and_has_attribute() -> None:
    seen: dict[str, object] = {}

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
    source = "<div>" * depth + "x" + "</div>" * depth
    result = rewrite(source, elements=[("div", _set("k", "1"))])
    assert result.count('<div k="1">') >= 1
    assert "x" in result


def test_rewrite_misnested_end_tags() -> None:
    assert rewrite("<b><i>x</b>y</i>", elements=[("i", _set("m", "1"))]) == '<b><i m="1">x</b>y</i>'


def test_rewrite_stray_end_tag_emitted_verbatim() -> None:
    assert rewrite("</p>text", elements=[("p", _set("m", "1"))]) == "</p>text"


def test_rewrite_stray_end_tag_with_open_elements() -> None:
    # </span> matches nothing on the open stack though <div> is open; it streams verbatim
    assert rewrite("<div>a</span>b</div>", elements=[("div", _set("m", "1"))]) == '<div m="1">a</span>b</div>'


def test_rewrite_stray_custom_end_tag_name_mismatch() -> None:
    # </x-bar> shares the unknown-atom bucket with the open <x-foo> but its name differs,
    # so it matches nothing and streams verbatim
    result = rewrite("<x-foo>a</x-bar>b</x-foo>", elements=[("x-foo", _set("m", "1"))])
    assert result == '<x-foo m="1">a</x-bar>b</x-foo>'


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
        assert comment.kind == "comment"
        assert comment.text == " keep "
        comment.set_text(" edited ")

    assert rewrite("<p>a<!-- keep -->b</p>", comments=handler) == "<p>a<!-- edited -->b</p>"


def test_rewrite_comment_remove_and_before() -> None:
    def handler(comment: Element) -> None:
        comment.before("X", html=True)
        comment.remove()

    assert rewrite("<p>a<!--c-->b</p>", comments=handler) == "<p>aXb</p>"


def test_rewrite_doctype_handler_reads_fields() -> None:
    seen: dict[str, object] = {}

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
    seen: dict[str, object] = {}

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
            element.get()  # ty: ignore[missing-argument]

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
            text.set_text(1)  # ty: ignore[invalid-argument-type]

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
    source = "<div><a href=/1>one</a><a href=/2>two</a></div>"

    def add_rel(element: Element) -> None:
        element.set_attribute("rel", "noopener")

    streamed = rewrite(source, elements=[("a[href]", add_rel)])

    tree = parse(source)
    for anchor in tree.select("a[href]"):
        anchor.attrs["rel"] = "noopener"
    assert streamed == '<div><a href="/1" rel="noopener">one</a><a href="/2" rel="noopener">two</a></div>'


def test_rewrite_wide_document_is_bounded() -> None:
    wide = "<ul>" + "<li>x</li>" * 20000 + "</ul>"
    result = rewrite(wide, elements=[("li", _set("k", "1"))])
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
    seen: dict[str, object] = {}

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
    seen: list[object] = []

    def handler(doctype: Element) -> None:
        seen.append(doctype.name)

    rewrite("<!DOCTYPE>x", doctype=handler)
    assert seen == [None]


@pytest.mark.parametrize(
    "method",
    [
        pytest.param(lambda h: h.set_content("x"), id="set_content"),
        pytest.param(lambda h: h.append("x"), id="append"),
        pytest.param(lambda h: h.prepend("x"), id="prepend"),
        pytest.param(lambda h: h.remove_and_keep_content(), id="remove_and_keep_content"),
        pytest.param(lambda h: h.get("x"), id="get"),
        pytest.param(lambda h: h.has_attribute("x"), id="has_attribute"),
        pytest.param(lambda h: h.remove_attribute("x"), id="remove_attribute"),
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
        pytest.param(lambda h: h.before(123), id="before"),
        pytest.param(lambda h: h.after(123), id="after"),
        pytest.param(lambda h: h.replace(123), id="replace"),
        pytest.param(lambda h: h.set_content(123), id="set_content"),
        pytest.param(lambda h: h.append(123), id="append"),
        pytest.param(lambda h: h.prepend(123), id="prepend"),
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
            element.get(123)  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError):
            element.has_attribute(123)  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError):
            element.remove_attribute(123)  # ty: ignore[invalid-argument-type]
        with pytest.raises(TypeError):
            element.set_attribute("ok", 123)  # ty: ignore[invalid-argument-type]

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
        rewrite(source, **{key: boom})  # ty: ignore[invalid-argument-type]


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
