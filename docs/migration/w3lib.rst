############
 From w3lib
############

.. package-meta:: w3lib scrapy/w3lib

`w3lib <https://w3lib.readthedocs.io>`_ is the Scrapy project's grab-bag of stateless web utilities: character-reference
resolution, regex tag/comment stripping, URL canonicalization and query cleaning, and response-encoding detection. It
carries no parser and no tree; every helper is a pure-Python function over a string, ``bytes``, or ``urllib`` split
result, which is why it ships as Scrapy's low-level layer and shows up in scrapers that need one canonical URL form or a
quick entity decode without pulling in a full DOM.

turbohtml covers the same ground from the other direction. The ``w3lib.html`` text and entity helpers map onto the
WHATWG tree turbohtml already builds, so a regex tag strip becomes a real parse plus a text read; the ``w3lib.url``
canonicalization surface maps onto the :mod:`turbohtml.extract` URL helpers, whose canonical form is the `WHATWG URL
standard <https://url.spec.whatwg.org/>`_ serialization instead of w3lib's urllib re-encoding. Only w3lib's HTTP-header
and file/data-URI plumbing stays outside turbohtml's scope.

********************
 turbohtml vs w3lib
********************

.. list-table::
    :header-rows: 1
    :widths: 18 41 41

    - - Dimension
      - turbohtml
      - w3lib
    - - Scope
      - WHATWG HTML parser, DOM tree, selectors, serializer, URL/extract and encoding detection
      - Stateless string, URL, and encoding utilities for scraping, no parser or tree
    - - Feature breadth
      - Broader on HTML: real tree, ``strip_tags``, ``remove``, document URL hints, sanitizing ``clean``
      - Broader on transport: HTTP headers, file/data URIs, in-place query-parameter mutation
    - - Performance
      - C extension; 1.5x-4.6x on the shared URL batch, several times faster on entity-heavy text
      - Pure-Python regex and ``urllib`` re-encoding
    - - Typing
      - Fully annotated with bundled ``.pyi`` stubs for the C extension
      - Typed, ``py.typed``
    - - Dependencies
      - Compiled C extension, no Python runtime dependencies
      - Pure Python, standard library only
    - - Maintenance
      - Actively developed
      - Mature and stable under the Scrapy org

Feature overlap
===============

These map 1:1 and port directly:

- :func:`w3lib.html.replace_entities` -> :func:`turbohtml.unescape` (same character-reference resolution).
- :func:`w3lib.html.remove_tags` -> :func:`turbohtml.parse` then :attr:`~turbohtml.Node.text`, or
  :meth:`~turbohtml.Node.strip_tags` to unwrap only a chosen set while keeping the rest of the document.
- :func:`w3lib.html.remove_comments` -> :attr:`~turbohtml.Node.text` (comments never appear in text).
- :func:`w3lib.html.remove_tags_with_content` -> :meth:`~turbohtml.Node.remove` (drops the tag and its subtree).
- :func:`w3lib.html.get_base_url` -> :meth:`~turbohtml.Document.base_url`.
- :func:`w3lib.html.get_meta_refresh` -> :meth:`~turbohtml.Document.meta_refresh`.
- :func:`w3lib.url.canonicalize_url` -> :func:`turbohtml.extract.normalize_url` with ``UrlCleaning.w3lib()``.
- :func:`w3lib.url.url_query_cleaner` -> ``UrlCleaning(query_allow=...)`` / ``UrlCleaning(query_deny=...)``.
- :func:`w3lib.url.safe_url_string` -> :func:`turbohtml.extract.clean_url` (also validates) or
  :func:`~turbohtml.extract.normalize_url`.
- ``w3lib.url.is_url`` -> ``turbohtml.extract.clean_url(text) is not None``.

What turbohtml adds
===================

- A real WHATWG tree behind every text operation: nested and malformed markup is parsed the way a browser does, not
  matched as ``<...>`` spans, and ``text`` returns decoded characters rather than leaving entities encoded.
- Selector-based tree editing (:meth:`~turbohtml.Node.remove`, :meth:`~turbohtml.Node.strip_tags`) that reshapes the
  document in place, where w3lib only returns strings.
