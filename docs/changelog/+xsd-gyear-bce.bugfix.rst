XSD range facets on ``xs:gYear`` no longer read past the end of a year with no timezone, so a negative year such as
``-0045`` is rejected by ``minInclusive="-0044"`` every time instead of only sometimes.
