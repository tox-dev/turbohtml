#############
 From markyp
#############

.. package-meta:: markyp-html volfpeter/markyp-html

`markyp <https://github.com/volfpeter/markyp>`_ assembles HTML in Python from the other direction than a parser: its
`markyp-html <https://github.com/volfpeter/markyp-html>`_ package provides one element class per tag, split across topic
modules (``markyp_html.block.div``, ``markyp_html.text.h1``, ``markyp_html.lists.li``), each taking children
positionally and attributes as trailing keywords, and stringifying the root renders the tree. It is a pure-Python
generator: markyp itself is the tiny element core, markyp-html the HTML tag layer, and a small ecosystem of companion
packages (``markyp-bootstrap``, ``markyp-highlightjs``, ``markyp-fontawesome``) wraps component libraries on top. A
``webpage(...)`` factory builds the full HTML5 document shell. It is used to emit reports, templated pages, and
component markup from Python without a template engine.

turbohtml covers the same construction ground with the terse :data:`turbohtml.build.E` builder, replacing both packages,
but every call returns a real :class:`~turbohtml.Element` in turbohtml's C-backed tree, so the markup you generate is
also a document you can query, edit, re-serialize, or convert -- not a one-way string.

*********************
 turbohtml vs markyp
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - markyp
    - - Scope
      - Parse, build, query, edit, serialize, and convert one WHATWG tree
      - One-way HTML generation from Python, one element class per tag
    - - Feature breadth
      - ``E`` builder, CSS ``select``, ``xpath``, ``find``/``find_all``, ``to_markdown``/``to_text``, indent or minify
        layout
      - Per-tag classes across topic modules, a ``webpage`` page-shell factory, pretty-printed output, companion
        component packages
    - - Performance
      - Builds in a C arena and serializes in C
      - Pure-Python string assembly (roughly a quarter faster on the microbenchmark below)
    - - Typing
      - Ships ``.pyi`` stubs for the element, query, and serialize surface
      - A concrete class per tag imported from its topic module; attributes stay untyped keywords
    - - Dependencies
      - Self-contained C extension (needs a wheel or a build)
      - Pure Python over the small ``markyp`` core; runs anywhere Python does
    - - Maintenance
      - Actively developed C core with a thin Python shim
      - Small, narrowly-scoped project with a companion-package ecosystem

Feature overlap
===============

The construction surface ports one-for-one:

- Elements with attributes: ``div(class_="card")`` becomes :data:`E.div({"class": "card"}) <turbohtml.build.E>`.
- Nested children: markyp's positional children become positional children on the ``E`` call.
- Text nodes: a string argument becomes a plain string child in both.
- ``data-*`` and boolean attributes: markyp's ``**{"data-i": "1"}`` and turbohtml's ``{"data-i": "1"}`` / ``{"disabled":
  None}``.
- Void tags close themselves in both.
- Stringify: ``str(root)`` becomes :meth:`~turbohtml.Node.serialize`.

What turbohtml adds
===================

- The result is a real :class:`~turbohtml.Element`, not a string, so the same call that builds the markup leaves a tree
  you can walk and mutate with ``append``, ``insert``, ``remove``, and ``extend``.
- Query the built tree with CSS :meth:`~turbohtml.Node.select`, :meth:`~turbohtml.Node.xpath`, and
  :meth:`~turbohtml.Node.find`/``find_all`` -- markyp has no query surface over what it emits.
- Convert with :meth:`~turbohtml.Node.to_markdown` and :meth:`~turbohtml.Node.to_text`.
- Round-trip: the same tree type parses arbitrary HTML back in, so generation and parsing share one API.
- No per-tag imports: any tag is named on ``E`` (or built via ``E("tag", ...)`` for a non-identifier name), so there is
  no ``markyp_html.block`` / ``markyp_html.text`` module to track.

What markyp has that turbohtml does not
=======================================

- Companion component packages. ``markyp-bootstrap``, ``markyp-highlightjs``, and ``markyp-fontawesome`` add typed
  wrappers for Bootstrap components, syntax highlighting, and icons on top of the element core. turbohtml builds raw
  elements only; there is no component library -- write the component markup as ``E`` calls yourself.
- Pure-Python, wheel-less install. markyp needs no compiled extension and runs in any restricted environment. turbohtml
  ships a C extension, so it depends on a wheel or a local build.

Performance
===========

.. bench-table::
    :file: bench/markyp.json

markyp renders roughly a quarter faster than ``E`` on this microbenchmark -- it concatenates strings as it goes -- but
the decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the
call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the per-tag imports for the single ``E`` singleton; there is no topic module to import from:

.. code-block:: python

    from turbohtml.build import E  # was: from markyp_html.block import div; from markyp_html.text import h1, p

markyp trails the attributes after positional children; turbohtml leads with a mapping and names any tag on ``E``:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `markyp <https://github.com/volfpeter/markyp-html>`__
      - turbohtml
    - - ``div(h1("Title"), p("body"), class_="card")``
      - :data:`E.div({"class": "card"}, E.h1("Title"), E.p("body")) <turbohtml.build.E>`; attributes are a leading
        mapping, and no per-tag import is needed
    - - ``li("text", class_="item", **{"data-i": "1"})``
      - :data:`E.li({"class": "item", "data-i": "1"}, "text") <turbohtml.build.E>`
    - - ``webpage(...)`` for the full page shell
      - :func:`turbohtml.build.document(title=..., body=[...]) <turbohtml.build.document>`
    - - ``str(root)``
      - :meth:`element.serialize() <turbohtml.Node.serialize>`

The same ``<div class="card">`` built both ways:

.. code-block:: python

    # markyp
    from markyp_html.block import div
    from markyp_html.text import h1, p

    card = div(h1("Title"), p("body"), class_="card")
    html = str(card)

    # turbohtml
    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    html = card.serialize()

``E("tag", ...)`` is the call form for a tag that is not a Python identifier (a custom element, say), and a list-valued
attribute joins on a space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

:func:`turbohtml.build.document` is the counterpart to markyp's ``webpage(...)``: it emits the doctype and the
``<html>``/``<head>``/``<body>`` shell around the content you pass, and hands back a :class:`~turbohtml.Document`:

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
  markyp's ``webpage(...)`` writes.
- markyp strips a trailing underscore (``class_`` to ``class``) but keeps other underscores, so hyphenated names need an
  unpacked dict already; ``E`` takes the real attribute name as a plain dict key everywhere (``"class"``, ``"data-i"``).
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- markyp pretty-prints -- a newline between children and a space after a bare tag name (``<h1 >``); ``E`` serializes
  compact markup. Pass ``serialize(Html(layout=Indent(2)))`` for indented output, or ``Html(layout=Minify())`` to
  minify.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
