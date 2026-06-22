################
 From html2text
################

.. image:: https://static.pepy.tech/badge/html2text
    :alt: html2text downloads
    :target: https://pepy.tech/project/html2text

`html2text <https://github.com/Alir3z4/html2text>`_ converts HTML to Markdown with a streaming ``HTMLParser`` subclass
and a wide option surface, including a ``google_doc`` mode that reads the inline CSS a Google Docs export carries.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Node.to_markdown` replaces an ``html2text`` call with one fully type annotated method on the parsed
tree. It walks the WHATWG tree in C rather than driving a Python ``HTMLParser``, converting a page roughly fifty times
faster while covering the same options, including the Google Docs mode:

.. list-table::
    :header-rows: 1
    :widths: 40 20 20 20

    - - to Markdown
      - turbohtml
      - html2text
      - speed-up
    - - article (2 KiB)
      - 13 µs
      - 542 µs
      - 42.2x
    - - list (4 KiB)
      - 23 µs
      - 1143 µs
      - 49.6x
    - - table (4 KiB)
      - 26 µs
      - 1017 µs
      - 38.9x
    - - google_doc (4 KiB)
      - 18 µs
      - 560 µs
      - 31.9x

*************
 The renames
*************

.. code-block:: python

    # html2text
    import html2text

    html2text.html2text(text)

    # turbohtml
    import turbohtml

    turbohtml.parse(text).to_markdown()

.. testcode::

    print(parse("<h1>Title</h1><p>Some <b>bold</b> text.</p>").to_markdown())

.. testoutput::

    # Title

    Some **bold** text.

The html2text options map onto :meth:`~turbohtml.Node.to_markdown` keywords with one name per concept:

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - html2text
      - turbohtml :meth:`~turbohtml.Node.to_markdown`
    - - ``ul_item_mark``
      - ``bullets``
    - - ``emphasis_mark``, ``strong_mark``
      - ``emphasis``, ``strong``
    - - ``ignore_emphasis``
      - ``ignore_emphasis``
    - - ``ignore_links``
      - ``ignore_links``
    - - ``skip_internal_links``
      - ``skip_internal_links``
    - - ``inline_links``
      - ``link_style`` (``"inline"``/``"reference"``)
    - - ``ignore_images``, ``images_to_alt``, ``images_as_html``, ``images_with_size``
      - ``image_mode`` (``"markdown"``/``"alt"``/``"ignore"``/``"html"``)
    - - ``default_image_alt``
      - ``default_image_alt``
    - - ``ignore_tables``, ``bypass_tables``
      - ``table_mode`` (``"markdown"``/``"strip"``/``"html"``)
    - - ``pad_tables``
      - ``pad_tables``
    - - ``body_width``, ``wrap_list_items``, ``wrap_links``
      - ``wrap_width``, ``wrap_list_items``, ``wrap_links``
    - - ``unicode_snob`` (and the ``UNIFIABLE`` table)
      - ``transliterate``
    - - ``mark_code``
      - ``mark_code``
    - - ``backquote_code_style``
      - ``code_block_style`` (``"fenced"``/``"indented"``)
    - - ``single_line_break``
      - ``block_spacing="single"``
    - - ``baseurl``
      - ``base_url``
    - - ``open_quote``, ``close_quote``
      - ``quote_open``, ``quote_close``
    - - ``escape_snob``
      - ``escape_mode="all"``
    - - ``google_doc``
      - ``google_doc``
    - - ``google_list_indent``
      - ``google_list_indent``
    - - ``hide_strikethrough``
      - ``hide_strikethrough``

``google_doc=True`` reads the inline-CSS styling a Google Docs HTML export carries: a ``font-weight`` of ``bold`` or
``700``--``900`` becomes ``strong``, ``font-style:italic`` becomes ``emphasis``, a ``Courier New``/``Consolas``
``font-family`` becomes an inline code span, ``list-style-type`` picks the list marker, and each ``google_list_indent``
pixels of ``margin-left`` add one list-nesting level. With ``hide_strikethrough=True`` a
``text-decoration:line-through`` drops the struck text.

.. testcode::

    export = '<p><span style="font-weight:700">Quarterly</span> revenue</p>'
    print(parse(export).to_markdown(google_doc=True))

.. testoutput::

    **Quarterly** revenue

**********
 Pitfalls
**********

- :meth:`~turbohtml.Node.to_markdown` is a method on any node, so convert a subtree by calling it on the element you
  selected (``doc.find("article").to_markdown()``) instead of slicing the HTML string first.
- Layout-aware plain text (the ``inscriptis`` role, :meth:`~turbohtml.Node.to_text`) is a separate method; for the
  unstructured concatenation read :attr:`~turbohtml.Node.text`.
- ``base_url`` does simple prefixing rather than full RFC-3986 URL resolution.
