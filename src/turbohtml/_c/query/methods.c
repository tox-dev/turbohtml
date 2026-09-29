/* The Node query-method bindings: CSS select/matches/closest, the regex text
   search, XPath evaluation and the compiled-XPath type, plus the selector- and
   XPath-cache management they share. Split out of dom/element.c (issue #478) so
   the query surface lives under query/ beside the engines it drives; the DOM
   element and mutation bindings stay in element.c. */

#include "core/node_map.h"
#include "core/vec.h"
#include "dom/nodes.h"
#include "css/select/selector.h"

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

void handle_clear_caches(HandleObject *handle) {
    handle_clear_css_cache(handle);
    for (int index = 0; index < handle->sel_cache_len; index++) {
        selector_free(handle->sel_cache[index].compiled);
        Py_DECREF(handle->sel_cache[index].key);
    }
    for (int index = 0; index < handle->xpath_cache_len; index++) {
        xp_free(handle->xpath_cache[index].prog);
        Py_DECREF(handle->xpath_cache[index].key);
    }
    PyMem_Free(handle->sel_cache);
    PyMem_Free(handle->xpath_cache);
}

/* The tag atom every alternative of compiled selects as its subject (rightmost
   compound), or TH_TAG_UNKNOWN when the subjects differ or any is not a plain
   type selector. Such a selector can enumerate just that atom's index bucket. An XML
   tree files every element under TH_TAG_UNKNOWN, so its selectors never use the index. */
