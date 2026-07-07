###################
 Traversal objects
###################

turbohtml already walks a tree two ways: the lazy :attr:`~turbohtml.Node.descendants` iterator visits every node once in
document order, and the query methods (:meth:`~turbohtml.Node.find_all`, :meth:`~turbohtml.Node.select`) return a
materialized list. :class:`~turbohtml.TreeWalker` and :class:`~turbohtml.NodeIterator` fill the gap between them: a
*steerable, filtered* view. They are the DOM Living Standard traversal objects, so code ported from a browser or from
`jsdom <https://github.com/jsdom/jsdom>`_ keeps its traversal logic (see :doc:`/migration/jsdom`).

The two differ in shape. A :class:`~turbohtml.TreeWalker` is a cursor: it remembers a
:attr:`~turbohtml.TreeWalker.current_node` and moves relative to it -- to a child, a sibling, the parent, or the next or
previous node in document order -- so you can change direction mid-walk, the way an outline editor moves through
headings. A :class:`~turbohtml.NodeIterator` is a flat sequence: it yields the accepted nodes front to back (or back to
front), with no notion of depth. Both are confined to a ``root`` subtree and both apply the same filter.

*****************
 Reject vs. skip
*****************

Filtering has two stages, run in this order for every node the walk reaches. First the ``what_to_show`` bitmask: a node
whose DOM type is not in the mask is dropped without the callback ever seeing it. Then, if the type passes, the
``filter`` callback returns one of three verdicts. Two of them keep the walk going but mean different things, and the
difference is the reason a *tree* walker exists:

- :attr:`~turbohtml.NodeFilter.FILTER_ACCEPT` yields the node.
- :attr:`~turbohtml.NodeFilter.FILTER_SKIP` drops the node but keeps descending, so an unwanted wrapper vanishes while
  its contents stay reachable.
- :attr:`~turbohtml.NodeFilter.FILTER_REJECT` drops the node *and its whole subtree*, so a rejected ``<nav>`` takes its
  links with it.

A :class:`~turbohtml.NodeIterator` is flat, so it has no subtree to prune: for it, reject and skip collapse to the same
"not this one, keep looking". The distinction only bites on a :class:`~turbohtml.TreeWalker`, where each move consults
the verdict to decide whether to step into a node's children. Getting that split exactly right is what makes these
objects a faithful port target rather than an approximation.

*********************
 Where the work runs
*********************

The traversal state machine and the ``what_to_show`` test are pure C, following turbohtml's rule that every non-trivial
algorithm lives in the extension. The one place the walk crosses back into Python is the ``filter`` callback -- it has
to, since the predicate is arbitrary user code. That boundary is guarded: while the callback runs, the traverser sets an
active flag, and a callback that re-enters the same object (calls a traversal method from inside itself) raises
:class:`ValueError`, the Python spelling of the DOM's ``InvalidStateError``, instead of corrupting the cursor. Each step
runs under the tree's per-object critical section, so a concurrent mutation cannot rewire the nodes mid-move on the
free-threaded build.

``current_node`` stays assignable, as the DOM requires, but only to a node in the walker's own tree: the cursor holds a
raw pointer into that tree's arena, so accepting a node from another document would let it dangle. That is the single
deliberate narrowing of the spec surface, traded for the guarantee that a walker can never point at freed memory.

Both objects are validated against jsdom, which passes the WPT ``dom/traversal`` suite:
:file:`tests/conformance/test_dom_jsdom_differential.py` replays each walk through both libraries over shared trees,
covering every ``what_to_show`` mask and each filter verdict, and confirms the accepted-node sequences match -- so
reject really prunes a subtree where skip drops only the node.
