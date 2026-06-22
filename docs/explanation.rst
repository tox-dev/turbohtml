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

- **You need XSLT, schema validation, or C14N.** turbohtml gives CSS selectors, the ``find`` filter grammar, and an
  XPath 1.0 engine, but none of `lxml <https://lxml.de>`_'s wider XML toolchain. Code that leans on those should stay on
  lxml.
- **You depend on `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`_'s ecosystem or its forgiving,
  duck-typed API.** ``bs4`` swaps parser backends, integrates with a long tail of tools, and accepts almost any shape;
  turbohtml is one conformant parser with a sealed, typed hierarchy. Code written to ``bs4``'s contract needs the
  :doc:`migration/index` guide, not a drop-in import.
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
text. :mod:`turbohtml.migration.markupsafe` is a drop-in for markupsafe's public surface, down to the numeric
``&#34;``/``&#39;`` quote references, so a `Jinja2 <https://jinja.palletsprojects.com>`_ or `WTForms
<https://wtforms.readthedocs.io>`_ project migrates by changing the import. It lives in a module apart from
:func:`turbohtml.escape` so each stays byte-exact with its own target: ``turbohtml.escape`` with the standard library,
``turbohtml.migration.markupsafe.escape`` with markupsafe. turbohtml builds the ``Markup`` in C in one call, where
markupsafe pays a Python call and a ``Markup`` construction on every interpolation, so it runs faster.

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
facade is Python; the filtering walk (the allowlist checks, the URL-scheme parsing, the escape/strip/remove of each
node) runs in C against the parsed tree, which is why it sanitizes faster than the Rust nh3.

Enumerating a page's links is the same tree walk from a third angle. :meth:`~turbohtml.Node.links` finds every
link-bearing location in C (not just ``<a href>`` but the URLs hidden in ``srcset`` candidate lists, a ``<meta
refresh>`` redirect, and CSS ``url()``/``@import`` in a ``style`` attribute or a ``<style>`` sheet), so the capability
no hand-rolled ``find_all`` loop reaches comes for free. The walk locates the URL spans and splices replacements back in
place; the URL resolution itself is :func:`urllib.parse.urljoin` from the standard library, deliberately *not*
reimplemented in C, because RFC 3986 reference resolution is a solved, standard problem and not where turbohtml's value
lies. The line between the two is the project's rule in miniature: the HTML-specific, performance-sensitive work is C,
and a thin typed facade wires it to the stdlib.

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
- The machine recovers from parse errors rather than stopping on them. The spec defines a recovery transition for every
  error and the machine takes it, so malformed input produces the same tokens a browser would see. A duplicate attribute
  name is one such recovery: the spec keeps the first occurrence and drops the rest at tokenization, so ``<a href=x
  href=y>`` carries a single ``href`` of ``x`` everywhere it is observed: in the token, the parsed tree, and the
  serialized output alike. The recovery is silent in the token stream itself; the errors it papered over surface on
  :attr:`Document.errors <turbohtml.Document.errors>` when you :func:`~turbohtml.parse` (see below).

Where behavior could drift, more than the suite pins it: a fuzz comparison runs the token stream against html5lib's
tokenizer, and source positions use the same 1-based-line, 0-based-column convention as :mod:`python:html.parser`, so
diagnostics line up with what the standard library reports. For code that prefers callbacks to a token stream,
:class:`turbohtml.migration.stdlib.HTMLParser` re-exposes the stream through ``html.parser``'s subclass-and-override
surface, so an existing ``HTMLParser`` subclass migrates by swapping its base class.

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

