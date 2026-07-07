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

Setting content from a string -- :meth:`~turbohtml.Element.set_inner_html` (the write side of
:attr:`~turbohtml.Node.inner_html`) and :meth:`~turbohtml.Element.insert_adjacent_html` -- runs the same fragment parser
the read path exposes, in the context of the element that will hold the result, so its content model and namespace are
honored exactly as the DOM ``innerHTML`` setter requires. Because parsing allocates and can re-enter Python, the
fragment is parsed first into a private detached tree, *before* the per-tree lock is taken and without holding any
pointer into the live tree; only then does a pure-C pass under the lock copy the parsed nodes into the destination arena
and splice them at the chosen position. That ordering is the same discipline the bulk wraps follow, and it is what keeps
the splice safe under free-threading: no structural pointer is dereferenced across the parse that could rewire the tree.
:meth:`~turbohtml.Element.set_text` is the degenerate case that needs no parser at all -- it drops the children for a
single verbatim text node, so any markup in the string is escaped rather than interpreted.

:meth:`~turbohtml.Node.prune` is the bulk version of that subtractive edit, and answers the gap a ``SoupStrainer``
filled by filtering *during* the parse. turbohtml keeps the parse whole and conformant, then trims afterward: it runs
the CSS matcher over the subtree once, snapshots every match together with its ancestor chain, and only then removes
everything the snapshot does not cover. Doing the match before any edit is what keeps it correct under free-threading: a
selector can call back into Python (a regex or string filter), and a structural pointer must never be dereferenced
across such a call once an edit could have rewired it, so the matching pass touches no links the removal pass will
change and the removal pass calls into no Python. A match keeps its whole subtree and its ancestors keep their place, so
a large document collapses to just the parts of interest in one locked pass over the arena.

:meth:`~turbohtml.Node.remove` and :meth:`~turbohtml.Node.strip_tags` are the same snapshot-then-edit pass run for the
opposite effect, the bulk inverses of ``prune``. ``remove`` deletes every match and its subtree -- what ``prune`` keeps
-- and ``strip_tags`` unwraps every match, splicing its children into its place to keep the content while dropping the
tag, the bulk form of :meth:`~turbohtml.Node.unwrap`. Both collect the matches in the pure-C matching pass first so the
edit pass dereferences no link a Python callback could have rewired. The arena's detach-only removal makes the bulk
edits robust to nesting: a removed node never frees, only unlinks, so a deeper match whose ancestor already left the
tree drops harmlessly, and an unwrapped node only relinks, so a nested match stays live -- reparented onto the surviving
ancestor -- until its own turn comes.

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

*************************
 Observing changes: sync
*************************

A :class:`~turbohtml.MutationObserver` watches a subtree and records every change made through the mutation API --
children added or removed, an attribute set or deleted, a character-data node rewritten -- following the DOM "queue a
mutation record" algorithm: each edit walks the target's ancestor chain and, for every registration that covers the
target (its own node, or an ancestor watching with ``subtree``), queues one :class:`~turbohtml.MutationRecord` carrying
the added and removed nodes, the surrounding siblings, and -- when asked -- the attribute name and the value before the
change. Registration mirrors the DOM options: ``child_list``, ``attributes``, ``character_data``, ``subtree``,
``attribute_old_value``, ``character_data_old_value``, and an ``attribute_filter`` that limits which attribute names
record.

Where turbohtml departs from the DOM is *when* the callback runs. The DOM observer is asynchronous: an edit only queues
a record and schedules a microtask, so the callback fires later, after the current script settles and never in the
middle of a half-finished edit. That timing needs an event loop to drain the microtask queue, and turbohtml is a library
with no loop of its own. Rather than invent one, it keeps the record *shape* identical but makes delivery synchronous
and explicit. Records still queue at mutation time, exactly as the DOM specifies, but they reach Python only when you
drain the queue: :meth:`~turbohtml.MutationObserver.take_records` returns and clears the pending batch, and
:meth:`~turbohtml.MutationObserver.deliver` drains it and calls the registered callback with ``(records, observer)``.
Because a callback runs only at a drain you choose, never re-entrantly from inside an edit, it can freely mutate the
tree without the reentrancy hazards the microtask indirection exists to avoid -- the same safety the DOM buys with the
microtask, bought instead by pull-based delivery. The queue, the ancestor walk, and the per-registration filtering all
live in the C core under the one per-tree lock; the callback is the single Python boundary, invoked only from
``deliver``.
