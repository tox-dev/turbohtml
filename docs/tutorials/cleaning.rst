#############################
 Cleaning and shrinking HTML
#############################

Two jobs sit at the output edge of a pipeline: making untrusted markup safe to publish, and making trusted markup
smaller to ship. This tutorial sanitizes a comment against an allowlist, renaming legacy tags as it goes, then minifies
HTML, CSS, and JavaScript.

*******************************
 Sanitize against an allowlist
*******************************

:func:`turbohtml.clean.sanitize` keeps only what a :class:`~turbohtml.clean.Policy` allows, and a non-overridable
baseline drops scripting and ``javascript:`` URLs whatever the policy says. ``transform_tags`` renames a tag as it is
cleaned -- here the deprecated ``<center>`` becomes a ``<div>`` and ``<b>``/``<i>`` become ``<strong>``/``<em>``:

.. testcode::

    from turbohtml.clean import sanitize, Policy, Transform

    policy = Policy(
        tags=frozenset({"strong", "em", "div"}),
        attributes={"div": frozenset({"class"})},
        transform_tags={"b": "strong", "i": "em", "center": Transform("div", {"class": "legacy"})},
    )
    print(sanitize("<center><b>bold</b> and <i>italic</i></center>", policy))

.. testoutput::

    <div class="legacy"><strong>bold</strong> and <em>italic</em></div>

The presets shortcut the common cases: ``Policy.relaxed()`` for typical user content, ``Policy.strict()`` for text with
minimal markup. The :doc:`/how-to/sanitizing` guide covers styles, custom elements, and reporting what was dropped.

*******************
 Minify the markup
*******************

:func:`turbohtml.clean.minify` parses a string and shrinks it in one call. Every transform is round-trip safe -- the
minified bytes reparse to the same tree -- so it folds whitespace, omits optional tags, and drops comments without
changing meaning:

.. testcode::

    from turbohtml.clean import minify

    print(minify("<html><head><title>Hi</title></head><body><p class='lead'>one</p>  <p>two</p><!--note--></body></html>"))

.. testoutput::

    <title>Hi</title><p class=lead>one</p> <p>two

*******************
 Minify CSS and JS
*******************

The same module shrinks stylesheets and scripts. :func:`~turbohtml.clean.minify_css` and
:func:`~turbohtml.clean.minify_js` are value-safe: they rewrite only what cannot change behavior, collapsing whitespace,
shortening colors, and (for JS) renaming local bindings:

.. testcode::

    from turbohtml.clean import minify_css, minify_js

    print(minify_css("a {  color:  #ffffff;  margin: 0px 0px;  }"))
    print(minify_js("function f(x) { var half = x / 2; return half * half; }"))

.. testoutput::

    a{color:#fff;margin:0}
    function f(b){var a=b/2;return a*a}

Sanitized for safety, minified for size. Next, :doc:`pipelines` transforms markup without ever building a full tree.
