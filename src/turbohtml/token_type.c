/* The Token type and the TokenType enum.

   A Token owns a copy of the state machine's emitted record (the machine reuses
   its records, so the copy is what keeps a token valid after iteration moves
   on). The Python-visible values — the tag name, the text, the attribute list —
   are built lazily on attribute access, so a token the caller inspects only to
   check its kind never pays for building a string or a list. */

#include "tokenizer_py.h"

typedef struct {
    PyObject_HEAD th_token record;
} TokenObject;

static const char *const KIND_NAMES[5] = {"TEXT", "START_TAG", "END_TAG", "COMMENT", "DOCTYPE"};

static module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static PyObject *buf_to_str(const th_buf *buf) {
    if (buf->len == 0) {
        return PyUnicode_New(0, 0);
    }
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, buf->data, buf->len);
}

PyObject *token_from_record(module_state *state, th_token *record) {
    TokenObject *self = PyObject_GC_New(TokenObject, (PyTypeObject *)state->token_type);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    memset(&self->record, 0, sizeof(self->record));
    if (record->kind == TH_TEXT && record->text.len >= 512) {
        /* move a large text run instead of copying it; the machine's record
           simply regrows, which costs far less than duplicating the run */
        self->record.kind = TH_TEXT;
        self->record.line = record->line;
        self->record.col = record->col;
        self->record.text = record->text;
        record->text.data = NULL;
        record->text.len = 0;
        record->text.cap = 0;
    } else if (th_token_copy(&self->record, record) < 0) { /* GCOVR_EXCL_BR_LINE */
        th_token_clear(&self->record);                     /* GCOVR_EXCL_LINE */
        PyObject_GC_Del(self);                             /* GCOVR_EXCL_LINE */
        return NULL;                                       /* GCOVR_EXCL_LINE */
    }
    PyObject_GC_Track(self);
    return (PyObject *)self;
}

static int token_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self)); /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    return 0;
}

static void token_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    th_token_clear(&((TokenObject *)self)->record);
    type->tp_free(self);
    Py_DECREF(type);
}

static int is_tag(const th_token *record) {
    return record->kind == TH_START_TAG || record->kind == TH_END_TAG;
}

static PyObject *token_get_type(PyObject *self, void *Py_UNUSED(closure)) {
    module_state *state = state_of(self);
    return Py_NewRef(state->kinds[((TokenObject *)self)->record.kind]);
}

static PyObject *token_get_data(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (record->kind == TH_TEXT || record->kind == TH_COMMENT) {
        return buf_to_str(&record->text);
    }
    Py_RETURN_NONE;
}

static PyObject *token_get_tag(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (is_tag(record)) {
        return buf_to_str(&record->name);
    }
    Py_RETURN_NONE;
}

/* Build the attribute list, keeping the first occurrence of each name (the spec
   discards later duplicates) and mapping a valueless attribute to None. */
static PyObject *token_get_attrs(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (!is_tag(record)) {
        Py_RETURN_NONE;
    }
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    for (Py_ssize_t i = 0; i < record->attr_count; i++) {
        const th_attr *attr = &record->attrs[i];
        int duplicate = 0;
        for (Py_ssize_t j = 0; j < i; j++) {
            const th_attr *prior = &record->attrs[j];
            if (prior->name.len == attr->name.len &&
                memcmp(prior->name.data, attr->name.data, (size_t)attr->name.len * sizeof(Py_UCS4)) == 0) {
                duplicate = 1;
                break;
            }
        }
        if (duplicate) {
            continue;
        }
        PyObject *name = buf_to_str(&attr->name);
        PyObject *value = attr->has_value ? buf_to_str(&attr->value) : Py_NewRef(Py_None);
        if (name == NULL || value == NULL) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE */
            Py_XDECREF(value);               /* GCOVR_EXCL_LINE */
            Py_DECREF(list);                 /* GCOVR_EXCL_LINE */
            return NULL;                     /* GCOVR_EXCL_LINE */
        }
        PyObject *pair = PyTuple_Pack(2, name, value);
        Py_DECREF(name);
        Py_DECREF(value);
        if (pair == NULL || PyList_Append(list, pair) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(pair);                                /* GCOVR_EXCL_LINE */
            Py_DECREF(list);                                 /* GCOVR_EXCL_LINE */
            return NULL;                                     /* GCOVR_EXCL_LINE */
        }
        Py_DECREF(pair);
    }
    return list;
}

