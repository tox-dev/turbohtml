################
 Free-threading
################

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine, so tokenizers in different threads never contend. The
extension declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.

********************************
 One tree, one critical section
********************************

A parsed tree is different from a tokenizer: several threads can hold wrappers into the same tree and read or edit it at
once. Each tree carries one lock, and every edit and every string read runs under a ``Py_BEGIN_CRITICAL_SECTION`` on the
shared handle, so two threads never mutate the same arena concurrently. On the default GIL build the critical section
compiles to nothing, so single-threaded code pays no lock.

A read takes one more precaution. Before it hands control to a Python callback -- a ``find`` predicate, a
:class:`turbohtml.NodeFilter`, a mutation observer -- it snapshots the arena, so a concurrent edit that runs during the
callback cannot tear the walk out from under it. The walk finishes against the state it started with, and the edit lands
after. This keeps a read consistent without holding the lock across arbitrary user code. The guarantee is per-tree: two
threads on one tree are safe, and two threads sharing a single :class:`turbohtml.Tokenizer` still need the caller's own
synchronization.
