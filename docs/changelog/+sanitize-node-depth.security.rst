The sanitize functions now raise :exc:`RecursionError` for a tree with more than 1,536 disallowed elements nested in one
another, instead of taking quadratic time.
