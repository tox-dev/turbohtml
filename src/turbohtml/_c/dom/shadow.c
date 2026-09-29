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
typedef union {
    th_node *node;
    PyObject *wrapper;
} nodevec_item;

typedef struct {
    nodevec_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} nodevec;

static void nodevec_push(nodevec *vec, th_node *node) {
    if (vec->failed) { /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    if (vec->len == vec->cap) {
        size_t cap, bytes;
        /* the requested length cannot overflow size_t, so the grow guard never trips */
        int fits = th_grow_cap((size_t)vec->len + 1, (size_t)vec->cap, 8, sizeof(nodevec_item), &cap, &bytes);
        if (!fits) {         /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: overflow-guard path */
            return;          /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        nodevec_item *items = PyMem_Realloc(vec->items, bytes);
        if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        vec->items = items;
        vec->cap = (Py_ssize_t)cap;
    }
    vec->items[vec->len++].node = node;
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

static th_node *find_named_slot(th_node *shadow, const Py_UCS4 *want, Py_ssize_t want_len);

static th_node *find_slot(th_tree *tree, th_node *slottable) {
    th_node *parent = slottable->parent;
    if (parent == NULL) {
        return NULL;
    }
    th_node *shadow = th_element_shadow_root(tree, parent);
    if (shadow == NULL) {
        return NULL;
    }
    if (th_shadow_mode(shadow) != 0) {
        return NULL;
    }
    const Py_UCS4 *want = NULL;
    Py_ssize_t want_len = 0;
    if (slottable->type == TH_NODE_ELEMENT) {
        named_value(slottable, TH_ATTR_SLOT, &want, &want_len);
    }
    return find_named_slot(shadow, want, want_len);
}

static th_node *find_named_slot(th_node *shadow, const Py_UCS4 *want, Py_ssize_t want_len) {
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
    th_node *first_slottable = th_shadow_host(tree, root)->first_child;
    while (first_slottable != NULL && !is_slottable(first_slottable)) {
        first_slottable = first_slottable->next_sibling;
    }
    if (first_slottable == NULL) {
        return;
    }
    const Py_UCS4 *name = NULL;
    Py_ssize_t name_len = 0;
    named_value(slot, TH_ATTR_NAME, &name, &name_len);
    int checked_slot = 0;
    for (th_node *child = first_slottable; child != NULL; child = child->next_sibling) {
        if (!is_slottable(child)) {
            continue;
        }
        const Py_UCS4 *want = NULL;
        Py_ssize_t want_len = 0;
        if (child->type == TH_NODE_ELEMENT) {
            named_value(child, TH_ATTR_SLOT, &want, &want_len);
        }
        if (runs_equal(name, name_len, want, want_len)) {
            if (!checked_slot) {
                if (find_named_slot(root, name, name_len) != slot) {
                    return;
                }
                checked_slot = 1;
            }
            nodevec_push(vec, child);
        }
    }
}

typedef struct {
    const Py_UCS4 *name;
    Py_ssize_t name_len;
    th_node *slot;
    nodevec assigned;
} slot_bucket;

typedef struct {
    th_node *root;
    slot_bucket *buckets;
    size_t cap;
} slot_index;

static void slot_index_clear(slot_index *index) {
    for (size_t position = 0; position < index->cap; position++) {
        PyMem_Free(index->buckets[position].assigned.items);
    }
    PyMem_Free(index->buckets);
    *index = (slot_index){0};
}

static slot_bucket *slot_index_bucket(slot_index *index, const Py_UCS4 *name, Py_ssize_t name_len) {
    size_t hash = 2166136261U;
    for (Py_ssize_t position = 0; position < name_len; position++) {
        hash = (hash ^ name[position]) * 16777619U;
    }
    size_t position = hash & (index->cap - 1);
    while (index->buckets[position].slot != NULL &&
           !runs_equal(name, name_len, index->buckets[position].name, index->buckets[position].name_len)) {
        position = (position + 1) & (index->cap - 1);
    }
    return &index->buckets[position];
}

static int slot_index_build(th_tree *tree, slot_index *index) {
    size_t count = 0;
    for (th_node *node = index->root->first_child; node != NULL; node = preorder_next(node, index->root)) {
        count += is_slot(node);
    }
    size_t cap = 8;
    while (count >= cap / 2) {
        if (cap > SIZE_MAX / 2 / sizeof(slot_bucket)) { /* GCOVR_EXCL_BR_LINE: unforceable allocation overflow */
            return -1;                                  /* GCOVR_EXCL_LINE: allocation-overflow path */
        } /* GCOVR_EXCL_LINE: closes the allocation-overflow-only branch */
        cap *= 2;
    }
    index->buckets = PyMem_Calloc(cap, sizeof(slot_bucket));
    if (index->buckets == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    index->cap = cap;
    for (th_node *node = index->root->first_child; node != NULL; node = preorder_next(node, index->root)) {
        if (is_slot(node)) {
            const Py_UCS4 *name = NULL;
            Py_ssize_t name_len = 0;
            named_value(node, TH_ATTR_NAME, &name, &name_len);
            slot_bucket *bucket = slot_index_bucket(index, name, name_len);
            if (bucket->slot == NULL) {
                bucket->name = name;
                bucket->name_len = name_len;
                bucket->slot = node;
            }
        }
    }
    for (th_node *child = th_shadow_host(tree, index->root)->first_child; child != NULL; child = child->next_sibling) {
        if (!is_slottable(child)) {
            continue;
        }
        const Py_UCS4 *name = NULL;
        Py_ssize_t name_len = 0;
        if (child->type == TH_NODE_ELEMENT) {
            named_value(child, TH_ATTR_SLOT, &name, &name_len);
        }
        slot_bucket *bucket = slot_index_bucket(index, name, name_len);
        if (bucket->slot != NULL) {
            nodevec_push(&bucket->assigned, child);
            if (bucket->assigned.failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
                return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
            } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        }
    }
    return 0;
}

static void collect_slotables_indexed(th_tree *tree, th_node *slot, th_node *root, nodevec *vec, slot_index *index) {
    if (index == NULL || th_shadow_host(tree, root)->first_child == NULL) {
        collect_slotables(tree, slot, vec);
        return;
    }
    if (index->root != root) {
        slot_index_clear(index);
        index->root = root;
        collect_slotables(tree, slot, vec);
        return;
    }
    if (index->buckets == NULL) {
        /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
        if (slot_index_build(tree, index) < 0) {
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        /* GCOVR_EXCL_BR_STOP */
    }
    const Py_UCS4 *name = NULL;
    Py_ssize_t name_len = 0;
    named_value(slot, TH_ATTR_NAME, &name, &name_len);
    slot_bucket *bucket = slot_index_bucket(index, name, name_len);
    if (bucket->slot == slot) {
        for (Py_ssize_t position = 0; position < bucket->assigned.len; position++) {
            nodevec_push(vec, bucket->assigned.items[position].node);
        }
    }
}

/* Collect the assigned slotables or fallback children that one slot contributes. */
static void collect_flattened_candidates(th_tree *tree, th_node *slot, nodevec *assigned, slot_index *index) {
    th_node *root = node_root(slot);
    if (!th_node_is_shadow_root(root)) {
        return;
    }
    collect_slotables_indexed(tree, slot, root, assigned, index);
    if (assigned->failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
        return;             /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    if (assigned->len == 0) {
        for (th_node *child = slot->first_child; child != NULL; child = child->next_sibling) {
            if (is_slottable(child)) {
                nodevec_push(assigned, child);
            }
        }
    }
}

/* Collect flattened slotables depth first. pending is an explicit checked stack, so nested fallback slots do not
   consume the C stack. (DOM: find flattened slotables.) */
static void collect_flattened(th_tree *tree, th_node *slot, nodevec *vec, slot_index *slots) {
    nodevec pending = {0};
    nodevec assigned = {0};
    collect_flattened_candidates(tree, slot, &assigned, slots);
    for (Py_ssize_t index = assigned.len; index > 0; index--) {
        nodevec_push(&pending, assigned.items[index - 1].node);
    }
    if (assigned.failed) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        pending.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    while (pending.len > 0) {
        if (pending.failed || vec->failed) { /* GCOVR_EXCL_BR_LINE: nodevec fails only on allocation failure */
            break;                           /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        th_node *node = pending.items[--pending.len].node;
        if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
            assigned.len = 0;
            collect_flattened_candidates(tree, node, &assigned, slots);
            for (Py_ssize_t index = assigned.len; index > 0; index--) {
                nodevec_push(&pending, assigned.items[index - 1].node);
            }
            if (assigned.failed) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                pending.failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        } else {
            nodevec_push(vec, node);
        }
    }
    if (pending.failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        vec->failed = 1;  /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
    PyMem_Free(assigned.items);
    PyMem_Free(pending.items);
}

/* Collect node's flattened-tree children: a shadow host descends into its shadow tree,
   a shadow slot yields its flattened slotables, and any child slot is replaced by its
   flattened slotables. Every other child passes through unchanged. */
static void collect_flattened_children(th_tree *tree, th_node *node, nodevec *vec) {
    if (is_slot(node) && th_node_is_shadow_root(node_root(node))) {
        collect_flattened(tree, node, vec, NULL);
        return;
    }
    th_node *shadow = th_element_shadow_root(tree, node);
    th_node *base = shadow != NULL ? shadow : node;
    slot_index slots = {0};
    for (th_node *child = base->first_child; child != NULL; child = child->next_sibling) {
        if (is_slot(child) && th_node_is_shadow_root(node_root(child))) {
            collect_flattened(tree, child, vec, &slots);
        } else {
            nodevec_push(vec, child);
        }
    }
    slot_index_clear(&slots);
}

static PyObject *nodevec_to_list(nodevec *vec, module_state *state, PyObject *handle, int elements_only) {
    if (vec->failed) {           /* GCOVR_EXCL_BR_LINE: only set on an unforceable allocation failure */
        PyMem_Free(vec->items);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
#if PY_VERSION_HEX >= 0x030C0000 && !defined(Py_GIL_DISABLED) && !defined(PYPY_VERSION)
    /* CPython 3.12+ defers automatic GC until bytecode evaluation resumes. */
    if (!elements_only) {
        PyObject *list = PyList_New(vec->len);
        if (list == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyMem_Free(vec->items); /* GCOVR_EXCL_LINE */
            return NULL;            /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < vec->len; index++) {
            PyObject *wrapped = node_wrap_locked(state, handle, vec->items[index].node);
            if (wrapped == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(list);        /* GCOVR_EXCL_LINE */
                PyMem_Free(vec->items); /* GCOVR_EXCL_LINE */
                return NULL;            /* GCOVR_EXCL_LINE */
            }
            PyList_SET_ITEM(list, index, wrapped);
        }
        PyMem_Free(vec->items);
        return list;
    }
#endif
    /* List allocation can run GC callbacks that adopt the host into another arena. */
    Py_ssize_t count = 0;
    for (Py_ssize_t index = 0; index < vec->len; index++) {
#if PY_VERSION_HEX >= 0x030C0000 && !defined(Py_GIL_DISABLED) && !defined(PYPY_VERSION)
        if (vec->items[index].node->type != TH_NODE_ELEMENT) {
#else
        if (elements_only && vec->items[index].node->type != TH_NODE_ELEMENT) {
#endif
            continue;
        }
        PyObject *wrapped = node_wrap_locked(state, handle, vec->items[index].node);
        if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto error;        /* GCOVR_EXCL_LINE */
        }
        vec->items[count++].wrapper = wrapped;
    }
    PyObject *list = PyList_New(count);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto error;     /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyList_SET_ITEM(list, index, vec->items[index].wrapper);
    }
    PyMem_Free(vec->items);
    return list;
    /* GCOVR_EXCL_START: allocation failure */
error:
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_DECREF(vec->items[index].wrapper);
    }
    PyMem_Free(vec->items);
    return NULL;
    /* GCOVR_EXCL_STOP */
}

TH_NODE_API(, PyObject *, element_attach_shadow, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
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

TH_NODE_API(, PyObject *, element_get_shadow_root, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
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
        collect_flattened(tree, node->node, &vec, NULL);
    } else {
        collect_slotables(tree, node->node, &vec);
    }
    Py_END_CRITICAL_SECTION();
    return nodevec_to_list(&vec, state_of(self), node->handle, elements_only);
}

TH_NODE_API(, PyObject *, element_assigned_nodes, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    return slot_assigned(self, args, kwds, 0);
}

TH_NODE_API(, PyObject *, element_assigned_elements, (PyObject * self, PyObject *args, PyObject *kwds),
            (self, args, kwds), (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    return slot_assigned(self, args, kwds, 1);
}

TH_NODE_API(, PyObject *, node_get_assigned_slot, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    th_node *slot = NULL;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    if (is_slottable(node->node)) {
        slot = find_slot(tree, node->node);
    }
    Py_END_CRITICAL_SECTION();
    return node_wrap(state_of(self), node->handle, slot);
}

TH_NODE_API(, PyObject *, node_get_flattened_children, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
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
PyDoc_STRVAR(shadow_root_delegates_focus_doc,
             "whether the shadow root delegates focus, from a declarative shadow root's\n"
             "shadowrootdelegatesfocus attribute (always False otherwise)");
PyDoc_STRVAR(shadow_root_clonable_doc, "whether the shadow root is clonable, from a declarative shadow root's\n"
                                       "shadowrootclonable attribute (always False otherwise)");

TH_NODE_API(static, PyObject *, shadow_root_get_mode, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyUnicode_FromString(th_shadow_mode(((NodeObject *)self)->node) != 0 ? "closed" : "open");
}

TH_NODE_API(static, PyObject *, shadow_root_get_delegates_focus, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyBool_FromLong((((NodeObject *)self)->node->tag_flags & TH_SHADOW_DELEGATES_FOCUS) != 0);
}

TH_NODE_API(static, PyObject *, shadow_root_get_clonable, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
    return PyBool_FromLong((((NodeObject *)self)->node->tag_flags & TH_SHADOW_CLONABLE) != 0);
}

TH_NODE_API(static, PyObject *, shadow_root_get_host, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *Py_UNUSED(closure)), (NodeObject *)self, NULL) {
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
    {"delegates_focus", shadow_root_get_delegates_focus, NULL, shadow_root_delegates_focus_doc, NULL},
    {"clonable", shadow_root_get_clonable, NULL, shadow_root_clonable_doc, NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(shadow_root_set_inner_html_doc,
             "set_inner_html(html, /)\n--\n\n"
             "Replace the shadow tree's content by parsing html as a fragment, the way\n"
             "declarative shadow DOM populates a shadow root.\n\n"
             ":param html: the markup to parse and install as the shadow content.\n"
             ":raises TypeError: if html is not a str.");

TH_NODE_API(static, PyObject *, shadow_root_set_inner_html, (PyObject * self, PyObject *html), (self, html),
            (PyObject * self, PyObject *html), (NodeObject *)self,
            html != NULL && is_node(html, state_of(self)) ? (NodeObject *)html : NULL) {
    if (!PyUnicode_Check(html)) {
        PyErr_SetString(PyExc_TypeError, "html must be a str");
        return NULL;
    }
    int scripting = th_tree_scripting(tree_of(self));
    th_tree *fragment = th_tree_parse_fragment(PyUnicode_KIND(html), PyUnicode_DATA(html), PyUnicode_GET_LENGTH(html),
                                               "div", 3, 0, 0, scripting, 1);
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

PyDoc_STRVAR(shadow_root_append_doc, "append(child, /)\n--\n\n"
                                     "Add child as the last node of the shadow tree, moving a node from this tree\n"
                                     "or adopting one from another by copy, like Element.append. A DocumentFragment\n"
                                     "argument moves its children in and is left empty.");

static PyMethodDef shadow_root_methods[] = {
    /* listed again so the reference documents it on ShadowRoot as well as on DocumentFragment */
    {"append", node_append_child, METH_O, shadow_root_append_doc},
    {"set_inner_html", shadow_root_set_inner_html, METH_O, shadow_root_set_inner_html_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(shadow_root_doc, "A shadow root: the DocumentFragment rooting an element's shadow tree, created\n"
                              "by Element.attach_shadow. It is held off the light tree, so it never appears\n"
                              "among the host's children or in its serialization. Inserting it elsewhere moves\n"
                              "its children, as for any DocumentFragment, and leaves it attached to its host.");

static PyType_Slot shadow_root_slots[] = {
    {Py_tp_doc, (void *)shadow_root_doc},
    {Py_tp_getset, shadow_root_getset},
    {Py_tp_methods, shadow_root_methods},
    TH_SEALED_END,
};

PyDoc_STRVAR(document_fragment_append_doc,
             "append(child, /)\n--\n\n"
             "Add child as the last node of this fragment, moving a node from this tree or\n"
             "adopting one from another by copy, like Element.append. A DocumentFragment\n"
             "argument moves its children in and is left empty.\n\n"
             ":param child: the node to append.\n"
             ":raises TypeError: if child is not a node, or is a Document.\n"
             ":raises ValueError: if child contains this fragment (which would form a cycle), or\n"
             "    is a doctype.");

static PyMethodDef document_fragment_methods[] = {
    {"append", node_append_child, METH_O, document_fragment_append_doc},
    {NULL, NULL, 0, NULL},
};

/* DocumentFragment(): an empty fragment in its own tree, ready to collect nodes and be inserted as a batch. */
static PyObject *document_fragment_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, ":DocumentFragment", keywords)) {
        return NULL;
    }
    th_tree *tree = th_tree_new();
    th_node *fragment = tree == NULL ? NULL : th_tree_make_fragment(tree); /* GCOVR_EXCL_BR_LINE: OOM only */
    if (fragment == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(PyType_GetModuleState(type), tree, fragment);
}

PyDoc_STRVAR(document_fragment_doc,
             "DocumentFragment()\n--\n\n"
             "A parentless container of nodes (the DOM DocumentFragment). Inserting one with\n"
             "append, insert_before, insert_after, replace_with, extend, or Range.insert_node\n"
             "moves its children into place, in order, and leaves it empty. Range.extract_contents\n"
             "and Range.clone_contents return one; ShadowRoot is a DocumentFragment with a host.");

static PyType_Slot document_fragment_slots[] = {
    {Py_tp_doc, (void *)document_fragment_doc},
    {Py_tp_new, document_fragment_new},
    {Py_tp_methods, document_fragment_methods},
    {0, NULL},
};

PyType_Spec document_fragment_spec = {
    .name = "turbohtml._html.DocumentFragment",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    .slots = document_fragment_slots,
};

PyType_Spec shadow_root_spec = {
    .name = "turbohtml._html.ShadowRoot",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = shadow_root_slots,
};
