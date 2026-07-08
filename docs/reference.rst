###########
 Reference
###########

The complete public API, grouped by task. Types and signatures come from the ``turbohtml._html`` extension's stub; the
prose is the C docstrings, so the reference cannot drift from the compiled core.

The pages group the same way the :doc:`how-to guides </how-to/index>` and :doc:`migration guides </migration/index>` do,
by the turbohtml namespace that owns each task:

**Parse & DOM** turns markup into a tree (:doc:`reference/parsing`), documents the node model with the navigation,
query, and mutation methods every node shares (:doc:`reference/nodes`), and exposes the low-level token surface beneath
it (:doc:`reference/tokenizer`). **Detect** sniffs the character encoding and language of raw bytes
(:doc:`reference/detect`). **Query** searches a tree with CSS, XPath, and the soupsieve-shaped API
(:doc:`reference/query`) and resolves the CSS cascade to a computed style (:doc:`reference/cssom`). **Clean** sanitizes
against an allowlist, rewrites bare URLs into links, and minifies (:doc:`reference/clean`). **Convert & transform**
translates CSS selectors to XPath 1.0 (:doc:`reference/convert`) and applies an XSLT 1.0 stylesheet
(:doc:`reference/transform`). **Extract** pulls the article and its metadata (:doc:`reference/extract`) and the JSON-LD,
Microdata, and OpenGraph records (:doc:`reference/structured-data`) out of a page. **Build** constructs a tree in code
(:doc:`reference/build`), and **Serialize** renders one back to HTML, Markdown, or plain text
(:doc:`reference/serialize`). **Validate** checks a document against an XSD 1.0 or RELAX NG schema
(:doc:`reference/validate`) and runs the HTML5 authoring-conformance rules (:doc:`reference/conformance`).

.. currentmodule:: turbohtml

.. autodata:: __version__

.. toctree::
    :maxdepth: 2
    :caption: Parse & DOM

    reference/parsing
    reference/nodes
    reference/tokenizer

.. toctree::
    :maxdepth: 2
    :caption: Detect

    reference/detect

.. toctree::
    :maxdepth: 2
    :caption: Query

    reference/query
    reference/cssom

.. toctree::
    :maxdepth: 2
    :caption: Clean

    reference/clean

.. toctree::
    :maxdepth: 2
    :caption: Convert & transform

    reference/convert
    reference/transform

.. toctree::
    :maxdepth: 2
    :caption: Extract

    reference/extract
    reference/structured-data

.. toctree::
    :maxdepth: 2
    :caption: Build

    reference/build

.. toctree::
    :maxdepth: 2
    :caption: Serialize

    reference/serialize

.. toctree::
    :maxdepth: 2
    :caption: Validate

    reference/validate
    reference/conformance
