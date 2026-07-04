#############
 From rjsmin
#############

.. package-meta:: rjsmin ndparker/rjsmin

`rjsmin <https://github.com/ndparker/rjsmin>`_ minifies JavaScript with a single regular-expression substitution: one
pass strips comments and insignificant whitespace and nothing else. It ships a compiled ``_rjsmin`` speedup with a
pure-Python fallback, so the regex runs fast even on large files, and it is a common build-step dependency where the
only goal is to drop bytes the tokenizer proves are optional. Because a regex never renames a binding or folds a
constant, the output stays close to the source size. Its whole surface is one function, ``jsmin(script,
keep_bang_comments=False) -> str``, where ``keep_bang_comments`` preserves ``/*! ... */`` license blocks.

:func:`~turbohtml.clean.minify_js` covers the same ground with a real front end: it lexes and parses to an arena AST in
C, renames function-local bindings, folds constants, and prints the result. It always does at least what rjsmin does
(strip whitespace and comments) and, with its optional passes on, shrinks well past what a whitespace-only substitution
can reach. The HTML-embedded case rjsmin leaves to you — it only ever sees a script string you extract — is built in:
pass a :class:`~turbohtml.clean.JSMinify` as :class:`~turbohtml.Minify`'s ``minify_js`` and inline ``<script>`` content
is minified during serialization.

*********************
 turbohtml vs rjsmin
*********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - rjsmin
    - - Scope
      - Full HTML parser plus a standalone JS minifier and inline ``<script>`` minification
      - JavaScript-only, whitespace and comment removal by one regex substitution
    - - Feature breadth
      - Whitespace, comment and number-literal folding always on; optional local-binding renaming and constant folding
        with dead-code elimination
      - Whitespace and comment removal only; no renaming, no folding; ``keep_bang_comments`` to retain ``/*! ... */``
    - - Performance
      - Parse-and-optimize front end in C; single-digit milliseconds on the library ladder
      - One regex pass with a C speedup; a fraction of a millisecond, faster than a parse but shrinks far less
    - - Typing
      - Typed API with bundled stubs; ``JSMinify`` is a frozen dataclass
      - Untyped ``jsmin(script, keep_bang_comments=False) -> str``
    - - Dependencies
      - Self-contained C extension
      - Pure Python with an optional compiled ``_rjsmin`` speedup, no third-party dependencies
    - - Maintenance
      - Actively developed
      - Stable and maintained, infrequent releases

Feature overlap
===============

The shared surface ports 1:1 — a single call that takes a JavaScript string and returns a smaller one:

- ``rjsmin.jsmin(source)`` maps directly to :func:`turbohtml.clean.minify_js(source) <turbohtml.clean.minify_js>`.
- Whitespace and ordinary-comment stripping is unconditional in both; the one difference is that turbohtml keeps ``/*!
  ... */`` license banners by default (see below), where the plain rjsmin call drops them.
- Neither tool needs a browser, DOM, or Node runtime; both operate on the string in-process.

What turbohtml adds
===================

- **Local-binding renaming.** ``JSMinify(mangle=True)`` (the default) renames bindings local to a function to short
  names, the bulk of the size win. rjsmin's regex never renames anything.
- **Constant folding and dead-code elimination.** ``JSMinify(fold=True)`` (the default) evaluates constant expressions
  and drops unreachable code. rjsmin does neither.
- **Number-literal minification.** turbohtml rewrites numeric literals to their shortest form unconditionally; rjsmin
  leaves them as written.
- **A real parse, not a regex.** Because turbohtml tokenizes against ECMA-262 it distinguishes a regex literal from a
  division operator by grammar rather than by regex heuristics, so no crafted ``/`` sequence can be misread.
- **Inline ``<script>`` minification.** Pass a :class:`~turbohtml.clean.JSMinify` as :class:`~turbohtml.Minify`'s
  ``minify_js`` and ``<script>`` bodies are minified during HTML serialization. Only scripts whose ``type`` marks them
  as JavaScript are rewritten; a ``type="application/json"`` or ``importmap`` payload is left byte-for-byte. rjsmin only
  ever sees a bare script string you extract yourself.
