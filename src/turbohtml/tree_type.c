/* The navigable Node tree: the public, typed surface over the C parser.

   A parse() call builds a th_tree in the arena and hands back a Document. A
   private _TreeHandle keeps the tree and the input string it borrows from alive;
   every Node holds a strong reference to that handle, so the whole tree survives
   as long as any node reachable from it. Traversal creates Node wrappers lazily:
   walking into a child or sibling allocates one small object pointing at the
   existing arena node, never copying the subtree.

   The wrappers form a sealed hierarchy (Document / Element / Text / Comment /
   Doctype, plus the bare Node for a <template>'s content fragment) sharing one C
   struct; the node's th_node type selects which Python type wraps it. */

#include "tokenizer_py.h"

#include "ascii.h"
#include "encoding.h"
#include "selector.h"
#include "treebuilder.h"
#include "xpath.h"

/* A per-tree cache of compiled CSS selectors. A repeated select()/select_one()
   with the same selector string then reuses the parse instead of re-running the
   UCS4 copy, recursive-descent parse, and allocations on every call. The cache is
   per tree because attribute atoms resolve against the tree, and is keyed by both
   the selector str and the tree's attribute generation so a mutation that interns
   a new attribute name (which could change an "absent" resolution) invalidates a
   stale entry. Move-to-front, capped, guarded by the handle's critical section. */
#define SEL_CACHE_CAP 16
#define XPATH_CACHE_CAP 16

typedef struct {
    PyObject *key;          /* the selector str (a strong reference) */
    sel_compiled *compiled; /* compiled against this handle's tree */
    uint32_t attr_gen;      /* tree attribute generation when compiled */
} sel_cache_entry;

/* A compiled XPath program is tree-independent (it resolves atoms at evaluation),
   so unlike the selector cache it needs no generation to invalidate it. */
typedef struct {
    PyObject *key; /* the expression str (a strong reference) */
    xp_program *prog;
} xpath_cache_entry;

typedef struct {
    PyObject_HEAD th_tree *tree;
    PyObject *source;   /* the input str whose storage the tree's spans borrow */
    PyObject *encoding; /* the resolved encoding name for bytes input, else None */
    /* Lazy per-tree element index, bucketed by tag atom: index_nodes holds every
       element in document (pre-order) order grouped by atom, with the bucket for
       atom a spanning index_nodes[index_offsets[a] .. index_offsets[a + 1]).
       Built on the first whole-tree query and reused until a structural mutation
       drops it. Read and written only under the handle's critical section. */
    th_node **index_nodes;
    Py_ssize_t *index_offsets; /* th_tag_count + 2 entries; NULL until built */
    int index_built;
    sel_cache_entry sel_cache[SEL_CACHE_CAP];
    int sel_cache_len;
    xpath_cache_entry xpath_cache[XPATH_CACHE_CAP];
    int xpath_cache_len;
} HandleObject;

typedef struct {
    PyObject_HEAD PyObject *handle; /* _TreeHandle keeping tree + source alive */
    th_node *node;
} NodeObject;

enum walk_mode { WALK_DESCENDANTS, WALK_ANCESTORS, WALK_NEXT_SIBLINGS, WALK_PREVIOUS_SIBLINGS, WALK_PRECEDING };

/* The axes find()/find_all() search over; the order matches the Axis enum members
   so a member's value is its enum index. */
enum th_axis {
    TH_AXIS_DESCENDANTS,
    TH_AXIS_CHILDREN,
    TH_AXIS_ANCESTORS,
    TH_AXIS_NEXT_SIBLINGS,
    TH_AXIS_PREVIOUS_SIBLINGS,
    TH_AXIS_FOLLOWING,
    TH_AXIS_PRECEDING,
};

/* Member counts of the three public enums, also the size of their cached-member
   arrays in module_state. */
#define TH_NAMESPACE_COUNT 3
#define TH_AXIS_COUNT 7
#define TH_FORMATTER_COUNT 3

typedef struct {
    PyObject_HEAD PyObject *handle;
    th_node *root;    /* subtree bound for pre-order walks (unused for ancestors) */
    th_node *current; /* next node to yield, or NULL when exhausted */
    int mode;
} WalkerObject;

typedef struct {
    PyObject_HEAD PyObject *handle;
    th_node *root;    /* the subtree whose Text descendants are yielded */
    th_node *current; /* next node to consider in pre-order, or NULL when exhausted */
    int strip;        /* drop surrounding whitespace and skip blank runs */
} StringWalkerObject;

typedef struct {
    PyObject_HEAD PyObject *handle;
    th_node *node; /* the element whose live attributes this view exposes */
} AttrsObject;

static module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static th_tree *tree_of(PyObject *self) {
    return ((HandleObject *)((NodeObject *)self)->handle)->tree;
}

/* Borrow the (tree, node) a Python tree node wraps, for the in-C sanitizer in sanitize.c. Sets a TypeError and
   returns -1 if obj is not one of this module's tree nodes. */
int turbohtml_node_borrow(PyObject *module, PyObject *obj, th_tree **tree, th_node **node) {
    module_state *state = PyModule_GetState(module);
    if (!PyObject_TypeCheck(obj, (PyTypeObject *)state->node_type)) {
        PyErr_SetString(PyExc_TypeError, "sanitize expected a turbohtml element");
        return -1;
    }
    *node = ((NodeObject *)obj)->node;
    *tree = ((HandleObject *)((NodeObject *)obj)->handle)->tree;
    return 0;
}

static PyObject *ucs4_to_str(const Py_UCS4 *data, Py_ssize_t len) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
}

/* Realize one of the th_node_* accessors (which return a PyMem UCS4 buffer) into
   a str, freeing the buffer. */
static PyObject *str_from_accessor(Py_UCS4 *(*accessor)(th_tree *, th_node *, Py_ssize_t *), th_tree *tree,
                                   th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *buf = accessor(tree, node, &len);
    if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(buf, len);
    PyMem_Free(buf);
    return result;
}

/* --------------------------------------------------------- node lifetime */

static PyObject *type_for_node(module_state *state, const th_node *node) {
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_DOCUMENT:
        return state->document_type;
    case TH_NODE_ELEMENT:
        return state->element_type;
    case TH_NODE_TEXT:
        return state->text_type;
    case TH_NODE_COMMENT:
        return state->comment_type;
    case TH_NODE_DOCTYPE:
        return state->doctype_type;
    case TH_NODE_PI:
        return state->pi_type;
    case TH_NODE_CDATA:
        return state->cdata_type;
    case TH_NODE_CONTENT:
        break; /* a template's content fragment wraps as the bare Node */
    }
    return state->node_type;
}

static PyObject *node_wrap(module_state *state, PyObject *handle, th_node *node) {
    if (node == NULL) {
        Py_RETURN_NONE;
    }
    PyTypeObject *type = (PyTypeObject *)type_for_node(state, node);
    NodeObject *self = (NodeObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(handle);
    self->node = node;
    return (PyObject *)self;
}

static void node_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((NodeObject *)self)->handle);
    type->tp_free(self);
    Py_DECREF(type);
}

static int is_node(PyObject *obj, module_state *state) {
    return PyObject_TypeCheck(obj, (PyTypeObject *)state->node_type);
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

/* --------------------------------------------------------------- walking */

static th_node *preorder_next(th_node *current, th_node *root) {
    if (current->first_child != NULL) {
        return current->first_child;
    }
    /* current can be NULL here if the cursor node was detached (extract()) mid-walk:
       its parent chain no longer reaches root, so end the walk instead of dereferencing. */
    while (current != NULL && current != root) {
        if (current->next_sibling != NULL) {
            return current->next_sibling;
        }
        current = current->parent;
    }
    return NULL;
}

/* The node before this one in document order: the deepest last descendant of the
   previous sibling, else the parent. */
static th_node *previous_element(th_node *node) {
    if (node->prev_sibling != NULL) {
        th_node *back = node->prev_sibling;
        while (back->last_child != NULL) {
            back = back->last_child;
        }
        return back;
    }
    return node->parent;
}

/* The node after this one's whole subtree in document order: the next sibling, or
   the nearest ancestor's next sibling, or NULL at the end of the document. */
static th_node *subtree_next(th_node *node) {
    while (node != NULL) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
    }
    return NULL;
}

static int is_ancestor(th_node *candidate, th_node *node) {
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        if (parent == candidate) {
            return 1;
        }
    }
    return 0;
}

/* Walk back in document order from current, skipping origin's ancestors so the
   preceding axis stays disjoint from the ancestors axis. */
