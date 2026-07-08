################
 From fast-html
################

.. package-meta:: fast-html pcarbonn/fast_html

`fast-html <https://github.com/pcarbonn/fast_html>`_ is a small pure-Python library that assembles HTML5 from the inside
out. Each tag is a function that takes its children first (a single node or a list) and its attributes as trailing
keyword arguments, with the usual underscore mangling (``class_`` to ``class``, ``data_i`` to ``data-i``). A tag yields
its markup lazily as a generator of string fragments, and ``render`` joins those fragments into the final string. It
escapes nothing and produces a string, not a tree, so it is a generation-only tool: composition happens in Python and
the result is markup you hand off to a response or a file.

turbohtml covers that ground with :data:`turbohtml.build.E`, a terse builder over :class:`~turbohtml.Element`:
``E.<tag>(attrs, *children)``, where a leading mapping is the attributes and each child is a node or a string that
becomes text. The difference is the result type. ``E`` hands back a real turbohtml tree, so the same call that builds
the markup also leaves an element you can query, edit, and re-serialize, and the markup it produces serializes by
exactly the rules that parse it back.

************************
 turbohtml vs fast-html
************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - fast-html
    - - Scope
      - Full HTML parse, query, edit, and serialize engine, with the ``E`` builder as one front end
      - HTML generation only: tag functions that render to a string
    - - Feature breadth
      - Build, parse, CSS ``select``, ``find``, mutate, ``serialize``, streaming ``serialize_iter``, ``to_markdown``,
        minify
      - Tag functions, keyword-mangled attributes, lazy fragment rendering
    - - Performance
      - Builds in turbohtml's arena and serializes in C, about twice as fast on the corpus below
      - Pure-Python generator that yields and joins string fragments
    - - Typing
      - Typed public API and shipped stubs (:data:`turbohtml.build.E`, :class:`~turbohtml.Element`)
      - Untyped
    - - Dependencies
      - Ships the C extension, no Python dependencies
      - Pure Python, zero dependencies
    - - Maintenance
      - Actively developed alongside the turbohtml serializer
      - Small, stable, single-maintainer

Feature overlap
===============

The construction path ports 1:1:

- Build an element with attributes and children: ``render(div([h1("Title"), p("body")], class_="card"))`` maps to
  :data:`E.div({"class": "card"}, E.h1("Title"), E.p("body")) <turbohtml.build.E>` ``.serialize()``. Children are plain
  arguments, with no list wrapper.
- Nest elements by passing built nodes as children, to any depth.
- Turn a string argument into a text child: ``li("text", ...)`` maps to ``E.li({...}, "text")``.
- Set a valueless boolean attribute (``disabled``) by mapping its name to ``None``.

What turbohtml adds
===================

- A real tree, not a string. The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface
  (``append``, ``extend``, ``find``, ``select``, ``serialize``, ``to_markdown``) stays available; the builder only saves
  the construction boilerplate.
- Escaping. ``E`` builds text nodes, so ``E.div("<b>")`` serializes as ``&lt;b&gt;`` instead of emitting the markup
  verbatim.
- Round-tripping. The markup ``E`` generates serializes by the same rules that parse it back, so a built fragment and a
  parsed one behave identically.
- Splicing into parsed documents. Because a built node is a real tree, you can ``append`` it under a document you parsed
  from existing HTML, not just render it in isolation.
- A native-C serialize path, about twice as fast as fast-html on the corpus below.
- Lazy, streaming output. :meth:`~turbohtml.Node.serialize_iter` yields the markup in bounded ``str`` chunks, so a very
  large page streams to a socket or file without ever materializing the whole string -- the same shape as a fast-html
  tag's fragment generator. ``''.join(node.serialize_iter())`` equals ``node.serialize()``.
- A typed surface with shipped stubs.

What fast-html has that turbohtml does not
==========================================

- A dependency-free pure-Python install. fast-html runs anywhere CPython does with no compiled extension; turbohtml
  ships a C extension, so it needs a wheel for the platform or a build toolchain. No equivalent if a pure-Python install
  is a hard requirement.
- Keyword-argument attribute syntax (``class_="card"``). turbohtml takes a leading mapping instead. Minor: pass a dict,
  writing the real attribute name as the key.

Performance
===========

``E`` assembles the fragment in turbohtml's arena and serializes it in C; fast-html stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/fast-html.json

``E`` is about twice as fast as fast-html, and it hands back a real :class:`~turbohtml.Element`, not a string, so the
call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the tag imports for the single builder. fast-html takes children first and attributes last; turbohtml leads with
the attributes:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - fast-html
      - turbohtml
    - - ``from fast_html import div, h1, p, li, render``
      - ``from turbohtml.build import E``
    - - ``render(div([h1("Title"), p("body")], class_="card"))``
      - :data:`E.div({"class": "card"}, E.h1("Title"), E.p("body")) <turbohtml.build.E>` ``.serialize()``
    - - ``li("text", class_="item", data_i="1")``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - a non-identifier tag (a custom element)
      - ``E("my-card", ...)``, the call form for any tag name
    - - a class list
      - a list value that joins on a space: ``{"class": ["card", "lg"]}``

``E("tag", ...)`` is the call form for a tag that is not a Python identifier, and a list-valued attribute joins on a
space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

Before and after, reusing the same card:

.. code-block:: python

    # fast-html
    from fast_html import div, h1, p, render

    render(div([h1("Title"), p("body")], class_="card"))  # a string, in Python

    # turbohtml
    from turbohtml.build import E

    E.div({"class": "card"}, E.h1("Title"), E.p("body")).serialize()  # a tree, serialized in C

**********************
 Gotchas and pitfalls
**********************

- Fragment, not document. ``E`` builds a fragment: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no
  doctype. Serialize the element you built, or append it under a parsed document when you need the full page shell.
- Attribute names. fast-html mangles keyword arguments (``class_`` to ``class``, ``data_i`` to ``data-i``); ``E`` takes
  a plain mapping, so write the real attribute name as the dict key, with no underscore convention to remember.
- Escaping changes the output. fast-html never escapes: ``render(div("<b>"))`` emits the ``<b>`` markup verbatim. ``E``
  builds text nodes, so the same call serializes as ``&lt;b&gt;``. Markup that used to pass through as a string must
  become child elements.
- Consumption. A fast-html tag is a generator, consumed once by the ``render`` that joins it; an
  :class:`~turbohtml.Element` is a tree you can serialize as many times as you like.
- Argument order. A mapping argument to ``E`` sets attributes and must come first; a mapping passed as a later child
  raises :class:`TypeError` rather than being rendered.
