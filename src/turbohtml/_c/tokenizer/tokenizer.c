/* The Tokenizer type, its token iterator, and the tokenize() helper.

   Tokenizer owns a state machine. feed() appends input and returns an iterator
   that yields the tokens completed so far; close() signals end-of-input and
   returns an iterator over whatever remains. tokenize() is the one-shot form:
   feed the whole string, close, iterate.

   The iterator is where the content model is applied: after a start tag for a
   raw-text element (script, style, title, ...) it tells the machine to switch,
   which the spec assigns to tree construction. The conformance hook
   _tokenize_states drives the machine directly without this switch, so the
   state machine stays a faithful, separately testable implementation. */

#include "tokenizer/binding.h"

#include <string.h>

typedef struct {
    PyObject_HEAD th_tokenizer *sm;
    PyObject *source; /* borrowed-input owner for one-shot tokenize(), else NULL */
    int closed;       /* set by close()/__exit__; feed() after it is an error until reset() */
} TokenizerObject;

typedef struct {
    PyObject_HEAD PyObject *owner; /* the Tokenizer whose machine we read */
} IterObject;

static module_state *state_of(PyObject *self) {
    return PyType_GetModuleState(Py_TYPE(self));
}

static int name_eq(const th_buf *name, const char *literal, size_t len) {
    if ((size_t)name->len != len || name->kind != PyUnicode_1BYTE_KIND) {
        return 0;
    }
    return memcmp(name->data, literal, len) == 0;
}

/* Compare against a string literal without a runtime strlen. */
#define name_is(name, literal) name_eq((name), (literal), sizeof(literal) - 1)

/* The content model a start tag switches the tokenizer into, or -1 for none. */
static int content_model_for(const th_buf *name) {
    if (name_is(name, "script")) {
        return TH_INIT_SCRIPT_DATA;
    }
    if (name_is(name, "title") || name_is(name, "textarea")) {
        return TH_INIT_RCDATA;
    }
    if (name_is(name, "style") || name_is(name, "xmp") || name_is(name, "iframe") || name_is(name, "noembed") ||
        name_is(name, "noframes") || name_is(name, "noscript")) {
        return TH_INIT_RAWTEXT;
    }
    if (name_is(name, "plaintext")) {
        return TH_INIT_PLAINTEXT;
    }
    return -1;
}

static PyObject *iter_new(module_state *state, PyObject *owner) {
    IterObject *self = PyObject_GC_New(IterObject, (PyTypeObject *)state->iter_type);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->owner = Py_NewRef(owner);
    PyObject_GC_Track(self);
    return (PyObject *)self;
}

static int iter_traverse(PyObject *self, visitproc visit, void *arg) {
    Py_VISIT(Py_TYPE(self));               /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(((IterObject *)self)->owner); /* GCOVR_EXCL_BR_LINE: set at creation, dropped only in dealloc */
    return 0;
}

static int iter_clear(PyObject *self) {
    Py_CLEAR(((IterObject *)self)->owner); /* GCOVR_EXCL_BR_LINE: the iterator never sits in a reference cycle, so clear
                                              runs once with a live owner */
    return 0;
}

