###############################
 Matching the standard library
###############################

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
