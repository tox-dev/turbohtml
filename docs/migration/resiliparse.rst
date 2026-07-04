##################
 From resiliparse
##################

.. package-meta:: resiliparse chatnoir-eu/chatnoir-resiliparse

`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ is the web-crawl processing toolkit from the Webis
group behind ChatNoir. It is built for large-scale corpus work: ``HTMLTree`` wraps the same `lexbor
<https://lexbor.com>`_ engine selectolax does and builds a WHATWG tree with real text nodes, and alongside the parser it
ships boilerplate-aware plain-text extraction, encoding detection, fast language detection, and process/memory guards
for hardening crawl workers. WARC reading lives in its companion package FastWARC. It shows up wherever people process
Common Crawl-scale archives in Python and need a fast, resilient HTML-to-text stage.

turbohtml covers the DOM and text-extraction ground with one library: it parses the same WHATWG tree in its own C
engine, then keeps you inside a fully typed, mutable :class:`~turbohtml.Document` where resiliparse's DOM traversal,
``get_element_by_*`` lookups, and CSS ``query_selector`` methods collapse into one ``find``/``find_all``/``select``
grammar. It also matches resiliparse's fast language detection with :func:`turbohtml.detect.detect_language`. It does
not try to be a crawl toolkit; process guards and WARC handling stay resiliparse's job.

**************************
 turbohtml vs resiliparse
**************************

.. list-table::
    :header-rows: 1
    :widths: 16 42 42

    - - Dimension
      - turbohtml
      - resiliparse
    - - Scope
      - Parse, query, mutate, serialize, and extract text in one library
      - Web-crawl processing toolkit: HTML parse and text extraction plus encoding/language detection and crawl guards
    - - Feature breadth
      - CSS :meth:`~turbohtml.Node.select`, XPath 1.0 :meth:`~turbohtml.Node.xpath`, the
        :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` grammar, a full edit surface, Markdown/plain-text
        renderers, sanitizer, linkifier, structured-data extraction
      - CSS ``query_selector`` plus ``get_element_by_*`` DOM lookups, boilerplate-aware ``extract_plain_text``, encoding
        and language detection, process/memory guards
    - - Performance
      - Own C engine straight into the native tree; text extraction walks the WHATWG tree once in C
      - lexbor parse (a dead heat with turbohtml); ``extract_plain_text`` renders off the lexbor tree in a second pass
    - - Typing
      - Fully type annotated with bundled stubs
      - Cython extension; limited Python-level type surface
    - - Dependencies
      - Self-contained C extension
      - Cython extension over lexbor; WARC handling needs the companion FastWARC package
    - - Maintenance
      - Actively developed
      - Actively developed by the Webis research group

Feature overlap
===============

Both are native WHATWG parsers with real text nodes, so the parse call, DOM walk, and text extraction port directly:

- ``HTMLTree.parse(html)`` maps to :func:`turbohtml.parse`.
- ``tree.body``, ``tree.head``, ``tree.title`` map to :meth:`doc.find("body") <turbohtml.Node.find>`,
  ``doc.find("head")``, and ``doc.find("title").text``.
- ``node.query_selector(sel)`` / ``node.query_selector_all(sel)`` map to :meth:`~turbohtml.Node.select_one` /
  :meth:`~turbohtml.Node.select`.
- ``node.get_element_by_id("main")`` maps to :meth:`node.find(id="main") <turbohtml.Node.find>`; the
  ``get_elements_by_tag_name``/``get_elements_by_class_name`` pair maps to :meth:`~turbohtml.Node.find_all`
  (``find_all("a")``, ``find_all(class_="x")``).
- The ``getattr``/``hasattr``/``setattr``/``delattr`` attribute methods map onto the :attr:`~turbohtml.Element.attrs`
  mapping (``attrs.get``, ``"href" in attrs``, ``attrs[...] = ...``, ``del attrs[...]``).
- ``node.tag``, ``node.text``, ``node.html`` map to :attr:`~turbohtml.Element.tag`, :attr:`~turbohtml.Node.text`,
  :attr:`~turbohtml.Node.html`; ``node.class_list`` maps to ``attrs["class"]``.
- ``node.create_element``/``append_child``/``decompose`` map to :class:`~turbohtml.Element`,
  :meth:`~turbohtml.Element.append`, :meth:`~turbohtml.Node.decompose`.
- ``extract_plain_text(html)`` maps to :meth:`~turbohtml.Node.to_text` for laid-out text (or
  :attr:`~turbohtml.Node.text` for the raw concatenation); ``extract_plain_text(html, main_content=True)`` maps to
  :meth:`~turbohtml.Node.main_text`, and the boilerplate-stripped main node to :meth:`~turbohtml.Node.main_content`.

What turbohtml adds
===================

- XPath 1.0 querying via :meth:`~turbohtml.Node.xpath`, which resiliparse has no equivalent for.
- The :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.find_all` filter grammar layered over CSS, so tag, id, class,
  and attribute predicates compose in one call instead of chaining ``get_element_by_*`` methods.
