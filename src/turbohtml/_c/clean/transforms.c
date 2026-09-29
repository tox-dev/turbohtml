#include "core/common.h"
#include "dom/ownership.h"
#include "tokenizer/binding.h"
#include "dom/observe.h"
#include "serialize/internal.h"

static int collapse_subtree(th_tree *tree, th_node *root);
static int preserve_whitespace(th_tree *tree, const th_node *node);
static int collapse_text(th_tree *tree, th_node *node, int *last_space);

PyObject *turbohtml_transform_node(PyObject *module, PyObject *const *args, Py_ssize_t count) {
    if (count == 0) {
        PyErr_SetString(PyExc_TypeError, "transform_node requires a node");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *root = args[0];
    if (!PyObject_TypeCheck(root, (PyTypeObject *)state->node_type)) {
        PyErr_SetString(PyExc_TypeError, "node must be a Node");
        return NULL;
    }
    root = Py_NewRef(root);
    for (Py_ssize_t index = 1; index < count; index++) {
        PyObject *result = PyObject_CallOneArg(args[index], root);
        if (result == NULL) {
            Py_DECREF(root);
            return NULL;
        }
        if (result == Py_None) {
            Py_DECREF(result);
        } else if (PyObject_TypeCheck(result, (PyTypeObject *)state->node_type)) {
            Py_SETREF(root, result);
        } else {
            PyErr_Format(PyExc_TypeError, "stage %zd returned %.200s; expected Node or None", index,
                         Py_TYPE(result)->tp_name);
            Py_DECREF(result);
            Py_DECREF(root);
            return NULL;
        }
    }
    return root;
}

TH_NODE_API(, PyObject *, turbohtml_collapse_whitespace_node, (PyObject * module, PyObject *owner), (module, owner),
            (PyObject * module, PyObject *owner),
            is_node(owner, PyModule_GetState(module)) ? (NodeObject *)owner : NULL, NULL) {
    th_tree *tree;
    th_node *root;
    if (turbohtml_node_borrow(module, owner, &tree, &root) < 0) {
        return NULL;
    }
    int status;
    Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(owner));
    if (tree->xml) {
        PyErr_SetString(PyExc_ValueError, "collapse_whitespace_node requires an HTML tree");
        status = -1;
    } else {
        status = collapse_subtree(tree, root);
    }
    Py_END_CRITICAL_SECTION();
    if (status < 0) {
        return NULL;
    }
    return Py_NewRef(owner);
}

TH_NODE_API(, PyObject *, turbohtml_strip_comments_node, (PyObject * module, PyObject *owner), (module, owner),
            (PyObject * module, PyObject *owner),
            is_node(owner, PyModule_GetState(module)) ? (NodeObject *)owner : NULL, NULL) {
    th_tree *tree;
    th_node *root;
    if (turbohtml_node_borrow(module, owner, &tree, &root) < 0) {
        return NULL;
    }
    Py_BEGIN_CRITICAL_SECTION(turbohtml_node_handle(owner));
    for (th_node *node = root->first_child; node != NULL;) {
        th_node *next = node;
        if (next->first_child != NULL) {
            next = next->first_child;
        } else {
            while (next != root && next->next_sibling == NULL) {
                next = next->parent;
            }
            next = next == root ? NULL : next->next_sibling;
        }
        if (node->type == TH_NODE_COMMENT) {
            th_node_remove_observed(tree, node);
        }
        node = next;
    }
    Py_END_CRITICAL_SECTION();
    return Py_NewRef(owner);
}

static int collapse_subtree(th_tree *tree, th_node *root) {
    for (th_node *ancestor = root->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (preserve_whitespace(tree, ancestor)) {
            return 0;
        }
    }
    int last_space = 0;
    th_node *node = root;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT) {
            if (collapse_text(tree, node, &last_space) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                    /* GCOVR_EXCL_LINE: allocation failure */
            }
        } else {
            last_space = 0;
        }
        if (!preserve_whitespace(tree, node) && node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
            last_space = 0;
        }
        node = node == root ? NULL : node->next_sibling;
    }
    return 0;
}

static int preserve_whitespace(th_tree *tree, const th_node *node) {
    return node->type == TH_NODE_ELEMENT &&
           (node->ns != TH_NS_HTML || is_rawtext_element(node, tree->scripting) || node->atom == TH_TAG_PRE ||
            node->atom == TH_TAG_TEXTAREA || node->atom == TH_TAG_LISTING || node->atom == TH_TAG_TITLE);
}

static int collapse_text(th_tree *tree, th_node *node, int *last_space) {
    int kind = PyUnicode_4BYTE_KIND;
    const void *data = node->text;
    if (text_is_span(node)) {
        kind = tree->kind;
        data = (const char *)tree->data + text_span_offset(node) * kind;
    }
    Py_ssize_t length = 0;
    int previous = *last_space;
    int changed = 0;
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        int space = is_space(character);
        length += !space || !previous;
        changed |= space && (previous || character != ' ');
        previous = space;
    }
    if (!changed) {
        *last_space = previous;
        return 0;
    }
    Py_UCS4 *output = NULL;
    if (length != 0) {
        output = arena_alloc(tree, length * (Py_ssize_t)sizeof(Py_UCS4));
        if (output == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
            return -1;        /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < node->text_len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        int space = is_space(character);
        if (!space || !*last_space) {
            output[written++] = space ? ' ' : character;
        }
        *last_space = space;
    }
    if (th_tree_has_observers(tree)) {
        const Py_UCS4 *old = node->text;
        if (text_is_span(node)) {
            old = copy_input_span(tree, text_span_offset(node), node->text_len);
            if (old == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
                return -1;        /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        th_mo_char_data_changed(tree, node, old, node->text_len);
    }
    node->text = output;
    node->text_len = length;
    return 0;
}
