/* Synchronous mutation observation (issue #554): the MutationObserver type and the
   per-tree record engine behind it.

   The DOM MutationObserver batches records and delivers them on a microtask, so a
   callback never runs mid-edit. turbohtml has no event loop, so it keeps the same
   record shape but drains the queue synchronously: take_records() pulls the batch,
   deliver() pulls it and calls the callback. Records still queue at mutation time and
   are only ever handed to Python at a drain, so an observer callback never fires while
   the tree is half-linked.

   The mutation primitives (dom/mutate.c) and the binding layer (dom/element.c) call the
   th_mo_* recording entry points; each runs the DOM "queue a mutation record" algorithm
   over the tree's live observers and is a no-op when there are none. A MutationObserver
   owns one th_observer entry in its tree's registry, holding a handle reference so the
   tree outlives it; the whole engine runs under the handle's per-tree critical section. */

#include "dom/nodes.h"
#include "dom/observe.h"

#include "core/vec.h" /* th_grow_cap */

#include <string.h>

/* One attributeFilter entry, an owned UTF-8 copy of an attribute name to match. */
typedef struct {
    char *name;
    Py_ssize_t len;
} th_mo_filter;

/* One registration: a watched target and the options that gate which changes on it
   (and, with subtree, in its subtree) reach the observer. */
typedef struct {
    th_node *target;
    uint8_t child_list;
    uint8_t attributes;
    uint8_t character_data;
    uint8_t subtree;
    uint8_t attribute_old_value;
    uint8_t character_data_old_value;
    uint8_t has_filter;   /* an attributeFilter was given (an empty one matches no name) */
    th_mo_filter *filter; /* NULL when no attributeFilter was given or it was empty */
    Py_ssize_t filter_count;
} th_mo_reg;

/* One queued record, holding raw node pointers (valid while the tree lives) and an
   owned copy of the old value when the record reports one. */
typedef struct {
    int kind; /* enum th_mo_kind */
    th_node *target;
    th_node *added;   /* childList: the inserted node, else NULL */
    th_node *removed; /* childList: the removed node, else NULL */
    th_node *prev_sibling;
    th_node *next_sibling;
    uint32_t attr_atom; /* attributes: the changed attribute's name atom */
    Py_UCS4 *old_value; /* owned; NULL for an empty or absent old value */
    Py_ssize_t old_len;
    int has_old; /* whether the record reports an old value (else oldValue is None) */
} th_mo_record;

struct th_observer {
    th_mo_reg *regs;
    Py_ssize_t reg_count;
    Py_ssize_t reg_cap;
    th_mo_record *records;
    Py_ssize_t record_count;
    Py_ssize_t record_cap;
};

typedef struct {
    PyObject_HEAD PyObject *callback; /* the MutationCallback, or None */
    PyObject *handle;                 /* _TreeHandle bound on the first observe(); NULL until then */
    th_observer *observer;            /* this observer's entry in its tree's registry; NULL until bound */
} MutationObserverObject;

/* Grow a dynamic array to hold needed elements, returning the (possibly moved) base or
   NULL on the unforceable overflow/allocation-failure paths. */
