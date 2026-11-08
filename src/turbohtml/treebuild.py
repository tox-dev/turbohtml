"""
turbohtml.treebuild: retarget the parser at your own tree.

Where :func:`turbohtml.parse` builds a navigable document and :func:`turbohtml.saxparse.sax_parse` streams
events, :func:`parse_into` runs the same WHATWG tree builder and hands the constructed tree straight to a builder
object you supply -- one method per node kind, plus an ``append`` that links a child under its parent. The parser
never materializes a turbohtml :class:`~turbohtml.Node` and you never walk the result a second time: a single pass
over the spec-correct tree (implied ``<html>``/``<head>``/``<body>``, foster parenting, the adoption agency) drives
your builder, so an index, a diff tree, or an extraction struct is populated directly from the parse.

This is the model Rust's html5ever exposes as its ``TreeSink`` trait and Node's parse5 as its ``TreeAdapter``: the
conformant parser is retargetable, the tree representation is yours. A builder returns an opaque *handle* for each
node it creates -- any object at all, since turbohtml only threads it back into a later ``append`` -- and the handle
your :meth:`TreeBuilder.create_document` returns is what :func:`parse_into` gives back.

The methods mirror parse5's adapter: every element carries its namespace URI (``http://www.w3.org/1999/xhtml`` for
HTML, and the SVG and MathML URIs for foreign content) and its attributes as ``(name, value)`` pairs, a valueless
attribute pairing with None. A ``<template>``'s content is appended straight under the template handle. Processing
instructions arrive through :meth:`TreeBuilder.create_pi` with separate target and data strings.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, cast

from ._html import _parse_into

H = TypeVar("H")
"""The builder's node handle: whatever :class:`TreeBuilder` returns for a node and takes back in ``append``."""


class TreeBuilder(Protocol[H]):
    """
    The tree-construction sink :func:`parse_into` drives, parse5's ``TreeAdapter`` in turbohtml shape.

    Implement each method to create the matching node in your own representation and return a handle for it; the
    handle is opaque to turbohtml, threaded back only into :meth:`append`. The protocol is structural, so any object
    with these methods works -- no base class to inherit.
    """

    def create_document(self) -> H:
        """
        Create the document root and return its handle; :func:`parse_into` returns this handle.

        :returns: the handle of the new document root.
        """

    def create_doctype(self, name: str, public_id: str | None, system_id: str | None) -> H:
        """
        Create a doctype node; ``public_id``/``system_id`` are None when the source omits them.

        :param name: the document type name, e.g. ``html``.
        :param public_id: the public identifier, or None when the source omits it.
        :param system_id: the system identifier, or None when the source omits it.

        :returns: the handle of the new doctype node.
        """

    def create_element(self, name: str, namespace: str, attrs: tuple[tuple[str, str | None], ...]) -> H:
        """
        Create an element ``name`` in ``namespace`` (a URI) with ``attrs`` (each a ``(name, value)`` pair).

        :param name: the element's tag name.
        :param namespace: the element's namespace URI.
        :param attrs: the attributes, each a ``(name, value)`` pair with ``value`` None for a valueless attribute.

        :returns: the handle of the new element.
        """

    def create_text(self, data: str) -> H:
        """
        Create a text node holding ``data``.

        :param data: the text.

        :returns: the handle of the new text node.
        """

    def create_comment(self, data: str) -> H:
        """
        Create a comment node holding ``data``.

        :param data: the comment body.

        :returns: the handle of the new comment node.
        """

    def create_pi(self, target: str, data: str) -> H:
        """
        Create a node for an HTML instruction whose target is not reserved for XML.

        :param target: the ASCII target, preserving source case.
        :param data: the text after leading whitespace and before the closing delimiter.

        :returns: the handle of the new instruction node.
        """

    def append(self, parent: H, child: H) -> None:
        """
        Append the ``child`` handle under the ``parent`` handle, in document order.

        :param parent: the handle to append under.
        :param child: the handle to append.
        """


def parse_into(html: str, builder: TreeBuilder[H]) -> H:
    """
    Parse ``html`` and drive ``builder`` to construct the tree, returning its document handle.

    The document is parsed with the full WHATWG tree-construction algorithm, then its nodes are handed to
    ``builder`` in document order: one ``create_*`` call per node followed by an :meth:`~TreeBuilder.append` linking
    it under its parent. No navigable :class:`~turbohtml.Node` is built and the tree is walked only once.

    :param html: the markup to parse.
    :param builder: the :class:`TreeBuilder` (any object with its methods) that constructs your representation.
    :returns: the handle :meth:`~TreeBuilder.create_document` returned, now populated with the parsed tree.
    :raises TypeError: if ``html`` is not a str.
    :raises AttributeError: if ``builder`` is missing one of the :class:`TreeBuilder` methods.
    """
    return cast("H", _parse_into(html, builder))


__all__ = [
    "TreeBuilder",
    "parse_into",
]
