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

***********
 Rewriting
***********

.. module:: turbohtml.rewrite

Single-pass, DOM-less rewriting in the style of Cloudflare's lol-html. :func:`rewrite` streams the tokenizer over the
input, matches CSS selectors against the open-element stack, and calls a handler for each match, applying its edits to
the output as it goes -- memory stays O(open-element depth), never O(document). See :doc:`/explanation/streaming` for
the memory model and the streamable-selector constraint, and :doc:`/how-to/rewriting` for worked recipes.

.. autofunction:: rewrite

.. py:class:: Element

    The node handle a rewrite handler receives. The same handle backs every handler kind; which members apply depends on
    ``kind``. It is valid only for the duration of the handler call -- stashing it and using it afterwards raises
    :exc:`RuntimeError`.

    .. py:attribute:: kind
        :type: str

        The node kind: ``"element"``, ``"text"``, ``"comment"``, or ``"doctype"``.

    .. py:attribute:: removed
        :type: bool

        Whether a handler removed or replaced this node.

    .. py:attribute:: tag
        :type: str

        An element's lowercased tag name.

    .. py:attribute:: attrs
        :type: tuple[tuple[str, str | None], ...]

        An element's attributes as ``(name, value)`` pairs, ``value`` None for a valueless attribute.

    .. py:attribute:: text
        :type: str

        A text or comment node's body.

    .. py:attribute:: name
        :type: str | None

        A doctype's name, or None.

    .. py:attribute:: public_id
        :type: str | None

        A doctype's public identifier, or None.

    .. py:attribute:: system_id
        :type: str | None

        A doctype's system identifier, or None.

    .. py:method:: get(name, default=None)

        An element attribute's value, or ``default`` when the attribute is absent.

    .. py:method:: has_attribute(name)

        Whether the element carries the named attribute.

    .. py:method:: set_attribute(name, value)

        Set (or add) an element attribute.

    .. py:method:: remove_attribute(name)

        Remove an element attribute if present.

    .. py:method:: set_text(value)

        Replace a text or comment node's body.

    .. py:method:: before(content, *, html=False)

        Insert ``content`` immediately before this node; ``html=True`` inserts raw markup, otherwise it is escaped.

    .. py:method:: after(content, *, html=False)

        Insert ``content`` immediately after this node.

    .. py:method:: prepend(content, *, html=False)

        Insert ``content`` as the first of an element's inner content.

    .. py:method:: append(content, *, html=False)

        Append ``content`` as the last of an element's inner content.

    .. py:method:: set_content(content, *, html=False)

        Replace an element's inner content, keeping its tags.

    .. py:method:: replace(content, *, html=False)

        Replace this node -- an element with its whole subtree -- with ``content``.

    .. py:method:: remove()

        Remove this node (an element removes its whole subtree).

    .. py:method:: remove_and_keep_content()

        Drop an element's own tags but keep its children.

.. autoclass:: ElementHandler
    :members:

.. autoclass:: TextHandler
    :members:

.. autoclass:: CommentHandler
    :members:

.. autoclass:: DoctypeHandler
    :members:
