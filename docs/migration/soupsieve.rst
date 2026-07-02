################
 From soupsieve
################

.. package-meta:: soupsieve facelessuser/soupsieve

`soupsieve <https://facelessuser.github.io/soupsieve/>`_ is BeautifulSoup's CSS selector engine and the library behind
every ``Tag.select`` call: ``soupsieve.compile(selector)`` returns a reusable ``SoupSieve`` matcher, and the
module-level ``select`` / ``select_one`` / ``iselect`` / ``match`` / ``filter`` / ``closest`` helpers run one-shot
queries over bs4 tags.

***************
 Why turbohtml
***************

:mod:`turbohtml.match` carries the same call shapes over turbohtml's native selector engine, so a port swaps ``import
soupsieve`` for ``from turbohtml import match`` and keeps its structure. soupsieve interprets each selector in Python
per element; turbohtml compiles it against the tree once and matches by comparing interned integer atoms in C, so a
compiled ``select`` runs 195 to 1,126 times faster across real pages and per-element ``match`` 94 to 155 times faster:

.. bench-table::
    :file: bench/soupsieve.json

The engines agree on what they select: on a corpus of 247 selectors harvested from soupsieve's own test suite, both pick
identical elements for every comparable selector over real documents, and the disagreements are three tracked turbohtml
gaps (`#349 <https://github.com/tox-dev/turbohtml/issues/349>`__, `#350
<https://github.com/tox-dev/turbohtml/issues/350>`__, `#351 <https://github.com/tox-dev/turbohtml/issues/351>`__) plus
two spots where soupsieve itself strays from the spec (see the pitfalls).

*************
 The renames
*************

.. code-block:: python

    # soupsieve
    import soupsieve as sv
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    matcher = sv.compile("li.on a[href]")
    matcher.select(soup, limit=5)

    # turbohtml
    from turbohtml import match, parse

    doc = parse(html)
    matcher = match.compile("li.on a[href]")
    matcher.select(doc, limit=5)

.. testcode::

    from turbohtml import match, parse

    doc = parse('<ul><li class="on"><a href="/a">a</a></li><li><a href="/b">b</a></li></ul>')
    print([a.attr("href") for a in match.select("li.on a[href]", doc)])
    print(match.match("li.on", match.compile("li").closest(match.select_one("a", doc))))

.. testoutput::

    ['/a']
    True

The matcher methods and module helpers keep soupsieve's names; only the trees and the keyword bundle differ.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - soupsieve
      - turbohtml :mod:`~turbohtml.match`
    - - ``sv.compile(sel, namespaces, flags)``
      - :func:`~turbohtml.match.compile` (``namespaces``/``flags`` ride on a frozen :class:`~turbohtml.match.Matching`:
        ``compile(sel, Matching.soupsieve(namespaces, flags))``)
    - - ``sv.select(sel, tag, limit=n)`` and the other module helpers
      - the same names taking turbohtml nodes: ``match.select(sel, node, limit=n)``
    - - ``SoupSieve.select`` / ``select_one`` / ``iselect`` / ``match`` / ``filter`` / ``closest``
      - the same methods on :class:`~turbohtml.match.Matcher`
    - - ``SoupSieve.pattern`` / ``.namespaces`` / ``.flags``
      - the same properties on :class:`~turbohtml.match.Matcher`
    - - ``sv.escape(ident)``
      - :func:`turbohtml.match.escape`
    - - ``soupsieve.SelectorSyntaxError``
      - :class:`turbohtml.match.SelectorSyntaxError` (a :class:`ValueError`, matching the native engine)
    - - ``sv.purge()`` (drop the global compile cache)
      - not needed; there is no global cache, a :class:`~turbohtml.match.Matcher` is the reusable artifact
    - - ``custom={":--name": "..."}`` pseudo-class aliases
      - not supported; inline the aliased selector (``:is(...)`` covers most uses)

**********
 Pitfalls
**********

- The nodes are turbohtml's, so parse with :func:`turbohtml.parse` and pass the :class:`~turbohtml.Document` or an
  :class:`~turbohtml.Element` where soupsieve took a bs4 ``Tag``; the matches come back as turbohtml elements with
  :meth:`~turbohtml.Element.attr`/:attr:`~turbohtml.Node.text` instead of ``tag["attr"]``/``tag.get_text()``.
- soupsieve's proprietary selectors raise :class:`~turbohtml.match.SelectorSyntaxError`: ``[attr!=value]`` (write
  ``:not([attr=value])``) and ``:-soup-contains()`` / ``:-soup-contains-own()`` (query text through
  :meth:`~turbohtml.Node.find_all` with ``text=`` instead).
- ``namespaces`` and ``flags`` are carried for parity but never change which elements match: turbohtml selects by local
  name, so ``svg|rect`` matches every ``rect`` whatever the mapping says, and ``DEBUG`` prints nothing.
- Three engine gaps silently match nothing for now: ``:link``/``:any-link`` (`#349
  <https://github.com/tox-dev/turbohtml/issues/349>`__), wildcard ``:lang()`` ranges such as ``:lang('*-US')`` (`#350
  <https://github.com/tox-dev/turbohtml/issues/350>`__), and ``:scope`` queried from a document (`#351
  <https://github.com/tox-dev/turbohtml/issues/351>`__); ``/* comments */`` inside a selector are rejected (`#352
  <https://github.com/tox-dev/turbohtml/issues/352>`__).
- Two soupsieve 2.8 behaviors are off spec and not reproduced: an only child never matches a functional
  ``:nth-child(An+B)`` (an only child sits at position 1, so ``:nth-child(-n+3)`` must match it), and
  ``input[type=hidden]`` is excluded from ``:enabled`` (the current HTML spec no longer carves out hidden inputs).
- Pseudo-classes soupsieve accepts and matches never (``:current``, ``:paused``, ``:local-link``, ``:host``, the removed
  ``:matches()``, ...) raise here instead, turning a selector that silently returned nothing into a loud error.
