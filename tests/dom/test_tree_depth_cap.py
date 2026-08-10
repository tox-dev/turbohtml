from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Final

import pytest

from turbohtml import (
    Document,
    Element,
    Html,
    Indent,
    MicrodataItem,
    Range,
    RdfaItem,
    StructuredData,
    Text,
    parse,
    parse_xml,
)
from turbohtml.clean import sanitize

if TYPE_CHECKING:
    from collections.abc import Callable

_PATHOLOGICAL: Final = 20000
_DEEP: Final = 1_200


def _max_element_depth(node: Element | Document) -> int:
    deepest = 0
    stack: list[tuple[object, int]] = [(node, 0)]
    while stack:
        current, depth = stack.pop()
        here = depth + 1 if isinstance(current, Element) else depth
        deepest = max(deepest, here)
        stack.extend((child, here) for child in getattr(current, "children", []) or [])
    return deepest


def _nested(tag: str, levels: int, text: str | None = "X") -> Element:
    return _nested_with_deepest(tag, levels, text)[0]


def _nested_with_deepest(tag: str, levels: int, text: str | None = "X") -> tuple[Element, Element]:
    root = Element(tag)
    node = root
    for _ in range(levels):
        child = Element(tag)
        node.append(child)
        node = child
    if text is not None:
        node.append(Text(text))
    return root, node


def test_deeply_nested_parse_caps_element_depth() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert _max_element_depth(doc) <= 520  # bounded near the 512 cap, not ~20000


def test_deeply_nested_parse_keeps_every_element() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert len(doc.find_all("div")) == _PATHOLOGICAL  # capped elements survive as siblings


def test_capped_parse_round_trips_through_serialization() -> None:
    doc = parse("<div>" * _PATHOLOGICAL)
    assert doc.html.count("<div>") == _PATHOLOGICAL
    assert doc.html.count("</div>") == _PATHOLOGICAL


def test_sanitize_of_deeply_nested_input_does_not_overflow() -> None:
    result = sanitize("<div>" * _PATHOLOGICAL)  # F1 repro: parse + recursive sanitize walk
    assert result.count("&lt;div&gt;") == _PATHOLOGICAL  # div is escaped, not kept, but every one survives


@pytest.mark.parametrize("levels", [pytest.param(400, id="under-cap"), pytest.param(511, id="at-cap")])
def test_nesting_below_the_cap_is_left_untouched(levels: int) -> None:
    doc = parse("<div>" * levels)
    assert _max_element_depth(doc) == levels + 2  # html + body + every div, fully nested


@pytest.mark.parametrize("source", [pytest.param("xml", id="parsed-xml"), pytest.param("mutation", id="mutation")])
def test_deep_text_and_serialization_are_complete(source: str) -> None:
    root = (
        parse_xml("<x>" * _DEEP + "bottom" + "</x>" * _DEEP).root if source == "xml" else _nested("x", _DEEP, "bottom")
    )
    assert isinstance(root, Element)
    expected_open_count = _DEEP if source == "xml" else _DEEP + 1
    assert (root.text, root.html.count("<x>"), "bottom" in root.serialize(Html(layout=Indent(1)))) == (
        "bottom",
        expected_open_count,
        True,
    )


def test_find_text_reads_a_deep_programmatic_tree() -> None:
    root = _nested("x", _DEEP, "bottom")
    assert root.find("x", text="bottom") is not None


def test_css_has_reads_a_deep_programmatic_tree() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.attrs["class"] = "target"
    assert root.matches(":has(.target)")
    assert root.select_one("x:has(.target)") is not None


@pytest.mark.parametrize(
    ("method", "args"),
    [
        pytest.param("to_markdown", (), id="markdown"),
        pytest.param("to_text", (), id="text"),
        pytest.param("to_annotated_text", ({"div": ["deep"]},), id="annotated-text"),
    ],
)
def test_recursive_renderers_reject_a_deep_tree_before_output(method: str, args: tuple[object, ...]) -> None:
    root = _nested("div", _DEEP)
    with pytest.raises(RecursionError, match=rf"{method}\(\).*1024"):
        getattr(root, method)(*args)


def test_recursive_renderers_accept_the_last_supported_depth() -> None:
    root = _nested("b", 1_022)
    assert "X" in root.to_markdown()
    assert root.to_text() == "X"


@pytest.mark.parametrize("duplicate", [pytest.param(copy.copy, id="copy"), pytest.param(copy.deepcopy, id="deepcopy")])
def test_clone_walk_copies_a_deep_tree(duplicate: Callable[[Element], Element]) -> None:
    root = _nested("x", _DEEP, "bottom")
    clone = duplicate(root)
    assert clone.text == "bottom"
    assert clone.equals(root)


def test_clone_walk_copies_deep_siblings() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.append(Element("a"))
    deepest.append(Element("b"))
    clone = copy.copy(root)
    assert (clone is not root, clone.html) == (True, root.html)


