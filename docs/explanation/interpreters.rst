##############
 Interpreters
##############

turbohtml runs on CPython 3.10 and newer, on the free-threaded build, and on PyPy 3.10 and 3.11. The same C core serves
all of them: there is no pure-Python fallback, and no separate PyPy backend. What differs is the layer underneath, and
it differs enough to be worth understanding before you choose PyPy for an HTML workload.

***********************
 What cpyext costs you
***********************

PyPy runs C extensions through ``cpyext``, an emulation of CPython's C API. PyPy's own objects are moved by a compacting
garbage collector and its strings are stored as UTF-8; a C extension expects neither. So the first time a Python object
crosses into C, cpyext allocates a non-moving ``PyObject`` shell for it and keeps the two views in sync, and the first
time a ``str`` is read through the PEP 393 buffer macros, cpyext transcodes its UTF-8 storage into the UCS1/2/4 buffer
those macros hand out and caches it on the shell.

Both costs are per-object and paid once. That shape decides where PyPy is cheap and where it is not: an operation that
crosses the boundary once and then spends its time in C amortizes the cost over the whole document, while one that
crosses per node pays it per node. Measured on a 142 KB document against CPython 3.13, both on the same machine:

.. list-table::
    :header-rows: 1
    :widths: 34 22 22 22

    - - Operation
      - CPython 3.13
      - PyPy 7.3.23
      - PyPy is
    - - ``parse()`` (one 142 KB ``str``)
      - 848 µs
      - 933 µs
      - 1.1x slower
    - - ``unescape()``
      - 45.6 µs
      - 75.7 µs
      - 1.7x slower
    - - ``escape()``
      - 18.2 µs
      - 49.5 µs
      - 2.7x slower
    - - ``to_text()``
      - 86.2 µs
      - 235 µs
      - 2.7x slower
    - - ``serialize()``
      - 138 µs
      - 482 µs
      - 3.5x slower
    - - Walking every node
      - 131 µs
      - 653 µs
      - 5.0x slower

Parsing a whole document is nearly free, because it is one string in and one tree out. Walking that tree from Python is
the worst case, because every node visited materializes a wrapper across the boundary. If your program parses and then
queries with CSS or XPath -- work that stays inside C -- PyPy costs you little. If it walks the DOM node by node in a
Python loop, expect it to be several times slower than CPython, and note that the JIT cannot recover the difference: the
time is spent in cpyext, not in your bytecode.

None of this makes PyPy the wrong choice. It makes turbohtml a poor reason to choose it. Pick PyPy because the rest of
your program is Python that the JIT speeds up, and accept that the HTML layer is no faster than it is on CPython.

*******************************
 Behavior that differs on PyPy
*******************************

The public API behaves identically on both interpreters; the conformance suites, the tokenizer state machine, the
selector and XPath engines, and every serializer produce byte-identical output. Three things do not carry over.

**Reference cycles through a C object are never collected.** cpyext does not break a cycle that runs through both a
Python object and a C extension object, even though every turbohtml type implements ``tp_traverse`` and ``tp_clear``. A
cycle like ``document -> your callback -> document`` leaks on PyPy and is reclaimed on CPython. Break such cycles
yourself, or hold the C object through a :mod:`weakref`.

**Deep recursion may raise** :exc:`SystemError`. The schema validator, the XSLT processor, and the XPath engine cap
their own recursion and report a clean error past it. On PyPy, RPython's stack check can trip on the C recursion first
and surface as :exc:`SystemError`. The recursion stays bounded either way -- neither interpreter crashes -- but the
exception you catch differs.

**Introspection is thinner.** :func:`inspect.signature` builds no signature for a C *type* on PyPy, even though
``__text_signature__`` is present, so ``inspect.signature(turbohtml.Minify)`` raises :exc:`ValueError` there. Functions
and methods are unaffected. :func:`gc.is_tracked` does not exist on PyPy at all.

*******************
 Sealing the types
*******************

``Document()``, ``Node()``, ``Token()`` and eleven siblings raise :exc:`TypeError`: only a parse builds them, and one
constructed by hand carries no tree. CPython enforces that with ``Py_TPFLAGS_DISALLOW_INSTANTIATION``. cpyext ignored
that flag until PyPy 7.3.21, so those types would construct with no tree attached and segfault on first use, letting
pure Python code take the interpreter down. 7.3.21 honors the flag and prints a debug line to stdout for every type that
sets it, which corrupts anything reading the CLI's output.

Neither costs anything to avoid. cpyext also seals a type through an explicit ``tp_new``, the branch the flag would
otherwise shadow, so on PyPy the flag comes off and a ``tp_new`` that raises goes on. Every supported PyPy then refuses
the same constructions with the same message, and 7.3.21 stays quiet because nothing sets the flag it prints for. A
subtype declaring its own ``tp_new`` overrides the inherited one, so ``Element("div")`` and ``Text("hi")`` build as they
do on CPython.

*********************
 How the core adapts
*********************

``src/turbohtml/_c/core/pycompat.h`` holds every adaptation, and each one is an identity macro on CPython, so a CPython
build's preprocessed token stream is unchanged and its machine code cannot shift. Besides the sealing above, three calls
need it.

``PyUnicode_CopyCharacters`` does not exist in cpyext, so it gets a ``PyUnicode_READ``/``PyUnicode_WRITE`` loop.

``PyUnicode_FromFormat`` returns a string cpyext has not put in canonical form, so ``PyUnicode_KIND``,
``PyUnicode_DATA``, and ``PyUnicode_GET_LENGTH`` are all undefined on it -- and with ``NDEBUG`` their assertions are
compiled out, so ``GET_LENGTH`` silently answers one past the code point count. Every result is readied before use.

A 2-byte buffer cannot be handed to cpyext at all. It materializes one by decoding it as UTF-16, so a leading U+FEFF is
eaten as a byte-order mark, a leading U+FFFE byte-swaps the rest of the string, a surrogate pair collapses into the one
code point it encodes, and a lone surrogate -- which a CPython ``str`` carries fine, and which HTML input and the WHATWG
tokenizer must both preserve -- aborts the interpreter through the strict error handler. cpyext's 1-byte and 4-byte
paths are exact, so on PyPy any result too wide for Latin-1 is built at 4-byte kind. CPython keeps the narrowest kind,
because its ``str`` equality compares kind before content and would report a too-wide string as unequal to its own
value.
