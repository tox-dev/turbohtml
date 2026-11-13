Raise ``ValueError`` instead of crashing the interpreter when a stylesheet calls ``system-property()``,
``function-available()`` or ``element-available()`` without an argument; a call with more than one is rejected too.
