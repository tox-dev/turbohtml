``minify_js()`` keeps a read or write of a ``let``, ``const`` or ``class`` binding that can run before the declaration
initializes it, since that access throws a ``ReferenceError``. It dropped ``x=2`` from ``x=2;let x=1`` and the read of
``x`` from ``y=x`` with ``y`` unused, also when the access sat in a hoisted function called early or after a skipped
``switch`` case.
