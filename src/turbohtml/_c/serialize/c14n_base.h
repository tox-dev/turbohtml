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

/* The code points the joins of one fixup may read. Each ancestor's xml:base is joined onto everything joined so far and
   a join reads both of its operands, so without a bound nested elements that each add a short value cost the square of
   their depth, and ancestors that add nothing each read the whole value again. The budget leaves room for hundreds of
   nested relative addresses and holds one fixup to a few milliseconds. */
#define C14N_BASE_JOIN_BUDGET 1048576

/* Canonical XML 1.1 (section 2.4) joins the apex's xml:base onto those of its omitted ancestors. Returns -1 with an
   exception set: a join's ValueError, or one for a chain past C14N_BASE_JOIN_BUDGET, since a canonical form can neither
   drop the attribute nor write it unresolved. */
static int c14n_base_fixup(th_tree *tree, const th_node *apex, Py_UCS4 **value, Py_ssize_t *length) {
    const th_node_attr *own = c14n_base_attribute(tree, apex);
    Py_ssize_t budget = C14N_BASE_JOIN_BUDGET;
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
        Py_ssize_t operands = base->value_len + PyUnicode_GET_LENGTH(current);
        if (operands > budget) {
            Py_DECREF(current);
            PyErr_Format(PyExc_ValueError,
                         "joining the xml:base attributes above the subtree exceeds the limit of %d characters; "
                         "shorten them or nest fewer of them",
                         C14N_BASE_JOIN_BUDGET);
            return -1;
        }
        budget -= operands;
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
