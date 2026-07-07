"""Behavioral tests for the DOM-less SAX API (turbohtml.saxparse).

The event stream is checked two ways: against explicit expectations for the tricky
tree-construction cases (implied tags, foster parenting, void and empty elements,
processing instructions, the doctype identifier variants), and against a
document-order walk of the tree turbohtml.parse builds for the same markup, which
proves the SAX walk stays faithful to the constructed tree across a corpus of
documents.
"""

from __future__ import annotations

import gc
import weakref
from types import GeneratorType
from typing import cast

import pytest

from turbohtml import Comment as DomComment
from turbohtml import Doctype as DomDoctype
from turbohtml import Element as DomElement
from turbohtml import Text as DomText
from turbohtml import _html, parse
from turbohtml.saxparse import (
    Characters,
    Comment,
    Doctype,
    EndElement,
    ProcessingInstruction,
    SaxEvent,
    SaxHandler,
    StartElement,
    iter_events,
    sax_parse,
)


class _Recorder(SaxHandler):
    def __init__(self) -> None:
        self.events: list[SaxEvent] = []

    def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
        self.events.append(StartElement(tag, attrs))

    def end_element(self, tag: str) -> None:
        self.events.append(EndElement(tag))

    def characters(self, data: str) -> None:
        self.events.append(Characters(data))

    def comment(self, data: str) -> None:
        self.events.append(Comment(data))

    def doctype(self, name: str, public_id: str | None, system_id: str | None) -> None:
        self.events.append(Doctype(name, public_id, system_id))

    def processing_instruction(self, data: str) -> None:
        self.events.append(ProcessingInstruction(data))


def test_sax_parse_matches_iter_events() -> None:
    markup = "<!DOCTYPE html><p id=x>hi<b>b</b><!--c--></p>"
    recorder = _Recorder()
    sax_parse(markup, recorder)
    assert recorder.events == list(iter_events(markup))


def test_sax_parse_fires_every_event_and_order() -> None:
    recorder = _Recorder()
    sax_parse("<!DOCTYPE html><p>hi<br><!--c--><?pi?></p>", recorder)
    assert recorder.events == [
        Doctype("html", None, None),
        StartElement("html", ()),
        StartElement("head", ()),
        EndElement("head"),
        StartElement("body", ()),
        StartElement("p", ()),
        Characters("hi"),
        StartElement("br", ()),
        EndElement("br"),
        Comment("c"),
        ProcessingInstruction("?pi?"),
        EndElement("p"),
        EndElement("body"),
        EndElement("html"),
    ]


@pytest.mark.parametrize(
    ("markup", "attrs"),
    [
        pytest.param("<p>", (), id="none"),
        pytest.param("<p id=x>", (("id", "x"),), id="valued"),
        pytest.param("<p disabled>", (("disabled", None),), id="valueless"),
        pytest.param("<p a=1 b=2>", (("a", "1"), ("b", "2")), id="order-preserved"),
    ],
)
def test_start_element_attrs(markup: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
    start = next(event for event in iter_events(markup) if isinstance(event, StartElement) and event.tag == "p")
    assert start.attrs == attrs


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<!DOCTYPE html>", Doctype("html", None, None), id="name-only"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://x">',
            Doctype("html", "-//W3C//DTD HTML 4.01//EN", "http://x"),
            id="public-and-system",
        ),
        pytest.param('<!DOCTYPE html PUBLIC "pub">', Doctype("html", "pub", None), id="public-only"),
        pytest.param('<!DOCTYPE html SYSTEM "sys">', Doctype("html", None, "sys"), id="system-only"),
    ],
)
def test_doctype_identifiers(markup: str, expected: Doctype) -> None:
    doctype = next(event for event in iter_events(markup) if isinstance(event, Doctype))
    assert doctype == expected


def test_void_and_empty_elements_emit_balanced_start_end() -> None:
    events = [event for event in iter_events("<div></div><br>") if isinstance(event, (StartElement, EndElement))]
    assert events == [
        StartElement("html", ()),
        StartElement("head", ()),
        EndElement("head"),
        StartElement("body", ()),
        StartElement("div", ()),
        EndElement("div"),
        StartElement("br", ()),
        EndElement("br"),
        EndElement("body"),
        EndElement("html"),
    ]


