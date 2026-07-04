###################################
 Read tables into rows and records
###################################

Turn an HTML ``<table>`` into Python data with :meth:`~turbohtml.Element.rows`, :meth:`~turbohtml.Element.records`, and
:meth:`~turbohtml.Node.tables`, with ``rowspan`` and ``colspan`` resolved so the result is rectangular -- no pandas
dependency.

:meth:`~turbohtml.Element.rows` reads a ``<table>`` into a list of rows, each a ``list[str]``, with ``rowspan`` and
``colspan`` resolved by filling every spanned cell, so the result is rectangular and you never resolve spans by hand:

.. testcode::

    import turbohtml

    table = turbohtml.parse(
        "<table>"
        "<tr><th>Region</th><th>Q1</th><th>Q2</th></tr>"
        "<tr><td rowspan=2>West</td><td>10</td><td>12</td></tr>"
        "<tr><td>8</td><td>9</td></tr>"
        "</table>"
    ).find("table")
    for row in table.rows():
        print(row)

.. testoutput::

    ['Region', 'Q1', 'Q2']
    ['West', '10', '12']
    ['West', '8', '9']

:meth:`~turbohtml.Element.records` keys the first row (the header, typically the ``thead`` row) over each later row as a
``list[dict]`` -- the shape a ``pandas.read_html`` user feeds straight to ``pandas.DataFrame``, with no pandas
dependency:

.. testcode::

    for record in table.records():
        print(record)

.. testoutput::

    {'Region': 'West', 'Q1': '10', 'Q2': '12'}
    {'Region': 'West', 'Q1': '8', 'Q2': '9'}

:meth:`~turbohtml.Node.tables` returns every table on the page, each as :meth:`~turbohtml.Element.rows`, so you can scan
a document without locating each ``<table>`` first:

.. testcode::

    document = turbohtml.parse("<table><tr><td>a</td></tr></table><table><tr><td>b</td><td>c</td></tr></table>")
    print(document.tables())

.. testoutput::

    [[['a']], [['b', 'c']]]
