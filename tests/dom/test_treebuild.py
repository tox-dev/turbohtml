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
from typing import Any

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

    def create_document(self) -> Built:  # noqa: PLR6301
        return Built("document", ())

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> Built:  # noqa: PLR6301
        return Built("doctype", (name, public_id, system_id))

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> Built:  # noqa: PLR6301
        return Built("element", (name, namespace, attrs))

    def create_text(self, data: str) -> Built:  # noqa: PLR6301
        return Built("text", (data,))

    def create_comment(self, data: str) -> Built:  # noqa: PLR6301
        return Built("comment", (data,))

    def create_pi(self, data: str) -> Built:  # noqa: PLR6301
        return Built("pi", (data,))

    def append(self, parent: Built, child: Built) -> None:  # noqa: PLR6301
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


def test_text_node_payload() -> None:
    body = build("<p>hello</p>").children[0].children[1]
    text = body.children[0].children[0]
    assert text.kind == "text"
    assert text.payload == ("hello",)


def test_comment_node_payload() -> None:
    body = build("<body><!--note--></body>").children[0].children[1]
    assert body.children[0].kind == "comment"
    assert body.children[0].payload == ("note",)


def test_processing_instruction_is_distinct_from_comment() -> None:
    body = build("<body><?php echo 1?></body>").children[0].children[1]
    node = body.children[0]
    assert node.kind == "pi"
    assert node.payload == ("?php echo 1?",)


def test_svg_and_mathml_carry_their_foreign_namespace() -> None:
    body = build("<svg><circle/></svg><math><mi>x</mi></math>").children[0].children[1]
    svg = body.children[0]
    math = body.children[1]
    assert svg.payload[1] == SVG_NS
    assert svg.children[0].payload[1] == SVG_NS
    assert math.payload[1] == MATHML_NS
    assert math.children[0].payload[1] == MATHML_NS


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


def _flatten(node: Built, out: list[SaxEvent]) -> None:
    if node.kind == "text":
        out.append(Characters(node.payload[0]))
        return
    if node.kind == "comment":
        out.append(Comment(node.payload[0]))
        return
    if node.kind == "pi":
        out.append(ProcessingInstruction(node.payload[0]))
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

    def create_pi(self, data: str) -> Built:
        return self._maybe("create_pi", super().create_pi(data))

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
def test_a_builder_method_that_raises_propagates(method: str) -> None:
    markup = "<!DOCTYPE html><body>text<!--c--><?pi?></body>"
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
