"""
turbohtml.traverse: the DOM Living Standard traversal objects.

:class:`~turbohtml.TreeWalker` is a movable cursor over a subtree; :class:`~turbohtml.NodeIterator` is a flat
forward/backward view of the same nodes. Both are C types that pair a ``what_to_show`` bitmask with an optional
``filter`` callback; :class:`NodeFilter` collects the named constants for both -- the ``SHOW_*`` bits and the
``FILTER_*`` verdicts -- so a filter reads as it would against jsdom's ``NodeFilter``.

The one turbohtml spelling difference from the DOM: methods are snake_case (``next_node``, ``current_node``) and the
constructor takes ``root`` positionally with ``what_to_show`` and ``filter`` as keywords, rather than the DOM's
``document.createTreeWalker(root, whatToShow, filter)`` factory.
"""

from __future__ import annotations

from typing import Final

from ._html import NodeIterator, TreeWalker

__all__ = ["NodeFilter", "NodeIterator", "TreeWalker"]


class NodeFilter:
    """
    The whatToShow bits and filter verdicts a :class:`TreeWalker` or :class:`NodeIterator` reads.

    The ``SHOW_*`` constants are the ``what_to_show`` bitmask -- OR them to consider several node types, or use
    ``SHOW_ALL``. A ``filter`` callback returns one of the ``FILTER_*`` verdicts: ``FILTER_ACCEPT`` yields the node,
    ``FILTER_REJECT`` skips it and (in a :class:`TreeWalker`) its whole subtree, and ``FILTER_SKIP`` skips only the
    node. A :class:`NodeIterator` has no subtree to skip, so it treats reject and skip alike.
    """

    SHOW_ALL: Final = 0xFFFFFFFF
    SHOW_ELEMENT: Final = 0x1
    SHOW_ATTRIBUTE: Final = 0x2
    SHOW_TEXT: Final = 0x4
    SHOW_CDATA_SECTION: Final = 0x8
    SHOW_ENTITY_REFERENCE: Final = 0x10
    SHOW_ENTITY: Final = 0x20
    SHOW_PROCESSING_INSTRUCTION: Final = 0x40
    SHOW_COMMENT: Final = 0x80
    SHOW_DOCUMENT: Final = 0x100
    SHOW_DOCUMENT_TYPE: Final = 0x200
    SHOW_DOCUMENT_FRAGMENT: Final = 0x400
    SHOW_NOTATION: Final = 0x800

    FILTER_ACCEPT: Final = 1
    FILTER_REJECT: Final = 2
    FILTER_SKIP: Final = 3