static void *mo_grow(void *base, Py_ssize_t needed, Py_ssize_t *cap, Py_ssize_t initial, size_t elem) {
    if (needed <= *cap) {
        return base;
    }
    size_t new_cap, bytes;
    int fits = th_grow_cap((size_t)needed, (size_t)*cap, (size_t)initial, elem, &new_cap, &bytes);
    if (!fits) {     /* GCOVR_EXCL_BR_LINE: overflow-guard path, unreachable from a test */
        return NULL; /* GCOVR_EXCL_LINE: overflow-guard path */
    }
    void *grown = PyMem_Realloc(base, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *cap = (Py_ssize_t)new_cap;
    return grown;
}

static void free_reg(th_mo_reg *reg) {
    for (Py_ssize_t index = 0; index < reg->filter_count; index++) {
        PyMem_Free(reg->filter[index].name);
    }
    PyMem_Free(reg->filter);
    reg->filter = NULL;
    reg->filter_count = 0;
}

/* Drop every registration and queued record, leaving the observer empty but allocated. */
static void mo_clear(th_observer *observer) {
    for (Py_ssize_t index = 0; index < observer->reg_count; index++) {
        free_reg(&observer->regs[index]);
    }
    PyMem_Free(observer->regs);
    observer->regs = NULL;
    observer->reg_count = 0;
    observer->reg_cap = 0;
    for (Py_ssize_t index = 0; index < observer->record_count; index++) {
        PyMem_Free(observer->records[index].old_value);
    }
    PyMem_Free(observer->records);
    observer->records = NULL;
    observer->record_count = 0;
    observer->record_cap = 0;
}

/* Allocate an observer and register it on the tree, returning it or NULL on failure. */
static th_observer *mo_ensure(th_tree *tree) {
    th_observer *observer = PyMem_Calloc(1, sizeof(th_observer));
    if (observer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_observer ***observers = th_tree_observers_ptr(tree);
    Py_ssize_t *count = th_tree_observer_count_ptr(tree);
    Py_ssize_t *cap = th_tree_observer_cap_ptr(tree);
    th_observer **grown = mo_grow(*observers, *count + 1, cap, 4, sizeof(th_observer *));
    if (grown == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(observer); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *observers = grown;
    (*observers)[(*count)++] = observer;
    return observer;
}

/* Detach the observer from its tree and free it. */
static void mo_destroy(th_tree *tree, th_observer *observer) {
    mo_clear(observer);
    th_observer ***observers = th_tree_observers_ptr(tree);
    Py_ssize_t *count = th_tree_observer_count_ptr(tree);
    Py_ssize_t *cap = th_tree_observer_cap_ptr(tree);
    /* the observer is always registered, so the scan always breaks and never runs to the end */
    for (Py_ssize_t index = 0; index < *count; index++) { /* GCOVR_EXCL_BR_LINE */
        if ((*observers)[index] == observer) {
            (*observers)[index] = (*observers)[*count - 1];
            (*count)--;
            break;
        }
    }
    if (*count == 0) {
        PyMem_Free(*observers);
        *observers = NULL;
        *cap = 0;
    }
    PyMem_Free(observer);
}

/* --- the DOM "queue a mutation record" algorithm --- */

/* Whether target is target-or-a-descendant of a registration's watched node. */
static int reg_covers(const th_mo_reg *reg, th_node *target) {
    if (reg->target == target) {
        return 1;
    }
    return is_ancestor(reg->target, target);
}

static int filter_matches(th_tree *tree, const th_mo_reg *reg, uint32_t atom) {
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, atom, &name_len);
    for (Py_ssize_t index = 0; index < reg->filter_count; index++) {
        if (reg->filter[index].len == name_len && memcmp(reg->filter[index].name, name, (size_t)name_len) == 0) {
            return 1;
        }
    }
    return 0;
}

static void append_record(th_observer *observer, int kind, th_node *target, uint32_t atom, const Py_UCS4 *old_value,
                          Py_ssize_t old_len, int has_old, th_node *added, th_node *removed, th_node *prev,
                          th_node *next) {
    th_mo_record *grown =
        mo_grow(observer->records, observer->record_count + 1, &observer->record_cap, 8, sizeof(th_mo_record));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path; the record is dropped */
    }
    observer->records = grown;
    th_mo_record *record = &observer->records[observer->record_count];
    record->kind = kind;
    record->target = target;
    record->added = added;
    record->removed = removed;
    record->prev_sibling = prev;
    record->next_sibling = next;
    record->attr_atom = atom;
    record->old_value = NULL;
    record->old_len = 0;
    record->has_old = has_old;
    if (has_old && old_len > 0) {
        Py_UCS4 *copy = PyMem_Malloc((size_t)old_len * sizeof(Py_UCS4));
        if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: the else runs only on an unforceable allocation failure */
            memcpy(copy, old_value, (size_t)old_len * sizeof(Py_UCS4));
            record->old_value = copy;
            record->old_len = old_len;
        } else {                 /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
            record->has_old = 0; /* report None rather than fail the edit */
        } /* GCOVR_EXCL_STOP */
    }
    observer->record_count++;
}

/* Queue a record on every observer with a registration that covers target and accepts
   this kind of change, capturing the old value only for a registration that asked for it. */
