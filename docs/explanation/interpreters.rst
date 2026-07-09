##############
 Interpreters
##############

turbohtml runs on CPython 3.10 and newer, on the free-threaded build, and on PyPy 3.10 and 3.11. The same C core serves
all of them: there is no pure-Python fallback, and no separate PyPy backend. What differs is the layer underneath, and
it differs enough to be worth understanding before you choose PyPy for an HTML workload. How the core adapts to that
layer is a maintenance concern, covered in :doc:`/development/cpyext`.

***********************
 What cpyext costs you
***********************

PyPy runs C extensions through ``cpyext``, an emulation of CPython's C API. A compacting garbage collector moves PyPy's
own objects, and PyPy stores its strings as UTF-8. A C extension expects neither. So the first time a Python object
crosses into C, cpyext allocates a non-moving ``PyObject`` shell for it and keeps the two views in sync, and the first
time a ``str`` is read through the PEP 393 buffer macros, cpyext transcodes its UTF-8 storage into the UCS1/2/4 buffer
those macros hand out and caches it on the shell.

Both costs are per-object and paid once, and that decides where PyPy is cheap. Reading a ``str`` that C has already seen
costs nothing extra, because the buffer is cached on the shell: turbohtml's scanners run at CPython's speed over it.
Handing a new string back does cost, because cpyext transcodes it into PyPy's own UTF-8 form. So does handing back a
wrapper per node.

.. bench-table::
    :file: bench/interpreters.json

Parsing pays both costs once: one string in, one tree out. Serializing a tree or collecting its visible text walks it in
C just as fast as on CPython, then pays for the long string it hands back, which is why they trail by an order of
magnitude while doing no per-node work in Python at all. Walking every descendant pays a shell per node. A CSS query
costs less than either, because the match stays in C and only the elements it finds cross.

The JIT recovers none of it. The time goes to cpyext rather than to your bytecode, so no amount of warm-up helps.

None of this makes PyPy the wrong choice. It makes turbohtml a poor reason to choose it. Pick PyPy because the rest of
your program is Python that the JIT speeds up, and accept that the HTML layer is slower than it is on CPython.

Regenerate the table with ``tox -e bench -- interpreters``, which builds the working tree under each interpreter and
measures the same corpus in each.

*******************************
 Behavior that differs on PyPy
*******************************

The public API behaves the same on both interpreters. The conformance suites, the tokenizer state machine, the selector
and XPath engines, and every serializer produce byte-identical output. Three things do not carry over.

**Reference cycles through a C object are never collected.** cpyext does not break a cycle that runs through both a
Python object and a C extension object, even though every turbohtml type implements ``tp_traverse`` and ``tp_clear``. A
cycle like ``document -> your callback -> document`` leaks on PyPy, where CPython reclaims it. Break such cycles
yourself, or hold the C object through a :mod:`weakref`.

**Deep recursion may raise** :exc:`SystemError`. The schema validator, the XSLT processor, and the XPath engine cap
their own recursion and report a clean error past it. On PyPy, RPython's stack check can trip on the C recursion first
and surface as :exc:`SystemError`. The recursion stays bounded either way, since neither interpreter crashes, but the
exception you catch differs.

**Introspection is thinner.** :func:`inspect.signature` builds no signature for a C *type* on PyPy, even though
``__text_signature__`` is present, so ``inspect.signature(turbohtml.Minify)`` raises :exc:`ValueError` there. Functions
and methods are unaffected. :func:`gc.is_tracked` does not exist on PyPy at all.
