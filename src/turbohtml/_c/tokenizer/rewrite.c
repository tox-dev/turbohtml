/* A DOM-less, single-pass HTML rewriter in the spirit of Cloudflare's lol-html.

   turbohtml.rewrite.rewrite() streams the raw WHATWG tokenizer over the input and
   maintains only the stack of currently-open elements -- an O(open-depth) spine, never
   a document tree. Each open element is a lightweight th_node whose sole live link is
   its parent, so the existing CSS selector engine (css/select/selector.c) matches the
   streamable selector subset (type, universal, id, class, attribute, the descendant and
   child combinators, :root, and :is()/:where()/:not() over that subset) against the
   spine directly. A matched element, or a streamed text/comment/doctype, is handed to a
   Python handler that records edits (set an attribute, insert markup before/after,
   replace the inner content, replace or remove the node); the driver applies the edits
   as it emits, so the output is produced incrementally and memory stays proportional to
   the open-element depth plus whatever a handler chooses to buffer.

   The tokenizer runs with resolve_references off and capture_source on, so an untouched
   token is copied out verbatim (a no-op rewrite reproduces the input) and only an edited
   construct is rebuilt. Selectors needing lookahead the stream cannot provide -- the
   sibling combinators, the positional/structural pseudo-classes (:nth-child, :last-of-
   type, :empty), and :has() -- are rejected at compile time with SelectorSyntaxError. */

#include "tokenizer/binding.h"

#include "core/vec.h"
#include "data/attr_atom.h"
#include "dom/tree.h"
#include "dom/tree_internal.h"
#include "css/select/selector.h"

#include <string.h>

/* The content model a start tag switches the tokenizer into, or -1 for none. The public
   Tokenizer applies this from its own binding; the rewriter drives the state machine
   directly, so it repeats the rule (kept in sync with tokenizer.c's content_model_for). */
static int rw_content_model(const th_buf *name) {
    if (name->kind != PyUnicode_1BYTE_KIND) {
        return -1;
    }
    /* A table with one comparison branch, not a chain of inlined memcmp per literal: clang expands
       each `len == N && memcmp` operand into its own branch, and no single tag exercises them all. */
    static const struct {
        const char *tag;
        Py_ssize_t len;
        int model;
    } special[] = {
        {"script", 6, TH_INIT_SCRIPT_DATA},  {"title", 5, TH_INIT_RCDATA},     {"textarea", 8, TH_INIT_RCDATA},
        {"style", 5, TH_INIT_RAWTEXT},       {"xmp", 3, TH_INIT_RAWTEXT},      {"iframe", 6, TH_INIT_RAWTEXT},
        {"noembed", 7, TH_INIT_RAWTEXT},     {"noframes", 8, TH_INIT_RAWTEXT}, {"noscript", 8, TH_INIT_RAWTEXT},
        {"plaintext", 9, TH_INIT_PLAINTEXT},
    };
    for (size_t index = 0; index < sizeof(special) / sizeof(special[0]); index++) {
        if (name->len == special[index].len && memcmp(name->data, special[index].tag, (size_t)name->len) == 0) {
            return special[index].model;
        }
    }
    return -1;
}

/* A grow-on-demand UCS4 output buffer; realized to a str once at the end. */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} rw_out;

static void rw_out_reserve(rw_out *out, Py_ssize_t extra) {
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (out->len + extra <= out->cap) {
        return;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)(out->len + extra), (size_t)out->cap, 256, sizeof(Py_UCS4), &cap, &bytes);
    if (!grew) {         /* GCOVR_EXCL_BR_LINE: a length this large cannot be allocated to reach here */
        out->failed = 1; /* GCOVR_EXCL_LINE: overflow-guard path */
        return;          /* GCOVR_EXCL_LINE: overflow-guard path */
    }
    Py_UCS4 *grown = PyMem_Realloc(out->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    out->data = grown;
    out->cap = (Py_ssize_t)cap;
}

static void rw_out_char(rw_out *out, Py_UCS4 ch) {
    rw_out_reserve(out, 1);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    out->data[out->len++] = ch;
}

static void rw_out_run(rw_out *out, const Py_UCS4 *run, Py_ssize_t len) {
    rw_out_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(out->data + out->len, run, (size_t)len * sizeof(Py_UCS4));
    out->len += len;
}

/* Append a NUL-terminated ASCII literal, widening each byte to a code point. */
static void rw_out_ascii(rw_out *out, const char *text) {
    for (const char *cursor = text; *cursor != '\0'; cursor++) {
        rw_out_char(out, (unsigned char)*cursor);
    }
}

/* Append a str's code points at whatever storage width it uses. */
static void rw_out_str(rw_out *out, PyObject *str) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(str);
    rw_out_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    for (Py_ssize_t index = 0; index < len; index++) {
        out->data[out->len++] = PyUnicode_READ(kind, data, index);
    }
}

/* Append a str, HTML-escaping the markup-significant code points, unless raw is set. */
static void rw_out_content(rw_out *out, PyObject *str, int raw) {
    if (raw) {
        rw_out_str(out, str);
        return;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(str);
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch == '&') {
            rw_out_ascii(out, "&amp;");
        } else if (ch == '<') {
            rw_out_ascii(out, "&lt;");
        } else if (ch == '>') {
            rw_out_ascii(out, "&gt;");
        } else {
            rw_out_char(out, ch);
        }
    }
}

/* Append well-formed UTF-8 bytes as decoded code points (interned attribute names come
   back from the tree as UTF-8; a reconstructed tag needs them as code points). */
static void rw_out_utf8(rw_out *out, const char *bytes, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        unsigned char lead = (unsigned char)bytes[index];
        Py_UCS4 cp;
        if (lead < 0x80) {
            cp = lead;
            index += 1;
        } else if ((lead & 0xE0) == 0xC0) {
            cp = ((Py_UCS4)(lead & 0x1F) << 6) | ((unsigned char)bytes[index + 1] & 0x3F);
            index += 2;
        } else if ((lead & 0xF0) == 0xE0) {
            cp = ((Py_UCS4)(lead & 0x0F) << 12) | (((unsigned char)bytes[index + 1] & 0x3F) << 6) |
                 ((unsigned char)bytes[index + 2] & 0x3F);
            index += 3;
        } else {
            cp = ((Py_UCS4)(lead & 0x07) << 18) | (((unsigned char)bytes[index + 1] & 0x3F) << 12) |
                 (((unsigned char)bytes[index + 2] & 0x3F) << 6) | ((unsigned char)bytes[index + 3] & 0x3F);
            index += 4;
        }
        rw_out_char(out, cp);
    }
}

