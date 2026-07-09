/* The DOM Range and StaticRange APIs (issue #552): boundary-point math, comparison, and the
   extract/clone/delete/insert/surround content operations, all in C under the per-tree critical
   section. A boundary point is a (container node, offset) pair; a Range keeps two of them and
   maintains start <= end, while a StaticRange stores four values verbatim and is immutable.

   Offsets index code points in character data (Text/Comment/CData) and children elsewhere, so a
   Python str's own indexing lines up with a Text-node offset. Range boundaries are not driven by
   edits made through other tree APIs; only the Range's own content operations move them, per the
   DOM extract/clone/delete algorithms. */

#include "dom/nodes.h"

typedef struct {
    PyObject_HEAD PyObject *start_handle; /* _TreeHandle owning start_node (keeps its tree alive) */
    PyObject *end_handle;                 /* _TreeHandle owning end_node; equals start_handle for a live Range */
    th_node *start_node;
    th_node *end_node;
    Py_ssize_t start_offset;
    Py_ssize_t end_offset;
} RangeObject;

/* Character data whose offset indexes code points and which the content operations split in place. */
static int is_char_data(const th_node *node) {
    switch (node->type) {
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        return 1;
    default:
        return 0;
    }
}

/* The DOM "length of a node": the code-point count for character data, else the child count. */
static Py_ssize_t node_length(th_node *node) {
    if (is_char_data(node)) {
        return node->text_len;
    }
    Py_ssize_t count = 0;
    for (th_node *child = node->first_child; child != NULL; child = child->next_sibling) {
        count++;
    }
    return count;
}

/* The node's position among its siblings (its DOM index). */
static Py_ssize_t node_index(th_node *node) {
    Py_ssize_t index = 0;
    for (th_node *prev = node->prev_sibling; prev != NULL; prev = prev->prev_sibling) {
        index++;
    }
    return index;
}

/* The topmost ancestor (the root of the node's tree). */
static th_node *node_root(th_node *node) {
    while (node->parent != NULL) {
        node = node->parent;
    }
    return node;
}

/* Whether ancestor is node or one of its ancestors. */
static int is_inclusive_ancestor(th_node *ancestor, th_node *node) {
    return ancestor == node || is_ancestor(ancestor, node);
}

/* Tree order of two distinct nodes sharing a root: -1 when left precedes right in a pre-order walk,
   +1 when it follows. An ancestor precedes its descendants. */
static int node_order(th_node *left, th_node *right) {
    Py_ssize_t left_depth = 0;
    for (th_node *walk = left; walk->parent != NULL; walk = walk->parent) {
        left_depth++;
    }
    Py_ssize_t right_depth = 0;
    for (th_node *walk = right; walk->parent != NULL; walk = walk->parent) {
        right_depth++;
    }
    th_node *left_at = left;
    th_node *right_at = right;
    while (left_depth > right_depth) {
        left_at = left_at->parent;
        left_depth--;
        if (left_at == right) {
            return 1; /* right is an ancestor of left, so left follows it */
        }
    }
    while (right_depth > left_depth) {
        right_at = right_at->parent;
        right_depth--;
        if (right_at == left) {
            return -1; /* left is an ancestor of right, so left precedes it */
        }
    }
    while (left_at->parent != right_at->parent) {
        left_at = left_at->parent;
        right_at = right_at->parent;
    }
    return node_index(left_at) < node_index(right_at) ? -1 : 1;
}

/* Compare two boundary points sharing a root: -1 (left before right), 0 (equal), +1 (left after). */
static int bp_compare(th_node *left_node, Py_ssize_t left_offset, th_node *right_node, Py_ssize_t right_offset) {
    if (left_node == right_node) {
        if (left_offset == right_offset) {
            return 0;
        }
        return left_offset < right_offset ? -1 : 1;
    }
    if (node_order(left_node, right_node) > 0) {
        return bp_compare(right_node, right_offset, left_node, left_offset) == -1 ? 1 : -1;
    }
    if (is_ancestor(left_node, right_node)) {
        th_node *child = right_node;
        while (child->parent != left_node) {
            child = child->parent;
        }
        if (node_index(child) < left_offset) {
            return 1;
        }
    }
    return -1;
}

/* Whether two boundary containers share a root; a differing handle guarantees a different tree. */
static int same_root(PyObject *left_handle, th_node *left_node, PyObject *right_handle, th_node *right_node) {
    return left_handle == right_handle && node_root(left_node) == node_root(right_node);
}

/* Splice the [from, to) code-point run out of a character-data node's text. Returns 0, or -1 on
   allocation failure. */
