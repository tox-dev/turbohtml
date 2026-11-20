``minify_js()`` no longer folds arithmetic over a legacy octal literal as if it were decimal: ``010*2`` became ``20``
where it evaluates to ``16``.
