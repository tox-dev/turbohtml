"""
The typed result classes and JSON-LD parsing facade behind :meth:`turbohtml.Document.structured_data`.

The tree walk that locates structured data and the OpenGraph/Microdata extraction all live in the C core
(``turbohtml._html``), exposed as the :meth:`~turbohtml.Document.structured_data`, :meth:`~turbohtml.Document.json_ld`,
:meth:`~turbohtml.Document.opengraph`, and :meth:`~turbohtml.Document.microdata` methods. The C pass assembles the plain
dict/list/str pieces and then hands them to the :class:`MicrodataItem`, :class:`StructuredData`, and :class:`OpenGraph`
record classes defined here, so the typed, read-only result shapes live in Python while every walk stays in C. The other
piece of real Python logic is parsing the gathered ``<script type="application/ld+json">`` texts with the standard
library :mod:`json`. Importing this module registers the parser and every record class with the core.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeAlias

from ._html import _register_structured_data

if TYPE_CHECKING:
    from collections.abc import Iterator

JSONValue: TypeAlias = bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"] | None
"""A decoded JSON value: a scalar, ``None``, or a list/dict nesting more of the same."""


@dataclass(frozen=True, slots=True)
class MicrodataItem:
    """
    One HTML Microdata item: an element carrying ``itemscope`` and the properties beneath it.

    :param type: the ``itemtype`` attribute verbatim, or ``None`` when the element has none.
    :param id: the ``itemid`` attribute verbatim, or ``None`` when the element has none.
    :param properties: each ``itemprop`` name mapped to its values in document order, a value being the property's
        text/URL as a ``str`` or a nested :class:`MicrodataItem` for a property that is itself an ``itemscope``.
    """

    type: str | None
    """the ``itemtype`` attribute verbatim, or ``None`` when the element has none."""
    id: str | None
    """the ``itemid`` attribute verbatim, or ``None`` when the element has none."""
    properties: dict[str, list[str | MicrodataItem]]
    """each ``itemprop`` name mapped to its values in document order."""

    def get(self, name: str) -> str | MicrodataItem | None:
        """Return the property's first value, or ``None`` when the item lacks it (``microdata.Item.get``)."""
        values = self.properties.get(name)
        return values[0] if values else None

    def get_all(self, name: str) -> list[str | MicrodataItem]:
        """Return the property's values in document order, an empty list when absent (``microdata.Item.get_all``)."""
        return self.properties.get(name, [])

    def json(self) -> str:
        """Serialize as ``microdata.Item.json`` does: a two-space-indented JSON object of the tree below the item."""
        return json.dumps(_as_dict(self), indent=2)


@dataclass(frozen=True, slots=True)
class RdfaItem:
    """
    One RDFa resource: an element carrying ``typeof`` and the ``property`` values beneath it.

    Mirrors :class:`MicrodataItem` with the RDFa vocabulary context added. ``property`` keys and the ``type`` IRIs are
    expanded against the in-scope ``@vocab`` and ``@prefix`` mappings (the RDFa 1.1 initial context seeds the common
    prefixes, so ``schema:``, ``dc:``, ``foaf:`` resolve without a page-level declaration); a token whose prefix is
    undeclared, or a bare term with no ``@vocab`` in scope, is kept verbatim.

    :param vocab: the in-scope ``@vocab`` IRI, or ``None`` when no vocabulary is set.
    :param type: the expanded ``@typeof`` IRIs in document order, an empty list for a valueless ``typeof``.
    :param resource: the resource subject (``@about``/``@resource``/``@href``/``@src``), or ``None`` for a blank node.
    :param properties: each expanded ``property`` IRI mapped to its values in document order, a value being a literal or
        IRI ``str`` or a nested :class:`RdfaItem` for a property that is itself a ``typeof``.
    """

    vocab: str | None
    """the in-scope ``@vocab`` IRI, or ``None`` when no vocabulary is set."""
    type: list[str]
    """the expanded ``@typeof`` IRIs in document order, an empty list for a valueless ``typeof``."""
    resource: str | None
    """the resource subject (``@about``/``@resource``/``@href``/``@src``), or ``None`` for a blank node."""
    properties: dict[str, list[str | RdfaItem]]
    """each expanded ``property`` IRI mapped to its values in document order."""

    def get(self, name: str) -> str | RdfaItem | None:
        """Return the property's first value, or ``None`` when the item lacks it."""
        values = self.properties.get(name)
        return values[0] if values else None

    def get_all(self, name: str) -> list[str | RdfaItem]:
        """Return the property's values in document order, an empty list when absent."""
        return self.properties.get(name, [])


