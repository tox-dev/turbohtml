############################
 Ranges and boundary points
############################

A :class:`~turbohtml.Range` is a pair of *boundary points* over one tree, and every operation on it is boundary-point
arithmetic. A boundary point is a ``(container, offset)`` pair: the offset counts the container's children, or its code
points when the container is character data (a :class:`~turbohtml.Text`, :class:`~turbohtml.Comment`, or
:class:`~turbohtml.CData`). Using code points, not UTF-16 code units, is the one deliberate departure from the DOM
Living Standard: it makes a text-node offset line up with the same index a Python string slices at, which is the model a
Python caller expects.

Every derived answer -- :attr:`~turbohtml.Range.collapsed`, :attr:`~turbohtml.Range.common_ancestor_container`, the
comparisons, and the content operations -- rests on one primitive: given two boundary points that share a root, which
comes first in tree order. That comparison is the spec's four-case rule (equal container compares offsets; otherwise the
deeper or following point is resolved against its ancestor's child index), and it runs in C by walking parent chains
rather than materializing paths. Because the setters keep ``start <= end`` -- a :meth:`~turbohtml.Range.set_start` past
the current end, or into a different tree, drags the end onto the new point -- the two boundaries always share a root,
so every later comparison is well defined.

Child offsets index a linked list, so turning a child into its offset, or an offset into its child, walks siblings. The
tree remembers the last few children a range call mapped, and the next call steps from the nearest of them: a loop that
selects, compares, wraps, or inserts at each child of a long list in turn takes a step per child instead of a walk from
the first child per call. An edit through another API clears that memory, so the next range call after it walks from the
first child once.

The content operations are the WHATWG extract, clone, and delete algorithms, split into the same three regions: the
*partially contained* children at each end (a node that is an inclusive ancestor of exactly one boundary), the fully
*contained* children in the middle, and, when both boundaries sit in one character-data node, the single spliced run.
Extract and clone share their structure and differ only in destructiveness: clone deep-copies the contained children and
leaves the tree alone, while extract moves the same nodes into the returned fragment and splices the boundary text out
of the source, then collapses the range to the point the removed content vacated. ``delete_contents`` is extract with
the fragment discarded, so the two never drift apart. The straddling ends recurse into a freshly built sub-range, so a
boundary many levels deep rebuilds only the sliver in range, not the whole subtree. The returned fragment is a
document-fragment node minted in the range's own tree, which is why an extract is a true move -- the nodes never cross
an arena boundary -- and why a clone reuses the tested deep-copy primitive within that one arena. Every operation takes
the per-tree critical section, so a content edit is atomic under the free-threaded build the same way the other
structural mutations are.

The straddling-end algorithm retains the DOM specification's recursive shape. Before allocating a fragment or changing
the source tree, it raises :exc:`RecursionError` when a partial boundary path reaches 400 levels. The iterative copy
walk handles contained subtrees without a nesting limit.

Two scope choices bound this. First, a ``Range`` follows the edits its tree makes: replacing, deleting or splitting
text, normalizing, and removing or inserting nodes move its boundaries as the DOM specifies. A boundary inside removed
content moves to the point where that content was, and a boundary in replaced text moves to the start of the new text,
so the range stays valid across the edit. Second, :class:`~turbohtml.StaticRange` is the immutable counterpart the spec
defines: it stores the four boundary values verbatim with no ordering, bounds, or same-root guarantee, so it is a cheap
snapshot to hand around rather than a handle to operate through.

Every boundary, comparison, and content operation is validated against jsdom, which passes the WPT ``dom/ranges`` suite:
:file:`tests/conformance/test_dom_jsdom_differential.py` replays the same sequences through both libraries over shared
trees and requires byte- and structure-exact agreement, which holds on every case.
