A recursive XSLT stylesheet on a small-stack thread now raises ``RecursionError`` instead of crashing, and a very deep
one may fail where it used to finish.
