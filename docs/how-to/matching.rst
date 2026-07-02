##################################
 Match elements the soupsieve way
##################################

:mod:`turbohtml.match` exposes turbohtml's CSS engine in the call shapes of `soupsieve
<https://facelessuser.github.io/soupsieve/>`_, BeautifulSoup's selector library. Porting a soupsieve (or bs4
``Tag.select``) codebase is then an import swap: ``import soupsieve`` becomes ``from turbohtml import match``, and the
calls keep their shape while running on the native engine.

*********************
 Compile once, reuse
*********************

:func:`~turbohtml.match.compile` validates the selector up front and returns a :class:`~turbohtml.match.Matcher` you
reuse across trees. Its methods mirror soupsieve's ``select`` / ``select_one`` / ``iselect`` / ``match`` / ``filter`` /
``closest``:

.. testcode::

    from turbohtml import match, parse

    doc = parse('<ul><li class="on"><a href="/a">a</a></li><li><a href="/b">b</a></li></ul>')
    links = match.compile("li.on a[href]")
    print([a.attr("href") for a in links.select(doc)])
    print(links.select_one(doc).attr("href"))

.. testoutput::

    ['/a']
    /a

``select`` takes a ``limit`` (``0`` means all), ``iselect`` yields lazily, ``match`` tests one element, ``closest``
walks up to the nearest matching ancestor, and ``filter`` keeps the matching members of an iterable (or, given one
element or document, its direct element children):

.. testcode::

    anchor = links.select_one(doc)
    print(match.compile("li").closest(anchor).attr("class"))
    print([a.attr("href") for a in match.compile("[href]").filter(doc.select("a"))])

.. testoutput::

    on
    ['/a', '/b']

******************
 One-shot helpers
******************

The module-level functions take ``(selector, node, ...)`` and compile internally, matching soupsieve's free functions
for a quick call:

.. testcode::

    print([a.attr("href") for a in match.select("a[href]", doc, limit=1)])
    print(match.match("a", match.select_one("a", doc)))

.. testoutput::

    ['/a']
    True

******************************
 Building selectors from data
******************************

:func:`~turbohtml.match.escape` turns an arbitrary string into a safe CSS identifier, so an id or class read from data
cannot break out of the selector:

.. testcode::

    raw_id = "12 col"
    print(match.escape(raw_id))
    page = parse('<p id="12 col">found</p>')
    print(match.select_one(f"#{match.escape(raw_id)}", page).text)

.. testoutput::

    \31 2\ col
    found

A malformed selector raises :class:`~turbohtml.match.SelectorSyntaxError` (a :class:`ValueError` subclass, so
soupsieve's exception name and the native engine's ``ValueError`` both catch it). soupsieve's ``namespaces`` and
``flags`` arguments are carried on a :class:`~turbohtml.match.Matching` config for API parity but do not change which
elements match -- see :doc:`the reference </reference/match>` for that limitation.
