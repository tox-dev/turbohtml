##################
 From minify-html
##################

.. package-meta:: minify-html wilsonzlin/minify-html

`minify-html <https://github.com/wilsonzlin/minify-html>`_ is a Rust HTML/CSS/JS minifier with a Python binding;
``minify_html.minify(html, **flags)`` collapses whitespace, drops optional tags, and unquotes attributes, and can also
minify embedded CSS and JavaScript.

***************
 Why turbohtml
***************

:func:`turbohtml.clean.minify` minifies a document with one call over the WHATWG tree turbohtml already builds,
configured by the frozen :class:`~turbohtml.Minify` options object. On the folds the two share (collapsing insignificant
whitespace, omitting the tags the WHATWG rules make optional, dropping redundant attribute quotes, and stripping
comments) it runs about twice as fast, parse included. It leaves embedded CSS and JavaScript untouched, which
minify-html can also shrink.

.. bench-table::
    :file: bench/minify-html.json

*************
 The renames
*************

.. code-block:: python

    # minify-html
    import minify_html

    minify_html.minify(html, keep_comments=False)

    # turbohtml
    from turbohtml.clean import minify

    minify(html)

.. testcode::

    from turbohtml.clean import minify

    print(minify("<ul>  <li>  one  </li>  <li>  two  </li>  </ul>"))

.. testoutput::

    <ul> <li> one </li> <li> two </li> </ul>

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
    - - attribute unquoting
      - ``Minify(unquote_attributes=...)`` (default ``True``)
    - - ``minify_js``
      - ``Minify(minify_js=JSMinify())`` rewrites inline ``<script>`` content (the default ``None`` leaves it verbatim)
    - - ``minify_css``
      - no inline hook -- run :func:`turbohtml.clean.minify_css` over ``<style>`` bodies yourself
    - - ``minify_doctype``
      - the doctype is always normalized to ``<!doctype html>``
    - - ``remove_processing_instructions``
      - the WHATWG algorithm has no processing instructions in the HTML namespace

**********
 Pitfalls
**********

- Every fold is round-trip safe, so minifying is idempotent and the output reparses to the input tree. The two minifiers
  do not produce byte-for-byte identical output, though: turbohtml is the more conservative of the two, so port against
  behavior rather than string equality.
- turbohtml keeps attributes in source order; minify-html sorts them alphabetically.
- turbohtml collapses each whitespace run to one space and keeps a few optional end tags (``</body>``, ``</html>``,
  ``</li>``) that minify-html drops, so its output stays valid and render-safe while running a few bytes larger.
- turbohtml writes boolean attributes in full (``checked="checked"``) and keeps ``&amp;`` where minify-html shortens to
  a bare ``checked`` and ``&``.
- Embedded ``<style>`` bodies pass through unchanged (:func:`turbohtml.clean.minify_css` shrinks them separately), and
  inline ``<script>`` minification is opt-in via ``Minify(minify_js=...)``.
- ``minify`` parses the input as a full document, so a bare fragment gains the ``<html>``/``<body>`` structure the
  WHATWG algorithm infers; call it on whole pages, not detached fragments.
