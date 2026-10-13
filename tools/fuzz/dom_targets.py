"""Ordinary DOM consumers keep qualified export ownership executable."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from html import escape
from typing import TYPE_CHECKING, Final, NamedTuple, cast

import turbohtml as dom
from turbohtml import build, etree, mutations, query, rewrite, saxparse, traverse, treebuild

from .atheris_registry import Target

if TYPE_CHECKING:
    from collections.abc import Callable


def dom_targets() -> tuple[Target, ...]:
    """Keep each corpus attached to the public operations it reaches."""
    return tuple(
        Target(name, partial(_check, domain=name), exports, (UnicodeDecodeError,)) for name, exports in _OWNERS.items()
    )


def _check(data: bytes, *, domain: str) -> None:
    dom_observation(data, domain).verify()


def dom_observation(data: bytes, domain: str) -> DomObservation:
    """Keep constructed trees small for large corpus entries."""
    return _OBSERVERS[domain](
        data.decode("utf-8")[:64].replace("\x00", "\ufffd").replace("\r\n", "\n").replace("\r", "\n")
    )


def _construction(text: str) -> DomObservation:
    attrs: Final[build.Attributes] = {"id": "item"}
    content: Final[build.Content] = text
    maker: Final = build.ElementMaker()
    first: Final = maker("p", attrs, content)
    second: Final = dom.ElementMaker()("p", attrs, content)
    fragment: Final = dom.DocumentFragment()
    fragment.append(dom.Text(text))
    root: Final = build.document(body=(build.E.p(text), dom.E.p(text), first, second))
    doctype: Final = next(child for child in root.children if isinstance(child, dom.Doctype))
    foreign: Final = cast("dom.Element", dom.parse_fragment("<svg><rect></rect></svg>").find("rect"))
    return DomObservation(
        repr((
            root.find_all("p")[0].text,
            second.text,
            fragment.text,
            dom.Comment(text).data,
            dom.CData(text).data,
            dom.ProcessingInstruction("target", text).data,
            doctype.name,
            foreign.namespace,
            dom.Namespace.MATHML.value,
            dom.Namespace.HTML.value,
        )),
        repr((text, text, text, text, text, text, "html", dom.Namespace.SVG, "math", "html")),
    )


def _elementtree(text: str) -> DomObservation:
    root: Final = etree.Element("div", {"class": "one two"})
    child: Final = etree.SubElement(root, "span")
    child.text = "prefix" + text
    child.tail = "tail"
    matches: Final = etree.XPath(".//span")(root) == [child]
    identity: Final = etree.ElementView(child.node) is child

    @etree.document_context
    def detach() -> bool:
        root.remove(child)
        return child.getroottree().getroot() is root

    context: Final = detach()
    root.append(child)
    etree.strip_tags(root, "span")
    etree.SubElement(root, "script").text = "discard"
    etree.strip_elements(root, "script")
    markup: Final = f"<p>prefix{escape(text)}</p>"
    document: Final = etree.document_fromstring(markup)
    snippet: Final = etree.fromstring(markup)
    fragment: Final = etree.fragment_fromstring(markup + "trailing")
    bridge: Final = etree.Element("p", {"data": text.encode().hex()})
    copied: str | None = None
    html_attribute: str | None = None
    try:
        external = etree.to_lxml(bridge)
    except ModuleNotFoundError:
        # lxml is optional in the native fuzz runtime.
        pass
    else:
        copied = etree.tostring(etree.from_lxml(external), encoding="unicode")
        html_attribute = etree.to_lxml_html(bridge).get("data")
    return DomObservation(
        repr((
            matches,
            identity,
            context,
            root.text,
            document.text_content(),
            snippet.text,
            fragment.tail,
            fragment.getparent(),
            copied,
            html_attribute,
        )),
        repr((
            True,
            True,
            True,
            "prefix" + text + "tail",
            "prefix" + text,
            "prefix" + text,
            "trailing",
            None,
            f'<p data="{text.encode().hex()}"/>' if copied is not None else None,
            text.encode().hex() if copied is not None else None,
        )),
    )


def _traversal(text: str) -> DomObservation:
    root: Final = build.E.div(build.E.p(text), build.E.b("tail"))
    walked: Final = traverse.TreeWalker(root, traverse.NodeFilter.SHOW_ELEMENT)
    iterator: Final = dom.NodeIterator(root, dom.NodeFilter.SHOW_ELEMENT)
    root_walker: Final = dom.TreeWalker(root, dom.NodeFilter.SHOW_TEXT)
    module_iterator: Final = traverse.NodeIterator(root, traverse.NodeFilter.SHOW_TEXT)
    names: Final = []
    while (node := walked.next_node()) is not None:
        names.append(cast("dom.Element", node).tag)
    iter_names: Final = []
    while (node := iterator.next_node()) is not None:
        iter_names.append(cast("dom.Element", node).tag)
    texts: Final = []
    while (node := root_walker.next_node()) is not None:
        texts.append(cast("dom.Text", node).data)
    module_texts: Final = []
    while (node := module_iterator.next_node()) is not None:
        module_texts.append(cast("dom.Text", node).data)
    return DomObservation(
        repr((names, iter_names, texts, module_texts, [item.tag for item in root.find_all(axis=dom.Axis.CHILDREN)])),
        repr((
            ["p", "b"],
            ["div", "p", "b"],
            [text, "tail"],
            [text, "tail"],
            ["p", "b"],
        )),
    )


def _query(text: str) -> DomObservation:
    root: Final = build.E.div({"id": "root"}, build.E.p({"id": "item", "class": "picked"}, text))
    child: Final = cast("dom.Element", root.find("p"))
    matching: Final = query.Matching(namespaces={"h": "http://www.w3.org/1999/xhtml"}, flags=0)
    matcher: Final = query.Matcher("p", matching)
    compiled: Final = query.compile("p")
    smart: Final = cast("list[dom.XPathString]", dom.XPath("//@id", smart_strings=True)(root))
    direct: Final = dom.XPathString(text, child, is_attribute=False, attrname=None)
    observed: Final = (
        [item.tag for item in query.select("p", root)],
        query.select_one("p", root) == child,
        [item.tag for item in query.iselect("p", root)],
        [item.tag for item in query.filter("p", root)],
        query.match("p", child),
        query.closest("div", child) == root,
        matcher.match(child),
        compiled.select_one(root) == child,
        query.Query(root).find("p").text(),
        query.css("p").select(root)[0].text,
        query.escape_identifier("a b"),
        query.DEBUG,
        matching.flags,
        smart[0].attrname,
        smart[0].getparent().tag,
        str(direct),
        direct.getparent().tag,
    )
    invalid = "accepted"
    try:
        query.compile("[")
    except query.SelectorSyntaxError:
        invalid = "rejected"
    return DomObservation(
        repr((observed, invalid)),
        repr((
            (["p"], True, ["p"], ["p"], True, True, True, True, text, text, "a\\ b", 1, 0, "id", "div", text, "p"),
            "rejected",
        )),
    )


def _mutation(text: str) -> DomObservation:
    root: Final = build.E.div()
    first: Final = dom.MutationObserver()
    second: Final = mutations.MutationObserver()
    for observer in (first, second):
        observer.observe(root, child_list=True)
    root.append(dom.Text(text))
    records: Final = first.take_records()
    other: Final = second.take_records()
    observed: Final = (
        [(record.type, record.added_nodes[0].text) for record in records],
        [(record.type, record.added_nodes[0].text) for record in other],
        all(isinstance(record, (dom.MutationRecord, mutations.MutationRecord)) for record in records + other),
    )
    first.disconnect()
    second.disconnect()
    return DomObservation(repr(observed), repr(([("childList", text)], [("childList", text)], True)))


def _range(text: str) -> DomObservation:
    root: Final = build.E.div(build.E.p(text), build.E.b("tail"))
    boundary: Final = dom.Range(root)
    boundary.set_end(root, 1)
    snapshot: Final = dom.StaticRange(root, 0, root, 1)
    cloned: Final = boundary.clone_contents()
    return DomObservation(
        repr((
            cloned.html,
            boundary.start_offset,
            boundary.end_offset,
            snapshot.start_offset,
            snapshot.end_offset,
            snapshot.collapsed,
            cloned.text,
        )),
        repr((f"<p>{escape(text, quote=False)}</p>", 0, 1, 0, 1, False, text)),
    )


def _shadow(text: str) -> DomObservation:
    host: Final = build.E.div(dom.Text(text))
    shadow: Final = host.attach_shadow("open")
    shadow.append(build.E.slot())
    return DomObservation(
        repr((
            isinstance(shadow, dom.ShadowRoot),
            shadow.mode,
            shadow.host.tag,
            shadow.html,
            [node.text for node in cast("dom.Element", shadow.children[0]).assigned_nodes()],
        )),
        repr((True, "open", "div", "<slot></slot>", [text])),
    )


def _locations(text: str) -> DomObservation:
    source: Final = f'<p id="item">{escape(text, quote=False)}</p>'
    root: Final = dom.parse_fragment(source, source_locations=True)
    child: Final = cast("dom.Element", root.find("p"))
    location: Final = cast("dom.SourceLocation", child.source_location)
    span: Final = location.start_tag
    end: Final = cast("dom.SourceSpan", location.end_tag)
    return DomObservation(
        repr((
            isinstance(location, dom.SourceLocation),
            isinstance(span, dom.SourceSpan),
            source[span.start_offset : span.end_offset],
            source[end.start_offset : end.end_offset],
            source[location.attrs["id"].start_offset : location.attrs["id"].end_offset],
        )),
        repr((True, True, '<p id="item">', "</p>", 'id="item"')),
    )


def _rewrite(text: str) -> DomObservation:
    element_handler: Final[rewrite.ElementHandler] = _rewrite_element
    text_handler: Final[rewrite.TextHandler] = partial(_rewrite_text, value=text)
    comment_handler: Final[rewrite.CommentHandler] = _rewrite_comment
    doctype_handler: Final[rewrite.DoctypeHandler] = _rewrite_doctype
    return DomObservation(
        rewrite.rewrite(
            "<!doctype html><!--old--><p>old</p>",
            elements=(("p", element_handler),),
            text=text_handler,
            comments=comment_handler,
            doctype=doctype_handler,
        ),
        '&lt;!--before--&gt;<!doctype html><!--new--><p id="item">' + escape(text, quote=False) + "</p>",
    )


def _rewrite_element(element: rewrite.Element) -> None:
    element.set_attribute("id", "item")


def _rewrite_text(text: rewrite.Element, *, value: str) -> None:
    text.set_text(value)


def _rewrite_comment(comment: rewrite.Element) -> None:
    comment.set_text("new")


def _rewrite_doctype(doctype: rewrite.Element) -> None:
    doctype.before("<!--before-->")


def _sax(text: str) -> DomObservation:
    source: Final = f"<!doctype html><?target data><!--note--><p>{escape(text, quote=False)}</p>"
    events: Final[tuple[saxparse.SaxEvent, ...]] = tuple(saxparse.iter_events(source))
    collector: Final = _SaxCollector()
    saxparse.sax_parse(source, collector)
    expected: Final[tuple[saxparse.SaxEvent, ...]] = (
        saxparse.Doctype("html", None, None),
        saxparse.ProcessingInstruction("target", "data"),
        saxparse.Comment("note"),
        saxparse.StartElement("html", ()),
        saxparse.StartElement("head", ()),
        saxparse.EndElement("head"),
        saxparse.StartElement("body", ()),
        saxparse.StartElement("p", ()),
        *((saxparse.Characters(text),) if text else ()),
        saxparse.EndElement("p"),
        saxparse.EndElement("body"),
        saxparse.EndElement("html"),
    )
    return DomObservation(repr((events, tuple(collector.events))), repr((expected, expected)))


class _SaxCollector(saxparse.SaxHandler):
    def __init__(self) -> None:
        self.events: Final[list[saxparse.SaxEvent]] = []

    def start_element(self, tag: str, attrs: tuple[tuple[str, str | None], ...]) -> None:
        self.events.append(saxparse.StartElement(tag, attrs))

    def end_element(self, tag: str) -> None:
        self.events.append(saxparse.EndElement(tag))

    def characters(self, data: str) -> None:
        self.events.append(saxparse.Characters(data))

    def comment(self, data: str) -> None:
        self.events.append(saxparse.Comment(data))

    def doctype(self, name: str, public_id: str | None, system_id: str | None) -> None:
        self.events.append(saxparse.Doctype(name, public_id, system_id))

    def processing_instruction(self, target: str, data: str) -> None:
        self.events.append(saxparse.ProcessingInstruction(target, data))


def _tree(text: str) -> DomObservation:
    sink: Final[treebuild.TreeBuilder[_Built]] = _TreeText()
    source: Final = f"<!doctype html><?target data><!--note--><p>{escape(text, quote=False)}</p>"

    def element(tag: str) -> str:
        return repr((tag, "http://www.w3.org/1999/xhtml", ()))

    expected: Final = (
        "document",
        repr(("html", None, None)),
        "pi:target:data",
        "comment:note",
        element("html"),
        element("head"),
        element("body"),
        element("p"),
        *(("text:" + text,) if text else ()),
    )
    return DomObservation(repr(_flatten(treebuild.parse_into(source, sink))), repr(expected))


def _flatten(node: _Built) -> tuple[str, ...]:
    return (node.payload, *(value for child in node.children for value in _flatten(child)))


class _TreeText:
    @staticmethod
    def create_document() -> _Built:
        return _Built("document")

    @staticmethod
    def create_doctype(name: str, public_id: str | None, system_id: str | None) -> _Built:
        return _Built(repr((name, public_id, system_id)))

    @staticmethod
    def create_element(name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> _Built:
        return _Built(repr((name, namespace, attrs)))

    @staticmethod
    def create_text(data: str) -> _Built:
        return _Built("text:" + data)

    @staticmethod
    def create_comment(data: str) -> _Built:
        return _Built("comment:" + data)

    @staticmethod
    def create_pi(target: str, data: str) -> _Built:
        return _Built(f"pi:{target}:{data}")

    @staticmethod
    def append(parent: _Built, child: _Built) -> None:
        parent.children.append(child)


@dataclass
class _Built:
    payload: str
    children: list[_Built] = field(default_factory=list)


class DomObservation(NamedTuple):
    """Expose both sides so corpus consumers can report a failed contract."""

    actual: str
    expected: str

    def verify(self) -> None:
        """Keep rejected oracle results visible to the fuzz driver."""
        if self.actual != self.expected:
            raise AssertionError((self.actual, self.expected))


_OBSERVERS: Final[dict[str, Callable[[str], DomObservation]]] = {
    "dom-construction": _construction,
    "dom-elementtree": _elementtree,
    "dom-traversal": _traversal,
    "dom-query": _query,
    "dom-mutation": _mutation,
    "dom-range": _range,
    "dom-shadow": _shadow,
    "dom-locations": _locations,
    "dom-rewrite": _rewrite,
    "dom-sax": _sax,
    "dom-treebuild": _tree,
}
_OWNERS: Final[dict[str, tuple[str, ...]]] = {
    "dom-construction": tuple(
        "turbohtml." + name
        for name in (
            "CData",
            "Comment",
            "Doctype",
            "DocumentFragment",
            "E",
            "ElementMaker",
            "Namespace",
            "ProcessingInstruction",
            "Text",
        )
    )
    + tuple("turbohtml.build." + name for name in ("Attributes", "Content", "E", "ElementMaker", "document")),
    "dom-elementtree": tuple(
        "turbohtml.etree." + name
        for name in (
            "Element",
            "ElementView",
            "SubElement",
            "XPath",
            "document_context",
            "document_fromstring",
            "fragment_fromstring",
            "from_lxml",
            "fromstring",
            "strip_elements",
            "strip_tags",
            "to_lxml",
            "to_lxml_html",
            "tostring",
        )
    ),
    "dom-traversal": tuple("turbohtml." + name for name in ("Axis", "NodeFilter", "NodeIterator", "TreeWalker"))
    + tuple("turbohtml.traverse." + name for name in ("NodeFilter", "NodeIterator", "TreeWalker")),
    "dom-query": (
        "turbohtml.XPath",
        "turbohtml.XPathString",
        *tuple(
            "turbohtml.query." + name
            for name in (
                "DEBUG",
                "Matcher",
                "Matching",
                "Query",
                "SelectorSyntaxError",
                "closest",
                "compile",
                "css",
                "escape_identifier",
                "filter",
                "iselect",
                "match",
                "select",
                "select_one",
            )
        ),
    ),
    "dom-mutation": (
        "turbohtml.MutationObserver",
        "turbohtml.MutationRecord",
        "turbohtml.mutations.MutationObserver",
        "turbohtml.mutations.MutationRecord",
    ),
    "dom-range": ("turbohtml.Range", "turbohtml.StaticRange"),
    "dom-shadow": ("turbohtml.ShadowRoot",),
    "dom-locations": ("turbohtml.SourceLocation", "turbohtml.SourceSpan"),
    "dom-rewrite": tuple(
        "turbohtml.rewrite." + name
        for name in ("CommentHandler", "DoctypeHandler", "Element", "ElementHandler", "TextHandler", "rewrite")
    ),
    "dom-sax": tuple(
        "turbohtml.saxparse." + name
        for name in (
            "Characters",
            "Comment",
            "Doctype",
            "EndElement",
            "ProcessingInstruction",
            "SaxEvent",
            "SaxHandler",
            "StartElement",
            "iter_events",
            "sax_parse",
        )
    ),
    "dom-treebuild": ("turbohtml.treebuild.TreeBuilder", "turbohtml.treebuild.parse_into"),
}
__all__ = ["DomObservation", "dom_observation", "dom_targets"]
