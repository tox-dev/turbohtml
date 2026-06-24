#############
 From pandas
#############

.. image:: https://static.pepy.tech/badge/pandas/month
    :alt: pandas monthly downloads
    :target: https://pepy.tech/project/pandas

`pandas <https://pandas.pydata.org>`_ is how scrapers turn ``<table>`` markup into rows: ``pandas.read_html`` parses
every table on a page into a list of ``DataFrame`` objects. Only that one helper overlaps with turbohtml; the rest of
pandas -- the ``DataFrame`` itself, its dtypes, and its analytics -- stays outside turbohtml's scope. ``read_html``
pulls NumPy and pandas into a project that may need neither just to read a grid of strings.

***************
 Why turbohtml
***************

:meth:`~turbohtml.Element.rows` and :meth:`~turbohtml.Element.records` resolve ``rowspan`` and ``colspan`` in C and
return plain lists and dicts, so the conversion carries no dependency. When you do want a frame, the records are the
exact shape ``pandas.DataFrame`` takes, so hand them over yourself and keep pandas optional:

.. testcode::

    import turbohtml

    table = turbohtml.parse("<table><tr><th>name</th><th>qty</th></tr><tr><td>pen</td><td>3</td></tr></table>").find(
        "table"
    )
    records = table.records()
    print(records)
    # pandas.DataFrame(records) builds the frame, keeping pandas optional

.. testoutput::

    [{'name': 'pen', 'qty': '3'}]

Reading a four-column table with :meth:`~turbohtml.Element.rows` and :meth:`~turbohtml.Element.records` against
``pandas.read_html``, both parsing the markup and resolving spans into a rectangular grid. turbohtml's C cell-grid walk
returns plain lists and dicts where ``read_html`` builds a ``DataFrame`` (importing NumPy), so it runs roughly twelve to
ninety times faster: the gap is widest on small tables, where pandas pays a fixed per-frame cost, and narrows as the row
count grows. See :doc:`/development/performance` for the methodology.

.. list-table::
    :header-rows: 1
    :widths: 40 30 30

    - - input
      - turbohtml
      - pandas.read_html
    - - rows (10 rows)
      - 10.8 µs
      - 943 µs (87.3x)
    - - records (10 rows)
      - 15.8 µs
      - 970 µs (61.4x)
    - - rows (100 rows)
      - 99.7 µs
      - 2178 µs (21.8x)
    - - records (100 rows)
      - 98.5 µs
      - 2165 µs (22.0x)
    - - rows (1000 rows)
      - 853 µs
      - 10.6 ms (12.4x)
    - - records (1000 rows)
      - 893 µs
      - 13.0 ms (14.6x)

*************
 The renames
*************

.. list-table::
    :header-rows: 1
    :widths: 50 50

    - - pandas
      - turbohtml
    - - ``pandas.read_html(html)`` -> ``list[DataFrame]``
      - :meth:`turbohtml.parse(html).tables() <turbohtml.Node.tables>` -> ``list[list[list[str]]]``
    - - ``pandas.read_html(html)[0]``
      - :meth:`turbohtml.parse(html).find("table").rows() <turbohtml.Element.rows>`
    - - ``pandas.read_html(html, header=0)[0].to_dict("records")``
      - :meth:`turbohtml.parse(html).find("table").records() <turbohtml.Element.records>`
    - - ``DataFrame(pandas.read_html(html)[0])``
      - ``pandas.DataFrame(turbohtml.parse(html).find("table").records())``

:meth:`~turbohtml.Node.tables` returns every table in a subtree, each already shaped as :meth:`~turbohtml.Element.rows`,
so a whole document reads back without locating each ``<table>`` first:

.. testcode::

    document = turbohtml.parse("<table><tr><td>a</td></tr></table><table><tr><td>b</td><td>c</td></tr></table>")
    print(document.tables())

.. testoutput::

    [[['a']], [['b', 'c']]]

**********
 Pitfalls
**********

- Cells are strings, always. turbohtml does not infer ``int``/``float``/``NaN`` dtypes the way pandas does; convert
  columns yourself, or let ``pandas.DataFrame`` do it after :meth:`~turbohtml.Element.records`.
- Spans are filled, not collapsed. A ``rowspan``/``colspan`` cell's text is copied into every slot it covers, where
  pandas can leave a single value and ``NaN`` padding depending on options.
- The header is the first row (typically the ``thead`` row); there is no ``header=``/``index_col=`` inference or a
  multi-row header. For anything fancier, read :meth:`~turbohtml.Element.rows` and slice it yourself.
- No I/O or flavors. ``read_html`` fetches URLs and picks an ``lxml``/``bs4`` parser; turbohtml parses markup you
  already hold with the one WHATWG algorithm, so fetch the page (e.g. with :mod:`urllib.request`) and pass the bytes to
  :func:`turbohtml.parse`.
