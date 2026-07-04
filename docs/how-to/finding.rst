####################################
 Find elements with the find filter
####################################

Search a tree with :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` and the filter grammar they take --
strings, regexes, callables, booleans, and lists -- applied to the tag, an attribute, the class list, or the collected
text.

************************************
 Find elements in a parsed document
************************************

Parse the document with :func:`turbohtml.parse`, then query it with :meth:`~turbohtml.Node.find` (first match) or
:meth:`~turbohtml.Node.find_all` (every match). A keyword argument constrains an attribute; both work from the document
or from any element, searching its descendants:

.. testcode::

    import turbohtml

    doc = turbohtml.parse("<form><input name=email><input name=token type=hidden></form>")
    print(doc.find("input", type="hidden").attrs["name"])
    print([field.attrs["name"] for field in doc.find_all("input")])

.. testoutput::

    token
    ['email', 'token']

************************************
 Collect the links of a parsed page
************************************

Collect the ``href`` of every anchor by iterating :meth:`~turbohtml.Node.find_all`; a missing attribute does not appear
in :attr:`~turbohtml.Element.attrs`:

.. testcode::

    page = '<p><a href="/a">one</a> and <a href="/b" download>two</a></p>'
    print([link.attrs["href"] for link in turbohtml.parse(page).find_all("a")])

.. testoutput::

    ['/a', '/b']

********************************
 Filter by attribute or pattern
********************************

:meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` take a filter that is a string, a compiled regex, a
callable, a ``bool`` (present or absent), or a list of those, applied to the tag or to an attribute. ``class_`` matches
a token in the class list, and ``axis`` aims the search at something other than descendants:

.. testcode::

    import re, turbohtml

    doc = turbohtml.parse('<a class="btn lg" href="/a">A</a><a href="mailto:x">B</a>')
    print([a.attrs["href"] for a in doc.find_all("a", href=re.compile(r"^/"))])
    print(doc.find("a", class_="lg").text)

.. testoutput::

    ['/a']
    A

********************
 Find nodes by text
********************

``text`` matches an element against its collected text (every :class:`~turbohtml.Text` descendant concatenated, the same
string :attr:`~turbohtml.Node.text` returns). It takes the same kinds as the other filters except that a plain string is
the *whole* collected text rather than a substring: pass a compiled regex to search, or a callable predicate for
anything else. It composes with the tag, ``class_``, and attribute filters:

.. testcode::

    import re, turbohtml

    doc = turbohtml.parse(
        '<section><button class="buy">Add to cart</button><p>Price: $19</p><span>SKU-7788</span></section>'
    ).find("section")
    print(doc.find(text="Add to cart").tag)
    print([node.tag for node in doc.find_all(text=re.compile(r"\$\d+"))])
    print([node.tag for node in doc.find_all(text=lambda value: value.startswith("SKU"))])
    print(doc.find("button", text="Add to cart", class_="buy").tag)

.. testoutput::

    button
    ['p']
    ['span']
    button

To filter a literal ``text`` *attribute* (rather than the text content), pass it through ``attrs={"text": ...}``, since
the ``text`` keyword is the text predicate.
