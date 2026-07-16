"""
turbohtml.query: search a tree, the one namespace for querying nodes.

Two facades over the same native CSS and XPath engines sit here. :class:`Query` is a pyquery-style fluent, chainable
wrapper for code migrating off `pyquery <https://github.com/gawel/pyquery>`_'s jQuery-style chaining: it wraps an
ordered, duplicate-free set of elements and every traversal and mutation method returns a :class:`Query`, so calls
compose (``Query(html)("div").find("a").filter(".x").eq(0).attr("href")``). Alongside it, the soupsieve-shaped surface
(:func:`compile`, :class:`Matcher`, the module-level :func:`select`/:func:`select_one`/:func:`iselect`/:func:`match`/
:func:`filter`/:func:`closest`, :func:`escape_identifier`, and the :class:`Matching` config) mirrors soupsieve's call
shapes for a BeautifulSoup port -- see :mod:`turbohtml.query._match` for the detail.

Both are kept out of the core API (which is one-name-per-concept and not chainable) so the extra shapes stay optional.
The :class:`Query` method names are turbohtml's own -- ``add_class`` rather than pyquery's ``addClass`` -- so a port
adjusts the spelling but keeps the structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, overload

from turbohtml._html import Document, Element, _matches_many, _select_many, parse

from ._match import (
    DEBUG,
    Matcher,
    Matching,
    SelectorSyntaxError,
    closest,
    compile,  # noqa: A004  # the soupsieve entry-point name
    css,
    escape_identifier,
    filter,  # noqa: A004  # the soupsieve entry-point name
    iselect,
    match,
    select,
    select_one,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

__all__ = [
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
]


def _unique(elements: Iterable[Element]) -> list[Element]:
    """
    Return the elements in order, dropping any later duplicate.

    A fresh wrapper is handed out on each tree access, so two wrappers of one node
    are distinct objects; equality and hashing compare the underlying node, so a
    set of the elements deduplicates by node identity.
    """
    seen: set[Element] = set()
    out: list[Element] = []
    for element in elements:
        if element not in seen:
            seen.add(element)
            out.append(element)
    return out


def _class_list(element: Element) -> list[str]:
    """Read the ``class`` attribute, which the tree stores tokenized as a list or omits entirely."""
    value = element.attrs.get("class")
    return list(value) if isinstance(value, list) else []


class Query:  # noqa: PLR0904  # a fluent wrapper mirrors pyquery's broad chainable method set
    """
    An ordered, duplicate-free set of elements with chainable traversal and mutation.

    :param source: HTML to parse, a single Element or Document, or an iterable of Elements.
    """

    def __init__(self, source: str | Element | Document | Iterable[Element]) -> None:
        """Wrap a source into a query set."""
        if isinstance(source, str):
            source = parse(source)
        if isinstance(source, Document):
            # a parsed document always has an <html> root, though the type admits None
            self._nodes: list[Element] = [cast("Element", source.root)]
        elif isinstance(source, Element):
            self._nodes = [source]
        else:
            self._nodes = _unique(source)

    @classmethod
    def _wrap(cls, nodes: list[Element]) -> Query:
        """Build a query around an already-unique, document-ordered node list, skipping the dedupe."""
        query = cls.__new__(cls)
        query._nodes = nodes  # noqa: SLF001  # a same-class factory setting its own private attribute
        return query

    def __call__(self, selector: str) -> Query:
        """
        Select the descendants of the wrapped elements matching a selector, like ``find``.

        :param selector: the CSS selector.
        :returns: a query over the matching descendants.
        """
        return self.find(selector)

    def find(self, selector: str) -> Query:
        """
        Select every descendant of the wrapped elements matching a selector.

        :param selector: the CSS selector.
        :returns: a query over the matching descendants in document order.
        """
        if len(self._nodes) == 1:
            # the C select() already returns unique results in document order
            return Query._wrap(self._nodes[0].select(selector))
        return Query._wrap(_select_many(self._nodes, selector))

    def filter(self, selector: str) -> Query:
        """
        Keep the wrapped elements that themselves match a selector.

        :param selector: the CSS selector.
        :returns: a query over the elements that match.
        """
        return Query._wrap(_matches_many(self._nodes, selector))

    def eq(self, index: int) -> Query:
        """
        Reduce the set to the element at one index.

        :param index: the position in the set.
        :returns: a query over that element, or an empty query when the index is out of range.
        """
        try:
            return Query([self._nodes[index]])
        except IndexError:
            return Query([])

    def parent(self) -> Query:
        """
        Select the immediate parent of each wrapped element.

        :returns: a query over the parent elements.
        """
        return Query(node.parent for node in self._nodes if isinstance(node.parent, Element))

    def children(self, selector: str | None = None) -> Query:
        """
        Select the element children of each wrapped element.

        :param selector: an optional CSS selector to keep only matching children.
        :returns: a query over the child elements.
        """
        children = (child for node in self._nodes for child in node.children if isinstance(child, Element))
        result = Query(children)
        return result if selector is None else result.filter(selector)

    def siblings(self, selector: str | None = None) -> Query:
        """
        Select the sibling elements of each wrapped element.

        :param selector: an optional CSS selector to keep only matching siblings.
        :returns: a query over the sibling elements.
        """
        result = Query(
            sibling
            for node in self._nodes
            if isinstance(node.parent, Element)
            for sibling in node.parent.children
            if isinstance(sibling, Element) and sibling != node
        )
        return result if selector is None else result.filter(selector)

    def closest(self, selector: str) -> Query:
        """
        Select the nearest self-or-ancestor of each wrapped element matching a selector.

        :param selector: the CSS selector.
        :returns: a query over the nearest matching elements.
        """
        return Query(found for node in self._nodes if (found := node.closest(selector)) is not None)

    def items(self) -> Iterator[Query]:
        """
        Iterate the wrapped elements, each as its own single-element query.

        :returns: an iterator of single-element queries.
        """
        for node in self._nodes:
            yield Query([node])

    @overload
    def attr(self, name: str) -> str | None: ...
    @overload
    def attr(self, name: str, value: str) -> Query: ...
    def attr(self, name: str, value: str | None = None) -> str | Query | None:
        """
        Get an attribute from the first element, or set it on every element.

        :param name: the attribute name.
        :param value: the value to set; omit to read instead.
        :returns: the attribute value when reading, or the query when setting.
        """
        if value is None:
            if not self._nodes:
                return None
            current = self._nodes[0].attrs.get(name)
            return " ".join(current) if isinstance(current, list) else current
        for node in self._nodes:
            node.attrs[name] = value
        return self

    @overload
    def text(self) -> str: ...
    @overload
    def text(self, value: str) -> Query: ...
    def text(self, value: str | None = None) -> str | Query:
        """
        Get the combined text of every element, or set each element's text.

        :param value: the text to set; omit to read instead.
        :returns: the combined text when reading, or the query when setting.
        """
        if value is None:
            return " ".join(node.text for node in self._nodes)
        for node in self._nodes:
            node.text = value
        return self

    def html(self) -> str | None:
        """
        Return the inner HTML of the first element.

        :returns: the first element's inner HTML, or None when the set is empty.
        """
        return self._nodes[0].inner_html if self._nodes else None

    def has_class(self, name: str) -> bool:
        """
        Test the wrapped elements for a class.

        :param name: the class name.
        :returns: whether any wrapped element carries the class.
        """
        return any(name in _class_list(node) for node in self._nodes)

    def add_class(self, name: str) -> Query:
        """
        Add a class to every wrapped element.

        :param name: the class name to add.
        :returns: the query.
        """
        for node in self._nodes:
            classes = _class_list(node)
            if name not in classes:
                classes.append(name)
                node.attrs["class"] = classes
        return self

    def remove_class(self, name: str) -> Query:
        """
        Remove a class from every wrapped element.

        :param name: the class name to remove.
        :returns: the query.
        """
        for node in self._nodes:
            classes = _class_list(node)
            if name in classes:
                node.attrs["class"] = [cls for cls in classes if cls != name]
        return self

    def toggle_class(self, name: str) -> Query:
        """
        Toggle a class on each element, adding it where absent and removing it where present.

        :param name: the class name to toggle.
        :returns: the query.
        """
        for node in self._nodes:
            classes = _class_list(node)
            node.attrs["class"] = [cls for cls in classes if cls != name] if name in classes else [*classes, name]
        return self

    def __iter__(self) -> Iterator[Element]:
        """Iterate the wrapped elements."""
        return iter(self._nodes)

    def __len__(self) -> int:
        """Return the number of wrapped elements."""
        return len(self._nodes)

    def __getitem__(self, index: int) -> Element:
        """Return the wrapped element at the given index."""
        return self._nodes[index]

    def __eq__(self, other: object) -> bool:
        """Return whether other is a query wrapping the same elements in the same order."""
        return isinstance(other, Query) and self._nodes == other._nodes

    def __hash__(self) -> int:
        """Hash the wrapped elements by node identity."""
        return hash(tuple(self._nodes))

    def __repr__(self) -> str:
        """Return a representation listing the wrapped elements' tags."""
        return f"Query({[node.tag for node in self._nodes]!r})"
