/* The DOM-less SAX event walk behind turbohtml.saxparse.

   _sax_events(str) parses the markup with the ordinary WHATWG tree builder, then
   returns a _SaxEvents iterator that streams the constructed tree in document
   order -- one event per __next__ -- and frees the tree when the iterator is
   dropped. The caller never receives a node: the iterator's own state is a single
   cursor plus a phase bit (O(1)), so the events reflect the fully spec-correct
   tree (implied html/head/body, foster parenting, the adoption agency) while the
   caller retains nothing but its own handler state.

   The one Python boundary is the per-event object: each event is a plain tuple
   (kind, ...fields) the thin saxparse shim maps to a typed record or dispatches to
   a handler method. Everything else -- tokenization, tree construction, the walk,
   the string extraction -- stays in C. */

#include "tokenizer/binding.h"

#include "dom/tree.h"

/* Event kinds, shared with turbohtml.saxparse's _EVENT_TYPES table. */
#define SAX_START 1
#define SAX_END 2
#define SAX_CHARACTERS 3
#define SAX_COMMENT 4
#define SAX_DOCTYPE 5
#define SAX_PI 6

typedef struct {
    PyObject_HEAD th_tree *tree;
    PyObject *source; /* the parsed str, kept alive because text spans point into it */
    th_node *cursor;  /* the node the next step enters or leaves */
    int leaving;      /* 0: about to enter cursor; 1: about to leave it */
    int done;         /* the walk has left the document root */
} SaxEventsObject;

/* A node's own character data (text/comment/doctype-name) as a str, realizing a
   zero-copy text span on the way. NULL with an exception set on allocation
   failure. */
static PyObject *data_str(th_tree *tree, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *data = th_node_data(tree, node, &len);
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
    PyMem_Free(data);
    return result;
}

/* An element's attributes as a tuple of (name, value) pairs; value is None for a
   valueless attribute. NULL with an exception set on allocation failure. */
static PyObject *attrs_tuple(th_tree *tree, th_node *node) {
    th_node_attr *attrs;
    Py_ssize_t count = th_node_attributes(node, &attrs);
    PyObject *result = PyTuple_New(count);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attrs[index].name_atom, &name_len);
        PyObject *name_obj = PyUnicode_FromStringAndSize(name, name_len);
        PyObject *value_obj =
            attrs[index].value == NULL
                ? Py_NewRef(Py_None)
                : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attrs[index].value, attrs[index].value_len);
        /* allocation failure cannot be forced from a test */
        if (name_obj == NULL || value_obj == NULL) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(name_obj);                    /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(value_obj);                   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, PyTuple_Pack(2, name_obj, value_obj));
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
        if (PyTuple_GET_ITEM(result, index) == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            Py_DECREF(result);                         /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return result;
}

/* A doctype's (5, name, public_id, system_id) event; either identifier is None
   when the source did not supply it. NULL with an exception set on allocation
   failure. */
static PyObject *doctype_event(th_tree *tree, th_node *node) {
    PyObject *name = data_str(tree, node);
    if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *public_id = Py_NewRef(Py_None);
    PyObject *system_id = Py_NewRef(Py_None);
    const Py_UCS4 *public_ptr;
    const Py_UCS4 *system_ptr;
    Py_ssize_t public_len;
    Py_ssize_t system_len;
    if (th_node_doctype_ids(node, &public_ptr, &public_len, &system_ptr, &system_len)) {
        if (node->tag_flags & TH_DOCTYPE_HAS_PUBLIC) {
            Py_SETREF(public_id, PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, public_ptr, public_len));
        }
        if (node->tag_flags & TH_DOCTYPE_HAS_SYSTEM) {
            Py_SETREF(system_id, PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, system_ptr, system_len));
        }
    }
    /* allocation failure cannot be forced from a test */
    if (public_id == NULL || system_id == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(name);                          /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(public_id);                    /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(system_id);                    /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return Py_BuildValue("(iNNN)", SAX_DOCTYPE, name, public_id, system_id);
}

/* The event for entering a node, or NULL: with an exception set on allocation
   failure, and without one for a node that emits nothing on entry (the document
   root and a <template>'s content fragment, whose children carry the events). */