/* The verbatim source slice a token came from, appended untouched. Every markup token
   carries a source span under capture_source; a text run is a borrowed input slice. */
static void rw_emit_verbatim(rw_out *out, const th_tokenizer *sm, const th_token *token) {
    int kind;
    const char *base = th_tok_input_data(sm, &kind);
    if (token->is_slice) {
        rw_out_reserve(out, token->src_len);
        if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < token->src_len; index++) {
            out->data[out->len++] = PyUnicode_READ(kind, base, token->src_start + index);
        }
        return;
    }
    if (token->has_source) {
        rw_out_reserve(out, token->src_span);
        if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < token->src_span; index++) {
            out->data[out->len++] = PyUnicode_READ(kind, base, token->src_off + index);
        }
        return;
    }
    /* a text/comment run the tokenizer normalized (so it is neither a borrowed slice nor
       a captured span): emit its buffered code points */
    rw_out_reserve(out, token->text.len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < token->text.len; index++) {
        out->data[out->len++] = PyUnicode_READ(token->text.kind, token->text.data, index);
    }
}

/* Encode a code-point run to lowercased UTF-8 in buf (leaving room for the 4-byte tail),
   returning the byte length. The one place tag names, attribute names, and selector
   attribute names all become the interning key. */
static Py_ssize_t rw_encode_utf8(int kind, const void *data, Py_ssize_t len, char *buf, Py_ssize_t cap) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len && at < cap - 4; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch >= 'A' && ch <= 'Z') {
            ch += 32;
        }
        if (ch < 0x80) {
            buf[at++] = (char)ch;
        } else if (ch < 0x800) {
            buf[at++] = (char)(0xC0 | (ch >> 6));
            buf[at++] = (char)(0x80 | (ch & 0x3F));
        } else if (ch < 0x10000) {
            buf[at++] = (char)(0xE0 | (ch >> 12));
            buf[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            buf[at++] = (char)(0x80 | (ch & 0x3F));
        } else {
            buf[at++] = (char)(0xF0 | (ch >> 18));
            buf[at++] = (char)(0x80 | ((ch >> 12) & 0x3F));
            buf[at++] = (char)(0x80 | ((ch >> 6) & 0x3F));
            buf[at++] = (char)(0x80 | (ch & 0x3F));
        }
    }
    return at;
}

/* Resolve a lowercased attribute name (UTF-8) to the atom this tree uses, interning a
   custom name so a streaming node and a compiled selector agree on it. */
static uint32_t rw_attr_atom(th_tree *tree, const char *bytes, Py_ssize_t len) {
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom != TH_ATTR_UNKNOWN) {
        return atom;
    }
    return intern_attr_dynamic(tree, bytes, len);
}

/* The interned tag atom for a token's name; the raw tokenizer leaves token->atom unset
   (only the tree builder fills it), so the rewriter resolves it from the name bytes. A
   non-ASCII name is never in the table, so it stays TH_TAG_UNKNOWN. */
static uint16_t rw_tag_atom(const th_buf *name) {
    if (name->kind != PyUnicode_1BYTE_KIND) {
        return TH_TAG_UNKNOWN;
    }
    return th_tag_lookup((const char *)name->data, name->len);
}

/* One open element on the streaming spine, plus the edits its handler recorded. The
   node's only live link is its parent, so the whole stack is O(open-depth). */
typedef struct {
    th_node *node;
    int edited;            /* an attribute changed: rebuild the start tag instead of verbatim */
    int drop_content;      /* suppress this element's inner tokens */
    int drop_end_tag;      /* do not emit the element's end tag */
    PyObject *append_html; /* emit just before the end tag (already escaped) */
    PyObject *after_html;  /* emit just after the end tag (already escaped) */
} rw_open;

typedef struct {
    sel_compiled *compiled;
    PyObject *handler;
} rw_rule;

/* The whole streaming rewrite: the tokenizer, the shared interning tree, the compiled
   selector rules, the open-element spine, and the growing output. */
typedef struct {
    th_tokenizer *sm;
    th_tree *tree;
    th_node root_node; /* the spine's document root: every outermost element's parent */
    th_node *document;
    rw_rule *rules;
    Py_ssize_t rule_count;
    PyObject *text_handler;
    PyObject *comment_handler;
    PyObject *doctype_handler;
    rw_open *stack;
    Py_ssize_t depth;
    Py_ssize_t stack_cap;
    Py_ssize_t suppress; /* number of open ancestors suppressing their content */
    rw_out out;
    PyObject *handle_type;
    int error; /* a Python exception is set; abort the drive */
} rw_ctx;

/* The deepest element nesting the rewriter tracks. Past it a start tag is still emitted
   but not pushed, so a pathologically deep or unclosed input keeps the spine (and the C
   stack the selector matcher walks) bounded rather than growing without limit. */
#define RW_MAX_DEPTH 8192

/* --- the handle object handed to a Python handler --- */

enum rw_kind { RW_ELEMENT, RW_TEXT, RW_COMMENT, RW_DOCTYPE };

typedef struct {
    PyObject_HEAD rw_ctx *ctx;
    rw_open *open;         /* the element being handled (RW_ELEMENT) */
    const th_token *token; /* the token being handled (text/comment/doctype) */
    enum rw_kind kind;
    int live; /* cleared once the handler returns, so a stashed handle raises */
    /* text/comment edits recorded here, applied by the driver after the handler */
    int removed;
    PyObject *replace_html; /* replacement for the whole node, or NULL */
    int replace_raw;
    PyObject *before_html; /* emitted before the node (accumulated) */
    PyObject *after_html;  /* emitted after the node (accumulated) */
    PyObject *set_text;    /* new text/comment body, or NULL */
} rw_handle;

static int rw_handle_check(rw_handle *self, enum rw_kind kind) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return -1;
    }
    if (self->kind != kind) {
        PyErr_SetString(PyExc_TypeError, "this operation does not apply to this node kind");
        return -1;
    }
    return 0;
}

