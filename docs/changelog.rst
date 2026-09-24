###########
 Changelog
###########

.. towncrier-draft-entries:: Unreleased

.. towncrier release notes start

**********************
 v1.10.0 (2026-09-24)
**********************

Features - 1.10.0
=================

- Add ``DocumentFragment``: ``Range.extract_contents`` and ``Range.clone_contents`` return one, and every insertion
  method moves a fragment's children into place and leaves it empty, as the DOM insert algorithm does. (:issue:`857`)

Bug fixes - 1.10.0
==================

- Stop :func:`~turbohtml.clean.sanitize` escape mode from rendering a start tag the source never wrote: the ``tbody`` a
  bare ``<table><tr>`` implies, a formatting element the parser clones, or the skeleton of an empty document.
  (:issue:`807`)
- Keep the end tag of an element built with :mod:`turbohtml.build` when :func:`~turbohtml.clean.sanitize_node` escapes
  it, so ``<span>x</span>y`` no longer escapes as ``&lt;span&gt;xy``. (:issue:`808`)
- Match elements named like HTML tags (``title``, ``p``, ``div``) with CSS selectors on a :func:`~turbohtml.parse_xml`
  tree, so ``select("title")`` from the document and combinators such as ``p > x`` find them. (:issue:`809`)
- :class:`turbohtml.IncrementalParser` with ``source_locations=True`` reports the same absolute offsets as
  :func:`turbohtml.parse` when a tag spans two feeds, and ``to_source()`` on its document no longer reads freed memory.
  (:issue:`810`)
- Stop :func:`~turbohtml.clean.sanitize` from leaving a kept HTML element such as ``<style>`` directly under ``<svg>``
  or ``<math>`` when it escapes the ``desc``, ``title``, ``foreignObject``, or MathML text element that held it.
  (:issue:`811`)
- :class:`turbohtml.Tokenizer` with ``capture_source=True`` reports the full source of a tag split across two ``feed()``
  calls, where it used to truncate it or raise ``ValueError``. (:issue:`812`)
- ``minify_css`` keeps the zero units the ``flex`` shorthand needs: ``flex:0px`` and ``flex:1 1 0px`` no longer change
  grow or basis, and ``flex-basis:0%`` no longer becomes ``flex-basis:0``. (:issue:`813`)
- ``minify_css`` keeps zero units inside ``round()``, ``abs()``, ``hypot()``, ``mod()``, ``rem()`` and the other CSS
  Values 4 math functions, where ``round(0px,1px)`` used to become the invalid ``round(0,1px)``. (:issue:`814`)
- Make :func:`~turbohtml.clean.sanitize` keep HTML content under MathML ``annotation-xml`` only while the output still
  carries an ``encoding`` of ``text/html`` or ``application/xhtml+xml``. (:issue:`815`)
- :class:`turbohtml.IncrementalParser` honors a byte-order mark at the start of bytes input: the mark picks the encoding
  over the ``encoding`` argument and is stripped, as :func:`turbohtml.parse` does. (:issue:`816`)
- Stop :func:`~turbohtml.clean.sanitize` from keeping an HTML ``mglyph`` or ``malignmark`` under a MathML ``mi``,
  ``mo``, ``mn``, ``ms``, or ``mtext``, where a reparse turns it and its children into MathML. (:issue:`817`)
- A ``colgroup`` fragment or a template holding a ``<col>`` keeps the whitespace that follows ignored text, so
  ``parse_fragment("a b\nc", "colgroup")`` yields ``" \n"`` instead of an empty fragment. (:issue:`818`)
- ``Range`` operations raise ``IndexError`` or ``ValueError`` when a tree edit left a boundary past the end of its text,
  in another tree, or after the end, instead of reading out of bounds or crashing. (:issue:`819`)
- ``node.wrap(node)`` raises ``ValueError`` instead of making the node its own parent and looping. (:issue:`820`)
- :func:`turbohtml.parse_fragment` keeps the SVG mixed case on its root, so an ``"svg foreignObject"`` context returns a
  ``foreignObject`` element instead of ``foreignobject``. (:issue:`821`)
- Make :func:`~turbohtml.clean.sanitize_node` and ``transform_tags`` scrub every text run of a kept ``<style>``, drop
  its element children, and escape a ``</style`` in its body so the stylesheet cannot close the element early.
  (:issue:`822`)
- Appending a shadow host into its own shadow tree raises ``ValueError`` instead of creating a cycle that made
  ``assigned_nodes(flatten=True)`` loop forever. (:issue:`823`)
- Make :func:`~turbohtml.clean.sanitize` and the bleach ``clean`` shim drop ``javascript:`` URLs even when
  ``url_schemes`` or ``protocols`` lists ``javascript``, as the safety baseline documents. (:issue:`824`)
- ``minify_js`` keeps ``(0,o.f)()``, ``(1&&o.f)()`` and similar value callees, tags and ``delete``/``typeof`` operands
  as ``(0,o.f)`` instead of unwrapping them to ``o.f``, which changed ``this``, deleted properties and turned indirect
  ``eval`` direct. (:issue:`825`)
- Make ``strip_template_markers`` in :func:`~turbohtml.clean.sanitize` catch a marker whose halves end up adjacent once
  a comment or disallowed element between them is removed, stripped, or escaped, and markers inside an escaped tag.
  (:issue:`826`)
- Make :func:`~turbohtml.clean.linkify` leave the text of ``xmp``, ``iframe``, ``noembed``, ``noframes``, ``plaintext``,
  ``textarea``, ``title``, and scripting-parsed ``noscript`` untouched instead of dropping or nesting link text there.
  (:issue:`827`)
- Stop :func:`~turbohtml.clean.sanitize` from appending another ``;}`` on every pass to a ``<style>`` body that ends
  inside an unterminated string or comment; the trailing declaration is now dropped. (:issue:`828`)
- ``minify_js`` keeps a function declared in a block, and the binding its name reaches outside the block, when mangling:
  Annex B makes the function visible after the block, so dropping or inlining it broke ``typeof g`` and calls there.
  (:issue:`829`)
- ``minify_js`` keeps assignments to parameters in functions that read ``arguments``, which mirrors each parameter in
  sloppy code; ``function(a){a=2;return arguments[0]}`` used to lose the ``a=2``. (:issue:`830`)
- ``minify_js`` accepts a raw U+2028 LINE SEPARATOR or U+2029 PARAGRAPH SEPARATOR inside a string literal, legal since
  ES2019, instead of raising a lexical error. (:issue:`831`)
- ``wrap``, ``wrap_siblings``, ``insert_before``, ``insert_after``, ``replace_with``, ``unwrap`` and
  ``Range.insert_node`` no longer corrupt the tree or crash on the free-threaded build when another thread edits it
  while a node moves in from another tree. (:issue:`832`)
- ``minify_js`` and the HTML minifier read ``<!--`` and a line-leading ``-->`` as line comments in classic scripts, as
  Annex B requires; ``<script type=module>`` keeps them as operators. (:issue:`833`)
- ``Element`` and ``E`` keep the first of several attribute keys that differ only in ASCII case, matching the parser,
  instead of storing the attribute twice. (:issue:`834`)
- ``Range.surround_contents`` raises ``ValueError`` for a wrapper that is not an element, such as a ``Text`` node,
  instead of silently dropping the range contents. (:issue:`835`)
- ``minify_js`` output is a fixed point again when a binding named ``undefined`` exists: reads that reach the global
  ``undefined`` fold to ``void 0`` on the first pass instead of the second. (:issue:`836`)
- ``NodeIterator`` follows the DOM pre-remove steps: removing an ancestor of its current node, even from inside the
  filter, moves the iterator out of the removed subtree so iteration continues with the remaining nodes. (:issue:`837`)
- XPath predicates and steps on an attribute now use the attribute as the context node: ``//@n[. > 5]`` compares the
  attribute value and ``//@n/..`` returns the owner element. (:issue:`838`)
- Parse well-formed feeds in ``turbohtml.extract.feed()`` as XML, so CDATA summaries and content keep their markup, a
  channel ``<image>`` no longer overrides the feed title and link, and ``parse_xml(...).feed()`` reads RSS ``<link>``
  text. (:issue:`839`)
- ``Element.attrs`` implements the full ``MutableMapping`` interface: ``update``, ``pop``, ``popitem``, ``setdefault``,
  ``clear``, ``copy``, equality with any mapping, and ``|`` / ``|=``. (:issue:`840`)
- XPath name tests match SVG and MathML elements by their exact spelling: ``//foreignObject`` finds the SVG element and
  ``//foreignobject`` no longer does. (:issue:`841`)
- ``to_markdown()`` escapes text a CommonMark reader would parse as markup: list-item line starts such as ``1.``, ``~``,
  ``<`` and ``&`` before markup, a heading's trailing ``#``, and unbalanced parentheses in link destinations.
  (:issue:`842`)
- Nodes adopted between ``parse_xml`` and HTML trees take the destination tree's naming and raw-text rules, and
  ``set_inner_html`` / ``insert_adjacent_html`` on an XML element use the XML fragment parser. (:issue:`843`)
- Apply each input type's value sanitization in ``Element.form_data()`` (newlines stripped, ``url``/``email`` trimmed,
  invalid ``number`` and date values emptied, ``range`` clamped), normalize textarea line breaks, and skip controls
  inside ``<datalist>``. (:issue:`844`)
- Stop a ``rowspan`` at the end of its ``<thead>``/``<tbody>``/``<tfoot>`` in ``Element.rows()``, ``records()``, and
  ``Node.tables()``, and return ``<tfoot>`` rows last, as the WHATWG table model does. (:issue:`845`)
- Type ``Node.xpath()``, ``xpath_one()``, ``xpath_iter()``, and ``XPath.__call__`` with the full result union: numbers,
  booleans, and strings from scalar expressions, and any node kind in a node-set. (:issue:`846`)
- List a microdata value once per property name when an ``itemprop`` attribute repeats a name, as in ``itemprop="a a"``.
  (:issue:`847`)
- Skip a JSON-LD block that uses the non-JSON ``NaN``, ``Infinity``, or ``-Infinity`` literals in
  ``Document.json_ld()``, as the docs promise for invalid JSON, instead of returning ``nan`` or ``inf``. (:issue:`848`)
- XPath ``round()`` no longer rounds up the largest doubles below a half or integers past 2^52, and returns negative
  zero for arguments in ``[-0.5, 0)``; ``substring()`` positions round the same way. (:issue:`849`)
- Pickling a ``Document``, fragment, or ``ShadowRoot`` rebuilds its structure instead of reparsing serialized markup:
  doctype identifiers, quirks mode, adjacent text, template contents, and foreign-element case survive, and elements
  holding a ``<template>`` pickle again. (:issue:`850`)
- Stop ``Html(layout=Indent())`` from adding whitespace inside inline content, which changed the rendered text
  (``<p>a<b>b</b>c</p>`` reparsed as "a b c"); it now breaks lines only between block-level elements. (:issue:`851`)
- XPath ``lang()`` and CSS ``:lang()`` honor ``xml:lang``: an XML tree reads it, and on an HTML tree it overrides
  ``lang`` on SVG and MathML elements. ``//@xml:lang`` now matches those attributes. (:issue:`852`)
- Node insertion methods and ``Range.insert_node`` raise ``ValueError`` instead of putting a doctype outside a
  ``Document``, or a second doctype, a second root element, or a ``Text`` node directly into a ``Document``.
  (:issue:`853`)
- Compare attribute values exactly on a :func:`~turbohtml.parse_xml` tree: ``[type=checkbox]`` no longer matches
  ``type="CheckBox"``, since the HTML case-insensitive value list applies to HTML documents only. (:issue:`854`)
- Minify keeps the ``</p>``, ``</li>`` or ``</dd>`` of the last child of a phrasing parent such as ``<span>`` or
  ``<label>``, whose end tag does not close it on reparse. (:issue:`855`)
- XPath number literals and ``number()`` convert decimals to the nearest double, so ``0.49999999999999994 < 0.5`` holds
  and ``number(string(x))`` round-trips. (:issue:`856`)
- Inserting a range's contents or a ``ShadowRoot`` no longer links the fragment node itself into the tree; its children
  move into place and a shadow root stays attached to its host. (:issue:`857`)
- Minify keeps the doctype's public and system identifiers and a ``</p>`` before a ``<table>`` in quirks mode, so the
  output reparses in the same document mode and to the same tree. (:issue:`858`)