def test_foster_parenting_moves_text_before_table() -> None:
    tags = [event.tag for event in iter_events("<table>stray<tr><td>c") if isinstance(event, StartElement)]
    chars = [event.data for event in iter_events("<table>stray<tr><td>c") if isinstance(event, Characters)]
    assert tags == ["html", "head", "body", "table", "tbody", "tr", "td"]
    assert chars == ["stray", "c"]


def test_template_content_children_are_walked() -> None:
    tags = [
        event.tag
        for event in iter_events("<template><i>t</i></template>")
        if isinstance(event, (StartElement, EndElement))
    ]
    assert tags.count("template") == 2
    assert "i" in tags


def test_processing_instruction_vs_comment() -> None:
    events = [
        event
        for event in iter_events("<p><?php echo 1?><!--c--></p>")
        if isinstance(event, (Comment, ProcessingInstruction))
    ]
    assert events == [ProcessingInstruction("?php echo 1?"), Comment("c")]


_CORPUS = [
    pytest.param("<!DOCTYPE html><title>t</title><p>hi</p>", id="basic"),
    pytest.param("<a><b><i>x</i></b></a>y", id="nested-formatting"),
    pytest.param("<table><caption>c<tbody><tr><td>d</td></tr>", id="table"),
    pytest.param("<ul><li>a<li>b</ul>", id="implied-li-close"),
    pytest.param("<select><option>1<option>2</select>", id="select"),
    pytest.param("<div><p>unclosed<span>text", id="unclosed"),
    pytest.param("text before <html> and <body> tags", id="stray-text"),
    pytest.param("<svg><circle/></svg><math><mi>x</mi></math>", id="foreign"),
    pytest.param("<!-- lead --><!DOCTYPE html><p>x</p><!-- trail -->", id="document-comments"),
]


def _dom_events(markup: str) -> list[SaxEvent]:
    events: list[SaxEvent] = []
    stack = [iter(parse(markup).children)]
    tags: list[str] = []
    while stack:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            if tags:
                events.append(EndElement(tags.pop()))
            continue
        if isinstance(node, DomElement):
            attrs = cast("tuple[tuple[str, str | None], ...]", tuple(node.attrs.items()))
            events.append(StartElement(node.tag, attrs))
            tags.append(node.tag)
            stack.append(iter(node.children))
        elif isinstance(node, DomText):
            events.append(Characters(node.data))
        elif isinstance(node, DomComment):
            events.append(Comment(node.data))
        else:  # the parser yields only these four node types into a document's descendant .children
            doctype = cast("DomDoctype", node)
            events.append(Doctype(doctype.name, doctype.public_id, doctype.system_id))
    return events


@pytest.mark.parametrize("markup", _CORPUS)
def test_events_match_dom_document_order(markup: str) -> None:
    assert list(iter_events(markup)) == _dom_events(markup)


def test_bare_handler_is_a_noop_for_every_event() -> None:
    handler = SaxHandler()
    handler.start_element("p", (("a", "b"),))
    handler.end_element("p")
    handler.characters("x")
    handler.comment("c")
    handler.doctype("html", None, None)
    handler.processing_instruction("pi")
    sax_parse("<!DOCTYPE html><p>x<!--c--><?pi?></p>", handler)


def test_iter_events_is_lazy() -> None:
    events = iter_events("<p>x</p>")
    assert isinstance(events, GeneratorType)
    assert isinstance(next(events), StartElement)


def test_gc_reclaims_a_reference_cycle_through_the_source() -> None:
    class _Cyclic(str):  # noqa: FURB189  # a real str subclass: _sax_events requires str, and the slots let it cycle
        __slots__ = ("loop", "sentinel")
        loop: object
        sentinel: object

    class _Sentinel:
        back: object

    source = _Cyclic("<p>x</p>")
    events = _html._sax_events(source)
    sentinel = _Sentinel()
    source.loop = events  # events -> source -> events, so only the cyclic GC (via tp_traverse/tp_clear) can reclaim it
    source.sentinel = sentinel
    sentinel.back = source
    marker = weakref.ref(sentinel)
    del source, events, sentinel
    gc.collect()
    assert marker() is None


def test_non_str_argument_raises_type_error() -> None:
    with pytest.raises(TypeError, match="must be str"):
        list(iter_events(cast("str", b"<p>")))