@dataclass(frozen=True, slots=True)
class StructuredData:
    """
    Every machine-readable metadata format a document embeds, the combined result of one walk.

    :param json_ld: each ``<script type="application/ld+json">`` block decoded with :mod:`json`.
    :param microdata: every top-level :class:`MicrodataItem`.
    :param opengraph: each ``og:``/``twitter:`` ``<meta>`` key mapped to its content.
    :param microformats: reserved for a later phase, an empty list for now.
    :param rdfa: every top-level :class:`RdfaItem`.
    :param dublin_core: each ``dc.*``/``dcterms.*`` ``<meta>`` name (lower-cased) mapped to its content.
    """

    json_ld: list[JSONValue]
    """each ``<script type="application/ld+json">`` block decoded with :mod:`json`."""
    microdata: list[MicrodataItem]
    """every top-level :class:`MicrodataItem`."""
    opengraph: dict[str, str]
    """each ``og:``/``twitter:`` ``<meta>`` key mapped to its content."""
    microformats: list[JSONValue]
    """reserved for a later phase, an empty list for now."""
    rdfa: list[RdfaItem]
    """every top-level :class:`RdfaItem`."""
    dublin_core: dict[str, str]
    """each ``dc.*``/``dcterms.*`` ``<meta>`` name (lower-cased) mapped to its content."""


_OG_REQUIRED: Final = ("title", "type", "image", "url")
"""The Open Graph protocol's four required properties; the check behind :meth:`OpenGraph.is_valid`."""


class OpenGraph(Mapping[str, str]):
    """
    A page's Open Graph metadata as a read-only mapping, the record :meth:`turbohtml.Document.opengraph` returns.

    Keys are the ``og:`` property names with that prefix stripped (``og:title`` reads as ``og["title"]``), matching the
    ``opengraph`` library's ``OpenGraph`` dict. The mapping supports the full read surface -- ``og["title"]``,
    ``"title" in og``, ``og.get("title")``, iteration, and equality against a plain ``dict`` -- and adds
    :meth:`is_valid`.
    """

    __slots__ = ("_properties",)

    def __init__(self, properties: dict[str, str], /) -> None:
        """Wrap the already prefix-stripped ``og:`` property mapping the C walk builds."""
        self._properties = properties

    def __getitem__(self, key: str) -> str:
        """Return the value of the ``og:<key>`` property, raising :exc:`KeyError` when the page lacks it."""
        return self._properties[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate the prefix-stripped property names in document order."""
        return iter(self._properties)

    def __len__(self) -> int:
        """Return the number of ``og:`` properties the page carries."""
        return len(self._properties)

    def __repr__(self) -> str:
        """Render as ``OpenGraph({...})`` around the underlying property mapping."""
        return f"OpenGraph({self._properties!r})"

    def is_valid(self) -> bool:
        """
        Return whether the page carries the four properties the Open Graph protocol requires, each non-empty.

        Mirrors the ``opengraph`` library's ``is_valid``: every one of ``og:title``, ``og:type``, ``og:image``, and
        ``og:url`` is present with a non-empty value. The ``opengraph_py3`` fork also demands ``og:description``; the
        `Open Graph protocol <https://ogp.me/>`_ does not, so it is not required here.
        """
        return all(self._properties.get(name) for name in _OG_REQUIRED)


def _as_dict(item: MicrodataItem) -> dict[str, JSONValue]:
    """Render the item as nested plain dicts: ``type`` (whitespace-split), ``id``, and its properties recursively."""
    result: dict[str, JSONValue] = {}
    if item.type is not None:
        result["type"] = item.type.split()
    if item.id is not None:
        result["id"] = item.id
    result["properties"] = {
        name: [_as_dict(value) if isinstance(value, MicrodataItem) else value for value in values]
        for name, values in item.properties.items()
    }
    return result


def _parse_json_ld(texts: list[str]) -> list[JSONValue]:
    """
    Decode each JSON-LD block, skipping any that is not valid JSON or whose payload is not a node object.

    A block whose JSON is a scalar or ``null`` (e.g. ``<script type="application/ld+json">null</script>``) is not a
    JSON-LD node object and carries no data, so only ``dict`` and ``list`` payloads are kept.
    """
    parsed: list[JSONValue] = []
    for text in texts:
        try:
            value: JSONValue = json.loads(text)
        except ValueError:
            continue
        if isinstance(value, (dict, list)):
            parsed.append(value)
    return parsed


_register_structured_data(_parse_json_ld, MicrodataItem, RdfaItem, StructuredData, OpenGraph)
