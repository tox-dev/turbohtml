from __future__ import annotations

from types import GeneratorType
from typing import Final, cast

import pytest

from turbohtml import Comment as DomComment
from turbohtml import Doctype as DomDoctype
from turbohtml import Element as DomElement
from turbohtml import ProcessingInstruction as DomProcessingInstruction
from turbohtml import Text as DomText
from turbohtml import parse
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


def test_sax_parse_matches_iter_events() -> None:
    recorder = _Recorder()
    markup = "<!DOCTYPE html><p id=x>hi<b>b</b><!--c--></p>"
    sax_parse(markup, recorder)
    assert recorder.events == list(iter_events(markup))


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

    def processing_instruction(self, target: str, data: str) -> None:
        self.events.append(ProcessingInstruction(target, data))


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<p><em class=k lang=en>a<p>b<p>c", id="reconstructed"),
        pytest.param("<b class=k lang=en>1<div>2<div>3</b>4", id="adoption-agency"),
    ],
)
def test_clones_of_one_start_tag_share_one_attribute_tuple(markup: str) -> None:
    # a tuple of new strings per clone is value length x clones
    for starts in _attributed_starts(markup):
        assert [start.attrs for start in starts] == [(("class", "k"), ("lang", "en"))] * 3
        assert starts[1].attrs is starts[2].attrs


def _attributed_starts(markup: str) -> list[list[StartElement]]:
    recorder = _Recorder()
    sax_parse(markup, recorder)
    return [
        [event for event in events if isinstance(event, StartElement) and event.attrs]
        for events in (recorder.events, list(iter_events(markup)))
    ]


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param(
            "<p><em title=one>a<p>b<p>c</em><em title=two>d<p>e<p>f",
            [("em", (("title", "one"),))] * 3 + [("em", (("title", "two"),))] * 3,
            id="same-name",
        ),
        pytest.param(
            "<p><em hidden>a<p>b<p>c</em><i hidden=until-found>d<p>e<p>f",
            [("em", (("hidden", None),))] * 3 + [("i", (("hidden", "until-found"),))] * 3,
            id="valueless",
        ),
        pytest.param(
            "<p>a<html lang=en><body class=c>",
            [("html", (("lang", "en"),)), ("body", (("class", "c"),))],
            id="implied-root",
        ),
    ],
)
def test_elements_without_a_start_tag_keep_their_own_attributes(
    markup: str, expected: list[tuple[str, tuple[tuple[str, str | None], ...]]]
) -> None:
    for starts in _attributed_starts(markup):
        assert [(start.tag, start.attrs) for start in starts] == expected


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
        ProcessingInstruction("pi", ""),
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
    assert events == [ProcessingInstruction("php", "echo 1"), Comment("c")]


_CORPUS: Final[list[pytest.ParameterSet]] = [
    pytest.param("<!DOCTYPE html><title>t</title><p>hi</p>", id="basic"),
    pytest.param("<a><b><i>x</i></b></a>y", id="nested-formatting"),
    pytest.param("<table><caption>c<tbody><tr><td>d</td></tr>", id="table"),
    pytest.param("<ul><li>a<li>b</ul>", id="implied-li-close"),
    pytest.param("<select><option>1<option>2</select>", id="select"),
    pytest.param("<div><p>unclosed<span>text", id="unclosed"),
    pytest.param("text before <html> and <body> tags", id="stray-text"),
    pytest.param("<?pi data?>", id="processing-instruction"),
    pytest.param("<svg><circle/></svg><math><mi>x</mi></math>", id="foreign"),
    pytest.param("<!-- lead --><!DOCTYPE html><p>x</p><!-- trail -->", id="document-comments"),
]