/* Escape (or keep raw) one content argument into a new str, or return the raw ref. */
static PyObject *rw_escape_arg(PyObject *content, int raw) {
    if (raw) {
        return Py_NewRef(content);
    }
    rw_out scratch = {NULL, 0, 0, 0};
    rw_out_content(&scratch, content, 0);
    if (scratch.failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(scratch.data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, scratch.data, scratch.len);
    PyMem_Free(scratch.data);
    return result;
}

/* Append escaped content onto an accumulating slot (before/after), creating it or
   concatenating in call order. Returns 0, or -1 with an exception set. */
static int rw_accumulate(PyObject **slot, PyObject *content, int raw) {
    PyObject *piece = rw_escape_arg(content, raw);
    if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (*slot == NULL) {
        *slot = piece;
        return 0;
    }
    PyObject *joined = PyUnicode_Concat(*slot, piece);
    Py_DECREF(piece);
    if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_SETREF(*slot, joined);
    return 0;
}

/* Parse the (content, html=False) argument shape shared by every insertion method. */
static int rw_parse_content(PyObject *args, PyObject *kwds, PyObject **content, int *raw) {
    static char *keywords[] = {"content", "html", NULL};
    *raw = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U|$p", keywords, content, raw)) {
        return -1;
    }
    return 0;
}

static PyObject *rw_before(rw_handle *self, PyObject *args, PyObject *kwds) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    if (self->kind == RW_ELEMENT) {
        /* an element's before() emits immediately: the start tag has not been written
           yet, so appending now places the content just ahead of it */
        rw_out_content(&self->ctx->out, content, raw);
    } else if (rw_accumulate(&self->before_html, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: alloc-failure only */
        return NULL;                                                  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *rw_after(rw_handle *self, PyObject *args, PyObject *kwds) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    PyObject **slot = self->kind == RW_ELEMENT ? &self->open->after_html : &self->after_html;
    if (rw_accumulate(slot, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *rw_replace(rw_handle *self, PyObject *args, PyObject *kwds) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    self->removed = 1;
    self->replace_raw = raw;
    Py_XSETREF(self->replace_html, Py_NewRef(content));
    if (self->kind == RW_ELEMENT) {
        self->open->drop_content = 1;
        self->open->drop_end_tag = 1;
    }
    Py_RETURN_NONE;
}

static PyObject *rw_remove(rw_handle *self, PyObject *Py_UNUSED(ignored)) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    self->removed = 1;
    Py_CLEAR(self->replace_html);
    if (self->kind == RW_ELEMENT) {
        self->open->drop_content = 1;
        self->open->drop_end_tag = 1;
    }
    Py_RETURN_NONE;
}

static PyObject *rw_removed_get(rw_handle *self, void *Py_UNUSED(closure)) {
    return PyBool_FromLong(self->removed);
}

/* --- element-only methods --- */

/* Look up the element's attribute value for a lowercased UTF-8 name; sets *found. */
static PyObject *rw_attr_value(rw_ctx *ctx, th_node *node, const char *bytes, Py_ssize_t len, int *found) {
    uint32_t atom = th_attr_atom(bytes, (size_t)len);
    if (atom == TH_ATTR_UNKNOWN) {
        atom = th_attr_lookup(ctx->tree, bytes, len);
    }
    *found = 0;
    if (atom == UINT32_MAX) {
        return NULL;
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            *found = 1;
            if (node->attrs[index].value == NULL) {
                Py_RETURN_NONE;
            }
            return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->attrs[index].value,
                                             node->attrs[index].value_len);
        }
    }
    return NULL;
}

/* A NUL-free lowercased UTF-8 rendering of a name argument, into caller storage. Returns
   the length, or -1 with a TypeError set for a non-str. */
static Py_ssize_t rw_name_bytes(PyObject *name, char *buf, Py_ssize_t cap) {
    if (!PyUnicode_Check(name)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be str");
        return -1;
    }
    return rw_encode_utf8(PyUnicode_KIND(name), PyUnicode_DATA(name), PyUnicode_GET_LENGTH(name), buf, cap);
}

static PyObject *rw_tag_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    th_node *node = self->open->node;
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
}

static PyObject *rw_attrs_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    th_node *node = self->open->node;
    PyObject *result = PyTuple_New(node->attr_count);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(self->ctx->tree, node->attrs[index].name_atom, &name_len);
        PyObject *name_obj = PyUnicode_FromStringAndSize(name, name_len);
        PyObject *value_obj = node->attrs[index].value == NULL
                                  ? Py_NewRef(Py_None)
                                  : PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->attrs[index].value,
                                                              node->attrs[index].value_len);
        if (name_obj == NULL || value_obj == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
            Py_XDECREF(name_obj);                    /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(value_obj);                   /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(result);                       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;                             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyTuple_SET_ITEM(result, index, PyTuple_Pack(2, name_obj, value_obj));
        Py_DECREF(name_obj);
        Py_DECREF(value_obj);
    }
    return result;
}

static PyObject *rw_get(rw_handle *self, PyObject *args) {
    PyObject *name;
    PyObject *fallback = Py_None;
    if (!PyArg_ParseTuple(args, "O|O", &name, &fallback)) {
        return NULL;
    }
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    char buf[256];
    Py_ssize_t len = rw_name_bytes(name, buf, sizeof(buf));
    if (len < 0) {
        return NULL;
    }
    int found;
    PyObject *value = rw_attr_value(self->ctx, self->open->node, buf, len, &found);
    if (found) {
        return value;
    }
    return Py_NewRef(fallback);
}

static PyObject *rw_has(rw_handle *self, PyObject *name) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    char buf[256];
    Py_ssize_t len = rw_name_bytes(name, buf, sizeof(buf));
    if (len < 0) {
        return NULL;
    }
    int found;
    PyObject *value = rw_attr_value(self->ctx, self->open->node, buf, len, &found);
    Py_XDECREF(value);
    return PyBool_FromLong(found);
}

/* Grow an element's attribute array by one slot; returns the new slot or NULL (OOM). */
static th_node_attr *rw_attr_grow(th_node *node) {
    th_node_attr *grown = PyMem_Realloc(node->attrs, (size_t)(node->attr_count + 1) * sizeof(th_node_attr));
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->attrs = grown;
    return &grown[node->attr_count];
}

/* Copy a str's code points into a fresh UCS4 buffer; *out_len set. NULL only on OOM. */
static Py_UCS4 *rw_copy_ucs4(PyObject *str, Py_ssize_t *out_len) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(str);
    Py_UCS4 *buf = PyMem_Malloc((size_t)(len ? len : 1) * sizeof(Py_UCS4));
    if (buf == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    for (Py_ssize_t index = 0; index < len; index++) {
        buf[index] = PyUnicode_READ(kind, data, index);
    }
    *out_len = len;
    return buf;
}

