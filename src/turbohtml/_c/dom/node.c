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
    while (state->node_freelist != NULL) {
        NodeObject *self = (NodeObject *)state->node_freelist;
        state->node_freelist = (PyObject *)self->node;
        Py_TYPE(self)->tp_free(self); /* the type ref was dropped on push; the types are still live here */
    }
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
    for (int i = 0; i < count; i++) {
        if (strcmp(text, choices[i]) == 0) {
            *out = i;
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

/* Unpack a renderer's config object to its keyword dict via _unpack(); a value that is
   not the expected config type has no _unpack, so the AttributeError is replaced with a
   clear TypeError naming the type. Returns a new dict reference, or NULL with the error
   set. */
static PyObject *config_unpack(PyObject *options, const char *type_name) {
    PyObject *spec = PyObject_CallMethod(options, "_unpack", NULL);
    if (spec == NULL) {
        PyErr_Clear();
        PyErr_Format(PyExc_TypeError, "options must be a %s, not %.200s", type_name, Py_TYPE(options)->tp_name);
    }
    return spec;
}

/* The shared dispatch for a renderer whose only argument is a config object: parse
   the single optional `options`, unpack it to the renderer keyword dict (or NULL for
   the defaults), and render. The unpacked dict owns the borrowed values for the call. */
static PyObject *node_render_with_options(PyObject *self, PyObject *args, PyObject *kwds, node_render_fn render,
                                          const char *type_name) {
    PyObject *options = NULL;
    static char *kw[] = {"options", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O", kw, &options)) {
        return NULL;
    }
    if (options == NULL || options == Py_None) {
        return render(self, NULL);
    }
    PyObject *spec = config_unpack(options, type_name);
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
    return node_render_with_options(self, args, kwds, node_markdown_render, "Markdown");
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
    return node_render_with_options(self, args, kwds, node_text_render, "PlainText");
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
    Py_ssize_t n = PyDict_Size(rules_dict);
    text_rule *rules = PyMem_Calloc((size_t)(n > 0 ? n : 1), sizeof(text_rule));
    PyObject **labels = PyMem_Calloc((size_t)(n > 0 ? n : 1), sizeof(PyObject *));
    Py_UCS4 **values = PyMem_Calloc((size_t)(n > 0 ? n : 1), sizeof(Py_UCS4 *));
    if (rules == NULL || labels == NULL || values == NULL) { /* GCOVR_EXCL_BR_LINE: cannot force an alloc failure */
        PyMem_Free(rules);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(labels);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(values);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *key, *value;
    Py_ssize_t pos = 0, r = 0;
    int failed = 0;
    while (PyDict_Next(rules_dict, &pos, &key, &value)) {
        if (text_parse_rule(key, value, &rules[r], &labels[r], &values[r]) < 0) {
            failed = 1;
            break;
        }
        r++;
    }
    PyObject *result = NULL;
    if (!failed) {
        text_span *spans = NULL;
        Py_ssize_t span_count = 0, out_len = 0;
        Py_UCS4 *data;
        Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
        data = th_node_annotated_text(tree_of(self), ((NodeObject *)self)->node, &opt, rules, n, &spans, &span_count,
                                      &out_len);
        Py_END_CRITICAL_SECTION();
        if (data == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: merge of the unreachable alloc-failure block */
        /* data is NULL only on an alloc failure, so the NULL arms are unreachable */
        PyObject *text = data != NULL ? ucs4_to_str(data, out_len) : NULL;   /* GCOVR_EXCL_BR_LINE */
        PyObject *label_list = data != NULL ? PyList_New(span_count) : NULL; /* GCOVR_EXCL_BR_LINE */
        if (text != NULL && label_list != NULL) { /* GCOVR_EXCL_BR_LINE: only an alloc failure makes either NULL */
            for (Py_ssize_t i = 0; i < span_count; i++) {
                PyList_SET_ITEM(label_list, i, Py_BuildValue("nnO", spans[i].start, spans[i].end, spans[i].label));
            }
            result = PyTuple_Pack(2, text, label_list);
        }
        Py_XDECREF(text);
        Py_XDECREF(label_list);
        PyMem_Free(data);
        PyMem_Free(spans);
    }
    for (Py_ssize_t i = 0; i < r; i++) {
        Py_XDECREF(labels[i]);
        PyMem_Free(values[i]);
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
    PyObject *spec = config_unpack(options, "PlainText");
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

/* Materialize one harvested metadata buffer as a str, or None when it is absent. */
static PyObject *article_field(const Py_UCS4 *data, Py_ssize_t len) {
    if (data == NULL) {
        Py_RETURN_NONE;
    }
    return ucs4_to_str(data, len);
}

PyDoc_STRVAR(article_doc, "article()\n--\n\n"
                          "Return an Article record for the dominant content under this node: the\n"
                          "scored content body (element), its layout-aware plain text (text, as\n"
                          "main_text()), and the page metadata harvested from the document -- title,\n"
                          "byline, date, description and lang. element is None and text is empty when\n"
                          "nothing reads as content; each metadata field is None when absent. Title\n"
                          "comes from <h1>, then og:title, then <title>; byline from a rel=author\n"
                          "link, then a meta author, then article:author; date from <time>, then\n"
                          "article:published_time, then a common date meta; description from\n"
                          "og:description, then a meta description; lang from <html lang>. Pure C.");

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
    th_article_meta_clear(&meta);

    /* Py_BuildValue("(N...)") steals each reference and, if any field is NULL from
       an (unforceable) allocation failure, propagates the error and frees the rest. */
    PyObject *args = Py_BuildValue("(NNNNNNN)", element, text, title, byline, date, description, lang);
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
                         ":returns: the matching descendant Elements in document order.");

PyDoc_STRVAR(select_one_doc, "select_one(selector, /)\n--\n\n"
                             "Find the first descendant Element matching a CSS selector.\n\n"
                             ":param selector: the CSS selector.\n"
                             ":returns: the first matching Element, or None.");

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
                          ":returns: whether this node is an Element that matches.");

PyDoc_STRVAR(closest_doc, "closest(selector, /)\n--\n\n"
                          "Find the nearest Element matching a CSS selector, testing this node then each\n"
                          "ancestor.\n\n"
                          ":param selector: the CSS selector.\n"
                          ":returns: the nearest matching Element, or None.");

PyDoc_STRVAR(prune_doc, "prune(selector, /)\n--\n\n"
                        "Keep only the descendants matching a CSS selector, together with their\n"
                        "ancestors up to this node and the whole subtree under each match, and remove\n"
                        "every other descendant in place. With no match the subtree is emptied. This\n"
                        "trims a parsed document to the parts of interest after a normal WHATWG parse,\n"
                        "the way BeautifulSoup's SoupStrainer filters a document while parsing it.\n\n"
                        ":param selector: the CSS selector the kept descendants must match.\n"
                        ":returns: this node.");

PyDoc_STRVAR(remove_doc, "remove(selector, /)\n--\n\n"
                         "Drop every descendant Element matching the CSS selector, each with its\n"
                         "whole subtree, and return this node. The bulk inverse of prune (which keeps\n"
                         "the matches): the destructive counterpart of selectolax's strip_tags and\n"
                         "w3lib's remove_tags_with_content, and of jQuery's .remove().");

PyDoc_STRVAR(strip_tags_doc, "strip_tags(selector, /)\n--\n\n"
                             "Unwrap every descendant Element matching the CSS selector, replacing each\n"
                             "match with its children in place while keeping that content, and return\n"
                             "this node. The bulk form of unwrap, matching selectolax's unwrap_tags,\n"
                             "w3lib's remove_tags, and jQuery's .unwrap().");

static PyObject *node_serialize(PyObject *self, PyObject *args, PyObject *kwds);

static PyObject *node_encode(PyObject *self, PyObject *args, PyObject *kwds);

PyDoc_STRVAR(serialize_doc, "serialize(options=None)\n--\n\n"
                            "Serialize this node and its subtree to a str.\n\n"
                            ":param options: an Html configuration object (formatter, layout, attribute\n"
                            "    ordering, and meta-charset handling), or None for the defaults.\n"
                            ":returns: the serialized markup.");

PyDoc_STRVAR(encode_doc, "encode(encoding='utf-8', options=None)\n--\n\n"
                         "Serialize this node and its subtree to bytes, with the same formatting controls\n"
                         "as serialize().\n\n"
                         ":param encoding: the codec to encode the markup with.\n"
                         ":param options: an Html configuration object, or None for the defaults.\n"
                         ":returns: the serialized markup encoded as bytes.");

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
             ":returns: the first matching Element, or None.");

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
             ":returns: the matching Elements in document order.");

PyDoc_STRVAR(insert_before_doc, "insert_before(*nodes)\n--\n\n"
                                "Insert each node into this node's parent right before this node, in order. A\n"
                                "node already in a tree is moved; a node from another tree is adopted by copy.\n\n"
                                ":param nodes: the nodes to insert.");

PyDoc_STRVAR(insert_after_doc, "insert_after(*nodes)\n--\n\n"
                               "Insert each node into this node's parent right after this node, in order,\n"
                               "with the same move-or-adopt rule as insert_before().\n\n"
                               ":param nodes: the nodes to insert.");

PyDoc_STRVAR(replace_with_doc, "replace_with(*nodes)\n--\n\n"
                               "Put nodes where this node is, in order, and detach this node, which becomes a\n"
                               "standalone root the caller still holds. With no nodes this just removes this\n"
                               "node.\n\n"
                               ":param nodes: the nodes to put in this node's place.");

PyDoc_STRVAR(wrap_doc, "wrap(wrapper, /)\n--\n\n"
                       "Put this node inside wrapper, in this node's place.\n\n"
                       ":param wrapper: the element to wrap this node in.\n"
                       ":returns: wrapper, now holding this node.");

PyDoc_STRVAR(wrap_siblings_doc, "wrap_siblings(wrapper, /, *, until=None)\n--\n\n"
                                "Wrap this node and the siblings that follow it in wrapper in one move; the\n"
                                "bulk form of wrap() for a contiguous run. wrapper lands where this node was.\n\n"
                                ":param wrapper: the element to wrap the run in.\n"
                                ":param until: the last sibling to include (this node or a later one);\n"
                                "    None reaches to the last sibling.\n"
                                ":returns: wrapper, now holding the run.");

PyDoc_STRVAR(unwrap_doc, "unwrap()\n--\n\n"
                         "Replace this node with its children, the inverse of wrap().\n\n"
                         ":returns: this node, detached.");

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
    {"matches", node_css_matches, METH_O, matches_doc},
    {"closest", node_css_closest, METH_O, closest_doc},
    {"prune", node_prune, METH_O, prune_doc},
    {"remove", node_remove, METH_O, remove_doc},
    {"strip_tags", node_strip_tags, METH_O, strip_tags_doc},
    {"re", (PyCFunction)(void (*)(void))node_re, METH_VARARGS | METH_KEYWORDS, re_doc},
    {"re_first", (PyCFunction)(void (*)(void))node_re_first, METH_VARARGS | METH_KEYWORDS, re_first_doc},
    {"serialize", (PyCFunction)(void (*)(void))node_serialize, METH_VARARGS | METH_KEYWORDS, serialize_doc},
    {"encode", (PyCFunction)(void (*)(void))node_encode, METH_VARARGS | METH_KEYWORDS, encode_doc},
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
                                    int meta_charset, const char *charset) {
    th_serialize_opts opts = {0, sort_attributes, meta_charset, charset, (Py_ssize_t)strlen(charset)};
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
static PyObject *node_serialize_from_spec(PyObject *self, PyObject *spec, const char *charset) {
    static char *keywords[] = {"formatter", "layout", "sort_attributes", "meta_charset", NULL};
    PyObject *formatter_obj = NULL;
    PyObject *layout_obj = NULL;
    int sort_attributes = 0;
    int meta_charset = 0;
    PyObject *empty = PyTuple_New(0);
    if (empty == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int parsed = PyArg_ParseTupleAndKeywords(empty, spec, "|$OOpp", keywords, &formatter_obj, &layout_obj,
                                             &sort_attributes, &meta_charset);
    Py_DECREF(empty);
    if (!parsed) {
        return NULL;
    }
    return node_serialize_str(self, formatter_obj, layout_obj, sort_attributes, meta_charset, charset);
}

/* Resolve a serialize/encode call's options object to a str rendering under charset.
   None renders the defaults; otherwise the config's _unpack() supplies its keywords. */
static PyObject *node_serialize_options(PyObject *self, PyObject *options, const char *charset) {
    if (options == NULL || options == Py_None) {
        return node_serialize_str(self, NULL, NULL, 0, 0, charset);
    }
    PyObject *spec = config_unpack(options, "Html");
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
