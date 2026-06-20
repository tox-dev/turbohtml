/* Python glue for the tree builder.

   Increment 1 exposes only the internal conformance hook _parse_tree, which
   parses a document and returns its html5lib "#document" serialization so the
   tree-construction test suite can diff against the .dat expectations. The
   public, navigable Node tree type is a follow-up increment; keeping the engine
   (treebuilder.c) Python-free means it is tested in isolation first. */

#include "turbohtml.h"

#include "treebuilder.h"

PyObject *turbohtml_parse_tree(PyObject *Py_UNUSED(module), PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_parse_tree() argument must be str");
        return NULL;
    }
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);

    th_tree *tree = th_tree_parse(kind, data, length, 0);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len;
    Py_UCS4 *serialized = th_tree_serialize(tree, &out_len);
    if (serialized == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, serialized, out_len);
    PyMem_Free(serialized);
    th_tree_free(tree);
    return result;
}

PyObject *turbohtml_parse_only(PyObject *Py_UNUSED(module), PyObject *arg) {
    /* benchmark hook: build and free the C tree without serializing, so the
       measured work matches what lexbor's document parse does */
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "_parse_only() argument must be str");
        return NULL;
    }
    th_tree *tree = th_tree_parse(PyUnicode_KIND(arg), PyUnicode_DATA(arg), PyUnicode_GET_LENGTH(arg), 0);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree_free(tree);
    Py_RETURN_NONE;
}

PyObject *turbohtml_parse_fragment(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    const char *context;
    Py_ssize_t context_len;
    if (!PyArg_ParseTuple(args, "Us#:_parse_fragment", &text, &context, &context_len)) {
        return NULL;
    }
    th_tree *tree = th_tree_parse_fragment(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text),
                                           context, context_len, 0);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len;
    Py_UCS4 *serialized = th_tree_serialize(tree, &out_len);
    if (serialized == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, serialized, out_len);
    PyMem_Free(serialized);
    th_tree_free(tree);
    return result;
}
