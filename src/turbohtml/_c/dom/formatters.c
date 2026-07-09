/* The serialize() option objects: Minify's round-trip-safe transforms and Indent's pretty-print unit,
   exposed as immutable value types. */

#include "dom/nodes.h"

/* Read the two toggles off a JSMinify and cache them as flags. The argument was already
   confirmed to be a JSMinify, so the attributes always exist; only allocation can fail. */
static int minify_unpack_js(PyObject *config, unsigned char *fold, unsigned char *mangle) {
    PyObject *fold_attr = PyObject_GetAttrString(config, "fold");
    if (fold_attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    PyObject *mangle_attr = PyObject_GetAttrString(config, "mangle");
    if (mangle_attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(fold_attr);  /* GCOVR_EXCL_LINE */
        return -1;             /* GCOVR_EXCL_LINE */
    }
    *fold = (unsigned char)(PyObject_IsTrue(fold_attr) == 1);
    *mangle = (unsigned char)(PyObject_IsTrue(mangle_attr) == 1);
    Py_DECREF(fold_attr);
    Py_DECREF(mangle_attr);
    return 0;
}

/* Read the baseline off a CSSMinify: an int year, or None (the default) as 0, which the CSS
   engine reads as "only long-interoperable syntax". The argument was already confirmed to be
   a CSSMinify, so the attribute always exists. Returns 0 on success, -1 with an exception on
   allocation or an overflowing year. */
static int minify_unpack_css(PyObject *config, int *baseline) {
    PyObject *baseline_attr = PyObject_GetAttrString(config, "baseline");
    if (baseline_attr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return -1;               /* GCOVR_EXCL_LINE */
    }
    if (baseline_attr == Py_None) {
        *baseline = 0;
        Py_DECREF(baseline_attr);
        return 0;
    }
    long year = PyLong_AsLong(baseline_attr);
    Py_DECREF(baseline_attr);
    if (year == -1 && PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a year wider than long cannot be forced here */
        return -1;                        /* GCOVR_EXCL_LINE: overflow path */
    }
    *baseline = (int)year;
    return 0;
}

static PyObject *minify_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"collapse_whitespace",
                               "omit_optional_tags",
                               "unquote_attributes",
                               "strip_comments",
                               "minify_js",
                               "minify_css",
                               NULL};
    int collapse = 1;
    int omit = 1;
    int unquote = 1;
    int strip = 1;
    PyObject *minify_js = NULL;  /* a JSMinify enables the inline-<script> pass; None (the default) leaves it off */
    PyObject *minify_css = NULL; /* a CSSMinify enables the <style>/style="" pass; None (the default) leaves it off */
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$ppppOO:Minify", keywords, &collapse, &omit, &unquote, &strip,
                                     &minify_js, &minify_css)) {
        return NULL;
    }
    unsigned char js = 0;
    unsigned char js_fold = 0;
    unsigned char js_mangle = 0;
    if (minify_js != NULL && minify_js != Py_None) {
        module_state *state = PyType_GetModuleState(type);
        int matches = PyObject_IsInstance(minify_js, state->js_minify_type);
        if (matches < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;   /* GCOVR_EXCL_LINE */
        }
        if (!matches) {
            PyErr_SetString(PyExc_TypeError, "minify_js must be a JSMinify or None");
            return NULL;
        }
        if (minify_unpack_js(minify_js, &js_fold, &js_mangle) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;                                             /* GCOVR_EXCL_LINE */
        }
        js = 1;
    }
    unsigned char css = 0;
    int css_baseline = 0;
    if (minify_css != NULL && minify_css != Py_None) {
        module_state *state = PyType_GetModuleState(type);
        int matches = PyObject_IsInstance(minify_css, state->css_minify_type);
        if (matches < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;   /* GCOVR_EXCL_LINE */
        }
        if (!matches) {
            PyErr_SetString(PyExc_TypeError, "minify_css must be a CSSMinify or None");
            return NULL;
        }
        if (minify_unpack_css(minify_css, &css_baseline) < 0) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
            return NULL;                                        /* GCOVR_EXCL_LINE */
        }
        css = 1;
    }
    MinifyObject *self = (MinifyObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->collapse_whitespace = (unsigned char)collapse;
    self->omit_optional_tags = (unsigned char)omit;
    self->unquote_attributes = (unsigned char)unquote;
    self->strip_comments = (unsigned char)strip;
    self->minify_js = js;
    self->minify_js_fold = js_fold;
    self->minify_js_mangle = js_mangle;
    self->minify_css = css;
    self->minify_css_baseline = css_baseline;
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

/* Rebuild the JSMinify the constructor was handed (it is a frozen value type, so a fresh
   one with the same toggles compares equal), or None when the script pass is off. */
static PyObject *minify_get_minify_js(PyObject *self, void *Py_UNUSED(closure)) {
    MinifyObject *minify = (MinifyObject *)self;
    if (!minify->minify_js) {
        Py_RETURN_NONE;
    }
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    /* JSMinify(mangle, fold) -- positional order matches the dataclass field order */
    return PyObject_CallFunction(state->js_minify_type, "OO", minify->minify_js_mangle ? Py_True : Py_False,
                                 minify->minify_js_fold ? Py_True : Py_False);
}

/* Rebuild the CSSMinify the constructor was handed (a frozen value type, so a fresh one with
   the same baseline compares equal), or None when the style pass is off. A stored baseline of
   0 rebuilds as CSSMinify(baseline=None), the same normalization CSSMinify itself applies. */
static PyObject *minify_get_minify_css(PyObject *self, void *Py_UNUSED(closure)) {
    MinifyObject *minify = (MinifyObject *)self;
    if (!minify->minify_css) {
        Py_RETURN_NONE;
    }
    PyObject *baseline =
        minify->minify_css_baseline ? PyLong_FromLong(minify->minify_css_baseline) : Py_NewRef(Py_None);
    if (baseline == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE */
    }
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    PyObject *config = PyObject_CallOneArg(state->css_minify_type, baseline);
    Py_DECREF(baseline);
    return config;
}

static PyGetSetDef minify_getset[] = {
    {"collapse_whitespace", minify_get_collapse, NULL, "fold insignificant whitespace runs to a single space", NULL},
    {"omit_optional_tags", minify_get_omit, NULL, "drop the start/end tags the WHATWG rules make optional", NULL},
    {"unquote_attributes", minify_get_unquote, NULL, "drop redundant attribute quotes and empty values", NULL},
    {"strip_comments", minify_get_strip, NULL, "remove comment nodes", NULL},
    {"minify_js", minify_get_minify_js, NULL, "the JSMinify config for inline <script>, or None when off", NULL},
    {"minify_css", minify_get_minify_css, NULL, "the CSSMinify config for <style>/style=\"\", or None when off", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyObject *turbohtml_register_js_minify(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->js_minify_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

PyObject *turbohtml_register_css_minify(PyObject *module, PyObject *type) {
    module_state *state = PyModule_GetState(module);
    Py_XSETREF(state->css_minify_type, Py_NewRef(type));
    Py_RETURN_NONE;
}

/* Pack every flag into the low bits so equality and hashing reduce to one integer compare; the
   script toggles only differ when the JS pass is on, and the baseline (shifted above the flags)
   only when the CSS pass is on, keeping two off configs equal regardless of their cached fields. */
static long minify_bits(MinifyObject *self) {
    return self->collapse_whitespace | self->omit_optional_tags << 1 | self->unquote_attributes << 2 |
           self->strip_comments << 3 | self->minify_js << 4 | self->minify_js_fold << 5 | self->minify_js_mangle << 6 |
           self->minify_css << 7 | (long)self->minify_css_baseline << 8;
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
    /* mirror JSMinify's own repr (field order mangle, fold) rather than build the object */
    PyObject *js = minify->minify_js
                       ? th_str_format("JSMinify(mangle=%s, fold=%s)", minify->minify_js_mangle ? "True" : "False",
                                       minify->minify_js_fold ? "True" : "False")
                       : PyUnicode_FromString("None");
    if (js == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        return NULL;  /* GCOVR_EXCL_LINE */
    }
    /* mirror CSSMinify's own dataclass repr (baseline None when the year is 0) */
    PyObject *css = minify->minify_css ? (minify->minify_css_baseline
                                              ? th_str_format("CSSMinify(baseline=%d)", minify->minify_css_baseline)
                                              : PyUnicode_FromString("CSSMinify(baseline=None)"))
                                       : PyUnicode_FromString("None");
    if (css == NULL) { /* GCOVR_EXCL_BR_LINE: allocation-failure path */
        Py_DECREF(js); /* GCOVR_EXCL_LINE */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    PyObject *repr = th_str_format(
        "Minify(collapse_whitespace=%s, omit_optional_tags=%s, unquote_attributes=%s, "
        "strip_comments=%s, minify_js=%U, minify_css=%U)",
        minify->collapse_whitespace ? "True" : "False", minify->omit_optional_tags ? "True" : "False",
        minify->unquote_attributes ? "True" : "False", minify->strip_comments ? "True" : "False", js, css);
    Py_DECREF(js);
    Py_DECREF(css);
    return repr;
}

PyDoc_STRVAR(minify_doc, "Minify(*, collapse_whitespace=True, omit_optional_tags=True, unquote_attributes=True, "
                         "strip_comments=True, minify_js=None, minify_css=None)\n--\n\n"
                         "A serialize(layout=...)/encode(layout=...) mode that shrinks the output. Each\n"
                         "markup flag toggles one round-trip-safe transform: the minified output always\n"
                         "reparses to the same tree.\n\n"
                         ":param collapse_whitespace: collapse runs of insignificant whitespace.\n"
                         ":param omit_optional_tags: drop start/end tags the parser can infer.\n"
                         ":param unquote_attributes: remove quotes around attribute values that allow it.\n"
                         ":param strip_comments: remove comments.\n"
                         ":param minify_js: a JSMinify to also minify inline <script> JavaScript, or None\n"
                         "    (the default) to leave scripts untouched. A script that fails to parse is\n"
                         "    emitted verbatim, so one bad script never breaks serialization.\n"
                         ":param minify_css: a CSSMinify to also minify <style> element bodies and style\n"
                         "    attribute values through the value-safe CSS minifier, or None (the default)\n"
                         "    to leave CSS untouched. Its baseline bounds how new the output syntax may be.");

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
    PyObject *repr = th_str_format("Indent(%R)", unit);
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
