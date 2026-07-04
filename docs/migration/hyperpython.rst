##################
 From hyperpython
##################

.. package-meta:: hyperpython fabiommendes/hyperpython

`hyperpython <https://github.com/fabiommendes/hyperpython>`_ assembles HTML in Python from the other direction than a
parser: each element takes keyword attributes in a call and children in a ``[...]`` subscript
(``div(class_="card")[h1("Title")]``), and stringifying the root renders the tree. It also carries the ``h("tag", attrs,
children)`` call form for dynamic tags, escapes string children through ``markupsafe``, and offers ``Blob``/``safe``
escape hatches for raw markup. It was built to generate fragments inside Django and other Python web stacks without a
template language.

The library is unmaintained and no longer imports on Python 3.11 or newer: its ``sidekick`` dependency crashes at import
time unless pinned to ``sidekick<0.7``, and it pins ``markupsafe<2``. Porting is also the unblock for current
interpreters and dependency stacks. turbohtml covers the same construction ground with the terse
:data:`turbohtml.build.E` builder, but the value it hands back is a real :class:`~turbohtml.Element` in a parsed tree
rather than a render object, so the whole query, edit, and serialize surface stays available on what you just built.

**************************
 turbohtml vs hyperpython
**************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - hyperpython
    - - Scope
      - Full WHATWG parser, serializer, builder, selectors, sanitizer, converters
      - HTML generation only, from Python
    - - Feature breadth
      - ``E`` builder over a real tree: CSS/XPath query, edit, ``to_markdown``, compact/indent/minify layouts
      - Subscript nesting, ``h`` call form, ``markupsafe`` escaping, ``Blob``/``safe`` raw hatches, framework helpers
    - - Performance
      - Builds and serializes in C
      - Pure Python
    - - Typing
      - Fully annotated, ``py.typed``
      - Untyped
    - - Dependencies
      - None (self-contained C extension)
      - ``sidekick<0.7`` and ``markupsafe<2``
    - - Maintenance
      - Active
      - Unmaintained; does not import on Python 3.11+

Feature overlap
===============

The shared surface ports one-to-one:

- ``div(class_="card")[h1("Title")]`` -> :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes
  are a leading mapping, children follow in the same call.
- ``li(class_="item", data_i="1")["text"]`` -> :data:`E.li({"class": "item", "data-i": "1"}, "text")
  <turbohtml.build.E>`; write the real attribute name as the dict key, with no ``class_`` alias or
  ``data_``-to-``data-`` rewrite to remember.
- ``h("tag", {"class": "x"}, children)`` -> :data:`E("tag", {"class": "x"}, *children) <turbohtml.build.E>`, the call
  form for a dynamic or non-identifier tag.
- A string child becomes escaped text in both: hyperpython routes it through ``markupsafe``, ``E`` builds a
  :class:`~turbohtml.Text` node.
- A class token list joins on a space: ``div(class_="card lg")`` -> ``E.div({"class": ["card", "lg"]})``.

What turbohtml adds
===================

- The result is an ordinary :class:`~turbohtml.Element`, so :meth:`~turbohtml.Node.select`,
  :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.find`, :meth:`~turbohtml.Node.to_markdown`, and the full edit
  API are available. hyperpython hands back a render object with no CSS or XPath query surface.
- The markup serializes by exactly the WHATWG rules that parse it back, so a fragment round-trips; hyperpython
  hand-renders its own string.
- ``E`` assembles the fragment in turbohtml's arena and serializes it in C, about two and a half times faster than
  hyperpython.
- A :class:`~turbohtml.Minify` layout (whitespace collapse, optional-tag omission, JS/CSS minify) on top of the compact
  and :class:`~turbohtml.Indent` layouts.
- Full type annotations with a ``py.typed`` marker; hyperpython is untyped.
- No pinned dependencies and it runs on current interpreters, where hyperpython needs ``sidekick<0.7`` and
  ``markupsafe<2`` and stops importing on Python 3.11+.
- You can :func:`turbohtml.parse` real markup and append an ``E`` fragment under it, mixing parsing and building in one
  tree.

What hyperpython has that turbohtml does not
============================================

- Subscript children: ``div(class_="card")[h1("Title")]`` reads the nesting in a ``[...]`` block. ``E`` has no subscript
  form. Workaround: pass children as positional arguments after the attribute mapping, ``E.div({"class": "card"},
  E.h1("Title"))``.
- Keyword attribute syntax: ``div(class_="card", data_i="1")`` mangles ``class_`` to ``class`` and ``data_i`` to
  ``data-i``. ``E`` takes a plain mapping instead, ``{"class": "card", "data-i": "1"}``, so there is no rename to
  memorize but also no keyword form.
- ``Blob``/``safe`` inject unescaped HTML into the tree. ``E`` always escapes a string into a :class:`~turbohtml.Text`
  node. Workaround: :func:`turbohtml.parse` the raw markup and append the resulting nodes, since turbohtml is a real
  parser.
- Framework-oriented component helpers aimed at Django rendering. turbohtml is a standalone parser and builder with no
  framework integration layer. Workaround: serialize the ``E`` fragment and hand the string to the framework.

Performance
===========

``E`` is about two and a half times faster than hyperpython. The same ``<ul>`` of rows -- a class, a ``data`` attribute,
and a text child apiece -- built both ways (hyperpython measured on its last importable dependency pin):

.. bench-table::
    :file: bench/hyperpython.json

The decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the
call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the per-tag imports for the single ``E`` builder:

.. code-block:: python

    # hyperpython
    from hyperpython import div, h1, p

    # turbohtml
    from turbohtml.build import E

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `hyperpython <https://github.com/fabiommendes/hyperpython>`__
      - turbohtml
    - - ``div(class_="card")[h1("Title")]``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`; attributes are a mapping, children are
        arguments
    - - ``li(class_="item", data_i="1")["text"]``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``h("tag", {"class": "x"}, children)``
      - :data:`E("tag", {"class": "x"}, *children) <turbohtml.build.E>`, the call form
    - - ``str(elem)`` / ``elem.render()``
      - :meth:`~turbohtml.Node.serialize` (compact) or ``serialize(Html(layout=Indent()))``

A subscript tree becomes one nested call, attributes leading and children following:

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
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- ``E`` escapes strings into text nodes, so ``E.div("<b>")`` serializes as ``&lt;b&gt;``. hyperpython's
  ``Blob``/``safe`` had no escaping; to inject real markup, :func:`turbohtml.parse` it and append the nodes.
- Write attribute names as plain dict keys (``{"data-i": "1"}``), not hyperpython's keyword mangling (``class_``,
  ``data_i``).
- turbohtml serializes compact by default. Pass ``Html(layout=Indent())`` to :meth:`~turbohtml.Node.serialize` for
  indented output.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
