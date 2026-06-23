#####################
 From w3lib (Scrapy)
#####################

.. image:: https://static.pepy.tech/badge/w3lib/month
    :alt: w3lib monthly downloads
    :target: https://pepy.tech/project/w3lib

`w3lib <https://w3lib.readthedocs.io>`_ collects the web utilities `Scrapy <https://scrapy.org>`_ reuses: entity
resolution, tag/comment stripping, URL canonicalization, and response-encoding helpers. Only its ``w3lib.html``
text/entity subset overlaps with turbohtml; the ``w3lib.url`` and HTTP helpers stay outside turbohtml's scope.

***************
 Why turbohtml
***************

:func:`turbohtml.unescape` resolves the same character references w3lib's regex-based ``replace_entities`` does, fully
type annotated and running the scan in C, so it is a drop-in that runs several times faster on entity-heavy input:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - unescape
      - turbohtml
      - w3lib
      - speed-up
    - - tiny plain (64 B)
      - 0.02 µs
      - 0.25 µs
      - 12.4x
    - - medium dense refs (4 KiB)
      - 8.10 µs
      - 116 µs
      - 14.3x
    - - book HTML, real refs (4 MiB)
      - 2.51 ms
      - 13.5 ms
      - 5.4x

Stripping a set of tags while keeping their text: w3lib's regex ``remove_tags`` against turbohtml's
:meth:`~turbohtml.Node.strip_tags`, over a 92 kB page holding 839 ``<code>``/``<a>``/``<q>`` elements. w3lib runs a
regular-expression substitution over the string; turbohtml builds the WHATWG tree, unwraps each match in place, and
serializes. turbohtml does the structure-aware pass a regex misreads on nested markup, and still runs it faster:

.. list-table::
    :header-rows: 1
    :widths: 46 18 18 18

    - - strip tags, keep text (92 kB)
      - turbohtml
      - w3lib
      - speed-up
    - - ``strip_tags`` vs ``remove_tags``
      - 607 µs
      - 1.11 ms
      - 1.8x

Reading a document's own URL hints: w3lib's ``get_base_url`` and ``get_meta_refresh`` against
:meth:`~turbohtml.Document.base_url` and :meth:`~turbohtml.Document.meta_refresh`. Both parse the string each call, over
a small page carrying a ``<base>`` and a meta refresh. w3lib runs a regular-expression pass; turbohtml runs the WHATWG
tree builder and reads the hint off the parsed ``<head>``, and still comes out ahead (``tox -e bench extract``):

.. list-table::
    :header-rows: 1
    :widths: 46 18 18 18

    - - url hint
      - turbohtml
      - w3lib
      - speed-up
    - - ``base_url`` vs ``get_base_url``
      - 2.9 µs
      - 7.4 µs
      - 2.6x
    - - ``meta_refresh`` vs ``get_meta_refresh``
      - 3.0 µs
      - 6.5 µs
      - 2.1x

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - w3lib
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
match's content. Reach for ``turbohtml.sanitizer`` instead when the goal is producing safe HTML rather than reshaping a
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
- The URL canonicalization, response-encoding, and HTTP helpers in ``w3lib.url`` and elsewhere have no equivalent here.
