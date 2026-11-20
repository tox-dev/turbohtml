``minify_js()`` writes a hexadecimal, octal or binary literal in decimal when that is shorter and the value is exact:
``0x10000`` becomes ``65536`` and ``0x3E8`` becomes ``1e3``. Integer arithmetic over such literals now folds as well, so
``4*0x40`` becomes ``256`` in one pass instead of two.
