Keep the text of a RELAX NG ``include`` or ``externalRef`` target alive while its tree is copied into the schema, so
compiling a multi-file schema no longer reads freed memory.
