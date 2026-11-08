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

from ._match import (
    DEBUG,
    Matcher,
    Matching,
    SelectorSyntaxError,
    closest,
    compile,  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
    css,
    escape_identifier,
    filter,  # ruff:ignore[builtin-import-shadowing]  # the soupsieve entry-point name
    iselect,
    match,
    select,
    select_one,
)
from ._query import Query

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