The same tokenizer and tree builder also drive :class:`turbohtml.IncrementalParser`, the push form of
:func:`turbohtml.parse`. The tokenizer is resumable (it suspends mid-token when its input runs out) and reclaims the
prefix it has consumed on every chunk, so the only state the parser carries across a ``feed`` is the few insertion-mode
variables of the tree-construction loop, lifted out of the one-shot call into the parser. The input side is therefore
bounded no matter how long the stream: you never hold the whole source at once, the concrete win over ``parse`` for a
document that arrives over a socket or a file larger than the buffer you would otherwise join. ``bytes`` chunks decode
through a stateful incremental codec, so a multi-byte character split across a chunk boundary still decodes correctly.
The recovery a browser performs hides the spec's parse errors, but a linter or a strict pipeline still wants them.
:func:`~turbohtml.parse` records each error it recovers from on :attr:`Document.errors <turbohtml.Document.errors>`, a
list of :class:`~turbohtml.ParseError` carrying the spec error code and the source position (1-based line, 0-based
column, the same convention :class:`~turbohtml.Token` uses). Collection costs nothing on well-formed input (there are no
errors to record and the per-character paths are untouched), so it stays on by default rather than behind a flag; the
standalone :func:`~turbohtml.tokenize` and :class:`~turbohtml.Tokenizer`, which expose the raw stream, do not collect.
Pass ``strict=True`` to raise the first error as :class:`~turbohtml.HTMLParseError` (with the ``ParseError`` on its
``error`` attribute) instead of returning a recovered tree.

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
:meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one` run a native CSS matcher covering type, id, class,
attribute, the four combinators, the structural pseudo-classes (including ``:nth-child(An+B of S)``, which indexes only
the siblings matching ``S``), the ``:is()``/``:where()``/``:has()``/``:not()`` functional pseudo-classes, and the
``:scope``, form/UI (``:checked``, ``:disabled``, ``:default``, ...), ``:lang()`` and ``:dir()`` pseudo-classes a static
tree can determine. :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest` test a node in place. ``:is()``
and ``:where()`` parse their argument as a forgiving selector list (a bad arm is dropped, the rest stay usable), while
``:not()`` and ``:has()`` take a real list where any bad arm is an error, as the Selectors standard specifies. The
pseudo-classes that depend on live interaction or navigation state (``:hover``, ``:focus``, ``:target``, ``:visited``,
``:link``, ...) parse but match nothing, since a parsed document has no such state. Selectors compile against the tree,
so a tag or attribute name resolves to the same interned atom the parser assigned and each match is an integer compare.
Compiling against the tree also captures its document mode, so ``#id`` and ``.class`` fold ASCII case in a quirks-mode
document and compare exactly otherwise, as the Selectors standard requires. :meth:`~turbohtml.Node.xpath`,
:meth:`~turbohtml.Node.xpath_one`, and :meth:`~turbohtml.Node.xpath_iter` evaluate XPath 1.0 over the same model: a
native-C engine compiles each expression once into an immutable, per-tree-cached program, resolves name tests to
interned atoms, and collapses the ``//`` abbreviation to a single ``descendant`` walk, so the structural axes,
predicates, operators, unions, and the core function library run at lxml's speed. The core API stays
one-name-per-concept and returns plain lists, so the jQuery-style chaining pyquery users expect lives in an optional
Python-side wrapper, :class:`turbohtml.query.Query`, whose traversal and mutation methods each return a wrapper. Output
runs back through :attr:`~turbohtml.Node.html`, :meth:`~turbohtml.Node.serialize`, and :meth:`~turbohtml.Node.encode`,
WHATWG-conformant by default with the escaping selectable through :class:`~turbohtml.Formatter`.

A :class:`~turbohtml.Minify` is a serialization mode on that same conformant tree, and its design rule is that the
minified bytes must reparse to the same tree: the hard part, a spec-correct parse, is already done, so minifying is only
allowed to drop or fold what the parser reconstructs on the way back in. That gives a single correctness gate:
``minify(parse(minify(parse(src))))`` equals ``minify(parse(src))``, checked across the html5lib-tests corpus and real
pages. Whitespace folds to one space rather than disappearing (a single space reparses in place, so the fold is
idempotent); optional tags are omitted only away from open formatting elements, because the adoption agency would
otherwise reconstruct one across the gap and shift the tree; and a value is unquoted only when no character could end or
re-open it. The transforms that would *not* round-trip (deleting whitespace between block elements, or omitting a tag
whose reparse changes nesting) are exactly the ones turbohtml declines to make.

***********************
 Exporting to Markdown
***********************

:meth:`~turbohtml.Node.to_markdown` is a second serializer that walks the same arena tree but emits GitHub-Flavored
Markdown instead of HTML. The survey of the field (the Python ``html2text``, ``markdownify``, and ``inscriptis``, Go's
``html-to-markdown``, and Rust's ``htmd``) converged on one architecture: a recursive visit over a real DOM (not a
streaming parse), with the block context threaded through the recursion rather than re-derived by walking parent
pointers. turbohtml already has the tree, so the exporter is a single pass over it into one growing buffer, classifying
each element as block (its own line, with collapsed blank-line margins) or inline (wrapped in a marker), the CSS
normal-flow distinction.

The one subtle part is whitespace, and it is where the reference libraries differ. turbohtml never emits a space from
text eagerly: a run of whitespace sets a pending flag, and the owed space is written only just before the next visible
character, and dropped at a line or block start. Because a closing emphasis marker does not flush that pending space, a
trailing space inside ``<b>bold </b>`` lands *after* the ``**`` rather than producing the invalid ``**bold **``; because
the opening marker is itself deferred until the first visible character, a leading space moves out the same way. The
common case (a run of plain prose with nothing to escape) is bulk-copied in one ``memcpy`` after its first character,
the borrow-or-copy fast path Rust's ``htmd`` uses.

