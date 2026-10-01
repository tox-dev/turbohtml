"""Behavioral tests for the pluggable tree builder (turbohtml.treebuild).

The builder is driven two ways: against explicit expectations for the tricky
tree-construction cases (implied tags, foreign namespaces, template content, the
processing-instruction and doctype-identifier variants), and against turbohtml's own
DOM-less SAX walk of the same markup, which shares the tree builder and the same PI
distinction, so flattening the builder tree back to an event stream must reproduce it
across a corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from turbohtml import Comment as DomComment
from turbohtml import Doctype as DomDoctype
from turbohtml import Element, IncrementalParser, Text, parse, parse_fragment

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from types import ModuleType

    from turbohtml import Node

import pytest

from turbohtml.saxparse import (
    Characters,
    Comment,
    Doctype,
    EndElement,
    ProcessingInstruction,
    SaxEvent,
    StartElement,
    iter_events,
)
from turbohtml.treebuild import parse_into

HTML_NS = "http://www.w3.org/1999/xhtml"
SVG_NS = "http://www.w3.org/2000/svg"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"


@dataclass
class Built:
    """A captured builder node: the method that made it and the payload it carried."""

    kind: str
    payload: tuple[Any, ...]
    children: list[Built] = field(default_factory=list)


class Recorder:
    """A :class:`turbohtml.treebuild.TreeBuilder` that records every call into a plain tree.

    Every method is an instance method because :func:`~turbohtml.treebuild.parse_into` resolves the builder's methods
    off the instance, so ``self`` is never referenced -- hence the per-method PLR6301 waivers.
    """

    def create_document(self) -> Built:  # ruff:ignore[no-self-use]
        return Built("document", ())

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> Built:  # ruff:ignore[no-self-use]
        return Built("doctype", (name, public_id, system_id))

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> Built:  # ruff:ignore[no-self-use]
        return Built("element", (name, namespace, attrs))

    def create_text(self, data: str) -> Built:  # ruff:ignore[no-self-use]
        return Built("text", (data,))

    def create_comment(self, data: str) -> Built:  # ruff:ignore[no-self-use]
        return Built("comment", (data,))

    def create_pi(self, target: str, data: str) -> Built:  # ruff:ignore[no-self-use]
        return Built("pi", (target, data))

    def append(self, parent: Built, child: Built) -> None:  # ruff:ignore[no-self-use]
        parent.children.append(child)


def build(markup: str) -> Built:
    return parse_into(markup, Recorder())


def test_returns_the_document_root_the_builder_made() -> None:
    root = build("<p>hi</p>")
    assert root.kind == "document"


def test_implied_html_head_body_are_emitted() -> None:
    root = build("<p>hi</p>")
    html = root.children[0]
    assert (html.kind, html.payload[0]) == ("element", "html")
    assert [child.payload[0] for child in html.children] == ["head", "body"]


def test_element_carries_html_namespace_and_attribute_pairs() -> None:
    body = build("<p class=x disabled>hi</p>").children[0].children[1]
    paragraph = body.children[0]
    assert paragraph.kind == "element"
    assert paragraph.payload == ("p", HTML_NS, (("class", "x"), ("disabled", None)))


@pytest.mark.parametrize(
    "data",
    [
        pytest.param("hello", id="ascii"),
        pytest.param("café", id="latin1"),
        pytest.param("水", id="bmp"),
        pytest.param("😀", id="astral"),
        pytest.param("before\nnext", id="newline"),
        pytest.param("a&b", id="entity"),
        pytest.param("\ud800", id="high-surrogate"),
        pytest.param("\udfff", id="low-surrogate"),
        pytest.param("\ud83d\ude00", id="surrogate-pair"),
    ],
)
def test_text_node_payload(data: str) -> None:
    body = build(f"<p>{data.replace('&', '&amp;')}</p>").children[0].children[1]
    text = body.children[0].children[0]
    assert text.kind == "text"
    assert text.payload == (data,)


@pytest.mark.parametrize("data", ["note", "", "café", "水", "😀"], ids=["ascii", "empty", "latin1", "bmp", "astral"])
def test_comment_node_payload(data: str) -> None:
    body = build(f"<body><!--{data}--></body>").children[0].children[1]
    assert body.children[0].kind == "comment"
    assert body.children[0].payload == (data,)


def test_processing_instruction_is_distinct_from_comment() -> None:
    body = build("<body><?php echo 1?></body>").children[0].children[1]
    node = body.children[0]
    assert node.kind == "pi"
    assert node.payload == ("php", "echo 1")


def test_svg_and_mathml_carry_their_foreign_namespace() -> None:
    body = build("<svg><circle/></svg><math><mi>x</mi></math>").children[0].children[1]
    svg = body.children[0]
    math = body.children[1]
    assert svg.payload[1] == SVG_NS
    assert svg.children[0].payload[1] == SVG_NS
    assert math.payload[1] == MATHML_NS
    assert math.children[0].payload[1] == MATHML_NS


def test_namespace_values_survive_builder_reentry() -> None:
    recorder: Final = _NamespaceRecorder()
    root: Final = parse_into("<svg><foreignObject><p>outer</p></foreignObject><circle/></svg>", recorder)
    svg: Final = root.children[0].children[1].children[0]
    assert recorder.nested is not None
    math: Final = recorder.nested.children[0].children[1].children[0]
    assert (
        svg.payload[1],
        svg.children[0].payload[1],
        svg.children[0].children[0].payload[1],
        svg.children[1].payload[1],
        math.payload[1],
        math.children[0].payload[1],
    ) == (SVG_NS, SVG_NS, HTML_NS, SVG_NS, MATHML_NS, MATHML_NS)


class _NamespaceRecorder(Recorder):
    def __init__(self) -> None:
        self.nested: Built | None = None

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> Built:
        if name == "svg":
            self.nested = parse_into("<math><mi>inner</mi></math>", self)
        return super().create_element(name, namespace, attrs)


def test_template_content_is_appended_under_the_template() -> None:
    body = build("<body><template><b>t</b></template></body>").children[0].children[1]
    template = body.children[0]
    assert template.payload[0] == "template"
    assert [(child.kind, child.payload[0]) for child in template.children] == [("element", "b")]


def test_empty_template_has_no_children() -> None:
    body = build("<body><template></template></body>").children[0].children[1]
    assert body.children[0].children == []


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        pytest.param("<!DOCTYPE html><p>x", ("html", None, None), id="name-only"),
        pytest.param(
            '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://x.dtd"><p>x',
            ("html", "-//W3C//DTD HTML 4.01//EN", "http://x.dtd"),
            id="public-and-system",
        ),
        pytest.param('<!DOCTYPE html PUBLIC "-//pub//EN"><p>x', ("html", "-//pub//EN", None), id="public-only"),
        pytest.param('<!DOCTYPE html SYSTEM "sys.dtd"><p>x', ("html", None, "sys.dtd"), id="system-only"),
    ],
)
def test_doctype_identifier_variants(markup: str, expected: tuple[str, str | None, str | None]) -> None:
    doctype = build(markup).children[0]
    assert doctype.kind == "doctype"
    assert doctype.payload == expected


def test_foster_parenting_is_reflected() -> None:
    body = build("<table><b>misnested</b><tr><td>c</td></tr></table>").children[0].children[1]
    kinds = [(child.kind, child.payload[0]) for child in body.children]
    assert ("element", "b") in kinds
    assert ("element", "table") in kinds


def test_deep_nesting_does_not_exhaust_the_stack() -> None:
    root = build("<div>" * 600)  # the parser caps tree depth, but far past any C-stack limit the walk could hit
    node = root
    descended = 0
    while node.children:
        node = node.children[-1]
        descended += 1
    assert descended > 200


def _parse_count(markup: str) -> int:
    return len(parse(markup).select("b"))


def _fragment_count(markup: str) -> int:
    return len(parse_fragment(markup, context="table").select("b"))


def _stream_count(markup: str) -> int:
    parser = IncrementalParser()
    parser.feed(markup)
    return len(parser.close().select("b"))


@pytest.mark.parametrize(
    "count",
    [pytest.param(600, id="just-past-cap"), pytest.param(1200, id="well-past-cap")],
)
@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(_parse_count, id="parse"),
        pytest.param(_fragment_count, id="fragment"),
        pytest.param(_stream_count, id="stream"),
    ],
)
def test_formatting_run_past_depth_cap_does_not_amplify(entry: Callable[[str], int], count: int) -> None:
    # Past the 512 open-element cap a formatting start tag the stack refuses must stay out of the
    # active-formatting list; otherwise reconstruct_afe re-clones every refused entry on each later
    # formatting tag -- O(n^2) retained nodes (~200x at 1200 tags before the fix). A distinct
    # attribute per tag defeats the Noah's Ark de-duplication, so the only bound is the fix.
    markup = "".join(f"<b c{index}>" for index in range(count)) + "x"
    assert entry(markup) == count


def _flatten(node: Built, out: list[SaxEvent]) -> None:
    if node.kind == "text":
        out.append(Characters(node.payload[0]))
        return
    if node.kind == "comment":
        out.append(Comment(node.payload[0]))
        return
    if node.kind == "pi":
        out.append(ProcessingInstruction(*node.payload))
        return
    if node.kind == "doctype":
        out.append(Doctype(*node.payload))
        return
    tag = node.payload[0] if node.kind == "element" else None
    if tag is not None:
        _, _namespace, attrs = node.payload
        out.append(StartElement(tag, attrs))
    for child in node.children:  # the document (never empty) and elements (empty for voids) share this walk
        _flatten(child, out)
    if tag is not None:
        out.append(EndElement(tag))


def _events(markup: str) -> list[SaxEvent]:
    out: list[SaxEvent] = []
    _flatten(build(markup), out)
    return out


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<!DOCTYPE html><title>t</title><p id=a>hi<b>x</b></p>", id="basic"),
        pytest.param("<ul><li>a<li>b</ul>", id="implied-li-close"),
        pytest.param("<table><tr><td>c</td></tr></table>", id="table"),
        pytest.param("<p><b><i>abc</p>def", id="adoption-agency"),
        pytest.param("<div><svg><g><rect/></g></svg><math><mi>y</mi></math></div>", id="foreign"),
        pytest.param("<body><!--c--><?pi?>text</body>", id="comment-pi-text"),
        pytest.param("<template><tr><td>x</td></tr></template>", id="template"),
    ],
)
def test_builder_stream_matches_the_sax_walk(markup: str) -> None:
    assert _events(markup) == list(iter_events(markup))


class _RaisingBuilder(Recorder):
    def __init__(self, at: str) -> None:
        self._at = at

    def _maybe(self, method: str, value: Built) -> Built:
        if method == self._at:
            message = f"boom in {method}"
            raise ValueError(message)
        return value

    def create_document(self) -> Built:
        return self._maybe("create_document", super().create_document())

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> Built:
        return self._maybe("create_doctype", super().create_doctype(name, public_id, system_id))

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> Built:
        return self._maybe("create_element", super().create_element(name, namespace, attrs))

    def create_text(self, data: str) -> Built:
        return self._maybe("create_text", super().create_text(data))

    def create_comment(self, data: str) -> Built:
        return self._maybe("create_comment", super().create_comment(data))

    def create_pi(self, target: str, data: str) -> Built:
        return self._maybe("create_pi", super().create_pi(target, data))

    def append(self, parent: Built, child: Built) -> None:
        if self._at == "append":
            message = "boom in append"
            raise ValueError(message)
        super().append(parent, child)


@pytest.mark.parametrize(
    "method",
    [
        "create_document",
        "create_doctype",
        "create_element",
        "create_text",
        "create_comment",
        "create_pi",
        "append",
    ],
)
@pytest.mark.parametrize(
    "markup",
    [
        pytest.param("<!DOCTYPE html><body>text<!--c--><?pi?></body>", id="html"),
        pytest.param("<!DOCTYPE html><svg><circle/></svg><math><mi>x</mi></math><!--c--><?pi?>", id="foreign"),
    ],
)
def test_a_builder_method_that_raises_propagates(method: str, markup: str) -> None:
    with pytest.raises(ValueError, match=f"boom in {method}"):
        parse_into(markup, _RaisingBuilder(method))


class _NoAppend:
    """Every create_* method, but no ``append``: the bind resolves six methods then fails on the last."""

    create_document = Recorder.create_document
    create_doctype = Recorder.create_doctype
    create_element = Recorder.create_element
    create_text = Recorder.create_text
    create_comment = Recorder.create_comment
    create_pi = Recorder.create_pi


def test_a_builder_missing_a_method_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="append"):
        parse_into("<p>x</p>", _NoAppend())  # ty: ignore[invalid-argument-type]  # the missing method is the point


def test_non_str_source_raises_type_error() -> None:
    with pytest.raises(TypeError):
        parse_into(b"<p>x</p>", Recorder())  # ty: ignore[invalid-argument-type]  # the rejected bytes is the point


_LXML_CORPUS: Final = [
    pytest.param("<!DOCTYPE html><title>t</title><p id=a class=lead>hi<b>x</b>tail</p>", id="basic"),
    pytest.param("<ul><li>a<li>b</ul><ol><li>c</li></ol>", id="implied-close"),
    pytest.param("<table><tr><td>c</td></tr></table>", id="table"),
    pytest.param("<div><!--note-->text<span>y</span>after</div>", id="comment-and-tail"),
    pytest.param("<p><b><i>abc</p>def", id="adoption-agency"),
    pytest.param("<section><input disabled><img src=x alt=y></section>", id="void-and-valueless"),
]


@pytest.mark.parametrize("markup", _LXML_CORPUS)
@pytest.mark.oracle
def test_parse_into_rebuilds_the_tree_in_lxml(markup: str) -> None:
    lxml_etree: Final = pytest.importorskip("lxml.etree")
    built: Final = cast("_LxmlNode", parse_into(markup, _LxmlBuilder(lxml_etree)))
    mine: Final[list[_LxmlEvent]] = []
    _flatten_lxml(built[0], mine, lxml_etree)
    theirs: Final[list[_LxmlEvent]] = []
    _flatten_turbo(parse(markup), theirs)
    assert mine == theirs


@pytest.mark.oracle
@pytest.mark.parametrize("data", ["", "echo 1"], ids=["empty", "body"])
def test_lxml_builder_preserves_processing_instruction(data: str) -> None:
    lxml_etree: Final = pytest.importorskip("lxml.etree")
    built: Final = cast("_LxmlNode", parse_into(f"<body><?php {data}?></body>", _LxmlBuilder(lxml_etree)))
    expected: Final = f"<html><head/><body><!--?php{f' {data}' if data else ''}?--></body></html>".encode()
    assert lxml_etree.tostring(built[0]) == expected


_LxmlEvent = tuple[object, ...]


@pytest.mark.oracle
class _LxmlNode(Protocol):
    """lxml provides no stubs for the builder protocol."""

    text: str | None
    tail: str | None

    @property
    def tag(self) -> object: ...
    @property
    def attrib(self) -> Mapping[str, str]: ...
    def set(self, key: str, value: str) -> None: ...
    def append(self, child: object) -> None: ...
    def __len__(self) -> int: ...
    def __getitem__(self, index: int) -> _LxmlNode: ...
    def __iter__(self) -> Iterator[_LxmlNode]: ...


@pytest.mark.oracle
class _LxmlBuilder:
    def __init__(self, etree: ModuleType) -> None:
        self.etree = etree

    def create_document(self) -> object:
        return self.etree.Element("document")

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> object:  # ruff:ignore[unused-method-argument, no-self-use]
        return ("doctype",)

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> object:  # ruff:ignore[unused-method-argument]
        node: Final = self.etree.Element(name)
        for attr_name, value in attrs:
            node.set(attr_name, value or "")
        return node

    def create_text(self, data: str) -> object:  # ruff:ignore[no-self-use]
        return ("text", data)

    def create_comment(self, data: str) -> object:
        return self.etree.Comment(data)

    def create_pi(self, target: str, data: str) -> object:
        return self.etree.Comment(f"?{target}{f' {data}' if data else ''}?")

    def append(self, parent: object, child: object) -> None:  # ruff:ignore[no-self-use]
        node: Final = cast("_LxmlNode", parent)
        if isinstance(child, tuple):
            if child[0] == "text":
                data: Final = str(child[1])
                if len(node) == 0:
                    node.text = (node.text or "") + data
                else:
                    node[-1].tail = (node[-1].tail or "") + data
            return
        node.append(child)


@pytest.mark.oracle
def _flatten_lxml(node: object, out: list[_LxmlEvent], etree: ModuleType) -> None:
    element: Final = cast("_LxmlNode", node)
    if element.tag is etree.Comment:
        out.append(("comment", element.text or ""))
        return
    out.append(("element", element.tag, tuple(sorted(element.attrib.items()))))
    if element.text:
        out.append(("text", element.text))
    for child in element:
        _flatten_lxml(child, out, etree)
        if child.tail:
            out.append(("text", child.tail))


@pytest.mark.oracle
def _flatten_turbo(node: Node, out: list[_LxmlEvent]) -> None:
    for child in node.children:
        if isinstance(child, DomDoctype):
            continue
        if isinstance(child, DomComment):
            out.append(("comment", child.data))
        elif isinstance(child, Text):
            out.append(("text", child.data))
        else:
            assert isinstance(child, Element)
            attrs: Final = tuple(sorted((name, _attr_value(value)) for name, value in child.attrs.items()))
            out.append(("element", child.tag, attrs))
            _flatten_turbo(child, out)


@pytest.mark.oracle
def _attr_value(value: str | list[str] | None) -> str:
    if isinstance(value, list):
        return " ".join(value)
    return value or ""


def test_builder_can_reenter_while_copying_text() -> None:
    root: Final = parse_into("<p>outer 水 😀</p>", _ReentrantRecorder())
    assert root.children[0].children[1].children[0].children[0].payload == ("outer 水 😀",)


class _ReentrantRecorder(Recorder):
    def create_text(self, data: str) -> Built:
        assert build("<p>inner</p>").children[0].children[1].children[0].children[0].payload == ("inner",)
        return super().create_text(data)
