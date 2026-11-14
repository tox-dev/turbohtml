Bound the time and memory :func:`~turbohtml.clean.minify_css` spends on nested ``@media`` blocks that repeat a prelude
at every level. Blocks fuse only when at most seven blocks enclose them, so deeper same-query blocks stay separate.
