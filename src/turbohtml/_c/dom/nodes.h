/* Shared internals for the Node type cluster: the wrapper structs, the per-tree handle and
   selector/XPath caches, and the hot inline helpers every split translation unit calls. */

#ifndef TURBOHTML_DOM_NODES_H
#define TURBOHTML_DOM_NODES_H

#include "tokenizer/binding.h"
#include "core/common.h"

#include "core/ascii.h"
#include "dom/tree.h"
#include "query/xpath/xpath.h"

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
    PyObject *key;     /* the selector str (a strong reference) */
    void *compiled;    /* sel_compiled* compiled against this handle's tree; opaque here so the */
                       /* header-only css/selector.h need only be included by element.c */
    uint32_t attr_gen; /* tree attribute generation when compiled */
} sel_cache_entry;

/* A compiled XPath program is tree-independent (it resolves atoms at evaluation),
   so unlike the selector cache it needs no generation to invalidate it. */
typedef struct {
    PyObject *key; /* the expression str (a strong reference) */
    xp_program *prog;
} xpath_cache_entry;

/* One bucket of the css_path id-occurrence map: an id value (a stable pointer into
   the tree's attribute storage), its length, and the number of elements carrying it
   (so the anchor uniqueness test reads "exactly one" in O(id-length)). A NULL value
   marks a free slot. */
typedef struct {
    const Py_UCS4 *value;
    Py_ssize_t len;
    Py_ssize_t count;
} path_id_slot;

/* Lazy per-tree open-addressed map from id value to its occurrence count, built on
   the first css_path and dropped with the element index on any structural mutation.
   ci folds id case the way the quirks-mode id selector does, so the map's keys
   compare exactly as path_id_unique used to. */
typedef struct {
    path_id_slot *slots;
    size_t mask; /* capacity - 1; capacity is a power of two */
    int ci;
} path_id_map;

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
    path_id_map *path_ids; /* css_path id-occurrence map; NULL until first css_path */
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

/* The serialize(minify=...) options object: four independent round-trip-safe
   transforms, each a bool. Immutable and reference-free, so it lives outside the
   garbage collector like Token. */
typedef struct {
    PyObject_HEAD unsigned char collapse_whitespace;
    unsigned char omit_optional_tags;
    unsigned char unquote_attributes;
    unsigned char strip_comments;
} MinifyObject;

/* The serialize(layout=...) pretty-print mode: a per-level whitespace unit. Like
   Minify it is immutable; it owns its unit as plain C memory and holds no Python
   references, so it lives outside the garbage collector and only needs a dealloc
   to free the buffer. */
typedef struct {
    PyObject_HEAD Py_UCS4 *unit;
    Py_ssize_t unit_len;
} IndentObject;

/* A precompiled, reusable XPath 1.0 expression (issue #267). It owns the immutable
   compiled program, which holds no tree pointers and no mutable state, so one
   instance is shareable across threads: evaluation reads only the program and the
   per-call context node and variables. smart_strings and the extensions dict are
   bound here at construction. It keeps Python references (the source string and the
   extensions dict, which a callable could cycle back to), so it is garbage-collected. */
typedef struct {
    PyObject_HEAD xp_program *prog;
    PyObject *expression; /* the source expression str, for .path and repr */
    PyObject *extensions; /* {(namespace, name): callable}, or NULL when none was bound */
    int smart_strings;
} XPathObject;

/* ---- hot inline helpers shared across the split units ---- */

static inline module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static inline th_tree *tree_of(PyObject *self) {
    return ((HandleObject *)((NodeObject *)self)->handle)->tree;
}

static inline PyObject *ucs4_to_str(const Py_UCS4 *data, Py_ssize_t len) {
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
}

/* Realize one of the th_node_* accessors (which return a PyMem UCS4 buffer) into
   a str, freeing the buffer. */
