Attribute names in the HTML tokenizer and tree, and the other tables keyed by text the input picks, now hash under a
per-process secret. A document can no longer choose strings that share one probe run and make :func:`turbohtml.parse`,
:func:`turbohtml.tokenize`, ``xsl:key`` lookups and the clean helpers take quadratic time, and results are unchanged.
