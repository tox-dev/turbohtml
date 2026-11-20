``minify_js()`` copies a literal into the closures that read its binding when the declarators before it in the
function's first statement run no code, not only when it is the first declarator, so a module that opens with a run of
constants, as lodash does, minifies 0.2% smaller.
