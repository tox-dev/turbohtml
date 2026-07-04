###############
 Edit the tree
###############

Change a parsed tree in place: build nodes by hand, insert and move them, set an element's text and attributes, and
remove what you do not need.

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

********************************
 Strip tags matching a selector
********************************

Where :meth:`~turbohtml.Node.prune` keeps the matches, its two inverses drop them in bulk.
:meth:`~turbohtml.Node.remove` deletes every matching element and its whole subtree, and
:meth:`~turbohtml.Node.strip_tags` unwraps every match, dropping the tag but lifting its children into its place. Both
take one CSS selector, edit in place, and return the node, so they chain like ``prune``:

.. testcode::

    markup = "<article><script>evil()</script><p>keep <b>this</b> text</p></article>"
    doc = turbohtml.parse(markup)
    doc.remove("script")  # drop the <script> subtree outright
    doc.strip_tags("b")  # unwrap every <b>, keeping its text
    print(doc.select_one("article").serialize())

.. testoutput::

    <article><p>keep this text</p></article>

``remove`` is the destructive counterpart of ``prune``: the same snapshot-then-edit pass, but it deletes what ``prune``
would keep. ``strip_tags`` is the bulk form of :meth:`~turbohtml.Node.unwrap`, applied to every match at once; nested
matches collapse to their innermost content. Both snapshot the matches under the per-tree lock before touching the tree,
so a selector that calls back into Python never sees a half-edited document.

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

****************************************
 Set an element's content from a string
****************************************

:meth:`~turbohtml.Element.set_inner_html` parses an HTML string as a fragment in the element's own context and replaces
its children with the result, the write side of :attr:`~turbohtml.Node.inner_html`. The string runs through the same
parser as :func:`~turbohtml.parse`, so malformed markup is repaired the same way:

.. testcode::

    doc = turbohtml.parse("<ul><li>old</li></ul>")
    ul = doc.find("ul")
    ul.set_inner_html("<li>one<li>two")
    print(ul.html)

.. testoutput::

    <ul><li>one</li><li>two</li></ul>

:meth:`~turbohtml.Element.set_text` replaces the children with a single text node, taking the string verbatim, so any
markup in it is escaped rather than parsed (the same as assigning :attr:`~turbohtml.Element.text`):

.. testcode::

    ul.find("li").set_text("a <b> & c")
    print(ul.find("li").html)

.. testoutput::

    <li>a &lt;b&gt; &amp; c</li>

:meth:`~turbohtml.Element.insert_adjacent_html` parses a fragment and splices it relative to the element at a DOM
position: ``"beforebegin"`` and ``"afterend"`` place the nodes among the element's siblings (so they need an element
parent), while ``"afterbegin"`` and ``"beforeend"`` add them as the first or last children:

.. testcode::

    doc = turbohtml.parse("<ul><li>one</li></ul>")
    first = doc.find("li")
    first.insert_adjacent_html("afterend", "<li>two</li>")
    print(doc.find("ul").html)

.. testoutput::

    <ul><li>one</li><li>two</li></ul>

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

***************************
 Edit an element's classes
***************************

For the common case of toggling a single class you do not have to read, split, and rewrite the whole ``class`` value by
hand. :meth:`~turbohtml.Element.add_class`, :meth:`~turbohtml.Element.remove_class`, and
:meth:`~turbohtml.Element.toggle_class` edit the space-separated token set in place and return the element, so the calls
chain; :meth:`~turbohtml.Element.has_class` tests membership. Adding a token already present is a no-op, removing the
last token leaves an empty ``class``, and a write collapses redundant whitespace to single spaces:

.. testcode::

    button = turbohtml.parse('<button class="btn  btn">Save</button>').find("button")
    button.add_class("primary").toggle_class("btn")  # btn was present, so it is removed
    print((button.has_class("primary"), button.attr("class")))

.. testoutput::

    (True, 'primary')

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
