#######
 Match
#######

.. module:: turbohtml.match

A `soupsieve <https://facelessuser.github.io/soupsieve/>`_-shaped CSS matching surface over turbohtml's native selector
engine, for code migrating off soupsieve (BeautifulSoup's selector library) or a bs4 ``Tag.select`` stack.
:func:`compile` returns a reusable :class:`Matcher` carrying soupsieve's matcher methods, and the module-level
:func:`select`/:func:`select_one`/:func:`iselect`/:func:`match`/:func:`filter`/:func:`closest` helpers compile a
one-shot matcher per call. Every entry point runs the same engine as :meth:`~turbohtml.Node.select`/
:meth:`~turbohtml.Node.matches`/:meth:`~turbohtml.Node.closest`, so the matching is identical to the node methods --
this module only adds the soupsieve call shapes.

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

**********************
 Module-level helpers
**********************

Each helper compiles the selector for the single call and delegates to the matching :class:`Matcher` method, mirroring
soupsieve's free functions. Reuse a :func:`compile` result instead when matching the same selector repeatedly.

.. autofunction:: select

.. autofunction:: select_one

.. autofunction:: iselect

.. autofunction:: match

.. autofunction:: filter

.. autofunction:: closest

***************
 Configuration
***************

.. autoclass:: Matching
    :members: soupsieve

.. autodata:: DEBUG

.. autoexception:: SelectorSyntaxError

.. autofunction:: escape
