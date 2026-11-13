Resolve RDFa ``prefix`` declarations in time linear in the page: an element that declares a prefix no longer copies
every mapping in scope, so many declaring elements under an ancestor with a large ``prefix`` no longer stall
:meth:`~turbohtml.Document.rdfa` and :meth:`~turbohtml.Document.structured_data`.
