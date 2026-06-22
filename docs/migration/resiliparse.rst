##################
 From resiliparse
##################

`resiliparse <https://github.com/chatnoir-eu/chatnoir-resiliparse>`_ wraps the same `lexbor <https://lexbor.com>`_
engine selectolax does, so like turbohtml it builds a WHATWG tree with real text nodes; the move is API surface. Its
``HTMLTree`` exposes DOM-style traversal and both ``get_element_by_*`` lookups and CSS ``query_selector`` methods, which
turbohtml folds into the one ``find``/``find_all``/``select`` grammar.

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - resiliparse
      - turbohtml
    - - ``HTMLTree.parse(html)``
      - ``turbohtml.parse(html)``
    - - ``tree.body``, ``tree.head``, ``tree.title``
      - ``doc.find("body")``, ``doc.find("head")``, ``doc.find("title").text``
    - - ``node.query_selector("a")``, ``node.query_selector_all("a")``
      - ``node.select_one("a")``, ``node.select("a")``
    - - ``node.get_element_by_id("main")``
      - ``node.find(id="main")``
    - - ``node.get_elements_by_tag_name("a")``, ``node.get_elements_by_class_name("x")``
      - ``node.find_all("a")``, ``node.find_all(class_="x")``
    - - ``node.getattr("href")``, ``node.hasattr("href")``, ``node.setattr(...)``, ``node.delattr(...)``
      - ``node.attrs.get("href")``, ``"href" in node.attrs``, ``node.attrs[...] = ...``, ``del node.attrs[...]``
    - - ``node.tag``, ``node.text``, ``node.html``, ``node.class_list``
      - ``node.tag``, ``node.text``, ``node.html``, ``node.attrs["class"]``
    - - ``node.parent``, ``node.next_element``, ``node.prev_element``, ``node.first_element_child``
      - ``node.parent``, ``node.next_sibling``, ``node.previous_sibling``, ``node.first_child``
    - - ``tree.create_element("div")``, ``node.append_child(...)``, ``node.decompose()``
      - ``Element("div")``, ``node.append(...)``, ``node.decompose()``
    - - ``extract_plain_text(html)`` (from ``resiliparse.extract``)
      - ``node.to_text()`` for layout, ``node.text`` for the raw concatenation
    - - ``extract_plain_text(html, main_content=True)``
      - ``node.main_text()`` (the dominant article body as text)
    - - ``ExtractNode`` / boilerplate-stripped main content
      - ``node.main_content()`` (the dominant article element, or ``None``)

.. testcode::

    doc = parse("<div id=main><p class=x>Hi <a href='/u'>link</a></p></div>")
    print(doc.select_one("#main a").attrs["href"])
    print(doc.find(id="main").find("p").text)

.. testoutput::

    /u
    Hi link

**********
 Pitfalls
**********

- resiliparse's main-content extraction maps to :py:meth:`~turbohtml.Node.main_content` and
  :py:meth:`~turbohtml.Node.main_text`, a content-density (readability) heuristic over the parsed tree. Its language
  detection and the WARC/archive and crawl utilities it ships for web-crawl processing have no turbohtml equivalent and
  are out of scope; reach for a dedicated tool there.
- turbohtml compares nodes by value, not identity, so ``find("p") == find("p")`` is ``False`` where resiliparse returns
  the same wrapped node; compare serializations or walk the tree instead.

*****************
 A note on gumbo
*****************

`gumbo <https://github.com/google/gumbo-parser>`_, the C WHATWG parser behind `html5-parser
<https://html5-parser.readthedocs.io>`_, has no migration table here. It is read-oriented and archived upstream, and its
Python binding no longer builds on a current toolchain, so there is nothing to port from in practice. Code that read a
gumbo tree maps onto the same :meth:`~turbohtml.Node.find`/:meth:`~turbohtml.Node.select` read surface shown above, with
turbohtml supplying the maintained, mutable, typed tree that lineage lacks. The maintained `html5-parser
<https://github.com/kovidgoyal/html5-parser>`_ wraps the same gumbo engine and has its own section below.
