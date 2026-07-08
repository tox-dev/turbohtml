/* Pluggable tree building behind turbohtml.treebuild (issue #545).

   parse_into(html, builder) parses the markup with the ordinary WHATWG tree builder,
   then drives the caller's builder object -- create_document / create_doctype /
   create_element / create_text / create_comment / create_pi / append -- once over the
   constructed tree in document order and returns whatever the builder made its root.
   No navigable Node is ever materialized and the caller never walks a second time: the
   one pass hands the spec-correct tree (implied html/head/body, foster parenting, the
   adoption agency) straight to the target structure, html5ever's TreeSink and parse5's
   TreeAdapter in turbohtml shape.

   The tree construction, the walk, and the string extraction stay in C; the builder is
   the single Python boundary, its methods called with the same arguments parse5 hands a
   TreeAdapter (a namespace URI on every element, attributes as (name, value) pairs). The
   walk is iterative with a heap handle stack, so a deeply nested document cannot exhaust
   the C stack the way a recursive descent would. */

#include "tokenizer/binding.h"

#include "core/vec.h"
#include "dom/tree.h"

/* The namespace URI create_element receives, indexed by enum th_ns
   (TH_NS_HTML / TH_NS_SVG / TH_NS_MATHML). */
static const char *const NS_URIS[] = {
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1998/Math/MathML",
};

/* The builder's bound methods, resolved once per parse so the walk pays no per-node
   attribute lookup and a builder missing a method fails before any parsing starts. */
enum {
    M_CREATE_DOCUMENT,
    M_CREATE_DOCTYPE,
    M_CREATE_ELEMENT,
    M_CREATE_TEXT,
    M_CREATE_COMMENT,
    M_CREATE_PI,
    M_APPEND,
    M_COUNT,
};

typedef struct {
    PyObject *slots[M_COUNT];
} builder;

static const char *const METHOD_NAMES[M_COUNT] = {
    "create_document", "create_doctype", "create_element", "create_text", "create_comment", "create_pi", "append",
};

static void builder_release(builder *methods) {
    for (int index = 0; index < M_COUNT; index++) {
        Py_XDECREF(methods->slots[index]);
    }
}

static int builder_bind(PyObject *sink, builder *methods) {
    for (int index = 0; index < M_COUNT; index++) {
        methods->slots[index] = NULL;
    }
    for (int index = 0; index < M_COUNT; index++) {
        methods->slots[index] = PyObject_GetAttrString(sink, METHOD_NAMES[index]);
        if (methods->slots[index] == NULL) {
            builder_release(methods);
            return -1;
        }
    }
    return 0;
}

/* A node's own character data (text/comment/doctype-name) as a str, realizing a
   zero-copy span on the way. NULL with an exception set on allocation failure. */
static PyObject *node_str(th_tree *tree, th_node *node) {
    Py_ssize_t len;
    Py_UCS4 *data = th_node_data(tree, node, &len);
    if (data == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, data, len);
    PyMem_Free(data);
    return result;
}

/* An element's attributes as a tuple of (name, value) pairs, value None for a
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
        PyObject *pair = PyTuple_Pack(2, name_obj, value_obj);
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
        if (pair == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, pair);
    }
    return result;
}

/* Build the builder handle for one node by dispatching on its type: an element carries
   its tag, namespace URI, and attributes; a comment splits into a processing
   instruction when the parser folded a bogus `<?...>` into it; a doctype carries its
   name and the public/system identifiers the source supplied. Returns a new reference,
   or NULL with an exception set (the builder method raised, or an allocation failed). */
