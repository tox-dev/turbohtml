Schema pattern checks in one :meth:`~turbohtml.validate.XMLSchema.validate` call now share a step budget, so a pattern
that runs for minutes raises :class:`ValueError` instead.
