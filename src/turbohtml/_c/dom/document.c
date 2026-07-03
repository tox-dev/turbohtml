/* The Document root, the tree-owning handle, the push IncrementalParser, the parse()/parse_fragment()
   entry points, and the type registration that wires every node type onto the module. */

#include "dom/nodes.h"

#include "encoding/encoding.h"
#include "encoding/detect.h"

static PyObject *document_get_root(PyObject *self, void *Py_UNUSED(closure)) {
    NodeObject *node = (NodeObject *)self;
    /* a parsed document always has an html element child, so the loop never exhausts */
    th_node *child = node->node->first_child;
    /* allocation failure cannot be forced from a test */
    for (; child != NULL; child = child->next_sibling) { /* GCOVR_EXCL_BR_LINE */
        if (child->type == TH_NODE_ELEMENT) {
            return node_wrap(state_of(self), node->handle, child);
        }
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: a parsed document always has an <html> element child */
}

static PyObject *document_get_encoding(PyObject *self, void *Py_UNUSED(closure)) {
    return Py_NewRef(((HandleObject *)((NodeObject *)self)->handle)->encoding);
}

/* RFC 3986 resolution is intricate, so the URL helpers delegate the join to the standard library, which they call at
   most once per document. PyImport caches urllib.parse in sys.modules after the first call. */
static PyObject *url_join(PyObject *base, PyObject *target) {
    PyObject *parse = PyImport_ImportModule("urllib.parse");
    if (parse == NULL) { /* GCOVR_EXCL_BR_LINE: urllib.parse is always importable */
        return NULL;     /* GCOVR_EXCL_LINE: import-failure path */
    }
    PyObject *joined = PyObject_CallMethod(parse, "urljoin", "OO", base, target);
    Py_DECREF(parse);
    return joined;
}

/* The value of `node`'s `name` attribute as a new str reference (the empty string when valueless), or NULL when the
   attribute is absent (or, on the excluded allocation-failure path, with an error set). */
static PyObject *element_attr_str(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = find_attr_index(tree, node, name, name_len);
    if (index < 0) {
        return NULL;
    }
    return attr_value_obj(&node->attrs[index]);
}

/* The ASCII whitespace HTML trims around these values, minus CR (the input preprocessor turns it into LF). An array
   keeps the branch count stable when clang inlines this into several call sites. */
static int meta_is_space(Py_UCS4 c) {
    static const Py_UCS4 whitespace[] = {' ', '\t', '\n', '\f'};
    for (size_t index = 0; index < sizeof(whitespace) / sizeof(whitespace[0]); index++) {
        if (c == whitespace[index]) {
            return 1;
        }
    }
    return 0;
}

static int meta_is_digit(Py_UCS4 c) {
    return c >= '0' && c <= '9';
}

/* One code-point read for every meta-refresh scan loop. Routing all reads through a single site keeps Python 3.10's
   dead PyUnicode_DATA/READ macro branches from multiplying across the loops; the coverage build runs at -O0 so this
   stays a real call, while release inlines it. */
static Py_UCS4 meta_char(int kind, const void *data, Py_ssize_t index) {
    return PyUnicode_READ(kind, data, index);
}

/* Parse a meta-refresh `content` ("<delay>" or "<delay>;url=<url>") into ``(delay, url)``, resolving the url against
   `fallback` and using `fallback` itself when the directive has none. Returns the tuple, None when there is no leading
   delay, or NULL on error. */
static PyObject *parse_meta_refresh(PyObject *content, PyObject *fallback) {
    int kind = PyUnicode_KIND(content);
    const void *data = PyUnicode_DATA(content);
    Py_ssize_t len = PyUnicode_GET_LENGTH(content);
    Py_ssize_t pos = 0;
    while (pos < len && meta_is_space(meta_char(kind, data, pos))) {
        pos++;
    }
    Py_ssize_t number_start = pos;
    while (pos < len && meta_is_digit(meta_char(kind, data, pos))) {
        pos++;
    }
    if (pos < len && meta_char(kind, data, pos) == '.') {
        pos++;
        while (pos < len && meta_is_digit(meta_char(kind, data, pos))) {
            pos++;
        }
    }
    if (pos == number_start) {
        Py_RETURN_NONE; /* no leading number, so not a refresh directive */
    }
    PyObject *number = PyUnicode_Substring(content, number_start, pos);
    if (number == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *delay = PyFloat_FromString(number);
    Py_DECREF(number);
    if (delay == NULL) { /* GCOVR_EXCL_BR_LINE: the substring is all digits and at most one dot */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *url = NULL;
    while (pos < len && meta_char(kind, data, pos) != ';' && meta_char(kind, data, pos) != ',') {
        pos++;
    }
    if (pos < len) {
        pos++; /* step past the ';' or ',' */
        while (pos < len && meta_is_space(meta_char(kind, data, pos))) {
            pos++;
        }
        /* an optional "url" "=" prefix, e.g. "url=next" or "URL = next" */
        if (pos + 2 < len && (meta_char(kind, data, pos) | 0x20) == 'u' &&
            (meta_char(kind, data, pos + 1) | 0x20) == 'r' && (meta_char(kind, data, pos + 2) | 0x20) == 'l') {
            Py_ssize_t after = pos + 3;
            while (after < len && meta_is_space(meta_char(kind, data, after))) {
                after++;
            }
            if (after < len && meta_char(kind, data, after) == '=') {
                after++;
                while (after < len && meta_is_space(meta_char(kind, data, after))) {
                    after++;
                }
                pos = after;
            }
        }
        Py_ssize_t url_end = len;
        while (url_end > pos && meta_is_space(meta_char(kind, data, url_end - 1))) {
            url_end--;
        }
        if (url_end > pos) {
            Py_UCS4 quote = meta_char(kind, data, pos);
            if ((quote == '"' || quote == '\'') && url_end - 1 > pos && meta_char(kind, data, url_end - 1) == quote) {
                pos++;
                url_end--;
            }
        }
        if (url_end > pos) {
            PyObject *raw = PyUnicode_Substring(content, pos, url_end);
            if (raw == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(delay); /* GCOVR_EXCL_LINE */
                return NULL;      /* GCOVR_EXCL_LINE */
            }
            url = url_join(fallback, raw);
            Py_DECREF(raw);
            if (url == NULL) {    /* GCOVR_EXCL_BR_LINE: url_join only fails on the import path */
                Py_DECREF(delay); /* GCOVR_EXCL_LINE */
                return NULL;      /* GCOVR_EXCL_LINE */
            }
        }
    }
    if (url == NULL) {
        url = Py_NewRef(fallback);
    }
    PyObject *result = PyTuple_Pack(2, delay, url);
    Py_DECREF(delay);
    Py_DECREF(url);
    return result; /* NULL on the excluded allocation-failure path */
}

/* Hold a borrowed fallback as an owned str, defaulting to "" when None was passed in. */
static PyObject *refresh_fallback(PyObject *fallback) {
    if (fallback == NULL) {
        return PyUnicode_FromStringAndSize("", 0);
    }
    return Py_NewRef(fallback);
}

PyDoc_STRVAR(base_url_doc, "base_url(fallback='')\n--\n\n"
                           "Resolve this document's base URL from its first <base href>.\n\n"
                           ":param fallback: the URL the <base href> is resolved against, and the result\n"
                           "    itself when the document has no <base href>.\n"
                           ":returns: the document's base URL.");

static PyObject *document_base_url(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"fallback", NULL};
    PyObject *fallback = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|U:base_url", keywords, &fallback)) {
        return NULL;
    }
    fallback = refresh_fallback(fallback);
    if (fallback == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *href = NULL;
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type == TH_NODE_ELEMENT && node->atom == TH_TAG_BASE &&
            (href = element_attr_str(tree, node, "href", 4)) != NULL) {
            break;
        }
    }
    Py_END_CRITICAL_SECTION();
    if (href == NULL) {
        return fallback; /* no <base href>: the fallback is the base */
    }
    PyObject *stripped = PyObject_CallMethod(href, "strip", NULL);
    Py_DECREF(href);
    if (stripped == NULL) {  /* GCOVR_EXCL_BR_LINE: str.strip cannot fail here */
        Py_DECREF(fallback); /* GCOVR_EXCL_LINE */
        return NULL;         /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_GET_LENGTH(stripped) == 0 ? Py_NewRef(fallback) : url_join(fallback, stripped);
    Py_DECREF(stripped);
    Py_DECREF(fallback);
    return result;
}

PyDoc_STRVAR(meta_refresh_doc, "meta_refresh(fallback='')\n--\n\n"
                               "Read the document's <meta http-equiv=refresh> redirect. A refresh tag inside\n"
                               "<noscript> is ignored.\n\n"
                               ":param fallback: the URL the directive's target is resolved against, and the\n"
                               "    target itself when the directive omits one.\n"
                               ":returns: a (delay_seconds, url) pair, or None when the document has no\n"
                               "    refresh directive.");

/* A <meta> nested in <noscript> is ignored, as w3lib does; <script> needs no entry because it is a raw-text element, so
   the parser never nests a <meta> element inside one. */
static int under_noscript(th_node *node) {
    for (th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
        if (ancestor->atom == TH_TAG_NOSCRIPT) {
            return 1;
        }
    }
    return 0;
}

/* Whether `equiv` is "refresh" once surrounding whitespace and case are ignored, matched in C to avoid Python calls
   inside the tree walk. */
static int equiv_is_refresh(PyObject *equiv) {
    int kind = PyUnicode_KIND(equiv);
    const void *data = PyUnicode_DATA(equiv);
    Py_ssize_t start = 0;
    Py_ssize_t end = PyUnicode_GET_LENGTH(equiv);
    while (start < end && meta_is_space(meta_char(kind, data, start))) {
        start++;
    }
    while (end > start && meta_is_space(meta_char(kind, data, end - 1))) {
        end--;
    }
    static const char target[] = "refresh";
    if (end - start != (Py_ssize_t)(sizeof(target) - 1)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < end - start; index++) {
        if ((meta_char(kind, data, start + index) | 0x20) != (Py_UCS4)target[index]) {
            return 0;
        }
    }
    return 1;
}

static PyObject *document_meta_refresh(PyObject *self, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"fallback", NULL};
    PyObject *fallback = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|U:meta_refresh", keywords, &fallback)) {
        return NULL;
    }
    fallback = refresh_fallback(fallback);
    if (fallback == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *content = NULL;
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META || under_noscript(node)) {
            continue;
        }
        PyObject *equiv = element_attr_str(tree, node, "http-equiv", 10);
        if (equiv == NULL) {
            continue;
        }
        int refresh = equiv_is_refresh(equiv);
        Py_DECREF(equiv);
        if (refresh) {
            content = element_attr_str(tree, node, "content", 7);
            break;
        }
    }
    Py_END_CRITICAL_SECTION();
    PyObject *result;
    if (content == NULL) {
        result = Py_NewRef(Py_None);
    } else {
        result = parse_meta_refresh(content, fallback);
        Py_DECREF(content);
    }
    Py_DECREF(fallback);
    return result;
}

