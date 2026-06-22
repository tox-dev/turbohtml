###################
 Mutating the tree
###################

The arena that makes reading cheap is built for append-only construction, not random edits, so making the tree mutable
took a deliberate rule rather than a writable wrapper over the read path: **mutate in place within a tree, copy on adopt
across trees**. An edit that keeps a node in its own tree (:meth:`~turbohtml.Element.append` of a child already under
the same root, :meth:`~turbohtml.Node.insert_before`, :meth:`~turbohtml.Node.unwrap`) is a few pointer swaps on the
arena nodes, so the node keeps its identity and any wrapper you hold stays valid. Inserting a node from a different tree
(a freshly constructed one, or a node lifted out of another document) deep-copies its subtree into the destination's
arena and re-points the moved wrapper at the copy, so the two arenas never alias and the source frees on its own. Making
a node a descendant of itself is refused. The bulk wraps (:meth:`~turbohtml.Element.wrap_children`,
:meth:`~turbohtml.Node.wrap_siblings`) are the same pointer swaps applied to a whole run at once: they resolve the run
and relink it in pure C under the one per-tree lock, never dereferencing a sibling pointer across a Python call that
could rewire the tree, so a group moves in a single atomic edit rather than node by node.

:meth:`~turbohtml.Node.prune` is the bulk version of that subtractive edit, and answers the gap a ``SoupStrainer``
filled by filtering *during* the parse. turbohtml keeps the parse whole and conformant, then trims afterward: it runs
the CSS matcher over the subtree once, snapshots every match together with its ancestor chain, and only then removes
everything the snapshot does not cover. Doing the match before any edit is what keeps it correct under free-threading: a
selector can call back into Python (a regex or string filter), and a structural pointer must never be dereferenced
across such a call once an edit could have rewired it, so the matching pass touches no links the removal pass will
change and the removal pass calls into no Python. A match keeps its whole subtree and its ancestors keep their place, so
a large document collapses to just the parts of interest in one locked pass over the arena.

Construction reuses the same arena machinery: :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and the rest build
a standalone single-node tree that owns its data, ready to adopt into a document, and tag and attribute names are
ASCII-lowercased so they resolve to the same interned atoms the parser assigns. :attr:`Element.attrs
<turbohtml.Element.attrs>` is a live mapping over the node's own attribute array (assignment and deletion edit the tree
directly rather than a throwaway dict), and ``copy.copy``, ``copy.deepcopy``, and :mod:`python:pickle` all run through
the same subtree copy, so a clone is always a standalone tree.

:class:`~turbohtml.ProcessingInstruction` and :class:`~turbohtml.CData` round out the node model for building, but the
parser never emits them: a WHATWG-conformant parse folds ``<? ... >`` into a comment and a foreign CDATA section into
text, and turbohtml keeps that conformance rather than inventing nodes the algorithm does not produce. Pickling carries
an element's children as an explicit list instead of re-serializing, so those two node types survive a round-trip that
serialize-and-reparse would fold away.