Three places where the field is inconsistent, turbohtml does the correct thing: an inline code span is fenced with one
more backtick than the longest run inside it (so ``` `a``b` ``` never splits), a ``|`` inside a table cell is escaped,
and a nested ordered list keeps its own counter through the recursion stack rather than a single mutable field that a
naive implementation corrupts on nesting. The output is opinionated GFM with no options, validated both by golden cases
and by rendering it back to HTML with a reference Markdown engine and checking that no visible text was lost.

The walk holds no state outside its stack frame (no module-level buffers, no per-converter object), so two threads
exporting two trees never interfere, and the binding takes the same per-tree critical section
:attr:`~turbohtml.Node.text` and :attr:`~turbohtml.Node.html` use so a concurrent mutation cannot rewire the tree
mid-walk (a no-op under the GIL build). Where Go's ``html-to-markdown`` reaches for a mutex, the stateless visitor needs
none.

Where ``markdownify`` makes extensibility a subclass with a ``convert_<tag>`` method per tag, turbohtml exposes the same
power as a ``converters`` mapping: tag name to ``callable(element, content) -> str``. The C walk checks it only on an
element and only when the mapping is present (one ``NULL`` test on the no-hook path), so the dispatch is free unless a
tag is actually registered. When one matches, the engine renders that element's children into a sub-buffer (sharing the
document's reference-link accumulator), hands the callable a real :class:`~turbohtml.Element` and that inner Markdown,
and splices the result back into the stream with block or inline framing from the tag. The callable runs inside the
walk's critical section, so reading the element is safe; CPython suspends and resumes the section around any reentrant
tree access the callable makes, so it cannot deadlock.

A lighter knob unwraps whole tags without a callable: ``strip`` (a denylist) and ``convert`` (an allowlist), the same
pair ``markdownify`` carries. Both compile to one 256-bit set indexed by the interned tag atom, so the per-element test
is a constant-time bit lookup with no bound check: a stripped element renders its children in place of its own markup.
The interning is what makes a name the tag table does not know fold to no entry, mirroring how ``markdownify`` ignores a
tag it has no converter for.

******************************
 Annotation output processors
******************************

:meth:`~turbohtml.Node.to_annotated_text` walks the tree once and returns the rendered text together with a list of
``(start, end, label)`` spans over its code points. inscriptis pairs that extraction step with a separate set of *output
processors* that turn the spans into a usable artifact, and turbohtml keeps the same split:
:func:`~turbohtml.annotation_surface` and :func:`~turbohtml.annotation_tags` are pure transforms over the ``(text,
spans)`` pair, never the tree. They take no node, no arena, and no shared handle, so unlike the serializers they need no
critical section at all: the input string is immutable and the spans sequence is only read, which makes them
free-threading safe by construction rather than by locking. Keeping extraction (the tree walk) and rendering (the span
transform) apart means one walk can feed several renderings, and the renderings compose with any spans of that shape,
not only the ones :meth:`~turbohtml.Node.to_annotated_text` happens to emit.

The surface extractor is the easy half: bucket each span's ``text[start:end]`` slice under its label, in document order.
The inline-tagged exporter is where nesting has to be handled, because two spans can share a boundary. It expands each
span into an open and a close event and sorts them so the result is always well-formed: at one position a non-zero-width
span closes before any opens, an outer span opens before an inner one and closes after it (the innermost always closes
first), and a zero-width span emits its own ``<label></label>`` intact rather than splitting it across a neighbor's
boundary. The sort key carries the span's other endpoint and its original index, so the order is total and the output
deterministic even when several spans coincide.

*****************************
 Extracting strings (parsel)
*****************************

Scraping is string work: the caller wants ``"/p/42"`` or ``"42"``, not a node to read an attribute off. Scrapy's
``parsel`` made that the center of its API with ``::text`` / ``::attr()`` pseudo-elements and ``Selector.re()`` /
``.re_first()``, and the migration path needs the same primitives without bolting non-standard pseudo-elements onto the
CSS engine. turbohtml keeps the selector pure and adds the extraction step as three node methods instead.

