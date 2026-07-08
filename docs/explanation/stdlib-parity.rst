###############################
 Matching the standard library
###############################

``turbohtml`` reproduces the exact behavior of :func:`python:html.escape` and :func:`python:html.unescape`. ``escape``
uses the same replacements, including ``&#x27;`` for the single quote, and ``unescape`` applies the full `HTML5
character-reference rules <https://html.spec.whatwg.org/multipage/named-characters.html>`_: named references with
longest-prefix matching, numeric references, the Windows-1252 remaps, and the invalid code-point handling that maps to
``U+FFFD`` or the empty string. The test suite checks the C output against the standard library over a large fuzzed
corpus.

Unicode normalization is stdlib parity from the string side: :func:`turbohtml.detect.normalize` reproduces
:func:`python:unicodedata.normalize` across all four forms (NFC, NFD, NFKC, NFKD), and
:func:`turbohtml.detect.is_normalized` its membership test. What differs is where the Unicode version lives.
``unicodedata`` tracks the interpreter's Unicode version, so the same call can decompose a character one way on Python
3.12 (Unicode 15.0) and another on 3.14 (16.0); turbohtml pins one version (16.0) into a generated table, the way ICU
pins a version, so its output is fixed whatever interpreter runs it -- the property a store-then-compare pipeline needs.
The table is generated from the interpreter's own ``unicodedata`` at that version, so the C engine is an exact
reimplementation of the same data, and a quick check (UAX #15) hands back already-normalized text -- the common case --
without decomposing it. The four-form pipeline (decompose, canonical-order, recompose) is C; the Python layer only maps
the form name and calls it. The engine is validated against the Unicode Consortium's own ``NormalizationTest.txt`` for
16.0 (19,965 rows, all pass), covering every header invariant plus the rule that code points absent from its Part 1 are
left unchanged by all four forms.

Template engines need a different contract: `markupsafe <https://markupsafe.palletsprojects.com>`_'s, where escaping
produces a ``Markup`` safe-string that records "this is already HTML" and combining it with untrusted text escapes that
text. :mod:`turbohtml.migration.markupsafe` is a drop-in for markupsafe's public surface, down to the numeric
``&#34;``/``&#39;`` quote references, so a `Jinja2 <https://jinja.palletsprojects.com>`_ or `WTForms
<https://wtforms.readthedocs.io>`_ project migrates by changing the import. It lives in a module apart from
:func:`turbohtml.escape` so each stays byte-exact with its own target: ``turbohtml.escape`` with the standard library,
``turbohtml.migration.markupsafe.escape`` with markupsafe. turbohtml builds the ``Markup`` in C in one call, where
markupsafe pays a Python call and a ``Markup`` construction on every interpolation, so it runs faster.

Linkifying needs the same HTML awareness from the other direction. :mod:`turbohtml.clean` parses the input first, so it
can see that a URL already sits inside an ``<a>`` or a ``<script>`` and leave it alone, which a regex over the raw
string cannot. The scan for link candidates is the trigger-then-expand model the Rust ``linkify`` crate uses, kept in C:
it looks for the few bytes that can start a link (``:`` for a scheme, ``@`` for an email, ``.`` for a bare domain) and
expands outward from each, rather than backtracking a regex. A bare domain counts only when its last label is a real
TLD, matched against a generated IANA table the same way the tag and entity tables are built. The Python layer owns the
tree walk and the callbacks; the C layer owns the byte scan.

Sanitizing untrusted HTML needs the tree, not the tokens. :mod:`turbohtml.clean` parses the input, walks the tree
dropping everything not on an allowlist, and serializes once. There is no serialize-then-reparse round trip: that round
trip is where mutation XSS lives, because the second parse can read the "safe" string differently than the first
(foreign-content and raw-text confusion), and a sanitizer that filters the same tree it will serialize cannot be fooled
that way. A non-overridable baseline removes ``<script>``, ``on*`` handlers, and ``javascript:`` URLs below the
configurable allowlist, so a policy cannot route around the unsafe set, and the test suite asserts the property
directly: ``sanitize(sanitize(x)) == sanitize(x)`` across an adversarial corpus, so any input whose cleaned form cleans
differently a second time fails the build. Only the :class:`~turbohtml.clean.Policy` facade is Python; the filtering
walk (the allowlist checks, the URL-scheme parsing, the escape/strip/remove of each node) runs in C against the parsed
tree, which is why it sanitizes faster than the Rust nh3.

Enumerating a page's links is the same tree walk from a third angle. :meth:`~turbohtml.Node.links` finds every
link-bearing location in C: ``<a href>``, the URLs hidden in ``srcset`` candidate lists, a ``<meta refresh>`` redirect,
and CSS ``url()``/``@import`` in a ``style`` attribute or a ``<style>`` sheet, so the capability no hand-rolled
``find_all`` loop reaches comes for free. The walk locates the URL spans and splices replacements back in place; the URL
resolution itself is :func:`urllib.parse.urljoin` from the standard library, deliberately *not* reimplemented in C,
because RFC 3986 reference resolution is a solved, standard problem and not where turbohtml's value lies. The line
between the two is the project's rule in miniature: the HTML-specific, performance-sensitive work is C, and a thin typed
facade wires it to the stdlib.

Generating HTML is the one place a builder helper earns a little Python of its own. :data:`turbohtml.build.E` adds no
tree mechanics: every ``E.<tag>(...)`` call constructs a real :class:`~turbohtml.Element` and appends its children
through the same C edit surface, so the helper is pure construction sugar -- the way ``lxml.builder.E`` is pure Python
over libxml2's tree. The tree, the attribute storage, and the serialize step all stay in C; the Python layer only spells
the call ``E.div({...}, child, "text")`` instead of three statements. Keeping it thin is deliberate: a generator that
reimplemented escaping or attribute handling in Python would drift from the parser's behavior, and the value of building
on the same tree is that what you generate serializes by exactly the rules that parse it back.