static void queue_record(th_tree *tree, int kind, th_node *target, uint32_t atom, const Py_UCS4 *old_value,
                         Py_ssize_t old_len, int had_value, th_node *added, th_node *removed, th_node *prev,
                         th_node *next) {
    th_observer **observers = *th_tree_observers_ptr(tree);
    Py_ssize_t observer_count = *th_tree_observer_count_ptr(tree);
    for (Py_ssize_t obs_index = 0; obs_index < observer_count; obs_index++) {
        th_observer *observer = observers[obs_index];
        int interested = 0;
        int want_old = 0;
        for (Py_ssize_t reg_index = 0; reg_index < observer->reg_count; reg_index++) {
            const th_mo_reg *reg = &observer->regs[reg_index];
            if (!reg_covers(reg, target)) {
                continue;
            }
            if (reg->target != target && !reg->subtree) {
                continue;
            }
            if (kind == TH_MO_CHILD_LIST && !reg->child_list) {
                continue;
            }
            if (kind == TH_MO_ATTRIBUTES) {
                if (!reg->attributes) {
                    continue;
                }
                if (reg->has_filter && !filter_matches(tree, reg, atom)) {
                    continue;
                }
            }
            if (kind == TH_MO_CHARACTER_DATA && !reg->character_data) {
                continue;
            }
            interested = 1;
            if ((kind == TH_MO_ATTRIBUTES && reg->attribute_old_value) ||
                (kind == TH_MO_CHARACTER_DATA && reg->character_data_old_value)) {
                want_old = 1;
            }
        }
        if (interested) {
            int has_old = want_old && (kind != TH_MO_ATTRIBUTES || had_value);
            append_record(observer, kind, target, atom, old_value, old_len, has_old, added, removed, prev, next);
        }
    }
}

void th_mo_child_inserted(th_tree *tree, th_node *parent, th_node *child) {
    if (!th_tree_has_observers(tree)) {
        return;
    }
    queue_record(tree, TH_MO_CHILD_LIST, parent, 0, NULL, 0, 0, child, NULL, child->prev_sibling, child->next_sibling);
}

void th_mo_child_removed(th_tree *tree, th_node *parent, th_node *child, th_node *prev, th_node *next) {
    if (!th_tree_has_observers(tree)) {
        return;
    }
    queue_record(tree, TH_MO_CHILD_LIST, parent, 0, NULL, 0, 0, NULL, child, prev, next);
}

/* The attribute and character-data hooks run unconditionally; queue_record iterates the
   (empty for an observer-free tree) registry, so the fast path stays a single loop test.
   The childList hooks above gate on th_tree_has_observers because they run from the
   binding layer for every structural edit, including on observer-free trees. */
void th_mo_attr_changed(th_tree *tree, th_node *target, uint32_t name_atom, const Py_UCS4 *old_value,
                        Py_ssize_t old_len, int had_value) {
    queue_record(tree, TH_MO_ATTRIBUTES, target, name_atom, old_value, old_len, had_value, NULL, NULL, NULL, NULL);
}

void th_mo_char_data_changed(th_tree *tree, th_node *target, const Py_UCS4 *old_value, Py_ssize_t old_len) {
    queue_record(tree, TH_MO_CHARACTER_DATA, target, 0, old_value, old_len, 1, NULL, NULL, NULL, NULL);
}

/* --- the MutationObserver Python type --- */

static PyObject *mo_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"callback", NULL};
    PyObject *callback = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|O:MutationObserver", keywords, &callback)) {
        return NULL;
    }
    if (callback != Py_None && !PyCallable_Check(callback)) {
        PyErr_SetString(PyExc_TypeError, "callback must be callable or None");
        return NULL;
    }
    MutationObserverObject *self = (MutationObserverObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->callback = Py_NewRef(callback);
    self->handle = NULL;
    self->observer = NULL;
    return (PyObject *)self;
}

static int mo_traverse(PyObject *self, visitproc visit, void *arg) {
    MutationObserverObject *observer = (MutationObserverObject *)self;
    Py_VISIT(Py_TYPE(self));      /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(observer->callback); /* GCOVR_EXCL_BR_LINE: set at creation, dropped only in clear/dealloc */
    Py_VISIT(observer->handle);   /* GCOVR_EXCL_BR_LINE: the failing-visit arm needs a gc callback that errors */
    return 0;
}

