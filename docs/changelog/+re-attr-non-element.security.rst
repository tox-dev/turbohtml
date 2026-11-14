Treat the attribute as absent when ``Node.re()`` or ``Node.re_first()`` is called with ``attr=`` on a text node,
processing instruction, doctype or other non-element node, instead of reading attribute storage that the node does not
have, which could crash the interpreter.