static PyObject *rw_set_attribute(rw_handle *self, PyObject *args) {
    PyObject *name;
    PyObject *value;
    if (!PyArg_ParseTuple(args, "OU", &name, &value)) {
        return NULL;
    }
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    char buf[256];
    Py_ssize_t len = rw_name_bytes(name, buf, sizeof(buf));
    if (len < 0) {
        return NULL;
    }
    if (len == 0) {
        PyErr_SetString(PyExc_ValueError, "attribute name must not be empty");
        return NULL;
    }
    uint32_t atom = rw_attr_atom(self->ctx->tree, buf, len);
    self->open->edited = 1;
    th_node *node = self->open->node;
    Py_ssize_t new_len;
    Py_UCS4 *copy = rw_copy_ucs4(value, &new_len);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            PyMem_Free(node->attrs[index].value);
            node->attrs[index].value = copy;
            node->attrs[index].value_len = new_len;
            Py_RETURN_NONE;
        }
    }
    th_node_attr *slot = rw_attr_grow(node);
    if (slot == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(copy); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    slot->name_atom = atom;
    slot->value = copy;
    slot->value_len = new_len;
    node->attr_count++;
    Py_RETURN_NONE;
}

static PyObject *rw_remove_attribute(rw_handle *self, PyObject *name) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    char buf[256];
    Py_ssize_t len = rw_name_bytes(name, buf, sizeof(buf));
    if (len < 0) {
        return NULL;
    }
    uint32_t atom = th_attr_atom(buf, (size_t)len);
    if (atom == TH_ATTR_UNKNOWN) {
        atom = th_attr_lookup(self->ctx->tree, buf, len);
    }
    self->open->edited = 1;
    th_node *node = self->open->node;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == atom) {
            PyMem_Free(node->attrs[index].value);
            memmove(&node->attrs[index], &node->attrs[index + 1],
                    (size_t)(node->attr_count - index - 1) * sizeof(th_node_attr));
            node->attr_count--;
            Py_RETURN_NONE;
        }
    }
    Py_RETURN_NONE;
}

/* Rebuild the start tag of an edited element from its current name and attributes. */
static void rw_emit_start_tag(rw_ctx *ctx, th_node *node, int self_closing) {
    rw_out_char(&ctx->out, '<');
    rw_out_run(&ctx->out, node->text, node->text_len);
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        rw_out_char(&ctx->out, ' ');
        Py_ssize_t name_len;
        const char *name = th_attr_name(ctx->tree, node->attrs[index].name_atom, &name_len);
        rw_out_utf8(&ctx->out, name, name_len);
        if (node->attrs[index].value != NULL) {
            rw_out_ascii(&ctx->out, "=\"");
            for (Py_ssize_t pos = 0; pos < node->attrs[index].value_len; pos++) {
                Py_UCS4 ch = node->attrs[index].value[pos];
                if (ch == '&') {
                    rw_out_ascii(&ctx->out, "&amp;");
                } else if (ch == '"') {
                    rw_out_ascii(&ctx->out, "&quot;");
                } else {
                    rw_out_char(&ctx->out, ch);
                }
            }
            rw_out_char(&ctx->out, '"');
        }
    }
    if (self_closing) {
        rw_out_char(&ctx->out, '/');
    }
    rw_out_char(&ctx->out, '>');
}

static PyObject *rw_set_content(rw_handle *self, PyObject *args, PyObject *kwds) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    /* replace the element's inner content: drop the original children, keep the tags */
    self->open->drop_content = 1;
    Py_CLEAR(self->open->append_html);
    PyObject *piece = rw_escape_arg(content, raw);
    if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->open->append_html = piece;
    Py_RETURN_NONE;
}

static PyObject *rw_append(rw_handle *self, PyObject *args, PyObject *kwds) {
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    if (rw_accumulate(&self->open->append_html, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                                                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *rw_prepend(rw_handle *self, PyObject *args, PyObject *kwds) {
    PyObject *content;
    int raw;
    if (rw_parse_content(args, kwds, &content, &raw) < 0) {
        return NULL;
    }
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    /* the start tag is emitted after the handler returns; record the prepend as the head
       of the inner content by folding it before any existing set_content/append text */
    PyObject *piece = rw_escape_arg(content, raw);
    if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* stored on a dedicated slot so it precedes the original inner content at emit time */
    if (self->before_html == NULL) {
        self->before_html = piece;
    } else {
        PyObject *joined = PyUnicode_Concat(self->before_html, piece);
        Py_DECREF(piece);
        if (joined == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_SETREF(self->before_html, joined);
    }
    Py_RETURN_NONE;
}

static PyObject *rw_remove_and_keep_content(rw_handle *self, PyObject *Py_UNUSED(ignored)) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    /* drop the element's own tags but let its children stream through unchanged */
    self->removed = 1;
    self->open->drop_end_tag = 1;
    Py_RETURN_NONE;
}

/* --- text / comment / doctype accessors --- */

static PyObject *rw_text_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    if (self->kind != RW_TEXT && self->kind != RW_COMMENT) {
        PyErr_SetString(PyExc_TypeError, "this operation does not apply to this node kind");
        return NULL;
    }
    if (self->set_text != NULL) {
        return Py_NewRef(self->set_text);
    }
    if (self->token->is_slice) {
        /* a text run with resolve_references off is a borrowed slice of the input, not a
           filled buffer; realize it from the tokenizer's input storage */
        int kind;
        const char *base = th_tok_input_data(self->ctx->sm, &kind);
        return th_str_from_kind(kind, base + self->token->src_start * kind, self->token->src_len);
    }
    return th_str_from_kind(self->token->text.kind, self->token->text.data, self->token->text.len);
}

static PyObject *rw_set_text(rw_handle *self, PyObject *value) {
    if (!self->live) {
        PyErr_SetString(PyExc_RuntimeError, "the handle is only valid inside its handler call");
        return NULL;
    }
    if (self->kind != RW_TEXT && self->kind != RW_COMMENT) {
        PyErr_SetString(PyExc_TypeError, "this operation does not apply to this node kind");
        return NULL;
    }
    if (!PyUnicode_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "text must be str");
        return NULL;
    }
    Py_XSETREF(self->set_text, Py_NewRef(value));
    Py_RETURN_NONE;
}

static PyObject *rw_doctype_name_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (rw_handle_check(self, RW_DOCTYPE) < 0) {
        return NULL;
    }
    if (self->token->name.len == 0) {
        Py_RETURN_NONE;
    }
    return th_str_from_kind(self->token->name.kind, self->token->name.data, self->token->name.len);
}

