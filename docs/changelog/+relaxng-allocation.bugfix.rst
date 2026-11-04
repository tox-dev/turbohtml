Raise ``MemoryError`` instead of crashing, raising ``SystemError`` or returning a wrong verdict when ``RelaxNG``
compilation or ``RelaxNG.validate()`` cannot allocate; ``XMLSchema`` compilation and validation raise it too instead of
keeping a partial schema or a wrong verdict.
