/* Internal header shared by the turbohtml._html translation units.

   The module is split per feature for readability (escape.c, unescape.c) but
   compiled into a single _html extension. Each feature file implements one
   public entry point declared here; _htmlmodule.c wires them into the module. */

#ifndef TURBOHTML_H
#define TURBOHTML_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdint.h>

/* Implemented in escape.c. Signature matches METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_escape(PyObject *module, PyObject *args, PyObject *kwds);

/* Implemented in unescape.c. Signature matches METH_O. */
PyObject *turbohtml_unescape(PyObject *module, PyObject *arg);

/* Implemented in markup.c, the markupsafe-compatible escape surface. escape,
   escape_silent, soft_str, and _register_markup all match METH_O. */
PyObject *turbohtml_markup_escape(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_escape_silent(PyObject *module, PyObject *s);
PyObject *turbohtml_markup_soft_str(PyObject *module, PyObject *s);
PyObject *turbohtml_register_markup(PyObject *module, PyObject *type);

/* Implemented in linkify.c. _linkify_scan finds URL/email spans in a text run;
   signature matches METH_VARARGS. */
PyObject *turbohtml_linkify_scan(PyObject *module, PyObject *args);

/* Implemented in tokenizer_type.c. tokenize() matches METH_O; the internal
   conformance hook _tokenize_states matches METH_VARARGS. */
PyObject *turbohtml_tokenize(PyObject *module, PyObject *arg);
PyObject *turbohtml_tokenize_states(PyObject *module, PyObject *args);

/* Implemented in treebuilder_py.c. The internal conformance hooks _parse_tree
   and _parse_fragment return the html5lib "#document" serialization of a parsed
   document / innerHTML fragment. */
PyObject *turbohtml_parse_tree(PyObject *module, PyObject *arg);
PyObject *turbohtml_parse_fragment(PyObject *module, PyObject *args);
PyObject *turbohtml_parse_only(PyObject *module, PyObject *arg);

/* SWAR lane probes over a 64-bit word holding four UCS-2 / two UCS-4 code
   points. The has-zero test is exact as an existence test: the subtraction can
   only borrow across a lane when that lane itself is zero, so the mask is
   nonzero iff some lane equals the searched value. Lane positions inside the
   mask depend on byte order, so callers treat a nonzero mask as "somewhere in
   these lanes" and resolve the exact index with a scalar scan. */

#define UCS2_LANES 4
#define UCS4_LANES 2

static inline uint64_t swar_haslane16(uint64_t word, uint16_t value) {
    uint64_t lanes = word ^ (0x0001000100010001ULL * value);
    return (lanes - 0x0001000100010001ULL) & ~lanes & 0x8000800080008000ULL;
}

static inline uint64_t swar_haslane32(uint64_t word, uint32_t value) {
    uint64_t lanes = word ^ (0x0000000100000001ULL * value);
    return (lanes - 0x0000000100000001ULL) & ~lanes & 0x8000000080000000ULL;
}

#endif /* TURBOHTML_H */
