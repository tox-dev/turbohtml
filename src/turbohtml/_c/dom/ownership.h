#ifndef TURBOHTML_DOM_OWNERSHIP_H
#define TURBOHTML_DOM_OWNERSHIP_H

#include "tokenizer/binding.h"
#include "dom/tree.h"

#ifdef Py_GIL_DISABLED
/* Immutable identity avoids suspending tree locks during alias hashing and comparison. */
typedef struct {
    Py_hash_t hash;
} node_identity;
#endif

typedef struct NodeObject {
    PyObject_HEAD PyObject *handle;
    th_node *node;
#ifdef Py_GIL_DISABLED
    node_identity *identity;
    PyThread_type_lock ownership_lock;
    struct NodeObject *previous_binding;
    struct NodeObject *next_binding;
#endif
} NodeObject;

#ifdef Py_GIL_DISABLED
/* Ownership changes must not suspend the tree critical sections. */
static inline PyObject *node_owner_ref(NodeObject *self) {
    PyThread_acquire_lock(self->ownership_lock, WAIT_LOCK);
    PyObject *handle = Py_NewRef(self->handle);
    PyThread_release_lock(self->ownership_lock);
    return handle;
}

static inline int node_owned_by(NodeObject *self, PyObject *handle) {
    PyThread_acquire_lock(self->ownership_lock, WAIT_LOCK);
    int same = self->handle == handle;
    PyThread_release_lock(self->ownership_lock);
    return same;
}

typedef struct {
    PyCriticalSection2 section;
    PyObject *first;
    PyObject *second;
} node_guard;

static inline void node_guard_begin(node_guard *guard, NodeObject *first, NodeObject *second) {
    if (first == NULL) {
        guard->first = NULL;
        return;
    }
    if (second == NULL) {
        second = first;
    }
    for (;;) {
        guard->first = node_owner_ref(first);
        guard->second = node_owner_ref(second);
        PyCriticalSection2_Begin(&guard->section, guard->first, guard->second);
        if (node_owned_by(first, guard->first) && node_owned_by(second, guard->second)) {
            return;
        }
        PyCriticalSection2_End(&guard->section);
        Py_DECREF(guard->first);
        Py_DECREF(guard->second);
    }
}

static inline void node_guard_begin_with_handle(node_guard *guard, PyObject *handle, NodeObject *node) {
    guard->first = Py_NewRef(handle);
    for (;;) {
        guard->second = node_owner_ref(node);
        PyCriticalSection2_Begin(&guard->section, guard->first, guard->second);
        if (node_owned_by(node, guard->second)) {
            return;
        }
        PyCriticalSection2_End(&guard->section);
        Py_DECREF(guard->second);
    }
}

static inline void node_guard_end(node_guard *guard) {
    if (guard->first == NULL) {
        return;
    }
    PyCriticalSection2_End(&guard->section);
    Py_DECREF(guard->first);
    Py_DECREF(guard->second);
}

#define TH_NODE_API(storage, result_type, name, parameters, arguments, body_parameters, owner, other)                  \
    static result_type name##_guarded_body body_parameters;                                                            \
    storage result_type name parameters {                                                                              \
        node_guard guard;                                                                                              \
        node_guard_begin(&guard, owner, other);                                                                        \
        result_type guarded_result = name##_guarded_body arguments;                                                    \
        node_guard_end(&guard);                                                                                        \
        return guarded_result;                                                                                         \
    }                                                                                                                  \
    static result_type name##_guarded_body body_parameters
#else
static inline PyObject *node_owner_ref(NodeObject *self) {
    return Py_NewRef(self->handle);
}
static inline int node_owned_by(NodeObject *self, PyObject *handle) {
    return self->handle == handle;
}
#define TH_NODE_API(storage, result_type, name, parameters, arguments, body_parameters, owner, other)                  \
    storage result_type name body_parameters
#endif

static inline int is_node(PyObject *obj, module_state *state) {
    return PyObject_TypeCheck(obj, (PyTypeObject *)state->node_type);
}

#ifdef Py_GIL_DISABLED
static inline NodeObject *node_argument(module_state *state, PyObject *args, PyObject *kwargs, Py_ssize_t index,
                                        const char *name) {
    PyObject *value = index < PyTuple_GET_SIZE(args) ? PyTuple_GET_ITEM(args, index)
                      : kwargs == NULL               ? NULL
                                                     : PyDict_GetItemString(kwargs, name);
    return value != NULL && is_node(value, state) ? (NodeObject *)value : NULL;
}
#endif

#endif
