"""
The typed result classes and JSON-LD parsing facade behind :meth:`turbohtml.Document.structured_data`.

The tree walk that locates structured data and the OpenGraph/Microdata extraction all live in the C core
(``turbohtml._html``), exposed as the :meth:`~turbohtml.Document.structured_data`, :meth:`~turbohtml.Document.json_ld`,
:meth:`~turbohtml.Document.opengraph`, and :meth:`~turbohtml.Document.microdata` methods. The C pass assembles the plain
dict/list/str pieces and then hands them to the :class:`MicrodataItem` and :class:`StructuredData` record classes
defined here, so the typed, read-only result shapes live in Python while every walk stays in C. The other piece of real
Python logic is parsing the gathered ``<script type="application/ld+json">`` texts with the standard library
:mod:`json`. Importing this module registers the parser and both record classes with the core.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeAlias

from ._html import _register_structured_data

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
class StructuredData:
    """
    Every machine-readable metadata format a document embeds, the combined result of one walk.

    :param json_ld: each ``<script type="application/ld+json">`` block decoded with :mod:`json`.
    :param microdata: every top-level :class:`MicrodataItem`.
    :param opengraph: each ``og:``/``twitter:`` ``<meta>`` key mapped to its content.
    :param microformats: reserved for a later phase, an empty list for now.
    :param rdfa: reserved for a later phase, an empty list for now.
    """

    json_ld: list[JSONValue]
    """each ``<script type="application/ld+json">`` block decoded with :mod:`json`."""
    microdata: list[MicrodataItem]
    """every top-level :class:`MicrodataItem`."""
    opengraph: dict[str, str]
    """each ``og:``/``twitter:`` ``<meta>`` key mapped to its content."""
    microformats: list[JSONValue]
    """reserved for a later phase, an empty list for now."""
    rdfa: list[JSONValue]
    """reserved for a later phase, an empty list for now."""


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
    """Decode each JSON-LD block, skipping any that is not valid JSON."""
    parsed: list[JSONValue] = []
    for text in texts:
        try:
            value: JSONValue = json.loads(text)
        except ValueError:
            continue
        parsed.append(value)
    return parsed


_register_structured_data(_parse_json_ld, MicrodataItem, StructuredData)
