##########################################
 Turn a web page into Markdown for an LLM
##########################################

Feeding a page to a language model wants the article as clean Markdown, not the raw HTML with its navigation, sidebars,
and scripts. This is the fetch-then-strip-then-convert pattern behind langchain's ``BeautifulSoupTransformer`` and the
markdownify loaders. turbohtml does it in three calls: parse, isolate the content, render to Markdown.

Fetch the page with whatever HTTP client you use, then hand the response body to :func:`turbohtml.parse`. Here a literal
string stands in for the download:

.. testcode::

    import turbohtml

    response_text = (
        "<body><nav>Home About Contact</nav>"
        "<article class='post'><h1>Comets</h1>"
        "<p>A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.</p>"
        "<p>The tail always points away from the Sun, pushed out by the solar wind and radiation.</p>"
        "</article>"
        "<aside class='sidebar'><p>Related links and more site navigation cruft live over here.</p></aside></body>"
    )
    doc = turbohtml.parse(response_text)

:meth:`~turbohtml.Node.main_content` scores the page and returns the article element, dropping the nav and sidebar.
:meth:`~turbohtml.Node.to_markdown` renders that element as GitHub-Flavored Markdown:

.. testcode::

    print(doc.main_content().to_markdown())

.. testoutput::

    # Comets

    A comet is an icy body that releases gas, forming a visible tail, as it nears the Sun.

    The tail always points away from the Sun, pushed out by the solar wind and radiation.

The two calls compose: :meth:`~turbohtml.Node.main_content` replaces the readability pass, and
:meth:`~turbohtml.Node.to_markdown` replaces markdownify, with no intermediate string. When you want to keep a known
region instead of letting the scorer choose -- a ``<main>``, an article by id -- reach for
:meth:`~turbohtml.Node.prune`, which trims the document to a selector in place, then render the kept element:

.. testcode::

    doc = turbohtml.parse(
        "<body><nav>menu</nav><main><h1>Docs</h1><p>Read me first.</p></main><footer>foot</footer></body>"
    )
    doc.prune("main")
    print(doc.find("main").to_markdown())

.. testoutput::

    # Docs

    Read me first.

For the scoring knobs and per-paragraph classification, see :doc:`main-content`; for the Markdown options, see
:doc:`markdown`.
