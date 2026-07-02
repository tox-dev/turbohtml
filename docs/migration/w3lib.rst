############
 From w3lib
############

.. package-meta:: w3lib scrapy/w3lib

`w3lib <https://w3lib.readthedocs.io>`_ collects low-level web utilities: entity resolution, tag/comment stripping, URL
canonicalization, and response-encoding helpers. Its ``w3lib.html`` text/entity subset maps onto the WHATWG tree, and
its ``w3lib.url`` canonicalization surface maps onto the :mod:`turbohtml.extract` URL helpers; only the HTTP-header and
file/data-URI helpers stay outside turbohtml's scope.

***************
 Why turbohtml
***************

:func:`turbohtml.unescape` resolves the same character references w3lib's regex-based ``replace_entities`` does, fully
type annotated and running the scan in C, so it is a drop-in that runs several times faster on entity-heavy input. Two
more ``w3lib.html`` helpers map onto the WHATWG tree: stripping a set of tags while keeping their text
(:meth:`~turbohtml.Node.strip_tags` against the regex ``remove_tags``, over a 92 kB page of 839
``<code>``/``<a>``/``<q>`` elements) and reading a document's own URL hints (:meth:`~turbohtml.Document.base_url` and
:meth:`~turbohtml.Document.meta_refresh` against ``get_base_url`` and ``get_meta_refresh``), each a structure-aware pass
that still beats the regex. The URL surface maps onto :mod:`turbohtml.extract`, whose canonical form is the `WHATWG URL
standard <https://url.spec.whatwg.org/>`_ serialization rather than w3lib's urllib re-encoding, 2x-6x faster over a
shared 100-URL batch:

.. bench-table::
    :file: bench/w3lib.json

*************
 The renames
*************

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

**********************
 URL canonicalization
**********************

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

Over the 253 URLs in w3lib's own test suite the two canonicalizers return identical output for 88% of inputs; every
divergence is the URL standard's form winning over a urllib legacy form. turbohtml keeps a valueless parameter as ``?q``
where w3lib appends ``?q=``, percent-encodes a query space as ``%20`` where w3lib emits ``+``, leaves ``,`` ``(`` ``)``
raw in queries (outside the WHATWG query percent-encode set) where w3lib escapes them, splits parameters on ``&`` only
(w3lib also splits the legacy ``;``), strips a default ``:80``/``:443`` port and resolves ``..`` segments where w3lib
keeps both, and drops known tracking parameters, which w3lib never does.

**********
 Pitfalls
**********

- ``remove_tags`` strips angle brackets with a regular expression and leaves entities encoded (``Tom &amp; Jerry``),
  while :attr:`~turbohtml.Node.text` runs the WHATWG tree builder and returns decoded characters (``Tom & Jerry``).
  turbohtml parses malformed and nested markup the way a browser does rather than matching ``<...>`` spans, so the two
  diverge on inputs a regex misreads.
- ``remove_tags_with_content`` edits the tree rather than returning a string: :meth:`~turbohtml.Node.remove` drops the
  matches in place, and :meth:`~turbohtml.Node.text` then reads what is left, so a one-line w3lib call becomes a
  parse-edit-read sequence.
- :func:`~turbohtml.extract.normalize_url` always removes known tracking parameters (``utm_*``, ``gclid``, ...);
  ``canonicalize_url`` keeps them. List one in ``UrlCleaning(query_allow=...)`` when it must survive.
- ``add_or_replace_parameter``, the response-encoding helpers (:func:`turbohtml.detect.detect` answers the encoding
  question from bytes), and the HTTP-header and file/data-URI helpers have no equivalent here.