- Minify writes no ``</plaintext>`` and no end tag after a ``<plaintext>`` element, which the parser would read back as
  text, so minifying such a page is idempotent. (:issue:`859`)
- ``Formatter.NAMED_ENTITIES`` writes ``&lang;`` and ``&rang;`` for U+27E8 and U+27E9, their HTML5 meaning, and keeps
  U+2329 and U+232A literal, so the output reparses to the same text. (:issue:`860`)

*********************
 v1.9.0 (2026-09-14)
*********************

Features - 1.9.0
================

- Add native DOM whitespace collapse, comment removal, and ordered transformation composition. Serialize subtree
  children with ``inner=True`` on ``serialize``, ``encode``, and ``serialize_iter``. (:issue:`791`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of long guard-return sequences. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of declarations with single-use initializers.
  (:issue:`795`)
- Speed up descendant ``:has()`` selectors on nested elements. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of libraries containing boolean literals.
  (:issue:`795`)
- Speed up :meth:`canonical serialization <turbohtml.Node.serialize>` with explicit options. (:issue:`795`)
- Speed up importing turbohtml from an installed wheel. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``translate()`` with large character maps. (:issue:`795`)
- Speed up sanitization of elements with many rejected attributes. (:issue:`795`)
- Speed up :class:`XSLT <turbohtml.transform.Transform>` numbering with explicit element, wildcard or document-root
  patterns. (:issue:`795`)
- Speed up XML parsing of long text without character references. (:issue:`795`)
- Speed up HTML parsing with deeply nested formatting elements. (:issue:`795`)
- Speed up repeated :func:`computed-style <turbohtml.cssom.computed_style>` lookups on an unchanged tree. (:issue:`795`)
- Speed up HTML parsing with repeated scope checks in nested structures. (:issue:`795`)
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation with inherited simple-type facets. (:issue:`795`)
- Speed up repeated named-slot assignment queries on wide shadow roots. (:issue:`795`)
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with long conflicting declaration values.
  (:issue:`795`)
- Speed up parent queries over selections with shared parents. (:issue:`795`)
- Speed up :meth:`HTML serialization <turbohtml.Node.serialize>` with explicit options. (:issue:`795`)
- Speed up XML parsing of attributes with many namespace prefixes. (:issue:`795`)
- Speed up generation of the :func:`Unicode normalization <turbohtml.detect.normalize>` tables. (:issue:`795`)
- Speed up :meth:`form-data collection <turbohtml.Element.form_data>` from deeply nested disabled fieldsets.
  (:issue:`795`)
- Speed up :func:`language detection <turbohtml.detect.detect_language>` on text with many distinct trigrams.
  (:issue:`795`)
- Speed up queries combining many roots supplied out of document order. (:issue:`795`)
- Speed up :meth:`article extraction <turbohtml.Node.article>` from deeply nested content. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` equality comparisons between node sets. (:issue:`795`)
- Speed up positional CSS queries over wide sibling lists. (:issue:`795`)
- Speed up repeated default :class:`XSLT <turbohtml.transform.Transform>` ``level="any"`` numbering. (:issue:`795`)
- Speed up :func:`computed-style <turbohtml.cssom.computed_style>` matching of class-qualified selectors. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` inequality comparisons between node sets. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``str:replace()`` with sparse matches of long strings. (:issue:`795`)
- Speed up HTML parsing of long text with carriage returns. (:issue:`795`)
- Speed up HTML parsing that merges text around tables. (:issue:`795`)
- Speed up :meth:`Markdown wrapping <turbohtml.Node.to_markdown>` of long lines. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of mixed retained and removable declarations.
  (:issue:`795`)
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of elements with many instance attributes.
  (:issue:`795`)
- Speed up :class:`RELAX NG <turbohtml.validate.RelaxNG>` validation of optional groups and interleaves. (:issue:`795`)
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` when referenced properties are already ordered.
  (:issue:`795`)
- Speed up closest-ancestor queries over overlapping selections. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:intersection()`` on large node sets. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` that removes declarations from long statement
  lists. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``id()`` when its argument contains many short nodes. (:issue:`795`)
- Speed up streaming rewrites that add many attributes to a start tag. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``str:concat()`` over many short text nodes. (:issue:`795`)
- Speed up compilation of schema patterns with large character classes. (:issue:`795`)
- Speed up external-link extraction from URLs with many subdomains. (:issue:`795`)
- Speed up flattening nested shadow-slot assignments. (:issue:`795`)
- Speed up :class:`XSLT <turbohtml.transform.Transform>` applications with many template rules. (:issue:`795`)
- Speed up :meth:`canonical serialization <turbohtml.Node.serialize>` of deeply nested trees with xlink attributes.
  (:issue:`795`)
- Speed up :func:`JavaScript literal propagation <turbohtml.clean.minify_js>` within large multi-binding declarations.
  (:issue:`795`)
- Speed up :meth:`plain-text serialization <turbohtml.Node.to_text>` with explicit options. (:issue:`795`)
- Speed up repeated default :class:`XSLT <turbohtml.transform.Transform>` sibling numbering. (:issue:`795`)
- Speed up adding many attributes to an element. (:issue:`795`)
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with many rules sharing declaration bodies.
  (:issue:`795`)
- Speed up mutations observed only for unrelated event kinds. (:issue:`795`)
- Speed up repeated element path generation on an unchanged tree. (:issue:`795`)
- Speed up :meth:`table extraction <turbohtml.Node.tables>` with large row and column spans. (:issue:`795`)
- Speed up :func:`Unicode normalization <turbohtml.detect.normalize>` of long combining-mark sequences. (:issue:`795`)
- Speed up cloning partially selected :class:`Range <turbohtml.Range>` ancestors. (:issue:`795`)
- Speed up serialization of elements with large attribute sets. (:issue:`795`)
- Speed up publication-date extraction from text containing many distinct dates. (:issue:`795`)
- Speed up pruning selections that share matching ancestors. (:issue:`795`)
- Speed up shadow-slot assignment queries on hosts with many children. (:issue:`795`)
- Speed up :meth:`DOM normalization <turbohtml.Element.normalize>` of empty text nodes. (:issue:`795`)
- Speed up reading attributes from tokens with many attributes. (:issue:`795`)
- Speed up :class:`Range <turbohtml.Range>` operations on fully contained sibling intervals. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of long expression sequences. (:issue:`795`)
- Speed up :class:`XSLT <turbohtml.transform.Transform>` numbering with static predicates and union patterns.
  (:issue:`795`)
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of values with named-type facets. (:issue:`795`)
- Speed up :class:`XSLT <turbohtml.transform.Transform>` stylesheets with repeated numbering patterns. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` that merges long declaration lists.
  (:issue:`795`)
- Speed up :func:`computed styles <turbohtml.cssom.computed_style>` for complex selector alternatives. (:issue:`795`)
- Speed up repeated validation with schema patterns. (:issue:`795`)
- Speed up queries combining many detached roots. (:issue:`795`)
- Speed up :meth:`Markdown conversion <turbohtml.Node.to_markdown>` of long runs of punctuation. (:issue:`795`)
- Speed up :meth:`Node.equals() <turbohtml.Node.equals>` for elements with many attributes. (:issue:`795`)
- Speed up :meth:`article extraction <turbohtml.Node.article>` from pages with many content candidates. (:issue:`795`)
- Speed up XML parsing of elements with many distinct attributes. (:issue:`795`)
- Speed up queries spanning many documents. (:issue:`795`)
- Speed up JavaScript initialization-order checks across many variable pairs. (:issue:`795`)
- Speed up explicit :class:`XSLT <turbohtml.transform.Transform>` numbering across repeated source-node visits.
  (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:difference()`` on large node sets. (:issue:`795`)
- Speed up :func:`JavaScript literal propagation <turbohtml.clean.minify_js>` across many interleaved declarations.
  (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ordered numeric comparisons between node sets. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` of integer arrays. (:issue:`795`)
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of elements with many declared attributes.
  (:issue:`795`)
- Speed up :class:`Range <turbohtml.Range>` operations whose boundary is near the start of a sibling list.
  (:issue:`795`)
- Speed up iteration over :func:`SAX element records <turbohtml.saxparse.iter_events>`. (:issue:`795`)
- Speed up :func:`encoding detection <turbohtml.detect.detect>` of short inputs. (:issue:`795`)
- Speed up repeated document-wide radio-group updates. (:issue:`795`)
- Speed up XML parsing of long attribute values without character references. (:issue:`795`)
- Speed up :class:`XSD <turbohtml.validate.XMLSchema>` validation of decimals without numeric bounds. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:distinct()`` on large node sets. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` equality comparisons of long strings. (:issue:`795`)
- Speed up :meth:`DOM normalization <turbohtml.Element.normalize>` of adjacent text nodes. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``set:has-same-node()`` on large node sets. (:issue:`795`)
- Speed up HTML parsing with many active formatting elements. (:issue:`795`)
- Speed up sibling queries over selections with shared parents. (:issue:`795`)
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` with repeated item references. (:issue:`795`)
- Speed up :meth:`microdata extraction <turbohtml.Document.microdata>` when properties are local to their item.
  (:issue:`795`)
- Speed up HTML parsing of long text containing isolated NUL characters. (:issue:`795`)
- Speed up :func:`feed extraction <turbohtml.extract.feed>` from entries with extension fields. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` unions of results already in document order. (:issue:`795`)
- Speed up :func:`CSS minification <turbohtml.clean.minify_css>` with many mergeable media blocks. (:issue:`795`)
- Speed up :func:`JavaScript minification <turbohtml.clean.minify_js>` with interleaved unused bindings. (:issue:`795`)
- Speed up :func:`DOM linkification <turbohtml.clean.linkify_node>` of long non-ASCII text. (:issue:`795`)
- Speed up :class:`XSLT <turbohtml.transform.Transform>` applications with many named declarations. (:issue:`795`)
- Speed up :meth:`XPath <turbohtml.Node.xpath>` ``translate()`` on text with repeated characters. (:issue:`795`)
- Speed up :class:`streaming encoding detection <turbohtml.detect.EncodingDetector>` of short inputs. (:issue:`795`)
- Speed up :meth:`structured-data extraction <turbohtml.Document.structured_data>` from documents without metadata.
  (:issue:`795`)
- Skip repeated NUL scans in :class:`~turbohtml.IncrementalParser`. (:issue:`799`)
- Speed up :func:`~turbohtml.treebuild.parse_into` by reusing namespace strings within each parse. (:issue:`799`)
- Avoid repeated duplicate scans when :class:`~turbohtml.transform.Transform` indexes XSLT keys. (:issue:`799`)
- Avoid shifting existing variable bindings when :class:`~turbohtml.transform.Transform` enters and leaves scopes.
  (:issue:`799`)
- Speed up escaping disallowed tags with many attributes in :class:`~turbohtml.clean.Sanitizer`. (:issue:`799`)
- Skip disqualified encoding candidates in :class:`~turbohtml.detect.EncodingDetector`. (:issue:`799`)
- Speed up :attr:`~turbohtml.Node.text` by determining string width while collecting text. (:issue:`799`)
- Speed up text emission in :class:`~turbohtml.transform.Transform`. (:issue:`799`)
- Reduce rule matching work in :meth:`~turbohtml.Node.to_annotated_text` with many annotation rules. (:issue:`799`)
- Speed up :func:`~turbohtml.conformance.check` on nested sections without headings. (:issue:`799`)
- Speed up exact text filters in :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all`. (:issue:`799`)
- Speed up :class:`~turbohtml.Element` construction. (:issue:`799`)
- Speed up template-safe :func:`~turbohtml.clean.sanitize`. (:issue:`799`)
- Speed up Unicode range sorting in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)
- Skip impossible composition lookups in :func:`~turbohtml.detect.normalize`. (:issue:`799`)
- Avoid redundant string copies and integer conversions in :class:`~turbohtml.transform.Transform` numeric sorting.
  (:issue:`799`)
