``minify_js()`` drops the names of a declaration's trailing declarators that the next statement reads once, in order, or
that nothing reads, keeping their initializers, so code that declares call results to use them once minifies 29-46%
smaller. A fold pass also skips the functions the pass before it left unchanged, so jquery, lodash and underscore minify
with 4-9% fewer instructions.