static void iter_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    iter_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyObject *iter_next(PyObject *self) {
    module_state *state = state_of(self);
    TokenizerObject *owner = (TokenizerObject *)((IterObject *)self)->owner;
    th_tokenizer *sm = owner->sm;
    PyObject *result = NULL;
    /* the shared state machine, its record buffers, and its free list are mutated here;
       lock the owning tokenizer so concurrent feed()/iteration on the free-threaded
       interpreter cannot corrupt them */
    Py_BEGIN_CRITICAL_SECTION(owner);
    th_token *record;
    switch (th_tok_next(sm, &record)) { /* GCOVR_EXCL_BR_LINE: the TH_STEP_ERROR edge needs an allocation failure, which
                                           cannot be forced from a test */
    case TH_STEP_TOKEN: {
        result = token_from_record(state, sm, owner->source, record);
        if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (record->kind == TH_START_TAG) {
            int model = content_model_for(&record->name);
            if (model >= 0) {
                th_tok_switch(sm, (enum th_initial_state)model);
            }
        }
        break;
    }
    case TH_STEP_ERROR:            /* GCOVR_EXCL_LINE: the only step error is an out-of-memory condition */
        result = PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        break;                     /* GCOVR_EXCL_LINE */
    default:                       /* NEED_MORE or DONE: nothing more from this iterator */
        break;
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

PyDoc_STRVAR(iter_doc, "Iterator over the tokens a Tokenizer has buffered. Yields Token objects.");

static PyType_Slot iter_slots[] = {
    {Py_tp_doc, (void *)iter_doc},
    {Py_tp_dealloc, iter_dealloc},
    {Py_tp_traverse, iter_traverse},
    {Py_tp_clear, iter_clear},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, iter_next},
    TH_SEALED_END,
};

static PyType_Spec iter_spec = {
    .name = "turbohtml._html._TokenIterator",
    .basicsize = sizeof(IterObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = iter_slots,
};

static PyObject *tokenizer_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"resolve_references", "capture_source", "capture_attributes", NULL};
    int resolve_references = 1;
    int capture_source = 0;
    int capture_attributes = 1;
    /* a streaming tokenizer starts empty; its options are keyword-only and
       reject any other positional or keyword argument */
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$ppp:Tokenizer", keywords, &resolve_references, &capture_source,
                                     &capture_attributes)) {
        return NULL;
    }
    TokenizerObject *self = (TokenizerObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->source = NULL;
    self->closed = 0;
    self->sm = th_tok_new();
    if (self->sm == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(self);         /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tok_set_options(self->sm, resolve_references, capture_source, capture_attributes);
    return (PyObject *)self;
}

static int tokenizer_traverse(PyObject *self, visitproc visit, void *arg) {
    TokenizerObject *tokenizer = (TokenizerObject *)self;
    Py_VISIT(Py_TYPE(self));     /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(tokenizer->source); /* GCOVR_EXCL_BR_LINE: the failing-visit arm needs a gc callback that errors */
    return 0;
}

static void tokenizer_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    th_tok_free(((TokenizerObject *)self)->sm);
    Py_XDECREF(((TokenizerObject *)self)->source);
    type->tp_free(self);
    Py_DECREF(type);
}

static int feed_string(th_tokenizer *sm, PyObject *data) {
    if (!PyUnicode_Check(data)) {
        PyErr_SetString(PyExc_TypeError, "feed() argument must be str");
        return -1;
    }
    th_tok_feed(sm, PyUnicode_KIND(data), PyUnicode_DATA(data), PyUnicode_GET_LENGTH(data));
    return 0;
}

PyDoc_STRVAR(tokenizer_feed_doc, "feed(data)\n--\n\n"
                                 "Append a chunk of markup. Text before an unfinished tag stays buffered until\n"
                                 "more is fed or close() is called.\n\n"
                                 ":param data: the next chunk of markup.\n"
                                 ":returns: an iterator over the tokens that are now complete.\n"
                                 ":raises TypeError: if data is not a str.\n"
                                 ":raises ValueError: if the tokenizer is closed; call reset() to reuse it.");

static PyObject *tokenizer_feed(PyObject *self, PyObject *data) {
    if (((TokenizerObject *)self)->closed) {
        PyErr_SetString(PyExc_ValueError, "cannot feed a closed Tokenizer; call reset() to reuse it");
        return NULL;
    }
    th_tokenizer *sm = ((TokenizerObject *)self)->sm;
    int rc;
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_feed mutates the shared state machine */
    rc = feed_string(sm, data);
    Py_END_CRITICAL_SECTION();
    if (rc < 0) {
        return NULL;
    }
    return iter_new(state_of(self), self);
}

PyDoc_STRVAR(tokenizer_close_doc, "close()\n--\n\n"
                                  "Signal end of input, flushing any buffered text and the token in progress.\n\n"
                                  ":returns: an iterator over the final tokens.");

static PyObject *tokenizer_close(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_tokenizer *sm = ((TokenizerObject *)self)->sm;
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_close mutates the shared state machine */
    th_tok_close(sm);
    ((TokenizerObject *)self)->closed = 1;
    Py_END_CRITICAL_SECTION();
    return iter_new(state_of(self), self);
}

PyDoc_STRVAR(tokenizer_reset_doc, "reset()\n--\n\n"
                                  "Discard all input and return to the initial Data state.");

