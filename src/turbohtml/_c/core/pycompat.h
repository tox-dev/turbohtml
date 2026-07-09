/* Where CPython's C API and PyPy's cpyext differ, for the calls this core depends on.

   Most of the surface needs no adaptation. cpyext emulates the PEP 393 layout -- PyASCIIObject's
   kind and data pointer, and with them PyUnicode_KIND / DATA / READ / WRITE -- by transcoding a
   str's UTF-8 storage into a UCS1/2/4 buffer the first time it crosses into C and caching it on the
   PyObject, so the SWAR scanners are correct there and pay one O(len) materialization per string.
   Heap types, module state, multi-phase init, and the GC slots all work, and
   Py_BEGIN_CRITICAL_SECTION already collapses to a brace no-op through tokenizer/binding.h's
   `#ifndef`, correct because cpyext holds the GIL across every call into this extension.

   Four things differ. Each name below expands to the CPython spelling on CPython, so a CPython
   translation unit's preprocessed token stream is unchanged by this file and its codegen cannot
   shift. dom/node.c handles a fifth difference where it bites, cpyext handing sq_item a raw negative
   index: https://github.com/pypy/pypy/issues/5526

   Sealing. cpyext ignored Py_TPFLAGS_DISALLOW_INSTANTIATION before PyPy 7.3.21, so the sealed types
   constructed with no tree attached and segfaulted on first use, and 7.3.21 prints a debug line to
   stdout for every type that sets it (https://github.com/pypy/pypy/issues/5318, .../5388). cpyext
   also seals through an explicit tp_new, the arm the flag would otherwise shadow, so on PyPy
   TH_SEALED drops the flag and TH_SEALED_END adds th_disallow_new. Both are needed together. A
   subtype declaring its own tp_new overrides the inherited one, as it does on CPython.

   PyUnicode_CopyCharacters does not exist in cpyext at all, so it gets the READ/WRITE loop below.

   PyUnicode_FromFormat returns a string cpyext left in the legacy wstr representation, so
   PyUnicode_KIND, PyUnicode_DATA and PyUnicode_GET_LENGTH are all undefined on it, and with NDEBUG
   their asserts are gone so GET_LENGTH answers one past the code point count
   (https://github.com/pypy/pypy/issues/5524). Every other constructor this core uses returns a ready
   string. Call th_str_format, never PyUnicode_FromFormat directly; tests/core/test_c_api_portability
   pins that.

   A 2-byte buffer cannot be handed to cpyext at all (https://github.com/pypy/pypy/issues/5525). It
   decodes one as UTF-16, eating a leading U+FEFF, byte-swapping the rest after a leading U+FFFE,
   folding a surrogate pair into the code point it encodes, and aborting the interpreter outright on
   a lone surrogate, which a CPython str carries fine and which the WHATWG tokenizer must preserve.
   Its 1-byte (Latin-1) and 4-byte (UTF-32) paths are exact, so on PyPy any result too wide for
   Latin-1 is built at 4-byte kind: th_str_maxchar widens the PyUnicode_New bin and th_str_from_kind
   widens the buffer. CPython keeps the narrowest kind instead, because its str equality compares
   kind before content and would report a too-wide str as unequal to its own value.

   encoding/decode.h calls PyUnicode_New directly and deliberately: every WHATWG decoder replaces an
   ill-formed sequence with U+FFFD and no legacy encoding maps a byte to a surrogate, so its 2-byte
   results hold no surrogate, and PyUnicode_New pins the byte order where FromKindAndData does not. */

#ifndef TURBOHTML_CORE_PYCOMPAT_H
#define TURBOHTML_CORE_PYCOMPAT_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#ifdef PYPY_VERSION

/* tp_new for a type CPython seals with Py_TPFLAGS_DISALLOW_INSTANTIATION. tp_name carries the
   spec's dotted name, so the message matches CPython's word for word. */
static inline PyObject *th_disallow_new(PyTypeObject *type, PyObject *Py_UNUSED(args), PyObject *Py_UNUSED(kwds)) {
    PyErr_Format(PyExc_TypeError, "cannot create '%s' instances", type->tp_name);
    return NULL;
}

/* how_many code points of `from` into `to` at `to_start`. The callers size `to` with a maxchar that
   covers `from`, so the widening write always fits and this cannot fail; CPython's function returns
   the count copied and the callers ignore it, so the fallback returns it too. */
static inline Py_ssize_t th_copy_characters(PyObject *to, Py_ssize_t to_start, PyObject *from, Py_ssize_t from_start,
                                            Py_ssize_t how_many) {
    int to_kind = PyUnicode_KIND(to);
    void *to_data = PyUnicode_DATA(to);
    int from_kind = PyUnicode_KIND(from);
    const void *from_data = PyUnicode_DATA(from);
    for (Py_ssize_t index = 0; index < how_many; index++) {
        PyUnicode_WRITE(to_kind, to_data, to_start + index, PyUnicode_READ(from_kind, from_data, from_start + index));
    }
    return how_many;
}

/* Canonicalize a cpyext PyUnicode_FromFormat[V] result. _PyUnicode_Ready transcodes into a fresh
   UCS buffer, so it can fail on allocation; free the string then, letting the caller's NULL check
   cover it. */
static inline PyObject *th_str_ready(PyObject *str) {
    if (str != NULL && PyUnicode_READY(str) < 0) {
        Py_CLEAR(str);
    }
    return str;
}

/* Widen a 2-byte buffer to 4-byte before handing it over; 1-byte and 4-byte pass straight through. */
static inline PyObject *th_str_from_kind(int kind, const void *data, Py_ssize_t size) {
    if (kind != PyUnicode_2BYTE_KIND || size == 0) {
        return PyUnicode_FromKindAndData(kind, data, size);
    }
    Py_UCS4 *wide = PyMem_Malloc((size_t)size * sizeof(Py_UCS4));
    if (wide == NULL) {
        return PyErr_NoMemory();
    }
    const Py_UCS2 *narrow = (const Py_UCS2 *)data;
    for (Py_ssize_t index = 0; index < size; index++) {
        wide[index] = narrow[index];
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, wide, size);
    PyMem_Free(wide);
    return result;
}

#define th_str_format(...) th_str_ready(PyUnicode_FromFormat(__VA_ARGS__))
#define th_str_format_v(format, args) th_str_ready(PyUnicode_FromFormatV((format), (args)))
#define th_str_maxchar(maxchar) ((maxchar) > 0xFF ? (Py_UCS4)0x10FFFF : (Py_UCS4)(maxchar))
#define TH_SEALED 0
#define TH_SEALED_END                                                                                                  \
    {Py_tp_new, th_disallow_new}, {                                                                                    \
        0, NULL                                                                                                        \
    }

#else

#define th_copy_characters PyUnicode_CopyCharacters
#define th_str_from_kind PyUnicode_FromKindAndData
#define th_str_format(...) PyUnicode_FromFormat(__VA_ARGS__)
#define th_str_format_v(format, args) PyUnicode_FromFormatV((format), (args))
#define th_str_maxchar(maxchar) (maxchar)
#define TH_SEALED Py_TPFLAGS_DISALLOW_INSTANTIATION
#define TH_SEALED_END {0, NULL}

#endif /* PYPY_VERSION */

#endif /* TURBOHTML_CORE_PYCOMPAT_H */