- Select only the highest-ranked trigrams when :func:`~turbohtml.detect.detect_language` scores text. (:issue:`799`)
- Avoid temporary text copies when :meth:`~turbohtml.Node.xpath` concatenates node strings. (:issue:`799`)
- Speed up zero terms with large exponents in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)
- Speed up boolean attribute filters in :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all`.
  (:issue:`799`)
- Avoid temporary function-rendering buffers in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)
- Avoid rescanning ASCII hosts in :func:`~turbohtml.extract.normalize_url`. (:issue:`799`)
- Speed up unescaped query keys in :func:`~turbohtml.extract.normalize_url`. (:issue:`799`)
- Speed up dot-segment handling in :func:`~turbohtml.extract.normalize_url`. (:issue:`799`)
- Speed up child and sibling ``:has()`` selectors in :meth:`~turbohtml.Node.select`. (:issue:`799`)
- Speed up :func:`~turbohtml.clean.sanitize` when checking allowed attribute prefixes. (:issue:`799`)
- Use indexed country-code lookup when formatting and validating :class:`~turbohtml.clean.PhoneNumber`. (:issue:`799`)
- Speed up phone table generation. (:issue:`799`)
- Speed up Unicode normalization table generation. (:issue:`799`)
- Skip unused attribute-name conversions in :class:`~turbohtml.clean.Sanitizer` prefix checks. (:issue:`799`)
- Reuse namespace declarations for consecutive copies of a literal element in :class:`~turbohtml.transform.Transform`.
  (:issue:`799`)
- Speed up literal conversion in :func:`~turbohtml.convert.css_to_xpath`. (:issue:`799`)
- Skip unchanged prefixes in :func:`~turbohtml.detect.normalize`. (:issue:`799`)
- Index Unicode digit ranges when parsing :class:`~turbohtml.clean.PhoneNumber`. (:issue:`799`)
- Reuse each form's first submit control when matching ``:default`` in :meth:`~turbohtml.Node.select`. (:issue:`799`)
- Stop temporal text scanning after the first valid date in :func:`~turbohtml.extract.dates`. (:issue:`799`)
- Reuse adjacent sibling positions in :meth:`~turbohtml.Node.select` nth selectors. (:issue:`799`)
- Use direct UTF decoders when :func:`~turbohtml.parse` reads UTF-8 or UTF-16 bytes. (:issue:`799`)
- Speed up single-character renaming in :func:`~turbohtml.clean.minify_js`. (:issue:`799`)
- Speed up named-entity serialization with :class:`~turbohtml.Html`. (:issue:`799`)
- Avoid intermediate metadata copies in :meth:`~turbohtml.Document.opengraph`. (:issue:`799`)
- Speed up :func:`~turbohtml.treebuild.parse_into` by avoiding temporary text copies. (:issue:`799`)
- Speed up attribute sorting with :class:`~turbohtml.Html` for elements with many attributes. (:issue:`799`)
- Speed up :meth:`~turbohtml.validate.RelaxNG.is_valid` and :meth:`~turbohtml.validate.XMLSchema.is_valid` for invalid
  documents. (:issue:`799`)
- Use direct combining-class lookup for common marks in :func:`~turbohtml.detect.normalize`. (:issue:`799`)

Bug fixes - 1.9.0
=================

- Avoid crashes when minifying detached subtrees containing adjacent ruby annotations or option groups. (:issue:`795`)
- Avoid crashes and stale results when selector-cache cleanup callbacks move or modify the queried node. (:issue:`795`)
- Preserve JavaScript initialization errors when a closure reads a ``let`` or ``const`` binding before initialization.
  (:issue:`795`)
- Protect Query parent, child and sibling traversal against concurrent mutation of the same tree on free-threaded
  Python. (:issue:`795`)
- Separate multi-level :class:`XSLT <turbohtml.transform.Transform>` numbers with periods when the format contains no
  separator token. (:issue:`795`)
- Avoid invalid integer conversions when formatting large :meth:`XPath <turbohtml.Node.xpath>` numbers. (:issue:`795`)
- Fix memory leaks when schema validation reads text and CDATA nodes. (:issue:`795`)
- Keep unrelated benchmark inputs out of each timing worker's setup and report invalid inputs as errors. (:issue:`795`)
- Fix crashes when annotation exporters consume generators that yield temporary labels. (:issue:`795`)
- Keep a captured JavaScript ``var`` undefined when an earlier initializer invokes its closure. (:issue:`795`)
- Preserve JavaScript initialization errors when a switch-case jump skips a binding's initializer. (:issue:`795`)
- Release temporary validation buffers after each call when reusing an :class:`XSD <turbohtml.validate.XMLSchema>` or
  RELAX NG schema. (:issue:`795`)
- Keep :class:`RELAX NG <turbohtml.validate.RelaxNG>` definition state separate between concurrent validations using the
  same schema. (:issue:`795`)
- Preserve JavaScript initialization errors when a ``let`` or ``const`` binding is read before its initializer.
  (:issue:`795`)
- Release temporary text buffers after :func:`boilerplate extraction <turbohtml.extract.boilerplate>`. (:issue:`795`)
- Avoid concurrent writes to shared schema text during :class:`RELAX NG <turbohtml.validate.RelaxNG>` validation.
  (:issue:`795`)
- Prevent repeated descendant ``:has()`` queries from growing retained cache memory without new entries. (:issue:`795`)
- Prevent extra unwritten bytes in escaped output when escapable characters occupy adjacent byte positions.
  (:issue:`795`)
- Handle empty leading text nodes when serializing ``pre``, ``textarea`` and ``listing`` elements. (:issue:`795`)
- Avoid crashes during :meth:`form-data collection <turbohtml.Element.form_data>` when a garbage-collection callback
  detaches a control's ancestor. (:issue:`795`)
- Include the ``of`` selector list when calculating ``:nth-child()`` and ``:nth-last-child()`` specificity.
  (:issue:`795`)
- Propagate exceptions raised while looking up an object's ``__html__`` method during MarkupSafe-compatible escaping.
  (:issue:`795`)
- Preserve JavaScript variable behavior when duplicate declarations include an uninitialized declaration. (:issue:`795`)
- Prevent buffer overruns when :meth:`XPath <turbohtml.Node.xpath>` regular-expression flags contain repeated letters.
  (:issue:`795`)
- Avoid concurrent writes while resolving the reserved ``xml`` namespace during schema validation. (:issue:`795`)
- Preserve the receiver type in :meth:`~turbohtml.Node.strip_tags` annotations. (:issue:`796`)
- Preserve the receiver type in :meth:`~turbohtml.Node.unwrap` annotations. (:issue:`796`)
- Preserve the receiver type in :meth:`~turbohtml.Node.prune` annotations. (:issue:`796`)
- Preserve the input node type in :func:`~turbohtml.clean.strip_comments_node` annotations. (:issue:`796`)
- Preserve the receiver type in :meth:`~turbohtml.Node.extract` annotations. (:issue:`796`)
- Preserve the input node type in :func:`~turbohtml.clean.sanitize_node` annotations. (:issue:`796`)
- Preserve the wrapper type in :meth:`~turbohtml.Range.surround_contents` annotations. (:issue:`796`)
- Preserve the input node type in :func:`~turbohtml.clean.sanitize_report_node` annotations. (:issue:`796`)
- Preserve the input node type in :func:`~turbohtml.clean.linkify_node` annotations. (:issue:`796`)
- Preserve the receiver type in :meth:`~turbohtml.Node.remove` annotations. (:issue:`796`)
- Infer :func:`~turbohtml.clean.transform_node` results from the input and callback types. (:issue:`796`)
- Preserve the input node type in :func:`~turbohtml.clean.collapse_whitespace_node` annotations. (:issue:`796`)
- Preserve prefix encoding errors when sanitizing attributes without exact-name rules. (:issue:`799`)
- Preserve numeric mantissas longer than 128 digits in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)
- Prevent numeric exponent overflow in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)
- Preserve benchmark notes for libraries that use different labels across operations in migration tables. (:issue:`799`)
- Bound decimal expansion of exponent notation in :func:`~turbohtml.clean.minify_css`. (:issue:`799`)

*********************
 v1.8.0 (2026-09-08)
*********************

Features - 1.8.0
================

- ``PhoneNumbers(collapse_whitespace=True)`` reads a run of HTML whitespace between the parts of a number as the one
  space it renders as, so a number a source formatter or a template broke across a line still links. (:issue:`758`)
- Detect a URL whose authority is a single-label host or an IP literal when its scheme is written, so
  ``http://localhost:8000/path``, ``http://intranet/`` and ``http://[::1]:8080/`` link. A bare domain still needs a dot
  and a known top-level domain to be told apart from an ordinary word.

  Recognize an internationalized top-level domain written as its Unicode label, so ``президент.рф`` links the way
  ``президент.xn--p1ai`` already did. (:issue:`768`)

- Add ``unique=True`` to :meth:`LinkDetector.find <turbohtml.clean.LinkDetector.find>`, which keeps the first span of
  each distinct URL. (:issue:`770`)
- Move the work behind the shipped link callbacks :func:`~turbohtml.clean.nofollow` and
  :func:`~turbohtml.clean.target_blank` into the C core. They keep their names, their signatures and their behavior,
  with one correction: the web-scheme test now matches the whole scheme, so ``ht:`` and ``httpx:`` no longer count as
  web links. (:issue:`774`)
- Move the :class:`~turbohtml.query.Query` facade's set algebra into the C core: deduplicating by node identity,
  collecting siblings, the combined ``text``, the joined attribute read, and the four class operations. Behavior is
  unchanged. (:issue:`775`)
- Move the encoding detector's candidate ranking into the C core: deduplicating the scored candidates, normalizing each
  score to its share, promoting the detector's own winner, and applying the :class:`~turbohtml.detect.Detection`
  allowlist, exclusions, language preference and confidence floor. Results are unchanged. (:issue:`776`)
- Move the boilerplate classifier into the C core: segmenting a page into paragraph units, collapsing each unit's text,
  and deciding which units are article content against the :class:`~turbohtml.extract.Extraction` thresholds. Results
  are unchanged. (:issue:`777`)
- Move the structured-data shaping into the C core: which JSON-LD blocks carry data, and rendering a
  :class:`~turbohtml.MicrodataItem` as nested plain dicts. Decoding each block stays with the standard library's JSON
  parser. Results are unchanged. (:issue:`778`)
- Move the publication-date stages into the C core: the canonical-URL, ``<meta>``, JSON-LD, ``<time>`` and visible-text
  signals :func:`~turbohtml.extract.dates` reads now run in one walk each. Results are unchanged. (:issue:`779`)
- Move the URL cleaning pipeline into the C core: :func:`~turbohtml.extract.normalize_url`,
  :func:`~turbohtml.extract.clean_url` and :func:`~turbohtml.extract.extract_links` now run their normalization, gates
  and anchor walk in one C call each. Results are unchanged. (:issue:`780`)
- Move the conformance verdict and the severity views into the C core: :func:`~turbohtml.conformance.check` reads
  ``valid`` from the walk, and ``errors``/``warnings``/``infos`` filter there. Results are unchanged. (:issue:`781`)
- Move the query facade's traversal into the C core: :meth:`~turbohtml.query.Query.parent`,
  :meth:`~turbohtml.query.Query.children` and :meth:`~turbohtml.query.Query.closest` walk there, and a
  :class:`~turbohtml.query.Matcher` applies its ``limit`` and filters a node's children inside the walk. Results are
  unchanged. (:issue:`782`)
- Move :class:`~turbohtml.cssom.StyleDeclaration`'s accessors into the C core: which declaration wins a repeated
  property and the ``text`` serialization are computed there. Results are unchanged. (:issue:`783`)
- Move the encoding and language detectors' last decisions into the C core: which label a ``whatwg-*`` codec name
  resolves to, when a byte-order mark settles an :class:`~turbohtml.detect.EncodingDetector`, the no-match answer, and
  the language confidence floor. Results are unchanged. (:issue:`784`)
- Move the sanitizer's policy compilation into the C core: the ``rel`` value, the value allowlists, the style patterns
  and the transform rules a :class:`~turbohtml.clean.Sanitizer` indexes are built there. Results are unchanged.
  (:issue:`785`)
- Move the bleach shim's ``attributes`` translation into the C core: the flat, per-tag and callable shapes of
  :func:`turbohtml.migration.bleach.clean` compile there, and the per-tag predicates run through a C-bound filter.
  Results are unchanged. (:issue:`786`)
- Move the link and phone detectors' configuration folding into the C core: tag, top-level-domain, scheme and word
  lists, region codes, the phone type mask and the E.164 check of :class:`~turbohtml.clean.PhoneNumber` are computed
  there. Results are unchanged. (:issue:`787`)
- Move the whitespace fold of :meth:`turbohtml.migration.markupsafe.Markup.striptags` into the C core. Results are
  unchanged. (:issue:`789`)
- Move :mod:`turbohtml.build`'s argument sorting into the C core: a leading mapping becomes the attributes and a string
  becomes a text node there, for :data:`~turbohtml.build.E` and :func:`~turbohtml.build.document` alike. Results are
  unchanged. (:issue:`790`)
- Add :func:`~turbohtml.clean.sanitize_node` and :func:`~turbohtml.clean.sanitize_report_node`, the tree-to-tree
  sanitizer for a pipeline that parses once and serializes once; :func:`~turbohtml.clean.sanitize` and
  :func:`~turbohtml.clean.sanitize_report` also accept a parsed node. (:issue:`793`)
- Add :func:`~turbohtml.clean.linkify_node`, which links URLs, email addresses and phone numbers in an already parsed
  tree in place; :func:`~turbohtml.clean.linkify` also accepts a parsed node. (:issue:`794`)

Bug fixes - 1.8.0
=================

- Escape a ``|`` where turbohtml writes a Markdown table cell's content rather than over the finished cell, so a nested
  table no longer adds a backslash per level; a table, list or ``<br>`` in a cell keeps its HTML unless
  ``Markdown.Tables(cell_blocks="text")``. (:issue:`764`)
- Leave a URL whose scheme syntax is malformed entirely plain instead of linking its host on its own, so
  ``https:/nonsense.com`` no longer renders as ``https:/<a href="http://nonsense.com">nonsense.com</a>``. Link a written
  ``mailto:`` URI as one anchor covering its own scheme. (:issue:`766`)
- End a host before a trailing hyphen rather than dropping the whole link, so ``http://example.com-`` links
  ``http://example.com``. Treat a port outside 0-65535 as text, and stop reading an underscore in a host's last two
  labels as a bare domain, so ``_example.com`` stays plain while the ``_dmarc.example.com`` of a DNS record still links.
  (:issue:`767`)
- A ``<script type="application/ld+json">`` block that the decoder cannot process for a reason other than malformed
  JSON, such as nesting past the interpreter's recursion budget, now raises from :meth:`~turbohtml.Document.json_ld` and
  :func:`~turbohtml.extract.dates` instead of being skipped. (:issue:`788`)

Improved documentation - 1.8.0
==============================

- Add a :doc:`migration guide from urlextract <migration/urlextract>`, which maps its ``find_urls``/``has_urls`` surface
  onto :class:`~turbohtml.clean.LinkDetector` and states what turbohtml does not do: no DNS check, no bare IP addresses,
  no tunable stop characters, and no runtime download of the top-level-domain list. (:issue:`772`)

*********************
 v1.7.0 (2026-08-29)
*********************

Features - 1.7.0
================

- Link phone numbers as ``tel:`` anchors with a :class:`~turbohtml.clean.PhoneNumbers` setting; each match carries a
  :class:`~turbohtml.clean.PhoneNumber`, the class that parses and formats numbers. (:issue:`758`)

*********************
 v1.6.1 (2026-08-15)
*********************

Bug fixes - 1.6.1
=================

- Parse a document, or feed a first stream chunk, that opens with a carriage return without touching the not yet
  allocated input buffer; UndefinedBehaviorSanitizer reported the empty append as a zero offset applied to a null
  pointer. (:issue:`752`)

*********************
 v1.6.0 (2026-08-11)
*********************

Features - 1.6.0
================

- Compile reusable XSLT stylesheet state during :class:`~turbohtml.transform.Transform` construction. (:issue:`713`)
- Add ``allow_imports`` and ``import_root`` controls for XSLT import filesystem access. (:issue:`714`)

Bug fixes - 1.6.0
=================

- Process ``</p>`` and ``</br>`` as foreign-content breakout tokens so the resulting HTML elements follow the foreign
  root, matching the HTML standard and WPT. (:issue:`32`)
- Recheck sanitizer callback and forced-attribute rewrites against the mandatory baseline safety rules. (:issue:`708`)
- Reject SVG animation elements that can assign event handlers or script URLs at runtime. (:issue:`709`)
- Make XML tag-name queries case-sensitive, including names shared with known HTML tags. (:issue:`710`)
- Stop :meth:`~turbohtml.clean.LinkDetector.has_link` after the first match without allocating a span list.
  (:issue:`711`)
- Avoid building a whole-tree tag index for first-result and small-limit queries. (:issue:`712`)
- Move HTML-aware linkifier traversal and mutation into one native operation. (:issue:`715`)
- Parse living-standard HTML processing instructions as typed nodes and events while keeping reserved XML targets as
  comments. (:issue:`716`)
- Apply the living-standard ``keygen`` rules inside ``select`` elements. (:issue:`717`)
- Keep repeated ``nobr`` elements as siblings across table formatting scope. (:issue:`718`)
- Decode CSS escapes when checking sanitizer function names and URL schemes. (:issue:`721`)
- Complete deep XML and programmatic-tree operations without silent truncation or C stack exhaustion. Bound recursive
  Microdata and RDFa records at 400 levels, reject cyclic Microdata ``itemref`` graphs, and read combined metadata from
  one tree snapshot. (:issue:`722`)
- Remove a redundant :class:`list` cast from :meth:`turbohtml.query.Matcher.filter` under current ``ty`` releases.
  (:issue:`738`)
- Report unacknowledged self-closing start tags as HTML parse errors. (:issue:`743`)

*********************
 v1.5.1 (2026-07-27)
*********************

Bug fixes - 1.5.1
=================

- Match tag and attribute names case-sensitively on a :func:`turbohtml.parse_xml` document: an uppercase name like ``Y``
  reads through :attr:`~turbohtml.Element.attrs` instead of raising :exc:`KeyError`, CSS selectors tell ``[Attr]`` from
  ``[attr]`` and ``Child`` from ``child``, and :func:`pickle.loads`/:func:`copy.deepcopy` round-trip the tree without
  folding its names to the HTML parser's lowercase. (:issue:`695`)
- Normalize line endings in a :func:`turbohtml.parse_xml` document per XML 1.0 §2.11 across attribute values, CDATA
  sections, comments, and processing instructions: a literal ``CRLF`` folds to a single ``LF`` (one space in an
  attribute value) rather than two, matching the text-content path and every other conformant XML parser. (:issue:`696`)

*********************
 v1.5.0 (2026-07-21)
*********************

Features - 1.5.0
================

- Speed the C core across the read, query, validation, and extraction paths. Measured against the 1.4.0 release on a
  fixed workload, an id-selective XPath lookup runs about 260x faster, overlapping text search over 100x faster, Unicode
  normalization about 6x, microdata itemref resolution about 5x, Relax NG compilation about 4x, and CSS minification,
  conformance checking, attribute rewriting, date extraction, and computed-style resolution roughly 2x to 3x. Linear
  table scans give way to hashed or indexed lookups for tag atoms, XPath id tokens, microdata itemrefs, the Relax NG and
  XSD global tables, the encoding labels, and the entity names (:issue:`659`, :issue:`672`, :issue:`671`, :issue:`657`,
  :issue:`656`, :issue:`661`, :issue:`660`, :issue:`651`, :issue:`654`). The substring and literal-regex searches, the
  transform sort, and the CSS rule merge now bound their worst case instead of scanning to the end (:issue:`665`,
  :issue:`664`, :issue:`666`, :issue:`652`, :issue:`662`). The tokenizer name buffers, the table grid rows, and the DOM
  arena grow by doubling so a large document amortizes its allocation (:issue:`669`, :issue:`668`, :issue:`667`). The
  GB18030 decoder, the NFC normalizer, the attribute rewriter, and the IDNA mapper drop the branchy table walks that a
  common input never needed (:issue:`674`, :issue:`673`, :issue:`649`, :issue:`670`, :issue:`650`). Stylesheets cache
  their parse, list filtering batches, the language detector merges its trigram profiles, and date extraction trims its
  scan (:issue:`653`, :issue:`663`, :issue:`648`, :issue:`658`).

  Serialize documents faster. The clean-run scan that hunts the next character needing an escape steps four UCS-4 code
  points per SIMD probe rather than two, and since the tree stores text as UCS-4 this scan runs on every serialization.
  The serialize benchmark improves by about 24%, and the gain carries into the strip and sanitize operations that
  serialize their result (:issue:`676`). IDNA host normalization rejects a non-composing mark pair with one comparison
  ahead of the composition-table search, saving the probes a run of unaccented text would otherwise spend (:issue:`680`,
  :issue:`679`). (:issue:`676`)

- Number a run of siblings in one pass. ``xsl:number`` computes a level's number as the count of preceding siblings that
  match the count criteria, which rescanned the whole run for each sibling and cost time quadratic in the run length;
  ``number_counts`` was 10.13% of all instructions executed by the instruction-dense transform workload, the largest
  single entry ahead of every CPython symbol. The engine now carries the previous answer forward, since a node's number
  is its previous sibling's number plus whether that sibling counted, and holds the criteria alongside it so a run
  numbered under different criteria does not reuse it. Callgrind measures 5.9% fewer instructions over that workload
  (:issue:`687`). (:issue:`687`)
- Dispatch the ``html.parser`` adapter's tokens from C. :class:`turbohtml.migration.stdlib.HTMLParser` used to loop in
  Python over the token stream, building a token object and reading six of its fields for every token; the tokenizer now
  calls the ``handle_*`` methods itself, binding them once per feed and passing the strings straight through. Under
  Callgrind that removes 30.8% of the instructions the whole workload executes and 46.7% of its indirect branches, which
  is what the interpreter spends on dispatch. The adapter now leads lxml's native target parser by 1.7 to 2.7 times
  where it previously trailed it, and html.parser by 7.5 to 11.2 times. (:issue:`689`)
- Move the read path's per-item loops into C. :func:`turbohtml.saxparse.sax_parse` built a tuple, a typed record, and an
  isinstance chain for every event; the tokenizer now binds the six handler methods once and drives them off the tree
  walk, for 43.7% fewer instructions with instruction-cache misses down 71% on the WHATWG specification. URL cleaning
  (:func:`turbohtml.extract.clean_url`, :func:`~turbohtml.extract.normalize_url`,
  :func:`~turbohtml.extract.extract_links`), the :func:`~turbohtml.extract.dates` ``<meta>`` stage,
  :func:`turbohtml.query.escape_identifier`, and :func:`turbohtml.clean.linkify`'s scheme classification each moved
  their loops to C as well, for 3.5% to 56.8% fewer instructions on those operations. Differential runs against the
  retired Python across 1,484,640, 809,481, 360,282, 302,762, 38,590, and 4,828 inputs found one divergence: the URL
  resolver matches ``%2e`` in either case as the `WHATWG URL path state <https://url.spec.whatwg.org/#path-state>`_
  specifies, which no public call reaches because percent-encoding uppercases escape hex first.
  :func:`~turbohtml.saxparse.iter_events` keeps its typed records, since those are its public product.

  Other hot paths keep their Python entry and shed work. XSD validation resolves each schema element's qname once at
  compile time into a binary-searched table for 17.2% fewer instructions, built for XSD only after the eager cache
  slowed RELAX NG compilation by 8.15%. The link walk mints element wrappers on first use (14.8% fewer) and skips the
  URL join that already returns an absolute reference (67.8% fewer); the ``<meta>`` encoding prescan jumps inert bytes
  with ``memchr`` (8.67% fewer on ASCII); the gb18030 decoder maps astral pointers by arithmetic (13.4% fewer); and CSS
  property dispatch gates on the first byte (9.3% fewer on ``bootstrap.css``). The migration and benchmark tables also
  stopped over-claiming: a caveat marks only the rows of the operation it describes, a shared measurement shows in both
  columns, XPath labels render as inline code, and the CSS-stripper note gives the measured 1-3% size difference.
  (:issue:`692`)

