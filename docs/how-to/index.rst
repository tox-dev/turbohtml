###############
 How-to guides
###############

Task-focused recipes, one job each. The pages follow the eight turbohtml namespaces, in the order the :doc:`/reference`
and :doc:`/migration/index` use: parse a document and edit its tree, detect an encoding, query and match nodes, clean
and minify markup, convert a selector, extract the article and its tables, build a tree, and serialize it back to HTML,
Markdown, or plain text. Each page is a short, self-contained walkthrough you can lift straight into your own code,
ending with running the toolkit from a shell.

.. toctree::
    :maxdepth: 1
    :caption: Workflows

    scrape-for-llm
    page-metadata
    truncating

.. toctree::
    :maxdepth: 1
    :caption: Parse & DOM

    parsing
    xml
    tokenizing
    sax
    treebuild
    rewriting
    editing
    inspecting
    ranges
    observing-mutations
    shadow-dom
    forms
    from-htmlparser

.. toctree::
    :maxdepth: 1
    :caption: Detect

    encoding

.. toctree::
    :maxdepth: 1
    :caption: Query

    selecting
    finding
    traversing
    matching
    chaining
    pruning
    xpath
    extracting
    computed-style

.. toctree::
    :maxdepth: 1
    :caption: Clean

    sanitizing
    links
    minifying

.. toctree::
    :maxdepth: 1
    :caption: Convert & transform

    css-to-xpath
    xslt

.. toctree::
    :maxdepth: 1
    :caption: Extract

    main-content
    tables
    structured-data
    feeds

.. toctree::
    :maxdepth: 1
    :caption: Build

    building

.. toctree::
    :maxdepth: 1
    :caption: Serialize

    serializing
    canonicalizing
    markdown
    plain-text
    escaping

.. toctree::
    :maxdepth: 1
    :caption: Validate

    validating
    conformance

.. toctree::
    :maxdepth: 1
    :caption: Getting help

    cli
    troubleshooting