static PyObject *rw_doctype_public_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (rw_handle_check(self, RW_DOCTYPE) < 0) {
        return NULL;
    }
    if (!self->token->has_public_id) {
        Py_RETURN_NONE;
    }
    return th_str_from_kind(self->token->public_id.kind, self->token->public_id.data, self->token->public_id.len);
}

static PyObject *rw_doctype_system_get(rw_handle *self, void *Py_UNUSED(closure)) {
    if (rw_handle_check(self, RW_DOCTYPE) < 0) {
        return NULL;
    }
    if (!self->token->has_system_id) {
        Py_RETURN_NONE;
    }
    return th_str_from_kind(self->token->system_id.kind, self->token->system_id.data, self->token->system_id.len);
}

static PyObject *rw_kind_get(rw_handle *self, void *Py_UNUSED(closure)) {
    static const char *const names[] = {"element", "text", "comment", "doctype"};
    return PyUnicode_FromString(names[self->kind]);
}

static PyMethodDef rw_handle_methods[] = {
    {"before", (PyCFunction)(void (*)(void))rw_before, METH_VARARGS | METH_KEYWORDS,
     "Insert content immediately before this node (html=True inserts raw markup)."},
    {"after", (PyCFunction)(void (*)(void))rw_after, METH_VARARGS | METH_KEYWORDS,
     "Insert content immediately after this node."},
    {"replace", (PyCFunction)(void (*)(void))rw_replace, METH_VARARGS | METH_KEYWORDS,
     "Replace this node (an element, its whole subtree) with content."},
    {"remove", (PyCFunction)rw_remove, METH_NOARGS,
     "remove()\n--\n\nRemove this node (an element, its whole subtree)."},
    {"set_content", (PyCFunction)(void (*)(void))rw_set_content, METH_VARARGS | METH_KEYWORDS,
     "Replace an element's inner content, keeping its tags."},
    {"append", (PyCFunction)(void (*)(void))rw_append, METH_VARARGS | METH_KEYWORDS,
     "Append content as the last of an element's inner content."},
    {"prepend", (PyCFunction)(void (*)(void))rw_prepend, METH_VARARGS | METH_KEYWORDS,
     "Insert content as the first of an element's inner content."},
    {"remove_and_keep_content", (PyCFunction)rw_remove_and_keep_content, METH_NOARGS,
     "remove_and_keep_content()\n--\n\nDrop an element's own tags but keep its children."},
    {"get", (PyCFunction)rw_get, METH_VARARGS, "An element attribute value, or the default when absent."},
    {"has_attribute", (PyCFunction)rw_has, METH_O,
     "has_attribute(name, /)\n--\n\nWhether the element carries the named attribute."},
    {"set_attribute", (PyCFunction)rw_set_attribute, METH_VARARGS, "Set (or add) an element attribute."},
    {"remove_attribute", (PyCFunction)rw_remove_attribute, METH_O,
     "remove_attribute(name, /)\n--\n\nRemove an element attribute if present."},
    {"set_text", (PyCFunction)rw_set_text, METH_O, "set_text(value, /)\n--\n\nReplace a text or comment node's body."},
    {NULL, NULL, 0, NULL},
};

static PyGetSetDef rw_handle_getset[] = {
    {"kind", (getter)rw_kind_get, NULL, "The node kind: element, text, comment or doctype.", NULL},
    {"removed", (getter)rw_removed_get, NULL, "Whether a handler removed or replaced this node.", NULL},
    {"tag", (getter)rw_tag_get, NULL, "The element's lowercased tag name.", NULL},
    {"attrs", (getter)rw_attrs_get, NULL, "The element's attributes as (name, value) pairs.", NULL},
    {"text", (getter)rw_text_get, NULL, "A text or comment node's body.", NULL},
    {"name", (getter)rw_doctype_name_get, NULL, "The doctype name.", NULL},
    {"public_id", (getter)rw_doctype_public_get, NULL, "The doctype public identifier, or None.", NULL},
    {"system_id", (getter)rw_doctype_system_get, NULL, "The doctype system identifier, or None.", NULL},
    {NULL, NULL, NULL, NULL, NULL},
};

static void rw_handle_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    rw_handle *handle = (rw_handle *)self;
    Py_XDECREF(handle->replace_html);
    Py_XDECREF(handle->before_html);
    Py_XDECREF(handle->after_html);
    Py_XDECREF(handle->set_text);
    type->tp_free(self);
    Py_DECREF(type);
}

PyDoc_STRVAR(rw_handle_doc, "A handle to the element, text, comment or doctype a rewrite handler is visiting.");

static PyType_Slot rw_handle_slots[] = {
    {Py_tp_doc, (void *)rw_handle_doc},
    {Py_tp_dealloc, rw_handle_dealloc},
    {Py_tp_methods, rw_handle_methods},
    {Py_tp_getset, rw_handle_getset},
    TH_SEALED_END,
};

static PyType_Spec rw_handle_spec = {
    .name = "turbohtml._html._RewriteHandle",
    .basicsize = sizeof(rw_handle),
    .flags = Py_TPFLAGS_DEFAULT | TH_SEALED,
    .slots = rw_handle_slots,
};

/* --- selector-subset validation and custom-attribute interning --- */

/* Whether a pseudo-class can be decided from an element and its ancestors alone (no
   later sibling, descendant, or text a streaming pass has not seen yet). */
static int rw_pseudo_streamable(int pseudo) {
    switch (pseudo) {
    case PSEUDO_NONE:
    case PSEUDO_ROOT:
    case PSEUDO_ANY_LINK:
    case PSEUDO_NEVER:
    case PSEUDO_LANG:
    case PSEUDO_CHECKED:
    case PSEUDO_REQUIRED:
    case PSEUDO_OPTIONAL:
    case PSEUDO_READ_ONLY:
    case PSEUDO_READ_WRITE:
    case PSEUDO_IS:
    case PSEUDO_WHERE:
    case PSEUDO_NOT:
        return 1;
    default:
        return 0;
    }
}

static int rw_validate_alts(sel_complex *alts, int count, th_tree *tree, const char **reason);

/* Validate one simple selector and intern any custom attribute name so a streaming node
   resolves the same atom the compiled selector holds. Returns 0 on an unstreamable one. */