Improved documentation - 1.5.0
==============================

- Refresh every benchmark comparison table in the performance and migration guides from a full profile-guided run, widen
  the read-path tables to the competitors the suites gained, and rewrite the surrounding prose to the measured numbers.
  (:issue:`686`)

*********************
 v1.4.0 (2026-07-11)
*********************

Features - 1.4.0
================

- Add ``capture_attributes=False`` for tag-and-text token streams, and reduce DOM allocation for large documents.
  (:issue:`640`)

Bug fixes - 1.4.0
=================

- Keep a :class:`turbohtml.Node` hash stable across cross-document adoption so sets and dictionaries can still find it.
  (:issue:`634`)
- Copy source subtrees from one state during DOM adoption, Range operations, and XSLT under free-threaded Python.
  (:issue:`635`)
- Recognize XSLT instructions by namespace URI and local name, including stylesheets with a default XSLT namespace or
  rebound prefix. (:issue:`636`)
- Raise :exc:`ValueError` with the import chain for circular ``xsl:import`` references. (:issue:`637`)
- Return :meth:`turbohtml.query.Query.find` results in document order across connected roots; retain root input order
  across disconnected trees. (:issue:`638`)

*********************
 v1.3.1 (2026-07-10)
*********************

No significant changes.

*********************
 v1.3.0 (2026-07-10)
