###################
 From calmjs.parse
###################

.. image:: https://static.pepy.tech/badge/calmjs.parse/month
    :alt: calmjs.parse monthly downloads
    :target: https://pepy.tech/project/calmjs.parse

`calmjs.parse <https://github.com/calmjs/calmjs.parse>`_ is a full JavaScript front end in pure Python: it parses to an
AST and prints it back, and its ``minify_print`` with ``obfuscate=True`` renames identifiers like a real minifier. It is
the most capable of the PyPI minifiers, but it parses only ES5 — modern syntax (arrow functions, ``let``/``const``,
classes, template literals) raises a syntax error — and the pure-Python parse is slow.

***************
 Why turbohtml
***************

:func:`~turbohtml.minify_js` is the same parse-and-rename approach in C, and it now matches or beats calmjs.parse's size
while running about two orders of magnitude faster. It also parses a much larger slice of the language, so scripts
calmjs.parse rejects still minify. A script it cannot handle is the standalone call's :class:`ValueError` (and, inside
HTML, a verbatim fallback) rather than a hard parser crash.

.. code-block:: python

    # calmjs.parse
    from calmjs.parse import es5
    from calmjs.parse.unparsers.es5 import minify_print

    minify_print(es5(source), obfuscate=True)  # ES5 only, in Python

    # turbohtml
    import turbohtml

    turbohtml.minify_js(source)  # in C, modern syntax too

On the ES5 library ladder turbohtml reaches the smaller output, and the speed gap is large: on the same machine
(``python -m bench minify-js``) calmjs.parse takes hundreds of milliseconds where turbohtml takes single-digit
milliseconds. Each ratio is against turbohtml:

.. bench-table::
    :file: bench/calmjs-parse.json

turbohtml beats calmjs.parse on size everywhere, at forty to eighty times less time and on modern JavaScript that
calmjs.parse rejects outright, so for any build where minify time or modern syntax is in the loop turbohtml is the
practical choice.