static PyObject *enter_event(th_tree *tree, th_node *node) {
    switch (node->type) {
    case TH_NODE_ELEMENT: {
        PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *attrs = attrs_tuple(tree, node);
        if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(tag);  /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(iNN)", SAX_START, tag, attrs);
    }
    case TH_NODE_TEXT: {
        PyObject *data = data_str(tree, node);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        return Py_BuildValue("(iN)", SAX_CHARACTERS, data);
    }
    case TH_NODE_COMMENT: {
        PyObject *data = data_str(tree, node);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        int kind = (node->tag_flags & TH_COMMENT_IS_PI) ? SAX_PI : SAX_COMMENT;
        return Py_BuildValue("(iN)", kind, data);
    }
    case TH_NODE_DOCTYPE:
        return doctype_event(tree, node);
    default: /* TH_NODE_DOCUMENT and TH_NODE_CONTENT emit nothing themselves */
        return NULL;
    }
}

/* The event for leaving a node: an end-element for an element, else NULL (only
   elements have a close). NULL-with-exception on allocation failure. */
static PyObject *leave_event(th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return NULL;
    }
    PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
    if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return Py_BuildValue("(iN)", SAX_END, tag);
}

static PyObject *sax_iter_next(PyObject *self) {
    SaxEventsObject *iterator = (SaxEventsObject *)self;
    PyObject *result = NULL;
    /* the cursor walk mutates the iterator and realizes text spans in the tree
       arena; lock self so two threads iterating the same _SaxEvents on the
       free-threaded interpreter cannot corrupt either */
    Py_BEGIN_CRITICAL_SECTION(self);
    while (!iterator->done) {
        th_node *node = iterator->cursor;
        PyObject *event;
        if (!iterator->leaving) {
            event = enter_event(iterator->tree, node);
            if (node->first_child != NULL) {
                iterator->cursor = node->first_child;
            } else {
                iterator->leaving = 1;
            }
        } else {
            event = leave_event(node);
            if (node->next_sibling != NULL) {
                iterator->cursor = node->next_sibling;
                iterator->leaving = 0;
            } else if (node->parent != NULL) {
                iterator->cursor = node->parent;
            } else {
                iterator->done = 1;
            }
        }
        if (event != NULL) {
            result = event;
            break;
        }
        if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: the only failure is an allocation one, unforceable from a test */
            break;              /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_END_CRITICAL_SECTION();
    return result;
}

static int sax_traverse(PyObject *self, visitproc visit, void *arg) {
    SaxEventsObject *iterator = (SaxEventsObject *)self;
    Py_VISIT(Py_TYPE(self));    /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(iterator->source); /* GCOVR_EXCL_BR_LINE: set at creation, dropped only in dealloc */
    return 0;
}

static int sax_clear(PyObject *self) {
    SaxEventsObject *iterator = (SaxEventsObject *)self;
    Py_CLEAR(iterator->source); /* GCOVR_EXCL_BR_LINE: the source is non-NULL until this single clear */
    return 0;
}

static void sax_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    PyObject_GC_UnTrack(self);
    th_tree_free(((SaxEventsObject *)self)->tree);
    (void)sax_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

PyDoc_STRVAR(sax_iter_doc, "Streams the SAX events of a parsed document one at a time, retaining no tree.");

static PyType_Slot sax_iter_slots[] = {
    {Py_tp_doc, (void *)sax_iter_doc},
    {Py_tp_dealloc, sax_dealloc},
    {Py_tp_traverse, sax_traverse},
    {Py_tp_clear, sax_clear},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, sax_iter_next},
    TH_SEALED_END,
};

static PyType_Spec sax_iter_spec = {
    .name = "turbohtml._html._SaxEvents",
    .basicsize = sizeof(SaxEventsObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = sax_iter_slots,
};

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in tokenizer/binding.h, called from core/module.c
PyObject *turbohtml_sax_events(PyObject *module, PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "sax_parse() argument must be str");
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    th_tree *tree = th_tree_parse(PyUnicode_KIND(arg), PyUnicode_DATA(arg), PyUnicode_GET_LENGTH(arg), 0, 0, 0, 1);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    SaxEventsObject *iterator = PyObject_GC_New(SaxEventsObject, (PyTypeObject *)state->sax_events_type);
    if (iterator == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    iterator->tree = tree;
    iterator->source = Py_NewRef(arg);
    iterator->cursor = th_tree_document(tree);
    iterator->leaving = 0;
    iterator->done = 0;
    PyObject_GC_Track(iterator);
    return (PyObject *)iterator;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in tokenizer/binding.h, called from core/module.c
int sax_register(PyObject *module, module_state *state) {
    state->sax_events_type = PyType_FromModuleAndSpec(module, &sax_iter_spec, NULL);
    if (state->sax_events_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}