PyDoc_STRVAR(structured_data_doc, "structured_data()\n--\n\n"
                                  "Extract every supported structured-data format from the document, a successor to\n"
                                  "extruct. Returns a StructuredData record with .json_ld (list of parsed JSON-LD\n"
                                  "values), .microdata (list of MicrodataItem), .opengraph (dict of og:/twitter: keys\n"
                                  "to their content), and .microformats/.rdfa (empty lists, a later phase).");

PyDoc_STRVAR(json_ld_doc, "json_ld()\n--\n\n"
                          "Parse every <script type=\"application/ld+json\"> block in the document with the\n"
                          "standard library json module, returning the list of decoded values in document order.\n"
                          "A block that is not valid JSON is skipped.");

PyDoc_STRVAR(opengraph_doc, "opengraph()\n--\n\n"
                            "Return a dict mapping each <meta property=\"og:...\"> or <meta name=\"twitter:...\">\n"
                            "key to its content value. When a key repeats, the last occurrence wins.");

PyDoc_STRVAR(microdata_doc,
             "microdata()\n--\n\n"
             "Extract HTML Microdata as a list of MicrodataItem, one per top-level itemscope. Each item\n"
             "has .properties (a dict mapping each itemprop name to its list of values), plus .type and\n"
             ".id carrying the itemtype/itemid attribute or None. A property value is a nested\n"
             "MicrodataItem for an itemscope, otherwise the element's microdata value.");

static PyMethodDef document_methods[] = {
    {"base_url", (PyCFunction)(void (*)(void))document_base_url, METH_VARARGS | METH_KEYWORDS, base_url_doc},
    {"meta_refresh", (PyCFunction)(void (*)(void))document_meta_refresh, METH_VARARGS | METH_KEYWORDS,
     meta_refresh_doc},
    {"structured_data", turbohtml_document_structured_data, METH_NOARGS, structured_data_doc},
    {"json_ld", turbohtml_document_json_ld, METH_NOARGS, json_ld_doc},
    {"opengraph", turbohtml_document_opengraph, METH_NOARGS, opengraph_doc},
    {"microdata", turbohtml_document_microdata, METH_NOARGS, microdata_doc},
    {NULL, NULL, 0, NULL},
};

