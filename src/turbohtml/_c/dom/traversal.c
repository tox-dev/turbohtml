/* TreeWalker and NodeIterator: the DOM Living Standard traversal objects.

   Both hold a root, a whatToShow bitmask, and an optional NodeFilter callback. The state machine and the whatToShow
   test run in C; the only callback into Python is the filter, invoked through run_filter. TreeWalker is a movable
   cursor (parent/child/sibling/next/previous); NodeIterator is a flat forward/backward view. The filter verdict drives
   the walk: FILTER_ACCEPT yields the node, FILTER_REJECT skips the node and (in TreeWalker) its whole subtree, and
   FILTER_SKIP skips only the node. NodeIterator's flat view has no subtree to skip, so it treats REJECT and SKIP alike.

   The algorithms are the spec's, cross-checked against jsdom's TreeWalker-impl and NodeIterator-impl sources. */

#include "dom/nodes.h"

#define TH_FILTER_ACCEPT 1
#define TH_FILTER_REJECT 2
#define TH_FILTER_SKIP 3

typedef struct {
    PyObject_HEAD PyObject *handle; /* keeps the tree + source alive and carries the per-tree lock */
    PyObject *filter;               /* the NodeFilter callback, or NULL when whatToShow is the only test */
    th_node *root;
    th_node *current;
    unsigned long what_to_show;
    int active; /* set only while the filter runs, so a re-entrant traversal is rejected */
} TreeWalkerObject;

typedef struct {
    PyObject_HEAD PyObject *handle;
    PyObject *filter;
    th_node *root;
    th_node *reference;
    unsigned long what_to_show;
    int pointer_before; /* the spec's pointer-before-reference-node flag */
    int active;
} NodeIteratorObject;

/* The whatToShow bit for a node's DOM nodeType, i.e. 1 << (nodeType - 1). turbohtml's node kinds map onto the DOM
   node types the tree can hold; the attribute/entity/notation bits have no node here and so never match. */
static unsigned long show_bit(enum th_node_type type) {
    switch (type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_ELEMENT:
        return 0x1UL; /* SHOW_ELEMENT */
    case TH_NODE_TEXT:
        return 0x4UL; /* SHOW_TEXT */
    case TH_NODE_CDATA:
        return 0x8UL; /* SHOW_CDATA_SECTION */
    case TH_NODE_PI:
        return 0x40UL; /* SHOW_PROCESSING_INSTRUCTION */
    case TH_NODE_COMMENT:
        return 0x80UL; /* SHOW_COMMENT */
    case TH_NODE_DOCUMENT:
        return 0x100UL; /* SHOW_DOCUMENT */
    case TH_NODE_DOCTYPE:
        return 0x200UL; /* SHOW_DOCUMENT_TYPE */
    case TH_NODE_CONTENT:
        return 0x400UL; /* SHOW_DOCUMENT_FRAGMENT (a <template>'s content) */
    }
    return 0; /* GCOVR_EXCL_LINE: unreachable, every kind is handled above */
}

/* Filter a node: the shared "filter" operation both traversers run. Stores FILTER_ACCEPT/REJECT/SKIP (or whatever int
   the filter returned) in *verdict and returns 0, or returns -1 with an exception set (a raised or re-entrant filter, a
   non-int verdict, or an overflowing one). filter is NULL when only whatToShow applies. Errors are the return value, so
   a filter free to return any int -- including a negative one -- is never mistaken for one. */
static int run_filter(module_state *state, PyObject *handle, PyObject *filter, unsigned long what_to_show, int *active,
                      th_node *node, int *verdict) {
    if (*active) {
        PyErr_SetString(PyExc_ValueError, "the node filter is already running (recursive traversal)");
        return -1;
    }
    if ((show_bit(node->type) & what_to_show) == 0) {
        *verdict = TH_FILTER_SKIP;
        return 0;
    }
    if (filter == NULL) {
        *verdict = TH_FILTER_ACCEPT;
        return 0;
    }
    PyObject *wrapped = node_wrap(state, handle, node);
    if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *active = 1;
    PyObject *result = PyObject_CallOneArg(filter, wrapped);
    *active = 0;
    Py_DECREF(wrapped);
    if (result == NULL) {
        return -1;
    }
    PyObject *index = PyNumber_Index(result);
    Py_DECREF(result);
    if (index == NULL) {
        return -1;
    }
    long value = PyLong_AsLong(index);
    Py_DECREF(index);
    if (value == -1 && PyErr_Occurred()) {
        return -1;
    }
    *verdict = (int)value; /* only ACCEPT/REJECT/SKIP are acted on; any other value reads as a plain skip */
    return 0;
}