- Serialization and rendering in the same library: the :attr:`~turbohtml.Node.html` property,
  :meth:`~turbohtml.Node.encode`, and the
  :class:`~turbohtml.Markdown`/:class:`~turbohtml.PlainText`/:class:`~turbohtml.Html` renderers, including
  :meth:`~turbohtml.Node.to_markdown`.
- HTML sanitization against an allowlist (:func:`turbohtml.clean.sanitize` with a :class:`~turbohtml.clean.Policy`).
- Link handling: :attr:`~turbohtml.Node.links`, :meth:`~turbohtml.Node.rewrite_links`,
  :meth:`~turbohtml.Node.resolve_links`, and the :class:`~turbohtml.clean.Linkify` pass.
- Structured-data extraction: :attr:`~turbohtml.Document.json_ld`, :attr:`~turbohtml.Document.opengraph`,
  :attr:`~turbohtml.Document.microdata`.
- Content-based language detection: :func:`turbohtml.detect.detect_language` classifies a string's natural language (ISO
  639-3 code, confidence, and Unicode script) the way ``resiliparse.parse.lang.detect_fast`` does, over the same trigram
  model whatlang uses. It replaces ``detect_fast`` for the text you extract with :meth:`~turbohtml.Node.text` or
  :meth:`~turbohtml.Node.main_text`.
- Full static typing across the tree with bundled stubs.

What resiliparse has that turbohtml does not
============================================

- **Process and memory guards.** ``resiliparse.process_guard`` provides time and memory guards that kill a worker whose
  parse or extraction runs away, which matters when processing adversarial crawl data at scale. turbohtml ships no such
  guard; wrap calls in your own resource limits.
- **WARC/archive processing.** resiliparse's ecosystem reads WARC records through the companion FastWARC package.
  turbohtml is a tree library and has no archive support; keep FastWARC for the ingestion stage.
- **Standalone MIME/encoding utilities.** ``resiliparse.parse.encoding`` exposes byte-to-str conversion and encoding
  detection as free functions over raw bytes. turbohtml detects the encoding during :func:`~turbohtml.parse`
  (``detect_encoding=True``) rather than as a standalone codec-inspection API.

Performance
===========

Parsing is a dead heat: resiliparse runs lexbor and turbohtml runs its own C engine straight into the native tree. On
text extraction (``extract_plain_text`` against :meth:`~turbohtml.Node.to_text`, and its ``main_content=True`` mode
against :meth:`~turbohtml.Node.main_text`) turbohtml walks the WHATWG tree once in C where resiliparse renders off the
lexbor tree in a second pass:

.. bench-table::
    :file: bench/resiliparse.json

****************
 How to migrate
****************

Swap ``HTMLTree.parse`` for :func:`turbohtml.parse` and the ``resiliparse.extract`` import for the text methods on the
returned node:

