/* Elements and their attributes: the live attribute view, form-control value semantics, and the
   find/select/xpath/regex query plus structural-mutation bindings. */

#include "dom/nodes.h"
#include "dom/form_value.h"
#include "core/node_map.h"

#include "core/vec.h" /* th_grow_cap overflow-safe buffer growth */

#include "css/select/selector.h"

static int validate_name(PyObject *name, int is_attr);

static int element_attr_value(PyObject *value, Py_UCS4 **points, Py_ssize_t *len, int *has_value);

/* Encode a str key into a freshly allocated UTF-8 buffer for an attribute lookup;
   *out_len its length. Folded to match an HTML tree's lowercased names, kept verbatim
   for a case-sensitive XML tree. NULL with TypeError when the key is not a str. Caller
   frees with PyMem_Free. */
char *attr_key_utf8(th_tree *tree, PyObject *key, Py_ssize_t *out_len) {
    if (!PyUnicode_Check(key)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be a str");
        return NULL;
    }
    Py_ssize_t len;
    const char *utf8 = PyUnicode_AsUTF8AndSize(key, &len);
    if (utf8 == NULL) { /* GCOVR_EXCL_BR_LINE: a lone-surrogate name cannot encode, hard to force */
        return NULL;    /* GCOVR_EXCL_LINE: surrogate path */
    }
    char *name = PyMem_Malloc((size_t)(len ? len : 1));
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int fold = !th_tree_is_xml(tree);
    for (Py_ssize_t index = 0; index < len; index++) {
        char ch = utf8[index];
        name[index] = fold && ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
    }
    *out_len = len;
    return name;
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
    Py_ssize_t count;
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    count = ((AttrsObject *)self)->node->attr_count;
    Py_END_CRITICAL_SECTION();
    return count;
}

static PyObject *attrs_subscript(PyObject *self, PyObject *key) {
    Py_ssize_t len;
    char *name = attr_key_utf8(tree_of(self), key, &len);
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
        char *name = attr_key_utf8(tree, key, &len);
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
    char *name = attr_key_utf8(tree, key, &len);
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
    char *name = attr_key_utf8(tree_of(self), key, &len);
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
    PyObject *names;
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    names = PyList_New(node->attr_count);
    if (names != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            PyObject *name = attr_name_obj(tree, &node->attrs[index]);
            if (name == NULL) {   /* GCOVR_EXCL_BR_LINE: a stored name always decodes */
                Py_DECREF(names); /* GCOVR_EXCL_LINE: decode-failure path */
                names = NULL;     /* GCOVR_EXCL_LINE: decode-failure path */
                break;            /* GCOVR_EXCL_LINE: decode-failure path */
            }
            PyList_SET_ITEM(names, index, name);
        }
    }
    Py_END_CRITICAL_SECTION();
    if (names == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure and stored-name decode failure cannot be forced */
        return NULL;     /* GCOVR_EXCL_LINE: allocation/decode-failure path */
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
    PyObject *out;
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    out = PyList_New(node->attr_count);
    if (out != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            const th_node_attr *attr = &node->attrs[index];
            PyObject *item;
            if (kind == ATTRS_VALUES) {
                item = attr_value_obj(attr);
            } else {
                PyObject *name = attr_name_obj(tree, attr);
                if (name == NULL) { /* GCOVR_EXCL_BR_LINE: a stored name always decodes */
                    Py_DECREF(out); /* GCOVR_EXCL_LINE: decode-failure path */
                    out = NULL;     /* GCOVR_EXCL_LINE: decode-failure path */
                    break;          /* GCOVR_EXCL_LINE: decode-failure path */
                }
                if (kind == ATTRS_KEYS) {
                    item = name;
                } else {
                    PyObject *value = attr_value_obj(attr);
                    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: value object build cannot be forced to fail */
                        Py_DECREF(name); /* GCOVR_EXCL_LINE: alloc-failure path */
                        Py_DECREF(out);  /* GCOVR_EXCL_LINE: alloc-failure path */
                        out = NULL;      /* GCOVR_EXCL_LINE: alloc-failure path */
                        break;           /* GCOVR_EXCL_LINE: alloc-failure path */
                    }
                    item = PyTuple_Pack(2, name, value);
                    Py_DECREF(name);
                    Py_DECREF(value);
                }
            }
            if (item == NULL) { /* GCOVR_EXCL_BR_LINE: item build cannot be forced to fail */
                Py_DECREF(out); /* GCOVR_EXCL_LINE: alloc-failure path */
                out = NULL;     /* GCOVR_EXCL_LINE: alloc-failure path */
                break;          /* GCOVR_EXCL_LINE: alloc-failure path */
            }
            PyList_SET_ITEM(out, index, item);
        }
    }
    Py_END_CRITICAL_SECTION();
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
        char *name = attr_key_utf8(tree_of(self), key, &len);
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

/* A snapshot dict of the attributes, in source order. */
static PyObject *attrs_to_dict(PyObject *self) {
    PyObject *items = attrs_collect(self, ATTRS_ITEMS);
    if (items == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *mapping = PyObject_CallOneArg((PyObject *)&PyDict_Type, items);
    Py_DECREF(items);
    return mapping;
}

static PyObject *attrs_repr(PyObject *self) {
    PyObject *mapping = attrs_to_dict(self);
    if (mapping == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *repr = PyObject_Repr(mapping);
    Py_DECREF(mapping);
    return repr;
}

static PyObject *attrs_copy(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return attrs_to_dict(self);
}

/* Remove the attribute at index and return its value; the caller holds the tree lock. */
static PyObject *attrs_take(th_tree *tree, th_node *node, Py_ssize_t index) {
    PyObject *value = attr_value_obj(&node->attrs[index]);
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, node->attrs[index].name_atom, &name_len);
    th_node_attr_del(tree, node, name, name_len);
    return value;
}

static PyObject *attrs_pop(PyObject *self, PyObject *args) {
    PyObject *key;
    PyObject *fallback = NULL;
    if (!PyArg_ParseTuple(args, "O|O:pop", &key, &fallback)) {
        return NULL;
    }
    PyObject *result = NULL;
    if (PyUnicode_Check(key)) {
        Py_ssize_t len;
        char *name = attr_key_utf8(tree_of(self), key, &len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: key is a str here */
            return NULL;    /* GCOVR_EXCL_LINE: unreachable */
        }
        th_node *node = ((AttrsObject *)self)->node;
        /* one critical section, so another thread cannot remove the attribute between the read and the delete */
        Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
        Py_ssize_t index = find_attr_index(tree_of(self), node, name, len);
        if (index >= 0) {
            result = attrs_take(tree_of(self), node, index);
        }
        Py_END_CRITICAL_SECTION();
        PyMem_Free(name);
    }
    if (result != NULL) {
        return result;
    }
    if (fallback != NULL) {
        return Py_NewRef(fallback);
    }
    PyErr_SetObject(PyExc_KeyError, key);
    return NULL;
}

static PyObject *attrs_popitem(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((AttrsObject *)self)->node;
    th_tree *tree = tree_of(self);
    PyObject *name = NULL;
    PyObject *value = NULL;
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    if (node->attr_count > 0) {
        Py_ssize_t last = node->attr_count - 1;
        name = attr_name_obj(tree, &node->attrs[last]);
        value = attrs_take(tree, node, last);
    }
    Py_END_CRITICAL_SECTION();
    if (name == NULL) {
        PyErr_SetString(PyExc_KeyError, "popitem(): attributes are empty");
        return NULL;
    }
    PyObject *pair = PyTuple_Pack(2, name, value);
    Py_DECREF(name);
    Py_DECREF(value);
    return pair;
}

static PyObject *attrs_clear(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((AttrsObject *)self)->node;
    th_tree *tree = tree_of(self);
    Py_BEGIN_CRITICAL_SECTION(((AttrsObject *)self)->handle);
    while (node->attr_count > 0) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[node->attr_count - 1].name_atom, &name_len);
        th_node_attr_del(tree, node, name, name_len);
    }
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyObject *attrs_setdefault(PyObject *self, PyObject *args) {
    PyObject *key;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTuple(args, "O|O:setdefault", &key, &fallback)) {
        return NULL;
    }
    PyObject *existing = attrs_subscript(self, key);
    if (existing != NULL || !PyErr_ExceptionMatches(PyExc_KeyError)) {
        return existing;
    }
    PyErr_Clear();
    if (attrs_ass_subscript(self, key, fallback) < 0) {
        return NULL;
    }
    return Py_NewRef(fallback);
}

/* Assign every pair of other (a mapping, or an iterable of pairs) and then of kwds, the way dict.update does. The
   pairs are gathered into a dict first, so a malformed argument fails before any attribute changes. */
static int attrs_merge(PyObject *self, PyObject *other, PyObject *kwds) {
    PyObject *pairs = other == NULL ? PyDict_New() : PyObject_CallOneArg((PyObject *)&PyDict_Type, other);
    if (pairs == NULL) {
        return -1;
    }
    int rc = kwds == NULL ? 0 : PyDict_Update(pairs, kwds);
    PyObject *key;
    PyObject *value;
    Py_ssize_t position = 0;
    while (rc == 0 && PyDict_Next(pairs, &position, &key, &value)) {
        rc = attrs_ass_subscript(self, key, value);
    }
    Py_DECREF(pairs);
    return rc;
}

