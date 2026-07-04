#####################################
 Select elements with a CSS selector
#####################################

Match descendants against a CSS selector and test a node you already hold. This covers :meth:`~turbohtml.Node.select`,
:meth:`~turbohtml.Node.select_one`, :meth:`~turbohtml.Node.matches`, and :meth:`~turbohtml.Node.closest`, the selector
grammar they share, and how case-sensitivity follows the document mode.

:meth:`~turbohtml.Node.select` returns every descendant matching a CSS selector in document order;
:meth:`~turbohtml.Node.select_one` returns the first or ``None``. The matcher covers type, ``#id``, ``.class``, and
attribute selectors with the ``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=`` operators, the tree-structural pseudo-classes
(``:root``, ``:empty``, ``:first-child``, ``:last-child``, ``:only-child``, their ``-of-type`` variants, and the
``:nth-child()`` family with the ``An+B`` microsyntax, and the Level-4 ``of S`` clause that filters the sibling list by
a selector), joined by the descendant, child (``>``), adjacent (``+``), and general-sibling (``~``) combinators, with
comma groups:

.. testcode::

    import turbohtml

    doc = turbohtml.parse('<ul><li class=on>a<li><a href="/x">b</a></ul>')
    print([li.text for li in doc.select("li.on")])
    print(doc.select_one('a[href^="/"]').text)
    print([li.text for li in doc.select("li:nth-child(odd)")])

.. testoutput::

    ['a']
    b
    ['a']

``:nth-child(An+B of S)`` counts only the inclusive siblings that match the selector list ``S``, so ``An+B`` indexes
that filtered subset rather than every sibling. Here that picks the second ``.row`` item, skipping the separator between
them:

.. testcode::

    table = turbohtml.parse("<ul><li class=row>a</li><li class=sep>-</li><li class=row>b</li><li class=row>c</li></ul>")
    print([li.text for li in table.select("li:nth-child(2 of .row)")])

.. testoutput::

    ['b']

The Selectors Level 4 functional pseudo-classes are supported too: ``:is()`` and ``:where()`` match an element against a
nested selector list (they differ only in specificity, which a tree matcher ignores), ``:has()`` keeps an element when a
relative selector finds a match anchored at it, and ``:not()`` keeps an element that matches none of its arguments.
``:not()`` takes a full selector list, so it negates compound and complex selectors (not just a single class or type)
and nests with the others (``article:not(:has(img))`` selects the image-less articles):

.. testcode::

    page = turbohtml.parse("<article><h1>Post</h1><figure><img></figure></article><article><h1>Note</h1></article>")
    print([a.select_one("h1").text for a in page.select("article:has(img)")])
    print([e.tag for e in page.select(":is(h1, figure)")])
    print([a.select_one("h1").text for a in page.select("article:not(:has(img))")])

.. testoutput::

    ['Post']
    ['h1', 'figure', 'h1']
    ['Note']

The form and UI pseudo-classes select controls by the state the markup pins down: ``:checked``, ``:disabled`` /
``:enabled``, ``:required`` / ``:optional``, ``:read-only`` / ``:read-write``, and ``:default``. ``:lang()`` matches the
nearest ``lang`` attribute (with hyphen-prefix ranges, so ``:lang(en)`` also matches ``en-GB``) and ``:dir()`` the
resolved text direction. ``:scope`` is the element the query is rooted at, which anchors a relative selector:

.. testcode::

    form = turbohtml.parse(
        "<form><input name=agree type=checkbox checked><input name=email required><input name=token disabled></form>"
    )
    print([e.attrs["name"] for e in form.select(":checked")])
    print([e.attrs["name"] for e in form.select(":required")])
    page = turbohtml.parse("<p lang=en-GB>hi</p><p lang=fr>salut</p>")
    print([p.text for p in page.select(":lang(en)")])
    card = turbohtml.parse("<div id=card><h2>T</h2><p>body</p></div>").select_one("#card")
    print([e.tag for e in card.select(":scope > p")])

.. testoutput::

    ['agree']
    ['email']
    ['hi']
    ['p']

The interaction- and navigation-state pseudo-classes (``:hover``, ``:focus``, ``:focus-within``, ``:focus-visible``,
``:active``, ``:target``, ``:target-within``, ``:visited``, ``:link``, and ``:any-link``) parse as valid selectors but
match nothing, since a parsed tree has no live UA state. They stay usable inside ``:is()`` and ``:not()`` rather than
raising, so ``a:not(:visited)`` keeps every link.

``:is()`` and ``:where()`` take a *forgiving* selector list: an arm that fails to parse is dropped and the rest stay
usable, so one unsupported or malformed arm never invalidates the whole selector (``:not()`` and ``:has()`` take a real
list, where a bad arm is still an error):

.. testcode::

    doc = turbohtml.parse("<p>one</p><div>two</div>")
    print([e.tag for e in doc.select(":is(p, :totally-unknown)")])

.. testoutput::

    ['p']

``#id`` and ``.class`` selectors compare case-sensitively in a standards-mode document and ASCII case-insensitively in a
quirks-mode one (a document with no doctype), matching how a browser resolves them. Add a ``<!doctype html>`` to keep
the comparison exact:

.. testcode::

    markup = '<div class="Lead" id="Main">x</div>'
    print(turbohtml.parse(markup).select_one(".lead").tag)  # quirks: folds case
    print(turbohtml.parse("<!doctype html>" + markup).select_one(".lead"))  # standards: exact

.. testoutput::

    div
    None

``:empty`` follows Selectors Level 4: an element counts as empty when its only children are comments or document white
space, so a blank item matches while one holding a non-breaking space (``&nbsp;`` is not white space) does not:

.. testcode::

    items = turbohtml.parse("<ul><li> </li><li>&nbsp;</li><li><!--TODO--></li><li>x</li></ul>")
    print([li.text for li in items.select("li:empty")])

.. testoutput::

    [' ', '']

To test a node you already hold rather than search beneath it, use :meth:`~turbohtml.Node.matches` (does this node
match) or :meth:`~turbohtml.Node.closest` (the nearest matching self-or-ancestor):

.. testcode::

    link = turbohtml.parse('<nav><a href="/x">home</a></nav>').select_one("a")
    print(link.matches("nav a"))
    print(link.closest("nav").tag)

.. testoutput::

    True
    nav