:meth:`~turbohtml.Element.attr` returns the *raw* attribute value as one string: ``class="a b c"`` reads back as ``"a b
c"`` rather than the token list :attr:`Element.attrs <turbohtml.Element.attrs>` exposes, a valueless attribute as
``""``, and an absent one as the supplied default. It is the single-string counterpart to the live mapping, the one
``parsel``'s ``::attr(name)`` translates to. :meth:`~turbohtml.Node.re` and :meth:`~turbohtml.Node.re_first` run a
pattern (a ``str`` compiled once through :func:`re.compile`, or a pattern you compiled yourself) over the node's
:attr:`~turbohtml.Node.text`, or over an attribute value when ``attr=`` is given. They follow ``parsel``'s group rule
(yield the lone capturing group when the pattern has exactly one, else the whole match) because that is what makes a
single pattern pull just the digits out of ``Order 1138``. The regex itself stays in Python's battle-tested :mod:`re`;
only the source string is produced in C, under the same per-tree critical section :attr:`~turbohtml.Node.text` takes so
a concurrent mutation cannot rewire the subtree mid-read. Unlike ``parsel``, these run on one node rather than a whole
``SelectorList``, so a comprehension over :meth:`~turbohtml.Node.select` covers a page: the explicit loop the rest of
the query API also asks for, rather than a hidden fan-out.

**************************
 Finding the main content
**************************

:meth:`~turbohtml.Node.main_content` answers a different question than the exporters: not *how do I render this tree*
but *which part of it is the article*. The field (``readability`` and ``readability-lxml``, Mozilla's
``Readability.js``, and ``resiliparse``'s main-content extractor) converged on a content-density heuristic, and
turbohtml implements the same shape in C over the arena tree, so no Python object is built for a node that loses.

The walk scores *containers* by the prose they hold. Every paragraph-like element (``<p>``, ``<td>``, ``<pre>``) with at
least twenty-five characters of text contributes a base point, one point per comma it contains (commas approximate
clause count, a cheap proxy for real sentences), and up to three points for length, one per hundred characters. That
contribution is added to the paragraph's parent in full and to its grandparent at half weight, because the article body
is usually a container *around* the paragraphs, not the paragraph itself. Each container is also seeded once with a
structural weight from its tag (``<div>`` ``+5``; ``<blockquote>``/``<td>``/``<pre>`` ``+3``; lists and ``<form>``
``-3``; headings and ``<th>`` ``-5``) and a class/id weight (``+25`` for an ``article``/``content``/``post`` hint,
``-25`` for a ``sidebar``/``comment``/``footer`` one), the well-known readability signals.

Two prunings keep boilerplate out of the count. Subtrees that are never content (``<script>``, ``<style>``, ``<nav>``,
``<aside>``, ``<header>``, ``<footer>``, and the like, plus anything in a foreign SVG or MathML namespace) are skipped
wholesale, their text excluded even from a surrounding container's totals. A subtree whose class or id reads as
boilerplate (``comment``, ``modal``, ``share``, ``sidebar``, ``pagination``...) is dropped too, *unless* the same
attribute also carries a rescue hint (``article``, ``content``, ``main``), the case where a single element is tagged
both ways. Finally each surviving candidate's score is discounted by its link density (the fraction of its text that
sits inside an ``<a>``), so a dense menu that slipped through scores near zero, and the highest remaining score wins.
When nothing scores positively (a stub page, pure navigation), there is no winner and the method returns ``None``.

The heuristic has limits worth stating. It is tuned for article-shaped pages; a search-results grid, a forum thread, or
a single-page app rendered entirely from script has no dominant prose container and may return ``None`` or a surprising
node. It selects an existing element unchanged (it does not clean inline boilerplate *within* the winner the way
``Readability.js`` rewrites the DOM), so pair it with :class:`~turbohtml.sanitizer.Sanitizer` when you need a scrubbed
fragment. And it is content extraction only: language detection and WARC/web-archive handling, which ``resiliparse``
bundles alongside, are out of scope; reach for a dedicated tool there.

The whole scoring walk is pure C and allocates only a small candidate array (a linear find-or-insert map, since the
candidate set is just the parents and grandparents of scored paragraphs). It touches no Python object until a winner is
chosen, at which point the binding (holding the same per-tree critical section the other walks use) wraps that one node,
or renders its text for :meth:`~turbohtml.Node.main_text`. Two threads extracting from two trees never interfere.

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

****************
 Free-threading
****************

The extension holds no shared mutable state: inputs are immutable ``str`` objects, the lookup tables are read-only, and
each :class:`turbohtml.Tokenizer` owns its state machine, so tokenizers in different threads never contend. The
extension declares free-threading support and a per-interpreter GIL on interpreters new enough to honor those slots, so
it does not force the global lock back on under a free-threaded build. As with any stateful object, feeding one
tokenizer from several threads at once needs synchronization on the caller's side. See the `free-threading extension
guide <https://docs.python.org/3/howto/free-threading-extensions.html>`_.
