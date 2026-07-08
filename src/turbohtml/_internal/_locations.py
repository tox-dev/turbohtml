"""
The source-location records :attr:`turbohtml.Node.source_location` exposes.

A parse with ``source_locations=True`` records, for every element, where its start tag, end tag, and each attribute sat
in the source -- parse5's ``sourceCodeLocationInfo``. The tracking lives in the C core (``turbohtml._html``): the
tokenizer stamps the spans as it runs and the tree builder hangs one :class:`SourceLocation` off each element. Importing
this module registers the two record types with the core.
"""

from __future__ import annotations

from typing import NamedTuple

from turbohtml._html import _register_locations  # Element stays importable so autodoc resolves it

__all__ = ["SourceLocation", "SourceSpan"]


class SourceSpan(NamedTuple):
    r"""
    Where one construct sat in the source: its start and end line, column, and code-point offset.

    Lines are 1-based, columns 0-based, and offsets are code-point indices into the newline-normalized source (a
    ``\\r\\n`` counts once), the same convention as :attr:`~turbohtml.Node.source_line` and
    :attr:`~turbohtml.Node.source_col`. The half-open ``[start_offset, end_offset)`` slices the construct out of the
    source.
    """

    start_line: int
    """the 1-based line the construct starts on."""
    start_col: int
    """the 0-based column the construct starts at."""
    start_offset: int
    """the code-point offset the construct starts at."""
    end_line: int
    """the 1-based line just past the construct's end."""
    end_col: int
    """the 0-based column just past the construct's end."""
    end_offset: int
    """the code-point offset just past the construct's end."""


class SourceLocation(NamedTuple):
    """
    Where a parsed element sat in the source.

    ``start_tag`` spans the ``<tag ...>``; ``end_tag`` spans the ``</tag>`` or is ``None`` when the source never closed
    the element (a void, self-closed, or implicitly/EOF-closed one). ``attrs`` maps each attribute's name to the span
    covering its ``name="value"`` (the name alone for a valueless attribute), keyed by the same lowercased name
    :attr:`~turbohtml.Element.attrs` uses, first occurrence winning on a duplicate.
    """

    start_tag: SourceSpan
    """the span of the element's start tag."""
    end_tag: SourceSpan | None
    """the span of the element's end tag, or ``None`` when the source did not close it."""
    attrs: dict[str, SourceSpan]
    """each attribute name mapped to the span of its whole ``name="value"``."""


_register_locations(SourceLocation, SourceSpan)
