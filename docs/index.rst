###########
 turbohtml
###########

A fast, fully typed HTML toolkit for Python, powered by a C-accelerated core. ``turbohtml`` provides spec-correct HTML
escaping and unescaping that match the standard library byte for byte, and a WHATWG-conformant streaming tokenizer — all
several times faster than their pure-Python counterparts and ready for the free-threaded build.

.. code-block:: pycon

    >>> import turbohtml
    >>> turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>')
    '&lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;'
    >>> turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute;")
    'café & résumé'
    >>> [token.tag or token.data for token in turbohtml.tokenize("<p>Tom &amp; Jerry</p>")]
    ['p', 'Tom & Jerry', 'p']

The documentation follows the `Diátaxis <https://diataxis.fr>`_ framework.

.. toctree::
    :maxdepth: 1

    tutorials
    how-to
    reference
    explanation
    development
    changelog
    license