static int tw_filter(TreeWalkerObject *self, module_state *state, th_node *node, int *verdict) {
    return run_filter(state, self->handle, self->filter, self->what_to_show, &self->active, node, verdict);
}

/* TreeWalker.parentNode: the nearest ancestor within root that the filter accepts. */
static th_node *tw_step_parent(TreeWalkerObject *self, module_state *state, int *failed) {
    th_node *node = self->current;
    while (node != NULL && node != self->root) {
        node = node->parent;
        if (node != NULL) {
            int verdict;
            if (tw_filter(self, state, node, &verdict) < 0) {
                *failed = 1;
                return NULL;
            }
            if (verdict == TH_FILTER_ACCEPT) {
                self->current = node;
                return node;
            }
        }
    }
    return NULL;
}

/* TreeWalker.firstChild (last == 0) / lastChild (last == 1): the first/last child the filter accepts, descending into
   a skipped child but never into a rejected one, and never climbing past the current node. */
static th_node *tw_traverse_children(TreeWalkerObject *self, module_state *state, int *failed, int last) {
    th_node *origin = self->current;
    th_node *node = last ? origin->last_child : origin->first_child;
    while (node != NULL) {
        int verdict;
        if (tw_filter(self, state, node, &verdict) < 0) {
            *failed = 1;
            return NULL;
        }
        if (verdict == TH_FILTER_ACCEPT) {
            self->current = node;
            return node;
        }
        if (verdict == TH_FILTER_SKIP) {
            th_node *child = last ? node->last_child : node->first_child;
            if (child != NULL) {
                node = child;
                continue;
            }
        }
        for (;;) {
            th_node *sibling = last ? node->prev_sibling : node->next_sibling;
            if (sibling != NULL) {
                node = sibling;
                break;
            }
            th_node *parent = node->parent;
            /* parent is never NULL here: node stays within origin's subtree, so the climb hits origin first */
            if (parent == self->root || parent == origin) {
                return NULL;
            }
            node = parent;
        }
    }
    return NULL;
}

/* TreeWalker.nextSibling (previous == 0) / previousSibling (previous == 1). */
static th_node *tw_traverse_siblings(TreeWalkerObject *self, module_state *state, int *failed, int previous) {
    th_node *node = self->current;
    if (node == self->root) {
        return NULL;
    }
    for (;;) {
        th_node *sibling = previous ? node->prev_sibling : node->next_sibling;
        while (sibling != NULL) {
            node = sibling;
            int verdict;
            if (tw_filter(self, state, node, &verdict) < 0) {
                *failed = 1;
                return NULL;
            }
            if (verdict == TH_FILTER_ACCEPT) {
                self->current = node;
                return node;
            }
            sibling = previous ? node->last_child : node->first_child;
            if (verdict == TH_FILTER_REJECT || sibling == NULL) {
                sibling = previous ? node->prev_sibling : node->next_sibling;
            }
        }
        node = node->parent;
        if (node == NULL || node == self->root) {
            return NULL;
        }
        int verdict;
        if (tw_filter(self, state, node, &verdict) < 0) {
            *failed = 1;
            return NULL;
        }
        if (verdict == TH_FILTER_ACCEPT) {
            return NULL;
        }
    }
}

/* TreeWalker.nextNode: the next node after current in document order the filter accepts, not descending into a
   rejected subtree. */
