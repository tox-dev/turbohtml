##############
 From rcssmin
##############

.. package-meta:: rcssmin ndparker/rcssmin

`rcssmin <https://github.com/ndparker/rcssmin>`_ is the most-used CSS minifier on PyPI, a fast C extension (with a
pure-Python fallback) that is deliberately *non-destructive*. Its whole surface is one function, ``rcssmin.cssmin(style,
keep_bang_comments=False)``: it strips comments, collapses insignificant whitespace, and rewrites nothing else, so
``#ffffff`` and ``0px`` survive untouched. That verbatim-value guarantee is the point -- build pipelines reach for
rcssmin when they want the bytes smaller without any value being touched, and its speed comes from doing strictly less
work. It has no runtime dependencies.

turbohtml covers that ground with :func:`turbohtml.clean.minify_css`, a value-rewriting minifier implemented in C. It
does everything rcssmin does -- comment stripping and whitespace collapsing -- and then applies every rewrite that is
provably value-safe, so the result is smaller while still parsing to the same cascade.

**********************
 turbohtml vs rcssmin
**********************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - rcssmin
    - - Scope
      - Value-safe CSS minifier (``minify_css``), plus inline ``style``-attribute minification
      - Non-destructive whitespace/comment stripper (``cssmin``)
    - - Feature breadth
      - Whitespace/comment removal, color and number shortening, constant ``calc()`` folding, box-longhand-to-shorthand
        merging, adjacent equal-body rule merging, optional Baseline-year shorthands
      - Whitespace/comment removal only; values left verbatim
    - - Performance
      - Native C, linear time; smaller output on nearly every framework in the corpus below
      - Native C, linear time; faster because it rewrites nothing
    - - Typing
      - Typed public API (:func:`~turbohtml.clean.minify_css`, :class:`~turbohtml.clean.CSSMinify`)
      - Untyped
    - - Dependencies
      - None (ships the C extension)
      - None (C extension with pure-Python fallback)
    - - Maintenance
      - Actively developed alongside the turbohtml serializer
      - Maintained, stable, deliberately fixed scope

Feature overlap
===============

The comment-and-whitespace path ports 1:1:

- Minify a full stylesheet: ``rcssmin.cssmin(css)`` maps to :func:`turbohtml.clean.minify_css(css)
  <turbohtml.clean.minify_css>`.
- Both strip comments and collapse insignificant whitespace between tokens.
- Both keep ``/*! ... */`` bang comments (license or copyright banners). turbohtml always preserves them; rcssmin
  preserves them when called with ``keep_bang_comments=True``.
- Both are idempotent and round-trip safe: the output parses to the same cascade as the input, and minifying it again is
  a no-op.

What turbohtml adds
===================

rcssmin rewrites no values, so everything below is turbohtml-only:

- Color shortening: ``#ffffff`` to ``#fff``, ``rgb(255,0,0)`` to ``red``. rcssmin leaves colors verbatim.
- Number and unit shortening: redundant leading and trailing zeros dropped, units removed on zero lengths (``0px`` to
  ``0``). rcssmin leaves them.
- Constant ``calc()`` folding: ``calc(2px + 3px)`` becomes ``5px``. rcssmin leaves the expression intact.
- Box-longhand-to-shorthand merging (``margin``, ``padding``, and friends) and merging of adjacent rules with equal
  bodies. rcssmin does neither, so the size gap widens on framework CSS that leans on those forms.
- Opt-in Baseline-year shorthands via :class:`~turbohtml.clean.CSSMinify`: ``CSSMinify(baseline=2021)`` additionally
  merges ``inset``, the flex ``gap``, and two-value ``overflow`` once they reached Baseline.
- Inline ``style``-attribute minification via :func:`~turbohtml.clean.minify_css_inline` for bare declaration lists.
- A typed surface: :func:`~turbohtml.clean.minify_css` and the frozen :class:`~turbohtml.clean.CSSMinify` options
  object.

What rcssmin has that turbohtml does not
========================================

