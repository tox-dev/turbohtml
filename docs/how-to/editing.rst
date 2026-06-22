###############
 Edit the tree
###############

**********************
 Build a tree by hand
**********************

Construct nodes with :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and :class:`~turbohtml.Comment`, then
assemble them. A list value for a token-list attribute (``class``, ``rel``, ...) joins on a space, and the ``text``
setter fills an element with a single text child:

.. testcode::

    from turbohtml import Element
    card = Element("article", {"class": ["card", "lg"]})
    heading = Element("h2")
    heading.text = "Title"
    card.append(heading)
    print(card.html)

.. testoutput::

    <article class="card lg"><h2>Title</h2></article>

*****************************
 Edit a parsed tree in place
*****************************

The structural edits move nodes within a tree and adopt nodes from another. ``unwrap`` replaces an element with its
children and ``decompose`` drops a subtree:

.. testcode::

    doc = turbohtml.parse("<p>keep <b>bold</b> <span>drop me</span></p>")
    p = doc.find("p")
    print(doc.find("b").unwrap())
    doc.find("span").decompose()
    print(p.html)

.. testoutput::

    Element('b')
    <p>keep bold </p>

****************************
 Wrap a group of nodes once
****************************

:meth:`~turbohtml.Node.wrap` nests a single node; the bulk forms wrap a whole group in one new container without
relocating each node by hand. :meth:`~turbohtml.Element.wrap_children` boxes every child of an element, and
:meth:`~turbohtml.Node.wrap_siblings` wraps a node and the contiguous run of siblings after it (through an ``until``
node, or to the last sibling when omitted), placing the wrapper where the run began. Both take a fresh element and
return it:

.. testcode::

    from turbohtml import Element
    doc = turbohtml.parse("<section><h2>Title</h2><p>one</p><p>two</p></section>")
    section = doc.find("section")
    paragraphs = section.find_all("p")
    paragraphs[0].wrap_siblings(Element("div", {"class": "body"}), until=paragraphs[-1])
    print(section.html)

.. testoutput::

    <section><h2>Title</h2><div class="body"><p>one</p><p>two</p></div></section>

*********************************
 Rewrite an element's attributes
*********************************

``element.attrs`` is a live mapping, so assignment and deletion rewrite the element directly:

.. testcode::

    link = turbohtml.parse('<a href="/old" class="x" data-tmp="1">go</a>').find("a")
    link.attrs["href"] = "/new"
    link.attrs["class"] = ["btn", "primary"]
    del link.attrs["data-tmp"]
    print(link.html)

.. testoutput::

    <a href="/new" class="btn primary">go</a>

***********************************
 Merge adjacent text after editing
***********************************

Edits can leave a run of adjacent text nodes; :meth:`~turbohtml.Element.normalize` merges each run into one and drops
empty text nodes, throughout the subtree (the DOM operation `BeautifulSoup
<https://www.crummy.com/software/BeautifulSoup/>`_ spells ``smooth``):

.. testcode::

    from turbohtml import Text
    p = turbohtml.Element("p")
    p.extend([Text("Hello "), Text(""), Text("world")])
    p.normalize()
    print((len(p), p.html))

.. testoutput::

    (1, '<p>Hello world</p>')

******************************
 Duplicate or cache a subtree
******************************

Any node deep-copies into a fresh standalone tree, so a clone is independent of the original. Use
:func:`python:copy.deepcopy` to duplicate in memory, or :mod:`python:pickle` to cross a process or cache boundary; both
preserve processing instructions and CDATA sections exactly:

.. testcode::

    import copy
    menu = turbohtml.parse("<ul><li>tea</li></ul>").find("ul")
    clone = copy.deepcopy(menu)
    clone.append(turbohtml.Element("li"))
    print((menu.html, clone.html))

.. testoutput::

    ('<ul><li>tea</li></ul>', '<ul><li>tea</li><li></li></ul>')