static th_node *preceding_skip(th_node *current, th_node *origin) {
    while (current != NULL && is_ancestor(current, origin)) {
        current = previous_element(current);
    }
    return current;
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

static PyType_Spec walker_spec = {
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

static PyType_Spec string_walker_spec = {
    .name = "turbohtml._html._StringIterator",
    .basicsize = sizeof(StringWalkerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = string_walker_slots,
};

/* ------------------------------------------------- shared Node behavior */

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
static PyObject *node_get_text(PyObject *self, void *Py_UNUSED(closure)) {
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

PyDoc_STRVAR(
    to_markdown_doc,
    "to_markdown(*, heading_style='atx', bullets='-', strong='**', emphasis='*', strikethrough='keep', "
    "ignore_emphasis=False, sub_symbol='', sup_symbol='', code_block_style='fenced', code_language='', "
    "mark_code=False, link_style='inline', autolink=True, link_title=False, ignore_links=False, "
    "skip_internal_links=False, base_url='', image_mode='markdown', default_image_alt='', table_mode='markdown', "
    "table_header='first', pad_tables=False, escape_mode='minimal', escape_asterisks=True, escape_underscores=True, "
    "line_break='spaces', block_spacing='double', document_strip='strip', quote_open='\"', quote_close='\"', "
    "google_doc=False, google_list_indent=36, hide_strikethrough=False)\n--\n\n"
    "Render this node and its subtree as Markdown. The keyword options cover the\n"
    "markdownify and html2text configuration surface; the defaults emit opinionated\n"
    "GitHub-Flavored Markdown. google_doc reads the inline-CSS styling a Google Docs\n"
    "export carries (bold/italic/strike weights and list margins).");

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

static PyObject *node_to_markdown(PyObject *self, PyObject *args, PyObject *kwds) {
    md_opts opt = th_markdown_default_opts();
    PyObject *heading = NULL, *strike = NULL, *code_style = NULL, *link = NULL, *image = NULL, *table = NULL;
    PyObject *header = NULL, *escape = NULL, *brk = NULL, *spacing = NULL, *docstrip = NULL;
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
                         "document_strip",
                         "quote_open",
                         "quote_close",
                         "google_doc",
                         "google_list_indent",
                         "hide_strikethrough",
                         NULL};
    if (!PyArg_ParseTupleAndKeywords(
            args, kwds, "|$OsssOpssOspOppppsOsOOpOppOOOsspip", kw, &heading, &opt.bullets, &opt.strong, &opt.emphasis,
            &strike, &ignore_emphasis, &opt.sub, &opt.sup, &code_style, &opt.code_language, &mark_code, &link,
            &opt.autolink, &opt.link_title, &opt.ignore_links, &opt.skip_internal_links, &opt.base_url, &image,
            &opt.default_image_alt, &table, &header, &opt.pad_tables, &escape, &opt.escape_asterisks,
            &opt.escape_underscores, &brk, &spacing, &docstrip, &opt.quote_open, &opt.quote_close, &opt.google_doc,
            &opt.google_list_indent, &opt.hide_strikethrough)) {
        return NULL;
    }
    static const char *const headings[] = {"atx", "atx_closed", "setext"};
    static const char *const strikes[] = {"keep", "hide"};
    static const char *const codes[] = {"fenced", "indented"};
    static const char *const links[] = {"inline", "reference"};
    static const char *const images[] = {"markdown", "alt", "ignore"};
    static const char *const tables[] = {"markdown", "strip"};
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
        md_resolve_enum("image_mode", image, images, 3, &opt.image_mode) < 0 ||
        md_resolve_enum("table_mode", table, tables, 2, &opt.table_mode) < 0 ||
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
    opt.keep_emphasis = !ignore_emphasis;
    opt.keep_strikethrough = keep_strike == 0;
    opt.block_spacing_single = block_spacing == 1;
    if (mark_code) {
        opt.code_mark_open = "[code]";
        opt.code_mark_close = "[/code]";
    }
    Py_ssize_t out_len;
    Py_UCS4 *data;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    data = th_node_markdown(tree_of(self), ((NodeObject *)self)->node, &opt, &out_len);
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

PyDoc_STRVAR(to_text_doc, "to_text(*, width=0, links='none', images=False, layout='extended', default_image_alt='', "
                          "table_cell_separator='  ', bullet='* ')\n--\n\n"
                          "Render this node and its subtree as layout-aware plain text: blocks\n"
                          "separated by blank lines, lists indented under their bullets, and tables\n"
                          "laid out as a column-aligned grid. The inscriptis role, in C.");

static PyObject *node_to_text(PyObject *self, PyObject *args, PyObject *kwds) {
    text_opts opt = th_text_default_opts();
    PyObject *links = NULL, *layout = NULL;
    static char *kw[] = {"width",  "links", "images", "layout", "default_image_alt", "table_cell_separator",
                         "bullet", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$iOpOsss", kw, &opt.width, &links, &opt.images, &layout,
                                     &opt.default_image_alt, &opt.cell_separator, &opt.bullet)) {
        return NULL;
    }
    static const char *const link_modes[] = {"none", "inline", "footnote"};
    static const char *const layouts[] = {"strict", "extended"};
    if (md_resolve_enum("links", links, link_modes, 3, &opt.links) < 0 ||
        md_resolve_enum("layout", layout, layouts, 2, &opt.extended) < 0) {
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

PyDoc_STRVAR(to_annotated_text_doc,
             "to_annotated_text(annotation_rules, *, width=0, links='none', images=False, layout='extended', "
             "default_image_alt='', table_cell_separator='  ', bullet='* ')\n--\n\n"
             "Render layout-aware text and, for every element matching a rule in\n"
             "annotation_rules, record a labeled span over its text. Returns\n"
             "(text, [(start, end, label), ...]). Spans inside table cells are not recorded.");

static PyObject *node_to_annotated_text(PyObject *self, PyObject *args, PyObject *kwds) {
    text_opts opt = th_text_default_opts();
    PyObject *rules_dict, *links = NULL, *layout = NULL;
    static char *kw[] = {"annotation_rules",     "width",  "links", "images", "layout", "default_image_alt",
                         "table_cell_separator", "bullet", NULL};
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|$iOpOsss", kw, &rules_dict, &opt.width, &links, &opt.images,
                                     &layout, &opt.default_image_alt, &opt.cell_separator, &opt.bullet)) {
        return NULL;
    }
    if (!PyDict_Check(rules_dict)) {
        PyErr_SetString(PyExc_TypeError, "annotation_rules must be a dict");
        return NULL;
    }
    static const char *const link_modes[] = {"none", "inline", "footnote"};
    static const char *const layouts[] = {"strict", "extended"};
    if (md_resolve_enum("links", links, link_modes, 3, &opt.links) < 0 ||
        md_resolve_enum("layout", layout, layouts, 2, &opt.extended) < 0) {
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

/* ------------------------------------------------------------- repr ----- */

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

/* find/find_all live on the base so a Document, an Element, or any container
   node answers them directly; the bodies sit further down with the matching
   machinery, forward-declared here. */
static PyObject *node_find(PyObject *self, PyObject *args, PyObject *kwargs);
static PyObject *node_find_all(PyObject *self, PyObject *args, PyObject *kwargs);
static PyObject *node_select(PyObject *self, PyObject *arg);
static PyObject *node_select_one(PyObject *self, PyObject *arg);
static PyObject *node_xpath(PyObject *self, PyObject *args, PyObject *kwds);
static PyObject *node_xpath_iter(PyObject *self, PyObject *args, PyObject *kwds);
static PyObject *node_xpath_one(PyObject *self, PyObject *args, PyObject *kwds);
static PyObject *node_css_matches(PyObject *self, PyObject *arg);
static PyObject *node_css_closest(PyObject *self, PyObject *arg);

PyDoc_STRVAR(select_doc, "select(selector, /)\n--\n\n"
                         "Return the list of descendant Elements matching the CSS selector, in\n"
                         "document order. The selector grammar covers type, #id, .class, and\n"
                         "attribute selectors, the four combinators, the structural pseudo-classes\n"
                         "(including :nth-child(An+B of S)), the :is(), :where(), :has(), and :not()\n"
                         "functional pseudo-classes, and the\n"
                         ":scope, form/UI, :lang() and :dir() pseudo-classes a static tree can\n"
                         "determine; live-state pseudo-classes (:hover, :focus, ...) match nothing.\n"
                         ":is() and :where() take a forgiving list, so a bad arm is dropped.");

PyDoc_STRVAR(select_one_doc, "select_one(selector, /)\n--\n\n"
                             "Return the first descendant Element matching the CSS selector, or None.");

PyDoc_STRVAR(xpath_doc, "xpath(expression, /)\n--\n\n"
                        "Evaluate an XPath expression relative to this node and return the result:\n"
                        "a list of Elements (and attribute/text values as str) in document order for\n"
                        "a node-set. Absolute paths start at the document root.");

PyDoc_STRVAR(xpath_iter_doc, "xpath_iter(expression, /)\n--\n\n"
                             "Like xpath, but return an iterator over the results in document order.");

PyDoc_STRVAR(xpath_one_doc, "xpath_one(expression, /)\n--\n\n"
                            "Return the first result of the XPath expression in document order, or None.");

PyDoc_STRVAR(matches_doc, "matches(selector, /)\n--\n\n"
                          "Return whether this node is an Element matching the CSS selector,\n"
                          "evaluated against its own ancestors and siblings.");

PyDoc_STRVAR(closest_doc, "closest(selector, /)\n--\n\n"
                          "Return the nearest Element matching the CSS selector, testing this node\n"
                          "then each ancestor, or None.");

static PyObject *node_serialize(PyObject *self, PyObject *args, PyObject *kwds);
static PyObject *node_encode(PyObject *self, PyObject *args, PyObject *kwds);

PyDoc_STRVAR(serialize_doc, "serialize(*, formatter=Formatter.WHATWG, layout=None)\n--\n\n"
                            "Serialize this node and its subtree to a str. formatter chooses the escape\n"
                            "policy. layout chooses the whitespace: None gives the compact WHATWG form,\n"
                            "an Indent pretty-prints (adding whitespace, so it does not preserve\n"
                            "meaning), and a Minify shrinks the output while reparsing to the same tree.");

PyDoc_STRVAR(encode_doc, "encode(encoding='utf-8', *, formatter=Formatter.WHATWG, layout=None)\n--\n\n"
                         "Serialize this node and its subtree to bytes in the named encoding,\n"
                         "with the same formatter and layout controls as serialize().");

PyDoc_STRVAR(find_doc, "find(tag=None, /, *, axis=Axis.DESCENDANTS, attrs=None, class_=None, **filters)\n--\n\n"
                       "Return the first Element along axis matching the tag filter and every\n"
                       "attribute filter, or None. A filter is a str, bool, compiled regex,\n"
                       "callable, or a list of those.");

PyDoc_STRVAR(find_all_doc,
             "find_all(tag=None, /, *, axis=Axis.DESCENDANTS, attrs=None, class_=None, limit=None, **filters)\n--\n\n"
             "Return the list of Elements along axis matching the tag filter and every\n"
             "attribute filter, up to limit results.");

static PyObject *node_insert_before(PyObject *self, PyObject *nodes);
static PyObject *node_insert_after(PyObject *self, PyObject *nodes);
static PyObject *node_replace_with(PyObject *self, PyObject *nodes);
static PyObject *node_wrap_in(PyObject *self, PyObject *wrapper_obj);
static PyObject *node_unwrap(PyObject *self, PyObject *ignored);
static PyObject *node_extract(PyObject *self, PyObject *ignored);
static PyObject *node_decompose(PyObject *self, PyObject *ignored);
static PyObject *node_copy(PyObject *self, PyObject *ignored);
static PyObject *node_deepcopy(PyObject *self, PyObject *memo);
static PyObject *node_reduce(PyObject *self, PyObject *ignored);

PyDoc_STRVAR(insert_before_doc, "insert_before(*nodes)\n--\n\n"
                                "Insert each node into this node's parent right before this node, in order.\n"
                                "A node already in a tree is moved; a node from another tree is adopted by\n"
                                "copy.");

PyDoc_STRVAR(insert_after_doc, "insert_after(*nodes)\n--\n\n"
                               "Insert each node into this node's parent right after this node, in order,\n"
                               "with the same move-or-adopt rule as insert_before().");

PyDoc_STRVAR(replace_with_doc, "replace_with(*nodes)\n--\n\n"
                               "Put nodes where this node is, in order, and detach this node, which\n"
                               "becomes a standalone root the caller still holds. With no nodes this just\n"
                               "removes this node.");

PyDoc_STRVAR(wrap_doc, "wrap(wrapper, /)\n--\n\n"
                       "Put this node inside wrapper, an element, in this node's place and return\n"
                       "wrapper.");

PyDoc_STRVAR(unwrap_doc, "unwrap()\n--\n\n"
                         "Replace this node with its children and return it detached (the inverse of\n"
                         "wrap).");

PyDoc_STRVAR(extract_doc, "extract()\n--\n\n"
                          "Detach this node from its parent and return it, leaving a standalone node\n"
                          "the caller can reinsert elsewhere.");

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
    {"serialize", (PyCFunction)(void (*)(void))node_serialize, METH_VARARGS | METH_KEYWORDS, serialize_doc},
    {"encode", (PyCFunction)(void (*)(void))node_encode, METH_VARARGS | METH_KEYWORDS, encode_doc},
    {"to_markdown", (PyCFunction)(void (*)(void))node_to_markdown, METH_VARARGS | METH_KEYWORDS, to_markdown_doc},
    {"to_text", (PyCFunction)(void (*)(void))node_to_text, METH_VARARGS | METH_KEYWORDS, to_text_doc},
    {"to_annotated_text", (PyCFunction)(void (*)(void))node_to_annotated_text, METH_VARARGS | METH_KEYWORDS,
     to_annotated_text_doc},
    {"insert_before", node_insert_before, METH_VARARGS, insert_before_doc},
    {"insert_after", node_insert_after, METH_VARARGS, insert_after_doc},
    {"replace_with", node_replace_with, METH_VARARGS, replace_with_doc},
    {"wrap", node_wrap_in, METH_O, wrap_doc},
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

static PyType_Spec node_spec = {
    .name = "turbohtml._html.Node",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = node_slots,
};

/* --------------------------------------------------------------- Element */

/* Attribute names the HTML standard treats as space-separated token lists. They
   surface in Element.attrs as a list[str] instead of a single string, so class
   membership and similar reads hit a list rather than re-splitting. The set is by
   name (its interned atom), not by element; invalid markup such as <div rel> is
   the only case where that differs from a tag-specific table. */
static int attr_is_token_list(uint32_t name_atom) {
    switch (name_atom) {
    case TH_ATTR_CLASS:
    case TH_ATTR_REL:
    case TH_ATTR_REV:
    case TH_ATTR_HEADERS:
    case TH_ATTR_ACCESSKEY:
    case TH_ATTR_DROPZONE:
    case TH_ATTR_SIZES:
    case TH_ATTR_SANDBOX:
    case TH_ATTR_ARCHIVE:
    case TH_ATTR_ACCEPT_CHARSET:
        return 1;
    default:
        return 0;
    }
}

/* Split a token-list attribute value on ASCII whitespace, dropping empty runs:
   "  a  b " yields ["a", "b"] and "" yields []. */
static PyObject *split_token_list(const Py_UCS4 *value, Py_ssize_t value_len) {
    PyObject *tokens = PyList_New(0);
    if (tokens == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t cursor = 0;
    while (cursor < value_len) {
        while (cursor < value_len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < value_len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor > start) {
            PyObject *token = ucs4_to_str(&value[start], cursor - start);
            if (token == NULL || PyList_Append(tokens, token) < 0) { /* GCOVR_EXCL_BR_LINE: alloc failure */
                Py_XDECREF(token);                                   /* GCOVR_EXCL_LINE: alloc-failure path */
                Py_DECREF(tokens);                                   /* GCOVR_EXCL_LINE: alloc-failure path */
                return NULL;                                         /* GCOVR_EXCL_LINE: alloc-failure path */
            }
            Py_DECREF(token);
        }
    }
    return tokens;
}

/* Build the Element.attrs mapping: token-list attributes (class and friends)
   become a list[str], everything else is a str; a valueless or empty attribute is
   the empty string (or the empty list for a token-list attribute). */
/* One attribute's name as a str. */
static PyObject *attr_name_obj(th_tree *tree, const th_node_attr *attr) {
    Py_ssize_t name_len;
    const char *bytes = th_attr_name(tree, attr->name_atom, &name_len);
    return PyUnicode_DecodeUTF8(bytes, name_len, "strict");
}

/* One attribute's value as the public object: a list[str] for a token-list attribute
   (class, rel, ...), else the str. A valueless (<x a>) or empty (<x a="">) attribute
   has value == NULL and reports "", the way getAttribute returns the empty string. */
static PyObject *attr_value_obj(const th_node_attr *attr) {
    if (attr_is_token_list(attr->name_atom)) {
        return attr->value == NULL ? PyList_New(0) : split_token_list(attr->value, attr->value_len);
    }
    return attr->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(attr->value, attr->value_len);
}

static int validate_name(PyObject *name, int is_attr);
static int element_attr_value(PyObject *value, Py_UCS4 **points, Py_ssize_t *len, int *has_value);

/* ASCII-lowercase a str key into a freshly allocated UTF-8 buffer so a lookup
   matches the parser's lowercased names; *out_len its length. NULL with TypeError
   when the key is not a str. Caller frees with PyMem_Free. */
static char *attr_key_utf8(PyObject *key, Py_ssize_t *out_len) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
        return NULL;
    }
    Py_ssize_t len;
    const char *utf8 = PyUnicode_AsUTF8AndSize(key, &len);
    if (utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: a lone-surrogate name cannot encode, hard to force */
        return NULL;    /* GCOVR_EXCL_LINE: surrogate path */
    }
    char *lower = PyMem_Malloc((size_t)(len ? len : 1));
    if (lower == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        char ch = utf8[index];
        lower[index] = ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
    }
    *out_len = len;
    return lower;
}

/* The index of the attribute named name in node, or -1 when it has none. */
static Py_ssize_t find_attr_index(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    return th_node_attr_find(tree, node, name, name_len);
}

/* The live mutable view of an element's attributes: a mapping name -> value over
   the node's own attribute array, so reads and edits go straight to the tree. */
static PyObject *attrs_new(module_state *state, PyObject *handle, th_node *node) {
    PyTypeObject *type = (PyTypeObject *)state->attrs_type;
    AttrsObject *self = (AttrsObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->handle = Py_NewRef(handle);
    self->node = node;
    return (PyObject *)self;
}

static void attrs_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    Py_DECREF(((AttrsObject *)self)->handle);
    type->tp_free(self);
    Py_DECREF(type);
}

static Py_ssize_t attrs_length(PyObject *self) {
    return ((AttrsObject *)self)->node->attr_count;
}

static PyObject *attrs_subscript(PyObject *self, PyObject *key) {
    Py_ssize_t len;
    char *name = attr_key_utf8(key, &len);
    if (name == NULL) {
        return NULL;
    }
    th_node *node = ((AttrsObject *)self)->node;
    Py_ssize_t index;
    PyObject *result = NULL;
    /* hold the per-tree lock across the lookup and the value read so a concurrent attr
       set/del cannot resize the attribute array between them (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    index = find_attr_index(tree_of(self), node, name, len);
    if (index >= 0) {
        result = attr_value_obj(&node->attrs[index]);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(name);
    if (index < 0) {
        PyErr_SetObject(PyExc_KeyError, key);
        return NULL;
    }
    return result;
}

static int attrs_ass_subscript(PyObject *self, PyObject *key, PyObject *value) {
    th_node *node = ((AttrsObject *)self)->node;
    th_tree *tree = tree_of(self);
    if (value == NULL) {
        Py_ssize_t len;
        char *name = attr_key_utf8(key, &len);
        if (name == NULL) {
            return -1;
        }
        int removed;
        Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
        removed = th_node_attr_del(tree, node, name, len);
        Py_END_CRITICAL_SECTION();
        PyMem_Free(name);
        if (!removed) {
            PyErr_SetObject(PyExc_KeyError, key);
            return -1;
        }
        return 0;
    }
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
        return -1;
    }
    if (validate_name(key, 1) < 0) {
        return -1;
    }
    Py_ssize_t len;
    char *name = attr_key_utf8(key, &len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: a validated name is a str that encodes */
        return -1;      /* GCOVR_EXCL_LINE: unreachable after validate_name */
    }
    Py_UCS4 *points;
    Py_ssize_t value_len;
    int has_value;
    int bad = element_attr_value(value, &points, &value_len, &has_value) < 0;
    int rc = -1;
    if (!bad) {
        Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
        rc = th_node_attr_set(tree, node, name, len, points, value_len, has_value);
        Py_END_CRITICAL_SECTION();
    }
    PyMem_Free(name);
    if (!bad) {
        PyMem_Free(points);
    }
    return rc < 0 ? -1 : 0;
}

static int attrs_contains(PyObject *self, PyObject *key) {
    if (!PyUnicode_Check(key)) {
        return 0; /* a non-str key is never an attribute name */
    }
    Py_ssize_t len;
    char *name = attr_key_utf8(key, &len);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: key is a str here, so this cannot fail */
        return -1;      /* GCOVR_EXCL_LINE: unreachable */
    }
    Py_ssize_t index;
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    index = find_attr_index(tree_of(self), ((AttrsObject *)self)->node, name, len);
    Py_END_CRITICAL_SECTION();
    PyMem_Free(name);
    return index >= 0;
}

static PyObject *attrs_iter(PyObject *self) {
    th_node *node = ((AttrsObject *)self)->node;
    th_tree *tree = tree_of(self);
    PyObject *names = PyList_New(node->attr_count);
    if (names == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        PyObject *name = attr_name_obj(tree, &node->attrs[index]);
        if (name == NULL) {   /* GCOVR_EXCL_BR_LINE: a stored name always decodes */
            Py_DECREF(names); /* GCOVR_EXCL_LINE: decode-failure path */
            return NULL;      /* GCOVR_EXCL_LINE: decode-failure path */
        }
        PyList_SET_ITEM(names, index, name);
    }
    PyObject *iterator = PyObject_GetIter(names);
    Py_DECREF(names);
    return iterator;
}

enum attrs_view { ATTRS_KEYS, ATTRS_VALUES, ATTRS_ITEMS };

/* Materialize the attribute names, values, or (name, value) pairs as a list. */
static PyObject *attrs_collect(PyObject *self, enum attrs_view kind) {
    th_node *node = ((AttrsObject *)self)->node;
    th_tree *tree = tree_of(self);
    PyObject *out = PyList_New(node->attr_count);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        PyObject *item;
        if (kind == ATTRS_VALUES) {
            item = attr_value_obj(attr);
        } else {
            PyObject *name = attr_name_obj(tree, attr);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: a stored name always decodes */
                Py_DECREF(out); /* GCOVR_EXCL_LINE: decode-failure path */
                return NULL;    /* GCOVR_EXCL_LINE: decode-failure path */
            }
            if (kind == ATTRS_KEYS) {
                item = name;
            } else {
                PyObject *value = attr_value_obj(attr);
                if (value == NULL) { /* GCOVR_EXCL_BR_LINE: value object build cannot be forced to fail */
                    Py_DECREF(name); /* GCOVR_EXCL_LINE: alloc-failure path */
                    Py_DECREF(out);  /* GCOVR_EXCL_LINE: alloc-failure path */
                    return NULL;     /* GCOVR_EXCL_LINE: alloc-failure path */
                }
                item = PyTuple_Pack(2, name, value);
                Py_DECREF(name);
                Py_DECREF(value);
            }
        }
        if (item == NULL) { /* GCOVR_EXCL_BR_LINE: item build cannot be forced to fail */
            Py_DECREF(out); /* GCOVR_EXCL_LINE: alloc-failure path */
            return NULL;    /* GCOVR_EXCL_LINE: alloc-failure path */
        }
        PyList_SET_ITEM(out, index, item);
    }
    return out;
}

static PyObject *attrs_keys(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return attrs_collect(self, ATTRS_KEYS);
}

static PyObject *attrs_values(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return attrs_collect(self, ATTRS_VALUES);
}

static PyObject *attrs_items(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return attrs_collect(self, ATTRS_ITEMS);
}

static PyObject *attrs_get(PyObject *self, PyObject *args) {
    PyObject *key;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTuple(args, "O|O", &key, &fallback)) {
        return NULL;
    }
    if (PyUnicode_Check(key)) {
        Py_ssize_t len;
        char *name = attr_key_utf8(key, &len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: key is a str here */
            return NULL;    /* GCOVR_EXCL_LINE: unreachable */
        }
        th_node *node = ((AttrsObject *)self)->node;
        Py_ssize_t index;
        PyObject *result = NULL;
        Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
        index = find_attr_index(tree_of(self), node, name, len);
        if (index >= 0) {
            result = attr_value_obj(&node->attrs[index]);
        }
        Py_END_CRITICAL_SECTION();
        PyMem_Free(name);
        if (index >= 0) {
            return result;
        }
    }
    return Py_NewRef(fallback);
}

