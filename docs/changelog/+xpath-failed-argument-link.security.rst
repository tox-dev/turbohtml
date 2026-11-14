Parsing an XPath function call now stops at the first malformed argument, so a rejected expression no longer corrupts
heap memory before raising :class:`ValueError`.
