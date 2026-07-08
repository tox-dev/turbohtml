/* The Node tree's read surface: navigation, text and markup access, serialization, and the lazy iterators
   every traversal API hands back. */

#include "dom/nodes.h"

/* Borrow the (tree, node) a Python tree node wraps, for the in-C sanitizer in sanitize.c. Sets a TypeError and
   returns -1 if obj is not one of this module's tree nodes. */
int turbohtml_node_borrow(PyObject *module, PyObject *obj, th_tree **tree, th_node **node) {
    module_state *state = PyModule_GetState(module);
    if (!PyObject_TypeCheck(obj, (PyTypeObject *)state->node_type)) {
        PyErr_SetString(PyExc_TypeError, "expected a turbohtml element");
        return -1;
    }
    *node = ((NodeObject *)obj)->node;
    *tree = ((HandleObject *)((NodeObject *)obj)->handle)->tree;
    return 0;
}

/* The per-tree handle a Python node holds (borrowed), so links.c can take the
   critical section around its walk. */
PyObject *turbohtml_node_handle(PyObject *obj) {
    return ((NodeObject *)obj)->handle;
}

/* Wrap `node` as a Python node sharing `owner`'s handle and module state, so
   links.c can hand back live Element wrappers for the nodes it enumerates. */
PyObject *turbohtml_node_wrap_in(PyObject *owner, th_node *node) {
    return node_wrap(state_of(owner), ((NodeObject *)owner)->handle, node);
}

#ifndef Py_GIL_DISABLED
/* The pool caps the wrappers a burst of find_all()/select()/iteration may recycle
   without pinning unbounded memory afterwards. At sizeof(NodeObject) (32 bytes) the
   cap costs at most ~32 KiB resident per interpreter, and it covers a query result
   or transient walk of up to this many nodes; a larger result falls back to malloc
   for the surplus. */
#define NODE_FREELIST_MAX 1024

void th_node_freelist_clear(module_state *state) {
    /* GCOVR_EXCL_START
       The drain runs only from html_clear (the module m_clear slot) at interpreter finalization, and only
       when the pool is non-empty at that instant. Whether CPython reaches this with a populated pool is
       nondeterministic across interpreters -- some 3.10/3.11 macOS and Linux runs finalize with the pool
       already empty -- so the loop body cannot be covered portably; the OS reclaims the wrappers at process
       exit regardless. */
    while (state->node_freelist != NULL) {
        NodeObject *self = (NodeObject *)state->node_freelist;
        state->node_freelist = (PyObject *)self->node;
        Py_TYPE(self)->tp_free(self); /* the type ref was dropped on push; the types are still live here */
    }
    /* GCOVR_EXCL_STOP */
    state->node_freelist_len = 0;
}
#else
void th_node_freelist_clear(module_state *Py_UNUSED(state)) {
} /* the free-threaded build keeps no pool */
#endif

static void node_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((NodeObject *)self)->handle);
#ifndef Py_GIL_DISABLED
    /* Park the wrapper for reuse instead of freeing it, unless the pool is full.
       Every node type has basicsize sizeof(NodeObject) and none accept a subclass,
       so any node object fits a base-type reuse. */
    module_state *state = state_of(self);
    if (state->node_freelist_len < NODE_FREELIST_MAX) {
        ((NodeObject *)self)->node = (th_node *)state->node_freelist; /* stash the next link in the node field */
        state->node_freelist = self;
        state->node_freelist_len++;
        Py_DECREF(type); /* release this object's type ref; PyObject_Init re-takes it on revive */
        return;
    }
#endif
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *node_richcompare(PyObject *left, PyObject *right, int op) {
    module_state *state = state_of(left);
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!is_node(right, state)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    int equal = ((NodeObject *)left)->node == ((NodeObject *)right)->node;
    return PyBool_FromLong(op == Py_EQ ? equal : !equal);
}

static Py_hash_t node_hash(PyObject *self) {
    Py_hash_t hash = (Py_hash_t)(uintptr_t)((NodeObject *)self)->node;
    return hash == -1 ? -2 : hash; /* GCOVR_EXCL_BR_LINE: an arena pointer is never (Py_hash_t)-1 */
}

PyDoc_STRVAR(equals_doc, "equals(other, /)\n--\n\n"
                         "Test whether this node and other are structurally equal: the same node type,\n"
                         "and for an element the same tag (namespace-aware) and attributes (names and\n"
                         "values, order-independent per the DOM) with the same children compared in\n"
                         "order; for a Text/Comment/other leaf the same data. The two nodes may come\n"
                         "from different documents. This is the BeautifulSoup notion of tree equality.\n\n"
                         "Distinct from ``==``, which is node identity: two separate parses of the same\n"
                         "markup are ``==``-unequal but ``equals()``-equal.\n\n"
                         ":param other: the node to compare against.\n"
                         ":returns: whether the two subtrees are structurally equal.\n"
                         ":raises TypeError: if other is not a node.");

static PyObject *node_equals(PyObject *self, PyObject *other) {
    if (!is_node(other, state_of(self))) {
        PyErr_Format(PyExc_TypeError, "other must be a node, not %.80s", Py_TYPE(other)->tp_name);
        return NULL;
    }
    th_tree *left_tree = tree_of(self);
    th_node *left = ((NodeObject *)self)->node;
    th_tree *right_tree = tree_of(other);
    th_node *right = ((NodeObject *)other)->node;
    PyObject *left_handle = ((NodeObject *)self)->handle;
    PyObject *right_handle = ((NodeObject *)other)->handle;
    int equal;
    /* Both subtrees are walked read-only; hold each tree's lock so a concurrent mutate
       cannot rewire it mid-walk (a no-op on the GIL build). Nest the second section only
       when the nodes belong to different trees, matching adopt_into's cross-tree pattern. */
    Py_BEGIN_CRITICAL_SECTION(left_handle);
    if (right_handle == left_handle) {
        equal = th_node_equals(left_tree, left, right_tree, right);
    } else {
        Py_BEGIN_CRITICAL_SECTION(right_handle);
        equal = th_node_equals(left_tree, left, right_tree, right);
        Py_END_CRITICAL_SECTION();
    }
    Py_END_CRITICAL_SECTION();
    return PyBool_FromLong(equal);
}

static PyObject *walker_new(module_state *state, PyObject *handle, th_node *start, th_node *root, int mode) {
    PyTypeObject *type = (PyTypeObject *)state->walker_type;
    WalkerObject *self = (WalkerObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(handle);
    self->root = root;
    self->current = start;
    self->mode = mode;
    return (PyObject *)self;
}

static void walker_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((WalkerObject *)self)->handle);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *walker_next(PyObject *self) {
    WalkerObject *walker = (WalkerObject *)self;
    if (walker->current == NULL) {
        return NULL;
    }
    th_node *node = walker->current;
    switch (walker->mode) {
    case WALK_ANCESTORS:
        walker->current = node->parent;
        break;
    case WALK_NEXT_SIBLINGS:
        walker->current = node->next_sibling;
        break;
    case WALK_PREVIOUS_SIBLINGS:
        walker->current = node->prev_sibling;
        break;
    case WALK_PRECEDING:
        walker->current = preceding_skip(previous_element(node), walker->root);
        break;
    default: /* WALK_DESCENDANTS, and the following axis bounded by a NULL root */
        walker->current = preorder_next(node, walker->root);
        break;
    }
    return node_wrap(state_of(self), walker->handle, node);
}

static PyType_Slot walker_slots[] = {
    {Py_tp_dealloc, walker_dealloc},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, walker_next},
    {0, NULL},
};

PyType_Spec walker_spec = {
    .name = "turbohtml._html._NodeIterator",
    .basicsize = sizeof(WalkerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = walker_slots,
};

static PyObject *string_walker_new(module_state *state, PyObject *handle, th_node *node, int strip) {
    PyTypeObject *type = (PyTypeObject *)state->string_walker_type;
    StringWalkerObject *self = (StringWalkerObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(handle);
    self->root = node;
    self->current = node->first_child;
    self->strip = strip;
    return (PyObject *)self;
}

static void string_walker_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((StringWalkerObject *)self)->handle);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *string_walker_next(PyObject *self) {
    StringWalkerObject *walker = (StringWalkerObject *)self;
    th_tree *tree = ((HandleObject *)walker->handle)->tree;
    while (walker->current != NULL) {
        th_node *node = walker->current;
        walker->current = preorder_next(node, walker->root);
        if (node->type != TH_NODE_TEXT) {
            continue;
        }
        Py_ssize_t len;
        Py_UCS4 *buf = th_node_data(tree, node, &len);
        if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t start = 0;
        Py_ssize_t stop = len;
        if (walker->strip) {
            while (start < stop && is_space(buf[start])) {
                start++;
            }
            while (stop > start && is_space(buf[stop - 1])) {
                stop--;
            }
            if (stop == start) {
                PyMem_Free(buf);
                continue;
            }
        }
        PyObject *text = ucs4_to_str(buf + start, stop - start);
        PyMem_Free(buf);
        return text;
    }
    return NULL;
}

static PyType_Slot string_walker_slots[] = {
    {Py_tp_dealloc, string_walker_dealloc},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, string_walker_next},
    {0, NULL},
};

