``minify_js()`` re-spells a string literal from its value when that is shorter: an escaped character is written as
itself, the quote the value holds fewer of delimits it, and a ``+`` chain of strings joins across quote styles, so
lodash minifies 0.9% smaller.
