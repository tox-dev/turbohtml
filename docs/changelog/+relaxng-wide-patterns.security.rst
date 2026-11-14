:class:`~turbohtml.validate.RelaxNG` no longer crashes the process on very wide patterns, and raises :class:`RecursionError` when references nest 400 levels deep without an ``element``.
