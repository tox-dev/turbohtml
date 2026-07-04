"""
A terse builder for constructing HTML trees, the way ``lxml.builder.E`` did.

This is a thin ergonomic layer over the existing element API: every ``E.<tag>(...)`` call builds a real
:class:`turbohtml.Element` and appends its children through :meth:`turbohtml.Element.append`, so the result is an
ordinary turbohtml tree that serializes with :meth:`turbohtml.Element.serialize`. The module is the one place a small
amount of Python logic is justified, mirroring how ``lxml.builder.E`` is pure Python over its C tree; it adds no tree
mechanics of its own.

A call takes its arguments in order: a leading mapping is the element's attributes, a string becomes a
:class:`turbohtml.Text` node, and any other node is appended as-is::

    from turbohtml.build import E

    doc = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(doc.serialize())  # <div class="card"><h1>Title</h1><p>body</p></div>
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeAlias, TypeGuard

from ._html import Element, Text

if TYPE_CHECKING:
    from ._html import Node

#: Attributes accepted by :class:`turbohtml.Element`: a name maps to a string, a token list, or ``None`` for a bare
#: boolean attribute such as ``disabled``.
Attributes: TypeAlias = "Mapping[str, str | list[str] | None]"

#: A single builder argument: a leading attribute mapping, an existing node, or a string that becomes a
#: :class:`turbohtml.Text` node.
Content: TypeAlias = "Attributes | Node | str"


def _is_attributes(arg: Content) -> TypeGuard[Attributes]:
    """Treat a mapping argument as the element's attributes and any other argument as a child."""
    return isinstance(arg, Mapping)


def _to_node(arg: Content) -> Node:
    """Turn one child argument into a node: a string becomes text, a node passes through, a mapping is rejected."""
    if isinstance(arg, str):
        return Text(arg)
    if isinstance(arg, Mapping):
        msg = "a mapping argument sets attributes and must come first, before any child"
        raise TypeError(msg)
    return arg


def _build(tag: str, args: tuple[Content, ...]) -> Element:
    """
    Build one element: a leading mapping sets its attributes, and the rest become children in order.

    :raises ValueError: if the tag or an attribute name carries a character HTML forbids there, or if a
        void element such as ``br`` or ``img`` is given children.
    :raises TypeError: if an attribute mapping is not the first argument.
    """
    attrs: Attributes | None = None
    children = args
    if args and _is_attributes(args[0]):
        attrs = args[0]
        children = args[1:]
    return Element(tag, attrs, [_to_node(arg) for arg in children])


class _TagFactory:
    """The callable returned by ``E.<tag>``; it remembers the tag name and builds that element when called."""

    __slots__ = ("_tag",)

    def __init__(self, tag: str) -> None:
        """Remember the tag name this factory builds."""
        self._tag = tag

    def __call__(self, *args: Content) -> Element:
        """
        Build the element from a leading attribute mapping and the child nodes and strings that follow.

        :raises ValueError: if the tag or an attribute name carries a character HTML forbids there, or if a
            void element such as ``br`` or ``img`` is given children.
        :raises TypeError: if an attribute mapping is not the first argument.
        """
        return _build(self._tag, args)


class ElementMaker:
    """
    A factory whose attribute access names the tag to build: ``E.div`` returns a callable that builds a ``<div>``.

    Use the singleton ``E``, or instantiate a private maker. ``E.section(...)`` and ``E("section", ...)`` are
    equivalent; the call form takes a tag that is not a Python identifier.
    """

    def __getattr__(self, tag: str) -> _TagFactory:
        """Return the factory for ``tag``; dunder lookups fall through so copy and pickle protocols are not hijacked."""
        if tag.startswith("__") and tag.endswith("__"):
            raise AttributeError(tag)
        return _TagFactory(tag)

    def __call__(self, tag: str, /, *args: Content) -> Element:
        """
        Build ``tag`` from a leading attribute mapping and the child nodes and strings that follow.

        :raises ValueError: if the tag or an attribute name carries a character HTML forbids there, or if a
            void element such as ``br`` or ``img`` is given children.
        :raises TypeError: if an attribute mapping is not the first argument.
        """
        return _build(tag, args)


#: The shared builder: ``E.div(...)`` or ``E("div", ...)`` builds a ``<div>`` element.
E = ElementMaker()

__all__ = [
    "Attributes",
    "Content",
    "E",
    "ElementMaker",
]
