XPath regex searches with back-references now count every capture group against the step limit, so such patterns raise
``ValueError`` instead of running past it.
