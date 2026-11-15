:meth:`~turbohtml.Node.find` and :meth:`~turbohtml.Node.find_all` no longer crash when a regex or callable filter edits
the tree during the search; the search carries on from the element it reached.