*********************

Features - 1.3.0
================

- Run the C core on PyPy 3.10 and 3.11 through ``cpyext``, with the same conformance and byte-identical output as
  CPython; see :doc:`/explanation/interpreters` for what ``cpyext`` costs and for the three behaviors that do not carry
  over. (:issue:`623`)
- Decode the legacy encodings 1.2x to 1.5x faster by inlining each WHATWG decoder into its specialized loop, and stop
  reallocating :class:`turbohtml.IncrementalParser`'s decode scratch on every :meth:`~turbohtml.IncrementalParser.feed`.
  (:issue:`624`)

Bug fixes - 1.3.0
=================

- Raise :exc:`IndexError` for a negative subscript further back than a node's child count; ``node[-len - 1]`` answered
  the first child, where :class:`list` raises. (:issue:`623`)
- Resume the ISO-2022-JP escape state across a :class:`turbohtml.IncrementalParser` chunk boundary. A chunk ending on an
  incomplete escape sequence dropped back to the output state, so the next chunk read the escape's first byte as content
  and lost one ``U+FFFD``. (:issue:`624`)
- :func:`turbohtml.parse` no longer mojibakes two common inputs. Bytes that are valid UTF-8 but declare no encoding now
  decode as UTF-8 rather than reaching the windows-1252 fallback. Validity is a structural proof rather than a frequency
  guess, so it costs none of the candidate scoring ``detect_encoding=True`` adds, and pure ASCII still resolves to
  windows-1252, which decodes it to the same text. A ``<meta>`` charset sitting past the prescan's 1024-byte window now
  redoes the parse against what it declares, following the WHATWG "changing the encoding while parsing" step, so where
  the declaration sits in the document no longer changes the answer. Only a real ``<meta>`` element counts, so a charset
  written inside a ``<script>`` string cannot pose as one, and a byte-order mark or the ``encoding`` argument still
  settles the encoding.

  :attr:`turbohtml.Document.encoding_confidence` reports which step answered: ``"certain"`` when the document named its
  own encoding through a byte-order mark, the ``encoding`` argument, or a ``<meta>`` charset, ``"tentative"`` when the
  sniff guessed, and ``None`` for ``str`` input. :func:`turbohtml.detect.detect` reads bytes and has no tree to consult,
  so on a document whose only ``<meta>`` sits past the prescan window it stops at the prescan and can disagree with
  :func:`turbohtml.parse`.

  :attr:`turbohtml.Document.errors` reports every parse error the WHATWG tokenizer defines. It carried 8 of the 48 codes
  the algorithm names, so ``a\x00b`` and a malformed ``<!DOCTYPE>`` both parsed without a word. turbohtml now reports
  the other 45, along with the preprocessing errors a control, noncharacter, or surrogate code point raises by being in
  the input at all, interleaved with the tokenizer's in source order. Reading :attr:`~turbohtml.Document.errors` is what
  finds the preprocessing ones, so a parse that never asks for them does not pay to look. ``strict=True`` therefore
  raises on documents it used to accept, and :class:`turbohtml.IncrementalParser` keeps no source to walk, so it reports
  none of them. Nothing gated the ``errors`` arrays the vendored ``html5lib-tests`` tokenizer suite carries; they now
  gate the codes and their order on all 7032 cases, and the line and column on every BMP-only input. (:issue:`625`)

- The encoding detector ignored the domain the bytes came from. chardetng, which ``_c/encoding/detect.h`` ports, takes
  the host's rightmost DNS label and reads it three ways: the encoding that label expects gains a point, the encodings
  native to it keep their score, and the rest pay a penalty. The port dropped all three, so turbohtml scored a Czech
  page served from a ``.ru`` domain the same as one served from ``.cz``. :class:`turbohtml.detect.Detection` now carries
  a ``tld`` field that reaches the scoring, and leaving it unset detects as before. A differential test runs both
  implementations over all 133 labels chardetng's classifier carries.

  :class:`turbohtml.detect.EncodingDetector` no longer buffers the stream. It appended every chunk to a ``bytearray``
  and detected once over the concatenation at :meth:`~turbohtml.detect.EncodingDetector.close`, so a caller streaming a
  large file paid its length in memory, in a class documented as mirroring chardet's ``UniversalDetector``. Each
  candidate now carries its own state between feeds, and the only bytes the detector keeps are the leading 1024 the
  byte-order-mark check and the ``<meta>`` prescan read, which the spec bounds. Where the chunks fall does not change
  the answer: the stateful detector is the only implementation, so :func:`turbohtml.detect.detect` drives it with a
  single feed and the differential test against chardetng covers the scoring both paths share.

  A four-byte gb18030 sequence that resolved to no code point returned an error without naming the byte that caused it,
  leaving ``error_at`` at whatever the previous error had set. Reading a whole buffer at once never noticed, because the
  value that means "the input ended mid-sequence" is only ever set at the end. A resumed decoder did notice, and so
  would :class:`turbohtml.IncrementalParser`, which tells "wait for more input" from "this input is wrong" by that same
  value. (:issue:`627`)

- Report parse errors from :class:`turbohtml.IncrementalParser`, which collected none: the streaming tokenizer never
  attached an error sink, and the preprocessing errors were found by a pass over a source no stream keeps. Each chunk is
  now swept as it arrives. (:issue:`629`)
- Report a control character that follows a tab, form feed, or NUL inside the same eight bytes of input. The
  word-at-a-time skip built its mask from a borrow-based test whose bits are not exact per byte, so an ordinary low byte
  erased its neighbor's error. (:issue:`630`)

Improved documentation - 1.3.0
==============================

- Explain what running the C core on PyPy costs, in :doc:`/explanation/interpreters`, from a table the benchmark harness
  generates (``tox -e bench -- interpreters``) rather than from figures typed into prose. How the core adapts to
  ``cpyext`` moved to :doc:`/development/cpyext`, alongside the invariant that keeps CPython's machine code unchanged.
  (:issue:`623`)

Packaging updates - 1.3.0
=========================

- Publish PyPy 3.10 and 3.11 wheels (``pp310``, ``pp311``) for Linux, macOS, and Windows. They carry LTO but skip the
  profile-guided optimization the CPython Linux wheels use, since a profile trained under PyPy measures ``cpyext``
  rather than the C hot paths. (:issue:`623`)

*********************
 v1.2.0 (2026-07-09)
*********************

Backward incompatible changes - 1.2.0
=====================================

- :func:`turbohtml.detect.detect` reports ``windows-1252``, not ``ascii``, for pure-ASCII input;
  :class:`~turbohtml.detect.EncodingMatch` gained a trailing ``codec`` field; and :class:`~turbohtml.IncrementalParser`
  takes WHATWG labels, so ``latin-1`` raises where ``iso-8859-1`` works. (:issue:`622`)

Features - 1.2.0
================

- Add :attr:`EncodingMatch.codec <turbohtml.detect.EncodingMatch>`; ``data.decode(match.codec)`` reproduces what
  :func:`turbohtml.parse` saw, where ``match.encoding`` corrupted or raised. (:issue:`622`)

Bug fixes - 1.2.0
=================

- Lock free-threaded DOM tree reads and attribute views; concurrent mutation no longer crashes those readers.
  (:issue:`617`)
- Resolve local ``file://`` stylesheet URLs for ``xsl:import``; ``Path.as_uri()`` bases now load sibling imports.
  (:issue:`618`)
- Normalize configured ``Linkify.skip_tags`` names before matching parsed HTML tags; ``CODE`` now skips ``<code>`` text.
  (:issue:`619`)
- Keep controls inside the first ``<legend>`` child of a disabled ``<fieldset>`` in ``form_data()`` output.
  (:issue:`621`)
- Decode legacy bytes with the WHATWG decoders rather than CPython's same-named codecs, whose tables and error handling
  both differ: ``koi8-u`` is KOI8-RU, and GBK ``0x80`` is the euro sign. The ``<meta>`` prescan, the content detector,
  and :class:`~turbohtml.IncrementalParser` now follow the spec too. (:issue:`622`)

*********************
 v1.1.1 (2026-07-08)
*********************

No significant changes.

*********************
 v1.1.0 (2026-07-08)
*********************

Features - 1.1.0
================

- :class:`~turbohtml.clean.Policy` gained ``strip_template_markers``: with it on, sanitizing collapses template-engine
  expressions (``{{ }}``, ``${ }``, ``<% %>``) in kept text and attribute values to a single space, so the output cannot
  re-inject when a template engine renders it. This matches DOMPurify's ``SAFE_FOR_TEMPLATES``. (:issue:`527`)
- :func:`turbohtml.clean.sanitize_report` (and :meth:`turbohtml.clean.Sanitizer.sanitize_report`) sanitize a fragment
  and return what the policy dropped alongside the cleaned HTML: one :class:`turbohtml.clean.Removed` record per removed
  element or stripped attribute, in walk order. This matches DOMPurify's ``DOMPurify.removed``. (:issue:`528`)
- :func:`turbohtml.convert.css_specificity` returns the ``(a, b, c)`` specificity of each selector in a comma-separated
  list, per CSS Selectors Level 4 §17, the value ``cssselect`` exposes as ``Selector.specificity()``. It weighs the
  parsed selector in one C pass, with ``:is()``/``:not()``/``:has()`` taking their most specific argument and
  ``:where()`` contributing zero. (:issue:`529`)
- :func:`turbohtml.extract.feed` normalizes an RSS 2.0, Atom 1.0, or RDF/RSS-1.0 document into one frozen, typed
  :class:`~turbohtml.extract.Feed` of :class:`~turbohtml.extract.Entry` records, the ``feedparser.parse`` entry point
  over :meth:`turbohtml.Document.feed`. It detects the format from the root element and maps each dialect's spelling of
  a field -- the entry ``title``, ``link``, ``id``, ``updated``/``published``, ``summary``/``content``, and ``author``
  -- onto one shape in a single C walk of the parsed tree, over 12x faster than feedparser on a 30-item feed.
  (:issue:`530`)
