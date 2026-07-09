####################
 Building on cpyext
####################

The same C core runs on CPython and on PyPy, with no pure-Python fallback and no second backend. This page covers what
had to change to make that true, and the invariant that keeps it from costing CPython anything. What cpyext costs a
*user* is on :doc:`/explanation/interpreters`.

Most of the C API surface needs no adaptation. cpyext emulates the PEP 393 layout, so ``PyUnicode_KIND``,
``PyUnicode_DATA``, ``PyUnicode_READ`` and ``PyUnicode_WRITE`` all work, and the heap types, module state, multi-phase
init, and GC slots the module is built on are supported. ``Py_BEGIN_CRITICAL_SECTION`` collapses to a brace no-op there
through the same ``#ifndef`` that CPython 3.10 through 3.12 take, and for the same reason: cpyext holds the GIL across
every call into the extension.

**********************************
 The CPython codegen is untouched
**********************************

``src/turbohtml/_c/core/pycompat.h`` holds every adaptation, and each name in it expands to the CPython spelling on
CPython. ``TH_SEALED`` becomes ``Py_TPFLAGS_DISALLOW_INSTANTIATION``, ``TH_SEALED_END`` becomes ``{0, NULL}``, and
``th_str_format(...)`` becomes ``PyUnicode_FromFormat(__VA_ARGS__)``. A CPython translation unit's preprocessed token
stream is therefore unchanged, so the profile-guided and link-time-optimized layouts the release wheels depend on cannot
shift. Every PyPy-only code path sits behind ``#ifdef PYPY_VERSION`` and never reaches the CPython compiler.

The invariant is checkable rather than aspirational: compile every translation unit from ``main`` and from a branch at
``-O3 -g0 -DNDEBUG`` and compare the normalized ``objdump -d`` output. Anything that differs and is not a deliberate
behavior change is a regression.

``tests/core/test_c_api_portability.py`` pins the other half. Each guarded spelling may be named only inside
``pycompat.h``; everywhere else the C sources must reach it through the wrapper. Reaching for the raw spelling compiles,
passes on CPython, and is silently wrong on PyPy, which no compiler catches.

*******************
 Sealing the types
*******************

``Document()``, ``Node()``, ``Token()`` and eleven siblings raise :exc:`TypeError`, because only a parse builds them and
one constructed by hand carries no tree. CPython enforces that with ``Py_TPFLAGS_DISALLOW_INSTANTIATION``. cpyext
ignored that flag until PyPy 7.3.21 (`pypy#5318 <https://github.com/pypy/pypy/issues/5318>`_), so those types would
construct with no tree attached and segfault on first use, letting pure Python code take the interpreter down. 7.3.21
honors the flag and prints a debug line to stdout for every type that sets it (`pypy#5388
<https://github.com/pypy/pypy/issues/5388>`_), which corrupts anything reading the CLI's output.

Neither costs anything to avoid. cpyext also seals a type through an explicit ``tp_new``, the branch the flag would
otherwise shadow, so on PyPy the flag comes off and a ``tp_new`` that raises goes on. Every supported PyPy then refuses
the same constructions with the same message, and 7.3.21 stays quiet because nothing sets the flag it prints for. A
subtype declaring its own ``tp_new`` overrides the inherited one, so ``Element("div")`` and ``Text("hi")`` build as they
do on CPython.

**********************
 The three other gaps
**********************

``PyUnicode_CopyCharacters`` does not exist in cpyext, so it gets a ``PyUnicode_READ``/``PyUnicode_WRITE`` loop.

``PyUnicode_FromFormat`` returns a string cpyext has not put in canonical form, so ``PyUnicode_KIND``,
``PyUnicode_DATA``, and ``PyUnicode_GET_LENGTH`` are all undefined on it, and with ``NDEBUG`` their assertions are
compiled out, so ``GET_LENGTH`` answers one past the code point count (`pypy#5524
<https://github.com/pypy/pypy/issues/5524>`_). The core readies every result before touching it.

cpyext cannot take a 2-byte buffer at all (`pypy#5525 <https://github.com/pypy/pypy/issues/5525>`_). It materializes one
by decoding it as UTF-16, so it eats a leading U+FEFF as a byte-order mark, byte-swaps the rest of the string after a
leading U+FFFE, collapses a surrogate pair into the one code point it encodes, and aborts the interpreter on a lone
surrogate through the strict error handler. A CPython ``str`` carries a lone surrogate fine, and both HTML input and the
WHATWG tokenizer have to preserve one. cpyext's 1-byte and 4-byte paths are exact, so on PyPy any result too wide for
Latin-1 is built at 4-byte kind. CPython keeps the narrowest kind, because its ``str`` equality compares kind before
content and would report a too-wide string as unequal to its own value.

``encoding/decode.h`` calls ``PyUnicode_New`` directly and deliberately: every WHATWG decoder replaces an ill-formed
sequence with U+FFFD and no legacy encoding maps a byte to a surrogate, so its 2-byte results hold none, and
``PyUnicode_New`` pins the byte order where ``PyUnicode_FromKindAndData`` does not.

A fourth difference lives outside that header. A negative subscript reaches ``sq_item`` unadjusted (`pypy#5526
<https://github.com/pypy/pypy/issues/5526>`_) where CPython adds the sequence length first, so ``dom/node.c`` does that
adjustment itself.
