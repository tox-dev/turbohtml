#################
 From markupsafe
#################

.. package-meta:: markupsafe pallets/markupsafe

`markupsafe <https://markupsafe.palletsprojects.com>`_ is the safe-string library behind `Jinja2
<https://jinja.palletsprojects.com>`_, `WTForms <https://wtforms.readthedocs.io>`_, and `Werkzeug
<https://werkzeug.palletsprojects.com>`_. It ships a ``Markup`` string subclass and an ``escape`` function so a template
engine can interpolate untrusted values into HTML without escaping trusted markup twice. Its scope is deliberately
narrow: HTML escaping and a safe-string type, with a small C accelerator for the escape itself. It carries no parser and
no DOM; ``striptags`` and ``unescape`` are regex scans over the string.

``turbohtml.migration.markupsafe`` reimplements that whole surface -- ``Markup``, ``escape``, ``escape_silent``,
``soft_str``, and ``EscapeFormatter`` -- fully type annotated, with the escape and every ``Markup`` operation backed by
turbohtml's C extension. Because turbohtml already has a WHATWG tokenizer and HTML5 reference table, ``striptags`` and
``unescape`` run on that real parser rather than a regex, so they resolve references and tag boundaries the regex scan
can miss.

*************************
 turbohtml vs markupsafe
*************************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - markupsafe
    - - Scope
      - Safe-string escaping as one module of a full HTML parser/serializer stack
      - Focused HTML escaping and a ``Markup`` safe-string type
    - - Feature breadth
      - Full markupsafe surface plus tokenizer-backed ``striptags``/``unescape`` and the rest of turbohtml
      - ``Markup``, ``escape``, ``escape_silent``, ``soft_str``, ``EscapeFormatter``
    - - Performance
      - C escape 2-3x faster on the small clean strings autoescape interpolates (see below)
      - C-accelerated ``escape``; regex ``striptags``/``unescape``
    - - Typing
      - Fully annotated, ships type stubs
      - Typed (ships ``py.typed``)
    - - Dependencies
      - Single self-contained package, C extension bundled
      - Single self-contained package, optional C speedup
    - - Maintenance
      - Active, part of the turbohtml project
      - Active, maintained by the Pallets team

Feature overlap
===============

The public surface ports 1:1 -- the names, signatures, and escaping semantics match, so a Jinja2, WTForms, or Werkzeug
project only changes the import:

- :class:`~turbohtml.migration.markupsafe.Markup` -- the ``str`` subclass, including the ``__html__`` protocol, the
  composing operators (``+``, ``%``, ``*``), and the full text-returning ``str`` method surface kept as ``Markup``.
- :func:`~turbohtml.migration.markupsafe.escape` -- returns a ``Markup``, honors ``__html__``, leaves an existing
  ``Markup`` untouched.
- :func:`~turbohtml.migration.markupsafe.escape_silent` -- like ``escape`` but maps ``None`` to an empty ``Markup``.
- :func:`~turbohtml.migration.markupsafe.soft_str` -- coerces to ``str`` without escaping.
- :class:`~turbohtml.migration.markupsafe.EscapeFormatter` -- the subclassable ``string.Formatter`` behind
  :meth:`~turbohtml.migration.markupsafe.Markup.format`, cooperating through ``super()`` for a template sandbox.
- :meth:`~turbohtml.migration.markupsafe.Markup.striptags` and :meth:`~turbohtml.migration.markupsafe.Markup.unescape`.

What turbohtml adds
===================

- The escape and every ``Markup`` operation run in C, so autoescaping stays a single C call and the small-string escape
  runs 2-3x faster than markupsafe's own C escape.
- :meth:`~turbohtml.migration.markupsafe.Markup.striptags` parses with the WHATWG tokenizer instead of scanning for
  ``<``, so a comment containing ``<`` cannot end tag removal early and references resolve during stripping.
- :meth:`~turbohtml.migration.markupsafe.Markup.unescape` uses the full HTML5 named-reference table, resolving
  references the regex scan can miss.
- Everything else turbohtml is: the same ``escape`` and tokenizer primitives back parsing, serialization, and the
  selector engine, so a project that adopts the safe-string also gets a parser under one name-per-concept API.

What markupsafe has that turbohtml does not
===========================================

- **Drop-in package identity.** turbohtml does not register itself as the ``markupsafe`` distribution, so it will not
  transparently replace the installed package for code that does ``import markupsafe``. Adoption is an explicit
  per-project import swap. No equivalent: change the import line.
