###################
 ElementTree views
###################

.. module:: turbohtml.etree

Use ``turbohtml.etree`` to port extraction code that uses element ``text`` and ``tail`` properties, child-element
iteration and XPath. A view shares its native DOM node; editing either representation changes the same tree. The adapter
runs in C and keeps one live view per node.

.. code-block:: pycon

    >>> from turbohtml import etree
    >>> article = etree.fromstring("<article>Before<b>bold</b>after</article>")
    >>> article.text, article[0].text, article[0].tail
    ('Before', 'bold', 'after')
    >>> list(article.itertext())
    ['Before', 'bold', 'after']
    >>> article[0].tag = "strong"
    >>> etree.tostring(article, encoding="unicode")
    '<article>Before<strong>bold</strong>after</article>'

``ElementView(node)`` wraps an existing ``turbohtml.Element``. Its ``node`` property gives access to DOM operations.
``attrib`` exposes string values, including ``class``; the native DOM's attribute mapping can expose a class list. Child
indices and slices count elements, excluding text and comments.

*********
 Parsing
*********

``document_fromstring(markup, /)`` returns an HTML root. ``fromstring(markup, /)`` returns a single element for a
single-element snippet, a ``span`` or ``div`` around sibling content, or an HTML root for a document or metadata
fragment. Both accept strings and bytes. ``document_fromstring`` uses turbohtml's encoding detection for bytes;
``fromstring`` decodes bytes as UTF-8 and replaces invalid sequences. Decode other encodings before calling
``fromstring``.

These extraction parsers use HTML5 tree construction, remove comments and processing instructions, and merge adjacent
text nodes. They preserve metadata between explicit head and body tags and drop empty implicit paragraphs. Use
``ElementView(turbohtml.parse(source).root)`` to retain the parser's DOM without those extraction adjustments.

``fragment_fromstring(markup, create_parent=False)`` accepts a string and requires one element. Pass ``True`` to wrap
multiple elements in a ``div``, or pass a tag name for another parent. A detached fragment retains its trailing text.

**********************
 Mutation and queries
**********************

``Element(tag, attrib=None, **extra)`` creates a detached element. ``SubElement(parent, /, tag, attrib=None, **extra)``
attaches one to a parent. ``append``, ``insert`` and ``replace`` move elements with their tails. ``remove`` detaches an
element and retains its tail; ``drop_tree`` leaves that tail in the former parent, so the dropped element has none.
``drop_tag`` unwraps the element. Assigning a string to ``text`` or ``tail`` writes it into the first ``Text`` node of
that run and removes the others, so a held ``Text`` sees the new value and a mutation observer records a
``characterData`` change; assigning ``None`` removes the run.

``iter`` and ``iterdescendants`` traverse elements in document order. ``iterchildren``, ``iterancestors`` and
``itersiblings`` select the corresponding axis. Tag filters accept strings, iterables of strings and ``*``. ``itertext``
returns a snapshot of text runs; ``with_tail=False`` omits descendant tails.

``xpath`` and ``XPath`` support turbohtml's XPath expressions and variable bindings. ``find``, ``findall`` and
``iterfind`` use XPath expressions, rather than ElementPath expressions. They return element views and discard scalar or
attribute results. Absolute ``//`` expressions on a detached tree search that tree.

Extraction pipelines that detach nodes while evaluating absolute XPath expressions can decorate their entry point with
``document_context``. Queries on removed nodes then retain the document of origin until the call returns. Nested
decorated calls share that context.

*****************
 lxml boundaries
*****************

Views do not satisfy lxml's C extension type checks. Keep lxml objects at public boundaries that promise lxml methods,
and use ``from_lxml``, ``to_lxml`` or ``to_lxml_html`` to copy across that boundary. The HTML variant supports methods
such as ``iterlinks`` and ``make_links_absolute``. These copies require the optional ``lxml`` package.

The adapter covers extraction operations; it does not implement lxml's complete API. Parsing repairs, namespace handling
and invalid-name normalization can differ. ``tostring`` defaults to XML-style serialization and ASCII bytes; pass
``encoding="unicode"`` for a string or ``method="text"`` for text content.
