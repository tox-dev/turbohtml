Feeding :class:`turbohtml.IncrementalParser`, :class:`turbohtml.Tokenizer` or
:class:`turbohtml.migration.stdlib.HTMLParser` a character reference whose digits, or whose name characters past the
longest entity name, never end no longer takes time quadratic in its length: the suspended reference resumes its scan
where the last chunk ran out instead of starting over at its ``&``.
