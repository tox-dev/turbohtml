XPath string and node-set functions now raise :class:`ValueError` once one call gathers more than 10 MiB of repeated
element text, instead of running without limit.
