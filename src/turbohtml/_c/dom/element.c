/* Elements and their attributes: the live attribute view, form-control value semantics, and the
   find/select/xpath/regex query plus structural-mutation bindings. */

#include "dom/nodes.h"

#include "query/css/selector.h"
#include "query/css/to_xpath.h"

void handle_clear_caches(HandleObject *handle) {
    for (int index = 0; index < handle->sel_cache_len; index++) {
        selector_free(handle->sel_cache[index].compiled);
        Py_DECREF(handle->sel_cache[index].key);
    }
    for (int index = 0; index < handle->xpath_cache_len; index++) {
        xp_free(handle->xpath_cache[index].prog);
        Py_DECREF(handle->xpath_cache[index].key);
    }
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

PyType_Spec attrs_spec = {
    .name = "turbohtml._html._Attrs",
    .basicsize = sizeof(AttrsObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
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
                              "selected option value(s) of a select (a list[str] when it is multiple);\n"
                              "non-controls read None. Assigning a str writes the value (selecting the\n"
                              "matching option of a select), a list[str] selects a multiple select, and None\n"
                              "clears it. The checked state lives in Element.checked, not here.");

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

static enum field_kind input_kind(th_node *node) {
    const th_node_attr *type = find_node_attr(node, TH_ATTR_TYPE);
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

/* The value attribute as a str, or the fallback str when the attribute is absent;
   a present-but-empty (or valueless) attribute is the empty string. */
static PyObject *value_attr_or(th_node *node, const char *fallback) {
    const th_node_attr *value = find_node_attr(node, TH_ATTR_VALUE);
    if (value == NULL) {
        return PyUnicode_FromString(fallback);
    }
    return value->value == NULL ? PyUnicode_FromString("") : ucs4_to_str(value->value, value->value_len);
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

/* The value(s) of a select: a list[str] of the selected options for a multiple
   select, else the selected option's value (the last marked selected, or the first
   option as the default), or None when it has no options. */
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
    th_node *chosen = NULL;
    th_node *first = NULL;
    for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
        if (first == NULL) {
            first = option;
        }
        if (find_node_attr(option, TH_ATTR_SELECTED) != NULL) {
            chosen = option;
        }
    }
    th_node *use = chosen != NULL ? chosen : first;
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
        result = value_attr_or(node, input_kind(node) == FIELD_CHECKABLE ? "on" : "");
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
static void clear_radio_group(th_tree *tree, th_node *radio) {
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
    for (th_node *node = preorder_next(scope, scope); node != NULL; node = preorder_next(node, scope)) {
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
            clear_radio_group(tree, node);
        }
    } else {
        th_node_attr_del(tree, node, "checked", 7);
    }
    Py_END_CRITICAL_SECTION();
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_attr_set only fails on OOM */
}

/* Whether a control is barred from submission: its own disabled attribute, or a
   disabled fieldset between it and the form. */
static int control_disabled(th_node *control, th_node *form) {
    if (find_node_attr(control, TH_ATTR_DISABLED) != NULL) {
        return 1;
    }
    /* form is always an ancestor (collect_control only walks its descendants), so the walk stops there */
    for (th_node *ancestor = control->parent; ancestor != form; ancestor = ancestor->parent) {
        if (ancestor->atom == TH_TAG_FIELDSET && find_node_attr(ancestor, TH_ATTR_DISABLED) != NULL) {
            return 1;
        }
    }
    return 0;
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

/* Append each selected option of a select as a (name, value) pair: every selected
   one for a multiple select, the selected (or default first) one otherwise,
   skipping disabled options. */
static int collect_select(th_tree *tree, th_node *select, const th_node_attr *name, PyObject *pairs) {
    if (find_node_attr(select, TH_ATTR_MULTIPLE) != NULL) {
        for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
            if (find_node_attr(option, TH_ATTR_DISABLED) != NULL || find_node_attr(option, TH_ATTR_SELECTED) == NULL) {
                continue;
            }
            if (emit_pair(pairs, name, option_value_str(tree, option)) < 0) { /* GCOVR_EXCL_BR_LINE: OOM only */
                return -1;                                                    /* GCOVR_EXCL_LINE: alloc-failure */
            }
        }
        return 0;
    }
    th_node *chosen = NULL;
    th_node *first = NULL;
    for (th_node *option = next_option(select, select); option != NULL; option = next_option(option, select)) {
        if (find_node_attr(option, TH_ATTR_DISABLED) != NULL) {
            continue;
        }
        if (first == NULL) {
            first = option;
        }
        if (find_node_attr(option, TH_ATTR_SELECTED) != NULL) {
            chosen = option;
        }
    }
    th_node *use = chosen != NULL ? chosen : first;
    if (use == NULL) {
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
    const th_node_attr *name = find_node_attr(node, TH_ATTR_NAME);
    if (name == NULL || name->value == NULL || name->value_len == 0 || control_disabled(node, form)) {
        return 0;
    }
    if (atom == TH_TAG_SELECT) {
        return collect_select(tree, node, name, pairs);
    }
    if (atom == TH_TAG_TEXTAREA) {
        Py_ssize_t len;
        Py_UCS4 *buffer = th_node_text(tree, node, &len);
        if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *value = ucs4_to_str(buffer, len);
        PyMem_Free(buffer);
        return emit_pair(pairs, name, value);
    }
    enum field_kind kind = input_kind(node);
    if (kind == FIELD_BUTTONLIKE || kind == FIELD_FILE) {
        return 0;
    }
    if (kind == FIELD_CHECKABLE && find_node_attr(node, TH_ATTR_CHECKED) == NULL) {
        return 0;
    }
    return emit_pair(pairs, name, value_attr_or(node, kind == FIELD_CHECKABLE ? "on" : ""));
}

PyDoc_STRVAR(form_data_doc, "form_data()\n--\n\n"
                            "Collect this form's successful controls, following the WHATWG form-submission\n"
                            "entry-list rules. Controls without a non-empty name, disabled controls (their\n"
                            "own disabled or a disabled ancestor fieldset), buttons, and\n"
                            "file/submit/reset/image inputs are skipped; a checkbox or radio contributes\n"
                            "only when checked, a select one pair per selected option. Controls are matched\n"
                            "by containment in the form.\n\n"
                            ":returns: the (name, value) pairs in document order.");

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
    for (th_node *control = preorder_next(node, node); control != NULL; control = preorder_next(control, node)) {
        if (collect_control(tree, node, control, pairs) < 0) { /* GCOVR_EXCL_BR_LINE: fails only on OOM */
            error = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
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
    char *name = attr_key_utf8(name_obj, &name_len);
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
static void handle_drop_index(PyObject *handle_obj) {
    HandleObject *handle = (HandleObject *)handle_obj;
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    handle->index_offsets = NULL;
    handle->index_nodes = NULL;
    handle->index_built = 0;
    path_id_map_free(handle->path_ids);
    handle->path_ids = NULL;
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

PyObject *node_select(PyObject *self, PyObject *arg) {
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

PyObject *node_select_one(PyObject *self, PyObject *arg) {
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

/* Resolve the optional attr keyword into a lowercased UTF-8 name (the caller frees
   it with PyMem_Free), leaving *name NULL when the regex should run over text. A
   non-str, non-None attr raises TypeError. Returns 0, or -1 with an exception. */
static int regex_attr_name(PyObject *attr_obj, char **name, Py_ssize_t *name_len) {
    *name = NULL;
    *name_len = 0;
    if (attr_obj == NULL || attr_obj == Py_None) {
        return 0;
    }
    *name = attr_key_utf8(attr_obj, name_len);
    return *name == NULL ? -1 : 0;
}

PyObject *node_re(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *kw[] = {"", "attr", NULL};
    PyObject *pattern;
    PyObject *attr_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|$O:re", kw, &pattern, &attr_obj)) {
        return NULL;
    }
    char *attr_name;
    Py_ssize_t attr_len;
    if (regex_attr_name(attr_obj, &attr_name, &attr_len) < 0) {
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

PyObject *node_re_first(PyObject *self, PyObject *args, PyObject *kwds) {
    static char *kw[] = {"", "default", "attr", NULL};
    PyObject *pattern;
    PyObject *fallback = Py_None;
    PyObject *attr_obj = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|O$O:re_first", kw, &pattern, &fallback, &attr_obj)) {
        return NULL;
    }
    char *attr_name;
    Py_ssize_t attr_len;
    if (regex_attr_name(attr_obj, &attr_name, &attr_len) < 0) {
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
static int xpath_pyobject_to_scalar(PyObject *obj, xp_result *out);

/* The state an extension callback needs, threaded through xp_eval as a void *. */
typedef struct {
    PyObject *extensions; /* {(None, name): callable}; only set when non-empty */
    PyObject *handle;
    module_state *state;
    th_tree *tree;
} xpath_ext_ctx;

/* Grow `set` by one node (attr -1, the node itself), doubling capacity as needed. */
static int xpath_nodeset_push(xp_nodeset *set, th_node *node) {
    if (set->len == set->cap) {
        Py_ssize_t cap = set->cap ? set->cap * 2 : 8;
        xp_item *grown = PyMem_Realloc(set->items, (size_t)cap * sizeof(xp_item));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
            return -1;        /* GCOVR_EXCL_LINE */
        }
        set->items = grown;
        set->cap = cap;
    }
    set->items[set->len].node = node;
    set->items[set->len].attr = -1;
    set->len++;
    return 0;
}

/* Append the tree node a returned element wraps to `set`, rejecting an object that
   is not one of this module's nodes or an element bound to a different document. */
static int xpath_append_result_node(xpath_ext_ctx *ec, PyObject *obj, xp_nodeset *set) {
    if (!is_node(obj, ec->state)) {
        PyErr_Format(PyExc_TypeError,
                     "xpath extension result must be str, int, float, bool, an element, or an iterable of elements, "
                     "not %.80s",
                     Py_TYPE(obj)->tp_name);
        return -1;
    }
    NodeObject *node_obj = (NodeObject *)obj;
    if (node_obj->handle != ec->handle) {
        PyErr_SetString(PyExc_ValueError, "xpath extension returned an element from a different document");
        return -1;
    }
    return xpath_nodeset_push(set, node_obj->node);
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
    PyObject *item;
    while ((item = PyIter_Next(iterator)) != NULL) {
        int rc = xpath_append_result_node(ec, item, &out->nodes);
        Py_DECREF(item);
        if (rc < 0) {
            Py_DECREF(iterator);
            xp_result_free(out);
            return -1;
        }
    }
    Py_DECREF(iterator);
    if (PyErr_Occurred()) { /* the iterable raised partway through */
        xp_result_free(out);
        return -1;
    }
    return 0;
}

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
    int rc = xpath_extension_result(ec, result, out);
    Py_DECREF(result);
    return rc;
}

/* Marshal an evaluated xp_result to a Python object under the held handle critical
   section: a node-set to a list (elements as nodes, attribute/text values as str),
   a scalar to its float / str / bool. Always frees *result. Returns the object (a
   new reference), or NULL with a Python error set on an allocation failure. */
static PyObject *xpath_result_to_py(module_state *state, PyObject *handle, th_tree *tree, int smart_strings,
                                    xp_result *result) {
    PyObject *out;
    if (result->kind == XP_NODESET) {
        out = PyList_New(result->nodes.len);
        if (out == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
            xp_result_free(result); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < result->nodes.len; index++) {
            PyObject *item = xpath_item_to_py(state, handle, tree, result->nodes.items[index], smart_strings);
            if (item == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                Py_DECREF(out);         /* GCOVR_EXCL_LINE: allocation-failure path */
                xp_result_free(result); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            PyList_SET_ITEM(out, index, item);
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
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    const char *feature = NULL;
    PyObject *out = NULL;
    int status = 0;
    int compiled = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: the compile cache and the eval read the tree */
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    xpath_ext_ctx ext_ctx = {extensions, handle, state, tree};
    xp_program *prog = cached_xpath_compile(handle_obj, arg);
    if (prog != NULL) {
        compiled = 1;
        xp_result result;
        status = xp_eval(prog, tree, origin, vars, namespaces, extensions != NULL ? xpath_call_extension : NULL,
                         extensions != NULL ? &ext_ctx : NULL, &result, &feature);
        if (status == 0) {
            out = xpath_result_to_py(state, handle, tree, smart_strings, &result);
        }
    }
    Py_END_CRITICAL_SECTION();
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
static PyObject *xpath_run_program(module_state *state, PyObject *handle, th_node *origin, const xp_program *prog,
                                   const xp_bindings *vars, int smart_strings, PyObject *extensions) {
    const char *feature = NULL;
    PyObject *out = NULL;
    int status = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: the eval reads the tree */
    HandleObject *handle_obj = (HandleObject *)handle;
    th_tree *tree = handle_obj->tree;
    xpath_ext_ctx ext_ctx = {extensions, handle, state, tree};
    xp_result result;
    status = xp_eval(prog, tree, origin, vars, NULL, extensions != NULL ? xpath_call_extension : NULL,
                     extensions != NULL ? &ext_ctx : NULL, &result, &feature);
    if (status == 0) {
        out = xpath_result_to_py(state, handle, tree, smart_strings, &result);
    }
    Py_END_CRITICAL_SECTION();
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
    if (element->handle != reference_handle) {
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

PyObject *node_xpath(PyObject *self, PyObject *args, PyObject *kwds) {
    return xpath_eval_with_vars(self, args, kwds, "xpath");
}

PyObject *node_xpath_iter(PyObject *self, PyObject *args, PyObject *kwds) {
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

PyObject *node_xpath_one(PyObject *self, PyObject *args, PyObject *kwds) {
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

/* ---- XPath: the precompiled, reusable expression object (issue #267) ---- */

/* XPath(expression, *, smart_strings=False, extensions=None): parse the expression
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
static PyObject *xpath_compiled_call(PyObject *self, PyObject *args, PyObject *kwds) {
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
    return PyUnicode_FromFormat("XPath(%R)", ((XPathObject *)self)->expression);
}

PyDoc_STRVAR(xpath_compiled_doc, "XPath(expression, *, smart_strings=False, extensions=None)\n--\n\n"
                                 "A precompiled XPath 1.0 expression, parsed once and evaluated against many\n"
                                 "context nodes. Call it with a context Node and optional $name keyword\n"
                                 "variables: XPath(\"//td[@class=$cls]\")(row, cls=\"num\"). It returns the same\n"
                                 "results as Node.xpath. smart_strings and the extensions dict are bound here\n"
                                 "at construction. The object is immutable and re-entrant, so one instance can\n"
                                 "be shared across threads.");

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

PyObject *node_css_matches(PyObject *self, PyObject *arg) {
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

PyObject *node_css_closest(PyObject *self, PyObject *arg) {
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
        Py_ssize_t grown = *capacity != 0 ? *capacity * 2 : 16;
        prune_keep *resized = PyMem_Realloc(*buffer, (size_t)grown * sizeof(prune_keep));
        if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: a realloc failure cannot be forced from a test */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        *buffer = resized;
        *capacity = grown;
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
                            th_node *origin) {
    if (prune_push(buffer, count, capacity, node, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *ancestor = node->parent; ancestor != origin; ancestor = ancestor->parent) {
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

PyObject *node_prune(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    prune_keep *keep = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t capacity = 0;
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile((HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
        /* Pass 1: snapshot each match and its ancestor chain while the tree is
           intact. A string/regex selector can call back into Python, so no edit
           may run here; matching alone never rewires a node, so the snapshot lets
           pass 2 edit in pure C without dereferencing a stale pointer. */
        const sel_simple *single = sel_single_simple(compiled);
        sel_ctx ctx = {compiled->tree, origin, compiled->quirks};
        for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
            if (node->type != TH_NODE_ELEMENT) {
                continue;
            }
            if (!(single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin))) {
                continue;
            }
            if (prune_keep_match(&keep, &count, &capacity, node, origin) < 0) { /* GCOVR_EXCL_BR_LINE: allocation */
                error = 1;                                                      /* GCOVR_EXCL_LINE: allocation path */
                break;                                                          /* GCOVR_EXCL_LINE: allocation path */
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
    PyMem_Free(keep);
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
        Py_ssize_t grown = snapshot->capacity != 0 ? snapshot->capacity * 2 : 16;
        th_node **resized = PyMem_Realloc(snapshot->items, (size_t)grown * sizeof(th_node *));
        if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: a realloc failure cannot be forced from a test */
            return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        snapshot->items = resized;
        snapshot->capacity = grown;
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
    sel_ctx ctx = {compiled->tree, origin, compiled->quirks};
    for (th_node *node = origin->first_child; node != NULL; node = preorder_next(node, origin)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        if (!(single != NULL ? sel_match_simple(node, single, &ctx) : selector_matches(node, compiled, origin))) {
            continue;
        }
        if (snapshot_push(snapshot, node) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

PyObject *node_remove(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    node_snapshot snapshot = {NULL, 0, 0};
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile((HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
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
    PyMem_Free(snapshot.items);
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}

PyObject *node_strip_tags(PyObject *self, PyObject *arg) {
    if (check_selector_arg(arg) < 0) {
        return NULL;
    }
    PyObject *handle = ((NodeObject *)self)->handle;
    th_node *origin = ((NodeObject *)self)->node;
    node_snapshot snapshot = {NULL, 0, 0};
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: match and edit must see one stable tree */
    sel_compiled *compiled = cached_compile((HandleObject *)handle, arg);
    if (compiled == NULL) {
        error = 1;
    } else {
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
    PyMem_Free(snapshot.items);
    if (error) {
        return NULL;
    }
    return Py_NewRef(self);
}

PyDoc_STRVAR(element_doc, "An element node: a tag, a namespace, attributes, and child nodes.\n\n"
                          ":param tag: the tag name.\n"
                          ":param attrs: initial attributes; a list value sets a token-list attribute and\n"
                          "    None a valueless one.");

static PyObject *element_new(PyTypeObject *type, PyObject *args, PyObject *kwds);

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
                         ":param child: the node to append.");

PyDoc_STRVAR(extend_doc, "extend(children, /)\n--\n\n"
                         "Append every node from the iterable in order, each one moved or adopted\n"
                         "like append().\n\n"
                         ":param children: the nodes to append.");

PyDoc_STRVAR(insert_doc, "insert(index, child, /)\n--\n\n"
                         "Insert child among this element's children, counted and clamped like\n"
                         "list.insert.\n\n"
                         ":param index: position among the existing children.\n"
                         ":param child: the node to insert.");

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
                                ":returns: wrapper, now holding the moved children.");

/* --- classList: the space-separated class token set ------------------------ */

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
                            "with single-space separators.");

static PyObject *element_add_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_ADD);
}

PyDoc_STRVAR(remove_class_doc, "remove_class(name, /)\n--\n\n"
                               "Remove every occurrence of name from this element's class tokens and return\n"
                               "the element. Removing the last token leaves an empty class attribute.");

static PyObject *element_remove_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_REMOVE);
}

PyDoc_STRVAR(toggle_class_doc, "toggle_class(name, /)\n--\n\n"
                               "Remove name from this element's class tokens when present, add it when\n"
                               "absent, and return the element.");

static PyObject *element_toggle_class(PyObject *self, PyObject *arg) {
    return class_mutate(self, arg, CLASS_TOGGLE);
}

PyDoc_STRVAR(set_inner_html_doc, "set_inner_html(html, /)\n--\n\n"
                                 "Replace this element's children with the nodes parsed from html, a fragment\n"
                                 "parsed in this element's own context (the DOM innerHTML= setter). The\n"
                                 "string is run through the same HTML parser as parse(), so malformed markup\n"
                                 "is repaired the same way.");

PyDoc_STRVAR(set_text_doc, "set_text(text, /)\n--\n\n"
                           "Replace this element's children with a single Text node holding text\n"
                           "verbatim (the DOM textContent= setter). text is never parsed, so any markup\n"
                           "in it is escaped on serialization. The Element.text= setter is equivalent.");

PyDoc_STRVAR(insert_adjacent_html_doc, "insert_adjacent_html(position, html, /)\n--\n\n"
                                       "Parse html as a fragment and insert it relative to this element at position,\n"
                                       "one of 'beforebegin', 'afterbegin', 'beforeend', or 'afterend' (the DOM\n"
                                       "insertAdjacentHTML, matched case-insensitively). 'beforebegin' and 'afterend'\n"
                                       "place the nodes among this element's siblings, so they require an element\n"
                                       "parent; 'afterbegin' and 'beforeend' add them as the first or last children.\n"
                                       "The fragment parses in the context of the element that will hold it.");

/* ---- css_path() / xpath_path(): the unique locator for a node ---- */

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
    Py_ssize_t cap = buf->cap ? buf->cap : 64;
    while (cap < buf->len + extra) {
        cap *= 2;
    }
    Py_UCS4 *grown = PyMem_Realloc(buf->data, (size_t)cap * sizeof(Py_UCS4));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        buf->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    buf->data = grown;
    buf->cap = cap;
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
static path_id_map *path_id_map_build(th_node *document, int ci) {
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

/* The 1-based position of node among its same-type element siblings, setting
   *needs_index when more than one such sibling exists so the position disambiguates
   it. Scans preceding siblings once (their count is the position), and only scans
   following siblings when node is the first, mirroring libxml2's xmlGetNodePath. */
static int path_step_index(th_node *node, int *needs_index) {
    int index = 1;
    for (th_node *sibling = node->prev_sibling; sibling != NULL; sibling = sibling->prev_sibling) {
        if (sibling->type == TH_NODE_ELEMENT && sel_same_type(node, sibling)) {
            index++;
        }
    }
    *needs_index = index > 1 ? 1 : !sel_no_sibling(node, 1, 1);
    return index;
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
    if (document != NULL && handle_obj->path_ids == NULL) {
        handle_obj->path_ids = path_id_map_build(document, th_tree_quirks(tree));
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
                int sibling_index = path_step_index(element, &needs_index);
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
            int sibling_index = path_step_index(element, &needs_index);
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
PyObject *make_element(PyTypeObject *type, PyObject *tag, PyObject *attrs) {
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
            th_node_remove(child);
            th_node_append_child(wrapper, child);
        }
        th_node_append_child(parent, wrapper);
    }
    Py_END_CRITICAL_SECTION();
    if (error) {
        return NULL;
    }
    return Py_NewRef(wrapper_obj);
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
PyObject *node_insert_before(PyObject *self, PyObject *nodes) {
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

PyObject *node_insert_after(PyObject *self, PyObject *nodes) {
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

PyObject *node_replace_with(PyObject *self, PyObject *nodes) {
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

PyObject *node_wrap_in(PyObject *self, PyObject *wrapper_obj) {
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
    th_node *wrapper_node = ((NodeObject *)wrapper_obj)->node;
    int error = 0;
    int no_parent = 0;
    const char *value_error = NULL;
    Py_BEGIN_CRITICAL_SECTION(node->handle);
    handle_drop_index(node->handle);
    /* The parent and the run are resolved under the lock; reading them earlier could
       stale them against a concurrent move that relinks this node to another parent. */
    th_node *parent = first->parent;
    if (parent == NULL) {
        no_parent = 1;
    } else {
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
            th_node *wrapper = adopt_into(node, parent, wrapper_obj);
            if (wrapper == NULL) {
                error = 1;
            } else {
                th_node_insert_before(parent, wrapper, first);
                for (th_node *cursor = first;;) {
                    th_node *next = cursor->next_sibling;
                    int is_last = cursor == last;
                    th_node_remove(cursor);
                    th_node_append_child(wrapper, cursor);
                    if (is_last) {
                        break;
                    }
                    cursor = next;
                }
            }
        }
    }
    Py_END_CRITICAL_SECTION();
    if (no_parent) {
        PyErr_SetString(PyExc_ValueError, "node has no parent");
        return NULL;
    }
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

PyObject *node_extract(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove(((NodeObject *)self)->node);
    Py_END_CRITICAL_SECTION();
    return Py_NewRef(self);
}

PyObject *node_decompose(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    th_node_remove(((NodeObject *)self)->node);
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

/* Parse html as a fragment in the context element's own context, so its content
   model and namespace drive the parse exactly as the DOM innerHTML setter requires
   (the context name is the tag, prefixed "svg "/"math " for a foreign element). The
   parse only borrows html and never touches the live tree, so it runs before the
   per-tree lock is taken and never holds a structural pointer across it. The caller
   owns the returned tree (free it with th_tree_free). NULL with an exception set on a
   non-encodable (lone-surrogate) tag name or an allocation failure. */
static th_tree *parse_fragment_in_context(th_node *context, PyObject *html) {
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
                                               name, name_len, 0);
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
            th_node_insert_before(parent, copy, target);
            break;
        case TH_ADJ_AFTERBEGIN:
            th_node_insert_before(target, copy, first_ref);
            break;
        case TH_ADJ_BEFOREEND:
            th_node_append_child(target, copy);
            break;
        case TH_ADJ_AFTEREND:
            th_node_insert_before(parent, copy, cursor->next_sibling);
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
    th_tree *fragment = parse_fragment_in_context(node, html);
    if (fragment == NULL) {
        return NULL;
    }
    th_tree *dest = tree_of(self);
    int error;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    handle_drop_index(((NodeObject *)self)->handle);
    while (node->first_child != NULL) {
        th_node_remove(node->first_child);
    }
    error = splice_fragment(dest, fragment, NULL, node, TH_ADJ_BEFOREEND);
    Py_END_CRITICAL_SECTION();
    th_tree_free(fragment);
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
    th_tree *fragment = parse_fragment_in_context(context, html);
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
    if (error) {                 /* GCOVR_EXCL_BR_LINE: splice only fails on a copy allocation failure */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}
