#############
 Explanation
#############

*******************************************
 When to reach for turbohtml, and when not
*******************************************

turbohtml parses, queries, edits, and serializes HTML through a fast, typed, WHATWG-conformant core. Reach for it when
you parse real-world markup and want the tree a browser builds (the `html5lib
<https://github.com/html5lib/html5lib-python>`_ suite passes, so malformed input recovers the way it does in a browser
rather than the way libxml2 guesses); when speed matters (the :doc:`performance` page has the figures); when you want a
modern typed API with one name per concept, ``__match_args__`` on every node, and full type stubs, alongside the
free-threaded build; or when you escape, unescape, or tokenize on a hot path and want a drop-in several times faster
than the standard library.

It is the wrong tool in a few honest cases:

- **You need XPath, XSLT, schema validation, or C14N.** turbohtml gives CSS selectors and the ``find`` filter grammar,
  not XPath, and none of `lxml <https://lxml.de>`_'s XML toolchain. Code that leans on those should stay on lxml.
- **You depend on `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_'s ecosystem or its forgiving,
  duck-typed API.** ``bs4`` swaps parser backends, integrates with a long tail of tools, and accepts almost any shape;
  turbohtml is one conformant parser with a sealed, typed hierarchy. Code written to ``bs4``'s contract needs the
  :doc:`migration` guide, not a drop-in import.
- **You need a decades-hardened dependency.** lxml and BeautifulSoup have been battle-tested for years across every
  platform and corner case; turbohtml is young.
- **HTML is not your bottleneck.** If parsing is a rounding error in your workload, the library you already use is fine.
  turbohtml's advantage is speed and a typed API; if you need neither, switching costs more than it saves.

**************
 Why a C core
**************

Escaping and unescaping sit on hot paths: HTML output escaping runs on every rendered fragment, and unescaping runs on
every chunk of text an HTML parser emits. ``turbohtml`` implements both in C so they run several times faster than an
equivalent pure-Python implementation, with no change in behavior.

The :doc:`performance` page measures both against :func:`python:html.escape` and :func:`python:html.unescape` over real
corpora. ``escape`` gains the most on text that needs little escaping (the SIMD scan classifies sixteen bytes at a time
and copies clean stretches in bulk); ``unescape`` gains the most on entity-heavy input, where the standard library pays
a Python call per match. The gap is narrowest on tiny strings, where call overhead dominates, and on special-dense
markup, where both sides spend their time writing replacements.

Unlike a standard-library accelerator, ``turbohtml`` ships **only** the compiled implementation. :PEP:`399` requires a
pure-Python fallback only for the standard library; as a third-party package distributing per-interpreter wheels,
turbohtml has no need for one, which keeps the surface small.

**************************
 Block-at-a-time scanning
**************************

``escape`` spends most of its time confirming that a string contains nothing that needs escaping. For one-byte strings
it classifies sixteen bytes at a time with SIMD (on arm64 NEON a single low-nibble table lookup plus one comparison
matches all five specials at once; on x86-64 SSE2 compares per special; elsewhere a 64-bit SWAR word applies the
`has-zero bit trick <https://graphics.stanford.edu/~seander/bithacks.html#ZeroInWord>`_). The sizing pass turns each
comparison into that special's output growth and sums the block without branches; the writing pass converts the
comparisons into a position bitmask, copies clean stretches in bulk, and rewrites only the matched bytes. When nothing
needs escaping, ``escape`` returns the input unchanged. Wider strings (UCS-2 / UCS-4; see :PEP:`393` for CPython's
string representations) pack four / two code points into a 64-bit SWAR word and probe all five special characters in a
single pass. ``unescape`` works the same way in reverse: it hops between ``&`` occurrences (``memchr`` on one-byte text)
and bulk-copies the clean spans between references instead of inspecting every character. This needs the `PyUnicode
buffer API <https://docs.python.org/3/c-api/unicode.html>`_, which is why turbohtml cannot use the :ref:`Limited API
<python:stable>`.

*******************************
 Matching the standard library
*******************************

``turbohtml`` reproduces the exact behavior of :func:`python:html.escape` and :func:`python:html.unescape`. ``escape``
uses the same replacements, including ``&#x27;`` for the single quote, and ``unescape`` applies the full `HTML5
character-reference rules <https://html.spec.whatwg.org/multipage/named-characters.html>`_: named references with
longest-prefix matching, numeric references, the Windows-1252 remaps, and the invalid code-point handling that maps to
``U+FFFD`` or the empty string. The test suite checks the C output against the standard library over a large fuzzed
corpus.