static th_node *tw_step_next_node(TreeWalkerObject *self, module_state *state, int *failed) {
    th_node *node = self->current;
    int verdict = TH_FILTER_ACCEPT;
    for (;;) {
        while (verdict != TH_FILTER_REJECT && node->first_child != NULL) {
            node = node->first_child;
            if (tw_filter(self, state, node, &verdict) < 0) {
                *failed = 1;
                return NULL;
            }
            if (verdict == TH_FILTER_ACCEPT) {
                self->current = node;
                return node;
            }
        }
        for (;;) {
            if (node == self->root) {
                return NULL;
            }
            th_node *sibling = node->next_sibling;
            if (sibling != NULL) {
                node = sibling;
                break;
            }
            node = node->parent;
            if (node == NULL) {
                break;
            }
        }
        if (node == NULL) {
            return NULL;
        }
        if (tw_filter(self, state, node, &verdict) < 0) {
            *failed = 1;
            return NULL;
        }
        if (verdict == TH_FILTER_ACCEPT) {
            self->current = node;
            return node;
        }
    }
}

/* TreeWalker.previousNode: the previous node before current in document order the filter accepts, descending to the
   deepest accepted last descendant of a preceding sibling before falling back to the parent. */
static th_node *tw_step_previous_node(TreeWalkerObject *self, module_state *state, int *failed) {
    th_node *node = self->current;
    while (node != self->root) {
        th_node *sibling = node->prev_sibling;
        while (sibling != NULL) {
            node = sibling;
            int verdict;
            if (tw_filter(self, state, node, &verdict) < 0) {
                *failed = 1;
                return NULL;
            }
            while (verdict != TH_FILTER_REJECT && node->first_child != NULL) {
                node = node->last_child;
                if (tw_filter(self, state, node, &verdict) < 0) {
                    *failed = 1;
                    return NULL;
                }
            }
            if (verdict == TH_FILTER_ACCEPT) {
                self->current = node;
                return node;
            }
            sibling = node->prev_sibling;
        }
        if (node == self->root || node->parent == NULL) {
            return NULL;
        }
        node = node->parent;
        int verdict;
        if (tw_filter(self, state, node, &verdict) < 0) {
            *failed = 1;
            return NULL;
        }
        if (verdict == TH_FILTER_ACCEPT) {
            self->current = node;
            return node;
        }
    }
    return NULL;
}

/* Run one TreeWalker step under the per-tree critical section, then wrap the found node (or None). */
static PyObject *tw_dispatch(PyObject *op, th_node *(*step)(TreeWalkerObject *, module_state *, int *)) {
    TreeWalkerObject *self = (TreeWalkerObject *)op;
    module_state *state = state_of(op);
    th_node *found;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(self->handle);
    found = step(self, state, &failed);
    Py_END_CRITICAL_SECTION();
    if (failed) {
        return NULL;
    }
    return node_wrap(state, self->handle, found);
}

static th_node *tw_step_first_child(TreeWalkerObject *self, module_state *state, int *failed) {
    return tw_traverse_children(self, state, failed, 0);
}

static th_node *tw_step_last_child(TreeWalkerObject *self, module_state *state, int *failed) {
    return tw_traverse_children(self, state, failed, 1);
}

static th_node *tw_step_next_sibling(TreeWalkerObject *self, module_state *state, int *failed) {
    return tw_traverse_siblings(self, state, failed, 0);
}

static th_node *tw_step_previous_sibling(TreeWalkerObject *self, module_state *state, int *failed) {
    return tw_traverse_siblings(self, state, failed, 1);
}

static PyObject *tree_walker_parent_node(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_parent);
}

static PyObject *tree_walker_first_child(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_first_child);
}

static PyObject *tree_walker_last_child(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_last_child);
}

static PyObject *tree_walker_next_sibling(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_next_sibling);
}

static PyObject *tree_walker_previous_sibling(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_previous_sibling);
}

static PyObject *tree_walker_next_node(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_next_node);
}

static PyObject *tree_walker_previous_node(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    return tw_dispatch(op, tw_step_previous_node);
}