def test_append_copies_a_deep_foreign_tree() -> None:
    root = Element("root")
    child = _nested("x", _DEEP, "bottom")
    root.append(child)
    assert root.text == "bottom"
    assert child.text == "bottom"


def test_normalize_walk_reaches_deep_text() -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    deepest.append(Text("a"))
    deepest.append(Text(""))
    deepest.append(Text("b"))
    root.normalize()
    assert root.text == "ab"


def test_normalize_walk_merges_text_within_the_cap() -> None:
    element = Element("div")
    element.append(Text("a"))
    element.append(Text("b"))
    element.normalize()
    assert element.text == "ab"


def test_readability_reaches_a_deep_programmatic_tree_before_render_preflight() -> None:
    root = _nested("x", _DEEP, None)
    root.append(Element("p", None, [Text("A long article sentence, " * 20)]))
    assert root.main_content() == root
    with pytest.raises(RecursionError, match=r"to_text\(\).*1024"):
        root.main_text()
    with pytest.raises(RecursionError, match=r"to_text\(\).*1024"):
        root.article()


@pytest.mark.parametrize("method", ["clone_contents", "extract_contents", "delete_contents"])
@pytest.mark.parametrize("deep_boundary", [pytest.param("start", id="deep-start"), pytest.param("end", id="deep-end")])
def test_range_rejects_a_deep_partial_boundary_before_mutation(method: str, deep_boundary: str) -> None:
    root, deepest = _nested_with_deepest("x", _DEEP, None)
    text_node = Text("bottom")
    deepest.append(text_node)
    if deep_boundary == "start":
        selection = Range(text_node, 0)
        selection.set_end(root, 1)
    else:
        selection = Range(root, 0)
        selection.set_end(text_node, 0)
    before = root.html
    with pytest.raises(RecursionError, match=rf"{method}\(\).*400"):
        getattr(selection, method)()
    assert (root.html, selection.start_container, selection.end_container) == (
        before,
        text_node if deep_boundary == "start" else root,
        root if deep_boundary == "start" else text_node,
    )


def test_structured_data_methods_accept_deep_dom_with_shallow_records() -> None:
    document = parse_xml("<x>" * _DEEP + "<item itemscope='' typeof='Thing'/>" + "</x>" * _DEEP)
    microdata = [MicrodataItem(type=None, id=None, properties={})]
    rdfa = [RdfaItem(vocab=None, type=["Thing"], resource=None, properties={})]
    assert (document.microdata(), document.rdfa(), document.structured_data()) == (
        microdata,
        rdfa,
        StructuredData(json_ld=[], microdata=microdata, opengraph={}, microformats=[], rdfa=rdfa, dublin_core={}),
    )


@pytest.mark.parametrize("method", ["microdata", "structured_data"])
def test_microdata_rejects_cyclic_itemref_graph(method: str) -> None:
    document = parse(
        "<div itemscope itemref='b'></div>"
        "<div id='b' itemprop='next' itemscope itemref='c'></div>"
        "<div id='c' itemprop='next' itemscope itemref='b'></div>"
    )
    with pytest.raises(RecursionError, match="cyclic nested item graph"):
        getattr(document, method)()


@pytest.mark.parametrize("method", ["microdata", "structured_data"])
def test_microdata_rejects_more_than_400_nested_records(method: str) -> None:
    document = parse("<div itemscope>" + "<div itemprop='next' itemscope>" * 400 + "</div>" * 401)
    with pytest.raises(RecursionError, match=r"microdata\(\).*400 nested items"):
        getattr(document, method)()


@pytest.mark.parametrize("method", ["rdfa", "structured_data"])
def test_rdfa_rejects_more_than_400_nested_records(method: str) -> None:
    document = parse("<div typeof='Thing'>" + "<div property='next' typeof='Thing'>" * 400 + "</div>" * 401)
    with pytest.raises(RecursionError, match=r"rdfa\(\).*400 nested items"):
        getattr(document, method)()


def test_deep_operations_fit_a_small_thread_stack() -> None:
    roots = [
        parse_xml("<x>" * _DEEP + "bottom" + "</x>" * _DEEP).root,
        _nested("x", _DEEP, "bottom"),
    ]

    def run() -> list[tuple[str, str, str]]:
        captured: list[tuple[str, str, str]] = []
        for root in roots:
            assert isinstance(root, Element)
            clone = copy.deepcopy(root)
            clone.normalize()
            with pytest.raises(RecursionError):
                root.to_markdown()
            assert root.matches(":has(x)")
            captured.append((root.text, clone.text, root.serialize(Html(layout=Indent(1)))))
        return captured

    previous = threading.stack_size(256 * 1024)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            captured = pool.submit(run).result()
    finally:
        threading.stack_size(previous)
    assert [(text, clone_text) for text, clone_text, _ in captured] == [("bottom", "bottom")] * 2
    assert all("bottom" in markup for _, _, markup in captured)
