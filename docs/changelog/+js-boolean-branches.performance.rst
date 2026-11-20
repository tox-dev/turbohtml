``minify_js()`` folds a conditional with a ``true`` or ``false`` branch into a short circuit of the same value:
``c?x:!0`` becomes ``!c||x``, ``c?!1:x`` becomes ``!c&&x``, and with a comparison ``c``, ``c?x:!1`` becomes ``c&&x``.