- Sanitizing output via ``turbohtml.clean`` when the goal is safe HTML rather than reshaping a tree, which w3lib has no
  concept of.
- WHATWG-standard URL serialization from :mod:`turbohtml.extract`, including default-port stripping, ``..`` segment
  resolution, and automatic removal of known tracking parameters (``utm_*``, ``gclid``, ...) that ``canonicalize_url``
  never drops.
- Encoding detection from bytes through :func:`turbohtml.detect.detect`.

What w3lib has that turbohtml does not
======================================

- ``w3lib.url.add_or_replace_parameter`` / ``add_or_replace_parameters`` — building a URL by setting a query value. No
  equivalent; turbohtml's ``UrlCleaning`` filters parameters but does not add or rewrite them.
- ``w3lib.url.url_query_parameter`` — reading a single query value out of a URL. No equivalent; use ``urllib.parse`` for
  extraction.
- File and data URI helpers (``file_uri_to_path``, ``path_to_file_uri``, ``any_to_uri``, ``parse_data_uri``). No
  equivalent, these are outside turbohtml's scope.
- HTTP-header helpers (``basic_auth_header``, ``headers_dict_to_raw``). No equivalent.
- ``w3lib.encoding``'s full decode chain (``html_to_unicode``, ``http_content_type_encoding``,
  ``html_body_declared_encoding``, ``read_bom``) that returns decoded text. :func:`turbohtml.detect.detect` answers the
  encoding-label question from bytes but does not chain an HTTP ``Content-Type`` header into the decision or hand back
  the decoded string.

Performance
===========

The URL surface is 1.5x-4.6x faster over a shared 100-URL batch, and the entity and tag helpers each run a
structure-aware C pass that still beats w3lib's regex on entity-heavy input:

.. bench-table::
    :file: bench/w3lib.json

****************
 How to migrate
****************

Swap the imports for the turbohtml entry points:

.. code-block:: python

    from turbohtml import parse, unescape
    from turbohtml.extract import UrlCleaning, clean_url, normalize_url

Then map each call:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - `w3lib <https://w3lib.readthedocs.io/>`__
      - turbohtml
    - - :func:`w3lib.html.replace_entities`
      - :func:`turbohtml.unescape`
    - - :func:`w3lib.html.remove_tags`
      - :func:`turbohtml.parse` then :attr:`~turbohtml.Node.text`
    - - :func:`w3lib.html.remove_comments`
      - :attr:`~turbohtml.Node.text` (comments never appear in text)
    - - :func:`w3lib.html.remove_tags_with_content`
      - :meth:`~turbohtml.Node.remove`
    - - :func:`w3lib.html.get_base_url`
      - :meth:`~turbohtml.Document.base_url`
    - - :func:`w3lib.html.get_meta_refresh`
      - :meth:`~turbohtml.Document.meta_refresh`
    - - :func:`w3lib.url.canonicalize_url`
      - :func:`turbohtml.extract.normalize_url` with ``UrlCleaning.w3lib()``
    - - :func:`w3lib.url.safe_url_string`
      - :func:`turbohtml.extract.clean_url` (also validates) or :func:`~turbohtml.extract.normalize_url`
    - - :func:`w3lib.url.url_query_cleaner`
      - ``UrlCleaning(query_allow=...)`` / ``UrlCleaning(query_deny=...)``
    - - ``w3lib.url.is_url``
      - ``turbohtml.extract.clean_url(text) is not None``

``replace_entities`` resolves character references the same way :func:`turbohtml.unescape` does, so it is a drop-in;
``w3lib.html.replace_entities("caf&eacute; &amp; co")`` returns the same string this prints:

.. testcode::

    from turbohtml import unescape

    print(unescape("caf&eacute; &amp; co"))

.. testoutput::

    café & co

The tag and comment strippers map onto parsing to a real tree and reading its text. ``remove_tags`` becomes
:func:`turbohtml.parse` followed by :attr:`~turbohtml.Node.text`, and ``remove_comments`` needs nothing extra because
comments never appear in ``text``:

.. testcode::

    from turbohtml import parse

    print(parse("<p>Tom &amp; Jerry <b>says</b> hi</p><!--note-->").text)

.. testoutput::

    Tom & Jerry says hi

``remove_tags_with_content``, which drops a tag together with its subtree, is :meth:`~turbohtml.Node.remove`:
``remove_tags_with_content(html, which_ones=("script",))`` becomes ``parse(html).remove("script")``, editing the tree in
place rather than returning a string. When the goal is to drop only some tags while keeping the rest of the document as
HTML (``remove_tags`` with ``which_ones``), unwrap them with :meth:`~turbohtml.Node.strip_tags`, which keeps each
match's content. Reach for ``turbohtml.clean`` instead when the goal is producing safe HTML rather than reshaping a
tree.

The two helpers that read a document's own URL hints map to the :meth:`~turbohtml.Document.base_url` and
:meth:`~turbohtml.Document.meta_refresh` methods on the parsed document. Each takes the fallback base URL w3lib calls
``baseurl`` and resolves the hint against it:

.. testcode::

    from turbohtml import parse

    doc = parse('<base href="/sub/"><meta http-equiv=refresh content="5; url=next.html">')
    print(doc.base_url("http://site.com/"))
    print(doc.meta_refresh("http://site.com/"))

.. testoutput::

    http://site.com/sub/
    (5.0, 'http://site.com/next.html')

``canonicalize_url`` becomes :func:`turbohtml.extract.normalize_url` with the ``UrlCleaning.w3lib()`` preset, which
mirrors w3lib's fragment dropping; ``url_query_cleaner``'s keep/remove parameter lists become the ``query_allow`` and
``query_deny`` fields on the same config:

.. testcode::

    from turbohtml.extract import UrlCleaning, normalize_url

    print(normalize_url("http://www.example.com/do?c=3&b=5&b=2&a=50#frag", UrlCleaning.w3lib()))
    print(
        normalize_url("http://x.example/product.html?id=200&foo=bar&name=wired", UrlCleaning(query_allow=frozenset({"id"})))
    )

.. testoutput::

    http://www.example.com/do?a=50&b=2&b=5&c=3
    http://x.example/product.html?id=200

**********************
 Gotchas and pitfalls
**********************

- ``remove_tags`` strips angle brackets with a regular expression and leaves entities encoded (``Tom &amp; Jerry``),
  while :attr:`~turbohtml.Node.text` runs the WHATWG tree builder and returns decoded characters (``Tom & Jerry``).
  turbohtml parses malformed and nested markup the way a browser does rather than matching ``<...>`` spans, so the two
  diverge on inputs a regex misreads.
- ``remove_tags_with_content`` edits the tree rather than returning a string: :meth:`~turbohtml.Node.remove` drops the
  matches in place, and :meth:`~turbohtml.Node.text` then reads what is left, so a one-line w3lib call becomes a
  parse-edit-read sequence.
- Over the 253 URLs in w3lib's own test suite the two canonicalizers return identical output for 88% of inputs; every
  divergence is the URL standard's form winning over a urllib legacy form. turbohtml keeps a valueless parameter as
  ``?q`` where w3lib appends ``?q=``, percent-encodes a query space as ``%20`` where w3lib emits ``+``, leaves ``,``
  ``(`` ``)`` raw in queries (outside the WHATWG query percent-encode set) where w3lib escapes them, splits parameters
  on ``&`` only (w3lib also splits the legacy ``;``), strips a default ``:80``/``:443`` port and resolves ``..``
  segments where w3lib keeps both.
- :func:`~turbohtml.extract.normalize_url` always removes known tracking parameters (``utm_*``, ``gclid``, ...);
  ``canonicalize_url`` keeps them. List one in ``UrlCleaning(query_allow=...)`` when it must survive.
- ``add_or_replace_parameter``, the response-encoding decode chain (:func:`turbohtml.detect.detect` answers the encoding
  question from bytes but does not return decoded text), and the HTTP-header and file/data-URI helpers have no
  equivalent here.
