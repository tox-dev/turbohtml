###################
 From lightningcss
###################

.. package-meta:: lightningcss

`lightningcss <https://pypi.org/project/lightningcss/>`_ binds the Rust CSS engine behind Parcel to Python through
``process_stylesheet``. It is a cascade-aware optimizer, not just a minifier: alongside whitespace and value compaction
it drops declarations overridden elsewhere in the sheet, merges rules, adds or strips vendor prefixes, and rewrites
syntax down to a configured browser-target set, so it can reach a smaller result than a value-safe minifier can. That
target-driven approach makes it the CSS transform stage in Parcel and a common build-time optimizer for web bundles.

turbohtml covers the same ground with :func:`turbohtml.clean.minify_css`, a value-safe minifier. It applies only
transforms that hold on any conformant browser, so its default output needs no target list, parses to the same cascade
everywhere, and recovers from malformed input that lightningcss rejects. The :class:`~turbohtml.clean.CSSMinify`
``baseline`` year opts into newer-syntax shorthand merges when you are ready to require them.

***************************
 turbohtml vs lightningcss
***************************

.. list-table::
    :header-rows: 1
    :widths: 20 40 40

    - - Dimension
      - turbohtml
      - lightningcss
    - - Scope
      - Value-safe CSS minify (full sheet and inline declarations)
      - Cascade-aware CSS optimizer: minify, prefix, bundle, CSS modules
    - - Feature breadth
      - Whitespace + value compaction, shortest colors, unit/number trimming, constant ``calc()`` folding, shorthand and
        adjacent-rule merges
      - All of that plus cross-sheet dead-declaration removal, target-driven syntax rewriting, vendor prefixing,
        ``@import`` bundling
    - - Performance
      - Faster on some sheets (up to 2.3x), behind the Rust engine on the largest (see Performance)
      - Smaller output on most inputs by trading target configuration for size
    - - Typing
      - Fully typed, frozen ``CSSMinify`` config object
      - Typed bindings over the Rust engine
    - - Dependencies
      - Native C extension in turbohtml, no extra dependency
      - Native Rust extension
    - - Maintenance
      - Maintained within turbohtml
      - Tracks the Parcel/lightningcss Rust project

Feature overlap
===============

The minify surface ports 1:1:

- ``lightningcss.process_stylesheet(css, minify=True)`` -> :func:`turbohtml.clean.minify_css`.
- Whitespace collapse, shortest hex/color forms, redundant unit and leading-zero trimming, and constant ``calc()``
  folding are applied by both.
- Adjacent-rule and shorthand merging: turbohtml merges the value-safe cases by default and the newer-syntax cases
  (``inset``, flex ``gap``, two-value ``overflow``) when you set ``baseline``.

What turbohtml adds
===================

- Value-safe by construction: every transform round-trips to the same cascade on any conformant browser, so there is no
  target list to configure and no way to emit syntax a target cannot parse.
- WHATWG error recovery: turbohtml minifies malformed stylesheets that lightningcss's parser rejects, for example a
  media query in ``foundation.css`` that the WHATWG rules accept.
- Faster on some sheets: up to 2.3x on ``animate.css`` and ahead on ``pico.css``, though the Rust engine leads on the
  largest inputs.
- Inline-declaration minify via :func:`turbohtml.clean.minify_css_inline` for bare ``style``-attribute declaration
  lists, without a surrounding selector or braces.
- Custom-property values and string contents stay byte-exact, as CSS Variables 1 requires.

What lightningcss has that turbohtml does not
=============================================

- Cross-sheet cascade optimization: lightningcss removes declarations overridden elsewhere in the sheet. turbohtml does
  not, because the result depends on which browsers you support; there is no value-safe equivalent.
- Target-driven syntax rewriting to a browser-target set. turbohtml's ``baseline`` gates newer-syntax shorthand merges
  but does not rewrite down to an arbitrary older-target set.
- Vendor prefixing (adding or removing prefixes for a target). No equivalent; turbohtml preserves the input's prefixes.
- ``@import`` bundling and CSS-modules transforms. No equivalent; turbohtml minifies a single stylesheet in place.

Performance
===========

lightningcss produces the smaller output on most of the corpus -- up to seven percent under turbohtml on ``animate.css``
-- because it optimizes for a browser-target set: it removes declarations overridden across the sheet and emits syntax
those targets support. turbohtml applies only transforms that hold on any conformant browser, so its default output
needs no target list and parses to the same cascade everywhere; the :class:`~turbohtml.clean.CSSMinify` ``baseline``
year opts into the newer-syntax shorthand merges when you are ready to require them. On speed the two trade places:
turbohtml is 2.3x faster on ``animate.css`` and edges ahead on ``pico.css``, while lightningcss's Rust engine leads on
the larger ``bootstrap.css`` and ``bulma.css``. turbohtml recovers from malformed CSS that lightningcss rejects:
``foundation.css`` raises a parse error on a media query the WHATWG rules accept, where turbohtml minifies all six
stylesheets. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/lightningcss.json

Reach for lightningcss when you can pin a browser-target set and want the last few percent of size; reach for turbohtml
when you want value-safe output with no configuration and tolerance of real-world CSS.

****************
 How to migrate
****************

The import and the call are the only change:

.. code-block:: python

    # lightningcss
    from lightningcss import process_stylesheet

    process_stylesheet(css, minify=True)

    # turbohtml
    from turbohtml.clean import minify_css

    minify_css(css)

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - lightningcss
      - turbohtml
    - - ``process_stylesheet(css, minify=True)``
      - :func:`turbohtml.clean.minify_css`
    - - ``process_stylesheet(css, minify=True, targets=...)``
      - :func:`turbohtml.clean.minify_css` with :class:`~turbohtml.clean.CSSMinify` (``baseline`` gates newer-syntax
        merges)
    - - No equivalent (full-sheet input only)
      - :func:`turbohtml.clean.minify_css_inline` for ``style``-attribute declaration lists

.. testcode::

    from turbohtml.clean import minify_css

    print(minify_css("a {\n  color: #ffffff;\n  margin: 0px 0px;\n}"))

.. testoutput::

    a{color:#fff;margin:0}

To target a newer browser baseline, pass :class:`~turbohtml.clean.CSSMinify`; the year gates the newer-syntax merges,
much as lightningcss's targets gate its own:

.. testcode::

    from turbohtml.clean import CSSMinify, minify_css

    print(minify_css("a{top:0;right:0;bottom:0;left:0}", CSSMinify(baseline=2021)))

.. testoutput::

    a{inset:0}

**********************
 Gotchas and pitfalls
**********************

- lightningcss removes declarations overridden elsewhere in the sheet and rewrites syntax for its targets; turbohtml
  does neither by default, because both depend on which browsers you support. Set ``baseline`` to recover the
  newer-syntax merges, but the whole-sheet cascade optimization has no value-safe equivalent.
- lightningcss aborts on a stylesheet its parser rejects; turbohtml follows the WHATWG error-recovery rules, so it
  minifies inputs like ``foundation.css`` that lightningcss will not parse.
- lightningcss can add or drop vendor prefixes for a target; turbohtml preserves the input's prefixes as written.
- turbohtml keeps custom-property values and string contents byte-exact; do not expect the internal whitespace of a
  ``var()`` value or a string literal to be collapsed.
- Both are native extensions (Rust versus C), so neither offers a pure-Python fallback.
