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
