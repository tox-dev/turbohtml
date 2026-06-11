/* The Token type and the TokenType enum.

   A Token owns a copy of the state machine's emitted record (the machine reuses
   its records, so the copy is what keeps a token valid after iteration moves
   on). The Python-visible values — the tag name, the text, the attribute list —
   are built lazily on attribute access, so a token the caller inspects only to
   check its kind never pays for building a string or a list. */

#include "tokenizer_py.h"

#include <stdint.h>

typedef struct {
    PyObject_HEAD th_token record;
    char *arena;        /* single allocation backing every buffer in record (NULL otherwise) */
    PyObject *source;   /* string whose storage backs a still-unresolved slice (NULL otherwise) */
    PyObject *data_str; /* resolved text for a slice record (NULL until needed) */
} TokenObject;

static const char *const KIND_NAMES[5] = {"TEXT", "START_TAG", "END_TAG", "COMMENT", "DOCTYPE"};

static module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static PyObject *buf_to_str(const th_buf *buf) {
    if (buf->len == 0) {
        return PyUnicode_New(0, 0);
    }
    return PyUnicode_FromKindAndData(buf->kind, buf->data, buf->len);
}

/* Copy src into dst with every buffer packed into one arena allocation, so a
   token costs a single malloc and free instead of one per buffer. */
/* Place src into the arena at cursor, aligned to its storage width so wide
   code points are never read through a misaligned pointer. */
static char *pack_buf(th_buf *dst, const th_buf *src, char *cursor) {
    cursor +=
        (uintptr_t)cursor % (size_t)src->kind ? src->kind - (Py_ssize_t)((uintptr_t)cursor % (size_t)src->kind) : 0;
    Py_ssize_t bytes = src->len * src->kind;
    memcpy(cursor, src->data, (size_t)bytes);
    *dst = *src;
    dst->data = cursor;
    dst->cap = 0;
    return cursor + bytes;
}

