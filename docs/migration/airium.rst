#############
 From airium
#############

.. package-meta:: airium

`airium <https://gitlab.com/kamichal/airium>`_ assembles HTML in Python from the other direction than a parser: you open
each element as a ``with a.tag(...)`` block on an ``Airium`` instance, call the instance for text, and let it track
indentation as you nest, then stringify it. It is a pure-Python, zero-dependency builder whose reach is generation only
-- it also ships a reverse tool (``python -m airium``) that turns existing HTML back into airium source. It is used to
emit reports, templated pages, and snippets from Python without a template engine.

turbohtml covers the same construction ground with the terse :data:`turbohtml.build.E` builder, but every call returns a
real :class:`~turbohtml.Element` in turbohtml's C-backed tree, so the markup you generate is also a document you can
query, edit, re-serialize, or convert -- not a one-way string.

*********************
 turbohtml vs airium
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - airium
    - - Scope
      - Parse, build, query, edit, serialize, and convert one WHATWG tree
      - One-way HTML generation from Python, plus an HTML-to-airium code generator
    - - Feature breadth
      - ``E`` builder, CSS ``select``, ``xpath``, ``find``/``find_all``, ``to_markdown``/``to_text``, indent or minify
        layout
      - Context-manager tag authoring, auto-indented pretty-print, void-tag handling, reverse codegen
    - - Performance
      - Builds in a C arena and serializes in C (about 5x to 7x faster on the bench below)
      - Pure-Python string assembly with running indentation bookkeeping
    - - Typing
      - Ships ``.pyi`` stubs for the element, query, and serialize surface
      - Dynamic ``__getattr__`` tag dispatch; no per-tag types
    - - Dependencies
      - Self-contained C extension (needs a wheel or a build)
      - Pure Python, zero runtime dependencies, runs anywhere Python does
    - - Maintenance
      - Actively developed C core with a thin Python shim
      - Small, stable, narrowly-scoped project

Feature overlap
===============

The construction surface ports one-for-one:

- Elements with attributes: ``a.div(klass="card")`` becomes :data:`E.div({"class": "card"}) <turbohtml.build.E>`.
- Nested children: airium's ``with`` blocks become positional children on the ``E`` call.
- Text nodes: ``a("text")`` or the ``_t=`` kwarg becomes a plain string child.
- ``data-*`` and boolean attributes: airium's ``data_i=`` (underscore to dash) and turbohtml's ``{"data-i": "1"}`` /
  ``{"disabled": None}``.
- Void tags close themselves in both.
- Stringify: ``str(a)`` becomes :meth:`~turbohtml.Node.serialize`.
- Pretty-printed output: airium indents by default; turbohtml opts in with ``serialize(Html(layout=Indent(2)))``.

What turbohtml adds
===================

- The result is a real :class:`~turbohtml.Element`, not a string, so the same call that builds the markup leaves a tree
  you can walk and mutate with ``append``, ``insert``, ``remove``, and ``extend``.
- Query the built tree with CSS :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.xpath`, and
  :meth:`~turbohtml.Node.find`/``find_all`` -- airium has no query surface over what it emits.
- Convert with :meth:`~turbohtml.Node.to_markdown` and :meth:`~turbohtml.Node.to_text`.
- Round-trip: the same tree type parses arbitrary HTML back in, so generation and parsing share one API.
- The arena build and C serializer run about five to seven times faster than airium's Python assembly (see below).

What airium has that turbohtml does not
=======================================

- ``with``-block authoring. airium spells nesting with context managers; turbohtml nests by passing children as
  arguments. No context-manager equivalent -- restructure the ``with`` bodies into nested ``E`` calls.
- HTML-to-source codegen. ``python -m airium page.html`` emits airium Python that reproduces the input. turbohtml has no
  builder-code generator; it parses HTML into a tree, not into Python source. No equivalent.
- Zero-dependency, pure-Python install. airium needs no compiled extension and runs in any restricted or wheel-less
  environment. turbohtml ships a C extension, so it depends on a wheel or a local build.

Performance
===========

.. bench-table::
    :file: bench/airium.json

``E`` is roughly five to seven times faster than airium, and it hands back a real :class:`~turbohtml.Element`, not a
string, so the call that builds the markup also leaves a tree you can query, edit, and
re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the import and drop the ``Airium`` instance; ``E`` is a module-level singleton:

.. code-block:: python

    from turbohtml.build import E  # was: from airium import Airium

airium tracks structure by call depth inside ``with`` blocks; turbohtml tracks it in the tree:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `airium <https://pypi.org/project/airium/>`__
      - turbohtml
    - - ``a = Airium()``
      - ``E`` (module-level singleton, no instance)
    - - ``with a.div(klass="card"):`` then ``a("text")``
      - :data:`E.div({"class": "card"}, "text") <turbohtml.build.E>`; nest by passing children, not by call depth
    - - ``a.li(klass="item", **{"data-i": "1"})``
      - :data:`E.li({"class": "item", "data-i": "1"}) <turbohtml.build.E>`
    - - ``a("text")`` / ``_t="text"``
      - a plain string child
    - - ``str(a)``
      - :meth:`element.serialize() <turbohtml.Node.serialize>`
    - - ``str(a)`` with airium's default indentation
      - :meth:`element.serialize(Html(layout=Indent(2))) <turbohtml.Node.serialize>`

The same ``<ul>`` built both ways:

.. code-block:: python

    # airium
    from airium import Airium

    a = Airium()
    with a.ul(), a.li(klass="item", **{"data-i": "1"}):
        a("row")
    html = str(a)

    # turbohtml
    from turbohtml.build import E

    ul = E.ul(E.li({"class": "item", "data-i": "1"}, "row"))
    html = ul.serialize()

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
  Serialize the element you built, or append it under a parsed document when you need the full page shell. airium is the
  same here -- you write the doctype and shell yourself.
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``). There is no ``_t=`` / ``klass=`` kwarg convention: attributes are a mapping and use
  their real names (``"class"``, ``"data-i"``), so airium's underscore-to-dash rewriting does not apply.
- airium pretty-prints with newlines and indentation by default; ``E`` serializes compact markup. Pass
  ``serialize(Html(layout=Indent(2)))`` for indented output, or ``Html(layout=Minify())`` to minify.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