static PyObject *tokenizer_reset(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_reset mutates the shared state machine */
    th_tok_reset(((TokenizerObject *)self)->sm);
    ((TokenizerObject *)self)->closed = 0;
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

PyDoc_STRVAR(tokenizer_enter_doc, "__enter__()\n--\n\nEnter a with block; returns the tokenizer itself.");

static PyObject *tokenizer_enter(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return Py_NewRef(self);
}

PyDoc_STRVAR(tokenizer_exit_doc, "__exit__(*exc)\n--\n\n"
                                 "Leave a with block, signaling end of input as close() does. Buffered\n"
                                 "tokens stay available: iterate the tokenizer to drain them.");

static PyObject *tokenizer_exit(PyObject *self, PyObject *Py_UNUSED(args)) {
    Py_BEGIN_CRITICAL_SECTION(self); /* th_tok_close mutates the shared state machine */
    th_tok_close(((TokenizerObject *)self)->sm);
    ((TokenizerObject *)self)->closed = 1;
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

/* iter(tokenizer) yields the tokens completed so far, like the feed() iterators. */
static PyObject *tokenizer_iter(PyObject *self) {
    return iter_new(state_of(self), self);
}

static PyMethodDef tokenizer_methods[] = {
    {"feed", tokenizer_feed, METH_O, tokenizer_feed_doc},
    {"close", tokenizer_close, METH_NOARGS, tokenizer_close_doc},
    {"reset", tokenizer_reset, METH_NOARGS, tokenizer_reset_doc},
    {"__enter__", tokenizer_enter, METH_NOARGS, tokenizer_enter_doc},
    {"__exit__", tokenizer_exit, METH_VARARGS, tokenizer_exit_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(tokenizer_doc,
             "Tokenizer(*, resolve_references=True, capture_source=False, capture_attributes=True)\n--\n\n"
             "Streaming HTML tokenizer. Feed markup with feed() and iterate the\n"
             "returned iterators; call close() at the end, or use the tokenizer as a\n"
             "context manager so leaving the with block signals end of input, then\n"
             "iterate the tokenizer itself for the remaining tokens. For a whole\n"
             "string at once use tokenize().\n\n"
             ":param resolve_references: fold character references into the surrounding\n"
             "    text run; when False each one is emitted as its own CHARACTER_REFERENCE\n"
             "    token (its data the resolved value, its source the verbatim reference).\n"
             "    Attribute-value references are always resolved.\n"
             ":param capture_source: record each markup token's verbatim source slice,\n"
             "    available as Token.source.\n"
             ":param capture_attributes: retain tag attributes; disable this when only tag names and\n"
             "    text are needed to skip attribute storage and copying.");

static PyType_Slot tokenizer_slots[] = {
    {Py_tp_doc, (void *)tokenizer_doc},
    {Py_tp_new, tokenizer_new},
    {Py_tp_dealloc, tokenizer_dealloc},
    {Py_tp_traverse, tokenizer_traverse},
    {Py_tp_iter, tokenizer_iter},
    {Py_tp_methods, tokenizer_methods},
    {0, NULL},
};

static PyType_Spec tokenizer_spec = {
    .name = "turbohtml._html.Tokenizer",
    .basicsize = sizeof(TokenizerObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC,
    .slots = tokenizer_slots,
};

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in core/common.h and called from core/module.c
PyObject *turbohtml_tokenize(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"", "resolve_references", "capture_source", "capture_attributes", NULL};
    PyObject *arg;
    int resolve_references = 1;
    int capture_source = 0;
    int capture_attributes = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|$ppp:tokenize", keywords, &arg, &resolve_references,
                                     &capture_source, &capture_attributes)) {
        return NULL;
    }
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "tokenize() argument must be str");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *tokenizer = PyObject_CallNoArgs(state->tokenizer_type);
    if (tokenizer == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tokenizer *sm = ((TokenizerObject *)tokenizer)->sm;
    th_tok_set_options(sm, resolve_references, capture_source, capture_attributes);
    Py_ssize_t length = PyUnicode_GET_LENGTH(arg);
    if (PyUnicode_FindChar(arg, '\r', 0, length, 1) == -1) {
        /* nothing to normalize: borrow the string's storage instead of
           copying the whole document; the tokenizer keeps the string alive */
        th_tok_borrow_input(sm, PyUnicode_KIND(arg), PyUnicode_DATA(arg), length);
        ((TokenizerObject *)tokenizer)->source = Py_NewRef(arg);
    } else {
        th_tok_feed(sm, PyUnicode_KIND(arg), PyUnicode_DATA(arg), length);
    }
    th_tok_close(sm);
    PyObject *iterator = iter_new(state, tokenizer);
    Py_DECREF(tokenizer);
    return iterator;
}

/* Format one record the way the html5lib tokenizer tests express tokens, so the
   test harness can compare directly. Used only by _tokenize_states. */
static PyObject *record_as_test_tuple(const th_tokenizer *sm, const th_token *record) {
    if (record->is_slice) {
        int kind;
        const char *data = th_tok_input_data(sm, &kind);
        PyObject *text = th_str_from_kind(kind, data + record->src_start * kind, record->src_len);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", "Character", text);
    }
    if (record->kind == TH_TEXT || record->kind == TH_COMMENT) {
        PyObject *data = th_str_from_kind(record->text.kind, record->text.data, record->text.len);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", record->kind == TH_TEXT ? "Character" : "Comment", data);
    }
    if (record->kind == TH_END_TAG) {
        PyObject *name = th_str_from_kind(record->name.kind, record->name.data, record->name.len);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(sN)", "EndTag", name);
    }
    if (record->kind == TH_START_TAG) {
        PyObject *name = th_str_from_kind(record->name.kind, record->name.data, record->name.len);
        PyObject *attrs = PyDict_New();
        if (name == NULL || attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_XDECREF(name);                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(attrs);               /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < record->attr_count; index++) {
            const th_attr *attr = &record->attrs[index];
            PyObject *key = th_str_from_kind(attr->name.kind, attr->name.data, attr->name.len);
            if (key == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(name);  /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(attrs); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            /* the tokenizer already dropped duplicate attribute names, so each key is unique */
            PyObject *value = th_str_from_kind(attr->value.kind, attr->value.data, attr->value.len);
            /* allocation failure cannot be forced from a test */
            if (value == NULL || PyDict_SetItem(attrs, key, value) < 0) { /* GCOVR_EXCL_BR_LINE */
                Py_XDECREF(value);                                        /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(key);                                           /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(name);                                          /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(attrs);                                         /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            Py_DECREF(key);
            Py_DECREF(value);
        }
        if (record->self_closing) {
            return Py_BuildValue("(sNNO)", "StartTag", name, attrs, Py_True);
        }
        return Py_BuildValue("(sNN)", "StartTag", name, attrs);
    }
    /* DOCTYPE */
    PyObject *name = record->name.len ? th_str_from_kind(record->name.kind, record->name.data, record->name.len)
                                      : Py_NewRef(Py_None);
    PyObject *public_id = record->has_public_id
                              ? th_str_from_kind(record->public_id.kind, record->public_id.data, record->public_id.len)
                              : Py_NewRef(Py_None);
    PyObject *system_id = record->has_system_id
                              ? th_str_from_kind(record->system_id.kind, record->system_id.data, record->system_id.len)
                              : Py_NewRef(Py_None);
    /* allocation failure cannot be forced from a test */
    if (name == NULL || public_id == NULL || system_id == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(name);                                         /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(public_id);                                    /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(system_id);                                    /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return Py_BuildValue("(sNNNO)", "DOCTYPE", name, public_id, system_id, record->force_quirks ? Py_False : Py_True);
}

static int initial_state_from_name(const char *name, enum th_initial_state *out) {
    if (strcmp(name, "Data state") == 0) {
        *out = TH_INIT_DATA;
    } else if (strcmp(name, "RCDATA state") == 0) {
        *out = TH_INIT_RCDATA;
    } else if (strcmp(name, "RAWTEXT state") == 0) {
        *out = TH_INIT_RAWTEXT;
    } else if (strcmp(name, "Script data state") == 0) {
        *out = TH_INIT_SCRIPT_DATA;
    } else if (strcmp(name, "PLAINTEXT state") == 0) {
        *out = TH_INIT_PLAINTEXT;
    } else if (strcmp(name, "CDATA section state") == 0) {
        *out = TH_INIT_CDATA;
    } else {
        PyErr_Format(PyExc_ValueError, "unknown initial state '%s'", name);
        return -1;
    }
    return 0;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in core/common.h and called from core/module.c
PyObject *turbohtml_tokenize_states(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    const char *state_name;
    PyObject *last_tag = Py_None;
    int storage_kind = 1;
    if (!PyArg_ParseTuple(args, "Us|Oi:_tokenize_states", &text, &state_name, &last_tag, &storage_kind)) {
        return NULL;
    }
    enum th_initial_state initial;
    if (initial_state_from_name(state_name, &initial) < 0) {
        return NULL;
    }
    if (storage_kind != 1 && storage_kind != 2 && storage_kind != 4) {
        PyErr_SetString(PyExc_ValueError, "storage_kind must be 1, 2 or 4");
        return NULL;
    }
    th_tokenizer *sm = th_tok_new();
    if (sm == NULL) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (storage_kind > (int)PyUnicode_KIND(text)) {
        th_tok_widen_input(sm, storage_kind);
    }
    if (last_tag != Py_None) {
        if (!PyUnicode_Check(last_tag)) {
            PyErr_SetString(PyExc_TypeError, "last_start_tag must be str or None");
            th_tok_free(sm);
            return NULL;
        }
        Py_ssize_t len = PyUnicode_GET_LENGTH(last_tag);
        int kind = PyUnicode_KIND(last_tag);
        const void *data = PyUnicode_DATA(last_tag);
        Py_UCS4 *buffer = PyMem_New(Py_UCS4, len ? len : 1); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            th_tok_free(sm);         /* GCOVR_EXCL_LINE: allocation-failure path */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < len; index++) {
            buffer[index] = PyUnicode_READ(kind, data, index);
        }
        th_tok_set_initial(sm, initial, buffer, len);
        PyMem_Free(buffer);
    } else {
        th_tok_set_initial(sm, initial, NULL, 0);
    }
    th_error_sink errors = {0};
    th_tok_set_error_sink(sm, &errors);
    th_tok_feed(sm, PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text));
    th_tok_close(sm);

    PyObject *out = PyList_New(0);
    if (out == NULL) {               /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tok_free(sm);             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_token *record;
    enum th_step step;
    while ((step = th_tok_next(sm, &record)) == TH_STEP_TOKEN) {
        PyObject *tuple = record_as_test_tuple(sm, record);
        /* allocation failure cannot be forced from a test */
        if (tuple == NULL || PyList_Append(out, tuple) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(tuple);                                /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(out);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            th_error_sink_free(&errors);                      /* GCOVR_EXCL_LINE: allocation-failure path */
            th_tok_free(sm);                                  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(tuple);
    }
    th_tok_free(sm);
    if (step == TH_STEP_ERROR) {     /* GCOVR_EXCL_BR_LINE: the only step error is an out-of-memory condition */
        Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_error_sink preprocessing = {0};
    th_input_stream_errors(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text), &preprocessing);
    int merge_failed = th_error_sink_merge(&errors, &preprocessing) < 0;
    th_error_sink_free(&preprocessing);
    if (merge_failed) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the conformance hook also reports the WHATWG parse errors, as (code, line, col)
       triples, so the suite's expected `errors` arrays gate them */
    PyObject *reported = PyList_New(errors.len);
    if (reported == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
        th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < errors.len; index++) {
        PyObject *entry =
            Py_BuildValue("(snn)", errors.items[index].code, errors.items[index].line, errors.items[index].col);
        if (entry == NULL) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(reported);         /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(out);              /* GCOVR_EXCL_LINE: allocation-failure path */
            th_error_sink_free(&errors); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(reported, index, entry);
    }
    th_error_sink_free(&errors);
    return Py_BuildValue("(NN)", out, reported);
}

int tokenizer_register(PyObject *module, module_state *state) {
    state->iter_type = PyType_FromModuleAndSpec(module, &iter_spec, NULL);
    if (state->iter_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->tokenizer_type = PyType_FromModuleAndSpec(module, &tokenizer_spec, NULL);
    if (state->tokenizer_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return PyModule_AddObjectRef(module, "Tokenizer", state->tokenizer_type);
}
