/* The leaf nodes (Text, Comment, CData, ProcessingInstruction, Doctype, ParseError) and the constructors
   that mint them in fresh standalone trees. */

#include "dom/nodes.h"

/* Take ownership of tree and the node built within it, wrap that node in a fresh
   handle, and return the wrapper. On allocation failure frees the tree and returns
   NULL with an exception set. */
PyObject *wrap_fresh_tree_node(module_state *state, th_tree *tree, th_node *node) {
    PyObject *handle = handle_new(state, tree, Py_None, Py_None);
    if (handle == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *wrapped = node_wrap(state, handle, node);
    Py_DECREF(handle);
    return wrapped;
}

/* Wrap a data node (Text/Comment/CData/Doctype) holding a copy of data in a fresh
   single-node tree, ready to be inserted into a document. */
PyObject *data_node_in_fresh_tree(module_state *state, int node_type, PyObject *data) {
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(data);
    Py_UCS4 *points = PyUnicode_AsUCS4Copy(data);
    if (points == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_data_node(tree, node_type, points, len);
    PyMem_Free(points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, node);
}

static PyObject *construct_data_node(PyTypeObject *type, int node_type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"data", NULL};
    PyObject *data;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U", keywords, &data)) {
        return NULL;
    }
    return data_node_in_fresh_tree(PyType_GetModuleState(type), node_type, data);
}

/* Deep-copy this node and its subtree into a fresh standalone tree, the body of
   __copy__ and __deepcopy__ (an HTML node has no meaningful shallow copy). */
static PyObject *node_copy_impl(PyObject *self) {
    module_state *state = state_of(self);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *copy = th_tree_copy_node(tree, tree_of(self), ((NodeObject *)self)->node);
    if (copy == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, copy);
}

PyObject *node_copy(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return node_copy_impl(self);
}

PyObject *node_deepcopy(PyObject *self, PyObject *Py_UNUSED(memo)) {
    return node_copy_impl(self);
}

static PyObject *text_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_TEXT, args, kwds);
}

static PyObject *comment_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_COMMENT, args, kwds);
}

static PyObject *cdata_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    return construct_data_node(type, TH_NODE_CDATA, args, kwds);
}

/* Reject a processing-instruction target the serialization could not round-trip:
   empty, or carrying whitespace (which the target/data packing splits on) or ">"
   (which closes the instruction). */
static int validate_pi_target(PyObject *target) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(target);
    if (len == 0) {
        PyErr_SetString(PyExc_ValueError, "processing instruction target must not be empty");
        return -1;
    }
    int kind = PyUnicode_KIND(target);
    const void *data = PyUnicode_DATA(target);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = PyUnicode_READ(kind, data, index);
        if (character <= ' ' || character == '>') {
            PyObject *ch = PyUnicode_FromOrdinal((int)character);
            if (ch != NULL) { /* GCOVR_EXCL_BR_LINE: a forbidden character is ASCII and always builds */
                PyErr_Format(PyExc_ValueError, "processing instruction target contains an invalid character: %R", ch);
                Py_DECREF(ch);
            }
            return -1;
        }
    }
    return 0;
}