/* tp_clear drops only the callback: it is the sole reference that can close a cycle
   back to the observer. The handle is deliberately kept -- _TreeHandle is not
   GC-tracked and frees the tree only in its own dealloc, so holding the reference
   through mo_dealloc keeps the tree (and the registry entry we must remove) alive.
   Clearing it here would let the tree free before mo_dealloc reaches its registry. */
static int mo_clear_py(PyObject *self) {
    Py_CLEAR(((MutationObserverObject *)self)->callback); /* GCOVR_EXCL_BR_LINE: a callback is always set */
    return 0;
}

static void mo_dealloc(PyObject *self) {
    MutationObserverObject *observer = (MutationObserverObject *)self;
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    if (observer->observer != NULL) {
        th_tree *tree = ((HandleObject *)observer->handle)->tree;
        Py_BEGIN_CRITICAL_SECTION(observer->handle);
        mo_destroy(tree, observer->observer);
        Py_END_CRITICAL_SECTION();
        observer->observer = NULL;
    }
    (void)mo_clear_py(self);
    Py_CLEAR(observer->handle); /* the NULL arm runs for an observer that never observed */
    type->tp_free(self);
    Py_DECREF(type);
}

static void free_names(th_mo_filter *filter, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        PyMem_Free(filter[index].name);
    }
    PyMem_Free(filter);
}

/* Read attribute_filter into an owned th_mo_filter array. *has_filter is set whenever a
   filter (even an empty one, which matches no name) was given; *out stays NULL for None,
   an empty filter, or no filter. */
static int build_filter(PyObject *value, int *has_filter, th_mo_filter **out, Py_ssize_t *out_count) {
    *has_filter = value != NULL && value != Py_None;
    *out = NULL;
    *out_count = 0;
    if (!*has_filter) {
        return 0;
    }
    PyObject *sequence = PySequence_Fast(value, "attribute_filter must be an iterable of str");
    if (sequence == NULL) {
        return -1;
    }
    Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    th_mo_filter *filter = count > 0 ? PyMem_Calloc((size_t)count, sizeof(th_mo_filter)) : NULL;
    if (count > 0 && filter == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(sequence);           /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();              /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *item = PySequence_Fast_GET_ITEM(sequence, index);
        if (!PyUnicode_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "attribute_filter entries must be str");
            free_names(filter, index);
            Py_DECREF(sequence);
            return -1;
        }
        Py_ssize_t len;
        const char *bytes = PyUnicode_AsUTF8AndSize(item, &len);
        if (bytes == NULL) { /* a lone-surrogate name has no UTF-8 form */
            free_names(filter, index);
            Py_DECREF(sequence);
            return -1;
        }
        char *copy = PyMem_Malloc((size_t)len + 1);
        if (copy == NULL) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            free_names(filter, index); /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(sequence);       /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();          /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(copy, bytes, (size_t)len + 1);
        filter[index].name = copy;
        filter[index].len = len;
    }
    Py_DECREF(sequence);
    *out = filter;
    *out_count = count;
    return 0;
}