static int rw_validate_simple(sel_simple *simple, th_tree *tree, const char **reason) {
    if (simple->kind == ':') {
        if (!rw_pseudo_streamable(simple->pseudo)) {
            *reason = "a positional, structural, or state pseudo-class needs lookahead a stream cannot provide";
            return 0;
        }
        if (simple->sub != NULL) {
            return rw_validate_alts(simple->sub, simple->sub_count, tree, reason);
        }
        return 1;
    }
    if (simple->kind == '[' &&
        simple->attr_atom == UINT32_MAX) { /* a custom attribute selector always carries a name */
        char buf[256];
        Py_ssize_t len = rw_encode_utf8(PyUnicode_4BYTE_KIND, simple->name, simple->name_len, buf, sizeof(buf));
        simple->attr_atom = intern_attr_dynamic(tree, buf, len);
    }
    return 1;
}

static int rw_validate_alts(sel_complex *alts, int count, th_tree *tree, const char **reason) {
    for (int alt = 0; alt < count; alt++) {
        for (int comp = 0; comp < alts[alt].count; comp++) {
            sel_compound *compound = &alts[alt].compounds[comp];
            if (comp > 0 && (compound->combinator == '+' || compound->combinator == '~')) {
                *reason = "the sibling combinators + and ~ need lookahead a stream cannot provide";
                return 0;
            }
            for (int simp = 0; simp < compound->count; simp++) {
                if (!rw_validate_simple(&compound->simples[simp], tree, reason)) {
                    return 0;
                }
            }
        }
    }
    return 1;
}

/* --- the driver --- */

/* Free a self-managed spine node and its attribute copies. */
static void rw_free_node(th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        PyMem_Free(node->attrs[index].value);
    }
    PyMem_Free(node->attrs);
    PyMem_Free(node->text);
    PyMem_Free(node);
}

/* Build a self-managed spine element from a start-tag token, linked only to its parent
   (the enclosing open element, or the shared document root). NULL on allocation failure. */
