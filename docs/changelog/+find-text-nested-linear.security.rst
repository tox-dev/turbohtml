Searching with a ``str`` or literal-regex ``text`` filter in :meth:`~turbohtml.Node.find` and
:meth:`~turbohtml.Node.find_all` no longer takes quadratic time on deeply nested XML documents.
