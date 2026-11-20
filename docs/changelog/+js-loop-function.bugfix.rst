``minify_js()`` no longer inlines a function declaration into the body of a ``while`` or ``do``-``while`` loop written
without braces, where the function was created again on every iteration. A block the output prints without braces also
resolves its names as the output parses, so minifying the output again changes it in fewer cases.
