############
 From jsmin
############

.. image:: https://static.pepy.tech/badge/jsmin/month
    :alt: jsmin monthly downloads
    :target: https://pepy.tech/project/jsmin

`jsmin <https://github.com/tikitu/jsmin>`_ is the Python port of Douglas Crockford's ``jsmin``: a character-level state
machine that walks the source one byte at a time and removes comments and insignificant whitespace. Like the original it
does not rename a binding or fold a constant — it only deletes bytes that the tokenizer proves are optional — so its
output stays close to the source size. Being a pure-Python character loop it is slow on large files, and because it
tracks state by hand rather than parsing it can mishandle the regex-versus-division ``/`` in some inputs. It ships a
single ``jsmin(str) -> str`` entry point and nothing else.

:func:`~turbohtml.clean.minify_js` covers the same ground with a real front end: it lexes and parses to an arena AST in
C, renames function-local bindings, folds constants, and prints the result. It always does at least what jsmin does
(strip whitespace, comments and number literals) and, with its optional passes on, shrinks well past what a
whitespace-only tool can reach.

********************
 turbohtml vs jsmin
********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - jsmin
    - - Scope
      - Full HTML parser plus a standalone JS minifier and inline ``<script>`` minification
      - JavaScript-only, whitespace and comment removal
    - - Feature breadth
      - Whitespace, comment and number-literal folding always on; optional local-binding renaming and constant folding
        with dead-code elimination
      - Whitespace and comment removal only; no renaming, no folding
    - - Performance
      - Parse-and-optimize front end in C, an order of magnitude faster than jsmin's Python loop
      - Pure-Python character loop, slow on large files
    - - Typing
      - Typed API with bundled stubs; ``JSMinify`` is a frozen dataclass
      - Untyped ``jsmin(str) -> str``
    - - Dependencies
      - Self-contained C extension
      - Pure Python, no dependencies
    - - Maintenance
      - Actively developed
      - Stable port of the Crockford algorithm, infrequent releases

Feature overlap
===============

The shared surface ports 1:1 — a single call that takes a JavaScript string and returns a smaller one:

- ``jsmin(source)`` maps directly to :func:`turbohtml.clean.minify_js(source) <turbohtml.clean.minify_js>`.
- Whitespace and comment stripping is unconditional in both, so turbohtml is a drop-in for the plain call.
- Neither tool needs a browser, DOM, or Node runtime; both operate on the string in-process.

What turbohtml adds
===================

- **Local-binding renaming.** ``JSMinify(mangle=True)`` (the default) renames bindings local to a function to short
  names, the bulk of the size win. jsmin never renames anything.
- **Constant folding and dead-code elimination.** ``JSMinify(fold=True)`` (the default) evaluates constant expressions
  and drops unreachable code. jsmin does neither.
- **Number-literal minification.** turbohtml rewrites numeric literals to their shortest form unconditionally; jsmin
  leaves them as written.
- **A real parse, not a character loop.** Because turbohtml tokenizes against ECMA-262 it distinguishes a regex literal
  from a division operator by grammar rather than by hand-tracked state.
- **Inline ``<script>`` minification.** Pass a :class:`~turbohtml.clean.JSMinify` as :class:`~turbohtml.Minify`'s
  ``minify_js`` and ``<script>`` bodies are minified during HTML serialization. Only scripts whose ``type`` marks them
  as JavaScript are rewritten; a ``type="application/json"`` or ``importmap`` payload is left byte-for-byte. jsmin only
  ever sees a bare script string you extract yourself.
- **Typed surface.** A bundled stub and the frozen ``JSMinify`` dataclass give static checkers full signatures.

What jsmin has that turbohtml does not
======================================

- **Pure-Python, zero-dependency install.** jsmin is a single Python module with no compiled extension. turbohtml ships
  a C extension; if a pure-Python wheel is a hard requirement, jsmin still fits where turbohtml cannot.

Performance
===========

On the library ladder (``python -m bench minify-js``) turbohtml runs about three to five times faster than jsmin and its
output is up to half the size, because jsmin only deletes whitespace where turbohtml renames every local binding and
runs the structural folds. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/jsmin.json

Unlike the regex-based :doc:`rjsmin <rjsmin>`, jsmin has no speed advantage to trade for its simpler output, so there is
no case where it beats :func:`~turbohtml.clean.minify_js`.

****************
 How to migrate
****************

Swap the import and the call. jsmin exposes one function; turbohtml exposes the same shape plus an options object.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - jsmin
      - turbohtml
    - - ``from jsmin import jsmin``
      - ``import turbohtml``
    - - ``jsmin(source)``
      - ``turbohtml.clean.minify_js(source)``
    - - (whitespace/comments only)
      - ``turbohtml.clean.minify_js(source, JSMinify(mangle=False, fold=False))`` for the closest whitespace-only match
    - - (renaming, no equivalent)
      - ``turbohtml.clean.minify_js(source, JSMinify(mangle=False))`` keeps readable names while still folding

.. code-block:: python

    # jsmin
    from jsmin import jsmin

    jsmin(source)  # Crockford whitespace/comment pass, in Python

    # turbohtml
    import turbohtml

    turbohtml.clean.minify_js(source)  # smaller output, in C

To minify inline ``<script>`` content — which jsmin leaves entirely to you — pass a :class:`~turbohtml.clean.JSMinify`
to the HTML serializer instead of extracting the script by hand:

.. code-block:: python

    import turbohtml
    from turbohtml import Html, Minify
    from turbohtml.clean import JSMinify

    doc = turbohtml.parse("<p>hi<script>function plus(a, b) { return a + b; }</script>")
    doc.serialize(Html(layout=Minify(minify_js=JSMinify())))

**********************
 Gotchas and pitfalls
**********************

- **Renaming is on by default.** ``turbohtml.clean.minify_js(source)`` renames local bindings, which jsmin never does.
  If a consumer reflects on function-local variable names (rare), pass ``JSMinify(mangle=False)``. Top-level names are
  global and are never renamed regardless of the setting.
- **Unparsable input raises by default.** jsmin emits something for any string; the standalone
  :func:`~turbohtml.clean.minify_js` raises :class:`ValueError` on a construct its parser does not handle. Pass
  ``on_error="passthrough"`` for jsmin's never-fail behavior -- the source comes back verbatim instead of raising. The
  inline ``<script>`` path already applies that fallback and never raises.
- **Number literals change form.** turbohtml rewrites numeric literals to their shortest equivalent; jsmin leaves them
  verbatim. The value is preserved, but a byte-for-byte diff against jsmin output will differ here even with the
  optional passes off.
- **Script ``type`` gates the inline path.** Only scripts marked as JavaScript are rewritten during HTML serialization;
  ``application/json`` and ``importmap`` payloads are left untouched. jsmin has no such awareness because it never sees
  the surrounding document.