@pytest.mark.parametrize("markup", _CORPUS)
def test_events_match_dom_document_order(markup: str) -> None:
    assert list(iter_events(markup)) == _dom_events(markup)


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
            events.append(StartElement(node.tag, cast("tuple[tuple[str, str | None], ...]", tuple(node.attrs.items()))))
            tags.append(node.tag)
            stack.append(iter(node.children))
        elif isinstance(node, DomText):
            events.append(Characters(node.data))
        elif isinstance(node, DomComment):
            events.append(Comment(node.data))
        elif isinstance(node, DomProcessingInstruction):
            events.append(ProcessingInstruction(node.target, node.data))
        else:  # parse() exposes no other Document child type
            doctype = cast("DomDoctype", node)
            events.append(Doctype(doctype.name, doctype.public_id, doctype.system_id))
    return events


def test_bare_handler_is_a_noop_for_every_event() -> None:
    handler = SaxHandler()
    handler.start_element("p", (("a", "b"),))
    handler.end_element("p")
    handler.characters("x")
    handler.comment("c")
    handler.doctype("html", None, None)
    handler.processing_instruction("pi", "data")
    sax_parse("<!DOCTYPE html><p>x<!--c--><?pi?></p>", handler)
    text_only: Final = _TextOnly()
    sax_parse("<!DOCTYPE html><p>x<!--c--><?pi?></p>", text_only)
    assert text_only.texts == ["x"]


class _TextOnly(SaxHandler):
    def __init__(self) -> None:
        self.texts: list[str] = []

    def characters(self, data: str) -> None:
        self.texts.append(data)


def test_iter_events_is_lazy() -> None:
    events = iter_events("<p>x</p>")
    assert isinstance(events, GeneratorType)
    assert isinstance(next(events), StartElement)


def test_non_str_argument_raises_type_error() -> None:
    with pytest.raises(TypeError, match="must be str"):
        list(iter_events(cast("str", b"<p>")))


def test_sax_parse_requires_every_handler_method() -> None:
    # dispatch binds the six methods once up front, so an object missing one fails before any parsing happens
    with pytest.raises(AttributeError, match="start_element"):
        sax_parse("<p>x</p>", cast("SaxHandler", object()))


def test_sax_parse_non_str_raises_type_error() -> None:
    with pytest.raises(TypeError):
        sax_parse(cast("str", b"<p>"), SaxHandler())


def test_handler_exception_in_start_stops_the_parse() -> None:
    class FailingStart(SaxHandler):
        def __init__(self) -> None:
            self.seen: list[str] = []

        def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
            self.seen.append(tag)
            if tag == "i":
                msg = f"stop at {tag} with {len(attrs)} attributes"
                raise ValueError(msg)

    handler = FailingStart()
    with pytest.raises(ValueError, match="stop at i"):
        sax_parse("<p><i>x</i></p>", handler)
    # the walk stops where the handler raised rather than swallowing the error and continuing
    assert handler.seen == ["html", "head", "body", "p", "i"]


def test_handler_exception_in_end_stops_the_parse() -> None:
    class FailingEnd(SaxHandler):
        def __init__(self) -> None:
            self.closed: list[str] = []

        def end_element(self, tag: str) -> None:
            self.closed.append(tag)
            if tag == "p":
                msg = f"stop closing {tag}"
                raise RuntimeError(msg)

    handler = FailingEnd()
    with pytest.raises(RuntimeError, match="stop closing p"):
        sax_parse("<p>x</p><div>y</div>", handler)
    # the implied <head> closes before <p> opens, so it precedes the raise
    assert handler.closed == ["head", "p"]


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        pytest.param(0, Doctype("html", None, None), id="doctype"),
        pytest.param(5, StartElement("p", (("id", "x"),)), id="start"),
        pytest.param(6, Characters("hi"), id="characters"),
        pytest.param(7, Comment("c"), id="comment"),
        pytest.param(8, ProcessingInstruction("pi", "data"), id="instruction"),
        pytest.param(9, EndElement("p"), id="end"),
    ],
)
def test_iter_events_record_types(index: int, expected: SaxEvent) -> None:
    event: Final = list(iter_events("<!DOCTYPE html><p id=x>hi<!--c--><?pi data?></p>"))[index]
    assert (type(event), event) == (type(expected), expected)


def test_iter_events_defers_argument_error() -> None:
    events: Final = iter_events(cast("str", b"<p>"))
    with pytest.raises(TypeError, match="must be str"):
        next(events)
