##################
 From simple-html
##################

.. package-meta:: simple-html keithasaurus/simple_html

`simple-html <https://github.com/keithasaurus/simple_html>`_ is a pure-Python library for *generating* HTML from the
inside out: each element is a tag object you call with a leading attribute mapping and then its children
(``div({"class": "card"}, h1("Title"))``), and ``render`` walks the resulting tuples into a string. It carries no
dependencies, is fully type-checked, and is scoped deliberately narrow -- it builds and escapes markup and nothing else,
which makes it a common choice for server-rendered views and email templates where a full DOM would be overkill.

turbohtml covers the same construction ground with the terse :data:`turbohtml.build.E` builder, which keeps
simple-html's call shape (attributes first, children after) but hands back a real :class:`~turbohtml.Element` tree
instead of a string. That one difference in result type is the whole reason to move: the same call that builds your
markup also leaves something you can query, edit, and re-serialize by exactly the rules that parse it back.

**************************
 turbohtml vs simple-html
**************************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - simple-html
    - - Scope
      - Full WHATWG parse, query, edit, and serialize; ``E`` is one builder over that tree
      - HTML generation and escaping only; no parser, no queryable tree
    - - Feature breadth
      - Build, ``find``/``select``, mutate, ``to_markdown``, re-parse
      - Build and ``render`` a string; the result is inert once produced
    - - Performance
      - Assembles in a C arena, serializes in C; ~3x slower than simple-html on raw string generation
      - Concatenates tuples straight into a string, the faster path for one-shot rendering
    - - Typing
      - ``E.<tag>`` resolves dynamically via ``__getattr__``; the tree API is fully typed
      - Every tag is an explicitly typed symbol, so a mistyped tag name fails at type-check time
    - - Dependencies
      - Requires the compiled C extension
      - Zero dependencies, pure Python
    - - Maintenance
      - Active; ``E`` shares the parser's escape and serialize rules
      - Small, stable, single-purpose

Feature overlap
===============

The construction surface ports 1:1 -- both put attributes first and children after:

- ``div({"class": "card"}, h1("Title"))`` -> :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>`.
- A string child becomes escaped text in both.
- A leading mapping sets attributes; everything after is a child in order.
- Nested calls compose the same way, so an existing simple-html view tree maps node for node onto ``E`` calls.

What turbohtml adds
===================

``E`` returns a real :class:`~turbohtml.Element`, so the entire tree surface is available on what you just built:

- Query the fragment you constructed with :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.select`.
- Mutate it with :meth:`~turbohtml.Element.append`, :meth:`~turbohtml.Element.extend`, and the rest of the edit API.
- Convert it with :meth:`~turbohtml.Node.to_markdown`.
- Serialize by the same C rules that parse HTML back, so round-tripping generated markup is exact.
- A list-valued attribute joins on a space, so ``{"class": ["card", "lg"]}`` reads as a token list naturally.

What simple-html has that turbohtml does not
============================================

- **Per-tag static typing.** simple-html exposes each tag as its own typed symbol, so a typo like ``dvi(...)`` is a
  type-check error. ``E.dvi(...)`` resolves through ``__getattr__`` and would build a literal ``<dvi>`` element; the
  builder cannot catch an unknown tag name statically. No equivalent -- rely on runtime output or tests.
- **A pre-escaped-markup escape hatch.** simple-html's ``SafeString("<b>hi</b>")`` injects trusted raw markup. ``E`` has
  no ``SafeString``; express the markup as child elements (:data:`E.b("hi") <turbohtml.build.E>`) or parse the string
  into nodes first.
- **A doctype constant.** simple-html ships ``DOCTYPE_HTML5`` to prepend a full-page doctype. ``E`` builds a fragment
  with no ``<html>``/``<head>``/``<body>`` shell and no doctype; append the fragment under a parsed document when you
  need the page frame.
- **Zero dependencies / pure Python.** simple-html installs with no build step. turbohtml requires its compiled C
  extension, which matters on locked-down or exotic targets.

Performance
===========

``E`` assembles the fragment in turbohtml's arena and serializes it in C; simple-html stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/simple-html.json

simple-html renders about three times faster than ``E`` on this microbenchmark -- it concatenates tuples straight into a
string -- but the decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a
string, so the call that builds the markup also leaves a tree you can query, edit, and
re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the import and the render step. simple-html and turbohtml share the call shape, so most calls port unchanged:

.. code-block:: python

    # before
    from simple_html import div, h1, render

    # after
    from turbohtml.build import E

The API mapping:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `simple-html <https://github.com/keithasaurus/simple_html>`__
      - turbohtml
    - - ``render(div({"class": "card"}, h1("Title")))``
      - :data:`E.div({"class": "card"}, E.h1("Title")) <turbohtml.build.E>` ``.serialize()``; same shape,
        :meth:`~turbohtml.Node.serialize` replaces ``render``
    - - ``li({"class": "item", "data-i": "1"}, "text")``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`, unchanged
    - - ``SafeString("<b>hi</b>")`` for pre-escaped markup
      - build the markup as child elements instead: :data:`E.b("hi") <turbohtml.build.E>`
    - - ``del_({}, "removed")`` (keyword-shadowing tag)
      - :data:`E("del", {}, "removed") <turbohtml.build.E>` via the call form

Before and after:

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

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype,
  where simple-html ships a ``DOCTYPE_HTML5`` constant to prepend. Serialize the element you built, or append it under a
  parsed document when you need the full page shell.
- Both escape string children by default; simple-html's ``SafeString`` escape hatch has no equivalent, so express raw
  markup as child elements (or parse it into nodes first).
- simple-html suffixes tags that shadow keywords (``del_``); ``E.del_`` would build a literal ``<del_>``, so use the
  call form ``E("del", ...)`` for those tags.
- A mistyped tag on ``E`` silently builds that element rather than erroring, since ``E.<tag>`` resolves dynamically;
  simple-html would flag the same typo at type-check time. Cover generated markup with a test.
- A mapping argument is always read as attributes and must come first; passing one after a child raises ``TypeError``.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
