#######
 Query
#######

.. currentmodule:: turbohtml

Search a tree with the methods on :class:`Node`: :meth:`~Node.find` and :meth:`~Node.find_all` walk an :class:`Axis`
with attribute filters, :meth:`~Node.select` and :meth:`~Node.select_one` take CSS selectors, and :meth:`~Node.xpath`,
:meth:`~Node.xpath_iter`, and :meth:`~Node.xpath_one` evaluate XPath. With ``smart_strings=True`` an XPath string result
comes back as an :class:`XPathString` that remembers the element it was selected from. :class:`XPath` compiles an
expression once and evaluates it against many context nodes or documents, skipping the per-call parse.

.. autoclass:: Axis
    :members:
    :undoc-members:

.. autoclass:: XPathString
    :members:

.. autoclass:: XPath
    :members:
    :special-members: __call__

Every selector-parse path -- :meth:`~Node.select`, :meth:`~Node.matches`, :meth:`~Node.closest`, the
:mod:`turbohtml.query` matching helpers, and :func:`turbohtml.convert.css_to_xpath` -- raises this one error on a
malformed selector. It subclasses :class:`ValueError`, so code catching ``ValueError`` keeps working.

.. autoexception:: SelectorSyntaxError

*****************
 turbohtml.query
*****************

.. module:: turbohtml.query

The :mod:`turbohtml.query` namespace adds two optional facades over the CSS and XPath engines the node methods above
already expose: a pyquery-style chainable :class:`Query`, and a soupsieve-shaped matching surface for a BeautifulSoup
port. Both run the same native engine as the node methods; neither is a second engine.

Chainable queries
=================

A pyquery-style fluent, chainable query wrapper over the tree and selector engine, for code migrating off pyquery's
jQuery-style chaining. Each traversal and mutation method returns a :class:`Query`, so calls compose.

.. autoclass:: Query
    :members:
    :special-members: __call__

Soupsieve-shaped matching
=========================

A `soupsieve <https://facelessuser.github.io/soupsieve/>`_-shaped CSS matching surface over turbohtml's native selector
engine, for code migrating off soupsieve (BeautifulSoup's selector library) or a bs4 ``Tag.select`` stack.
:func:`compile` returns a reusable :class:`Matcher` carrying soupsieve's matcher methods, and the module-level
:func:`select`/:func:`select_one`/:func:`iselect`/:func:`match`/:func:`filter`/:func:`closest` helpers compile a
one-shot matcher per call. Every entry point runs the same engine as :meth:`~turbohtml.Node.select`/
:meth:`~turbohtml.Node.matches`/:meth:`~turbohtml.Node.closest`, so the matching is identical to the node methods --
this surface only adds the soupsieve call shapes.

It is a pure-Python facade with no second engine. The selector entry points on the C core take only the selector string,
so soupsieve's ``namespaces`` and ``flags`` arguments are bundled into one immutable :class:`Matching` config that
travels with the matcher for API parity. Neither alters which elements match: turbohtml selects by an element's local
name, so a prefixed type selector such as ``svg|rect`` matches every ``rect`` regardless of the ``namespaces`` map, and
``flags`` is advisory exactly as in soupsieve. Namespace-discriminating selection is a tracked engine gap, not part of
this surface.

.. autofunction:: compile

.. autofunction:: css

.. autoclass:: Matcher
    :members: select, select_one, iselect, match, filter, closest, pattern, namespaces, flags

Module-level helpers
--------------------

Each helper compiles the selector for the single call and delegates to the matching :class:`Matcher` method, mirroring
soupsieve's free functions. Reuse a :func:`compile` result instead when matching the same selector repeatedly.

.. autofunction:: select

.. autofunction:: select_one

.. autofunction:: iselect

.. autofunction:: match

.. autofunction:: filter

.. autofunction:: closest

Configuration
-------------

.. autoclass:: Matching
    :members: soupsieve

.. autodata:: DEBUG

A malformed selector raises :class:`turbohtml.SelectorSyntaxError`, the one error every selector-parse path shares.

.. autofunction:: escape_identifier