static int store_reg(th_observer *observer, th_node *target, const uint8_t opts[6], int has_filter,
                     th_mo_filter *filter, Py_ssize_t filter_count) {
    th_mo_reg *reg = NULL;
    for (Py_ssize_t index = 0; index < observer->reg_count; index++) {
        if (observer->regs[index].target == target) {
            reg = &observer->regs[index];
            free_reg(reg); /* replace the earlier options for this target */
            break;
        }
    }
    if (reg == NULL) {
        th_mo_reg *grown = mo_grow(observer->regs, observer->reg_count + 1, &observer->reg_cap, 4, sizeof(th_mo_reg));
        if (grown == NULL) {                  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            free_names(filter, filter_count); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        observer->regs = grown;
        reg = &observer->regs[observer->reg_count++];
    }
    reg->target = target;
    reg->child_list = opts[0];
    reg->attributes = opts[1];
    reg->character_data = opts[2];
    reg->subtree = opts[3];
    reg->attribute_old_value = opts[4];
    reg->character_data_old_value = opts[5];
    reg->has_filter = (uint8_t)has_filter;
    reg->filter = filter;
    reg->filter_count = filter_count;
    return 0;
}

PyDoc_STRVAR(mo_observe_doc,
             "observe(target, *, child_list=False, attributes=False, character_data=False, subtree=False, "
             "attribute_old_value=False, character_data_old_value=False, attribute_filter=None)\n--\n\n"
             "Watch target for mutations, recording each matching change until disconnect().\n\n"
             ":param target: the node to observe; observing a second target adds to the set,\n"
             "    re-observing one replaces its options. Every target must share one tree.\n"
             ":param child_list: record additions and removals among target's children.\n"
             ":param attributes: record attribute changes; implied by attribute_old_value or\n"
             "    attribute_filter.\n"
             ":param character_data: record text changes on a Text/Comment/CData node; implied\n"
             "    by character_data_old_value.\n"
             ":param subtree: extend the watch to target's whole subtree, not just its children.\n"
             ":param attribute_old_value: record the previous value on each attribute record.\n"
             ":param character_data_old_value: record the previous text on each character-data record.\n"
             ":param attribute_filter: only record changes to these attribute names.\n"
             ":raises TypeError: target is not a node, or the options set nothing to observe.\n"
             ":raises ValueError: target belongs to a different tree than an earlier one.");

static PyObject *mo_observe(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"",
                               "child_list",
                               "attributes",
                               "character_data",
                               "subtree",
                               "attribute_old_value",
                               "character_data_old_value",
                               "attribute_filter",
                               NULL};
    PyObject *target_obj;
    int child_list = 0, attributes = 0, character_data = 0, subtree = 0;
    int attribute_old_value = 0, character_data_old_value = 0;
    PyObject *attribute_filter = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|$ppppppO:observe", keywords, &target_obj, &child_list, &attributes,
                                     &character_data, &subtree, &attribute_old_value, &character_data_old_value,
                                     &attribute_filter)) {
        return NULL;
    }
    module_state *state = state_of(self);
    if (!is_node(target_obj, state)) {
        PyErr_SetString(PyExc_TypeError, "target must be a node");
        return NULL;
    }
    if (attribute_old_value || (attribute_filter != NULL && attribute_filter != Py_None)) {
        attributes = 1; /* the DOM implies attributes from either attribute option */
    }
    if (character_data_old_value) {
        character_data = 1;
    }
    if (!child_list && !attributes && !character_data) {
        PyErr_SetString(PyExc_TypeError, "at least one of child_list, attributes, or character_data must be set");
        return NULL;
    }
    MutationObserverObject *observer = (MutationObserverObject *)self;
    PyObject *handle = ((NodeObject *)target_obj)->handle;
    if (observer->handle != NULL && observer->handle != handle) {
        PyErr_SetString(PyExc_ValueError, "target belongs to a different tree than an earlier one");
        return NULL;
    }
    int has_filter;
    th_mo_filter *filter;
    Py_ssize_t filter_count;
    if (build_filter(attribute_filter, &has_filter, &filter, &filter_count) < 0) {
        return NULL;
    }
    if (observer->handle == NULL) {
        observer->handle = Py_NewRef(handle);
    }
    th_node *target = ((NodeObject *)target_obj)->node;
    th_tree *tree = ((HandleObject *)handle)->tree;
    const uint8_t opts[6] = {(uint8_t)child_list, (uint8_t)attributes,          (uint8_t)character_data,
                             (uint8_t)subtree,    (uint8_t)attribute_old_value, (uint8_t)character_data_old_value};
    int stored = -1;
    Py_BEGIN_CRITICAL_SECTION(handle);
    if (observer->observer == NULL) {
        observer->observer = mo_ensure(tree);
    }
    if (observer->observer != NULL) { /* GCOVR_EXCL_BR_LINE: the else runs only when mo_ensure hits OOM */
        stored = store_reg(observer->observer, target, opts, has_filter, filter, filter_count);
    } else {
        free_names(filter, filter_count); /* GCOVR_EXCL_LINE: mo_ensure only fails on OOM */
    }
    Py_END_CRITICAL_SECTION();
    if (stored < 0) {            /* GCOVR_EXCL_BR_LINE: mo_ensure/store_reg only fail on OOM */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *node_tuple(module_state *state, PyObject *handle, th_node *node) {
    if (node == NULL) {
        return PyTuple_New(0);
    }
    PyObject *wrapped = node_wrap(state, handle, node);
    if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *tuple = PyTuple_New(1);
    if (tuple == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(wrapped); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyTuple_SET_ITEM(tuple, 0, wrapped);
    return tuple;
}

static PyObject *build_record(module_state *state, PyObject *handle, th_tree *tree, const th_mo_record *record) {
    const char *type_name = record->kind == TH_MO_CHILD_LIST   ? "childList"
                            : record->kind == TH_MO_ATTRIBUTES ? "attributes"
                                                               : "characterData";
    PyObject *type_obj = PyUnicode_FromString(type_name);
    PyObject *target = node_wrap(state, handle, record->target);
    PyObject *added = node_tuple(state, handle, record->added);
    PyObject *removed = node_tuple(state, handle, record->removed);
    PyObject *prev = node_wrap(state, handle, record->prev_sibling);
    PyObject *next = node_wrap(state, handle, record->next_sibling);
    PyObject *attr_name;
    if (record->kind == TH_MO_ATTRIBUTES) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, record->attr_atom, &name_len);
        attr_name = PyUnicode_DecodeUTF8(name, name_len, "strict");
    } else {
        attr_name = Py_NewRef(Py_None);
    }
    PyObject *old_value;
    if (record->has_old) {
        old_value = record->old_len > 0 ? ucs4_to_str(record->old_value, record->old_len) : PyUnicode_FromString("");
    } else {
        old_value = Py_NewRef(Py_None);
    }
    PyObject *fields[] = {type_obj, target, added, removed, prev, next, attr_name, old_value};
    int missing = 0;
    for (int index = 0; index < 8; index++) {
        missing |= fields[index] == NULL; /* GCOVR_EXCL_BR_LINE: no field is NULL without an alloc failure */
    }
    PyObject *result = NULL;
    if (!missing) { /* GCOVR_EXCL_BR_LINE: the missing arm needs an unforceable allocation failure */
        result = PyObject_CallFunctionObjArgs(state->mutation_record_type, type_obj, target, added, removed, prev, next,
                                              attr_name, old_value, NULL);
    }
    for (int index = 0; index < 8; index++) {
        Py_XDECREF(fields[index]); /* GCOVR_EXCL_BR_LINE: only an unforceable alloc failure leaves a NULL field */
    }
    return result;
}

