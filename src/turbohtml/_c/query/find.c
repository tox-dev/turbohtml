/* Pythonic tree search: find()/find_all() over any axis with tag, attribute, and class
   filters, the everyday query API that pulls matching elements out of a parsed tree. */

#include "core/work_steps.h"
#include "dom/nodes.h"

/* Plain strings and literal regexes need no Python callback. Other predicates
   snapshot candidates under the lock because callbacks can mutate the tree. */
enum th_text_scan { TH_TEXT_PY = 0, TH_TEXT_EQ, TH_TEXT_SUBSTR };

enum th_find_filter { TH_FIND_PY, TH_FIND_STRING, TH_FIND_PRESENT, TH_FIND_ABSENT };

typedef struct {
    uint32_t atom;
    enum th_find_filter kind;
    PyObject *filter;
} find_attr;

typedef struct {
    PyObject *tag;          /* tag filter, or NULL for no tag constraint */
    PyObject *class_filter; /* class_ filter, or NULL */
    PyObject *text;         /* text predicate over each element's collected text, or NULL */
    /* When the text predicate is a pure-C scan, text_scan is TH_TEXT_EQ/SUBSTR and
       text_needle holds the expected/needle code points; otherwise TH_TEXT_PY. */
    enum th_text_scan text_scan;
    Py_UCS4 *text_needle;
    Py_ssize_t text_needle_len;
    /* Steps the C scan's reads may still take before the search weighs a text_index;
       the query carries it so the per-candidate loop keeps it out of a register. */
    Py_ssize_t text_left;
    /* The search's text_index, reached through the query so the reads pass no pointer to it. */
    struct text_index *text_index;
    enum th_axis axis;
    Py_ssize_t limit; /* -1 for unlimited */
    find_attr *attrs;
    Py_ssize_t nattr;
    /* Fast path for a plain-string tag filter: resolve the name to an atom once
       so the per-node test is an integer compare instead of building a str for
       every element. tag_plain is set when tag is a single str; tag_atom is its
       atom, or TH_TAG_UNKNOWN for a name outside the table. XML names always
       use the exact-name path, including names present in the HTML atom table. */
    int tag_plain;
    int tag_exact;
    uint16_t tag_atom;
    /* Fast path for a plain-string class_ filter: its code points are copied
       once so each element's class tokens are compared without building a str
       per token. class_ucs4 is NULL unless class_filter is a single str. */
    Py_UCS4 *class_ucs4;
    Py_ssize_t class_len;
} query_t;

