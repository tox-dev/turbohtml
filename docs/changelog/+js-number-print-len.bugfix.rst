``minify_js()`` weighs a number literal by the spelling it prints, not the one in the source, when deciding whether to
copy it into every read. ``const h=3600000`` read twice now inlines as ``36e5`` in one pass; before, the second
minification inlined it, so minifying the output again changed it.