static PyObject *attrs_update(PyObject *self, PyObject *args, PyObject *kwds) {
    PyObject *other = NULL;
    if (!PyArg_UnpackTuple(args, "update", 0, 1, &other) || attrs_merge(self, other, kwds) < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* Whether obj is an attrs view; the dealloc slot identifies the type without the module state. */
static int is_attrs(PyObject *obj) {
    return Py_TYPE(obj)->tp_dealloc == attrs_dealloc;
}

/* Whether obj is a mapping for comparison and |: a dict, a view, or any object with keys(), the test dict.update
   applies to its argument. */
static int is_mapping(PyObject *obj) {
    return PyDict_Check(obj) || is_attrs(obj) || PyObject_HasAttrString(obj, "keys");
}

/* A dict snapshot of obj: the attributes for an attrs view, else dict(obj). */
static PyObject *mapping_to_dict(PyObject *obj) {
    if (is_attrs(obj)) {
        return attrs_to_dict(obj);
    }
    return PyObject_CallOneArg((PyObject *)&PyDict_Type, obj);
}

static PyObject *attrs_richcompare(PyObject *self, PyObject *other, int op) {
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (!is_mapping(other)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *left = attrs_to_dict(self);
    PyObject *right = left == NULL ? NULL : mapping_to_dict(other); /* GCOVR_EXCL_BR_LINE: left is NULL on OOM only */
    PyObject *result = right == NULL ? NULL : PyObject_RichCompare(left, right, op);
    Py_XDECREF(left);
    Py_XDECREF(right);
    return result;
}

/* attrs | mapping and mapping | attrs return a new dict, as dict | dict does; the view itself is unchanged. */
static PyObject *attrs_or(PyObject *left, PyObject *right) {
    if (!is_mapping(left) || !is_mapping(right)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    PyObject *merged = mapping_to_dict(left);
    PyObject *extra = merged == NULL ? NULL : mapping_to_dict(right);
    int rc = extra == NULL ? -1 : PyDict_Update(merged, extra);
    Py_XDECREF(extra);
    if (rc < 0) {
        Py_XDECREF(merged);
        return NULL;
    }
    return merged;
}

static PyObject *attrs_inplace_or(PyObject *self, PyObject *other) {
    if (!is_mapping(other)) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (attrs_merge(self, other, NULL) < 0) {
        return NULL;
    }
    return Py_NewRef(self);
}

static PyMethodDef attrs_methods[] = {
    {"get", attrs_get, METH_VARARGS, "get(name, default=None) -> the value, or default when absent"},
    {"keys", attrs_keys, METH_NOARGS, "keys() -> the attribute names in source order"},
    {"values", attrs_values, METH_NOARGS, "values() -> the attribute values in source order"},
    {"items", attrs_items, METH_NOARGS, "items() -> the (name, value) pairs in source order"},
    {"pop", attrs_pop, METH_VARARGS, "pop(name[, default]) -> remove the attribute and return its value"},
    {"popitem", attrs_popitem, METH_NOARGS, "popitem() -> remove and return the last (name, value) pair"},
    {"setdefault", attrs_setdefault, METH_VARARGS,
     "setdefault(name, default=None) -> the value, setting default first when absent"},
    {"update", (PyCFunction)(void (*)(void))attrs_update, METH_VARARGS | METH_KEYWORDS,
     "update([other], **pairs) -> set every pair of other and then of pairs"},
    {"clear", attrs_clear, METH_NOARGS, "clear() -> remove every attribute"},
    {"copy", attrs_copy, METH_NOARGS, "copy()\n--\n\nReturn a dict snapshot of the attributes."},
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
    {Py_tp_richcompare, attrs_richcompare},
    {Py_tp_hash, PyObject_HashNotImplemented},
    {Py_nb_or, attrs_or},
    {Py_nb_inplace_or, attrs_inplace_or},
    TH_SEALED_END,
};

PyType_Spec attrs_spec = {
    .name = "turbohtml._html._Attrs",
    .basicsize = sizeof(AttrsObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = attrs_slots,
};

PyObject *element_get_tag(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text, node->text_len);
}

static PyObject *element_get_namespace(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(state_of(self)->namespaces[((NodeObject *)self)->node->ns]);
}

PyObject *element_get_attrs(PyObject *self, void *Py_UNUSED(closure)) {
    return attrs_new(state_of(self), ((NodeObject *)self)->handle, ((NodeObject *)self)->node);
}

static int element_set_text(PyObject *self, PyObject *value, void *closure);

static PyObject *element_get_field_value(PyObject *self, void *closure);

static int element_set_field_value(PyObject *self, PyObject *value, void *closure);

static PyObject *element_get_checked(PyObject *self, void *closure);

static int element_set_checked(PyObject *self, PyObject *value, void *closure);

PyDoc_STRVAR(field_value_doc, "the form control's value, with form semantics. Reading returns the value\n"
                              "attribute (defaulting to \"on\" for a checkbox/radio), a textarea's text, an\n"
                              "option's value (its stripped text when it has no value attribute), or the\n"
                              "selected option value(s) of a select (a list[str] when it is multiple, None\n"
                              "when nothing is selected); non-controls read None. Assigning a str writes the\n"
                              "value (selecting the matching option of a select), a list[str] selects a\n"
                              "multiple select, and None clears it. The checked state lives in\n"
                              "Element.checked, not here.");

PyDoc_STRVAR(checked_doc, "whether a checkbox or radio input is checked. Assigning requires a checkbox or\n"
                          "radio; setting a radio to True clears the other same-name radios in the owning\n"
                          "form (or document), the radio-group exclusivity rule.");

static PyGetSetDef element_getset[] = {
    {"tag", element_get_tag, NULL, "the lowercased tag name", NULL},
    {"namespace", element_get_namespace, NULL, "the element's Namespace (HTML, SVG, or MATHML)", NULL},
    {"attrs", element_get_attrs, NULL,
     "the live mutable attribute mapping; token-list attributes (class, rel, ...) map to a list[str], a valueless "
     "attribute maps to None",
     NULL},
    {"text", node_get_text, element_set_text,
     "the element's text; assigning replaces all children with a single Text node", NULL},
    {"field_value", element_get_field_value, element_set_field_value, field_value_doc, NULL},
    {"checked", element_get_checked, element_set_checked, checked_doc, NULL},
    {"shadow_root", element_get_shadow_root, NULL,
     "this element's open shadow root, or None when it has none or the root is closed", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

/* Whether a code-point run equals a lowercase ASCII literal, comparing the run
   case-insensitively (the literal must already be lowercase). */
static int ucs4_iequals_ascii(const Py_UCS4 *value, Py_ssize_t len, const char *ascii) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (ascii[index] == '\0' || lower_ascii(value[index]) != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return ascii[len] == '\0';
}

/* Whether two code-point runs are byte-for-byte equal (control name matching is
   case-sensitive, like form submission). */
static int ucs4_runs_equal(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
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

/* Whether an input element's type attribute equals a lowercase ASCII literal. */
static int input_type_is(th_node *node, const char *name) {
    const th_node_attr *type = find_node_attr(node, TH_ATTR_TYPE);
    return type != NULL && type->value != NULL && ucs4_iequals_ascii(type->value, type->value_len, name);
}

/* The submission category of an input by its type attribute. A missing or
   unrecognized type is text-like, the WHATWG default. */
enum field_kind { FIELD_TEXTLIKE, FIELD_CHECKABLE, FIELD_BUTTONLIKE, FIELD_FILE };

static enum field_kind input_kind(const th_node_attr *type) {
    if (type == NULL || type->value == NULL) {
        return FIELD_TEXTLIKE;
    }
    const Py_UCS4 *value = type->value;
    Py_ssize_t len = type->value_len;
    if (ucs4_iequals_ascii(value, len, "checkbox") || ucs4_iequals_ascii(value, len, "radio")) {
        return FIELD_CHECKABLE;
    }
    if (ucs4_iequals_ascii(value, len, "submit") || ucs4_iequals_ascii(value, len, "reset") ||
        ucs4_iequals_ascii(value, len, "button") || ucs4_iequals_ascii(value, len, "image")) {
        return FIELD_BUTTONLIKE;
    }
    if (ucs4_iequals_ascii(value, len, "file")) {
        return FIELD_FILE;
    }
    return FIELD_TEXTLIKE;
}

/* An attribute's value as a str, or the fallback str when the attribute is absent;
   a present-but-empty (or valueless) attribute is the empty string. */
static PyObject *attr_value_or(const th_node_attr *value, const char *fallback) {
    if (value == NULL) {
        return PyUnicode_FromString(fallback);
    }
    return value->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(value->value, value->value_len);
}

static PyObject *value_attr_or(th_node *node, const char *fallback) {
    return attr_value_or(find_node_attr(node, TH_ATTR_VALUE), fallback);
}

/* A str from a code-point run with leading and trailing ASCII whitespace removed. */
static PyObject *stripped_str(const Py_UCS4 *buffer, Py_ssize_t len) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (start < end && is_space(buffer[start])) {
        start++;
    }
    while (end > start && is_space(buffer[end - 1])) {
        end--;
    }
    return ucs4_to_str(buffer + start, end - start);
}

/* An option's value: its value attribute if present (empty string when valueless),
   else its stripped text content (WHATWG option value rule). */
static PyObject *option_value_str(th_tree *tree, th_node *option) {
    const th_node_attr *value = find_node_attr(option, TH_ATTR_VALUE);
    if (value != NULL) {
        return value->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(value->value, value->value_len);
    }
    Py_ssize_t len;
    Py_UCS4 *buffer = th_node_text(tree, option, &len);
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = stripped_str(buffer, len);
    PyMem_Free(buffer);
    return result;
}

/* The next option element after current within root's subtree, in document order. */
static th_node *next_option(th_node *current, th_node *root) {
    for (th_node *node = preorder_next(current, root); node != NULL; node = preorder_next(node, root)) {
        if (node->atom == TH_TAG_OPTION) { /* only option elements carry this atom (text nodes are TH_TAG_UNKNOWN) */
            return node;
        }
    }
    return NULL;
}

/* WHATWG: an option is disabled if its own disabled attribute is present or it is a
   child of an optgroup whose disabled attribute is present. */
static int option_disabled(th_node *option) {
    if (find_node_attr(option, TH_ATTR_DISABLED) != NULL) {
        return 1;
    }
    /* an option enumerated within a select always has a parent; only optgroup elements
       carry this atom (text and content nodes are TH_TAG_UNKNOWN) */
    th_node *group = option->parent;
    return group->atom == TH_TAG_OPTGROUP && find_node_attr(group, TH_ATTR_DISABLED) != NULL;
}

/* WHATWG display size of a single (non-multiple) select: the parsed non-negative
   integer of its size attribute when that parses to at least one digit, else 1. The
   default-first-option reset fires only when the display size is 1. */
static Py_ssize_t single_select_display_size(th_tree *tree, th_node *select) {
    Py_ssize_t index = find_attr_index(tree, select, "size", 4);
    if (index >= 0) {
        const th_node_attr *size = &select->attrs[index];
        if (size->value != NULL) {
            Py_ssize_t cursor = 0;
            while (cursor < size->value_len && is_space(size->value[cursor])) {
                cursor++;
            }
            Py_ssize_t number = 0;
            int digits = 0;
            while (cursor < size->value_len && size->value[cursor] >= '0' && size->value[cursor] <= '9') {
                number = number * 10 + (size->value[cursor] - '0');
                if (number > 0xffff) { /* a display size past this is meaningless and guards overflow */
                    number = 0xffff;
                }
                cursor++;
                digits = 1;
            }
            if (digits) {
                return number;
            }
        }
    }
    return 1;
}

/* The option a single (non-multiple) select resolves to: the last-marked selection
   (a disabled option still wins, keeping its selectedness), else the default first
   non-disabled option when the display size is 1, else NULL. Callers decide whether a
   disabled result is submittable. */
static th_node *single_select_selection(th_tree *tree, th_node *select) {
    th_node *selected = NULL;
    th_node *first_enabled = NULL;
    for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
        if (find_node_attr(option, TH_ATTR_SELECTED) != NULL) {
            selected = option;
        }
        if (first_enabled == NULL && !option_disabled(option)) {
            first_enabled = option;
        }
    }
    if (selected != NULL) {
        return selected;
    }
    /* parse the size attribute only when an enabled option could actually be the
       default, so an empty or all-disabled select skips the scan */
    return first_enabled != NULL && single_select_display_size(tree, select) == 1 ? first_enabled : NULL;
}

/* The value(s) of a select: a list[str] of the selected options for a multiple
   select, else the resolved option's value, or None when no option is selected. */
static PyObject *select_value(th_tree *tree, th_node *select) {
    if (find_node_attr(select, TH_ATTR_MULTIPLE) != NULL) {
        PyObject *values = PyList_New(0);
        if (values == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
            if (find_node_attr(option, TH_ATTR_SELECTED) == NULL) {
                continue;
            }
            PyObject *value = option_value_str(tree, option);
            if (value == NULL || PyList_Append(values, value) < 0) { /* GCOVR_EXCL_BR_LINE: alloc failure */
                Py_XDECREF(value);                                   /* GCOVR_EXCL_LINE: alloc-failure path */
                Py_DECREF(values);                                   /* GCOVR_EXCL_LINE: alloc-failure path */
                return NULL;                                         /* GCOVR_EXCL_LINE: alloc-failure path */
            }
            Py_DECREF(value);
        }
        return values;
    }
    th_node *use = single_select_selection(tree, select);
    if (use == NULL) {
        Py_RETURN_NONE;
    }
    return option_value_str(tree, use);
}

static PyObject *element_get_field_value(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    th_tree *tree = tree_of(self);
    PyObject *result = NULL;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    switch (node->atom) {
    case TH_TAG_INPUT:
        result = value_attr_or(node, input_kind(find_node_attr(node, TH_ATTR_TYPE)) == FIELD_CHECKABLE ? "on" : "");
        break;
    case TH_TAG_TEXTAREA:
        result = str_from_accessor(th_node_text, tree, node);
        break;
    case TH_TAG_BUTTON:
        result = value_attr_or(node, "");
        break;
    case TH_TAG_OPTION:
        result = option_value_str(tree, node);
        break;
    case TH_TAG_SELECT:
        result = select_value(tree, node);
        break;
    default:
        result = Py_NewRef(Py_None);
        break;
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

/* Write or, when value is None or a deletion, remove the value attribute. */
static int set_value_attr(PyObject *self, th_node *node, PyObject *value) {
    th_tree *tree = tree_of(self);
    if (value == NULL || value == Py_None) {
        Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
        th_node_attr_del(tree, node, "value", 5);
        Py_END_CRITICAL_SECTION();
        return 0;
    }
    if (!PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "field_value must be a str or None");
        return -1;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(value);
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(value);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    rc = th_node_attr_set(tree, node, "value", 5, points, len, 1);
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_attr_set only fails on OOM */
}

/* Replace a textarea's children with a single Text node holding value, or clear it
   when value is None or a deletion. */
static int set_textarea_value(PyObject *self, th_node *node, PyObject *value) {
    int has_text = value != NULL && value != Py_None;
    if (has_text && !PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "field_value must be a str or None");
        return -1;
    }
    Py_UCS4 *points = NULL;
    Py_ssize_t len = 0;
    if (has_text) {
        len = PyUnicode_GET_LENGTH(value);
        points = PyUnicode_AsUCS4Copy(value);
        if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    th_tree *tree = tree_of(self);
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    while (node->first_child != NULL) {
        th_node_remove_observed(tree, node->first_child);
    }
    th_node *text = len > 0 ? th_tree_make_data_node(tree, TH_NODE_TEXT, points, len) : NULL;
    /* GCOVR_EXCL_START: a make_data_node allocation failure cannot be forced from a test */
    if (len > 0 && text == NULL) {
        error = 1;
    }
    /* GCOVR_EXCL_STOP */
    if (text != NULL) {
        th_node_append_child_observed(tree, node, text);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return error ? -1 : 0; /* GCOVR_EXCL_BR_LINE: error is set only on the excluded allocation failure */
}

/* The set of wanted option values for a select assignment: {value} for a single
   select (a str), the list members for a multiple select, or empty for None. */
static PyObject *wanted_values(th_node *select, PyObject *value) {
    int multiple = find_node_attr(select, TH_ATTR_MULTIPLE) != NULL;
    PyObject *wanted = PySet_New(NULL);
    if (wanted == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (value == NULL || value == Py_None) {
        return wanted;
    }
    if (multiple) {
        if (!PyList_Check(value)) {
            PyErr_SetString(PyExc_TypeError, "field_value of a multiple select must be a list of str or None");
            Py_DECREF(wanted);
            return NULL;
        }
        Py_ssize_t count = PyList_GET_SIZE(value);
        for (Py_ssize_t index = 0; index < count; index++) {
            PyObject *item = PyList_GET_ITEM(value, index);
            if (!PyUnicode_Check(item)) {
                PyErr_SetString(PyExc_TypeError, "field_value of a multiple select must be a list of str or None");
                Py_DECREF(wanted);
                return NULL;
            }
            if (PySet_Add(wanted, item) < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Add only fails on OOM */
                Py_DECREF(wanted);             /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                   /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        return wanted;
    }
    if (!PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "field_value of a single select must be a str or None");
        Py_DECREF(wanted);
        return NULL;
    }
    if (PySet_Add(wanted, value) < 0) { /* GCOVR_EXCL_BR_LINE: PySet_Add only fails on OOM */
        Py_DECREF(wanted);              /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wanted;
}

/* Select the options whose value is in wanted (only the first match for a single
   select) and deselect the rest. */
static int set_select_value(PyObject *self, th_node *select, PyObject *value) {
    PyObject *wanted = wanted_values(select, value);
    if (wanted == NULL) {
        return -1;
    }
    int multiple = find_node_attr(select, TH_ATTR_MULTIPLE) != NULL;
    th_tree *tree = tree_of(self);
    int error = 0;
    int selected_one = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
        PyObject *option_value = option_value_str(tree, option);
        if (option_value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            error = 1;              /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int want = PySet_Contains(wanted, option_value);
        Py_DECREF(option_value);
        if (want < 0) { /* GCOVR_EXCL_BR_LINE: membership of a str in a str set cannot raise */
            error = 1;  /* GCOVR_EXCL_LINE: allocation-failure path */
            break;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int select_it = want && (multiple || !selected_one);
        if (select_it) {
            selected_one = 1;
            th_node_attr_set(tree, option, "selected", 8, NULL, 0, 0);
        } else {
            th_node_attr_del(tree, option, "selected", 8);
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(wanted);
    return error ? -1 : 0; /* GCOVR_EXCL_BR_LINE: error is set only on the excluded allocation failures */
}

static int element_set_field_value(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    switch (node->atom) {
    case TH_TAG_INPUT:
    case TH_TAG_BUTTON:
    case TH_TAG_OPTION:
        return set_value_attr(self, node, value);
    case TH_TAG_TEXTAREA:
        return set_textarea_value(self, node, value);
    case TH_TAG_SELECT:
        return set_select_value(self, node, value);
    default:
        PyErr_SetString(PyExc_TypeError, "field_value can only be set on a form control");
        return -1;
    }
}

static PyObject *element_get_checked(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    int present;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    present = find_node_attr(node, TH_ATTR_CHECKED) != NULL;
    Py_END_CRITICAL_SECTION();
    return PyBool_FromLong(present);
}

/* Remove the checked flag from the other same-name radios in the radio's owning
   form (nearest ancestor form, else the document), enforcing group exclusivity. */
static void clear_radio_group(HandleObject *handle, th_node *radio) {
    th_tree *tree = handle->tree;
    const th_node_attr *name = find_node_attr(radio, TH_ATTR_NAME);
    if (name == NULL || name->value == NULL || name->value_len == 0) {
        return;
    }
    th_node *root = radio;
    th_node *form = NULL;
    for (th_node *ancestor = radio->parent; ancestor != NULL; ancestor = ancestor->parent) {
        root = ancestor;
        if (form == NULL && ancestor->type == TH_NODE_ELEMENT && ancestor->atom == TH_TAG_FORM) {
            form = ancestor;
        }
    }
    th_node *scope = form != NULL ? form : root;
    const int indexed = scope == root && handle->index_built && handle_index_usable(handle, root);
    Py_ssize_t cursor = indexed ? handle->index_offsets[TH_TAG_INPUT] : 0;
    const Py_ssize_t end = indexed ? handle->index_offsets[TH_TAG_INPUT + 1] : 0;
    for (th_node *node = indexed ? handle->index_nodes[cursor] : preorder_next(scope, scope); node != NULL;
         node = indexed ? (++cursor < end ? handle->index_nodes[cursor] : NULL) : preorder_next(node, scope)) {
        if (node == radio || node->atom != TH_TAG_INPUT || !input_type_is(node, "radio")) {
            continue;
        }
        const th_node_attr *other = find_node_attr(node, TH_ATTR_NAME);
        if (other != NULL && other->value != NULL &&
            ucs4_runs_equal(name->value, name->value_len, other->value, other->value_len)) {
            th_node_attr_del(tree, node, "checked", 7);
        }
    }
}

static int element_set_checked(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    if (value == NULL) {
        PyErr_SetString(PyExc_TypeError, "cannot delete checked");
        return -1;
    }
    if (node->atom != TH_TAG_INPUT || (!input_type_is(node, "checkbox") && !input_type_is(node, "radio"))) {
        PyErr_SetString(PyExc_TypeError, "checked can only be set on a checkbox or radio input");
        return -1;
    }
    int on = PyObject_IsTrue(value);
    if (on < 0) {
        return -1;
    }
    th_tree *tree = tree_of(self);
    int rc = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    if (on) {
        rc = th_node_attr_set(tree, node, "checked", 7, NULL, 0, 0);
        if (rc >= 0 && input_type_is(node, "radio")) { /* GCOVR_EXCL_BR_LINE: attr_set only fails on OOM */
            clear_radio_group((HandleObject *)((NodeObject *)self)->handle, node);
        }
    } else {
        th_node_attr_del(tree, node, "checked", 7);
    }
    Py_END_CRITICAL_SECTION();
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_attr_set only fails on OOM */
}

static th_node *fieldset_first_legend(th_node *fieldset) {
    for (th_node *child = fieldset->first_child; child != NULL; child = child->next_sibling) {
        if (child->atom == TH_TAG_LEGEND) {
            return child;
        }
    }
    return NULL;
}

/* CPython 3.12+ defers collection callbacks until this C call returns, so the control walk skips a disabled fieldset's
   subtree and no control needs to look up its ancestors. Older CPython, PyPy, and free-threaded builds walk instead. */
#if PY_VERSION_HEX < 0x030C0000 || defined(PYPY_VERSION) || defined(Py_GIL_DISABLED)
#define FORM_WALKS_ANCESTOR_FIELDSETS 1

static int control_in_first_legend(th_node *fieldset, th_node *control) {
    th_node *legend = fieldset_first_legend(fieldset);
    if (legend == NULL) {
        return 0;
    }
    for (th_node *ancestor = control->parent; ancestor != fieldset; ancestor = ancestor->parent) {
        if (ancestor == legend) {
            return 1;
        }
    }
    return 0;
}

/* Whether a disabling fieldset sits between a control and the form. */
static int fieldset_disables(th_node *control, th_node *form) {
    for (th_node *ancestor = control->parent; ancestor != form; ancestor = ancestor->parent) {
        if (ancestor == NULL) {
            return 1;
        }
        if (ancestor->atom == TH_TAG_FIELDSET && find_node_attr(ancestor, TH_ATTR_DISABLED) != NULL &&
            !control_in_first_legend(ancestor, control)) {
            return 1;
        }
    }
    return 0;
}
#endif

/* The attributes a form control's submission reads, found in one pass over its attribute list (an element never
   holds two attributes of one name). */
typedef struct {
    const th_node_attr *name;
    const th_node_attr *type;
    const th_node_attr *value;
    const th_node_attr *checked;
    int disabled;
} control_attrs;

static control_attrs read_control_attrs(const th_node *node) {
    control_attrs found = {0};
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        switch (attr->name_atom) {
        case TH_ATTR_NAME:
            found.name = attr;
            break;
        case TH_ATTR_TYPE:
            found.type = attr;
            break;
        case TH_ATTR_VALUE:
            found.value = attr;
            break;
        case TH_ATTR_CHECKED:
            found.checked = attr;
            break;
        case TH_ATTR_DISABLED:
            found.disabled = 1;
            break;
        default:
            break;
        }
    }
    return found;
}

/* Append a (name, value) pair, taking ownership of value and stealing nothing from
   name. Returns 0, or -1 with an exception set. */
static int emit_pair(PyObject *pairs, const th_node_attr *name, PyObject *value) {
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: a value builder only fails on OOM */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *name_obj = ucs4_to_str(name->value, name->value_len);
    if (name_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(value);   /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *pair = PyTuple_Pack(2, name_obj, value);
    Py_DECREF(name_obj);
    Py_DECREF(value);
    if (pair == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = PyList_Append(pairs, pair);
    Py_DECREF(pair);
    return rc; /* GCOVR_EXCL_BR_LINE: PyList_Append only fails on OOM */
}

/* Append the submitted option(s) of a select as (name, value) pairs: every selected
   non-disabled option for a multiple select, else the resolved single selection. */
static int collect_select(th_tree *tree, th_node *select, const th_node_attr *name, PyObject *pairs) {
    if (find_node_attr(select, TH_ATTR_MULTIPLE) != NULL) {
        for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
            if (option_disabled(option) || find_node_attr(option, TH_ATTR_SELECTED) == NULL) {
                continue;
            }
            if (emit_pair(pairs, name, option_value_str(tree, option)) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
                return -1;                                                    /* GCOVR_EXCL_LINE: alloc-failure */
            }
        }
        return 0;
    }
    th_node *use = single_select_selection(tree, select);
    if (use == NULL || option_disabled(use)) { /* a disabled resolved option is not submittable */
        return 0;
    }
    return emit_pair(pairs, name, option_value_str(tree, use));
}

/* Append node's submission pair(s) to pairs when it is a successful control. */
static int collect_control(th_tree *tree, th_node *form, th_node *node, PyObject *pairs) {
    uint16_t atom = node->atom;
    if (atom != TH_TAG_INPUT && atom != TH_TAG_TEXTAREA && atom != TH_TAG_SELECT) {
        return 0;
    }
    control_attrs attrs = read_control_attrs(node);
    const th_node_attr *name = attrs.name;
    if (name == NULL || name->value == NULL || name->value_len == 0 || attrs.disabled) {
        return 0;
    }
#ifdef FORM_WALKS_ANCESTOR_FIELDSETS
    if (fieldset_disables(node, form)) {
        return 0;
    }
#else
    (void)form;
#endif
    if (atom == TH_TAG_SELECT) {
        return collect_select(tree, node, name, pairs);
    }
    if (atom == TH_TAG_TEXTAREA) {
        return emit_pair(pairs, name, th_form_textarea_value(tree, node));
    }
    enum field_kind kind = input_kind(attrs.type);
    if (kind == FIELD_BUTTONLIKE || kind == FIELD_FILE) {
        return 0;
    }
    if (kind == FIELD_CHECKABLE) {
        return attrs.checked == NULL ? 0 : emit_pair(pairs, name, attr_value_or(attrs.value, "on"));
    }
    return emit_pair(pairs, name, th_form_input_value(node, attrs.type, attrs.value));
}

PyDoc_STRVAR(form_data_doc, "form_data()\n--\n\n"
                            "Collect this form's successful controls, following the WHATWG form-submission\n"
                            "entry-list rules. Controls without a non-empty name, disabled controls (their\n"
                            "own disabled or a disabling ancestor fieldset), buttons, and\n"
                            "file/submit/reset/image inputs are skipped; a checkbox or radio contributes\n"
                            "only when checked, a select one pair per selected non-disabled option (the\n"
                            "default first option only when its display size is 1). An input submits its\n"
                            "value after its type's value sanitization (newlines stripped, url/email\n"
                            "trimmed, an invalid number or date/time value emptied, datetime-local\n"
                            "normalized, range clamped to min/max/step); hidden and color inputs submit the\n"
                            "value attribute as written. A textarea submits its text with CRLF and CR\n"
                            "normalized to LF. Controls inside a datalist or a template's contents are\n"
                            "excluded. Controls are matched by containment in the form.\n\n"
                            ":returns: the (name, value) pairs in document order.");

static th_node *next_form_control(th_node *current, th_node *form) {
    if (current->atom == TH_TAG_FIELDSET && find_node_attr(current, TH_ATTR_DISABLED) != NULL) {
        th_node *legend = fieldset_first_legend(current);
        if (legend != NULL) {
            return legend;
        }
    } else if (current->atom != TH_TAG_TEMPLATE && current->atom != TH_TAG_DATALIST && current->first_child != NULL) {
        return current->first_child;
    }
    while (current != form) {
#if PY_VERSION_HEX < 0x030C0000 || defined(PYPY_VERSION) || defined(Py_GIL_DISABLED)
        if (current == NULL) {
            return NULL;
        }
#endif
        th_node *parent = current->parent;
        /* Pair allocation can run callbacks, so re-read fieldset state before skipping siblings. */
        if (current->next_sibling != NULL && !(current->atom == TH_TAG_LEGEND && parent->atom == TH_TAG_FIELDSET &&
                                               find_node_attr(parent, TH_ATTR_DISABLED) != NULL)) {
            return current->next_sibling;
        }
        current = parent;
    }
    return NULL;
}

static PyObject *element_form_data(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    if (node->atom != TH_TAG_FORM) {
        PyErr_SetString(PyExc_TypeError, "form_data can only be called on a form element");
        return NULL;
    }
    th_tree *tree = tree_of(self);
    PyObject *pairs = PyList_New(0);
    if (pairs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    th_node *control = next_form_control(node, node);
    while (control != NULL) {
        if (collect_control(tree, node, control, pairs) < 0) { /* GCOVR_EXCL_BR_LINE: fails only on OOM */
            error = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        control = next_form_control(control, node);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {          /* GCOVR_EXCL_BR_LINE: error is set only on an allocation failure */
        Py_DECREF(pairs); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return pairs;
}

PyDoc_STRVAR(rows_doc, "rows()\n--\n\n"
                       "Return the table's cells as a list of rows, each a list[str], with rowspan and\n"
                       "colspan resolved by filling every spanned slot with a copy of the cell text.\n"
                       "Rows are padded to a rectangular width; a nested table's rows belong to that\n"
                       "table, not this one. Cell text is the cell's text content with surrounding\n"
                       "whitespace stripped. Raises TypeError on a non-table element.");

static PyObject *element_rows(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    if (node->atom != TH_TAG_TABLE) {
        PyErr_SetString(PyExc_TypeError, "rows can only be called on a table element");
        return NULL;
    }
    return turbohtml_element_table_rows(self, tree_of(self), node);
}

PyDoc_STRVAR(records_doc, "records()\n--\n\n"
                          "Return the table's data rows as a list of dicts, keyed by the first row (the\n"
                          "header, typically the thead row) over each later row, with rowspan and colspan\n"
                          "resolved as in rows(). A table with no rows or only a header yields an empty\n"
                          "list; a duplicated header keeps the rightmost column's value. Pass the result\n"
                          "to pandas.DataFrame for a frame. Raises TypeError on a non-table element.");

static PyObject *element_records(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    if (node->atom != TH_TAG_TABLE) {
        PyErr_SetString(PyExc_TypeError, "records can only be called on a table element");
        return NULL;
    }
    return turbohtml_element_table_records(self, tree_of(self), node);
}

PyDoc_STRVAR(attr_doc, "attr(name, /, default=None)\n--\n\n"
                       "Read one attribute as a single str. The raw value is returned, so a token-list\n"
                       "attribute like class reads back as \"a b c\" rather than a list, and a valueless\n"
                       "attribute reads back as the empty string.\n\n"
                       ":param name: the attribute name.\n"
                       ":param default: value returned when the attribute is absent.\n"
                       ":returns: the attribute value, or default when it is absent.");

static PyObject *element_attr(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *kw[] = {"", "default", NULL};
    PyObject *name_obj;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|O:attr", kw, &name_obj, &fallback)) {
        return NULL;
    }
    Py_ssize_t name_len;
    char *name = attr_key_utf8(tree_of(self), name_obj, &name_len);
    if (name == NULL) {
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    PyObject *value = NULL;
    int absent = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    Py_ssize_t index = find_attr_index(tree_of(self), node->node, name, name_len);
    if (index < 0) {
        absent = 1;
    } else {
        const th_node_attr *attr = &node->node->attrs[index];
        value = attr->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(attr->value, attr->value_len);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(name);
    if (absent) {
        return Py_NewRef(fallback);
    }
    return value;
}

/* Drop the cached element index so the next whole-tree query rebuilds it. Called
   under the handle's critical section from every structural mutator. */
void handle_drop_index(PyObject *handle_obj) {
    HandleObject *handle = (HandleObject *)handle_obj;
    handle_clear_css_cache(handle);
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    handle->index_offsets = NULL;
    handle->index_nodes = NULL;
    handle->index_built = 0;
    path_id_map_free(handle->path_ids);
    handle->path_ids = NULL;
    path_positions_free(handle->path_positions);
    handle->path_positions = NULL;
}

PyDoc_STRVAR(element_doc, "An element node: a tag, a namespace, attributes, and child nodes.\n\n"
                          ":param tag: the tag name.\n"
                          ":param attrs: initial attributes; a list value sets a token-list attribute and\n"
                          "    None a valueless one.\n"
                          ":param children: initial child nodes, appended in order.\n"
                          ":raises TypeError: if tag or an attribute name is not a str, an attribute value\n"
                          "    is not a str, a list of str, or None, or a child is not a node.\n"
                          ":raises ValueError: if the tag or an attribute name carries a character HTML\n"
                          "    forbids there, or if children are given for a void element.");

static PyObject *element_new(PyTypeObject *type, PyObject *args, PyObject *kwds);

static int append_build_children(PyObject *element, PyObject *tag, PyObject *children);

static PyObject *element_append(PyObject *self, PyObject *child);

static PyObject *element_extend(PyObject *self, PyObject *iterable);

static PyObject *element_insert(PyObject *self, PyObject *args);

static PyObject *element_clear(PyObject *self, PyObject *ignored);

static PyObject *element_normalize(PyObject *self, PyObject *ignored);

static PyObject *element_wrap_children(PyObject *self, PyObject *wrapper_obj);

static PyObject *element_set_inner_html(PyObject *self, PyObject *html);

static PyObject *element_set_text_method(PyObject *self, PyObject *value);

static PyObject *element_insert_adjacent_html(PyObject *self, PyObject *args);

PyDoc_STRVAR(append_doc, "append(child, /)\n--\n\n"
                         "Add child as the last child of this element. A node already in a tree is\n"
                         "moved; a node from another tree is adopted by copy.\n\n"
                         ":param child: the node to append.\n"
                         ":raises TypeError: if child is not a node, or is a Document.\n"
                         ":raises ValueError: if child is an ancestor of this element (which would form a\n"
                         "    cycle).");

PyDoc_STRVAR(extend_doc, "extend(children, /)\n--\n\n"
                         "Append every node from the iterable in order, each one moved or adopted\n"
                         "like append().\n\n"
                         ":param children: the nodes to append.\n"
                         ":raises TypeError: if children is not iterable, or a member is not a node or is\n"
                         "    a Document.\n"
                         ":raises ValueError: if a member is an ancestor of this element (which would form\n"
                         "    a cycle).");

PyDoc_STRVAR(insert_doc, "insert(index, child, /)\n--\n\n"
                         "Insert child among this element's children, counted and clamped like\n"
                         "list.insert.\n\n"
                         ":param index: position among the existing children.\n"
                         ":param child: the node to insert.\n"
                         ":raises TypeError: if index is not an int, or child is not a node or is a\n"
                         "    Document.\n"
                         ":raises ValueError: if child is an ancestor of this element (which would form a\n"
                         "    cycle).");

PyDoc_STRVAR(clear_doc, "clear()\n--\n\n"
                        "Detach every child of this element, leaving it empty.");

PyDoc_STRVAR(normalize_doc, "normalize()\n--\n\n"
                            "Merge each run of adjacent Text descendants into one node and drop empty\n"
                            "Text nodes, throughout this element's subtree.");

PyDoc_STRVAR(wrap_children_doc, "wrap_children(wrapper, /)\n--\n\n"
                                "Move every child of this element into wrapper, make wrapper the sole child,\n"
                                "and return it. The bulk form of wrap() for a container's whole content; an\n"
                                "empty element gains an empty wrapper.\n\n"
                                ":param wrapper: the element to move the children into.\n"
                                ":returns: wrapper, now holding the moved children.\n"
                                ":raises TypeError: if wrapper is not an element.");

/* Whether the value run [start, end) equals the token. */
static int class_token_equals(const Py_UCS4 *value, Py_ssize_t start, Py_ssize_t end, const Py_UCS4 *token,
                              Py_ssize_t token_len) {
    if (end - start != token_len) {
        return 0;
    }
    for (Py_ssize_t offset = 0; offset < token_len; offset++) {
        if (value[start + offset] != token[offset]) {
            return 0;
        }
    }
    return 1;
}

/* Whether token appears among value's ASCII-whitespace-separated tokens. */
static int class_has_token(const Py_UCS4 *value, Py_ssize_t value_len, const Py_UCS4 *token, Py_ssize_t token_len) {
    Py_ssize_t cursor = 0;
    while (cursor < value_len) {
        while (cursor < value_len && is_space(value[cursor])) {
            cursor++;
        }
        Py_ssize_t start = cursor;
        while (cursor < value_len && !is_space(value[cursor])) {
            cursor++;
        }
        if (cursor > start && class_token_equals(value, start, cursor, token, token_len)) {
            return 1;
        }
    }
    return 0;
}

/* Decode a class-name argument to a UCS4 buffer the caller frees. Requires a str
   (TypeError otherwise). When require_token is set, also rejects an empty name or
   one carrying ASCII whitespace (ValueError) so the token round-trips through the
   space-separated class value; has_class skips that check, since such a string can
   never equal a stored token. NULL with an exception set on a rejection. */
static Py_UCS4 *class_token_arg(PyObject *arg, Py_ssize_t *out_len, int require_token) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "class name must be a str");
        return NULL;
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);
    if (require_token) {
        if (length == 0) {
            PyErr_SetString(PyExc_ValueError, "class name must not be empty");
            return NULL;
        }
        int kind = PyUnicode_KIND(arg);
        const void *data = PyUnicode_DATA(arg);
        for (Py_ssize_t index = 0; index < length; index++) {
            if (is_space(PyUnicode_READ(kind, data, index))) {
                PyErr_SetString(PyExc_ValueError, "class name must not contain whitespace");
                return NULL;
            }
        }
    }
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(arg);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *out_len = length;
    return points;
}

PyDoc_STRVAR(has_class_doc, "has_class(name, /)\n--\n\n"
                            "Return whether name is one of this element's space-separated class tokens.");

static PyObject *element_has_class(PyObject *self, PyObject *arg) {
    Py_ssize_t token_len;
    Py_UCS4 *token = class_token_arg(arg, &token_len, 0);
    if (token == NULL) {
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    int present = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    const th_node_attr *attr = find_node_attr(node->node, TH_ATTR_CLASS);
    if (attr != NULL && attr->value != NULL) {
        present = class_has_token(attr->value, attr->value_len, token, token_len);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(token);
    return PyBool_FromLong(present);
}

/* The three mutating classList operations. */
enum class_op { CLASS_ADD, CLASS_REMOVE, CLASS_TOGGLE };

/* Apply op for token to self's class attribute, rewriting it as single-space-
   separated tokens (so redundant whitespace collapses, matching an attrs write).
   Returns a new reference to self, or NULL with an exception set. */
static PyObject *class_mutate(PyObject *self, PyObject *arg, enum class_op op) {
    Py_ssize_t token_len;
    Py_UCS4 *token = class_token_arg(arg, &token_len, 1);
    if (token == NULL) {
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    th_tree *tree = tree_of(self);
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    const th_node_attr *attr = find_node_attr(node->node, TH_ATTR_CLASS);
    const Py_UCS4 *value = attr != NULL ? attr->value : NULL;
    Py_ssize_t value_len = value != NULL ? attr->value_len : 0;
    int present = value != NULL && class_has_token(value, value_len, token, token_len);
    int write = op == CLASS_TOGGLE || (op == CLASS_ADD && !present) || (op == CLASS_REMOVE && present);
    Py_UCS4 *rebuilt = write ? PyMem_Malloc((size_t)(value_len + token_len + 1) * sizeof(Py_UCS4)) : NULL;
    if (write && rebuilt == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        failed = 1;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: fall-through brace of the unforceable alloc-failure arm */
    if (rebuilt != NULL) {
        Py_ssize_t out_len = 0;
        Py_ssize_t cursor = 0;
        while (cursor < value_len) {
            while (cursor < value_len && is_space(value[cursor])) {
                cursor++;
            }
            Py_ssize_t start = cursor;
            while (cursor < value_len && !is_space(value[cursor])) {
                cursor++;
            }
            if (cursor == start || (present && class_token_equals(value, start, cursor, token, token_len))) {
                continue;
            }
            if (out_len > 0) {
                rebuilt[out_len++] = ' ';
            }
            for (Py_ssize_t offset = start; offset < cursor; offset++) {
                rebuilt[out_len++] = value[offset];
            }
        }
        if (!present) {
            if (out_len > 0) {
                rebuilt[out_len++] = ' ';
            }
            for (Py_ssize_t offset = 0; offset < token_len; offset++) {
                rebuilt[out_len++] = token[offset];
            }
        }
        failed = th_node_attr_set(tree, node->node, "class", 5, rebuilt, out_len, 1);
        PyMem_Free(rebuilt);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(token);
    if (failed) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return Py_NewRef(self);
}

PyDoc_STRVAR(add_class_doc, "add_class(name, /)\n--\n\n"
                            "Add name to this element's class tokens when absent and return the element.\n"
                            "Existing tokens keep their order; on a change the class value is rewritten\n"
                            "with single-space separators.\n\n"
                            ":raises TypeError: if name is not a str.\n"
                            ":raises ValueError: if name is empty or contains whitespace.");

static PyObject *element_add_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_ADD);
}

PyDoc_STRVAR(remove_class_doc, "remove_class(name, /)\n--\n\n"
                               "Remove every occurrence of name from this element's class tokens and return\n"
                               "the element. Removing the last token leaves an empty class attribute.\n\n"
                               ":raises TypeError: if name is not a str.\n"
                               ":raises ValueError: if name is empty or contains whitespace.");

static PyObject *element_remove_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_REMOVE);
}

PyDoc_STRVAR(toggle_class_doc, "toggle_class(name, /)\n--\n\n"
                               "Remove name from this element's class tokens when present, add it when\n"
                               "absent, and return the element.\n\n"
                               ":raises TypeError: if name is not a str.\n"
                               ":raises ValueError: if name is empty or contains whitespace.");

static PyObject *element_toggle_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_TOGGLE);
}

PyDoc_STRVAR(set_inner_html_doc, "set_inner_html(html, /)\n--\n\n"
                                 "Replace this element's children with the nodes parsed from html, a fragment\n"
                                 "parsed in this element's own context (the DOM innerHTML= setter). In an\n"
                                 "HTML tree the string is run through the same HTML parser as parse(), so\n"
                                 "malformed markup is repaired the same way; in a parse_xml tree it is parsed\n"
                                 "as XML with the namespace declarations in scope, and must be well-formed.\n\n"
                                 ":raises TypeError: if html is not a str.\n"
                                 ":raises HTMLParseError: if this element is in a parse_xml tree and html is\n"
                                 "    not well-formed XML; the children are left unchanged.");

PyDoc_STRVAR(set_text_doc, "set_text(text, /)\n--\n\n"
                           "Replace this element's children with a single Text node holding text\n"
                           "verbatim (the DOM textContent= setter). text is never parsed, so any markup\n"
                           "in it is escaped on serialization. The Element.text= setter is equivalent.\n\n"
                           ":raises TypeError: if text is not a str.");

PyDoc_STRVAR(insert_adjacent_html_doc, "insert_adjacent_html(position, html, /)\n--\n\n"
                                       "Parse html as a fragment and insert it relative to this element at position,\n"
                                       "one of 'beforebegin', 'afterbegin', 'beforeend', or 'afterend' (the DOM\n"
                                       "insertAdjacentHTML, matched case-insensitively). 'beforebegin' and 'afterend'\n"
                                       "place the nodes among this element's siblings, so they require an element\n"
                                       "parent; 'afterbegin' and 'beforeend' add them as the first or last children.\n"
                                       "The fragment parses in the context of the element that will hold it.\n\n"
                                       ":raises TypeError: if position or html is not a str.\n"
                                       ":raises ValueError: if position is not one of the four keywords, or a\n"
                                       "    sibling-relative position is used on a node without an element parent.");

/* A growable code-point buffer for assembling a path string. failed records an
   allocation failure so the caller raises once, after the walk. */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} path_buf;

/* Grow so at least extra more code points fit, doubling for amortized O(1). */
static void path_reserve(path_buf *buf, Py_ssize_t extra) {
    if (buf->len + extra <= buf->cap) {
        return;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)(buf->len + extra), (size_t)buf->cap, 64, sizeof(Py_UCS4), &cap, &bytes);
    if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        buf->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        return;          /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    Py_UCS4 *grown = PyMem_Realloc(buf->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        buf->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buf->data = grown;
    buf->cap = (Py_ssize_t)cap;
}

static void path_put_ucs4(path_buf *buf, const Py_UCS4 *text, Py_ssize_t len) {
    path_reserve(buf, len);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(buf->data + buf->len, text, (size_t)len * sizeof(Py_UCS4));
    buf->len += len;
}

static void path_puts(path_buf *buf, const char *ascii) {
    Py_ssize_t len = (Py_ssize_t)strlen(ascii);
    path_reserve(buf, len);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        buf->data[buf->len + index] = (Py_UCS4)(unsigned char)ascii[index];
    }
    buf->len += len;
}

/* Write a positive integer in decimal. */
static void path_put_int(path_buf *buf, Py_ssize_t value) {
    char digits[20];
    int count = 0;
    do {
        digits[count++] = (char)('0' + (int)(value % 10));
        value /= 10;
    } while (value > 0);
    path_reserve(buf, count);
    if (buf->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    while (count > 0) {
        buf->data[buf->len++] = (Py_UCS4)digits[--count];
    }
}

/* Whether an id value is a bare CSS identifier the selector parser reads back
   verbatim (every code point an identifier char), so #value round-trips with no
   escaping. An empty value (a valueless id) never qualifies. */
static int path_id_safe(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!sel_is_ident(value[index])) {
            return 0;
        }
    }
    return 1;
}

/* FNV-1a over an id value, folding case in quirks mode so values that the id
   selector treats as equal hash equal. */
static uint64_t path_id_hash(const Py_UCS4 *value, Py_ssize_t len, int ci) {
    uint64_t hash = 14695981039346656037u;
    for (Py_ssize_t index = 0; index < len; index++) {
        hash ^= (uint64_t)sel_fold(value[index], ci);
        hash *= 1099511628211u;
    }
    return hash;
}

/* Build the document's id-occurrence map: every element's id value mapped to how
   many elements carry it, so the anchor test is an O(id-length) probe instead of a
   whole-document scan. ci folds id case the way the quirks-mode id selector does.
   Returns the map (the caller caches it) or NULL on allocation failure. */
static path_id_map *path_id_map_build(th_tree *tree, th_node *document) {
    Py_ssize_t id_count = 0;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        const th_node_attr *id = node->type == TH_NODE_ELEMENT ? find_node_attr(node, TH_ATTR_ID) : NULL;
        if (id != NULL && id->value != NULL) {
            id_count++;
        }
    }
    size_t capacity = 8;
    while (capacity < (size_t)id_count * 2) {
        capacity *= 2;
    }
    path_id_map *map = PyMem_Malloc(sizeof(path_id_map));
    if (map == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    map->slots = PyMem_Calloc(capacity, sizeof(path_id_slot));
    if (map->slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(map);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    map->mask = capacity - 1;
    map->id_version = th_tree_id_version(tree);
    const int ci = th_tree_quirks(tree);
    map->ci = ci;
    for (th_node *node = document->first_child; node != NULL; node = preorder_next(node, document)) {
        const th_node_attr *id = node->type == TH_NODE_ELEMENT ? find_node_attr(node, TH_ATTR_ID) : NULL;
        if (id == NULL || id->value == NULL) {
            continue;
        }
        size_t slot = (size_t)path_id_hash(id->value, id->value_len, ci) & map->mask;
        while (map->slots[slot].value != NULL &&
               !sel_eq(map->slots[slot].value, map->slots[slot].len, id->value, id->value_len, ci)) {
            slot = (slot + 1) & map->mask;
        }
        if (map->slots[slot].value == NULL) {
            map->slots[slot].value = id->value;
            map->slots[slot].len = id->value_len;
            map->slots[slot].count = 1;
        } else {
            map->slots[slot].count++;
        }
    }
    return map;
}

/* Whether value names exactly one element, so #value selects it alone. The probed
   candidate's own id is always present in the map, so a count of one means unique;
   sel_eq returns false on an empty slot (a length mismatch) so the probe walks past
   any collision and always terminates on the candidate's slot. */
static int path_id_unique(const path_id_map *map, const Py_UCS4 *value, Py_ssize_t len) {
    size_t slot = (size_t)path_id_hash(value, len, map->ci) & map->mask;
    while (!sel_eq(map->slots[slot].value, map->slots[slot].len, value, len, map->ci)) {
        slot = (slot + 1) & map->mask;
    }
    return map->slots[slot].count == 1;
}

void path_positions_free(void *positions) {
    th_node_map *const map = positions;
    if (map != NULL) {
        PyMem_Free(map->entries);
        PyMem_Free(map);
    }
}

static int path_step_index(HandleObject *handle, th_node *node, int *needs_index) {
    th_node_map *map = handle->path_positions;
    if (map == NULL) {
        map = PyMem_Calloc(1, sizeof(*map));
        if (map == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;     /* GCOVR_EXCL_LINE: allocation failure */
        }
        handle->path_positions = map;
    }
    Py_ssize_t value = th_node_map_find(map, node);
    if (value == 0) {
        th_node *previous = node->prev_sibling;
        while (previous != NULL && (previous->type != TH_NODE_ELEMENT || !sel_same_type(node, previous))) {
            previous = previous->prev_sibling;
        }
        value = previous == NULL ? 1 : th_node_map_find(map, previous) + 1;
        if (previous != NULL && value == 1) {
            value = 2;
            for (th_node *sibling = previous->prev_sibling; sibling != NULL; sibling = sibling->prev_sibling) {
                if (sibling->type == TH_NODE_ELEMENT && sel_same_type(node, sibling)) {
                    value++;
                }
            }
        }
        if (th_node_map_insert(map, node, value) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                  /* GCOVR_EXCL_LINE: allocation failure */
        }
    }
    *needs_index = value > 1 || !sel_no_sibling(node, 1, 1);
    return (int)value;
}

/* Snapshot the element ancestor chain, node first up to the topmost element
   (its parent is the document or a non-element). Returns the count, fills *out
   with a PyMem array the caller frees, or -1 on allocation failure. */
static Py_ssize_t path_collect_chain(th_node *node, th_node ***out) {
    Py_ssize_t depth = 0;
    for (th_node *cursor = node; cursor != NULL && cursor->type == TH_NODE_ELEMENT; cursor = cursor->parent) {
        depth++;
    }
    th_node **chain = PyMem_Malloc((size_t)depth * sizeof(th_node *));
    if (chain == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    for (th_node *cursor = node; cursor != NULL && cursor->type == TH_NODE_ELEMENT; cursor = cursor->parent) {
        chain[count++] = cursor;
    }
    *out = chain;
    return count;
}

PyDoc_STRVAR(css_path_doc, "css_path()\n--\n\n"
                           "Return a CSS selector that uniquely locates this element from the document\n"
                           "root, the way browser devtools \"copy selector\" does. The path is anchored at\n"
                           "the nearest ancestor (or the element itself) carrying a document-unique id\n"
                           "(\"#main > ...\"), otherwise it descends from the root with positional\n"
                           ":nth-of-type() steps (\"html > body > div > p:nth-of-type(3)\"). Feeding the\n"
                           "result back to select() on the document returns exactly this element.");

static PyObject *element_css_path(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *node = ((NodeObject *)self)->node;
    path_buf buf = {0};
    th_node **chain = NULL;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle);
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    th_node *document = th_tree_document(tree);
    Py_ssize_t count = path_collect_chain(node, &chain);
    if (handle_obj->path_ids != NULL && handle_obj->path_ids->id_version != th_tree_id_version(tree)) {
        path_id_map_free(handle_obj->path_ids);
        handle_obj->path_ids = NULL;
    }
    if (document != NULL && handle_obj->path_ids == NULL) {
        handle_obj->path_ids = path_id_map_build(tree, document);
    }
    if (count < 0 || (document != NULL && handle_obj->path_ids == NULL)) { /* GCOVR_EXCL_BR_LINE: alloc failure */
        error = 1;                                                         /* GCOVR_EXCL_LINE: alloc-failure */
    } else { /* GCOVR_EXCL_LINE: brace of the alloc-failure branch */
        const path_id_map *id_map = handle_obj->path_ids;
        Py_ssize_t top = count - 1;
        int anchored = 0;
        for (Py_ssize_t index = 0; index < count; index++) {
            const th_node_attr *id = find_node_attr(chain[index], TH_ATTR_ID);
            if (id != NULL && id->value != NULL && path_id_safe(id->value, id->value_len) && id_map != NULL &&
                path_id_unique(id_map, id->value, id->value_len)) {
                top = index;
                anchored = 1;
                break;
            }
        }
        for (Py_ssize_t index = top; index >= 0; index--) {
            th_node *element = chain[index];
            if (index != top) {
                path_puts(&buf, " > ");
            }
            if (anchored && index == top) {
                const th_node_attr *id = find_node_attr(element, TH_ATTR_ID);
                path_puts(&buf, "#");
                path_put_ucs4(&buf, id->value, id->value_len);
            } else {
                path_put_ucs4(&buf, element->text, element->text_len);
                int needs_index;
                int sibling_index = path_step_index(handle_obj, element, &needs_index);
                if (sibling_index < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    error = 1;           /* GCOVR_EXCL_LINE: allocation failure */
                    break;               /* GCOVR_EXCL_LINE: allocation failure */
                }
                if (needs_index) {
                    path_puts(&buf, ":nth-of-type(");
                    path_put_int(&buf, sibling_index);
                    path_puts(&buf, ")");
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(chain);
    if (error || buf.failed) {   /* GCOVR_EXCL_BR_LINE: set only on an allocation failure */
        PyMem_Free(buf.data);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(buf.data, buf.len);
    PyMem_Free(buf.data);
    return result;
}

PyDoc_STRVAR(xpath_path_doc, "xpath_path()\n--\n\n"
                             "Return the positional XPath that locates this element from the document\n"
                             "root, like lxml's getroottree().getpath(). Each step is the tag name with a\n"
                             "1-based [n] index among same-name siblings when more than one exists\n"
                             "(\"/html/body/div[2]/p[3]\"). Feeding the result back to xpath() on the\n"
                             "document returns exactly this element.");

static PyObject *element_xpath_path(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    path_buf buf = {0};
    th_node **chain = NULL;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    Py_ssize_t count = path_collect_chain(node, &chain);
    if (count < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
    } else {         /* GCOVR_EXCL_LINE: brace of the never-taken alloc-failure branch */
        for (Py_ssize_t index = count - 1; index >= 0; index--) {
            th_node *element = chain[index];
            path_puts(&buf, "/");
            path_put_ucs4(&buf, element->text, element->text_len);
            int needs_index;
            int sibling_index = path_step_index((HandleObject *)((NodeObject *)self)->handle, element, &needs_index);
            if (sibling_index < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                error = 1;           /* GCOVR_EXCL_LINE: allocation failure */
                break;               /* GCOVR_EXCL_LINE: allocation failure */
            }
            if (needs_index) {
                path_puts(&buf, "[");
                path_put_int(&buf, sibling_index);
                path_puts(&buf, "]");
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(chain);
    if (error || buf.failed) {   /* GCOVR_EXCL_BR_LINE: set only on an allocation failure */
        PyMem_Free(buf.data);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = ucs4_to_str(buf.data, buf.len);
    PyMem_Free(buf.data);
    return result;
}

PyDoc_STRVAR(attach_shadow_doc, "attach_shadow(mode='open')\n--\n\n"
                                "Attach a shadow root to this element and return it, the DOM attachShadow.\n\n"
                                "The shadow root is a document-fragment-like container held off the light\n"
                                "tree, so it never appears among this element's children or in its\n"
                                "serialization; build its content with ShadowRoot.set_inner_html or append.\n\n"
                                ":param mode: 'open' (Element.shadow_root exposes the root) or 'closed' (it\n"
                                "    reads None, so only this returned reference reaches the shadow tree).\n"
                                ":returns: the new ShadowRoot.\n"
                                ":raises ValueError: if mode is not 'open' or 'closed', or the element already\n"
                                "    has a shadow root.");

PyDoc_STRVAR(assigned_nodes_doc, "assigned_nodes(*, flatten=False)\n--\n\n"
                                 "The nodes assigned to this <slot>, in tree order (the DOM assignedNodes).\n\n"
                                 "A slot is assigned the host's direct child nodes whose slot name matches its\n"
                                 "own name attribute (the default slot has the empty name). With flatten, empty\n"
                                 "slots fall back to their own children and nested shadow slots are expanded.\n\n"
                                 ":param flatten: return the flattened assignment instead of the direct one.\n"
                                 ":returns: the assigned nodes as a list.\n"
                                 ":raises TypeError: if the element is not a <slot>.");

PyDoc_STRVAR(assigned_elements_doc, "assigned_elements(*, flatten=False)\n--\n\n"
                                    "The elements assigned to this <slot> (assigned_nodes without the Text\n"
                                    "nodes), the DOM assignedElements.\n\n"
                                    ":param flatten: return the flattened assignment instead of the direct one.\n"
                                    ":returns: the assigned elements as a list.\n"
                                    ":raises TypeError: if the element is not a <slot>.");

static PyMethodDef element_methods[] = {
    {"append", element_append, METH_O, append_doc},
    {"extend", element_extend, METH_O, extend_doc},
    {"insert", element_insert, METH_VARARGS, insert_doc},
    {"clear", element_clear, METH_NOARGS, clear_doc},
    {"normalize", element_normalize, METH_NOARGS, normalize_doc},
    {"wrap_children", element_wrap_children, METH_O, wrap_children_doc},
    {"set_inner_html", element_set_inner_html, METH_O, set_inner_html_doc},
    {"set_text", element_set_text_method, METH_O, set_text_doc},
    {"insert_adjacent_html", element_insert_adjacent_html, METH_VARARGS, insert_adjacent_html_doc},
    {"form_data", element_form_data, METH_NOARGS, form_data_doc},
    {"rows", element_rows, METH_NOARGS, rows_doc},
    {"records", element_records, METH_NOARGS, records_doc},
    {"css_path", element_css_path, METH_NOARGS, css_path_doc},
    {"xpath_path", element_xpath_path, METH_NOARGS, xpath_path_doc},
    {"attr", (PyCFunction)(void (*)(void))element_attr, METH_VARARGS | METH_KEYWORDS, attr_doc},
    {"has_class", element_has_class, METH_O, has_class_doc},
    {"add_class", element_add_class, METH_O, add_class_doc},
    {"remove_class", element_remove_class, METH_O, remove_class_doc},
    {"toggle_class", element_toggle_class, METH_O, toggle_class_doc},
    {"attach_shadow", (PyCFunction)(void (*)(void))element_attach_shadow, METH_VARARGS | METH_KEYWORDS,
     attach_shadow_doc},
    {"assigned_nodes", (PyCFunction)(void (*)(void))element_assigned_nodes, METH_VARARGS | METH_KEYWORDS,
     assigned_nodes_doc},
    {"assigned_elements", (PyCFunction)(void (*)(void))element_assigned_elements, METH_VARARGS | METH_KEYWORDS,
     assigned_elements_doc},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot element_slots[] = {
    {Py_tp_doc, (void *)element_doc},
    {Py_tp_getset, element_getset},
    {Py_tp_methods, element_methods},
    {Py_tp_new, element_new},
    {0, NULL},
};

PyType_Spec element_spec = {
    .name = "turbohtml._html.Element",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = element_slots,
};

/* Reject a tag or attribute name the HTML spec forbids, the way DOM
   createElement / setAttribute raise InvalidCharacterError: empty, or carrying
   whitespace, a control, "/", ">" or "<" (none of which round-trip through the
   tokenizer), plus "=" or a quote in an attribute name. "<" is rejected in an
   attribute name too: it is an unexpected-character-in-attribute-name parse
   error, so a name carrying it reparses differently and is non-conforming. */
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
        /* =, ", and ' break a tag name's serialization just as they do an attribute
           name's, so both reject them (a tag <a"b> would round-trip as malformed markup) */
        int bad = character <= ' ' || character == '/' || character == '>' || character == '<' || character == '=' ||
                  character == '"' || character == '\'';
        if (bad) {
            PyObject *ch = PyUnicode_FromOrdinal((int)character);
            if (ch != NULL) { /* GCOVR_EXCL_BR_LINE: a forbidden character is ASCII and always builds */
                PyErr_Format(PyExc_ValueError, "%s name %R contains an invalid character: %R",
                             is_attr ? "attribute" : "tag", name, ch);
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

/* Whether one of the first filled attributes of node is named name. */
static int has_attr_named(th_tree *tree, const th_node *node, Py_ssize_t filled, const char *name,
                          Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < filled; index++) {
        Py_ssize_t existing_len;
        const char *existing = th_attr_name(tree, node->attrs[index].name_atom, &existing_len);
        if (existing_len == name_len && memcmp(existing, name, (size_t)name_len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Fill a constructed element's attribute slots from the keys of attrs. fold lowercases
   each name for an HTML tree; an XML tree keeps case, so its names are stored verbatim.
   Two keys that fold to one name keep the first, as the HTML tokenizer does for a
   repeated attribute, so the element never carries the same name twice. */
static int fill_element_attrs(th_tree *tree, th_node *node, PyObject *attrs, PyObject *keys, int fold) {
    Py_ssize_t count = PyList_GET_SIZE(keys);
    Py_ssize_t filled = 0;
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
        char *stored = PyMem_Malloc((size_t)name_len);
        if (stored == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t byte = 0; byte < name_len; byte++) {
            char ch = name_utf8[byte];
            stored[byte] = fold && ch >= 'A' && ch <= 'Z' ? (char)(ch + 32) : ch;
        }
        if (fold && has_attr_named(tree, node, filled, stored, name_len)) {
            PyMem_Free(stored);
            continue;
        }
        PyObject *value = PyObject_GetItem(attrs, name);
        Py_UCS4 *points;
        Py_ssize_t value_len;
        int has_value;
        int bad = value == NULL || element_attr_value(value, &points, &value_len, &has_value) < 0;
        Py_XDECREF(value);
        if (bad) {
            PyMem_Free(stored);
            return -1;
        }
        int rc = th_tree_set_attr(tree, node, filled++, stored, name_len, points, value_len, has_value);
        PyMem_Free(stored);
        PyMem_Free(points);
        if (rc < 0) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    node->attr_count = filled;
    return 0;
}

/* On-stack scratch for ASCII-lowercasing a tag before the atom lookup; a tag
   whose UTF-8 exceeds this is treated as an unknown atom. */
#define ELEMENT_TAG_LOWER_STACK_BYTES 64

/* Build an Element wrapper for tag with attrs, without the public constructor's
   name validation. The parser and pickle reconstruction produce tag names (e.g.
   "a<b" from malformed input) that Element() rejects but that must round-trip
   unchanged, so the trusted callers reach the element through this helper. xml keeps
   the tag and attribute names case-sensitive (no fold, no builtin atom), so an element
   unpickled from an XML tree matches parse_xml's storage. keep_case keeps the spelling
   of an HTML-tree element's names while still resolving its atom, for an unpickled SVG
   or MathML element whose parser-adjusted case (foreignObject, viewBox) must survive. */
PyObject *make_element(PyTypeObject *type, PyObject *tag, PyObject *attrs, int xml, int keep_case) {
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
    th_tree_set_xml(tree, xml);
    uint16_t atom = TH_TAG_UNKNOWN;
    if (!xml) { /* an XML tree stores every element as an unknown atom, keeping its spelling */
        Py_ssize_t utf8_len;
        const char *utf8 = PyUnicode_AsUTF8AndSize(tag, &utf8_len);
        char stack[ELEMENT_TAG_LOWER_STACK_BYTES];
        if (utf8 != NULL && utf8_len <= (Py_ssize_t)sizeof(stack)) {
            for (Py_ssize_t byte = 0; byte < utf8_len; byte++) {
                stack[byte] = utf8[byte] >= 'A' && utf8[byte] <= 'Z' ? (char)(utf8[byte] + 32) : utf8[byte];
            }
            atom = th_tag_lookup(stack, utf8_len);
        } else {
            PyErr_Clear(); /* a surrogate or very long custom tag is not in the table */
        }
    }
    Py_UCS4 *tag_points = atom == TH_TAG_UNKNOWN || keep_case ? PyUnicode_AsUCS4Copy(tag) : NULL;
    if ((atom == TH_TAG_UNKNOWN || keep_case) && tag_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        th_tree_free(tree);                                            /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(keys);                                              /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* An HTML unknown tag is ASCII-lowercased to match what the parser stores; a known
       name already points at its lowercase entry, and an XML name keeps its case. */
    for (Py_ssize_t index = 0; !xml && !keep_case && index < tag_len && tag_points != NULL; index++) {
        if (tag_points[index] >= 'A' && tag_points[index] <= 'Z') {
            tag_points[index] += 32;
        }
    }
    th_node *node = th_tree_make_element(tree, tag_points, tag_len, atom, attr_count);
    PyMem_Free(tag_points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(keys);        /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (keys != NULL && fill_element_attrs(tree, node, attrs, keys, !xml && !keep_case) < 0) {
        th_tree_free(tree);
        Py_DECREF(keys);
        return NULL;
    }
    Py_XDECREF(keys);
    return wrap_fresh_tree_node(state, tree, node);
}

static PyObject *element_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"tag", "attrs", "children", NULL};
    PyObject *tag;
    PyObject *attrs = NULL;
    PyObject *children = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|OO", keywords, &tag, &attrs, &children)) {
        return NULL;
    }
    if (!PyUnicode_Check(tag)) {
        PyErr_Format(PyExc_TypeError, "tag must be a str, not %.80s", Py_TYPE(tag)->tp_name);
        return NULL;
    }
    if (validate_name(tag, 0) < 0) {
        return NULL;
    }
    PyObject *element = make_element(type, tag, attrs, 0, 0); /* the public constructor builds HTML elements */
    if (element == NULL) {
        return NULL;
    }
    if (children != NULL && children != Py_None && append_build_children(element, tag, children) < 0) {
        Py_DECREF(element);
        return NULL;
    }
    return element;
}

/* Deep-copy child, a node of another tree, into dest_handle's tree and re-point its
   wrapper at the copy, so the source tree frees on its own. The caller holds
   dest_handle's critical section; taking the source tree's lock as well suspends
   that section while it waits, so another thread can edit the destination tree in
   between and the caller must not trust tree state it read before this call.
   NULL with MemoryError on allocation failure. */
static th_node *import_node(PyObject *dest_handle, NodeObject *child) {
    th_tree *dest_tree = ((HandleObject *)dest_handle)->tree;
    PyObject *source_handle = child->handle;
#ifdef Py_GIL_DISABLED
    Py_INCREF(source_handle);
#endif
    th_node *copy;
    Py_BEGIN_CRITICAL_SECTION2(dest_handle, source_handle);
    copy = th_tree_adopt_copy(dest_tree, tree_of((PyObject *)child), child->node);
    if (copy != NULL && /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        handle_add_hash_override((HandleObject *)dest_handle, copy,
                                 handle_node_hash((HandleObject *)source_handle, child->node)) == 0) {
        handle_drop_index(source_handle);
        th_node_remove_observed(tree_of((PyObject *)child), child->node);
        Py_SETREF(child->handle, Py_NewRef(dest_handle));
        child->node = copy;
    } else {
        copy = NULL; /* GCOVR_EXCL_LINE */
    }
    Py_END_CRITICAL_SECTION2();
#ifdef Py_GIL_DISABLED
    Py_DECREF(source_handle);
#endif
    if (copy == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* a reader may have rebuilt the index while the section was suspended */
    handle_drop_index(dest_handle);
    return copy;
}

/* Whether obj is a DocumentFragment (a fragment or a shadow root): inserting one inserts its children in its place
   and leaves it empty (DOM insert). */
static int is_fragment_arg(module_state *state, PyObject *obj) {
    return PyObject_TypeCheck(obj, (PyTypeObject *)state->document_fragment_type);
}

/* Copy the children of fragment, a DocumentFragment of another tree, into a new fragment of dest_handle's tree and
   empty the source, as inserting a fragment leaves it. The argument keeps its own node, so a shadow root stays
   attached to its host; the returned wrapper of the local copy takes its place in the insertion. NULL with an
   exception on allocation failure. */
static PyObject *import_fragment_children(PyObject *dest_handle, NodeObject *fragment) {
    th_tree *dest_tree = ((HandleObject *)dest_handle)->tree;
    PyObject *source_handle = fragment->handle;
#ifdef Py_GIL_DISABLED
    Py_INCREF(source_handle);
#endif
    th_node *copy;
    Py_BEGIN_CRITICAL_SECTION2(dest_handle, source_handle);
    copy = th_tree_make_fragment(dest_tree);
    /* copy is NULL only on allocation failure */
    for (th_node *child = fragment->node->first_child; copy != NULL && child != NULL; /* GCOVR_EXCL_BR_LINE */
         child = child->next_sibling) {
        th_node *child_copy = th_tree_copy_node(dest_tree, tree_of((PyObject *)fragment), child);
        if (child_copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            copy = NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        th_node_append_child(copy, child_copy);
    }
    if (copy != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        handle_drop_index(source_handle);
        while (fragment->node->first_child != NULL) {
            th_node_remove_observed(tree_of((PyObject *)fragment), fragment->node->first_child);
        }
    }
    Py_END_CRITICAL_SECTION2();
#ifdef Py_GIL_DISABLED
    Py_DECREF(source_handle);
#endif
    if (copy == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* a reader may have rebuilt the index while the section was suspended */
    handle_drop_index(dest_handle);
    return node_wrap(state_of(dest_handle), dest_handle, copy);
}

/* Prepare wrapper_obj, an element the wrap methods link in, to become a child of dest_parent in anchor's tree and
   return the th_node to link (already detached from any old position). A node in the same tree is moved in place, its
   wrapper unchanged; a node from another tree is imported (see import_node). NULL with an exception on a cycle
   (making a node a descendant of itself) or allocation failure. */
static th_node *adopt_into(NodeObject *anchor, th_node *dest_parent, PyObject *wrapper_obj) {
    NodeObject *child = (NodeObject *)wrapper_obj;
    th_tree *dest_tree = tree_of((PyObject *)anchor);
    if (dest_tree != tree_of(wrapper_obj)) {
        return import_node(anchor->handle, child);
    }
    if (th_node_contains(dest_tree, child->node, dest_parent)) {
        PyErr_SetString(PyExc_ValueError, "cannot insert a node into its own subtree");
        return NULL;
    }
    th_node_remove_observed(dest_tree, child->node);
    return child->node;
}

/* adopt_into for a child the caller has not checked: NULL with a TypeError on a non-node or a Document. */
th_node *adopt_child(NodeObject *anchor, th_node *dest_parent, PyObject *child_obj) {
    if (!PyObject_TypeCheck(child_obj, (PyTypeObject *)state_of((PyObject *)anchor)->node_type)) {
        PyErr_SetString(PyExc_TypeError, "child must be a node");
        return NULL;
    }
    if (((NodeObject *)child_obj)->node->type == TH_NODE_DOCUMENT) {
        PyErr_SetString(PyExc_TypeError, "a Document cannot be inserted as a child");
        return NULL;
    }
    return adopt_into(anchor, dest_parent, child_obj);
}

int import_foreign_node(PyObject *dest_handle, PyObject **slot) {
    module_state *state = state_of(dest_handle);
    PyObject *node = *slot;
    if (!PyObject_TypeCheck(node, (PyTypeObject *)state->node_type) ||
        ((NodeObject *)node)->node->type == TH_NODE_DOCUMENT || tree_of(node) == ((HandleObject *)dest_handle)->tree) {
        return 0;
    }
    if (is_fragment_arg(state, node)) {
        PyObject *local = import_fragment_children(dest_handle, (NodeObject *)node);
        if (local == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_SETREF(*slot, local);
    } else if (import_node(dest_handle, (NodeObject *)node) == NULL) { /* GCOVR_EXCL_BR_LINE: OOM only */
        return -1;                                                     /* GCOVR_EXCL_LINE: OOM path */
    }
    return 1;
}

/* import_foreign_node for every item of list, replacing an imported fragment by its local copy. Returns how many it
   imported, or -1 on allocation failure. Reads and writes the items through the list accessors, not the item array,
   which PyPy's C-API layer does not expose. */
static Py_ssize_t import_foreign_list(PyObject *dest_handle, PyObject *list) {
    Py_ssize_t imported = 0;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(list); index++) {
        PyObject *item = PyList_GET_ITEM(list, index);
        PyObject *slot = Py_NewRef(item);
        int status = import_foreign_node(dest_handle, &slot);
        if (status < 0) {    /* GCOVR_EXCL_BR_LINE: OOM only */
            Py_DECREF(slot); /* GCOVR_EXCL_LINE */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (slot != item) {
            PyList_SetItem(list, index, slot);
        } else {
            Py_DECREF(slot);
        }
        imported += status;
    }
    return imported;
}

/* The C nodes an insertion of the arguments in list places, in order: each node itself, or a DocumentFragment's
   children. skip (the node a sibling edit is anchored on, or NULL) is left out, since the edit keeps it in place.
   The whole call is checked before anything moves: a non-node or a Document raises TypeError; a node or fragment
   that contains parent, and anything the hierarchy rules reject before child or in place of the run (see
   th_pre_insert_error), raise ValueError. Every argument already lives in anchor's tree (the import pass ran).
   Returns a PyMem array the caller frees, with *out_count entries, or NULL with an exception. */
static th_node **gather_insert(NodeObject *anchor, th_node *parent, PyObject *list, th_node *skip, th_node *child,
                               th_node *run_first, th_node *run_last, Py_ssize_t *out_count) {
    module_state *state = state_of((PyObject *)anchor);
    Py_ssize_t capacity = 1;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(list); index++) {
        PyObject *item = PyList_GET_ITEM(list, index);
        if (!PyObject_TypeCheck(item, (PyTypeObject *)state->node_type)) {
            PyErr_SetString(PyExc_TypeError, "child must be a node");
            return NULL;
        }
        th_node *node = ((NodeObject *)item)->node;
        if (node->type == TH_NODE_DOCUMENT) {
            PyErr_SetString(PyExc_TypeError, "a Document cannot be inserted as a child");
            return NULL;
        }
        for (th_node *walk = node->first_child; is_fragment_arg(state, item) && walk != NULL;
             walk = walk->next_sibling) {
            capacity++;
        }
        capacity++;
    }
    th_node **nodes = PyMem_Malloc((size_t)capacity * sizeof(th_node *));
    if (nodes == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = 0;
    const char *message = NULL;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(list); index++) {
        PyObject *item = PyList_GET_ITEM(list, index);
        th_node *node = ((NodeObject *)item)->node;
        if (node == skip) {
            continue;
        }
        if (th_node_contains(tree_of((PyObject *)anchor), node, parent)) {
            message = "cannot insert a node into its own subtree";
        }
        if (!is_fragment_arg(state, item)) {
            nodes[count++] = node;
            continue;
        }
        for (th_node *walk = node->first_child; walk != NULL; walk = walk->next_sibling) {
            nodes[count++] = walk;
        }
    }
    if (message == NULL) {
        message = th_pre_insert_error(parent, nodes, count, child, run_first, run_last);
    }
    if (message != NULL) {
        PyMem_Free(nodes);
        PyErr_SetString(PyExc_ValueError, message);
        return NULL;
    }
    *out_count = count;
    return nodes;
}

/* Move gathered nodes into parent: each before ref or, with after set, each right after the one before it, starting
   after ref. A node that is ref itself keeps its place. The caller holds the tree's lock. */
static void link_gathered(th_tree *tree, th_node *parent, th_node *const *nodes, Py_ssize_t count, th_node *ref,
                          int after) {
    for (Py_ssize_t index = 0; index < count; index++) {
        th_node *node = nodes[index];
        if (!after && node == ref) {
            ref = ref->next_sibling;
        }
        th_node_remove_observed(tree, node);
        th_node_insert_before_observed(tree, parent, node, after ? ref->next_sibling : ref);
        if (after) {
            ref = node;
        }
    }
}

/* Insert the arguments in list into parent before ref (NULL appends), the whole call checked first (see
   gather_insert). The caller holds self's critical section and has imported the foreign arguments. Returns 0, or -1
   with an exception. */
static int insert_gathered(PyObject *self, th_node *parent, PyObject *list, th_node *ref) {
    Py_ssize_t count;
    th_node **nodes = gather_insert((NodeObject *)self, parent, list, NULL, ref, NULL, NULL, &count);
    if (nodes == NULL) {
        return -1;
    }
    handle_drop_index(((NodeObject *)self)->handle);
    link_gathered(tree_of(self), parent, nodes, count, ref, 0);
    PyMem_Free(nodes);
    return 0;
}

/* Import every foreign argument in list, repeating the pass until it imports nothing (see import_foreign_node).
   Returns 0, or -1 on allocation failure. */
static int import_all(PyObject *self, PyObject *list) {
    for (;;) {
        Py_ssize_t imported = import_foreign_list(((NodeObject *)self)->handle, list);
        if (imported <= 0) {
            return (int)imported;
        }
    }
}

/* Append one node that is not a DocumentFragment as parent's last child: the same imports and checks as gathering it
   into a list (see gather_insert), without the list or the scratch array. A foreign node is imported first, which
   suspends the caller's critical section, and every check below then reads the tree afresh, so one import is enough.
   The imported copy is fresh and nothing links to it, so it needs no ancestor walk. Returns 0, or -1 with an exception.
 */
static int append_one(PyObject *self, th_node *parent, PyObject *item) {
    if (!PyObject_TypeCheck(item, (PyTypeObject *)state_of(self)->node_type)) {
        PyErr_SetString(PyExc_TypeError, "child must be a node");
        return -1;
    }
    NodeObject *child = (NodeObject *)item;
    if (child->node->type == TH_NODE_DOCUMENT) {
        PyErr_SetString(PyExc_TypeError, "a Document cannot be inserted as a child");
        return -1;
    }
    th_tree *tree = tree_of(self);
    int foreign = tree_of(item) != tree;
    if (foreign && import_node(((NodeObject *)self)->handle, child) == NULL) { /* GCOVR_EXCL_BR_LINE: OOM only */
        return -1;                                                             /* GCOVR_EXCL_LINE: OOM path */
    }
    th_node *node = child->node;
    const char *message = NULL;
    if (!foreign && th_node_contains(tree, node, parent)) {
        message = "cannot insert a node into its own subtree";
    } else if (node->type == TH_NODE_DOCTYPE) { /* no Document appends: a doctype is the only rule that can fail */
        message = th_pre_insert_error(parent, &node, 1, NULL, NULL, NULL);
    }
    if (message != NULL) {
        PyErr_SetString(PyExc_ValueError, message);
        return -1;
    }
    handle_drop_index(((NodeObject *)self)->handle);
    if (!foreign) {
        th_node_remove_observed(tree, node);
    }
    th_node_append_child_observed(tree, parent, node);
    return 0;
}

/* Import the foreign arguments in list, then append them all to parent (see insert_gathered). Returns 0, or -1 with
   an exception. */
static int append_gathered(PyObject *self, th_node *parent, PyObject *list) {
    if (PyList_GET_SIZE(list) == 1 && !is_fragment_arg(state_of(self), PyList_GET_ITEM(list, 0))) {
        return append_one(self, parent, PyList_GET_ITEM(list, 0));
    }
    if (import_all(self, list) < 0) { /* GCOVR_EXCL_BR_LINE: the import fails only on allocation failure */
        return -1;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return insert_gathered(self, parent, list, NULL);
}

/* Append child (a node, or a fragment whose children move) as this node's last child: the body of append() on an
   element, a DocumentFragment, and a ShadowRoot. */
PyObject *node_append_child(PyObject *self, PyObject *child) {
    int error;
    if (!is_fragment_arg(state_of(self), child)) {
        Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
        error = append_one(self, ((NodeObject *)self)->node, child) < 0;
        Py_END_CRITICAL_SECTION();
        return error ? NULL : Py_NewRef(Py_None);
    }
    PyObject *list = PyList_New(1);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyList_SET_ITEM(list, 0, Py_NewRef(child));
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    error = append_gathered(self, ((NodeObject *)self)->node, list) < 0;
    Py_END_CRITICAL_SECTION();
    Py_DECREF(list);
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* Attach a freshly built element's children, rejecting content on a void tag the
   way the constructor rejects an invalid attribute name. A void element takes no
   children per the HTML spec, so storing them only for the serializer to drop
   would silently lose what the caller passed; a hard error at construction time
   surfaces the programming mistake instead. */
static int append_build_children(PyObject *element, PyObject *tag, PyObject *children) {
    PyObject *sequence = PySequence_Fast(children, "children must be iterable");
    if (sequence == NULL) {
        return -1;
    }
    Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    NodeObject *self = (NodeObject *)element;
    if (count > 0 && th_tag_is_void(self->node->atom)) {
        Py_DECREF(sequence);
        PyErr_Format(PyExc_ValueError, "void element %R cannot have children", tag);
        return -1;
    }
    int error;
    PyObject *only = count == 1 ? PySequence_Fast_GET_ITEM(sequence, 0) : NULL;
    if (only != NULL && !is_fragment_arg(state_of(element), only)) { /* one plain child needs no list */
        Py_BEGIN_CRITICAL_SECTION(self->handle);
        error = append_one(element, self->node, only) < 0;
        Py_END_CRITICAL_SECTION();
        Py_DECREF(sequence);
        return error ? -1 : 0;
    }
    PyObject *list = PySequence_List(sequence);
    Py_DECREF(sequence);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: a fast sequence always converts */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_BEGIN_CRITICAL_SECTION(self->handle);
    error = append_gathered(element, self->node, list) < 0;
    Py_END_CRITICAL_SECTION();
    Py_DECREF(list);
    return error ? -1 : 0;
}

/* Whether new_obj is a node wrapping the same C node as ref: inserting a node
   relative to itself is a no-op the link primitives must not be handed. */
static int is_same_node(PyObject *self, PyObject *new_obj, th_node *ref) {
    module_state *state = state_of(self);
    return PyObject_TypeCheck(new_obj, (PyTypeObject *)state->node_type) && ((NodeObject *)new_obj)->node == ref;
}

static PyObject *element_append(PyObject *self, PyObject *child) {
    return node_append_child(self, child);
}

/* The iterable is drained before the tree lock is taken, so a generator that edits the tree runs outside it and the
   whole batch is checked before anything moves. */
static PyObject *element_extend(PyObject *self, PyObject *iterable) {
    PyObject *list = PySequence_List(iterable);
    if (list == NULL) {
        return NULL;
    }
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    error = append_gathered(self, ((NodeObject *)self)->node, list) < 0;
    Py_END_CRITICAL_SECTION();
    Py_DECREF(list);
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* Extract an optional str argument for the page-shell builder as UCS4 code points.
   None yields NULL (the piece is omitted); a str yields a freshly allocated buffer,
   non-NULL even when empty, with *len set. Anything else raises TypeError naming the
   field and clears *ok. Caller frees a non-NULL buffer with PyMem_Free. */
static Py_UCS4 *shell_optional_ucs4(PyObject *arg, const char *what, Py_ssize_t *len, int *ok) {
    *ok = 1;
    if (arg == Py_None) {
        *len = 0;
        return NULL;
    }
    if (!PyUnicode_Check(arg)) {
        PyErr_Format(PyExc_TypeError, "%s must be a str or None, not %.80s", what, Py_TYPE(arg)->tp_name);
        *ok = 0;
        return NULL;
    }
    *len = PyUnicode_GET_LENGTH(arg);
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(arg);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *ok = 0;          /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: llvm-cov counts the fall-through brace of the alloc-failure arm */
    return points;
}

/* Append every node from children into section (a head or body element of the
   fresh shell), through the same adoption path as extend(). Returns 0, or -1 with
   an exception set when children is not iterable or holds a non-node. */
/* The node list a builder's arguments become, from `first` on: a str becomes a Text node, anything else passes
   through for the tree to accept or reject. With a `mapping_type`, a mapping past the first argument is the
   TypeError the element builder raises for attributes out of place. */
static PyObject *build_children(module_state *state, PyObject *items, Py_ssize_t first, PyObject *mapping_type) {
    PyObject *sequence = PySequence_Fast(items, "children must be an iterable");
    if (sequence == NULL) {
        return NULL;
    }
    Py_ssize_t count = PySequence_Fast_GET_SIZE(sequence);
    PyObject *children = PyList_New(count - first);
    if (children == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(sequence); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t index = first; index < count; index++) {
        PyObject *item = PySequence_Fast_GET_ITEM(sequence, index);
        PyObject *child;
        if (PyUnicode_Check(item)) {
            child = PyObject_CallOneArg(state->text_type, item);
        } else {
            int late_mapping = mapping_type == NULL ? 0 : PyObject_IsInstance(item, mapping_type);
            if (late_mapping < 0) {
                child = NULL; /* the shape test itself failed */
            } else if (late_mapping) {
                PyErr_SetString(PyExc_TypeError,
                                "a mapping argument sets attributes and must come first, before any child");
                child = NULL;
            } else {
                child = Py_NewRef(item);
            }
        }
        if (child == NULL) {
            Py_DECREF(children);
            Py_DECREF(sequence);
            return NULL;
        }
        PyList_SET_ITEM(children, index - first, child);
    }
    Py_DECREF(sequence);
    return children;
}

static int shell_fill(module_state *state, PyObject *handle, th_node *section, PyObject *children) {
    PyObject *nodes = build_children(state, children, 0, NULL);
    if (nodes == NULL) {
        return -1;
    }
    PyObject *wrapper = node_wrap(state, handle, section);
    if (wrapper == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(nodes);  /* GCOVR_EXCL_LINE */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = element_extend(wrapper, nodes);
    Py_DECREF(nodes);
    Py_DECREF(wrapper);
    if (result == NULL) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

PyObject *turbohtml_build_document(PyObject *module, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"head", "body", "title", "lang", "charset", NULL};
    PyObject *head_arg;
    PyObject *body_arg;
    PyObject *title_arg = Py_None;
    PyObject *lang_arg = Py_None;
    PyObject *charset_arg = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OO|OOO:_build_document", keywords, &head_arg, &body_arg, &title_arg,
                                     &lang_arg, &charset_arg)) {
        return NULL;
    }
    Py_ssize_t title_len;
    Py_ssize_t lang_len;
    Py_ssize_t charset_len;
    int ok;
    Py_UCS4 *title = shell_optional_ucs4(title_arg, "title", &title_len, &ok);
    if (!ok) {
        return NULL;
    }
    Py_UCS4 *lang = shell_optional_ucs4(lang_arg, "lang", &lang_len, &ok);
    if (!ok) {
        PyMem_Free(title);
        return NULL;
    }
    Py_UCS4 *charset = shell_optional_ucs4(charset_arg, "charset", &charset_len, &ok);
    if (!ok) {
        PyMem_Free(title);
        PyMem_Free(lang);
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(title);       /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(lang);        /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(charset);     /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *head_node;
    th_node *body_node;
    th_node *document =
        th_tree_build_shell(tree, lang, lang_len, title, title_len, charset, charset_len, &head_node, &body_node);
    PyMem_Free(title);
    PyMem_Free(lang);
    PyMem_Free(charset);
    if (document == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *doc = wrap_fresh_tree_node(state, tree, document);
    if (doc == NULL) { /* GCOVR_EXCL_BR_LINE: wrap_fresh_tree_node frees the tree on its own OOM */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *handle = ((NodeObject *)doc)->handle;
    if (shell_fill(state, handle, head_node, head_arg) < 0 || shell_fill(state, handle, body_node, body_arg) < 0) {
        Py_DECREF(doc);
        return NULL;
    }
    return doc;
}

static PyObject *element_insert(PyObject *self, PyObject *args) {
    Py_ssize_t index;
    PyObject *child;
    if (!PyArg_ParseTuple(args, "nO", &index, &child)) {
        return NULL;
    }
    th_node *parent = ((NodeObject *)self)->node;
    PyObject *list = PyList_New(1);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyList_SET_ITEM(list, 0, Py_NewRef(child));
    int error = 1;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    /* the index resolves after the import, which can suspend the section */
    if (import_all(self, list) == 0) { /* GCOVR_EXCL_BR_LINE: the import fails only on allocation failure */
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
        error = insert_gathered(self, parent, list, ref) < 0;
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(list);
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
        th_node_remove_observed(tree_of(self), parent->first_child);
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

/* adopt_into detaches the wrapper before the child walk, so the loop only sees this
   element's own children; the moves are pure C under the per-tree lock. */
static PyObject *element_wrap_children(PyObject *self, PyObject *wrapper_obj) {
    module_state *state = state_of(self);
    if (!PyObject_TypeCheck(wrapper_obj, (PyTypeObject *)state->node_type) ||
        ((NodeObject *)wrapper_obj)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "wrapper must be an element");
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    th_node *parent = node->node;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    handle_drop_index(node->handle);
    th_node *wrapper = adopt_into(node, parent, wrapper_obj);
    if (wrapper == NULL) {
        error = 1;
    } else {
        while (parent->first_child != NULL) {
            th_node *child = parent->first_child;
            th_node_remove_observed(tree_of(self), child);
            th_node_append_child_observed(tree_of(self), wrapper, child);
        }
        th_node_append_child_observed(tree_of(self), parent, wrapper);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return Py_NewRef(wrapper_obj);
}

/* The parent for an edit that links nodes beside self, read under self's critical
   section once every foreign argument in list is imported. An import suspends that
   section (see import_node), so the parent is re-read until a pass imports nothing;
   the first read comes before any import, so a parentless node raises without
   moving the arguments. NULL with a ValueError when the node has no parent, or with
   MemoryError on allocation failure. */
static th_node *sibling_parent(PyObject *self, PyObject *list) {
    for (;;) {
        th_node *parent = ((NodeObject *)self)->node->parent;
        if (parent == NULL) {
            PyErr_SetString(PyExc_ValueError, "node has no parent");
            return NULL;
        }
        Py_ssize_t imported = list == NULL ? 0 : import_foreign_list(((NodeObject *)self)->handle, list);
        if (imported < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (imported == 0) {
            return parent;
        }
    }
}

/* The hierarchy rules for a wrap: the wrapper takes the place of the sibling run [first, last] in parent, and the run
   moves into the wrapper, so a doctype in the run is rejected. Sets a ValueError and returns -1 on a violation. */
static int reject_wrap(th_node *parent, th_node *wrapper, th_node *first, th_node *last) {
    const char *message = th_pre_insert_error(parent, &wrapper, 1, NULL, first, last);
    for (th_node *walk = first; message == NULL; walk = walk->next_sibling) {
        message = th_pre_insert_error(wrapper, &walk, 1, NULL, NULL, NULL);
        if (walk == last) {
            break;
        }
    }
    if (message != NULL) {
        PyErr_SetString(PyExc_ValueError, message);
        return -1;
    }
    return 0;
}

/* The wrapper wrap() links into parent in node's place, checked against the hierarchy rules first. NULL with an
   exception when they reject it or adoption fails. */
static th_node *adopt_wrapper(NodeObject *node, th_node *parent, PyObject *wrapper_obj) {
    if (reject_wrap(parent, ((NodeObject *)wrapper_obj)->node, node->node, node->node) < 0) {
        return NULL;
    }
    return adopt_into(node, parent, wrapper_obj);
}

enum sibling_edit { SIBLING_BEFORE, SIBLING_AFTER, SIBLING_REPLACE };

/* insert_before / insert_after / replace_with: place the node arguments beside self, a DocumentFragment argument
   contributing its children. The structural edits hold the per-tree lock around the pointer rewiring so a concurrent
   read/mutate cannot observe a half-linked tree (a no-op on the GIL build). sibling_parent imports the foreign
   arguments before the edit reads the parent, and gather_insert checks the whole call, so nothing moves on a
   rejected call and nothing suspends the section between the parent read and the relink. Self among the arguments
   stays where it is; replace_with then inserts the others around it rather than removing it. */
static PyObject *sibling_edit(PyObject *self, PyObject *nodes, enum sibling_edit edit) {
    PyObject *list = PySequence_List(nodes);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: a tuple always converts */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *anchor = ((NodeObject *)self)->node;
    int keep_self = edit != SIBLING_REPLACE;
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(list); index++) {
        keep_self |= is_same_node(self, PyList_GET_ITEM(list, index), anchor);
    }
    int error = 1;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    th_node *parent = sibling_parent(self, list);
    Py_ssize_t count;
    th_node **gathered = parent == NULL ? NULL
                                        : gather_insert((NodeObject *)self, parent, list, anchor,
                                                        edit == SIBLING_AFTER ? anchor->next_sibling : anchor,
                                                        keep_self ? NULL : anchor, keep_self ? NULL : anchor, &count);
    if (gathered != NULL) {
        error = 0;
        handle_drop_index(((NodeObject *)self)->handle);
        link_gathered(tree_of(self), parent, gathered, count, anchor, edit == SIBLING_AFTER);
        PyMem_Free(gathered);
        if (!keep_self) {
            th_node_remove_observed(tree_of(self), anchor);
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(list);
    if (error) {
        return NULL;
    }
    Py_RETURN_NONE;
}

PyObject *node_insert_before(PyObject *self, PyObject *nodes) {
    return sibling_edit(self, nodes, SIBLING_BEFORE);
}

PyObject *node_insert_after(PyObject *self, PyObject *nodes) {
    return sibling_edit(self, nodes, SIBLING_AFTER);
}

PyObject *node_replace_with(PyObject *self, PyObject *nodes) {
    return sibling_edit(self, nodes, SIBLING_REPLACE);
}

PyObject *node_wrap_in(PyObject *self, PyObject *wrapper_obj) {
    module_state *state = state_of(self);
    if (!PyObject_TypeCheck(wrapper_obj, (PyTypeObject *)state->node_type) ||
        ((NodeObject *)wrapper_obj)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "wrapper must be an element");
        return NULL;
    }
    NodeObject *node = (NodeObject *)self;
    if (((NodeObject *)wrapper_obj)->node == node->node) {
        PyErr_SetString(PyExc_ValueError, "wrapper cannot be the wrapped node");
        return NULL;
    }
    PyObject *wrapper_list = PyList_New(1);
    if (wrapper_list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyList_SET_ITEM(wrapper_list, 0, Py_NewRef(wrapper_obj));
    int standalone = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    /* read under the lock: a concurrent move detaches this node for a moment */
    if (node->node->parent == NULL) {
        standalone = 1;
    } else {
        handle_drop_index(node->handle);
        th_node *parent = sibling_parent(self, wrapper_list);
        /* parent is NULL only when another thread detached this node, or on OOM */
        th_node *wrapper = parent == NULL ? NULL : adopt_wrapper(node, parent, wrapper_obj); /* GCOVR_EXCL_BR_LINE */
        if (wrapper == NULL) {
            error = 1;
        } else {
            th_node_insert_before_observed(tree_of(self), parent, wrapper, node->node);
            th_node_remove_observed(tree_of(self), node->node);
            th_node_append_child_observed(tree_of(self), wrapper, node->node);
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(wrapper_list);
    if (error) {
        return NULL;
    }
    if (standalone) {
        /* a standalone node just moves into the wrapper, under the wrapper's tree lock */
        PyObject *appended = element_append(wrapper_obj, self);
        if (appended == NULL) {
            return NULL;
        }
        Py_DECREF(appended);
    }
    return Py_NewRef(wrapper_obj);
}

/* Wrap this node and the contiguous run of siblings after it in one new element.
   Everything happens under the per-tree lock and the run is resolved and rewired in
   pure C, so the sibling pointers are never dereferenced across a Python call that
   could relink the tree under free-threading. */
PyObject *node_wrap_siblings(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"wrapper", "until", NULL};
    PyObject *wrapper_obj;
    PyObject *until_obj = Py_None;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|$O", keywords, &wrapper_obj, &until_obj)) {
        return NULL;
    }
    module_state *state = state_of(self);
    if (!PyObject_TypeCheck(wrapper_obj, (PyTypeObject *)state->node_type) ||
        ((NodeObject *)wrapper_obj)->node->type != TH_NODE_ELEMENT) {
        PyErr_SetString(PyExc_TypeError, "wrapper must be an element");
        return NULL;
    }
    th_node *until_node = NULL;
    if (until_obj != Py_None) {
        if (!PyObject_TypeCheck(until_obj, (PyTypeObject *)state->node_type)) {
            PyErr_SetString(PyExc_TypeError, "until must be a node or None");
            return NULL;
        }
        until_node = ((NodeObject *)until_obj)->node;
    }
    NodeObject *node = (NodeObject *)self;
    th_node *first = node->node;
    PyObject *wrapper_list = PyList_New(1);
    if (wrapper_list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyList_SET_ITEM(wrapper_list, 0, Py_NewRef(wrapper_obj));
    int error = 0;
    const char *value_error = NULL;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    handle_drop_index(node->handle);
    /* The parent and the run are resolved under the lock after the wrapper import;
       reading them earlier could stale them against a concurrent move that relinks
       this node to another parent. */
    th_node *parent = sibling_parent(self, wrapper_list);
    if (parent == NULL) {
        error = 1;
    } else {
        th_node *wrapper_node = ((NodeObject *)wrapper_obj)->node;
        th_node *last = NULL;
        if (until_node == NULL) {
            last = parent->last_child; /* the whole run from this node to the end */
        } else if (until_node->parent != parent) {
            value_error = "until must be this node or one of its following siblings";
        } else {
            for (th_node *walk = first; walk != NULL; walk = walk->next_sibling) {
                if (walk == until_node) {
                    last = walk;
                    break;
                }
            }
            if (last == NULL) {
                value_error = "until must be this node or one of its following siblings";
            }
        }
        if (value_error == NULL) {
            for (th_node *walk = first;; walk = walk->next_sibling) {
                if (walk == wrapper_node) {
                    value_error = "wrapper cannot be one of the wrapped nodes";
                    break;
                }
                if (walk == last) {
                    break;
                }
            }
        }
        if (value_error == NULL) {
            th_node *wrapper =
                reject_wrap(parent, wrapper_node, first, last) < 0 ? NULL : adopt_into(node, parent, wrapper_obj);
            if (wrapper == NULL) {
                error = 1;
            } else {
                th_node_insert_before_observed(tree_of(self), parent, wrapper, first);
                for (th_node *cursor = first;;) {
                    th_node *next = cursor->next_sibling;
                    int is_last = cursor == last;
                    th_node_remove_observed(tree_of(self), cursor);
                    th_node_append_child_observed(tree_of(self), wrapper, cursor);
                    if (is_last) {
                        break;
                    }
                    cursor = next;
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(wrapper_list);
    if (value_error != NULL) {
        PyErr_SetString(PyExc_ValueError, value_error);
        return NULL;
    }
    if (error) {
        return NULL;
    }
    return Py_NewRef(wrapper_obj);
}

PyObject *node_unwrap(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    th_node *parent = sibling_parent(self, NULL);
    if (parent == NULL) {
        error = 1;
    } else {
        handle_drop_index(((NodeObject *)self)->handle);
        while (node->first_child != NULL) {
            th_node *child = node->first_child;
            th_node_remove_observed(tree_of(self), child);
            th_node_insert_before_observed(tree_of(self), parent, child, node);
        }
        th_node_remove_observed(tree_of(self), node);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}

PyObject *node_extract(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove_observed(tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return Py_NewRef(self);
}

PyObject *node_decompose(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove_observed(tree_of(self), ((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
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
        th_node_remove_observed(tree, node->first_child);
    }
    th_node *text = len > 0 ? th_tree_make_data_node(tree, TH_NODE_TEXT, points, len) : NULL;
    /* GCOVR_EXCL_START: a make_data_node allocation failure cannot be forced from a test */
    if (len > 0 && text == NULL) {
        error = 1;
    }
    /* GCOVR_EXCL_STOP */
    if (text != NULL) {
        th_node_append_child_observed(tree, node, text);
    }
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return error ? -1 : 0; /* GCOVR_EXCL_BR_LINE: error is set only on the excluded allocation failure */
}

static PyObject *element_set_text_method(PyObject *self, PyObject *value) {
    if (element_set_text(self, value, NULL) < 0) {
        return NULL;
    }
    Py_RETURN_NONE;
}

/* The four DOM insertAdjacentHTML positions, also the splice mode for set_inner_html
   (which clears the element first, then inserts as for "beforeend"). */
enum th_adjacency { TH_ADJ_BEFOREBEGIN, TH_ADJ_AFTERBEGIN, TH_ADJ_BEFOREEND, TH_ADJ_AFTEREND };

/* Map a position string to its enum case-insensitively (the DOM keywords are ASCII
   case-insensitive), or -1 with a ValueError naming the offending value. */
static int resolve_adjacency(PyObject *position, enum th_adjacency *out) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(position);
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(position);
    if (points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = 0;
    if (ucs4_iequals_ascii(points, len, "beforebegin")) {
        *out = TH_ADJ_BEFOREBEGIN;
    } else if (ucs4_iequals_ascii(points, len, "afterbegin")) {
        *out = TH_ADJ_AFTERBEGIN;
    } else if (ucs4_iequals_ascii(points, len, "beforeend")) {
        *out = TH_ADJ_BEFOREEND;
    } else if (ucs4_iequals_ascii(points, len, "afterend")) {
        *out = TH_ADJ_AFTEREND;
    } else {
        PyErr_Format(PyExc_ValueError,
                     "position must be 'beforebegin', 'afterbegin', 'beforeend', or 'afterend', not %R", position);
        rc = -1;
    }
    PyMem_Free(points);
    return rc;
}

/* Whether an attribute name declares a namespace: xmlns, or xmlns:prefix. */
static int is_xmlns_name(const char *name, Py_ssize_t name_len) {
    if (name_len < 5 || memcmp(name, "xmlns", 5) != 0) {
        return 0;
    }
    return name_len == 5 || (name_len > 6 && name[5] == ':');
}

/* Add scope's own xmlns declarations to declarations unless a nearer scope already declared the name. Returns 0, or
   -1 on allocation failure. */
static int add_scope_declarations(th_tree *tree, th_node *scope, PyObject *declarations) {
    for (Py_ssize_t index = 0; index < scope->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, scope->attrs[index].name_atom, &name_len);
        if (!is_xmlns_name(name, name_len)) {
            continue;
        }
        PyObject *key = PyUnicode_DecodeUTF8(name, name_len, "strict");
        int seen = key == NULL ? -1 : PyDict_Contains(declarations, key); /* GCOVR_EXCL_BR_LINE: OOM only */
        PyObject *value = seen == 0 ? attr_value_obj(&scope->attrs[index]) : NULL;
        /* allocation failure cannot be forced from a test */
        int failed =
            seen < 0 ||                                                                     /* GCOVR_EXCL_BR_LINE */
            (seen == 0 && (value == NULL || PyDict_SetItem(declarations, key, value) < 0)); /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(key);
        Py_XDECREF(value);
        if (failed) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* The xmlns declarations in scope at context, as {attribute name: value} with the nearest declaration winning,
   read under the per-tree lock. NULL with an exception on allocation failure. */
static PyObject *xml_declarations_in_scope(PyObject *self, th_node *context) {
    PyObject *declarations = PyDict_New();
    if (declarations == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *scope = context; scope != NULL && scope->type == TH_NODE_ELEMENT; scope = scope->parent) {
        if (add_scope_declarations(tree_of(self), scope, declarations) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            failed = 1;                                                       /* GCOVR_EXCL_LINE: OOM path */
            break;                                                            /* GCOVR_EXCL_LINE: OOM path */
        }
    }
    Py_END_CRITICAL_SECTION();
    if (failed) {                /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(declarations); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return declarations;
}

/* value escaped for a double-quoted XML attribute: the three characters it cannot hold become references. NULL with
   an exception on allocation failure. */
static PyObject *escape_attribute_value(PyObject *value) {
    static const char *const replacements[][2] = {{"&", "&amp;"}, {"<", "&lt;"}, {"\"", "&quot;"}};
    PyObject *escaped = Py_NewRef(value);
    for (size_t index = 0; index < sizeof(replacements) / sizeof(replacements[0]); index++) {
        Py_SETREF(escaped,
                  PyObject_CallMethod(escaped, "replace", "ss", replacements[index][0], replacements[index][1]));
        if (escaped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return escaped;
}

/* The wrapped source for the XML fragment parse: html inside a start and end tag named like the context element,
   the start tag carrying the xmlns declarations in scope, so prefixes and the default namespace resolve as they would
   inside the context. *start_len receives the start tag's length, for shifting error columns. NULL with an exception
   on allocation failure. */
static PyObject *xml_fragment_source(PyObject *self, th_node *context, PyObject *html, Py_ssize_t *start_len) {
    PyObject *declarations = xml_declarations_in_scope(self, context);
    if (declarations == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *tag = ucs4_to_str(context->text, context->text_len);
    PyObject *parts = Py_BuildValue("[sO]", "<", tag);
    PyObject *name;
    PyObject *value;
    Py_ssize_t position = 0;
    while (parts != NULL && PyDict_Next(declarations, &position, &name, &value)) { /* GCOVR_EXCL_BR_LINE: OOM */
        PyObject *escaped = escape_attribute_value(value);
        if (escaped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_CLEAR(parts);   /* GCOVR_EXCL_LINE: allocation-failure path */
            break;             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *declaration = th_str_format(" %U=\"%U\"", name, escaped);
        Py_DECREF(escaped);
        if (declaration == NULL || PyList_Append(parts, declaration) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
            Py_CLEAR(parts);                                                /* GCOVR_EXCL_LINE: OOM path */
        } /* GCOVR_EXCL_LINE */
        Py_XDECREF(declaration);
    }
    Py_DECREF(declarations);
    PyObject *empty = PyUnicode_FromString("");
    PyObject *start = parts == NULL || empty == NULL ? NULL : PyUnicode_Join(empty, parts); /* GCOVR_EXCL_BR_LINE */
    Py_XDECREF(empty);
    Py_XDECREF(parts);
    if (start == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(tag); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *start_len = PyUnicode_GET_LENGTH(start) + 1;
    PyObject *source = th_str_format("%U>%U</%U>", start, html, tag);
    Py_DECREF(start);
    Py_DECREF(tag);
    return source;
}

/* The HTML standard's XML fragment parsing algorithm, used for a context element in an XML document: html is parsed
   as XML inside the wrapper xml_fragment_source builds, and the wrapper's children become the fragment's top-level
   nodes. A well-formedness error raises the parse error parse_xml raises, with a first-line column counted from the
   start of html. *keep_alive receives the source the fragment's text borrows; the caller releases it after the
   splice. */
static th_tree *parse_xml_fragment_in_context(PyObject *self, th_node *context, PyObject *html, PyObject **keep_alive) {
    Py_ssize_t start_len;
    PyObject *source = xml_fragment_source(self, context, html, &start_len);
    if (source == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tree *fragment = th_tree_parse_xml(PyUnicode_KIND(source), PyUnicode_DATA(source), PyUnicode_GET_LENGTH(source));
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(source);  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (raise_xml_fragment_error(state_of(self), fragment, start_len) < 0) {
        Py_DECREF(source);
        return NULL;
    }
    th_node *document = th_tree_document(fragment);
    th_node *wrapper = document->first_child;
    while (wrapper->first_child != NULL) {
        th_node *child = wrapper->first_child;
        th_node_remove(child);
        th_node_insert_before(document, child, wrapper);
    }
    th_node_remove(wrapper);
    *keep_alive = source;
    return fragment;
}

/* Parse html as a fragment in the context element's own context, so its content
   model and namespace drive the parse exactly as the DOM innerHTML setter requires
   (the context name is the tag, prefixed "svg "/"math " for a foreign element). The
   parse only borrows html and never touches the live tree, so it runs before the
   per-tree lock is taken and never holds a structural pointer across it. The caller
   owns the returned tree (free it with th_tree_free) and any *keep_alive the XML path
   sets. NULL with an exception set on a non-encodable (lone-surrogate) tag name, an XML
   well-formedness error, or an allocation failure. */
static th_tree *parse_fragment_in_context(PyObject *self, th_node *context, PyObject *html, PyObject **keep_alive) {
    if (th_tree_is_xml(tree_of(self))) {
        return parse_xml_fragment_in_context(self, context, html, keep_alive);
    }
    int scripting = th_tree_scripting(tree_of(self));
    PyObject *tag = ucs4_to_str(context->text, context->text_len);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t tag_len;
    const char *tag_utf8 = PyUnicode_AsUTF8AndSize(tag, &tag_len);
    if (tag_utf8 == NULL) { /* a lone-surrogate tag name has no UTF-8 form */
        Py_DECREF(tag);
        return NULL;
    }
    const char *prefix = context->ns == TH_NS_SVG ? "svg " : context->ns == TH_NS_MATHML ? "math " : "";
    Py_ssize_t prefix_len = (Py_ssize_t)strlen(prefix);
    Py_ssize_t name_len = prefix_len + tag_len;
    char *name = PyMem_Malloc((size_t)name_len + 1);
    if (name == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(tag);   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(name, prefix, (size_t)prefix_len);
    memcpy(name + prefix_len, tag_utf8, (size_t)tag_len);
    name[name_len] = '\0';
    Py_DECREF(tag);
    th_tree *fragment = th_tree_parse_fragment(PyUnicode_KIND(html), PyUnicode_DATA(html), PyUnicode_GET_LENGTH(html),
                                               name, name_len, 0, 0, scripting, 0);
    PyMem_Free(name);
    if (fragment == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return fragment;
}

/* Copy each top-level node of a parsed fragment into dest in document order and link
   it relative to target per position; parent is target's parent (read by the caller
   before the lock, used only by the sibling positions). The caller holds the per-tree
   lock and fragment is a private detached tree no other thread can see, so iterating
   its child pointers and copying are a pure-C pass that no concurrent mutation can
   tear. Returns 0, or -1 on a copy allocation failure (no exception set). */
static int splice_fragment(th_tree *dest, th_tree *fragment, th_node *parent, th_node *target,
                           enum th_adjacency position) {
    th_node *first_ref = position == TH_ADJ_AFTERBEGIN ? target->first_child : NULL;
    th_node *cursor = target;
    for (th_node *child = th_tree_document(fragment)->first_child; child != NULL;) {
        th_node *next = child->next_sibling;
        th_node *copy = th_tree_copy_node(dest, fragment, child);
        if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        switch (position) { /* GCOVR_EXCL_BR_LINE: the enum is exhaustive; the implicit default is unreachable */
        case TH_ADJ_BEFOREBEGIN:
            th_node_insert_before_observed(dest, parent, copy, target);
            break;
        case TH_ADJ_AFTERBEGIN:
            th_node_insert_before_observed(dest, target, copy, first_ref);
            break;
        case TH_ADJ_BEFOREEND:
            th_node_append_child_observed(dest, target, copy);
            break;
        case TH_ADJ_AFTEREND:
            th_node_insert_before_observed(dest, parent, copy, cursor->next_sibling);
            cursor = copy;
            break;
        }
        child = next;
    }
    return 0;
}

static PyObject *element_set_inner_html(PyObject *self, PyObject *html) {
    if (!PyUnicode_Check(html)) {
        PyErr_SetString(PyExc_TypeError, "html must be a str");
        return NULL;
    }
    th_node *node = ((NodeObject *)self)->node;
    PyObject *keep_alive = NULL;
    th_tree *fragment = parse_fragment_in_context(self, node, html, &keep_alive);
    if (fragment == NULL) {
        return NULL;
    }
    th_tree *dest = tree_of(self);
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while (node->first_child != NULL) {
        th_node_remove_observed(dest, node->first_child);
    }
    error = splice_fragment(dest, fragment, NULL, node, TH_ADJ_BEFOREEND);
    Py_END_CRITICAL_SECTION();
    th_tree_free(fragment);
    Py_XDECREF(keep_alive);
    if (error) {                 /* GCOVR_EXCL_BR_LINE: splice only fails on a copy allocation failure */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *element_insert_adjacent_html(PyObject *self, PyObject *args) {
    PyObject *position;
    PyObject *html;
    if (!PyArg_ParseTuple(args, "UU:insert_adjacent_html", &position, &html)) {
        return NULL;
    }
    enum th_adjacency position_kind;
    if (resolve_adjacency(position, &position_kind) < 0) {
        return NULL;
    }
    th_node *node = ((NodeObject *)self)->node;
    th_node *parent = NULL;
    th_node *context = node;
    if (position_kind == TH_ADJ_BEFOREBEGIN || position_kind == TH_ADJ_AFTEREND) {
        parent = node->parent;
        if (parent == NULL || parent->type == TH_NODE_DOCUMENT) {
            PyErr_SetString(PyExc_ValueError, "'beforebegin' and 'afterend' need an element parent to insert beside");
            return NULL;
        }
        context = parent;
    }
    PyObject *keep_alive = NULL;
    th_tree *fragment = parse_fragment_in_context(self, context, html, &keep_alive);
    if (fragment == NULL) {
        return NULL;
    }
    th_tree *dest = tree_of(self);
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    error = splice_fragment(dest, fragment, parent, node, position_kind);
    Py_END_CRITICAL_SECTION();
    th_tree_free(fragment);
    Py_XDECREF(keep_alive);
    if (error) {                 /* GCOVR_EXCL_BR_LINE: splice only fails on a copy allocation failure */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

/* _build_element(tag, args, mapping_type) -> Element: the turbohtml.build call. A leading mapping among `args` sets
   the attributes; every other argument is a child, a str becoming a Text node, a node passing through, and a
   mapping past the first position a TypeError. `mapping_type` is collections.abc.Mapping. */
PyObject *turbohtml_build_element(PyObject *module, PyObject *args) {
    PyObject *tag, *parts, *mapping_type;
    if (!PyArg_ParseTuple(args, "UO!O:_build_element", &tag, &PyTuple_Type, &parts, &mapping_type)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *attrs = Py_None;
    Py_ssize_t first = 0;
    if (PyTuple_GET_SIZE(parts) > 0) {
        int leading = PyObject_IsInstance(PyTuple_GET_ITEM(parts, 0), mapping_type);
        if (leading < 0) {
            return NULL;
        }
        if (leading) {
            attrs = PyTuple_GET_ITEM(parts, 0);
            first = 1;
        }
    }
    PyObject *children = build_children(state, parts, first, mapping_type);
    if (children == NULL) {
        return NULL;
    }
    PyObject *element = PyObject_CallFunctionObjArgs(state->element_type, tag, attrs, children, NULL);
    Py_DECREF(children);
    return element;
}
