####################
 From csscompressor
####################

.. image:: https://static.pepy.tech/badge/csscompressor/month
    :alt: csscompressor monthly downloads
    :target: https://pepy.tech/project/csscompressor

`csscompressor <https://github.com/sprymix/csscompressor>`_ is a pure-Python port of the YUI CSS compressor. Like
turbohtml it *rewrites* values -- colors to hex, redundant zeros and units dropped -- so it is in the same minifier
class, not a whitespace-only stripper. ``csscompressor.compress`` maps to :func:`turbohtml.clean.minify_css`, which
produces a smaller result and runs in C rather than chained regular expressions.

***************
 Why turbohtml
***************

turbohtml's output is smaller on every framework, and its C engine is 40x to 130x faster -- csscompressor's regex passes
turn quadratic on a large stylesheet, where turbohtml stays linear. Each cell shows the figure with its ratio to
turbohtml:

.. list-table::
    :header-rows: 1
    :widths: 24 19 19 19 19

    - - stylesheet
      - turbohtml size
      - csscompressor size
      - turbohtml time
      - csscompressor time
    - - normalize.css (6 kB)
      - 1.8 kB
      - 1.8 kB (1.04x)
      - 16.2 µs
      - 1.11 ms (69x)
    - - animate.css (93 kB)
      - 72.8 kB
      - 75.7 kB (1.04x)
      - 646 µs
      - 24.8 ms (38x)
    - - pico.css (90 kB)
      - 81.1 kB
      - 81.6 kB (1.01x)
      - 519 µs
      - 35.1 ms (68x)
    - - foundation.css (164 kB)
      - 132.3 kB
      - 136.4 kB (1.03x)
      - 1.18 ms
      - 58.8 ms (50x)
    - - bootstrap.css (274 kB)
      - 228.7 kB
      - 234.2 kB (1.02x)
      - 1.85 ms
      - 80.9 ms (44x)
    - - bulma.css (745 kB)
      - 678.2 kB
      - 681.3 kB (1.00x)
      - 4.24 ms
      - 538 ms (127x)

turbohtml also folds constant ``calc()``, merges box longhands into shorthands, and combines adjacent equal-bodied
rules, none of which csscompressor attempts, so the size gap widens on framework CSS that leans on those forms.

************************
 csscompressor.compress
************************

The import and the call name are the only change:

.. code-block:: python

    # csscompressor
    from csscompressor import compress

    # turbohtml
    from turbohtml.clean import minify_css

.. testcode::

    from turbohtml.clean import minify_css

    print(minify_css("a{ color: rgb(255, 0, 0); width: calc(2px + 3px) }"))

.. testoutput::

    a{color:red;width:5px}

Pitfalls
========

- csscompressor's ``max_linelen`` argument, which wraps the output every *N* columns, has no turbohtml equivalent:
  turbohtml emits a single line, which is the right shape once the bytes are gzipped on the wire.
- turbohtml takes no options at all; every rewrite it applies is value-safe, so there is nothing to configure.
