/* Where CPython's C API and PyPy's cpyext differ, for the calls this core depends on.

   Most of the surface needs no adaptation. cpyext emulates the whole PEP 393 layout --
   PyASCIIObject.state.kind, the compact/non-compact data pointer, and with them PyUnicode_KIND /
   PyUnicode_DATA / PyUnicode_READ / PyUnicode_WRITE -- by transcoding a str's UTF-8 storage into a
   UCS1/2/4 buffer the first time it crosses into C and caching that buffer on the PyObject. So the
   SWAR scanners are correct there; they pay a one-time O(len) materialization per string, which a
   whole-document parse amortizes. The heap types, module state, multi-phase init, and GC slots this
   module is built on are all supported, and Py_BEGIN_CRITICAL_SECTION already collapses to a brace
   no-op through tokenizer/binding.h's `#ifndef` -- the same shim CPython 3.10-3.12 take, and
   correct for the same reason: cpyext holds the GIL across every call into this extension.

   Three things differ.

   PyUnicode_CopyCharacters does not exist in cpyext at all, so it gets the READ/WRITE loop below.

   PyUnicode_FromFormat is the one constructor whose cpyext result is not in canonical form: it is
   built through the legacy wstr representation and returned without _PyUnicode_Ready, so
   PyUnicode_KIND, PyUnicode_DATA, and PyUnicode_GET_LENGTH are all undefined on it -- and with
   NDEBUG their asserts are gone, so GET_LENGTH silently returns the wstr length, one past the code
   point count. Every other constructor this core uses (FromString, FromStringAndSize,
   FromKindAndData, New, Concat, Substring, FromOrdinal, DecodeUTF8, InternFromString, Join, Replace,
   Py_BuildValue) returns a ready string. Call th_str_format, never PyUnicode_FromFormat directly.

   A 2-byte buffer cannot be handed to cpyext at all. It realizes one by decoding it as UTF-16, so a
   leading U+FEFF is eaten as a byte-order mark, a leading U+FFFE byte-swaps the rest, a surrogate
   pair folds into the one code point it encodes, and a lone surrogate -- which a CPython str carries
   fine, and which HTML input and the WHATWG tokenizer must both preserve -- aborts the interpreter
   outright through the strict error handler. Its 1-byte (Latin-1) and 4-byte (UTF-32, surrogates
   allowed) paths are exact. So on PyPy any result too wide for Latin-1 is built at 4-byte kind:
   th_str_maxchar widens the PyUnicode_New bin and th_str_from_kind widens the buffer. CPython must
   keep the narrowest kind instead, because its str equality compares kind before content and would
   report a too-wide str as unequal to its own value, so both are identities there.

   encoding/decode.h builds its results with PyUnicode_New directly and deliberately: every WHATWG
   decoder replaces an ill-formed sequence with U+FFFD and no legacy encoding maps a byte to a
   surrogate, so its 2-byte results hold no surrogate, and PyUnicode_New (unlike FromKindAndData)
   pins the byte order, leaving U+FEFF and U+FFFE intact.

   Each name is a macro expanding to the CPython spelling on CPython, so a CPython translation unit's
   preprocessed token stream is unchanged by this file and its codegen cannot shift. */

#ifndef TURBOHTML_CORE_PYCOMPAT_H
#define TURBOHTML_CORE_PYCOMPAT_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#ifdef PYPY_VERSION

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

/* Canonicalize a cpyext PyUnicode_FromFormat[V] result, freeing it on the one failure
   _PyUnicode_Ready reports (a code point outside U+0000..U+10FFFF, which no format string here can
   produce) so the caller's existing NULL check covers this too. */
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

#else

#define th_copy_characters PyUnicode_CopyCharacters
#define th_str_from_kind PyUnicode_FromKindAndData
#define th_str_format(...) PyUnicode_FromFormat(__VA_ARGS__)
#define th_str_format_v(format, args) PyUnicode_FromFormatV((format), (args))
#define th_str_maxchar(maxchar) (maxchar)

#endif /* PYPY_VERSION */

#endif /* TURBOHTML_CORE_PYCOMPAT_H */
