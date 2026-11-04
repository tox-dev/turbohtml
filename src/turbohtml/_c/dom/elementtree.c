#include "dom/nodes.h"

#include <stddef.h>

typedef struct {
    PyObject_HEAD NodeObject *node;
    PyObject *weakrefs;
} ElementView;

typedef struct {
    PyObject *cache;
    PyObject *key;
} view_cache_key;

static PyObject *view_wrap(module_state *state, PyObject *node);
static int view_clear_refs(PyObject *self);
static int view_root_clear(PyObject *self);
static int view_xpath_clear(PyObject *self);
static int view_iter_clear(PyObject *self);
static int view_remember_origin(NodeObject *node);
static PyObject *view_element(PyObject *module, PyObject *args, PyObject *kwargs);
static PyObject *view_subelement(PyObject *module, PyObject *args, PyObject *kwargs);
static PyObject *view_strip_tags(PyObject *module, PyObject *args);
static PyObject *view_strip_elements(PyObject *module, PyObject *args, PyObject *kwargs);
static PyObject *view_tostring(PyObject *module, PyObject *args, PyObject *kwargs);
static PyObject *view_from_lxml(PyObject *module, PyObject *source);
static PyObject *view_to_lxml(PyObject *module, PyObject *source);
static PyObject *view_to_lxml_html(PyObject *module, PyObject *source);
static PyObject *view_document_fromstring(PyObject *module, PyObject *source);
static PyObject *view_fromstring(PyObject *module, PyObject *source);
static PyObject *view_fragment_fromstring(PyObject *module, PyObject *args, PyObject *kwargs);

static void view_cache_key_free(PyObject *capsule) {
    view_cache_key *entry = PyCapsule_GetPointer(capsule, "turbohtml.elementtree.cache");
    Py_DECREF(entry->cache);
    Py_DECREF(entry->key);
    PyMem_Free(entry);
}

static PyObject *view_forget(PyObject *capsule, PyObject *weakref) {
    view_cache_key *entry = PyCapsule_GetPointer(capsule, "turbohtml.elementtree.cache");
    Py_BEGIN_CRITICAL_SECTION(entry->cache);
    if (PyDict_GetItem(entry->cache, entry->key) == weakref) {
        PyDict_DelItem(entry->cache, entry->key);
    }
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyMethodDef view_forget_method = {"_forget", view_forget, METH_O, NULL};

static void view_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
#ifndef PYPY_VERSION
    /* PyPy clears weakrefs before tp_dealloc; its API cannot rewrap a dying object. */
    PyObject_ClearWeakRefs(self);
#endif
    view_clear_refs(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static int view_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));               /* GCOVR_EXCL_BR_LINE: non-NULL type; Python cannot induce visit errors */
    Py_VISIT(((ElementView *)self)->node); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    return 0;
}

static int view_clear_refs(PyObject *self) {
    Py_CLEAR(((ElementView *)self)->node); /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    return 0;
}

