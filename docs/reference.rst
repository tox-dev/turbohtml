###########
 Reference
###########

The complete public API, grouped by task. Types and signatures come from the ``turbohtml._html`` extension's stub; the
prose is the C docstrings, so the reference cannot drift from the compiled core.

The pages follow the eight namespaces, in the order the :doc:`how-to guides </how-to/index>` and :doc:`migration guides
</migration/index>` use. :doc:`reference/parsing` turns markup into a tree, :doc:`reference/nodes` is the node model and
the navigation, query, and mutation methods shared by every node, and :doc:`reference/tokenizer` is the low-level token
surface. :doc:`reference/detect` sniffs the character encoding of raw bytes. :doc:`reference/query` is the CSS, XPath,
and soupsieve-shaped search surface, and :doc:`reference/cssom` resolves the CSS cascade to a computed style.
:doc:`reference/clean` covers allowlist sanitizing, link rewriting, and minification, :doc:`reference/convert`
translates CSS selectors to XPath 1.0, and :doc:`reference/transform` applies an XSLT 1.0 stylesheet.
:doc:`reference/extract` and :doc:`reference/structured-data` pull the article, its metadata, and the JSON-LD /
Microdata / OpenGraph records out of a page. :doc:`reference/build` constructs a tree in code, and
:doc:`reference/serialize` renders one back to HTML, Markdown, or plain text.

.. currentmodule:: turbohtml

.. autodata:: __version__

.. toctree::
    :maxdepth: 2

    reference/parsing
    reference/nodes
    reference/tokenizer
    reference/detect
    reference/query
    reference/cssom
    reference/clean
    reference/convert
    reference/transform
    reference/extract
    reference/structured-data
    reference/build
    reference/serialize
