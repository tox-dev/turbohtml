##################
 From hyperpython
##################

.. package-meta:: hyperpython fabiommendes/hyperpython

`hyperpython <https://github.com/fabiommendes/hyperpython>`_ assembles HTML in Python from the other direction than a
parser: each element takes keyword attributes in a call and children in a ``[...]`` subscript
(``div(class_="card")[h1("Title")]``), and stringifying the root renders the tree. The library is unmaintained and no
longer imports on Python 3.11 or newer -- its ``sidekick`` dependency crashes at import time unless pinned to
``sidekick<0.7``, and it pins ``markupsafe<2`` -- so porting is also the unblock for current interpreters and dependency
stacks. turbohtml replaces it with the terse :data:`turbohtml.build.E` builder.

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

``E`` assembles the fragment in turbohtml's arena and serializes it in C; hyperpython stays in Python. The same ``<ul>``
of rows -- a class, a ``data`` attribute, and a text child apiece -- built both ways (hyperpython measured on its last
importable dependency pin):

.. bench-table::
    :file: bench/hyperpython.json

``E`` is about two and a half times faster than hyperpython, and the decisive difference is the result type: ``E`` hands
back a real :class:`~turbohtml.Element`, not a string, so the call that builds the markup also leaves a tree you can
query, edit, and re-:meth:`~turbohtml.Node.serialize`.

*************
 The renames
*************

hyperpython carries attributes in the call and children in a subscript; turbohtml passes both to one call:

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
- hyperpython mangles keyword arguments (``class_`` to ``class``, ``data_i`` to ``data-i``); ``E`` takes a plain
  mapping, so write the real attribute name as the dict key -- no underscore convention to remember.
- Both escape string children by default; hyperpython's ``Blob``/``safe`` escape hatches have no equivalent, so express
  raw markup as child elements (or parse it into nodes first).
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