/* The parse errors are immutable once the parse returns, so this reads them
   lock-free (like the other accessors) and materializes a fresh list each call. */
static PyObject *document_get_errors(PyObject *self, void *Py_UNUSED(closure)) {
    th_tree *tree = ((HandleObject *)((NodeObject *)self)->handle)->tree;
    Py_ssize_t count;
    const th_parse_error *errors = th_tree_errors(tree, &count);
    PyObject *list = PyList_New(count);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    module_state *state = state_of(self);
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *error = parse_error_new(state, &errors[index]);
        if (error == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(list); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(list, index, error);
    }
    return list;
}

static PyGetSetDef document_getset[] = {
    {"root", document_get_root, NULL, "the root <html> element, or None", NULL},
    {"encoding", document_get_encoding, NULL, "the resolved encoding name for bytes input, or None for str", NULL},
    {"errors", document_get_errors, NULL, "the WHATWG parse errors detected, as a list of ParseError in document order",
     NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

PyDoc_STRVAR(document_doc, "A parsed document: the root of the tree returned by parse().");

static PyType_Slot document_slots[] = {
    {Py_tp_doc, (void *)document_doc},
    {Py_tp_getset, document_getset},
    {Py_tp_methods, document_methods},
    {0, NULL},
};

static PyType_Spec document_spec = {
    .name = "turbohtml._html.Document",
    .basicsize = sizeof(NodeObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = document_slots,
};

static void handle_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    HandleObject *handle = (HandleObject *)self;
    handle_clear_caches(handle);
    PyMem_Free(handle->index_offsets);
    PyMem_Free(handle->index_nodes);
    path_id_map_free(handle->path_ids);
    th_tree_free(handle->tree);
    Py_XDECREF(handle->source);
    Py_XDECREF(handle->encoding);
    type->tp_free(self);
    Py_DECREF(type);
}

static PyType_Slot handle_slots[] = {
    {Py_tp_dealloc, handle_dealloc},
    {0, NULL},
};

static PyType_Spec handle_spec = {
    .name = "turbohtml._html._TreeHandle",
    .basicsize = sizeof(HandleObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = handle_slots,
};

PyObject *handle_new(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding) {
    PyTypeObject *type = (PyTypeObject *)state->handle_type;
    HandleObject *self = (HandleObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->tree = tree;
    self->source = Py_NewRef(source);
    self->encoding = Py_NewRef(encoding);
    return (PyObject *)self;
}

/* Wrap a freshly built tree (which borrows source's storage) and return its
   document/context node. Frees the tree on wrapping failure. */
static PyObject *tree_to_node(module_state *state, th_tree *tree, PyObject *source, PyObject *encoding) {
    PyObject *handle = handle_new(state, tree, source, encoding);
    if (handle == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = node_wrap(state, handle, th_tree_document(tree));
    Py_DECREF(handle);
    return node;
}

/* In strict mode, raise HTMLParseError carrying the first parse error and free
   the tree, returning -1; otherwise return 0 and leave the tree to be wrapped.
   The exception's str is a readable summary and its .error is the ParseError. */
static int strict_raise(module_state *state, th_tree *tree, int strict) {
    if (!strict) {
        return 0;
    }
    Py_ssize_t count;
    const th_parse_error *errors = th_tree_errors(tree, &count);
    if (count == 0) {
        return 0;
    }
    PyObject *error = parse_error_new(state, &errors[0]);
    PyObject *message =
        PyUnicode_FromFormat("%s at line %zd, column %zd", errors[0].code, errors[0].line, errors[0].col);
    /* allocation failure cannot be forced from a test */
    if (error == NULL || message == NULL) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(error);                  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(message);                /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *exc = PyObject_CallOneArg(state->parse_error_exc, message);
    Py_DECREF(message);
    /* allocation failure cannot be forced from a test */
    if (exc == NULL || PyObject_SetAttrString(exc, "error", error) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(exc);                                                  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(error);                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        th_tree_free(tree);                                               /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_DECREF(error);
    PyErr_SetObject(state->parse_error_exc, exc);
    Py_DECREF(exc);
    th_tree_free(tree);
    return -1;
}

/* Decode windows-1252 per the WHATWG index. CPython's cp1252 leaves 0x81, 0x8D,
   0x8F, 0x90, and 0x9D undefined, so "replace" maps them to U+FFFD, but the WHATWG
   windows-1252 index defines them as the matching C1 controls (codepoint equals the
   byte). Those five bytes are cp1252's only source of U+FFFD, so every U+FFFD in the
   decoded string is one of them; a single-byte codec emits one char per byte, so the
   output index is the byte index. Rebuild only when one is present, since removing
   the U+FFFD may lower the string's kind. */
static PyObject *decode_windows_1252(const unsigned char *bytes, Py_ssize_t len) {
    PyObject *decoded = PyUnicode_Decode((const char *)bytes, len, "cp1252", "replace");
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: cp1252 with the replace handler never fails */
        return NULL;       /* GCOVR_EXCL_LINE: decode failure */
    }
    Py_ssize_t count = PyUnicode_GET_LENGTH(decoded);
    int kind = PyUnicode_KIND(decoded);
    const void *data = PyUnicode_DATA(decoded);
    Py_UCS4 maxchar = 0;
    int restored = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch == 0xFFFD) {
            ch = bytes[index];
            restored = 1;
        }
        if (ch > maxchar) {
            maxchar = ch;
        }
    }
    if (!restored) {
        return decoded;
    }
    PyObject *fixed = PyUnicode_New(count, maxchar);
    if (fixed == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(decoded); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int out_kind = PyUnicode_KIND(fixed);
    void *out = PyUnicode_DATA(fixed);
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        PyUnicode_WRITE(out_kind, out, index, ch == 0xFFFD ? (Py_UCS4)bytes[index] : ch);
    }
    Py_DECREF(decoded);
    return fixed;
}

/* Parse bytes: sniff the encoding (BOM, then the encoding argument, then a <meta>
   prescan, then windows-1252), decode with that codec replacing malformed bytes,
   and parse the resulting str. The decoded str is retained as the tree's source. */
static PyObject *parse_bytes(module_state *state, PyObject *markup, const char *enc_arg, Py_ssize_t enc_len, int strict,
                             int detect, int positions) {
    Py_buffer view;
    if (PyObject_GetBuffer(markup, &view, PyBUF_SIMPLE) < 0) { /* GCOVR_EXCL_BR_LINE: bytes expose a simple buffer */
        return NULL;                                           /* GCOVR_EXCL_LINE: buffer-acquisition failure */
    }
    const unsigned char *bytes = view.buf;
    Py_ssize_t len = view.len;
    const th_encoding_entry *entry = NULL;
    Py_ssize_t skip = th_encoding_bom(bytes, len, &entry);
    if (entry == NULL && enc_arg != NULL) {
        entry = th_encoding_lookup(enc_arg, enc_len);
    }
    if (entry == NULL) {
        entry = th_encoding_prescan(bytes, len);
    }
    if (entry == NULL && detect) {
        /* opt-in content-based detection, strictly after the spec sniffing steps */
        th_detect_scores scores;
        entry = th_encoding_detect(bytes, len, &scores);
    }
    if (entry == NULL) {
        entry = th_encoding_lookup("windows-1252", 12);
    }
    PyObject *decoded;
    if (strcmp(entry->codec, "x-user-defined") == 0) {
        /* x-user-defined has no CPython codec: ASCII bytes stay, 0x80-0xFF map to the
           private-use block U+F780-U+F7FF, per the WHATWG Encoding Standard */
        Py_ssize_t count = len - skip;
        decoded = PyUnicode_New(count, 0xF7FF);
        if (decoded != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            void *out = PyUnicode_DATA(decoded);
            for (Py_ssize_t index = 0; index < count; index++) {
                unsigned char byte = bytes[skip + index];
                PyUnicode_WRITE(PyUnicode_2BYTE_KIND, out, index, byte < 0x80 ? byte : (Py_UCS4)(0xF700 + byte));
            }
        }
    } else if (strcmp(entry->codec, "replacement") == 0) {
        /* the WHATWG replacement encoding refuses the stateful ISO-2022/HZ byte streams,
           which can smuggle markup past a sanitizer: a non-empty input decodes to a
           single U+FFFD and an empty input to nothing */
        decoded = len - skip > 0 ? PyUnicode_FromOrdinal(0xFFFD) : PyUnicode_New(0, 0);
    } else if (strcmp(entry->codec, "cp1252") == 0) {
        decoded = decode_windows_1252(bytes + skip, len - skip);
    } else {
        decoded = PyUnicode_Decode((const char *)bytes + skip, len - skip, entry->codec, "replace");
    }
    PyBuffer_Release(&view);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: the codec is from the table and the replace handler never fails */
        return NULL;       /* GCOVR_EXCL_LINE: decode failure */
    }
    th_tree *tree =
        th_tree_parse(PyUnicode_KIND(decoded), PyUnicode_DATA(decoded), PyUnicode_GET_LENGTH(decoded), positions);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
        Py_DECREF(decoded);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (strict_raise(state, tree, strict) < 0) {
        Py_DECREF(decoded);
        return NULL;
    }
    PyObject *canonical = PyUnicode_FromString(entry->canonical);
    if (canonical == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_tree_free(tree);  /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(decoded);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = tree_to_node(state, tree, decoded, canonical);
    Py_DECREF(canonical);
    Py_DECREF(decoded);
    return node;
}

/* The standalone encoding-detection binding behind turbohtml.detect: run the same
   sniffing pipeline as parse_bytes (BOM, <meta> prescan, then the content detector)
   without decoding or parsing. Returns (winner, certain, ranked): the winner's
   canonical name or None for pure ASCII, whether it came from a declaration or a
   structural proof rather than frequency scoring, and every surviving scored
   candidate as (canonical name, raw score) pairs for the ranked surface. */
PyObject *turbohtml_detect_encoding(PyObject *module, PyObject *arg) {
    (void)module;
    Py_buffer view;
    if (PyObject_GetBuffer(arg, &view, PyBUF_SIMPLE) < 0) {
        return NULL;
    }
    const unsigned char *bytes = view.buf;
    Py_ssize_t len = view.len;
    const th_encoding_entry *entry = NULL;
    th_encoding_bom(bytes, len, &entry);
    if (entry == NULL) {
        entry = th_encoding_prescan(bytes, len);
    }
    int certain = entry != NULL;
    th_detect_scores scores = {.count = 0, .structural = 0};
    if (entry == NULL) {
        entry = th_encoding_detect(bytes, len, &scores);
        certain = scores.structural;
    }
    PyBuffer_Release(&view);
    PyObject *ranked = PyList_New(scores.count);
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < scores.count; index++) {
        const char *label = scores.items[index].label;
        const th_encoding_entry *item = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
        PyObject *pair = Py_BuildValue("(sl)", item->canonical, scores.items[index].score);
        if (pair == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(ranked); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyList_SET_ITEM(ranked, index, pair);
    }
    return Py_BuildValue("(zON)", entry == NULL ? NULL : entry->canonical, certain ? Py_True : Py_False, ranked);
}

PyObject *turbohtml_parse(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"markup", "encoding", "strict", "detect_encoding", "positions", NULL};
    PyObject *markup;
    const char *enc_arg = NULL;
    Py_ssize_t enc_len = 0;
    int strict = 0;
    int detect = 0;
    int positions = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|$z#ppp:parse", keywords, &markup, &enc_arg, &enc_len, &strict,
                                     &detect, &positions)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    if (PyUnicode_Check(markup)) {
        th_tree *tree =
            th_tree_parse(PyUnicode_KIND(markup), PyUnicode_DATA(markup), PyUnicode_GET_LENGTH(markup), positions);
        if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: only an allocation failure returns NULL */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (strict_raise(state, tree, strict) < 0) {
            return NULL;
        }
        return tree_to_node(state, tree, markup, Py_None);
    }
    if (!PyObject_CheckBuffer(markup)) {
        PyErr_SetString(PyExc_TypeError, "parse() argument must be str or a bytes-like object");
        return NULL;
    }
    return parse_bytes(state, markup, enc_arg, enc_len, strict, detect, positions);
}

PyObject *turbohtml_tree_parse_fragment(PyObject *module, PyObject *args, PyObject *kwargs) {
    static char *keywords[] = {"html", "context", "positions", NULL};
    PyObject *text;
    PyObject *context_obj = NULL;
    int positions = 1;
    /* require str: the "s#" format also accepts bytes, which would decode as latin-1 garbage */
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "U|U$p:parse_fragment", keywords, &text, &context_obj, &positions)) {
        return NULL;
    }
    const char *context = "div";
    Py_ssize_t context_len = 3;
    if (context_obj != NULL) {
        context = PyUnicode_AsUTF8AndSize(context_obj, &context_len);
        if (context == NULL) { /* a lone-surrogate context has no UTF-8 form */
            return NULL;
        }
    }
    th_tree *tree = th_tree_parse_fragment(PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text),
                                           context, context_len, positions);
    if (tree == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return tree_to_node(PyModule_GetState(module), tree, text, Py_None);
}

