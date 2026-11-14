Parsing a MathML ``annotation-xml`` start tag with many attributes, followed by many child tokens, no longer takes time
quadratic in the input size. The parser reads the element's ``encoding`` attribute once, when the start tag is parsed.
