/* The internal parse hooks behind turbohtml's own test and benchmark harness:
   _parse_tree returns the html5lib "#document" dump the conformance suite diffs against, _parse_fragment does the same
   for fragment parsing, and _parse_only builds and frees a tree to time the raw parse against lexbor. */

#include "core/common.h"

#include "dom/tree.h"

PyObject *turbohtml_parse_tree(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *arg;
    int scripting = 0;
    if (!PyArg_ParseTuple(args, "U|p:_parse_tree", &arg, &scripting)) {
        return NULL;
    }
    int kind = PyUnicode_KIND(arg);
    const void *data = PyUnicode_DATA(arg);
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);

    th_tree *tree = th_tree_parse(kind, data, length, 0, scripting);
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
    th_tree *tree = th_tree_parse(PyUnicode_KIND(arg), PyUnicode_DATA(arg), PyUnicode_GET_LENGTH(arg), 0, 0);
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
    int scripting = 0;
    if (!PyArg_ParseTuple(args, "Us#|p:_parse_fragment", &text, &context, &context_len, &scripting)) {
        return NULL;
    }
    th_tree *tree = th_tree_parse_fragment(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text),
                                           context, context_len, 0, scripting);
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
