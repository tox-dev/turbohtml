##############################
 Work with ranges of the tree
##############################

A :class:`~turbohtml.Range` marks a run of the tree between two boundary points and copies, moves, deletes, or wraps
everything between them. Each boundary is a ``(container, offset)`` pair: the offset indexes the container's children,
or its code points when the container is character data. Build one with a starting boundary, then move the end.

********************************
 Copy or move a run of siblings
********************************

:meth:`~turbohtml.Range.clone_contents` returns a fragment copying the range without touching the tree;
:meth:`~turbohtml.Range.extract_contents` moves the nodes out into the fragment instead, leaving the range collapsed:

.. testcode::

    from turbohtml import Range

    doc = turbohtml.parse("<ul><li>a</li><li>b</li><li>c</li></ul>")
    ul = doc.find("ul")
    span = Range(ul, 0)
    span.set_end(ul, 2)
    moved = span.extract_contents()
    print(moved.html)
    print(ul.html)

.. testoutput::

    <li>a</li><li>b</li>
    <ul><li>c</li></ul>

****************************
 Delete part of a text node
****************************

A boundary inside a :class:`~turbohtml.Text` node offsets by code point, so the same string index you would slice with
addresses the boundary. :meth:`~turbohtml.Range.delete_contents` splices the run out:

.. testcode::

    doc = turbohtml.parse("<p>Hello brave World</p>")
    text = doc.find("p").children[0]
    span = Range(text, 6)
    span.set_end(text, 12)
    span.delete_contents()
    print(doc.find("p").html)

.. testoutput::

    <p>Hello World</p>

******************
 Wrap a selection
******************

:meth:`~turbohtml.Range.surround_contents` extracts the range, wraps it in a new parent, and puts the parent back where
the range was -- splitting the surrounding text as needed:

.. testcode::

    from turbohtml import Element

    doc = turbohtml.parse("<p>one two three</p>")
    text = doc.find("p").children[0]
    span = Range(text, 4)
    span.set_end(text, 7)
    span.surround_contents(Element("em"))
    print(doc.find("p").html)

.. testoutput::

    <p>one <em>two</em> three</p>

*****************************
 Insert a node at a boundary
*****************************

:meth:`~turbohtml.Range.insert_node` places a node at the range's start. When that start sits inside a text node the
text splits around it:

.. testcode::

    doc = turbohtml.parse("<div><p>x</p></div>")
    box = doc.find("div")
    Range(box, 1).insert_node(Element("hr"))
    print(box.html)

.. testoutput::

    <div><p>x</p><hr></div>

Where a live ``Range`` shifts its own boundaries as its content operations run, a :class:`~turbohtml.StaticRange` is a
fixed snapshot -- the four boundary values with the :attr:`~turbohtml.StaticRange.collapsed` flag and nothing else:

.. testcode::

    from turbohtml import StaticRange

    box = doc.find("div")
    snapshot = StaticRange(box, 0, box, len(box.children))
    print(snapshot.collapsed, snapshot.end_offset)

.. testoutput::

    False 2
