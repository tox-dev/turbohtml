##############
 From htmlmin
##############

.. package-meta:: htmlmin mankyd/htmlmin

`htmlmin <https://github.com/mankyd/htmlmin>`_ is a pure-Python HTML minifier built on the standard library's
``HTMLParser``. ``htmlmin.minify(html, **flags)`` collapses whitespace runs to a single space and can optionally drop
comments, reduce empty attributes, and remove redundant attribute quotes; ``htmlmin.Minifier`` exposes the same folds
over incremental input, and the package also ships a WSGI middleware and a decorator for minifying responses in web
frameworks. It sees wide use through those integrations in Django and Flask stacks. Its last release, 0.1.12 from 2017,
imports the ``cgi`` module Python 3.13 removed, so it no longer installs on current interpreters; the maintained
``htmlmin2`` fork restores the import but changes nothing else.

turbohtml covers the same ground with :func:`turbohtml.clean.minify`, one call that minifies a document over the WHATWG
tree turbohtml already builds, configured by the frozen :class:`~turbohtml.Minify` options object. It applies the same
whitespace, comment, and attribute folds, adds a fold htmlmin lacks (omitting the tags the WHATWG rules make optional),
and does the work in a compiled C serializer instead of a Python token loop.

**********************
 turbohtml vs htmlmin
**********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - htmlmin
    - - Scope
      - Full WHATWG parser plus serializer; minify is one output layout
      - HTML minification only, over ``HTMLParser``
    - - Feature breadth
      - Whitespace, comment, attribute-quote folds plus optional-tag omission and optional inline JS/CSS minification
      - Whitespace, comment, empty-attribute, boolean-attribute, and quote folds; incremental ``Minifier``; WSGI
        middleware and framework decorator
    - - Performance
      - C serializer over an already-built tree; 14-20x faster, parse included (see below)
      - Pure-Python ``HTMLParser`` token loop
    - - Typing
      - Fully typed, frozen :class:`~turbohtml.Minify` options object
      - Untyped ``**flags`` keyword arguments
    - - Dependencies
      - Self-contained C extension, no runtime dependencies
      - Pure Python; last release does not install on Python 3.13+ (use ``htmlmin2``)
    - - Maintenance
      - Actively maintained
      - Upstream last released 2017; community ``htmlmin2`` fork carries compatibility fixes only

Feature overlap
===============

These folds port one-to-one; each htmlmin flag becomes a field on :class:`~turbohtml.Minify`:

- Whitespace collapsing — ``htmlmin.minify(html)`` (on by default) maps to ``Minify(collapse_whitespace=True)`` (also
  the default).
- Comment removal — ``remove_comments=True`` maps to ``Minify(strip_comments=True)`` (the turbohtml default; comments go
  unless you opt out).
- Attribute-quote removal and empty-attribute reduction — ``remove_optional_attribute_quotes`` and
  ``reduce_empty_attributes`` map to the single ``Minify(unquote_attributes=True)``, which also collapses an empty value
  to a bare attribute name.

What turbohtml adds
===================

- Optional-tag omission — ``Minify(omit_optional_tags=True)`` drops the start and end tags the WHATWG rules make
  optional (``</li>``, ``</p>``, ``<tbody>``, and so on). htmlmin keeps every tag.
- Character-reference resolution — ``&eacute;`` serializes as the shorter literal ``é``; htmlmin passes references
  through unchanged.
- Inline JavaScript minification — ``Minify(minify_js=JSMinify())`` rewrites ``<script>`` bodies with the shipped JS
  minifier. htmlmin never touches script bodies.
- Inline and standalone CSS minification — :func:`turbohtml.clean.minify_css` shrinks ``<style>`` bodies and standalone
  stylesheets. htmlmin never touches ``<style>`` bodies.
- A real parse — the input runs through the full WHATWG algorithm, so malformed markup is repaired to the same tree a
  browser builds before it is serialized.
- A shell entry point — ``python -m turbohtml minify`` (installed as the ``turbohtml`` console script, with a
  ``--minify-css`` flag) reads a file or stdin, covering htmlmin's command-line tool.

What htmlmin has that turbohtml does not
========================================

- ``remove_empty_space`` / ``remove_all_empty_space`` — htmlmin can delete inter-element whitespace outright. turbohtml
  has no equivalent by design: dropping that whitespace can change rendering, so every run collapses to a single space
  and the output reparses to the same tree. No equivalent.
- ``reduce_boolean_attributes`` — htmlmin can rewrite ``checked="checked"`` to ``checked``. turbohtml leaves boolean
  attributes written in full. No equivalent.