static void free_query(query_t *query) {
    for (Py_ssize_t index = 0; index < query->nattr; index++) {
        Py_DECREF(query->attrs[index].filter);
    }
    PyMem_Free(query->attrs);
    PyMem_Free(query->class_ucs4);
    PyMem_Free(query->text_needle);
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

/* For a plain-string tag filter, whether node's tag name matches: a known name is
   a pure integer atom compare; an unknown one can only match the rare unknown-atom
   elements, compared by name. Returns 1/0, or -1 with an exception set. */
static int tag_plain_matches(const query_t *query, th_node *node) {
    if (query->tag_atom != TH_TAG_UNKNOWN) {
        return node->atom == query->tag_atom;
    }
    if (!query->tag_exact && node->atom != TH_TAG_UNKNOWN) {
        return 0;
    }
    return ucs4_equals_pystr(node->text, node->text_len, query->tag);
}

/* Whether an element matches the whole query. Returns 1/0, or -1 with an
   exception set. */
static int node_matches(module_state *state, th_node *node, const query_t *query) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (query->tag_plain) {
        int matched = tag_plain_matches(query, node);
        if (matched <= 0) {
            return matched;
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
        const find_attr *filter = &query->attrs[index];
        if (filter->kind == TH_FIND_STRING) {
            /* a str filter matches only a present, valued attribute equal to it */
            const th_node_attr *attr = find_node_attr(node, filter->atom);
            int equal =
                attr != NULL && attr->value != NULL && ucs4_equals_pystr(attr->value, attr->value_len, filter->filter);
            if (!equal) {
                return 0;
            }
            continue;
        }
        if (filter->kind == TH_FIND_PRESENT || filter->kind == TH_FIND_ABSENT) {
            int present = find_node_attr(node, filter->atom) != NULL;
            if (present != (filter->kind == TH_FIND_PRESENT)) {
                return 0;
            }
            continue;
        }
        int owned;
        PyObject *value = attr_value(node, filter->atom, &owned);
        if (owned && value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int matched = filter_matches(state, filter->filter, value);
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
    enum th_find_filter kind = PyUnicode_Check(filter) ? TH_FIND_STRING
                               : filter == Py_True     ? TH_FIND_PRESENT
                               : filter == Py_False    ? TH_FIND_ABSENT
                                                       : TH_FIND_PY;
    query->attrs[query->nattr++] = (find_attr){th_attr_lookup(tree, bytes, len), kind, Py_NewRef(filter)};
    return 0;
}

static int is_reserved_key(PyObject *key, int is_find_all) {
    return PyUnicode_CompareWithASCIIString(key, "axis") == 0 || PyUnicode_CompareWithASCIIString(key, "class_") == 0 ||
           PyUnicode_CompareWithASCIIString(key, "attrs") == 0 || PyUnicode_CompareWithASCIIString(key, "text") == 0 ||
           (is_find_all && PyUnicode_CompareWithASCIIString(key, "limit") == 0);
}

/* The re flag bits that keep a pattern off the literal fast path: IGNORECASE folds
   case (the C scan is case-sensitive) and VERBOSE drops whitespace and starts # to
   end-of-line comments, so a "literal-looking" verbose source is not literal. */
#define TH_RE_IGNORECASE 2
#define TH_RE_VERBOSE 64

/* Whether a code point is a Python regex metacharacter; a source free of these and
   not case-insensitive or verbose matches exactly as a literal substring. */
static int is_regex_meta(Py_UCS4 character) {
    static const char meta[] = ".^$*+?{}[]\\|()";
    return character < 0x80 && memchr(meta, (int)character, sizeof(meta) - 1) != NULL;
}

/* When the text predicate reduces to a pure-C scan, record its needle on the query
   so each candidate is tested against the gathered code points with no str built.
   A plain str is an exact match; a literal compiled regex is a substring match.
   Leaves text_scan TH_TEXT_PY for everything else. Returns -1 with an exception set
   only on a (test-unreachable) allocation failure. */
static int classify_text(module_state *state, query_t *query) {
    if (PyUnicode_Check(query->text)) {
        query->text_needle = PyUnicode_AsUCS4Copy(query->text);
        if (query->text_needle == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        query->text_needle_len = PyUnicode_GET_LENGTH(query->text);
        query->text_scan = TH_TEXT_EQ;
        return 0;
    }
    if (!PyObject_TypeCheck(query->text, (PyTypeObject *)state->pattern_type)) {
        return 0;
    }
    PyObject *source = PyObject_GetAttrString(query->text, "pattern");
    if (source == NULL) { /* GCOVR_EXCL_BR_LINE: a Pattern always has .pattern */
        return -1;        /* GCOVR_EXCL_LINE: attribute-error path */
    }
    if (!PyUnicode_Check(source)) { /* a bytes pattern never matches a str value */
        Py_DECREF(source);
        return 0;
    }
    PyObject *flags_obj = PyObject_GetAttrString(query->text, "flags");
    if (flags_obj == NULL) { /* GCOVR_EXCL_BR_LINE: a Pattern always has .flags */
        Py_DECREF(source);   /* GCOVR_EXCL_LINE: attribute-error path */
        return -1;           /* GCOVR_EXCL_LINE: attribute-error path */
    }
    long flags = PyLong_AsLong(flags_obj); /* a RegexFlag is a small int; no overflow */
    Py_DECREF(flags_obj);
    Py_ssize_t len = PyUnicode_GET_LENGTH(source);
    int kind = PyUnicode_KIND(source);
    const void *data = PyUnicode_DATA(source);
    int literal = (flags & (TH_RE_IGNORECASE | TH_RE_VERBOSE)) == 0;
    for (Py_ssize_t index = 0; literal && index < len; index++) {
        if (is_regex_meta(PyUnicode_READ(kind, data, index))) {
            literal = 0;
        }
    }
    if (literal) {
        query->text_needle = PyUnicode_AsUCS4Copy(source);
        if (query->text_needle == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            Py_DECREF(source);            /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        query->text_needle_len = len;
        query->text_scan = TH_TEXT_SUBSTR;
    }
    Py_DECREF(source);
    return 0;
}

/* Compile the (tag, axis, class_, attrs, limit, **filters) arguments. Always
   leaves query in a free_query-safe state, even on error. */
static int build_query(PyObject *self, PyObject *args, PyObject *kwargs, int is_find_all, query_t *query) {
    module_state *state = state_of(self);
    th_tree *tree = tree_of(self);
    query->tag = NULL;
    query->class_filter = NULL;
    query->text = NULL;
    query->text_scan = TH_TEXT_PY;
    query->text_needle = NULL;
    query->text_needle_len = 0;
    query->axis = TH_AXIS_DESCENDANTS;
    query->limit = -1;
    query->attrs = NULL;
    query->nattr = 0;
    query->tag_plain = 0;
    query->tag_exact = 0;
    query->tag_atom = TH_TAG_UNKNOWN;
    query->class_ucs4 = NULL;
    query->class_len = 0;

    PyObject *tag = NULL;
    if (!PyArg_ParseTuple(args, "|O:find", &tag)) {
        return -1;
    }
    if (tag != NULL && tag != Py_None) {
        query->tag = tag;
        if (PyUnicode_Check(tag)) {
            query->tag_plain = 1;
            query->tag_exact = th_tree_is_xml(tree);
            if (!query->tag_exact) {
                Py_ssize_t blen;
                const char *bytes = PyUnicode_AsUTF8AndSize(tag, &blen);
                if (bytes == NULL) {
                    /* A lone surrogate cannot encode, so the exact-name path handles it. */
                    PyErr_Clear();
                } else {
                    query->tag_atom = blen > 0 ? th_tag_lookup(bytes, blen) : TH_TAG_UNKNOWN;
                }
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
        } else if (PyUnicode_CompareWithASCIIString(key, "text") == 0) {
            query->text = value;
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
                if (query->limit < 0) {
                    PyErr_Format(PyExc_ValueError, "limit must be non-negative, not %R", value);
                    return -1;
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
        query->attrs = PyMem_Malloc((size_t)capacity * sizeof(find_attr));
        if (query->attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();       /* GCOVR_EXCL_LINE */
            return -1;              /* GCOVR_EXCL_LINE: allocation-failure path */
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
    if (query->text != NULL && classify_text(state, query) < 0) { /* GCOVR_EXCL_BR_LINE: only on alloc failure */
        return -1;                                                /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

/* Whether the query pins a known subject tag on the descendant axis: the shape
   whose candidates are exactly the tag's atom-index bucket. A query with no other
   constraint reads the bucket straight through (query_is_simple_tag); one that adds
   class or attribute filters runs the general matcher over the bucket instead of
   over every descendant, so it tests only the elements of that tag. */
static int query_is_indexed_tag(const query_t *query) {
    return query->tag_plain && query->tag_atom != TH_TAG_UNKNOWN && query->axis == TH_AXIS_DESCENDANTS;
}

/* Whether the query is a plain known tag with no other constraint on the
   descendant axis: the shape the tag-only fast path handles with a pre-order
   walk and an integer atom compare, skipping the general matcher. */
static int query_is_simple_tag(const query_t *query) {
    return query_is_indexed_tag(query) && query->nattr == 0 && query->class_ucs4 == NULL && query->class_filter == NULL;
}

/* The cheap, pure-C structural prefilter for the text-search path: whether node
   could match before its collected text is tested, deciding which elements get
   their text gathered in the snapshot pass. Only the allocation-free checks live
   here (the element type, a known plain tag, a plain class); the remaining filters,
   including any regex/callable that would need Python, are deferred to node_matches
   in the post-snapshot pass. */
static int text_candidate(const query_t *query, th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return 0;
    }
    if (query->tag_plain && query->tag_atom != TH_TAG_UNKNOWN && node->atom != query->tag_atom) {
        return 0;
    }
    if (query->class_ucs4 != NULL && class_matches_plain(node, query->class_ucs4, query->class_len) == 0) {
        return 0;
    }
    return 1;
}

/* Snapshot, under the handle's critical section, the elements that satisfy the tag,
   class, and attribute filters together with their collected text. node_matches runs
   the structural filters here, the same way the non-text path does inside the lock;
   only the text predicate is deferred, because it may run arbitrary Python and so
   suspend the lock. text_candidate counts an upper bound first so the arrays are
   sized once with no reallocation, and it never invokes a structural callable twice.
   Writes the matched element pointers to *out_nodes and their text to *out_texts
   (both PyMem arrays the caller always frees, with *out_count entries to DECREF in
   out_texts) and the count to *out_count. Returns 0, or -1 with an exception set
   when a structural filter raised; the partial snapshot is still returned for the
   caller to release. */
static int snapshot_text_candidates(PyObject *self, const query_t *query, th_node ***out_nodes, PyObject ***out_texts,
                                    Py_ssize_t *out_count) {
    module_state *state = state_of(self);
    th_node *origin = ((NodeObject *)self)->node;
    th_tree *tree = tree_of(self);
    th_node **nodes = NULL;
    PyObject **texts = NULL;
    Py_ssize_t filled = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    Py_ssize_t capacity = 0;
    for (th_node *node = axis_first(origin, query->axis); node != NULL; node = axis_next(node, origin, query->axis)) {
        if (text_candidate(query, node)) {
            capacity++;
        }
    }
    if (capacity > 0) {
        nodes = PyMem_Malloc((size_t)capacity * sizeof(th_node *));
        texts = PyMem_Malloc((size_t)capacity * sizeof(PyObject *));
        if (nodes == NULL || texts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced from a test */
            error = 1;                        /* GCOVR_EXCL_LINE: allocation-failure path */
        } else {                              /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
            for (th_node *node = axis_first(origin, query->axis); node != NULL;
                 node = axis_next(node, origin, query->axis)) {
                if (!text_candidate(query, node)) {
                    continue;
                }
                int matched = node_matches(state, node, query);
                if (matched < 0) {
                    error = 1;
                    break;
                }
                if (matched == 0) {
                    continue;
                }
                PyObject *text = str_from_accessor(th_node_text, tree, node);
                if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    error = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;          /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                nodes[filled] = node;
                texts[filled] = text;
                filled++;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    *out_nodes = nodes;
    *out_texts = texts;
    *out_count = filled;
    return error ? -1 : 0;
}

/* A bound on what reading node's collected text costs, without realizing any span:
   a step for each descendant and one per code point of its text_len, which every
   node type has (an element's is its tag name), so no type is tested per node. It
   also bounds the collected text's length, which sizes the buffer it is gathered in. */
static Py_ssize_t subtree_text_bound(th_node *node) {
    Py_ssize_t total = 0;
    for (th_node *descendant = node->first_child; descendant != NULL; descendant = preorder_next(descendant, node)) {
        TH_WORK_STEP();
        total += descendant->text_len + 1;
    }
    return total;
}

static int ucs4_contains_naive(const Py_UCS4 *hay, Py_ssize_t hay_len, const Py_UCS4 *needle, Py_ssize_t needle_len,
                               Py_ssize_t offset) {
    for (Py_ssize_t start = offset; start + needle_len <= hay_len; start++) {
        Py_ssize_t index = 0;
        while (index < needle_len && hay[start + index] == needle[index]) {
            index++;
        }
        if (index == needle_len) {
            return 1;
        }
    }
    return 0;
}

/* The KMP failure table of a non-empty needle: a PyMem array the caller frees, or NULL when it cannot be allocated. */
static Py_ssize_t *kmp_prefix(const Py_UCS4 *needle, Py_ssize_t needle_len) {
    Py_ssize_t *prefix = PyMem_Malloc((size_t)needle_len * sizeof(*prefix));
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    prefix[0] = 0;
    for (Py_ssize_t index = 1, matched = 0; index < needle_len; index++) {
        while (matched > 0 && needle[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (needle[index] == needle[matched]) {
            matched++;
        }
        prefix[index] = matched;
    }
    return prefix;
}

static int ucs4_contains_kmp(const Py_UCS4 *hay, Py_ssize_t hay_len, const Py_UCS4 *needle, Py_ssize_t needle_len,
                             Py_ssize_t offset) {
    Py_ssize_t *prefix = kmp_prefix(needle, needle_len);
    if (prefix == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t matched = 0;
    for (Py_ssize_t index = offset; index < hay_len; index++) {
        while (matched > 0 && hay[index] != needle[matched]) {
            matched = prefix[matched - 1];
        }
        if (hay[index] == needle[matched]) {
            matched++;
            if (matched == needle_len) {
                PyMem_Free(prefix);
                return 1;
            }
        }
    }
    PyMem_Free(prefix);
    return 0;
}

/* The candidate loop is forced inline once per scan, and the reads and the substring test are forced inline into each
   copy and into text_scan_moved: GCC keeps them out of line once they have several callers, which measured slower per
   candidate. A debug build keeps one copy of each, which the coverage gates need. */
#if defined(_MSC_VER)
#define TH_FORCEINLINE __forceinline
#elif defined(__OPTIMIZE__)
#define TH_FORCEINLINE inline __attribute__((always_inline))
#else
#define TH_FORCEINLINE
#endif

static TH_FORCEINLINE int ucs4_contains(const Py_UCS4 *hay, Py_ssize_t hay_len, const Py_UCS4 *needle,
                                        Py_ssize_t needle_len) {
    if (needle_len == 0) {
        return 1;
    }
    if (needle_len > hay_len) {
        return 0;
    }
    if (memcmp(hay, needle, (size_t)needle_len * sizeof(Py_UCS4)) == 0) {
        return 1;
    }
    if (needle_len <= 64 || hay_len < 256) {
        return ucs4_contains_naive(hay, hay_len, needle, needle_len, 1);
    }
    size_t candidates = 64;
    Py_ssize_t last = needle_len - 1;
    for (Py_ssize_t start = 1; start + needle_len <= hay_len; start++) {
        if (hay[start + last] != needle[last]) {
            continue;
        }
        if (memcmp(hay + start, needle, (size_t)needle_len * sizeof(Py_UCS4)) == 0) {
            return 1;
        }
        candidates--;
        if (candidates == 0) {
            int found = ucs4_contains_kmp(hay, hay_len, needle, needle_len, start + 1);
            if (found >= 0) { /* GCOVR_EXCL_BR_LINE: negative only after unforceable allocation failure */
                return found;
            }
            return ucs4_contains_naive(hay, hay_len, needle, needle_len, start + 1); /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

/* Grow scratch to hold at least need code points, reusing it across candidates so
   one buffer serves the whole walk. */
static int scratch_ensure(Py_UCS4 **scratch, Py_ssize_t *cap, Py_ssize_t need) {
    if (need <= *cap) {
        return 0;
    }
    Py_UCS4 *grown = PyMem_Realloc(*scratch, (size_t)need * sizeof(Py_UCS4));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *scratch = grown;
    *cap = need;
    return 0;
}

/* A candidate's text is a slice of each ancestor's, so reading every candidate from its own subtree visits a node once
   per candidate around it: quadratic in the depth of a tree, which parse_xml does not cap. A few reads in a large tree
   still cost less than one walk of it, so the search starts with them and charges them to an allowance of
   TEXT_SCAN_BUDGET steps. Once that is spent, text_index_weigh compares the reads with a walk, and after
   TEXT_SCAN_PASSES such walks' worth a text_index answers the rest of the search. */
#define TEXT_SCAN_BUDGET 65536
#define TEXT_SCAN_PASSES 16
/* A substring read of at most this many steps goes uncharged, so the per-candidate loop skips the accounting for the
   short texts most elements hold. Such a read visits at most this many nodes, so a node lies inside at most this many
   of them: together they cost at most that many walks of the tree, and the search stays linear. */
#define TEXT_SCAN_SMALL 256
/* What a read returns once the search built a text_index during it, which then answers for the candidate. */
#define TEXT_SCAN_INDEXED (-2)
/* What a lookup returns for a candidate the text_index does not hold. */
#define TEXT_SCAN_ABSENT (-3)
/* The weighing runs out of line, so the per-candidate loop keeps the registers it needs for the reads. */
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* One element of a text_index: hit says whether its text passes the query's C scan. While the building walk is
   inside the element, start is the count of code points of text the walk met before it and outer the entry of the
   element around it. */
typedef struct {
    th_node *node;
    Py_ssize_t start;
    Py_ssize_t outer;
    int hit;
} text_slice;

/* Whether the text of each element at or below one root passes the scan, all from a single walk of that root, and
   what the search's reads have cost until it is built. */
typedef struct text_index {
    th_tree *tree;
    th_node *origin;    /* the node the search walks from */
    Py_ssize_t granted; /* steps the reads may take before the next weighing, the query's text_left counting down */
    Py_ssize_t widest;  /* the costliest substring read a weighing met: a walk of the tree costs at least that */
    th_node *root;      /* the subtree the sizing walk and the table cover; NULL until the first walk */
    th_node *sizing;    /* the next node the sizing walk reads; NULL once the walk has covered root */
    Py_ssize_t cost;    /* steps the sizing walk has taken so far; each weighing resumes it */
    text_slice *slices; /* in document order; NULL until the search builds the index */
    Py_ssize_t count;
    Py_ssize_t cursor; /* where the last lookup stopped: an axis only ever moves one way from there */
    Py_ssize_t step;   /* +1 when the axis hands its candidates over in document order, -1 when in reverse */
} text_index;

/* Fill index with an entry for each element at or below root, from one pre-order walk that feeds the text to a KMP
   matcher. An element holds the needle when the latest whole match starts at or after its start, and equals it when
   its text is needle-sized and that match starts at its start. Returns 0, or -1 on allocation failure with no
   exception set. */
static int text_index_build(th_tree *tree, th_node *root, const query_t *query, text_index *index) {
    const Py_UCS4 *needle = query->text_needle;
    Py_ssize_t needle_len = query->text_needle_len;
    Py_ssize_t *prefix = needle_len > 0 ? kmp_prefix(needle, needle_len) : NULL;
    Py_ssize_t elements = 0;
    for (th_node *node = root; node != NULL; node = preorder_next(node, root)) {
        elements += node->type == TH_NODE_ELEMENT;
    }
    text_slice *slices = PyMem_Malloc((size_t)elements * sizeof(*slices));
    int failed = slices == NULL; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
    if (needle_len > 0) {
        failed |= prefix == NULL;
    }
    Py_ssize_t count = 0;
    Py_ssize_t open = -1;                 /* the entry of the innermost element the walk is inside */
    Py_ssize_t offset = 0;                /* code points of text met so far */
    Py_ssize_t matched = 0;               /* how much of the needle the text just met ends with */
    Py_ssize_t latest = -1;               /* the offset the latest whole needle started at */
    th_node *node = failed ? NULL : root; /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
    while (node != NULL) {
        TH_WORK_STEP();
        if (node->type == TH_NODE_ELEMENT) {
            slices[count] = (text_slice){node, offset, open, 0};
            open = count++;
        } else if (node->type == TH_NODE_TEXT && query->text_scan == TH_TEXT_EQ && node->text_len > needle_len) {
            /* a Text longer than the needle rules out an exact match around it, so it is not read, as in
               text_scan_exact; no match can span it, so the matcher starts afresh */
            matched = 0;
            offset += node->text_len;
        } else if (node->type == TH_NODE_TEXT && node->text_len > 0) {
            const Py_UCS4 *text = th_node_realize_text(tree, node);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
                failed = 1;     /* GCOVR_EXCL_LINE: allocation failure */
                break;          /* GCOVR_EXCL_LINE: allocation failure */
            }
            for (Py_ssize_t at = 0; needle_len > 0 && at < node->text_len; at++) {
                while (matched > 0 && text[at] != needle[matched]) {
                    matched = prefix[matched - 1];
                }
                if (text[at] == needle[matched]) {
                    matched++;
                    if (matched == needle_len) {
                        latest = offset + at + 1 - needle_len;
                        matched = prefix[matched - 1];
                    }
                }
            }
            offset += node->text_len;
        }
        th_node *next = node->first_child;
        while (next == NULL) { /* nothing further down: leave node, and every ancestor it was the last child of */
            if (node->type == TH_NODE_ELEMENT) {
                text_slice *slice = &slices[open];
                open = slice->outer;
                if (query->text_scan == TH_TEXT_EQ) {
                    slice->hit = offset - slice->start == needle_len && (needle_len == 0 || latest == slice->start);
                } else {
                    slice->hit = needle_len == 0 || latest >= slice->start;
                }
            }
            if (node == root) {
                break;
            }
            next = node->next_sibling;
            if (next == NULL) {
                node = node->parent;
            }
        }
        node = next;
    }
    PyMem_Free(prefix);
    if (failed) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(slices); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* +1 for the axes whose candidates come in document order, -1 for those that come in reverse */
    static const Py_ssize_t axis_steps[] = {
        [TH_AXIS_DESCENDANTS] = 1,   [TH_AXIS_CHILDREN] = 1,           [TH_AXIS_ANCESTORS] = -1,
        [TH_AXIS_NEXT_SIBLINGS] = 1, [TH_AXIS_PREVIOUS_SIBLINGS] = -1, [TH_AXIS_FOLLOWING] = 1,
        [TH_AXIS_PRECEDING] = -1,
    };
    index->slices = slices;
    index->count = count;
    index->step = axis_steps[query->axis];
    index->cursor = index->step > 0 ? 0 : count - 1;
    return 0;
}

/* Called from inside a read once the reads have spent what they were granted: decide whether a text_index is the
   cheaper way on. The index covers the origin's subtree for DESCENDANTS and the whole tree for the other axes, which
   reach outside it. A walk of it costs a step per node, plus, for the substring scan, the node's text_len, the measure
   its reads are charged in; the exact scan, which skips long text, pays for nodes alone. The walk costs no less than
   the substring read in hand, whose cost is read (0 for an exact read), since that read walks part of the same tree.
   The index is built once the reads have spent TEXT_SCAN_PASSES walks; until then the grant doubles. The sizing walk
   only starts once the costliest read leaves it a chance and resumes where the previous weighing stopped, so it never
   costs more than that share of the reads. Returns 0 to go on reading, TEXT_SCAN_INDEXED once the index is built, or
   -1 on allocation failure with no exception set. */
TH_NOINLINE static int text_index_weigh(query_t *query, Py_ssize_t read) {
    text_index *index = query->text_index;
    Py_ssize_t spent = index->granted - query->text_left;
    index->granted = 2 * spent;
    query->text_left = spent;
    if (read > index->widest) {
        index->widest = read;
    }
    if (spent < TEXT_SCAN_PASSES * index->widest) {
        return 0;
    }
    if (index->root == NULL) {
        index->root = index->origin;
        while (query->axis != TH_AXIS_DESCENDANTS && index->root->parent != NULL) {
            index->root = index->root->parent;
        }
        index->sizing = index->root;
    }
    Py_ssize_t limit = spent / TEXT_SCAN_PASSES;
    while (index->sizing != NULL && index->cost <= limit) {
        TH_WORK_STEP();
        index->cost += query->text_scan == TH_TEXT_EQ ? 1 : index->sizing->text_len + 1;
        index->sizing = preorder_next(index->sizing, index->root);
    }
    if (index->cost > limit) {
        return 0;
    }
    if (text_index_build(index->tree, index->root, query, index) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return TEXT_SCAN_INDEXED;
}

/* Whether a candidate's text equals the needle, read from its own subtree. Only the nodes without text are charged, a
   step each: every non-empty Text the read meets either rules the candidate out or matches at least a code point of
   the needle, so they add at most one more than the needle's length per read, and an ordinary candidate, whose first
   Text decides, costs no accounting. */
static TH_FORCEINLINE int text_scan_exact(th_tree *tree, th_node *node, query_t *query) {
    Py_ssize_t offset = 0;
    for (th_node *descendant = node->first_child; descendant != NULL; descendant = preorder_next(descendant, node)) {
        TH_WORK_STEP();
        if (descendant->type != TH_NODE_TEXT || descendant->text_len == 0) {
            if (--query->text_left < 0) {
                if (text_index_weigh(query, 0) != 0) {
                    return -1;
                }
            }
            continue;
        }
        if (descendant->text_len > query->text_needle_len - offset) {
            return 0;
        }
        const Py_UCS4 *text = th_node_realize_text(tree, descendant);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return -1;      /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (memcmp(text, query->text_needle + offset, (size_t)descendant->text_len * sizeof(Py_UCS4)) != 0) {
            return 0;
        }
        offset += descendant->text_len;
    }
    return offset == query->text_needle_len;
}

/* Whether a candidate's collected text contains the needle, read from its own subtree. A read bounded by more than
   TEXT_SCAN_SMALL steps is charged that bound. */
static TH_FORCEINLINE int text_scan_contains(th_tree *tree, th_node *node, query_t *query, Py_UCS4 **scratch,
                                             Py_ssize_t *cap) {
    Py_ssize_t bound = subtree_text_bound(node);
    if (bound > TEXT_SCAN_SMALL && (query->text_left -= bound) < 0) {
        if (text_index_weigh(query, bound) != 0) {
            return -1;
        }
    }
    if (bound == 0) { /* a childless node leaves the scratch buffer NULL, which memcpy may not receive */
        return query->text_needle_len == 0;
    }
    if (scratch_ensure(scratch, cap, bound) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t text_len;
    if (th_node_collect_text(tree, node, *scratch, &text_len) < 0) { /* GCOVR_EXCL_BR_LINE: lazy text OOM */
        return -1;                                                   /* GCOVR_EXCL_LINE: allocation failure */
    }
    return ucs4_contains(*scratch, text_len, query->text_needle, query->text_needle_len);
}

/* Whether the candidate's text passes the scan, as the index holds it, or TEXT_SCAN_ABSENT when node is not in it. Only
   a filter that runs Python and moved nodes after the build can cause that. Entries are compared by address and never
   dereferenced, and their verdicts reflect the text as the building walk met it. */
static int text_index_lookup(text_index *index, th_node *node) {
    Py_ssize_t cursor = index->cursor;
    while (cursor >= 0 && cursor < index->count && index->slices[cursor].node != node) {
        cursor += index->step;
    }
    index->cursor = cursor;
    return cursor >= 0 && cursor < index->count ? index->slices[cursor].hit : TEXT_SCAN_ABSENT;
}

/* Out of line, so the reading loop never touches the index's fields and keeps none of them in a register. */
TH_NOINLINE static void text_index_free(text_index *index) {
    PyMem_Free(index->slices);
    index->slices = NULL;
}

/* Out of line, for the same reason. A filter that moved a candidate the table does not hold has changed the tree, so
   the table and the sizing walk that built it are dropped. The next table covers the whole subtree node is in, so a
   candidate the callable detached still falls in a table. The allowance carries on, so the reads after the change are
   charged as the earlier ones were. */
TH_NOINLINE static void text_index_rearm(text_index *index, th_node *node) {
    PyMem_Free(index->slices);
    index->slices = NULL;
    index->root = node;
    while (index->root->parent != NULL) {
        index->root = index->root->parent;
    }
    index->sizing = index->root;
    index->cost = 0;
}

/* Whether node passes the scan, read from its own subtree with a buffer of its own: out of line, so the reading loop
   keeps its registers and its scratch buffer. */
TH_NOINLINE static int text_scan_moved(th_node *node, query_t *query) {
    th_tree *tree = query->text_index->tree;
    if (query->text_scan == TH_TEXT_EQ) {
        return text_scan_exact(tree, node, query);
    }
    Py_UCS4 *scratch = NULL;
    Py_ssize_t scratch_cap = 0;
    int passed = text_scan_contains(tree, node, query, &scratch, &scratch_cap);
    PyMem_Free(scratch);
    return passed;
}

/* The rest of a search once the read of node, a candidate past the structural filters, built the text_index: the
   index answers for node and every later candidate. It sets found or fills result as find_with_text_scan does, and
   error when a filter fails or a read cannot allocate. Only a filter that moved nodes after the build can hand over a
   candidate the index does not hold. The index is dropped, and the candidate is read from its own subtree; that read is
   weighed like any other, so a new index is built once the reads have spent enough. Returns the candidate to go on
   from, or NULL once the search is over. */
TH_NOINLINE static th_node *text_index_search(module_state *state, PyObject *handle, th_node *origin, th_node *node,
                                              query_t *query, int want_all, text_index *index, PyObject *result,
                                              th_node **found, int *error) {
    for (int filtered = 1; node != NULL; node = axis_next(node, origin, query->axis), filtered = 0) {
        if (!filtered) {
            if (want_all && query->limit >= 0 && PyList_GET_SIZE(result) >= query->limit) {
                break;
            }
            int matched = node_matches(state, node, query);
            if (matched < 0) {
                *error = 1;
                break;
            }
            if (matched == 0) {
                continue;
            }
        }
        int passed = text_index_lookup(index, node);
        th_node *resume = NULL;
        if (passed == TEXT_SCAN_ABSENT) {
            /* the read builds no table: a table needs sixteen walks of its root spent, and the root holds the read */
            text_index_rearm(index, node);
            passed = text_scan_moved(node, query);
            if (passed < 0) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
                *error = 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
                break;            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            resume = node;
        }
        if (passed == 0) {
            if (resume != NULL) {
                return resume;
            }
            continue;
        }
        if (!want_all) {
            *found = node;
            break;
        }
        if (append_wrapped(result, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot fail */
            *error = 1;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (resume != NULL && PyList_GET_SIZE(result) != query->limit) {
            return resume;
        }
    }
    return NULL;
}

/* The candidate loop of find_with_text_scan, inlined once per scan so neither copy tests which scan runs for each
   candidate. It sets found or fills result as find_with_text_scan describes. Returns 0, or -1 with an exception set. */
static TH_FORCEINLINE int text_scan_candidates(module_state *state, PyObject *handle, th_node *origin, th_tree *tree,
                                               query_t *query, enum th_text_scan scan, int want_all, PyObject *result,
                                               th_node **found) {
    Py_UCS4 *scratch = NULL;
    Py_ssize_t scratch_cap = 0;
    int error = 0;
    /* only an append grows the result, so the limit is tested there rather than once per element */
    th_node *first = want_all && query->limit == 0 ? NULL : axis_first(origin, query->axis);
    for (th_node *node = first; node != NULL; node = axis_next(node, origin, query->axis)) {
        int matched = node_matches(state, node, query);
        if (matched < 0) {
            error = 1;
            break;
        }
        if (matched == 0) {
            continue;
        }
        int passed = scan == TH_TEXT_EQ ? text_scan_exact(tree, node, query)
                                        : text_scan_contains(tree, node, query, &scratch, &scratch_cap);
        if (passed < 0) {
            if (query->text_index->slices != NULL) { /* GCOVR_EXCL_BR_LINE: otherwise allocation failed, unforceable */
                node = text_index_search(state, handle, origin, node, query, want_all, query->text_index, result, found,
                                         &error);
                if (node == NULL) {
                    break;
                }
                continue;
            }
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            error = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
            break;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (passed == 0) {
            continue;
        }
        if (!want_all) {
            *found = node;
            break;
        }
        if (append_wrapped(result, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot fail */
            error = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (PyList_GET_SIZE(result) == query->limit) {
            break;
        }
    }
    PyMem_Free(scratch);
    return error ? -1 : 0;
}

/* The text= search when the predicate is a pure-C scan (an exact str or a literal
   regex). The text predicate runs no Python: the collected text never becomes a
   Python str, so the scan stays inside the lock. node_matches still runs the
   structural filters per element, and a callable among them may run Python, exactly
   as the non-text path does. Each candidate's text is read from its own subtree until
   those reads have cost what a text_index is worth (text_index_weigh); the index then
   answers for the rest of the search (text_index_search), which keeps it linear. */
static PyObject *find_with_text_scan(PyObject *self, query_t *query, int want_all) {
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    th_tree *tree = tree_of(self);
    PyObject *result = NULL;
    if (want_all) {
        result = PyList_New(0);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    th_node *found = NULL;
    text_index index = {.tree = tree, .origin = origin, .granted = TEXT_SCAN_BUDGET};
    query->text_left = TEXT_SCAN_BUDGET;
    query->text_index = &index;
    int error;
    Py_BEGIN_CRITICAL_SECTION(handle);
    if (query->text_scan == TH_TEXT_EQ) {
        error = text_scan_candidates(state, handle, origin, tree, query, TH_TEXT_EQ, want_all, result, &found);
    } else {
        error = text_scan_candidates(state, handle, origin, tree, query, TH_TEXT_SUBSTR, want_all, result, &found);
    }
    Py_END_CRITICAL_SECTION();
    if (index.slices != NULL) {
        text_index_free(&index);
    }
    if (error) {
        Py_XDECREF(result);
        return NULL;
    }
    if (want_all) {
        return result;
    }
    return node_wrap(state, handle, found);
}

/* Search along the axis for elements whose collected text satisfies the text
   predicate, composed with the tag, class, and attribute filters. The predicate
   (a str, compiled regex, or callable) runs over the snapshot taken under the lock;
   nodes are arena-allocated and never freed individually, so the snapshot's
   pointers stay valid across the predicate even if a concurrent mutation rewires
   the tree. Returns the first match (want_all 0, None when nothing matches) or the
   list of matches up to the limit (want_all 1). */
static PyObject *find_with_text(PyObject *self, query_t *query, int want_all) {
    if (query->text_scan != TH_TEXT_PY) {
        return find_with_text_scan(self, query, want_all);
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node **nodes;
    PyObject **texts;
    Py_ssize_t count;
    int error = snapshot_text_candidates(self, query, &nodes, &texts, &count) < 0;
    PyObject *result = NULL;
    if (!error && want_all) {
        result = PyList_New(0);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            error = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
    }
    th_node *found = NULL;
    for (Py_ssize_t index = 0; !error && index < count; index++) {
        if (want_all && query->limit >= 0 && PyList_GET_SIZE(result) >= query->limit) {
            break;
        }
        int matched = filter_matches(state, query->text, texts[index]);
        if (matched < 0) {
            error = 1;
            break;
        }
        if (matched == 0) {
            continue;
        }
        if (!want_all) {
            found = nodes[index];
            break;
        }
        if (append_wrapped(result, state, handle, nodes[index]) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot fail */
            error = 1;                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_DECREF(texts[index]);
    }
    PyMem_Free(nodes);
    PyMem_Free(texts);
    if (error) {
        Py_XDECREF(result);
        return NULL;
    }
    if (want_all) {
        return result;
    }
    return node_wrap(state, handle, found);
}

TH_NODE_API(, PyObject *, node_find, (PyObject * self, PyObject *args, PyObject *kwargs), (self, args, kwargs),
            (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    query_t query;
    if (build_query(self, args, kwargs, 0, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    if (query.text != NULL) {
        PyObject *result = find_with_text(self, &query, 0);
        free_query(&query);
        return result;
    }
    module_state *state = state_of(self);
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    th_node *found = NULL;
    int error = 0;
    PyObject *result = NULL;
    /* hold the per-tree lock across the walk so a concurrent extract() cannot rewire
       the child/sibling pointers mid-read (a no-op on the GIL build) */
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (handle_obj->index_built && handle_index_usable(handle_obj, origin) && query_is_indexed_tag(&query)) {
        Py_ssize_t pos = handle_obj->index_offsets[query.tag_atom];
        Py_ssize_t end = handle_obj->index_offsets[query.tag_atom + 1];
        if (query_is_simple_tag(&query)) {
            if (end > pos) {
                found = handle_obj->index_nodes[pos];
            }
        } else {
            for (; pos < end; pos++) {
                int matched = node_matches(state, handle_obj->index_nodes[pos], &query);
                if (matched < 0) {
                    error = 1;
                    break;
                }
                if (matched) {
                    found = handle_obj->index_nodes[pos];
                    break;
                }
            }
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
    if (!error) {
        result = node_wrap(state, handle, found);
    }
    Py_END_CRITICAL_SECTION();
    free_query(&query);
    return result;
}

/* Build the element index an unbounded document-rooted tag query reads before the query
   runs, and raise MemoryError when the build fails, where node_find_all would fall back to
   a walk. The same check inside node_find_all cost find 430 instructions a call. Flattened:
   as an out-of-line clone the build cost parse-inner 11,000 instructions a call more than on
   main, where it inlines into node_find_all. */
#if defined(__GNUC__) || defined(__clang__)
__attribute__((flatten))
#endif
static int find_index_prebuild(PyObject *self, const query_t *query) {
    if (query->limit >= 0 && query->limit <= 8) {
        return 0;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    if (!handle_obj->index_built && handle_index_usable(handle_obj, ((NodeObject *)self)->node) &&
        query_is_indexed_tag(query)) {
        failed = handle_build_index(handle_obj) < 0; /* GCOVR_EXCL_BR_LINE: the build fails only to allocate */
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {         /* GCOVR_EXCL_BR_LINE: the build fails only to allocate */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

TH_NODE_API(, PyObject *, node_find_all, (PyObject * self, PyObject *args, PyObject *kwargs), (self, args, kwargs),
            (PyObject * self, PyObject *args, PyObject *kwargs), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    query_t query;
    if (build_query(self, args, kwargs, 1, &query) < 0) {
        free_query(&query);
        return NULL;
    }
    if (query.text != NULL) {
        PyObject *result = find_with_text(self, &query, 1);
        free_query(&query);
        return result;
    }
    if (find_index_prebuild(self, &query) < 0) { /* GCOVR_EXCL_BR_LINE: the build fails only to allocate */
        free_query(&query);                      /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                             /* GCOVR_EXCL_LINE: allocation-failure path */
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
    int use_index =
        query.limit >= 0 && query.limit <= 8
            ? handle_obj->index_built && handle_index_usable(handle_obj, origin) && query_is_indexed_tag(&query)
            : handle_use_index(handle_obj, origin, query_is_indexed_tag(&query));
    if (use_index && query_is_simple_tag(&query)) {
        Py_SETREF(out, node_wrap_indexed(state, handle, query.tag_atom, query.limit));
        error = out == NULL;
    } else if (use_index && query.nattr == 1 && query.class_ucs4 == NULL && query.class_filter == NULL &&
               (query.attrs[0].kind == TH_FIND_PRESENT || query.attrs[0].kind == TH_FIND_ABSENT)) {
        Py_ssize_t end = handle_obj->index_offsets[query.tag_atom + 1];
        for (Py_ssize_t pos = handle_obj->index_offsets[query.tag_atom]; pos < end; pos++) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            th_node *node = handle_obj->index_nodes[pos];
            if ((find_node_attr(node, query.attrs[0].atom) != NULL) != (query.attrs[0].kind == TH_FIND_PRESENT)) {
                continue;
            }
            if (append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                error = 1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    } else if (use_index) {
        Py_ssize_t end = handle_obj->index_offsets[query.tag_atom + 1];
        for (Py_ssize_t pos = handle_obj->index_offsets[query.tag_atom]; pos < end; pos++) {
            if (query.limit >= 0 && PyList_GET_SIZE(out) >= query.limit) {
                break;
            }
            th_node *node = handle_obj->index_nodes[pos];
            int matched = node_matches(state, node, &query);
            if (matched < 0) {
                error = 1;
                break;
            }
            if (matched == 0) {
                continue;
            }
            if (append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
                error = 1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
                break;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
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
        Py_XDECREF(out);
        return NULL;
    }
    return out;
}
