:class:`~turbohtml.validate.RelaxNG` compiles very wide patterns, and raises :class:`RecursionError` when references
nest 400 levels deep without an ``element``.
