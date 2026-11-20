``minify_js()`` keeps the name of an outer binding that a block function inside a same-named ``catch`` clause shadows.
Annex B hoists such a function to a ``var`` of the enclosing function, but only the ``catch`` parameter kept its name,
so in ``function g(){try{}catch(g){{function g(){}}}return typeof g}`` the renamed self-name changed ``typeof g`` from
``"undefined"`` to ``"function"``.
