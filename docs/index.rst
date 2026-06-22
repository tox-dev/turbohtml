###########
 turbohtml
###########

``turbohtml`` is a fast, fully typed HTML toolkit for Python built on a C-accelerated core. It provides spec-correct
HTML escaping and unescaping that match the standard library byte for byte, a WHATWG-conformant streaming tokenizer, and
a WHATWG-conformant parser that builds a navigable, lazily-wrapped tree you query with CSS selectors, edit in place,
build from scratch, and serialize back to conformant HTML. Each runs several times faster than its pure-Python
counterpart and supports the free-threaded build.

.. testcode::

    import turbohtml

    print(turbohtml.escape('<a href="?x=1&y=2">Tom & Jerry</a>'))
    print(turbohtml.unescape("caf&eacute; &amp; r&eacute;sum&eacute;"))
    print([token.tag or token.data for token in turbohtml.tokenize("<p>Tom &amp; Jerry</p>")])
    doc = turbohtml.parse("<p>Tom &amp; <a href='/j'>Jerry</a></p>")
    print([link.attrs["href"] for link in doc.find_all("a")])
    print(doc.find("p").text)

.. testoutput::

    &lt;a href=&quot;?x=1&amp;y=2&quot;&gt;Tom &amp; Jerry&lt;/a&gt;
    café & résumé
    ['p', 'Tom & Jerry', 'p']
    ['/j']
    Tom & Jerry

.. important::

    Learn this rule first: turbohtml models text as real **child nodes** following the WHATWG DOM shape, where `lxml
    <https://lxml.de>`_ uses ``text``/``tail`` and `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_
    uses ``.string``. So ``node[i]`` indexes a node's children, and attributes are reached through ``node.attrs``, never
    ``node["attr"]``.

The documentation follows the `Diátaxis <https://diataxis.fr>`_ framework.

.. toctree::
    :maxdepth: 1

    tutorials/index
    how-to/index
    migration/index
    reference
    explanation/index
    development/index
    changelog
    license