static PyObject *token_get_self_closing(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    return PyBool_FromLong(is_tag(record) && record->self_closing);
}

static PyObject *token_get_name(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (record->kind == TH_DOCTYPE) {
        return buf_to_str(&record->name);
    }
    Py_RETURN_NONE;
}

static PyObject *token_get_public_id(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (record->kind == TH_DOCTYPE && record->has_public_id) {
        return buf_to_str(&record->public_id);
    }
    Py_RETURN_NONE;
}

static PyObject *token_get_system_id(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    if (record->kind == TH_DOCTYPE && record->has_system_id) {
        return buf_to_str(&record->system_id);
    }
    Py_RETURN_NONE;
}

static PyObject *token_get_force_quirks(PyObject *self, void *Py_UNUSED(closure)) {
    const th_token *record = &((TokenObject *)self)->record;
    return PyBool_FromLong(record->kind == TH_DOCTYPE && record->force_quirks);
}

static PyGetSetDef token_getset[] = {
    {"type", token_get_type, NULL, "the TokenType of this token", NULL},
    {"data", token_get_data, NULL, "text run or comment data, else None", NULL},
    {"tag", token_get_tag, NULL, "lowercased tag name for start/end tags, else None", NULL},
    {"attrs", token_get_attrs, NULL, "attribute (name, value) pairs for tags, else None", NULL},
    {"self_closing", token_get_self_closing, NULL, "whether a start tag carried a trailing slash", NULL},
    {"name", token_get_name, NULL, "DOCTYPE name, else None", NULL},
    {"public_id", token_get_public_id, NULL, "DOCTYPE public identifier, else None", NULL},
    {"system_id", token_get_system_id, NULL, "DOCTYPE system identifier, else None", NULL},
    {"force_quirks", token_get_force_quirks, NULL, "whether a DOCTYPE forces quirks mode", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(token_attr_doc, "attr(name, default=None)\n--\n\n"
                             "Return the value of attribute name on a start or end tag. A valueless\n"
                             "attribute yields None; a missing attribute yields default.");

static PyObject *token_attr(PyObject *self, PyObject *args) {
    const char *name;
    Py_ssize_t name_len;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTuple(args, "s#|O:attr", &name, &name_len, &fallback)) {
        return NULL;
    }
    const th_token *record = &((TokenObject *)self)->record;
    if (is_tag(record)) {
        for (Py_ssize_t i = 0; i < record->attr_count; i++) {
            const th_attr *attr = &record->attrs[i];
            if (attr->name.len != name_len) {
                continue;
            }
            int match = 1;
            for (Py_ssize_t j = 0; j < name_len; j++) {
                if (attr->name.data[j] != (Py_UCS4)(unsigned char)name[j]) {
                    match = 0;
                    break;
                }
            }
            if (match) {
                return attr->has_value ? buf_to_str(&attr->value) : Py_NewRef(Py_None);
            }
        }
    }
    return Py_NewRef(fallback);
}

PyDoc_STRVAR(token_getpos_doc, "getpos()\n--\n\n"
                               "Return the (line, column) where this token began, 1-based line and\n"
                               "0-based column, matching html.parser.HTMLParser.getpos().");

static PyObject *token_getpos(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    const th_token *record = &((TokenObject *)self)->record;
    return Py_BuildValue("(nn)", record->line, record->col);
}

static PyObject *token_repr(PyObject *self) {
    const th_token *record = &((TokenObject *)self)->record;
    const char *kind = KIND_NAMES[record->kind];
    if (is_tag(record)) {
        PyObject *name = buf_to_str(&record->name);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE */
            return NULL;    /* GCOVR_EXCL_LINE */
        }
        PyObject *repr = PyUnicode_FromFormat("Token(%s, tag=%R)", kind, name);
        Py_DECREF(name);
        return repr;
    }
    if (record->kind == TH_DOCTYPE) {
        PyObject *name = buf_to_str(&record->name);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE */
            return NULL;    /* GCOVR_EXCL_LINE */
        }
        PyObject *repr = PyUnicode_FromFormat("Token(DOCTYPE, name=%R)", name);
        Py_DECREF(name);
        return repr;
    }
    PyObject *data = buf_to_str(&record->text);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    PyObject *repr = PyUnicode_FromFormat("Token(%s, data=%R)", kind, data);
    Py_DECREF(data);
    return repr;
}

