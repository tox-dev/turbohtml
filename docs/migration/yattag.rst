#############
 From yattag
#############

.. package-meta:: yattag leforestier/yattag

`yattag <https://www.yattag.org>`_ assembles HTML in Python from the other direction than a parser. You unpack ``doc,
tag, text = Doc().tagtext()``, open each element as a ``with tag(...)`` block, call ``text(...)`` for escaped content
and ``line(...)`` for an open-text-close shorthand, then read the string back with ``doc.getvalue()``. Attributes are
keyword arguments (``klass`` for ``class``) or ``("data-i", "1")`` tuples for names that are not Python identifiers.
Beyond plain generation it scaffolds forms: ``Doc(defaults=..., errors=...)`` with
``doc.input``/``doc.textarea``/``doc.select`` fills field values from a defaults dict and wraps invalid fields with
error text, which makes it a common pick for server-rendered pages and form redisplay in Flask and similar stacks
without a template language.

turbohtml covers the same generation ground with the terse :data:`turbohtml.build.E` builder, but the value it hands
back is a real :class:`~turbohtml.Element` in a parsed tree rather than an accumulated string, so the whole query, edit,
and serialize surface stays available on what you just built and the markup serializes by exactly the WHATWG rules that
parse it back.

*********************
 turbohtml vs yattag
*********************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - yattag
    - - Scope
      - Full WHATWG parser, serializer, builder, selectors, sanitizer, converters
      - HTML/XML generation only, from Python
    - - Feature breadth
      - ``E`` builder over a real tree: CSS/XPath query, edit, ``to_markdown``, compact/indent/minify layouts
      - Context-manager nesting, ``line``/``stag``/``asis`` helpers, ``indent`` pretty-printer, form fill and error
        wrapping
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

- ``with tag("div", klass="card"): ...`` -> :data:`E.div({"class": "card"}, ...) <turbohtml.build.E>`; the attribute
  mapping leads, the children follow in the same call instead of a context-manager block.
- ``line("h1", "Title", klass="t")`` -> :data:`E.h1({"class": "t"}, "Title") <turbohtml.build.E>`; the open-text-close
  shorthand is the default builder call.
- ``text("body")`` -> a plain string child, which ``E`` escapes into a :class:`~turbohtml.Text` node just as ``text``
  escapes into the buffer.
- ``doc.stag("img", src="a.png")`` -> :data:`E.img({"src": "a.png"}) <turbohtml.build.E>`; the serializer omits the
  close tag for a void element by the WHATWG rules, so no explicit self-closing call is needed.
- A non-identifier attribute name such as ``("data-i", "1")`` -> the plain dict key ``{"data-i": "1"}``, with no
  ``klass`` alias or tuple form to remember.
- A non-identifier tag name -> :data:`E("my-card", ...) <turbohtml.build.E>`, the call form.
- A class token list joins on a space: ``klass="card lg"`` -> ``E.div({"class": ["card", "lg"]})``.

What turbohtml adds
===================

- The result is an ordinary :class:`~turbohtml.Element`, so :meth:`~turbohtml.Node.select`,
  :meth:`~turbohtml.Node.xpath`, :meth:`~turbohtml.Node.find`, :meth:`~turbohtml.Node.to_markdown`, and the full edit
  API are available on what you built. yattag hands back only a string from ``doc.getvalue()``.
- The markup serializes by exactly the WHATWG rules that parse it back, so a fragment round-trips through
  :func:`turbohtml.parse` unchanged; yattag hand-assembles its own string.
- ``E`` assembles the fragment in turbohtml's arena and serializes it in C, where yattag stays in Python.
- Layout control beyond yattag's ``indent``: a compact default, an :class:`~turbohtml.Indent` pretty-print, and a
  :class:`~turbohtml.Minify` layout (whitespace collapse, optional-tag omission, JS/CSS minify), all through one
  :meth:`~turbohtml.Node.serialize` call.
- Full type annotations with a ``py.typed`` marker.
- You can :func:`turbohtml.parse` real markup and append an ``E`` fragment under it, mixing parsing and building in one
  tree.

What yattag has that turbohtml does not
=======================================

