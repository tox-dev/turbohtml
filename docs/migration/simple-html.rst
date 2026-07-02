##################
 From simple-html
##################

.. package-meta:: simple-html keithasaurus/simple_html

`simple-html <https://github.com/keithasaurus/simple_html>`_ assembles HTML in Python from the other direction than a
parser: each element is a tag called with an attribute dict first and children after (``div({"class": "card"},
h1("Title"))``), and ``render`` walks the resulting tuples into a string. turbohtml replaces it with the terse
:data:`turbohtml.build.E` builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; simple-html stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways:

.. bench-table::
    :file: bench/simple-html.json

simple-html renders about three times faster than ``E`` on this microbenchmark -- it concatenates tuples straight into a
string -- but the decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a
string, so the call that builds the markup also leaves a tree you can query, edit, and
re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

simple-html and turbohtml share the call shape -- attributes lead, children follow -- so most calls port by swapping the
import and the render step:

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

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype,
  where simple-html ships a ``DOCTYPE_HTML5`` constant to prepend. Serialize the element you built, or append it under a
  parsed document when you need the full page shell.
- Both escape string children by default; simple-html's ``SafeString`` escape hatch has no equivalent, so express raw
  markup as child elements (or parse it into nodes first).
- simple-html suffixes tags that shadow keywords (``del_``); ``E.del_`` would build a literal ``<del_>``, so use the
  call form ``E("del", ...)`` for those tags.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
