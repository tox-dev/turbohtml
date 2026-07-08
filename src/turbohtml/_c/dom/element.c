/* Elements and their attributes: the live attribute view, form-control value semantics, and the
   find/select/xpath/regex query plus structural-mutation bindings. */

#include "dom/nodes.h"

#include "core/vec.h" /* th_grow_cap overflow-safe buffer growth */

#include "css/select/selector.h"

static int validate_name(PyObject *name, int is_attr);

static int element_attr_value(PyObject *value, Py_UCS4 **points, Py_ssize_t *len, int *has_value);

/* ASCII-lowercase a str key into a freshly allocated UTF-8 buffer so a lookup
   matches the parser's lowercased names; *out_len its length. NULL with TypeError
   when the key is not a str. Caller frees with PyMem_Free. */
char *attr_key_utf8(PyObject *key, Py_ssize_t *out_len) {
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
                            "only when checked, a select one pair per selected non-disabled option (the\n"
                            "default first option only when its display size is 1). Controls inside a\n"
                            "template's contents have no form owner and are excluded. Controls are matched\n"
                            "by containment in the form.\n\n"
                            ":returns: the (name, value) pairs in document order.");

/* The next node after current's whole subtree within root, skipping its descendants;
   current is always a descendant of root, so the climb reaches root before NULL. */
static th_node *after_subtree_within(th_node *current, th_node *root) {
    while (current != root) {
        if (current->next_sibling != NULL) {
            return current->next_sibling;
        }
        current = current->parent;
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
    th_node *control = preorder_next(node, node);
    while (control != NULL) {
        if (collect_control(tree, node, control, pairs) < 0) { /* GCOVR_EXCL_BR_LINE: fails only on OOM */
            error = 1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* A template's contents live in a separate inert fragment; those controls have
           no form owner, so skip the subtree instead of descending (WHATWG §4.10.3). */
        control = control->atom == TH_TAG_TEMPLATE ? after_subtree_within(control, node) : preorder_next(control, node);
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
void handle_drop_index(PyObject *handle_obj) {
    HandleObject *handle = (HandleObject *)handle_obj;
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    handle->index_offsets = NULL;
    handle->index_nodes = NULL;
    handle->index_built = 0;
    path_id_map_free(handle->path_ids);
    handle->path_ids = NULL;
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
                                 "parsed in this element's own context (the DOM innerHTML= setter). The\n"
                                 "string is run through the same HTML parser as parse(), so malformed markup\n"
                                 "is repaired the same way.\n\n"
                                 ":raises TypeError: if html is not a str.");

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
   whose UTF-8 exceeds this is treated as an unknown atom. */
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
        PyErr_Clear(); /* a surrogate or very long custom tag is not in the table */
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
    PyObject *element = make_element(type, tag, attrs);
    if (element == NULL) {
        return NULL;
    }
    if (children != NULL && children != Py_None && append_build_children(element, tag, children) < 0) {
        Py_DECREF(element);
        return NULL;
    }
    return element;
}

/* Prepare child_obj to become a child of dest_parent in anchor's tree and return
   the th_node to link (already detached from any old position). A node in the same
   tree is moved in place, its wrapper unchanged; a node from another tree is
   deep-copied in and its wrapper re-pointed at the copy, so the source tree frees
   on its own. NULL with an exception on a non-node, a Document, a cycle (making a
   node a descendant of itself), or allocation failure. */
th_node *adopt_into(NodeObject *anchor, th_node *dest_parent, PyObject *child_obj) {
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
        th_node_remove_observed(dest_tree, child->node);
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
    th_node_remove_observed(tree_of(child_obj), child->node);
    Py_END_CRITICAL_SECTION();
    Py_SETREF(child->handle, Py_NewRef(anchor->handle));
    child->node = copy;
    return copy;
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
    int error = 0;
    Py_BEGIN_CRITICAL_SECTION(self->handle);
    for (Py_ssize_t index = 0; index < count; index++) {
        th_node *node = adopt_into(self, self->node, PySequence_Fast_GET_ITEM(sequence, index));
        if (node == NULL) {
            error = 1;
            break;
        }
        th_node_append_child_observed(tree_of(element), self->node, node);
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(sequence);
    return error ? -1 : 0;
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
        th_node_append_child_observed(tree_of(self), parent, node);
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
        th_node_append_child_observed(tree_of(self), parent, node);
    }
    Py_END_CRITICAL_SECTION();
    Py_DECREF(iterator);
    if (error || PyErr_Occurred()) {
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
static int shell_fill(module_state *state, PyObject *handle, th_node *section, PyObject *children) {
    PyObject *wrapper = node_wrap(state, handle, section);
    if (wrapper == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = element_extend(wrapper, children);
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
        th_node_insert_before_observed(tree_of(self), parent, node, ref != NULL && ref->parent == parent ? ref : NULL);
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
        th_node_insert_before_observed(tree_of(self), parent, node, ref);
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
        th_node_insert_before_observed(tree_of(self), parent, node, cursor->next_sibling);
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
        th_node_insert_before_observed(tree_of(self), parent, node, ref);
    }
    if (!error && !keep_self) {
        th_node_remove_observed(tree_of(self), ref);
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
            th_node_insert_before_observed(tree_of(self), parent, wrapper, node->node);
            th_node_remove_observed(tree_of(self), node->node);
            th_node_append_child_observed(tree_of(self), wrapper, node->node);
        }
    } else {
        NodeObject *wrapper = (NodeObject *)wrapper_obj;
        th_node *moved = adopt_into(wrapper, wrapper->node, self);
        if (moved == NULL) {
            error = 1;
        } else {
            th_node_append_child_observed(tree_of(self), wrapper->node, moved);
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
        th_node_remove_observed(tree_of(self), child);
        th_node_insert_before_observed(tree_of(self), parent, child, node);
    }
    th_node_remove_observed(tree_of(self), node);
    Py_END_CRITICAL_SECTION();
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

/* Parse html as a fragment in the context element's own context, so its content
   model and namespace drive the parse exactly as the DOM innerHTML setter requires
   (the context name is the tag, prefixed "svg "/"math " for a foreign element). The
   parse only borrows html and never touches the live tree, so it runs before the
   per-tree lock is taken and never holds a structural pointer across it. The caller
   owns the returned tree (free it with th_tree_free). NULL with an exception set on a
   non-encodable (lone-surrogate) tag name or an allocation failure. */
static th_tree *parse_fragment_in_context(th_node *context, PyObject *html, int scripting) {
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
    th_tree *fragment = parse_fragment_in_context(node, html, th_tree_scripting(tree_of(self)));
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
    th_tree *fragment = parse_fragment_in_context(context, html, th_tree_scripting(tree_of(self)));
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