/* Build the record list and clear the queue. The caller holds the critical section. */
static PyObject *drain_locked(MutationObserverObject *observer) {
    module_state *state = state_of((PyObject *)observer);
    PyObject *records = PyList_New(0);
    if (records == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the handle is bound with the registry entry, so a bound observer has one here;
       only an unforceable mo_ensure OOM can leave the handle bound with no entry */
    if (observer->observer == NULL) { /* GCOVR_EXCL_BR_LINE */
        return records;               /* GCOVR_EXCL_LINE: reachable only after an ensure OOM */
    }
    th_tree *tree = ((HandleObject *)observer->handle)->tree;
    th_observer *inner = observer->observer;
    for (Py_ssize_t index = 0; index < inner->record_count; index++) {
        PyObject *record = build_record(state, observer->handle, tree, &inner->records[index]);
        if (record == NULL || PyList_Append(records, record) < 0) { /* GCOVR_EXCL_BR_LINE: alloc-failure path */
            Py_XDECREF(record);                                     /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(records);                                     /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(record);
        PyMem_Free(inner->records[index].old_value);
    }
    inner->record_count = 0;
    return records;
}

PyDoc_STRVAR(mo_take_records_doc, "take_records()\n--\n\n"
                                  "Return the queued MutationRecords and empty the queue.");

static PyObject *mo_take_records(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    MutationObserverObject *observer = (MutationObserverObject *)self;
    if (observer->handle == NULL) {
        return PyList_New(0);
    }
    PyObject *records;
    Py_BEGIN_CRITICAL_SECTION(observer->handle);
    records = drain_locked(observer);
    Py_END_CRITICAL_SECTION();
    return records;
}

PyDoc_STRVAR(mo_deliver_doc, "deliver()\n--\n\n"
                             "Drain the queued records and pass them to the callback synchronously.\n\n"
                             "The DOM schedules this on a microtask; with no event loop turbohtml runs it\n"
                             "when you call deliver(). Does nothing when the queue is empty or no callback\n"
                             "was given. Returns the records that were delivered.");

static PyObject *mo_deliver(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    MutationObserverObject *observer = (MutationObserverObject *)self;
    PyObject *records;
    if (observer->handle == NULL) {
        records = PyList_New(0);
    } else {
        Py_BEGIN_CRITICAL_SECTION(observer->handle);
        records = drain_locked(observer);
        Py_END_CRITICAL_SECTION();
    }
    if (records == NULL) { /* GCOVR_EXCL_BR_LINE: drain only fails on OOM */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (observer->callback != Py_None && PyList_GET_SIZE(records) > 0) {
        PyObject *outcome = PyObject_CallFunctionObjArgs(observer->callback, records, self, NULL);
        if (outcome == NULL) {
            Py_DECREF(records);
            return NULL;
        }
        Py_DECREF(outcome);
    }
    return records;
}

PyDoc_STRVAR(mo_disconnect_doc, "disconnect()\n--\n\n"
                                "Stop observing every target and discard the queued records.");

static PyObject *mo_disconnect(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    MutationObserverObject *observer = (MutationObserverObject *)self;
    if (observer->observer != NULL) {
        th_tree *tree = ((HandleObject *)observer->handle)->tree;
        Py_BEGIN_CRITICAL_SECTION(observer->handle);
        mo_destroy(tree, observer->observer);
        Py_END_CRITICAL_SECTION();
        observer->observer = NULL;
        Py_CLEAR(observer->handle); /* GCOVR_EXCL_BR_LINE: a bound observer always has a handle to drop */
    }
    Py_RETURN_NONE;
}

static PyObject *mo_repr(PyObject *Py_UNUSED(self)) {
    return PyUnicode_FromString("MutationObserver()");
}

static PyMethodDef mo_methods[] = {
    {"observe", (PyCFunction)(void (*)(void))mo_observe, METH_VARARGS | METH_KEYWORDS, mo_observe_doc},
    {"take_records", mo_take_records, METH_NOARGS, mo_take_records_doc},
    {"deliver", mo_deliver, METH_NOARGS, mo_deliver_doc},
    {"disconnect", mo_disconnect, METH_NOARGS, mo_disconnect_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(mo_doc, "MutationObserver(callback=None)\n--\n\n"
                     "A synchronous watcher for tree mutations, modeled on the DOM MutationObserver.\n\n"
                     "Register a target with observe(), then read the recorded changes with\n"
                     "take_records(), or call deliver() to hand them to callback synchronously. Unlike\n"
                     "the DOM, delivery never happens on its own -- turbohtml has no event loop -- so a\n"
                     "callback fires only when you drain the queue.\n\n"
                     ":param callback: called as callback(records, observer) by deliver(); optional when\n"
                     "    you only pull records with take_records().");

static PyType_Slot mo_slots[] = {
    {Py_tp_doc, (void *)mo_doc}, {Py_tp_new, mo_new},   {Py_tp_dealloc, mo_dealloc}, {Py_tp_traverse, mo_traverse},
    {Py_tp_clear, mo_clear_py},  {Py_tp_repr, mo_repr}, {Py_tp_methods, mo_methods}, {0, NULL},
};

static PyType_Spec mo_spec = {
    .name = "turbohtml._html.MutationObserver",
    .basicsize = sizeof(MutationObserverObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .slots = mo_slots,
};

int observe_register(PyObject *module, module_state *state) {
    state->mutation_observer_type = PyType_FromModuleAndSpec(module, &mo_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->mutation_observer_type == NULL ||                                                /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "MutationObserver", state->mutation_observer_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

PyObject *turbohtml_register_mutation_record(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->mutation_record_type, Py_NewRef(type));
    Py_RETURN_NONE;
}
