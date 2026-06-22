#######
 Query
#######

.. currentmodule:: turbohtml

Search a tree with the methods on :class:`Node`: :meth:`~Node.find` and :meth:`~Node.find_all` walk an :class:`Axis`
with attribute filters, :meth:`~Node.select` and :meth:`~Node.select_one` take CSS selectors, and :meth:`~Node.xpath`,
:meth:`~Node.xpath_iter`, and :meth:`~Node.xpath_one` evaluate XPath. With ``smart_strings=True`` an XPath string result
comes back as an :class:`XPathString` that remembers the element it was selected from.

.. autoclass:: Axis
    :members:
    :undoc-members:

.. autoclass:: XPathString
    :members:

*****************
 turbohtml.query
*****************

.. module:: turbohtml.query

A pyquery-style fluent, chainable query wrapper over the tree and selector engine, for code migrating off pyquery's
jQuery-style chaining. Each traversal and mutation method returns a :class:`Query`, so calls compose.

.. autoclass:: Query
    :members:
    :special-members: __call__
