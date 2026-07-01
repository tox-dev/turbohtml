###################
 From lightningcss
###################

.. image:: https://static.pepy.tech/badge/lightningcss/month
    :alt: lightningcss monthly downloads
    :target: https://pepy.tech/project/lightningcss

`lightningcss <https://pypi.org/project/lightningcss/>`_ binds the Rust CSS engine behind Parcel to Python through
``process_stylesheet``. It is a cascade-aware optimizer: besides minifying, it drops declarations overridden elsewhere
in the sheet and rewrites syntax for a browser-target set, so it reaches a smaller result than a value-safe minifier
can. ``lightningcss.process_stylesheet(code, minify=True)`` maps to :func:`turbohtml.clean.minify_css`, which trades a
little size for output that is value-safe with no target list to configure, minifies faster, and survives malformed
input.

***************
 Why turbohtml
***************

lightningcss produces the smaller output -- one to five percent under turbohtml on the framework corpus -- because it
optimizes for a browser-target set: it removes declarations overridden across the sheet and emits syntax those targets
support. turbohtml applies only transforms that hold on any conformant browser, so its default output needs no target
list and parses to the same cascade everywhere; the :class:`~turbohtml.clean.CSSMinify` ``baseline`` year opts into the
newer-syntax shorthand merges when you are ready to require them. turbohtml also minifies two to three times faster, and
it recovers from malformed CSS that lightningcss rejects: ``foundation.css`` raises a parse error on a media query the
WHATWG rules accept, where turbohtml minifies all six stylesheets. Each cell shows the figure with its ratio to
turbohtml:

.. list-table::
    :header-rows: 1
    :widths: 24 19 19 19 19

    - - stylesheet
      - turbohtml size
      - lightningcss size
      - turbohtml time
      - lightningcss time
    - - normalize.css (6 kB)
      - 1.8 kB
      - 1.8 kB (0.99x)
      - 15.9 µs
      - 48.2 µs (3.0x)
    - - animate.css (93 kB)
      - 72.8 kB
      - 68.8 kB (0.95x)
      - 605 µs
      - 1.25 ms (2.1x)
    - - pico.css (90 kB)
      - 81.0 kB
      - 80.0 kB (0.99x)
      - 457 µs
      - 1.56 ms (3.4x)
    - - foundation.css (164 kB)
      - 131.4 kB
      - parse error
      - 1.09 ms
      - parse error
    - - bootstrap.css (274 kB)
      - 229.4 kB
      - 228.7 kB (1.00x)
      - 1.65 ms
      - 4.82 ms (2.9x)
    - - bulma.css (745 kB)
      - 682.2 kB
      - 674.3 kB (0.99x)
      - 3.46 ms
      - 11.7 ms (3.4x)

Reach for lightningcss when you can pin a browser-target set and want the last few percent of size; reach for turbohtml
when you want value-safe output with no configuration, faster runs, and tolerance of real-world CSS.

*********************************
 lightningcss.process_stylesheet
*********************************

The import and the call are the only change:

.. code-block:: python

    # lightningcss
    from lightningcss import process_stylesheet

    process_stylesheet(css, minify=True)

    # turbohtml
    from turbohtml.clean import minify_css

    minify_css(css)

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

Pitfalls
========

- lightningcss removes declarations overridden elsewhere in the sheet and rewrites syntax for its targets; turbohtml
  does neither by default, because both depend on which browsers you support. Set ``baseline`` to recover the
  newer-syntax merges, but the whole-sheet cascade optimization has no value-safe equivalent.
- lightningcss aborts on a stylesheet its parser rejects; turbohtml follows the WHATWG error-recovery rules, so it
  minifies inputs like ``foundation.css`` that lightningcss will not parse.
- Both are native extensions (Rust versus C), so neither offers a pure-Python fallback.
