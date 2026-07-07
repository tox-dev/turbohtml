"""
Synchronous DOM mutation observation.

:class:`MutationObserver` watches a node for tree changes and records them the way the DOM
`MutationObserver <https://dom.spec.whatwg.org/#mutationobserver>`_ does. The DOM delivers records
on a microtask, so a callback never runs mid-edit; turbohtml has no event loop, so it keeps the same
record shape but delivers synchronously -- pull the batch with :meth:`~MutationObserver.take_records`, or
push it to the callback with :meth:`~MutationObserver.deliver`. Records still queue at mutation time and
reach Python only at a drain, so a callback never fires while the tree is half-linked.

The observation engine lives in the C core (``turbohtml._html``); importing this module registers the
:class:`MutationRecord` type it builds each change into.
"""

from __future__ import annotations

from typing import NamedTuple

from ._html import MutationObserver as MutationObserver  # noqa: PLC0414  # re-export the C type
from ._html import Node, _register_mutation_record


class MutationRecord(NamedTuple):
    """One change delivered to an observer, mirroring the DOM MutationRecord."""

    type: str
    """``"childList"``, ``"attributes"``, or ``"characterData"``."""
    target: Node
    """the node the change applies to: the parent for a childList change, else the changed node."""
    added_nodes: tuple[Node, ...]
    """the nodes added, for a childList change (else empty)."""
    removed_nodes: tuple[Node, ...]
    """the nodes removed, for a childList change (else empty)."""
    previous_sibling: Node | None
    """the sibling before the added or removed nodes, or ``None``."""
    next_sibling: Node | None
    """the sibling after the added or removed nodes, or ``None``."""
    attribute_name: str | None
    """the changed attribute's name, for an attributes change (else ``None``)."""
    old_value: str | None
    """the previous value, when the registration asked for it (else ``None``)."""


_register_mutation_record(MutationRecord)

__all__ = [
    "MutationObserver",
    "MutationRecord",
]
