Compile a regular expression with many named groups in linear time: ``matches``, ``replace``, ``re:test`` and
``re:replace`` no longer compare each ``(?P<name>...)`` with every earlier name, which made a pattern with many named
groups take quadratic time to compile.