- Context-manager nesting: ``with tag("div"): text("hi")`` collects everything written inside the block. ``E`` has no
  context-manager form. Workaround: pass children as positional arguments, or build bottom-up and
  :meth:`~turbohtml.Element.append`.
- Form fill and validation: ``Doc(defaults=..., errors=...)`` with ``doc.input``/``doc.textarea``/``doc.select`` fills a
  field's value from the defaults dict and wraps an invalid field with its error text. ``E`` has no form-aware builder.
  Workaround: set the ``value``/``checked`` attributes yourself from the dict, and build the error markup as ordinary
  elements.
- ``doc.asis("<b>x</b>")`` injects unescaped HTML into the buffer. ``E`` always escapes a string into a
  :class:`~turbohtml.Text` node. Workaround: :func:`turbohtml.parse` the raw markup and append the resulting nodes,
  since turbohtml is a real parser.
- ``doc.attr(...)`` adds attributes to the currently open tag from inside its block. ``E`` sets every attribute in the
  leading mapping at construction, so there is no open-tag-to-mutate. Workaround: assemble the full attribute dict
  before the ``E`` call, or set attributes on the returned :class:`~turbohtml.Element` afterward.
- ``Doc`` also builds XML with arbitrary tags and no void-element or HTML-serialization rules. ``E`` builds an HTML tree
  and serializes by WHATWG rules. Workaround: for XML output, build the tree and serialize it as HTML, accepting the
  HTML void-element and escaping conventions.

Performance
===========

``E`` runs on par with yattag. The same ``<ul>`` of rows -- a class, a ``data`` attribute, and a text child apiece --
built both ways:

.. bench-table::
    :file: bench/yattag.json

The decisive difference is the result type: ``E`` hands back a real :class:`~turbohtml.Element`, not a string, so the
call that builds the markup also leaves a tree you can query, edit, and re-:meth:`~turbohtml.Node.serialize`.

****************
 How to migrate
****************

Swap the ``Doc().tagtext()`` unpacking for the single ``E`` builder:

.. code-block:: python

    # yattag
    from yattag import Doc

    doc, tag, text = Doc().tagtext()

    # turbohtml
    from turbohtml.build import E

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `yattag <https://www.yattag.org/>`__
      - turbohtml
    - - ``with tag("div", klass="card"): ...`` and ``text("x")``
      - :data:`E.div({"class": "card"}, "x") <turbohtml.build.E>`; build children inline instead of opening a tag scope
    - - ``line("h1", "Title", klass="t")``
      - :data:`E.h1({"class": "t"}, "Title") <turbohtml.build.E>`
    - - ``doc.stag("img", src="a.png")``
      - :data:`E.img({"src": "a.png"}) <turbohtml.build.E>`
    - - ``("data-i", "1")`` tuple attribute
      - the plain dict key :data:`E.li({"data-i": "1"}) <turbohtml.build.E>`
    - - ``tag(...)`` for a non-identifier tag name
      - :data:`E("tag", ...) <turbohtml.build.E>`, the call form
    - - ``doc.getvalue()`` / ``indent(doc.getvalue())``
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

**********************
 Gotchas and pitfalls
**********************

- ``E`` builds a fragment, not a document: there is no implicit ``<html>``/``<head>``/``<body>`` wrapper and no doctype.
  Serialize the element you built, or append it under a parsed document when you need the full page shell.
- A leading mapping is always read as attributes; to start an element with literal text, pass the string first
  (``E.p("text", E.b("bold"))``).
- ``E`` escapes strings into text nodes, so ``E.div("<b>")`` serializes as ``&lt;b&gt;``. yattag's ``asis()`` did no
  escaping; to inject real markup, :func:`turbohtml.parse` it and append the nodes.
- turbohtml serializes compact by default, where yattag's ``indent()`` pretty-prints. Pass ``Html(layout=Indent())`` to
  :meth:`~turbohtml.Node.serialize` for indented output.
- Write attribute names as plain dict keys (``{"class": "card", "data-i": "1"}``), not yattag's ``klass`` keyword or
  ``("name", "value")`` tuple conventions.
- The result is an ordinary :class:`~turbohtml.Element`, so the whole edit and query surface (``append``, ``find``,
  ``select``, ``serialize``, ``to_markdown``) is available -- the builder only saves the construction boilerplate.