- :class:`turbohtml.clean.Policy` gains ``transform_tags``: a map that renames elements while sanitizing,
  sanitize-html's ``transformTags``. Map a source tag to a string to rename it, or to a
  :class:`turbohtml.clean.Transform` to rename it and add attributes (sanitize-html's ``simpleTransform``). The rename
  runs before the allowlist in the same C walk, so the renamed element is re-checked from scratch -- a transform decides
  an element's name but never its safety: mapping a tag to ``script`` still drops it, and an added attribute is scrubbed
  like the element's own. (:issue:`531`)
- A new :mod:`turbohtml.saxparse` module adds a DOM-less, event-driven parse. :func:`turbohtml.saxparse.sax_parse`
  drives a document through the WHATWG tree builder and fires a callback on a :class:`turbohtml.saxparse.SaxHandler`
  subclass for each construct it builds -- a start tag, an end tag, a run of text, a comment, the doctype, and a
  ``<?...>`` processing instruction -- while :func:`turbohtml.saxparse.iter_events` yields the same stream as typed
  records. The events reflect the fully spec-correct tree (implied ``html``/``head``/``body``, foster parenting, the
  adoption agency), so unlike :class:`html.parser.HTMLParser` you see the tree the parser built; no per-node Python
  object is created and nothing is retained after the parse, so a one-pass extraction never builds a document-sized
  object graph. The tokenization, tree construction, and walk all run in C. (:issue:`532`)
- ``turbohtml.clean.Policy`` gains ``allowed_styles``, a per-element, per-property value allowlist for the ``style``
  attribute keyed ``{tag: {property: [pattern, ...]}}`` with ``"*"`` matching every tag. A declaration survives only
  when its value matches one of the property's patterns, porting sanitize-html's ``allowedStyles``. It narrows
  ``css_properties`` by value and never weakens the baseline that drops ``expression()`` and disallowed-scheme
  ``url()``. (:issue:`533`)
- :class:`~turbohtml.clean.Policy` gained ``isolate_named_props``: with it on, sanitizing prefixes every kept ``id`` and
  ``name`` value with ``user-content-``, moving it out of the property namespace so it cannot shadow a built-in
  ``document`` or form property through named access (DOM clobbering, where ``<input name="attributes">`` makes
  ``form.attributes`` resolve to the input and ``<img name="body">`` hides ``document.body``). An already-prefixed value
  is left alone, so re-sanitizing is a fixpoint. This matches DOMPurify's ``SANITIZE_NAMED_PROPS``. (:issue:`534`)
- :class:`~turbohtml.Html` gained ``xml``: with ``xml=True``, :meth:`~turbohtml.Node.serialize`,
  :meth:`~turbohtml.Node.encode`, and :meth:`~turbohtml.Node.serialize_iter` emit XML/XHTML instead of HTML -- the
  equivalent of lxml's ``tostring(method="xml")``. Every empty element self-closes (``<br/>``), foreign SVG and MathML
  subtrees carry their namespace declarations, and text and attribute values follow the XML escaping rules, with no HTML
  void-element or raw-text special casing. It composes with ``sort_attributes`` and an :class:`~turbohtml.Indent`
  layout. (:issue:`535`)
- :class:`~turbohtml.clean.Policy` gained a predicate-based custom-element allowance and split content profiles, porting
  DOMPurify's ``CUSTOM_ELEMENT_HANDLING`` and ``USE_PROFILES``. ``custom_element_check`` keeps an unlisted hyphenated
  custom element (``my-widget``, ``x-card``) when a caller-supplied matcher admits its name, ``custom_attribute_check``
  extends the same idea to that element's attributes, and ``allow_customized_builtins`` keeps an ``is`` attribute whose
  value names a custom element. ``allow_html``, ``allow_svg``, and ``allow_mathml`` gate each namespace independently,
  so a policy can keep SVG but drop MathML, or the reverse. All of it runs in the one C sanitize walk, and the
  non-configurable safety baseline -- ``on*`` handlers, ``javascript:`` URLs, unsafe tags -- still applies to whatever a
  matcher keeps. (:issue:`536`)
- :mod:`turbohtml.transform` adds a full XSLT 1.0 processor, the job `lxml <https://lxml.de>`_'s ``etree.XSLT`` does.
  :class:`turbohtml.transform.Transform` compiles a stylesheet (parsed with :func:`turbohtml.parse_xml`) and applies it
  to source documents, and :func:`turbohtml.transform.transform` does both in one call. The whole transform runs in the
  C extension, reusing turbohtml's XPath 1.0 engine for every match pattern and select expression. It covers the entire
  XSLT 1.0 instruction set: templates with ``match``/``name``/``mode``/``priority``, ``apply-templates`` with ``sort``
  and ``with-param``, ``call-template``, ``for-each``, ``if``, ``choose``, ``value-of``, ``copy``/``copy-of``,
  ``element``/ ``attribute``/``text``, ``variable``/``param``, multi-level ``number``, ``key`` with the ``key()``
  function, ``strip-space``/``preserve-space``, ``attribute-set`` with ``use-attribute-sets``, ``namespace-alias``,
  ``fallback``, simplified literal-result-element stylesheets, ``xsl:import`` with import precedence (resolved against a
  ``base_url``), ``cdata-section-elements``, and the ``xml``/``html``/``text`` output methods (html auto-selected for a
  null-namespace ``html`` root, with ``<meta>`` injection). Validated against libxslt's XSLT 1.0 Recommendation corpus
  at 76 of 79 cases byte-for-byte; the three remaining need a locale-collation, DTD, or XPath-namespace-axis layer
  turbohtml does not carry. (:issue:`537`)
- :meth:`turbohtml.Node.canonicalize` serializes a subtree to Canonical XML (c14n), the byte-exact form an XML signature
  signs. A :class:`turbohtml.Canonical` config selects the algorithm: Canonical XML 1.0 or 1.1, the exclusive variant
  that renders only the namespaces a subtree visibly uses, the with-comments variant, and an ``inclusive_ns_prefixes``
  prefix list for exclusive mode. Attributes are reordered (namespace declarations first, then by namespace URI and
  local name), redundant namespace declarations are dropped, empty elements are written as start-end pairs, and
  character references are normalized, matching ``lxml``'s ``tostring(method="c14n")`` byte-for-byte over the same
  infoset. (:issue:`538`)
- :class:`turbohtml.validate.XMLSchema` and :class:`turbohtml.validate.RelaxNG` validate a document parsed with
  :func:`turbohtml.parse_xml` against an XSD 1.0 or RELAX NG schema, mirroring lxml's ``etree.XMLSchema`` /
  ``etree.RelaxNG``. A schema compiles once in the C core and each :meth:`~turbohtml.validate.XMLSchema.validate`
  returns a :class:`~turbohtml.validate.ValidationResult` -- a ``valid`` flag plus one
  :class:`~turbohtml.validate.ValidationError` per violation, each with the document-order path that located it. XSD
  covers the element/attribute declarations, the sequence/choice/all content models with ``minOccurs``/``maxOccurs``,
  references, complex/simple types with extension, the built-in datatypes, and the constraining facets; RELAX NG covers
  the full XML-syntax pattern set (including ``interleave``) through the derivative algorithm. (:issue:`539`)
- :func:`turbohtml.parse_xml` parses a document under XML 1.0 well-formedness instead of the WHATWG HTML tree builder,
  returning the same navigable :class:`~turbohtml.Document`. Names stay case-sensitive, ``<x/>`` self-closes any
  element, CDATA sections and processing instructions become :class:`~turbohtml.CData` and
  :class:`~turbohtml.ProcessingInstruction` nodes, only the five predefined entities and numeric references resolve, and
  a namespace prefix must be declared with ``xmlns``. Names follow the exact XML 1.0 ``NameStartChar`` /``NameChar``
  productions, and the Namespaces in XML 1.0 well-formedness constraints hold in full: the reserved ``xml`` and
  ``xmlns`` prefixes and their namespace names cannot be rebound, a prefix declaration cannot be empty, a
  processing-instruction target carries no colon, and no two attributes share an expanded name. There is no HTML
  recovery: the first well-formedness violation -- a mismatched or unclosed tag, an undeclared prefix, an undefined
  entity, a duplicate attribute -- raises :exc:`~turbohtml.HTMLParseError`. This is the equivalent of
  ``lxml.etree.fromstring`` / ``etree.XMLParser`` over turbohtml's dependency-free, fully typed node API. (:issue:`540`)
- Added an HTML5 authoring-conformance checker with a severity model. :func:`turbohtml.conformance.check` walks a parsed
  document and returns a :class:`~turbohtml.conformance.ConformanceReport` -- a ``valid`` verdict plus every
  :class:`~turbohtml.conformance.ConformanceMessage`, each carrying a stable ``code``, a ``severity`` (``"error"``,
  ``"warning"``, or ``"info"``), a human-readable message, and a source line and column. It flags the
  document-conformance requirements the parser does not raise as a :class:`~turbohtml.ParseError`: a missing ``img``
  alt, obsolete presentational elements and attributes, duplicate ids, invalid or redundant ARIA roles, empty headings,
  a ``section`` without a heading, and a document with no title or ``lang``. The document is valid exactly when nothing
  is an error, so warnings and info notes never change the verdict. The whole walk runs in the C core against the WHATWG
  authoring rules and WAI-ARIA 1.2, the model the Nu Html Checker (validator.nu) uses;
  :func:`~turbohtml.conformance.check_html` parses a markup string first. (:issue:`541`)
- :meth:`~turbohtml.Node.xpath` gained the string subset of XPath 2.0: ``ends-with``, ``string-join(seq, sep)``,
  ``lower-case`` and ``upper-case`` (Unicode case mapping), and the regex ``matches(input, pattern[, flags])`` and
  ``replace(input, pattern, repl[, flags])`` spellings, where ``replace`` reads ``$1``-style group references and
  rewrites every match. They dispatch in the compiled-C engine alongside the XPath 1.0 core and the EXSLT namespaces, so
  an expression ported from ``elementpath``, ``lxml``, or ``htmlquery`` that leans on them runs without registration.
  (:issue:`542`)
- :func:`turbohtml.detect.normalize` returns text in a Unicode normalization form (UAX #15) -- ``NFC``, ``NFD``,
  ``NFKC``, or ``NFKD`` -- the C successor to :func:`python:unicodedata.normalize`, and
  :func:`turbohtml.detect.is_normalized` tests membership. Both run over tables generated from the interpreter's own
  ``unicodedata``, so they agree with it exactly, and a quick check returns already-normalized text without allocating.
  (:issue:`543`)
- :func:`turbohtml.rewrite.rewrite` transforms HTML in a single streaming pass without building a tree, the model
  Cloudflare's lol-html popularized. It runs the WHATWG tokenizer over the input while tracking only the open-element
  stack, hands each element a CSS selector matches -- and, on request, each run of text, each comment, and the doctype
  -- to a Python handler that edits it in place (set or remove an attribute, insert markup before, after, or around it,
  replace its inner content, unwrap it, or drop it), and emits the result incrementally. Working memory stays
  proportional to the open-element depth, not the document size, so a multi-megabyte page rewrites in a fixed footprint,
  and an untouched construct is reproduced verbatim. Because the pass never looks ahead, the matchable selector subset
  is the one decidable from an element and its ancestors -- type, universal, id, class, and attribute selectors, the
  descendant and child combinators, ``:root``, and ``:is()``/``:where()``/``:not()`` over that subset; a sibling
  combinator, a positional or structural pseudo-class, or ``:has()`` raises :class:`~turbohtml.SelectorSyntaxError`.
  (:issue:`544`)
- A new :mod:`turbohtml.treebuild` module retargets the parser at a tree of your own.
  :func:`turbohtml.treebuild.parse_into` runs the WHATWG tree builder and drives a builder object -- a ``create_*``
  method per node kind plus an ``append`` that links a child under its parent -- to construct the tree directly,
  returning whatever the builder made its document root. No navigable :class:`turbohtml.Node` is materialized and the
  tree is walked only once, so an index, a diff tree, or another library's nodes is populated straight from the parse
  rather than by a second descent. Each element carries its namespace URI and its attributes as ``(name, value)`` pairs,
  a ``<template>``'s content is appended under the template handle, and a bogus ``<?...>`` construct reaches a distinct
  ``create_pi``. This is Rust html5ever's ``TreeSink`` and Node parse5's ``TreeAdapter`` in turbohtml shape; the tree
  construction, the walk, and the string extraction all run in C. (:issue:`545`)
- :mod:`turbohtml.cssom` runs the CSS Object Model cascade: :func:`~turbohtml.cssom.computed_style` resolves the
  ``getComputedStyle`` of an element by collecting every ``<style>`` sheet plus the inline ``style`` along its ancestor
  chain, matching the native selector engine, ordering the declarations by origin importance, the style attribute,
  specificity, and source order, then applying inheritance, shorthand expansion, and each property's initial value --
  all in the C core under the per-tree critical section. Alongside it, :class:`~turbohtml.cssom.StyleSheet`,
  :class:`~turbohtml.cssom.RuleList`, :class:`~turbohtml.cssom.StyleRule`, and
  :class:`~turbohtml.cssom.StyleDeclaration` are the read-only, turbohtml-native spelling of the CSSOM ``CSSStyleSheet``
  / ``CSSRuleList`` / ``CSSStyleRule`` / ``CSSStyleDeclaration`` interfaces. The returned value is the computed value,
  not the used value: turbohtml runs no layout, so lengths and percentages come back as written, the same boundary jsdom
  and cssstyle draw. Shorthand expansion covers the distributive families (``margin``, ``padding``,
  ``border-width``/``style``/``color``, ``overflow``) and the ``<line-width> || <line-style> || <color>`` shorthands
  ``border``, each ``border-<side>``, and ``outline``, whose components resolve in any order and reset every longhand
  they cover. (:issue:`546`)
- :meth:`turbohtml.Node.to_source` losslessly serializes a tree back to HTML, re-emitting the verbatim source bytes of
  every element and text run a parse left untouched and reserializing only the parts a mutation changed. Parse with
  ``source_locations=True`` and an unedited round trip reproduces the input byte for byte -- author quoting, tag-name
  case, character-reference spelling, and insignificant whitespace intact -- for markup that parsed without implied
  elements or content reordering; after an edit only the changed node's markup is rewritten while every untouched
  sibling and subtree keeps its original span. It is the tree-based counterpart to the streaming
  :func:`turbohtml.rewrite.rewrite`, the model Cloudflare's lol-html popularized. (:issue:`547`)
- :func:`turbohtml.parse`, :func:`turbohtml.parse_fragment`, and :class:`~turbohtml.IncrementalParser` gained a
  ``source_locations`` flag (default ``False``) that records the granular source spans parse5 exposes as
  ``sourceCodeLocationInfo``. With it on, each element's :attr:`~turbohtml.Node.source_location` returns a
  :class:`~turbohtml.SourceLocation` giving the :class:`~turbohtml.SourceSpan` of its start tag, its end tag (``None``
  when the source never closed it), and each attribute's whole ``name="value"``, every span carrying start/end line,
  column, and code-point offset so ``source[start_offset:end_offset]`` slices the construct out. The tokenizer stamps
  the spans in C as it runs and the tree builder hangs the record off each element, so the feature is zero-overhead when
  off; it implies ``positions`` when on, keeping :attr:`~turbohtml.Node.source_line` and
  :attr:`~turbohtml.Node.position` available beside the spans. (:issue:`548`)
- Added declarative Shadow DOM to the parser. When the tree builder meets a ``<template>`` carrying a ``shadowrootmode``
  of ``open`` or ``closed`` on a valid shadow host, it attaches a shadow root to the template's parent and parses the
  template's content into it, reusing the Shadow DOM tree model -- the template element never joins the light tree.
  ``shadowrootdelegatesfocus`` and ``shadowrootclonable`` set the matching flags, readable as the new
  :attr:`~turbohtml.ShadowRoot.delegates_focus` and :attr:`~turbohtml.ShadowRoot.clonable` properties. Following the
  WHATWG per-document flag, :func:`turbohtml.parse` honors declarative shadow roots by default (a browser navigation)
  while :func:`turbohtml.parse_fragment` does not (an ``innerHTML`` assignment); the new
  ``allow_declarative_shadow_roots`` argument flips either default, matching ``setHTMLUnsafe`` when turned on for a
  fragment. (:issue:`549`)
- Added the DOM Living Standard traversal objects :class:`turbohtml.TreeWalker` and :class:`turbohtml.NodeIterator`,
  with the :class:`turbohtml.NodeFilter` constants for the ``what_to_show`` bitmask and the filter verdicts. A
  ``TreeWalker`` is a movable cursor over a subtree -- ``parent_node``, ``first_child``, ``last_child``,
  ``next_sibling``, ``previous_sibling``, ``next_node``, ``previous_node`` -- while a ``NodeIterator`` is the flat,
  filtered forward/backward view and iterates directly in a ``for`` loop. Both take a ``what_to_show`` node-type mask
  and an optional filter callback returning ``FILTER_ACCEPT``, ``FILTER_REJECT``, or ``FILTER_SKIP``; reject drops a
  node and its whole subtree while skip drops only the node, so a ``TreeWalker`` prunes where a ``NodeIterator`` (having
  no subtree) treats the two alike. The state machine and the ``what_to_show`` test run in the C core; the filter is the
  one callback into Python. This ports traversal code written against the browser DOM or jsdom. (:issue:`550`)
- :func:`turbohtml.parse` and :func:`turbohtml.parse_fragment` gained a ``scripting`` flag (default ``False``). With it
  on, turbohtml sets the WHATWG scripting flag: ``<noscript>`` becomes a raw-text element, so its content is one text
  run rather than parsed markup and serializes back unescaped, reproducing the tree a scripting browser builds. The flag
  is a property of the parsed tree, so the serializer and ``inner_html`` stay consistent with how it was parsed. parse5
  and html5ever default this on for browser fidelity; turbohtml keeps it off so ``<noscript>`` fallback content stays
  navigable. (:issue:`551`)
- Added the DOM Living Standard :class:`~turbohtml.Range` and :class:`~turbohtml.StaticRange` types. A ``Range`` holds
  two boundary points -- each a ``(container, offset)`` pair -- and carries the full boundary API
  (:meth:`~turbohtml.Range.set_start`/:meth:`~turbohtml.Range.set_end` and their ``_before``/``_after`` variants,
  :meth:`~turbohtml.Range.select_node`, :meth:`~turbohtml.Range.select_node_contents`,
  :meth:`~turbohtml.Range.collapse`), the derived :attr:`~turbohtml.Range.collapsed` and
  :attr:`~turbohtml.Range.common_ancestor_container` properties, the comparisons
  (:meth:`~turbohtml.Range.compare_boundary_points`, :meth:`~turbohtml.Range.compare_point`,
  :meth:`~turbohtml.Range.is_point_in_range`, :meth:`~turbohtml.Range.intersects_node`), and the content operations
  (:meth:`~turbohtml.Range.clone_contents`, :meth:`~turbohtml.Range.extract_contents`,
  :meth:`~turbohtml.Range.delete_contents`, :meth:`~turbohtml.Range.insert_node`,
  :meth:`~turbohtml.Range.surround_contents`, :meth:`~turbohtml.Range.clone_range`), each following the WHATWG
  boundary-point ordering and extract/clone/delete algorithms in C under the per-tree critical section. ``StaticRange``
  is the immutable four-value snapshot. Offsets index code points in character data and children elsewhere, so a Python
  string's own indexing lines up with a text-node offset. (:issue:`552`)
- Added the DOM Living Standard Shadow DOM tree model. :meth:`~turbohtml.Element.attach_shadow` attaches an open or
  closed shadow tree and returns a :class:`~turbohtml.ShadowRoot` -- a document-fragment-like root held off the light
  tree, so it never appears among the host's children or in its serialization -- reachable through
  :attr:`~turbohtml.Element.shadow_root` (``None`` for a closed root) and carrying :attr:`~turbohtml.ShadowRoot.mode`,
  :attr:`~turbohtml.ShadowRoot.host`, :meth:`~turbohtml.ShadowRoot.set_inner_html`, and
  :meth:`~turbohtml.ShadowRoot.append`. ``<slot>`` elements assign the host's children by name (the unnamed default slot
  takes the rest): :meth:`~turbohtml.Element.assigned_nodes` and :meth:`~turbohtml.Element.assigned_elements` read what
  a slot received, with a ``flatten`` option that falls back to a slot's own children and expands nested shadow slots,
  and :attr:`~turbohtml.Node.assigned_slot` gives the slot a child landed in. :attr:`~turbohtml.Node.flattened_children`
  returns the composed tree with every slot replaced by its assigned nodes. The assignment and flattening algorithms run
  in C under the per-tree critical section and are computed on demand, so they always reflect the current tree.
  (:issue:`553`)
- Added :class:`~turbohtml.MutationObserver`, a synchronous take on the DOM ``MutationObserver`` for recording tree
  edits. Register a node with :meth:`~turbohtml.MutationObserver.observe` and the DOM options (``child_list``,
  ``attributes``, ``character_data``, ``subtree``, ``attribute_old_value``, ``character_data_old_value``,
  ``attribute_filter``); every change made through the mutation API queues a :class:`~turbohtml.MutationRecord` carrying
  the added and removed nodes, the surrounding siblings, and the attribute name and old value when asked, following the
  WHATWG "queue a mutation record" algorithm in C under the per-tree critical section. Because turbohtml has no event
  loop, delivery is synchronous rather than microtask-scheduled: :meth:`~turbohtml.MutationObserver.take_records`
  returns and clears the queued batch, and :meth:`~turbohtml.MutationObserver.deliver` drains it and calls the
  observer's callback. :meth:`~turbohtml.MutationObserver.disconnect` stops observing and discards pending records.
  (:issue:`554`)
- :class:`~turbohtml.clean.Policy` gained ``xml``: with it on, the sanitizer serializes the cleaned tree as well-formed
  XML/XHTML instead of HTML. Every kept empty element self-closes (``<br/>``), foreign SVG and MathML subtrees declare
  their namespace, text and attribute values follow the XML escaping rules, and a kept comment, a control character
  outside XML's ``Char`` production, or an attribute name XML cannot hold is neutralized, so the output always reparses
  through :func:`turbohtml.parse_xml`. The walk and the safety baseline are unchanged, so an XML-mode policy is exactly
  as safe as its HTML-mode twin. This clones DOMPurify's ``PARSER_MEDIA_TYPE: 'application/xhtml+xml'`` and replaces the
  brittle ``.replace("<br>", "<br/>")`` a bleach-based cleaner needs to feed a strict XHTML consumer such as Reportlab's
  RML. :attr:`turbohtml.Node.inner_xml` exposes the same children-only XML serialization for any node. (:issue:`565`)
- The ``rewrite`` benchmark now runs against a fair in-process peer. lxml and BeautifulSoup do the same edits --
  ``rel=nofollow`` on every link, ``loading=lazy`` on every image, every comment dropped -- through the parse, mutate,
  and serialize round trip that :func:`turbohtml.rewrite.rewrite` skips, and the table reports each party's peak
  resident memory beside throughput, so the tree the streaming rewriter never builds shows up as memory it never holds.
  The :doc:`lol-html migration guide </migration/lol-html>` carries the numbers. (:issue:`612`)

*********************
 v1.0.0 (2026-07-05)
*********************

The 1.0 release finishes the native-C port, settles one canonical public API, and closes the feature gap against the
libraries turbohtml replaces. The notes below fold the whole 0.4.0 to 1.0.0 span into one overview; the anchor issues
point at the epics behind each theme.

Backward incompatible changes - 1.0.0
=====================================

- Give the public surface one name per concept. CSS matching folds from ``turbohtml.match`` into :mod:`turbohtml.query`;
  the sanitizer, linkifier, and every minifier gather under :mod:`turbohtml.clean`; a malformed selector raises one
  :class:`turbohtml.SelectorSyntaxError` from every parse path; the two ``Detector`` classes split into
  :class:`turbohtml.detect.EncodingDetector` and :class:`turbohtml.clean.LinkDetector`; and each surface with more than
  six arguments takes one frozen ``options`` config. (:issue:`478`)
- Select a serialization mode with a single ``layout`` argument in place of ``indent``, so ``serialize(indent=2)``
  becomes ``serialize(layout=Indent(2))`` and :class:`~turbohtml.Minify` selects minified output. (:issue:`171`)
- Report a valueless attribute (``<x a>``) as the empty string rather than ``None`` in :attr:`turbohtml.Element.attrs`,
  matching the WHATWG tokenizer and the DOM. (:issue:`87`)

Features - 1.0.0
================

- Query a tree with the full Selectors Level 4 grammar (``:is()``, ``:where()``, ``:has()``, ``:not()``,
  ``:nth-child(An+B of S)``, the structural, input, ``:lang()``, ``:dir()``, and ``:scope`` pseudo-classes) and an XPath
  1.0 engine: :meth:`~turbohtml.Node.xpath`, a compiled reusable :class:`turbohtml.XPath`, the EXSLT function set, and
  namespace, variable, and extension binding. A pyquery-style :class:`turbohtml.query.Query`, a soupsieve-shaped
  matcher, and :func:`turbohtml.convert.css_to_xpath` cover the BeautifulSoup, cssselect, parsel, and pyquery surfaces.
  (:issue:`179`)
- Serialize back to HTML with pretty-print, whitespace minification, and lazy streaming
  (:meth:`~turbohtml.Node.serialize_iter`), and minify HTML, CSS, and JavaScript through native engines under
  :mod:`turbohtml.clean`. Every transform is round-trip safe, replacing rcssmin, csscompressor, rjsmin, jsmin,
  minify-html, and htmlmin. (:issue:`343`, :issue:`346`)
- Convert a tree to GitHub-Flavored Markdown, layout-aware text, or annotated ``(start, end, label)`` spans, and extract
  a page's main article, boilerplate paragraphs, publication date, tables, links, and structured data (JSON-LD,
  Microdata, RDFa, Dublin Core, Open Graph) through :mod:`turbohtml.extract`, replacing markdownify, html2text,
  inscriptis, trafilatura, readability, htmldate, extruct, and microdata. (:issue:`273`, :issue:`276`)