PyType_Spec string_walker_spec = {
    .name = "turbohtml._html._StringIterator",
    .basicsize = sizeof(StringWalkerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = string_walker_slots,
};

/* The iterator serialize_iter() hands back: it holds the resolved output options and
   the walk's resume point, and each __next__ drives one bounded chunk of the
   subtree. opts.charset points at the static "utf-8", and layout (an Indent, when
   pretty) keeps the buffer indent points into alive; root stays valid because handle
   pins the tree. Mutating the tree mid-iteration invalidates the cursor, the same
   hazard as the tree's other node iterators. */
static PyObject *serialize_iter_new(module_state *state, PyObject *handle, th_node *root, const th_serialize_opts *opts,
                                    const Py_UCS4 *indent, Py_ssize_t indent_len, PyObject *layout) {
    PyTypeObject *type = (PyTypeObject *)state->serialize_iter_type;
    SerializeIterObject *self = (SerializeIterObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(handle);
    self->layout = Py_XNewRef(layout);
    self->root = root;
    self->opts = *opts;
    self->cursor.node = root;
    self->cursor.depth = 0;
    self->indent = indent;
    self->indent_len = indent_len;
    return (PyObject *)self;
}

static void serialize_iter_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((SerializeIterObject *)self)->handle);
    Py_XDECREF(((SerializeIterObject *)self)->layout);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *serialize_iter_next(PyObject *self) {
    SerializeIterObject *iter = (SerializeIterObject *)self;
    if (iter->cursor.node == NULL) {
        return NULL; /* the walk is exhausted: raise StopIteration */
    }
    th_tree *tree = ((HandleObject *)iter->handle)->tree;
    Py_ssize_t out_len;
    Py_UCS4 *data;
    /* the chunk walks live tree nodes; hold the per-tree lock so a concurrent mutate
       cannot rewire them mid-chunk (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(iter->handle);
    data =
        th_node_serialize_chunk(tree, iter->root, &iter->opts, iter->indent, iter->indent_len, &iter->cursor, &out_len);
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (out_len == 0) {
        /* an empty subtree finishes without ever yielding, so no trailing "" chunk;
           a non-empty walk only reaches out_len 0 once the cursor has run out */
        PyMem_Free(data);
        return NULL;
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

static PyType_Slot serialize_iter_slots[] = {
    {Py_tp_dealloc, serialize_iter_dealloc},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, serialize_iter_next},
    {0, NULL},
};

PyType_Spec serialize_iter_spec = {
    .name = "turbohtml._html._SerializeIterator",
    .basicsize = sizeof(SerializeIterObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = serialize_iter_slots,
};

static PyObject *node_get_parent(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return node_wrap(state_of(self), node->handle, node->node->parent);
}

static PyObject *node_get_next_sibling(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return node_wrap(state_of(self), node->handle, node->node->next_sibling);
}

static PyObject *node_get_previous_sibling(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return node_wrap(state_of(self), node->handle, node->node->prev_sibling);
}

static PyObject *node_get_source_line(PyObject *self, void *Py_UNUSED(closure)) {
    Py_ssize_t line, col;
    if (th_node_source_position(tree_of(self), ((NodeObject *)self)->node, &line, &col)) {
        return PyLong_FromSsize_t(line);
    }
    Py_RETURN_NONE;
}

static PyObject *node_get_source_col(PyObject *self, void *Py_UNUSED(closure)) {
    Py_ssize_t line, col;
    if (th_node_source_position(tree_of(self), ((NodeObject *)self)->node, &line, &col)) {
        return PyLong_FromSsize_t(col);
    }
    Py_RETURN_NONE;
}

static PyObject *node_get_position(PyObject *self, void *Py_UNUSED(closure)) {
    Py_ssize_t line, col;
    if (th_node_source_position(tree_of(self), ((NodeObject *)self)->node, &line, &col)) {
        return Py_BuildValue("(nn)", line, col);
    }
    Py_RETURN_NONE;
}

static PyObject *make_source_span(module_state *state, const th_src_span *span) {
    return PyObject_CallFunction(state->source_span_type, "nnnnnn", span->start_line, span->start_col,
                                 span->start_offset, span->end_line, span->end_col, span->end_offset);
}

static PyObject *node_get_source_location(PyObject *self, void *Py_UNUSED(closure)) {
    th_tree *tree = tree_of(self);
    const th_src_loc *loc = th_node_source_location(tree, ((NodeObject *)self)->node);
    if (loc == NULL) {
        Py_RETURN_NONE;
    }
    module_state *state = state_of(self);
    PyObject *start_tag = make_source_span(state, &loc->start_tag);
    PyObject *end_tag = loc->has_end_tag ? make_source_span(state, &loc->end_tag) : Py_NewRef(Py_None);
    PyObject *attrs = PyDict_New();
    if (start_tag == NULL || end_tag == NULL || attrs == NULL) { /* GCOVR_EXCL_BR_LINE: alloc failure */
        Py_XDECREF(start_tag);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(end_tag);                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(attrs);                                       /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < loc->attr_count; index++) {
        Py_ssize_t name_len;
        const char *bytes = th_attr_name(tree, loc->attrs[index].name_atom, &name_len);
        PyObject *name = PyUnicode_DecodeUTF8(bytes, name_len, "strict");
        PyObject *span = make_source_span(state, &loc->attrs[index].span);
        /* every branch here is an unreachable allocation failure */
        if (name == NULL || span == NULL || PyDict_SetItem(attrs, name, span) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(name);     /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(span);     /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(start_tag); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(end_tag);   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(attrs);     /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(name);
        Py_DECREF(span);
    }
    return PyObject_CallFunction(state->source_location_type, "NNN", start_tag, end_tag, attrs);
}

static PyObject *node_children_tuple(PyObject *self) {
    NodeObject *node = (NodeObject *)self;
    module_state *state = state_of(self);
    Py_ssize_t count = 0;
    for (th_node *child = node->node->first_child; child != NULL; child = child->next_sibling) {
        count++;
    }
    PyObject *tuple = PyTuple_New(count);
    if (tuple == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t index = 0;
    for (th_node *child = node->node->first_child; child != NULL; child = child->next_sibling) {
        PyObject *wrapped = node_wrap(state, node->handle, child);
        if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(tuple);  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(tuple, index++, wrapped);
    }
    return tuple;
}

static PyObject *node_get_children(PyObject *self, void *Py_UNUSED(closure)) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle); /* walks the child list */
    result = node_children_tuple(self);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *node_get_descendants(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return walker_new(state_of(self), node->handle, node->node->first_child, node->node, WALK_DESCENDANTS);
}

static PyObject *node_get_ancestors(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return walker_new(state_of(self), node->handle, node->node->parent, NULL, WALK_ANCESTORS);
}

static PyObject *node_get_next_siblings(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return walker_new(state_of(self), node->handle, node->node->next_sibling, NULL, WALK_NEXT_SIBLINGS);
}

static PyObject *node_get_previous_siblings(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return walker_new(state_of(self), node->handle, node->node->prev_sibling, NULL, WALK_PREVIOUS_SIBLINGS);
}

static PyObject *node_get_following(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return walker_new(state_of(self), node->handle, subtree_next(node->node), NULL, WALK_DESCENDANTS);
}

static PyObject *node_get_preceding(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    th_node *start = preceding_skip(previous_element(node->node), node->node);
    return walker_new(state_of(self), node->handle, start, node->node, WALK_PRECEDING);
}

static PyObject *node_get_strings(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return string_walker_new(state_of(self), node->handle, node->node, 0);
}

static PyObject *node_get_stripped_strings(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    return string_walker_new(state_of(self), node->handle, node->node, 1);
}

/* .text/.html/.inner_html walk the whole subtree, so hold the per-tree lock so a
   concurrent mutate cannot rewire it mid-walk (a no-op on the GIL build). */
PyObject *node_get_text(PyObject *self, void *Py_UNUSED(closure)) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    result = str_from_accessor(th_node_text, tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *node_get_html(PyObject *self, void *Py_UNUSED(closure)) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    result = str_from_accessor(th_node_html, tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *node_get_inner_html(PyObject *self, void *Py_UNUSED(closure)) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    result = str_from_accessor(th_node_inner_html, tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return result;
}

PyDoc_STRVAR(to_markdown_doc, "to_markdown(options=None)\n--\n\n"
                              "Render this node and its subtree as Markdown. The defaults emit opinionated\n"
                              "GitHub-Flavored Markdown.\n\n"
                              ":param options: a Markdown configuration object, or None for the defaults. Its\n"
                              "    grouped knobs (headings, links, tables, ...) cover the markdownify and\n"
                              "    html2text configuration surface.\n"
                              ":returns: the Markdown rendering of this node's subtree.");

/* Resolve a string option against its allowed values, writing the matched index
   into *out (an enum), or leave *out untouched when the argument was omitted. */
static int md_resolve_enum(const char *name, PyObject *value, const char *const *choices, int count, int *out) {
    if (value == NULL) {
        return 0;
    }
    const char *text = PyUnicode_AsUTF8(value);
    if (text == NULL) {
        PyErr_Clear();
        PyErr_Format(PyExc_TypeError, "%s must be a string", name);
        return -1;
    }
    for (int choice_index = 0; choice_index < count; choice_index++) {
        if (strcmp(text, choices[choice_index]) == 0) {
            *out = choice_index;
            return 0;
        }
    }
    PyErr_Format(PyExc_ValueError, "%s: invalid value %R", name, value);
    return -1;
}

/* The state a to_markdown converter hook needs to turn a th_node back into an
   Element for the callback: the module's types and the tree handle to keep alive. */
typedef struct {
    module_state *state;
    PyObject *handle;
} md_wrap_ctx;

static PyObject *md_wrap_node(void *wrap_ctx, th_node *node) {
    md_wrap_ctx *ctx = wrap_ctx;
    return node_wrap(ctx->state, ctx->handle, node);
}

/* Coerce the converters argument to a plain dict the C walker can look up in: a
   dict is borrowed as-is, any other mapping is copied once. An empty mapping
   yields NULL so the walk keeps its zero-overhead no-hook path. */
static PyObject *md_converters_dict(PyObject *converters) {
    PyObject *dict;
    if (PyDict_Check(converters)) {
        dict = Py_NewRef(converters);
    } else {
        dict = PyDict_New();
        if (dict == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyDict_Update(dict, converters) < 0) {
            Py_DECREF(dict);
            return NULL;
        }
    }
    if (PyDict_GET_SIZE(dict) == 0) {
        Py_DECREF(dict);
        Py_RETURN_NONE;
    }
    return dict;
}

/* Mark every tag named in seq in opt->filter_tags and switch opt to the given
   filter mode. Each name is matched as a lowercased atom, so a name outside the
   tag table matches nothing and is skipped, the way markdownify ignores a tag it
   has no converter for. A bare str is rejected so it never iterates per character.
   Returns -1 with an exception set on a non-string element or an iteration error. */
static int md_build_tag_filter(PyObject *seq, md_opts *opt, int mode) {
    if (PyUnicode_Check(seq)) {
        PyErr_SetString(PyExc_TypeError,
                        "to_markdown strip/convert must be an iterable of tag names, not a single str");
        return -1;
    }
    PyObject *iter = PyObject_GetIter(seq);
    if (iter == NULL) {
        return -1;
    }
    PyObject *item;
    while ((item = PyIter_Next(iter)) != NULL) {
        if (!PyUnicode_Check(item)) {
            PyErr_Format(PyExc_TypeError, "to_markdown strip/convert tags must be str, not %.200s",
                         Py_TYPE(item)->tp_name);
            Py_DECREF(item);
            Py_DECREF(iter);
            return -1;
        }
        Py_ssize_t utf8_len;
        const char *utf8 = PyUnicode_AsUTF8AndSize(item, &utf8_len);
        char lowered[64];
        if (utf8 != NULL && utf8_len <= (Py_ssize_t)sizeof(lowered)) {
            for (Py_ssize_t byte = 0; byte < utf8_len; byte++) {
                lowered[byte] = utf8[byte] >= 'A' && utf8[byte] <= 'Z' ? (char)(utf8[byte] + 32) : utf8[byte];
            }
            uint16_t atom = th_tag_lookup(lowered, utf8_len);
            if (atom != TH_TAG_UNKNOWN) {
                opt->filter_tags[atom >> 6] |= (uint64_t)1 << (atom & 63);
            }
        } else {
            PyErr_Clear(); /* a surrogate or over-long name matches no known tag */
        }
        Py_DECREF(item);
    }
    Py_DECREF(iter);
    if (PyErr_Occurred()) {
        return -1;
    }
    opt->tag_filter = mode;
    return 0;
}

/* Render this node from spec, a borrowed dict of renderer keyword options (a config
   object's non-default values) or NULL for every default. */
typedef PyObject *(*node_render_fn)(PyObject *self, PyObject *spec);

/* Unpack a renderer's config object to its keyword dict via _unpack(). The three
   config classes each expose _unpack, so duck-typing would silently accept the
   wrong one (a PlainText handed to to_markdown); the isinstance check against the
   expected type rejects it with a clear TypeError naming the type. Returns a new
   dict reference, or NULL with the error set. */
static PyObject *config_unpack(PyObject *options, PyObject *expected_type, const char *type_name) {
    int matches = PyObject_IsInstance(options, expected_type);
    if (matches < 0) { /* GCOVR_EXCL_BR_LINE: a real class second argument never raises */
        return NULL;   /* GCOVR_EXCL_LINE: unreachable isinstance-error path */
    }
    if (!matches) {
        PyErr_Format(PyExc_TypeError, "options must be a %s, not %.200s", type_name, Py_TYPE(options)->tp_name);
        return NULL;
    }
    return PyObject_CallMethod(options, "_unpack", NULL);
}

/* The shared dispatch for a renderer whose only argument is a config object: parse
   the single optional `options`, unpack it to the renderer keyword dict (or NULL for
   the defaults), and render. The unpacked dict owns the borrowed values for the call. */
static PyObject *node_render_with_options(PyObject *self, PyObject *args, PyObject *kwds, node_render_fn render,
                                          PyObject *expected_type, const char *type_name) {
    PyObject *options = NULL;
    static char *kw[] = {"options", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", kw, &options)) {
        return NULL;
    }
    if (options == NULL || options == Py_None) {
        return render(self, NULL);
    }
    PyObject *spec = config_unpack(options, expected_type, type_name);
    if (spec == NULL) {
        return NULL;
    }
    PyObject *result = render(self, spec);
    Py_DECREF(spec);
    return result;
}

/* Render this node as Markdown from spec, a borrowed dict of the renderer keyword
   options (the Markdown config's non-default values) or NULL for every default.
   The caller owns spec; the borrowed strings stay valid for this call. */
static PyObject *node_markdown_render(PyObject *self, PyObject *spec) {
    md_opts opt = th_markdown_default_opts();
    PyObject *heading = NULL, *strike = NULL, *code_style = NULL, *link = NULL, *image = NULL, *table = NULL;
    PyObject *header = NULL, *escape = NULL, *brk = NULL, *spacing = NULL, *docstrip = NULL;
    PyObject *converters = NULL, *strip = NULL, *convert = NULL;
    int ignore_emphasis = 0, mark_code = 0;
    static char *kw[] = {"heading_style",
                         "bullets",
                         "strong",
                         "emphasis",
                         "strikethrough",
                         "ignore_emphasis",
                         "sub_symbol",
                         "sup_symbol",
                         "code_block_style",
                         "code_language",
                         "mark_code",
                         "link_style",
                         "autolink",
                         "link_title",
                         "ignore_links",
                         "skip_internal_links",
                         "base_url",
                         "image_mode",
                         "default_image_alt",
                         "table_mode",
                         "table_header",
                         "pad_tables",
                         "escape_mode",
                         "escape_asterisks",
                         "escape_underscores",
                         "line_break",
                         "block_spacing",
                         "wrap_width",
                         "wrap_list_items",
                         "wrap_links",
                         "transliterate",
                         "document_strip",
                         "quote_open",
                         "quote_close",
                         "google_doc",
                         "google_list_indent",
                         "hide_strikethrough",
                         "strip",
                         "convert",
                         "converters",
                         NULL};
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int parsed = PyArg_ParseTupleAndKeywords(
        empty, spec, "|$OsssOpssOspOppppsOsOOpOppOOipppOsspipOOO", kw, &heading, &opt.bullets, &opt.strong,
        &opt.emphasis, &strike, &ignore_emphasis, &opt.sub, &opt.sup, &code_style, &opt.code_language, &mark_code,
        &link, &opt.autolink, &opt.link_title, &opt.ignore_links, &opt.skip_internal_links, &opt.base_url, &image,
        &opt.default_image_alt, &table, &header, &opt.pad_tables, &escape, &opt.escape_asterisks,
        &opt.escape_underscores, &brk, &spacing, &opt.wrap_width, &opt.wrap_list_items, &opt.wrap_links,
        &opt.transliterate, &docstrip, &opt.quote_open, &opt.quote_close, &opt.google_doc, &opt.google_list_indent,
        &opt.hide_strikethrough, &strip, &convert, &converters);
    Py_DECREF(empty);
    if (!parsed) {
        return NULL;
    }
    static const char *const headings[] = {"atx", "atx_closed", "setext"};
    static const char *const strikes[] = {"keep", "hide"};
    static const char *const codes[] = {"fenced", "indented"};
    static const char *const links[] = {"inline", "reference"};
    static const char *const images[] = {"markdown", "alt", "ignore", "html"};
    static const char *const tables[] = {"markdown", "strip", "html"};
    static const char *const headers[] = {"first", "detect", "none"};
    static const char *const escapes[] = {"minimal", "all"};
    static const char *const breaks[] = {"spaces", "backslash"};
    static const char *const spacings[] = {"double", "single"};
    static const char *const strips[] = {"strip", "lstrip", "rstrip", "none"};
    int keep_strike = 0, block_spacing = 0;
    if (md_resolve_enum("heading_style", heading, headings, 3, &opt.heading_style) < 0 ||
        md_resolve_enum("strikethrough", strike, strikes, 2, &keep_strike) < 0 ||
        md_resolve_enum("code_block_style", code_style, codes, 2, &opt.code_block_style) < 0 ||
        md_resolve_enum("link_style", link, links, 2, &opt.link_style) < 0 ||
        md_resolve_enum("image_mode", image, images, 4, &opt.image_mode) < 0 ||
        md_resolve_enum("table_mode", table, tables, 3, &opt.table_mode) < 0 ||
        md_resolve_enum("table_header", header, headers, 3, &opt.table_header) < 0 ||
        md_resolve_enum("escape_mode", escape, escapes, 2, &opt.escape_mode) < 0 ||
        md_resolve_enum("line_break", brk, breaks, 2, &opt.line_break) < 0 ||
        md_resolve_enum("block_spacing", spacing, spacings, 2, &block_spacing) < 0 ||
        md_resolve_enum("document_strip", docstrip, strips, 4, &opt.document_strip) < 0) {
        return NULL;
    }
    if (*opt.bullets == '\0') {
        PyErr_SetString(PyExc_ValueError, "bullets must not be empty");
        return NULL;
    }
    if (opt.google_list_indent < 1) {
        PyErr_SetString(PyExc_ValueError, "google_list_indent must be a positive number of pixels");
        return NULL;
    }
    if (opt.wrap_width < 0) {
        PyErr_SetString(PyExc_ValueError, "wrap_width must be a non-negative number of columns");
        return NULL;
    }
    opt.keep_emphasis = !ignore_emphasis;
    opt.keep_strikethrough = keep_strike == 0;
    opt.block_spacing_single = block_spacing == 1;
    if (mark_code) {
        opt.code_mark_open = "[code]";
        opt.code_mark_close = "[/code]";
    }
    /* The Markdown config's _unpack omits a None tag filter, so strip/convert/converters
       arrive here either absent (NULL) or a real object, never Py_None. strip and convert
       are mutually exclusive; the config rejects the pair on construction. */
    int has_strip = strip != NULL;
    int has_convert = convert != NULL;
    if (has_strip && md_build_tag_filter(strip, &opt, TH_MD_FILTER_STRIP) < 0) {
        return NULL;
    }
    if (has_convert && md_build_tag_filter(convert, &opt, TH_MD_FILTER_CONVERT) < 0) {
        return NULL;
    }
    PyObject *conv = NULL;
    md_wrap_ctx wrap_ctx;
    if (converters != NULL) {
        conv = md_converters_dict(converters);
        if (conv == NULL) {
            return NULL;
        }
        if (conv != Py_None) {
            wrap_ctx.state = state_of(self);
            wrap_ctx.handle = ((NodeObject *)self)->handle;
            opt.converters = conv;
            opt.wrap_node = md_wrap_node;
            opt.wrap_node_ctx = &wrap_ctx;
        }
    }
    Py_ssize_t out_len;
    Py_UCS4 *data;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    data = th_node_markdown(tree_of(self), ((NodeObject *)self)->node, &opt, &out_len);
    Py_END_CRITICAL_SECTION();
    Py_XDECREF(conv);
    if (data == NULL) {
        /* a converter that raised or returned a non-str leaves the exception set;
           a bare NULL with no exception is the unforceable allocation failure */
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the no-exception NULL is an unforceable allocation failure */
            return NULL;
        }
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

static PyObject *node_to_markdown(PyObject *self, PyObject *args, PyObject *kwds) {
    return node_render_with_options(self, args, kwds, node_markdown_render, state_of(self)->markdown_config_type,
                                    "Markdown");
}

PyDoc_STRVAR(to_text_doc, "to_text(options=None)\n--\n\n"
                          "Render this node and its subtree as layout-aware plain text: blocks\n"
                          "separated by blank lines, lists indented under their bullets, and tables\n"
                          "laid out as a column-aligned grid. The inscriptis role, in C.\n\n"
                          ":param options: a PlainText configuration object, or None for the defaults.\n"
                          ":returns: the plain-text rendering of this node's subtree.");

/* Parse the PlainText keyword options out of spec into opt; spec is a borrowed dict
   of the config's non-default values, or NULL for every default. -1 with an exception
   set on a bad value, 0 otherwise. */
static int text_opts_from_spec(text_opts *opt, PyObject *spec) {
    PyObject *links = NULL, *layout = NULL;
    static char *kw[] = {"width",  "links", "images", "layout", "default_image_alt", "table_cell_separator",
                         "bullet", NULL};
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) { /* GCOVR_EXCL_START - allocation failure cannot be forced from a test */
        PyErr_NoMemory();
        return -1;
    } /* GCOVR_EXCL_STOP */
    int parsed = PyArg_ParseTupleAndKeywords(empty, spec, "|$iOpOsss", kw, &opt->width, &links, &opt->images, &layout,
                                             &opt->default_image_alt, &opt->cell_separator, &opt->bullet);
    Py_DECREF(empty);
    if (!parsed) {
        return -1;
    }
    static const char *const link_modes[] = {"none", "inline", "footnote"};
    static const char *const layouts[] = {"strict", "extended"};
    if (md_resolve_enum("links", links, link_modes, 3, &opt->links) < 0 ||
        md_resolve_enum("layout", layout, layouts, 2, &opt->extended) < 0) {
        return -1;
    }
    return 0;
}

static PyObject *node_text_render(PyObject *self, PyObject *spec) {
    text_opts opt = th_text_default_opts();
    if (text_opts_from_spec(&opt, spec) < 0) {
        return NULL;
    }
    Py_ssize_t out_len;
    Py_UCS4 *data;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    data = th_node_layout_text(tree_of(self), ((NodeObject *)self)->node, &opt, &out_len);
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

static PyObject *node_to_text(PyObject *self, PyObject *args, PyObject *kwds) {
    return node_render_with_options(self, args, kwds, node_text_render, state_of(self)->plaintext_config_type,
                                    "PlainText");
}

/* Parse one annotation_rules entry ("tag", "tag#attr", "tag#attr=value", or
   "#attr...") into a text_rule. The attr name borrows the key's UTF-8 (the dict
   outlives the call); a value token is copied to *value_out (the caller frees it)
   and the labels tuple to *labels_out (the caller releases it). */
static int text_parse_rule(PyObject *key, PyObject *value, text_rule *rule, PyObject **labels_out,
                           Py_UCS4 **value_out) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "annotation_rules keys must be strings");
        return -1;
    }
    if (PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "annotation_rules values must be a list of labels, not a string");
        return -1;
    }
    Py_ssize_t klen;
    const char *kb = PyUnicode_AsUTF8AndSize(key, &klen);
    if (kb == NULL) { /* GCOVR_EXCL_BR_LINE: a lone-surrogate key cannot be built from a real parse */
        return -1;    /* GCOVR_EXCL_LINE: encoding-failure path */
    }
    const char *hash = memchr(kb, '#', (size_t)klen);
    Py_ssize_t taglen = hash != NULL ? hash - kb : klen;
    rule->any_tag = taglen == 0;
    rule->tag_atom = rule->any_tag ? TH_TAG_UNKNOWN : th_tag_lookup(kb, taglen);
    rule->attr = NULL;
    rule->attr_len = 0;
    rule->value = NULL;
    rule->value_len = 0;
    if (hash != NULL) {
        const char *spec = hash + 1;
        Py_ssize_t speclen = klen - taglen - 1;
        const char *eq = memchr(spec, '=', (size_t)speclen);
        rule->attr = spec;
        rule->attr_len = eq != NULL ? eq - spec : speclen;
        if (eq != NULL) {
            PyObject *vstr = PyUnicode_FromStringAndSize(eq + 1, speclen - rule->attr_len - 1);
            if (vstr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            Py_UCS4 *vbuf = PyUnicode_AsUCS4Copy(vstr);
            rule->value_len = PyUnicode_GET_LENGTH(vstr);
            Py_DECREF(vstr);
            if (vbuf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            *value_out = vbuf;
            rule->value = vbuf;
        }
    }
    PyObject *labels = PySequence_Tuple(value);
    if (labels == NULL) {
        return -1;
    }
    *labels_out = labels;
    rule->labels = labels;
    return 0;
}

PyDoc_STRVAR(to_annotated_text_doc, "to_annotated_text(annotation_rules, options=None)\n--\n\n"
                                    "Render layout-aware text and, for every element matching a rule in\n"
                                    "annotation_rules, record a labeled span over its text. Spans inside table\n"
                                    "cells are not recorded.\n\n"
                                    ":param annotation_rules: maps a selector ('tag', 'tag#attr', 'tag#attr=value',\n"
                                    "    or '#attr') to the list of labels to attach to each matching element.\n"
                                    ":param options: a PlainText configuration object, or None for the defaults.\n"
                                    ":returns: a (text, spans) pair, where spans is a list of (start, end, label).");

static PyObject *node_annotated_render(PyObject *self, PyObject *rules_dict, PyObject *spec) {
    if (!PyDict_Check(rules_dict)) {
        PyErr_SetString(PyExc_TypeError, "annotation_rules must be a dict");
        return NULL;
    }
    text_opts opt = th_text_default_opts();
    if (text_opts_from_spec(&opt, spec) < 0) {
        return NULL;
    }
    Py_ssize_t rule_count = PyDict_Size(rules_dict);
    text_rule *rules = PyMem_Calloc((size_t)(rule_count > 0 ? rule_count : 1), sizeof(text_rule));
    PyObject **labels = PyMem_Calloc((size_t)(rule_count > 0 ? rule_count : 1), sizeof(PyObject *));
    Py_UCS4 **values = PyMem_Calloc((size_t)(rule_count > 0 ? rule_count : 1), sizeof(Py_UCS4 *));
    if (rules == NULL || labels == NULL || values == NULL) { /* GCOVR_EXCL_BR_LINE: cannot force an alloc failure */
        PyMem_Free(rules);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(labels);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(values);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *key, *value;
    Py_ssize_t pos = 0, filled_count = 0;
    int failed = 0;
    while (PyDict_Next(rules_dict, &pos, &key, &value)) {
        if (text_parse_rule(key, value, &rules[filled_count], &labels[filled_count], &values[filled_count]) < 0) {
            failed = 1;
            break;
        }
        filled_count++;
    }
    PyObject *result = NULL;
    if (!failed) {
        text_span *spans = NULL;
        Py_ssize_t span_count = 0, out_len = 0;
        Py_UCS4 *data;
        Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
        data = th_node_annotated_text(tree_of(self), ((NodeObject *)self)->node, &opt, rules, rule_count, &spans,
                                      &span_count, &out_len);
        Py_END_CRITICAL_SECTION();
        if (data == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: merge of the unreachable alloc-failure block */
        /* data is NULL only on an alloc failure, so the NULL arms are unreachable */
        PyObject *text = data != NULL ? ucs4_to_str(data, out_len) : NULL;   /* GCOVR_EXCL_BR_LINE */
        PyObject *label_list = data != NULL ? PyList_New(span_count) : NULL; /* GCOVR_EXCL_BR_LINE */
        if (text != NULL && label_list != NULL) { /* GCOVR_EXCL_BR_LINE: only an alloc failure makes either NULL */
            for (Py_ssize_t span_index = 0; span_index < span_count; span_index++) {
                PyList_SET_ITEM(
                    label_list, span_index,
                    Py_BuildValue("nnO", spans[span_index].start, spans[span_index].end, spans[span_index].label));
            }
            result = PyTuple_Pack(2, text, label_list);
        }
        Py_XDECREF(text);
        Py_XDECREF(label_list);
        PyMem_Free(data);
        PyMem_Free(spans);
    }
    for (Py_ssize_t rule_index = 0; rule_index < filled_count; rule_index++) {
        Py_XDECREF(labels[rule_index]);
        PyMem_Free(values[rule_index]);
    }
    PyMem_Free(rules);
    PyMem_Free(labels);
    PyMem_Free(values);
    return result;
}

static PyObject *node_to_annotated_text(PyObject *self, PyObject *args, PyObject *kwds) {
    PyObject *rules_dict, *options = NULL;
    static char *kw[] = {"annotation_rules", "options", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|O", kw, &rules_dict, &options)) {
        return NULL;
    }
    if (options == NULL || options == Py_None) {
        return node_annotated_render(self, rules_dict, NULL);
    }
    PyObject *spec = config_unpack(options, state_of(self)->plaintext_config_type, "PlainText");
    if (spec == NULL) {
        return NULL;
    }
    PyObject *result = node_annotated_render(self, rules_dict, spec);
    Py_DECREF(spec);
    return result;
}

PyDoc_STRVAR(links_doc, "links()\n--\n\n"
                        "Find every link in this node and its subtree. Beyond <a href>, this finds the\n"
                        "URLs hidden in srcset/ping/archive lists, a <meta http-equiv=refresh>\n"
                        "redirect, and CSS url()/@import in a style attribute or a <style> sheet.\n\n"
                        ":returns: the Link records in document order.");

static PyObject *node_links(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return turbohtml_node_links(self, tree_of(self), ((NodeObject *)self)->node);
}

PyDoc_STRVAR(rewrite_links_doc, "rewrite_links(replace, /)\n--\n\n"
                                "Rewrite every link in this node and its subtree in place.\n\n"
                                ":param replace: called with each URL; return a str to substitute it or\n"
                                "    None to leave it unchanged.");

static PyObject *node_rewrite_links(PyObject *self, PyObject *replace) {
    return turbohtml_node_rewrite_links(self, tree_of(self), ((NodeObject *)self)->node, replace);
}

PyDoc_STRVAR(resolve_links_doc, "resolve_links(base_url, /)\n--\n\n"
                                "Rewrite every link in this node and its subtree to an absolute URL, in\n"
                                "place, using stdlib urllib.parse.urljoin.\n\n"
                                ":param base_url: the base each relative URL is resolved against.");

static PyObject *node_resolve_links(PyObject *self, PyObject *base_url) {
    return turbohtml_node_resolve_links(self, tree_of(self), ((NodeObject *)self)->node, base_url);
}

PyDoc_STRVAR(tables_doc, "tables()\n--\n\n"
                         "Return every table in this node and its subtree, in document order, each as the\n"
                         "list of rows that Element.rows() produces. A nested table appears as its own\n"
                         "entry. The result is a list[list[list[str]]] of plain strings, with no pandas\n"
                         "dependency; pass one table to pandas.DataFrame for a frame.");

static PyObject *node_tables(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return turbohtml_node_tables(self, tree_of(self), ((NodeObject *)self)->node);
}

PyDoc_STRVAR(main_content_doc, "main_content()\n--\n\n"
                               "Find the dominant content element under this node, the article body with\n"
                               "navigation, sidebars, ads, comments and other boilerplate scored out. Scores\n"
                               "the tree by content density (text length, comma count, tag and class/id\n"
                               "weight, discounted by link density), the readability heuristic, in C.\n\n"
                               ":returns: the winning content Element, or None when nothing reads as content.");

static PyObject *node_main_content(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *winner;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    winner = th_node_main_content(tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    if (winner == NULL) {
        Py_RETURN_NONE;
    }
    return turbohtml_node_wrap_in(self, winner);
}

PyDoc_STRVAR(main_text_doc, "main_text()\n--\n\n"
                            "Render the main content under this node as layout-aware plain text, as\n"
                            "to_text() renders main_content().\n\n"
                            ":returns: the main content's text, or an empty string when there is none.");

static PyObject *node_main_text(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    text_opts opt = th_text_default_opts();
    Py_ssize_t out_len = 0;
    Py_UCS4 *data = NULL;
    int empty = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    th_node *winner = th_node_main_content(tree_of(self), ((NodeObject *)self)->node);
    if (winner == NULL) {
        empty = 1;
    } else {
        data = th_node_layout_text(tree_of(self), winner, &opt, &out_len);
    }
    Py_END_CRITICAL_SECTION();
    if (empty) {
        return ucs4_to_str(NULL, 0);
    }
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

/* Store the Article record type the C core constructs for article(); turbohtml._article registers it on import. */
PyObject *turbohtml_register_article(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->article_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

/* Store the SourceLocation and SourceSpan record types Element.source_location
   builds; turbohtml._locations registers them on import. */
PyObject *turbohtml_register_locations(PyObject *module, PyObject *args) {
    PyObject *location, *span;
    if (!PyArg_ParseTuple(args, "OO", &location, &span)) { /* GCOVR_EXCL_BR_LINE: the facade always passes both types */
        return NULL;                                       /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->source_location_type, Py_NewRef(location));
    Py_XSETREF(state->source_span_type, Py_NewRef(span));
    Py_RETURN_NONE;
}

/* Store the Markdown/PlainText/Html config types so to_markdown()/to_text()/serialize()
   reject the wrong config with a type check; turbohtml._render registers them on import. */
PyObject *turbohtml_register_render_configs(PyObject *module, PyObject *args) {
    PyObject *markdown, *plaintext, *html, *canonical;
    if (!PyArg_ParseTuple(args, "OOOO", &markdown, &plaintext, &html, &canonical)) { /* GCOVR_EXCL_BR_LINE: the facade
                                          always registers with the four config classes */
        return NULL; /* GCOVR_EXCL_LINE: argument-error path */
    }
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->markdown_config_type, Py_NewRef(markdown));
    Py_XSETREF(state->plaintext_config_type, Py_NewRef(plaintext));
    Py_XSETREF(state->html_config_type, Py_NewRef(html));
    Py_XSETREF(state->canonical_config_type, Py_NewRef(canonical));
    Py_RETURN_NONE;
}

/* Materialize one harvested metadata buffer as a str, or None when it is absent. */
static PyObject *article_field(const Py_UCS4 *data, Py_ssize_t len) {
    if (data == NULL) {
        Py_RETURN_NONE;
    }
    return ucs4_to_str(data, len);
}

/* Materialize the harvested tags as a tuple of str, empty when none were found. */
static PyObject *article_tags(const th_article_tag *tags, Py_ssize_t count) {
    PyObject *tuple = PyTuple_New(count);
    if (tuple == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *item = ucs4_to_str(tags[index].data, tags[index].len);
        if (item == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(tuple); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;      /* GCOVR_EXCL_LINE */
        }
        PyTuple_SET_ITEM(tuple, index, item);
    }
    return tuple;
}

PyDoc_STRVAR(article_doc, "article()\n--\n\n"
                          "Return an Article record for the dominant content under this node: the\n"
                          "scored content body (element), its layout-aware plain text (text, as\n"
                          "main_text()), and the page metadata harvested from the document -- title,\n"
                          "byline, date, description, lang, canonical, site_name, tags and image.\n"
                          "element is None and text is empty when nothing reads as content; each\n"
                          "single-valued metadata field is None when absent and tags is an empty\n"
                          "tuple. Title comes from <h1>, then og:title, then <title>; byline from a\n"
                          "rel=author link, then a meta author, then article:author; date from\n"
                          "<time>, then article:published_time, then a common date meta; description\n"
                          "from og:description, then a meta description; lang from <html lang>;\n"
                          "canonical from <link rel=canonical>, then og:url; site_name from\n"
                          "og:site_name, then a meta application-name; tags from every <meta\n"
                          "name=keywords> (comma-split) and article:tag; image from og:image, then\n"
                          "twitter:image. Pure C.");

static PyObject *node_article(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_tree *tree = tree_of(self);
    text_opts opt = th_text_default_opts();
    th_node *winner = NULL;
    Py_UCS4 *text_data = NULL;
    Py_ssize_t text_len = 0;
    th_article_meta meta = {0};
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    winner = th_node_main_content(tree, ((NodeObject *)self)->node);
    if (winner != NULL) {
        text_data = th_node_layout_text(tree, winner, &opt, &text_len);
    }
    th_article_metadata(tree, th_tree_document(tree), &meta);
    Py_END_CRITICAL_SECTION();

    PyObject *element = winner != NULL ? turbohtml_node_wrap_in(self, winner) : Py_NewRef(Py_None);
    /* th_node_layout_text leaves text_data NULL with text_len 0 only on an
       (unforceable) allocation failure, which ucs4_to_str renders as empty text. */
    PyObject *text = winner != NULL ? ucs4_to_str(text_data, text_len) : ucs4_to_str(NULL, 0);
    PyMem_Free(text_data);
    PyObject *title = article_field(meta.title, meta.title_len);
    PyObject *byline = article_field(meta.byline, meta.byline_len);
    PyObject *date = article_field(meta.date, meta.date_len);
    PyObject *description = article_field(meta.description, meta.description_len);
    PyObject *lang = article_field(meta.lang, meta.lang_len);
    PyObject *canonical = article_field(meta.canonical, meta.canonical_len);
    PyObject *site_name = article_field(meta.site_name, meta.site_name_len);
    PyObject *image = article_field(meta.image, meta.image_len);
    PyObject *tags = article_tags(meta.tags, meta.tags_count);
    th_article_meta_clear(&meta);

    /* Py_BuildValue("(N...)") steals each reference and, if any field is NULL from
       an (unforceable) allocation failure, propagates the error and frees the rest. */
    PyObject *args = Py_BuildValue("(NNNNNNNNNNN)", element, text, title, byline, date, description, lang, canonical,
                                   site_name, tags, image);
    if (args == NULL) { /* GCOVR_EXCL_BR_LINE: a field is NULL only on an unforceable allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyObject_CallObject(state_of(self)->article_type, args);
    Py_DECREF(args);
    return result;
}

static PyGetSetDef node_getset[] = {
    {"parent", node_get_parent, NULL, "the parent Element or Document, or None for the document root", NULL},
    {"children", node_get_children, NULL, "the child nodes as a tuple", NULL},
    {"next_sibling", node_get_next_sibling, NULL, "the following sibling node, or None", NULL},
    {"previous_sibling", node_get_previous_sibling, NULL, "the preceding sibling node, or None", NULL},
    {"descendants", node_get_descendants, NULL, "an iterator over every descendant in document order", NULL},
    {"ancestors", node_get_ancestors, NULL, "an iterator from parent up to the document", NULL},
    {"next_siblings", node_get_next_siblings, NULL, "an iterator over the following siblings in document order", NULL},
    {"previous_siblings", node_get_previous_siblings, NULL, "an iterator over the preceding siblings, nearest first",
     NULL},
    {"following", node_get_following, NULL,
     "an iterator over nodes after this one in document order, excluding its descendants", NULL},
    {"preceding", node_get_preceding, NULL,
     "an iterator over nodes before this one in document order, nearest first, excluding its ancestors", NULL},
    {"strings", node_get_strings, NULL, "an iterator over the text of every Text descendant", NULL},
    {"stripped_strings", node_get_stripped_strings, NULL,
     "strings with surrounding whitespace removed and blank runs skipped", NULL},
    {"text", node_get_text, NULL, "the concatenated character data of every Text descendant", NULL},
    {"html", node_get_html, NULL, "the HTML serialization of this node and its subtree", NULL},
    {"inner_html", node_get_inner_html, NULL, "the HTML serialization of this node's children", NULL},
    {"source_line", node_get_source_line, NULL,
     "the 1-based source line of this element's start tag, or None if unavailable", NULL},
    {"source_col", node_get_source_col, NULL,
     "the 0-based source column of this element's start tag, or None if unavailable", NULL},
    {"position", node_get_position, NULL,
     "the (source_line, source_col) of this element's start tag, or None if unavailable", NULL},
    {"assigned_slot", node_get_assigned_slot, NULL,
     "the <slot> element this node is assigned to in its shadow host, or None when it is unassigned or the host's "
     "shadow root is closed",
     NULL},
    {"flattened_children", node_get_flattened_children, NULL,
     "the flattened-tree children as a list: a shadow host yields its shadow tree with each <slot> replaced by its "
     "assigned nodes (or fallback content)",
     NULL},
    {"source_location", node_get_source_location, NULL,
     "the SourceLocation (start-/end-tag and per-attribute spans) of this element, or None when the tree was not "
     "parsed "
     "with source_locations or the element has no source start tag",
     NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static Py_ssize_t node_length(PyObject *self) {
    Py_ssize_t count = 0;
    for (th_node *child = ((NodeObject *)self)->node->first_child; child != NULL; child = child->next_sibling) {
        count++;
    }
    return count;
}

static PyObject *node_item(PyObject *self, Py_ssize_t index) {
    NodeObject *node = (NodeObject *)self;
    th_node *child = node->node->first_child;
    for (Py_ssize_t step = 0; step < index && child != NULL; step++) {
        child = child->next_sibling;
    }
    if (child == NULL) {
        PyErr_SetString(PyExc_IndexError, "node child index out of range");
        return NULL;
    }
    return node_wrap(state_of(self), node->handle, child);
}

static PyObject *node_iter(PyObject *self) {
    PyObject *children = node_children_tuple(self);
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *iterator = PyObject_GetIter(children);
    Py_DECREF(children);
    return iterator;
}

static int node_bool(PyObject *Py_UNUSED(self)) {
    return 1; /* a node is always truthy; emptiness is len(), not bool() */
}

static PyObject *node_repr(PyObject *self) {
    th_node *node = ((NodeObject *)self)->node;
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_ELEMENT: {
        PyObject *tag = ucs4_to_str(node->text, node->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *repr = PyUnicode_FromFormat("Element(%R)", tag);
        Py_DECREF(tag);
        return repr;
    }
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
    case TH_NODE_DOCTYPE: {
        PyObject *data = str_from_accessor(th_node_data, tree_of(self), node);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        const char *label = node->type == TH_NODE_TEXT      ? "Text"
                            : node->type == TH_NODE_COMMENT ? "Comment"
                            : node->type == TH_NODE_CDATA   ? "CData"
                                                            : "Doctype";
        PyObject *repr = PyUnicode_FromFormat("%s(%R)", label, data);
        Py_DECREF(data);
        return repr;
    }
    case TH_NODE_PI: {
        PyObject *target = ucs4_to_str(node->text, node->attr_count);
        PyObject *data = ucs4_to_str(node->text + node->attr_count + 1, node->text_len - node->attr_count - 1);
        if (target == NULL || data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_XDECREF(target);               /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(data);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *repr = PyUnicode_FromFormat("ProcessingInstruction(%R, %R)", target, data);
        Py_DECREF(target);
        Py_DECREF(data);
        return repr;
    }
    case TH_NODE_DOCUMENT:
        return PyUnicode_FromString("Document()");
    case TH_NODE_CONTENT:
        break;
    }
    return PyUnicode_FromString("Node()");
}

PyDoc_STRVAR(re_doc, "re(pattern, /, *, attr=None)\n--\n\n"
                     "Run a regex over this node's text. Each match yields its one capturing group\n"
                     "when the pattern has exactly one, otherwise the whole match.\n\n"
                     ":param pattern: a str or compiled re.Pattern to search for.\n"
                     ":param attr: search this attribute's value instead of the node text; an\n"
                     "    absent attribute yields [].\n"
                     ":returns: every match as a list of str.");

PyDoc_STRVAR(re_first_doc, "re_first(pattern, /, default=None, *, attr=None)\n--\n\n"
                           "Run a regex over this node's text and return the first match, with the same\n"
                           "group rule as re().\n\n"
                           ":param pattern: a str or compiled re.Pattern to search for.\n"
                           ":param default: value returned when nothing matches.\n"
                           ":param attr: search this attribute's value instead of the node text.\n"
                           ":returns: the first match as a str, or default when nothing matches.");

PyDoc_STRVAR(select_doc, "select(selector, /)\n--\n\n"
                         "Find the descendant Elements matching a CSS selector. The grammar covers\n"
                         "type, #id, .class, and attribute selectors, the four combinators, the\n"
                         "structural pseudo-classes (including :nth-child(An+B of S)), the :is(),\n"
                         ":where(), :has(), and :not() functional pseudo-classes, and the :scope,\n"
                         "form/UI, :lang() and :dir() pseudo-classes a static tree can determine;\n"
                         "live-state pseudo-classes (:hover, :focus, ...) match nothing. :is() and\n"
                         ":where() take a forgiving list, so a bad arm is dropped.\n\n"
                         ":param selector: the CSS selector.\n"
                         ":returns: the matching descendant Elements in document order.\n"
                         ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                         "    and the source offset.");

PyDoc_STRVAR(select_one_doc, "select_one(selector, /)\n--\n\n"
                             "Find the first descendant Element matching a CSS selector.\n\n"
                             ":param selector: the CSS selector.\n"
                             ":returns: the first matching Element, or None.\n"
                             ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                             "    and the source offset.");

PyDoc_STRVAR(xpath_doc, "xpath(expression, /, *, smart_strings=False, extensions=None, **variables)\n--\n\n"
                        "Evaluate an XPath expression relative to this node. A node-set returns a list\n"
                        "of Elements (with attribute/text values as str) in document order; absolute\n"
                        "paths start at the document root.\n\n"
                        ":param expression: the XPath expression.\n"
                        ":param smart_strings: return each string result as a value that remembers the\n"
                        "    Element it came from.\n"
                        ":param extensions: maps an (namespace, name) pair to a callable, registering a\n"
                        "    custom XPath function; the callable may return a scalar, an Element, or an\n"
                        "    iterable of Elements (a node-set that feeds later steps).\n"
                        ":param variables: values bound to the $name variables used in the expression; a\n"
                        "    str, int, float, or bool binds a scalar, and an Element or an iterable of\n"
                        "    Elements binds a node-set. A node from a different document raises ValueError.\n"
                        ":returns: the result list, of Elements and str values in document order.\n"
                        ":raises TypeError: expression is not a str, a variable binding is of an\n"
                        "    unsupported type, or a value is used where the grammar requires a node-set.\n"
                        ":raises ValueError: the expression is not valid XPath (with the offending token\n"
                        "    and its offset), nests past the depth limit, calls an unknown function or one\n"
                        "    with the wrong number of arguments, references an unbound variable or prefix,\n"
                        "    or binds a node from a different document.");

PyDoc_STRVAR(xpath_iter_doc, "xpath_iter(expression, /, *, smart_strings=False, extensions=None, **variables)\n--\n\n"
                             "Like xpath(), but stream the results instead of building a list.\n\n"
                             ":param expression: the XPath expression.\n"
                             ":param smart_strings: return each string result as a value that remembers\n"
                             "    the Element it came from.\n"
                             ":param extensions: maps an (namespace, name) pair to a custom XPath function.\n"
                             ":param variables: values bound to the $name variables in the expression.\n"
                             ":returns: an iterator over the results in document order.\n"
                             ":raises TypeError: expression is not a str, a variable binding is of an\n"
                             "    unsupported type, or a value is used where a node-set is required.\n"
                             ":raises ValueError: the expression is invalid XPath, nests too deeply, calls an\n"
                             "    unknown or wrong-arity function, or references an unbound variable or prefix.");

PyDoc_STRVAR(xpath_one_doc, "xpath_one(expression, /, *, smart_strings=False, extensions=None, **variables)\n--\n\n"
                            "Like xpath(), but return only the first result.\n\n"
                            ":param expression: the XPath expression.\n"
                            ":param smart_strings: return a string result as a value that remembers the\n"
                            "    Element it came from.\n"
                            ":param extensions: maps an (namespace, name) pair to a custom XPath function.\n"
                            ":param variables: values bound to the $name variables in the expression.\n"
                            ":returns: the first result in document order, or None when there is none.\n"
                            ":raises TypeError: expression is not a str, a variable binding is of an\n"
                            "    unsupported type, or a value is used where a node-set is required.\n"
                            ":raises ValueError: the expression is invalid XPath, nests too deeply, calls an\n"
                            "    unknown or wrong-arity function, or references an unbound variable or prefix.");

PyDoc_STRVAR(matches_doc, "matches(selector, /)\n--\n\n"
                          "Test this node against a CSS selector, evaluated with its own ancestors and\n"
                          "siblings as context.\n\n"
                          ":param selector: the CSS selector.\n"
                          ":returns: whether this node is an Element that matches.\n"
                          ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                          "    and the source offset.");

PyDoc_STRVAR(closest_doc, "closest(selector, /)\n--\n\n"
                          "Find the nearest Element matching a CSS selector, testing this node then each\n"
                          "ancestor.\n\n"
                          ":param selector: the CSS selector.\n"
                          ":returns: the nearest matching Element, or None.\n"
                          ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                          "    and the source offset.");

PyDoc_STRVAR(prune_doc, "prune(selector, /)\n--\n\n"
                        "Keep only the descendants matching a CSS selector, together with their\n"
                        "ancestors up to this node and the whole subtree under each match, and remove\n"
                        "every other descendant in place. With no match the subtree is emptied. This\n"
                        "trims a parsed document to the parts of interest after a normal WHATWG parse,\n"
                        "the way BeautifulSoup's SoupStrainer filters a document while parsing it.\n\n"
                        ":param selector: the CSS selector the kept descendants must match.\n"
                        ":returns: this node.\n"
                        ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                        "    and the source offset.");

PyDoc_STRVAR(remove_doc, "remove(selector, /)\n--\n\n"
                         "Drop every descendant Element matching the CSS selector, each with its\n"
                         "whole subtree, and return this node. The bulk inverse of prune (which keeps\n"
                         "the matches): the destructive counterpart of selectolax's strip_tags and\n"
                         "w3lib's remove_tags_with_content, and of jQuery's .remove().\n\n"
                         ":param selector: the CSS selector the dropped descendants must match.\n"
                         ":returns: this node.\n"
                         ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                         "    and the source offset.");

PyDoc_STRVAR(strip_tags_doc, "strip_tags(selector, /)\n--\n\n"
                             "Unwrap every descendant Element matching the CSS selector, replacing each\n"
                             "match with its children in place while keeping that content, and return\n"
                             "this node. The bulk form of unwrap, matching selectolax's unwrap_tags,\n"
                             "w3lib's remove_tags, and jQuery's .unwrap().\n\n"
                             ":param selector: the CSS selector the unwrapped descendants must match.\n"
                             ":returns: this node.\n"
                             ":raises ValueError: the selector is not valid CSS; the message names the reason\n"
                             "    and the source offset.");

static PyObject *node_serialize(PyObject *self, PyObject *args, PyObject *kwds);

static PyObject *node_encode(PyObject *self, PyObject *args, PyObject *kwds);

static PyObject *node_serialize_iter(PyObject *self, PyObject *args, PyObject *kwds);

static PyObject *node_canonicalize(PyObject *self, PyObject *args, PyObject *kwds);

static PyObject *node_to_source(PyObject *self, PyObject *ignored);

PyDoc_STRVAR(to_source_doc, "to_source()\n--\n\n"
                            "Losslessly serialize this node and its subtree back to a str, re-emitting\n"
                            "the verbatim source bytes of every element and text run the parse left\n"
                            "untouched and reserializing only the parts a mutation changed.\n\n"
                            "On a tree parsed with source_locations=True and not otherwise read, the\n"
                            "round trip reproduces the source byte for byte -- author quoting, tag-name\n"
                            "case, character-reference spelling, and insignificant whitespace intact --\n"
                            "for input that parsed without implied elements or content reordering. After a\n"
                            "mutation only the changed node's markup is rewritten: an element whose\n"
                            "attributes changed rebuilds its start tag, an edited text run re-escapes, and\n"
                            "an inserted element serializes canonically, while every untouched sibling and\n"
                            "subtree still copies its original span. Without source locations every element\n"
                            "reserializes canonically, so the result matches serialize().\n\n"
                            ":returns: the lossless HTML serialization.");

PyDoc_STRVAR(canonicalize_doc, "canonicalize(options=None)\n--\n\n"
                               "Serialize this node and its subtree to Canonical XML (c14n), the byte-exact\n"
                               "form an XML signature signs, returned as UTF-8 bytes.\n\n"
                               "Attributes are reordered (namespace declarations first, then each attribute\n"
                               "by namespace URI and local name), redundant namespace declarations are\n"
                               "dropped, empty elements are written as start-end pairs, and the c14n\n"
                               "character-reference rules apply. The whole subtree is canonicalized, so\n"
                               "c14n 1.0 and 1.1 coincide here bar the apex's inherited xml: attributes.\n\n"
                               ":param options: a Canonical configuration object (version, exclusive,\n"
                               "    with_comments, inclusive_ns_prefixes), or None for the defaults.\n"
                               ":returns: the canonical XML, encoded as UTF-8 bytes.\n"
                               ":raises TypeError: if options is not a Canonical configuration object.");

PyDoc_STRVAR(serialize_doc, "serialize(options=None)\n--\n\n"
                            "Serialize this node and its subtree to a str.\n\n"
                            ":param options: an Html configuration object (formatter, layout, attribute\n"
                            "    ordering, and meta-charset handling), or None for the defaults.\n"
                            ":returns: the serialized markup.\n"
                            ":raises TypeError: if options is not an Html configuration object.");

PyDoc_STRVAR(serialize_iter_doc, "serialize_iter(options=None)\n--\n\n"
                                 "Serialize this node and its subtree lazily, yielding the markup in bounded\n"
                                 "str chunks so a large document can stream to a socket or file without a\n"
                                 "full-size output string. ``''.join(node.serialize_iter(options))`` equals\n"
                                 "``node.serialize(options)`` for every options the stream supports.\n\n"
                                 "The tree must not be mutated while the iterator is live, the same rule as\n"
                                 "the other node iterators.\n\n"
                                 ":param options: an Html configuration object, or None for the defaults. A\n"
                                 "    Minify layout is rejected: minification needs the whole tree at once.\n"
                                 ":returns: an iterator of str chunks whose concatenation is the markup.\n"
                                 ":raises TypeError: if options is not an Html configuration object.\n"
                                 ":raises ValueError: if options selects a Minify layout, which cannot stream.");

PyDoc_STRVAR(encode_doc, "encode(encoding='utf-8', options=None)\n--\n\n"
                         "Serialize this node and its subtree to bytes, with the same formatting controls\n"
                         "as serialize().\n\n"
                         ":param encoding: the codec to encode the markup with.\n"
                         ":param options: an Html configuration object, or None for the defaults.\n"
                         ":returns: the serialized markup encoded as bytes.\n"
                         ":raises TypeError: if options is not an Html configuration object.\n"
                         ":raises LookupError: if encoding names a codec Python does not know.\n"
                         ":raises UnicodeEncodeError: if the markup has characters the encoding cannot\n"
                         "    represent.");

PyDoc_STRVAR(find_doc,
             "find(tag=None, /, *, axis=Axis.DESCENDANTS, attrs=None, class_=None, text=None, **filters)\n--\n\n"
             "Find the first Element along axis matching the tag filter and every attribute\n"
             "filter. A filter is a str, bool, compiled regex, callable, or a list of those.\n\n"
             ":param tag: filter on the tag name.\n"
             ":param axis: which nodes to walk relative to this one.\n"
             ":param attrs: a mapping of attribute name to filter.\n"
             ":param class_: filter on a token of the class attribute.\n"
             ":param text: match the element's collected text (an exact str, a regex search, or a callable\n"
             "    predicate); filter a literal text attribute through attrs={'text': ...}.\n"
             ":param filters: further attribute filters given as keyword arguments.\n"
             ":returns: the first matching Element, or None.\n"
             ":raises TypeError: if axis is not an Axis, or a filter is not a str, bool, regex,\n"
             "    callable, or a list of those.");

PyDoc_STRVAR(find_all_doc,
             "find_all(tag=None, /, *, axis=Axis.DESCENDANTS, attrs=None, class_=None, text=None, limit=None, "
             "**filters)\n--\n\n"
             "Find every Element along axis matching the tag filter and every attribute\n"
             "filter, with the same filter forms as find().\n\n"
             ":param tag: filter on the tag name.\n"
             ":param axis: which nodes to walk relative to this one.\n"
             ":param attrs: a mapping of attribute name to filter.\n"
             ":param class_: filter on a token of the class attribute.\n"
             ":param text: match each element's collected text (an exact str, a regex search, or a callable\n"
             "    predicate).\n"
             ":param limit: stop after this many matches; None collects them all.\n"
             ":param filters: further attribute filters given as keyword arguments.\n"
             ":returns: the matching Elements in document order.\n"
             ":raises TypeError: if axis is not an Axis, limit is not an int or None, or a\n"
             "    filter is not a str, bool, regex, callable, or a list of those.\n"
             ":raises ValueError: if limit is negative.");

PyDoc_STRVAR(insert_before_doc, "insert_before(*nodes)\n--\n\n"
                                "Insert each node into this node's parent right before this node, in order. A\n"
                                "node already in a tree is moved; a node from another tree is adopted by copy.\n\n"
                                ":param nodes: the nodes to insert.\n"
                                ":raises TypeError: if an argument is not a node, or is a Document.\n"
                                ":raises ValueError: if this node has no parent, or a node is an ancestor of\n"
                                "    the insertion point (which would form a cycle).");

PyDoc_STRVAR(insert_after_doc, "insert_after(*nodes)\n--\n\n"
                               "Insert each node into this node's parent right after this node, in order,\n"
                               "with the same move-or-adopt rule as insert_before().\n\n"
                               ":param nodes: the nodes to insert.\n"
                               ":raises TypeError: if an argument is not a node, or is a Document.\n"
                               ":raises ValueError: if this node has no parent, or a node is an ancestor of\n"
                               "    the insertion point (which would form a cycle).");

PyDoc_STRVAR(replace_with_doc, "replace_with(*nodes)\n--\n\n"
                               "Put nodes where this node is, in order, and detach this node, which becomes a\n"
                               "standalone root the caller still holds. With no nodes this just removes this\n"
                               "node.\n\n"
                               ":param nodes: the nodes to put in this node's place.\n"
                               ":raises TypeError: if an argument is not a node, or is a Document.\n"
                               ":raises ValueError: if this node has no parent, or a node is an ancestor of\n"
                               "    this node (which would form a cycle).");

PyDoc_STRVAR(wrap_doc, "wrap(wrapper, /)\n--\n\n"
                       "Put this node inside wrapper, in this node's place.\n\n"
                       ":param wrapper: the element to wrap this node in.\n"
                       ":returns: wrapper, now holding this node.\n"
                       ":raises TypeError: if wrapper is not an element.");

PyDoc_STRVAR(wrap_siblings_doc, "wrap_siblings(wrapper, /, *, until=None)\n--\n\n"
                                "Wrap this node and the siblings that follow it in wrapper in one move; the\n"
                                "bulk form of wrap() for a contiguous run. wrapper lands where this node was.\n\n"
                                ":param wrapper: the element to wrap the run in.\n"
                                ":param until: the last sibling to include (this node or a later one);\n"
                                "    None reaches to the last sibling.\n"
                                ":returns: wrapper, now holding the run.\n"
                                ":raises TypeError: if wrapper is not an element, or until is not a node.\n"
                                ":raises ValueError: if this node has no parent, or until is not this node or\n"
                                "    a following sibling.");

PyDoc_STRVAR(unwrap_doc, "unwrap()\n--\n\n"
                         "Replace this node with its children, the inverse of wrap().\n\n"
                         ":returns: this node, detached.\n"
                         ":raises ValueError: if this node has no parent.");

PyDoc_STRVAR(extract_doc, "extract()\n--\n\n"
                          "Detach this node from its parent, leaving a standalone node the caller can\n"
                          "reinsert elsewhere.\n\n"
                          ":returns: this node, detached.");

PyDoc_STRVAR(decompose_doc, "decompose()\n--\n\n"
                            "Detach this node and its subtree from the document and drop it.");

static PyMethodDef node_methods[] = {
    {"find", (PyCFunction)(void (*)(void))node_find, METH_VARARGS | METH_KEYWORDS, find_doc},
    {"find_all", (PyCFunction)(void (*)(void))node_find_all, METH_VARARGS | METH_KEYWORDS, find_all_doc},
    {"select", node_select, METH_O, select_doc},
    {"select_one", node_select_one, METH_O, select_one_doc},
    {"xpath", (PyCFunction)(void (*)(void))node_xpath, METH_VARARGS | METH_KEYWORDS, xpath_doc},
    {"xpath_iter", (PyCFunction)(void (*)(void))node_xpath_iter, METH_VARARGS | METH_KEYWORDS, xpath_iter_doc},
    {"xpath_one", (PyCFunction)(void (*)(void))node_xpath_one, METH_VARARGS | METH_KEYWORDS, xpath_one_doc},
    {"equals", node_equals, METH_O, equals_doc},
    {"matches", node_css_matches, METH_O, matches_doc},
    {"closest", node_css_closest, METH_O, closest_doc},
    {"prune", node_prune, METH_O, prune_doc},
    {"remove", node_remove, METH_O, remove_doc},
    {"strip_tags", node_strip_tags, METH_O, strip_tags_doc},
    {"re", (PyCFunction)(void (*)(void))node_re, METH_VARARGS | METH_KEYWORDS, re_doc},
    {"re_first", (PyCFunction)(void (*)(void))node_re_first, METH_VARARGS | METH_KEYWORDS, re_first_doc},
    {"serialize", (PyCFunction)(void (*)(void))node_serialize, METH_VARARGS | METH_KEYWORDS, serialize_doc},
    {"serialize_iter", (PyCFunction)(void (*)(void))node_serialize_iter, METH_VARARGS | METH_KEYWORDS,
     serialize_iter_doc},
    {"encode", (PyCFunction)(void (*)(void))node_encode, METH_VARARGS | METH_KEYWORDS, encode_doc},
    {"canonicalize", (PyCFunction)(void (*)(void))node_canonicalize, METH_VARARGS | METH_KEYWORDS, canonicalize_doc},
    {"to_source", node_to_source, METH_NOARGS, to_source_doc},
    {"to_markdown", (PyCFunction)(void (*)(void))node_to_markdown, METH_VARARGS | METH_KEYWORDS, to_markdown_doc},
    {"to_text", (PyCFunction)(void (*)(void))node_to_text, METH_VARARGS | METH_KEYWORDS, to_text_doc},
    {"to_annotated_text", (PyCFunction)(void (*)(void))node_to_annotated_text, METH_VARARGS | METH_KEYWORDS,
     to_annotated_text_doc},
    {"links", node_links, METH_NOARGS, links_doc},
    {"rewrite_links", node_rewrite_links, METH_O, rewrite_links_doc},
    {"resolve_links", node_resolve_links, METH_O, resolve_links_doc},
    {"tables", node_tables, METH_NOARGS, tables_doc},
    {"main_content", node_main_content, METH_NOARGS, main_content_doc},
    {"main_text", node_main_text, METH_NOARGS, main_text_doc},
    {"article", node_article, METH_NOARGS, article_doc},
    {"insert_before", node_insert_before, METH_VARARGS, insert_before_doc},
    {"insert_after", node_insert_after, METH_VARARGS, insert_after_doc},
    {"replace_with", node_replace_with, METH_VARARGS, replace_with_doc},
    {"wrap", node_wrap_in, METH_O, wrap_doc},
    {"wrap_siblings", (PyCFunction)(void (*)(void))node_wrap_siblings, METH_VARARGS | METH_KEYWORDS, wrap_siblings_doc},
    {"unwrap", node_unwrap, METH_NOARGS, unwrap_doc},
    {"extract", node_extract, METH_NOARGS, extract_doc},
    {"decompose", node_decompose, METH_NOARGS, decompose_doc},
    {"__copy__", node_copy, METH_NOARGS, "Return a standalone deep copy of this node and its subtree."},
    {"__deepcopy__", node_deepcopy, METH_O, "Return a standalone deep copy of this node and its subtree."},
    {"__reduce__", node_reduce, METH_NOARGS, "Support pickling: rebuild this node and its subtree on unpickle."},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(node_doc, "Common navigation shared by every node in a parsed tree. Text appears as\n"
                       "real child nodes (the WHATWG DOM shape), so there is no text/tail split.");

static PyType_Slot node_slots[] = {
    {Py_tp_doc, (void *)node_doc}, {Py_tp_dealloc, node_dealloc},
    {Py_tp_repr, node_repr},       {Py_tp_richcompare, node_richcompare},
    {Py_tp_hash, node_hash},       {Py_tp_getset, node_getset},
    {Py_tp_methods, node_methods}, {Py_tp_iter, node_iter},
    {Py_sq_length, node_length},   {Py_sq_item, node_item},
    {Py_nb_bool, node_bool},       {0, NULL},
};

PyType_Spec node_spec = {
    .name = "turbohtml._html.Node",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = node_slots,
};

/* Map a Formatter member to its enum th_formatter index; absent means the
   WHATWG default. */
static int resolve_formatter(module_state *state, PyObject *formatter, int *out) {
    if (formatter == NULL) {
        *out = 0;
        return 0;
    }
    for (int index = 0; index < TH_FORMATTER_COUNT; index++) {
        if (formatter == state->formatters[index]) {
            *out = index;
            return 0;
        }
    }
    PyErr_SetString(PyExc_TypeError, "formatter must be a Formatter member");
    return -1;
}

/* The serialization layout the layout=... argument selects. */
enum th_layout_mode {
    TH_LAYOUT_COMPACT, /* the WHATWG fragment algorithm, no inserted whitespace */
    TH_LAYOUT_INDENT,  /* pretty form keyed on an Indent's per-level unit */
    TH_LAYOUT_MINIFY,  /* the round-trip-safe minify transforms */
};

/* Resolve the layout argument: an absent layout compacts, an Indent pretty-prints, a
   Minify minifies. An Indent owns its unit buffer and a Minify its flags, so the resolved
   values borrow from the object the caller keeps alive for the serialize. The Html config's
   _unpack omits a None layout, so layout_obj arrives here NULL (absent) or a real object,
   never Py_None. Returns 0 on success (mode set) or -1 with a TypeError on any other object. */
static int resolve_layout(module_state *state, PyObject *layout_obj, enum th_layout_mode *mode,
                          const Py_UCS4 **indent_unit, Py_ssize_t *indent_len, th_minify_opts *opts) {
    if (layout_obj == NULL) {
        *mode = TH_LAYOUT_COMPACT;
        return 0;
    }
    if (Py_IS_TYPE(layout_obj, (PyTypeObject *)state->indent_type)) {
        IndentObject *indent = (IndentObject *)layout_obj;
        *indent_unit = indent->unit;
        *indent_len = indent->unit_len;
        *mode = TH_LAYOUT_INDENT;
        return 0;
    }
    if (Py_IS_TYPE(layout_obj, (PyTypeObject *)state->minify_type)) {
        MinifyObject *minify = (MinifyObject *)layout_obj;
        opts->collapse_whitespace = minify->collapse_whitespace;
        opts->omit_optional_tags = minify->omit_optional_tags;
        opts->unquote_attributes = minify->unquote_attributes;
        opts->strip_comments = minify->strip_comments;
        opts->minify_js = minify->minify_js;
        opts->minify_js_fold = minify->minify_js_fold;
        opts->minify_js_mangle = minify->minify_js_mangle;
        opts->minify_css = minify->minify_css;
        opts->minify_css_baseline = minify->minify_css_baseline;
        *mode = TH_LAYOUT_MINIFY;
        return 0;
    }
    PyErr_SetString(PyExc_TypeError, "layout must be an Indent, a Minify, or None");
    return -1;
}

/* Serialize self to a str under the given Formatter member, layout mode, and the
   two output normalizations. charset is the label the meta_charset option writes
   (the str output is conceptually UTF-8 for serialize, the target encoding for
   encode); it is borrowed only for the duration of the call. */
static PyObject *node_serialize_str(PyObject *self, PyObject *formatter_obj, PyObject *layout_obj, int sort_attributes,
                                    int meta_charset, int xml, const char *charset) {
    th_serialize_opts opts = {0, sort_attributes, meta_charset, charset, (Py_ssize_t)strlen(charset), xml};
    if (resolve_formatter(state_of(self), formatter_obj, &opts.formatter) < 0) {
        return NULL;
    }
    enum th_layout_mode mode;
    const Py_UCS4 *indent_unit = NULL;
    Py_ssize_t indent_len = 0;
    th_minify_opts minify_opts;
    if (resolve_layout(state_of(self), layout_obj, &mode, &indent_unit, &indent_len, &minify_opts) < 0) {
        return NULL;
    }
    Py_ssize_t out_len;
    Py_UCS4 *data;
    /* the serializer walks the whole subtree; hold the per-tree lock so a concurrent
       mutate cannot rewire it mid-walk (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    if (mode == TH_LAYOUT_MINIFY) {
        data = th_node_minify(tree_of(self), ((NodeObject *)self)->node, &minify_opts, &opts, &out_len);
    } else {
        const Py_UCS4 *indent = mode == TH_LAYOUT_INDENT ? indent_unit : NULL;
        data = th_node_serialize(tree_of(self), ((NodeObject *)self)->node, &opts, indent, indent_len, &out_len);
    }
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

/* Serialize self to a str under the Html config in spec (a borrowed dict of the
   config's non-default values, or NULL for the defaults), writing charset as the
   meta_charset label. */
/* Parse an Html config's non-default values (spec) into the four serialize settings,
   leaving each unmentioned one at the caller's default. Returns 0 on success, -1 with
   an exception set. */
static int parse_html_spec(PyObject *spec, PyObject **formatter_obj, PyObject **layout_obj, int *sort_attributes,
                           int *meta_charset, int *xml) {
    static char *keywords[] = {"formatter", "layout", "sort_attributes", "meta_charset", "xml", NULL};
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int parsed = PyArg_ParseTupleAndKeywords(empty, spec, "|$OOppp", keywords, formatter_obj, layout_obj,
                                             sort_attributes, meta_charset, xml);
    Py_DECREF(empty);
    return parsed ? 0 : -1;
}

static PyObject *node_serialize_from_spec(PyObject *self, PyObject *spec, const char *charset) {
    PyObject *formatter_obj = NULL;
    PyObject *layout_obj = NULL;
    int sort_attributes = 0;
    int meta_charset = 0;
    int xml = 0;
    if (parse_html_spec(spec, &formatter_obj, &layout_obj, &sort_attributes, &meta_charset, &xml) < 0) {
        return NULL;
    }
    return node_serialize_str(self, formatter_obj, layout_obj, sort_attributes, meta_charset, xml, charset);
}

/* Resolve a serialize/encode call's options object to a str rendering under charset.
   None renders the defaults; otherwise the config's _unpack() supplies its keywords. */
static PyObject *node_serialize_options(PyObject *self, PyObject *options, const char *charset) {
    if (options == NULL || options == Py_None) {
        return node_serialize_str(self, NULL, NULL, 0, 0, 0, charset);
    }
    PyObject *spec = config_unpack(options, state_of(self)->html_config_type, "Html");
    if (spec == NULL) {
        return NULL;
    }
    PyObject *result = node_serialize_from_spec(self, spec, charset);
    Py_DECREF(spec);
    return result;
}

static PyObject *node_serialize(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"options", NULL};
    PyObject *options = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", keywords, &options)) {
        return NULL;
    }
    return node_serialize_options(self, options, "utf-8");
}

static PyObject *node_encode(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"encoding", "options", NULL};
    const char *encoding = "utf-8";
    PyObject *options = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sO", keywords, &encoding, &options)) {
        return NULL;
    }
    PyObject *text = node_serialize_options(self, options, encoding);
    if (text == NULL) {
        return NULL;
    }
    PyObject *encoded = PyUnicode_AsEncodedString(text, encoding, NULL);
    Py_DECREF(text);
    return encoded;
}

/* Parse a Canonical config's non-default values (spec) into the c14n options, mapping
   the version label to 0 (1.0) / 1 (1.1) and resolving the exclusive-mode inclusive
   prefix tuple to a C array. On success 0 is returned and *inclusive_utf8 owns a
   PyMem block the caller frees; on error -1 with an exception set. The prefix strings
   the array points at stay valid while inclusive (a member of spec) is alive. */
static int parse_canonical_spec(PyObject *spec, th_c14n_opts *opts, const char ***inclusive_utf8) {
    static char *keywords[] = {"version", "exclusive", "with_comments", "inclusive_ns_prefixes", NULL};
    PyObject *version_obj = NULL;
    PyObject *inclusive = NULL;
    opts->exclusive = 0;
    opts->with_comments = 0;
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int parsed = PyArg_ParseTupleAndKeywords(empty, spec, "|$OppO", keywords, &version_obj, &opts->exclusive,
                                             &opts->with_comments, &inclusive);
    Py_DECREF(empty);
    if (!parsed) { /* GCOVR_EXCL_BR_LINE: _unpack only ever emits the known keys */
        return -1; /* GCOVR_EXCL_LINE: argument-error path */
    }
    /* _unpack emits version only when it differs from the "1.0" default, i.e. is "1.1",
       so its mere presence selects c14n 1.1 */
    opts->version = version_obj != NULL;
    opts->inclusive = NULL;
    opts->inclusive_count = 0;
    *inclusive_utf8 = NULL;
    if (inclusive == NULL) {
        /* _unpack omits an empty prefix tuple (it equals the default), so a present
           inclusive is always non-empty and the malloc below is never zero-sized */
        return 0;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(inclusive);
    const char **names = PyMem_Malloc((size_t)count * sizeof(const char *));
    if (names == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        names[index] = PyUnicode_AsUTF8(PyTuple_GET_ITEM(inclusive, index));
        if (names[index] == NULL) { /* GCOVR_EXCL_BR_LINE: the config validates str members */
            PyMem_Free(names);      /* GCOVR_EXCL_LINE: encode-failure path */
            return -1;              /* GCOVR_EXCL_LINE: encode-failure path */
        }
    }
    opts->inclusive = names;
    opts->inclusive_count = count;
    *inclusive_utf8 = names;
    return 0;
}

static PyObject *node_canonicalize_from_spec(PyObject *self, PyObject *spec) {
    th_c14n_opts opts = {0, 0, 0, NULL, 0};
    const char **inclusive_utf8;
    /* parse_canonical_spec fails only on the excluded allocation/encode paths */
    if (parse_canonical_spec(spec, &opts, &inclusive_utf8) < 0) { /* GCOVR_EXCL_BR_LINE */
        return NULL;                                              /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out_len;
    Py_UCS4 *data;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    data = th_node_canonicalize(tree_of(self), ((NodeObject *)self)->node, &opts, &out_len);
    Py_END_CRITICAL_SECTION();
    PyMem_Free(inclusive_utf8);
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *text = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *encoded = PyUnicode_AsUTF8String(text);
    Py_DECREF(text);
    return encoded;
}

static PyObject *node_canonicalize(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"options", NULL};
    PyObject *options = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", keywords, &options)) {
        return NULL;
    }
    if (options == NULL || options == Py_None) {
        PyObject *spec = PyDict_New();
        if (spec == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *result = node_canonicalize_from_spec(self, spec);
        Py_DECREF(spec);
        return result;
    }
    PyObject *spec = config_unpack(options, state_of(self)->canonical_config_type, "Canonical");
    if (spec == NULL) {
        return NULL;
    }
    PyObject *result = node_canonicalize_from_spec(self, spec);
    Py_DECREF(spec);
    return result;
}

static PyObject *node_to_source(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_ssize_t out_len;
    Py_UCS4 *data;
    /* hold the per-tree lock so a concurrent mutate cannot rewire the subtree mid-walk */
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    data = th_node_serialize_source(tree_of(self), ((NodeObject *)self)->node, &out_len);
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

/* Build the serialize_iter iterator from the resolved output options. The stream is
   always str (UTF-8 conceptually, like serialize), so charset is the static "utf-8".
   A Minify layout is rejected: its transforms analyze the whole tree, so there is no
   per-node stream to resume. */
static PyObject *node_make_serialize_iter(PyObject *self, PyObject *formatter_obj, PyObject *layout_obj,
                                          int sort_attributes, int meta_charset, int xml) {
    module_state *state = state_of(self);
    th_serialize_opts opts = {0, sort_attributes, meta_charset, "utf-8", (Py_ssize_t)strlen("utf-8"), xml};
    if (resolve_formatter(state, formatter_obj, &opts.formatter) < 0) {
        return NULL;
    }
    enum th_layout_mode mode;
    const Py_UCS4 *indent_unit = NULL;
    Py_ssize_t indent_len = 0;
    th_minify_opts minify_opts;
    if (resolve_layout(state, layout_obj, &mode, &indent_unit, &indent_len, &minify_opts) < 0) {
        return NULL;
    }
    if (mode == TH_LAYOUT_MINIFY) {
        PyErr_SetString(PyExc_ValueError,
                        "serialize_iter() cannot stream a Minify layout; minification needs the whole tree");
        return NULL;
    }
    const Py_UCS4 *indent = mode == TH_LAYOUT_INDENT ? indent_unit : NULL;
    PyObject *layout = mode == TH_LAYOUT_INDENT ? layout_obj : NULL;
    return serialize_iter_new(state, ((NodeObject *)self)->handle, ((NodeObject *)self)->node, &opts, indent,
                              indent_len, layout);
}

static PyObject *node_serialize_iter(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"options", NULL};
    PyObject *options = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", keywords, &options)) {
        return NULL;
    }
    if (options == NULL || options == Py_None) {
        return node_make_serialize_iter(self, NULL, NULL, 0, 0, 0);
    }
    PyObject *spec = config_unpack(options, state_of(self)->html_config_type, "Html");
    if (spec == NULL) {
        return NULL;
    }
    PyObject *formatter_obj = NULL;
    PyObject *layout_obj = NULL;
    int sort_attributes = 0;
    int meta_charset = 0;
    int xml = 0;
    int parsed = parse_html_spec(spec, &formatter_obj, &layout_obj, &sort_attributes, &meta_charset, &xml);
    Py_DECREF(spec);
    if (parsed < 0) {
        return NULL;
    }
    return node_make_serialize_iter(self, formatter_obj, layout_obj, sort_attributes, meta_charset, xml);
}
