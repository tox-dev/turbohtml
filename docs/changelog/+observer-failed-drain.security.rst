Prevent a double free when ``MutationObserver.take_records()`` or ``deliver()`` fails part-way through a batch, as it
does on an attribute whose name is a lone surrogate. The failed drain now drops its batch, and a drain no longer reads
observer state that ``take_records()``, ``deliver()`` or ``disconnect()`` changes while a record is being built.
