XPath ``string-join``, ``replace``, ``re:replace``, ``str:replace`` and ``str:padding`` now raise :class:`ValueError`
when one evaluation adds more than 64 MiB to its strings, instead of allocating gigabytes.
