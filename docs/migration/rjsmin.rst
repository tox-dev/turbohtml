#############
 From rjsmin
#############

.. image:: https://static.pepy.tech/badge/rjsmin/month
    :alt: rjsmin monthly downloads
    :target: https://pepy.tech/project/rjsmin

`rjsmin <https://github.com/ndparker/rjsmin>`_ minifies JavaScript with a single regular-expression substitution: it
strips comments and insignificant whitespace and nothing else. That makes it very fast, but it never renames a variable
or folds a constant, so its output stays close to the source size.

***************
 Why turbohtml
***************

:func:`~turbohtml.minify_js` is a one-call replacement that does strictly more. It is a real front end — lex, parse,
optimize, print — so on top of whitespace it renames function-local bindings and folds constants, and because it parses
it can never mis-handle a regex-versus-division ``/`` the way a regex pass can. The HTML-embedded case rjsmin leaves to
you (it only sees the script string) is built in: pass a :class:`~turbohtml.JSMinify` as :class:`~turbohtml.Minify`'s
``minify_js`` and inline ``<script>`` content is minified during serialization.

.. code-block:: python

    # rjsmin
    import rjsmin

    rjsmin.jsmin(source)  # whitespace and comments only

    # turbohtml
    import turbohtml

    turbohtml.minify_js(source)  # whitespace + rename locals + fold constants

The trade is deliberate: rjsmin's regex is faster than a parse, but it shrinks far less. On the library ladder (``python
-m bench minify-js``) turbohtml takes single-digit milliseconds where rjsmin takes a fraction of one, and in return its
output is up to half the size: jQuery 3.7 minifies to 31% of source under turbohtml versus 51% under rjsmin, lodash 4.17
to 13% versus 28%, because turbohtml renames and folds rather than only deleting space. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/rjsmin.json

When the cost that matters is bytes shipped rather than minify time, turbohtml wins; when you only need whitespace
stripped as cheaply as possible, rjsmin is still the lighter tool.
