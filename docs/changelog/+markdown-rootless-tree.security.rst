:meth:`~turbohtml.Node.to_markdown` no longer crashes the interpreter on an XML document, a fragment, a shadow root or a
detached inline element whose content ends in a line break, a no-break space or emphasized punctuation with no block
element around it.