- Detect a byte stream's encoding and a text's natural language from the standalone :mod:`turbohtml.detect` module,
  which reports confidence and a BOM label, covers 69 languages across nine scripts, and replaces chardet,
  charset-normalizer, and cchardet. (:issue:`315`, :issue:`474`)
- Parse incrementally from a stream with :class:`turbohtml.IncrementalParser`, read the WHATWG parse errors recovery
  swallows through :attr:`~turbohtml.Document.errors`, and locate each element in the source through
  :attr:`~turbohtml.Node.source_line` and :attr:`~turbohtml.Node.source_col`. (:issue:`210`, :issue:`212`)
- Sanitize untrusted HTML against a frozen allowlist :class:`turbohtml.clean.Policy` that is safe with no arguments and
  scrubs inline and embedded CSS, foreign namespaces, and media hosts, and auto-link URLs and email addresses, together
  replacing bleach. (:issue:`8`, :issue:`9`)
- Build and edit the tree in place: construct nodes and a whole HTML5 page with :data:`turbohtml.build.E` and
  :func:`turbohtml.build.document`, edit the class token set and inner HTML or text, wrap and unwrap subtrees, trim a
  document to a selector with :meth:`~turbohtml.Node.prune`, read and fill form fields, and compare two subtrees with
  :meth:`~turbohtml.Node.equals`. (:issue:`275`, :issue:`225`, :issue:`468`)