static PyObject *pi_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"target", "data", NULL};
    PyObject *target;
    PyObject *data;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "UU", keywords, &target, &data)) {
        return NULL;
    }
    if (validate_pi_target(target) < 0) {
        return NULL;
    }
    module_state *state = PyType_GetModuleState(type);
    th_tree *tree = th_tree_new();
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t target_len = PyUnicode_GET_LENGTH(target);
    Py_UCS4 *target_points = PyUnicode_AsUCS4Copy(target);
    Py_ssize_t data_len = PyUnicode_GET_LENGTH(data);
    Py_UCS4 *data_points = PyUnicode_AsUCS4Copy(data);
    if (target_points == NULL || data_points == NULL) { /* GCOVR_EXCL_BR_LINE: allocation cannot be forced to fail */
        PyMem_Free(target_points);                      /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(data_points);                        /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = th_tree_make_pi(tree, target_points, target_len, data_points, data_len);
    PyMem_Free(target_points);
    PyMem_Free(data_points);
    if (node == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return wrap_fresh_tree_node(state, tree, node);
}

static PyObject *node_get_data(PyObject *self, void *Py_UNUSED(closure)) {
    return str_from_accessor(th_node_data, tree_of(self), ((NodeObject *)self)->node);
}

/* The str a data/text setter assigns: rejects deletion and a non-str, then copies
   the code points (caller frees with PyMem_Free). *len is the length; NULL on
   error. */
Py_UCS4 *assigned_str(PyObject *value, const char *what, Py_ssize_t *len) {
    if (value == NULL) {
        PyErr_Format(PyExc_TypeError, "cannot delete %s", what);
        return NULL;
    }
    if (!PyUnicode_Check(value)) {
        PyErr_Format(PyExc_TypeError, "%s must be a str", what);
        return NULL;
    }
    *len = PyUnicode_GET_LENGTH(value);
    return PyUnicode_AsUCS4Copy(value);
}

static int node_set_data(PyObject *self, PyObject *value, void *Py_UNUSED(closure)) {
    Py_ssize_t len;
    Py_UCS4 *points = assigned_str(value, "data", &len);
    if (points == NULL) {
        return -1;
    }
    int rc;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    rc = th_node_set_data(tree_of(self), ((NodeObject *)self)->node, points, len);
    Py_END_CRITICAL_SECTION();
    PyMem_Free(points);
    return rc < 0 ? -1 : 0; /* GCOVR_EXCL_BR_LINE: th_node_set_data only fails on OOM */
}

static PyGetSetDef data_getset[] = {
    {"data", node_get_data, node_set_data, "the character data of this node", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(text_doc, "A run of character data.\n\n"
                       ":param data: the text content.");

static PyType_Slot text_slots[] = {
    {Py_tp_doc, (void *)text_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, text_new},
    {0, NULL},
};

PyType_Spec text_spec = {
    .name = "turbohtml._html.Text",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = text_slots,
};

PyDoc_STRVAR(comment_doc, "An HTML comment.\n\n"
                          ":param data: the comment text, without the <!-- --> delimiters.");

static PyType_Slot comment_slots[] = {
    {Py_tp_doc, (void *)comment_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, comment_new},
    {0, NULL},
};

PyType_Spec comment_spec = {
    .name = "turbohtml._html.Comment",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = comment_slots,
};

PyDoc_STRVAR(cdata_doc, "A CDATA section.\n\n"
                        ":param data: the section text, without the <![CDATA[ ]]> delimiters.");

static PyType_Slot cdata_slots[] = {
    {Py_tp_doc, (void *)cdata_doc},
    {Py_tp_getset, data_getset},
    {Py_tp_new, cdata_new},
    {0, NULL},
};

PyType_Spec cdata_spec = {
    .name = "turbohtml._html.CData",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = cdata_slots,
};

PyObject *pi_get_target(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text, node->attr_count);
}

PyObject *pi_get_data(PyObject *self, void *Py_UNUSED(closure)) {
    th_node *node = ((NodeObject *)self)->node;
    return ucs4_to_str(node->text + node->attr_count + 1, node->text_len - node->attr_count - 1);
}

static PyGetSetDef pi_getset[] = {
    {"target", pi_get_target, NULL, "the instruction target (the name right after <?)", NULL},
    {"data", pi_get_data, NULL, "the instruction data (everything after the target)", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(pi_doc, "A processing instruction.\n\n"
                     ":param target: the instruction target (the name right after <?).\n"
                     ":param data: the instruction data (everything after the target).");

static PyType_Slot pi_slots[] = {
    {Py_tp_doc, (void *)pi_doc},
    {Py_tp_getset, pi_getset},
    {Py_tp_new, pi_new},
    {0, NULL},
};

PyType_Spec pi_spec = {
    .name = "turbohtml._html.ProcessingInstruction",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = pi_slots,
};

PyObject *doctype_get_name(PyObject *self, void *Py_UNUSED(closure)) {
    return str_from_accessor(th_node_data, tree_of(self), ((NodeObject *)self)->node);
}

/* The doctype's public id (want_system 0) or system id (want_system 1) as a str,
   or None when that identifier was not supplied. A supplied-but-empty identifier
   is the empty string, distinct from a missing one (issue #68). */
static PyObject *doctype_id(PyObject *self, int want_system) {
    th_node *node = ((NodeObject *)self)->node;
    const Py_UCS4 *public_id;
    const Py_UCS4 *system_id;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (!th_node_doctype_ids(node, &public_id, &public_len, &system_id, &system_len)) {
        Py_RETURN_NONE; /* the doctype carries no identifiers at all */
    }
    uint8_t supplied = want_system ? TH_DOCTYPE_HAS_SYSTEM : TH_DOCTYPE_HAS_PUBLIC;
    if (!(node->tag_flags & supplied)) {
        Py_RETURN_NONE; /* only the sibling identifier was supplied; this one is missing, not empty */
    }
    return want_system ? ucs4_to_str(system_id, system_len) : ucs4_to_str(public_id, public_len);
}

PyObject *doctype_get_public_id(PyObject *self, void *Py_UNUSED(closure)) {
    return doctype_id(self, 0);
}

PyObject *doctype_get_system_id(PyObject *self, void *Py_UNUSED(closure)) {
    return doctype_id(self, 1);
}

static PyGetSetDef doctype_getset[] = {
    {"name", doctype_get_name, NULL, "the document type name", NULL},
    {"public_id", doctype_get_public_id, NULL, "the public identifier, or None when the doctype has none", NULL},
    {"system_id", doctype_get_system_id, NULL, "the system identifier, or None when the doctype has none", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(doctype_doc, "A document type declaration.");

static PyType_Slot doctype_slots[] = {
    {Py_tp_doc, (void *)doctype_doc},
    {Py_tp_getset, doctype_getset},
    TH_SEALED_END,
};

PyType_Spec doctype_spec = {
    .name = "turbohtml._html.Doctype",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = doctype_slots,
};

/* An immutable value object describing one WHATWG parse error. code points at a
   static literal owned by the C core, so the object holds no Python references
   and needs no traversal; line/col mirror Token.line (1-based) and Token.col
   (0-based). */
typedef struct {
    PyObject_HEAD const char *code;
    Py_ssize_t line;
    Py_ssize_t col;
} ParseErrorObject;

PyObject *parse_error_new(module_state *state, const th_parse_error *error) {
    PyTypeObject *type = (PyTypeObject *)state->parse_error_type;
    ParseErrorObject *self = (ParseErrorObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->code = error->code;
    self->line = error->line;
    self->col = error->col;
    return (PyObject *)self;
}

static PyObject *parse_error_get_code(PyObject *self, void *Py_UNUSED(closure)) {
    return PyUnicode_FromString(((ParseErrorObject *)self)->code);
}

static PyObject *parse_error_get_line(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((ParseErrorObject *)self)->line);
}

static PyObject *parse_error_get_col(PyObject *self, void *Py_UNUSED(closure)) {
    return PyLong_FromSsize_t(((ParseErrorObject *)self)->col);
}

static PyGetSetDef parse_error_getset[] = {
    {"code", parse_error_get_code, NULL,
     "the WHATWG parse-error code, e.g. 'unexpected-question-mark-instead-of-tag-name'", NULL},
    {"line", parse_error_get_line, NULL, "the 1-based source line where the error was detected", NULL},
    {"col", parse_error_get_col, NULL, "the 0-based source column where the error was detected", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static PyObject *parse_error_repr(PyObject *self) {
    ParseErrorObject *error = (ParseErrorObject *)self;
    PyObject *code = PyUnicode_FromString(error->code);
    if (code == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *repr = th_str_format("ParseError(code=%R, line=%zd, col=%zd)", code, error->line, error->col);
    Py_DECREF(code);
    return repr;
}

static Py_hash_t parse_error_hash(PyObject *self) {
    ParseErrorObject *error = (ParseErrorObject *)self;
    Py_uhash_t hash = 14695981039346656037ULL;
    for (const char *byte = error->code; *byte != '\0'; byte++) {
        hash = (hash ^ (unsigned char)*byte) * 1099511628211ULL;
    }
    hash = (hash ^ (Py_uhash_t)error->line) * 1099511628211ULL;
    hash = (hash ^ (Py_uhash_t)error->col) * 1099511628211ULL;
    return (Py_hash_t)(hash & 0x7fffffffffffffffULL);
}

static PyObject *parse_error_richcompare(PyObject *self, PyObject *other, int op) {
    if (!Py_IS_TYPE(other, Py_TYPE(self))) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    if (op != Py_EQ && op != Py_NE) {
        Py_RETURN_NOTIMPLEMENTED;
    }
    ParseErrorObject *left = (ParseErrorObject *)self;
    ParseErrorObject *right = (ParseErrorObject *)other;
    int equal = left->line == right->line && left->col == right->col && strcmp(left->code, right->code) == 0;
    return PyBool_FromLong(op == Py_EQ ? equal : !equal);
}

static void parse_error_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    type->tp_free(self);
    Py_DECREF(type);
}

PyDoc_STRVAR(parse_error_doc, "One WHATWG parse error collected during parse(): a code and a source position.\n\n"
                              "code is the spec error code, line is 1-based and col is 0-based (as for Token).");

static PyType_Slot parse_error_slots[] = {
    {Py_tp_doc, (void *)parse_error_doc},
    {Py_tp_dealloc, parse_error_dealloc},
    {Py_tp_repr, parse_error_repr},
    {Py_tp_hash, parse_error_hash},
    {Py_tp_richcompare, parse_error_richcompare},
    {Py_tp_getset, parse_error_getset},
    TH_SEALED_END,
};

PyType_Spec parse_error_spec = {
    .name = "turbohtml._html.ParseError",
    .basicsize = sizeof(ParseErrorObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | TH_SEALED,
    .slots = parse_error_slots,
};
