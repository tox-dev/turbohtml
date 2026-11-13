Reject a script whose identifier holds a malformed ``\u`` escape or one for a code point a name cannot spell, and stop
joining string literals whose ``\x`` or ``\u`` escape has no value, so ``minify_js`` never returns a ``str`` holding a
code point past U+10FFFF.
