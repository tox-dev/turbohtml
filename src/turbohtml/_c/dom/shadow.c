/* Shadow DOM tree model: attach_shadow / ShadowRoot, the host<->root linkage, <slot>
   assignment (named + default slots, assigned_nodes / assigned_elements /
   assigned_slot), and the flattened-tree traversal.

   A shadow root is a document-fragment-like TH_NODE_CONTENT node held off the light
   tree -- it is never a child of any node, so the light DOM's walks and serialization
   never reach it. The per-tree shadow table (tree_internal.h) is the only path between
   a host element and its shadow root and back. The slot-assignment algorithms follow
   the DOM Living Standard (find a slot, find slotables, find flattened slotables); they
   run on demand rather than caching an assignment, so a later light-DOM edit is always
   reflected. Every algorithm here is pure C over th_node; the bindings hold the host's
   per-tree critical section and wrap the resulting node arrays. */

#include "dom/nodes.h"

#include "core/vec.h" /* th_grow_cap overflow-safe growth */

/* A grow-on-demand array of node pointers the assignment/flatten walks accumulate
   into, wrapped into a Python list by the binding once the walk finishes. failed is
   set on an allocation failure so the caller reports it after freeing the buffer. */
typedef struct {
    th_node **items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} nodevec;

static void nodevec_push(nodevec *vec, th_node *node) {
    if (vec->failed) { /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (vec->len == vec->cap) {
        size_t cap, bytes;
        /* the requested length cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((size_t)vec->len + 1, (size_t)vec->cap, 8, sizeof(th_node *), &cap, &bytes);
        if (!fits) {         /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: overflow-guard path */
            return;          /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        th_node **items = PyMem_Realloc(vec->items, bytes);
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        vec->items = items;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++] = node;
}

/* The topmost ancestor of node (its root): a shadow root for a shadow-tree node, the
   document for a light-DOM node, or the node itself when it is detached. */
static th_node *node_root(th_node *node) {
    while (node->parent != NULL) {
        node = node->parent;
    }
    return node;
}

/* Whether node is an HTML <slot> element (the shadow tree's insertion point). */
static int is_slot(const th_node *node) {
    return node->type == TH_NODE_ELEMENT && node->ns == TH_NS_HTML && node->atom == TH_TAG_SLOT;
}

/* Whether node can be assigned to a slot: an element or a text node (DOM slotable). */
static int is_slottable(const th_node *node) {
    return node->type == TH_NODE_ELEMENT || node->type == TH_NODE_TEXT;
}

/* The value run of an element's named attribute, or the empty run when the attribute
   is absent or valueless (both are the empty name for slot matching). */
static void named_value(th_node *node, uint32_t atom, const Py_UCS4 **value, Py_ssize_t *len) {
    const th_node_attr *attr = find_node_attr(node, atom);
    if (attr != NULL && attr->value != NULL) {
        *value = attr->value;
        *len = attr->value_len;
    } else {
        *value = NULL;
        *len = 0;
    }
}

/* Whether two code-point runs are byte-for-byte equal (slot names match case-sensitively). */
static int runs_equal(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    if (left_len != right_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < left_len; index++) {
        if (left[index] != right[index]) {
            return 0;
        }
    }
    return 1;
}

/* Find the slot a slotable is assigned to: the first <slot> in the host's shadow tree
   (in tree order) whose name matches the slotable's slot name. NULL when the slotable's
   parent is not a shadow host, or -- with open_flag -- when that host's shadow is
   closed, or when no slot name matches. (DOM: find a slot.) */
static th_node *find_slot(th_tree *tree, th_node *slottable, int open_flag) {
    th_node *parent = slottable->parent;
    if (parent == NULL) {
        return NULL;
    }
    th_node *shadow = th_element_shadow_root(tree, parent);
    if (shadow == NULL) {
        return NULL;
    }
    if (open_flag && th_shadow_mode(shadow) != 0) {
        return NULL;
    }
    const Py_UCS4 *want = NULL;
    Py_ssize_t want_len = 0;
    if (slottable->type == TH_NODE_ELEMENT) {
        named_value(slottable, TH_ATTR_SLOT, &want, &want_len);
    }
    for (th_node *node = shadow->first_child; node != NULL; node = preorder_next(node, shadow)) {
        if (is_slot(node)) {
            const Py_UCS4 *name = NULL;
            Py_ssize_t name_len = 0;
            named_value(node, TH_ATTR_NAME, &name, &name_len);
            if (runs_equal(name, name_len, want, want_len)) {
                return node;
            }
        }
    }
    return NULL;
}

/* Collect the slotables assigned to slot: the host's direct children whose assigned
   slot is this one, in order. A slot outside a shadow tree has none. (DOM: find slotables.) */
static void collect_slotables(th_tree *tree, th_node *slot, nodevec *vec) {
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    th_node *host = th_shadow_host(tree, root);
    for (th_node *child = host->first_child; child != NULL; child = child->next_sibling) {
        if (is_slottable(child) && find_slot(tree, child, 0) == slot) {
            nodevec_push(vec, child);
        }
    }
}

/* Collect the flattened slotables of slot: its assigned slotables, or -- when it has
   none -- its own slotable children as fallback, with any nested shadow slot expanded
   recursively. (DOM: find flattened slotables.) */
static void collect_flattened(th_tree *tree, th_node *slot, nodevec *vec) {
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    nodevec assigned = {0};
    collect_slotables(tree, slot, &assigned);
    if (assigned.failed) {          /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        vec->failed = 1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(assigned.items); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (assigned.len == 0) {
        for (th_node *child = slot->first_child; child != NULL; child = child->next_sibling) {
            if (is_slottable(child)) {
                nodevec_push(&assigned, child);
            }
        }
    }
    for (Py_ssize_t index = 0; index < assigned.len; index++) {
        th_node *node = assigned.items[index];
        if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
            collect_flattened(tree, node, vec);
        } else {
            nodevec_push(vec, node);
        }
    }
    PyMem_Free(assigned.items);
}

/* Collect node's flattened-tree children: a shadow host descends into its shadow tree,
   a shadow slot yields its flattened slotables, and any child slot is replaced by its
   flattened slotables. Every other child passes through unchanged. */
static void collect_flattened_children(th_tree *tree, th_node *node, nodevec *vec) {
    if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
        collect_flattened(tree, node, vec);
        return;
    }
    th_node *shadow = th_element_shadow_root(tree, node);
    th_node *base = shadow != NULL ? shadow : node;
    for (th_node *child = base->first_child; child != NULL; child = child->next_sibling) {
        if (is_slot(child) && th_node_is_shadow_root(node_root(child))) {
            collect_flattened(tree, child, vec);
        } else {
            nodevec_push(vec, child);
        }
    }
}

