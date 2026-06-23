##################
 From resiliparse
##################

.. image:: https://static.pepy.tech/badge/resiliparse
    :alt: resiliparse downloads
    :target: https://pepy.tech/project/resiliparse

`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ is the web-crawl processing toolkit from ChatNoir.
Its ``HTMLTree`` wraps the same `lexbor <https://lexbor.com>`_ engine selectolax does, building a WHATWG tree with real
text nodes, and it ships boilerplate extraction, language detection, and WARC utilities alongside the parser.

***************
 Why turbohtml
***************

resiliparse wraps lexbor's native parser; turbohtml has its own C engine and matches resiliparse's parse throughput,
while returning a fully type annotated, mutable :class:`~turbohtml.Document` and folding resiliparse's DOM traversal,
``get_element_by_*`` lookups, and CSS ``query_selector`` methods into one ``find``/``find_all``/``select`` grammar:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - parse
      - turbohtml
      - resiliparse
      - speed-up
    - - wpt page (4 kB)
      - 11.4 µs
      - 12.7 µs
      - 1.1x
    - - wpt page (92 kB)
      - 272 µs
      - 282 µs
      - 1.0x
    - - ecmascript spec (3 MB)
      - 4.54 ms
      - 5.35 ms
      - 1.2x

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - resiliparse
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
        ``first_child``
    - - ``tree.create_element("div")``, ``node.append_child(...)``, ``node.decompose()``
      - :class:`~turbohtml.Element`, :meth:`~turbohtml.Element.append`, :meth:`~turbohtml.Node.decompose`
    - - ``extract_plain_text(html)`` (from ``resiliparse.extract``)
      - :meth:`~turbohtml.Node.to_text` for layout, :attr:`~turbohtml.Node.text` for the raw concatenation
    - - ``extract_plain_text(html, main_content=True)``
      - :meth:`~turbohtml.Node.main_text`
    - - ``ExtractNode`` / boilerplate-stripped main content
      - :meth:`~turbohtml.Node.main_content`

.. testcode::

    doc = parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    print(doc.select_one("#main a").attrs["href"])
    print(doc.find(id="main").find("p").text)

.. testoutput::

    /u
    Hi link

*************
 Performance
*************

The parse table above is throughput only. resiliparse's ``extract_plain_text`` maps to :meth:`~turbohtml.Node.to_text`,
and its ``main_content=True`` mode to :meth:`~turbohtml.Node.main_text`; both render text off the lexbor tree in a
second pass, where turbohtml walks the WHATWG tree once in C:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - to text
      - turbohtml
      - resiliparse
      - speed-up
    - - article (2 KiB)
      - 7 µs
      - 23 µs
      - 3.1x
    - - table (4 KiB)
      - 28 µs
      - 52 µs
      - 1.8x
    - - main content (4 KiB)
      - 7 µs
      - 21 µs
      - 2.9x

The ``main content`` row strips page boilerplate first (:meth:`~turbohtml.Node.main_text` against
``extract_plain_text(main_content=True)``); resiliparse matches turbohtml on the raw parse but trails it on the text
walk, where the layout runs natively rather than over the lexbor tree.

**********
 Pitfalls
**********

- resiliparse's main-content extraction maps to :meth:`~turbohtml.Node.main_content` and
  :meth:`~turbohtml.Node.main_text`, a content-density (readability) heuristic over the parsed tree. Its language
  detection and the WARC/archive and crawl utilities it ships have no turbohtml equivalent and are out of scope; reach
  for a dedicated tool there.
- turbohtml compares nodes by identity over the underlying arena node, so two wrappers for the same element are equal
  but two separately parsed trees with identical markup are not; compare serializations or walk the tree instead.