/* Reject a non-Node or a node from another tree so current stays a live pointer into the walker's own tree. */
static int check_same_tree(PyObject *op, PyObject *value, module_state *state, NodeObject **out) {
    if (!is_node(value, state)) {
        PyErr_Format(PyExc_TypeError, "current_node must be a Node, not %.80s", Py_TYPE(value)->tp_name);
        return -1;
    }
    NodeObject *node = (NodeObject *)value;
    if (node->handle != ((TreeWalkerObject *)op)->handle) {
        PyErr_SetString(PyExc_ValueError, "current_node must belong to the walker's own tree");
        return -1;
    }
    *out = node;
    return 0;
}

static PyObject *tree_walker_get_root(PyObject *op, void *Py_UNUSED(closure)) {
    TreeWalkerObject *self = (TreeWalkerObject *)op;
    return node_wrap(state_of(op), self->handle, self->root);
}

static PyObject *tree_walker_get_what_to_show(PyObject *op, void *Py_UNUSED(closure)) {
    return PyLong_FromUnsignedLong(((TreeWalkerObject *)op)->what_to_show);
}

static PyObject *tree_walker_get_filter(PyObject *op, void *Py_UNUSED(closure)) {
    PyObject *filter = ((TreeWalkerObject *)op)->filter;
    return Py_NewRef(filter == NULL ? Py_None : filter);
}

static PyObject *tree_walker_get_current(PyObject *op, void *Py_UNUSED(closure)) {
    TreeWalkerObject *self = (TreeWalkerObject *)op;
    return node_wrap(state_of(op), self->handle, self->current);
}

static int tree_walker_set_current(PyObject *op, PyObject *value, void *Py_UNUSED(closure)) {
    if (value == NULL) {
        PyErr_SetString(PyExc_TypeError, "cannot delete current_node");
        return -1;
    }
    NodeObject *node;
    if (check_same_tree(op, value, state_of(op), &node) < 0) {
        return -1;
    }
    ((TreeWalkerObject *)op)->current = node->node;
    return 0;
}

static PyObject *tree_walker_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"root", "what_to_show", "filter", NULL};
    PyObject *root_obj;
    unsigned long what_to_show = 0xFFFFFFFFUL;
    PyObject *filter = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|kO:TreeWalker", keywords, &root_obj, &what_to_show, &filter)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(root_obj, state)) {
        PyErr_Format(PyExc_TypeError, "root must be a Node, not %.80s", Py_TYPE(root_obj)->tp_name);
        return NULL;
    }
    if (filter != Py_None && !PyCallable_Check(filter)) {
        PyErr_SetString(PyExc_TypeError, "filter must be callable or None");
        return NULL;
    }
    NodeObject *root = (NodeObject *)root_obj;
    TreeWalkerObject *self = (TreeWalkerObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(root->handle);
    self->root = root->node;
    self->current = root->node;
    self->what_to_show = what_to_show;
    self->filter = filter == Py_None ? NULL : Py_NewRef(filter);
    self->active = 0;
    return (PyObject *)self;
}

static int traversal_traverse(PyObject *op, visitproc visit, void *arg, PyObject *handle, PyObject *filter) {
    Py_VISIT(Py_TYPE(op)); /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(handle);      /* GCOVR_EXCL_BR_LINE: the handle is set at creation and dropped only in clear/dealloc */
    Py_VISIT(filter);      /* GCOVR_EXCL_BR_LINE: the visit-error arm needs a gc callback that errors */
    return 0;
}

static int tree_walker_traverse(PyObject *op, visitproc visit, void *arg) {
    TreeWalkerObject *self = (TreeWalkerObject *)op;
    return traversal_traverse(op, visit, arg, self->handle, self->filter);
}

static int tree_walker_clear(PyObject *op) {
    TreeWalkerObject *self = (TreeWalkerObject *)op;
    Py_CLEAR(self->handle); /* GCOVR_EXCL_BR_LINE: the handle is non-NULL until this single clear */
    Py_CLEAR(self->filter); /* the NULL arm runs for a walker built without a filter */
    return 0;
}

static void tree_walker_dealloc(PyObject *op) {
    PyTypeObject *type = Py_TYPE(op);
    PyObject_GC_UnTrack(op);
    (void)tree_walker_clear(op);
    type->tp_free(op);
    Py_DECREF(type);
}

