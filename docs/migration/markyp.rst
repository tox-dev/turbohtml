#############
 From markyp
#############

.. package-meta:: markyp-html volfpeter/markyp-html

`markyp <https://github.com/volfpeter/markyp>`_ assembles HTML in Python from the other direction than a parser: its
`markyp-html <https://github.com/volfpeter/markyp-html>`_ package provides one element class per tag, split across topic
modules (``markyp_html.block.div``, ``markyp_html.text.h1``, ``markyp_html.lists.li``), each taking children
positionally and attributes as trailing keywords, and stringifying the root renders the tree. turbohtml replaces both
packages with the terse :data:`turbohtml.build.E` builder.

***************
 Why turbohtml
***************

turbohtml builds HTML with :class:`~turbohtml.Element` plus :meth:`~turbohtml.Node.serialize`, and
:data:`turbohtml.build.E` is a terse front end for it: ``E.<tag>(attrs, *children)``, where a leading mapping is the
attributes and each child is a node or a string that becomes text. The result is a real turbohtml tree, so the whole
edit, query, and serialize surface stays available and the markup you generate serializes by exactly the rules that
parse it back:

.. testcode::

    from turbohtml.build import E

    card = E.div({"class": "card"}, E.h1("Title"), E.p("body"))
    print(card.serialize())

.. testoutput::

    <div class="card"><h1>Title</h1><p>body</p></div>

``E`` assembles the fragment in turbohtml's arena and serializes it in C; markyp stays in Python. The same ``<ul>`` of
rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/markyp.json

markyp renders about a third faster than ``E`` on this microbenchmark -- it concatenates strings as it goes -- but the
decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the call
that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

markyp imports one class per tag from a topic module and trails the attributes; turbohtml names any tag on ``E`` and
leads with them:

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
      - build it: :data:`E.html(E.head(...), E.body(...)) <turbohtml.build.E>`, or append the fragment under a parsed
        document

``E("tag", ...)`` is the call form for a tag that is not a Python identifier (a custom element, say), and a list-valued
attribute joins on a space so a class list reads naturally:

.. testcode::

    from turbohtml.build import E

    print(E("my-card", {"class": ["card", "lg"]}, "hi").serialize())

.. testoutput::

    <my-card class="card lg">hi</my-card>

**********
 Pitfalls
**********

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype.
  Serialize the element you built, or append it under a parsed document when you need the full page shell.
- markyp strips a trailing underscore (``class_`` to ``class``) but keeps other underscores, so hyphenated names need an
  unpacked dict already; ``E`` takes the real attribute name as a plain dict key everywhere.
- markyp pretty-prints -- a newline between children and a space after a bare tag name (``<h1 >``); ``E`` serializes
  compact markup. Parse and re-serialize, or use a formatter, when you need indented output.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
