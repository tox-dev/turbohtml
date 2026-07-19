####################
 From csscompressor
####################

.. package-meta:: csscompressor sprymix/csscompressor

`csscompressor <https://github.com/sprymix/csscompressor>`_ is a pure-Python port of the CSS half of the YUI Compressor.
It is a value-rewriting minifier, not a whitespace-only stripper: it collapses whitespace, drops comments, and rewrites
values -- colors to their shortest form, redundant zeros and units removed -- through a sequence of regular-expression
passes. Its surface is one function, ``csscompressor.compress(css)``, plus ``compress_partitioned`` for splitting output
under IE's 4095-selector limit. It has no runtime dependencies and is used as a drop-in CSS compressor in Python build
pipelines that want YUI-equivalent output without a Java or Node step.

turbohtml covers that ground with :func:`turbohtml.clean.minify_css`, the same value-rewriting minifier class
implemented in C. It reaches a smaller result on real stylesheets, keeps custom-property values and string contents
byte-exact, and runs in linear time where csscompressor's regex passes degrade on large input.

****************************
 turbohtml vs csscompressor
****************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - csscompressor
    - - Scope
      - Value-safe CSS minifier (``minify_css``), plus inline ``style``-attribute minification
      - Value-rewriting CSS compressor (``compress``), YUI Compressor port
    - - Feature breadth
      - Whitespace/comment removal, color and number shortening, constant ``calc()`` folding, box-longhand-to-shorthand
        merging, adjacent equal-body rule merging, optional Baseline-year shorthands
      - Whitespace/comment removal, color and number shortening, line wrapping, output partitioning
    - - Performance
      - Native C, linear time (37x-109x faster on the corpus below)
      - Pure-Python regex passes, degrade on large stylesheets
    - - Typing
      - Typed public API (:func:`~turbohtml.clean.minify_css`, :class:`~turbohtml.clean.CSSMinify`)
      - Untyped
    - - Dependencies
      - None (ships the C extension)
      - None (pure Python)
    - - Maintenance
      - Actively developed alongside the turbohtml serializer
      - Maintained, stable, YUI-scoped

Feature overlap
===============

The compression path ports 1:1:

- Minify a full stylesheet: ``csscompressor.compress(css)`` maps to :func:`turbohtml.clean.minify_css(css)
  <turbohtml.clean.minify_css>`.
- Both shorten colors to their shortest equivalent (``#ffffff`` to ``#fff``, ``rgb(255,0,0)`` to ``red``), drop
  redundant leading and trailing zeros, and remove units on zero lengths.
- Both strip comments and collapse insignificant whitespace between tokens.
- Both keep ``/*! ... */`` bang comments (license or copyright banners) verbatim. turbohtml always preserves them;
  csscompressor preserves them by default (``preserve_exclamation_comments=True``).

What turbohtml adds
===================

- Constant ``calc()`` folding: ``calc(2px + 3px)`` becomes ``5px``. csscompressor leaves the expression intact.
- Box-longhand-to-shorthand merging (``margin``, ``padding``, and friends) and merging of adjacent rules with equal
  bodies. csscompressor does neither, so the size gap widens on framework CSS that leans on those forms.
- Byte-exact custom-property values. turbohtml treats a ``--var`` value as the literal token stream ``var()`` splices
  verbatim (`CSS Variables 1 §2 <https://www.w3.org/TR/css-variables-1/#defining-variables>`_); csscompressor rewrites
  the whitespace inside it, so its output is not guaranteed to parse to the same cascade.
- Opt-in Baseline-year shorthands via :class:`~turbohtml.clean.CSSMinify`: ``CSSMinify(baseline=2021)`` additionally
  merges ``inset``, the flex ``gap``, and two-value ``overflow`` once they reached Baseline.
- Inline ``style``-attribute minification via :func:`~turbohtml.clean.minify_css_inline` for bare declaration lists.
- A typed surface: :func:`~turbohtml.clean.minify_css` and the frozen :class:`~turbohtml.clean.CSSMinify` options
  object.
- A native-C pipeline that stays linear where csscompressor's regex passes turn quadratic on large input.

What csscompressor has that turbohtml does not
==============================================

- ``max_linelen``: csscompressor can wrap the output every *N* columns. turbohtml emits a single line, which is the
  right shape once the bytes are gzipped on the wire. No equivalent, and none needed for network transfer.
- ``compress_partitioned``: csscompressor can split the output into chunks under IE's 4095-selector-per-file limit.
  turbohtml has no equivalent; split the source into separate stylesheets before minifying if you still target that
  limit.
- Dropping bang comments: csscompressor's ``preserve_exclamation_comments=False`` removes ``/*! ... */`` banners.
  turbohtml always keeps them; strip the banner from the source before minifying if you need it gone.

Performance
===========

turbohtml's output is smaller on every framework except the custom-property-heavy ``bulma.css``, where the two tie
within 100 bytes, and its C engine is 37x to 109x faster -- csscompressor's regex passes turn quadratic on a large
stylesheet, where turbohtml stays linear. csscompressor also rewrites whitespace inside custom-property values, which
`CSS Variables 1 §2 <https://www.w3.org/TR/css-variables-1/#defining-variables>`_ keeps as the literal token stream that
``var()`` splices verbatim, so its output is not guaranteed to parse to the same cascade. Each ratio is against
turbohtml:

.. bench-table::
    :file: bench/csscompressor.json

turbohtml also folds constant ``calc()``, merges box longhands into shorthands, and combines adjacent equal-bodied
rules, none of which csscompressor attempts, so the size gap widens on framework CSS that leans on those forms.

****************
 How to migrate
****************

The import and the call name are the only change:

.. code-block:: python

    # csscompressor
    from csscompressor import compress

    # turbohtml
    from turbohtml.clean import minify_css

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - csscompressor
      - turbohtml
    - - ``from csscompressor import compress``
      - ``from turbohtml.clean import minify_css``
    - - ``compress(css)``
      - ``minify_css(css)``
    - - ``compress(css, max_linelen=N)``
      - no equivalent (single-line output)
    - - ``compress_partitioned(css, max_rules_per_file=N)``
      - no equivalent (split the source before minifying)

.. testcode::

    from turbohtml.clean import minify_css

    print(minify_css("a{ color: rgb(255, 0, 0); width: calc(2px + 3px) }"))

.. testoutput::

    a{color:red;width:5px}

**********************
 Gotchas and pitfalls
**********************

- turbohtml takes no per-call flags. ``compress``'s ``max_linelen`` and ``preserve_exclamation_comments`` arguments have
  no counterpart; every rewrite turbohtml applies is value-safe, and the only knob is the frozen
  :class:`~turbohtml.clean.CSSMinify` ``baseline`` year.
- Custom-property values differ. csscompressor collapses whitespace inside a ``--var`` value; turbohtml keeps it
  byte-exact. Output that fed a ``var()`` splice can change under csscompressor but not turbohtml, so byte-comparing the
  two minifiers' results on custom-property-heavy CSS will show a difference by design.
- Inline declarations need the inline entry point. A bare ``color:red;margin:0`` from a ``style`` attribute has no
  selector or braces; pass it to :func:`~turbohtml.clean.minify_css_inline`, not :func:`~turbohtml.clean.minify_css`.
- Output size shifts. turbohtml folds ``calc()``, merges shorthands, and combines equal-bodied rules, so its output is
  smaller than csscompressor's rather than byte-identical; pin tests to turbohtml's output, not to a stored
  csscompressor string.
