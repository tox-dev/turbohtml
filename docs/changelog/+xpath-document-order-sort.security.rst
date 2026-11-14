Putting an XPath node-set into document order walked a sibling list or the path to the root for every compared pair, so
a union, a bound variable, or a reverse-axis step over a wide sibling list or a deep tree took time quadratic in its
size, and cubic inside a predicate. Each node-set is now ranked in one pass, and reverse-axis results are reversed
without a sort.