static PyGetSetDef tree_walker_getset[] = {
    {"root", tree_walker_get_root, NULL, "the node the walk is rooted at (read-only)", NULL},
    {"what_to_show", tree_walker_get_what_to_show, NULL, "the whatToShow bitmask of node types the walk considers",
     NULL},
    {"filter", tree_walker_get_filter, NULL, "the NodeFilter callback, or None", NULL},
    {"current_node", tree_walker_get_current, tree_walker_set_current,
     "the node the cursor rests on; assignable to any node in the same tree", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyMethodDef tree_walker_methods[] = {
    {"parent_node", tree_walker_parent_node, METH_NOARGS,
     "parent_node()\n--\n\nMove to the nearest accepted ancestor within root and return it, or None."},
    {"first_child", tree_walker_first_child, METH_NOARGS,
     "first_child()\n--\n\nMove to the first accepted child of the current node and return it, or None."},
    {"last_child", tree_walker_last_child, METH_NOARGS,
     "last_child()\n--\n\nMove to the last accepted child of the current node and return it, or None."},
    {"next_sibling", tree_walker_next_sibling, METH_NOARGS,
     "next_sibling()\n--\n\nMove to the next accepted sibling of the current node and return it, or None."},
    {"previous_sibling", tree_walker_previous_sibling, METH_NOARGS,
     "previous_sibling()\n--\n\nMove to the previous accepted sibling of the current node and return it, or None."},
    {"next_node", tree_walker_next_node, METH_NOARGS,
     "next_node()\n--\n\nMove to the next accepted node in document order and return it, or None."},
    {"previous_node", tree_walker_previous_node, METH_NOARGS,
     "previous_node()\n--\n\nMove to the previous accepted node in document order and return it, or None."},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(tree_walker_doc, "TreeWalker(root, what_to_show=SHOW_ALL, filter=None)\n--\n\n"
                              "A movable cursor over the nodes of root's subtree the filter accepts.\n\n"
                              "The DOM Living Standard TreeWalker. what_to_show is a NodeFilter bitmask of\n"
                              "node types to consider; filter is a callable taking a Node and returning\n"
                              "NodeFilter.FILTER_ACCEPT, FILTER_REJECT, or FILTER_SKIP. REJECT skips the node\n"
                              "and its whole subtree; SKIP skips only the node. current_node starts at root\n"
                              "and each move returns the new node, or None when there is none in that\n"
                              "direction (leaving current_node unchanged).\n\n"
                              ":param root: the node whose subtree the walk is confined to.\n"
                              ":param what_to_show: the whatToShow bitmask; SHOW_ALL by default.\n"
                              ":param filter: a Node -> int callback, or None to test only what_to_show.\n"
                              ":raises TypeError: root is not a Node, or filter is neither callable nor None.\n"
                              ":raises ValueError: a filter re-enters the walker, or current_node is set to a\n"
                              "    node from another tree.");

static PyType_Slot tree_walker_slots[] = {
    {Py_tp_doc, (void *)tree_walker_doc}, {Py_tp_new, tree_walker_new},
    {Py_tp_dealloc, tree_walker_dealloc}, {Py_tp_traverse, tree_walker_traverse},
    {Py_tp_clear, tree_walker_clear},     {Py_tp_methods, tree_walker_methods},
    {Py_tp_getset, tree_walker_getset},   {0, NULL},
};

PyType_Spec tree_walker_spec = {
    .name = "turbohtml._html.TreeWalker",
    .basicsize = sizeof(TreeWalkerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = tree_walker_slots,
};

/* NodeIterator's "following" within root's subtree: the next node in document order, or NULL at the subtree end. */
static th_node *ni_following(th_node *node, th_node *root) {
    return preorder_next(node, root);
}

/* NodeIterator's "preceding" within root's subtree: the previous node in document order, or NULL at root itself. */
static th_node *ni_preceding(th_node *node, th_node *root) {
    if (node == root) {
        return NULL;
    }
    return previous_element(node);
}

/* NodeIterator.traverse: advance the reference across the flat filtered view. previous == 0 moves forward, 1 back.
   The flat view has no subtree, so REJECT and SKIP both just keep looking. Returns the accepted node, NULL at an
   end, or NULL with *failed set on a filter error. */
static th_node *ni_traverse(NodeIteratorObject *self, module_state *state, int *failed, int previous) {
    th_node *node = self->reference;
    int before = self->pointer_before;
    for (;;) {
        if (!previous) {
            if (!before) {
                node = ni_following(node, self->root);
                if (node == NULL) {
                    return NULL;
                }
            }
            before = 0;
        } else {
            if (before) {
                node = ni_preceding(node, self->root);
                if (node == NULL) {
                    return NULL;
                }
            }
            before = 1;
        }
        int verdict;
        if (run_filter(state, self->handle, self->filter, self->what_to_show, &self->active, node, &verdict) < 0) {
            *failed = 1;
            return NULL;
        }
        if (verdict == TH_FILTER_ACCEPT) {
            self->reference = node;
            self->pointer_before = before;
            return node;
        }
    }
}

/* Run one NodeIterator step under the per-tree critical section; return the found node, or found_is_stop set so the
   caller can raise StopIteration rather than return None. */
static th_node *ni_dispatch(PyObject *op, int previous, int *failed) {
    NodeIteratorObject *self = (NodeIteratorObject *)op;
    module_state *state = state_of(op);
    th_node *found;
    Py_BEGIN_CRITICAL_SECTION(self->handle);
    found = ni_traverse(self, state, failed, previous);
    Py_END_CRITICAL_SECTION();
    return found;
}

static PyObject *node_iterator_next_node(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    int failed = 0;
    th_node *found = ni_dispatch(op, 0, &failed);
    if (failed) {
        return NULL;
    }
    return node_wrap(state_of(op), ((NodeIteratorObject *)op)->handle, found);
}

static PyObject *node_iterator_previous_node(PyObject *op, PyObject *Py_UNUSED(ignored)) {
    int failed = 0;
    th_node *found = ni_dispatch(op, 1, &failed);
    if (failed) {
        return NULL;
    }
    return node_wrap(state_of(op), ((NodeIteratorObject *)op)->handle, found);
}

static PyObject *node_iterator_iternext(PyObject *op) {
    int failed = 0;
    th_node *found = ni_dispatch(op, 0, &failed);
    if (failed) {
        return NULL;
    }
    if (found == NULL) {
        return NULL; /* exhausted: NULL with no exception raises StopIteration */
    }
    return node_wrap(state_of(op), ((NodeIteratorObject *)op)->handle, found);
}

static PyObject *node_iterator_get_root(PyObject *op, void *Py_UNUSED(closure)) {
    NodeIteratorObject *self = (NodeIteratorObject *)op;
    return node_wrap(state_of(op), self->handle, self->root);
}

static PyObject *node_iterator_get_what_to_show(PyObject *op, void *Py_UNUSED(closure)) {
    return PyLong_FromUnsignedLong(((NodeIteratorObject *)op)->what_to_show);
}

static PyObject *node_iterator_get_filter(PyObject *op, void *Py_UNUSED(closure)) {
    PyObject *filter = ((NodeIteratorObject *)op)->filter;
    return Py_NewRef(filter == NULL ? Py_None : filter);
}

static PyObject *node_iterator_get_reference(PyObject *op, void *Py_UNUSED(closure)) {
    NodeIteratorObject *self = (NodeIteratorObject *)op;
    return node_wrap(state_of(op), self->handle, self->reference);
}

static PyObject *node_iterator_get_pointer_before(PyObject *op, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(((NodeIteratorObject *)op)->pointer_before);
}

static PyObject *node_iterator_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"root", "what_to_show", "filter", NULL};
    PyObject *root_obj;
    unsigned long what_to_show = 0xFFFFFFFFUL;
    PyObject *filter = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|kO:NodeIterator", keywords, &root_obj, &what_to_show, &filter)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(root_obj, state)) {
        PyErr_Format(PyExc_TypeError, "root must be a Node, not %.80s", Py_TYPE(root_obj)->tp_name);
        return NULL;
    }
    if (filter != Py_None && !PyCallable_Check(filter)) {
        PyErr_SetString(PyExc_TypeError, "filter must be callable or None");
        return NULL;
    }
    NodeObject *root = (NodeObject *)root_obj;
    NodeIteratorObject *self = (NodeIteratorObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(root->handle);
    self->root = root->node;
    self->reference = root->node;
    self->what_to_show = what_to_show;
    self->pointer_before = 1;
    self->filter = filter == Py_None ? NULL : Py_NewRef(filter);
    self->active = 0;
    return (PyObject *)self;
}

static int node_iterator_traverse(PyObject *op, visitproc visit, void *arg) {
    NodeIteratorObject *self = (NodeIteratorObject *)op;
    return traversal_traverse(op, visit, arg, self->handle, self->filter);
}

static int node_iterator_clear(PyObject *op) {
    NodeIteratorObject *self = (NodeIteratorObject *)op;
    Py_CLEAR(self->handle); /* GCOVR_EXCL_BR_LINE: the handle is non-NULL until this single clear */
    Py_CLEAR(self->filter); /* the NULL arm runs for an iterator built without a filter */
    return 0;
}

static void node_iterator_dealloc(PyObject *op) {
    PyTypeObject *type = Py_TYPE(op);
    PyObject_GC_UnTrack(op);
    (void)node_iterator_clear(op);
    type->tp_free(op);
    Py_DECREF(type);
}

static PyGetSetDef node_iterator_getset[] = {
    {"root", node_iterator_get_root, NULL, "the node the iteration is rooted at (read-only)", NULL},
    {"what_to_show", node_iterator_get_what_to_show, NULL,
     "the whatToShow bitmask of node types the iteration considers", NULL},
    {"filter", node_iterator_get_filter, NULL, "the NodeFilter callback, or None", NULL},
    {"reference_node", node_iterator_get_reference, NULL, "the node the iterator last returned or started at", NULL},
    {"pointer_before_reference_node", node_iterator_get_pointer_before, NULL,
     "whether the iterator sits just before (True) or after (False) reference_node", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyMethodDef node_iterator_methods[] = {
    {"next_node", node_iterator_next_node, METH_NOARGS,
     "next_node()\n--\n\nReturn the next accepted node in document order, or None past the end."},
    {"previous_node", node_iterator_previous_node, METH_NOARGS,
     "previous_node()\n--\n\nReturn the previous accepted node in document order, or None past the start."},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(node_iterator_doc, "NodeIterator(root, what_to_show=SHOW_ALL, filter=None)\n--\n\n"
                                "A flat forward/backward view of the nodes of root's subtree the filter accepts.\n\n"
                                "The DOM Living Standard NodeIterator. what_to_show and filter work as for\n"
                                "TreeWalker, but because the view is flat there is no subtree to skip:\n"
                                "FILTER_REJECT and FILTER_SKIP behave identically. next_node and previous_node\n"
                                "walk the accepted nodes in document order and return None past an end;\n"
                                "iterating the object with a for loop yields the same forward sequence,\n"
                                "stopping at the end.\n\n"
                                ":param root: the node whose subtree the iteration is confined to.\n"
                                ":param what_to_show: the whatToShow bitmask; SHOW_ALL by default.\n"
                                ":param filter: a Node -> int callback, or None to test only what_to_show.\n"
                                ":raises TypeError: root is not a Node, or filter is neither callable nor None.\n"
                                ":raises ValueError: a filter re-enters the iterator.");

static PyType_Slot node_iterator_slots[] = {
    {Py_tp_doc, (void *)node_iterator_doc},   {Py_tp_new, node_iterator_new},
    {Py_tp_dealloc, node_iterator_dealloc},   {Py_tp_traverse, node_iterator_traverse},
    {Py_tp_clear, node_iterator_clear},       {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, node_iterator_iternext}, {Py_tp_methods, node_iterator_methods},
    {Py_tp_getset, node_iterator_getset},     {0, NULL},
};

PyType_Spec node_iterator_spec = {
    .name = "turbohtml._html.NodeIterator",
    .basicsize = sizeof(NodeIteratorObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = node_iterator_slots,
};