- Clean, canonicalize, and extract page URLs through :mod:`turbohtml.extract`, backed by a native URL pipeline (WHATWG
  splitting, percent-coding, relative-reference joining, UTS #46 IDNA, and Public Suffix List registrable-domain
  filtering), and run the toolkit from a ``turbohtml`` command line covering minify, detect, to-markdown, to-text, and
  sanitize. (:issue:`321`, :issue:`470`)

Bug fixes - 1.0.0
=================

- Bring tree construction to WHATWG conformance across foster parenting, foreign content, the select, table, and
  template insertion modes, doctype and quirks detection, duplicate and void-element handling, validated against the
  html5lib-tests corpus. (:issue:`32`)
- Resolve every label in the WHATWG Encoding Standard, decode the replacement and gb18030 families and the windows-1252
  C1 bytes, and honor the 1024-byte ``<meta>`` prescan. (:issue:`54`, :issue:`423`)
- Match the Selectors Level 4 corrections: forgiving ``:is()``/``:where()`` lists, quirks-mode case folding, CSS escape
  decoding, namespace prefixes, whitespace-only ``:empty``, and ``:scope`` resolution inside ``:has()``. (:issue:`174`)
- Fix XPath 1.0 semantics: shortest round-tripping number formatting, half-to-positive-infinity rounding, fixed function
  arity, node-set typing, and arena-safe predicate compilation. (:issue:`398`)
- Correct the Markdown and text exporters on nested and loose lists, link and image escaping, the ``<pre>`` leading
  newline, ordinal ``<li value>``, and raw-text suppression in every namespace. (:issue:`384`)
- Fix extraction on landmark-wrapped and list-structured articles, Microdata ``itemref`` merging, table ``rowspan``
  bounds, and the WHATWG form ``select``/``optgroup`` submission rules. (:issue:`385`, :issue:`408`)
- Keep the CSS and JavaScript minifiers value-safe: ``calc()`` type mixing, duplicate-declaration fallbacks,
  ``currentcolor``, conditional folding, and adjacent-string-literal concatenation. (:issue:`415`)
- Raise a precise, typed error from every public failure path in place of a silent wrong result or a leaked low-level
  type, and document each callable's exceptions. (:issue:`434`)

Improved documentation - 1.0.0
==============================

- Ship a migration guide for each library turbohtml replaces, every one carrying a monthly-downloads badge, a measured
  benchmark, and a speed-up multiplier, with the index ordered by adoption. (:issue:`313`)
- Restructure the reference, how-to, and migration trees around the eight-namespace taxonomy (parse/DOM, detect, query,
  clean, convert, extract, build, serialize), and add ``llms.txt`` maps, a sitemap, and per-page meta descriptions.
  (:issue:`478`)
- Add a written security policy, a "How turbohtml was built" page, and the seven design principles that shape the
  library. (:issue:`500`)

Packaging updates - 1.0.0
=========================

- Build the release wheels with profile-guided, link-time optimization trained offline over a real-world corpus of
  clean, malformed, legacy-encoded, and structured-data markup. (:issue:`481`)
- Pin every network-sourced C data table (IANA TLDs, the Public Suffix List, and the Unicode IDNA and NFC tables) to a
  named source commit and a SHA-256 checksum, so a rebuild is reproducible and aborts on a poisoned upstream.
  (:issue:`478`)
- Strip the compiled extension's local symbol table from the release wheels at link time, trimming about 65 KB from the
  Linux ``.so`` and 46 KB from the macOS bundle; the source distribution still carries every test, tool, and generated
  table needed to build from source. (:issue:`478`)

Miscellaneous internal changes - 1.0.0
======================================

- Finish the native-C port: URL splitting, percent-coding, relative joining, IDNA, registrable-domain lookup, and date
  parsing move from ``urllib``, ``re``, and ``datetime`` into the C extension, leaving Python a thin configure-and-wrap
  shim. (:issue:`478`)
- Speed the read path: a per-tree atom index runs a tag-pinned :meth:`~turbohtml.Node.find` several times faster,
  ``:has()`` evaluates in one amortized-linear pass, streaming serialization sizes its buffer up front, and link-time
  optimization re-inlines across a subsystem-first C layout. (:issue:`162`, :issue:`509`)
- Harden against untrusted input: ASan and UBSan fuzz gates on every entry point, a DOMPurify XSS oracle and an
  html5lib-tests tree-equality oracle over the sanitizer, mutation-XSS namespace checks, caps on element nesting and
  duplicate attributes, and one overflow-safe buffer-growth helper across the C core. (:issue:`503`, :issue:`511`)
- Validate free-threaded safety under pytest-run-parallel and ThreadSanitizer, and gate performance on every pull
  request with a per-operation CodSpeed benchmark over the real corpora. (:issue:`380`)

*********************
 v0.4.0 (2026-06-16)
*********************

Features - 0.4.0
================

- Build and edit the tree, not just read it: construct :class:`~turbohtml.Element`, :class:`~turbohtml.Text`, and
  :class:`~turbohtml.Comment` nodes and rearrange them with the full set of insert, wrap, extract, and normalize
  methods, with :attr:`~turbohtml.Element.attrs` and ``.text``/``.data`` as live setters. ``copy``, ``deepcopy``, and
  ``pickle`` duplicate a subtree - by :user:`gaborbernat`. (:issue:`19`)
- Round out the node model: :class:`~turbohtml.ProcessingInstruction` and :class:`~turbohtml.CData` join the hierarchy,
  :class:`~turbohtml.Doctype` exposes its :attr:`~turbohtml.Doctype.public_id` and :attr:`~turbohtml.Doctype.system_id`,
  and every node type supports structural pattern matching - by :user:`gaborbernat`. (:issue:`22`)

Improved documentation - 0.4.0
==============================

- Learn the write path through new tutorial, how-to, and explanation docs, backed by benchmarks showing turbohtml builds
  and rewrites trees about twice as fast as `lxml <https://lxml.de/>`__ and an order of magnitude faster than
  `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__ - by :user:`gaborbernat`. (:issue:`19`)
- Port to turbohtml with migration guides from `BeautifulSoup <https://www.crummy.com/software/BeautifulSoup/>`__, `lxml
  <https://lxml.de/>`__, `selectolax <https://github.com/rushter/selectolax>`__, `html5lib
  <https://github.com/html5lib/html5lib-python>`__, and the standard library, each mapping the source library's idioms
  to their turbohtml equivalents and flagging behavior differences - by :user:`gaborbernat`. (:issue:`23`)

*********************
 v0.3.0 (2026-06-16)
*********************

Features - 0.3.0
================

- Query any node with CSS through :meth:`~turbohtml.Node.select` and :meth:`~turbohtml.Node.select_one`, a native
  matcher covering type, universal, ``#id``, ``.class``, and attribute selectors (all operators plus the
  case-sensitivity flag) across the descendant, child, adjacent, and sibling combinators, returning comma groups in
  document order. An invalid selector raises ``ValueError`` - by :user:`gaborbernat`. (:issue:`14`)
- Search with a richer :meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` filter grammar: match the tag
  and attributes by string, regex, bool, callable, or list (including ``class_`` and the ``attrs`` mapping), and choose
  the search direction with the ``axis`` keyword. ``find_all`` takes a ``limit`` and returns a ``list`` - by
  :user:`gaborbernat`. (:issue:`15`)
- Test a node against a selector with :meth:`~turbohtml.Node.matches` and :meth:`~turbohtml.Node.closest`: ``matches()``
  reports whether the node satisfies a CSS selector in context, and ``closest()`` returns the nearest matching ancestor
  (or the node itself), or ``None`` - by :user:`gaborbernat`. (:issue:`16`)
- Walk the tree by axis with new iterators: :attr:`~turbohtml.Node.next_siblings`,
  :attr:`~turbohtml.Node.previous_siblings`, document-order :attr:`~turbohtml.Node.following` and
  :attr:`~turbohtml.Node.preceding`, plus the :attr:`~turbohtml.Node.strings` and
  :attr:`~turbohtml.Node.stripped_strings` text iterators - by :user:`gaborbernat`. (:issue:`17`)
- Read HTML token-list attributes (``class``, ``rel``, ``headers``, ``sizes``, ``sandbox``, and the rest) as a
  ``list[str]`` in :attr:`turbohtml.Element.attrs`, split on ASCII whitespace; other attributes stay strings and
  valueless ones stay ``None`` - by :user:`gaborbernat`. (:issue:`18`)
- Control serialization on any node: :attr:`~turbohtml.Node.inner_html` returns the children, while
  :meth:`~turbohtml.Node.serialize` and :meth:`~turbohtml.Node.encode` take a ``formatter`` (the
  :class:`~turbohtml.Formatter` enum picks the escape policy) and an ``indent`` for pretty output. The default stays
  WHATWG-conformant HTML - by :user:`gaborbernat`. (:issue:`20`)
- Parse ``bytes`` directly: :func:`turbohtml.parse` sniffs the encoding with the WHATWG algorithm (BOM, ``encoding``
  argument, ``<meta>`` charset, then windows-1252), decodes with U+FFFD replacement, and reports the result in
  :attr:`~turbohtml.Document.encoding` - by :user:`gaborbernat`. (:issue:`21`)

*********************
 v0.2.0 (2026-06-11)
*********************

Features - 0.2.0
================

- Tokenize HTML directly with a WHATWG-conformant tokenizer: :func:`turbohtml.tokenize` for whole strings, the streaming
  :class:`turbohtml.Tokenizer`, and the :class:`turbohtml.Token` / :class:`turbohtml.TokenType` types, validated against
  the html5lib-tests tokenizer conformance suite. (:issue:`6`)
- Run :func:`turbohtml.escape` and :func:`turbohtml.unescape` faster: vectorized scanning and bulk copying speed up both
  calls, with unescaping of real escaped HTML about three times faster than the general lookup path. The benchmark now
  uses `pyperf <https://pyperf.readthedocs.io>`_ over multi-MiB real documents - by :user:`gaborbernat`. (:issue:`7`)

*********************
 v0.1.1 (2026-06-09)
*********************

Packaging updates - 0.1.1
=========================

- Install reliably from PyPI again: publishing each wheel in its own job keeps PEP 740 attestations within the Sigstore
  identity's lifetime, fixing the ``sigstore.oidc.ExpiredIdentity`` failure that blocked the first upload - by
  :user:`gaborbernat`. (:issue:`4`)

*********************
 v0.1.0 (2026-06-09)
*********************

Features - 0.1.0
================

- Speed up entity handling with C-accelerated :func:`turbohtml.escape` and :func:`turbohtml.unescape`, drop-in
  replacements for :func:`python:html.escape` and :func:`python:html.unescape`, shipped as wheels for CPython 3.10
  through 3.15 - by :user:`gaborbernat`. (:issue:`1`)
- Escape non-ASCII text that needs no escaping several times faster with a vectorized special-character scan, ahead of
  :func:`python:html.escape` - by :user:`gaborbernat`. (:issue:`3`)

Improved documentation - 0.1.0
==============================

- See the measured :func:`turbohtml.escape`/:func:`turbohtml.unescape` speedups in the README and docs, reproduce them
  with ``tox -e bench``, and browse a typed API reference with intersphinx links - by :user:`gaborbernat`. (:issue:`2`)

Miscellaneous internal changes - 0.1.0
======================================

- Automate releases with git-tag-derived versioning, a towncrier-managed changelog, and a prepare-release workflow that
  tags and triggers the trusted-publishing wheel build - by :user:`gaborbernat`. (:issue:`1`)
