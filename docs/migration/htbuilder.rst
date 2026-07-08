################
 From htbuilder
################

.. package-meta:: htbuilder tvst/htbuilder

`htbuilder <https://github.com/tvst/htbuilder>`_ assembles HTML in Python from the other direction than a parser: each
element takes its attributes as keyword arguments in one call and its children in a second (``div(_class="card")(...)``,
with ``_class`` mangled to ``class`` and ``data_i`` to ``data-i``), and stringifying the result renders it. Tag
callables (``div``, ``span``, ``img``, and the rest) are generated on import, and a set of helpers builds attribute
values: ``classes()`` and ``styles()`` assemble ``class`` and ``style`` strings, and CSS unit functions (``px``, ``em``,
``rem``, ``percent``, ``rgba``) format lengths and colors. It ships from the author of Streamlit and is used mostly to
compose small HTML snippets inside Streamlit components. It is pure Python and renders to a string, not a tree.

turbohtml covers that ground with the terse :data:`turbohtml.build.E` builder, a thin front end over
:class:`~turbohtml.Element`. Every ``E.<tag>(...)`` call builds a real turbohtml tree in the arena and serializes it in
C, so the same call that emits the markup also leaves a node you can query, edit, and re-serialize.

************************
 turbohtml vs htbuilder
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - htbuilder
    - - Scope
      - HTML builder (:data:`~turbohtml.build.E`) over a full parse, query, edit, and serialize tree
      - HTML string builder for composing snippets, mostly inside Streamlit components
    - - Feature breadth
      - Builds a real :class:`~turbohtml.Element`; spec-correct serialization and text escaping; the whole
        ``append``/``find``/``select``/``serialize``/``to_markdown`` surface stays available
      - Two-call element form, keyword-argument mangling, and ``classes``/``styles``/``fonts``/``params`` plus CSS unit
        helpers for building attribute values
    - - Performance
      - Native C build and serialize, about twice as fast on the corpus below
      - Pure-Python string assembly
    - - Typing
      - Typed public API (:data:`~turbohtml.build.E`, :class:`~turbohtml.Element`)
      - Untyped
    - - Dependencies
      - None (ships the C extension)
      - Pure Python
    - - Maintenance
      - Actively developed alongside the turbohtml serializer
      - Maintained, small, Streamlit-scoped

Feature overlap
===============

The construction surface ports 1:1:

- Build an element with attributes and children: ``div(_class="card")(h1("Title"))`` maps to :data:`E.div({"class":
  "card"}, E.h1("Title")) <turbohtml.build.E>`.
- Hyphenated and ``data-`` attributes: htbuilder's ``li(_class="item", data_i="1")("text")`` maps to
  :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`.
- A class token list: htbuilder's ``classes("card", "lg")`` maps to a space-joined list value, ``{"class": ["card",
  "lg"]}``.
- Rendering to a string: htbuilder stringifies the root; turbohtml calls :meth:`~turbohtml.Node.serialize`.

What turbohtml adds
===================

- A real tree, not a string. ``E`` hands back an :class:`~turbohtml.Element`, so ``append``, ``find``, ``select``,
  ``serialize``, and ``to_markdown`` all work on the result; htbuilder gives you a value whose only real operation is
  ``str()``.
- Spec-correct text escaping. ``E`` builds :class:`~turbohtml.Text` nodes, so ``E.div("<b>")`` serializes as
  ``&lt;b&gt;``. htbuilder escapes nothing, so ``str(div("<b>"))`` emits the ``<b>`` markup verbatim.
- Plain attribute names, no mangling to remember. ``E`` takes a mapping, so ``class`` and ``data-i`` are written as the
  literal dict keys rather than the ``_class`` / ``data_i`` underscore convention.
- Native-C build and serialize, so the fragment is assembled in the arena and written out in C rather than joined in
  Python.
- The rest of turbohtml: the same tree you built can be re-parsed, converted with :meth:`~turbohtml.Node.to_markdown`,
  or edited in place, none of which htbuilder attempts.

What htbuilder has that turbohtml does not
==========================================

- CSS value helpers. htbuilder's ``styles(padding=px(3), color=rgba(...))`` and the ``px``/``em``/``rem``/``percent``
  unit functions build a ``style`` string for you. ``E`` has no equivalent; write the ``style`` value as a plain string
  (``{"style": "padding:3px"}``).
- ``classes()`` / ``fonts()`` / ``params()`` builder helpers. ``E`` covers ``classes`` with a list value (``{"class":
  ["card", "lg"]}``); the ``fonts`` and ``params`` helpers have no direct counterpart, so build those attribute values
  yourself.
- Generated tag callables you can import by name (``from htbuilder import div, span``). ``E`` namespaces every tag under
  one object instead: ``E.div``, ``E.span``, or ``E("div", ...)`` for a non-identifier tag.

Performance
===========

``E`` assembles the fragment in turbohtml's arena and serializes it in C; htbuilder stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/htbuilder.json

``E`` is about twice as fast as htbuilder, and it hands back a real :class:`~turbohtml.Element` rather than a string, so
the call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

htbuilder splits an element across two calls, attributes first and children second; turbohtml passes both to one call:

.. code-block:: python

    # htbuilder
    from htbuilder import div, h1, li

    # turbohtml
    from turbohtml.build import E

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `htbuilder <https://github.com/tvst/htbuilder>`__
      - turbohtml
    - - ``div(_class="card")(h1("Title"))``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes are a leading mapping, children
        follow in the same call
    - - ``li(_class="item", data_i="1")("text")``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``classes("card", "lg")``
      - a list value that joins on a space: ``{"class": ["card", "lg"]}``
    - - ``styles(padding=px(3))``
      - a plain string value: ``{"style": "padding:3px"}``
    - - a non-identifier tag name
      - :data:`E("tag", ...) <turbohtml.build.E>`, the call form
    - - ``str(element)``
      - :meth:`element.serialize() <turbohtml.Node.serialize>`

Attributes are a leading mapping and children follow in the same call:

.. testcode::

    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(card.serialize())

.. testoutput::

    <div class="card"><h1>Title</h1><p>body</p></div>

``E("tag", ...)`` is the call form for a tag that is not a Python identifier (a custom element, say), and a list-valued
attribute joins on a space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

**********************
 Gotchas and pitfalls
**********************

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype.
  Serialize the element you built, or append it under a parsed document when you need the full page shell.
- htbuilder mangles keyword arguments (``_class`` to ``class``, ``data_i`` to ``data-i``); ``E`` takes a plain mapping,
  so write the real attribute name as the dict key -- no underscore convention to remember.
- htbuilder renders lazily, escaping nothing: ``str(div("<b>"))`` emits the ``<b>`` markup verbatim. ``E`` builds text
  nodes, so the same call serializes as ``&lt;b&gt;``; markup belongs in child elements, not strings.
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