static PyObject *make_handle(th_tree *tree, th_node *node, builder *methods) {
    switch (node->type) {
    case TH_NODE_ELEMENT: {
        PyObject *tag = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
        if (tag == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *namespace = PyUnicode_FromString(NS_URIS[node->ns]);
        if (namespace == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(tag);      /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *attrs = attrs_tuple(tree, node);
        if (attrs == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(tag);       /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(namespace); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *handle = PyObject_CallFunctionObjArgs(methods->slots[M_CREATE_ELEMENT], tag, namespace, attrs, NULL);
        Py_DECREF(tag);
        Py_DECREF(namespace);
        Py_DECREF(attrs);
        return handle;
    }
    case TH_NODE_TEXT: {
        PyObject *data = node_str(tree, node);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *handle = PyObject_CallOneArg(methods->slots[M_CREATE_TEXT], data);
        Py_DECREF(data);
        return handle;
    }
    case TH_NODE_COMMENT: {
        PyObject *data = node_str(tree, node);
        if (data == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *method =
            (node->tag_flags & TH_COMMENT_IS_PI) ? methods->slots[M_CREATE_PI] : methods->slots[M_CREATE_COMMENT];
        PyObject *handle = PyObject_CallOneArg(method, data);
        Py_DECREF(data);
        return handle;
    }
    default: { /* TH_NODE_DOCTYPE */
        PyObject *name = node_str(tree, node);
        if (name == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *public_id = Py_NewRef(Py_None);
        PyObject *system_id = Py_NewRef(Py_None);
        const Py_UCS4 *public_ptr;
        const Py_UCS4 *system_ptr;
        Py_ssize_t public_len = 0;
        Py_ssize_t system_len = 0;
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
        PyObject *handle =
            PyObject_CallFunctionObjArgs(methods->slots[M_CREATE_DOCTYPE], name, public_id, system_id, NULL);
        Py_DECREF(name);
        Py_DECREF(public_id);
        Py_DECREF(system_id);
        return handle;
    }
    }
}

/* Owned handles of the ancestor spine: entry d is the parent every node at depth d+1
   is appended to, entry 0 the builder's document root. Grown on descent, dropped on
   ascent, so its height never exceeds the parser's own tree-depth cap. */
typedef struct {
    PyObject **items;
    size_t len;
    size_t cap;
} spine;

static int spine_push(spine *stack, PyObject *handle) {
    if (stack->len == stack->cap) {
        size_t cap = 0;
        size_t bytes = 0;
        int grew = th_grow_cap(stack->len + 1, stack->cap, 16, sizeof(PyObject *), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: a spine that overflows size_t cannot be built in memory */
            return -1; /* GCOVR_EXCL_LINE: overflow-guard path */
        }
        PyObject **grown = PyMem_Realloc(stack->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        stack->items = grown;
        stack->cap = cap;
    }
    stack->items[stack->len++] = handle;
    return 0;
}

/* Walk the constructed tree in document order, driving the builder, and return its
   document root (a new reference) or NULL with an exception set. */
static PyObject *walk_into(th_tree *tree, builder *methods) {
    th_node *document = th_tree_document(tree);
    PyObject *root = PyObject_CallNoArgs(methods->slots[M_CREATE_DOCUMENT]);
    if (root == NULL) {
        return NULL;
    }
    spine stack = {NULL, 0, 0};
    if (spine_push(&stack, root) < 0) { /* GCOVR_EXCL_BR_LINE: the first push cannot fail its allocation */
        Py_DECREF(root);                /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(stack.items);        /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node *node = document->first_child;
    while (node != NULL) {
        PyObject *parent = stack.items[stack.len - 1];
        PyObject *handle;
        if (node->type == TH_NODE_CONTENT) { /* a <template>'s content: its children hang off the template */
            handle = Py_NewRef(parent);
        } else {
            handle = make_handle(tree, node, methods);
            if (handle == NULL) {
                goto fail;
            }
            PyObject *appended = PyObject_CallFunctionObjArgs(methods->slots[M_APPEND], parent, handle, NULL);
            if (appended == NULL) {
                Py_DECREF(handle);
                goto fail;
            }
            Py_DECREF(appended);
        }
        if (node->first_child != NULL) {
            if (spine_push(&stack, handle) < 0) { /* GCOVR_EXCL_BR_LINE: a descent push cannot force its allocation */
                Py_DECREF(handle);                /* GCOVR_EXCL_LINE: allocation-failure path */
                goto fail;                        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node = node->first_child;
            continue;
        }
        Py_DECREF(handle);
        while (node->next_sibling == NULL) {
            node = node->parent;
            if (node == document) {
                node = NULL;
                break;
            }
            Py_DECREF(stack.items[--stack.len]);
        }
        if (node != NULL) {
            node = node->next_sibling;
        }
    }
    PyMem_Free(stack.items);
    return root;
fail:
    for (size_t index = 0; index < stack.len; index++) {
        Py_DECREF(stack.items[index]);
    }
    PyMem_Free(stack.items);
    return NULL;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in tokenizer/binding.h, called from core/module.c
PyObject *turbohtml_parse_into(PyObject *module, PyObject *args) {
    (void)module;
    PyObject *source;
    PyObject *sink;
    if (!PyArg_ParseTuple(args, "UO", &source, &sink)) {
        return NULL;
    }
    builder methods;
    if (builder_bind(sink, &methods) < 0) {
        return NULL;
    }
    th_tree *tree =
        th_tree_parse(PyUnicode_KIND(source), PyUnicode_DATA(source), PyUnicode_GET_LENGTH(source), 0, 0, 0, 1);
    if (tree == NULL) {            /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        builder_release(&methods); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = walk_into(tree, &methods);
    th_tree_free(tree);
    builder_release(&methods);
    return result;
}