/* Wrap a collected node array into a Python list, filtering to elements when
   elements_only is set, and free the array. NULL with an exception set on failure. */
static PyObject *nodevec_to_list(nodevec *vec, module_state *state, PyObject *handle, int elements_only) {
    if (vec->failed) {           /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        PyMem_Free(vec->items);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *list = PyList_New(0);
    if (list == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(vec->items); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < vec->len; index++) {
        if (elements_only && vec->items[index]->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (append_wrapped(list, state, handle, vec->items[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc failure */
            Py_DECREF(list);                                              /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(vec->items);                                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    PyMem_Free(vec->items);
    return list;
}

PyObject *element_attach_shadow(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"mode", NULL};
    PyObject *mode_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|U:attach_shadow", keywords, &mode_obj)) {
        return NULL;
    }
    int mode = 0;
    if (mode_obj != NULL) {
        if (PyUnicode_CompareWithASCIIString(mode_obj, "open") == 0) {
            mode = 0;
        } else if (PyUnicode_CompareWithASCIIString(mode_obj, "closed") == 0) {
            mode = 1;
        } else {
            PyErr_SetString(PyExc_ValueError, "mode must be 'open' or 'closed'");
            return NULL;
        }
    }
    NodeObject *host = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    PyObject *result = NULL;
    int already = 0;
    Py_BEGIN_CRITICAL_SECTION(host->handle);
    if (th_element_shadow_root(tree, host->node) != NULL) {
        already = 1;
    } else {
        th_node *root = th_element_attach_shadow(tree, host->node, mode);
        if (root != NULL) { /* GCOVR_EXCL_BR_LINE: attach only fails on an unforceable allocation */
            result = node_wrap(state_of(self), host->handle, root);
        }
    }
    Py_END_CRITICAL_SECTION();
    if (already) {
        PyErr_SetString(PyExc_ValueError, "element already has a shadow root");
        return NULL;
    }
    if (result == NULL) {        /* GCOVR_EXCL_BR_LINE: attach and wrap only fail on an unforceable allocation */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return result;
}

PyObject *element_get_shadow_root(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *root;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    root = th_element_shadow_root(tree, node->node);
    Py_END_CRITICAL_SECTION();
    if (root == NULL || th_shadow_mode(root) != 0) {
        Py_RETURN_NONE;
    }
    return node_wrap(state_of(self), node->handle, root);
}

/* Shared body of assigned_nodes / assigned_elements: reject a non-slot, then collect
   the direct (or, with flatten, flattened) assignment and wrap it. */
static PyObject *slot_assigned(PyObject *self, PyObject *args, PyObject *kwds, int elements_only) {
    static char *keywords[] = {"flatten", NULL};
    int flatten = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|p:assigned_nodes", keywords, &flatten)) {
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    if (!is_slot(node->node)) {
        PyErr_SetString(PyExc_TypeError, "assigned_nodes is only valid on a <slot> element");
        return NULL;
    }
    th_tree *tree = tree_of(self);
    nodevec vec = {0};
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    if (flatten) {
        collect_flattened(tree, node->node, &vec);
    } else {
        collect_slotables(tree, node->node, &vec);
    }
    Py_END_CRITICAL_SECTION();
    return nodevec_to_list(&vec, state_of(self), node->handle, elements_only);
}

PyObject *element_assigned_nodes(PyObject *self, PyObject *args, PyObject *kwds) {
    return slot_assigned(self, args, kwds, 0);
}

PyObject *element_assigned_elements(PyObject *self, PyObject *args, PyObject *kwds) {
    return slot_assigned(self, args, kwds, 1);
}

PyObject *node_get_assigned_slot(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *slot = NULL;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    if (is_slottable(node->node)) {
        slot = find_slot(tree, node->node, 1);
    }
    Py_END_CRITICAL_SECTION();
    return node_wrap(state_of(self), node->handle, slot);
}

PyObject *node_get_flattened_children(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    nodevec vec = {0};
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    collect_flattened_children(tree, node->node, &vec);
    Py_END_CRITICAL_SECTION();
    return nodevec_to_list(&vec, state_of(self), node->handle, 0);
}

PyDoc_STRVAR(shadow_root_mode_doc, "the shadow root's mode: 'open' or 'closed'");
PyDoc_STRVAR(shadow_root_host_doc, "the Element this shadow root is attached to");

static PyObject *shadow_root_get_mode(PyObject *self, void *Py_UNUSED(closure)) {
    return PyUnicode_FromString(th_shadow_mode(((NodeObject *)self)->node) != 0 ? "closed" : "open");
}

static PyObject *shadow_root_get_host(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *host;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    host = th_shadow_host(tree, node->node);
    Py_END_CRITICAL_SECTION();
    return node_wrap(state_of(self), node->handle, host);
}

static PyGetSetDef shadow_root_getset[] = {
    {"mode", shadow_root_get_mode, NULL, shadow_root_mode_doc, NULL},
    {"host", shadow_root_get_host, NULL, shadow_root_host_doc, NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(shadow_root_append_doc, "append(child, /)\n--\n\n"
                                     "Add child as the last node of the shadow tree, moving a node from this tree\n"
                                     "or adopting one from another by copy, like Element.append.");

static PyObject *shadow_root_append(PyObject *self, PyObject *child) {
    th_node *parent = ((NodeObject *)self)->node;
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node *node = adopt_into((NodeObject *)self, parent, child);
    error = node == NULL;
    if (node != NULL) {
        th_node_append_child(parent, node);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(shadow_root_set_inner_html_doc,
             "set_inner_html(html, /)\n--\n\n"
             "Replace the shadow tree's content by parsing html as a fragment, the way\n"
             "declarative shadow DOM populates a shadow root.\n\n"
             ":param html: the markup to parse and install as the shadow content.\n"
             ":raises TypeError: if html is not a str.");

static PyObject *shadow_root_set_inner_html(PyObject *self, PyObject *html) {
    if (!PyUnicode_Check(html)) {
        PyErr_SetString(PyExc_TypeError, "html must be a str");
        return NULL;
    }
    int scripting = th_tree_scripting(tree_of(self));
    th_tree *fragment = th_tree_parse_fragment(PyUnicode_KIND(html), PyUnicode_DATA(html), PyUnicode_GET_LENGTH(html),
                                               "div", 3, 0, 0, scripting);
    if (fragment == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *root = ((NodeObject *)self)->node;
    th_tree *dest = tree_of(self);
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    while (root->first_child != NULL) {
        th_node_remove(root->first_child);
    }
    for (th_node *child = th_tree_document(fragment)->first_child; child != NULL; child = child->next_sibling) {
        th_node *copy = th_tree_copy_node(dest, fragment, child);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            error = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(root, copy);
    }
    Py_END_CRITICAL_SECTION();
    th_tree_free(fragment);
    if (error) {                 /* GCOVR_EXCL_BR_LINE: the copy only fails on an unforceable allocation */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyMethodDef shadow_root_methods[] = {
    {"append", shadow_root_append, METH_O, shadow_root_append_doc},
    {"set_inner_html", shadow_root_set_inner_html, METH_O, shadow_root_set_inner_html_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(shadow_root_doc, "A shadow root: the document-fragment-like root of an element's shadow tree,\n"
                              "created by Element.attach_shadow. It is held off the light tree, so it never\n"
                              "appears among the host's children or in its serialization.");

static PyType_Slot shadow_root_slots[] = {
    {Py_tp_doc, (void *)shadow_root_doc},
    {Py_tp_getset, shadow_root_getset},
    {Py_tp_methods, shadow_root_methods},
    {0, NULL},
};

PyType_Spec shadow_root_spec = {
    .name = "turbohtml._html.ShadowRoot",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = shadow_root_slots,
};
