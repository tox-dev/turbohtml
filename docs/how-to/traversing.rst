######################################
 Walk a tree with a cursor and filter
######################################

Drive a :class:`turbohtml.TreeWalker` or :class:`turbohtml.NodeIterator` over a parsed tree, selecting node types with a
``what_to_show`` bitmask and refining the walk with a :class:`turbohtml.NodeFilter` callback.

********************************
 Steer a cursor with TreeWalker
********************************

A :class:`~turbohtml.TreeWalker` is a movable cursor. Build it from the root node, then step in any direction; each move
sets :attr:`~turbohtml.TreeWalker.current_node` to the node it lands on and returns it, or returns ``None`` (leaving the
cursor put) when there is nothing that way. ``what_to_show`` restricts which node types count -- here elements only:

.. testcode::

    import turbohtml
    from turbohtml import NodeFilter, TreeWalker

    doc = turbohtml.parse("<ul><li>one</li><li>two</li></ul>")
    walker = TreeWalker(doc.find("ul"), NodeFilter.SHOW_ELEMENT)
    print(walker.first_child())
    print(walker.next_sibling())
    print(walker.parent_node())

.. testoutput::

    Element('li')
    Element('li')
    Element('ul')

*************************************
 Select node types with what_to_show
*************************************

``what_to_show`` is a bitmask of :class:`~turbohtml.NodeFilter` ``SHOW_*`` constants; OR them to keep several kinds, or
use :attr:`~turbohtml.NodeFilter.SHOW_ALL`. A node whose type is not in the mask is skipped before the filter ever runs:

.. testcode::

    doc = turbohtml.parse("<p>Alpha<!-- note -->Beta<b>Gamma</b></p>")
    text_only = TreeWalker(doc.find("p"), NodeFilter.SHOW_TEXT)
    print([node.data for node in iter(text_only.next_node, None)])

.. testoutput::

    ['Alpha', 'Beta', 'Gamma']

**********************************
 Prune subtrees with a NodeFilter
**********************************

Pass a ``filter`` callable to decide each node. Return :attr:`~turbohtml.NodeFilter.FILTER_ACCEPT` to keep it,
:attr:`~turbohtml.NodeFilter.FILTER_SKIP` to drop the node but keep walking its children, or
:attr:`~turbohtml.NodeFilter.FILTER_REJECT` to drop the node and its whole subtree. Rejecting a ``<nav>`` removes its
links; skipping it would leave them:

.. testcode::

    page = turbohtml.parse("<body><nav><a href='/'>home</a></nav><main><a href='/post'>post</a></main></body>")


    def without_nav(node):
        if node.tag == "nav":
            return NodeFilter.FILTER_REJECT
        return NodeFilter.FILTER_ACCEPT


    walker = TreeWalker(page.find("body"), NodeFilter.SHOW_ELEMENT, without_nav)
    print([node.tag for node in iter(walker.next_node, None)])

.. testoutput::

    ['main', 'a']

************************************
 Take a flat view with NodeIterator
************************************

When you want the accepted nodes as a plain forward (or backward) sequence rather than a steerable cursor, use
:class:`turbohtml.NodeIterator`. It iterates directly, and its flat view has no subtree to prune, so ``FILTER_REJECT``
and ``FILTER_SKIP`` behave the same:

.. testcode::

    from turbohtml import NodeIterator

    doc = turbohtml.parse("<article><h2>Q3</h2><p>Up 12%</p></article>")
    iterator = NodeIterator(doc.find("article"), NodeFilter.SHOW_ELEMENT)
    print([node.tag for node in iterator])

.. testoutput::

    ['article', 'h2', 'p']

Move backward with :meth:`~turbohtml.NodeIterator.previous_node`; :attr:`~turbohtml.NodeIterator.reference_node` reports
where the iterator currently sits.
