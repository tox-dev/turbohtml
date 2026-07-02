##############
 From rcssmin
##############

.. image:: https://static.pepy.tech/badge/rcssmin/month
    :alt: rcssmin monthly downloads
    :target: https://pepy.tech/project/rcssmin

`rcssmin <https://github.com/ndparker/rcssmin>`_ is the most-used CSS minifier on PyPI, a fast C extension that is
deliberately *non-destructive*: it strips comments and collapses whitespace and rewrites nothing else, so ``#ffffff``
and ``0px`` survive untouched. ``rcssmin.cssmin`` maps to :func:`turbohtml.clean.minify_css`, which goes further --
every value-safe rewrite as well -- so the result is smaller while still parsing to the same cascade.

***************
 Why turbohtml
***************

turbohtml rewrites each value to its shortest equivalent form -- colors to their shortest hex or name, redundant zeros
and units dropped, constant ``calc()`` folded, longhands merged into shorthands, adjacent equal-bodied rules combined --
where rcssmin leaves values verbatim. turbohtml's output is smaller on every framework except the custom-property-heavy
``bulma.css``, where rcssmin edges ahead only by stripping the custom-property whitespace `CSS Variables 1 §3
<https://www.w3.org/TR/css-variables-1/#defining-variables>`_ preserves; rcssmin's whitespace-only pass is faster
because it does strictly less work. Each cell shows the figure with its ratio to turbohtml:

Each row pairs the two outputs and reads the difference in the unit that fits its scale -- size as a percent, since it
moves a few points, and speed as a factor, since it spans an order of magnitude:

.. list-table::
    :header-rows: 1
    :widths: 24 22 22 32

    - - stylesheet
      - turbohtml
      - rcssmin
      - rcssmin vs turbohtml
    - - normalize.css (6 kB)
      - 1.8 kB · 15.9 µs
      - 1.8 kB · 5.12 µs
      - same size · 3.1× faster
    - - animate.css (93 kB)
      - 72.8 kB · 605 µs
      - 75.7 kB · 165 µs
      - 4% larger · 3.7× faster
    - - pico.css (90 kB)
      - 81.0 kB · 457 µs
      - 82.1 kB · 194 µs
      - 1% larger · 2.4× faster
    - - foundation.css (164 kB)
      - 131.4 kB · 1.09 ms
      - 136.7 kB · 382 µs
      - 4% larger · 2.9× faster
    - - bootstrap.css (274 kB)
      - 229.4 kB · 1.65 ms
      - 233.2 kB · 625 µs
      - 2% larger · 2.6× faster
    - - bulma.css (745 kB)
      - 682.2 kB · 3.46 ms
      - 680.0 kB · 1.73 ms
      - 0.3% smaller · 2.0× faster

Both round-trip safely -- the output parses to the same cascade -- and both are idempotent. turbohtml is still far
faster than every *other* value-rewriting minifier, which are pure-Python and turn quadratic on a large stylesheet.

****************
 rcssmin.cssmin
****************

The import and the call name are the only change:

.. code-block:: python

    # rcssmin
    from rcssmin import cssmin

    # turbohtml
    from turbohtml.clean import minify_css

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

Pitfalls
========

- turbohtml has no options: every transform it applies is value-safe, so there is nothing to turn off for correctness.
  rcssmin's ``keep_bang_comments`` flag has no turbohtml equivalent because turbohtml always keeps ``/*! ... */``
  license comments and always drops the rest.
- rcssmin is faster, because it only strips whitespace. Reach for it when a larger result is acceptable and raw speed
  matters more than size; reach for turbohtml when you want the smallest round-trip-safe output.
