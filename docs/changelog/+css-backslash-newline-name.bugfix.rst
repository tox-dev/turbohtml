Minifying CSS now reads a backslash before a newline as browsers do, dropping declarations that hold one unless they are
custom properties or use ``var()`` or ``env()``.
