Bound the time ``sanitize``, ``sanitize_node`` and ``migration.bleach.clean`` spend on deeply nested disallowed elements
when the output must also be parse-stable, and escape a link nested in a link that follows an element that escapes to
nothing. Past 32 levels, the output of ``sanitize_node``, of ``sanitize`` on input that must be parse-stable, and of
``migration.bleach.clean`` can differ from before, and ``sanitize_node`` agrees with ``sanitize`` on such input.
