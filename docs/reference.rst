###########
 Reference
###########

The complete public API, grouped by task. Types and signatures come from the ``turbohtml._html`` extension's stub; the
prose is the C docstrings, so the reference cannot drift from the compiled core.

Start with :doc:`reference/parsing` to turn markup into a tree, :doc:`reference/nodes` for the node model and the
navigation, query, and mutation methods shared by every node, and :doc:`reference/query`, :doc:`reference/serialize`,
and :doc:`reference/tokenizer` for the search, output, and low-level token surfaces. :doc:`reference/match` covers the
soupsieve-shaped CSS matching surface, :doc:`reference/clean` the allowlist sanitizing and link-rewriting features,
:doc:`reference/extract` the content-extraction namespace, and :doc:`reference/structured-data` the JSON-LD / Microdata
/ OpenGraph extraction records.

.. currentmodule:: turbohtml

.. autodata:: __version__

.. toctree::
    :maxdepth: 2

    reference/parsing
    reference/nodes
    reference/query
    reference/serialize
    reference/tokenizer
    reference/match
    reference/clean
    reference/extract
    reference/structured-data