static char *token_pack(th_token *dst, const th_token *src) {
    /* each buffer may need up to kind - 1 padding bytes for alignment */
    Py_ssize_t total = src->attr_count * (Py_ssize_t)sizeof(th_attr) + src->name.len * src->name.kind +
                       src->text.len * src->text.kind + src->public_id.len * src->public_id.kind +
                       src->system_id.len * src->system_id.kind + 4 * 3;
    for (Py_ssize_t i = 0; i < src->attr_count; i++) {
        total += src->attrs[i].name.len * src->attrs[i].name.kind + src->attrs[i].value.len * src->attrs[i].value.kind +
                 2 * 3;
    }
    char *arena = PyMem_Malloc((size_t)total + 1);
    if (arena == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    *dst = *src;
    dst->attr_cap = src->attr_count;
    char *cursor = arena;
    dst->attrs = (th_attr *)cursor;
    cursor += src->attr_count * (Py_ssize_t)sizeof(th_attr);
    const th_buf *from[4] = {&src->name, &src->text, &src->public_id, &src->system_id};
    th_buf *into[4] = {&dst->name, &dst->text, &dst->public_id, &dst->system_id};
    for (int i = 0; i < 4; i++) {
        cursor = pack_buf(into[i], from[i], cursor);
    }
    for (Py_ssize_t i = 0; i < src->attr_count; i++) {
        th_attr *attr = &dst->attrs[i];
        attr->has_value = src->attrs[i].has_value;
        cursor = pack_buf(&attr->name, &src->attrs[i].name, cursor);
        cursor = pack_buf(&attr->value, &src->attrs[i].value, cursor);
    }
    return arena;
}

PyObject *token_from_record(module_state *state, const th_tokenizer *sm, PyObject *source, th_token *record) {
    PyTypeObject *type = (PyTypeObject *)state->token_type;
    TokenObject *self = (TokenObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    if (record->is_slice) {
        /* the run is a span of the machine's input, so no intermediate buffer
           is involved. Strings hold no references, so owning one keeps Token
           safely outside the GC either way. */
        self->record.kind = TH_TEXT;
        self->record.is_slice = 1;
        self->record.src_start = record->src_start;
        self->record.src_len = record->src_len;
        self->record.line = record->line;
        self->record.col = record->col;
        if (source != NULL) {
            /* borrowed input: source owns the storage, resolve on first use */
            self->source = Py_NewRef(source);
        } else {
            /* the machine owns the storage and a later feed may move it */
            int kind;
            const char *data = th_tok_input_data(sm, &kind);
            self->data_str = PyUnicode_FromKindAndData(kind, data + record->src_start * kind, record->src_len);
            if (self->data_str == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(self);          /* GCOVR_EXCL_LINE */
                return NULL;              /* GCOVR_EXCL_LINE */
            }
        }
    } else if (record->kind == TH_TEXT && record->text.len >= 512) {
        self->arena = NULL;
        /* move a large text run instead of copying it; the machine's record
           simply regrows, which costs far less than duplicating the run */
        self->record.kind = TH_TEXT;
        self->record.line = record->line;
        self->record.col = record->col;
        self->record.text = record->text;
        record->text.data = NULL;
        record->text.len = 0;
        record->text.cap = 0;
    } else if ((self->arena = token_pack(&self->record, record)) == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(self);                                                    /* GCOVR_EXCL_LINE */
        return PyErr_NoMemory();                                            /* GCOVR_EXCL_LINE */
    }
    return (PyObject *)self;
}

static void token_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    TokenObject *token = (TokenObject *)self;
    Py_XDECREF(token->source);
    Py_XDECREF(token->data_str);
    if (token->arena != NULL) {
        PyMem_Free(token->arena);
    } else {
        th_token_clear(&token->record);
    }
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
    TokenObject *token = (TokenObject *)self;
    if (token->data_str != NULL) {
        return Py_NewRef(token->data_str);
    }
    if (token->source != NULL) {
        token->data_str = PyUnicode_Substring(token->source, token->record.src_start,
                                              token->record.src_start + token->record.src_len);
        if (token->data_str == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;               /* GCOVR_EXCL_LINE */
        }
        return Py_NewRef(token->data_str);
    }
    if (token->record.kind == TH_TEXT || token->record.kind == TH_COMMENT) {
        return buf_to_str(&token->record.text);
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
            if (prior->name.len == attr->name.len && prior->name.kind == attr->name.kind &&
                memcmp(prior->name.data, attr->name.data, (size_t)(attr->name.len * attr->name.kind)) == 0) {
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

static PyObject *token_get_line(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((TokenObject *)self)->record.line);
}

static PyObject *token_get_col(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((TokenObject *)self)->record.col);
}

/* Types are not repeated here: the docs build lifts each property's annotation
   from _html.pyi, the single source of truth (see docs/conf.py). */
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
    {"line", token_get_line, NULL, "1-based source line where this token began", NULL},
    {"col", token_get_col, NULL, "0-based source column where this token began", NULL},
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
                if (PyUnicode_READ(attr->name.kind, attr->name.data, j) != (Py_UCS4)(unsigned char)name[j]) {
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
    PyObject *data = token_get_data(self, NULL);
    if (data == NULL) { /* GCOVR_EXCL_BR_LINE */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    PyObject *repr = PyUnicode_FromFormat("Token(%s, data=%R)", kind, data);
    Py_DECREF(data);
    return repr;
}

static PyMethodDef token_methods[] = {
    {"attr", token_attr, METH_VARARGS, token_attr_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(token_doc, "An HTML token produced by Tokenizer or tokenize(). Immutable; the meaningful\n"
                        "attributes depend on .type.");

/* Tokens hold no object references, so they live outside the garbage
   collector: no tracking on creation, nothing to traverse on collection. */
static PyType_Slot token_slots[] = {
    {Py_tp_doc, (void *)token_doc}, {Py_tp_dealloc, token_dealloc}, {Py_tp_repr, token_repr},
    {Py_tp_getset, token_getset},   {Py_tp_methods, token_methods}, {0, NULL},
};

static PyType_Spec token_spec = {
    .name = "turbohtml._html.Token",
    .basicsize = sizeof(TokenObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
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
    PyObject *doc = PyUnicode_FromString("The kind of a Token; selects which of its attributes are meaningful.");
    if (doc == NULL || PyObject_SetAttrString(kind_enum, "__doc__", doc) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(doc);                                                        /* GCOVR_EXCL_LINE */
        Py_DECREF(kind_enum);                                                   /* GCOVR_EXCL_LINE */
        return -1;                                                              /* GCOVR_EXCL_LINE */
    }
    Py_DECREF(doc);
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
