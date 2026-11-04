Raise ``MemoryError`` instead of crashing or returning a wrong result when ``Text.data`` assignment, ``Node.equals()``,
``copy.copy()``/``copy.deepcopy()``, ``Element.normalize()``, ``Range`` content operations, ``main_text()``,
``article()``, ``collapse_whitespace_node()``, ``XMLSchema.validate()``, ``RelaxNG`` compilation or an XSLT
``xsl:strip-space`` cannot materialize parsed text.
