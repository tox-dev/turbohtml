###########
 Reference
###########

The complete public API, grouped by task. Types and signatures come from the ``turbohtml._html`` extension's stub; the
prose is the C docstrings, so the reference cannot drift from the compiled core.

Start with :doc:`reference/parsing` to turn markup into a tree, :doc:`reference/nodes` for the node model and the
navigation, query, and mutation methods shared by every node, and :doc:`reference/query`, :doc:`reference/serialize`,
and :doc:`reference/tokenizer` for the search, output, and low-level token surfaces. :doc:`reference/linkify` and
:doc:`reference/sanitize` cover the link-rewriting and allowlist features.

.. currentmodule:: turbohtml

.. autodata:: __version__

.. toctree::
    :maxdepth: 2

    reference/parsing
    reference/nodes
    reference/query
    reference/serialize
    reference/tokenizer
    reference/linkify
    reference/sanitize