/* The public IncrementalParser: a push parser that owns a C th_stream plus, once
   the first bytes chunk arrives, a stateful incremental decoder for the chosen
   encoding so a multi-byte character split across two chunks decodes correctly.
   stream is NULL once close() has handed the tree off (or __exit__ discarded it),
   marking the parser spent. */
typedef struct {
    PyObject_HEAD th_stream *stream;
    PyObject *encoding; /* the encoding str used to decode bytes chunks */
    PyObject *decoder;  /* the incremental decoder, created on the first bytes feed; else NULL */
} StreamObject;

static PyObject *stream_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *keywords[] = {"encoding", "positions", NULL};
    PyObject *encoding = NULL;
    int positions = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|$Up:IncrementalParser", keywords, &encoding, &positions)) {
        return NULL;
    }
    StreamObject *self = (StreamObject *)type->tp_alloc(type, 0);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->decoder = NULL;
    self->encoding = encoding != NULL ? Py_NewRef(encoding) : PyUnicode_FromString("utf-8");
    if (self->encoding == NULL) { /* GCOVR_EXCL_BR_LINE: the literal "utf-8" always builds */
        Py_DECREF(self);          /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->stream = th_stream_new(positions);
    if (self->stream == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(self);         /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return (PyObject *)self;
}

static void stream_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    StreamObject *parser = (StreamObject *)self;
    if (parser->stream != NULL) {
        th_stream_free(parser->stream);
    }
    Py_XDECREF(parser->encoding);
    Py_XDECREF(parser->decoder);
    type->tp_free(self);
    Py_DECREF(type);
}

