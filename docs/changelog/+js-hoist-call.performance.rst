``minify_js()`` rewrites a conditional whose two arms call the same name with arguments that differ in one place,
``c?g(a):g(b)``, as ``g(c?a:b)`` when evaluating the test can neither throw nor run code, so a chain of guard
returns that each call one function minifies about 20% smaller.
