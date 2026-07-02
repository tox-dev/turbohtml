############
 From jsmin
############

.. image:: https://static.pepy.tech/badge/jsmin/month
    :alt: jsmin monthly downloads
    :target: https://pepy.tech/project/jsmin

`jsmin <https://github.com/tikitu/jsmin>`_ is the Python port of Douglas Crockford's ``jsmin``: a character-level state
machine that removes comments and whitespace. Like the original it does not rename or fold anything, and being a
pure-Python character loop it is slow on large files.

***************
 Why turbohtml
***************

:func:`~turbohtml.minify_js` replaces the call and wins on both axes — it is faster and it shrinks more. turbohtml
parses to an arena AST in C and then renames function-local bindings and folds constants, so it produces smaller output
than jsmin's whitespace-only pass while running an order of magnitude quicker. Inline ``<script>`` minification, which
jsmin leaves entirely to you, is a :class:`~turbohtml.JSMinify` on :class:`~turbohtml.Minify`.

.. code-block:: python

    # jsmin
    from jsmin import jsmin

    jsmin(source)  # Crockford whitespace/comment pass, in Python

    # turbohtml
    import turbohtml

    turbohtml.minify_js(source)  # smaller output, in C

On the library ladder (``python -m bench minify-js``) turbohtml runs about three to five times faster than jsmin and its
output is up to half the size, because jsmin only deletes whitespace where turbohtml renames every local binding and
runs the structural folds. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/jsmin.json

Unlike the regex-based :doc:`rjsmin <rjsmin>`, jsmin has no speed advantage to trade for its simpler output, so there is
no case where it beats :func:`~turbohtml.minify_js`.