- **License-comment preservation, on by default.** turbohtml keeps ``/*! ... */`` bang comments and any comment carrying
  an ``@license`` or ``@preserve`` annotation, emitting them byte-exact as a leading banner so a copyright or license
  header survives minification; every other comment is dropped. rjsmin keeps bang comments only when called with
  ``keep_bang_comments=True``, and this matches turbohtml's CSS minifier, which keeps ``/*! ... */`` the same way.
- **Typed surface.** A bundled stub and the frozen ``JSMinify`` dataclass give static checkers full signatures.

What rjsmin has that turbohtml does not
=======================================

- **A cheaper whitespace-only pass.** rjsmin's single regex substitution is faster than a full parse and is the lighter
  tool when the only requirement is stripping space as cheaply as possible. turbohtml parses, so it spends more time to
  produce a smaller result.
- **Pure-Python-only install.** rjsmin runs from its pure-Python fallback with no compiled extension at all. turbohtml
  ships a C extension; if a build must avoid compiled code entirely, rjsmin still fits where turbohtml cannot.

Performance
===========

The trade is deliberate: rjsmin's regex is faster than a parse, but it shrinks far less. On the library ladder (``python
-m bench minify-js``) turbohtml takes single-digit milliseconds where rjsmin takes a fraction of one, and in return its
output is up to half the size: jQuery 3.7 minifies to 31% of source under turbohtml versus 51% under rjsmin, lodash 4.17
to 13% versus 28%, because turbohtml renames and folds rather than only deleting space. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/rjsmin.json

When the cost that matters is bytes shipped rather than minify time, turbohtml wins; when you only need whitespace
stripped as cheaply as possible, rjsmin is still the lighter tool.

****************
 How to migrate
****************

Swap the import and the call. rjsmin exposes one function; turbohtml exposes the same shape plus an options object.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - rjsmin
      - turbohtml
    - - ``import rjsmin``
      - ``import turbohtml``
    - - ``rjsmin.jsmin(source)``
      - ``turbohtml.clean.minify_js(source)``
    - - ``rjsmin.jsmin(source)`` (whitespace/comments only)
      - ``turbohtml.clean.minify_js(source, JSMinify(mangle=False, fold=False))`` for the closest whitespace-only match
    - - ``rjsmin.jsmin(source, keep_bang_comments=True)``
      - ``turbohtml.clean.minify_js(source)`` keeps ``/*! ... */`` and ``@license`` / ``@preserve`` banners by default

.. code-block:: python

    # rjsmin
    import rjsmin

    rjsmin.jsmin(source)  # whitespace and comments only

    # turbohtml
    import turbohtml

    turbohtml.clean.minify_js(source)  # whitespace + rename locals + fold constants

To minify inline ``<script>`` content — which rjsmin leaves entirely to you — pass a :class:`~turbohtml.clean.JSMinify`
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

- **Renaming is on by default.** ``turbohtml.clean.minify_js(source)`` renames local bindings, which rjsmin never does.
  If a consumer reflects on function-local variable names (rare), pass ``JSMinify(mangle=False)``. Top-level names are
  global and are never renamed regardless of the setting.
- **License banners are kept, not stripped, and hoisted to the top.** turbohtml keeps ``/*! ... */`` and ``@license`` /
  ``@preserve`` comments byte-exact and emits them as a leading banner in source order, while dropping every other
  comment. rjsmin only keeps them under ``keep_bang_comments=True`` and leaves them in place, so a diff against rjsmin
  output differs when a bang comment sits mid-script.
- **Unparsable input raises by default.** rjsmin emits something for any string; the standalone
  :func:`~turbohtml.clean.minify_js` raises :class:`ValueError` on a construct its parser does not handle. Pass
  ``on_error="passthrough"`` for rjsmin's never-fail behavior -- the source comes back verbatim instead of raising. The
  inline ``<script>`` path already applies that fallback and never raises.
- **Number literals change form.** turbohtml rewrites numeric literals to their shortest equivalent; rjsmin leaves them
  verbatim. The value is preserved, but a byte-for-byte diff against rjsmin output will differ here even with the
  optional passes off.
- **Script ``type`` gates the inline path.** Only scripts marked as JavaScript are rewritten during HTML serialization;
  ``application/json`` and ``importmap`` payloads are left untouched. rjsmin has no such awareness because it never sees
  the surrounding document.