.. code-block:: python

    # resiliparse
    from resiliparse.parse.html import HTMLTree
    from resiliparse.extract.html2text import extract_plain_text

    tree = HTMLTree.parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    href = tree.body.query_selector("#main a").getattr("href")
    text = extract_plain_text(tree.document.html)

    # turbohtml
    from turbohtml import parse

    doc = parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    href = doc.select_one("#main a").attrs["href"]
    text = doc.to_text()

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `resiliparse <https://resiliparse.chatnoir.eu/>`__
      - turbohtml
    - - ``HTMLTree.parse(html)``
      - :func:`turbohtml.parse`
    - - ``tree.body``, ``tree.head``, ``tree.title``
      - :meth:`doc.find("body") <turbohtml.Node.find>`, ``doc.find("head")``, ``doc.find("title").text``
    - - ``node.query_selector("a")``, ``node.query_selector_all("a")``
      - :meth:`~turbohtml.Node.select_one`, :meth:`~turbohtml.Node.select`
    - - ``node.get_element_by_id("main")``
      - :meth:`node.find(id="main") <turbohtml.Node.find>`
    - - ``node.get_elements_by_tag_name("a")``, ``node.get_elements_by_class_name("x")``
      - :meth:`~turbohtml.Node.find_all` (``find_all("a")``, ``find_all(class_="x")``)
    - - ``node.getattr("href")``, ``node.hasattr("href")``, ``node.setattr(...)``, ``node.delattr(...)``
      - :attr:`~turbohtml.Element.attrs` (``attrs.get``, ``"href" in attrs``, ``attrs[...] = ...``, ``del attrs[...]``)
    - - ``node.tag``, ``node.text``, ``node.html``, ``node.class_list``
      - :attr:`~turbohtml.Element.tag`, :attr:`~turbohtml.Node.text`, :attr:`~turbohtml.Node.html`, ``attrs["class"]``
    - - ``node.parent``, ``node.next_element``, ``node.prev_element``, ``node.first_element_child``
      - :attr:`~turbohtml.Node.parent`, :attr:`~turbohtml.Node.next_sibling`, :attr:`~turbohtml.Node.previous_sibling`,
        ``node.children[0]``
    - - ``tree.create_element("div")``, ``node.append_child(...)``, ``node.decompose()``
      - :class:`~turbohtml.Element`, :meth:`~turbohtml.Element.append`, :meth:`~turbohtml.Node.decompose`
    - - ``extract_plain_text(html)`` (from ``resiliparse.extract``)
      - :meth:`~turbohtml.Node.to_text` for layout, :attr:`~turbohtml.Node.text` for the raw concatenation
    - - ``extract_plain_text(html, main_content=True)``
      - :meth:`~turbohtml.Node.main_text`
    - - ``ExtractNode`` / boilerplate-stripped main content
      - :meth:`~turbohtml.Node.main_content`
    - - ``detect_fast(text)`` (from ``resiliparse.parse.lang``)
      - :func:`turbohtml.detect.detect_language`

.. testcode::

    doc = parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    print(doc.select_one("#main a").attrs["href"])
    print(doc.find(id="main").find("p").text)

.. testoutput::

    /u
    Hi link

**********************
 Gotchas and pitfalls
**********************

- **Sibling traversal spans text nodes.** resiliparse's ``next_element``/``prev_element``/``first_element_child`` skip
  to the next element; :attr:`~turbohtml.Node.next_sibling`/:attr:`~turbohtml.Node.previous_sibling` and
  ``node.children`` are node-level and include :class:`~turbohtml.Text` nodes. Filter for elements, or use
  :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.select` when you want elements only.
- **Main-content extraction is a heuristic.** :meth:`~turbohtml.Node.main_content` and :meth:`~turbohtml.Node.main_text`
  run a content-density (readability) pass over the parsed tree. It approximates resiliparse's boilerplate removal but
  will not match it token for token.
- **Node identity, not markup identity.** turbohtml compares nodes by identity over the underlying arena node, so two
  wrappers for the same element are equal, but two separately parsed trees with identical markup are not. Compare
  serializations or walk the tree instead.
- **Language detection reads text, not a tree.** :func:`turbohtml.detect.detect_language` takes the extracted string
  (from :meth:`~turbohtml.Node.text` or :meth:`~turbohtml.Node.main_text`), where ``detect_fast`` takes the document; it
  reports an ISO 639-3 code with a confidence rather than resiliparse's own label set, so map the codes your pipeline
  expects.
- **The crawl toolkit stays behind.** Dropping resiliparse also drops its process guards and the FastWARC ingestion
  path. If a pipeline depends on those, keep resiliparse for that stage and hand turbohtml the HTML string.
