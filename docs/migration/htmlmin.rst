##############
 From htmlmin
##############

.. package-meta:: htmlmin mankyd/htmlmin

`htmlmin <https://github.com/mankyd/htmlmin>`_ is a pure-Python minifier built on the standard library's ``HTMLParser``;
``htmlmin.minify(html, **flags)`` collapses whitespace runs to one space and can drop comments and redundant attribute
quotes, and ``htmlmin.Minifier`` exposes the same folds over incremental input. Its last release, 0.1.12 from 2017,
imports the ``cgi`` module Python 3.13 removed, so it no longer installs on current interpreters.

***************
 Why turbohtml
***************

:func:`turbohtml.clean.minify` minifies a document with one call over the WHATWG tree turbohtml already builds,
configured by the frozen :class:`~turbohtml.Minify` options object. On the folds the two share (collapsing insignificant
whitespace, dropping comments, and unquoting attributes) it runs fourteen to twenty times faster, parse included, and it
also omits the tags the WHATWG rules make optional, a fold htmlmin does not have. Output sizes stay within one percent
of each other on the benchmark pages; turbohtml's is the smaller on three of the four.

.. bench-table::
    :file: bench/htmlmin.json

The benchmark installs `htmlmin2 <https://pypi.org/project/htmlmin2/>`__ 0.1.13, the fork that fixes the ``cgi`` import
and changes nothing else, because htmlmin 0.1.12 itself cannot build on Python 3.13 or later.

*************
 The renames
*************

.. code-block:: python

    # htmlmin
    import htmlmin

    htmlmin.minify(html, remove_comments=True)

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

**********
 Pitfalls
**********

- Every fold is round-trip safe, so minifying is idempotent and the output reparses to the input tree. The two minifiers
  do not produce byte-for-byte identical output, though: turbohtml drops optional tags htmlmin keeps and resolves
  character references htmlmin passes through, so port against behavior rather than string equality.
- htmlmin strips comments only when asked (``remove_comments=False`` is its default); turbohtml strips them unless you
  pass ``Minify(strip_comments=False)``.
- ``minify`` parses the input as a full document, so a bare fragment gains the ``<html>``/``<body>`` structure the
  WHATWG algorithm infers; call it on whole pages, not detached fragments.
- htmlmin never touches ``<style>`` and ``<script>`` bodies. turbohtml also leaves them verbatim by default, and can go
  further: ``Minify(minify_js=JSMinify())`` rewrites inline scripts, and :func:`turbohtml.clean.minify_css` shrinks
  ``<style>`` bodies separately.