/* Decode a bytes chunk through the parser's incremental decoder, creating it on
   first use (an unknown encoding raises LookupError here). final flushes any
   bytes the decoder held back at a chunk boundary. */
static PyObject *stream_decode(StreamObject *parser, PyObject *data, int final) {
    if (parser->decoder == NULL) {
        const char *name = PyUnicode_AsUTF8(parser->encoding);
        if (name == NULL) { /* a lone-surrogate encoding name has no UTF-8 form */
            return NULL;
        }
        parser->decoder = PyCodec_IncrementalDecoder(name, "replace");
        if (parser->decoder == NULL) {
            return NULL;
        }
    }
    return PyObject_CallMethod(parser->decoder, "decode", "Oi", data, final);
}

/* Feed already-decoded code points to the C stream; -1 with an exception set on
   allocation failure. */
static int stream_feed_str(StreamObject *parser, PyObject *text) {
    int rc = th_stream_feed(parser->stream, PyUnicode_KIND(text), PyUnicode_DATA(text), PyUnicode_GET_LENGTH(text));
    if (rc < 0) {         /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

static PyObject *stream_feed_locked(StreamObject *parser, PyObject *data) {
    if (parser->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "cannot feed a closed IncrementalParser");
        return NULL;
    }
    if (PyUnicode_Check(data)) {
        if (stream_feed_str(parser, data) < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
            return NULL;                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_RETURN_NONE;
    }
    if (!PyObject_CheckBuffer(data)) {
        PyErr_SetString(PyExc_TypeError, "feed() argument must be str or a bytes-like object");
        return NULL;
    }
    PyObject *decoded = stream_decode(parser, data, 0);
    if (decoded == NULL) {
        return NULL;
    }
    int failed = stream_feed_str(parser, decoded);
    Py_DECREF(decoded);
    if (failed < 0) { /* GCOVR_EXCL_BR_LINE: only an allocation failure returns -1 */
        return NULL;  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

PyDoc_STRVAR(stream_feed_doc, "feed(data)\n--\n\n"
                              "Push a chunk of markup, so the source need never be held whole in memory.\n"
                              "Raises ValueError once the parser is closed.\n\n"
                              ":param data: str (fed as code points) or a bytes-like object decoded with\n"
                              "    the parser's encoding, an incomplete trailing multi-byte sequence held\n"
                              "    back until the next chunk.");

static PyObject *stream_feed(PyObject *self, PyObject *data) {
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is mutated as the chunk is consumed */
    result = stream_feed_locked((StreamObject *)self, data);
    Py_END_CRITICAL_SECTION();
    return result;
}

static PyObject *stream_close_locked(StreamObject *parser, module_state *state) {
    if (parser->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "IncrementalParser is already closed");
        return NULL;
    }
    if (parser->decoder != NULL) {
        /* flush any bytes the decoder held back at the last chunk boundary */
        PyObject *tail = PyObject_CallMethod(parser->decoder, "decode", "yi", "", 1);
        if (tail == NULL) { /* GCOVR_EXCL_BR_LINE: a final empty decode cannot fail once the codec exists */
            return NULL;    /* GCOVR_EXCL_LINE: decode-failure path */
        }
        int failed = stream_feed_str(parser, tail);
        Py_DECREF(tail);
        if (failed < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    th_tree *tree = th_stream_finish(parser->stream);
    if (tree == NULL) {                 /* GCOVR_EXCL_BR_LINE: finish only fails on allocation */
        th_stream_free(parser->stream); /* GCOVR_EXCL_LINE: allocation-failure path */
        parser->stream = NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_stream_free(parser->stream); /* frees the tokenizer; the tree is the caller's now */
    parser->stream = NULL;
    return tree_to_node(state, tree, Py_None, parser->decoder != NULL ? parser->encoding : Py_None);
}

PyDoc_STRVAR(stream_close_doc, "close()\n--\n\n"
                               "Signal end of input, flushing the decoder and tokenizer and applying the\n"
                               "missing html/head/body rules. Raises ValueError if the parser is already\n"
                               "closed.\n\n"
                               ":returns: the finished Document.");

static PyObject *stream_close(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    module_state *state = PyType_GetModuleState(Py_TYPE(self));
    PyObject *result;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is finished and handed off */
    result = stream_close_locked((StreamObject *)self, state);
    Py_END_CRITICAL_SECTION();
    return result;
}

PyDoc_STRVAR(stream_enter_doc, "__enter__()\n--\n\nEnter a with block; returns the parser itself.");

static PyObject *stream_enter(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    return Py_NewRef(self);
}

PyDoc_STRVAR(stream_exit_doc, "__exit__(*exc)\n--\n\n"
                              "Leave a with block. If close() was not called the in-progress parse is\n"
                              "discarded and its memory released, so abandoning a stream early is cheap.");

static PyObject *stream_exit(PyObject *self, PyObject *Py_UNUSED(args)) {
    StreamObject *parser = (StreamObject *)self;
    Py_BEGIN_CRITICAL_SECTION(self); /* the C stream is released if still open */
    if (parser->stream != NULL) {
        th_stream_free(parser->stream);
        parser->stream = NULL;
    }
    Py_END_CRITICAL_SECTION();
    Py_RETURN_NONE;
}

static PyMethodDef stream_methods[] = {
    {"feed", stream_feed, METH_O, stream_feed_doc},
    {"close", stream_close, METH_NOARGS, stream_close_doc},
    {"__enter__", stream_enter, METH_NOARGS, stream_enter_doc},
    {"__exit__", stream_exit, METH_VARARGS, stream_exit_doc},
    {NULL, NULL, 0, NULL},
};

PyDoc_STRVAR(stream_doc, "IncrementalParser(*, encoding='utf-8', positions=True)\n--\n\n"
                         "A push parser that builds a Document from chunks fed with feed(), so a\n"
                         "document arriving over a stream never has to be held whole in memory. Feed\n"
                         "str or bytes chunks in any size, then call close() for the finished\n"
                         "Document. For a whole string or bytes at once use parse().\n\n"
                         ":param encoding: codec used to decode any bytes chunks.\n"
                         ":param positions: whether to record each element's source line and column;\n"
                         "    pass False to skip it when memory or speed matters more.");

static PyType_Slot stream_slots[] = {
    {Py_tp_doc, (void *)stream_doc},
    {Py_tp_new, stream_new},
    {Py_tp_dealloc, stream_dealloc},
    {Py_tp_methods, stream_methods},
    {0, NULL},
};

static PyType_Spec stream_spec = {
    .name = "turbohtml._html.IncrementalParser",
    .basicsize = sizeof(StreamObject),
    .flags = Py_TPFLAGS_DEFAULT,
    .slots = stream_slots,
};

/* This node's children as a fresh list of wrappers, so pickling an element
   recurses into each child. */
static PyObject *node_children_list(PyObject *self) {
    NodeObject *node = (NodeObject *)self;
    module_state *state = state_of(self);
    PyObject *list = PyList_New(0);
    if (list == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (th_node *child = node->node->first_child; child != NULL; child = child->next_sibling) {
        PyObject *wrapped = node_wrap(state, node->handle, child);
        if (wrapped == NULL || PyList_Append(list, wrapped) < 0) { /* GCOVR_EXCL_BR_LINE: alloc cannot be forced */
            Py_XDECREF(wrapped);                                   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(list);                                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(wrapped);
    }
    return list;
}

/* The pickle payload for this node: the leaf data, the element tag and attribute
   dict, the doctype identifiers, or the document's own markup. */
static PyObject *node_pickle_data(PyObject *self, th_node *node) {
    switch (node->type) { /* GCOVR_EXCL_BR_LINE: th_node_type is exhaustive; the implicit default is unreachable */
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        return str_from_accessor(th_node_data, tree_of(self), node);
    case TH_NODE_PI:
        return Py_BuildValue("(NN)", pi_get_target(self, NULL), pi_get_data(self, NULL));
    case TH_NODE_DOCTYPE:
        return Py_BuildValue("(NNN)", doctype_get_name(self, NULL), doctype_get_public_id(self, NULL),
                             doctype_get_system_id(self, NULL));
    case TH_NODE_ELEMENT: {
        PyObject *view = element_get_attrs(self, NULL);
        if (view == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *attrs = PyObject_CallFunctionObjArgs((PyObject *)&PyDict_Type, view, NULL);
        Py_DECREF(view);
        if (attrs == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (node->ns == TH_NS_HTML) {
            return Py_BuildValue("(NN)", element_get_tag(self, NULL), attrs);
        }
        /* a foreign (SVG/MathML) element carries its namespace so the round-trip keeps it */
        return Py_BuildValue("(NNi)", element_get_tag(self, NULL), attrs, (int)node->ns);
    }
    case TH_NODE_DOCUMENT:
    case TH_NODE_CONTENT:
        return str_from_accessor(th_node_html, tree_of(self), node);
    }
    Py_RETURN_NONE; /* GCOVR_EXCL_LINE: unreachable, the switch is exhaustive */
}

PyObject *node_reduce(PyObject *self, PyObject *Py_UNUSED(ignored)) {
    th_node *node = ((NodeObject *)self)->node;
    PyObject *reconstruct = PyObject_GetAttrString(PyType_GetModule(Py_TYPE(self)), "_reconstruct");
    if (reconstruct == NULL) { /* GCOVR_EXCL_BR_LINE: the module always carries _reconstruct */
        return NULL;           /* GCOVR_EXCL_LINE: unreachable */
    }
    PyObject *data = node_pickle_data(self, node);
    PyObject *children = node->type == TH_NODE_ELEMENT ? node_children_list(self) : PyList_New(0);
    if (data == NULL || children == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_XDECREF(data);                   /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_XDECREF(children);               /* GCOVR_EXCL_LINE: allocation-failure path */
        Py_DECREF(reconstruct);             /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = Py_BuildValue("(O(iNN))", reconstruct, (int)node->type, data, children);
    Py_DECREF(reconstruct);
    return result;
}

/* Rebuild a doctype from its (name, public_id, system_id) triple, repacking the
   identifiers into the node's stored "name \"public\" \"system\"" form. Either
   identifier may be None (not supplied); a missing one is written as an empty
   string in the text but recorded as absent in tag_flags, so the round-trip
   keeps missing distinct from present-but-empty (issue #68). */
static PyObject *reconstruct_doctype(module_state *state, PyObject *data) {
    PyObject *name;
    PyObject *public_id;
    PyObject *system_id;
    if (!PyArg_ParseTuple(data, "OOO", &name, &public_id, &system_id)) { /* GCOVR_EXCL_BR_LINE: we build this tuple */
        return NULL;                                                     /* GCOVR_EXCL_LINE: unreachable */
    }
    int has_public = public_id != Py_None;
    int has_system = system_id != Py_None;
    PyObject *packed;
    if (!has_public && !has_system) {
        packed = Py_NewRef(name);
    } else {
        PyObject *empty = PyUnicode_New(0, 0);
        if (empty == NULL) { /* GCOVR_EXCL_BR_LINE: the empty string is interned and cannot fail */
            return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        packed = PyUnicode_FromFormat("%U \"%U\" \"%U\"", name, has_public ? public_id : empty,
                                      has_system ? system_id : empty);
        Py_DECREF(empty);
    }
    if (packed == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *node = data_node_in_fresh_tree(state, TH_NODE_DOCTYPE, packed);
    Py_DECREF(packed);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ((NodeObject *)node)->node->tag_flags =
        (uint8_t)((has_public ? TH_DOCTYPE_HAS_PUBLIC : 0) | (has_system ? TH_DOCTYPE_HAS_SYSTEM : 0));
    return node;
}

PyObject *turbohtml_reconstruct(PyObject *module, PyObject *args) {
    int kind;
    PyObject *data;
    PyObject *children;
    if (!PyArg_ParseTuple(args, "iOO", &kind, &data, &children)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PyObject *node;
    switch (kind) {
    case TH_NODE_TEXT:
    case TH_NODE_COMMENT:
    case TH_NODE_CDATA:
        node = data_node_in_fresh_tree(state, kind, data);
        break;
    case TH_NODE_PI:
        node = PyObject_CallObject(state->pi_type, data);
        break;
    case TH_NODE_ELEMENT: {
        /* the parser keeps characters Element() rejects (e.g. "a<b"), so reconstruct
           through the trusted builder rather than the validating public constructor */
        PyObject *tag;
        PyObject *element_attrs;
        int ns = TH_NS_HTML; /* an HTML element omits the namespace; a foreign one appends it */
        /* GCOVR_EXCL_START: node_reduce builds this (tag, attrs[, ns]) tuple, so the parse never fails */
        if (!PyArg_ParseTuple(data, "UO|i", &tag, &element_attrs, &ns)) {
            return NULL;
        }
        /* GCOVR_EXCL_STOP */
        if (ns < TH_NS_HTML || ns > TH_NS_MATHML) { /* guard the namespaces[] index against a crafted payload */
            PyErr_SetString(PyExc_ValueError, "namespace out of range");
            return NULL;
        }
        node = make_element((PyTypeObject *)state->element_type, tag, element_attrs);
        if (node != NULL) {
            ((NodeObject *)node)->node->ns = (uint8_t)ns;
        }
        break;
    }
    case TH_NODE_DOCTYPE:
        node = reconstruct_doctype(state, data);
        break;
    default: { /* TH_NODE_DOCUMENT / TH_NODE_CONTENT: data is the serialized markup */
        PyObject *call_args = PyTuple_Pack(1, data);
        if (call_args == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return NULL;         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        node = turbohtml_parse(module, call_args, NULL);
        Py_DECREF(call_args);
        break;
    }
    }
    if (node == NULL) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < PyList_GET_SIZE(children); index++) {
        PyObject *appended = PyObject_CallMethod(node, "append", "O", PyList_GET_ITEM(children, index));
        if (appended == NULL) { /* GCOVR_EXCL_BR_LINE: a reconstructed child always appends */
            Py_DECREF(node);    /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_DECREF(appended);
    }
    return node;
}

/* Build one public enum named qualname with count members, register it on the
   module, cache each member into cached_out, and store the enum object in
   *enum_out. base_is_int_enum picks IntEnum over Enum; string_values, when not
   NULL, gives each member a str value, otherwise members take their index. doc,
   when not NULL, becomes the enum class docstring so the reference is not blank. */
static int build_enum(PyObject *module, const char *qualname, int base_is_int_enum, const char *const *names, int count,
                      const char *const *string_values, const char *doc, PyObject **cached_out, PyObject **enum_out) {
    PyObject *members = PyDict_New();
    if (members == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (int index = 0; index < count; index++) {
        PyObject *value = string_values != NULL ? PyUnicode_FromString(string_values[index]) : PyLong_FromLong(index);
        /* allocation failure cannot be forced from a test */
        if (value == NULL || PyDict_SetItemString(members, names[index], value) < 0) { /* GCOVR_EXCL_BR_LINE */
            Py_XDECREF(value);  /* GCOVR_EXCL_LINE: alloc-failure path */
            Py_DECREF(members); /* GCOVR_EXCL_LINE: alloc-failure path */
            return -1;          /* GCOVR_EXCL_LINE: alloc-failure path */
        }
        Py_DECREF(value);
    }
    PyObject *enum_module = PyImport_ImportModule("enum");
    if (enum_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);    /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *enum_type = PyObject_GetAttrString(enum_module, base_is_int_enum ? "IntEnum" : "Enum");
    Py_DECREF(enum_module);
    if (enum_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(members);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *args = Py_BuildValue("(sO)", qualname, members);
    Py_DECREF(members);
    PyObject *kwargs = Py_BuildValue("{s:s,s:s}", "module", "turbohtml", "qualname", qualname);
    PyObject *built = NULL;
    if (args != NULL && kwargs != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        built = PyObject_Call(enum_type, args, kwargs);
    }
    Py_DECREF(enum_type);
    Py_XDECREF(args);
    Py_XDECREF(kwargs);
    if (built == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *docstring = PyUnicode_FromString(doc);
    /* allocation failure cannot be forced from a test */
    if (docstring == NULL || PyObject_SetAttrString(built, "__doc__", docstring) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(docstring); /* GCOVR_EXCL_LINE: alloc-failure path */
        Py_DECREF(built);      /* GCOVR_EXCL_LINE: alloc-failure path */
        return -1;             /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    Py_DECREF(docstring);
    for (int index = 0; index < count; index++) {
        cached_out[index] = PyObject_GetAttrString(built, names[index]);
        if (cached_out[index] == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(built);            /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    *enum_out = built;
    return PyModule_AddObjectRef(module, qualname, built);
}

static int build_namespace_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_NAMESPACE_COUNT] = {"HTML", "SVG", "MATHML"};
    static const char *const values[TH_NAMESPACE_COUNT] = {"html", "svg", "math"};
    static const char doc[] = "The XML namespace an Element belongs to, as reported by Element.namespace.\n\n"
                              "HTML is the ordinary HTML namespace; SVG is inline <svg> content; MATHML is\n"
                              "inline <math> content. Each member's value is the namespace short name "
                              "(\"html\", \"svg\", \"math\").";
    return build_enum(module, "Namespace", 0, names, TH_NAMESPACE_COUNT, values, doc, state->namespaces,
                      &state->namespace_enum);
}

static int build_axis_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_AXIS_COUNT] = {"DESCENDANTS",       "CHILDREN",  "ANCESTORS", "NEXT_SIBLINGS",
                                                     "PREVIOUS_SIBLINGS", "FOLLOWING", "PRECEDING"};
    static const char doc[] = "The direction find()/find_all() walk from a node, passed as their axis argument.\n\n"
                              "DESCENDANTS visits every node in the subtree in document order (the default);\n"
                              "CHILDREN only the direct children; ANCESTORS the parent chain up to the document;\n"
                              "NEXT_SIBLINGS the following siblings and PREVIOUS_SIBLINGS the preceding ones;\n"
                              "FOLLOWING every node after this one in document order (skipping its descendants)\n"
                              "and PRECEDING every node before it (skipping its ancestors).";
    return build_enum(module, "Axis", 1, names, TH_AXIS_COUNT, NULL, doc, state->axes, &state->axis_enum);
}

static int build_formatter_enum(PyObject *module, module_state *state) {
    static const char *const names[TH_FORMATTER_COUNT] = {"WHATWG", "MINIMAL", "NAMED_ENTITIES"};
    static const char *const values[TH_FORMATTER_COUNT] = {"whatwg", "minimal", "named"};
    static const char doc[] = "The character-escaping policy serialize()/encode() apply, passed as formatter.\n\n"
                              "WHATWG escapes exactly what the HTML serialization algorithm requires (the\n"
                              "default); MINIMAL escapes only the characters that would otherwise change the\n"
                              "markup; NAMED_ENTITIES prefers named character references where one exists.";
    return build_enum(module, "Formatter", 0, names, TH_FORMATTER_COUNT, values, doc, state->formatters,
                      &state->formatter_enum);
}

static int cache_pattern_type(module_state *state) {
    PyObject *re_module = PyImport_ImportModule("re");
    if (re_module == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->pattern_type = PyObject_GetAttrString(re_module, "Pattern");
    if (state->pattern_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(re_module);          /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->re_compile = PyObject_GetAttrString(re_module, "compile");
    Py_DECREF(re_module);
    if (state->re_compile == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return 0;
}

/* Create a heap type subclassing Node, register it on the module, and stamp its
   single-field __match_args__ for structural pattern use. */
static PyObject *register_subtype(PyObject *module, PyType_Spec *spec, PyObject *base, const char *name,
                                  const char *match_arg1, const char *match_arg2) {
    PyObject *bases = PyTuple_Pack(1, base);
    if (bases == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *type = PyType_FromModuleAndSpec(module, spec, bases);
    Py_DECREF(bases);
    if (type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *tuple =
        match_arg2 != NULL ? Py_BuildValue("(ss)", match_arg1, match_arg2) : Py_BuildValue("(s)", match_arg1);
    /* allocation failure cannot be forced from a test */
    if (tuple == NULL || PyObject_SetAttrString(type, "__match_args__", tuple) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_XDECREF(tuple); /* GCOVR_EXCL_LINE: alloc-failure path */
        Py_DECREF(type);   /* GCOVR_EXCL_LINE: alloc-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    Py_DECREF(tuple);
    /* allocation failure cannot be forced from a test */
    if (PyModule_AddObjectRef(module, name, type) < 0) { /* GCOVR_EXCL_BR_LINE */
        Py_DECREF(type);                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return type;
}

int tree_register(PyObject *module, module_state *state) {
    /* allocation failure cannot be forced from a test */
    if (build_namespace_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_axis_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (build_formatter_enum(module, state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (cache_pattern_type(state) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return -1;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->minify_type = PyType_FromModuleAndSpec(module, &minify_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->minify_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Minify", state->minify_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->indent_type = PyType_FromModuleAndSpec(module, &indent_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->indent_type == NULL ||                                      /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "Indent", state->indent_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->xpath_type = PyType_FromModuleAndSpec(module, &xpath_compiled_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->xpath_type == NULL ||                                     /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "XPath", state->xpath_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->walker_type = PyType_FromModuleAndSpec(module, &walker_spec, NULL);
    state->string_walker_type = PyType_FromModuleAndSpec(module, &string_walker_spec, NULL);
    state->handle_type = PyType_FromModuleAndSpec(module, &handle_spec, NULL);
    state->attrs_type = PyType_FromModuleAndSpec(module, &attrs_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->walker_type == NULL || state->string_walker_type == NULL || /* GCOVR_EXCL_BR_LINE */
        state->handle_type == NULL || state->attrs_type == NULL) {         /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->node_type = PyType_FromModuleAndSpec(module, &node_spec, NULL);
    if (state->node_type == NULL || PyModule_AddObjectRef(module, "Node", state->node_type) < 0) { /* GCOVR_EXCL_BR_LINE
                                                                                                    */
        return -1; /* GCOVR_EXCL_LINE: alloc-failure path */
    }
    state->element_type = register_subtype(module, &element_spec, state->node_type, "Element", "tag", NULL);
    state->text_type = register_subtype(module, &text_spec, state->node_type, "Text", "data", NULL);
    state->comment_type = register_subtype(module, &comment_spec, state->node_type, "Comment", "data", NULL);
    state->doctype_type = register_subtype(module, &doctype_spec, state->node_type, "Doctype", "name", NULL);
    state->pi_type = register_subtype(module, &pi_spec, state->node_type, "ProcessingInstruction", "target", "data");
    state->cdata_type = register_subtype(module, &cdata_spec, state->node_type, "CData", "data", NULL);
    state->document_type = register_subtype(module, &document_spec, state->node_type, "Document", "root", NULL);
    /* allocation failure cannot be forced from a test */
    if (state->element_type == NULL || state->text_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->comment_type == NULL || state->doctype_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->pi_type == NULL || state->cdata_type == NULL || /* GCOVR_EXCL_BR_LINE */
        /* allocation failure cannot be forced from a test */
        state->document_type == NULL) { /* GCOVR_EXCL_BR_LINE */
        return -1;                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->parser_type = PyType_FromModuleAndSpec(module, &stream_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parser_type == NULL ||                                                 /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "IncrementalParser", state->parser_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    state->parse_error_type = PyType_FromModuleAndSpec(module, &parse_error_spec, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parse_error_type == NULL ||                                          /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "ParseError", state->parse_error_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                  /* GCOVR_EXCL_LINE: alloc-fail */
    }
    state->parse_error_exc = PyErr_NewExceptionWithDoc(
        "turbohtml.HTMLParseError", "Raised by parse(strict=True) on the first parse error; .error is the ParseError.",
        NULL, NULL);
    /* allocation failure cannot be forced from a test */
    if (state->parse_error_exc == NULL ||                                              /* GCOVR_EXCL_BR_LINE */
        PyModule_AddObjectRef(module, "HTMLParseError", state->parse_error_exc) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                     /* GCOVR_EXCL_LINE: alloc-fail */
    }
    return 0;
}