static PyObject *attrs_repr(PyObject *self) {
    PyObject *items = attrs_collect(self, ATTRS_ITEMS);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *mapping = PyObject_CallFunctionObjArgs((PyObject *)&PyDict_Type, items, NULL);
    Py_DECREF(items);
    if (mapping == NULL) { /* GCOVR_EXCL_BR_LINE: dict() over a name/value list cannot fail */
        return NULL;       /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    PyObject *repr = PyObject_Repr(mapping);
    Py_DECREF(mapping);
    return repr;
}

static PyMethodDef attrs_methods[] = {
    {"get", attrs_get, METH_VARARGS, "get(name, default=None) -> the value, or default when absent"},
    {"keys", attrs_keys, METH_NOARGS, "keys() -> the attribute names in source order"},
    {"values", attrs_values, METH_NOARGS, "values() -> the attribute values in source order"},
    {"items", attrs_items, METH_NOARGS, "items() -> the (name, value) pairs in source order"},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot attrs_slots[] = {
    {Py_tp_dealloc, attrs_dealloc},
    {Py_tp_repr, attrs_repr},
    {Py_mp_length, attrs_length},
    {Py_mp_subscript, attrs_subscript},
    {Py_mp_ass_subscript, attrs_ass_subscript},
    {Py_sq_contains, attrs_contains},
    {Py_tp_iter, attrs_iter},
    {Py_tp_methods, attrs_methods},
    {0, NULL},
};

static PyType_Spec attrs_spec = {
    .name = "turbohtml._html._Attrs",
    .basicsize = sizeof(AttrsObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = attrs_slots,
};

static PyObject *element_get_tag(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text, node->text_len);
}

static PyObject *element_get_namespace(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(state_of(self)->namespaces[((NodeObject *)self)->node->ns]);
}

static PyObject *element_get_attrs(PyObject *self, void *Py_UNUSED(closure)) {
    return attrs_new(state_of(self), ((NodeObject *)self)->handle, ((NodeObject *)self)->node);
}

static PyObject *node_get_text(PyObject *self, void *closure);
static int element_set_text(PyObject *self, PyObject *value, void *closure);

static PyGetSetDef element_getset[] = {
    {"tag", element_get_tag, NULL, "the lowercased tag name", NULL},
    {"namespace", element_get_namespace, NULL, "the element's Namespace (HTML, SVG, or MATHML)", NULL},
    {"attrs", element_get_attrs, NULL,
     "the live mutable attribute mapping; token-list attributes (class, rel, ...) map to a list[str], a valueless "
     "attribute maps to None",
     NULL},
    {"text", node_get_text, element_set_text,
     "the element's text; assigning replaces all children with a single Text node", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

/* A compiled find()/find_all() query: the tag and class_ filters, the resolved
   (atom, filter) pairs for the named attribute filters, the axis to search, and
   the result cap. Every filter PyObject is borrowed from the live call. */
typedef struct {
    PyObject *tag;          /* tag filter, or NULL for no tag constraint */
    PyObject *class_filter; /* class_ filter, or NULL */
    enum th_axis axis;
    Py_ssize_t limit; /* -1 for unlimited */
    uint32_t *atoms;  /* resolved name atoms for the attribute filters */
    PyObject **filters;
    int *filter_plain; /* per attribute filter: 1 when it is a single str */
    Py_ssize_t nattr;
    /* Fast path for a plain-string tag filter: resolve the name to an atom once
       so the per-node test is an integer compare instead of building a str for
       every element. tag_plain is set when tag is a single str; tag_atom is its
       atom, or TH_TAG_UNKNOWN for a name outside the table (then the rare
       unknown-atom elements are compared by name). */
    int tag_plain;
    uint16_t tag_atom;
    /* Fast path for a plain-string class_ filter: its code points are copied
       once so each element's class tokens are compared without building a str
       per token. class_ucs4 is NULL unless class_filter is a single str. */
    Py_UCS4 *class_ucs4;
    Py_ssize_t class_len;
} query_t;

static void free_query(query_t *query) {
    PyMem_Free(query->atoms);
    PyMem_Free(query->filters);
    PyMem_Free(query->filter_plain);
    PyMem_Free(query->class_ucs4);
}

/* Whether a node's UCS4 value equals a str filter's code points, with no
   intermediate str allocation. */
static int ucs4_equals_pystr(const Py_UCS4 *value, Py_ssize_t value_len, PyObject *filter) {
    if (value_len != PyUnicode_GET_LENGTH(filter)) {
        return 0;
    }
    int kind = PyUnicode_KIND(filter);
    const void *data = PyUnicode_DATA(filter);
    for (Py_ssize_t index = 0; index < value_len; index++) {
        if (value[index] != PyUnicode_READ(kind, data, index)) {
            return 0;
        }
    }
    return 1;
}

/* The attribute carrying name_atom on a node, or NULL when absent. */
static const th_node_attr *find_node_attr(th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

static int resolve_axis(module_state *state, PyObject *value, enum th_axis *out) {
    for (int index = 0; index < TH_AXIS_COUNT; index++) {
        if (value == state->axes[index]) {
            *out = (enum th_axis)index;
            return 0;
        }
    }
    PyErr_SetString(PyExc_TypeError, "axis must be an Axis member");
    return -1;
}

/* Match one filter against a candidate value: a str (a tag name or attribute
   value), Py_None (a valueless attribute), or NULL (an absent attribute).
   Returns 1/0, or -1 with an exception set. */
static int filter_matches(module_state *state, PyObject *filter, PyObject *value) {
    if (PyBool_Check(filter)) {
        int present = value != NULL;
        return filter == Py_True ? present : !present;
    }
    if (PyList_Check(filter)) {
        Py_ssize_t count = PyList_GET_SIZE(filter);
        for (Py_ssize_t index = 0; index < count; index++) {
            int matched = filter_matches(state, PyList_GET_ITEM(filter, index), value);
            if (matched != 0) {
                return matched;
            }
        }
        return 0;
    }
    if (PyUnicode_Check(filter)) {
        if (value == NULL || value == Py_None) {
            return 0;
        }
        return PyUnicode_Compare(value, filter) == 0;
    }
    if (PyObject_TypeCheck(filter, (PyTypeObject *)state->pattern_type)) {
        if (value == NULL || value == Py_None) {
            return 0;
        }
        PyObject *match = PyObject_CallMethod(filter, "search", "O", value);
        if (match == NULL) { /* GCOVR_EXCL_BR_LINE: search over a str cannot raise */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = match != Py_None;
        Py_DECREF(match);
        return matched;
    }
    if (PyCallable_Check(filter)) {
        PyObject *result = PyObject_CallOneArg(filter, value != NULL ? value : Py_None);
        if (result == NULL) {
            return -1;
        }
        int truth = PyObject_IsTrue(result);
        Py_DECREF(result);
        return truth;
    }
    PyErr_SetString(PyExc_TypeError, "filter must be a str, bool, compiled regex, callable, or list");
    return -1;
}

/* The node's value for an attribute atom: a new str reference, Py_None for a
   valueless attribute (borrowed), or NULL when absent. *owned says whether the
   caller must DECREF (only the new-str case is owned). */
static PyObject *attr_value(th_node *node, uint32_t atom, int *owned) {
    *owned = 0;
    const th_node_attr *attr = find_node_attr(node, atom);
    if (attr == NULL) {
        return NULL;
    }
    if (attr->value == NULL) {
        return Py_None;
    }
    *owned = 1;
    return ucs4_to_str(attr->value, attr->value_len);
}

/* class_ matches the multi-valued class attribute: against the whole value first,
   then each whitespace-separated token. */
static int class_matches(module_state *state, th_node *node, PyObject *filter) {
    const th_node_attr *attr = find_node_attr(node, TH_ATTR_CLASS);
    if (attr == NULL || attr->value == NULL) {
        return filter_matches(state, filter, attr == NULL ? NULL : Py_None);
    }
    PyObject *whole = ucs4_to_str(attr->value, attr->value_len);
    if (whole == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int matched = filter_matches(state, filter, whole);
    Py_DECREF(whole);
    if (matched != 0) {
        return matched;
    }
    Py_ssize_t cursor = 0;
    while (cursor < attr->value_len) {
        while (cursor < attr->value_len && is_space(attr->value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < attr->value_len && !is_space(attr->value[cursor])) {
            cursor++;
        }
        if (cursor > start) {
            PyObject *token = ucs4_to_str(&attr->value[start], cursor - start);
            if (token == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int token_matched = filter_matches(state, filter, token);
            Py_DECREF(token);
            if (token_matched != 0) {
                return token_matched;
            }
        }
    }
    return 0;
}

/* class_matches for a plain-string filter: compare the filter's code points to
   the whole class value and then each token directly, allocating nothing. */
static int class_matches_plain(th_node *node, const Py_UCS4 *want, Py_ssize_t want_len) {
    const th_node_attr *attr = find_node_attr(node, TH_ATTR_CLASS);
    if (attr == NULL || attr->value == NULL) {
        return 0; /* a string filter never matches an absent or valueless class */
    }
    size_t bytes = (size_t)want_len * sizeof(Py_UCS4);
    if (attr->value_len == want_len && memcmp(attr->value, want, bytes) == 0) {
        return 1; /* the whole value equals the filter */
    }
    Py_ssize_t cursor = 0;
    while (cursor < attr->value_len) {
        while (cursor < attr->value_len && is_space(attr->value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < attr->value_len && !is_space(attr->value[cursor])) {
            cursor++;
        }
        if (cursor - start == want_len && memcmp(&attr->value[start], want, bytes) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Whether an element matches the whole query. Returns 1/0, or -1 with an
   exception set. */
static int node_matches(module_state *state, th_node *node, const query_t *query) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (query->tag_plain) {
        /* a known name is a pure integer compare; an unknown one can only match
           the rare unknown-atom elements, compared by name */
        if (query->tag_atom != TH_TAG_UNKNOWN) {
            if (node->atom != query->tag_atom) {
                return 0;
            }
        } else {
            if (node->atom != TH_TAG_UNKNOWN) {
                return 0;
            }
            PyObject *name = ucs4_to_str(node->text, node->text_len);
            if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int equal = PyUnicode_Compare(name, query->tag) == 0;
            Py_DECREF(name);
            if (!equal) {
                return 0;
            }
        }
    } else if (query->tag != NULL) {
        PyObject *name = ucs4_to_str(node->text, node->text_len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = filter_matches(state, query->tag, name);
        Py_DECREF(name);
        if (matched <= 0) {
            return matched;
        }
    }
    if (query->class_ucs4 != NULL) {
        if (class_matches_plain(node, query->class_ucs4, query->class_len) == 0) {
            return 0;
        }
    } else if (query->class_filter != NULL) {
        int matched = class_matches(state, node, query->class_filter);
        if (matched <= 0) {
            return matched;
        }
    }
    for (Py_ssize_t index = 0; index < query->nattr; index++) {
        if (query->filter_plain[index]) {
            /* a str filter matches only a present, valued attribute equal to it */
            const th_node_attr *attr = find_node_attr(node, query->atoms[index]);
            int equal = attr != NULL && attr->value != NULL &&
                        ucs4_equals_pystr(attr->value, attr->value_len, query->filters[index]);
            if (!equal) {
                return 0;
            }
            continue;
        }
        int owned;
        PyObject *value = attr_value(node, query->atoms[index], &owned);
        if (owned && value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = filter_matches(state, query->filters[index], value);
        if (owned) {
            Py_DECREF(value);
        }
        if (matched <= 0) {
            return matched;
        }
    }
    return 1;
}

static th_node *axis_first(th_node *node, enum th_axis axis) {
    switch (axis) {
    case TH_AXIS_ANCESTORS:
        return node->parent;
    case TH_AXIS_NEXT_SIBLINGS:
        return node->next_sibling;
    case TH_AXIS_PREVIOUS_SIBLINGS:
        return node->prev_sibling;
    case TH_AXIS_FOLLOWING:
        return subtree_next(node);
    case TH_AXIS_PRECEDING:
        return preceding_skip(previous_element(node), node);
    default: /* DESCENDANTS and CHILDREN both start at the first child */
        return node->first_child;
    }
}

static th_node *axis_next(th_node *current, th_node *origin, enum th_axis axis) {
    switch (axis) {
    case TH_AXIS_CHILDREN:
    case TH_AXIS_NEXT_SIBLINGS:
        return current->next_sibling;
    case TH_AXIS_PREVIOUS_SIBLINGS:
        return current->prev_sibling;
    case TH_AXIS_ANCESTORS:
        return current->parent;
    case TH_AXIS_FOLLOWING:
        return preorder_next(current, NULL);
    case TH_AXIS_PRECEDING:
        return preceding_skip(previous_element(current), origin);
    default: /* DESCENDANTS: pre-order, bounded to the origin's subtree */
        return preorder_next(current, origin);
    }
}

static int add_attr_filter(th_tree *tree, query_t *query, PyObject *key, PyObject *filter) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
        return -1;
    }
    Py_ssize_t len;
    const char *bytes = PyUnicode_AsUTF8AndSize(key, &len);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: a str always encodes to UTF-8 */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    query->atoms[query->nattr] = th_attr_lookup(tree, bytes, len);
    query->filters[query->nattr] = filter;
    query->filter_plain[query->nattr] = PyUnicode_Check(filter);
    query->nattr++;
    return 0;
}

static int is_reserved_key(PyObject *key, int is_find_all) {
    return PyUnicode_CompareWithASCIIString(key, "axis") == 0 || PyUnicode_CompareWithASCIIString(key, "class_") == 0 ||
           PyUnicode_CompareWithASCIIString(key, "attrs") == 0 ||
           (is_find_all && PyUnicode_CompareWithASCIIString(key, "limit") == 0);
}

/* Compile the (tag, axis, class_, attrs, limit, **filters) arguments. Always
   leaves query in a free_query-safe state, even on error. */
static int build_query(PyObject *self, PyObject *args, PyObject *kwargs, int is_find_all, query_t *query) {
    module_state *state = state_of(self);
    th_tree *tree = tree_of(self);
    query->tag = NULL;
    query->class_filter = NULL;
    query->axis = TH_AXIS_DESCENDANTS;
    query->limit = -1;
    query->atoms = NULL;
    query->filters = NULL;
    query->filter_plain = NULL;
    query->nattr = 0;
    query->tag_plain = 0;
    query->tag_atom = TH_TAG_UNKNOWN;
    query->class_ucs4 = NULL;
    query->class_len = 0;

    PyObject *tag = NULL;
    if (!PyArg_ParseTuple(args, "|O:find", &tag)) {
        return -1;
    }
    if (tag != NULL && tag != Py_None) {
        query->tag = tag;
        /* a plain str tag matches by interned atom; encode case-sensitively (a
           find() name match is exact, unlike a case-insensitive CSS type) */
        if (PyUnicode_Check(tag)) {
            Py_ssize_t blen;
            const char *bytes = PyUnicode_AsUTF8AndSize(tag, &blen);
            if (bytes == NULL) {
                /* a lone surrogate cannot encode; fall back to the str-compare path */
                PyErr_Clear();
            } else {
                query->tag_plain = 1;
                query->tag_atom = blen > 0 ? th_tag_lookup(bytes, blen) : TH_TAG_UNKNOWN;
            }
        }
    }

    PyObject *attrs_dict = NULL;
    Py_ssize_t named = 0;
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (kwargs != NULL && PyDict_Next(kwargs, &pos, &key, &value)) {
        if (PyUnicode_CompareWithASCIIString(key, "axis") == 0) {
            if (resolve_axis(state, value, &query->axis) < 0) {
                return -1;
            }
        } else if (PyUnicode_CompareWithASCIIString(key, "class_") == 0) {
            query->class_filter = value;
        } else if (PyUnicode_CompareWithASCIIString(key, "attrs") == 0) {
            if (!PyDict_Check(value)) {
                PyErr_SetString(PyExc_TypeError, "attrs must be a dict");
                return -1;
            }
            attrs_dict = value;
        } else if (is_find_all && PyUnicode_CompareWithASCIIString(key, "limit") == 0) {
            if (value == Py_None) {
                query->limit = -1;
            } else if (PyLong_Check(value)) {
                query->limit = PyLong_AsSsize_t(value);
                if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: an overflowing limit cannot be forced from a test */
                    return -1;          /* GCOVR_EXCL_LINE: overflow path */
                }
            } else {
                PyErr_SetString(PyExc_TypeError, "limit must be an int or None");
                return -1;
            }
        } else {
            named++;
        }
    }

    if (query->class_filter != NULL && PyUnicode_Check(query->class_filter)) {
        /* a plain str class_ filter matches by code-point compare, no per-token str */
        query->class_len = PyUnicode_GET_LENGTH(query->class_filter);
        query->class_ucs4 = PyUnicode_AsUCS4Copy(query->class_filter);
        if (query->class_ucs4 == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }

    Py_ssize_t capacity = named + (attrs_dict != NULL ? PyDict_GET_SIZE(attrs_dict) : 0);
    if (capacity > 0) {
        query->atoms = PyMem_Malloc((size_t)capacity * sizeof(uint32_t));
        query->filters = PyMem_Malloc((size_t)capacity * sizeof(PyObject *));
        query->filter_plain = PyMem_Malloc((size_t)capacity * sizeof(int));
        /* allocation failure cannot be forced from a test */
        if (query->atoms == NULL || query->filters == NULL || query->filter_plain == NULL) { /* GCOVR_EXCL_BR_LINE */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    pos = 0;
    while (kwargs != NULL && PyDict_Next(kwargs, &pos, &key, &value)) {
        if (is_reserved_key(key, is_find_all)) {
            continue;
        }
        if (add_attr_filter(tree, query, key, value) < 0) { /* GCOVR_EXCL_BR_LINE: a **kwargs key is always a str */
            return -1; /* GCOVR_EXCL_LINE: add_attr_filter only fails on a non-str name, impossible here */
        }
    }
    pos = 0;
    while (attrs_dict != NULL && PyDict_Next(attrs_dict, &pos, &key, &value)) {
        if (add_attr_filter(tree, query, key, value) < 0) {
            return -1;
        }
    }
    return 0;
}

/* Whether the query is a plain known tag with no other constraint on the
   descendant axis: the shape the tag-only fast path handles with a pre-order
   walk and an integer atom compare, skipping the general matcher. */
static int query_is_simple_tag(const query_t *query) {
    return query->tag_plain && query->tag_atom != TH_TAG_UNKNOWN && query->nattr == 0 && query->class_ucs4 == NULL &&
           query->class_filter == NULL && query->axis == TH_AXIS_DESCENDANTS;
}

/* Drop the cached element index so the next whole-tree query rebuilds it. Called
   under the handle's critical section from every structural mutator. */
static void handle_drop_index(PyObject *handle_obj) {
    HandleObject *handle = (HandleObject *)handle_obj;
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    handle->index_offsets = NULL;
    handle->index_nodes = NULL;
    handle->index_built = 0;
}

/* Build the per-atom element index for the whole tree. Returns 0 on success (the
   index is cached on the handle), -1 on allocation failure. The caller holds the
   handle's critical section. */
static int handle_build_index(HandleObject *handle) {
    th_node *document = th_tree_document(handle->tree);
    Py_ssize_t *offsets = PyMem_Calloc((size_t)th_tag_count + 2, sizeof(Py_ssize_t));
    if (offsets == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t total = 0;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type == TH_NODE_ELEMENT) {
            offsets[node->atom + 1]++;
            total++;
        }
    }
    for (int atom = 0; atom <= th_tag_count; atom++) {
        offsets[atom + 1] += offsets[atom];
    }
    th_node **nodes = PyMem_Malloc(((size_t)total + 1) * sizeof(th_node *));
    if (nodes == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(offsets); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t *cursor = PyMem_Malloc(((size_t)th_tag_count + 1) * sizeof(Py_ssize_t));
    if (cursor == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(offsets); /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(nodes);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int atom = 0; atom <= th_tag_count; atom++) {
        cursor[atom] = offsets[atom];
    }
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        if (node->type == TH_NODE_ELEMENT) {
            nodes[cursor[node->atom]++] = node;
        }
    }
    PyMem_Free(cursor);
    handle->index_offsets = offsets;
    handle->index_nodes = nodes;
    handle->index_built = 1;
    return 0;
}

/* The tag atom every alternative of compiled selects as its subject (rightmost
   compound), or TH_TAG_UNKNOWN when the subjects differ or any is not a plain
   type selector. Such a selector can enumerate just that atom's index bucket. */
static uint16_t selector_subject_atom(const sel_compiled *compiled) {
    uint16_t atom = TH_TAG_UNKNOWN;
    for (int alt = 0; alt < compiled->count; alt++) {
        const sel_complex *complex = &compiled->alts[alt];
        const sel_compound *subject = &complex->compounds[complex->count - 1];
        uint16_t found = TH_TAG_UNKNOWN;
        for (int index = 0; index < subject->count; index++) {
            const sel_simple *simple = &subject->simples[index];
            if (simple->kind == 'e' && simple->tag_atom != TH_TAG_UNKNOWN) {
                found = simple->tag_atom;
                break;
            }
        }
        if (found == TH_TAG_UNKNOWN || (atom != TH_TAG_UNKNOWN && found != atom)) {
            return TH_TAG_UNKNOWN;
        }
        atom = found;
    }
    return atom;
}

/* Whether a query rooted at origin may use the whole-tree atom index: the index
   covers every element under the document node, so only a query whose origin is
   that document node enumerates the right candidate set. */
static int handle_index_usable(HandleObject *handle, th_node *origin) {
    return origin == th_tree_document(handle->tree);
}

/* Whether an eligible query (a known subject tag, rooted at the document) can read
   the whole-tree atom index, building it on the first such query and reusing it
   after. Returns 0 when the query is ineligible, the origin is a subtree, or a
   build fails (out of memory); the caller then falls back to a pre-order walk. */
static int handle_use_index(HandleObject *handle, th_node *origin, int eligible) {
    if (!eligible || !handle_index_usable(handle, origin)) {
        return 0;
    }
    if (handle->index_built) {
        return 1;
    }
    return handle_build_index(handle) ==
           0; /* GCOVR_EXCL_BR_LINE: an index build only fails on unforceable allocation */
}

static PyObject *node_find(PyObject *self, PyObject *args, PyObject *kwargs) {
    query_t query;
    if (build_query(self, args, kwargs, 0, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    th_node *found = NULL;
    int error = 0;
    /* hold the per-tree lock across the walk so a concurrent extract() cannot rewire
       the child/sibling pointers mid-read (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (handle_use_index(handle_obj, origin, query_is_simple_tag(&query))) {
        if (handle_obj->index_offsets[query.tag_atom + 1] > handle_obj->index_offsets[query.tag_atom]) {
            found = handle_obj->index_nodes[handle_obj->index_offsets[query.tag_atom]];
        }
    } else if (query_is_simple_tag(&query)) {
        /* the first element whose atom matches, found with an integer compare */
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (node->atom == query.tag_atom) {
                found = node;
                break;
            }
        }
    } else {
        for (th_node *node = axis_first(origin, query.axis); node != NULL; node = axis_next(node, origin, query.axis)) {
            int matched = node_matches(state, node, &query);
            if (matched < 0) {
                error = 1;
                break;
            }
            if (matched) {
                found = node;
                break;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    free_query(&query);
    if (error) {
        return NULL;
    }
    return node_wrap(state, ((NodeObject *)self)->handle, found);
}

/* Wrap node and append it to the result list; -1 on allocation failure. */
static int append_wrapped(PyObject *out, module_state *state, PyObject *handle, th_node *node) {
    PyObject *wrapped = node_wrap(state, handle, node);
    if (wrapped == NULL || PyList_Append(out, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_XDECREF(wrapped);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_DECREF(wrapped);
    return 0;
}

static PyObject *node_find_all(PyObject *self, PyObject *args, PyObject *kwargs) {
    query_t query;
    if (build_query(self, args, kwargs, 1, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    PyObject *out = PyList_New(0);
    if (out == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        free_query(&query); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int error = 0;
    /* a non-element node carries TH_TAG_UNKNOWN, so the atom compare alone
       selects the right elements on the fast path */
    /* hold the per-tree lock across the walk so a concurrent extract() cannot rewire
       the child/sibling pointers mid-read (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (handle_use_index(handle_obj, origin, query_is_simple_tag(&query))) {
        Py_ssize_t end = handle_obj->index_offsets[query.tag_atom + 1];
        for (Py_ssize_t pos = handle_obj->index_offsets[query.tag_atom]; pos < end; pos++) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            if (append_wrapped(out, state, handle, handle_obj->index_nodes[pos]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    } else if (query_is_simple_tag(&query)) {
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            if (node->atom != query.tag_atom) {
                continue;
            }
            if (append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                error = 1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    } else {
        for (th_node *node = axis_first(origin, query.axis); node != NULL; node = axis_next(node, origin, query.axis)) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            int matched = node_matches(state, node, &query);
            if (matched < 0) {
                error = 1;
                break;
            }
            if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot fail */
                error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                break;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    free_query(&query);
    if (error) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* Type-check arg as a selector str, returning 0, or -1 with a TypeError set. */
static int check_selector_arg(PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "selector must be a str");
        return -1;
    }
    return 0;
}

/* Look up arg in handle's per-tree compiled-selector cache, compiling and
   inserting on a miss (most recent first, capped at SEL_CACHE_CAP). An entry
   compiled before a new attribute name was interned is recompiled in place so a
   once-absent attribute now resolves. The caller must already hold the handle's
   critical section and have type-checked arg; the returned pointer is borrowed
   (the cache owns it). Returns NULL with a Python error set on a compile error. */
static sel_compiled *cached_compile(HandleObject *handle, PyObject *arg) {
    uint32_t gen = th_tree_attr_generation(handle->tree);
    for (int index = 0; index < handle->sel_cache_len; index++) {
        sel_cache_entry entry = handle->sel_cache[index];
        if (entry.key != arg && PyUnicode_Compare(entry.key, arg) != 0) {
            continue;
        }
        if (entry.attr_gen != gen) {
            sel_compiled *fresh = selector_compile(handle->tree, arg);
            if (fresh == NULL) { /* GCOVR_EXCL_BR_LINE: recompiling a valid selector fails only on alloc */
                return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            selector_free(entry.compiled);
            entry.compiled = fresh;
            entry.attr_gen = gen;
        }
        memmove(&handle->sel_cache[1], &handle->sel_cache[0], (size_t)index * sizeof(sel_cache_entry));
        handle->sel_cache[0] = entry;
        return entry.compiled;
    }
    sel_compiled *compiled = selector_compile(handle->tree, arg);
    if (compiled == NULL) {
        return NULL;
    }
    if (handle->sel_cache_len == SEL_CACHE_CAP) {
        sel_cache_entry *evicted = &handle->sel_cache[SEL_CACHE_CAP - 1];
        selector_free(evicted->compiled);
        Py_DECREF(evicted->key);
    } else {
        handle->sel_cache_len++;
    }
    memmove(&handle->sel_cache[1], &handle->sel_cache[0],
            (size_t)(handle->sel_cache_len - 1) * sizeof(sel_cache_entry));
    handle->sel_cache[0] = (sel_cache_entry){Py_NewRef(arg), compiled, gen};
    return compiled;
}

static PyObject *node_select(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    PyObject *out = PyList_New(0);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: a concurrent mutate must not rewire mid-walk */
    HandleObject *handle_obj = (HandleObject *)handle;
    sel_compiled *compiled = cached_compile(handle_obj, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
        /* a single simple selector (one group, one compound, one simple) is tested
           with sel_match_simple directly, skipping the group/combinator machinery */
        const sel_simple *single = sel_single_simple(compiled);
        uint16_t subject = selector_subject_atom(compiled);
        sel_ctx ctx = {compiled->tree, origin, compiled->quirks}; /* :scope is the query root */
        if (handle_use_index(handle_obj, origin, subject != TH_TAG_UNKNOWN)) {
            /* only the candidate subjects need the matcher, drawn in document order
               from the atom bucket instead of a full pre-order walk */
            Py_ssize_t end = handle_obj->index_offsets[subject + 1];
            for (Py_ssize_t pos = handle_obj->index_offsets[subject]; pos < end; pos++) {
                th_node *node = handle_obj->index_nodes[pos];
                int matched =
                    single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin);
                if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
        } else {
            for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
                if (node->type != TH_NODE_ELEMENT) {
                    continue;
                }
                int matched =
                    single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin);
                if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                    error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;     /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

static PyObject *node_select_one(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    th_node *found = NULL;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock around the walk */
    HandleObject *handle_obj = (HandleObject *)handle;
    sel_compiled *compiled = cached_compile(handle_obj, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
        const sel_simple *single = sel_single_simple(compiled);
        uint16_t subject = selector_subject_atom(compiled);
        sel_ctx ctx = {compiled->tree, origin, compiled->quirks}; /* :scope is the query root */
        if (handle_use_index(handle_obj, origin, subject != TH_TAG_UNKNOWN)) {
            Py_ssize_t end = handle_obj->index_offsets[subject + 1];
            for (Py_ssize_t pos = handle_obj->index_offsets[subject]; pos < end; pos++) {
                th_node *node = handle_obj->index_nodes[pos];
                if (single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin)) {
                    found = node;
                    break;
                }
            }
        } else {
            for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
                if (node->type != TH_NODE_ELEMENT) {
                    continue;
                }
                if (single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin)) {
                    found = node;
                    break;
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return node_wrap(state_of(self), handle, found);
}

/* Return the compiled program for arg from handle's per-tree cache, compiling and
   inserting it on a miss (most recent first, capped at XPATH_CACHE_CAP). The program
   is tree-independent, so an entry never goes stale. The caller must hold the handle's
   critical section and have type-checked arg; the returned pointer is borrowed. NULL
   with a Python error set on a compile error. */
static xp_program *cached_xpath_compile(HandleObject *handle, PyObject *arg) {
    for (int index = 0; index < handle->xpath_cache_len; index++) {
        xpath_cache_entry entry = handle->xpath_cache[index];
        if (entry.key != arg && PyUnicode_Compare(entry.key, arg) != 0) {
            continue;
        }
        memmove(&handle->xpath_cache[1], &handle->xpath_cache[0], (size_t)index * sizeof(xpath_cache_entry));
        handle->xpath_cache[0] = entry;
        return entry.prog;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(arg);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    char err[128];
    xp_program *prog = xp_compile(src, len, err, sizeof(err));
    PyMem_Free(src);
    if (prog == NULL) {
        PyErr_SetString(PyExc_ValueError, err);
        return NULL;
    }
    if (handle->xpath_cache_len == XPATH_CACHE_CAP) {
        xpath_cache_entry *evicted = &handle->xpath_cache[XPATH_CACHE_CAP - 1];
        xp_free(evicted->prog);
        Py_DECREF(evicted->key);
    } else {
        handle->xpath_cache_len++;
    }
    memmove(&handle->xpath_cache[1], &handle->xpath_cache[0],
            (size_t)(handle->xpath_cache_len - 1) * sizeof(xpath_cache_entry));
    handle->xpath_cache[0] = (xpath_cache_entry){Py_NewRef(arg), prog};
    return prog;
}

/* Marshal one evaluated node-set item: a namespace node (attr == -2) and an attribute
   to their string value as str, a text node to its data as str, any other node to its
   element/comment/... wrapper. */
PyObject *turbohtml_register_xpath_string(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->xpath_string_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

/* Wrap an attribute or text string result as an XPathString that remembers the
   element it came from (lxml's smart strings). Steals the value reference. */
static PyObject *xpath_smart_string(module_state *state, PyObject *handle, th_node *owner, PyObject *value,
                                    int is_attribute, PyObject *attrname) {
    PyObject *parent = node_wrap(state, handle, owner);
    if (parent == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_DECREF(value);    /* GCOVR_EXCL_LINE */
        Py_DECREF(attrname); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyObject_CallFunction(state->xpath_string_type, "OOOO", value, parent,
                                             is_attribute ? Py_True : Py_False, attrname);
    Py_DECREF(value);
    Py_DECREF(parent);
    Py_DECREF(attrname);
    return result;
}

static PyObject *xpath_item_to_py(module_state *state, PyObject *handle, th_tree *tree, xp_item item,
                                  int smart_strings) {
    if (item.attr == -2) {
        return PyUnicode_FromString("http://www.w3.org/XML/1998/namespace");
    }
    if (item.attr >= 0) {
        const th_node_attr *attr = &item.node->attrs[item.attr];
        PyObject *value = attr->value == NULL ? PyUnicode_New(0, 0) : ucs4_to_str(attr->value, attr->value_len);
        if (!smart_strings || value == NULL) { /* GCOVR_EXCL_BR_LINE: value NULL is an unforced alloc */
            return value;
        }
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        PyObject *attrname = PyUnicode_FromStringAndSize(name, name_len);
        if (attrname == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            Py_DECREF(value);   /* GCOVR_EXCL_LINE */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        return xpath_smart_string(state, handle, item.node, value, 1, attrname);
    }
    if (item.node->type == TH_NODE_TEXT) {
        PyObject *value = str_from_accessor(th_node_data, tree, item.node);
        if (!smart_strings || value == NULL) { /* GCOVR_EXCL_BR_LINE: value NULL is an unforced alloc */
            return value;
        }
        return xpath_smart_string(state, handle, item.node->parent, value, 0, Py_NewRef(Py_None));
    }
    return node_wrap(state, handle, item.node);
}

/* Translate an xp_eval status (-2 unsupported, -1 OOM) into a Python error. */
static void *xpath_raise_status(int status, const char *feature) {
    if (PyErr_Occurred()) { /* a borrowed Python call (e.g. a malformed regex) already set the exception */
        return NULL;
    }
    if (status == -2) {
        PyErr_Format(PyExc_NotImplementedError, "xpath: %s are not implemented yet", feature);
        return NULL;
    }
    if (status == -3) { /* GCOVR_EXCL_BR_LINE: the remaining status -1 is an allocation failure */
        PyErr_Format(PyExc_ValueError, "xpath: %s", feature);
        return NULL;
    }
    return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: status == -1 is an allocation failure that cannot be forced */
}

static PyObject *xpath_scalar_to_py(const xp_result *result) {
    if (result->kind == XP_NUMBER) {
        return PyFloat_FromDouble(result->number);
    }
    if (result->kind == XP_STRING) {
        return ucs4_to_str(result->string, result->string_len);
    }
    return PyBool_FromLong(result->boolean);
}

/* Evaluate an expression and marshal it to a Python object: a node-set becomes a
   list (elements as Element, attribute/text values as str); a scalar becomes the
   matching float / str / bool. */
static int xpath_pyobject_to_scalar(PyObject *obj, xp_result *out, const char *what);

/* The state an extension callback needs, threaded through xp_eval as a void *. */
typedef struct {
    PyObject *extensions; /* {(None, name): callable}; only set when non-empty */
    PyObject *handle;
    module_state *state;
    th_tree *tree;
} xpath_ext_ctx;

/* Marshal one evaluated argument to the Python object an extension receives. */
static PyObject *xpath_arg_to_py(xpath_ext_ctx *ec, const xp_result *value) {
    if (value->kind == XP_NODESET) {
        PyObject *list = PyList_New(value->nodes.len);
        if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return NULL;    /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t index = 0; index < value->nodes.len; index++) {
            PyObject *item = xpath_item_to_py(ec->state, ec->handle, ec->tree, value->nodes.items[index], 0);
            if (item == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                Py_DECREF(list); /* GCOVR_EXCL_LINE */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
            PyList_SET_ITEM(list, index, item);
        }
        return list;
    }
    if (value->kind == XP_STRING) {
        return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, value->string, value->string_len);
    }
    if (value->kind == XP_NUMBER) {
        return PyFloat_FromDouble(value->number);
    }
    return PyBool_FromLong(value->boolean);
}

/* SimpleNamespace(context_node=element): the lightweight context lxml extensions
   read .context_node off. */
static PyObject *xpath_extension_context(PyObject *element) {
    PyObject *types = PyImport_ImportModule("types");
    if (types == NULL) { /* GCOVR_EXCL_BR_LINE: the types import cannot be forced to fail */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *ns_type = PyObject_GetAttrString(types, "SimpleNamespace");
    Py_DECREF(types);
    if (ns_type == NULL) { /* GCOVR_EXCL_BR_LINE: SimpleNamespace is always present */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    PyObject *context = PyObject_CallNoArgs(ns_type);
    Py_DECREF(ns_type);
    if (context == NULL) { /* GCOVR_EXCL_BR_LINE: a SimpleNamespace allocation cannot be forced */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    if (PyObject_SetAttrString(context, "context_node", element) < 0) { /* GCOVR_EXCL_BR_LINE: cannot be forced */
        Py_DECREF(context);                                             /* GCOVR_EXCL_LINE */
        return NULL;                                                    /* GCOVR_EXCL_LINE */
    }
    return context;
}

/* xp_extension_fn: resolve `name` against the (None, name) extension keys and run
   the callable over the marshaled context and arguments. */
static int xpath_call_extension(void *vctx, th_node *context_node, const Py_UCS4 *name, Py_ssize_t name_len,
                                const xp_result *args, int argc, xp_result *out) {
    xpath_ext_ctx *ec = vctx;
    PyObject *name_obj = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, name, name_len);
    if (name_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    PyObject *key = Py_BuildValue("(OO)", Py_None, name_obj);
    Py_DECREF(name_obj);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    PyObject *func = PyDict_GetItem(ec->extensions, key); /* borrowed, no exception when absent */
    Py_DECREF(key);
    if (func == NULL) {
        return -2; /* no such extension; the engine reports an unknown function */
    }
    PyObject *element = node_wrap(ec->state, ec->handle, context_node);
    if (element == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    PyObject *context = xpath_extension_context(element);
    Py_DECREF(element);
    if (context == NULL) { /* GCOVR_EXCL_BR_LINE: an import or allocation failure cannot be forced */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    PyObject *call_args = PyTuple_New((Py_ssize_t)argc + 1);
    if (call_args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_DECREF(context);  /* GCOVR_EXCL_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    PyTuple_SET_ITEM(call_args, 0, context); /* steals the context reference */
    for (int index = 0; index < argc; index++) {
        PyObject *arg = xpath_arg_to_py(ec, &args[index]);
        if (arg == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            Py_DECREF(call_args); /* GCOVR_EXCL_LINE */
            return -1;            /* GCOVR_EXCL_LINE */
        }
        PyTuple_SET_ITEM(call_args, index + 1, arg); /* steals the argument reference */
    }
    PyObject *result = PyObject_CallObject(func, call_args);
    Py_DECREF(call_args);
    if (result == NULL) {
        return -1; /* the extension raised */
    }
    int rc = xpath_pyobject_to_scalar(result, out, "an extension result");
    Py_DECREF(result);
    return rc;
}

static PyObject *xpath_eval_object(PyObject *self, PyObject *arg, const xp_bindings *vars, int smart_strings,
                                   PyObject *extensions) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "xpath expression must be a str");
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    xp_result result;
    const char *feature = NULL;
    PyObject *out = NULL;
    int status = 0;
    int build_error = 0;
    int compiled = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: the compile cache and the eval read the tree */
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    xpath_ext_ctx ext_ctx = {extensions, handle, state, tree};
    xp_program *prog = cached_xpath_compile(handle_obj, arg);
    if (prog != NULL) {
        compiled = 1;
        status = xp_eval(prog, tree, origin, vars, extensions != NULL ? xpath_call_extension : NULL,
                         extensions != NULL ? &ext_ctx : NULL, &result, &feature);
    }
    if (compiled && status == 0) {
        if (result.kind == XP_NODESET) {
            out = PyList_New(result.nodes.len);
            if (out == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                build_error = 1; /* GCOVR_EXCL_LINE */
            } else {             /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
                for (Py_ssize_t index = 0; index < result.nodes.len; index++) {
                    PyObject *item = xpath_item_to_py(state, handle, tree, result.nodes.items[index], smart_strings);
                    if (item == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                        build_error = 1; /* GCOVR_EXCL_LINE */
                        break;           /* GCOVR_EXCL_LINE */
                    }
                    PyList_SET_ITEM(out, index, item);
                }
            }
        } else {
            out = xpath_scalar_to_py(&result);
            if (out == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                build_error = 1; /* GCOVR_EXCL_LINE */
            } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        }
        xp_result_free(&result);
    }
    Py_END_CRITICAL_SECTION();
    if (!compiled) {
        return NULL; /* a compile error left a Python exception set */
    }
    if (status < 0) {
        return xpath_raise_status(status, feature);
    }
    if (build_error) {   /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_XDECREF(out); /* GCOVR_EXCL_LINE */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    return out;
}

/* Convert one keyword-argument value into an XPath variable binding value. */
/* Convert a Python scalar (bool/int/float/str) to an xp_result; `what` names the
   source for the type error a node-set or any other object triggers. */
static int xpath_pyobject_to_scalar(PyObject *obj, xp_result *out, const char *what) {
    memset(out, 0, sizeof(*out));
    if (PyBool_Check(obj)) {
        out->kind = XP_BOOLEAN;
        out->boolean = obj == Py_True;
    } else if (PyLong_Check(obj) || PyFloat_Check(obj)) {
        double value = PyFloat_AsDouble(obj);
        if (value == -1.0 && PyErr_Occurred()) {
            return -1;
        }
        out->kind = XP_NUMBER;
        out->number = value;
    } else if (PyUnicode_Check(obj)) {
        Py_ssize_t length = PyUnicode_GET_LENGTH(obj);
        Py_UCS4 *buf = PyUnicode_AsUCS4Copy(obj);
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        out->kind = XP_STRING;
        out->string = buf;
        out->string_len = length;
    } else {
        PyErr_Format(PyExc_TypeError, "xpath %s must be str, int, float, or bool, not %.80s", what,
                     Py_TYPE(obj)->tp_name);
        return -1;
    }
    return 0;
}

static int xpath_var_value(PyObject *obj, xp_result *out) {
    return xpath_pyobject_to_scalar(obj, out, "variable");
}

static void free_xpath_vars(xp_binding *items, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        PyMem_Free((void *)items[index].name);
        xp_result_free(&items[index].value);
    }
    PyMem_Free(items);
}

/* Build the $name bindings from the call's keyword arguments (kwargs keys are
   always str). On error an exception is set and any partial bindings are freed. */
static int build_xpath_vars(PyObject *kwds, xp_binding **out_items, Py_ssize_t *out_len) {
    *out_items = NULL;
    *out_len = 0;
    Py_ssize_t total = kwds == NULL ? 0 : PyDict_Size(kwds);
    if (total == 0) {
        return 0;
    }
    xp_binding *items = PyMem_Calloc((size_t)total, sizeof(xp_binding));
    if (items == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    Py_ssize_t pos = 0;
    PyObject *key;
    PyObject *value;
    while (PyDict_Next(kwds, &pos, &key, &value)) {
        if (PyUnicode_CompareWithASCIIString(key, "smart_strings") == 0 ||
            PyUnicode_CompareWithASCIIString(key, "extensions") == 0) {
            continue; /* a reserved option the caller reads, not a $variable */
        }
        Py_UCS4 *name = PyUnicode_AsUCS4Copy(key);
        if (name == NULL) {                /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            free_xpath_vars(items, count); /* GCOVR_EXCL_LINE */
            return -1;                     /* GCOVR_EXCL_LINE */
        }
        if (xpath_var_value(value, &items[count].value) < 0) {
            PyMem_Free(name);
            free_xpath_vars(items, count);
            return -1;
        }
        items[count].name = name;
        items[count].name_len = PyUnicode_GET_LENGTH(key);
        count++;
    }
    *out_items = items;
    *out_len = count;
    return 0;
}

/* Shared front end for xpath/xpath_iter/xpath_one: the expression is positional,
   every keyword argument is a $name variable binding. */
static PyObject *xpath_eval_with_vars(PyObject *self, PyObject *args, PyObject *kwds, const char *fname) {
    PyObject *expr_obj;
    char fmt[24];
    snprintf(fmt, sizeof(fmt), "O:%s", fname);
    if (!PyArg_ParseTuple(args, fmt, &expr_obj)) {
        return NULL;
    }
    int smart_strings = 0;
    PyObject *extensions = NULL;
    if (kwds != NULL) {
        PyObject *flag = PyDict_GetItemString(kwds, "smart_strings"); /* borrowed, no exception when absent */
        if (flag != NULL) {
            smart_strings = PyObject_IsTrue(flag);
            if (smart_strings < 0) { /* GCOVR_EXCL_BR_LINE: a __bool__ that raises cannot be forced */
                return NULL;         /* GCOVR_EXCL_LINE */
            }
        }
        PyObject *ext = PyDict_GetItemString(kwds, "extensions"); /* borrowed */
        if (ext != NULL && ext != Py_None) {
            if (!PyDict_Check(ext)) {
                PyErr_SetString(PyExc_TypeError, "xpath extensions must be a dict of (namespace, name) -> callable");
                return NULL;
            }
            extensions = PyDict_GET_SIZE(ext) > 0 ? ext : NULL;
        }
    }
    xp_binding *items;
    Py_ssize_t len;
    if (build_xpath_vars(kwds, &items, &len) < 0) {
        return NULL;
    }
    xp_bindings vars = {items, len};
    PyObject *result = xpath_eval_object(self, expr_obj, len == 0 ? NULL : &vars, smart_strings, extensions);
    free_xpath_vars(items, len);
    return result;
}

static PyObject *node_xpath(PyObject *self, PyObject *args, PyObject *kwds) {
    return xpath_eval_with_vars(self, args, kwds, "xpath");
}

static PyObject *node_xpath_iter(PyObject *self, PyObject *args, PyObject *kwds) {
    PyObject *value = xpath_eval_with_vars(self, args, kwds, "xpath_iter");
    if (value == NULL) {
        return NULL;
    }
    PyObject *sequence = value;
    if (!PyList_Check(value)) { /* wrap a scalar result so it can still be iterated */
        sequence = PyList_New(1);
        if (sequence == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            Py_DECREF(value);   /* GCOVR_EXCL_LINE */
            return NULL;        /* GCOVR_EXCL_LINE */
        }
        PyList_SET_ITEM(sequence, 0, value);
    }
    PyObject *iterator = PyObject_GetIter(sequence);
    Py_DECREF(sequence);
    return iterator;
}

static PyObject *node_xpath_one(PyObject *self, PyObject *args, PyObject *kwds) {
    PyObject *value = xpath_eval_with_vars(self, args, kwds, "xpath_one");
    if (value == NULL) {
        return NULL;
    }
    if (!PyList_Check(value)) {
        return value; /* a scalar expression has a single value */
    }
    PyObject *first = PyList_GET_SIZE(value) == 0 ? Py_None : PyList_GET_ITEM(value, 0);
    Py_INCREF(first);
    Py_DECREF(value);
    return first;
}

static PyObject *node_css_matches(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *node = ((NodeObject *)self)->node;
    int matched = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* selector_matches walks ancestors/siblings */
    sel_compiled *compiled = cached_compile((HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
        /* matches() scopes :scope to the node being tested */
        matched = node->type == TH_NODE_ELEMENT && selector_matches(node, compiled, node);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return PyBool_FromLong(matched);
}

static PyObject *node_css_closest(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *found = NULL;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock around the ancestor walk */
    sel_compiled *compiled = cached_compile((HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
        /* closest() scopes :scope to the element it was invoked on */
        th_node *scope = ((NodeObject *)self)->node;
        for (th_node *node = scope; node != NULL; node = node->parent) {
            if (node->type == TH_NODE_ELEMENT && selector_matches(node, compiled, scope)) {
                found = node;
                break;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return node_wrap(state_of(self), handle, found);
}

/* The serialize(minify=...) options object: four independent round-trip-safe
   transforms, each a bool. Immutable and reference-free, so it lives outside the
   garbage collector like Token. */
typedef struct {
    PyObject_HEAD unsigned char collapse_whitespace;
    unsigned char omit_optional_tags;
    unsigned char unquote_attributes;
    unsigned char strip_comments;
} MinifyObject;

static PyObject *minify_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"collapse_whitespace", "omit_optional_tags", "unquote_attributes", "strip_comments",
                               NULL};
    int collapse = 1;
    int omit = 1;
    int unquote = 1;
    int strip = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$pppp:Minify", keywords, &collapse, &omit, &unquote, &strip)) {
        return NULL;
    }
    MinifyObject *self = (MinifyObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->collapse_whitespace = (unsigned char)collapse;
    self->omit_optional_tags = (unsigned char)omit;
    self->unquote_attributes = (unsigned char)unquote;
    self->strip_comments = (unsigned char)strip;
    return (PyObject *)self;
}

static PyObject *minify_get_collapse(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(((MinifyObject *)self)->collapse_whitespace);
}

static PyObject *minify_get_omit(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(((MinifyObject *)self)->omit_optional_tags);
}

static PyObject *minify_get_unquote(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(((MinifyObject *)self)->unquote_attributes);
}

static PyObject *minify_get_strip(PyObject *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(((MinifyObject *)self)->strip_comments);
}

static PyGetSetDef minify_getset[] = {
    {"collapse_whitespace", minify_get_collapse, NULL, "fold insignificant whitespace runs to a single space", NULL},
    {"omit_optional_tags", minify_get_omit, NULL, "drop the start/end tags the WHATWG rules make optional", NULL},
    {"unquote_attributes", minify_get_unquote, NULL, "drop redundant attribute quotes and empty values", NULL},
    {"strip_comments", minify_get_strip, NULL, "remove comment nodes", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

/* Pack the four flags into the low bits so equality and hashing reduce to one
   integer compare. */
static long minify_bits(MinifyObject *self) {
    return self->collapse_whitespace | self->omit_optional_tags << 1 | self->unquote_attributes << 2 |
           self->strip_comments << 3;
}

static PyObject *minify_richcompare(PyObject *self, PyObject *other, int op) {
    /* split rather than one compound condition: clang's branch instrumentation
       miscounts the short-circuit edges of (... && ...) || ... and fails the gate */
    if (!Py_IS_TYPE(other, Py_TYPE(self))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    int equal = minify_bits((MinifyObject *)self) == minify_bits((MinifyObject *)other);
    return PyBool_FromLong(op == Py_EQ ? equal : !equal);
}

static Py_hash_t minify_hash(PyObject *self) {
    return minify_bits((MinifyObject *)self); /* small non-negative; -1 is never produced, so no sentinel clash */
}

static PyObject *minify_repr(PyObject *self) {
    MinifyObject *minify = (MinifyObject *)self;
    return PyUnicode_FromFormat(
        "Minify(collapse_whitespace=%s, omit_optional_tags=%s, unquote_attributes=%s, "
        "strip_comments=%s)",
        minify->collapse_whitespace ? "True" : "False", minify->omit_optional_tags ? "True" : "False",
        minify->unquote_attributes ? "True" : "False", minify->strip_comments ? "True" : "False");
}

PyDoc_STRVAR(minify_doc, "Minify(*, collapse_whitespace=True, omit_optional_tags=True, unquote_attributes=True, "
                         "strip_comments=True)\n--\n\n"
                         "A serialize(layout=...)/encode(layout=...) mode that shrinks the output. Each\n"
                         "flag toggles one round-trip-safe transform: the minified output always reparses\n"
                         "to the same tree.");

static PyType_Slot minify_slots[] = {
    {Py_tp_doc, (void *)minify_doc},
    {Py_tp_new, minify_new},
    {Py_tp_repr, minify_repr},
    {Py_tp_getset, minify_getset},
    {Py_tp_richcompare, minify_richcompare},
    {Py_tp_hash, minify_hash},
    {0, NULL},
};

static PyType_Spec minify_spec = {
    .name = "turbohtml._html.Minify",
    .basicsize = sizeof(MinifyObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = minify_slots,
};

/* The serialize(layout=...) pretty-print mode: a per-level whitespace unit. Like
   Minify it is immutable; it owns its unit as plain C memory and holds no Python
   references, so it lives outside the garbage collector and only needs a dealloc
   to free the buffer. */
typedef struct {
    PyObject_HEAD Py_UCS4 *unit;
    Py_ssize_t unit_len;
} IndentObject;

static PyObject *indent_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"indent", NULL};
    PyObject *spec = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O:Indent", keywords, &spec)) {
        return NULL;
    }
    Py_UCS4 *unit;
    Py_ssize_t unit_len;
    if (spec == NULL || PyLong_Check(spec)) {
        long count = spec == NULL ? 2 : PyLong_AsLong(spec);
        if (count == -1 && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: an int wider than long cannot be forced here */
            return NULL;                       /* GCOVR_EXCL_LINE: overflow path */
        }
        if (count < 0) {
            PyErr_SetString(PyExc_ValueError, "indent must not be negative");
            return NULL;
        }
        unit = PyMem_Malloc((count ? (size_t)count : 1) * sizeof(Py_UCS4));
        if (unit == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (long index = 0; index < count; index++) {
            unit[index] = ' ';
        }
        unit_len = count;
    } else if (PyUnicode_Check(spec)) {
        unit_len = PyUnicode_GET_LENGTH(spec);
        unit = PyUnicode_AsUCS4Copy(spec);
        if (unit == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "indent must be an int or str");
        return NULL;
    }
    IndentObject *self = (IndentObject *)type->tp_alloc(type, 0);
    if (self == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(unit); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->unit = unit;
    self->unit_len = unit_len;
    return (PyObject *)self;
}

static void indent_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyMem_Free(((IndentObject *)self)->unit);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *indent_get_unit(PyObject *self, void *Py_UNUSED(closure)) {
    IndentObject *indent = (IndentObject *)self;
    return ucs4_to_str(indent->unit, indent->unit_len);
}

static PyGetSetDef indent_getset[] = {
    {"unit", indent_get_unit, NULL, "the whitespace inserted per nesting level", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

/* Mask off the sign bit so the hash is always non-negative and so never collides
   with the -1 error sentinel, which removes the branch a sentinel remap would add. */
static Py_hash_t indent_hash(PyObject *self) {
    IndentObject *indent = (IndentObject *)self;
    Py_uhash_t hash = 14695981039346656037ULL;
    for (Py_ssize_t index = 0; index < indent->unit_len; index++) {
        hash = (hash ^ indent->unit[index]) * 1099511628211ULL;
    }
    return (Py_hash_t)(hash & 0x7fffffffffffffffULL);
}

static PyObject *indent_richcompare(PyObject *self, PyObject *other, int op) {
    /* split as in minify_richcompare so clang's branch gate covers each edge */
    if (!Py_IS_TYPE(other, Py_TYPE(self))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    IndentObject *left = (IndentObject *)self;
    IndentObject *right = (IndentObject *)other;
    int equal = left->unit_len == right->unit_len &&
                memcmp(left->unit, right->unit, (size_t)left->unit_len * sizeof(Py_UCS4)) == 0;
    return PyBool_FromLong(op == Py_EQ ? equal : !equal);
}

static PyObject *indent_repr(PyObject *self) {
    IndentObject *indent = (IndentObject *)self;
    PyObject *unit = ucs4_to_str(indent->unit, indent->unit_len);
    if (unit == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *repr = PyUnicode_FromFormat("Indent(%R)", unit);
    Py_DECREF(unit);
    return repr;
}

PyDoc_STRVAR(indent_doc, "Indent(indent=2)\n--\n\n"
                         "A serialize(layout=...)/encode(layout=...) mode that pretty-prints with the\n"
                         "given per-level unit: an int for that many spaces, or a string used verbatim.\n"
                         "It adds whitespace, so unlike the compact default it does not preserve meaning.");

static PyType_Slot indent_slots[] = {
    {Py_tp_doc, (void *)indent_doc}, {Py_tp_new, indent_new},
    {Py_tp_dealloc, indent_dealloc}, {Py_tp_repr, indent_repr},
    {Py_tp_getset, indent_getset},   {Py_tp_richcompare, indent_richcompare},
    {Py_tp_hash, indent_hash},       {0, NULL},
};

static PyType_Spec indent_spec = {
    .name = "turbohtml._html.Indent",
    .basicsize = sizeof(IndentObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = indent_slots,
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

/* Resolve the layout argument: None compacts, an Indent pretty-prints, a Minify
   minifies. An Indent owns its unit buffer and a Minify its flags, so the resolved
   values borrow from the object the caller keeps alive for the serialize. Returns
   0 on success (mode set) or -1 with a TypeError on any other object. */
static int resolve_layout(module_state *state, PyObject *layout_obj, enum th_layout_mode *mode,
                          const Py_UCS4 **indent_unit, Py_ssize_t *indent_len, th_minify_opts *opts) {
    if (layout_obj == NULL || layout_obj == Py_None) {
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
        *mode = TH_LAYOUT_MINIFY;
        return 0;
    }
    PyErr_SetString(PyExc_TypeError, "layout must be an Indent, a Minify, or None");
    return -1;
}

/* Serialize self to a str under the given Formatter member and layout mode. */
static PyObject *node_serialize_str(PyObject *self, PyObject *formatter_obj, PyObject *layout_obj) {
    int formatter;
    if (resolve_formatter(state_of(self), formatter_obj, &formatter) < 0) {
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
        data = th_node_minify(tree_of(self), ((NodeObject *)self)->node, &minify_opts, formatter, &out_len);
    } else {
        const Py_UCS4 *indent = mode == TH_LAYOUT_INDENT ? indent_unit : NULL;
        data = th_node_serialize(tree_of(self), ((NodeObject *)self)->node, formatter, indent, indent_len, &out_len);
    }
    Py_END_CRITICAL_SECTION();
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(data, out_len);
    PyMem_Free(data);
    return result;
}

static PyObject *node_serialize(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"formatter", "layout", NULL};
    PyObject *formatter_obj = NULL;
    PyObject *layout_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$OO", keywords, &formatter_obj, &layout_obj)) {
        return NULL;
    }
    return node_serialize_str(self, formatter_obj, layout_obj);
}

static PyObject *node_encode(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"encoding", "formatter", "layout", NULL};
    const char *encoding = "utf-8";
    PyObject *formatter_obj = NULL;
    PyObject *layout_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|s$OO", keywords, &encoding, &formatter_obj, &layout_obj)) {
        return NULL;
    }
    PyObject *text = node_serialize_str(self, formatter_obj, layout_obj);
    if (text == NULL) {
        return NULL;
    }
    PyObject *encoded = PyUnicode_AsEncodedString(text, encoding, NULL);
    Py_DECREF(text);
    return encoded;
}

PyDoc_STRVAR(element_doc, "An element node: a tag, a namespace, attributes, and child nodes.");

static PyObject *element_new(PyTypeObject *type, PyObject *args, PyObject *kwds);
static PyObject *element_append(PyObject *self, PyObject *child);
static PyObject *element_extend(PyObject *self, PyObject *iterable);
static PyObject *element_insert(PyObject *self, PyObject *args);
static PyObject *element_clear(PyObject *self, PyObject *ignored);
static PyObject *element_normalize(PyObject *self, PyObject *ignored);

PyDoc_STRVAR(append_doc, "append(child, /)\n--\n\n"
                         "Add child as the last child of this element. A node already in a tree is\n"
                         "moved; a node from another tree is adopted by copy.");

PyDoc_STRVAR(extend_doc, "extend(children, /)\n--\n\n"
                         "Append every node from the iterable in order, each one moved or adopted\n"
                         "like append().");

PyDoc_STRVAR(insert_doc, "insert(index, child, /)\n--\n\n"
                         "Insert child among this element's children at index, counted and clamped\n"
                         "like list.insert.");

PyDoc_STRVAR(clear_doc, "clear()\n--\n\n"
                        "Detach every child of this element, leaving it empty.");

PyDoc_STRVAR(normalize_doc, "normalize()\n--\n\n"
                            "Merge each run of adjacent Text descendants into one node and drop empty\n"
                            "Text nodes, throughout this element's subtree.");

static PyMethodDef element_methods[] = {
    {"append", element_append, METH_O, append_doc},
    {"extend", element_extend, METH_O, extend_doc},
    {"insert", element_insert, METH_VARARGS, insert_doc},
    {"clear", element_clear, METH_NOARGS, clear_doc},
    {"normalize", element_normalize, METH_NOARGS, normalize_doc},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot element_slots[] = {
    {Py_tp_doc, (void *)element_doc},
    {Py_tp_getset, element_getset},
    {Py_tp_methods, element_methods},
    {Py_tp_new, element_new},
    {0, NULL},
};

static PyType_Spec element_spec = {
    .name = "turbohtml._html.Element",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = element_slots,
};

/* ------------------------------------------- Text / Comment / Doctype --- */

static PyObject *handle_new(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding);

/* Take ownership of tree and the node built within it, wrap that node in a fresh
   handle, and return the wrapper. On allocation failure frees the tree and returns
   NULL with an exception set. */
static PyObject *wrap_fresh_tree_node(module_state *state, th_tree *tree, th_node *node) {
    PyObject *handle = handle_new(state, tree, Py_None, Py_None);
    if (handle == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *wrapped = node_wrap(state, handle, node);
    Py_DECREF(handle);
    return wrapped;
}

/* Wrap a data node (Text/Comment/CData/Doctype) holding a copy of data in a fresh
   single-node tree, ready to be inserted into a document. */
static PyObject *data_node_in_fresh_tree(module_state *state, int node_type, PyObject *data) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(data);
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(data);
    if (points == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_data_node(tree, node_type, points, len);
    PyMem_Free(points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, node);
}

static PyObject *construct_data_node(PyTypeObject *type, int node_type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"data", NULL};
    PyObject *data;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U", keywords, &data)) {
        return NULL;
    }
    return data_node_in_fresh_tree(PyType_GetModuleState(type), node_type, data);
}

/* Deep-copy this node and its subtree into a fresh standalone tree, the body of
   __copy__ and __deepcopy__ (an HTML node has no meaningful shallow copy). */
static PyObject *node_copy_impl(PyObject *self) {
    module_state *state = state_of(self);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *copy = th_tree_copy_node(tree, tree_of(self), ((NodeObject *)self)->node);
    if (copy == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, copy);
}

static PyObject *node_copy(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return node_copy_impl(self);
}

static PyObject *node_deepcopy(PyObject *self, PyObject *Py_UNUSED(memo)) {
    return node_copy_impl(self);
}

static PyObject *text_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_TEXT, args, kwds);
}

static PyObject *comment_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_COMMENT, args, kwds);
}

static PyObject *cdata_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_CDATA, args, kwds);
}

/* Reject a processing-instruction target the serialization could not round-trip:
   empty, or carrying whitespace (which the target/data packing splits on) or ">"
   (which closes the instruction). */
static int validate_pi_target(PyObject *target) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(target);
    if (len == 0) {
        PyErr_SetString(PyExc_ValueError, "processing instruction target must not be empty");
        return -1;
    }
    int kind = PyUnicode_KIND(target);
    const void *data = PyUnicode_DATA(target);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character <= ' ' || character == '>') {
            PyObject *ch = PyUnicode_FromOrdinal((int)character);
            if (ch != NULL) { /* GCOVR_EXCL_BR_LINE: a forbidden character is ASCII and always builds */
                PyErr_Format(PyExc_ValueError, "processing instruction target contains an invalid character: %R", ch);
                Py_DECREF(ch);
            }
            return -1;
        }
    }
    return 0;
}

static PyObject *pi_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"target", "data", NULL};
    PyObject *target;
    PyObject *data;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "UU", keywords, &target, &data)) {
        return NULL;
    }
    if (validate_pi_target(target) < 0) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t target_len = PyUnicode_GET_LENGTH(target);
    Py_UCS4 *target_points = PyUnicode_AsUCS4Copy(target);
    Py_ssize_t data_len = PyUnicode_GET_LENGTH(data);
    Py_UCS4 *data_points = PyUnicode_AsUCS4Copy(data);
    if (target_points == NULL || data_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced to fail */
        PyMem_Free(target_points);                      /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(data_points);                        /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_pi(tree, target_points, target_len, data_points, data_len);
    PyMem_Free(target_points);
    PyMem_Free(data_points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, node);
}

/* Reject a tag or attribute name the HTML spec forbids, the way DOM
   createElement / setAttribute raise InvalidCharacterError: empty, or carrying
   whitespace, a control, "/" or ">" (none of which round-trip), plus "<" in a
   tag name and "=" or a quote in an attribute name. */
static int validate_name(PyObject *name, int is_attr) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(name);
    if (len == 0) {
        PyErr_SetString(PyExc_ValueError, is_attr ? "attribute name must not be empty" : "tag must not be empty");
        return -1;
    }
    int kind = PyUnicode_KIND(name);
    const void *data = PyUnicode_DATA(name);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        int bad = character <= ' ' || character == '/' || character == '>' ||
                  (is_attr ? (character == '=' || character == '"' || character == '\'') : character == '<');
        if (bad) {
            PyObject *ch = PyUnicode_FromOrdinal((int)character);
            if (ch != NULL) { /* GCOVR_EXCL_BR_LINE: a forbidden character is ASCII and always builds */
                PyErr_Format(PyExc_ValueError, "%s name contains an invalid character: %R",
                             is_attr ? "attribute" : "tag", ch);
                Py_DECREF(ch);
            }
            return -1;
        }
    }
    return 0;
}

/* Resolve one attribute value to code points: None is valueless (points stays
   NULL, has_value 0); a str is itself; a list of str joins on a space. */
static int element_attr_value(PyObject *value, Py_UCS4 **points, Py_ssize_t *len, int *has_value) {
    *points = NULL;
    *len = 0;
    *has_value = 0;
    if (value == Py_None) {
        return 0;
    }
    PyObject *as_str;
    if (PyUnicode_Check(value)) {
        as_str = Py_NewRef(value);
    } else if (PyList_Check(value)) {
        PyObject *space = PyUnicode_FromOrdinal(' ');
        if (space == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        as_str = PyUnicode_Join(space, value); /* a non-str member raises TypeError */
        Py_DECREF(space);
        if (as_str == NULL) {
            return -1;
        }
    } else {
        PyErr_SetString(PyExc_TypeError, "attribute value must be a str, a list of str, or None");
        return -1;
    }
    *len = PyUnicode_GET_LENGTH(as_str);
    *points = PyUnicode_AsUCS4Copy(as_str);
    Py_DECREF(as_str);
    if (*points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *has_value = 1;
    return 0;
}

/* Fill a constructed element's attribute slots from the keys of attrs. */
static int fill_element_attrs(th_tree *tree, th_node *node, PyObject *attrs, PyObject *keys) {
    Py_ssize_t count = PyList_GET_SIZE(keys);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *name = PyList_GET_ITEM(keys, index);
        if (!PyUnicode_Check(name)) {
            PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
            return -1;
        }
        if (validate_name(name, 1) < 0) {
            return -1;
        }
        Py_ssize_t name_len;
        const char *name_utf8 = PyUnicode_AsUTF8AndSize(name, &name_len);
        if (name_utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: a lone-surrogate name cannot encode, hard to force */
            return -1;           /* GCOVR_EXCL_LINE: surrogate path */
        }
        char *lower = PyMem_Malloc((size_t)name_len);
        if (lower == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t byte = 0; byte < name_len; byte++) {
            char ch = name_utf8[byte];
            lower[byte] = ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
        }
        PyObject *value = PyObject_GetItem(attrs, name);
        Py_UCS4 *points;
        Py_ssize_t value_len;
        int has_value;
        int bad = value == NULL || element_attr_value(value, &points, &value_len, &has_value) < 0;
        Py_XDECREF(value);
        if (bad) {
            PyMem_Free(lower);
            return -1;
        }
        int rc = th_tree_set_attr(tree, node, index, lower, name_len, points, value_len, has_value);
        PyMem_Free(lower);
        PyMem_Free(points);
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* On-stack scratch for ASCII-lowercasing a tag before the atom lookup; a tag
   whose UTF-8 exceeds this is simply treated as an unknown atom. */
#define ELEMENT_TAG_LOWER_STACK_BYTES 64

/* Build an Element wrapper for tag with attrs, without the public constructor's
   name validation. The parser and pickle reconstruction produce tag names (e.g.
   "a<b" from malformed input) that Element() rejects but that must round-trip
   unchanged, so the trusted callers reach the element through this helper. */
static inline PyObject *make_element(PyTypeObject *type, PyObject *tag, PyObject *attrs) {
    Py_ssize_t tag_len = PyUnicode_GET_LENGTH(tag);
    PyObject *keys = NULL;
    Py_ssize_t attr_count = 0;
    if (attrs != NULL && attrs != Py_None) {
        keys = PyMapping_Keys(attrs);
        if (keys == NULL) {
            if (PyErr_ExceptionMatches(PyExc_AttributeError)) { /* a non-mapping has no keys() to enumerate */
                PyErr_SetString(PyExc_TypeError, "attrs must be a mapping");
            }
            return NULL;
        }
        attr_count = PyList_GET_SIZE(keys);
    }
    module_state *state = PyType_GetModuleState(type);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(keys);        /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 *tag_points = PyUnicode_AsUCS4Copy(tag);
    if (tag_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(keys);     /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* tag and attribute names are ASCII-lowercased to match what the parser
       stores, so a constructed and a parsed element compare and serialize alike */
    for (Py_ssize_t index = 0; index < tag_len; index++) {
        if (tag_points[index] >= 'A' && tag_points[index] <= 'Z') {
            tag_points[index] += 32;
        }
    }
    Py_ssize_t utf8_len;
    const char *utf8 = PyUnicode_AsUTF8AndSize(tag, &utf8_len);
    uint16_t atom = TH_TAG_UNKNOWN;
    char stack[ELEMENT_TAG_LOWER_STACK_BYTES];
    if (utf8 != NULL && utf8_len <= (Py_ssize_t)sizeof(stack)) {
        for (Py_ssize_t byte = 0; byte < utf8_len; byte++) {
            stack[byte] = utf8[byte] >= 'A' && utf8[byte] <= 'Z' ? (char)(utf8[byte] + 32) : utf8[byte];
        }
        atom = th_tag_lookup(stack, utf8_len);
    } else {
        PyErr_Clear(); /* a surrogate or very long custom tag is simply not in the table */
    }
    th_node *node = th_tree_make_element(tree, tag_points, tag_len, atom, attr_count);
    PyMem_Free(tag_points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(keys);        /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (keys != NULL && fill_element_attrs(tree, node, attrs, keys) < 0) {
        th_tree_free(tree);
        Py_DECREF(keys);
        return NULL;
    }
    Py_XDECREF(keys);
    return wrap_fresh_tree_node(state, tree, node);
}

static PyObject *element_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"tag", "attrs", NULL};
    PyObject *tag;
    PyObject *attrs = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U|O", keywords, &tag, &attrs)) {
        return NULL;
    }
    if (validate_name(tag, 0) < 0) {
        return NULL;
    }
    return make_element(type, tag, attrs);
}

/* Prepare child_obj to become a child of dest_parent in anchor's tree and return
   the th_node to link (already detached from any old position). A node in the same
   tree is moved in place, its wrapper unchanged; a node from another tree is
   deep-copied in and its wrapper re-pointed at the copy, so the source tree frees
   on its own. NULL with an exception on a non-node, a Document, a cycle (making a
   node a descendant of itself), or allocation failure. */
static th_node *adopt_into(NodeObject *anchor, th_node *dest_parent, PyObject *child_obj) {
    module_state *state = state_of((PyObject *)anchor);
    if (!PyObject_TypeCheck(child_obj, (PyTypeObject *)state->node_type)) {
        PyErr_SetString(PyExc_TypeError, "child must be a node");
        return NULL;
    }
    NodeObject *child = (NodeObject *)child_obj;
    if (child->node->type == TH_NODE_DOCUMENT) {
        PyErr_SetString(PyExc_TypeError, "a Document cannot be inserted as a child");
        return NULL;
    }
    th_tree *dest_tree = tree_of((PyObject *)anchor);
    if (dest_tree == tree_of(child_obj)) {
        if (th_node_contains(child->node, dest_parent)) {
            PyErr_SetString(PyExc_ValueError, "cannot insert a node into its own subtree");
            return NULL;
        }
        th_node_remove(child->node);
        return child->node;
    }
    th_node *copy = th_tree_copy_node(dest_tree, tree_of(child_obj), child->node);
    if (copy == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the caller holds the destination tree's lock; detaching from the source tree (a
       different tree here) takes the source's lock so it cannot race a source mutation */
    Py_BEGIN_CRITICAL_SECTION(child->handle);
    handle_drop_index(child->handle);
    th_node_remove(child->node);
    Py_END_CRITICAL_SECTION();
    Py_SETREF(child->handle, Py_NewRef(anchor->handle));
    child->node = copy;
    return copy;
}

/* Whether new_obj is a node wrapping the same C node as ref: inserting a node
   relative to itself is a no-op the link primitives must not be handed. */
static int is_same_node(PyObject *self, PyObject *new_obj, th_node *ref) {
    module_state *state = state_of(self);
    return PyObject_TypeCheck(new_obj, (PyTypeObject *)state->node_type) && ((NodeObject *)new_obj)->node == ref;
}

static PyObject *element_append(PyObject *self, PyObject *child) {
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

static PyObject *element_extend(PyObject *self, PyObject *iterable) {
    PyObject *iterator = PyObject_GetIter(iterable);
    if (iterator == NULL) {
        return NULL;
    }
    th_node *parent = ((NodeObject *)self)->node;
    PyObject *child;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while ((child = PyIter_Next(iterator)) != NULL) {
        th_node *node = adopt_into((NodeObject *)self, parent, child);
        Py_DECREF(child);
        if (node == NULL) {
            error = 1;
            break;
        }
        th_node_append_child(parent, node);
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(iterator);
    if (error || PyErr_Occurred()) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *element_insert(PyObject *self, PyObject *args) {
    Py_ssize_t index;
    PyObject *child;
    if (!PyArg_ParseTuple(args, "nO", &index, &child)) {
        return NULL;
    }
    th_node *parent = ((NodeObject *)self)->node;
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    Py_ssize_t count = 0;
    for (th_node *walk = parent->first_child; walk != NULL; walk = walk->next_sibling) {
        count++;
    }
    if (index < 0 && (index += count) < 0) {
        index = 0;
    }
    th_node *ref = NULL;
    if (index < count) {
        ref = parent->first_child;
        for (Py_ssize_t step = 0; step < index; step++) {
            ref = ref->next_sibling;
        }
    }
    th_node *node = adopt_into((NodeObject *)self, parent, child);
    error = node == NULL;
    if (node != NULL) {
        th_node_insert_before(parent, node, ref != NULL && ref->parent == parent ? ref : NULL);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *element_clear(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *parent = ((NodeObject *)self)->node;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while (parent->first_child != NULL) {
        th_node_remove(parent->first_child);
    }
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyObject *element_normalize(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    th_node_normalize(tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

/* The shared parent for a sibling edit, or NULL with a ValueError set when this
   node stands alone and so has nowhere to place a sibling. */
static th_node *sibling_parent(PyObject *self) {
    th_node *parent = ((NodeObject *)self)->node->parent;
    if (parent == NULL) {
        PyErr_SetString(PyExc_ValueError, "node has no parent");
    }
    return parent;
}

/* The structural edits hold the per-tree lock around the pointer rewiring so a
   concurrent read/mutate cannot observe a half-linked tree (a no-op on the GIL
   build). adopt_into may run under the section; on a blocking point the section
   suspends safely and the NULL-guarded walks stay memory-safe. */
static PyObject *node_insert_before(PyObject *self, PyObject *nodes) {
    th_node *ref = ((NodeObject *)self)->node;
    th_node *parent = sibling_parent(self);
    if (parent == NULL) {
        return NULL;
    }
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(nodes); index++) {
        PyObject *new_obj = PyTuple_GET_ITEM(nodes, index);
        if (is_same_node(self, new_obj, ref)) {
            continue;
        }
        th_node *node = adopt_into((NodeObject *)self, parent, new_obj);
        if (node == NULL) {
            error = 1;
            break;
        }
        th_node_insert_before(parent, node, ref);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *node_insert_after(PyObject *self, PyObject *nodes) {
    th_node *cursor = ((NodeObject *)self)->node;
    th_node *parent = sibling_parent(self);
    if (parent == NULL) {
        return NULL;
    }
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(nodes); index++) {
        PyObject *new_obj = PyTuple_GET_ITEM(nodes, index);
        if (is_same_node(self, new_obj, cursor)) {
            continue;
        }
        th_node *node = adopt_into((NodeObject *)self, parent, new_obj);
        if (node == NULL) {
            error = 1;
            break;
        }
        th_node_insert_before(parent, node, cursor->next_sibling);
        cursor = node; /* keep multiple inserts in argument order after this node */
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *node_replace_with(PyObject *self, PyObject *nodes) {
    th_node *ref = ((NodeObject *)self)->node;
    th_node *parent = sibling_parent(self);
    if (parent == NULL) {
        return NULL;
    }
    int keep_self = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(nodes); index++) {
        PyObject *new_obj = PyTuple_GET_ITEM(nodes, index);
        if (is_same_node(self, new_obj, ref)) {
            keep_self = 1; /* replacing a node with itself leaves it in place */
            continue;
        }
        th_node *node = adopt_into((NodeObject *)self, parent, new_obj);
        if (node == NULL) {
            error = 1;
            break;
        }
        th_node_insert_before(parent, node, ref);
    }
    if (!error && !keep_self) {
        th_node_remove(ref);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *node_wrap_in(PyObject *self, PyObject *wrapper_obj) {
    module_state *state = state_of(self);
    if (!PyObject_TypeCheck(wrapper_obj, (PyTypeObject *)state->node_type) ||
        ((NodeObject *)wrapper_obj)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "wrapper must be an element");
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    th_node *parent = node->node->parent;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    if (parent != NULL) {
        th_node *wrapper = adopt_into(node, parent, wrapper_obj);
        if (wrapper == NULL) {
            error = 1;
        } else {
            th_node_insert_before(parent, wrapper, node->node);
            th_node_remove(node->node);
            th_node_append_child(wrapper, node->node);
        }
    } else {
        NodeObject *wrapper = (NodeObject *)wrapper_obj;
        th_node *moved = adopt_into(wrapper, wrapper->node, self);
        if (moved == NULL) {
            error = 1;
        } else {
            th_node_append_child(wrapper->node, moved);
        }
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return Py_NewRef(wrapper_obj);
}

static PyObject *node_unwrap(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    th_node *parent = sibling_parent(self);
    if (parent == NULL) {
        return NULL;
    }
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while (node->first_child != NULL) {
        th_node *child = node->first_child;
        th_node_remove(child);
        th_node_insert_before(parent, child, node);
    }
    th_node_remove(node);
    Py_END_CRITICAL_SECTION();
    return Py_NewRef(self);
}

static PyObject *node_extract(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove(((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return Py_NewRef(self);
}

static PyObject *node_decompose(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove(((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyObject *node_get_data(PyObject *self, void *Py_UNUSED(closure)) {
    return str_from_accessor(th_node_data, tree_of(self), ((NodeObject *)self)->node);
}

/* The str a data/text setter assigns: rejects deletion and a non-str, then copies
   the code points (caller frees with PyMem_Free). *len is the length; NULL on
   error. */
static Py_UCS4 *assigned_str(PyObject *value, const char *what, Py_ssize_t *len) {
    if (value == NULL) {
        PyErr_Format(PyExc_TypeError, "cannot delete %s", what);
        return NULL;
    }
    if (!PyUnicode_Check(value)) {
        PyErr_Format(PyExc_TypeError, "%s must be a str", what);
        return NULL;
    }
    *len = PyUnicode_GET_LENGTH(value);
    return PyUnicode_AsUCS4Copy(value);
}

static int node_set_data(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    Py_ssize_t len;
    Py_UCS4 *points = assigned_str(value, "data", &len);
    if (points == NULL) {
        return -1;
    }
    int rc;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    rc = th_node_set_data(tree_of(self), ((NodeObject *)self)->node, points, len);
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_set_data only fails on OOM */
}

static int element_set_text(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    Py_ssize_t len;
    Py_UCS4 *points = assigned_str(value, "text", &len);
    if (points == NULL) {
        return -1;
    }
    th_node *node = ((NodeObject *)self)->node;
    th_tree *tree = tree_of(self);
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    while (node->first_child != NULL) {
        th_node_remove(node->first_child);
    }
    th_node *text = len > 0 ? th_tree_make_data_node(tree, TH_NODE_TEXT, points, len) : NULL;
    /* GCOVR_EXCL_START: a make_data_node allocation failure cannot be forced from a test */
    if (len > 0 && text == NULL) {
        error = 1;
    }
    /* GCOVR_EXCL_STOP */
    if (text != NULL) {
        th_node_append_child(node, text);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return error ? -1 : 0; /* GCOVR_EXCL_BR_LINE: error is set only on the excluded allocation failure */
}

static PyGetSetDef data_getset[] = {
    {"data", node_get_data, node_set_data, "the character data of this node", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(text_doc, "A run of character data.");

static PyType_Slot text_slots[] = {
    {Py_tp_doc, (void *)text_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, text_new},
    {0, NULL},
};

static PyType_Spec text_spec = {
    .name = "turbohtml._html.Text",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = text_slots,
};

PyDoc_STRVAR(comment_doc, "An HTML comment.");

static PyType_Slot comment_slots[] = {
    {Py_tp_doc, (void *)comment_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, comment_new},
    {0, NULL},
};

static PyType_Spec comment_spec = {
    .name = "turbohtml._html.Comment",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = comment_slots,
};

PyDoc_STRVAR(cdata_doc, "A CDATA section.");

static PyType_Slot cdata_slots[] = {
    {Py_tp_doc, (void *)cdata_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, cdata_new},
    {0, NULL},
};

static PyType_Spec cdata_spec = {
    .name = "turbohtml._html.CData",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = cdata_slots,
};

static PyObject *pi_get_target(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text, node->attr_count);
}

static PyObject *pi_get_data(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text + node->attr_count + 1, node->text_len - node->attr_count - 1);
}

static PyGetSetDef pi_getset[] = {
    {"target", pi_get_target, NULL, "the instruction target (the name right after <?)", NULL},
    {"data", pi_get_data, NULL, "the instruction data (everything after the target)", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(pi_doc, "A processing instruction.");

static PyType_Slot pi_slots[] = {
    {Py_tp_doc, (void *)pi_doc},
    {Py_tp_getset, pi_getset},
    {Py_tp_new, pi_new},
    {0, NULL},
};

static PyType_Spec pi_spec = {
    .name = "turbohtml._html.ProcessingInstruction",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = pi_slots,
};

static PyObject *doctype_get_name(PyObject *self, void *Py_UNUSED(closure)) {
    return str_from_accessor(th_node_data, tree_of(self), ((NodeObject *)self)->node);
}

/* The doctype's public id (want_system 0) or system id (want_system 1) as a str,
   or None when that identifier was not supplied. A supplied-but-empty identifier
   is the empty string, distinct from a missing one (issue #68). */
static PyObject *doctype_id(PyObject *self, int want_system) {
    th_node *node = ((NodeObject *)self)->node;
    const Py_UCS4 *public_id;
    const Py_UCS4 *system_id;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (!th_node_doctype_ids(node, &public_id, &public_len, &system_id, &system_len)) {
        Py_RETURN_NONE; /* the doctype carries no identifiers at all */
    }
    uint8_t supplied = want_system ? TH_DOCTYPE_HAS_SYSTEM : TH_DOCTYPE_HAS_PUBLIC;
    if (!(node->tag_flags & supplied)) {
        Py_RETURN_NONE; /* only the sibling identifier was supplied; this one is missing, not empty */
    }
    return want_system ? ucs4_to_str(system_id, system_len) : ucs4_to_str(public_id, public_len);
}

static PyObject *doctype_get_public_id(PyObject *self, void *Py_UNUSED(closure)) {
    return doctype_id(self, 0);
}

static PyObject *doctype_get_system_id(PyObject *self, void *Py_UNUSED(closure)) {
    return doctype_id(self, 1);
}

static PyGetSetDef doctype_getset[] = {
    {"name", doctype_get_name, NULL, "the document type name", NULL},
    {"public_id", doctype_get_public_id, NULL, "the public identifier, or None when the doctype has none", NULL},
    {"system_id", doctype_get_system_id, NULL, "the system identifier, or None when the doctype has none", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(doctype_doc, "A document type declaration.");

static PyType_Slot doctype_slots[] = {
    {Py_tp_doc, (void *)doctype_doc},
    {Py_tp_getset, doctype_getset},
    {0, NULL},
};

static PyType_Spec doctype_spec = {
    .name = "turbohtml._html.Doctype",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = doctype_slots,
};

/* -------------------------------------------------------------- Document */

static PyObject *document_get_root(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    /* a parsed document always has an html element child, so the loop never exhausts */
    th_node *child = node->node->first_child;
    /* allocation failure cannot be forced from a test */
    for (; child != NULL; child = child->next_sibling) { /* GCOVR_EXCL_BR_LINE */
        if (child->type == TH_NODE_ELEMENT) {
            return node_wrap(state_of(self), node->handle, child);
        }
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: a parsed document always has an <html> element child */
}

static PyObject *document_get_encoding(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(((HandleObject *)((NodeObject *)self)->handle)->encoding);
}

static PyGetSetDef document_getset[] = {
    {"root", document_get_root, NULL, "the root <html> element, or None", NULL},
    {"encoding", document_get_encoding, NULL, "the resolved encoding name for bytes input, or None for str", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(document_doc, "A parsed document: the root of the tree returned by parse().");

static PyType_Slot document_slots[] = {
    {Py_tp_doc, (void *)document_doc},
    {Py_tp_getset, document_getset},
    {0, NULL},
};

static PyType_Spec document_spec = {
    .name = "turbohtml._html.Document",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = document_slots,
};

/* ----------------------------------------------------------- _TreeHandle */

static void handle_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    HandleObject *handle = (HandleObject *)self;
    for (int index = 0; index < handle->sel_cache_len; index++) {
        selector_free(handle->sel_cache[index].compiled);
        Py_DECREF(handle->sel_cache[index].key);
    }
    for (int index = 0; index < handle->xpath_cache_len; index++) {
        xp_free(handle->xpath_cache[index].prog);
        Py_DECREF(handle->xpath_cache[index].key);
    }
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    th_tree_free(handle->tree);
    Py_XDECREF(handle->source);
    Py_XDECREF(handle->encoding);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyType_Slot handle_slots[] = {
    {Py_tp_dealloc, handle_dealloc},
    {0, NULL},
};

static PyType_Spec handle_spec = {
    .name = "turbohtml._html._TreeHandle",
    .basicsize = sizeof(HandleObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = handle_slots,
};

static PyObject *handle_new(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding) {
    PyTypeObject *type = (PyTypeObject *)state->handle_type;
    HandleObject *self = (HandleObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->tree = tree;
    self->source = Py_NewRef(source);
    self->encoding = Py_NewRef(encoding);
    return (PyObject *)self;
}

/* ------------------------------------------------------- parse entrypoints */

/* Wrap a freshly built tree (which borrows source's storage) and return its
   document/context node. Frees the tree on wrapping failure. */
static PyObject *tree_to_node(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding) {
    PyObject *handle = handle_new(state, tree, source, encoding);
    if (handle == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = node_wrap(state, handle, th_tree_document(tree));
    Py_DECREF(handle);
    return node;
}

/* Parse bytes: sniff the encoding (BOM, then the encoding argument, then a <meta>
   prescan, then windows-1252), decode with that codec replacing malformed bytes,
   and parse the resulting str. The decoded str is retained as the tree's source. */
static PyObject *parse_bytes(module_state *state, PyObject *markup, const char *enc_arg, Py_ssize_t enc_len) {
    Py_buffer view;
    if (PyObject_GetBuffer(markup, &view, PyBUF_SIMPLE) < 0) { /* GCOVR_EXCL_BR_LINE: bytes expose a simple buffer */
        return NULL;                                           /* GCOVR_EXCL_LINE: buffer-acquisition failure */
    }
    const unsigned char *bytes = view.buf;
    Py_ssize_t len = view.len;
    const th_encoding_entry *entry = NULL;
    Py_ssize_t skip = th_encoding_bom(bytes, len, &entry);
    if (entry == NULL && enc_arg != NULL) {
        entry = th_encoding_lookup(enc_arg, enc_len);
    }
    if (entry == NULL) {
        entry = th_encoding_prescan(bytes, len);
    }
    if (entry == NULL) {
        entry = th_encoding_lookup("windows-1252", 12);
    }
    PyObject *decoded;
    if (strcmp(entry->codec, "x-user-defined") == 0) {
        /* x-user-defined has no CPython codec: ASCII bytes stay, 0x80-0xFF map to the
           private-use block U+F780-U+F7FF, per the WHATWG Encoding Standard */
        Py_ssize_t count = len - skip;
        decoded = PyUnicode_New(count, 0xF7FF);
        if (decoded != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            void *out = PyUnicode_DATA(decoded);
            for (Py_ssize_t index = 0; index < count; index++) {
                unsigned char byte = bytes[skip + index];
                PyUnicode_WRITE(PyUnicode_2BYTE_KIND, out, index, byte < 0x80 ? byte : (Py_UCS4)(0xF700 + byte));
            }
        }
    } else if (strcmp(entry->codec, "replacement") == 0) {
        /* the WHATWG replacement encoding refuses the stateful ISO-2022/HZ byte streams,
           which can smuggle markup past a sanitizer: a non-empty input decodes to a
           single U+FFFD and an empty input to nothing */
        decoded = len - skip > 0 ? PyUnicode_FromOrdinal(0xFFFD) : PyUnicode_New(0, 0);
    } else {
        decoded = PyUnicode_Decode((const char *)bytes + skip, len - skip, entry->codec, "replace");
    }
    PyBuffer_Release(&view);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: the codec is from the table and the replace handler never fails */
        return NULL;       /* GCOVR_EXCL_LINE: decode failure */
    }
    th_tree *tree = th_tree_parse(PyUnicode_KIND(decoded), PyUnicode_DATA(decoded), PyUnicode_GET_LENGTH(decoded));
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        Py_DECREF(decoded);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *canonical = PyUnicode_FromString(entry->canonical);
    if (canonical == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(decoded);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = tree_to_node(state, tree, decoded, canonical);
    Py_DECREF(canonical);
    Py_DECREF(decoded);
    return node;
}

PyObject *turbohtml_parse(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"markup", "encoding", NULL};
    PyObject *markup;
    const char *enc_arg = NULL;
    Py_ssize_t enc_len = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|$z#:parse", keywords, &markup, &enc_arg, &enc_len)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    if (PyUnicode_Check(markup)) {
        th_tree *tree = th_tree_parse(PyUnicode_KIND(markup), PyUnicode_DATA(markup), PyUnicode_GET_LENGTH(markup));
        if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return tree_to_node(state, tree, markup, Py_None);
    }
    if (!PyObject_CheckBuffer(markup)) {
        PyErr_SetString(PyExc_TypeError, "parse() argument must be str or a bytes-like object");
        return NULL;
    }
    return parse_bytes(state, markup, enc_arg, enc_len);
}

PyObject *turbohtml_tree_parse_fragment(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"html", "context", NULL};
    PyObject *text;
    PyObject *context_obj = NULL;
    /* require str: the "s#" format also accepts bytes, which would decode as latin-1 garbage */
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U|U:parse_fragment", keywords, &text, &context_obj)) {
        return NULL;
    }
    const char *context = "div";
    Py_ssize_t context_len = 3;
    if (context_obj != NULL) {
        context = PyUnicode_AsUTF8AndSize(context_obj, &context_len);
        if (context == NULL) { /* a lone-surrogate context has no UTF-8 form */
            return NULL;
        }
    }
    th_tree *tree = th_tree_parse_fragment(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text),
                                           context, context_len);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree_to_node(PyModule_GetState(module), tree, text, Py_None);
}

/* ---------------------------------------------------------------- pickle */

/* This node's children as a fresh list of wrappers, so pickling an element
   recurses into each child. */
static PyObject *node_children_list(PyObject *self) {
    NodeObject *node = (NodeObject *)self;
    module_state *state = state_of(self);
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *child = node->node->first_child; child != NULL; child = child->next_sibling) {
        PyObject *wrapped = node_wrap(state, node->handle, child);
        if (wrapped == NULL || PyList_Append(list, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            Py_XDECREF(wrapped);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(list);                                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(wrapped);
    }
    return list;
}

/* The pickle payload for this node: the leaf data, the element tag and attribute
   dict, the doctype identifiers, or the document's own markup. */
static PyObject *node_pickle_data(PyObject *self, th_node *node) {
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        return str_from_accessor(th_node_data, tree_of(self), node);
    case TH_NODE_PI:
        return Py_BuildValue("(NN)", pi_get_target(self, NULL), pi_get_data(self, NULL));
    case TH_NODE_DOCTYPE:
        return Py_BuildValue("(NNN)", doctype_get_name(self, NULL), doctype_get_public_id(self, NULL),
                             doctype_get_system_id(self, NULL));
    case TH_NODE_ELEMENT: {
        PyObject *view = element_get_attrs(self, NULL);
        if (view == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *attrs = PyObject_CallFunctionObjArgs((PyObject *)&PyDict_Type, view, NULL);
        Py_DECREF(view);
        if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (node->ns == TH_NS_HTML) {
            return Py_BuildValue("(NN)", element_get_tag(self, NULL), attrs);
        }
        /* a foreign (SVG/MathML) element carries its namespace so the round-trip keeps it */
        return Py_BuildValue("(NNi)", element_get_tag(self, NULL), attrs, (int)node->ns);
    }
    case TH_NODE_DOCUMENT:
    case TH_NODE_CONTENT:
        return str_from_accessor(th_node_html, tree_of(self), node);
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: unreachable, the switch is exhaustive */
}

static PyObject *node_reduce(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    PyObject *reconstruct = PyObject_GetAttrString(PyType_GetModule(Py_TYPE(self)), "_reconstruct");
    if (reconstruct == NULL) { /* GCOVR_EXCL_BR_LINE: the module always carries _reconstruct */
        return NULL;           /* GCOVR_EXCL_LINE: unreachable */
    }
    PyObject *data = node_pickle_data(self, node);
    PyObject *children = node->type == TH_NODE_ELEMENT ? node_children_list(self) : PyList_New(0);
    if (data == NULL || children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(data);                   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(children);               /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(reconstruct);             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = Py_BuildValue("(O(iNN))", reconstruct, (int)node->type, data, children);
    Py_DECREF(reconstruct);
    return result;
}

/* Rebuild a doctype from its (name, public_id, system_id) triple, repacking the
   identifiers into the node's stored "name \"public\" \"system\"" form. Either
   identifier may be None (not supplied); a missing one is written as an empty
   string in the text but recorded as absent in tag_flags, so the round-trip
   keeps missing distinct from present-but-empty (issue #68). */
static PyObject *reconstruct_doctype(module_state *state, PyObject *data) {
    PyObject *name;
    PyObject *public_id;
    PyObject *system_id;
    if (!PyArg_ParseTuple(data, "OOO", &name, &public_id, &system_id)) { /* GCOVR_EXCL_BR_LINE: we build this tuple */
        return NULL;                                                     /* GCOVR_EXCL_LINE: unreachable */
    }
    int has_public = public_id != Py_None;
    int has_system = system_id != Py_None;
    PyObject *packed;
    if (!has_public && !has_system) {
        packed = Py_NewRef(name);
    } else {
        PyObject *empty = PyUnicode_New(0, 0);
        if (empty == NULL) { /* GCOVR_EXCL_BR_LINE: the empty string is interned and cannot fail */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        packed = PyUnicode_FromFormat("%U \"%U\" \"%U\"", name, has_public ? public_id : empty,
                                      has_system ? system_id : empty);
        Py_DECREF(empty);
    }
    if (packed == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = data_node_in_fresh_tree(state, TH_NODE_DOCTYPE, packed);
    Py_DECREF(packed);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ((NodeObject *)node)->node->tag_flags =
        (uint8_t)((has_public ? TH_DOCTYPE_HAS_PUBLIC : 0) | (has_system ? TH_DOCTYPE_HAS_SYSTEM : 0));
    return node;
}

PyObject *turbohtml_reconstruct(PyObject *module, PyObject *args) {
    int kind;
    PyObject *data;
    PyObject *children;
    if (!PyArg_ParseTuple(args, "iOO", &kind, &data, &children)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *node;
    switch (kind) {
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        node = data_node_in_fresh_tree(state, kind, data);
        break;
    case TH_NODE_PI:
        node = PyObject_CallObject(state->pi_type, data);
        break;
    case TH_NODE_ELEMENT: {
        /* the parser keeps characters Element() rejects (e.g. "a<b"), so reconstruct
           through the trusted builder rather than the validating public constructor */
        PyObject *tag;
        PyObject *element_attrs;
        int ns = TH_NS_HTML; /* an HTML element omits the namespace; a foreign one appends it */
        /* GCOVR_EXCL_START: node_reduce builds this (tag, attrs[, ns]) tuple, so the parse never fails */
        if (!PyArg_ParseTuple(data, "UO|i", &tag, &element_attrs, &ns)) {
            return NULL;
        }
        /* GCOVR_EXCL_STOP */
        if (ns < TH_NS_HTML || ns > TH_NS_MATHML) { /* guard the namespaces[] index against a crafted payload */
            PyErr_SetString(PyExc_ValueError, "namespace out of range");
            return NULL;
        }
        node = make_element((PyTypeObject *)state->element_type, tag, element_attrs);
        if (node != NULL) {
            ((NodeObject *)node)->node->ns = (uint8_t)ns;
        }
        break;
    }
    case TH_NODE_DOCTYPE:
        node = reconstruct_doctype(state, data);
        break;
    default: { /* TH_NODE_DOCUMENT / TH_NODE_CONTENT: data is the serialized markup */
        PyObject *call_args = PyTuple_Pack(1, data);
        if (call_args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node = turbohtml_parse(module, call_args, NULL);
        Py_DECREF(call_args);
        break;
    }
    }
    if (node == NULL) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(children); index++) {
        PyObject *appended = PyObject_CallMethod(node, "append", "O", PyList_GET_ITEM(children, index));
        if (appended == NULL) { /* GCOVR_EXCL_BR_LINE: a reconstructed child always appends */
            Py_DECREF(node);    /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(appended);
    }
    return node;
}

/* ----------------------------------------------------------- registration */

/* Build one public enum named qualname with count members, register it on the
   module, cache each member into cached_out, and store the enum object in
   *enum_out. base_is_int_enum picks IntEnum over Enum; string_values, when not
   NULL, gives each member a str value, otherwise members take their index. */
static int build_enum(PyObject *module, const char *qualname, int base_is_int_enum, const char *const *names, int count,
                      const char *const *string_values, PyObject **cached_out, PyObject **enum_out) {
    PyObject *members = PyDict_New();
    if (members == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < count; index++) {
        PyObject *value = string_values != NULL ? PyUnicode_FromString(string_values[index]) : PyLong_FromLong(index);
        /* allocation failure cannot be forced from a test */
        if (value == NULL || PyDict_SetItemString(members, names[index], value) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(value);  /* GCOVR_EXCL_LINE: alloc-failure path */
            Py_DECREF(members); /* GCOVR_EXCL_LINE: alloc-failure path */
            return -1;          /* GCOVR_EXCL_LINE: alloc-failure path */
        }
        Py_DECREF(value);
    }
    PyObject *enum_module = PyImport_ImportModule("enum");
    if (enum_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *enum_type = PyObject_GetAttrString(enum_module, base_is_int_enum ? "IntEnum" : "Enum");
    Py_DECREF(enum_module);
    if (enum_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *args = Py_BuildValue("(sO)", qualname, members);
    Py_DECREF(members);
    PyObject *kwargs = Py_BuildValue("{s:s,s:s}", "module", "turbohtml", "qualname", qualname);
    PyObject *built = NULL;
    if (args != NULL && kwargs != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        built = PyObject_Call(enum_type, args, kwargs);
    }
    Py_DECREF(enum_type);
    Py_XDECREF(args);
    Py_XDECREF(kwargs);
    if (built == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < count; index++) {
        cached_out[index] = PyObject_GetAttrString(built, names[index]);
        if (cached_out[index] == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(built);            /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    *enum_out = built;
    return PyModule_AddObjectRef(module, qualname, built);
}

static int build_namespace_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_NAMESPACE_COUNT] = {"HTML", "SVG", "MATHML"};
    static const char *const values[TH_NAMESPACE_COUNT] = {"html", "svg", "math"};
    return build_enum(module, "Namespace", 0, names, TH_NAMESPACE_COUNT, values, state->namespaces,
                      &state->namespace_enum);
}

static int build_axis_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_AXIS_COUNT] = {"DESCENDANTS",       "CHILDREN",  "ANCESTORS", "NEXT_SIBLINGS",
                                                     "PREVIOUS_SIBLINGS", "FOLLOWING", "PRECEDING"};
    return build_enum(module, "Axis", 1, names, TH_AXIS_COUNT, NULL, state->axes, &state->axis_enum);
}

static int build_formatter_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_FORMATTER_COUNT] = {"WHATWG", "MINIMAL", "NAMED_ENTITIES"};
    static const char *const values[TH_FORMATTER_COUNT] = {"whatwg", "minimal", "named"};
    return build_enum(module, "Formatter", 0, names, TH_FORMATTER_COUNT, values, state->formatters,
                      &state->formatter_enum);
}

static int cache_pattern_type(module_state *state) {
    PyObject *re_module = PyImport_ImportModule("re");
    if (re_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->pattern_type = PyObject_GetAttrString(re_module, "Pattern");
    Py_DECREF(re_module);
    if (state->pattern_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

/* Create a heap type subclassing Node, register it on the module, and stamp its
   single-field __match_args__ for structural pattern use. */
static PyObject *register_subtype(PyObject *module, PyType_Spec *spec, PyObject *base, const char *name,
                                  const char *match_arg1, const char *match_arg2) {
    PyObject *bases = PyTuple_Pack(1, base);
    if (bases == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *type = PyType_FromModuleAndSpec(module, spec, bases);
    Py_DECREF(bases);
    if (type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *tuple =
        match_arg2 != NULL ? Py_BuildValue("(ss)", match_arg1, match_arg2) : Py_BuildValue("(s)", match_arg1);
    /* allocation failure cannot be forced from a test */
    if (tuple == NULL || PyObject_SetAttrString(type, "__match_args__", tuple) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(tuple); /* GCOVR_EXCL_LINE: alloc-failure path */
        Py_DECREF(type);   /* GCOVR_EXCL_LINE: alloc-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    Py_DECREF(tuple);
    /* allocation failure cannot be forced from a test */
    if (PyModule_AddObjectRef(module, name, type) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(type);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return type;
}

int tree_register(PyObject *module, module_state *state) {
    /* allocation failure cannot be forced from a test */
    if (build_namespace_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_axis_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_formatter_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (cache_pattern_type(state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->minify_type = PyType_FromModuleAndSpec(module, &minify_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->minify_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Minify", state->minify_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->indent_type = PyType_FromModuleAndSpec(module, &indent_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->indent_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Indent", state->indent_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->walker_type = PyType_FromModuleAndSpec(module, &walker_spec, NULL);
    state->string_walker_type = PyType_FromModuleAndSpec(module, &string_walker_spec, NULL);
    state->handle_type = PyType_FromModuleAndSpec(module, &handle_spec, NULL);
    state->attrs_type = PyType_FromModuleAndSpec(module, &attrs_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->walker_type == NULL || state->string_walker_type == NULL || /* GCOVR_EXCL_BR_LINE */
        state->handle_type == NULL || state->attrs_type == NULL) {         /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->node_type = PyType_FromModuleAndSpec(module, &node_spec, NULL);
    if (state->node_type == NULL || PyModule_AddObjectRef(module, "Node", state->node_type) < 0) { /* GCOVR_EXCL_BR_LINE
                                                                                                    */
        return -1; /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    state->element_type = register_subtype(module, &element_spec, state->node_type, "Element", "tag", NULL);
    state->text_type = register_subtype(module, &text_spec, state->node_type, "Text", "data", NULL);
    state->comment_type = register_subtype(module, &comment_spec, state->node_type, "Comment", "data", NULL);
    state->doctype_type = register_subtype(module, &doctype_spec, state->node_type, "Doctype", "name", NULL);
    state->pi_type = register_subtype(module, &pi_spec, state->node_type, "ProcessingInstruction", "target", "data");
    state->cdata_type = register_subtype(module, &cdata_spec, state->node_type, "CData", "data", NULL);
    state->document_type = register_subtype(module, &document_spec, state->node_type, "Document", "root", NULL);
    /* allocation failure cannot be forced from a test */
    if (state->element_type == NULL || state->text_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->comment_type == NULL || state->doctype_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->pi_type == NULL || state->cdata_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->document_type == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}
