/* The Query facade's primitives: the set algebra and the joins behind turbohtml.query.Query.

   Query is a pyquery-shaped wrapper over an ordered, duplicate-free element set. Deciding what is in that set --
   deduplicating by node identity, collecting siblings, reading and writing the tokenized class attribute -- and
   joining the text or an attribute of the whole set are what the facade computes, so they run here rather than in
   the typed layer, which only forwards a list and wraps the answer. Every entry point takes the wrapper objects the
   Python layer holds and borrows each one's node, so the traversal walks the arena rather than the object graph. */

#include "core/common.h"
#include "dom/nodes.h"
#include "dom/tree.h"

#include <string.h>

/* Borrow the node behind one Element wrapper, rejecting anything else. */
static th_node *facade_node(PyObject *module, PyObject *element) {
    th_tree *tree;
    th_node *node;
    if (turbohtml_node_borrow(module, element, &tree, &node) < 0) {
        return NULL;
    }
    return node;
}

/* Append `element` to `out` when its node has not been seen, recording it in `seen`. A fresh wrapper is handed out on
   each tree access, so two wrappers of one node are distinct objects; the node address is the identity. */
static int facade_keep_new(PyObject *out, PyObject *seen, PyObject *element, th_node *node) {
    PyObject *address = PyLong_FromVoidPtr(node);
    if (address == NULL) { /* GCOVR_EXCL_BR_LINE: integer allocation cannot be forced to fail */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t before = PySet_GET_SIZE(seen);
    int added = PySet_Add(seen, address);
    Py_DECREF(address);
    /* GCOVR_EXCL_BR_START: set insertion only fails on allocation failure */
    if (added < 0) {
        return -1; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (PySet_GET_SIZE(seen) == before) {
        return 0; /* a duplicate of an element already in the set */
    }
    return PyList_Append(out, element); /* GCOVR_EXCL_BR_LINE: list append only fails on allocation failure */
}

/* _query_unique(module, elements) -> list[Element]: the elements in order, dropping any later duplicate. */
PyObject *turbohtml_query_unique(PyObject *module, PyObject *elements) {
    PyObject *iterator = PyObject_GetIter(elements);
    if (iterator == NULL) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    PyObject *seen = PySet_New(NULL);
    /* GCOVR_EXCL_BR_START: list and set allocation cannot be forced to fail */
    if (out == NULL || seen == NULL) {
        Py_XDECREF(out);     /* GCOVR_EXCL_LINE */
        Py_XDECREF(seen);    /* GCOVR_EXCL_LINE */
        Py_DECREF(iterator); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    PyObject *element;
    int status = 0;
    while (status == 0 && (element = PyIter_Next(iterator)) != NULL) {
        th_node *node = facade_node(module, element);
        status = node == NULL ? -1 : facade_keep_new(out, seen, element, node);
        Py_DECREF(element);
    }
    Py_DECREF(iterator);
    Py_DECREF(seen);
    if (status < 0 || PyErr_Occurred() != NULL) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* _query_siblings(module, nodes) -> list[Element]: every element sibling of every wrapped element, deduplicated. */
PyObject *turbohtml_query_siblings(PyObject *module, PyObject *args) {
    PyObject *nodes;
    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &nodes)) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    PyObject *seen = PySet_New(NULL);
    /* GCOVR_EXCL_BR_START: list and set allocation cannot be forced to fail */
    if (out == NULL || seen == NULL) {
        Py_XDECREF(out);  /* GCOVR_EXCL_LINE */
        Py_XDECREF(seen); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int status = 0;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *owner = PyList_GET_ITEM(nodes, index);
        th_node *node = facade_node(module, owner);
        if (node == NULL) {
            status = -1;
            break;
        }
        /* the document root has no parent, and a node whose parent is not an element has no element siblings */
        if (node->parent == NULL || node->parent->type != TH_NODE_ELEMENT) {
            continue;
        }
        for (th_node *sibling = node->parent->first_child; sibling != NULL; sibling = sibling->next_sibling) {
            if (sibling->type != TH_NODE_ELEMENT || sibling == node) {
                continue;
            }
            PyObject *wrapper = turbohtml_node_wrap_in(owner, sibling);
            /* GCOVR_EXCL_BR_START: wrapper allocation cannot be forced to fail */
            if (wrapper == NULL) {
                status = -1; /* GCOVR_EXCL_LINE */
                break;       /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            status = facade_keep_new(out, seen, wrapper, sibling);
            Py_DECREF(wrapper);
            if (status < 0) { /* GCOVR_EXCL_BR_LINE: the append only fails on allocation failure */
                break;        /* GCOVR_EXCL_LINE */
            }
        }
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: the append only fails on allocation failure */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(seen);
    if (status < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* _query_parents(module, nodes) -> list[Element]: the element parent of every wrapped element, deduplicated,
   since siblings share one. */
PyObject *turbohtml_query_parents(PyObject *module, PyObject *args) {
    PyObject *nodes;
    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &nodes)) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    PyObject *seen = PySet_New(NULL);
    /* GCOVR_EXCL_BR_START: list and set allocation cannot be forced to fail */
    if (out == NULL || seen == NULL) {
        Py_XDECREF(out);  /* GCOVR_EXCL_LINE */
        Py_XDECREF(seen); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int status = 0;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *owner = PyList_GET_ITEM(nodes, index);
        th_node *node = facade_node(module, owner);
        if (node == NULL) {
            status = -1;
            break;
        }
        th_node *parent = node->parent;
        if (parent == NULL || parent->type != TH_NODE_ELEMENT) {
            continue; /* the document root, or a fragment's top-level element */
        }
        PyObject *wrapper = turbohtml_node_wrap_in(owner, parent);
        if (wrapper == NULL) { /* GCOVR_EXCL_BR_LINE: wrapper allocation cannot be forced to fail */
            status = -1;       /* GCOVR_EXCL_LINE */
            break;             /* GCOVR_EXCL_LINE */
        }
        status = facade_keep_new(out, seen, wrapper, parent);
        Py_DECREF(wrapper);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: the append only fails on allocation failure */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(seen);
    if (status < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* _query_children(module, nodes) -> list[Element]: the element children of every wrapped node, in order. A set of
   distinct parents has distinct children, so nothing is deduplicated. */
PyObject *turbohtml_query_children(PyObject *module, PyObject *args) {
    PyObject *nodes;
    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &nodes)) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced to fail */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *owner = PyList_GET_ITEM(nodes, index);
        th_node *node = facade_node(module, owner);
        if (node == NULL) {
            Py_DECREF(out);
            return NULL;
        }
        for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type != TH_NODE_ELEMENT) {
                continue;
            }
            PyObject *wrapper = turbohtml_node_wrap_in(owner, child);
            if (wrapper == NULL) { /* GCOVR_EXCL_BR_LINE: wrapper allocation cannot be forced to fail */
                Py_DECREF(out);    /* GCOVR_EXCL_LINE */
                return NULL;       /* GCOVR_EXCL_LINE */
            }
            int appended = PyList_Append(out, wrapper);
            Py_DECREF(wrapper);
            if (appended < 0) { /* GCOVR_EXCL_BR_LINE: the append only fails on allocation failure */
                Py_DECREF(out); /* GCOVR_EXCL_LINE */
                return NULL;    /* GCOVR_EXCL_LINE */
            }
        }
    }
    return out;
}

/* _query_closest(module, nodes, selector) -> list[Element]: the nearest self-or-ancestor of every wrapped element
   that matches the selector, deduplicated, since siblings share one. */
PyObject *turbohtml_query_closest(PyObject *module, PyObject *args) {
    PyObject *nodes;
    PyObject *selector;
    if (!PyArg_ParseTuple(args, "O!U", &PyList_Type, &nodes, &selector)) {
        return NULL;
    }
    PyObject *out = PyList_New(0);
    PyObject *seen = PySet_New(NULL);
    /* GCOVR_EXCL_BR_START: list and set allocation cannot be forced to fail */
    if (out == NULL || seen == NULL) {
        Py_XDECREF(out);  /* GCOVR_EXCL_LINE */
        Py_XDECREF(seen); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int status = 0;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *owner = PyList_GET_ITEM(nodes, index);
        if (facade_node(module, owner) == NULL) {
            status = -1;
            break;
        }
        PyObject *found = node_css_closest(owner, selector);
        if (found == NULL) {
            status = -1; /* an invalid selector */
            break;
        }
        if (found != Py_None) {
            status = facade_keep_new(out, seen, found, facade_node(module, found));
        }
        Py_DECREF(found);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: the append only fails on allocation failure */
            break;        /* GCOVR_EXCL_LINE */
        }
    }
    Py_DECREF(seen);
    if (status < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* Join `parts` with a single space. */
static PyObject *facade_join(PyObject *parts) {
    PyObject *space = PyUnicode_FromString(" ");
    /* GCOVR_EXCL_BR_START: string allocation cannot be forced to fail */
    if (space == NULL) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    PyObject *joined = PyUnicode_Join(space, parts);
    Py_DECREF(space);
    return joined;
}

/* _query_text(module, nodes) -> str: the wrapped elements' text, joined with a space. */
PyObject *turbohtml_query_text(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *nodes;
    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &nodes)) {
        return NULL;
    }
    PyObject *parts = PyList_New(0);
    /* GCOVR_EXCL_BR_START: list allocation cannot be forced to fail */
    if (parts == NULL) {
        return NULL; /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *text = PyObject_GetAttrString(PyList_GET_ITEM(nodes, index), "text");
        /* GCOVR_EXCL_BR_START: the text property and the append only fail on allocation failure */
        int status = text == NULL ? -1 : PyList_Append(parts, text);
        Py_XDECREF(text);
        if (status < 0) {
            Py_DECREF(parts); /* GCOVR_EXCL_LINE */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        /* GCOVR_EXCL_BR_STOP */
    }
    PyObject *joined = facade_join(parts);
    Py_DECREF(parts);
    return joined;
}

/* The `attrs` mapping of one wrapper. */
static PyObject *facade_attrs(PyObject *element) {
    return PyObject_GetAttrString(element, "attrs");
}

/* _query_attr(module, nodes, name) -> str | None: the first element's attribute, a tokenized one joined with a
   space, or None when the set is empty or the attribute is absent. */
PyObject *turbohtml_query_attr(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *nodes;
    PyObject *name;
    if (!PyArg_ParseTuple(args, "O!U", &PyList_Type, &nodes, &name)) {
        return NULL;
    }
    if (PyList_GET_SIZE(nodes) == 0) {
        Py_RETURN_NONE;
    }
    PyObject *attrs = facade_attrs(PyList_GET_ITEM(nodes, 0));
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: the attrs property cannot fail for an element */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *value = PyObject_GetItem(attrs, name);
    Py_DECREF(attrs);
    if (value == NULL) {
        PyErr_Clear(); /* absent, which reads as None rather than an error */
        Py_RETURN_NONE;
    }
    if (!PyList_Check(value)) {
        return value; /* a plain attribute is its own answer */
    }
    PyObject *joined = facade_join(value); /* class and the other tokenized attributes read back space-joined */
    Py_DECREF(value);
    return joined;
}

/* The `class` attribute as a fresh list of its tokens; the tree stores it tokenized or omits it entirely. */
static PyObject *facade_class_list(PyObject *element) {
    PyObject *attrs = facade_attrs(element);
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: the attrs property cannot fail for an element */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *key = PyUnicode_FromString("class");
    PyObject *value = key == NULL ? NULL : PyObject_GetItem(attrs, key); /* GCOVR_EXCL_BR_LINE: allocation */
    Py_XDECREF(key);
    Py_DECREF(attrs);
    if (value == NULL) {
        PyErr_Clear(); /* no class attribute, which is an empty token list rather than an error */
        return PyList_New(0);
    }
    /* the tree stores class tokenized whatever a caller assigns, so the value is always a list here */
    PyObject *copy = PyList_GetSlice(value, 0, PyList_GET_SIZE(value));
    Py_DECREF(value);
    return copy;
}

/* Store `classes` as the element's class attribute. */
static int facade_set_classes(PyObject *element, PyObject *classes) {
    PyObject *attrs = facade_attrs(element);
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: the attrs property cannot fail for an element */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    PyObject *key = PyUnicode_FromString("class");
    int status = key == NULL ? -1 : PyObject_SetItem(attrs, key, classes); /* GCOVR_EXCL_BR_LINE: allocation */
    Py_XDECREF(key);
    Py_DECREF(attrs);
    return status; /* GCOVR_EXCL_BR_LINE: the assignment only fails on allocation failure */
}

/* _query_has_class(module, nodes, name) -> bool: does any wrapped element carry the class? */
PyObject *turbohtml_query_has_class(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *nodes;
    PyObject *name;
    if (!PyArg_ParseTuple(args, "O!U", &PyList_Type, &nodes, &name)) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *classes = facade_class_list(PyList_GET_ITEM(nodes, index));
        if (classes == NULL) { /* GCOVR_EXCL_BR_LINE: the class read cannot fail for an element */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
        int held = PySequence_Contains(classes, name);
        Py_DECREF(classes);
        if (held != 0) { /* GCOVR_EXCL_BR_LINE: string membership cannot fail */
            return Py_NewRef(Py_True);
        }
    }
    Py_RETURN_FALSE;
}

enum facade_class_edit { FACADE_CLASS_ADD, FACADE_CLASS_REMOVE, FACADE_CLASS_TOGGLE };

/* Apply one class edit across the wrapped elements; the caller answers with its own query object. */
static PyObject *facade_edit_classes(PyObject *args, enum facade_class_edit edit) {
    PyObject *nodes;
    PyObject *name;
    if (!PyArg_ParseTuple(args, "O!U", &PyList_Type, &nodes, &name)) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(nodes); index++) {
        PyObject *element = PyList_GET_ITEM(nodes, index);
        PyObject *classes = facade_class_list(element);
        if (classes == NULL) { /* GCOVR_EXCL_BR_LINE: the class read cannot fail for an element */
            return NULL;       /* GCOVR_EXCL_LINE */
        }
        int held = PySequence_Contains(classes, name);
        int status = 0;
        if (held < 0) {  /* GCOVR_EXCL_BR_LINE: string membership cannot fail */
            status = -1; /* GCOVR_EXCL_LINE */
        } else if (held == 0 && edit != FACADE_CLASS_REMOVE) {
            /* absent: add and toggle both put it on, and only they write when nothing changes for remove */
            /* GCOVR_EXCL_BR_START: the append only fails on allocation failure */
            status = PyList_Append(classes, name);
            status = status < 0 ? -1 : facade_set_classes(element, classes);
            /* GCOVR_EXCL_BR_STOP */
        } else if (held == 1 && edit != FACADE_CLASS_ADD) {
            /* present: remove and toggle both take it off, keeping every other token in order */
            PyObject *kept = PyList_New(0);
            status = kept == NULL ? -1 : 0; /* GCOVR_EXCL_BR_LINE: list allocation cannot be forced to fail */
            for (Py_ssize_t at = 0; at < PyList_GET_SIZE(classes); at++) {
                PyObject *token = PyList_GET_ITEM(classes, at);
                /* GCOVR_EXCL_BR_START: the append only fails on allocation failure */
                status = PyUnicode_Compare(token, name) == 0 ? status : PyList_Append(kept, token);
                /* GCOVR_EXCL_BR_STOP */
            }
            status = status < 0 ? -1 : facade_set_classes(element, kept); /* GCOVR_EXCL_BR_LINE: allocation */
            Py_XDECREF(kept);
        }
        Py_DECREF(classes);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: every failure above is an allocation failure */
            return NULL;  /* GCOVR_EXCL_LINE */
        }
    }
    Py_RETURN_NONE;
}

/* _query_add_class(module, nodes, name) -> None */
PyObject *turbohtml_query_add_class(PyObject *Py_UNUSED(module), PyObject *args) {
    return facade_edit_classes(args, FACADE_CLASS_ADD);
}

/* _query_remove_class(module, nodes, name) -> None */
PyObject *turbohtml_query_remove_class(PyObject *Py_UNUSED(module), PyObject *args) {
    return facade_edit_classes(args, FACADE_CLASS_REMOVE);
}

/* _query_toggle_class(module, nodes, name) -> None */
PyObject *turbohtml_query_toggle_class(PyObject *Py_UNUSED(module), PyObject *args) {
    return facade_edit_classes(args, FACADE_CLASS_TOGGLE);
}
