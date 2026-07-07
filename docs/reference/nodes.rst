#######
 Nodes
#######

.. currentmodule:: turbohtml

A parsed tree is made of nodes. :class:`Node` carries the navigation, query, mutation, and serialization surface every
node shares; the concrete types below add their own data. Text is a real :class:`Text` child node (the WHATWG DOM
shape), so there is no text/tail split.

.. autoclass:: Node
    :members:

.. autoclass:: Element
    :members:

.. autoclass:: Namespace
    :members:

.. autoclass:: Text
    :members:

.. autoclass:: Comment
    :members:

.. autoclass:: CData
    :members:

.. autoclass:: ProcessingInstruction
    :members:

.. autoclass:: Doctype
    :members:

:meth:`Node.links` yields one :class:`Link` per link it finds.

.. autoclass:: Link
    :members:

:meth:`Node.article` returns an :class:`Article` record: the scored content body and the page metadata harvested beside
it.

.. autoclass:: Article
    :members:

When a tree is parsed with ``source_locations=True``, :attr:`Node.source_location` returns a :class:`SourceLocation`
record built from :class:`SourceSpan` values -- the start-tag, end-tag, and per-attribute spans parse5 exposes as
``sourceCodeLocationInfo``.

.. autoclass:: SourceLocation
    :members:

.. autoclass:: SourceSpan
    :members:

*******************
 Traversal objects
*******************

The DOM Living Standard traversal objects walk a subtree under a :class:`NodeFilter` bitmask and callback. A
:class:`TreeWalker` is a movable cursor; a :class:`NodeIterator` is a flat forward/backward view. See
:doc:`/how-to/traversing` for recipes and :doc:`/explanation/traversal` for the reject/skip semantics.

.. autoclass:: NodeFilter
    :members:
    :undoc-members:

.. autoclass:: TreeWalker
    :members:

.. autoclass:: NodeIterator
    :members:
