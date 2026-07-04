##################
 From minify-html
##################

.. package-meta:: minify-html wilsonzlin/minify-html

`minify-html <https://github.com/wilsonzlin/minify-html>`_ is a Rust HTML minifier with bindings for Python, Node, Deno,
Ruby, Java, and a standalone CLI. ``minify_html.minify(html, **flags)`` collapses whitespace with a context-aware
sensitivity model, drops the tags and attribute quotes the WHATWG rules make optional, strips comments, and can minify
embedded CSS (via lightningcss) and JavaScript in the same pass. It targets maximum size reduction and is used to shrink
HTML at build time and in response pipelines across those language ecosystems.

turbohtml covers the same HTML folds with :func:`turbohtml.clean.minify`, one call that minifies a document over the
WHATWG tree turbohtml already builds, configured by the frozen :class:`~turbohtml.Minify` options object. It collapses
insignificant whitespace, omits the optional tags, unquotes attributes, strips comments, and optionally rewrites inline
JavaScript and CSS, running the work in a compiled C serializer over the parsed tree rather than as a separate Rust
pass.

**************************
 turbohtml vs minify-html
**************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - minify-html
    - - Scope
      - Full WHATWG parser plus serializer; minify is one output layout of a queryable tree
      - Standalone string-in/string-out HTML/CSS/JS minifier
    - - Feature breadth
      - Whitespace, comment, optional-tag, and attribute-quote folds plus opt-in inline JS and CSS minification
      - The same HTML folds plus in-pass CSS and JS minification, template-syntax and SSI preservation flags, and more
        aggressive whitespace and attribute rewrites
    - - Performance
      - C serializer over an already-built tree; about 2x faster, parse included (see below)
      - Optimized Rust minifier, no tree retained
    - - Typing
      - Fully typed, frozen :class:`~turbohtml.Minify` options object
      - Untyped ``**flags`` keyword arguments
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Prebuilt Rust wheel, no runtime Python dependencies
    - - Maintenance
      - Actively maintained
      - Actively maintained across multiple language bindings

Feature overlap
===============

These HTML folds port one-to-one; each minify-html flag becomes a field on :class:`~turbohtml.Minify`:

- Whitespace collapsing — on by default in both; maps to ``Minify(collapse_whitespace=True)`` (also the default).
- Comment removal — ``keep_comments=False`` (the minify-html default) maps to ``Minify(strip_comments=True)`` (the
  turbohtml default; comments go unless you opt out).
- Optional-tag omission — ``keep_closing_tags`` and ``keep_html_and_head_opening_tags`` map to the single
  ``Minify(omit_optional_tags=True)``, which drops the start and end tags the WHATWG rules make optional.
- Attribute unquoting — on by default in both; maps to ``Minify(unquote_attributes=True)``.
- Inline JavaScript minification — ``minify_js=True`` maps to ``Minify(minify_js=JSMinify())``, which rewrites
  ``<script>`` bodies with turbohtml's shipped JS minifier.
- Inline CSS minification — ``minify_css=True`` maps to ``Minify(minify_css=True)``, which shrinks ``<style>`` bodies
  and ``style`` attribute values with turbohtml's shipped, value-safe CSS minifier.

What turbohtml adds
===================

- A real parse and a tree — the input runs through the full WHATWG algorithm, so the same call site can select, mutate,
  and re-serialize the document. minify-html is a one-shot string transform with no tree to query.
- A typed, frozen options object — :class:`~turbohtml.Minify` validates configuration at construction and reuses one
  instance across calls, versus untyped keyword flags.
- Conservative, render-safe output — every fold is round-trip safe, so the output reparses to the input tree and
  minifying twice changes nothing.
- Self-contained C extension — no separate Rust minifier in the toolchain; minify shares the parser turbohtml already
  ships.
- A shell entry point — ``python -m turbohtml minify`` (installed as the ``turbohtml`` console script, with a
  ``--minify-css`` flag) reads a file or stdin, covering minify-html's standalone CLI on Python.

What minify-html has that turbohtml does not
============================================

- Aggressive whitespace removal — minify-html's context-aware model can delete inter-element whitespace outright where
  its sensitivity rules judge it insignificant. turbohtml collapses each run to a single space and never deletes it, so
  the output stays render-safe. No equivalent by design.
