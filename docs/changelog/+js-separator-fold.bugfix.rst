``minify_js()`` folds integer arithmetic over literals with ``_`` separators. ``1_0+1`` printed as ``10+1``, which a
second minification then folded to ``11``, so minifying the output again changed it.