static th_node *rw_make_node(rw_ctx *ctx, const th_token *token, uint16_t atom, th_node *parent) {
    th_node *node = PyMem_Calloc(1, sizeof(th_node));
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->type = TH_NODE_ELEMENT;
    node->ns = TH_NS_HTML;
    node->atom = atom;
    node->tag_flags = th_tag_flags(atom);
    node->parent = parent;
    Py_ssize_t tag_len = token->name.len; /* a start tag always carries a name, so tag_len >= 1 */
    node->text = PyMem_Malloc((size_t)tag_len * sizeof(Py_UCS4));
    if (node->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(node);     /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < tag_len; index++) {
        node->text[index] = PyUnicode_READ(token->name.kind, token->name.data, index);
    }
    node->text_len = tag_len;
    if (token->attr_count > 0) {
        node->attrs = PyMem_Calloc((size_t)token->attr_count, sizeof(th_node_attr));
        if (node->attrs == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyMem_Free(node->text); /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(node);       /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < token->attr_count; index++) {
            const th_attr *attr = &token->attrs[index];
            char buf[256];
            Py_ssize_t at = rw_encode_utf8(attr->name.kind, attr->name.data, attr->name.len, buf, sizeof(buf));
            node->attrs[index].name_atom = rw_attr_atom(ctx->tree, buf, at);
            if (attr->has_value) {
                node->attrs[index].value_len = attr->value.len;
                node->attrs[index].value =
                    PyMem_Malloc((size_t)(attr->value.len ? attr->value.len : 1) * sizeof(Py_UCS4));
                if (node->attrs[index].value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
                    node->attr_count = index;           /* GCOVR_EXCL_LINE: allocation-failure path */
                    rw_free_node(node);                 /* GCOVR_EXCL_LINE: allocation-failure path */
                    return NULL;                        /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                for (Py_ssize_t pos = 0; pos < attr->value.len; pos++) {
                    node->attrs[index].value[pos] = PyUnicode_READ(attr->value.kind, attr->value.data, pos);
                }
            }
        }
        node->attr_count = token->attr_count;
    }
    return node;
}

/* Make a fresh handle bound to the driver context. NULL on allocation failure. */
static rw_handle *rw_handle_new(rw_ctx *ctx, enum rw_kind kind) {
    rw_handle *handle = (rw_handle *)((PyTypeObject *)ctx->handle_type)->tp_alloc((PyTypeObject *)ctx->handle_type, 0);
    if (handle == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    handle->ctx = ctx;
    handle->open = NULL;
    handle->token = NULL;
    handle->kind = kind;
    handle->live = 1;
    handle->removed = 0;
    handle->replace_html = NULL;
    handle->replace_raw = 0;
    handle->before_html = NULL;
    handle->after_html = NULL;
    handle->set_text = NULL;
    return handle;
}

/* Run a Python handler, converting its failure into the context error flag. */
static void rw_call_handler(rw_ctx *ctx, PyObject *handler, PyObject *handle) {
    PyObject *result = PyObject_CallOneArg(handler, handle);
    if (result == NULL) {
        ctx->error = 1;
        return;
    }
    Py_DECREF(result);
}

static void rw_handle_element(rw_ctx *ctx, const th_token *token) {
    int suppressed = ctx->suppress > 0;
    uint16_t atom = rw_tag_atom(&token->name);
    int self_terminating = token->self_closing || th_tag_is_void(atom);
    th_node *parent = ctx->depth > 0 ? ctx->stack[ctx->depth - 1].node : ctx->document;
    th_node *node = rw_make_node(ctx, token, atom, parent);
    if (node == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }

    rw_open open = {node, 0, 0, 0, NULL, NULL};
    rw_handle *handle = NULL;
    for (Py_ssize_t index = 0; index < ctx->rule_count && !suppressed && !ctx->error; index++) {
        if (!selector_matches(node, ctx->rules[index].compiled, NULL)) {
            continue;
        }
        if (handle == NULL) {
            handle = rw_handle_new(ctx, RW_ELEMENT);
            if (handle == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
                break;            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            handle->open = &open;
        }
        rw_call_handler(ctx, ctx->rules[index].handler, (PyObject *)handle);
    }

    if (ctx->error) {
        if (handle != NULL) { /* GCOVR_EXCL_BR_LINE: NULL needs a handle-alloc failure, unforceable */
            handle->live = 0;
            Py_DECREF(handle);
        }
        rw_free_node(node);
        return;
    }

    int emit = !suppressed;
    int removed = handle != NULL && handle->removed;

    if (emit) {
        if (removed) {
            if (handle->replace_html != NULL) {
                rw_out_content(&ctx->out, handle->replace_html, handle->replace_raw);
            }
        } else if (open.edited) {
            rw_emit_start_tag(ctx, node, token->self_closing);
        } else {
            rw_emit_verbatim(&ctx->out, ctx->sm, token);
        }
        /* prepend content, then any inner replacement, all inside the element */
        if (!removed && handle != NULL && handle->before_html != NULL) {
            rw_out_str(&ctx->out, handle->before_html);
        }
        if (!removed && open.append_html != NULL && open.drop_content) {
            rw_out_str(&ctx->out, open.append_html);
            Py_DECREF(open.append_html); /* guarded non-NULL above; an unconditional drop has no dead NULL branch */
            open.append_html = NULL;
        }
    }

    if (self_terminating) {
        /* no content or end tag follows; emit the trailing pieces now */
        if (emit && open.append_html != NULL) {
            rw_out_str(&ctx->out, open.append_html);
        }
        if (emit && open.after_html != NULL) {
            rw_out_str(&ctx->out, open.after_html);
        }
        Py_XDECREF(open.append_html);
        Py_XDECREF(open.after_html);
        if (handle != NULL) {
            handle->live = 0;
            Py_DECREF(handle);
        }
        rw_free_node(node);
        return;
    }

    if (handle != NULL) {
        handle->live = 0;
        Py_DECREF(handle);
    }

    if (ctx->depth >= RW_MAX_DEPTH) {
        /* keep the spine bounded: emit the element's content inline but do not track it,
           so it never nests deeper than the guard (its end tag streams as a stray) */
        rw_free_node(node);
        return;
    }
    if (ctx->depth == ctx->stack_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(ctx->depth + 1), (size_t)ctx->stack_cap, 32, sizeof(rw_open), &cap, &bytes);
        if (!grew) {            /* GCOVR_EXCL_BR_LINE: the depth guard caps growth first */
            ctx->error = 1;     /* GCOVR_EXCL_LINE: unreachable overflow-guard path */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE */
            rw_free_node(node); /* GCOVR_EXCL_LINE */
            return;             /* GCOVR_EXCL_LINE */
        }
        rw_open *grown = PyMem_Realloc(ctx->stack, bytes);
        if (grown == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->error = 1;     /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();   /* GCOVR_EXCL_LINE */
            rw_free_node(node); /* GCOVR_EXCL_LINE */
            return;             /* GCOVR_EXCL_LINE */
        }
        ctx->stack = grown;
        ctx->stack_cap = (Py_ssize_t)cap;
    }
    ctx->stack[ctx->depth++] = open;
    if (open.drop_content) {
        ctx->suppress++;
    }
    /* a rawtext/rcdata element switches the tokenizer's content model */
    int model = rw_content_model(&token->name);
    if (model >= 0) {
        th_tok_switch(ctx->sm, (enum th_initial_state)model);
    }
}

static void rw_handle_end(rw_ctx *ctx, const th_token *token) {
    uint16_t atom = rw_tag_atom(&token->name);
    /* find the matching open element, top-down; a known tag matches by atom, an
       unknown (custom) tag by its lowercased name */
    Py_ssize_t match = -1;
    for (Py_ssize_t index = ctx->depth - 1; index >= 0; index--) {
        th_node *node = ctx->stack[index].node;
        if (node->atom != atom) {
            continue;
        }
        int same = 1;
        if (atom == TH_TAG_UNKNOWN) {
            same = node->text_len == token->name.len;
            for (Py_ssize_t pos = 0; same && pos < node->text_len; pos++) {
                same = node->text[pos] == PyUnicode_READ(token->name.kind, token->name.data, pos);
            }
        }
        if (same) {
            match = index;
            break;
        }
    }
    if (match < 0) {
        /* a stray end tag with no open match: emit it verbatim */
        if (ctx->suppress == 0) {
            rw_emit_verbatim(&ctx->out, ctx->sm, token);
        }
        return;
    }
    /* pop the intermediate unclosed elements (no synthetic end tags), then the match */
    for (Py_ssize_t index = ctx->depth - 1; index > match; index--) {
        rw_open *open = &ctx->stack[index];
        if (open->drop_content) {
            ctx->suppress--;
        }
        Py_XDECREF(open->append_html);
        Py_XDECREF(open->after_html);
        rw_free_node(open->node);
    }
    rw_open *closing = &ctx->stack[match];
    if (closing->drop_content) {
        ctx->suppress--;
    }
    int emit = ctx->suppress == 0;
    if (emit && closing->append_html != NULL) {
        rw_out_str(&ctx->out, closing->append_html);
    }
    if (emit && !closing->drop_end_tag) {
        rw_emit_verbatim(&ctx->out, ctx->sm, token);
    }
    if (emit && closing->after_html != NULL) {
        rw_out_str(&ctx->out, closing->after_html);
    }
    Py_XDECREF(closing->append_html);
    Py_XDECREF(closing->after_html);
    rw_free_node(closing->node);
    ctx->depth = match;
}

/* Drive a text/comment/doctype token through its handler and emit the result. */
static void rw_handle_leaf(rw_ctx *ctx, const th_token *token, PyObject *handler, enum rw_kind kind) {
    if (ctx->suppress > 0) {
        return;
    }
    if (handler == NULL) {
        rw_emit_verbatim(&ctx->out, ctx->sm, token);
        return;
    }
    rw_handle *handle = rw_handle_new(ctx, kind);
    if (handle == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return;           /* GCOVR_EXCL_LINE */
    }
    handle->token = token;
    rw_call_handler(ctx, handler, (PyObject *)handle);
    handle->live = 0;
    if (ctx->error) {
        Py_DECREF(handle);
        return;
    }
    if (handle->before_html != NULL) {
        rw_out_str(&ctx->out, handle->before_html);
    }
    if (handle->removed) {
        if (handle->replace_html != NULL) {
            rw_out_content(&ctx->out, handle->replace_html, handle->replace_raw);
        }
    } else if (handle->set_text != NULL) {
        if (kind == RW_COMMENT) {
            rw_out_ascii(&ctx->out, "<!--");
            rw_out_str(&ctx->out, handle->set_text);
            rw_out_ascii(&ctx->out, "-->");
        } else {
            rw_out_content(&ctx->out, handle->set_text, 0);
        }
    } else {
        rw_emit_verbatim(&ctx->out, ctx->sm, token);
    }
    if (handle->after_html != NULL) {
        rw_out_str(&ctx->out, handle->after_html);
    }
    Py_DECREF(handle);
}

/* Free everything the context owns. */
static void rw_ctx_clear(rw_ctx *ctx) {
    for (Py_ssize_t index = 0; index < ctx->depth; index++) {
        Py_XDECREF(ctx->stack[index].append_html);
        Py_XDECREF(ctx->stack[index].after_html);
        rw_free_node(ctx->stack[index].node);
    }
    PyMem_Free(ctx->stack);
    for (Py_ssize_t index = 0; index < ctx->rule_count; index++) {
        selector_free(ctx->rules[index].compiled);
        Py_DECREF(ctx->rules[index].handler);
    }
    PyMem_Free(ctx->rules);
    PyMem_Free(ctx->out.data);
    if (ctx->tree != NULL) { /* GCOVR_EXCL_BR_LINE: the tree is set before any clear runs, never NULL here */
        th_tree_free(ctx->tree);
    }
    if (ctx->sm != NULL) {
        th_tok_free(ctx->sm);
    }
}

/* Compile the element rules and reject any selector the stream cannot match. Returns 0,
   or -1 with an exception set. */
static int rw_compile_rules(rw_ctx *ctx, module_state *state, PyObject *handlers) {
    Py_ssize_t count = PySequence_Size(handlers); /* the shim always passes a tuple */
    if (count < 0) {                              /* GCOVR_EXCL_BR_LINE: a tuple's size never fails */
        return -1;                                /* GCOVR_EXCL_LINE: defensive */
    }
    if (count == 0) {
        return 0;
    }
    ctx->rules = PyMem_Calloc((size_t)count, sizeof(rw_rule));
    if (ctx->rules == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();     /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *pair = PySequence_GetItem(handlers, index);
        if (pair == NULL) { /* GCOVR_EXCL_BR_LINE: the shim always passes a real sequence of pairs */
            return -1;      /* GCOVR_EXCL_LINE: defensive */
        }
        PyObject *selector = PyTuple_GetItem(pair, 0);
        PyObject *handler = PyTuple_GetItem(pair, 1);
        if (selector == NULL || handler == NULL) { /* GCOVR_EXCL_BR_LINE: the shim always passes 2-tuples */
            Py_DECREF(pair);                       /* GCOVR_EXCL_LINE: defensive */
            return -1;                             /* GCOVR_EXCL_LINE: defensive */
        }
        sel_compiled *compiled = selector_compile(state->selector_error, ctx->tree, selector);
        if (compiled == NULL) {
            Py_DECREF(pair);
            return -1;
        }
        const char *reason = NULL;
        if (!rw_validate_alts(compiled->alts, compiled->count, ctx->tree, &reason)) {
            selector_free(compiled);
            PyErr_Format(state->selector_error, "selector %R is not streamable: %s", selector, reason);
            Py_DECREF(pair);
            return -1;
        }
        ctx->rules[ctx->rule_count].compiled = compiled;
        ctx->rules[ctx->rule_count].handler = Py_NewRef(handler);
        ctx->rule_count++;
        Py_DECREF(pair);
    }
    return 0;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in tokenizer/binding.h, called from core/module.c
PyObject *turbohtml_rewrite(PyObject *module, PyObject *args) {
    PyObject *source;
    PyObject *handlers;
    PyObject *text_handler;
    PyObject *comment_handler;
    PyObject *doctype_handler;
    if (!PyArg_ParseTuple(args, "UOOOO", &source, &handlers, &text_handler, &comment_handler, &doctype_handler)) {
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    rw_ctx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.text_handler = text_handler == Py_None ? NULL : text_handler;
    ctx.comment_handler = comment_handler == Py_None ? NULL : comment_handler;
    ctx.doctype_handler = doctype_handler == Py_None ? NULL : doctype_handler;
    ctx.handle_type = state->rewrite_handle_type;
    ctx.tree = th_tree_new();
    if (ctx.tree == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ctx.root_node.type = TH_NODE_DOCUMENT;
    ctx.document = &ctx.root_node;
    if (rw_compile_rules(&ctx, state, handlers) < 0) {
        rw_ctx_clear(&ctx);
        return NULL;
    }
    ctx.sm = th_tok_new();
    if (ctx.sm == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        rw_ctx_clear(&ctx);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_tok_set_options(ctx.sm, 0, 1, 1);
    Py_ssize_t length = PyUnicode_GET_LENGTH(source);
    th_tok_feed(ctx.sm, PyUnicode_KIND(source), PyUnicode_DATA(source), length);
    th_tok_close(ctx.sm);

    th_token *token;
    enum th_step step;
    Py_BEGIN_CRITICAL_SECTION(module);
    while (!ctx.error) {
        step = th_tok_next(ctx.sm, &token);
        if (step != TH_STEP_TOKEN) {
            break;
        }
        switch (token->kind) {
        case TH_START_TAG:
            rw_handle_element(&ctx, token);
            break;
        case TH_END_TAG:
            rw_handle_end(&ctx, token);
            break;
        case TH_TEXT:
        case TH_CHARREF:
            rw_handle_leaf(&ctx, token, ctx.text_handler, RW_TEXT);
            break;
        case TH_COMMENT:
            rw_handle_leaf(&ctx, token, ctx.comment_handler, RW_COMMENT);
            break;
        default: /* TH_DOCTYPE */
            rw_handle_leaf(&ctx, token, ctx.doctype_handler, RW_DOCTYPE);
            break;
        }
    }
    Py_END_CRITICAL_SECTION();

    /* an unclosed element at EOF: flush its trailing edits and free the spine */
    while (ctx.depth > 0 && !ctx.error) {
        rw_open *open = &ctx.stack[--ctx.depth];
        int emit = ctx.suppress == 0;
        if (open->drop_content) {
            ctx.suppress--;
        }
        if (emit && open->append_html != NULL) {
            rw_out_str(&ctx.out, open->append_html);
        }
        if (emit && open->after_html != NULL) {
            rw_out_str(&ctx.out, open->after_html);
        }
        Py_XDECREF(open->append_html);
        Py_XDECREF(open->after_html);
        rw_free_node(open->node);
    }

    PyObject *result = NULL;
    if (ctx.out.failed) {     /* GCOVR_EXCL_BR_LINE: an output OOM cannot be forced from a test */
        if (!ctx.error) {     /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        } /* GCOVR_EXCL_LINE: llvm flags the OOM branch's closing brace */
    } else if (!ctx.error) {
        result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, ctx.out.data, ctx.out.len);
    }
    rw_ctx_clear(&ctx);
    return result;
}

// NOLINTNEXTLINE(misc-use-internal-linkage): declared in tokenizer/binding.h, called from core/module.c
int rewrite_register(PyObject *module, module_state *state) {
    state->rewrite_handle_type = PyType_FromModuleAndSpec(module, &rw_handle_spec, NULL);
    if (state->rewrite_handle_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return PyModule_AddObjectRef(module, "_RewriteHandle", state->rewrite_handle_type);
}