Template engines need a different contract: `markupsafe <https://markupsafe.palletsprojects.com>`_'s, where escaping
produces a ``Markup`` safe-string that records "this is already HTML" and combining it with untrusted text escapes that
text. :mod:`turbohtml.markup` is a drop-in for markupsafe's public surface, down to the numeric ``&#34;``/``&#39;``
quote references, so a `Jinja2 <https://jinja.palletsprojects.com>`_ or `WTForms <https://wtforms.readthedocs.io>`_
project migrates by changing the import. It lives in a module apart from :func:`turbohtml.escape` so each stays
byte-exact with its own target: ``turbohtml.escape`` with the standard library, ``turbohtml.markup.escape`` with
markupsafe. turbohtml builds the ``Markup`` in C in one call, where markupsafe pays a Python call and a ``Markup``
construction on every interpolation, so it runs faster.

Linkifying needs the same HTML awareness from the other direction. :mod:`turbohtml.linkify` parses the input first, so
it can see that a URL already sits inside an ``<a>`` or a ``<script>`` and leave it alone, which a regex over the raw
string cannot. The scan for link candidates is the trigger-then-expand model the Rust ``linkify`` crate uses, kept in C:
it looks for the few bytes that can start a link (``:`` for a scheme, ``@`` for an email, ``.`` for a bare domain) and
expands outward from each, rather than backtracking a regex. A bare domain counts only when its last label is a real
TLD, matched against a generated IANA table the same way the tag and entity tables are built. The Python layer owns the
tree walk and the callbacks; the C layer owns the byte scan.

Sanitizing untrusted HTML needs the tree, not the tokens. :mod:`turbohtml.sanitizer` parses the input, walks the tree
dropping everything not on an allowlist, and serializes once. The single most important property is that there is no
serialize-then-reparse round trip: that round trip is where mutation XSS lives, because the second parse can read the
"safe" string differently than the first (foreign-content and raw-text confusion), and a sanitizer that filters the same
tree it will serialize cannot be fooled that way. A non-overridable baseline removes ``<script>``, ``on*`` handlers, and
``javascript:`` URLs below the configurable allowlist, so a policy cannot route around the unsafe set, and the test
suite asserts the property directly: ``sanitize(sanitize(x)) == sanitize(x)`` across an adversarial corpus, so any input
whose cleaned form cleans differently a second time fails the build. Only the :class:`~turbohtml.sanitizer.Policy`
facade is Python; the filtering walk -- the allowlist checks, the URL-scheme parsing, the escape/strip/remove of each
node -- runs in C against the parsed tree, which is why it sanitizes faster than the Rust nh3.

************************
 A spec-exact tokenizer
************************

:func:`turbohtml.tokenize` implements the `WHATWG HTML tokenization algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tokenization>`_ (the same state machine inside every browser)
rather than a regex approximation like :class:`python:html.parser.HTMLParser`. The C implementation mirrors the spec
state by state so the two read side by side, and the shared `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ conformance suite that browsers and parser libraries validate against
checks it at all three input storage widths, once per width, because the token stream must be invariant to how CPython
stores the string.

Two decisions bound the tokenizer's scope:

- The tokenizer is not a parser. It hands you the token stream; it does not build a tree, balance tags, or apply the
  tree-construction rules. The one tree-construction duty it takes on is content-model switching: after a start tag for
  ``script``, ``style``, ``title`` and the other raw-text elements, the element's contents tokenize as the spec requires
  (a ``<b>`` inside a script body is text, not a tag).
- The machine recovers from parse errors instead of reporting them. The spec defines a recovery transition for every
  error and the machine takes it, so malformed input produces the same tokens a browser would see; the error stream is
  not part of the API. A duplicate attribute name is one such recovery: the spec keeps the first occurrence and drops
  the rest at tokenization, so ``<a href=x href=y>`` carries a single ``href`` of ``x`` everywhere it is observed — in
  the token, the parsed tree, and the serialized output alike.

Where behavior could drift, more than the suite pins it: a fuzz comparison runs the token stream against html5lib's
tokenizer, and source positions use the same 1-based-line, 0-based-column convention as :mod:`python:html.parser`, so
diagnostics line up with what the standard library reports.

****************************
 Tokenizing at native width
****************************

CPython stores a string at one of three widths (:PEP:`393`): one byte per character for Latin-1, two for the basic
multilingual plane, four beyond. The tokenizer keeps that representation end to end instead of widening everything to
UCS-4: the input buffer, accumulated text runs, tag names and attribute values all store code points at the narrowest
width their content needs, promoting only when a wider character arrives. turbohtml compiles the state-machine core once
per width (the same trick CPython's ``stringlib`` uses), so every read is direct indexing, and the plain-text states
bulk-scan to the next special character the way `html5ever <https://github.com/servo/html5ever>`_ does rather than
dispatching the state machine per character. For the ASCII documents that dominate real traffic, a text run travels from
input to the final ``str`` as one-byte copies.

