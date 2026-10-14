#ifndef TURBOHTML_C14N_BASE_H
#define TURBOHTML_C14N_BASE_H

#include "serialize/internal.h"

static const th_node_attr *c14n_base_attribute(th_tree *tree, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t length;
        const char *name = th_attr_name(tree, node->attrs[index].name_atom, &length);
        if (length == 8 && memcmp(name, "xml:base", 8) == 0) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

static int c14n_base_fixup(th_tree *tree, const th_node *apex, Py_UCS4 **value, Py_ssize_t *length) {
    const th_node_attr *own = c14n_base_attribute(tree, apex);
    PyObject *current = own == NULL ? PyUnicode_FromString("")
                                    : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, own->value, own->value_len);
    if (current == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (const th_node *ancestor = apex->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        const th_node_attr *base = c14n_base_attribute(tree, ancestor);
        if (base == NULL) {
            continue;
        }
        PyObject *joined = th_url_join_c14n_raw(base->value, base->value_len, current);
        Py_DECREF(current);
        if (joined == NULL) {
            return -1;
        }
        current = joined;
    }
    *length = PyUnicode_GET_LENGTH(current);
    *value = PyUnicode_AsUCS4Copy(current);
    Py_DECREF(current);
    if (*value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

#endif
