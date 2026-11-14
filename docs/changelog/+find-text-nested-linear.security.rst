Searching with a ``str`` or literal-regex ``text`` filter in :meth:`~turbohtml.Node.find` and
:meth:`~turbohtml.Node.find_all` no longer takes time quadratic in the nesting depth of the tree. Once the search has
built its index, a callable filter that edits the text of a later candidate is not seen by that candidate's text match.