static inline PyObject *str_from_accessor(Py_UCS4 *(*accessor)(th_tree *, th_node *, Py_ssize_t *), th_tree *tree,
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

static inline PyObject *type_for_node(module_state *state, const th_node *node) {
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

static inline PyObject *node_wrap(module_state *state, PyObject *handle, th_node *node) {
    if (node == NULL) {
        Py_RETURN_NONE;
    }
    PyTypeObject *type = (PyTypeObject *)type_for_node(state, node);
    NodeObject *self;
#ifndef Py_GIL_DISABLED
    if (state->node_freelist != NULL) {
        self = (NodeObject *)state->node_freelist;
        state->node_freelist = (PyObject *)self->node; /* the next link rode in the node field */
        state->node_freelist_len--;
        PyObject_Init((PyObject *)self, type); /* revive: refcount 1, restamp ob_type (+incref heaptype) */
    } else
#endif
    {
        self = (NodeObject *)type->tp_alloc(type, 0);
        if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    self->handle = Py_NewRef(handle);
    self->node = node;
    return (PyObject *)self;
}

static inline int is_node(PyObject *obj, module_state *state) {
    return PyObject_TypeCheck(obj, (PyTypeObject *)state->node_type);
}

static inline th_node *preorder_next(th_node *current, th_node *root) {
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
static inline th_node *previous_element(th_node *node) {
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
static inline th_node *subtree_next(th_node *node) {
    while (node != NULL) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
    }
    return NULL;
}

static inline int is_ancestor(th_node *candidate, th_node *node) {
    for (th_node *parent = node->parent; parent != NULL; parent = parent->parent) {
        if (parent == candidate) {
            return 1;
        }
    }
    return 0;
}

/* Walk back in document order from current, skipping origin's ancestors so the
   preceding axis stays disjoint from the ancestors axis. */
static inline th_node *preceding_skip(th_node *current, th_node *origin) {
    while (current != NULL && is_ancestor(current, origin)) {
        current = previous_element(current);
    }
    return current;
}

/* Attribute names the HTML standard treats as space-separated token lists. They
   surface in Element.attrs as a list[str] instead of a single string, so class
   membership and similar reads hit a list rather than re-splitting. The set is by
   name (its interned atom), not by element; invalid markup such as <div rel> is
   the only case where that differs from a tag-specific table. */
static inline int attr_is_token_list(uint32_t name_atom) {
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
static inline PyObject *split_token_list(const Py_UCS4 *value, Py_ssize_t value_len) {
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
static inline PyObject *attr_name_obj(th_tree *tree, const th_node_attr *attr) {
    Py_ssize_t name_len;
    const char *bytes = th_attr_name(tree, attr->name_atom, &name_len);
    return PyUnicode_DecodeUTF8(bytes, name_len, "strict");
}

/* One attribute's value as the public object: a list[str] for a token-list attribute
   (class, rel, ...), else the str. A valueless (<x a>) or empty (<x a="">) attribute
   has value == NULL and reports "", the way getAttribute returns the empty string. */
static inline PyObject *attr_value_obj(const th_node_attr *attr) {
    if (attr_is_token_list(attr->name_atom)) {
        return attr->value == NULL ? PyList_New(0) : split_token_list(attr->value, attr->value_len);
    }
    return attr->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(attr->value, attr->value_len);
}

/* The index of the attribute named name in node, or -1 when it has none. */
static inline Py_ssize_t find_attr_index(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    return th_node_attr_find(tree, node, name, name_len);
}

/* The attribute carrying name_atom on a node, or NULL when absent. */
static inline const th_node_attr *find_node_attr(th_node *node, uint32_t atom) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            return &node->attrs[index];
        }
    }
    return NULL;
}

/* Release the css_path id-occurrence map, if any. */
static inline void path_id_map_free(path_id_map *map) {
    if (map != NULL) {
        PyMem_Free(map->slots);
        PyMem_Free(map);
    }
}

/* Build the per-atom element index for the whole tree. Returns 0 on success (the
   index is cached on the handle), -1 on allocation failure. The caller holds the
   handle's critical section. */
static inline int handle_build_index(HandleObject *handle) {
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

/* Whether a query rooted at origin may use the whole-tree atom index: the index
   covers every element under the document node, so only a query whose origin is
   that document node enumerates the right candidate set. */
static inline int handle_index_usable(HandleObject *handle, th_node *origin) {
    return origin == th_tree_document(handle->tree);
}

/* Whether an eligible query (a known subject tag, rooted at the document) can read
   the whole-tree atom index, building it on the first such query and reusing it
   after. Returns 0 when the query is ineligible, the origin is a subtree, or a
   build fails (out of memory); the caller then falls back to a pre-order walk. */
static inline int handle_use_index(HandleObject *handle, th_node *origin, int eligible) {
    if (!eligible || !handle_index_usable(handle, origin)) {
        return 0;
    }
    if (handle->index_built) {
        return 1;
    }
    return handle_build_index(handle) ==
           0; /* GCOVR_EXCL_BR_LINE: an index build only fails on unforceable allocation */
}

/* Wrap node and append it to the result list; -1 on allocation failure. */
static inline int append_wrapped(PyObject *out, module_state *state, PyObject *handle, th_node *node) {
    PyObject *wrapped = node_wrap(state, handle, node);
    if (wrapped == NULL || PyList_Append(out, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_XDECREF(wrapped);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_DECREF(wrapped);
    return 0;
}

/* ---- definitions living in one unit, referenced from others ---- */

/* Free a handle's compiled-selector and compiled-XPath caches. Lives in element.c so the
   header-only css/selector.h stays confined to the single unit that uses it. */
void handle_clear_caches(HandleObject *handle);

PyObject *node_get_text(PyObject *self, void *Py_UNUSED(closure));
PyObject *element_get_tag(PyObject *self, void *Py_UNUSED(closure));
PyObject *element_get_attrs(PyObject *self, void *Py_UNUSED(closure));
PyObject *node_find(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *node_find_all(PyObject *self, PyObject *args, PyObject *kwargs);
PyObject *node_select(PyObject *self, PyObject *arg);
PyObject *node_select_one(PyObject *self, PyObject *arg);
PyObject *node_re(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_re_first(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_xpath(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_xpath_iter(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_xpath_one(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_css_matches(PyObject *self, PyObject *arg);
PyObject *node_css_closest(PyObject *self, PyObject *arg);
PyObject *node_prune(PyObject *self, PyObject *arg);
PyObject *node_remove(PyObject *self, PyObject *arg);
PyObject *node_strip_tags(PyObject *self, PyObject *arg);
PyObject *wrap_fresh_tree_node(module_state *state, th_tree *tree, th_node *node);
PyObject *data_node_in_fresh_tree(module_state *state, int node_type, PyObject *data);
PyObject *node_copy(PyObject *self, PyObject *Py_UNUSED(ignored));
PyObject *node_deepcopy(PyObject *self, PyObject *Py_UNUSED(memo));
PyObject *make_element(PyTypeObject *type, PyObject *tag, PyObject *attrs);
PyObject *node_insert_before(PyObject *self, PyObject *nodes);
PyObject *node_insert_after(PyObject *self, PyObject *nodes);
PyObject *node_replace_with(PyObject *self, PyObject *nodes);
PyObject *node_wrap_in(PyObject *self, PyObject *wrapper_obj);
PyObject *node_wrap_siblings(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *node_unwrap(PyObject *self, PyObject *Py_UNUSED(ignored));
PyObject *node_extract(PyObject *self, PyObject *Py_UNUSED(ignored));
PyObject *node_decompose(PyObject *self, PyObject *Py_UNUSED(ignored));
Py_UCS4 *assigned_str(PyObject *value, const char *what, Py_ssize_t *len);
PyObject *pi_get_target(PyObject *self, void *Py_UNUSED(closure));
PyObject *pi_get_data(PyObject *self, void *Py_UNUSED(closure));
PyObject *doctype_get_name(PyObject *self, void *Py_UNUSED(closure));
PyObject *doctype_get_public_id(PyObject *self, void *Py_UNUSED(closure));
PyObject *doctype_get_system_id(PyObject *self, void *Py_UNUSED(closure));
PyObject *parse_error_new(module_state *state, const th_parse_error *error);
PyObject *handle_new(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding);
PyObject *node_reduce(PyObject *self, PyObject *Py_UNUSED(ignored));
extern PyType_Spec walker_spec;
extern PyType_Spec string_walker_spec;
extern PyType_Spec node_spec;
extern PyType_Spec attrs_spec;
extern PyType_Spec element_spec;
extern PyType_Spec minify_spec;
extern PyType_Spec indent_spec;
extern PyType_Spec xpath_compiled_spec;
extern PyType_Spec text_spec;
extern PyType_Spec comment_spec;
extern PyType_Spec cdata_spec;
extern PyType_Spec pi_spec;
extern PyType_Spec doctype_spec;
extern PyType_Spec parse_error_spec;

#endif /* TURBOHTML_DOM_NODES_H */
