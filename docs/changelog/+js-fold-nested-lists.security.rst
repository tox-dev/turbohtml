:func:`~turbohtml.clean.minify_js` no longer slows with the depth of nested redundant blocks and constant branches, and
drops statements after a jump only up to the next declaration.
