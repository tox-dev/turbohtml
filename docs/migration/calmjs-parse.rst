###################
 From calmjs.parse
###################

.. image:: https://static.pepy.tech/badge/calmjs.parse/month
    :alt: calmjs.parse monthly downloads
    :target: https://pepy.tech/project/calmjs.parse

`calmjs.parse <https://github.com/calmjs/calmjs.parse>`_ is a full JavaScript front end in pure Python. It lexes and
parses ES5 to a concrete AST (``es5(source)``), walks and rewrites that tree through its ``walkers`` and ``asttypes``
modules, and prints it back with either ``pretty_print`` (readable, re-indented) or ``minify_print`` — whose
``obfuscate=True`` renames local identifiers the way a real minifier does. It can also emit source maps for the printed
output. Built for the calmjs Node.js integration toolchain, it is the most capable of the PyPI minifiers, but it parses
only ES5: modern syntax (arrow functions, ``let``/``const``, classes, template literals) raises a syntax error, and the
pure-Python parse is slow.

turbohtml covers the minification slice of that surface. :func:`~turbohtml.minify_js` is the same parse-and-rename
approach implemented in C, matching or beating calmjs.parse's output size while running about two orders of magnitude
faster and accepting a much larger slice of the language. It does not expose an AST, a pretty-printer, or source maps —
it is a minifier, not a general JavaScript toolkit.

***************************
 turbohtml vs calmjs.parse
***************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - calmjs.parse
    - - Scope
      - JavaScript minifier only (plus inline ``<script>`` minification in HTML)
      - Full ES5 front end: lexer, parser, AST, walkers, pretty-printer, minifier, source maps
    - - Feature breadth
      - Whitespace/comment/number folding, identifier mangling, constant folding, dead-code elimination
      - AST access and rewriting, beautify and minify printers, identifier obfuscation, source maps
    - - Performance
      - Native C, single-digit milliseconds on the corpus below
      - Pure Python, hundreds of milliseconds on the same inputs
    - - Typing
      - Typed public API (:func:`~turbohtml.minify_js`, :class:`~turbohtml.JSMinify`)
      - Untyped
    - - Dependencies
      - None (ships the C extension)
      - ``ply`` (parser generator)
    - - Maintenance
      - Actively developed alongside the turbohtml serializer
      - Maintained, stable, ES5-scoped

Feature overlap
===============

The minification path ports 1:1:

- Minify a source string to the shortest equivalent program: ``minify_print(es5(source))`` maps to
  :func:`turbohtml.minify_js(source, JSMinify(mangle=False)) <turbohtml.minify_js>`.
- Rename local identifiers: calmjs.parse's ``minify_print(es5(source), obfuscate=True)`` maps to turbohtml's default
  ``mangle=True``.
- Both fold whitespace, drop comments, and shorten numeric literals unconditionally.

What turbohtml adds
===================

- Modern JavaScript: arrow functions, ``let``/``const``, classes, and template literals minify instead of raising a
  syntax error.
- Constant folding and dead-code elimination (:class:`~turbohtml.JSMinify` ``fold=True``), beyond calmjs.parse's
  whitespace-and-rename minification.
- A native-C pipeline that runs about forty to eighty times faster on the corpus below.
- Inline-``<script>`` minification inside a full HTML document via ``Minify(minify_js=JSMinify())`` on
  :meth:`~turbohtml.Node.serialize` — no separate JS toolchain step.
- A typed surface: :func:`~turbohtml.minify_js` and the frozen :class:`~turbohtml.JSMinify` options object.

What calmjs.parse has that turbohtml does not
=============================================

- A parsed AST. ``es5(source)`` returns a walkable, mutable concrete syntax tree; turbohtml exposes no JavaScript AST,
  only the minified string. No equivalent — use calmjs.parse (or a Node-based tool) when you need to inspect or
  programmatically rewrite JavaScript.
- A pretty-printer. ``pretty_print`` re-indents and beautifies; turbohtml only shrinks. No equivalent.
- Source maps. calmjs.parse can emit a source map for its printed output; turbohtml's minifier does not. No equivalent.
- AST walkers and node types (``calmjs.parse.walkers``, ``calmjs.parse.asttypes``) for custom analysis or transformation
  passes. No equivalent.

Performance
===========

On the ES5 library ladder turbohtml reaches the smaller output, and the speed gap is large: on the same machine
(``python -m bench minify-js``) calmjs.parse takes hundreds of milliseconds where turbohtml takes single-digit
milliseconds. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/calmjs-parse.json

turbohtml beats calmjs.parse on size everywhere, at forty to eighty times less time and on modern JavaScript that
calmjs.parse rejects outright, so for any build where minify time or modern syntax is in the loop turbohtml is the
practical choice.

****************
 How to migrate
****************

Swap the parse-then-print pair for the single minify call:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - calmjs.parse
      - turbohtml
    - - ``from calmjs.parse import es5``
      - ``import turbohtml``
    - - ``from calmjs.parse.unparsers.es5 import minify_print``
      - (none needed)
    - - ``minify_print(es5(src), obfuscate=True)``
      - ``turbohtml.minify_js(src)``
    - - ``minify_print(es5(src))``
      - ``turbohtml.minify_js(src, JSMinify(mangle=False))``
    - - ``pretty_print(es5(src))``
      - no equivalent (turbohtml only minifies)

.. code-block:: python

    # calmjs.parse
    from calmjs.parse import es5
    from calmjs.parse.unparsers.es5 import minify_print

    minify_print(es5(source), obfuscate=True)  # ES5 only, in Python

    # turbohtml
    import turbohtml

    turbohtml.minify_js(source)  # in C, modern syntax too

To keep readable local names while still folding whitespace and comments, pass a :class:`~turbohtml.JSMinify` with
``mangle=False``:

.. code-block:: python

    from turbohtml import minify_js, JSMinify

    minify_js(source, JSMinify(mangle=False))

**********************
 Gotchas and pitfalls
**********************

- Error type. calmjs.parse raises ``ECMASyntaxError`` on a script it cannot parse; turbohtml's standalone
  :func:`~turbohtml.minify_js` raises :class:`ValueError` (with the construct, byte offset, and offending token), and
  :class:`TypeError` if ``source`` is not a :class:`str`. Inside HTML the inline-``<script>`` pass instead leaves an
  unminifiable script verbatim rather than raising.
- Modern syntax is a hard stop in calmjs.parse (a syntax error), where turbohtml minifies it. A migration that was
  silently skipping ES6+ files because they failed to parse will start minifying them.
- No AST after the call. If existing code parses with ``es5()`` and then walks or mutates the tree, that path has no
  turbohtml counterpart; only the minified string is returned.
- Obfuscation scope. calmjs.parse's ``obfuscate=True`` renames local bindings and leaves globals alone unless
  ``obfuscate_globals`` is set; turbohtml's ``mangle`` likewise renames only locals, so global-name behavior matches by
  default.