static PyMethodDef token_methods[] = {
    {"attr", token_attr, METH_VARARGS, token_attr_doc},
    {"getpos", token_getpos, METH_NOARGS, token_getpos_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(token_doc, "An HTML token produced by Tokenizer or tokenize(). Immutable; the meaningful\n"
                        "attributes depend on .type.");

static PyType_Slot token_slots[] = {
    {Py_tp_doc, (void *)token_doc},
    {Py_tp_dealloc, token_dealloc},
    {Py_tp_traverse, token_traverse},
    {Py_tp_repr, token_repr},
    {Py_tp_getset, token_getset},
    {Py_tp_methods, token_methods},
    {0, NULL},
};

static PyType_Spec token_spec = {
    .name = "turbohtml._html.Token",
    .basicsize = sizeof(TokenObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = token_slots,
};

/* Build the TokenType IntEnum and cache its members for fast Token.type. */
static int build_kind_enum(PyObject *module, module_state *state) {
    PyObject *members = PyDict_New();
    if (members == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    for (int i = 0; i < 5; i++) {
        PyObject *value = PyLong_FromLong(i);
        if (value == NULL || PyDict_SetItemString(members, KIND_NAMES[i], value) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(value);                                                          /* GCOVR_EXCL_LINE */
            Py_DECREF(members);                                                         /* GCOVR_EXCL_LINE */
            return -1;                                                                  /* GCOVR_EXCL_LINE */
        }
        Py_DECREF(value);
    }
    PyObject *enum_module = PyImport_ImportModule("enum");
    if (enum_module == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(members);    /* GCOVR_EXCL_LINE */
        return -1;             /* GCOVR_EXCL_LINE */
    }
    PyObject *int_enum = PyObject_GetAttrString(enum_module, "IntEnum");
    Py_DECREF(enum_module);
    if (int_enum == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(members); /* GCOVR_EXCL_LINE */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    PyObject *args = Py_BuildValue("(sO)", "TokenType", members);
    Py_DECREF(members);
    PyObject *kwargs = Py_BuildValue("{s:s,s:s}", "module", "turbohtml", "qualname", "TokenType");
    PyObject *kind_enum = NULL;
    if (args != NULL && kwargs != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        kind_enum = PyObject_Call(int_enum, args, kwargs);
    }
    Py_DECREF(int_enum);
    Py_XDECREF(args);
    Py_XDECREF(kwargs);
    if (kind_enum == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    for (int i = 0; i < 5; i++) {
        state->kinds[i] = PyObject_GetAttrString(kind_enum, KIND_NAMES[i]);
        if (state->kinds[i] == NULL) { /* GCOVR_EXCL_BR_LINE */
            Py_DECREF(kind_enum);      /* GCOVR_EXCL_LINE */
            return -1;                 /* GCOVR_EXCL_LINE */
        }
    }
    state->kind_enum = kind_enum;
    return PyModule_AddObjectRef(module, "TokenType", kind_enum);
}

int token_register(PyObject *module, module_state *state) {
    if (build_kind_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                            /* GCOVR_EXCL_LINE */
    }
    state->token_type = PyType_FromModuleAndSpec(module, &token_spec, NULL);
    if (state->token_type == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;                   /* GCOVR_EXCL_LINE */
    }
    return PyModule_AddObjectRef(module, "Token", state->token_type);
}
