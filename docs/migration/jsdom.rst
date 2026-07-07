############
 From jsdom
############

`jsdom <https://github.com/jsdom/jsdom>`_ is a JavaScript implementation of the WHATWG DOM and HTML standards, used to
run browser-shaped code under Node. Its ``document.createTreeWalker`` and ``document.createNodeIterator`` are the DOM
Living Standard traversal objects: a movable cursor and a flat filtered view over a subtree, each driven by a
``whatToShow`` bitmask and a ``NodeFilter`` callback.

turbohtml ships those same two objects for Python, built on the same spec, so a scraper or transform ported from jsdom
keeps its traversal logic. The surface is the DOM's, respelled to turbohtml's conventions: methods are snake_case
(``next_node``, ``current_node``), the objects are constructed directly rather than through a ``document`` factory, and
the filter is a plain callable returning a :class:`turbohtml.NodeFilter` verdict. The state machine and the
``whatToShow`` test run in turbohtml's C core; the filter callback is the one step that calls back into Python.

This guide covers the traversal API. The rest of the DOM turbohtml exposes -- parsing, the node model, queries,
mutation, serialization -- is in :doc:`/reference/nodes` and the :doc:`beautifulsoup` guide.

********************
 turbohtml vs jsdom
********************

.. list-table::
    :header-rows: 1
    :widths: 22 39 39

    - - Dimension
      - turbohtml
      - jsdom
    - - Language
      - Python, C-accelerated
      - JavaScript (Node)
    - - Construct a walker
      - ``TreeWalker(root, what_to_show, filter)``
      - ``document.createTreeWalker(root, whatToShow, filter)``
    - - Construct an iterator
      - ``NodeIterator(root, what_to_show, filter)``
      - ``document.createNodeIterator(root, whatToShow, filter)``
    - - Filter
      - a callable ``node -> int``
      - a function or ``{ acceptNode }`` object
    - - Method names
      - snake_case (``next_node``, ``parent_node``)
      - camelCase (``nextNode``, ``parentNode``)
    - - Constants
      - :class:`turbohtml.NodeFilter` attributes
      - ``NodeFilter`` constants

********************
 Map the vocabulary
********************

The constructor arguments line up one-to-one. jsdom's ``document.createTreeWalker(root, whatToShow, filter)`` becomes
``TreeWalker(root, what_to_show, filter)``, with ``what_to_show`` defaulting to :attr:`~turbohtml.NodeFilter.SHOW_ALL`
and ``filter`` to ``None``:

.. testcode::

    import turbohtml
    from turbohtml import NodeFilter, TreeWalker

    doc = turbohtml.parse("<main><h1>Title</h1><p>Body <a href='/x'>link</a></p></main>")
    walker = TreeWalker(doc.find("main"), NodeFilter.SHOW_ELEMENT)
    print(walker.first_child())
    print(walker.next_node())
    print(walker.next_node())

.. testoutput::

    Element('h1')
    Element('p')
    Element('a')

The ``whatToShow`` bits carry over unchanged -- ``NodeFilter.SHOW_ELEMENT``, ``SHOW_TEXT``, ``SHOW_COMMENT``, and the
rest keep their DOM values -- so a mask ported verbatim selects the same node types.

*******************
 Port a NodeFilter
*******************

In jsdom a filter is a function (or an object with an ``acceptNode`` method) returning ``NodeFilter.FILTER_ACCEPT``,
``FILTER_REJECT``, or ``FILTER_SKIP``. In turbohtml it is a plain callable taking the node and returning the same
verdict off :class:`turbohtml.NodeFilter`. The reject/skip distinction is the spec's and turbohtml honors it exactly:
``FILTER_REJECT`` drops a node and its entire subtree, while ``FILTER_SKIP`` drops only the node:

.. testcode::

    page = turbohtml.parse("<section><figure><img></figure><p>keep</p></section>").find("section")


    def drop_figures(node):
        if node.tag == "figure":
            return NodeFilter.FILTER_REJECT
        return NodeFilter.FILTER_ACCEPT


    walker = TreeWalker(page, NodeFilter.SHOW_ELEMENT, drop_figures)
    print([node.tag for node in iter(walker.next_node, None)])

.. testoutput::

    ['p']

Swap ``FILTER_REJECT`` for ``FILTER_SKIP`` and the ``<img>`` inside the figure reappears, because a skip keeps the
subtree; that mirrors jsdom, and is why a :class:`~turbohtml.NodeIterator` -- which has no subtree to prune -- treats
the two verdicts alike.

***********************
 NodeIterator and iter
***********************

``document.createNodeIterator`` becomes :class:`turbohtml.NodeIterator`. Its ``nextNode``/``previousNode`` become
``next_node``/``previous_node``, and because it is a Python iterator you can also drop it straight into a ``for`` loop
for the forward walk:

.. testcode::

    from turbohtml import NodeIterator

    iterator = NodeIterator(doc.find("main"), NodeFilter.SHOW_ELEMENT)
    print([node.tag for node in iterator])

.. testoutput::

    ['main', 'h1', 'p', 'a']

``referenceNode`` and ``pointerBeforeReferenceNode`` are exposed as :attr:`~turbohtml.NodeIterator.reference_node` and
:attr:`~turbohtml.NodeIterator.pointer_before_reference_node`, so code that inspects the iterator's position ports
directly. jsdom's legacy no-op ``detach()`` has no counterpart; drop the call.

*******************
 What is different
*******************

turbohtml keeps ``current_node`` assignable, as in the DOM, but restricts the assigned node to the walker's own tree, so
the cursor can never dangle into a detached document. A filter that re-enters the walker (calls a traversal method from
inside ``acceptNode``) raises :class:`ValueError`, the Python spelling of the DOM's ``InvalidStateError``. And there is
no ``document.createTreeWalker`` factory: construct :class:`~turbohtml.TreeWalker` and :class:`~turbohtml.NodeIterator`
directly from the root node.