static int replace_data_delete(th_tree *tree, th_node *node, Py_ssize_t from, Py_ssize_t to) {
    Py_ssize_t remaining = node->text_len - to;
    Py_ssize_t new_len = from + remaining;
    const Py_UCS4 *text = th_node_realize_text(tree, node);
    Py_UCS4 *buffer = PyMem_Malloc((size_t)(new_len > 0 ? new_len : 1) * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(buffer, text, (size_t)from * sizeof(Py_UCS4));
    memcpy(buffer + from, text + to, (size_t)remaining * sizeof(Py_UCS4));
    int rc = th_node_set_data(tree, node, buffer, new_len);
    PyMem_Free(buffer);
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_set_data only fails on OOM */
}

/* Split a character-data node at offset: the tail becomes a new sibling right after it, which is
   returned. NULL on allocation failure. */
static th_node *split_data_node(th_tree *tree, th_node *node, Py_ssize_t offset) {
    const Py_UCS4 *text = th_node_realize_text(tree, node);
    th_node *tail = th_tree_make_data_node(tree, node->type, text + offset, node->text_len - offset);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (replace_data_delete(tree, node, offset, node->text_len) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
        return NULL;                                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node_insert_before(node->parent, tail, node->next_sibling);
    return tail;
}

/* A childless copy of an element (same tag, namespace, and attributes) in the same tree: deep-copy
   then drop the children, reusing the tested copy primitive. NULL on allocation failure. */
static th_node *shallow_clone(th_tree *tree, th_node *node) {
    th_node *copy = th_tree_copy_node(tree, tree, node);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (copy->first_child != NULL) {
        th_node_remove(copy->first_child);
    }
    return copy;
}

/* A node fully inside the range: its whole span sits strictly between the boundaries. */
static int is_contained(th_node *node, th_node *start_node, Py_ssize_t start_offset, th_node *end_node,
                        Py_ssize_t end_offset) {
    return bp_compare(node, 0, start_node, start_offset) > 0 &&
           bp_compare(node, node_length(node), end_node, end_offset) < 0;
}

/* A node straddling exactly one boundary (an inclusive ancestor of one endpoint but not the other). */
static int is_partially_contained(th_node *node, th_node *start_node, th_node *end_node) {
    return is_inclusive_ancestor(node, start_node) != is_inclusive_ancestor(node, end_node);
}

/* Move every child of a throwaway fragment onto parent, in order. */
static void adopt_fragment_children(th_node *parent, th_node *fragment) {
    while (fragment->first_child != NULL) {
        th_node *child = fragment->first_child;
        th_node_remove(child);
        th_node_append_child(parent, child);
    }
}

/* The children of common that the range fully contains, checked for a contained DocumentType (a
   HierarchyRequestError). *out_count is the run length; the returned array is PyMem-owned (NULL when
   empty). Sets an exception and returns -1 through *error on a doctype or allocation failure. */
static th_node **collect_contained(th_node *common, th_node *start_node, Py_ssize_t start_offset, th_node *end_node,
                                   Py_ssize_t end_offset, Py_ssize_t *out_count, int *error) {
    *error = 0;
    Py_ssize_t count = 0;
    for (th_node *child = common->first_child; child != NULL; child = child->next_sibling) {
        if (is_contained(child, start_node, start_offset, end_node, end_offset)) {
            count++;
        }
    }
    *out_count = count;
    if (count == 0) {
        return NULL;
    }
    th_node **nodes = PyMem_Malloc((size_t)count * sizeof(th_node *));
    if (nodes == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        *error = 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t index = 0;
    for (th_node *child = common->first_child; child != NULL; child = child->next_sibling) {
        if (is_contained(child, start_node, start_offset, end_node, end_offset)) {
            if (child->type == TH_NODE_DOCTYPE) {
                PyErr_SetString(PyExc_ValueError, "cannot extract a range spanning a doctype");
                PyMem_Free(nodes);
                *error = 1;
                return NULL;
            }
            nodes[index++] = child;
        }
    }
    return nodes;
}

/* The common ancestor of the two boundary containers (they share a root). */
static th_node *common_ancestor(th_node *start_node, th_node *end_node) {
    th_node *container = start_node;
    while (!is_inclusive_ancestor(container, end_node)) {
        container = container->parent;
    }
    return container;
}

/* The first (walking forward) child of common the range partially contains. */
static th_node *first_partial(th_node *common, th_node *start_node, th_node *end_node) {
    th_node *child = common->first_child;
    while (!is_partially_contained(child, start_node, end_node)) {
        child = child->next_sibling;
    }
    return child;
}

/* The last (walking back) child of common the range partially contains. */
static th_node *last_partial(th_node *common, th_node *start_node, th_node *end_node) {
    th_node *child = common->last_child;
    while (!is_partially_contained(child, start_node, end_node)) {
        child = child->prev_sibling;
    }
    return child;
}

/* Extract the range's content into a fresh fragment, moving contained nodes and splitting the
   boundary character data out of the source tree. Recursive over straddled elements. NULL on
   error with an exception set; the returned CONTENT node lives in tree. */
static th_node *do_extract(th_tree *tree, th_node *start_node, Py_ssize_t start_offset, th_node *end_node,
                           Py_ssize_t end_offset) {
    th_node *fragment = th_tree_make_fragment(tree);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (start_node == end_node && start_offset == end_offset) {
        return fragment;
    }
    if (start_node == end_node && is_char_data(start_node)) {
        const Py_UCS4 *text = th_node_realize_text(tree, start_node);
        th_node *piece = th_tree_make_data_node(tree, start_node->type, text + start_offset, end_offset - start_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        if (replace_data_delete(tree, start_node, start_offset, end_offset) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;                                                           /* GCOVR_EXCL_LINE: OOM path */
        }
        return fragment;
    }
    th_node *common = common_ancestor(start_node, end_node);
    th_node *first = is_inclusive_ancestor(start_node, end_node) ? NULL : first_partial(common, start_node, end_node);
    th_node *last = is_inclusive_ancestor(end_node, start_node) ? NULL : last_partial(common, start_node, end_node);
    Py_ssize_t contained_count;
    int error;
    th_node **contained =
        collect_contained(common, start_node, start_offset, end_node, end_offset, &contained_count, &error);
    if (error) {
        return NULL;
    }
    if (first != NULL && is_char_data(first)) {
        const Py_UCS4 *text = th_node_realize_text(tree, start_node);
        th_node *piece =
            th_tree_make_data_node(tree, start_node->type, text + start_offset, start_node->text_len - start_offset);
        if (piece == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        /* replace_data_delete only fails on OOM */
        if (replace_data_delete(tree, start_node, start_offset, start_node->text_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            PyMem_Free(contained);                                                           /* GCOVR_EXCL_LINE: OOM */
            return NULL;                                                                     /* GCOVR_EXCL_LINE: OOM */
        }
    } else if (first != NULL) {
        th_node *clone = shallow_clone(tree, first);
        if (clone == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_extract(tree, start_node, start_offset, first, node_length(first));
        if (sub == NULL) {         /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(clone, sub);
    }
    for (Py_ssize_t index = 0; index < contained_count; index++) {
        th_node_remove(contained[index]);
        th_node_append_child(fragment, contained[index]);
    }
    PyMem_Free(contained);
    if (last != NULL && is_char_data(last)) {
        const Py_UCS4 *text = th_node_realize_text(tree, end_node);
        th_node *piece = th_tree_make_data_node(tree, end_node->type, text, end_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        if (replace_data_delete(tree, end_node, 0, end_offset) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;                                              /* GCOVR_EXCL_LINE: OOM path */
        }
    } else if (last != NULL) {
        th_node *clone = shallow_clone(tree, last);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_extract(tree, last, 0, end_node, end_offset);
        if (sub == NULL) { /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(clone, sub);
    }
    return fragment;
}

/* Clone the range's content into a fresh fragment, leaving the source tree untouched. Recursive
   over straddled elements. NULL on error with an exception set. */
static th_node *do_clone(th_tree *tree, th_node *start_node, Py_ssize_t start_offset, th_node *end_node,
                         Py_ssize_t end_offset) {
    th_node *fragment = th_tree_make_fragment(tree);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (start_node == end_node && start_offset == end_offset) {
        return fragment;
    }
    if (start_node == end_node && is_char_data(start_node)) {
        const Py_UCS4 *text = th_node_realize_text(tree, start_node);
        th_node *piece = th_tree_make_data_node(tree, start_node->type, text + start_offset, end_offset - start_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
        return fragment;
    }
    th_node *common = common_ancestor(start_node, end_node);
    th_node *first = is_inclusive_ancestor(start_node, end_node) ? NULL : first_partial(common, start_node, end_node);
    th_node *last = is_inclusive_ancestor(end_node, start_node) ? NULL : last_partial(common, start_node, end_node);
    Py_ssize_t contained_count;
    int error;
    th_node **contained =
        collect_contained(common, start_node, start_offset, end_node, end_offset, &contained_count, &error);
    if (error) {
        return NULL;
    }
    if (first != NULL && is_char_data(first)) {
        const Py_UCS4 *text = th_node_realize_text(tree, start_node);
        th_node *piece =
            th_tree_make_data_node(tree, start_node->type, text + start_offset, start_node->text_len - start_offset);
        if (piece == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
    } else if (first != NULL) {
        th_node *clone = shallow_clone(tree, first);
        if (clone == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_clone(tree, start_node, start_offset, first, node_length(first));
        if (sub == NULL) {         /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(clone, sub);
    }
    for (Py_ssize_t index = 0; index < contained_count; index++) {
        th_node *copy = th_tree_copy_node(tree, tree, contained[index]);
        if (copy == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(contained); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, copy);
    }
    PyMem_Free(contained);
    if (last != NULL && is_char_data(last)) {
        const Py_UCS4 *text = th_node_realize_text(tree, end_node);
        th_node *piece = th_tree_make_data_node(tree, end_node->type, text, end_offset);
        if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, piece);
    } else if (last != NULL) {
        th_node *clone = shallow_clone(tree, last);
        if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(fragment, clone);
        th_node *sub = do_clone(tree, last, 0, end_node, end_offset);
        if (sub == NULL) { /* GCOVR_EXCL_BR_LINE: only the OOM paths of the recursion reach here */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        adopt_fragment_children(clone, sub);
    }
    return fragment;
}

/* The collapsed boundary a range takes after extract/delete, computed before the tree mutates. */
static void collapse_point(th_node *start_node, Py_ssize_t start_offset, th_node *end_node, th_node **out_node,
                           Py_ssize_t *out_offset) {
    if (is_inclusive_ancestor(start_node, end_node)) {
        *out_node = start_node;
        *out_offset = start_offset;
        return;
    }
    th_node *reference = start_node;
    while (!is_inclusive_ancestor(reference->parent, end_node)) {
        reference = reference->parent;
    }
    *out_node = reference->parent;
    *out_offset = node_index(reference) + 1;
}

/* --- boundary helpers on the RangeObject --- */

static int validate_boundary(th_node *node, Py_ssize_t offset) {
    if (node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "a boundary point cannot be inside a doctype");
        return -1;
    }
    if (offset < 0 || offset > node_length(node)) {
        PyErr_SetString(PyExc_IndexError, "offset is out of range for the node");
        return -1;
    }
    return 0;
}

static int range_is_collapsed(RangeObject *self) {
    return self->start_node == self->end_node && self->start_offset == self->end_offset;
}

static void store_start(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    Py_SETREF(self->start_handle, Py_NewRef(handle));
    self->start_node = node;
    self->start_offset = offset;
}

static void store_end(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    Py_SETREF(self->end_handle, Py_NewRef(handle));
    self->end_node = node;
    self->end_offset = offset;
}

/* The DOM "set the start" step: drag the end onto the new point when it lands in another tree or
   past the current end, so start <= end holds. */
static void set_start_bp(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    if (!same_root(handle, node, self->end_handle, self->end_node) ||
        bp_compare(node, offset, self->end_node, self->end_offset) > 0) {
        store_end(self, handle, node, offset);
    }
    store_start(self, handle, node, offset);
}

static void set_end_bp(RangeObject *self, PyObject *handle, th_node *node, Py_ssize_t offset) {
    if (!same_root(handle, node, self->start_handle, self->start_node) ||
        bp_compare(node, offset, self->start_node, self->start_offset) < 0) {
        store_start(self, handle, node, offset);
    }
    store_end(self, handle, node, offset);
}

/* --- construction --- */

static PyObject *range_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"container", "offset", NULL};
    PyObject *container;
    Py_ssize_t offset = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|n:Range", keywords, &container, &offset)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(container, state)) {
        PyErr_SetString(PyExc_TypeError, "container must be a node");
        return NULL;
    }
    th_node *node = ((NodeObject *)container)->node;
    if (validate_boundary(node, offset) < 0) {
        return NULL;
    }
    RangeObject *self = (RangeObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *handle = ((NodeObject *)container)->handle;
    self->start_handle = Py_NewRef(handle);
    self->end_handle = Py_NewRef(handle);
    self->start_node = node;
    self->end_node = node;
    self->start_offset = offset;
    self->end_offset = offset;
    return (PyObject *)self;
}

static void range_dealloc(PyObject *self) {
    RangeObject *range = (RangeObject *)self;
    PyTypeObject *type = Py_TYPE(self);
    Py_XDECREF(range->start_handle);
    Py_XDECREF(range->end_handle);
    type->tp_free(self);
    Py_DECREF(type);
}

/* --- AbstractRange getters (shared by Range and StaticRange) --- */

static PyObject *get_start_container(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    return node_wrap(state_of(self), range->start_handle, range->start_node);
}

static PyObject *get_start_offset(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((RangeObject *)self)->start_offset);
}

static PyObject *get_end_container(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    return node_wrap(state_of(self), range->end_handle, range->end_node);
}

static PyObject *get_end_offset(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((RangeObject *)self)->end_offset);
}

static PyObject *get_collapsed(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(range_is_collapsed((RangeObject *)self));
}

static PyObject *get_common_ancestor(PyObject *self, void *Py_UNUSED(closure)) {
    RangeObject *range = (RangeObject *)self;
    th_node *container = common_ancestor(range->start_node, range->end_node);
    return node_wrap(state_of(self), range->start_handle, container);
}

/* --- boundary setters --- */

/* Borrow the (handle, node) a node argument wraps; sets a TypeError and returns -1 otherwise. */
static int node_arg(PyObject *self, PyObject *arg, PyObject **handle, th_node **node) {
    if (!is_node(arg, state_of(self))) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return -1;
    }
    *handle = ((NodeObject *)arg)->handle;
    *node = ((NodeObject *)arg)->node;
    return 0;
}

static PyObject *set_boundary(PyObject *self, PyObject *args, int is_start) {
    PyObject *container;
    Py_ssize_t offset;
    if (!PyArg_ParseTuple(args, "On", &container, &offset)) {
        return NULL;
    }
    PyObject *handle;
    th_node *node;
    if (node_arg(self, container, &handle, &node) < 0 || validate_boundary(node, offset) < 0) {
        return NULL;
    }
    if (is_start) {
        set_start_bp((RangeObject *)self, handle, node, offset);
    } else {
        set_end_bp((RangeObject *)self, handle, node, offset);
    }
    Py_RETURN_NONE;
}

static PyObject *range_set_start(PyObject *self, PyObject *args) {
    return set_boundary(self, args, 1);
}

static PyObject *range_set_end(PyObject *self, PyObject *args) {
    return set_boundary(self, args, 0);
}

/* The parent and index of a node used as a before/after reference; sets InvalidNodeTypeError and
   returns -1 when the node has no parent. */
static int reference_parent(PyObject *self, PyObject *arg, PyObject **handle, th_node **parent, Py_ssize_t *index) {
    th_node *node;
    if (node_arg(self, arg, handle, &node) < 0) {
        return -1;
    }
    if (node->parent == NULL) {
        PyErr_SetString(PyExc_ValueError, "the node has no parent");
        return -1;
    }
    *parent = node->parent;
    *index = node_index(node);
    return 0;
}

static PyObject *range_set_start_before(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_start_bp((RangeObject *)self, handle, parent, index);
    Py_RETURN_NONE;
}

static PyObject *range_set_start_after(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_start_bp((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

static PyObject *range_set_end_before(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_end_bp((RangeObject *)self, handle, parent, index);
    Py_RETURN_NONE;
}

static PyObject *range_set_end_after(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    set_end_bp((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

static PyObject *range_select_node(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *parent;
    Py_ssize_t index;
    if (reference_parent(self, arg, &handle, &parent, &index) < 0) {
        return NULL;
    }
    store_start((RangeObject *)self, handle, parent, index);
    store_end((RangeObject *)self, handle, parent, index + 1);
    Py_RETURN_NONE;
}

static PyObject *range_select_node_contents(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *node;
    if (node_arg(self, arg, &handle, &node) < 0) {
        return NULL;
    }
    if (node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "cannot select the contents of a doctype");
        return NULL;
    }
    store_start((RangeObject *)self, handle, node, 0);
    store_end((RangeObject *)self, handle, node, node_length(node));
    Py_RETURN_NONE;
}

static PyObject *range_collapse(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"to_start", NULL};
    int to_start = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|p:collapse", keywords, &to_start)) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (to_start) {
        store_end(range, range->start_handle, range->start_node, range->start_offset);
    } else {
        store_start(range, range->end_handle, range->end_node, range->end_offset);
    }
    Py_RETURN_NONE;
}

/* --- comparison --- */

static PyObject *range_compare_boundary_points(PyObject *self, PyObject *args) {
    int how;
    PyObject *source;
    module_state *state = state_of(self);
    if (!PyArg_ParseTuple(args, "iO!", &how, (PyTypeObject *)state->range_type, &source)) {
        return NULL;
    }
    if (how < 0 || how > 3) {
        PyErr_SetString(PyExc_ValueError, "how must be one of START_TO_START, START_TO_END, END_TO_END, END_TO_START");
        return NULL;
    }
    RangeObject *left = (RangeObject *)self;
    RangeObject *right = (RangeObject *)source;
    if (!same_root(left->start_handle, left->start_node, right->start_handle, right->start_node)) {
        PyErr_SetString(PyExc_ValueError, "the two ranges are in different trees");
        return NULL;
    }
    th_node *left_node;
    Py_ssize_t left_offset;
    th_node *right_node;
    Py_ssize_t right_offset;
    if (how == 0) {
        left_node = left->start_node;
        left_offset = left->start_offset;
        right_node = right->start_node;
        right_offset = right->start_offset;
    } else if (how == 1) {
        left_node = left->end_node;
        left_offset = left->end_offset;
        right_node = right->start_node;
        right_offset = right->start_offset;
    } else if (how == 2) {
        left_node = left->end_node;
        left_offset = left->end_offset;
        right_node = right->end_node;
        right_offset = right->end_offset;
    } else {
        left_node = left->start_node;
        left_offset = left->start_offset;
        right_node = right->end_node;
        right_offset = right->end_offset;
    }
    return PyLong_FromLong(bp_compare(left_node, left_offset, right_node, right_offset));
}

/* Parse and validate a (node, offset) point argument against the range's own tree. Returns 0, or
   -1 with an exception; *outside is set true when the point is in another tree (for the callers
   that treat that as a plain false rather than an error). */
static int point_arg(PyObject *self, PyObject *args, th_node **node, Py_ssize_t *offset, int *outside) {
    PyObject *container;
    if (!PyArg_ParseTuple(args, "On", &container, offset)) {
        return -1;
    }
    PyObject *handle;
    if (node_arg(self, container, &handle, node) < 0) {
        return -1;
    }
    RangeObject *range = (RangeObject *)self;
    *outside = !same_root(handle, *node, range->start_handle, range->start_node);
    return 0;
}

static PyObject *range_compare_point(PyObject *self, PyObject *args) {
    th_node *node;
    Py_ssize_t offset;
    int outside;
    if (point_arg(self, args, &node, &offset, &outside) < 0) {
        return NULL;
    }
    if (outside) {
        PyErr_SetString(PyExc_ValueError, "the point is in a different tree");
        return NULL;
    }
    if (validate_boundary(node, offset) < 0) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (bp_compare(node, offset, range->start_node, range->start_offset) < 0) {
        return PyLong_FromLong(-1);
    }
    if (bp_compare(node, offset, range->end_node, range->end_offset) > 0) {
        return PyLong_FromLong(1);
    }
    return PyLong_FromLong(0);
}

static PyObject *range_is_point_in_range(PyObject *self, PyObject *args) {
    th_node *node;
    Py_ssize_t offset;
    int outside;
    if (point_arg(self, args, &node, &offset, &outside) < 0) {
        return NULL;
    }
    if (outside) {
        Py_RETURN_FALSE;
    }
    if (validate_boundary(node, offset) < 0) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (bp_compare(node, offset, range->start_node, range->start_offset) < 0 ||
        bp_compare(node, offset, range->end_node, range->end_offset) > 0) {
        Py_RETURN_FALSE;
    }
    Py_RETURN_TRUE;
}

static PyObject *range_intersects_node(PyObject *self, PyObject *arg) {
    PyObject *handle;
    th_node *node;
    if (node_arg(self, arg, &handle, &node) < 0) {
        return NULL;
    }
    RangeObject *range = (RangeObject *)self;
    if (!same_root(handle, node, range->start_handle, range->start_node)) {
        Py_RETURN_FALSE;
    }
    if (node->parent == NULL) {
        Py_RETURN_TRUE;
    }
    Py_ssize_t offset = node_index(node);
    if (bp_compare(node->parent, offset, range->end_node, range->end_offset) < 0 &&
        bp_compare(node->parent, offset + 1, range->start_node, range->start_offset) > 0) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

/* --- content operations --- */

/* Run the extract algorithm and collapse the range; return the fragment node (already wrapped) or,
   when discard is set (deleteContents), None. */
static PyObject *extract_or_delete(PyObject *self, int discard) {
    RangeObject *range = (RangeObject *)self;
    th_tree *tree = ((HandleObject *)range->start_handle)->tree;
    module_state *state = state_of(self);
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    handle_drop_index(range->start_handle);
    th_node *new_node;
    Py_ssize_t new_offset;
    int collapsed = range_is_collapsed(range);
    if (!collapsed) {
        collapse_point(range->start_node, range->start_offset, range->end_node, &new_node, &new_offset);
    }
    th_node *fragment = do_extract(tree, range->start_node, range->start_offset, range->end_node, range->end_offset);
    if (fragment != NULL) {
        if (!collapsed) {
            store_start(range, range->start_handle, new_node, new_offset);
            store_end(range, range->start_handle, new_node, new_offset);
        }
        result = discard ? Py_NewRef(Py_None) : node_wrap(state, range->start_handle, fragment);
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *range_extract_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return extract_or_delete(self, 0);
}

static PyObject *range_delete_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return extract_or_delete(self, 1);
}

static PyObject *range_clone_contents(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    RangeObject *range = (RangeObject *)self;
    th_tree *tree = ((HandleObject *)range->start_handle)->tree;
    module_state *state = state_of(self);
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    th_node *fragment = do_clone(tree, range->start_node, range->start_offset, range->end_node, range->end_offset);
    if (fragment != NULL) {
        result = node_wrap(state, range->start_handle, fragment);
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

/* Detach a node argument from wherever it lives and return the th_node to link into the range's
   tree (a same-tree node moves in place; a foreign node is copied and its wrapper re-pointed).
   NULL with an exception on a Document, a cycle, or allocation failure. */
static th_node *adopt(RangeObject *range, PyObject *child_obj) {
    NodeObject *child = (NodeObject *)child_obj;
    if (child->node->type == TH_NODE_DOCUMENT) {
        PyErr_SetString(PyExc_ValueError, "a Document cannot be inserted");
        return NULL;
    }
    th_tree *dest_tree = ((HandleObject *)range->start_handle)->tree;
    th_tree *child_tree = ((HandleObject *)child->handle)->tree;
    if (dest_tree == child_tree) {
        if (th_node_contains(child->node, range->start_node)) {
            PyErr_SetString(PyExc_ValueError, "cannot insert a node into its own subtree");
            return NULL;
        }
        th_node_remove(child->node);
        return child->node;
    }
    th_node *copy = th_tree_copy_node(dest_tree, child_tree, child->node);
    if (copy == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_BEGIN_CRITICAL_SECTION(child->handle);
    handle_drop_index(child->handle);
    th_node_remove(child->node);
    Py_END_CRITICAL_SECTION();
    Py_SETREF(child->handle, Py_NewRef(range->start_handle));
    child->node = copy;
    return copy;
}

/* The DOM insert algorithm; returns the linked th_node in the range's tree, or NULL on error. The
   caller holds the range handle's critical section. */
static th_node *insert_core(RangeObject *range, PyObject *node_obj) {
    if (!is_node(node_obj, state_of((PyObject *)range))) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return NULL;
    }
    th_node *start_node = range->start_node;
    Py_ssize_t start_offset = range->start_offset;
    th_node *incoming = ((NodeObject *)node_obj)->node;
    int start_text_like = start_node->type == TH_NODE_TEXT || start_node->type == TH_NODE_CDATA;
    if (start_node->type == TH_NODE_COMMENT || start_node->type == TH_NODE_PI ||
        (start_text_like && start_node->parent == NULL) || incoming == start_node) {
        PyErr_SetString(PyExc_ValueError, "cannot insert at this boundary point");
        return NULL;
    }
    th_node *reference;
    if (start_text_like) {
        reference = start_node;
    } else {
        reference = start_node->first_child;
        for (Py_ssize_t index = 0; index < start_offset && reference != NULL; index++) {
            reference = reference->next_sibling;
        }
    }
    th_node *parent = reference == NULL ? start_node : reference->parent;
    handle_drop_index(range->start_handle);
    if (start_text_like) {
        reference = split_data_node(((HandleObject *)range->start_handle)->tree, start_node, start_offset);
        if (reference == NULL) { /* GCOVR_EXCL_BR_LINE: split only fails on OOM */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (incoming == reference) {
        reference = reference->next_sibling;
    }
    th_node *linked = adopt(range, node_obj);
    if (linked == NULL) {
        return NULL;
    }
    Py_ssize_t new_offset = reference == NULL ? node_length(parent) : node_index(reference);
    new_offset += linked->type == TH_NODE_CONTENT ? node_length(linked) : 1;
    th_node_insert_before(parent, linked, reference);
    if (range_is_collapsed(range)) {
        store_end(range, range->start_handle, parent, new_offset);
    }
    return linked;
}

static PyObject *range_insert_node(PyObject *self, PyObject *node_obj) {
    RangeObject *range = (RangeObject *)self;
    th_node *linked;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    linked = insert_core(range, node_obj);
    Py_END_CRITICAL_SECTION();
    if (linked == NULL) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* Whether the range partially contains any non-Text node (surroundContents forbids it). */
static int has_partial_non_text(RangeObject *range) {
    th_node *common = common_ancestor(range->start_node, range->end_node);
    th_node *node = common;
    while (node != NULL) {
        if (node->type != TH_NODE_TEXT && is_partially_contained(node, range->start_node, range->end_node)) {
            return 1;
        }
        node = preorder_next(node, common);
    }
    return 0;
}

static PyObject *range_surround_contents(PyObject *self, PyObject *new_parent) {
    RangeObject *range = (RangeObject *)self;
    module_state *state = state_of(self);
    if (!is_node(new_parent, state)) {
        PyErr_SetString(PyExc_TypeError, "expected a node");
        return NULL;
    }
    th_node *parent_node = ((NodeObject *)new_parent)->node;
    if (parent_node->type == TH_NODE_DOCUMENT || parent_node->type == TH_NODE_DOCTYPE ||
        parent_node->type == TH_NODE_CONTENT) {
        PyErr_SetString(PyExc_ValueError, "the wrapper cannot be a document, doctype, or fragment");
        return NULL;
    }
    PyObject *result = NULL;
    th_tree *tree = ((HandleObject *)range->start_handle)->tree;
    Py_BEGIN_CRITICAL_SECTION(range->start_handle);
    if (has_partial_non_text(range)) {
        PyErr_SetString(PyExc_ValueError, "the range partially contains a non-Text node");
    } else {
        handle_drop_index(range->start_handle);
        th_node *new_node;
        Py_ssize_t new_offset;
        int collapsed = range_is_collapsed(range);
        if (!collapsed) {
            collapse_point(range->start_node, range->start_offset, range->end_node, &new_node, &new_offset);
        }
        th_node *fragment =
            do_extract(tree, range->start_node, range->start_offset, range->end_node, range->end_offset);
        if (fragment != NULL) {
            if (!collapsed) {
                store_start(range, range->start_handle, new_node, new_offset);
                store_end(range, range->start_handle, new_node, new_offset);
            }
            while (parent_node->first_child != NULL) {
                th_node_remove(parent_node->first_child);
            }
            th_node *linked = insert_core(range, new_parent);
            if (linked != NULL) {
                adopt_fragment_children(linked, fragment);
                store_start(range, range->start_handle, linked->parent, node_index(linked));
                store_end(range, range->start_handle, linked->parent, node_index(linked) + 1);
                result = Py_NewRef(new_parent);
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *range_clone_range(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    RangeObject *range = (RangeObject *)self;
    PyTypeObject *type = Py_TYPE(self);
    RangeObject *copy = (RangeObject *)type->tp_alloc(type, 0);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    copy->start_handle = Py_NewRef(range->start_handle);
    copy->end_handle = Py_NewRef(range->end_handle);
    copy->start_node = range->start_node;
    copy->end_node = range->end_node;
    copy->start_offset = range->start_offset;
    copy->end_offset = range->end_offset;
    return (PyObject *)copy;
}

static PyObject *range_repr(PyObject *self) {
    RangeObject *range = (RangeObject *)self;
    module_state *state = state_of(self);
    PyObject *start = node_wrap(state, range->start_handle, range->start_node);
    PyObject *end = node_wrap(state, range->end_handle, range->end_node);
    if (start == NULL || end == NULL) { /* GCOVR_EXCL_BR_LINE: node_wrap only fails on OOM */
        Py_XDECREF(start);              /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(end);                /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result =
        th_str_format("<Range start=(%R, %zd) end=(%R, %zd)>", start, range->start_offset, end, range->end_offset);
    Py_DECREF(start);
    Py_DECREF(end);
    return result;
}

/* --- StaticRange --- */

static PyObject *static_range_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"start_container", "start_offset", "end_container", "end_offset", NULL};
    PyObject *start_container;
    Py_ssize_t start_offset;
    PyObject *end_container;
    Py_ssize_t end_offset;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OnOn:StaticRange", keywords, &start_container, &start_offset,
                                     &end_container, &end_offset)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    if (!is_node(start_container, state) || !is_node(end_container, state)) {
        PyErr_SetString(PyExc_TypeError, "the containers must be nodes");
        return NULL;
    }
    th_node *start_node = ((NodeObject *)start_container)->node;
    th_node *end_node = ((NodeObject *)end_container)->node;
    if (start_node->type == TH_NODE_DOCTYPE || end_node->type == TH_NODE_DOCTYPE) {
        PyErr_SetString(PyExc_ValueError, "a StaticRange boundary cannot be a doctype");
        return NULL;
    }
    RangeObject *self = (RangeObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->start_handle = Py_NewRef(((NodeObject *)start_container)->handle);
    self->end_handle = Py_NewRef(((NodeObject *)end_container)->handle);
    self->start_node = start_node;
    self->end_node = end_node;
    self->start_offset = start_offset;
    self->end_offset = end_offset;
    return (PyObject *)self;
}

/* --- type registration --- */

static PyGetSetDef abstract_range_getset[] = {
    {"start_container", get_start_container, NULL, "the node the range starts in", NULL},
    {"start_offset", get_start_offset, NULL, "the offset of the start boundary within its container", NULL},
    {"end_container", get_end_container, NULL, "the node the range ends in", NULL},
    {"end_offset", get_end_offset, NULL, "the offset of the end boundary within its container", NULL},
    {"collapsed", get_collapsed, NULL, "whether the two boundary points coincide", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyGetSetDef range_getset[] = {
    {"start_container", get_start_container, NULL, "the node the range starts in", NULL},
    {"start_offset", get_start_offset, NULL, "the offset of the start boundary within its container", NULL},
    {"end_container", get_end_container, NULL, "the node the range ends in", NULL},
    {"end_offset", get_end_offset, NULL, "the offset of the end boundary within its container", NULL},
    {"collapsed", get_collapsed, NULL, "whether the two boundary points coincide", NULL},
    {"common_ancestor_container", get_common_ancestor, NULL, "the deepest node containing both boundary points", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(set_start_doc, "set_start(container, offset, /)\n--\n\nSet the start boundary to (container, offset).");
PyDoc_STRVAR(set_end_doc, "set_end(container, offset, /)\n--\n\nSet the end boundary to (container, offset).");
PyDoc_STRVAR(set_start_before_doc, "set_start_before(node, /)\n--\n\nSet the start boundary just before node.");
PyDoc_STRVAR(set_start_after_doc, "set_start_after(node, /)\n--\n\nSet the start boundary just after node.");
PyDoc_STRVAR(set_end_before_doc, "set_end_before(node, /)\n--\n\nSet the end boundary just before node.");
PyDoc_STRVAR(set_end_after_doc, "set_end_after(node, /)\n--\n\nSet the end boundary just after node.");
PyDoc_STRVAR(select_node_doc, "select_node(node, /)\n--\n\nSet the range to select node itself.");
PyDoc_STRVAR(select_node_contents_doc, "select_node_contents(node, /)\n--\n\nSet the range to select node's children.");
PyDoc_STRVAR(collapse_doc, "collapse(to_start=False, /)\n--\n\nCollapse the range onto one of its boundaries.");
PyDoc_STRVAR(compare_boundary_points_doc,
             "compare_boundary_points(how, source_range, /)\n--\n\nCompare a boundary of this range with one of "
             "source_range; returns -1, 0, or 1.");
PyDoc_STRVAR(compare_point_doc,
             "compare_point(node, offset, /)\n--\n\nWhere (node, offset) lies relative to the range: -1, 0, or 1.");
PyDoc_STRVAR(is_point_in_range_doc,
             "is_point_in_range(node, offset, /)\n--\n\nWhether (node, offset) is within the range.");
PyDoc_STRVAR(intersects_node_doc, "intersects_node(node, /)\n--\n\nWhether node overlaps the range.");
PyDoc_STRVAR(clone_contents_doc, "clone_contents()\n--\n\nCopy the range's content into a new fragment.");
PyDoc_STRVAR(extract_contents_doc, "extract_contents()\n--\n\nMove the range's content into a new fragment.");
PyDoc_STRVAR(delete_contents_doc, "delete_contents()\n--\n\nRemove the range's content from the tree.");
PyDoc_STRVAR(insert_node_doc, "insert_node(node, /)\n--\n\nInsert node at the start of the range.");
PyDoc_STRVAR(surround_contents_doc, "surround_contents(new_parent, /)\n--\n\nWrap the range's content in new_parent.");
PyDoc_STRVAR(clone_range_doc, "clone_range()\n--\n\nReturn a new Range with the same boundary points.");

static PyMethodDef range_methods[] = {
    {"set_start", range_set_start, METH_VARARGS, set_start_doc},
    {"set_end", range_set_end, METH_VARARGS, set_end_doc},
    {"set_start_before", range_set_start_before, METH_O, set_start_before_doc},
    {"set_start_after", range_set_start_after, METH_O, set_start_after_doc},
    {"set_end_before", range_set_end_before, METH_O, set_end_before_doc},
    {"set_end_after", range_set_end_after, METH_O, set_end_after_doc},
    {"select_node", range_select_node, METH_O, select_node_doc},
    {"select_node_contents", range_select_node_contents, METH_O, select_node_contents_doc},
    {"collapse", (PyCFunction)(void (*)(void))range_collapse, METH_VARARGS | METH_KEYWORDS, collapse_doc},
    {"compare_boundary_points", range_compare_boundary_points, METH_VARARGS, compare_boundary_points_doc},
    {"compare_point", range_compare_point, METH_VARARGS, compare_point_doc},
    {"is_point_in_range", range_is_point_in_range, METH_VARARGS, is_point_in_range_doc},
    {"intersects_node", range_intersects_node, METH_O, intersects_node_doc},
    {"clone_contents", range_clone_contents, METH_NOARGS, clone_contents_doc},
    {"extract_contents", range_extract_contents, METH_NOARGS, extract_contents_doc},
    {"delete_contents", range_delete_contents, METH_NOARGS, delete_contents_doc},
    {"insert_node", range_insert_node, METH_O, insert_node_doc},
    {"surround_contents", range_surround_contents, METH_O, surround_contents_doc},
    {"clone_range", range_clone_range, METH_NOARGS, clone_range_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(range_doc, "A live DOM Range between two boundary points.\n\n"
                        ":param container: the node both boundaries start collapsed in.\n"
                        ":param offset: the shared start offset (default 0).");

static PyType_Slot range_slots[] = {
    {Py_tp_doc, (void *)range_doc},
    {Py_tp_new, range_new},
    {Py_tp_dealloc, range_dealloc},
    {Py_tp_repr, range_repr},
    {Py_tp_getset, range_getset},
    {Py_tp_methods, range_methods},
    {0, NULL},
};

static PyType_Spec range_spec = {
    .name = "turbohtml._html.Range",
    .basicsize = sizeof(RangeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = range_slots,
};

PyDoc_STRVAR(static_range_doc, "An immutable snapshot of two boundary points.\n\n"
                               ":param start_container: the node the range starts in.\n"
                               ":param start_offset: the start offset.\n"
                               ":param end_container: the node the range ends in.\n"
                               ":param end_offset: the end offset.");

static PyType_Slot static_range_slots[] = {
    {Py_tp_doc, (void *)static_range_doc},
    {Py_tp_new, static_range_new},
    {Py_tp_dealloc, range_dealloc},
    {Py_tp_getset, abstract_range_getset},
    {0, NULL},
};

static PyType_Spec static_range_spec = {
    .name = "turbohtml._html.StaticRange",
    .basicsize = sizeof(RangeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = static_range_slots,
};

/* Publish an integer class constant (the compare_boundary_points modes) on the Range type. */
static int add_int_constant(PyObject *type, const char *name, long value) {
    PyObject *number = PyLong_FromLong(value);
    if (number == NULL) { /* GCOVR_EXCL_BR_LINE: a small int always allocates */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = PyObject_SetAttrString(type, name, number);
    Py_DECREF(number);
    return rc;
}

int range_register(PyObject *module, module_state *state) {
    state->range_type = PyType_FromModuleAndSpec(module, &range_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->range_type == NULL ||                                     /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Range", state->range_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the four compare_boundary_points modes, mirrored from the DOM Range constants */
    if (add_int_constant(state->range_type, "START_TO_START", 0) < 0 || /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "START_TO_END", 1) < 0 ||   /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "END_TO_END", 2) < 0 ||     /* GCOVR_EXCL_BR_LINE: OOM only */
        add_int_constant(state->range_type, "END_TO_START", 3) < 0) {   /* GCOVR_EXCL_BR_LINE: OOM only */
        return -1;                                                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->static_range_type = PyType_FromModuleAndSpec(module, &static_range_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->static_range_type == NULL ||                                           /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "StaticRange", state->static_range_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                    /* GCOVR_EXCL_LINE: OOM path */
    }
    return 0;
}