- ``keep_pre`` / ``pre_tags`` / ``pre_attr`` — htmlmin lets you configure which tags and per-attribute markers preserve
  whitespace. turbohtml always preserves whitespace inside ``<pre>`` and ``<textarea>`` (significant per WHATWG) and
  offers no per-attribute opt-out. Workaround: none needed for the common case; the preservation is automatic and
  spec-driven, but the configurability is gone.
- ``htmlmin.Minifier`` incremental input — htmlmin can feed input in chunks. :func:`~turbohtml.clean.minify` takes the
  whole document in one call. Workaround: accumulate the document, then minify once.
- WSGI middleware and framework decorator — htmlmin ships ``htmlmin.middleware.HTMLMinMiddleware`` and a decorator to
  minify HTTP responses in place. turbohtml has no built-in web-framework integration. Workaround: call
  :func:`~turbohtml.clean.minify` on the rendered response body in your own middleware or view wrapper.

Performance
===========

.. bench-table::
    :file: bench/htmlmin.json

On the folds the two share (collapsing insignificant whitespace, dropping comments, and unquoting attributes) turbohtml
runs fourteen to twenty times faster, parse included. Output sizes stay within one percent of each other on the
benchmark pages; turbohtml's is the smaller on three of the four.

The benchmark installs `htmlmin2 <https://pypi.org/project/htmlmin2/>`__ 0.1.13, the fork that fixes the ``cgi`` import
and changes nothing else, because htmlmin 0.1.12 itself cannot build on Python 3.13 or later.

****************
 How to migrate
****************

Swap the import and drop the per-flag keywords for the options object:

.. code-block:: python

    # htmlmin
    import htmlmin

    htmlmin.minify(html, remove_comments=True)

    # turbohtml
    from turbohtml.clean import minify

    minify(html)

Each fold is a field on :class:`~turbohtml.Minify`, so a flag becomes one keyword on the options object:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `htmlmin <https://github.com/mankyd/htmlmin>`__
      - turbohtml :class:`~turbohtml.Minify`
    - - whitespace collapsing (on by default)
      - ``Minify(collapse_whitespace=...)`` (default ``True``)
    - - ``remove_comments`` (default ``False``)
      - ``Minify(strip_comments=...)`` (default ``True``; comments go unless you opt out)
    - - ``remove_optional_attribute_quotes``, ``reduce_empty_attributes``
      - ``Minify(unquote_attributes=...)`` (default ``True``; an empty value becomes a bare attribute name)
    - - no equivalent -- htmlmin keeps every tag
      - ``Minify(omit_optional_tags=...)`` (default ``True``) drops the tags the WHATWG rules make optional
    - - ``remove_empty_space``, ``remove_all_empty_space``
      - not supported -- deleting inter-element whitespace outright can change rendering, so every run collapses to one
        space and the output reparses to the same tree
    - - ``reduce_boolean_attributes``
      - not supported -- boolean attributes stay written in full (``checked="checked"``)
    - - ``keep_pre``, ``pre_tags``, ``pre_attr``
      - whitespace inside ``<pre>`` and ``<textarea>`` is significant per WHATWG and always survives; there is no
        per-attribute opt-out
    - - ``convert_charrefs``
      - character references always resolve: ``&eacute;`` serializes as the shorter literal ``é``
    - - ``htmlmin.Minifier`` incremental input
      - not supported -- :func:`~turbohtml.clean.minify` takes the whole document

The default call minifies with every fold engaged:

.. testcode::

    from turbohtml.clean import minify

    print(minify("<ul>  <li>  one  </li>  <li>  two  </li>  </ul>"))

.. testoutput::

    <ul> <li> one </li> <li> two </li> </ul>

**********************
 Gotchas and pitfalls
**********************

- Every fold is round-trip safe, so minifying is idempotent and the output reparses to the input tree. The two minifiers
  do not produce byte-for-byte identical output, though: turbohtml drops optional tags htmlmin keeps and resolves
  character references htmlmin passes through, so port against behavior rather than string equality.
- The comment default flips. htmlmin strips comments only when asked (``remove_comments=False`` is its default);
  turbohtml strips them unless you pass ``Minify(strip_comments=False)``.
- ``minify`` parses the input as a full document, so a bare fragment gains the ``<html>``/``<body>`` structure the
  WHATWG algorithm infers; call it on whole pages, not detached fragments.
- htmlmin never touches ``<style>`` and ``<script>`` bodies. turbohtml also leaves them verbatim by default, and can go
  further: ``Minify(minify_js=JSMinify())`` rewrites inline scripts, and :func:`turbohtml.clean.minify_css` shrinks
  ``<style>`` bodies separately.
