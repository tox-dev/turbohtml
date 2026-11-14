Parsing and sanitizing a MathML ``annotation-xml`` element with many attributes and many children no longer takes time
quadratic in the input size. The parser reads the element's ``encoding`` attribute once, when the start tag is parsed,
and the sanitizer reads it once per element rather than once per child.
