XSLT ``key()`` with a node-set as its second argument now returns each matching node once. It returned the matches once
per argument node, so the result and its memory grew with the square of a document that repeated one key value.