- **The ``soft_unicode`` alias** was removed in markupsafe 3.0 and is absent here too; use
  :func:`~turbohtml.migration.markupsafe.soft_str`.

Performance
===========

.. bench-table::
    :file: bench/markupsafe.json

The escape runs two to three times faster than markupsafe's own C escape on the small, mostly-clean strings a template
engine interpolates under autoescape, and the other ``Markup`` operations stay ahead too -- ``striptags`` and
``unescape`` run on turbohtml's tokenizer and HTML5 reference resolution rather than markupsafe's regex scan, and the
composing operations (``format``, ``join``) escape each untrusted operand through the same C ``escape``.

****************
 How to migrate
****************

A Jinja2, WTForms, or Werkzeug project changes only the import line:

.. code-block:: python

    # markupsafe
    from markupsafe import Markup, escape, escape_silent, soft_str, EscapeFormatter

    # turbohtml
    from turbohtml.migration.markupsafe import (
        Markup,
        escape,
        escape_silent,
        soft_str,
        EscapeFormatter,
    )

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `markupsafe <https://markupsafe.palletsprojects.com/>`__
      - turbohtml
    - - :class:`markupsafe.Markup`
      - :class:`~turbohtml.migration.markupsafe.Markup`
    - - :func:`markupsafe.escape`
      - :func:`~turbohtml.migration.markupsafe.escape`
    - - :func:`markupsafe.escape_silent`
      - :func:`~turbohtml.migration.markupsafe.escape_silent`
    - - :func:`markupsafe.soft_str`
      - :func:`~turbohtml.migration.markupsafe.soft_str`
    - - ``EscapeFormatter``
      - :class:`~turbohtml.migration.markupsafe.EscapeFormatter`
    - - :meth:`markupsafe.Markup.striptags`
      - :meth:`~turbohtml.migration.markupsafe.Markup.striptags`
    - - :meth:`markupsafe.Markup.unescape`
      - :meth:`~turbohtml.migration.markupsafe.Markup.unescape`
    - - ``Markup.format``, ``Markup.join``
      - :meth:`~turbohtml.migration.markupsafe.Markup.format`, :meth:`~turbohtml.migration.markupsafe.Markup.join`

``escape`` returns a :class:`~turbohtml.migration.markupsafe.Markup` with the same numeric quote references markupsafe
emits, honors the ``__html__`` protocol, and leaves an existing ``Markup`` untouched. ``Markup`` overrides the full
:class:`str` method surface, so a value that flows through a template filter such as ``upper`` or ``replace`` stays a
``Markup`` and autoescaping does not escape it a second time. The operations that combine text (``+``, ``%``,
:meth:`~turbohtml.migration.markupsafe.Markup.format`, :meth:`~turbohtml.migration.markupsafe.Markup.join`, ``replace``,
...) escape their untrusted operands:

.. testcode::

    from turbohtml.migration.markupsafe import Markup, escape, escape_silent

    print(escape('<a href="x">Tom & Jerry</a>'))
    print(Markup("<b>{}</b>").format("<i>"))
    print(Markup("<b>safe</b>").upper())  # str methods keep the Markup, so it is not re-escaped
    print(escape_silent(None) == Markup(""))

.. testoutput::

    &lt;a href=&#34;x&#34;&gt;Tom &amp; Jerry&lt;/a&gt;
    <b>&lt;i&gt;</b>
    <B>SAFE</B>
    True

**********************
 Gotchas and pitfalls
**********************

- Two methods are upgrades rather than reimplementations: :meth:`~turbohtml.migration.markupsafe.Markup.striptags` and
  :meth:`~turbohtml.migration.markupsafe.Markup.unescape` run on turbohtml's tokenizer and HTML5 reference resolution.
  They resolve references markupsafe's regex stripping can miss and treat comments as real nodes, so the plain text a
  page produces can differ where the input contains comments, malformed tags, or named references.
- ``striptags`` collapses runs of whitespace to single spaces, matching markupsafe; do not rely on the exact incidental
  whitespace of either implementation.
- The ``soft_unicode`` alias that markupsafe 3.0 removed is absent here too; use
  :func:`~turbohtml.migration.markupsafe.soft_str`.
- turbohtml does not register itself as ``markupsafe``, so adoption stays an explicit per-project import rather than a
  transparent replacement of the installed package.
