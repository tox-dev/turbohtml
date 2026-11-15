XPath ``matches``, ``replace``, ``re:test`` and ``re:replace`` now count matching work against the evaluation's step
budget, so expensive patterns raise :class:`ValueError` instead of running for minutes.
