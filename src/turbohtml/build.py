"""
A terse builder for constructing HTML trees, the way ``lxml.builder.E`` did.

This is a thin ergonomic layer over the existing element API: every ``E.<tag>(...)`` call builds a real
:class:`turbohtml.Element` and appends its children through :meth:`turbohtml.Element.append`, so the result is an
ordinary turbohtml tree that serializes with :meth:`turbohtml.Element.serialize`. Sorting a call's arguments into the
attribute mapping and the children runs in C too; this module only names the tags.

A call takes its arguments in order: a leading mapping is the element's attributes, a string becomes a
:class:`turbohtml.Text` node, and any other node is appended as-is::

    from turbohtml.build import E

    doc = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(doc.serialize())  # <div class="card"><h1>Title</h1><p>body</p></div>

Text serializes escaped, except inside ``script``, ``style``, ``xmp``, ``iframe``, ``noembed``, ``noframes`` and
``plaintext``, whose content the HTML serialization writes as is. Keep untrusted text out of those elements: a
``</script`` in it ends the element and the rest reads as markup.
"""

from __future__ import annotations

__lazy_modules__ = [
    "collections.abc",
]

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Final, TypeAlias

from ._html import TagMaker, build_document, build_element

if TYPE_CHECKING:
    from ._html import Document, Element, Node, TagFactory

#: Attributes accepted by :class:`turbohtml.Element`: a name maps to a string, a token list, or ``None`` for a bare
#: boolean attribute such as ``disabled``.
Attributes: TypeAlias = "Mapping[str, str | list[str] | None]"

#: A single builder argument: a leading attribute mapping, an existing node, or a string that becomes a
#: :class:`turbohtml.Text` node.
Content: TypeAlias = "Attributes | Node | str"


class ElementMaker(TagMaker):
    """
    A factory whose attribute access names the tag to build: ``E.div`` returns a callable that builds a ``<div>``.

    Use the singleton ``E``, or instantiate a private maker. ``E.section(...)`` and ``E("section", ...)`` are
    equivalent; the call form takes a tag that is not a Python identifier.
    """

    if TYPE_CHECKING:
        # TagMaker answers a missing attribute in C: a Python __getattr__ frame costs more than the element it builds.
        def __getattr__(self, tag: str) -> TagFactory:
            """
            Return the factory for ``tag``; a dunder name raises AttributeError, so copy and pickle are not hijacked.

            :param tag: the tag name.
            :returns: the factory that builds ``tag`` elements.
            """

    def __call__(self, tag: str, /, *args: Content) -> Element:
        """
        Build ``tag`` from a leading attribute mapping and the child nodes and strings that follow.

        :param tag: the element's tag name.
        :param args: an optional leading attribute mapping, then the child nodes and strings.
        :raises ValueError: if the tag or an attribute name carries a character HTML forbids there, or if a
            void element such as ``br`` or ``img`` is given children.
        :raises TypeError: if an attribute mapping is not the first argument.
        :returns: the element built from ``tag``, its attributes, and its children.
        """
        return build_element(tag, Mapping, *args)


#: The shared builder: ``E.div(...)`` or ``E("div", ...)`` builds a ``<div>`` element.
E: Final[ElementMaker] = ElementMaker()  # simplint: ignore[single-letter-name]  # public name exported by turbohtml


def document(
    *,
    title: str | None = None,
    lang: str | None = None,
    charset: str | None = "utf-8",
    head: Iterable[Node | str] = (),
    body: Iterable[Node | str] = (),
) -> Document:
    """
    Build a complete HTML5 document around the given head and body content.

    Where ``E`` builds a bare fragment, this scaffolds the whole page: a ``<!DOCTYPE html>`` followed by ``<html>``
    holding a ``<head>`` and a ``<body>``. The head leads with a ``<meta charset>`` and a ``<title>`` (when supplied),
    then the ``head`` content; the body holds the ``body`` content. The result is a :class:`turbohtml.Document`, so
    ``document(...).serialize()`` emits the doctype and the full shell::

        from turbohtml.build import E, document

        page = document(title="Report", lang="en", body=[E.h1("Sales"), E.p("Up 4%")])
        page.serialize()
        # <!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Report</title></head>
        # <body><h1>Sales</h1><p>Up 4%</p></body></html>

    :param title: the ``<title>`` text; ``None`` omits the element.
    :param lang: the ``lang`` attribute for ``<html>``; ``None`` omits it.
    :param charset: the ``<meta charset>`` value; ``None`` omits the meta element (e.g. when the charset is set by an
        HTTP header instead).
    :param head: extra ``<head>`` content, appended after the meta and title; a string becomes a
        :class:`turbohtml.Text` node.
    :param body: the ``<body>`` content; a string becomes a :class:`turbohtml.Text` node.
    :returns: the assembled :class:`turbohtml.Document`.
    :raises TypeError: if ``title``, ``lang``, or ``charset`` is neither a str nor ``None``, or a head or body item is
        not a node or string.
    """
    return build_document(head, body, title, lang, charset)


__all__ = [
    "Attributes",
    "Content",
    "E",
    "ElementMaker",
    "document",
]