- Verbatim value preservation. rcssmin's non-destructive contract guarantees every value survives byte-for-byte
  (``#ffffff`` stays ``#ffffff``). If a downstream step needs the original literals unchanged, that guarantee has no
  turbohtml equivalent: turbohtml rewrites to the shortest value-safe form, so the cascade is identical but the bytes
  are not.
- Dropping bang comments. rcssmin's default ``keep_bang_comments=False`` removes ``/*! ... */`` banners; passing
  ``True`` keeps them. turbohtml always keeps them, so there is no way to strip a banner through the minifier -- remove
  it from the source before minifying.
- Bytes input. ``rcssmin.cssmin`` accepts and returns ``bytes`` as well as ``str``. turbohtml's
  :func:`~turbohtml.clean.minify_css` takes ``str``; decode first if your source is bytes.
- Raw speed. rcssmin only strips whitespace, so on cold input it is faster; reach for it when a larger result is
  acceptable and speed matters more than size.

Performance
===========

turbohtml's output is smaller on every framework except the custom-property-heavy ``bulma.css``, where rcssmin ends 0.2%
ahead only by rewriting whitespace inside custom-property values. That rewrite is not value-safe: `CSS Variables 1 §2
<https://www.w3.org/TR/css-variables-1/#defining-variables>`_ keeps a custom property's value as its literal token
stream, ``var()`` splices it verbatim and ``getPropertyValue()`` reads it back byte-exact, so a collapsed space is
observable wherever the value is substituted or compared. rcssmin's whitespace-only pass is faster because it does
strictly less work. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/rcssmin.json

Both round-trip safely -- the output parses to the same cascade -- and both are idempotent. turbohtml is still far
faster than every *other* value-rewriting minifier, which are pure-Python and turn quadratic on a large stylesheet.

****************
 How to migrate
****************

The import and the call name are the only change:

.. code-block:: python

    # rcssmin
    from rcssmin import cssmin

    # turbohtml
    from turbohtml.clean import minify_css

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - rcssmin
      - turbohtml
    - - ``from rcssmin import cssmin``
      - ``from turbohtml.clean import minify_css``
    - - ``cssmin(css)``
      - ``minify_css(css)``
    - - ``cssmin(css, keep_bang_comments=True)``
      - ``minify_css(css)`` (bang comments always kept)
    - - ``cssmin(css)`` on a ``style`` attribute value
      - ``minify_css_inline(css)``

.. testcode::

    from turbohtml.clean import minify_css

    print(minify_css("a {\n  color: #ffffff;\n  margin: 0px 0px;\n}"))

.. testoutput::

    a{color:#fff;margin:0}

For the value of an HTML ``style`` attribute -- a bare declaration list with no selector or braces -- use
:func:`turbohtml.clean.minify_css_inline` instead:

.. testcode::

    from turbohtml.clean import minify_css_inline

    print(minify_css_inline("color: #ff0000 ; margin: 0.50px"))

.. testoutput::

    color:red;margin:.5px

**********************
 Gotchas and pitfalls
**********************

- Output size shifts. rcssmin leaves values verbatim; turbohtml shortens colors and numbers, folds ``calc()``, and
  merges shorthands and equal-bodied rules, so its output is smaller than rcssmin's rather than byte-identical. Pin
  tests to turbohtml's output, not to a stored rcssmin string.
- turbohtml takes no per-call flags. rcssmin's ``keep_bang_comments`` argument has no counterpart because turbohtml
  always keeps ``/*! ... */`` license comments and always drops the rest; the only knob is the frozen
  :class:`~turbohtml.clean.CSSMinify` ``baseline`` year, which bounds output syntax, never the cascade.
- Inline declarations need the inline entry point. A bare ``color:red;margin:0`` from a ``style`` attribute has no
  selector or braces; pass it to :func:`~turbohtml.clean.minify_css_inline`, not :func:`~turbohtml.clean.minify_css`.
- Custom-property values differ. rcssmin collapses whitespace inside a ``--var`` value; turbohtml keeps it byte-exact.
  Byte-comparing the two minifiers on custom-property-heavy CSS will show a difference by design.
- Input type. ``rcssmin.cssmin`` accepts ``bytes``; :func:`~turbohtml.clean.minify_css` takes ``str``. Decode a bytes
  source before passing it.
