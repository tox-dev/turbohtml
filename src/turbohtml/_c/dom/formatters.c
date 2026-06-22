/* The serialize() option objects: Minify's round-trip-safe transforms and Indent's pretty-print unit,
   exposed as immutable value types. */

#include "dom/nodes.h"

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
                         "to the same tree.\n\n"
                         ":param collapse_whitespace: collapse runs of insignificant whitespace.\n"
                         ":param omit_optional_tags: drop start/end tags the parser can infer.\n"
                         ":param unquote_attributes: remove quotes around attribute values that allow it.\n"
                         ":param strip_comments: remove comments.");

static PyType_Slot minify_slots[] = {
    {Py_tp_doc, (void *)minify_doc},
    {Py_tp_new, minify_new},
    {Py_tp_repr, minify_repr},
    {Py_tp_getset, minify_getset},
    {Py_tp_richcompare, minify_richcompare},
    {Py_tp_hash, minify_hash},
    {0, NULL},
};

PyType_Spec minify_spec = {
    .name = "turbohtml._html.Minify",
    .basicsize = sizeof(MinifyObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = minify_slots,
};

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
                         "A serialize(layout=...)/encode(layout=...) mode that pretty-prints. It adds\n"
                         "whitespace, so unlike the compact default it does not preserve meaning.\n\n"
                         ":param indent: the per-level unit: an int for that many spaces, or a string\n"
                         "    used verbatim.");

static PyType_Slot indent_slots[] = {
    {Py_tp_doc, (void *)indent_doc}, {Py_tp_new, indent_new},
    {Py_tp_dealloc, indent_dealloc}, {Py_tp_repr, indent_repr},
    {Py_tp_getset, indent_getset},   {Py_tp_richcompare, indent_richcompare},
    {Py_tp_hash, indent_hash},       {0, NULL},
};

PyType_Spec indent_spec = {
    .name = "turbohtml._html.Indent",
    .basicsize = sizeof(IndentObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = indent_slots,
};