static uint16_t selector_subject_atom(const sel_compiled *compiled) {
    if (th_tree_is_xml(compiled->tree)) {
        return TH_TAG_UNKNOWN;
    }
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
static sel_compiled *cached_compile(PyObject *selector_error, HandleObject *handle, PyObject *arg) {
restart:;
    uint32_t gen = th_tree_attr_generation(handle->tree);
    for (int index = 0; index < handle->sel_cache_len; index++) {
        sel_cache_entry entry = handle->sel_cache[index];
        if (entry.key != arg && PyUnicode_Compare(entry.key, arg) != 0) {
            continue;
        }
        if (entry.attr_gen != gen) {
            sel_compiled *fresh = selector_compile(selector_error, handle->tree, arg);
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
    if (handle->sel_cache_len == SEL_CACHE_CAP) {
        sel_cache_entry evicted = handle->sel_cache[--handle->sel_cache_len];
        selector_free(evicted.compiled);
        /* A selector subclass finalizer can query or mutate this tree. */
        if (!PyUnicode_CheckExact(evicted.key)) {
            Py_DECREF(evicted.key);
            goto restart;
        }
        Py_DECREF(evicted.key);
    }
    sel_compiled *compiled = selector_compile(selector_error, handle->tree, arg);
    if (compiled == NULL) {
        return NULL;
    }
    if (handle->sel_cache == NULL) {
        handle->sel_cache = PyMem_Malloc(sizeof(sel_cache_entry) * SEL_CACHE_CAP);
        if (handle->sel_cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            selector_free(compiled);     /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();            /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    handle->sel_cache_len++;
    memmove(&handle->sel_cache[1], &handle->sel_cache[0],
            (size_t)(handle->sel_cache_len - 1) * sizeof(sel_cache_entry));
    handle->sel_cache[0] = (sel_cache_entry){Py_NewRef(arg), compiled, gen};
    return compiled;
}

/* Store the SelectorSyntaxError type the selector and CSS-to-XPath parsers raise on a
   malformed selector; turbohtml._selectors registers it on import. */
PyObject *turbohtml_register_selector_error(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->selector_error, Py_NewRef(type));
    Py_RETURN_NONE;
}

static int append_selected(PyObject *out, module_state *state, PyObject *handle, th_node *origin,
                           sel_compiled *compiled, Py_ssize_t limit, const sel_simple *single) {
    int error = 0;
    sel_nth_memo nth_memo = {0};
    sel_default_memo default_memo = {0};
    sel_has_memo has_memo = {0};
    uint16_t subject = selector_subject_atom(compiled);
    HandleObject *handle_obj = (HandleObject *)handle;
    sel_ctx ctx = {compiled->tree, origin,       compiled->quirks, selector_uses_has_memo(compiled) ? &has_memo : NULL,
                   &nth_memo,      &default_memo};
    if (handle_use_index(handle_obj, origin, subject != TH_TAG_UNKNOWN)) {
        Py_ssize_t end = handle_obj->index_offsets[subject + 1];
        for (Py_ssize_t pos = handle_obj->index_offsets[subject]; pos < end; pos++) {
            th_node *node = handle_obj->index_nodes[pos];
            int matched = selector_matches_c(node, compiled, &ctx);
            if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                error = 1;                                                 /* GCOVR_EXCL_LINE */
                break;                                                     /* GCOVR_EXCL_LINE */
            }
            if (limit > 0 && PyList_GET_SIZE(out) == limit) {
                break;
            }
        }
    } else {
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (node->type != TH_NODE_ELEMENT) {
                continue;
            }
            int matched =
                single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches_c(node, compiled, &ctx);
            if (matched && append_wrapped(out, state, handle, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                error = 1;                                                 /* GCOVR_EXCL_LINE */
                break;                                                     /* GCOVR_EXCL_LINE */
            }
            if (limit > 0 && PyList_GET_SIZE(out) == limit) {
                break;
            }
        }
    }
    sel_has_memo_free(&has_memo);
    if (error) { /* GCOVR_EXCL_START: append_wrapped failed to allocate */
        return -1;
    } /* GCOVR_EXCL_STOP */
    return 0;
}

/* The matching descendants of `self`, at most `limit` of them in document order (0 for all). */
static PyObject *select_limited(PyObject *self, PyObject *arg, Py_ssize_t limit) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    module_state *state = state_of(self);
retry:;
    PyObject *out = PyList_New(0);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    int error = 0;
    int moved = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: a concurrent mutate must not rewire mid-walk */
    HandleObject *handle_obj = (HandleObject *)handle;
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, handle_obj, arg);
    if (compiled == NULL) {
        error = -1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *origin = ((NodeObject *)self)->node;
        const sel_simple *single = sel_single_simple(compiled);
        if (single != NULL && single->kind == 'e' && single->tag_atom != TH_TAG_UNKNOWN &&
            !th_tree_is_xml(compiled->tree) && handle_use_index(handle_obj, origin, 1)) {
            Py_SETREF(out, node_wrap_indexed(state, handle, single->tag_atom, limit > 0 ? limit : -1));
            error = out == NULL;
        } else {
            error = append_selected(out, state, handle, origin, compiled, limit, single);
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        Py_DECREF(out);
        goto retry;
    }
    if (error) {
        Py_XDECREF(out);
        return NULL;
    }
    return out;
}

TH_NODE_API(, PyObject *, node_select, (PyObject * self, PyObject *arg), (self, arg), (PyObject * self, PyObject *arg),
            (NodeObject *)self, arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    return select_limited(self, arg, 0);
}

/* _select_limited(node, selector, limit) -> list[Element]: the first `limit` matching descendants in document order,
   or every one for a limit of 0 or less; the walk stops once it has enough. */
TH_NODE_API(, PyObject *, turbohtml_select_limited, (PyObject * module, PyObject *args), (module, args),
            (PyObject * module, PyObject *args), node_argument(PyModule_GetState(module), args, NULL, 0, NULL), NULL) {
    PyObject *node;
    PyObject *selector;
    Py_ssize_t limit;
    if (!PyArg_ParseTuple(args, "OUn:_select_limited", &node, &selector, &limit)) {
        return NULL;
    }
    th_tree *tree;
    th_node *borrowed;
    if (turbohtml_node_borrow(module, node, &tree, &borrowed) < 0) {
        return NULL;
    }
    return select_limited(node, selector, limit > 0 ? limit : 0);
}

typedef struct {
    NodeObject *node;
    int processed;
    Py_ssize_t next;
    Py_ssize_t tail;
    Py_ssize_t next_group;
    Py_ssize_t next_handle;
} multi_root;

typedef struct {
    Py_ssize_t offset;
    Py_ssize_t size;
} multi_batch;

PyObject *turbohtml_matches_many(PyObject *module, PyObject *args) {
    PyObject *nodes;
    PyObject *selector;
    if (!PyArg_ParseTuple(args, "O!U:_matches_many", &PyList_Type, &nodes, &selector)) {
        return NULL;
    }
    Py_ssize_t count = PyList_GET_SIZE(nodes);
    if (count == 0) {
        return PyList_New(0);
    }
    unsigned char *results = PyMem_Calloc((size_t)count, sizeof(unsigned char));
    if (results == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    module_state *state = PyModule_GetState(module);
    Py_ssize_t matched_count = 0;
    int error = 0;
    for (Py_ssize_t first = 0; first < count;) {
        PyObject *item = PyList_GET_ITEM(nodes, first);
        if (!Py_IS_TYPE(item, (PyTypeObject *)state->element_type)) {
            PyErr_SetString(PyExc_TypeError, "filter candidates must be Element instances");
            error = 1;
            break;
        }
        NodeObject *anchor = (NodeObject *)item;
        Py_ssize_t index = first;
        PyObject *handle = node_owner_ref(anchor);
        int moved = 0;
        Py_BEGIN_CRITICAL_SECTION(handle);
        sel_compiled *compiled = cached_compile(state->selector_error, (HandleObject *)handle, selector);
        if (compiled == NULL) {
            error = 1;
        } else if (!node_owned_by(anchor, handle)) {
            moved = 1;
        } else {
            for (; index < count; index++) {
                item = PyList_GET_ITEM(nodes, index);
                if (!Py_IS_TYPE(item, (PyTypeObject *)state->element_type)) {
                    PyErr_SetString(PyExc_TypeError, "filter candidates must be Element instances");
                    error = 1;
                    break;
                }
                NodeObject *candidate = (NodeObject *)item;
                if (!node_owned_by(candidate, handle)) {
                    break;
                }
                int matched = selector_matches(candidate->node, compiled, candidate->node);
                results[index] = (unsigned char)matched;
                matched_count += matched;
            }
        }
        Py_END_CRITICAL_SECTION();
        Py_DECREF(handle);
        if (moved) {
            continue;
        }
        if (error) {
            break;
        }
        first = index;
    }
    PyObject *out = NULL;
    if (!error && (out = PyList_New(matched_count)) != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_ssize_t position = 0;
        for (Py_ssize_t index = 0; index < count; index++) {
            if (results[index] != 0) {
                PyList_SET_ITEM(out, position++, Py_NewRef(PyList_GET_ITEM(nodes, index)));
            }
        }
    }
    PyMem_Free(results);
    return out;
}

static int compare_roots(const void *left, const void *right) {
    th_node *left_node = *(th_node *const *)left;
    th_node *right_node = *(th_node *const *)right;
    return left_node == right_node ? 0 : node_order(left_node, right_node); /* GCOVR_EXCL_BR_LINE: qsort self-compare */
}

static int sort_sibling_roots(th_node **group, Py_ssize_t group_count) {
    th_node *parent = group[0]->parent;
    for (Py_ssize_t index = 1; index < group_count; index++) {
        if (group[index]->parent != parent) {
            return 0;
        }
    }
    th_node *after = parent->first_child;
    for (Py_ssize_t index = 0; index < group_count; index++) {
        after = after->next_sibling;
    }
    /* Unique roots cover their parent's children when the counts match. */
    if (after == NULL) {
        th_node *node = parent->first_child;
        for (Py_ssize_t index = 0; index < group_count; index++) {
            group[index] = node;
            node = node->next_sibling;
        }
        return 1;
    }
    th_node_map selected = {0};
    for (Py_ssize_t index = 0; index < group_count; index++) {
        if (th_node_map_insert(&selected, group[index], 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyMem_Free(selected.entries);                         /* GCOVR_EXCL_LINE */
            return -1;                                            /* GCOVR_EXCL_LINE */
        }
    }
    Py_ssize_t position = 0;
    for (th_node *node = parent->first_child; position < group_count; node = node->next_sibling) {
        if (th_node_map_find(&selected, node) != 0) {
            group[position++] = node;
        }
    }
    PyMem_Free(selected.entries);
    return 1;
}

static int sort_roots(th_node **group, Py_ssize_t group_count) {
    if (group_count >= 32) {
        int sorted = sort_sibling_roots(group, group_count);
        if (sorted != 0) {
            return sorted < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: allocation failure */
        }
        for (Py_ssize_t index = 1; index < group_count; index++) {
            if (node_order(group[index], group[index - 1]) < 0) {
                qsort(group, (size_t)group_count, sizeof(th_node *), compare_roots);
                break;
            }
        }
        return 0;
    }
    for (Py_ssize_t index = 1; index < group_count; index++) {
        th_node *node = group[index];
        Py_ssize_t position = index;
        while (position > 0 && node_order(node, group[position - 1]) < 0) {
            group[position] = group[position - 1];
            position--;
        }
        group[position] = node;
    }
    return 0;
}

#ifndef Py_GIL_DISABLED
static PyObject *index_root_handles(multi_root *roots, Py_ssize_t count, PyObject *selector) {
    if (count < 32 || !PyUnicode_CheckExact(selector)) {
        return NULL;
    }
    Py_ssize_t distinct = 1;
    while (distinct < count && roots[distinct].node->handle == roots[0].node->handle) {
        distinct++;
    }
    if (distinct == count) {
        return NULL;
    }
    PyObject *handles = PyDict_New();
    if (handles == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = count; index-- > 0;) {
        HandleObject *handle = (HandleObject *)roots[index].node->handle;
        PyObject *previous = PyDict_GetItemWithError(handles, (PyObject *)handle);
        if (previous == NULL) {
            /* Exact keys cannot run a destructor during later selector-cache eviction. */
            for (int entry = 0; entry < handle->sel_cache_len; entry++) {
                if (!PyUnicode_CheckExact(handle->sel_cache[entry].key)) {
                    Py_DECREF(handles);
                    return NULL;
                }
            }
        }
        roots[index].next_handle = previous == NULL ? count : PyLong_AsSsize_t(previous);
        PyObject *position = PyLong_FromSsize_t(index);
        if (position == NULL || PyDict_SetItem(handles, (PyObject *)handle, position) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(position); /* GCOVR_EXCL_LINE: allocation failure */
            Py_DECREF(handles);   /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        Py_DECREF(position);
    }
    return handles;
}
#endif

static Py_ssize_t index_root_group(th_node_map *groups, th_node *root, Py_ssize_t position) {
    size_t slot = 0;
    if (groups->capacity != 0) {
        slot = th_node_map_slot(groups, root);
        if (groups->entries[slot].node != NULL) {
            return groups->entries[slot].value;
        }
    }
    size_t capacity = groups->capacity;
    if (th_node_map_reserve(groups, groups->count + 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                            /* GCOVR_EXCL_LINE */
    }
    if (groups->capacity != capacity) {
        slot = th_node_map_slot(groups, root);
    }
    groups->entries[slot] = (th_node_map_entry){root, position};
    groups->count++;
    return 0;
}

PyObject *turbohtml_select_many(PyObject *module, PyObject *args) {
    PyObject *roots_obj;
    PyObject *selector;
    if (!PyArg_ParseTuple(args, "O!U:_select_many", &PyList_Type, &roots_obj, &selector)) {
        return NULL;
    }
    Py_ssize_t count = PyList_GET_SIZE(roots_obj);
    PyObject *out = PyList_New(0);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    if (count == 0) {
        return out;
    }
    multi_root *roots = PyMem_Calloc((size_t)count, sizeof(multi_root));
    th_node **group = PyMem_Malloc((size_t)count * sizeof(th_node *));
    multi_batch *batches = PyMem_Calloc((size_t)count, sizeof(multi_batch));
    if (roots == NULL || group == NULL || batches == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(roots);                                   /* GCOVR_EXCL_LINE */
        PyMem_Free(group);                                   /* GCOVR_EXCL_LINE */
        PyMem_Free(batches);                                 /* GCOVR_EXCL_LINE */
        Py_DECREF(out);                                      /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                             /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        roots[index].node = (NodeObject *)PyList_GET_ITEM(roots_obj, index);
    }
    module_state *state = PyModule_GetState(module);
    int error = 0;
    PyObject *handles = NULL;
#ifndef Py_GIL_DISABLED
    handles = index_root_handles(roots, count, selector);
    if (PyErr_Occurred() != NULL) { /* GCOVR_EXCL_BR_LINE: handle-index allocation failure */
        PyMem_Free(batches);        /* GCOVR_EXCL_LINE */
        PyMem_Free(group);          /* GCOVR_EXCL_LINE */
        PyMem_Free(roots);          /* GCOVR_EXCL_LINE */
        Py_DECREF(out);             /* GCOVR_EXCL_LINE */
        return NULL;                /* GCOVR_EXCL_LINE */
    }
#endif
    int ordered = 1;
    Py_ssize_t previous_group = 0;
    for (Py_ssize_t first = 0; first < count;) {
        if (roots[first].processed) {
            first++;
            continue;
        }
        NodeObject *anchor = roots[first].node;
        PyObject *handle = node_owner_ref(anchor);
        int moved = 0;
        Py_BEGIN_CRITICAL_SECTION(handle);
        sel_compiled *compiled = cached_compile(state->selector_error, (HandleObject *)handle, selector);
        if (compiled == NULL) {
            error = 1;
        } else if (!node_owned_by(anchor, handle)) {
            moved = 1;
        } else {
            th_node_map tops = {0};
            th_node *first_top = NULL;
            Py_ssize_t first_group = 0;
            Py_ssize_t last_group = 0;
            for (Py_ssize_t index = first; index < count;
                 index = handles == NULL ? index + 1 : roots[index].next_handle) {
                NodeObject *candidate = roots[index].node;
                if (roots[index].processed || !node_owned_by(candidate, handle)) {
                    continue;
                }
                th_node *top = node_root(candidate->node);
                Py_ssize_t head = top == first_top   ? first_group
                                  : first_group == 0 ? 0
                                                     : index_root_group(&tops, top, index + 1);
                if (head < 0) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
                    PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
                    error = 1;        /* GCOVR_EXCL_LINE */
                    break;            /* GCOVR_EXCL_LINE */
                }
                if (head == 0) {
                    if (first_group != 0) {
                        roots[last_group - 1].next_group = index + 1;
                    } else {
                        first_top = top;
                        first_group = index + 1;
                    }
                    last_group = index + 1;
                    roots[index].tail = index;
                } else {
                    roots[roots[head - 1].tail].next = index + 1;
                    roots[head - 1].tail = index;
                }
                roots[index].processed = 1;
            }
            PyMem_Free(tops.entries);
            /* Keep tracked-list allocation outside the lifetime of the root grouping. */
            for (Py_ssize_t head = first_group; head != 0; head = roots[head - 1].next_group) {
                if (error) { /* GCOVR_EXCL_BR_LINE: group-map or result allocation failure */
                    break;   /* GCOVR_EXCL_LINE */
                }
                Py_ssize_t tree_first = head - 1;
                ordered = ordered && tree_first >= previous_group;
                previous_group = tree_first;
                Py_ssize_t group_count = 0;
                for (Py_ssize_t member = head; member != 0; member = roots[member - 1].next) {
                    group[group_count++] = roots[member - 1].node->node;
                }
                if (sort_roots(group, group_count) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    PyErr_NoMemory();                     /* GCOVR_EXCL_LINE */
                    error = 1;                            /* GCOVR_EXCL_LINE */
                    break;                                /* GCOVR_EXCL_LINE */
                }
                batches[tree_first].offset = PyList_GET_SIZE(out);
                th_node *covered = NULL;
                for (Py_ssize_t index = 0; index < group_count; index++) {
                    th_node *origin = group[index];
                    if (covered != NULL && is_ancestor(covered, origin)) {
                        continue;
                    }
                    covered = origin;
                    int append_error =
                        append_selected(out, state, anchor->handle, origin, compiled, 0, sel_single_simple(compiled));
                    if (append_error < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                        error = 1;          /* GCOVR_EXCL_LINE */
                        break;              /* GCOVR_EXCL_LINE */
                    }
                }
                batches[tree_first].size = PyList_GET_SIZE(out) - batches[tree_first].offset;
            }
        }
        Py_END_CRITICAL_SECTION();
        Py_DECREF(handle);
        if (moved) {
            continue;
        }
        if (error) {
            break;
        }
        first++;
    }
    Py_XDECREF(handles);
    if (!error && !ordered) {
        PyObject *result = PyList_New(PyList_GET_SIZE(out));
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            error = 1;        /* GCOVR_EXCL_LINE */
        } else {              /* GCOVR_EXCL_LINE: clang attributes the allocation-failure edge to this brace */
            Py_ssize_t position = 0;
            for (Py_ssize_t index = 0; index < count; index++) {
                for (Py_ssize_t item = 0; item < batches[index].size; item++) {
                    PyList_SET_ITEM(result, position++, Py_NewRef(PyList_GET_ITEM(out, batches[index].offset + item)));
                }
            }
            Py_SETREF(out, result);
        }
    }
    PyMem_Free(batches);
    PyMem_Free(group);
    PyMem_Free(roots);
    if (error) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

TH_NODE_API(, PyObject *, node_select_one, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), (NodeObject *)self,
            arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    th_node *found = NULL;
    int error = 0;
    int moved = 0;
    sel_nth_memo nth_memo = {0};
    sel_default_memo default_memo = {0};
    sel_has_memo has_memo = {0};       /* shared across the walk so :has() memoizes its subtree scans */
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock around the walk */
    HandleObject *handle_obj = (HandleObject *)handle;
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, handle_obj, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *origin = ((NodeObject *)self)->node;
        const sel_simple *single = sel_single_simple(compiled);
        uint16_t subject = selector_subject_atom(compiled);
        sel_ctx ctx = {compiled->tree,   origin,
                       compiled->quirks, selector_uses_has_memo(compiled) ? &has_memo : NULL,
                       &nth_memo,        &default_memo};
        if (handle_use_index(handle_obj, origin, subject != TH_TAG_UNKNOWN)) {
            Py_ssize_t end = handle_obj->index_offsets[subject + 1];
            for (Py_ssize_t pos = handle_obj->index_offsets[subject]; pos < end; pos++) {
                th_node *node = handle_obj->index_nodes[pos];
                if (single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches_c(node, compiled, &ctx)) {
                    found = node;
                    break;
                }
            }
        } else {
            for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
                if (node->type != TH_NODE_ELEMENT) {
                    continue;
                }
                if (single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches_c(node, compiled, &ctx)) {
                    found = node;
                    break;
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    sel_has_memo_free(&has_memo);
    if (error) {
        return NULL;
    }
    return node_wrap(state_of(self), handle, found);
}

/* Turn the pattern argument into a compiled re.Pattern: a str is compiled through
   re.compile, an already-compiled pattern is returned with a new reference. NULL
   with an exception set on a bad type or a compile error. */
static PyObject *coerce_pattern(module_state *state, PyObject *pattern) {
    if (PyUnicode_Check(pattern)) {
        return PyObject_CallOneArg(state->re_compile, pattern);
    }
    if (PyObject_TypeCheck(pattern, (PyTypeObject *)state->pattern_type)) {
        return Py_NewRef(pattern);
    }
    PyErr_SetString(PyExc_TypeError, "pattern must be a str or a compiled re.Pattern");
    return NULL;
}

/* Snapshot the string a regex runs over: the node's text when attr_name is NULL,
   else the named attribute's value ("" for a valueless attribute). *absent is set
   when attr_name names an attribute the node does not carry, so the caller yields
   no match. Holds the per-tree lock so a concurrent mutate cannot rewire the
   subtree or resize the attribute array mid-read. NULL with *absent set and no
   exception for the absent case, or NULL with an exception on allocation failure. */
static PyObject *regex_source(PyObject *handle, th_tree *tree, th_node *node, const char *attr_name,
                              Py_ssize_t attr_len, int *absent) {
    *absent = 0;
    PyObject *source = NULL;
    (void)handle; /* only the per-tree lock on free-threaded builds; a no-op argument otherwise */
    Py_BEGIN_CRITICAL_SECTION(handle);
    if (attr_name == NULL) {
        source = str_from_accessor(th_node_text, tree, node);
    } else {
        Py_ssize_t index = find_attr_index(tree, node, attr_name, attr_len);
        if (index < 0) {
            *absent = 1;
        } else {
            const th_node_attr *attr = &node->attrs[index];
            source = attr->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(attr->value, attr->value_len);
        }
    }
    Py_END_CRITICAL_SECTION();
    return source;
}

/* The number of capturing groups in a compiled pattern, or -1 with an exception. */
static long pattern_group_count(PyObject *compiled) {
    PyObject *groups = PyObject_GetAttrString(compiled, "groups");
    if (groups == NULL) { /* GCOVR_EXCL_BR_LINE: a compiled pattern always exposes .groups */
        return -1;        /* GCOVR_EXCL_LINE: unreachable on a real re.Pattern */
    }
    long count = PyLong_AsLong(groups);
    Py_DECREF(groups);
    return count; /* .groups is a non-negative int, so the AsLong overflow path cannot fire */
}

/* Every match of compiled over source as a list[str]: with zero or one capturing
   group re.findall already yields the strings; with two or more groups it yields
   tuples, so fall back to the whole match per finditer. */
static PyObject *regex_find_all(PyObject *compiled, PyObject *source) {
    long count = pattern_group_count(compiled);
    if (count < 0) { /* GCOVR_EXCL_BR_LINE: .groups is always a readable non-negative int */
        return NULL; /* GCOVR_EXCL_LINE: unreachable on a real re.Pattern */
    }
    if (count <= 1) {
        return PyObject_CallMethod(compiled, "findall", "O", source);
    }
    PyObject *iterator = PyObject_CallMethod(compiled, "finditer", "O", source);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: finditer over a str cannot raise */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *out = PyList_New(0);
    if (out == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(iterator); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *match;
    int error = 0;
    while ((match = PyIter_Next(iterator)) != NULL) {
        PyObject *whole = PyObject_CallMethod(match, "group", "i", 0);
        Py_DECREF(match);
        if (whole == NULL || PyList_Append(out, whole) < 0) { /* GCOVR_EXCL_BR_LINE: group(0)/append cannot fail */
            Py_XDECREF(whole);                                /* GCOVR_EXCL_LINE: allocation-failure path */
            error = 1;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(whole);
    }
    Py_DECREF(iterator);
    if (error) {        /* GCOVR_EXCL_BR_LINE: the loop body only breaks on unforceable allocation failure */
        Py_DECREF(out); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return out;
}

/* The first match of compiled over source as a str (its one capturing group when
   the pattern has exactly one, else the whole match), or a new reference to
   fallback when nothing matches. */
static PyObject *regex_find_first(PyObject *compiled, PyObject *source, PyObject *fallback) {
    PyObject *match = PyObject_CallMethod(compiled, "search", "O", source);
    if (match == NULL) { /* GCOVR_EXCL_BR_LINE: search over a str cannot raise */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (match == Py_None) {
        Py_DECREF(match);
        return Py_NewRef(fallback);
    }
    long count = pattern_group_count(compiled);
    if (count < 0) {      /* GCOVR_EXCL_BR_LINE: .groups is always a readable non-negative int */
        Py_DECREF(match); /* GCOVR_EXCL_LINE: unreachable on a real re.Pattern */
        return NULL;      /* GCOVR_EXCL_LINE: unreachable on a real re.Pattern */
    }
    PyObject *value = PyObject_CallMethod(match, "group", "i", count == 1 ? 1 : 0);
    Py_DECREF(match);
    return value;
}

/* Resolve the optional attr keyword into a UTF-8 name for a lookup on tree (the caller
   frees it with PyMem_Free), leaving *name NULL when the regex should run over text. A
   non-str, non-None attr raises TypeError. Returns 0, or -1 with an exception. */
static int regex_attr_name(th_tree *tree, PyObject *attr_obj, char **name, Py_ssize_t *name_len) {
    *name = NULL;
    *name_len = 0;
    if (attr_obj == NULL || attr_obj == Py_None) {
        return 0;
    }
    *name = attr_key_utf8(tree, attr_obj, name_len);
    return *name == NULL ? -1 : 0;
}

TH_NODE_API(, PyObject *, node_re, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    static char *kw[] = {"", "attr", NULL};
    PyObject *pattern;
    PyObject *attr_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|$O:re", kw, &pattern, &attr_obj)) {
        return NULL;
    }
    char *attr_name;
    Py_ssize_t attr_len;
    if (regex_attr_name(tree_of(self), attr_obj, &attr_name, &attr_len) < 0) {
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *compiled = coerce_pattern(state, pattern);
    if (compiled == NULL) {
        PyMem_Free(attr_name);
        return NULL;
    }
    int absent;
    NodeObject *node = (NodeObject *)self;
    PyObject *source = regex_source(node->handle, tree_of(self), node->node, attr_name, attr_len, &absent);
    PyMem_Free(attr_name);
    if (absent) {
        Py_DECREF(compiled);
        return PyList_New(0);
    }
    if (source == NULL) {    /* GCOVR_EXCL_BR_LINE: with absent ruled out, only an allocation failure reaches here */
        Py_DECREF(compiled); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = regex_find_all(compiled, source);
    Py_DECREF(compiled);
    Py_DECREF(source);
    return result;
}

TH_NODE_API(, PyObject *, node_re_first, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    static char *kw[] = {"", "default", "attr", NULL};
    PyObject *pattern;
    PyObject *fallback = Py_None;
    PyObject *attr_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|O$O:re_first", kw, &pattern, &fallback, &attr_obj)) {
        return NULL;
    }
    char *attr_name;
    Py_ssize_t attr_len;
    if (regex_attr_name(tree_of(self), attr_obj, &attr_name, &attr_len) < 0) {
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *compiled = coerce_pattern(state, pattern);
    if (compiled == NULL) {
        PyMem_Free(attr_name);
        return NULL;
    }
    int absent;
    NodeObject *node = (NodeObject *)self;
    PyObject *source = regex_source(node->handle, tree_of(self), node->node, attr_name, attr_len, &absent);
    PyMem_Free(attr_name);
    if (absent) {
        Py_DECREF(compiled);
        return Py_NewRef(fallback);
    }
    if (source == NULL) {    /* GCOVR_EXCL_BR_LINE: with absent ruled out, only an allocation failure reaches here */
        Py_DECREF(compiled); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = regex_find_first(compiled, source, fallback);
    Py_DECREF(compiled);
    Py_DECREF(source);
    return result;
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
    if (handle->xpath_cache == NULL) {
        handle->xpath_cache = PyMem_Malloc(sizeof(xpath_cache_entry) * XPATH_CACHE_CAP);
        if (handle->xpath_cache == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            xp_free(prog);                 /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();              /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
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

typedef struct xpath_ext_ctx {
    PyObject *extensions; /* {(None, name): callable}; only set when non-empty */
    PyObject *handle;
    module_state *state;
    th_tree *tree;
    PyObject **retained;
    size_t retained_len;
    size_t retained_cap;
    th_node_map owners;
    th_node_map text_parents;
    struct xpath_ext_ctx *previous;
    struct xpath_frame_pins **frames;
    size_t frame_count;
} xpath_ext_ctx;

typedef struct xpath_frame_pins {
    uint64_t activation;
    uint64_t version;
    size_t references;
    Py_ssize_t appended;
    xpath_ext_ctx pins;
} xpath_frame_pins;

static PyObject *xpath_owned_node(xpath_ext_ctx *ec, th_node *node);
static PyObject *xpath_text_parent(xpath_ext_ctx *ec, th_node *node);

static PyObject *xpath_item_to_py(module_state *state, PyObject *handle, th_tree *tree, xp_item item) {
    if (item.attr == -1 && item.node->type == TH_NODE_ELEMENT) {
        return element_wrap(state, handle, item.node);
    }
    if (item.attr == -2) {
        return PyUnicode_FromString("http://www.w3.org/XML/1998/namespace");
    }
    if (xp_is_attribute(item.attr)) {
        const th_node_attr *attr = xp_item_attribute(tree, item);
        return attr->value == NULL ? PyUnicode_New(0, 0) : ucs4_to_str(attr->value, attr->value_len);
    }
    if (item.node->type == TH_NODE_TEXT) {
        return str_from_accessor(th_node_data, tree, item.node);
    }
    return node_wrap(state, handle, item.node);
}

/* Translate an xp_eval status into a Python error: -4 a TypeError (a value where a
   node-set is required), -3 a ValueError (an unbound variable, an unbound prefix, or an
   over-deep expression), -1 an allocation failure or an already-set exception (an unknown
   function, a wrong-arity call, a malformed regex, or an extension failure). */
static void *xpath_raise_status(int status, const char *feature) {
    if (PyErr_Occurred()) { /* a borrowed Python call already set the exception (regex, unknown function, ...) */
        return NULL;
    }
    if (status == -4) {
        PyErr_Format(PyExc_TypeError, "xpath: %s", feature);
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
static int xpath_pyobject_to_scalar(PyObject *obj, xp_result *out);

typedef struct {
    PyObject *value;
    PyObject *parent;
    PyObject *attrname;
} xpath_snapshot_item;

typedef struct {
    xpath_snapshot_item *items;
    Py_ssize_t len;
} xpath_snapshot;

static void xpath_snapshot_free(xpath_snapshot *snapshot) {
    for (Py_ssize_t index = 0; index < snapshot->len; index++) {
        Py_XDECREF(snapshot->items[index].value);
        Py_XDECREF(snapshot->items[index].parent);
        Py_XDECREF(snapshot->items[index].attrname);
    }
    PyMem_Free(snapshot->items);
}

static int xpath_snapshot_prepare(module_state *state, PyObject *handle, th_tree *tree, int smart_strings,
                                  const xp_nodeset *nodes, xpath_snapshot *snapshot, xpath_ext_ctx *ec) {
    if (nodes->len == 0) {
        return 0;
    }
    snapshot->items = PyMem_Calloc((size_t)nodes->len, sizeof(xpath_snapshot_item));
    if (snapshot->items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();          /* GCOVR_EXCL_LINE */
        return -1;                 /* GCOVR_EXCL_LINE */
    }
    snapshot->len = nodes->len;
    /* GC and smart-string constructors can adopt nodes before later results are wrapped. */
    for (Py_ssize_t index = 0; index < nodes->len; index++) {
        xp_item source = nodes->items[index];
        snapshot->items[index].value = ec != NULL && source.attr == -1 && source.node->type != TH_NODE_TEXT
                                           ? xpath_owned_node(ec, source.node)
                                           : xpath_item_to_py(state, handle, tree, source);
        if (snapshot->items[index].value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                              /* GCOVR_EXCL_LINE */
        }
        if (!smart_strings) {
            continue;
        }
        if (xp_is_attribute(source.attr) || source.node->type == TH_NODE_TEXT) {
            PyObject *parent = ec == NULL ? NULL : xpath_text_parent(ec, source.node);
            th_node *owner = xp_is_attribute(source.attr) ? source.node : source.node->parent;
            snapshot->items[index].parent = parent != NULL ? Py_NewRef(parent)
                                            : ec != NULL   ? xpath_owned_node(ec, owner)
                                                           : node_wrap(state, handle, owner);
            if (snapshot->items[index].parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                               /* GCOVR_EXCL_LINE */
            }
            if (xp_is_attribute(source.attr)) {
                Py_ssize_t length;
                const char *name = xp_item_attr_name(tree, source, &length);
                snapshot->items[index].attrname = PyUnicode_FromStringAndSize(name, length);
                if (snapshot->items[index].attrname == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                 /* GCOVR_EXCL_LINE */
                }
            }
        }
    }
    return 0;
}

static PyObject *xpath_snapshot_materialize(module_state *state, const xpath_snapshot *snapshot) {
    PyObject *out = PyList_New(snapshot->len);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = 0; index < snapshot->len; index++) {
        xpath_snapshot_item item = snapshot->items[index];
        PyObject *value;
        if (item.parent == NULL) {
            value = Py_NewRef(item.value);
        } else {
            value = PyObject_CallFunction(state->xpath_string_type, "OOOO", item.value, item.parent,
                                          item.attrname == NULL ? Py_False : Py_True,
                                          item.attrname == NULL ? Py_None : item.attrname);
            if (value == NULL) { /* GCOVR_EXCL_BR_LINE: smart-string allocation failure */
                Py_CLEAR(out);   /* GCOVR_EXCL_LINE */
                return NULL;     /* GCOVR_EXCL_LINE */
            }
        }
        PyList_SET_ITEM(out, index, value);
    }
    return out;
}

static TH_NOINLINE PyObject *xpath_snapshot_to_py(module_state *state, PyObject *handle, th_tree *tree,
                                                  int smart_strings, const xp_nodeset *nodes, xpath_ext_ctx *ec) {
    xpath_snapshot snapshot = {0};
    int prepared = xpath_snapshot_prepare(state, handle, tree, smart_strings, nodes, &snapshot, ec);
    if (prepared < 0) {                 /* GCOVR_EXCL_BR_LINE: allocation failure */
        xpath_snapshot_free(&snapshot); /* GCOVR_EXCL_LINE */
        return NULL;                    /* GCOVR_EXCL_LINE */
    }
    PyObject *out = xpath_snapshot_materialize(state, &snapshot);
    xpath_snapshot_free(&snapshot);
    return out;
}

static int xpath_extension_retain(xpath_ext_ctx *ec, PyObject *object) {
    if (ec->retained_len == ec->retained_cap) {
        size_t capacity;
        size_t bytes;
        int grew = th_grow_cap(ec->retained_len + 1, ec->retained_cap, 8, sizeof(PyObject *), &capacity, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation size overflow */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        PyObject **retained = PyMem_Realloc(ec->retained, bytes);
        if (retained == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE */
            return -1;          /* GCOVR_EXCL_LINE */
        }
        ec->retained = retained;
        ec->retained_cap = capacity;
    }
    ec->retained[ec->retained_len++] = Py_NewRef(object);
    return 0;
}

static PyObject *xpath_retained_node(xpath_ext_ctx *ec, th_node *node, int parent) {
    Py_ssize_t index = th_node_map_find(parent ? &ec->text_parents : &ec->owners, node);
    if (index != 0) {
        return ec->retained[index - 1];
    }
    for (size_t frame = 0; frame < ec->frame_count; frame++) {
        xpath_ext_ctx *pins = &ec->frames[frame]->pins;
        index = th_node_map_find(parent ? &pins->text_parents : &pins->owners, node);
        if (index != 0) {
            return pins->retained[index - 1];
        }
    }
    return NULL;
}

static PyObject *xpath_text_parent(xpath_ext_ctx *ec, th_node *node) {
    return xpath_retained_node(ec, node, 1);
}

static PyObject *xpath_owned_node(xpath_ext_ctx *ec, th_node *node) {
    PyObject *owner = xpath_retained_node(ec, node, 0);
    return owner != NULL ? Py_NewRef(owner) : node_wrap(ec->state, ec->handle, node);
}

static int xpath_capture_owner(void *context, th_node *node) {
    xpath_ext_ctx *ec = context;
    if (th_node_map_find(&ec->owners, node) != 0) {
        return 0;
    }
    xpath_ext_ctx *previous = ec->previous != NULL ? ec->previous : ec;
    PyObject *wrapped = xpath_owned_node(previous, node);
    if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    int status = xpath_extension_retain(ec, wrapped);
    Py_DECREF(wrapped);
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;    /* GCOVR_EXCL_LINE */
    }
    if (th_node_map_insert(&ec->owners, node, (Py_ssize_t)ec->retained_len) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyErr_NoMemory();                                                          /* GCOVR_EXCL_LINE */
        return -1;                                                                 /* GCOVR_EXCL_LINE */
    }
    if (node->type == TH_NODE_TEXT) {
        PyObject *prior = xpath_text_parent(previous, node);
        PyObject *parent = prior != NULL ? Py_NewRef(prior) : xpath_owned_node(previous, node->parent);
        if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        status = xpath_extension_retain(ec, parent);
        Py_DECREF(parent);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;    /* GCOVR_EXCL_LINE */
        }
        int inserted = th_node_map_insert(&ec->text_parents, node, (Py_ssize_t)ec->retained_len);
        if (inserted < 0) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

static void xpath_extension_clear(xpath_ext_ctx *ec) {
    for (size_t index = 0; index < ec->retained_len; index++) {
        Py_DECREF(ec->retained[index]);
    }
    PyMem_Free(ec->retained);
    PyMem_Free(ec->owners.entries);
    PyMem_Free(ec->text_parents.entries);
    for (size_t index = 0; index < ec->frame_count; index++) {
        xpath_frame_pins *frame = ec->frames[index];
        if (--frame->references == 0) {
            xpath_extension_clear(&frame->pins);
            PyMem_Free(frame);
        }
    }
    PyMem_Free(ec->frames);
}

static int xpath_before_python(void *context, const xp_live_frame *frame) {
    xpath_ext_ctx *ec = context;
    xpath_ext_ctx current = {
        .extensions = ec->extensions, .handle = ec->handle, .state = ec->state, .tree = ec->tree, .previous = ec};
    size_t count = 0;
    for (const xp_live_frame *active = frame; active != NULL; active = xp_live_previous(active)) {
        count++;
    }
    current.frames = PyMem_Malloc(count * sizeof(xpath_frame_pins *));
    if (current.frames == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();         /* GCOVR_EXCL_LINE */
        return -1;                /* GCOVR_EXCL_LINE */
    }
    for (const xp_live_frame *active = frame; active != NULL; active = xp_live_previous(active)) {
        xpath_frame_pins *pins = NULL;
        for (size_t index = 0; index < ec->frame_count; index++) {
            xpath_frame_pins *prior = ec->frames[index];
            if (prior->activation == xp_live_id(active) && prior->version == xp_live_version(active)) {
                pins = prior;
                pins->references++;
                break;
            }
        }
        if (pins == NULL) {
            pins = PyMem_Calloc(1, sizeof(xpath_frame_pins));
            if (pins == NULL) {                  /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyErr_NoMemory();                /* GCOVR_EXCL_LINE */
                xpath_extension_clear(&current); /* GCOVR_EXCL_LINE */
                return -1;                       /* GCOVR_EXCL_LINE */
            }
            pins->activation = xp_live_id(active);
            pins->version = xp_live_version(active);
            pins->references = 1;
            pins->pins = (xpath_ext_ctx){.handle = ec->handle, .state = ec->state, .tree = ec->tree, .previous = ec};
            current.frames[current.frame_count++] = pins;
            int visited = xp_visit_frame_nodes(active, xpath_capture_owner, &pins->pins);
            if (visited < 0) {                   /* GCOVR_EXCL_BR_LINE: allocation failure */
                xpath_extension_clear(&current); /* GCOVR_EXCL_LINE */
                return -1;                       /* GCOVR_EXCL_LINE */
            }
            pins->pins.previous = NULL;
            pins->appended = xp_live_append_count(active);
        } else {
            current.frames[current.frame_count++] = pins;
            if (pins->appended < xp_live_append_count(active)) {
                pins->pins.previous = ec;
                int visited = xp_visit_frame_append(active, pins->appended, xpath_capture_owner, &pins->pins);
                pins->pins.previous = NULL;
                if (visited < 0) {                   /* GCOVR_EXCL_BR_LINE: allocation failure */
                    xpath_extension_clear(&current); /* GCOVR_EXCL_LINE */
                    return -1;                       /* GCOVR_EXCL_LINE */
                }
                pins->appended = xp_live_append_count(active);
            }
        }
    }
    xpath_ext_ctx previous = *ec;
    current.previous = NULL;
    *ec = current;
    xpath_extension_clear(&previous);
    return 0;
}

/* Grow `set` by one node (attr -1, the node itself), doubling capacity as needed. */
static int xpath_nodeset_push(xp_nodeset *set, th_node *node) {
    if (set->len == set->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)set->cap + 1, (size_t)set->cap, 8, sizeof(xp_item), &cap, &bytes);
        if (!grew) {          /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return -1;        /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        xp_item *grown = PyMem_Realloc(set->items, bytes);
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        set->items = grown;
        set->cap = (Py_ssize_t)cap;
    }
    set->items[set->len].node = node;
    set->items[set->len].attr = -1;
    set->len++;
    return 0;
}

/* Returned nodes must belong to the evaluation arena. */
static int xpath_check_result_owner(xpath_ext_ctx *ec, PyObject *obj) {
    if (!node_owned_by((NodeObject *)obj, ec->handle)) {
        PyErr_SetString(PyExc_ValueError, "xpath extension returned an element from a different document");
        return -1;
    }
    return 0;
}

static int xpath_append_result_node(xpath_ext_ctx *ec, PyObject *obj, xp_nodeset *set) {
    if (xpath_check_result_owner(ec, obj) < 0) {
        return -1;
    }
    th_node *node = ((NodeObject *)obj)->node;
    if (xpath_capture_owner(ec, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                           /* GCOVR_EXCL_LINE */
    }
    return xpath_nodeset_push(set, node);
}

/* Marshal an extension's return value into an xp_result: a Python scalar keeps the
   str/int/float/bool conversion; a single element or an iterable of elements becomes
   a node-set (issue #265). */
static int xpath_extension_result(xpath_ext_ctx *ec, PyObject *obj, xp_result *out) {
    if (PyBool_Check(obj) || PyLong_Check(obj) || PyFloat_Check(obj) || PyUnicode_Check(obj)) {
        return xpath_pyobject_to_scalar(obj, out);
    }
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    if (is_node(obj, ec->state)) {
        return xpath_append_result_node(ec, obj, &out->nodes);
    }
    PyObject *iterator = PyObject_GetIter(obj);
    if (iterator == NULL) {
        PyErr_Format(PyExc_TypeError,
                     "xpath extension result must be str, int, float, bool, an element, or an iterable of elements, "
                     "not %.80s",
                     Py_TYPE(obj)->tp_name);
        return -1;
    }
    xpath_ext_ctx yielded = {0};
    int status = -1;
    PyObject *item;
    while ((item = PyIter_Next(iterator)) != NULL) {
        if (!is_node(item, ec->state)) {
            PyErr_Format(
                PyExc_TypeError,
                "xpath extension result must be str, int, float, bool, an element, or an iterable of elements, "
                "not %.80s",
                Py_TYPE(item)->tp_name);
            Py_DECREF(item);
            goto done;
        }
        if (xpath_check_result_owner(ec, item) < 0) {
            Py_DECREF(item);
            goto done;
        }
        int rc = xpath_extension_retain(&yielded, item);
        Py_DECREF(item);
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto done; /* GCOVR_EXCL_LINE */
        }
    }
    if (PyErr_Occurred()) {
        goto done;
    }
    for (size_t index = 0; index < yielded.retained_len; index++) {
        if (xpath_append_result_node(ec, yielded.retained[index], &out->nodes) < 0) {
            xp_result_free(out);
            goto done;
        }
    }
    status = 0;
done:
    Py_DECREF(iterator);
    xpath_extension_clear(&yielded);
    return status;
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
    typedef struct {
        xpath_snapshot nodes;
        PyObject *scalar;
    } argument;
    argument *values = argc == 0 ? NULL : PyMem_Calloc((size_t)argc, sizeof(argument));
    if (argc != 0 && values == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();              /* GCOVR_EXCL_LINE */
        return -1;                     /* GCOVR_EXCL_LINE */
    }
    int status = -1;
    PyObject *element = xpath_owned_node(ec, context_node);
    PyObject *func = NULL;
    PyObject *call_args = NULL;
    if (element == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto done;         /* GCOVR_EXCL_LINE */
    }
    for (int index = 0; index < argc; index++) {
        if (args[index].kind == XP_NODESET) {
            int prepared = xpath_snapshot_prepare(ec->state, ec->handle, ec->tree, 0, &args[index].nodes,
                                                  &values[index].nodes, ec);
            if (prepared < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                goto done;      /* GCOVR_EXCL_LINE */
            }
        } else {
            values[index].scalar = xpath_scalar_to_py(&args[index]);
            if (values[index].scalar == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                goto done;                      /* GCOVR_EXCL_LINE */
            }
        }
    }
    PyObject *name_obj = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, name, name_len);
    if (name_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto done;          /* GCOVR_EXCL_LINE */
    }
    PyObject *key = Py_BuildValue("(OO)", Py_None, name_obj);
    Py_DECREF(name_obj);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto done;     /* GCOVR_EXCL_LINE */
    }
    Py_BEGIN_CRITICAL_SECTION(ec->extensions);
    func = Py_XNewRef(PyDict_GetItem(ec->extensions, key));
    Py_END_CRITICAL_SECTION();
    Py_DECREF(key);
    if (func == NULL) {
        status = -2;
        goto done;
    }
    PyObject *context = xpath_extension_context(element);
    if (context == NULL) { /* GCOVR_EXCL_BR_LINE: import or allocation failure */
        goto done;         /* GCOVR_EXCL_LINE */
    }
    call_args = PyTuple_New((Py_ssize_t)argc + 1);
    if (call_args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(context);  /* GCOVR_EXCL_LINE */
        goto done;           /* GCOVR_EXCL_LINE */
    }
    PyTuple_SET_ITEM(call_args, 0, context);
    for (int index = 0; index < argc; index++) {
        PyObject *arg = values[index].scalar != NULL ? Py_NewRef(values[index].scalar)
                                                     : xpath_snapshot_materialize(ec->state, &values[index].nodes);
        if (arg == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            goto done;     /* GCOVR_EXCL_LINE */
        }
        PyTuple_SET_ITEM(call_args, index + 1, arg);
    }
    PyObject *result = PyObject_CallObject(func, call_args);
    if (result == NULL) {
        goto done;
    }
    status = xpath_extension_result(ec, result, out);
    Py_DECREF(result);
done:
    Py_XDECREF(func);
    Py_XDECREF(call_args);
    Py_XDECREF(element);
    for (int index = 0; index < argc; index++) {
        xpath_snapshot_free(&values[index].nodes);
        Py_XDECREF(values[index].scalar);
    }
    PyMem_Free(values);
    return status;
}

/* Marshal an evaluated xp_result to a Python object under the held handle critical
   section: a node-set to a list (elements as nodes, attribute/text values as str),
   a scalar to its float / str / bool. Always frees *result. Returns the object (a
   new reference), or NULL with a Python error set on an allocation failure. */
static PyObject *xpath_result_to_py(module_state *state, PyObject *handle, th_tree *tree, int smart_strings,
                                    xp_result *result, xpath_ext_ctx *ec) {
    PyObject *out;
    if (result->kind == XP_NODESET) {
        if (ec != NULL) {
            out = xpath_snapshot_to_py(state, handle, tree, smart_strings, &result->nodes, ec);
        } else {
#if PY_VERSION_HEX < 0x030C0000 && !defined(PYPY_VERSION)
            out = xpath_snapshot_to_py(state, handle, tree, smart_strings, &result->nodes, NULL);
#else
            if (smart_strings) {
                out = xpath_snapshot_to_py(state, handle, tree, smart_strings, &result->nodes, NULL);
            } else {
                const Py_ssize_t count = result->nodes.len;
                const xp_item *items = result->nodes.items;
                out = PyList_New(count);
                if (out == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure */
                    xp_result_free(result); /* GCOVR_EXCL_LINE */
                    return NULL;            /* GCOVR_EXCL_LINE */
                }
#ifndef PYPY_VERSION
                PyObject **values = ((PyListObject *)out)->ob_item;
#endif
                for (Py_ssize_t index = 0; index < count; index++) {
                    xp_item source = items[index];
                    PyObject *item = source.attr == -1 && source.node->type == TH_NODE_ELEMENT
                                         ? element_wrap(state, handle, source.node)
                                         : xpath_item_to_py(state, handle, tree, source);
                    if (item == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure */
                        Py_DECREF(out);         /* GCOVR_EXCL_LINE */
                        xp_result_free(result); /* GCOVR_EXCL_LINE */
                        return NULL;            /* GCOVR_EXCL_LINE */
                    }
#ifdef PYPY_VERSION
                    PyList_SET_ITEM(out, index, item);
#else
                    values[index] = item;
#endif
                }
            }
#endif
        }
    } else {
        out = xpath_scalar_to_py(result); /* NULL with the error set on an unforced allocation failure */
    }
    xp_result_free(result);
    return out;
}

static PyObject *xpath_eval_object(PyObject *self, PyObject *arg, const xp_bindings *vars,
                                   const xp_namespaces *namespaces, int smart_strings, PyObject *extensions) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "xpath expression must be a str");
        return NULL;
    }
    module_state *state = state_of(self);
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    Py_XINCREF(extensions);
    th_node *origin = ((NodeObject *)self)->node;
    const char *feature = NULL;
    PyObject *out = NULL;
    int status = 0;
    int compiled = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: the compile cache and the eval read the tree */
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    xpath_ext_ctx ext_ctx = {.extensions = extensions, .handle = handle, .state = state, .tree = tree};
    xp_program *prog = cached_xpath_compile(handle_obj, arg);
    if (prog != NULL) {
        compiled = 1;
        int callbacks = extensions != NULL || xp_calls_python(prog);
        if (callbacks) {
            xp_retain(prog);
        }
        xp_result result;
        status = callbacks ? xp_eval_snapshot(prog, tree, origin, vars, namespaces,
                                              extensions != NULL ? xpath_call_extension : NULL, &ext_ctx,
                                              xpath_before_python, &result, &feature)
                           : xp_eval(prog, tree, origin, vars, namespaces, NULL, NULL, &result, &feature);
        if (status == 0) {
            out = xpath_result_to_py(state, handle, tree, smart_strings, &result, callbacks ? &ext_ctx : NULL);
        }
        if (callbacks) {
            xp_free(prog);
        }
    }
    xpath_extension_clear(&ext_ctx);
    Py_END_CRITICAL_SECTION();
    Py_XDECREF(extensions);
    Py_DECREF(handle);
    if (!compiled) {
        return NULL; /* a compile error left a Python exception set */
    }
    if (status < 0) {
        return xpath_raise_status(status, feature);
    }
    return out; /* the result, or NULL with an error set if marshaling hit an allocation failure */
}

/* Evaluate an already-compiled program against a context node, taking the node's
   handle critical section. prog is borrowed and re-entrant; the caller owns vars.
   Returns the result object, or NULL with a Python error set. Shared by the
   precompiled XPath object's call. */
/* GCOVR_EXCL_BR_START: handle objects are mortal */
static PyObject *xpath_run_program(module_state *state, PyObject *handle, th_node *origin, const xp_program *prog,
                                   const xp_bindings *vars, int smart_strings, PyObject *extensions) {
    Py_INCREF(handle);
    /* GCOVR_EXCL_BR_STOP */
    Py_XINCREF(extensions);
    const char *feature = NULL;
    PyObject *out = NULL;
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: the eval reads the tree */
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    xpath_ext_ctx ext_ctx = {.extensions = extensions, .handle = handle, .state = state, .tree = tree};
    int callbacks = extensions != NULL || xp_calls_python(prog);
    xp_result result;
    status = callbacks
                 ? xp_eval_snapshot(prog, tree, origin, vars, NULL, extensions != NULL ? xpath_call_extension : NULL,
                                    &ext_ctx, xpath_before_python, &result, &feature)
                 : xp_eval(prog, tree, origin, vars, NULL, NULL, NULL, &result, &feature);
    if (status == 0) {
        out = xpath_result_to_py(state, handle, tree, smart_strings, &result, callbacks ? &ext_ctx : NULL);
    }
    xpath_extension_clear(&ext_ctx);
    Py_END_CRITICAL_SECTION();
    Py_XDECREF(extensions);
    Py_DECREF(handle);
    if (status < 0) {
        return xpath_raise_status(status, feature);
    }
    return out;
}

/* Convert a Python scalar to an xp_result. Both callers (variable and extension-result
   marshaling) check the type is bool/int/float/str before calling, so the final branch
   is str rather than a guarded case with an unreachable type error. */
static int xpath_pyobject_to_scalar(PyObject *obj, xp_result *out) {
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
    } else {
        Py_ssize_t length = PyUnicode_GET_LENGTH(obj);
        Py_UCS4 *buf = PyUnicode_AsUCS4Copy(obj);
        if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            return -1;     /* GCOVR_EXCL_LINE */
        }
        out->kind = XP_STRING;
        out->string = buf;
        out->string_len = length;
    }
    return 0;
}

/* Append one Element's node to a node-set variable value. Its node pointer is only
   valid inside the tree being queried, so a node wrapped against a different handle
   (another document) is rejected rather than dereferenced into a foreign arena. */
static int xpath_push_element(PyObject *obj, module_state *state, PyObject *reference_handle, xp_nodeset *ns) {
    if (!is_node(obj, state)) {
        PyErr_Format(PyExc_TypeError,
                     "xpath variable must be str, int, float, bool, an element, or an iterable of elements, not %.80s",
                     Py_TYPE(obj)->tp_name);
        return -1;
    }
    NodeObject *element = (NodeObject *)obj;
    if (!node_owned_by(element, reference_handle)) {
        PyErr_SetString(PyExc_ValueError, "xpath variable refers to a node from a different tree");
        return -1;
    }
    if (ns_push(ns, element->node, -1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory();                     /* GCOVR_EXCL_LINE */
        return -1;                            /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* Convert one keyword-argument value into a variable binding. A scalar maps to its
   XPath type; an Element or an iterable of Elements maps to a node-set the engine
   then orders and de-duplicates on reference. */
static int xpath_var_value(PyObject *obj, xp_result *out, module_state *state, PyObject *reference_handle) {
    if (PyBool_Check(obj) || PyLong_Check(obj) || PyFloat_Check(obj) || PyUnicode_Check(obj)) {
        return xpath_pyobject_to_scalar(obj, out);
    }
    memset(out, 0, sizeof(*out));
    out->kind = XP_NODESET;
    if (is_node(obj, state)) {
        return xpath_push_element(obj, state, reference_handle, &out->nodes);
    }
    PyObject *iterator = PyObject_GetIter(obj);
    if (iterator == NULL) {
        PyErr_Clear();
        PyErr_Format(PyExc_TypeError,
                     "xpath variable must be str, int, float, bool, an element, or an iterable of elements, not %.80s",
                     Py_TYPE(obj)->tp_name);
        return -1;
    }
    PyObject *item;
    while ((item = PyIter_Next(iterator)) != NULL) {
        int rc = xpath_push_element(item, state, reference_handle, &out->nodes);
        Py_DECREF(item);
        if (rc < 0) {
            Py_DECREF(iterator);
            xp_nodeset_free(&out->nodes);
            return -1;
        }
    }
    Py_DECREF(iterator);
    if (PyErr_Occurred()) { /* the iterable raised partway through */
        xp_nodeset_free(&out->nodes);
        return -1;
    }
    return 0;
}

static void free_xpath_vars(xp_binding *items, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        PyMem_Free((void *)items[index].name);
        xp_result_free(&items[index].value);
    }
    PyMem_Free(items);
}

static void free_xpath_namespaces(xp_namespace *items, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        PyMem_Free((void *)items[index].prefix);
        PyMem_Free((void *)items[index].uri);
    }
    PyMem_Free(items);
}

/* Build the prefix-to-URI bindings from the `namespaces` keyword: a dict mapping str
   prefixes to str URIs. On error an exception is set and any partial bindings are freed. */
static int build_xpath_namespaces(PyObject *mapping, xp_namespace **out_items, Py_ssize_t *out_len) {
    *out_items = NULL;
    *out_len = 0;
    if (mapping == NULL || mapping == Py_None) {
        return 0;
    }
    if (!PyDict_Check(mapping)) {
        PyErr_SetString(PyExc_TypeError, "xpath namespaces must be a dict of prefix -> URI");
        return -1;
    }
    Py_ssize_t total = PyDict_GET_SIZE(mapping);
    if (total == 0) {
        return 0;
    }
    xp_namespace *items = PyMem_Calloc((size_t)total, sizeof(xp_namespace));
    if (items == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    Py_ssize_t pos = 0;
    PyObject *key;
    PyObject *value;
    while (PyDict_Next(mapping, &pos, &key, &value)) {
        if (!PyUnicode_Check(key) || !PyUnicode_Check(value)) {
            free_xpath_namespaces(items, count);
            PyErr_SetString(PyExc_TypeError, "xpath namespaces must map str prefixes to str URIs");
            return -1;
        }
        Py_UCS4 *prefix = PyUnicode_AsUCS4Copy(key);
        if (prefix == NULL) {                    /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            free_xpath_namespaces(items, count); /* GCOVR_EXCL_LINE */
            return -1;                           /* GCOVR_EXCL_LINE */
        }
        Py_UCS4 *uri = PyUnicode_AsUCS4Copy(value);
        if (uri == NULL) {                       /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            PyMem_Free(prefix);                  /* GCOVR_EXCL_LINE */
            free_xpath_namespaces(items, count); /* GCOVR_EXCL_LINE */
            return -1;                           /* GCOVR_EXCL_LINE */
        }
        items[count].prefix = prefix;
        items[count].prefix_len = PyUnicode_GET_LENGTH(key);
        items[count].uri = uri;
        items[count].uri_len = PyUnicode_GET_LENGTH(value);
        count++;
    }
    *out_items = items;
    *out_len = count;
    return 0;
}

/* Build the $name bindings from the call's keyword arguments (kwargs keys are
   always str). With reserve_options set, the smart_strings and extensions keys are
   skipped because Node.xpath reads them as options off the same kwargs; the
   precompiled XPath object binds them at construction and clears the flag, so every
   keyword is a $variable. self is the context Node: it supplies the module state and
   the reference handle a bound Element is validated against. On error an exception is
   set and partial bindings freed. */
static int build_xpath_vars(PyObject *self, PyObject *kwds, int reserve_options, xp_binding **out_items,
                            Py_ssize_t *out_len) {
    *out_items = NULL;
    *out_len = 0;
    module_state *state = state_of(self);
    PyObject *reference_handle = ((NodeObject *)self)->handle;
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
        if (reserve_options) {
            /* split rather than a chained || so clang's branch gate covers each edge */
            if (PyUnicode_CompareWithASCIIString(key, "smart_strings") == 0) {
                continue; /* a reserved option the caller reads, not a $variable */
            }
            if (PyUnicode_CompareWithASCIIString(key, "extensions") == 0) {
                continue;
            }
            if (PyUnicode_CompareWithASCIIString(key, "namespaces") == 0) {
                continue;
            }
        }
        Py_UCS4 *name = PyUnicode_AsUCS4Copy(key);
        if (name == NULL) {                /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            free_xpath_vars(items, count); /* GCOVR_EXCL_LINE */
            return -1;                     /* GCOVR_EXCL_LINE */
        }
        if (xpath_var_value(value, &items[count].value, state, reference_handle) < 0) {
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
    xp_namespace *ns_items;
    Py_ssize_t ns_len;
    PyObject *ns_arg = kwds == NULL ? NULL : PyDict_GetItemString(kwds, "namespaces"); /* borrowed */
    if (build_xpath_namespaces(ns_arg, &ns_items, &ns_len) < 0) {
        return NULL;
    }
    xp_binding *items;
    Py_ssize_t len;
    if (build_xpath_vars(self, kwds, 1, &items, &len) < 0) {
        free_xpath_namespaces(ns_items, ns_len);
        return NULL;
    }
    xp_bindings vars = {items, len};
    xp_namespaces namespaces = {ns_items, ns_len};
    PyObject *result = xpath_eval_object(self, expr_obj, len == 0 ? NULL : &vars, ns_len == 0 ? NULL : &namespaces,
                                         smart_strings, extensions);
    free_xpath_vars(items, len);
    free_xpath_namespaces(ns_items, ns_len);
    return result;
}

TH_NODE_API(, PyObject *, node_xpath, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
    return xpath_eval_with_vars(self, args, kwds, "xpath");
}

TH_NODE_API(, PyObject *, node_xpath_iter, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
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

TH_NODE_API(, PyObject *, node_xpath_one, (PyObject * self, PyObject *args, PyObject *kwds), (self, args, kwds),
            (PyObject * self, PyObject *args, PyObject *kwds), (NodeObject *)self,
            args != NULL && is_node(args, state_of(self)) ? (NodeObject *)args : NULL) {
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

/* XPath(expression, *, smart_strings=False, extensions=None), the precompiled
   reusable expression object (issue #267): parse the expression
   once into an immutable program and bind the two evaluation options. The program
   is tree-independent and re-entrant, so the object is shareable across threads. */
static PyObject *xpath_compiled_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"expression", "smart_strings", "extensions", NULL};
    PyObject *expr_obj;
    int smart_strings = 0;
    PyObject *extensions = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U|$pO:XPath", keywords, &expr_obj, &smart_strings, &extensions)) {
        return NULL;
    }
    PyObject *bound_extensions = NULL;
    if (extensions != NULL && extensions != Py_None) {
        if (!PyDict_Check(extensions)) {
            PyErr_SetString(PyExc_TypeError, "xpath extensions must be a dict of (namespace, name) -> callable");
            return NULL;
        }
        if (PyDict_GET_SIZE(extensions) > 0) {
            bound_extensions = extensions; /* an empty mapping binds nothing, like Node.xpath */
        }
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(expr_obj);
    Py_UCS4 *src = PyUnicode_AsUCS4Copy(expr_obj);
    if (src == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    char err[128];
    xp_program *prog = xp_compile(src, len, err, sizeof(err));
    PyMem_Free(src);
    if (prog == NULL) {
        PyErr_SetString(PyExc_ValueError, err);
        return NULL;
    }
    XPathObject *self = (XPathObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        xp_free(prog);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->prog = prog;
    self->expression = Py_NewRef(expr_obj);
    self->smart_strings = smart_strings;
    self->extensions = bound_extensions == NULL ? NULL : Py_NewRef(bound_extensions);
    return (PyObject *)self;
}

static int xpath_compiled_traverse(PyObject *self, visitproc visit, void *arg) {
    XPathObject *compiled = (XPathObject *)self;
    Py_VISIT(Py_TYPE(self));        /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(compiled->expression); /* GCOVR_EXCL_BR_LINE: set at creation, dropped only in clear/dealloc */
    Py_VISIT(compiled->extensions); /* GCOVR_EXCL_BR_LINE: the failing-visit arm needs a gc callback that errors */
    return 0;
}

static int xpath_compiled_clear(PyObject *self) {
    XPathObject *compiled = (XPathObject *)self;
    Py_CLEAR(compiled->expression); /* GCOVR_EXCL_BR_LINE: expression is non-NULL until the single clear */
    Py_CLEAR(compiled->extensions); /* the NULL arm runs for an XPath bound without extensions */
    return 0;
}

static void xpath_compiled_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    xp_free(((XPathObject *)self)->prog);
    (void)xpath_compiled_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

/* XPath.__call__(node, /, **variables): evaluate the compiled program against the
   context Node, binding each keyword as a $name variable. Returns the same result a
   matching Node.xpath call would. */
TH_NODE_API(static, PyObject *, xpath_compiled_call, (PyObject * self, PyObject *args, PyObject *kwds),
            (self, args, kwds), (PyObject * self, PyObject *args, PyObject *kwds),
            node_argument(state_of(self), args, NULL, 0, NULL), NULL) {
    XPathObject *compiled = (XPathObject *)self;
    module_state *state = state_of(self);
    if (PyTuple_GET_SIZE(args) != 1) {
        PyErr_SetString(PyExc_TypeError, "XPath takes exactly one context node");
        return NULL;
    }
    PyObject *node_obj = PyTuple_GET_ITEM(args, 0);
    if (!PyObject_TypeCheck(node_obj, (PyTypeObject *)state->node_type)) {
        PyErr_SetString(PyExc_TypeError, "XPath context must be a turbohtml Node");
        return NULL;
    }
    xp_binding *items;
    Py_ssize_t len;
    if (build_xpath_vars(node_obj, kwds, 0, &items, &len) < 0) {
        return NULL;
    }
    xp_bindings vars = {items, len};
    PyObject *handle = ((NodeObject *)node_obj)->handle;
    th_node *origin = ((NodeObject *)node_obj)->node;
    PyObject *result = xpath_run_program(state, handle, origin, compiled->prog, len == 0 ? NULL : &vars,
                                         compiled->smart_strings, compiled->extensions);
    free_xpath_vars(items, len);
    return result;
}

static PyObject *xpath_compiled_get_path(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(((XPathObject *)self)->expression);
}

static PyGetSetDef xpath_compiled_getset[] = {
    {"path", xpath_compiled_get_path, NULL, "the source expression string the object was compiled from", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyObject *xpath_compiled_repr(PyObject *self) {
    return th_str_format("XPath(%R)", ((XPathObject *)self)->expression);
}

PyDoc_STRVAR(xpath_compiled_doc, "XPath(expression, *, smart_strings=False, extensions=None)\n--\n\n"
                                 "A precompiled XPath 1.0 expression, parsed once and evaluated against many\n"
                                 "context nodes. Call it with a context Node and optional $name keyword\n"
                                 "variables: XPath(\"//td[@class=$cls]\")(row, cls=\"num\"). It returns the same\n"
                                 "results as Node.xpath. smart_strings and the extensions dict are bound here\n"
                                 "at construction. The object is immutable and re-entrant, so one instance can\n"
                                 "be shared across threads.\n\n"
                                 ":raises TypeError: expression is not a str, a variable binding is of an\n"
                                 "    unsupported type, or a value is used where a node-set is required.\n"
                                 ":raises ValueError: the expression is not valid XPath (with the offending\n"
                                 "    token and its offset), nests past the depth limit, calls an unknown or\n"
                                 "    wrong-arity function, or references an unbound variable or prefix.");

static PyType_Slot xpath_compiled_slots[] = {
    {Py_tp_doc, (void *)xpath_compiled_doc},
    {Py_tp_new, xpath_compiled_new},
    {Py_tp_dealloc, xpath_compiled_dealloc},
    {Py_tp_traverse, xpath_compiled_traverse},
    {Py_tp_clear, xpath_compiled_clear},
    {Py_tp_call, xpath_compiled_call},
    {Py_tp_repr, xpath_compiled_repr},
    {Py_tp_getset, xpath_compiled_getset},
    {0, NULL},
};

PyType_Spec xpath_compiled_spec = {
    .name = "turbohtml._html.XPath",
    .basicsize = sizeof(XPathObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = xpath_compiled_slots,
};

TH_NODE_API(, PyObject *, node_css_matches, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), (NodeObject *)self,
            arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    int matched = 0;
    int error = 0;
    int moved = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* selector_matches walks ancestors/siblings */
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, (HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *node = ((NodeObject *)self)->node;
        /* matches() scopes :scope to the node being tested */
        matched = node->type == TH_NODE_ELEMENT && selector_matches(node, compiled, node);
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    if (error) {
        return NULL;
    }
    return PyBool_FromLong(matched);
}

TH_NODE_API(, PyObject *, node_css_closest, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), (NodeObject *)self,
            arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    th_node *found;
    if (node_css_closest_borrowed(self, arg, &found) < 0) {
        return NULL;
    }
    return node_wrap(state_of(self), ((NodeObject *)self)->handle, found);
}

int node_css_closest_borrowed(PyObject *self, PyObject *arg, th_node **found) {
    if (check_selector_arg(arg) < 0) {
        return -1;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    *found = NULL;
    int error = 0;
    int moved = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock around the ancestor walk */
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, (HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        /* closest() scopes :scope to the element it was invoked on */
        th_node *scope = ((NodeObject *)self)->node;
        for (th_node *node = scope; node != NULL; node = node->parent) {
            if (node->type == TH_NODE_ELEMENT && selector_matches(node, compiled, scope)) {
                *found = node;
                break;
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    if (error) {
        return -1;
    }
    return 0;
}

/* One entry in prune()'s keep set: a node the prune must retain. full marks a
   match, whose whole subtree stays; a cleared full marks an ancestor of a match,
   whose other children are still pruned. */
typedef struct {
    th_node *node;
    int full;
} prune_keep;

/* Append (node, full) to the keep buffer, doubling its capacity on demand.
   Returns 0, or -1 on allocation failure. */
static int prune_push(prune_keep **buffer, Py_ssize_t *count, Py_ssize_t *capacity, th_node *node, int full) {
    if (*count == *capacity) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)*capacity + 1, (size_t)*capacity, 16, sizeof(prune_keep), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        prune_keep *resized = PyMem_Realloc(*buffer, bytes);
        if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: a realloc failure cannot be forced from a test */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        *buffer = resized;
        *capacity = (Py_ssize_t)cap;
    }
    (*buffer)[*count].node = node;
    (*buffer)[*count].full = full;
    (*count)++;
    return 0;
}

/* Order keep entries by node address so prune_kept can binary-search them. */
static int prune_compare(const void *left, const void *right) {
    uintptr_t left_node = (uintptr_t)((const prune_keep *)left)->node;
    uintptr_t right_node = (uintptr_t)((const prune_keep *)right)->node;
    if (left_node < right_node) {
        return -1;
    }
    return left_node > right_node ? 1 : 0;
}

/* Binary-search the address-sorted keep set; on a hit set *full and return 1. */
static int prune_kept(const prune_keep *keep, Py_ssize_t count, th_node *node, int *full) {
    Py_ssize_t low = 0;
    Py_ssize_t high = count;
    uintptr_t target = (uintptr_t)node;
    while (low < high) {
        Py_ssize_t mid = low + (high - low) / 2;
        uintptr_t at = (uintptr_t)keep[mid].node;
        if (at < target) {
            low = mid + 1;
        } else if (at > target) {
            high = mid;
        } else {
            *full = keep[mid].full;
            return 1;
        }
    }
    return 0;
}

/* Record a match and its ancestor chain up to (but excluding) origin in the keep
   set. Returns 0, or -1 on allocation failure. */
static int prune_keep_match(prune_keep **buffer, Py_ssize_t *count, Py_ssize_t *capacity, th_node *node,
                            th_node *origin, th_node_map *ancestors) {
    if (prune_push(buffer, count, capacity, node, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *ancestor = node->parent; ancestor != origin; ancestor = ancestor->parent) {
        if (th_node_map_find(ancestors, ancestor) != 0) {
            break;
        }
        if (th_node_map_insert(ancestors, ancestor, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            PyErr_NoMemory();                                 /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                                        /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: closes the allocation-failure-only branch */
        if (prune_push(buffer, count, capacity, ancestor, 0) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* The next node after node's whole subtree in document order, bounded to root's
   subtree (NULL once the walk leaves root). Like subtree_next but stopping at
   root, so pruning one node's subtree never escapes into the rest of the tree. */
static th_node *subtree_after(th_node *node, th_node *root) {
    while (node != root) {
        if (node->next_sibling != NULL) {
            return node->next_sibling;
        }
        node = node->parent;
    }
    return NULL;
}

TH_NODE_API(, PyObject *, node_prune, (PyObject * self, PyObject *arg), (self, arg), (PyObject * self, PyObject *arg),
            (NodeObject *)self, arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    prune_keep *keep = NULL;
    th_node_map ancestors = {0};
    Py_ssize_t count = 0;
    Py_ssize_t capacity = 0;
    int error = 0;
    int moved = 0;
    sel_nth_memo nth_memo = {0};
    sel_default_memo default_memo = {0};
    sel_has_memo has_memo = {0};       /* shared across the walk so :has() memoizes its subtree scans */
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, (HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *origin = ((NodeObject *)self)->node;
        /* Pass 1: snapshot each match and its ancestor chain while the tree is
           intact. A string/regex selector can call back into Python, so no edit
           may run here; matching alone never rewires a node, so the snapshot lets
           pass 2 edit in pure C without dereferencing a stale pointer. */
        const sel_simple *single = sel_single_simple(compiled);
        sel_ctx ctx = {compiled->tree,   origin,
                       compiled->quirks, selector_uses_has_memo(compiled) ? &has_memo : NULL,
                       &nth_memo,        &default_memo};
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (node->type != TH_NODE_ELEMENT) {
                continue;
            }
            if (!(single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches_c(node, compiled, &ctx))) {
                continue;
            }
            /* GCOVR_EXCL_BR_START: allocation failure */
            if (prune_keep_match(&keep, &count, &capacity, node, origin, &ancestors) < 0) {
                /* GCOVR_EXCL_BR_STOP */
                error = 1; /* GCOVR_EXCL_LINE: allocation path */
                break;     /* GCOVR_EXCL_LINE: allocation path */
            }
        }
        if (!error) { /* GCOVR_EXCL_BR_LINE: the false arm is the pass-1 allocation-failure bail, unforceable */
            /* Sort by address and fold duplicates so a node that is both a match and
               an ancestor of a deeper match keeps its whole subtree (full wins). */
            if (count > 0) {
                qsort(keep, (size_t)count, sizeof(prune_keep), prune_compare);
            }
            Py_ssize_t unique = 0;
            for (Py_ssize_t read = 0; read < count; read++) {
                if (unique > 0 && keep[unique - 1].node == keep[read].node) {
                    keep[unique - 1].full |= keep[read].full;
                } else {
                    keep[unique++] = keep[read];
                }
            }
            /* Pass 2: pure-C edit. Removing one node rewires only links outside the
               nodes still to visit, and subtree_after reads them before the remove,
               so no snapshot pointer is dereferenced after it goes stale. */
            handle_drop_index(handle);
            th_node *node = origin->first_child;
            while (node != NULL) {
                int full = 0;
                if (!prune_kept(keep, unique, node, &full)) {
                    th_node *after = subtree_after(node, origin);
                    th_node_remove(node);
                    node = after;
                } else if (full) {
                    node = subtree_after(node, origin); /* a match: keep its whole subtree, skip it */
                } else {
                    node = node->first_child; /* an ancestor of a match always has a child to descend into */
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    sel_has_memo_free(&has_memo);
    PyMem_Free(keep);
    PyMem_Free(ancestors.entries);
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}

/* A growable array of matched nodes, the snapshot the bulk edits walk. */
typedef struct {
    th_node **items;
    Py_ssize_t count;
    Py_ssize_t capacity;
} node_snapshot;

/* Append node to the snapshot, doubling its capacity on demand. Returns 0, or
   -1 on allocation failure. */
static int snapshot_push(node_snapshot *snapshot, th_node *node) {
    if (snapshot->count == snapshot->capacity) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)snapshot->capacity + 1, (size_t)snapshot->capacity, 16, sizeof(th_node *), &cap,
                               &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_node **resized = PyMem_Realloc(snapshot->items, bytes);
        if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: a realloc failure cannot be forced from a test */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        snapshot->items = resized;
        snapshot->capacity = (Py_ssize_t)cap;
    }
    snapshot->items[snapshot->count++] = node;
    return 0;
}

/* Record every element descendant of origin matching compiled, in document
   order, into snapshot. A string/regex selector can call back into Python, so
   matching must finish before any edit; matching alone never rewires a node, so
   the snapshot lets the edit pass run in pure C. Returns 0, or -1 on allocation
   failure. */
static int snapshot_matches(sel_compiled *compiled, th_node *origin, node_snapshot *snapshot) {
    const sel_simple *single = sel_single_simple(compiled);
    sel_nth_memo nth_memo = {0};
    sel_default_memo default_memo = {0};
    sel_has_memo has_memo = {0}; /* shared across the walk so :has() memoizes its subtree scans */
    sel_ctx ctx = {compiled->tree, origin,       compiled->quirks, selector_uses_has_memo(compiled) ? &has_memo : NULL,
                   &nth_memo,      &default_memo};
    int result = 0;
    for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (!(single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches_c(node, compiled, &ctx))) {
            continue;
        }
        if (snapshot_push(snapshot, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            result = -1;                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    sel_has_memo_free(&has_memo);
    return result;
}

TH_NODE_API(, PyObject *, node_remove, (PyObject * self, PyObject *arg), (self, arg), (PyObject * self, PyObject *arg),
            (NodeObject *)self, arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    node_snapshot snapshot = {NULL, 0, 0};
    int error = 0;
    int moved = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, (HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *origin = ((NodeObject *)self)->node;
        if (snapshot_matches(compiled, origin, &snapshot) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            error = 1;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        } else if (snapshot.count > 0) {
            /* Pure-C edit: detach each match. A node never frees on remove, only
               unlinks, and re-detaching an already-detached node is a no-op, so a
               nested match whose ancestor already left the tree drops harmlessly. */
            handle_drop_index(handle);
            for (Py_ssize_t at = 0; at < snapshot.count; at++) {
                th_node_remove(snapshot.items[at]);
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    PyMem_Free(snapshot.items);
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}

TH_NODE_API(, PyObject *, node_strip_tags, (PyObject * self, PyObject *arg), (self, arg),
            (PyObject * self, PyObject *arg), (NodeObject *)self,
            arg != NULL && is_node(arg, state_of(self)) ? (NodeObject *)arg : NULL) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
retry:;
    PyObject *handle = Py_NewRef(((NodeObject *)self)->handle);
    node_snapshot snapshot = {NULL, 0, 0};
    int error = 0;
    int moved = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile(state_of(self)->selector_error, (HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else if (((NodeObject *)self)->handle != handle) {
        moved = 1;
    } else {
        th_node *origin = ((NodeObject *)self)->node;
        if (snapshot_matches(compiled, origin, &snapshot) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            error = 1;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        } else if (snapshot.count > 0) {
            /* Pure-C edit: unwrap each match, splicing its children into its parent
               in its place, then detach it. Unwrapping only relinks, so a nested
               match stays live, reparented to the surviving ancestor, until its own
               turn; its parent is re-read here and is always set (a match is a
               strict descendant of origin). */
            handle_drop_index(handle);
            for (Py_ssize_t at = 0; at < snapshot.count; at++) {
                th_node *node = snapshot.items[at];
                th_node *parent = node->parent;
                while (node->first_child != NULL) {
                    th_node *child = node->first_child;
                    th_node_remove(child);
                    th_node_insert_before(parent, child, node);
                }
                th_node_remove(node);
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(handle);
    if (moved) {
        goto retry;
    }
    PyMem_Free(snapshot.items);
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}
