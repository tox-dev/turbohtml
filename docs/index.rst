###########
 turbohtml
###########

``turbohtml`` is a fast, fully typed HTML toolkit for Python built on a C-accelerated core. It provides spec-correct
HTML escaping and unescaping that match the standard library byte for byte, a WHATWG-conformant streaming tokenizer, and
a WHATWG-conformant parser that builds a navigable, lazily-wrapped tree. Each runs several times faster than its
pure-Python counterpart and supports the free-threaded build.

.. code-block:: pycon

    >>> import turbohtml
    >>> turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>')
    '&lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;'
    >>> turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute;")
    'café & résumé'
    >>> [token.tag or token.data for token in turbohtml.tokenize("<p>Tom &amp; Jerry</p>")]
    ['p', 'Tom & Jerry', 'p']
    >>> doc = turbohtml.parse("<p>Tom &amp; <a href='/j'>Jerry</a></p>")
    >>> [link.attrs["href"] for link in doc.find_all("a")]
    ['/j']
    >>> doc.find("p").text
    'Tom & Jerry'

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
