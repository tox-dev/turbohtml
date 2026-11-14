``minify_js`` no longer searches the statement after a declaration once per regex-initialized binding. Scripts with many
such bindings in front of one long statement took time quadratic in their length.
