###########
 Tokenizer
###########

.. currentmodule:: turbohtml

The low-level WHATWG tokenization surface, below tree construction. :func:`tokenize` runs a whole string at once;
:class:`Tokenizer` streams chunks. Both yield :class:`Token` objects, whose :class:`TokenType` selects which attributes
are meaningful.

.. autofunction:: tokenize

.. autoclass:: Tokenizer
    :members:

.. autoclass:: Token
    :members:

.. autoclass:: TokenType
    :members:
    :undoc-members:

*****
 SAX
*****

.. module:: turbohtml.saxparse

Event-driven parsing that builds no tree. :func:`sax_parse` drives the WHATWG tree builder and calls a
:class:`SaxHandler` method for each construct; :func:`iter_events` yields the same stream as typed records. The events
reflect the constructed tree (implied tags, foster parenting, the adoption agency), not the raw token stream, and no
node object is created. See :doc:`/explanation/sax` for the memory model and :doc:`/how-to/sax` for a worked recipe.

.. autofunction:: sax_parse

.. autofunction:: iter_events

.. autoclass:: SaxHandler
    :members:

The records :func:`iter_events` yields, unioned as :data:`SaxEvent`:

.. autoclass:: StartElement
    :members:

.. autoclass:: EndElement
    :members:

.. autoclass:: Characters
    :members:

.. autoclass:: Comment
    :members:

.. autoclass:: Doctype
    :members:

.. autoclass:: ProcessingInstruction
    :members:

.. autodata:: SaxEvent
