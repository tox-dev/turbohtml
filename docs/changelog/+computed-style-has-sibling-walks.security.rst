``computed_style()`` no longer slows down quadratically on ``:has()`` rules that read ``:scope`` across long sibling
runs; past a work allowance it raises :exc:`ValueError` instead.