- Boolean-attribute and entity shortening — minify-html rewrites ``checked="checked"`` to a bare ``checked`` and
  shortens ``&amp;`` to ``&`` where safe. turbohtml writes boolean attributes in full and keeps ``&amp;``. No
  equivalent.
- Template and server-side markers — ``preserve_brace_template_syntax``, ``preserve_chevron_percent_template_syntax``,
  ``keep_ssi_comments``, ``keep_input_type_text_attr``, ``ensure_spec_compliant_unquoted_attribute_values``,
  ``remove_bangs``, and ``remove_processing_instructions`` tune edge cases turbohtml does not expose knobs for. No
  equivalent; turbohtml applies the WHATWG-safe default in each case.
- Non-Python bindings — minify-html ships for Node, Deno, Ruby, and Java; turbohtml is Python-only (the standalone
  binary's job is covered by ``python -m turbohtml minify``).

Performance
===========

.. bench-table::
    :file: bench/minify-html.json

On the folds the two share (collapsing insignificant whitespace, omitting the tags the WHATWG rules make optional,
dropping redundant attribute quotes, and stripping comments) turbohtml runs about twice as fast, parse included.
minify-html produces smaller output because it removes whitespace and shortens attributes more aggressively; turbohtml
trades those bytes for output that stays valid and reparses to the same tree.

****************
 How to migrate
****************

Swap the import and drop the per-flag keywords for the options object:

.. code-block:: python

    # minify-html
    import minify_html

    minify_html.minify(html, keep_comments=False)

    # turbohtml
    from turbohtml.clean import minify

    minify(html)

Each fold is a field on :class:`~turbohtml.Minify`, so a flag becomes one keyword on the options object:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `minify-html <https://github.com/wilsonzlin/minify-html>`__
      - turbohtml :class:`~turbohtml.Minify`
    - - whitespace collapsing (on by default)
      - ``Minify(collapse_whitespace=...)`` (default ``True``)
    - - ``keep_comments`` (``False`` strips them)
      - ``Minify(strip_comments=...)`` (default ``True``)
    - - ``keep_closing_tags``, ``keep_html_and_head_opening_tags``
      - ``Minify(omit_optional_tags=...)`` (default ``True``; set ``False`` to keep every tag)
    - - attribute unquoting (on by default)
      - ``Minify(unquote_attributes=...)`` (default ``True``)
    - - ``minify_js``
      - ``Minify(minify_js=JSMinify())`` rewrites inline ``<script>`` content (the default ``None`` leaves it verbatim)
    - - ``minify_css``
      - ``Minify(minify_css=True)`` shrinks ``<style>`` bodies and ``style`` attribute values (default ``False``)
    - - ``minify_doctype`` / ``do_not_minify_doctype``
      - the doctype is always normalized to ``<!doctype html>``
    - - ``remove_processing_instructions``
      - the WHATWG algorithm has no processing instructions in the HTML namespace

The default call minifies with every HTML fold engaged:

.. testcode::

    from turbohtml.clean import minify

    print(minify("<ul>  <li>  one  </li>  <li>  two  </li>  </ul>"))

.. testoutput::

    <ul> <li> one </li> <li> two </li> </ul>

**********************
 Gotchas and pitfalls
**********************

- The two minifiers do not produce byte-for-byte identical output. Every turbohtml fold is round-trip safe, so minifying
  is idempotent and the output reparses to the input tree; turbohtml is the more conservative of the two, so port
  against behavior rather than string equality.
- turbohtml keeps attributes in source order; minify-html sorts them.
- turbohtml collapses each whitespace run to one space and keeps a few optional end tags (``</body>``, ``</html>``,
  ``</li>``) that minify-html drops, so its output stays valid and render-safe while running a few bytes larger.
- turbohtml writes boolean attributes in full (``checked="checked"``) and keeps ``&amp;`` where minify-html shortens to
  a bare ``checked`` and ``&``.
- Embedded CSS and JavaScript minification is opt-in: ``Minify(minify_css=True)`` shrinks ``<style>`` bodies and
  ``style`` attribute values, and ``Minify(minify_js=...)`` rewrites inline ``<script>`` content. minify-html folds both
  in the same call and turbohtml leaves them verbatim unless asked.
- ``minify`` parses the input as a full document, so a bare fragment gains the ``<html>``/``<body>`` structure the
  WHATWG algorithm infers; call it on whole pages, not detached fragments.
