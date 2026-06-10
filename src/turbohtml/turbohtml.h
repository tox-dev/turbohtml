/* Internal header shared by the turbohtml._html translation units.

   The module is split per feature for readability (escape.c, unescape.c) but
   compiled into a single _html extension. Each feature file implements one
   public entry point declared here; _htmlmodule.c wires them into the module. */

#ifndef TURBOHTML_H
#define TURBOHTML_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* Implemented in escape.c. Signature matches METH_VARARGS | METH_KEYWORDS. */
PyObject *turbohtml_escape(PyObject *module, PyObject *args, PyObject *kwds);

/* Implemented in unescape.c. Signature matches METH_O. */
PyObject *turbohtml_unescape(PyObject *module, PyObject *arg);

/* Implemented in tokenizer_type.c. tokenize() matches METH_O; the internal
   conformance hook _tokenize_states matches METH_VARARGS. */
PyObject *turbohtml_tokenize(PyObject *module, PyObject *arg);
PyObject *turbohtml_tokenize_states(PyObject *module, PyObject *args);

#endif /* TURBOHTML_H */
