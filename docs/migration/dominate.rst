###############
 From dominate
###############

.. package-meta:: dominate Knio/dominate

`dominate <https://github.com/Knio/dominate>`_ assembles HTML in Python from the other direction than a parser: you open
a tag as a ``with`` block and nest children inside it (or pass them as positional arguments), set attributes with
keyword arguments (``cls`` for ``class``, ``data_i`` for ``data-i``), then render the tree to a string. It also
scaffolds whole pages: ``dominate.document(title=...)`` emits a doctype with ``<html>``, ``<head>``, and ``<body>``, and
exposes ``doc.head``/``doc.body`` to append into. It is a common way to build reports, emails, and server-rendered pages
in Flask and other Python web stacks without a template language.

turbohtml covers the same ground with the terse :data:`turbohtml.build.E` builder, but the value it hands back is a real
:class:`~turbohtml.Element` in a parsed tree rather than a bespoke render object, so the whole query, edit, and
serialize surface stays available on what you just built.

***********************
 turbohtml vs dominate
***********************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - dominate
    - - Scope
      - Full WHATWG parser, serializer, builder, selectors, sanitizer, converters
      - HTML generation only, from Python
    - - Feature breadth
      - ``E`` builder over a real tree: CSS/XPath query, edit, ``to_markdown``, compact/indent/minify layouts
      - Context-manager nesting, full-page ``document`` scaffolding, pretty-print, ``text``/``raw``/``comment`` helpers
    - - Performance
      - Builds and serializes in C
      - Pure Python
    - - Typing
      - Fully annotated, ``py.typed``
      - Untyped
    - - Dependencies
      - None (self-contained C extension)
      - None (pure Python)
    - - Maintenance
      - Active
      - Stable, infrequent releases

Feature overlap
===============

The shared surface ports one-to-one:

- ``div(child, cls="card")`` -> :data:`E.div({"class": "card"}, child) <turbohtml.build.E>`; attributes are a leading
  mapping, children follow in the same call.
- ``li("text", cls="item", data_i="1")`` -> :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`;
  write the real attribute name as the dict key, with no ``cls`` alias or ``data_``-to-``data-`` rewrite to remember.
- A string child becomes escaped text in both: dominate wraps it in ``text()``, ``E`` builds a :class:`~turbohtml.Text`
  node.
- A non-identifier tag such as a custom element -> :data:`E("my-card", ...) <turbohtml.build.E>`, the call form.
- A class token list joins on a space: ``div(cls="card lg")`` -> ``E.div({"class": ["card", "lg"]})``.

What turbohtml adds
===================

- The result is an ordinary :class:`~turbohtml.Element`, so :meth:`~turbohtml.Node.select`,
  :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.find`, :meth:`~turbohtml.Node.to_markdown`, and the full edit
  API are available. dominate's tree offers only a limited ``get(tag=..., **kwargs)`` descendant search, no CSS or
  XPath.
- The markup serializes by exactly the WHATWG rules that parse it back, so a fragment round-trips; dominate hand-renders
  its own string.
- ``E`` assembles the fragment in turbohtml's arena and serializes it in C, about three to four times faster than
  dominate.
- A :class:`~turbohtml.Minify` layout (whitespace collapse, optional-tag omission, JS/CSS minify) on top of the compact
  and :class:`~turbohtml.Indent` layouts; dominate pretty-prints but does not minify.
- Full type annotations with a ``py.typed`` marker.
- You can :func:`turbohtml.parse` real markup and append an ``E`` fragment under it, mixing parsing and building in one
  tree.

What dominate has that turbohtml does not
=========================================

- Context-manager nesting: ``with div(): p("hi")`` auto-collects children created inside the block, and ``d += child``
  appends in place. ``E`` has no context-manager or ``+=`` form. Workaround: pass children as positional arguments, or
  build bottom-up and :meth:`~turbohtml.Element.append`.
- Mutable page accessors: ``dominate.document(title=...)`` hands back an object exposing ``doc.head``/``doc.body`` to
  append into after the fact. :func:`turbohtml.build.document` takes the head and body content up front and returns a
  finished :class:`~turbohtml.Document`; to add more, reach its ``<body>`` with :meth:`~turbohtml.Node.select` and
  :meth:`~turbohtml.Element.append`.
- ``dominate.util.raw`` injects unescaped HTML into the tree. ``E`` always escapes a string into a
  :class:`~turbohtml.Text` node. Workaround: :func:`turbohtml.parse` the raw markup and append the resulting nodes,
  since turbohtml is a real parser.
- Conditional comments: ``comment(p("IE"), condition="lt IE 9")`` renders ``<!--[if lt IE 9]>...<![endif]-->``.
  turbohtml has a :class:`~turbohtml.Comment` node but no conditional-comment helper. Workaround: build a ``Comment``
  node whose text carries the ``[if ...]`` guard and append it.

Performance
===========

``E`` is about three to four times faster than dominate. The same ``<ul>`` of rows -- a class, a ``data`` attribute, and
a text child apiece -- built both ways:

.. bench-table::
    :file: bench/dominate.json

``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree
you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the tag imports (and any ``document`` scaffolding) for the single ``E`` builder:

.. code-block:: python

    # dominate
    from dominate.tags import div, h1, p

    # turbohtml
    from turbohtml.build import E

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `dominate <https://github.com/Knio/dominate>`__
      - turbohtml
    - - ``div(p("x"), cls="card")`` / ``with div():`` blocks
      - :data:`E.div({"class": "card"}, E.p("x")) <turbohtml.build.E>`; nest by passing children, not a context manager
    - - ``li("text", cls="item", data_i="1")``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``tag(...)`` for a non-identifier tag name
      - :data:`E("tag", ...) <turbohtml.build.E>`, the call form
    - - ``document(title="T")`` full page
      - :func:`turbohtml.build.document(title="T") <turbohtml.build.document>`
    - - ``str(tag)`` / ``tag.render(pretty=True)``
      - :meth:`~turbohtml.Node.serialize` (compact) or ``serialize(Html(layout=Indent()))``

A ``with``-block tree becomes one nested call, attributes leading and children following:

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

For a whole page, :func:`turbohtml.build.document` is the counterpart to ``dominate.document(title=...)``: it emits the
doctype and the ``<html>``/``<head>``/``<body>`` shell around the content you pass, and hands back a
:class:`~turbohtml.Document`:

.. testcode::

    from turbohtml.build import E, document

    page = document(title="Report", lang="en", body=[E.h1("Sales"), E.p("Up 4%")])
    print(page.serialize())

.. testoutput::

    <!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Report</title></head><body><h1>Sales</h1><p>Up 4%</p></body></html>

**********************
 Gotchas and pitfalls
**********************

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype.
  Serialize the element you built, or reach for :func:`turbohtml.build.document` when you need the full page shell that
  dominate's ``document(...)`` writes.
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- ``E`` escapes strings into text nodes, so ``E.div("<b>")`` serializes as ``&lt;b&gt;``. dominate's ``raw()`` had no
  escaping; to inject real markup, :func:`turbohtml.parse` it and append the nodes.
- turbohtml serializes compact by default, where dominate pretty-prints. Pass ``Html(layout=Indent())`` to
  :meth:`~turbohtml.Node.serialize` for indented output.
- Write attribute names as plain dict keys (``{"data-i": "1"}``), not dominate's keyword conventions (``cls``, trailing
  underscore for reserved words, ``data_``/``aria_`` underscore-to-dash).
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
