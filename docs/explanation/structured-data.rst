############################
 Structured-data extraction
############################

:meth:`~turbohtml.Document.structured_data` answers the scraping question ``extruct`` and ``metadata_parser`` exist for:
what machine-readable metadata does this page embed? It pulls JSON-LD, Microdata, OpenGraph/Twitter card metadata, RDFa,
and Dublin Core in one walk, with the per-format helpers :meth:`~turbohtml.Document.json_ld`,
:meth:`~turbohtml.Document.opengraph`, :meth:`~turbohtml.Document.microdata`, :meth:`~turbohtml.Document.rdfa`, and
:meth:`~turbohtml.Document.dublin_core` beside it. The combined result is a frozen :class:`~turbohtml.StructuredData`
record with a stable six-field shape -- ``json_ld``, ``microdata``, ``opengraph``, ``rdfa``, ``dublin_core``, plus
``microformats`` reserved for a later phase -- so code that reads it does not break when that format lands. The records
hold no reference back into the tree, so they outlive the document they came from.

*********************
 Where the work runs
*********************

The division of labor is the same one the rest of the read path follows: the *locating* runs in C and the only genuinely
Python step stays in Python. A pure-C pass under the per-tree critical section walks the document once per format,
gathering the ``itemscope``/``itemprop``/``itemtype`` structure into nested :class:`~turbohtml.MicrodataItem` records
and the OpenGraph and Twitter ``<meta>`` pairs into a flat mapping, all holding no reference back into the tree. JSON-LD
is the one exception: the C walk gathers the verbatim text of each ``<script type="application/ld+json">`` block into a
list of strings, then the critical section is released and a thin facade parses them with the standard library
:mod:`json`. The JSON grammar is not reinvented in C, and the parse never touches the tree, so it cannot race a
concurrent mutation -- the snapshot-then-parse split is what keeps the Python call off the live structure. A block that
is not valid JSON is skipped rather than raising, the safe default for scraping a page whose markup the author did not
validate.

*******************************
 Microdata, OpenGraph, Twitter
*******************************

Microdata follows the HTML value algorithm: a property's value is the nested item dict when the element carries
``itemscope``, otherwise its ``content`` for a meta element, the relevant URL attribute for the link and media tags,
``datetime`` (or text) for a time element, and the element's text content elsewhere. Only top-level items -- an element
with ``itemscope`` and no ``itemprop``, so it is not a property of another item -- become list entries; a nested item is
reached through its parent's ``properties``. OpenGraph and Twitter share one mapping because pages mix the ``property``
and ``name`` attributes freely; when a key repeats, the last occurrence wins.

*******************
 RDFa, Dublin Core
*******************

RDFa reuses the Microdata item shape instead of emitting raw triples: a ``typeof`` element becomes a
:class:`~turbohtml.RdfaItem`, with ``typeof`` playing ``itemscope``'s role, ``property`` playing ``itemprop``'s,
``resource``/``about`` the subject, and a nested ``typeof`` the nested item. The RDFa-specific step is term expansion. A
bare ``property``/``typeof`` token joins the in-scope ``@vocab``, and a ``prefix:reference`` token joins the prefix's
IRI from the in-scope ``@prefix`` map, which starts from the RDFa 1.1 initial context so ``schema:``, ``dc:``, and
``foaf:`` resolve without a page-level declaration. ``@vocab`` and ``@prefix`` thread down the tree and can be
overridden per subtree (an empty ``@vocab`` clears it); a token whose prefix is undeclared, or a bare term with no
vocabulary in scope, stays verbatim. The object of a property follows the RDFa rules: the ``content`` literal, else a
nested item, else the ``resource``/``href``/``src`` IRI, else a ``<time>`` ``datetime``, else the text. turbohtml does
not expand CURIEs embedded in arbitrary IRIs or surface the ``datatype`` type IRI, so typed literals come back as their
lexical string. Dublin Core is a flat mapping like OpenGraph: the ``dc.*``/``dcterms.*`` ``<meta>`` names, lower-cased
so ``DC.Title`` and ``dc.title`` key alike, last occurrence winning. Microformats2 remains a later phase.