The :doc:`performance` page measures the tokenizer against :class:`python:html.parser.HTMLParser` and html5lib's
pure-Python tokenizer. The closest case is a document dominated by a single text node, where the standard library's
regex performs one C scan and never tokenizes; everywhere markup appears, the state machine is roughly ten times faster.

*********************************
 A navigable tree without copies
*********************************

:func:`turbohtml.parse` runs the full `WHATWG tree-construction algorithm
<https://html.spec.whatwg.org/multipage/parsing.html#tree-construction>`_ on top of the tokenizer (the insertion modes,
the adoption agency, foreign content, and the error recovery a browser performs); the same `html5lib-tests
<https://github.com/html5lib/html5lib-tests>`_ tree-construction suite browsers use validates it. The result is the tree
a browser builds for the same bytes.

turbohtml builds the tree in C as a pointer-linked node graph in a single bump-allocated arena, the layout `lexbor
<https://lexbor.com>`_, Go's ``x/net/html``, and html5ever all converge on. It holds **no Python objects**. Element
identity is an interned integer atom, so every scope and category test in the algorithm is an integer compare rather
than a string comparison, and freeing the whole tree is one release of the arena rather than a per-node teardown.

turbohtml creates the Python :class:`~turbohtml.Node` objects only for the nodes you touch. Walking into a child or
sibling allocates one small wrapper that points at the existing arena node; it never copies the subtree. Text payloads
go further: a text node borrows a slice of the original input string and materializes into its own buffer only when you
first read its :attr:`~turbohtml.Node.text` or :attr:`~turbohtml.Text.data`. A private handle keeps the arena and the
input string alive for as long as any node reachable from them, so a node extracted from a document keeps working after
the document goes out of scope. Building the navigable :class:`~turbohtml.Document` costs about the same as building the
raw C tree, and the wrappers add cost only for the nodes you touch.

turbohtml parses faster than the C parsers lxml and `selectolax <https://github.com/rushter/selectolax>`_, and 30 to 80
times faster than the pure-Python BeautifulSoup and html5lib, while building the WHATWG tree that lxml's libxml2 does
not; the :doc:`performance` page has the per-document figures.

The node types are a small sealed hierarchy (:class:`~turbohtml.Document`, :class:`~turbohtml.Element`,
:class:`~turbohtml.Text`, :class:`~turbohtml.Comment`, :class:`~turbohtml.Doctype`,
:class:`~turbohtml.ProcessingInstruction`, :class:`~turbohtml.CData`) sharing the navigation defined on
:class:`~turbohtml.Node`. turbohtml models text as real child nodes (the WHATWG DOM shape) rather than the
``.text``/``.tail`` split lxml-style trees use, so a node's children are its text runs and elements interleaved in
document order. Each type sets ``__match_args__``, so structural pattern matching unpacks a node's defining field, and
node equality is identity over the underlying arena node, so two wrappers for the same element compare equal and hash
alike.

The query surface builds on that node model. Navigation covers parents, siblings, and the lazy
:attr:`~turbohtml.Node.descendants`, :attr:`~turbohtml.Node.ancestors`, and document-order
:attr:`~turbohtml.Node.following` / :attr:`~turbohtml.Node.preceding` iterators, plus the sequence protocol over a
node's children. :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter a chosen
:class:`~turbohtml.Axis` by tag and attributes, where a filter is a string, regex, callable, or list.
:meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one` run a native CSS matcher -- type, id, class,
attribute, the four combinators, and the ``:is()``/``:where()``/``:has()`` functional pseudo-classes -- and
:meth:`~turbohtml.Node.matches` / :meth:`~turbohtml.Node.closest` test a node in place. Selectors compile against the
tree, so a tag or attribute name resolves to the same interned atom the parser assigned and each match is an integer
compare. Output runs back through :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.serialize`, and
:meth:`~turbohtml.Node.encode`, WHATWG-conformant by default with the escaping selectable through
:class:`~turbohtml.Formatter`.

*******************
 Mutating the tree
*******************

The arena that makes reading cheap is built for append-only construction, not random edits, so making the tree mutable
took a deliberate rule rather than a writable wrapper over the read path: **mutate in place within a tree, copy on adopt
across trees**. An edit that keeps a node in its own tree (:meth:`~turbohtml.Element.append` of a child already under
the same root, :meth:`~turbohtml.Node.insert_before`, :meth:`~turbohtml.Node.unwrap`) is a few pointer swaps on the
arena nodes, so the node keeps its identity and any wrapper you hold stays valid. Inserting a node from a different tree
(a freshly constructed one, or a node lifted out of another document) deep-copies its subtree into the destination's
arena and re-points the moved wrapper at the copy, so the two arenas never alias and the source frees on its own. Making
a node a descendant of itself is refused.

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

****************
 Free-threading
****************

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine, so tokenizers in different threads never contend. The
extension declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.
