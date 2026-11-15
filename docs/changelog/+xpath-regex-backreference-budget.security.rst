Back-reference matching in XPath regular expressions now counts every compared character against the step budget, so
patterns with long captured groups raise :class:`ValueError` promptly.