static PyObject *view_wrap(module_state *state, PyObject *node) {
    if (node == Py_None) {
        return Py_NewRef(Py_None);
    }
#ifdef Py_GIL_DISABLED
    PyObject *key = PyLong_FromVoidPtr(((NodeObject *)node)->identity);
#else
    PyObject *key = PyLong_FromVoidPtr(node);
#endif
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(state->element_view_cache);
    PyObject *weakref = PyDict_GetItem(state->element_view_cache, key);
    if (weakref != NULL) {
#if PY_VERSION_HEX >= 0x030D0000
        PyWeakref_GetRef(weakref, &result);
#else
        PyObject *cached = PyWeakref_GetObject(weakref);
        if (cached != Py_None) {
            result = Py_NewRef(cached);
        }
#endif
    }
    if (result != NULL) {
        goto done;
    }
    PyTypeObject *type = (PyTypeObject *)state->element_view_type;
    ElementView *view = (ElementView *)type->tp_alloc(type, 0);
    if (view == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        goto done;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    view->node = (NodeObject *)Py_NewRef(node);
    view_cache_key *entry = PyMem_Malloc(sizeof(*entry));
    if (entry == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(view);  /* GCOVR_EXCL_LINE: allocation failure */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation failure */
        goto done;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    entry->cache = Py_NewRef(state->element_view_cache);
    entry->key = Py_NewRef(key);
    PyObject *capsule = PyCapsule_New(entry, "turbohtml.elementtree.cache", view_cache_key_free);
    if (capsule == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(entry->cache); /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(entry->key);   /* GCOVR_EXCL_LINE: allocation failure */
        PyMem_Free(entry);       /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(view);         /* GCOVR_EXCL_LINE: allocation failure */
        goto done;               /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *callback = PyCFunction_New(&view_forget_method, capsule);
    Py_DECREF(capsule);
    if (callback == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(view);    /* GCOVR_EXCL_LINE: allocation failure */
        goto done;          /* GCOVR_EXCL_LINE: allocation failure */
    }
    weakref = PyWeakref_NewRef((PyObject *)view, callback);
    Py_DECREF(callback);
    if (weakref == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(view);   /* GCOVR_EXCL_LINE: allocation failure */
        goto done;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    int stored = PyDict_SetItem(state->element_view_cache, key, weakref);
    Py_DECREF(weakref);
    if (stored < 0) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(view); /* GCOVR_EXCL_LINE: allocation failure */
        goto done;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    result = (PyObject *)view;
done:;
    Py_END_CRITICAL_SECTION();
    Py_DECREF(key);
    return result;
}

static PyObject *view_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"node", NULL};
    module_state *state = PyType_GetModuleState(type);
    PyObject *node;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O!:ElementView", keywords, state->element_type, &node)) {
        return NULL;
    }
    return view_wrap(state, node);
}

static PyObject *view_node(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef((PyObject *)((ElementView *)self)->node);
}

static PyObject *view_tag(PyObject *self, void *Py_UNUSED(closure)) {
    return PyObject_GetAttrString((PyObject *)((ElementView *)self)->node, "tag");
}

static int view_set_tag(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    return PyObject_SetAttrString((PyObject *)((ElementView *)self)->node, "tag", value);
}

static PyObject *view_repr(PyObject *self) {
    PyObject *tag = view_tag(self, NULL);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = th_str_format("<Element %U at %p>", tag, self);
    Py_DECREF(tag);
    return result;
}

TH_NODE_API(static, Py_ssize_t, view_length, (PyObject * self), (self), (PyObject * self), ((ElementView *)self)->node,
            NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    Py_ssize_t count = 0;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    for (th_node *child = owner->node->first_child; child != NULL; child = child->next_sibling) {
        count += child->type == TH_NODE_ELEMENT;
    }
    Py_END_CRITICAL_SECTION();
    return count;
}

static PyObject *view_text_run(NodeObject *owner, th_node *node) {
    if (node == NULL || node->type != TH_NODE_TEXT) {
        return Py_NewRef(Py_None);
    }
    PyObject *result = th_node_text_string(tree_of((PyObject *)owner), node);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (node = node->next_sibling; node != NULL && node->type == TH_NODE_TEXT; node = node->next_sibling) {
        PyObject *part = th_node_text_string(tree_of((PyObject *)owner), node);
        if (part == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        PyUnicode_AppendAndDel(&result, part);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    return result;
}

TH_NODE_API(static, PyObject *, view_text, (PyObject * self, void *closure), (self, closure),
            (PyObject * self, void *closure), ((ElementView *)self)->node, NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    result = view_text_run(owner, closure == NULL ? owner->node->first_child : owner->node->next_sibling);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *view_get(PyObject *self, PyObject *args) {
    PyObject *key;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTuple(args, "O|O:get", &key, &fallback)) {
        return NULL;
    }
    PyObject *value = PyObject_CallMethod((PyObject *)((ElementView *)self)->node, "attr", "O", key);
    if (value == Py_None) {
        Py_DECREF(value);
        return Py_NewRef(fallback);
    }
    return value;
}

static PyObject *view_set(PyObject *self, PyObject *args) {
    PyObject *key;
    PyObject *value;
    if (!PyArg_ParseTuple(args, "OO:set", &key, &value)) {
        return NULL;
    }
    PyObject *attrs = PyObject_GetAttrString((PyObject *)((ElementView *)self)->node, "attrs");
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = PyObject_SetItem(attrs, key, value);
    Py_DECREF(attrs);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_text_content(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return PyObject_GetAttrString((PyObject *)((ElementView *)self)->node, "text");
}

static PyObject *view_attrib(PyObject *self, void *Py_UNUSED(closure)) {
    PyObject *result = element_get_attrs((PyObject *)((ElementView *)self)->node, NULL);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result != NULL) {
        /* GCOVR_EXCL_BR_STOP */
        ((AttrsObject *)result)->flat = 1;
    }
    return result;
}

static PyObject *view_attribute_list(PyObject *self, const char *method) {
    PyObject *attrs = view_attrib(self, NULL);
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_CallMethod(attrs, method, NULL);
    Py_DECREF(attrs);
    return result;
}

static PyObject *view_keys(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_attribute_list(self, "keys");
}

static PyObject *view_items(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_attribute_list(self, "items");
}

static PyObject *view_values(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_attribute_list(self, "values");
}

TH_NODE_API(static, PyObject *, view_text_nodes, (NodeObject * owner, int tail), (owner, tail),
            (NodeObject * owner, int tail), owner, NULL) {
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    th_node *node = tail ? owner->node->next_sibling : owner->node->first_child;
    for (; node != NULL && node->type == TH_NODE_TEXT; node = node->next_sibling) {
        PyObject *wrapped = node_wrap_locked(state_of((PyObject *)owner), owner->handle, node);
        if (wrapped == NULL || PyList_Append(result, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_XDECREF(wrapped);                                     /* GCOVR_EXCL_LINE: allocation failure */
            Py_CLEAR(result);                                        /* GCOVR_EXCL_LINE: allocation failure */
            break;                                                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        Py_DECREF(wrapped);
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static int view_discard(PyObject *result) {
    if (result == NULL) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

TH_NODE_API(static, int, view_mark_holder, (NodeObject * holder), (holder), (NodeObject * holder), holder, NULL) {
    Py_BEGIN_CRITICAL_SECTION(holder->handle);
    holder->node->tag_flags |= TH_ELEM_TAIL_HOLDER;
    Py_END_CRITICAL_SECTION();
    return 0;
}

static PyObject *view_holder(NodeObject *node) {
    module_state *state = state_of((PyObject *)node);
    PyObject *holder = PyObject_CallFunction(state->element_type, "s", "turbohtml-holder");
    if (holder == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *parent = PyObject_GetAttrString((PyObject *)node, "parent");
    if (parent == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(holder); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    int insert = parent != Py_None && !Py_IS_TYPE(parent, (PyTypeObject *)state->document_type);
    Py_DECREF(parent);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (insert && view_discard(PyObject_CallMethod((PyObject *)node, "insert_before", "O", holder)) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        Py_DECREF(holder); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    view_mark_holder((NodeObject *)holder);
    return holder;
}

static int view_detach(NodeObject *node, int with_tail, int keep_origin) {
    if (keep_origin && view_remember_origin(node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *tail = with_tail ? view_text_nodes(node, 1) : PyList_New(0);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *holder = view_holder(node);
    if (holder == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(tail);  /* GCOVR_EXCL_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = view_discard(PyObject_CallMethod(holder, "append", "O", node));
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(tail); index++) {
        /* GCOVR_EXCL_BR_STOP */
        status = view_discard(PyObject_CallMethod(holder, "append", "O", PyList_GET_ITEM(tail, index)));
    }
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status == 0) {
        /* GCOVR_EXCL_BR_STOP */
        status = view_discard(node_extract(holder, NULL));
    }
    Py_DECREF(holder);
    Py_DECREF(tail);
    return status;
}

static int view_set_text(PyObject *self, PyObject *value, void *closure) {
    if (value == NULL || (value != Py_None && !PyUnicode_Check(value))) {
        PyErr_SetString(PyExc_TypeError, "text must be a str or None");
        return -1;
    }
    NodeObject *node = ((ElementView *)self)->node;
    PyObject *previous = view_text_nodes(node, closure != NULL);
    if (previous == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;          /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(previous); index++) {
        /* GCOVR_EXCL_BR_STOP */
        status = view_discard(node_extract(PyList_GET_ITEM(previous, index), NULL));
    }
    Py_DECREF(previous);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status < 0 || value == Py_None) {
        /* GCOVR_EXCL_BR_STOP */
        return status;
    }
    module_state *state = state_of((PyObject *)node);
    if (closure != NULL) {
        PyObject *parent = PyObject_GetAttrString((PyObject *)node, "parent");
        if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;        /* GCOVR_EXCL_LINE: allocation failure */
        }
        int detached = parent == Py_None;
        int document = Py_IS_TYPE(parent, (PyTypeObject *)state->document_type);
        Py_DECREF(parent);
        if (document) {
            return 0;
        }
        if (detached && view_detach(node, 1, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                 /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    PyObject *text = PyObject_CallOneArg(state->text_type, value);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    status = view_discard(closure == NULL ? PyObject_CallMethod((PyObject *)node, "insert", "iO", 0, text)
                                          : PyObject_CallMethod((PyObject *)node, "insert_after", "O", text));
    Py_DECREF(text);
    return status;
}

static NodeObject *view_argument(PyObject *self, PyObject *value) {
    module_state *state = state_of((PyObject *)((ElementView *)self)->node);
    if (!Py_IS_TYPE(value, (PyTypeObject *)state->element_view_type)) {
        PyErr_SetString(PyExc_TypeError, "expected an ElementView");
        return NULL;
    }
    return ((ElementView *)value)->node;
}

static PyObject *view_moving_nodes(NodeObject *node) {
    PyObject *tail = view_text_nodes(node, 1);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyTuple_New(PyList_GET_SIZE(tail) + 1);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result != NULL) {
        /* GCOVR_EXCL_BR_STOP */
        PyTuple_SET_ITEM(result, 0, Py_NewRef((PyObject *)node));
        for (Py_ssize_t index = 0; index < PyList_GET_SIZE(tail); index++) {
            PyTuple_SET_ITEM(result, index + 1, Py_NewRef(PyList_GET_ITEM(tail, index)));
        }
    }
    Py_DECREF(tail);
    return result;
}

static PyObject *view_append(PyObject *self, PyObject *child) {
    NodeObject *node = view_argument(self, child);
    if (node == NULL) {
        return NULL;
    }
    PyObject *moving = view_moving_nodes(node);
    if (moving == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = view_discard(PyObject_CallMethod((PyObject *)((ElementView *)self)->node, "append", "O", node));
    if (status == 0 && PyTuple_GET_SIZE(moving) > 1) {
        PyObject *tail = PyTuple_GetSlice(moving, 1, PyTuple_GET_SIZE(moving));
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        status = tail == NULL ? -1 : view_discard(node_insert_after((PyObject *)node, tail));
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(tail);
    }
    Py_DECREF(moving);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_extend(PyObject *self, PyObject *children) {
    PyObject *items = PySequence_List(children);
    if (items == NULL) {
        return NULL;
    }
    int status = 0;
    for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(items); index++) {
        status = view_discard(view_append(self, PyList_GET_ITEM(items, index)));
    }
    Py_DECREF(items);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static int view_check_child(PyObject *self, NodeObject *child) {
    PyObject *parent = PyObject_GetAttrString((PyObject *)child, "parent");
    if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    int valid = PyObject_RichCompareBool(parent, (PyObject *)((ElementView *)self)->node, Py_EQ);
    Py_DECREF(parent);
    if (!valid) {
        PyErr_SetString(PyExc_ValueError, "Element is not a child of this node.");
        return -1;
    }
    return 0;
}

static PyObject *view_remove(PyObject *self, PyObject *child) {
    NodeObject *node = view_argument(self, child);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (node == NULL || view_check_child(self, node) < 0 || view_detach(node, 1, 1) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_replace(PyObject *self, PyObject *args) {
    PyObject *old;
    PyObject *replacement;
    if (!PyArg_ParseTuple(args, "OO:replace", &old, &replacement)) {
        return NULL;
    }
    NodeObject *node = view_argument(self, old);
    if (node == NULL || view_check_child(self, node) < 0) {
        return NULL;
    }
    NodeObject *incoming = view_argument(self, replacement);
    if (incoming == NULL) {
        return NULL;
    }
    PyObject *moving = view_moving_nodes(incoming);
    if (moving == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = view_discard(node_insert_before((PyObject *)node, moving));
    Py_DECREF(moving);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status < 0 || view_detach(node, 1, 1) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_drop_tag(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    if (view_discard(node_unwrap((PyObject *)((ElementView *)self)->node, NULL)) < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_copy(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *clone = node_deepcopy((PyObject *)owner, NULL);
    if (clone == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_wrap(state_of((PyObject *)owner), clone);
    Py_DECREF(clone);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *tail = view_text(self, (void *)1);
    if (tail == NULL || view_set_text(result, tail, (void *)1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(tail);                                             /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(result);                                            /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                                                  /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_DECREF(tail);
    return result;
}

static int view_is_holder(th_node *node) {
    return node->type == TH_NODE_ELEMENT && (node->tag_flags & TH_ELEM_TAIL_HOLDER) != 0;
}

TH_NODE_API(static, PyObject *, view_top, (NodeObject * owner), (owner), (NodeObject * owner), owner, NULL) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    th_node *node = owner->node;
    while (node->parent != NULL && !view_is_holder(node->parent)) {
        node = node->parent;
    }
    result = node_wrap_locked(state_of((PyObject *)owner), owner->handle, node);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *view_origin(NodeObject *node) {
    PyObject *top = view_top(node);
    if (top == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *origins;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (PyContextVar_Get(state_of((PyObject *)node)->element_view_origins, Py_None, &origins) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        Py_DECREF(top); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (origins != Py_None) {
        PyObject *origin = PyDict_GetItem(origins, top);
        if (origin != NULL) {
            Py_SETREF(top, Py_NewRef(origin));
        }
    }
    Py_DECREF(origins);
    return top;
}

typedef struct {
    PyObject_HEAD PyObject *root;
} ViewRoot;

static void view_root_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    view_root_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static int view_root_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));            /* GCOVR_EXCL_BR_LINE: non-NULL type; Python cannot induce visit errors */
    Py_VISIT(((ViewRoot *)self)->root); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    return 0;
}

static int view_root_clear(PyObject *self) {
    Py_CLEAR(((ViewRoot *)self)->root); /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    return 0;
}

static PyObject *view_root_getroot(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return Py_NewRef(((ViewRoot *)self)->root);
}

static PyObject *view_getroottree(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    NodeObject *node = ((ElementView *)self)->node;
    module_state *state = state_of((PyObject *)node);
    PyObject *origin = view_origin(node);
    if (origin == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (Py_IS_TYPE(origin, (PyTypeObject *)state->document_type)) {
        PyObject *root = PyObject_GetAttrString(origin, "root");
        Py_DECREF(origin);
        origin = root;
    }
    if (origin == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *root = view_wrap(state, origin);
    Py_DECREF(origin);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyTypeObject *type = (PyTypeObject *)state->element_view_root_type;
    ViewRoot *result = (ViewRoot *)type->tp_alloc(type, 0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(root);  /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    result->root = root;
    return (PyObject *)result;
}

static PyMethodDef view_root_methods[] = {
    {"getroot", view_root_getroot, METH_NOARGS, "getroot()\n--\n\n"},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot view_root_slots[] = {
    {Py_tp_dealloc, view_root_dealloc},
    {Py_tp_traverse, view_root_traverse},
    {Py_tp_clear, view_root_clear},
    {Py_tp_methods, view_root_methods},
    TH_SEALED_END,
};

static PyType_Spec view_root_spec = {
    .name = "turbohtml.etree.ElementTree",
    .basicsize = sizeof(ViewRoot),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = view_root_slots,
};

typedef struct {
    PyObject_HEAD PyObject *path;
    PyObject *compiled;
    PyObject *relative;
} ViewXPath;

static void view_xpath_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    view_xpath_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static int view_xpath_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));                 /* GCOVR_EXCL_BR_LINE: non-NULL type; Python cannot induce visit errors */
    Py_VISIT(((ViewXPath *)self)->path);     /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    Py_VISIT(((ViewXPath *)self)->compiled); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    Py_VISIT(((ViewXPath *)self)->relative); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    return 0;
}

static int view_xpath_clear(PyObject *self) {
    Py_CLEAR(((ViewXPath *)self)->path);     /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    Py_CLEAR(((ViewXPath *)self)->compiled); /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    Py_CLEAR(((ViewXPath *)self)->relative); /* GCOVR_EXCL_BR_LINE: NULL without a "//" rewrite; llvm-cov extra arc */
    return 0;
}

static PyObject *view_relative_path(PyObject *path) {
    Py_ssize_t length = PyUnicode_GET_LENGTH(path);
    PyObject *parts = PyList_New(0);
    if (parts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    int quote = 0;
    int start = 1;
    Py_ssize_t previous = 0;
    int status = 0;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    for (Py_ssize_t index = 0; status == 0 && index < length; index++) {
        /* GCOVR_EXCL_BR_STOP */
        Py_UCS4 character = PyUnicode_READ_CHAR(path, index);
        if (quote) {
            if (character == (Py_UCS4)quote) {
                quote = 0;
            }
            continue;
        }
        if (character == '\'' || character == '"') {
            quote = (int)character;
            start = 0;
            continue;
        }
        if (start && character == '/' && index + 1 < length && PyUnicode_READ_CHAR(path, index + 1) == '/') {
            PyObject *prefix = PyUnicode_Substring(path, previous, index);
            PyObject *replacement = PyUnicode_FromString("descendant-or-self::node()/");
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            status = prefix == NULL || replacement == NULL ? -1 : PyList_Append(parts, prefix);
            if (status == 0) {
                /* GCOVR_EXCL_BR_STOP */
                status = PyList_Append(parts, replacement);
            }
            Py_XDECREF(prefix);
            Py_XDECREF(replacement);
            previous = ++index + 1;
        }
        if (!Py_UNICODE_ISSPACE(character)) {
            start = character == '(' || character == '|';
        }
    }
    if (status < 0) {     /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(parts); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (PyList_GET_SIZE(parts) == 0) {
        Py_DECREF(parts);
        return Py_NewRef(Py_None);
    }
    PyObject *suffix = PyUnicode_Substring(path, previous, length);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    status = suffix == NULL ? -1 : PyList_Append(parts, suffix);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(suffix);
    PyObject *separator = PyUnicode_FromString("");
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *result = status < 0 || separator == NULL ? NULL : PyUnicode_Join(separator, parts);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(separator);
    Py_DECREF(parts);
    return result;
}

static PyObject *view_xpath_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"path", NULL};
    PyObject *path;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U:XPath", keywords, &path)) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    PyObject *compiled = PyObject_CallOneArg(state->xpath_type, path);
    if (compiled == NULL) {
        return NULL;
    }
    PyObject *rewritten = view_relative_path(path);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *relative =
        rewritten == NULL || rewritten == Py_None ? NULL : PyObject_CallOneArg(state->xpath_type, rewritten);
    int failed = rewritten == NULL || (rewritten != Py_None && relative == NULL);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(rewritten);
    if (failed) {            /* GCOVR_EXCL_BR_LINE: allocation failure after a valid XPath */
        Py_DECREF(compiled); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    ViewXPath *result = (ViewXPath *)type->tp_alloc(type, 0);
    if (result == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(compiled);  /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(relative); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
    }
    result->path = Py_NewRef(path);
    result->compiled = compiled;
    result->relative = relative;
    return (PyObject *)result;
}

static PyObject *view_wrap_result(module_state *state, PyObject *result) {
    if (result == NULL || !PyList_Check(result)) {
        return result;
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(result); index++) {
        PyObject *item = PyList_GET_ITEM(result, index);
        if (Py_IS_TYPE(item, (PyTypeObject *)state->element_type)) {
            PyObject *wrapped = view_wrap(state, item);
            if (wrapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
            }
            PyList_SET_ITEM(result, index, wrapped);
            Py_DECREF(item);
        }
    }
    return result;
}

static PyObject *view_xpath_eval(ViewXPath *expression, ElementView *element, PyObject *variables) {
    module_state *state = state_of((PyObject *)element->node);
    PyObject *node = expression->relative == NULL ? Py_NewRef((PyObject *)element->node) : view_origin(element->node);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *compiled = expression->relative != NULL && !Py_IS_TYPE(node, (PyTypeObject *)state->document_type)
                             ? expression->relative
                             : expression->compiled;
    PyObject *args = PyTuple_Pack(1, node);
    Py_DECREF(node);
    if (args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_Call(compiled, args, variables);
    Py_DECREF(args);
    return view_wrap_result(state, result);
}

static PyObject *view_xpath_call(PyObject *self, PyObject *args, PyObject *kwargs) {
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    PyObject *element;
    if (!PyArg_ParseTuple(args, "O!:XPath", state->element_view_type, &element)) {
        return NULL;
    }
    return view_xpath_eval((ViewXPath *)self, (ElementView *)element, kwargs);
}

static PyObject *view_xpath_path(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(((ViewXPath *)self)->path);
}

static PyObject *view_xpath_str(PyObject *self) {
    return Py_NewRef(((ViewXPath *)self)->path);
}

static PyObject *view_compiled(module_state *state, PyObject *path) {
    if (!PyUnicode_Check(path)) {
        PyErr_SetString(PyExc_TypeError, "XPath expression must be a str");
        return NULL;
    }
    PyObject *key = PyUnicode_FromObject(path);
    if (key == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure copying a string subclass */
        return NULL;   /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(state->element_view_xpath_cache);
    result = PyDict_GetItem(state->element_view_xpath_cache, key);
    Py_XINCREF(result);
    Py_END_CRITICAL_SECTION();
    if (result != NULL) {
        Py_DECREF(key);
        return result;
    }
    result = PyObject_CallOneArg(state->element_view_xpath_type, key);
    if (result == NULL) {
        Py_DECREF(key);
        return NULL;
    }
    Py_BEGIN_CRITICAL_SECTION(state->element_view_xpath_cache);
    if (PyDict_Size(state->element_view_xpath_cache) >= 128) {
        PyDict_Clear(state->element_view_xpath_cache);
    }
    /* GCOVR_EXCL_BR_START: allocation failure */
    if (PyDict_SetItem(state->element_view_xpath_cache, key, result) < 0) {
        Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
        /* GCOVR_EXCL_BR_STOP */
    } /* GCOVR_EXCL_LINE: allocation failure */
    Py_END_CRITICAL_SECTION();
    Py_DECREF(key);
    return result;
}

static PyObject *view_query(PyObject *self, PyObject *path, PyObject *variables) {
    PyObject *compiled = view_compiled(state_of((PyObject *)((ElementView *)self)->node), path);
    if (compiled == NULL) {
        return NULL;
    }
    PyObject *result = view_xpath_eval((ViewXPath *)compiled, (ElementView *)self, variables);
    Py_DECREF(compiled);
    return result;
}

static PyObject *view_xpath(PyObject *self, PyObject *args, PyObject *kwargs) {
    PyObject *path;
    if (!PyArg_ParseTuple(args, "U:xpath", &path)) {
        return NULL;
    }
    return view_query(self, path, kwargs);
}

static PyObject *view_findall(PyObject *self, PyObject *path) {
    PyObject *items = view_query(self, path, NULL);
    if (items == NULL) {
        return NULL;
    }
    PyObject *result = PyList_New(0);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result != NULL && PyList_Check(items)) {
        /* GCOVR_EXCL_BR_STOP */
        module_state *state = state_of((PyObject *)((ElementView *)self)->node);
        for (Py_ssize_t index = 0; index < PyList_GET_SIZE(items); index++) {
            PyObject *item = PyList_GET_ITEM(items, index);
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            if (Py_IS_TYPE(item, (PyTypeObject *)state->element_view_type) && PyList_Append(result, item) < 0) {
                /* GCOVR_EXCL_BR_STOP */
                Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
                break;            /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
    }
    Py_DECREF(items);
    return result;
}

static PyObject *view_find(PyObject *self, PyObject *path) {
    PyObject *items = view_findall(self, path);
    if (items == NULL) {
        return NULL;
    }
    PyObject *result = Py_NewRef(PyList_GET_SIZE(items) ? PyList_GET_ITEM(items, 0) : Py_None);
    Py_DECREF(items);
    return result;
}

static PyObject *view_iterfind(PyObject *self, PyObject *path) {
    PyObject *items = view_findall(self, path);
    if (items == NULL) {
        return NULL;
    }
    PyObject *result = PyObject_GetIter(items);
    Py_DECREF(items);
    return result;
}

static PyGetSetDef view_xpath_getset[] = {
    {"path", view_xpath_path, NULL, NULL, NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyType_Slot view_xpath_slots[] = {
    {Py_tp_new, view_xpath_new},           {Py_tp_dealloc, view_xpath_dealloc},
    {Py_tp_traverse, view_xpath_traverse}, {Py_tp_clear, view_xpath_clear},
    {Py_tp_call, view_xpath_call},         {Py_tp_str, view_xpath_str},
    {Py_tp_getset, view_xpath_getset},     {0, NULL},
};

static PyType_Spec view_xpath_spec = {
    .name = "turbohtml.etree.XPath",
    .basicsize = sizeof(ViewXPath),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .slots = view_xpath_slots,
};

static int view_remember_origin(NodeObject *node) {
    PyObject *origins;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (PyContextVar_Get(state_of((PyObject *)node)->element_view_origins, Py_None, &origins) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return -1; /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    if (origins != Py_None) {
        PyObject *origin = view_origin(node);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        status = origin == NULL ? -1 : PyDict_SetItem(origins, (PyObject *)node, origin);
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(origin);
    }
    Py_DECREF(origins);
    return status;
}

typedef struct {
    PyObject_HEAD PyObject *function;
} ViewContext;

static int view_context_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self)); /* GCOVR_EXCL_BR_LINE: non-NULL type; Python cannot induce visit errors */
    Py_VISIT(((ViewContext *)self)->function); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    return 0;
}

static int view_context_clear(PyObject *self) {
    Py_CLEAR(((ViewContext *)self)->function); /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    return 0;
}

static void view_context_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    view_context_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *view_context_call(PyObject *self, PyObject *args, PyObject *kwargs) {
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    PyObject *origins;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (PyContextVar_Get(state->element_view_origins, Py_None, &origins) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    int nested = origins != Py_None;
    Py_DECREF(origins);
    if (nested) {
        return PyObject_Call(((ViewContext *)self)->function, args, kwargs);
    }
    origins = PyDict_New();
    if (origins == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *token = PyContextVar_Set(state->element_view_origins, origins);
    Py_DECREF(origins);
    if (token == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_Call(((ViewContext *)self)->function, args, kwargs);
    PyObject *error_type;
    PyObject *error_value;
    PyObject *error_traceback;
    PyErr_Fetch(&error_type, &error_value, &error_traceback);
    int status = PyContextVar_Reset(state->element_view_origins, token);
    Py_DECREF(token);
    if (status < 0) {                /* GCOVR_EXCL_BR_LINE: allocation failure with this context's unused token */
        Py_XDECREF(result);          /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(error_type);      /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(error_value);     /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(error_traceback); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                 /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyErr_Restore(error_type, error_value, error_traceback);
    return result;
}

static PyObject *view_context_getattr(PyObject *self, PyObject *name) {
    if (PyUnicode_CompareWithASCIIString(name, "__wrapped__") == 0) {
        return Py_NewRef(((ViewContext *)self)->function);
    }
    PyObject *result = PyObject_GenericGetAttr(self, name);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result == NULL && PyErr_ExceptionMatches(PyExc_AttributeError)) {
        /* GCOVR_EXCL_BR_STOP */
        PyErr_Clear();
        return PyObject_GetAttr(((ViewContext *)self)->function, name);
    }
    return result;
}

static PyObject *view_document_context(PyObject *module, PyObject *function) {
    if (!PyCallable_Check(function)) {
        PyErr_SetString(PyExc_TypeError, "document_context requires a callable");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyTypeObject *type = (PyTypeObject *)state->element_view_context_type;
    ViewContext *result = (ViewContext *)type->tp_alloc(type, 0);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result != NULL) {
        /* GCOVR_EXCL_BR_STOP */
        result->function = Py_NewRef(function);
    }
    return (PyObject *)result;
}

static PyType_Slot view_context_slots[] = {
    {Py_tp_dealloc, view_context_dealloc}, {Py_tp_traverse, view_context_traverse}, {Py_tp_clear, view_context_clear},
    {Py_tp_call, view_context_call},       {Py_tp_getattro, view_context_getattr},  TH_SEALED_END,
};

static PyType_Spec view_context_spec = {
    .name = "turbohtml.etree.DocumentContext",
    .basicsize = sizeof(ViewContext),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = view_context_slots,
};

static PyMethodDef view_module_methods[] = {
    {"_elementtree_document_fromstring", view_document_fromstring, METH_O, NULL},
    {"_elementtree_fromstring", view_fromstring, METH_O, NULL},
    {"_elementtree_fragment_fromstring", (PyCFunction)(void (*)(void))view_fragment_fromstring,
     METH_VARARGS | METH_KEYWORDS, NULL},
    {"_elementtree_context", view_document_context, METH_O, NULL},
    {"_elementtree_element", (PyCFunction)(void (*)(void))view_element, METH_VARARGS | METH_KEYWORDS, NULL},
    {"_elementtree_subelement", (PyCFunction)(void (*)(void))view_subelement, METH_VARARGS | METH_KEYWORDS, NULL},
    {"_elementtree_strip_tags", view_strip_tags, METH_VARARGS, NULL},
    {"_elementtree_strip_elements", (PyCFunction)(void (*)(void))view_strip_elements, METH_VARARGS | METH_KEYWORDS,
     NULL},
    {"_elementtree_tostring", (PyCFunction)(void (*)(void))view_tostring, METH_VARARGS | METH_KEYWORDS, NULL},
    {"_elementtree_from_lxml", view_from_lxml, METH_O, NULL},
    {"_elementtree_to_lxml", view_to_lxml, METH_O, NULL},
    {"_elementtree_to_lxml_html", view_to_lxml_html, METH_O, NULL},
    {NULL, NULL, 0, NULL},
};

TH_NODE_API(static, PyObject *, view_relation, (PyObject * self, int axis), (self, axis), (PyObject * self, int axis),
            ((ElementView *)self)->node, NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *node;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    th_node *relative = axis == 0   ? owner->node->parent
                        : axis == 1 ? owner->node->next_sibling
                                    : owner->node->prev_sibling;
    if (axis != 0) {
        while (relative != NULL && relative->type != TH_NODE_ELEMENT) {
            relative = axis == 1 ? relative->next_sibling : relative->prev_sibling;
        }
    } else if (relative != NULL && (relative->type != TH_NODE_ELEMENT || view_is_holder(relative))) {
        relative = NULL;
    }
    node = node_wrap_locked(state_of((PyObject *)owner), owner->handle, relative);
    Py_END_CRITICAL_SECTION();
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_wrap(state_of((PyObject *)owner), node);
    Py_DECREF(node);
    return result;
}

static PyObject *view_parent(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_relation(self, 0);
}

static PyObject *view_next(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_relation(self, 1);
}

static PyObject *view_previous(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return view_relation(self, 2);
}

TH_NODE_API(static, PyObject *, view_children, (PyObject * self, PyObject *ignored), (self, ignored),
            (PyObject * self, PyObject *Py_UNUSED(ignored)), ((ElementView *)self)->node, NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    for (th_node *child = owner->node->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT) {
            continue;
        }
        PyObject *node = node_wrap_locked(state_of((PyObject *)owner), owner->handle, child);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        PyObject *wrapped = node == NULL ? NULL : view_wrap(state_of((PyObject *)owner), node);
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(node);
        if (wrapped == NULL || PyList_Append(result, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_XDECREF(wrapped);                                     /* GCOVR_EXCL_LINE: allocation failure */
            Py_CLEAR(result);                                        /* GCOVR_EXCL_LINE: allocation failure */
            break;                                                   /* GCOVR_EXCL_LINE: allocation failure */
        }
        Py_DECREF(wrapped);
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *view_subscript(PyObject *self, PyObject *key) {
    PyObject *children = view_children(self, NULL);
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_GetItem(children, key);
    Py_DECREF(children);
    return result;
}

static PyObject *view_index(PyObject *self, PyObject *child) {
    PyObject *children = view_children(self, NULL);
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t index = PySequence_Index(children, child);
    Py_DECREF(children);
    return index < 0 ? NULL : PyLong_FromSsize_t(index);
}

typedef struct {
    PyObject_HEAD PyObject *iterator;
    PyObject *filter;
    PyObject *current;
    int axis;
#ifdef Py_GIL_DISABLED
    PyThread_type_lock lock;
#endif
} ViewIterator;

static void view_iter_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    view_iter_clear(self);
#ifdef Py_GIL_DISABLED
    if (((ViewIterator *)self)->lock != NULL) {
        PyThread_free_lock(((ViewIterator *)self)->lock);
    }
#endif
    type->tp_free(self);
    Py_DECREF(type);
}

static int view_iter_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self)); /* GCOVR_EXCL_BR_LINE: non-NULL type; Python cannot induce visit errors */
    Py_VISIT(((ViewIterator *)self)->iterator); /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    Py_VISIT(((ViewIterator *)self)->filter);   /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    Py_VISIT(((ViewIterator *)self)->current);  /* GCOVR_EXCL_BR_LINE: Python cannot induce visit errors */
    return 0;
}

static int view_iter_clear(PyObject *self) {
    Py_CLEAR(((ViewIterator *)self)->iterator);
    Py_CLEAR(((ViewIterator *)self)->filter); /* GCOVR_EXCL_BR_LINE: set on construction, cleared on destruction */
    Py_CLEAR(((ViewIterator *)self)->current);
    return 0;
}

static int view_matches(PyObject *node, PyObject *filter) {
    if (filter == Py_None) {
        return 1;
    }
    PyObject *tag = PyObject_GetAttrString(node, "tag");
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    int result = PySequence_Contains(filter, tag);
    Py_DECREF(tag);
    return result;
}

static PyObject *view_iter_next_locked(PyObject *self) {
    ViewIterator *iterator = (ViewIterator *)self;
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    if (iterator->iterator == NULL) {
        while (iterator->current != Py_None) {
            PyObject *current = iterator->current;
            PyObject *following = view_relation(current, iterator->axis);
            if (following == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
            }
            iterator->current = following;
            int matches = view_matches((PyObject *)((ElementView *)current)->node, iterator->filter);
            if (matches > 0) {
                return current;
            }
            Py_DECREF(current);
            if (matches < 0) { /* GCOVR_EXCL_BR_LINE: native tag allocation; filters contain exact strings */
                return NULL;   /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        return NULL;
    }
    PyObject *node = PyIter_Next(iterator->iterator);
    if (node == NULL) {
        return NULL;
    }
    PyObject *result = view_wrap(state, node);
    Py_DECREF(node);
    return result;
}

static PyObject *view_iter_next(PyObject *self) {
#ifdef Py_GIL_DISABLED
    Py_BEGIN_ALLOW_THREADS PyThread_acquire_lock(((ViewIterator *)self)->lock, WAIT_LOCK);
    Py_END_ALLOW_THREADS
#endif
        PyObject *result = view_iter_next_locked(self);
#ifdef Py_GIL_DISABLED
    PyThread_release_lock(((ViewIterator *)self)->lock);
#endif
    return result;
}

static ViewIterator *view_iter_allocate(module_state *state) {
    PyTypeObject *type = (PyTypeObject *)state->element_view_iterator_type;
    ViewIterator *result = (ViewIterator *)type->tp_alloc(type, 0);
#ifdef Py_GIL_DISABLED
    if (result != NULL && (result->lock = PyThread_allocate_lock()) == NULL) {
        Py_DECREF(result);
        PyErr_NoMemory();
        return NULL;
    }
#endif
    return result;
}

static PyObject *view_filter(PyObject *args) {
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (Py_ssize_t index = 0; index < PyTuple_GET_SIZE(args); index++) {
        PyObject *item = PyTuple_GET_ITEM(args, index);
        if (item == Py_None) {
            continue;
        }
        PyObject *names = PyUnicode_Check(item) ? PyTuple_Pack(1, item) : PySequence_Tuple(item);
        if (names == NULL) {
            Py_DECREF(result);
            return NULL;
        }
        for (Py_ssize_t offset = 0; offset < PyTuple_GET_SIZE(names); offset++) {
            PyObject *name = PyTuple_GET_ITEM(names, offset);
            if (!PyUnicode_Check(name)) {
                Py_DECREF(names);
                Py_DECREF(result);
                PyErr_SetString(PyExc_TypeError, "tags must be strings");
                return NULL;
            }
            if (PyUnicode_CompareWithASCIIString(name, "*") == 0) {
                Py_DECREF(names);
                Py_DECREF(result);
                return Py_NewRef(Py_None);
            }
            PyObject *plain = PyUnicode_FromObject(name);
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            int status = plain == NULL ? -1 : PyList_Append(result, plain);
            /* GCOVR_EXCL_BR_STOP */
            Py_XDECREF(plain);
            if (status < 0) {      /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_DECREF(names);  /* GCOVR_EXCL_LINE: allocation failure */
                Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation failure */
                return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
            }
        }
        Py_DECREF(names);
    }
    if (PyList_GET_SIZE(result) == 0) {
        Py_DECREF(result);
        return Py_NewRef(Py_None);
    }
    return result;
}

static PyObject *view_iterator(PyObject *self, PyObject *args, int include_self) {
    PyObject *filter = view_filter(args);
    if (filter == NULL) {
        return NULL;
    }
    PyObject *iterator =
        PyObject_CallMethod((PyObject *)((ElementView *)self)->node, "iter_elements", "Oi", filter, include_self);
    Py_DECREF(filter);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    module_state *state = state_of((PyObject *)((ElementView *)self)->node);
    ViewIterator *result = view_iter_allocate(state);
    if (result == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(iterator); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    result->iterator = iterator;
    result->filter = Py_NewRef(Py_None);
    return (PyObject *)result;
}

static PyObject *view_iter(PyObject *self, PyObject *args) {
    return view_iterator(self, args, 1);
}

static PyObject *view_descendants(PyObject *self, PyObject *args) {
    return view_iterator(self, args, 0);
}

static PyObject *view_axis_iterator(PyObject *self, PyObject *args, int axis, int children) {
    PyObject *filter = view_filter(args);
    if (filter == NULL) {
        return NULL;
    }
    PyObject *first;
    if (children) {
        PyObject *items = view_children(self, NULL);
        if (items == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(filter); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        first = Py_NewRef(
            PyList_GET_SIZE(items) == 0 ? Py_None : PyList_GET_ITEM(items, axis == 1 ? 0 : PyList_GET_SIZE(items) - 1));
        Py_DECREF(items);
    } else {
        first = view_relation(self, axis);
    }
    if (first == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(filter); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    module_state *state = state_of((PyObject *)((ElementView *)self)->node);
    ViewIterator *result = view_iter_allocate(state);
    if (result == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(first);  /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(filter); /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    result->current = first;
    result->filter = filter;
    result->axis = axis;
    return (PyObject *)result;
}

static PyObject *view_children_iter(PyObject *self) {
    PyObject *args = PyTuple_New(0);
    if (args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_axis_iterator(self, args, 1, 1);
    Py_DECREF(args);
    return result;
}

static int view_option(PyObject *kwargs, const char *keyword, int fallback) {
    if (kwargs == NULL) {
        return fallback;
    }
    PyObject *key;
    PyObject *value;
    Py_ssize_t position = 0;
    int result = fallback;
    while (PyDict_Next(kwargs, &position, &key, &value)) {
        if (PyUnicode_CompareWithASCIIString(key, keyword) != 0) {
            PyErr_Format(PyExc_TypeError, "unexpected keyword argument %R", key);
            return -1;
        }
        result = PyObject_IsTrue(value);
        if (result < 0) {
            return -1;
        }
    }
    return result;
}

static PyObject *view_iterchildren(PyObject *self, PyObject *args, PyObject *kwargs) {
    int reverse = view_option(kwargs, "reversed", 0);
    return reverse < 0 ? NULL : view_axis_iterator(self, args, reverse ? 2 : 1, 1);
}

static PyObject *view_itersiblings(PyObject *self, PyObject *args, PyObject *kwargs) {
    int preceding = view_option(kwargs, "preceding", 0);
    return preceding < 0 ? NULL : view_axis_iterator(self, args, preceding ? 2 : 1, 0);
}

static PyObject *view_iterancestors(PyObject *self, PyObject *args) {
    return view_axis_iterator(self, args, 0, 0);
}

TH_NODE_API(static, PyObject *, view_collect_text, (PyObject * self, PyObject *filter, int with_tail),
            (self, filter, with_tail), (PyObject * self, PyObject *filter, int with_tail), ((ElementView *)self)->node,
            NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    th_node *root = owner->node;
    th_node *node = root->first_child;
    while (node != NULL) {
        if (node->type == TH_NODE_TEXT) {
            th_node *previous = node->prev_sibling;
            th_node *selected = previous == NULL ? node->parent : previous;
            int matches = previous == NULL || with_tail;
            if (matches && filter != Py_None && selected->type == TH_NODE_ELEMENT) {
                PyObject *tag = ucs4_to_str(selected->text, selected->text_len);
                /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
                matches = tag == NULL ? -1 : PySequence_Contains(filter, tag);
                /* GCOVR_EXCL_BR_STOP */
                Py_XDECREF(tag);
            }
            if (matches < 0) {    /* GCOVR_EXCL_BR_LINE: native tag allocation; filters contain exact strings */
                Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
                break;            /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (matches) {
                PyObject *text = view_text_run(owner, node);
                if (text == NULL || PyList_Append(result, text) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    Py_XDECREF(text);                                  /* GCOVR_EXCL_LINE: allocation failure */
                    Py_CLEAR(result);                                  /* GCOVR_EXCL_LINE: allocation failure */
                    break;                                             /* GCOVR_EXCL_LINE: allocation failure */
                }
                Py_DECREF(text);
            }
            while (node->next_sibling != NULL && node->next_sibling->type == TH_NODE_TEXT) {
                node = node->next_sibling;
            }
        } else if (node->type == TH_NODE_ELEMENT && node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node->next_sibling == NULL && node->parent != root) {
            node = node->parent;
        }
        node = node->next_sibling;
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *view_itertext(PyObject *self, PyObject *args, PyObject *kwargs) {
    int with_tail = view_option(kwargs, "with_tail", 1);
    if (with_tail < 0) {
        return NULL;
    }
    PyObject *filter = view_filter(args);
    if (filter == NULL) {
        return NULL;
    }
    PyObject *items = view_collect_text(self, filter, with_tail);
    Py_DECREF(filter);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_GetIter(items);
    Py_DECREF(items);
    return result;
}

static PyObject *view_insert(PyObject *self, PyObject *args) {
    Py_ssize_t index;
    PyObject *child;
    if (!PyArg_ParseTuple(args, "nO:insert", &index, &child)) {
        return NULL;
    }
    NodeObject *node = view_argument(self, child);
    if (node == NULL) {
        return NULL;
    }
    PyObject *children = view_children(self, NULL);
    if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t length = PyList_GET_SIZE(children);
    if (index < 0) {
        index = index < -length ? 0 : length + index;
    }
    if (index >= length) {
        Py_DECREF(children);
        return view_append(self, child);
    }
    PyObject *moving = view_moving_nodes(node);
    NodeObject *anchor = ((ElementView *)PyList_GET_ITEM(children, index))->node;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    int status = moving == NULL ? -1 : view_discard(node_insert_before((PyObject *)anchor, moving));
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(moving);
    Py_DECREF(children);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_clear(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"keep_tail", NULL};
    int keep_tail = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|p:clear", keywords, &keep_tail)) {
        return NULL;
    }
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (view_discard(view_attribute_list(self, "clear")) < 0 ||
        (!keep_tail && view_set_text(self, Py_None, (void *)1) < 0) ||
        view_discard(PyObject_CallMethod((PyObject *)((ElementView *)self)->node, "clear", NULL)) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return NULL; /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_RETURN_NONE;
}

static PyObject *view_addprevious(PyObject *self, PyObject *child) {
    NodeObject *node = view_argument(self, child);
    if (node == NULL) {
        return NULL;
    }
    PyObject *moving = view_moving_nodes(node);
    if (moving == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = view_discard(node_insert_before((PyObject *)((ElementView *)self)->node, moving));
    Py_DECREF(moving);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_addnext(PyObject *self, PyObject *child) {
    NodeObject *node = view_argument(self, child);
    if (node == NULL) {
        return NULL;
    }
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *parent = PyObject_GetAttrString((PyObject *)owner, "parent");
    if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    int document = Py_IS_TYPE(parent, (PyTypeObject *)state_of((PyObject *)owner)->document_type);
    Py_DECREF(parent);
    if (document) {
        Py_RETURN_NONE;
    }
    PyObject *tail = view_text_nodes(owner, 1);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *moving = view_moving_nodes(node);
    PyObject *anchor = PyList_GET_SIZE(tail) ? PyList_GET_ITEM(tail, PyList_GET_SIZE(tail) - 1) : (PyObject *)owner;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    int status = moving == NULL ? -1 : view_discard(node_insert_after(anchor, moving));
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(moving);
    Py_DECREF(tail);
    if (status < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *view_drop_tree(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    NodeObject *node = ((ElementView *)self)->node;
    PyObject *tail = view_text(self, (void *)1);
    if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = view_detach(node, 0, 1);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status == 0) {
        /* GCOVR_EXCL_BR_STOP */
        status = view_set_text(self, tail, (void *)1);
    }
    Py_DECREF(tail);
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;  /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_RETURN_NONE;
}

static PyObject *view_normalize_name(PyObject *name, int colon) {
    Py_UCS4 *text = PyUnicode_AsUCS4Copy(name);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(name);
    for (Py_ssize_t index = 0; index < length; index++) {
        Py_UCS4 character = text[index];
        int valid = index == 0 ? (character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
                                     character == '_'
                               : Py_UNICODE_ISALNUM(character) || character == '_' || character == '.' ||
                                     character == '-' || (colon && character == ':');
        if (!valid) {
            text[index] = '_';
        }
    }
    PyObject *result = ucs4_to_str(text, length);
    PyMem_Free(text);
    return result;
}

static PyObject *view_create(module_state *state, PyObject *tag, PyObject *attrs) {
    PyObject *node = PyObject_CallOneArg(state->element_type, tag);
    if (node == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {
        PyErr_Clear();
        PyObject *normalized = view_normalize_name(tag, 1);
        if (normalized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;          /* GCOVR_EXCL_LINE: allocation failure */
        }
        node = PyObject_CallOneArg(state->element_type, normalized);
        Py_DECREF(normalized);
    }
    if (node == NULL) {
        return NULL;
    }
    PyObject *attributes = element_get_attrs(node, NULL);
    PyObject *items = PyMapping_Items(attrs);
    if (attributes == NULL || items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure in native/lxml mappings */
        Py_XDECREF(attributes);                /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(items);                     /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(node);                       /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                           /* GCOVR_EXCL_LINE: allocation failure */
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(items); index++) {
        PyObject *pair = PyList_GET_ITEM(items, index);
        if (PyObject_SetItem(attributes, PyTuple_GET_ITEM(pair, 0), PyTuple_GET_ITEM(pair, 1)) < 0) {
            if (PyErr_ExceptionMatches(PyExc_ValueError)) {
                PyErr_Clear();
            } else {
                Py_CLEAR(node); /* GCOVR_EXCL_BR_LINE: node construction succeeded */
                break;
            }
        }
    }
    Py_DECREF(attributes);
    Py_DECREF(items);
    return node;
}

static PyObject *view_construct(module_state *state, PyObject *args, PyObject *kwargs) {
    PyObject *options = PyDict_New();
    PyObject *extra = kwargs == NULL ? PyDict_New() : PyDict_Copy(kwargs);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (options == NULL || extra == NULL) {
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(options); /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(extra);   /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    static const char *names[] = {"tag", "attrib"};
    for (size_t index = 0; index < sizeof(names) / sizeof(names[0]); index++) {
        PyObject *value = PyDict_GetItemString(extra, names[index]);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (value != NULL &&
            (PyDict_SetItemString(options, names[index], value) < 0 || PyDict_DelItemString(extra, names[index]) < 0)) {
            /* GCOVR_EXCL_BR_STOP */
            Py_DECREF(extra);   /* GCOVR_EXCL_LINE: allocation failure */
            Py_DECREF(options); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    static char *keywords[] = {"tag", "attrib", NULL};
    PyObject *tag;
    PyObject *attrib = Py_None;
    int parsed = PyArg_ParseTupleAndKeywords(args, options, "U|O:Element", keywords, &tag, &attrib);
    PyObject *result = NULL;
    if (parsed) {
        PyObject *attrs = PyDict_New();
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (attrs != NULL && (attrib == Py_None || PyDict_Update(attrs, attrib) == 0) &&
            PyDict_Update(attrs, extra) == 0) {
            /* GCOVR_EXCL_BR_STOP */
            PyObject *node = view_create(state, tag, attrs);
            result = node == NULL ? NULL : view_wrap(state, node);
            Py_XDECREF(node);
        }
        Py_XDECREF(attrs);
    }
    Py_DECREF(options);
    Py_DECREF(extra);
    return result;
}

static PyObject *view_element(PyObject *module, PyObject *args, PyObject *kwargs) {
    return view_construct(PyModule_GetState(module), args, kwargs);
}

static PyObject *view_makeelement(PyObject *self, PyObject *args, PyObject *kwargs) {
    return view_construct(state_of((PyObject *)((ElementView *)self)->node), args, kwargs);
}

static PyObject *view_subelement(PyObject *module, PyObject *args, PyObject *kwargs) {
    if (PyTuple_GET_SIZE(args) == 0) {
        PyErr_SetString(PyExc_TypeError, "SubElement requires a parent");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *parent = PyTuple_GET_ITEM(args, 0);
    if (!Py_IS_TYPE(parent, (PyTypeObject *)state->element_view_type)) {
        PyErr_SetString(PyExc_TypeError, "parent must be an ElementView");
        return NULL;
    }
    PyObject *arguments = PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args));
    if (arguments == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;         /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_construct(state, arguments, kwargs);
    Py_DECREF(arguments);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result != NULL && view_discard(view_append(parent, result)) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
    } /* GCOVR_EXCL_LINE: allocation failure */
    return result;
}

static PyObject *view_strip(PyObject *module, PyObject *args, int unwrap, int with_tail) {
    module_state *state = PyModule_GetState(module);
    if (PyTuple_GET_SIZE(args) == 0 ||
        !Py_IS_TYPE(PyTuple_GET_ITEM(args, 0), (PyTypeObject *)state->element_view_type)) {
        PyErr_SetString(PyExc_TypeError, "expected an ElementView followed by tag names");
        return NULL;
    }
    int present = 0;
    for (Py_ssize_t index = 1; index < PyTuple_GET_SIZE(args); index++) {
        int truth = PyObject_IsTrue(PyTuple_GET_ITEM(args, index));
        if (truth < 0) {
            return NULL;
        }
        present |= truth;
    }
    if (!present) {
        Py_RETURN_NONE;
    }
    PyObject *root = PyTuple_GET_ITEM(args, 0);
    PyObject *tags = PyTuple_GetSlice(args, 1, PyTuple_GET_SIZE(args));
    if (tags == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *iterator = view_descendants(root, tags);
    Py_DECREF(tags);
    if (iterator == NULL) {
        return NULL;
    }
    PyObject *items = PySequence_List(iterator);
    Py_DECREF(iterator);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(items); index++) {
        /* GCOVR_EXCL_BR_STOP */
        PyObject *item = PyList_GET_ITEM(items, index);
        if (unwrap) {
            status = view_discard(view_drop_tag(item, NULL));
        } else {
            PyObject *parent = view_parent(item, NULL);
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            while (parent != NULL && parent != Py_None && parent != root) {
                /* GCOVR_EXCL_BR_STOP */
                PyObject *previous = parent;
                parent = view_parent(parent, NULL);
                Py_DECREF(previous);
            }
            if (parent == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                status = -1;      /* GCOVR_EXCL_LINE: allocation failure */
            } else if (parent == root) {
                status = with_tail ? view_set_text(item, Py_None, (void *)1) : 0;
                /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
                if (status == 0) {
                    /* GCOVR_EXCL_BR_STOP */
                    status = view_discard(node_extract((PyObject *)((ElementView *)item)->node, NULL));
                }
            }
            Py_XDECREF(parent);
        }
    }
    Py_DECREF(items);
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: selected descendants have parents; only allocation can fail */
        return NULL;  /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_RETURN_NONE;
}

static PyObject *view_strip_tags(PyObject *module, PyObject *args) {
    return view_strip(module, args, 1, 0);
}

static PyObject *view_strip_elements(PyObject *module, PyObject *args, PyObject *kwargs) {
    int with_tail = view_option(kwargs, "with_tail", 1);
    return with_tail < 0 ? NULL : view_strip(module, args, 0, with_tail);
}

TH_NODE_API(static, PyObject *, view_serialize_node, (NodeObject * owner), (owner), (NodeObject * owner), owner, NULL) {
    th_serialize_opts options = {0};
    options.xml = 1;
    options.charset = "utf-8";
    options.charset_len = 5;
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    Py_ssize_t length;
    Py_UCS4 *text = th_node_serialize(tree_of((PyObject *)owner), owner->node, &options, NULL, 0, &length);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (text != NULL) {
        /* GCOVR_EXCL_BR_STOP */
        result = ucs4_to_str(text, length);
        PyMem_Free(text);
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *view_tostring(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"elem", "encoding", "method", "with_tail", NULL};
    module_state *state = PyModule_GetState(module);
    PyObject *element;
    PyObject *encoding = Py_None;
    const char *method = "xml";
    int with_tail = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O!|Osp:tostring", keywords, state->element_view_type, &element,
                                     &encoding, &method, &with_tail)) {
        return NULL;
    }
    NodeObject *node = ((ElementView *)element)->node;
    int plain = strcmp(method, "text") == 0;
    PyObject *result = plain ? node_get_text((PyObject *)node, NULL) : view_serialize_node(node);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (with_tail) {
        PyObject *tail = view_text_nodes(node, 1);
        if (tail == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
        }
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        for (Py_ssize_t index = 0; result != NULL && index < PyList_GET_SIZE(tail); index++) {
            /* GCOVR_EXCL_BR_STOP */
            PyObject *text = PyList_GET_ITEM(tail, index);
            PyObject *part = plain ? node_get_text(text, NULL) : view_serialize_node((NodeObject *)text);
            if (part == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
                break;            /* GCOVR_EXCL_LINE: allocation failure */
            }
            PyUnicode_AppendAndDel(&result, part);
        }
        Py_DECREF(tail);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    if (encoding == (PyObject *)&PyUnicode_Type ||
        (PyUnicode_Check(encoding) && PyUnicode_CompareWithASCIIString(encoding, "unicode") == 0)) {
        return result;
    }
    const char *codec = encoding == Py_None ? "ascii" : PyUnicode_AsUTF8(encoding);
    PyObject *encoded = codec == NULL ? NULL : PyUnicode_AsEncodedString(result, codec, "xmlcharrefreplace");
    Py_DECREF(result);
    return encoded;
}

static PyObject *view_lxml_node(PyObject *factory, NodeObject *node) {
    PyObject *tag = element_get_tag((PyObject *)node, NULL);
    PyObject *attributes = element_get_attrs((PyObject *)node, NULL);
    if (tag == NULL || attributes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(tag);                     /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(attributes);              /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                         /* GCOVR_EXCL_LINE: allocation failure */
    }
    ((AttrsObject *)attributes)->flat = 1;
    PyObject *attrs = PyObject_CallMethod(attributes, "copy", NULL);
    Py_DECREF(attributes);
    if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_DECREF(tag);  /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = PyObject_CallFunctionObjArgs(factory, tag, attrs, NULL);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (result == NULL && PyErr_ExceptionMatches(PyExc_ValueError)) {
        /* GCOVR_EXCL_BR_STOP */
        PyErr_Clear();
        PyObject *normalized = view_normalize_name(tag, 0);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        result = normalized == NULL ? NULL : PyObject_CallOneArg(factory, normalized);
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(normalized);
        if (result != NULL) {
            PyObject *key;
            PyObject *value;
            Py_ssize_t position = 0;
            while (PyDict_Next(attrs, &position, &key, &value)) {
                if (view_discard(PyObject_CallMethod(result, "set", "OO", key, value)) < 0) {
                    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
                    if (PyErr_ExceptionMatches(PyExc_ValueError)) {
                        /* GCOVR_EXCL_BR_STOP */
                        PyErr_Clear();
                    } else {              /* GCOVR_EXCL_LINE: allocation failure with native string attributes */
                        Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
                        break;            /* GCOVR_EXCL_LINE: allocation failure */
                    }
                }
            }
        }
    }
    Py_DECREF(tag);
    Py_DECREF(attrs);
    return result;
}

static int view_push_pair(PyObject *stack, PyObject *source, PyObject *target) {
    PyObject *pair = PyTuple_Pack(2, source, target);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    int status = pair == NULL ? -1 : PyList_Append(stack, pair);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(pair);
    return status;
}

TH_NODE_API(static, PyObject *, view_lxml_text, (PyObject * self, int tail), (self, tail), (PyObject * self, int tail),
            ((ElementView *)self)->node, NULL) {
    NodeObject *owner = ((ElementView *)self)->node;
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(owner->handle);
    th_node *node = tail ? owner->node->next_sibling : owner->node->first_child;
    for (; node != NULL && node->type != TH_NODE_ELEMENT; node = node->next_sibling) {
        if (node->type == TH_NODE_TEXT) {
            PyObject *part = th_node_text_string(tree_of((PyObject *)owner), node);
            if (part == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure */
                Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation failure */
                break;            /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (result == NULL) {
                result = part;
            } else {
                PyUnicode_AppendAndDel(&result, part);
                if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    break;            /* GCOVR_EXCL_LINE: allocation failure */
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    return result == NULL && !PyErr_Occurred() ? Py_NewRef(Py_None) : result;
    /* GCOVR_EXCL_BR_STOP */
}

static PyObject *view_copy_to_lxml(PyObject *element, PyObject *factory) {
    PyObject *result = view_lxml_node(factory, ((ElementView *)element)->node);
    if (result == NULL) {
        return NULL;
    }
    PyObject *stack = PyList_New(0);
    if (stack == NULL || view_push_pair(stack, element, result) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(stack);                                             /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(result);                                             /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                                                   /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    while (status == 0 && PyList_GET_SIZE(stack) > 0) {
        Py_ssize_t last = PyList_GET_SIZE(stack) - 1;
        PyObject *pair = Py_NewRef(PyList_GET_ITEM(stack, last));
        status = PySequence_DelItem(stack, last);
        PyObject *source = PyTuple_GET_ITEM(pair, 0);
        PyObject *target = PyTuple_GET_ITEM(pair, 1);
        PyObject *text = view_lxml_text(source, 0);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (status == 0) {
            status = text == NULL ? -1 : PyObject_SetAttrString(target, "text", text);
            /* GCOVR_EXCL_BR_STOP */
        }
        Py_XDECREF(text);
        PyObject *children = view_children(source, NULL);
        if (children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            status = -1;        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: allocation failure */
        for (Py_ssize_t index = 0; status == 0 && index < PyList_GET_SIZE(children); index++) {
            PyObject *child = PyList_GET_ITEM(children, index);
            PyObject *constructor = PyObject_GetAttrString(target, "makeelement");
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            PyObject *copied = constructor == NULL ? NULL : view_lxml_node(constructor, ((ElementView *)child)->node);
            /* GCOVR_EXCL_BR_STOP */
            Py_XDECREF(constructor);
            PyObject *tail = view_lxml_text(child, 1);
            /* GCOVR_EXCL_BR_START: tail can be NULL only on allocation failure */
            status = copied == NULL || tail == NULL ? -1 : PyObject_SetAttrString(copied, "tail", tail);
            /* GCOVR_EXCL_BR_STOP */
            Py_XDECREF(tail);
            if (status == 0) {
                status = view_discard(PyObject_CallMethod(target, "append", "O", copied));
            }
            if (status == 0) {
                status = view_push_pair(stack, child, copied);
            }
            Py_XDECREF(copied);
        }
        Py_XDECREF(children);
        Py_DECREF(pair);
    }
    Py_DECREF(stack);
    if (status < 0) {
        Py_CLEAR(result); /* GCOVR_EXCL_BR_LINE: result construction succeeded */
    }
    return result;
}

static PyObject *view_to_lxml(PyObject *module, PyObject *source) {
    module_state *state = PyModule_GetState(module);
    if (!Py_IS_TYPE(source, (PyTypeObject *)state->element_view_type)) {
        return Py_NewRef(source);
    }
    PyObject *lxml = PyImport_ImportModule("lxml.etree");
    if (lxml == NULL) {
        return NULL;
    }
    PyObject *factory = PyObject_GetAttrString(lxml, "Element");
    Py_DECREF(lxml);
    if (factory == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure looking up lxml's Element */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_copy_to_lxml(source, factory);
    Py_DECREF(factory);
    return result;
}

static PyObject *view_to_lxml_html(PyObject *module, PyObject *source) {
    if (!Py_IS_TYPE(source, (PyTypeObject *)((module_state *)PyModule_GetState(module))->element_view_type)) {
        PyErr_SetString(PyExc_TypeError, "expected an ElementView");
        return NULL;
    }
    PyObject *lxml = PyImport_ImportModule("lxml.html");
    if (lxml == NULL) {
        return NULL;
    }
    PyObject *parser = PyObject_GetAttrString(lxml, "html_parser");
    Py_DECREF(lxml);
    if (parser == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure looking up lxml's html_parser */
        return NULL;      /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *factory = PyObject_GetAttrString(parser, "makeelement");
    Py_DECREF(parser);
    if (factory == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure looking up lxml's makeelement */
        return NULL;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *result = view_copy_to_lxml(source, factory);
    Py_DECREF(factory);
    return result;
}

static PyObject *view_from_lxml_node(module_state *state, PyObject *source) {
    PyObject *tag = PyObject_GetAttrString(source, "tag");
    PyObject *attrs = PyObject_GetAttrString(source, "attrib");
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *result = tag == NULL || attrs == NULL ? NULL : view_create(state, tag, attrs);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(tag);
    Py_XDECREF(attrs);
    return result;
}

static int view_append_lxml_text(module_state *state, PyObject *target, PyObject *source, const char *property) {
    PyObject *value = PyObject_GetAttrString(source, property);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure reading an lxml text or tail property */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    if (value != Py_None) {
        PyObject *text = PyObject_CallOneArg(state->text_type, value);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        status = text == NULL ? -1 : view_discard(PyObject_CallMethod(target, "append", "O", text));
        /* GCOVR_EXCL_BR_STOP */
        Py_XDECREF(text);
    }
    Py_DECREF(value);
    return status;
}

static PyObject *view_from_lxml(PyObject *module, PyObject *source) {
    module_state *state = PyModule_GetState(module);
    PyObject *root = view_from_lxml_node(state, source);
    if (root == NULL) {
        return NULL;
    }
    PyObject *stack = PyList_New(0);
    if (stack == NULL || view_push_pair(stack, source, root) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        Py_XDECREF(stack);                                          /* GCOVR_EXCL_LINE: allocation failure */
        Py_DECREF(root);                                            /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;                                                /* GCOVR_EXCL_LINE: allocation failure */
    }
    int status = 0;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    while (status == 0 && PyList_GET_SIZE(stack) > 0) {
        /* GCOVR_EXCL_BR_STOP */
        Py_ssize_t last = PyList_GET_SIZE(stack) - 1;
        PyObject *pair = Py_NewRef(PyList_GET_ITEM(stack, last));
        status = PySequence_DelItem(stack, last);
        PyObject *original = PyTuple_GET_ITEM(pair, 0);
        PyObject *target = PyTuple_GET_ITEM(pair, 1);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (status == 0) {
            /* GCOVR_EXCL_BR_STOP */
            status = view_append_lxml_text(state, target, original, "text");
        }
        PyObject *iterator = PyObject_GetIter(original);
        if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure creating an lxml child iterator */
            status = -1;        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: allocation failure */
        PyObject *child;
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        while (status == 0 && (child = PyIter_Next(iterator)) != NULL) {
            /* GCOVR_EXCL_BR_STOP */
            PyObject *tag = PyObject_GetAttrString(child, "tag");
            if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure reading an lxml tag */
                status = -1;   /* GCOVR_EXCL_LINE: allocation failure */
            } else if (PyUnicode_Check(tag)) {
                PyObject *copied = view_from_lxml_node(state, child);
                /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
                status = copied == NULL ? -1 : view_discard(PyObject_CallMethod(target, "append", "O", copied));
                if (status == 0) {
                    /* GCOVR_EXCL_BR_STOP */
                    status = view_push_pair(stack, child, copied);
                }
                Py_XDECREF(copied);
            }
            Py_XDECREF(tag);
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            if (status == 0) {
                /* GCOVR_EXCL_BR_STOP */
                status = view_append_lxml_text(state, target, child, "tail");
            }
            Py_DECREF(child);
        }
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: allocation failure during lxml traversal */
            status = -1;        /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: allocation failure */
        Py_XDECREF(iterator);
        Py_DECREF(pair);
    }
    Py_DECREF(stack);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status == 0) {
        /* GCOVR_EXCL_BR_STOP */
        status = view_discard(PyObject_CallMethod(root, "normalize", NULL));
    }
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *result = status < 0 ? NULL : view_wrap(state, root);
    /* GCOVR_EXCL_BR_STOP */
    Py_DECREF(root);
    return result;
}

typedef struct {
    Py_ssize_t line;
    Py_ssize_t column;
} view_position;

static int view_position_before(view_position left, view_position right) {
    return left.line < right.line || (left.line == right.line && left.column < right.column);
}

static int view_ascii_prefix(PyObject *source, Py_ssize_t offset, const char *prefix) {
    for (Py_ssize_t index = 0; prefix[index] != '\0'; index++) {
        if (offset + index >= PyUnicode_GET_LENGTH(source) ||
            (PyUnicode_READ_CHAR(source, offset + index) | 32) != (Py_UCS4)prefix[index]) {
            return 0;
        }
    }
    return 1;
}

static PyObject *view_markup_text(PyObject *source) {
    if (PyUnicode_Check(source)) {
        return Py_NewRef(source);
    }
    if (PyBytes_Check(source)) {
        return PyUnicode_DecodeUTF8(PyBytes_AS_STRING(source), PyBytes_GET_SIZE(source), "replace");
    }
    PyErr_SetString(PyExc_TypeError, "markup must be str or bytes");
    return NULL;
}

static void view_strip_parsed_nodes(NodeObject *owner, int implicit_paragraphs) {
    th_tree *tree = tree_of((PyObject *)owner);
    th_node *root = owner->node;
    for (th_node *node = root->first_child; node != NULL;) {
        th_node *following = preorder_next(node, root);
        Py_ssize_t line, column;
        if (node->type == TH_NODE_COMMENT ||
            (implicit_paragraphs && node->atom == TH_TAG_P && node->first_child == NULL && node->attr_count == 0 &&
             !th_node_source_position(tree, node, &line, &column))) {
            th_node_remove(node);
        }
        node = following;
    }
}

static int view_remains_in_head(th_tree *tree, th_node *root, view_position body) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        view_position position;
        if (th_node_source_position(tree, node, &position.line, &position.column) &&
            !view_position_before(position, body)) {
            return 0;
        }
    }
    return 1;
}

static int view_restore_head(NodeObject *owner, PyObject *source) {
    th_node *head = NULL;
    th_node *body = NULL;
    for (th_node *child = owner->node->first_child; child != NULL; child = child->next_sibling) {
        if (child->atom == TH_TAG_HEAD) {
            head = child;
        } else if (child->atom == TH_TAG_BODY) {
            body = child;
        }
    }
    if (body == NULL) {
        return 0;
    }
    view_position head_position = {0};
    view_position body_position = {0};
    view_position current = {1, 0};
    for (Py_ssize_t index = 0; index < PyUnicode_GET_LENGTH(source); index++) {
        Py_UCS4 character = PyUnicode_READ_CHAR(source, index);
        if (character == '<' && index + 5 < PyUnicode_GET_LENGTH(source)) {
            Py_UCS4 delimiter = PyUnicode_READ_CHAR(source, index + 5);
            if (delimiter == '/' || delimiter == '>' || Py_UNICODE_ISSPACE(delimiter)) {
                if (head_position.line == 0 && view_ascii_prefix(source, index + 1, "head")) {
                    head_position = current;
                } else if (body_position.line == 0 && view_ascii_prefix(source, index + 1, "body")) {
                    body_position = current;
                }
            }
        }
        if (head_position.line != 0 && body_position.line != 0) {
            break;
        }
        if (character == '\n') {
            current.line++;
            current.column = 0;
        } else {
            current.column++;
        }
    }
    if (head_position.line == 0 || body_position.line == 0) {
        return 0;
    }
    PyObject *moved = PyList_New(0);
    if (moved == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;       /* GCOVR_EXCL_LINE: allocation failure */
    }
    th_tree *tree = tree_of((PyObject *)owner);
    int status = 0;
    /* GCOVR_EXCL_BR_START: status stops the loop only after allocation failure */
    for (int pass = 0; pass < 2 && status == 0; pass++) {
        /* GCOVR_EXCL_BR_STOP */
        for (th_node *node = body->first_child; node != NULL;
             node = pass == 0 ? node->next_sibling : preorder_next(node, body)) {
            if (node->type != TH_NODE_ELEMENT || (pass == 1 && node->atom != TH_TAG_META && node->atom != TH_TAG_LINK &&
                                                  node->atom != TH_TAG_TITLE && node->atom != TH_TAG_BASE)) {
                continue;
            }
            view_position position = {0};
            th_node_source_position(tree, node, &position.line, &position.column);
            if (view_position_before(head_position, position) && view_position_before(position, body_position) &&
                (pass == 1 || view_remains_in_head(tree, node, body_position))) {
                PyObject *wrapped = node_wrap(state_of((PyObject *)owner), owner->handle, node);
                /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
                status = wrapped == NULL ? -1 : PyList_Append(moved, wrapped);
                /* GCOVR_EXCL_BR_STOP */
                Py_XDECREF(wrapped);
                if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    break;        /* GCOVR_EXCL_LINE: allocation failure */
                }
            }
        }
    }
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status == 0) {
        /* GCOVR_EXCL_BR_STOP */
        for (Py_ssize_t index = 0; index < PyList_GET_SIZE(moved); index++) {
            th_node *node = ((NodeObject *)PyList_GET_ITEM(moved, index))->node;
            th_node_remove(node);
            th_node_append_child(head, node);
        }
    }
    Py_DECREF(moved);
    return status;
}

static PyObject *view_parse_document(PyObject *module, PyObject *source) {
    PyObject *text = view_markup_text(source);
    if (text == NULL) {
        return NULL;
    }
    PyObject *arguments = PyTuple_Pack(1, source);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *document = arguments == NULL ? NULL : turbohtml_parse(module, arguments, NULL);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(arguments);
    if (document == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure parsing validated str/bytes */
        Py_DECREF(text);    /* GCOVR_EXCL_LINE: allocation failure */
        return NULL;        /* GCOVR_EXCL_LINE: allocation failure */
    }
    PyObject *root = PyObject_GetAttrString(document, "root");
    Py_DECREF(document);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (root != NULL) {
        /* GCOVR_EXCL_BR_STOP */
        /* The fresh tree has no external iterators or observers. */
        view_strip_parsed_nodes((NodeObject *)root, 1);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (view_restore_head((NodeObject *)root, text) < 0 ||
            view_discard(PyObject_CallMethod(root, "normalize", NULL)) < 0) {
            /* GCOVR_EXCL_BR_STOP */
            Py_CLEAR(root); /* GCOVR_EXCL_LINE: allocation failure */
        } /* GCOVR_EXCL_LINE: allocation failure */
    }
    Py_DECREF(text);
    return root;
}

static PyObject *view_document_fromstring(PyObject *module, PyObject *source) {
    PyObject *root = view_parse_document(module, source);
    if (root == NULL) {
        return NULL;
    }
    for (th_node *child = ((NodeObject *)root)->node->first_child; child != NULL;) {
        th_node *following = child->next_sibling;
        if ((child->atom == TH_TAG_HEAD || child->atom == TH_TAG_BODY) && child->first_child == NULL &&
            child->attr_count == 0) {
            th_node_remove(child);
        }
        child = following;
    }
    PyObject *result = view_wrap(PyModule_GetState(module), root);
    Py_DECREF(root);
    return result;
}

static int view_block_tag(uint16_t atom) {
    switch (atom) {
    case TH_TAG_ADDRESS:
    case TH_TAG_BLOCKQUOTE:
    case TH_TAG_CAPTION:
    case TH_TAG_CENTER:
    case TH_TAG_COL:
    case TH_TAG_COLGROUP:
    case TH_TAG_DD:
    case TH_TAG_DEL:
    case TH_TAG_DIR:
    case TH_TAG_DIV:
    case TH_TAG_DL:
    case TH_TAG_DT:
    case TH_TAG_FIELDSET:
    case TH_TAG_FORM:
    case TH_TAG_H1:
    case TH_TAG_H2:
    case TH_TAG_H3:
    case TH_TAG_H4:
    case TH_TAG_H5:
    case TH_TAG_H6:
    case TH_TAG_HR:
    case TH_TAG_INS:
    case TH_TAG_ISINDEX:
    case TH_TAG_LEGEND:
    case TH_TAG_LI:
    case TH_TAG_MENU:
    case TH_TAG_NOSCRIPT:
    case TH_TAG_OL:
    case TH_TAG_OPTGROUP:
    case TH_TAG_OPTION:
    case TH_TAG_P:
    case TH_TAG_PRE:
    case TH_TAG_TABLE:
    case TH_TAG_TBODY:
    case TH_TAG_TD:
    case TH_TAG_TFOOT:
    case TH_TAG_TH:
    case TH_TAG_THEAD:
    case TH_TAG_TR:
    case TH_TAG_UL:
        return 1;
    default:
        return 0;
    }
}

static int view_has_text(th_tree *tree, th_node *parent) {
    for (th_node *child = parent->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_TEXT) {
            PyObject *text = th_node_text_string(tree, child);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;      /* GCOVR_EXCL_LINE: allocation failure */
            }
            for (Py_ssize_t index = 0; index < PyUnicode_GET_LENGTH(text); index++) {
                if (!Py_UNICODE_ISSPACE(PyUnicode_READ_CHAR(text, index))) {
                    Py_DECREF(text);
                    return 1;
                }
            }
            Py_DECREF(text);
        }
    }
    return 0;
}

static PyObject *view_fromstring(PyObject *module, PyObject *source) {
    PyObject *text = view_markup_text(source);
    if (text == NULL) {
        return NULL;
    }
    Py_ssize_t start = 0;
    while (start < PyUnicode_GET_LENGTH(text) && Py_UNICODE_ISSPACE(PyUnicode_READ_CHAR(text, start))) {
        start++;
    }
    if (start < PyUnicode_GET_LENGTH(text) && PyUnicode_READ_CHAR(text, start) == '<' &&
        (view_ascii_prefix(text, start + 1, "html") || view_ascii_prefix(text, start + 1, "!doctype"))) {
        PyObject *result = view_document_fromstring(module, text);
        Py_DECREF(text);
        return result;
    }
    PyObject *root = view_parse_document(module, text);
    Py_DECREF(text);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure parsing a validated string */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    NodeObject *owner = (NodeObject *)root;
    th_node *head = NULL;
    th_node *body = NULL;
    for (th_node *child = owner->node->first_child; child != NULL; child = child->next_sibling) {
        if (child->atom == TH_TAG_HEAD) {
            head = child;
        } else if (child->atom == TH_TAG_BODY) {
            body = child;
        }
    }
    int head_content = 0;
    for (th_node *child = head->first_child; child != NULL; child = child->next_sibling) {
        head_content |= child->type == TH_NODE_ELEMENT;
    }
    th_node *target = owner->node;
    int status = 0;
    if (body != NULL && !head_content) {
        target = body;
        th_node *only = NULL;
        Py_ssize_t children = 0;
        for (th_node *child = body->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                only = child;
                children++;
            }
        }
        int has_text = children == 1 ? view_has_text(tree_of(root), body) : 1;
        if (has_text < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure */
            Py_DECREF(root); /* GCOVR_EXCL_LINE: allocation failure */
            return NULL;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        if (!has_text) {
            target = only;
        } else {
            int block = 0;
            for (th_node *node = body; node != NULL; node = preorder_next(node, body)) {
                if (node->type == TH_NODE_ELEMENT && view_block_tag(node->atom)) {
                    block = 1;
                    break;
                }
            }
            status = th_node_rename(tree_of(root), body, NULL, 0, block ? TH_TAG_DIV : TH_TAG_SPAN);
        }
    }
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *node = status < 0 ? NULL : node_wrap(PyModule_GetState(module), owner->handle, target);
    PyObject *result = node == NULL ? NULL : view_wrap(PyModule_GetState(module), node);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(node);
    Py_DECREF(root);
    return result;
}

static PyObject *view_fragment_fromstring(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"markup", "create_parent", NULL};
    PyObject *source;
    PyObject *parent = Py_False;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U|O:fragment_fromstring", keywords, &source, &parent)) {
        return NULL;
    }
    PyObject *arguments = PyTuple_Pack(1, source);
    PyObject *options = Py_BuildValue("{s:O}", "positions", Py_False);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    PyObject *root =
        arguments == NULL || options == NULL ? NULL : turbohtml_tree_parse_fragment(module, arguments, options);
    /* GCOVR_EXCL_BR_STOP */
    Py_XDECREF(arguments);
    Py_XDECREF(options);
    if (root == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure parsing a validated string */
        return NULL;    /* GCOVR_EXCL_LINE: allocation failure */
    }
    NodeObject *owner = (NodeObject *)root;
    view_strip_parsed_nodes(owner, 0);
    int status = view_discard(PyObject_CallMethod(root, "normalize", NULL));
    int create_parent = PyObject_IsTrue(parent);
    PyObject *target = NULL;
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (status == 0 && create_parent > 0) {
        /* GCOVR_EXCL_BR_STOP */
        PyObject *tag = PyUnicode_Check(parent) ? Py_NewRef(parent) : PyUnicode_FromString("div");
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
        if (tag != NULL && PyObject_SetAttrString(root, "tag", tag) == 0) {
            /* GCOVR_EXCL_BR_STOP */
            target = Py_NewRef(root);
        }
        Py_XDECREF(tag);
        /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    } else if (status == 0 && create_parent == 0) {
        /* GCOVR_EXCL_BR_STOP */
        th_node *only = NULL;
        Py_ssize_t children = 0;
        for (th_node *child = owner->node->first_child; child != NULL; child = child->next_sibling) {
            if (child->type == TH_NODE_ELEMENT) {
                only = child;
                children++;
            }
        }
        if (children != 1) {
            if (children == 0) {
                PyErr_SetString(PyExc_ValueError, "No elements found");
            } else {
                PyErr_Format(PyExc_ValueError, "Multiple elements found (%zd)", children);
            }
        } else {
            target = node_wrap(PyModule_GetState(module), owner->handle, only);
            /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
            if (target != NULL && view_detach((NodeObject *)target, 1, 0) < 0) {
                /* GCOVR_EXCL_BR_STOP */
                Py_CLEAR(target); /* GCOVR_EXCL_LINE: allocation failure */
            } /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    PyObject *result = target == NULL ? NULL : view_wrap(PyModule_GetState(module), target);
    Py_XDECREF(target);
    Py_DECREF(root);
    return result;
}

static PyType_Slot view_iter_slots[] = {
    {Py_tp_dealloc, view_iter_dealloc}, {Py_tp_traverse, view_iter_traverse}, {Py_tp_clear, view_iter_clear},
    {Py_tp_iter, PyObject_SelfIter},    {Py_tp_iternext, view_iter_next},     TH_SEALED_END,
};

static PyType_Spec view_iter_spec = {
    .name = "turbohtml.etree.ElementIterator",
    .basicsize = sizeof(ViewIterator),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = view_iter_slots,
};

static PyGetSetDef view_getset[] = {
    {"node", view_node, NULL, NULL, NULL},          {"tag", view_tag, view_set_tag, NULL, NULL},
    {"text", view_text, view_set_text, NULL, NULL}, {"tail", view_text, view_set_text, NULL, (void *)1},
    {"attrib", view_attrib, NULL, NULL, NULL},      {NULL, NULL, NULL, NULL, NULL},
};

static PyMethodDef view_methods[] = {
    {"get", view_get, METH_VARARGS, NULL},
    {"set", view_set, METH_VARARGS, NULL},
    {"text_content", view_text_content, METH_NOARGS, "text_content()\n--\n\n"},
    {"getparent", view_parent, METH_NOARGS, "getparent()\n--\n\n"},
    {"getnext", view_next, METH_NOARGS, "getnext()\n--\n\n"},
    {"getprevious", view_previous, METH_NOARGS, "getprevious()\n--\n\n"},
    {"getchildren", view_children, METH_NOARGS, "getchildren()\n--\n\n"},
    {"getroottree", view_getroottree, METH_NOARGS, "getroottree()\n--\n\n"},
    {"xpath", (PyCFunction)(void (*)(void))view_xpath, METH_VARARGS | METH_KEYWORDS, NULL},
    {"find", view_find, METH_O, "find(path, /)\n--\n\n"},
    {"findall", view_findall, METH_O, "findall(path, /)\n--\n\n"},
    {"iterfind", view_iterfind, METH_O, "iterfind(path, /)\n--\n\n"},
    {"index", view_index, METH_O, "index(child, /)\n--\n\n"},
    {"iter", view_iter, METH_VARARGS, NULL},
    {"iterdescendants", view_descendants, METH_VARARGS, NULL},
    {"iterchildren", (PyCFunction)(void (*)(void))view_iterchildren, METH_VARARGS | METH_KEYWORDS, NULL},
    {"itersiblings", (PyCFunction)(void (*)(void))view_itersiblings, METH_VARARGS | METH_KEYWORDS, NULL},
    {"iterancestors", view_iterancestors, METH_VARARGS, NULL},
    {"itertext", (PyCFunction)(void (*)(void))view_itertext, METH_VARARGS | METH_KEYWORDS, NULL},
    {"keys", view_keys, METH_NOARGS, "keys()\n--\n\n"},
    {"items", view_items, METH_NOARGS, "items()\n--\n\n"},
    {"values", view_values, METH_NOARGS, "values()\n--\n\n"},
    {"append", view_append, METH_O, "append(child, /)\n--\n\n"},
    {"extend", view_extend, METH_O, "extend(children, /)\n--\n\n"},
    {"remove", view_remove, METH_O, "remove(child, /)\n--\n\n"},
    {"replace", view_replace, METH_VARARGS, NULL},
    {"drop_tag", view_drop_tag, METH_NOARGS, "drop_tag()\n--\n\n"},
    {"drop_tree", view_drop_tree, METH_NOARGS, "drop_tree()\n--\n\n"},
    {"insert", view_insert, METH_VARARGS, NULL},
    {"clear", (PyCFunction)(void (*)(void))view_clear, METH_VARARGS | METH_KEYWORDS, NULL},
    {"addnext", view_addnext, METH_O, "addnext(child, /)\n--\n\n"},
    {"addprevious", view_addprevious, METH_O, "addprevious(child, /)\n--\n\n"},
    {"makeelement", (PyCFunction)(void (*)(void))view_makeelement, METH_VARARGS | METH_KEYWORDS, NULL},
    {"__copy__", view_copy, METH_NOARGS, NULL},
    {"__deepcopy__", view_copy, METH_O, NULL},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot view_slots[] = {
    {Py_tp_new, view_new},
    {Py_tp_dealloc, view_dealloc},
    {Py_tp_traverse, view_traverse},
    {Py_tp_clear, view_clear_refs},
    {Py_tp_repr, view_repr},
    {Py_tp_iter, view_children_iter},
    {Py_tp_methods, view_methods},
    {Py_tp_getset, view_getset},
    {Py_sq_length, view_length},
    {Py_mp_subscript, view_subscript},
    {0, NULL},
};

static PyType_Spec view_spec = {
    .name = "turbohtml.etree.ElementView",
    .basicsize = sizeof(ElementView),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .slots = view_slots,
};

int elementtree_register(PyObject *module, module_state *state) {
    state->element_view_cache = PyDict_New();
    state->element_view_xpath_cache = PyDict_New();
    state->element_view_xpath_type = PyType_FromModuleAndSpec(module, &view_xpath_spec, NULL);
    state->element_view_root_type = PyType_FromModuleAndSpec(module, &view_root_spec, NULL);
    state->element_view_origins = PyContextVar_New("turbohtml.elementtree.origins", Py_None);
    state->element_view_context_type = PyType_FromModuleAndSpec(module, &view_context_spec, NULL);
    state->element_view_iterator_type = PyType_FromModuleAndSpec(module, &view_iter_spec, NULL);
    state->element_view_type = PyType_FromModuleAndSpec(module, &view_spec, NULL);
    /* GCOVR_EXCL_BR_START: the untested arms require allocation failure */
    if (state->element_view_cache == NULL || state->element_view_type == NULL ||
        state->element_view_iterator_type == NULL || state->element_view_origins == NULL ||
        state->element_view_context_type == NULL || state->element_view_xpath_cache == NULL ||
        state->element_view_xpath_type == NULL || state->element_view_root_type == NULL ||
        PyModule_AddObjectRef(module, "_ElementXPath", state->element_view_xpath_type) < 0 ||
        PyModule_AddObjectRef(module, "_ElementTree", state->element_view_root_type) < 0 ||
        PyModule_AddFunctions(module, view_module_methods) < 0) {
        /* GCOVR_EXCL_BR_STOP */
        return -1; /* GCOVR_EXCL_LINE: module-init allocation */
    }
    ((PyTypeObject *)state->element_view_type)->tp_weaklistoffset = offsetof(ElementView, weakrefs);
    return PyModule_AddObjectRef(module, "ElementView", state->element_view_type);
}
