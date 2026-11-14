Bound the time ``sanitize``, ``sanitize_node`` and ``migration.bleach.clean`` spend on deeply nested disallowed elements
when the output must also be parse-stable, and escape a link nested in a link that follows an element that escapes to
nothing. Past 32 levels, ``sanitize_node`` now keeps an allowed element it used to escape, so it agrees with
``sanitize`` on such input.
