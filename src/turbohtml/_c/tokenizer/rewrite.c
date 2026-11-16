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

#include "core/hash.h"
#include "core/vec.h"
#include "core/work_steps.h"
#include "data/attr_atom.h"
#include "dom/tree.h"
#include "dom/tree_internal.h"
#include "css/select/selector.h"

#include <string.h>

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* The content model an HTML start tag switches the tokenizer into, or -1 for none. The public
   Tokenizer applies this from its own binding; the rewriter drives the state machine
   directly, so it repeats the rule (kept in sync with tokenizer.c's content_model_for).
   Only an element the HTML rules insert switches: an SVG or MathML <title>, <style>,
   <script>, ... is an ordinary element whose content stays markup (rw_start_ns). */
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

/* A grow-on-demand UCS4 output buffer; realized to a str once at the end. A write past room takes rw_out_make_room,
   which grows the buffer and settles a pending tail (rw_hold_tail). */
typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    Py_ssize_t room;  /* cap, or len while a tail is pending, so the next write settles it */
    Py_ssize_t tail;  /* one past the offset of the pending tail's `<`, or 0 for none */
    Py_ssize_t moved; /* one past the offset the last settled tail ended at, which its escape moved up by 3 */
    int rcdata;       /* the pending tail is RCDATA text */
    int failed;
} rw_out;

/* Whether a `<` in data text reads as the start of markup when `next` follows it. */
static int rw_lt_joins(Py_UCS4 next) {
    return is_ascii_alpha(next) || next == '/' || next == '!' || next == '?';
}

/* Whether the pending tail, `<` and what follows it to the end of the output, reads as the start of markup once
   `next` is written after it. The source read it as text because what followed there ended it. */
static int rw_tail_joins(const rw_out *out, Py_UCS4 next) {
    Py_ssize_t len = out->len - (out->tail - 1);
    if (!out->rcdata) {
        return len == 2 || rw_lt_joins(next);
    }
    if (len == 1) {
        return next == '/';
    }
    return is_ascii_alpha(next) || (len > 2 && (is_space(next) || next == '/' || next == '>'));
}

/* Make room for extra code points, the first of them `next`. A pending tail that `next` would join is escaped in
   place first, while it is still the end of the output. */
static TH_NOINLINE void rw_out_make_room(rw_out *out, Py_ssize_t extra, Py_UCS4 next) {
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t escape = out->tail > 0 && rw_tail_joins(out, next) ? 3 : 0;
    Py_ssize_t need = out->len + escape + extra;
    if (need > out->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)need, (size_t)out->cap, 256, sizeof(Py_UCS4), &cap, &bytes);
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
    if (escape) {
        Py_UCS4 *lt = out->data + out->tail - 1;
        memmove(lt + 4, lt + 1, (size_t)(out->len - out->tail) * sizeof(Py_UCS4));
        memcpy(lt, (const Py_UCS4[]){'&', 'l', 't', ';'}, 4 * sizeof(Py_UCS4));
        out->moved = out->len + 1;
        out->len += 3;
    }
    out->tail = 0;
    out->room = out->cap;
}

static void rw_out_char(rw_out *out, Py_UCS4 ch) {
    if (out->len + 1 > out->room) {
        rw_out_make_room(out, 1, ch);
    }
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    out->data[out->len++] = ch;
}

static void rw_out_run(rw_out *out, const Py_UCS4 *run, Py_ssize_t len) {
    if (out->len + len > out->room) {
        rw_out_make_room(out, len, run[0]);
    }
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
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    if (out->len + len > out->room) {
        rw_out_make_room(out, len, PyUnicode_READ(kind, data, 0));
    }
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: an output allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        out->data[out->len++] = PyUnicode_READ(kind, data, index);
    }
}

/* Append raw text, writing each CR or CRLF as LF: raw text has no character reference for a CR, and a parse folds a
   raw CR to LF, so the LF is what a reparse reads back. */
static void rw_out_raw(rw_out *out, PyObject *str) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(str);
    if (PyUnicode_FindChar(str, '\r', 0, len, 1) < 0) {
        rw_out_str(out, str);
        return;
    }
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch == '\r') {
            rw_out_char(out, '\n');
            if (index + 1 < len && PyUnicode_READ(kind, data, index + 1) == '\n') {
                index++;
            }
        } else {
            rw_out_char(out, ch);
        }
    }
}

/* Append a code-point run, HTML-escaping the markup-significant code points and the CR a parse would fold to LF. */
static void rw_out_escaped(rw_out *out, int kind, const void *data, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (ch == '&') {
            rw_out_ascii(out, "&amp;");
        } else if (ch == '<') {
            rw_out_ascii(out, "&lt;");
        } else if (ch == '>') {
            rw_out_ascii(out, "&gt;");
        } else if (ch == '\r') {
            rw_out_ascii(out, "&#13;");
        } else {
            rw_out_char(out, ch);
        }
    }
}

/* Append a str, HTML-escaping the markup-significant code points, unless raw is set. */
static void rw_out_content(rw_out *out, PyObject *str, int raw) {
    if (raw) {
        rw_out_raw(out, str);
        return;
    }
    rw_out_escaped(out, PyUnicode_KIND(str), PyUnicode_DATA(str), PyUnicode_GET_LENGTH(str));
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
   carries a source span under capture_source, and so does the text of a CDATA section;
   any other text run is a borrowed input slice. Returns 0 when the output could not grow. */
static int rw_emit_verbatim(rw_out *out, const th_tokenizer *sm, const th_token *token) {
    int kind;
    const char *base = th_tok_input_data(sm, &kind);
    if (token->is_slice) {
        if (out->len + token->src_len > out->room) {
            rw_out_make_room(out, token->src_len, PyUnicode_READ(kind, base, token->src_start));
        }
        if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < token->src_len; index++) {
            out->data[out->len++] = PyUnicode_READ(kind, base, token->src_start + index);
        }
        return 1;
    }
    if (token->has_source) {
        if (out->len + token->src_span > out->room) {
            rw_out_make_room(out, token->src_span, PyUnicode_READ(kind, base, token->src_off));
        }
        if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t index = 0; index < token->src_span; index++) {
            out->data[out->len++] = PyUnicode_READ(kind, base, token->src_off + index);
        }
        return 1;
    }
    /* a text/comment run the tokenizer normalized (so it is neither a borrowed slice nor
       a captured span): emit its buffered code points */
    if (out->len + token->text.len > out->room) {
        rw_out_make_room(out, token->text.len, buf_read(&token->text, 0));
    }
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 0;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < token->text.len; index++) {
        out->data[out->len++] = PyUnicode_READ(token->text.kind, token->text.data, index);
    }
    return 1;
}

/* Encode a code-point run to lowercased UTF-8 in buf, which holds four bytes per code point, returning the byte
   length. The one place tag names, attribute names, and selector attribute names all become the interning key. */
static Py_ssize_t rw_encode_utf8(int kind, const void *data, Py_ssize_t len, char *buf) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
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

/* A name's lowercased UTF-8, whole: in small when four bytes per code point fit there, else in a fresh allocation, so
   no name is cut short to fit a buffer. */
#define RW_KEY_SMALL 256
typedef struct {
    char *bytes;
    Py_ssize_t len;
    char small[RW_KEY_SMALL];
} rw_key;

/* Returns 0, or -1 with MemoryError set when the long form cannot be allocated. */
static int rw_key_encode(rw_key *key, int kind, const void *data, Py_ssize_t len) {
    key->bytes = key->small;
    if (4 * len > RW_KEY_SMALL) {
        key->bytes = PyMem_Malloc((size_t)(4 * len));
        if (key->bytes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory();     /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    key->len = rw_encode_utf8(kind, data, len, key->bytes);
    return 0;
}

static void rw_key_free(rw_key *key) {
    if (key->bytes != key->small) {
        PyMem_Free(key->bytes);
    }
}

/* The lowercased UTF-8 of a str name; returns -1 with a TypeError set for a non-str, or MemoryError. */
static int rw_key_str(rw_key *key, PyObject *name) {
    if (!PyUnicode_Check(name)) {
        PyErr_SetString(PyExc_TypeError, "attribute name must be str");
        return -1;
    }
    return rw_key_encode(key, PyUnicode_KIND(name), PyUnicode_DATA(name), PyUnicode_GET_LENGTH(name));
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

/* One open element on the streaming spine, plus the edits its handler recorded. The
   node's only live link is its parent, so the whole stack is O(open-depth). */
typedef struct {
    th_node *node;
    PyObject *append_html;    /* emit just before the end tag (already escaped) */
    PyObject *after_html;     /* emit just after the end tag (already escaped) */
    th_node *shadow;          /* the element of the th_shadow this entry follows, or NULL */
    th_node *mirrored;        /* the SVG/MathML element the mirror opened for the start tag, or NULL */
    Py_ssize_t content_start; /* output offset where the element's content begins */
    Py_ssize_t end_at;        /* set when popped early: output offset the element ended at, or -1 (not in the output) */
    uint32_t line;            /* where the start tag began, which pairs the entry with the th_shadow element */
    uint32_t col;
    uint32_t name_hash;     /* the keyed hash of a custom name (rw_open_name), unused for a known tag */
    int32_t followed;       /* index of the nearest entry from here down that follows one, or -1 */
    uint16_t foreign_open;  /* SVG/MathML entries from here down */
    uint16_t points_open;   /* integration point entries from here down */
    uint8_t edited;         /* an attribute changed: rebuild the start tag instead of verbatim */
    uint8_t reads_attr;     /* an attribute the tree builder reads changed (th_builder_reads_attr) */
    uint8_t drop_content;   /* suppress this element's inner tokens */
    uint8_t drop_end_tag;   /* do not emit the element's end tag */
    uint8_t literal;        /* a handler wrote unescaped text into this raw-text element */
    uint8_t ns;             /* the namespace the tree builder gives the element (the node stays HTML for matching) */
    uint8_t integration;    /* RW_TEXT_INTEGRATION or RW_HTML_INTEGRATION as the start tag read, before any edit */
    int8_t model;           /* the content model the start tag switched to, or -1 */
    Py_ssize_t long_values; /* code points of its values long enough for the value memo (rw_long_values) */
} rw_open;

/* One custom name's count of open elements, keyed by its hash. A name that closed keeps its slot at count 0. */
typedef struct {
    uint32_t hash;
    Py_ssize_t count;
    uint8_t used;
} rw_name_slot;

/* The first slots of the custom-name table sit inside it, so a document with a few custom names allocates nothing for
   them. */
#define RW_NAMES_INLINE 8

/* The custom names the spine has held, counted per keyed hash (core/hash.h) in an open-addressed table with linear
   probing. cap is 0 or a power of two, and at most half the slots are used, so a probe ends at an unused slot. The
   table only grows, as dom/tree.c's attribute-name table does, so a probe never shifts a slot. */
typedef struct {
    rw_name_slot *slots;
    size_t cap;
    size_t used;
    rw_name_slot inline_slots[RW_NAMES_INLINE];
} rw_names;

/* The open elements per name: a known tag atom counts in its array slot, a custom name in the table. */
typedef struct {
    Py_ssize_t atoms[TH_TAG_XMP + 1];
    rw_names names;
} rw_counts;

typedef struct {
    sel_compiled *compiled; /* the selector the matcher runs: source, or its stand-in in deep */
    PyObject *handler;
    sel_compiled *source;
    sel_deep_query *deep; /* NULL until the spine nests deep (rw_go_deep) */
} rw_rule;

/* A spine element an end tag closed, kept until the tag is written to learn whether the output closed it too. */
typedef struct {
    th_node *node;
    th_node *mirrored;
} rw_closed;

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
    /* number of open ancestors suppressing their content, or -1 inside a raw-text or RCDATA element whose tags were
       dropped, so a text token tests one field for both cases */
    Py_ssize_t suppress;
    /* the open elements per name, so an end tag that no open element has skips the spine scan for its match. Built only
       once the stray end tags' scans have walked enough of the spine (rw_note_stray), NULL until then */
    rw_counts *counts;
    Py_ssize_t walked; /* spine elements the stray end tags' scans have walked in this call, while counts is NULL */
    Py_UCS4 *name_buf; /* an end tag's name as code points, for its hash (rw_token_hash) */
    Py_ssize_t name_cap;
    rw_out out;
    PyObject *handle_type;
    PyObject *append_text; /* the handled element's append_html as ordinary text, for when its tags are dropped */
    int error;             /* a Python exception is set; abort the drive */
    int cdata;             /* the tokenizer opens a CDATA section at "<![CDATA[" (rw_sync_cdata) */
    th_shadow *shadow;     /* tree construction run alongside once the spine alone cannot follow it, or NULL */
    th_shadow *parked;     /* that tree construction while the spine follows the input alone again, or NULL */
    th_shadow *mirror;     /* tree construction over the output once an edit can make it parse unlike the input */
    Py_ssize_t mirrored;   /* output code points the mirror has read */
    rw_closed *closing;    /* elements the end tag being handled closed, for rw_mirror_close */
    Py_ssize_t closing_len;
    Py_ssize_t closing_cap;
    /* a handler edit may change how a parse reads the markup after it (see rw_note_element_edits) */
    int edited;
    int changed; /* a handler wrote or dropped markup (rw_note_edit), whether or not a parse can read it differently */
    /* an edit changed an attribute the tree builder reads, so the shadow follows every later tag and never parks */
    int read_changed;
    /* the first edit to the markup, while no tree follows the input (rw_note_edit): 0 none, 1 recorded, 2 followed.
       Its position and output offset decide where the input's body opened in the output (rw_body_at). */
    int track;
    Py_ssize_t edit_line;
    Py_ssize_t edit_col;
    Py_ssize_t edit_at;
    Py_ssize_t body_at;     /* the output offset the input's body opened at, or -1 before it does (rw_note_body) */
    int frameset_hazard;    /* a removed element would have made a parse ignore a later <frameset> */
    Py_ssize_t *attr_index; /* position + 1 per slot, 0 when free; NULL until a by-name call needs it */
    size_t attr_index_cap;  /* power-of-two slot count, at least twice the attribute slots */
    uint64_t attr_seed;     /* salt so chosen attribute names cannot flood one slot */
    Py_ssize_t attr_dead;   /* removed attributes still holding a slot of the element's array */
    /* The selector's answers for long attribute values, kept across start tags: each descendant tests the long values
       of its open ancestors again. The memo keys a value by its buffer, so a freed long buffer waits in the retired
       list (linked through its own first bytes) until the memo is emptied, and no later buffer can take its address
       while an entry names it. The memo is emptied once the retired code points outnumber those still open, so a
       value read again afterwards is paid for by the retired ones and the list never outgrows the open values. */
    sel_memo value_memo;
    void *retired;
    Py_ssize_t retired_len;
    Py_ssize_t open_len;
    uint32_t next_seq; /* the value memo hashes a spine element by its seq */
    /* Some rules run on stand-ins (rw_go_deep), whose memos hold answers for elements by their seq, so a closed element
       waits in the retired list (linked through its parent) until the memos are emptied. The memos are emptied once
       RW_DEEP_RETIRED_MAX elements wait. */
    int deep;
    th_node *retired_nodes;
    Py_ssize_t retired_node_count;
} rw_ctx;

/* The most closed elements the stand-ins' memos may name (see rw_ctx.retired_nodes). Emptying them costs the walks
   that refill them no more than the closed elements already paid for. */
#define RW_DEEP_RETIRED_MAX ((Py_ssize_t)8 * TH_DEPTH_LIMIT(8192))

/* Switch the rules that climb at least this far (sel_compiled.climbs) to stand-ins that keep the answers of their
   ancestor walks: those that climb from their climbs from the start, the others once the spine grows past
   TH_MAX_TREE_DEPTH open elements and a walk can be as long as the spine. Returns -1 on allocation failure. */
static int rw_go_deep(rw_ctx *ctx, int climbs) {
    for (Py_ssize_t index = 0; index < ctx->rule_count; index++) {
        rw_rule *rule = &ctx->rules[index];
        if (rule->source->climbs < climbs || rule->deep != NULL) {
            continue;
        }
        ctx->deep = 1;
        rule->deep = PyMem_Calloc(1, sizeof(sel_deep_query));
        if (rule->deep == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        rule->compiled = sel_deep_wrap(rule->source, rule->deep);
    }
    return 0;
}

/* Empty the stand-ins' memos and free the elements retired while they held entries. */
static void rw_deep_flush(rw_ctx *ctx) {
    for (Py_ssize_t index = 0; index < ctx->rule_count; index++) {
        if (ctx->rules[index].deep != NULL) {
            sel_deep_memo_free(&ctx->rules[index].deep->memo);
        }
    }
    while (ctx->retired_nodes != NULL) {
        th_node *next = ctx->retired_nodes->parent;
        PyMem_Free(ctx->retired_nodes);
        ctx->retired_nodes = next;
    }
    ctx->retired_node_count = 0;
}

/* Empty the value memo and free the long value buffers retired while it held entries. */
static void rw_values_flush(rw_ctx *ctx) {
    sel_memo_free(&ctx->value_memo);
    while (ctx->retired != NULL) {
        void *next;
        memcpy(&next, ctx->retired, sizeof(next));
        PyMem_Free(ctx->retired);
        ctx->retired = next;
    }
    ctx->retired_len = 0;
}

/* Free an attribute value buffer, or retire it while the value memo may key it (see rw_ctx.value_memo). */
static void rw_free_value(rw_ctx *ctx, Py_UCS4 *value, Py_ssize_t value_len) {
    if (value_len < SEL_VALUE_MEMO_MIN || ctx->value_memo.slots == NULL) {
        PyMem_Free(value);
        return;
    }
    memcpy(value, &ctx->retired, sizeof(ctx->retired));
    ctx->retired = value;
    ctx->retired_len += value_len;
    if (ctx->retired_len > ctx->open_len) {
        rw_values_flush(ctx);
    }
}

/* The code points of the element's values that the value memo may key. */
static Py_ssize_t rw_long_values(const th_node *node) {
    Py_ssize_t total = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].value_len >= SEL_VALUE_MEMO_MIN) {
            total += node->attrs[index].value_len;
        }
    }
    return total;
}

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
    int subtree;            /* remove or replace dropped the element with its content, so a prepend goes too */
    PyObject *replace_html; /* replacement for the whole node, or NULL */
    int replace_raw;
    PyObject *before_html; /* emitted before the node (accumulated) */
    PyObject *before_text; /* an element's before_html with its text escaped, for rw_mirror_opened */
    PyObject *after_html;  /* emitted after the node (accumulated) */
    PyObject *set_text;    /* new text/comment body, or NULL */
    int set_text_raw;      /* set_text lands in a raw-text element, so it is written unescaped */
} rw_handle;

/* RAWTEXT, script data and PLAINTEXT content is read as is; RCDATA and ordinary content decode references. */
static int rw_model_is_raw(int model) {
    return model == TH_INIT_RAWTEXT || model == TH_INIT_SCRIPT_DATA || model == TH_INIT_PLAINTEXT;
}

/* Whether a spine element's (lowercased) name is the tag name in buf. */
static int rw_name_is(const th_node *node, const th_buf *name) {
    if (node->text_len != name->len) {
        return 0;
    }
    for (Py_ssize_t pos = 0; pos < node->text_len; pos++) {
        if (node->text[pos] != buf_read(name, pos)) {
            return 0;
        }
    }
    return 1;
}

/* Whether the output reads what is written next as the raw text or RCDATA of an element named as this one, which
   ends at the same end tag. Only while the mirror runs. */
static int rw_output_raw(const rw_ctx *ctx, const rw_open *element) {
    const th_buf *name;
    return th_shadow_mirror_model(ctx->mirror, &name) != TH_INIT_DATA && rw_name_is(element->node, name);
}

/* The lowest spine index an end tag searches. The spine grows with the input, but the search reads only the top
   TH_MAX_TREE_DEPTH elements, so a stray end tag costs the same at any depth, as the depth cap used to bound it. */
static Py_ssize_t rw_window_floor(const rw_ctx *ctx) {
    return ctx->depth > TH_MAX_TREE_DEPTH ? ctx->depth - TH_MAX_TREE_DEPTH : 0;
}

/* Whether the token being handled is the text of a raw-text or RCDATA element whose start tag is in the output. */
static int rw_source_raw(const rw_ctx *ctx) {
    return ctx->depth > 0 && ctx->stack[ctx->depth - 1].model >= 0 && !ctx->stack[ctx->depth - 1].drop_end_tag;
}

/* The raw-text element that text written through this handle lands in: the handled element itself for the calls
   that edit its content, the enclosing element for a text node. A browser reads script, style and the other RAWTEXT
   and PLAINTEXT content without decoding character references, so text there is written unescaped and the element
   is marked for rw_raw_content_closes_early; RCDATA title/textarea decode references and keep escaped text. An
   element whose tags were dropped leaves no raw-text context in the output, and neither does one the output reads
   as markup (see rw_mirror_opened for the handled element's own content), so text there is escaped as anywhere. */
static rw_open *rw_raw_target(rw_handle *self, int inside_element) {
    rw_open *target = NULL;
    if (self->kind == RW_ELEMENT) {
        target = inside_element ? self->open : NULL;
    } else if (self->kind == RW_TEXT && self->ctx->depth > 0) {
        target = &self->ctx->stack[self->ctx->depth - 1];
    }
    if (target == NULL || target->drop_end_tag || !rw_model_is_raw(target->model) ||
        (self->kind == RW_TEXT && self->ctx->mirror != NULL && !rw_output_raw(self->ctx, target))) {
        return NULL;
    }
    target->literal = 1;
    return target;
}

/* Whether written text is unescaped: html=True content always is, text inside a raw-text element is too.
   inside_element picks the handled element's own content over the position around it. */
static int rw_text_is_raw(rw_handle *self, int raw, int inside_element) {
    return raw || rw_raw_target(self, inside_element) != NULL;
}

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

/* Escape (or keep raw) one content argument into a new str, or return the raw ref when it holds no CR to fold. */
static PyObject *rw_escape_arg(PyObject *content, int raw) {
    if (raw && PyUnicode_FindChar(content, '\r', 0, PyUnicode_GET_LENGTH(content), 1) < 0) {
        return Py_NewRef(content);
    }
    rw_out scratch = {.data = NULL};
    rw_out_content(&scratch, content, raw);
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
    int text_raw = rw_text_is_raw(self, raw, 0);
    if (self->kind == RW_ELEMENT) {
        /* an element's before() emits immediately: the start tag has not been written
           yet, so appending now places the content just ahead of it */
        rw_out_content(&self->ctx->out, content, raw);
    } else if (rw_accumulate(&self->before_html, content, text_raw) < 0) { /* GCOVR_EXCL_BR_LINE: alloc-failure only */
        return NULL; /* GCOVR_EXCL_LINE: allocation-failure path */
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
    int text_raw = rw_text_is_raw(self, raw, 0);
    if (rw_accumulate(slot, content, text_raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                                  /* GCOVR_EXCL_LINE: allocation-failure path */
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
    self->subtree = 1;
    self->replace_raw = rw_text_is_raw(self, raw, 0);
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
    self->subtree = 1;
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

/* A handler names one attribute per call, and the markup chooses how many a start tag carries. A call that scanned
   the array, or closed the gap a removed attribute left, would make one tag cost the square of its attribute count.
   So while a handler runs, a removed attribute keeps its slot as a dead one (name_atom TH_ATTR_UNKNOWN, which no name
   resolves to), rw_attrs_settle squeezes the dead slots out, and past RW_ATTR_SCAN slots a name is found through an
   open-addressing table that holds the live attributes by atom. */
#define RW_ATTR_SCAN 16

/* The slot an atom probes from: the salted atom through the splitmix64 finalizer that tokenizer/xml.c hash_mix applies
   to its interned atoms, so the slot bits depend on every bit of the seed and the atom. */
static size_t rw_attr_slot(const rw_ctx *ctx, uint32_t atom) {
    uint64_t hash = ctx->attr_seed ^ atom;
    hash = (hash ^ (hash >> 30)) * UINT64_C(0xBF58476D1CE4E5B9);
    hash = (hash ^ (hash >> 27)) * UINT64_C(0x94D049BB133111EB);
    return (size_t)(hash ^ (hash >> 31)) & (ctx->attr_index_cap - 1);
}

/* The table holds one entry per live attribute, so a probe chain holds only the attributes sharing its slot. It has at
   least two slots per array slot, so every probe ends at a free one. */
static void rw_attr_index_add(rw_ctx *ctx, const th_node *node, Py_ssize_t position) {
    size_t slot = rw_attr_slot(ctx, node->attrs[position].name_atom);
    while (ctx->attr_index[slot] != 0) {
        slot = (slot + 1) & (ctx->attr_index_cap - 1);
    }
    ctx->attr_index[slot] = position + 1;
}

static void rw_attr_index_drop(rw_ctx *ctx) {
    PyMem_Free(ctx->attr_index);
    ctx->attr_index = NULL;
}

/* Index the element's live attributes. A failed allocation leaves no index, and rw_attr_find falls back to its scan. */
static void rw_attr_index_build(rw_ctx *ctx, const th_node *node) {
    size_t cap;
    size_t bytes;
    const int fits = th_grow_cap((size_t)node->attr_count * 2, 0, RW_ATTR_SCAN, sizeof(Py_ssize_t), &cap, &bytes);
    if (!fits) { /* GCOVR_EXCL_BR_LINE: allocation size overflow */
        return;  /* GCOVR_EXCL_LINE */
    }
    ctx->attr_index = PyMem_Calloc(1, bytes);
    if (ctx->attr_index == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ctx->attr_index_cap = cap;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        TH_WORK_STEP();
        if (node->attrs[index].name_atom != TH_ATTR_UNKNOWN) {
            rw_attr_index_add(ctx, node, index);
        }
    }
}

/* Keep the index, when there is one, in step with an attribute appended at position. An array the table is too small
   for drops it, and the next lookup builds one for the longer array. */
static void rw_attr_index_append(rw_ctx *ctx, const th_node *node, Py_ssize_t position) {
    if ((size_t)(position + 1) * 2 > ctx->attr_index_cap) {
        rw_attr_index_drop(ctx);
        return;
    }
    rw_attr_index_add(ctx, node, position);
}

/* The table slot of the element's live attribute with this atom, or SIZE_MAX when there is none. */
static size_t rw_attr_index_probe(const rw_ctx *ctx, const th_node *node, uint32_t atom) {
    for (size_t slot = rw_attr_slot(ctx, atom); ctx->attr_index[slot] != 0;
         slot = (slot + 1) & (ctx->attr_index_cap - 1)) {
        TH_WORK_STEP();
        if (node->attrs[ctx->attr_index[slot] - 1].name_atom == atom) {
            return slot;
        }
    }
    return SIZE_MAX;
}

/* The element's first live attribute with this atom, or NULL. */
static th_node_attr *rw_attr_find(rw_ctx *ctx, const th_node *node, uint32_t atom) {
    if (ctx->attr_index == NULL && node->attr_count > RW_ATTR_SCAN) {
        rw_attr_index_build(ctx, node);
    }
    if (ctx->attr_index == NULL) {
        for (Py_ssize_t index = 0; index < node->attr_count; index++) {
            TH_WORK_STEP();
            if (node->attrs[index].name_atom == atom) {
                return &node->attrs[index];
            }
        }
        return NULL;
    }
    size_t slot = rw_attr_index_probe(ctx, node, atom);
    return slot == SIZE_MAX ? NULL : &node->attrs[ctx->attr_index[slot] - 1];
}

/* Empty the table slot at hole and move the entries behind it back over the gap, so each remaining entry stays
   reachable from its probe start and no tombstone is left. */
static void rw_attr_index_delete(rw_ctx *ctx, const th_node *node, size_t hole) {
    size_t mask = ctx->attr_index_cap - 1;
    for (size_t next = (hole + 1) & mask; ctx->attr_index[next] != 0; next = (next + 1) & mask) {
        TH_WORK_STEP();
        size_t ideal = rw_attr_slot(ctx, node->attrs[ctx->attr_index[next] - 1].name_atom);
        if (((next - ideal) & mask) >= ((next - hole) & mask)) {
            ctx->attr_index[hole] = ctx->attr_index[next];
            hole = next;
        }
    }
    ctx->attr_index[hole] = 0;
}

/* Squeeze the dead slots out of the element's attribute array, keeping the order of the live ones, and drop the index
   of the positions they had. */
static void rw_attrs_settle(rw_ctx *ctx, th_node *node) {
    rw_attr_index_drop(ctx);
    if (ctx->attr_dead == 0) {
        return;
    }
    Py_ssize_t live = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        TH_WORK_STEP();
        if (node->attrs[index].name_atom != TH_ATTR_UNKNOWN) {
            node->attrs[live++] = node->attrs[index];
        }
    }
    node->attr_count = live;
    ctx->attr_dead = 0;
}

/* The atom of a lowercased name in the tree, or UINT32_MAX when neither table holds it. */
static uint32_t rw_lookup_atom(th_tree *tree, const rw_key *key) {
    uint32_t atom = th_attr_atom(key->bytes, (size_t)key->len);
    if (atom == TH_ATTR_UNKNOWN) {
        atom = th_attr_lookup(tree, key->bytes, key->len);
    }
    return atom;
}

/* The element's value for an atom; sets *found. */
static PyObject *rw_attr_value(rw_ctx *ctx, th_node *node, uint32_t atom, int *found) {
    *found = 0;
    if (atom == UINT32_MAX) {
        return NULL;
    }
    const th_node_attr *attr = rw_attr_find(ctx, node, atom);
    if (attr == NULL) {
        return NULL;
    }
    *found = 1;
    if (attr->value == NULL) {
        Py_RETURN_NONE;
    }
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, attr->value, attr->value_len);
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
    PyObject *result = PyTuple_New(node->attr_count - self->ctx->attr_dead);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t live = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        if (node->attrs[index].name_atom == TH_ATTR_UNKNOWN) {
            continue; /* removed by this handler, not yet squeezed out */
        }
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
        PyTuple_SET_ITEM(result, live++, PyTuple_Pack(2, name_obj, value_obj));
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
    rw_key key;
    if (rw_key_str(&key, name) < 0) {
        return NULL;
    }
    uint32_t atom = rw_lookup_atom(self->ctx->tree, &key);
    rw_key_free(&key);
    int found;
    PyObject *value = rw_attr_value(self->ctx, self->open->node, atom, &found);
    if (found) {
        return value;
    }
    return Py_NewRef(fallback);
}

static PyObject *rw_has(rw_handle *self, PyObject *name) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    rw_key key;
    if (rw_key_str(&key, name) < 0) {
        return NULL;
    }
    uint32_t atom = rw_lookup_atom(self->ctx->tree, &key);
    rw_key_free(&key);
    int found;
    PyObject *value = rw_attr_value(self->ctx, self->open->node, atom, &found);
    Py_XDECREF(value);
    return PyBool_FromLong(found);
}

static th_node_attr *rw_attr_grow(th_node *node) {
    size_t capacity = th_node_attr_capacity(node);
    if ((size_t)node->attr_count < capacity) {
        return &node->attrs[node->attr_count];
    }
    size_t bytes;
    const int fits = th_grow_cap((size_t)node->attr_count + 1, capacity, 2, sizeof(th_node_attr), &capacity, &bytes);
    if (!fits) {          /* GCOVR_EXCL_BR_LINE: allocation size overflow */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    th_node_attr *grown = PyMem_Realloc(node->attrs, bytes);
    if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    node->attrs = grown;
    /* th_grow_cap doubles from two slots, so the shift starts at 1 and a new element's first array takes no loop */
    uint8_t shift = 1;
    for (size_t slots = capacity; slots > 2; slots >>= 1) {
        shift++;
    }
    node->attr_capacity_shift = shift;
    return &grown[node->attr_count];
}

/* Copy a str's code points into a fresh UCS4 buffer; *out_len set. NULL with MemoryError set on OOM. */
static Py_UCS4 *rw_copy_ucs4(PyObject *str, Py_ssize_t *out_len) {
    Py_ssize_t len = PyUnicode_GET_LENGTH(str);
    *out_len = len;
    Py_UCS4 *buf = PyMem_Malloc((size_t)(len ? len : 1) * sizeof(Py_UCS4));
    if (buf == NULL) {                   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return (void *)PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(str);
    const void *data = PyUnicode_DATA(str);
    for (Py_ssize_t index = 0; index < len; index++) {
        buf[index] = PyUnicode_READ(kind, data, index);
    }
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
    rw_key key;
    if (rw_key_str(&key, name) < 0) {
        return NULL;
    }
    if (th_validate_markup_name(name, 1) < 0) {
        rw_key_free(&key);
        return NULL;
    }
    uint32_t atom = th_attr_atom(key.bytes, (size_t)key.len);
    int fresh = 0;
    if (atom == TH_ATTR_UNKNOWN) {
        uint32_t count = self->ctx->tree->attr_rec_count;
        atom = intern_attr_dynamic(self->ctx->tree, key.bytes, key.len);
        fresh = self->ctx->tree->attr_rec_count != count;
    }
    if (th_builder_reads_attr(self->open->ns, self->open->node->atom, key.bytes, key.len)) {
        self->open->reads_attr = 1;
    }
    rw_key_free(&key);
    if (atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
        return PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->open->edited = 1;
    th_node *node = self->open->node;
    Py_ssize_t new_len;
    Py_UCS4 *copy = rw_copy_ucs4(value, &new_len);
    if (copy == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_node_attr *slot = fresh ? NULL : rw_attr_find(self->ctx, node, atom);
    if (slot != NULL) {
        rw_free_value(self->ctx, slot->value, slot->value_len);
        slot->value = copy;
        slot->value_len = new_len;
        Py_RETURN_NONE;
    }
    slot = rw_attr_grow(node);
    if (slot == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(copy); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    slot->name_atom = atom;
    slot->value = copy;
    slot->value_len = new_len;
    if (self->ctx->attr_index != NULL) {
        rw_attr_index_append(self->ctx, node, node->attr_count);
    }
    node->attr_count++;
    Py_RETURN_NONE;
}

static PyObject *rw_remove_attribute(rw_handle *self, PyObject *name) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    rw_key key;
    if (rw_key_str(&key, name) < 0) {
        return NULL;
    }
    uint32_t atom = rw_lookup_atom(self->ctx->tree, &key);
    if (th_builder_reads_attr(self->open->ns, self->open->node->atom, key.bytes, key.len)) {
        self->open->reads_attr = 1;
    }
    rw_key_free(&key);
    self->open->edited = 1;
    th_node *node = self->open->node;
    th_node_attr *attr = rw_attr_find(self->ctx, node, atom);
    if (attr != NULL) {
        rw_free_value(self->ctx, attr->value, attr->value_len);
        if (self->ctx->attr_index != NULL) {
            rw_attr_index_delete(self->ctx, node, rw_attr_index_probe(self->ctx, node, atom));
        }
        *attr = (th_node_attr){.name_atom = TH_ATTR_UNKNOWN};
        self->ctx->attr_dead++;
        if (self->ctx->attr_dead * 2 > node->attr_count) {
            rw_attrs_settle(self->ctx, node);
        }
    }
    Py_RETURN_NONE;
}

/* Rebuild the start tag of an edited element from its current name and attributes. Values escape & " < > as the
   default serializer does. */
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
                } else if (ch == '<') {
                    rw_out_ascii(&ctx->out, "&lt;");
                } else if (ch == '>') {
                    rw_out_ascii(&ctx->out, "&gt;");
                } else if (ch == '\r') {
                    rw_out_ascii(&ctx->out, "&#13;");
                } else {
                    rw_out_char(&ctx->out, ch);
                }
            }
            rw_out_char(&ctx->out, '"');
        } else if (index + 1 < node->attr_count) {
            Py_ssize_t next_len;
            /* after a bare name a leading "=" would open its value, so the empty value is written out */
            if (th_attr_name(ctx->tree, node->attrs[index + 1].name_atom, &next_len)[0] == '=') {
                rw_out_ascii(&ctx->out, "=\"\"");
            }
        }
    }
    if (self_closing) {
        rw_out_char(&ctx->out, '/');
    }
    rw_out_char(&ctx->out, '>');
}

/* Text appended to a raw-text element is written unescaped, which is only right between its tags. Keep the same
   content escaped as ordinary text too, for rw_handle_element to use when a handler drops the tags, whichever call
   comes first. Returns 0, or -1 with an exception set. */
static int rw_append_text(rw_handle *self, PyObject *content, int raw) {
    if (!rw_model_is_raw(self->open->model)) {
        return 0;
    }
    return rw_accumulate(&self->ctx->append_text, content, raw);
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
    Py_CLEAR(self->ctx->append_text);
    PyObject *piece = rw_escape_arg(content, rw_text_is_raw(self, raw, 1));
    if (piece == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    self->open->append_html = piece;
    if (rw_append_text(self, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
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
    int text_raw = rw_text_is_raw(self, raw, 1);
    if (rw_accumulate(&self->open->append_html, content, text_raw) < 0) { /* GCOVR_EXCL_BR_LINE: alloc-failure only */
        return NULL;                                                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (rw_append_text(self, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                              /* GCOVR_EXCL_LINE: allocation-failure path */
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
    PyObject *piece = rw_escape_arg(content, rw_text_is_raw(self, raw, 1));
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
    if (!rw_model_is_raw(self->open->model)) {
        Py_RETURN_NONE;
    }
    if (rw_accumulate(&self->before_text, content, raw) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
        return NULL;                                           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_RETURN_NONE;
}

static PyObject *rw_remove_and_keep_content(rw_handle *self, PyObject *Py_UNUSED(ignored)) {
    if (rw_handle_check(self, RW_ELEMENT) < 0) {
        return NULL;
    }
    /* drop the element's own tags: its children stream through, escaped when the element is raw text */
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

/* The HTML tokenizer ends a comment at a leading `>` or `->` and at `-->` or `--!>` anywhere, so the rest would parse
   as markup. Returns where the first such sequence starts (stored in *sequence), or -1 when there is none. */
static Py_ssize_t rw_comment_early_end(PyObject *text, const char **sequence) {
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    Py_ssize_t start = len > 0 && PyUnicode_READ(kind, data, 0) == '-' ? 1 : 0;
    if (start < len && PyUnicode_READ(kind, data, start) == '>') {
        *sequence = start == 0 ? ">" : "->";
        return 0;
    }
    for (Py_ssize_t index = 0; index + 2 < len; index++) {
        if (PyUnicode_READ(kind, data, index) != '-' || PyUnicode_READ(kind, data, index + 1) != '-') {
            continue;
        }
        Py_UCS4 next = PyUnicode_READ(kind, data, index + 2);
        if (next == '>') {
            *sequence = "-->";
            return index;
        }
        if (next == '!' && index + 3 < len && PyUnicode_READ(kind, data, index + 3) == '>') {
            *sequence = "--!>";
            return index;
        }
    }
    return -1;
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
    const char *sequence;
    Py_ssize_t early_end;
    if (self->kind == RW_COMMENT && (early_end = rw_comment_early_end(value, &sequence)) >= 0) {
        PyErr_Format(PyExc_ValueError,
                     "comment text has '%s' at index %zd, which ends an HTML comment early; remove or change it",
                     sequence, early_end);
        return NULL;
    }
    self->set_text_raw = rw_raw_target(self, 0) != NULL;
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
    Py_XDECREF(handle->before_text);
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
        rw_key key;
        const int failed = rw_key_encode(&key, PyUnicode_4BYTE_KIND, simple->name, simple->name_len) < 0;
        if (failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return 0; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        simple->attr_atom = intern_attr_dynamic(tree, key.bytes, key.len);
        rw_key_free(&key);
        /* a failed interning leaves no reason, which the caller reports as MemoryError */
        return simple->attr_atom != TH_ATTR_UNKNOWN; /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
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
static void rw_free_node(rw_ctx *ctx, th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        rw_free_value(ctx, node->attrs[index].value, node->attrs[index].value_len);
    }
    PyMem_Free(node->attrs);
    PyMem_Free(node->text);
    if (!ctx->deep) {
        PyMem_Free(node);
        return;
    }
    node->parent = ctx->retired_nodes;
    ctx->retired_nodes = node;
    if (++ctx->retired_node_count > RW_DEEP_RETIRED_MAX) {
        rw_deep_flush(ctx);
    }
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
    node->seq = ++ctx->next_seq;
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
            rw_key key;
            const int failed = rw_key_encode(&key, attr->name.kind, attr->name.data, attr->name.len) < 0;
            if (failed) {                 /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                node->attr_count = index; /* GCOVR_EXCL_LINE: allocation-failure path */
                rw_free_node(ctx, node);  /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;              /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            uint32_t name_atom = rw_attr_atom(ctx->tree, key.bytes, key.len);
            rw_key_free(&key);
            if (name_atom == TH_ATTR_UNKNOWN) { /* GCOVR_EXCL_BR_LINE: interning fails only to allocate */
                node->attr_count = index;       /* GCOVR_EXCL_LINE: allocation-failure path */
                rw_free_node(ctx, node);        /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            node->attrs[index].name_atom = name_atom;
            if (attr->has_value) {
                node->attrs[index].value_len = attr->value.len;
                node->attrs[index].value =
                    PyMem_Malloc((size_t)(attr->value.len ? attr->value.len : 1) * sizeof(Py_UCS4));
                if (node->attrs[index].value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure only */
                    node->attr_count = index;           /* GCOVR_EXCL_LINE: allocation-failure path */
                    rw_free_node(ctx, node);            /* GCOVR_EXCL_LINE: allocation-failure path */
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
    handle->set_text_raw = 0;
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

/* A browser switches the tokenizer's content model only for a start tag its HTML rules process. Inside <svg> or <math>
   the foreign-content rules insert <title>, <style>, <xmp>, <script>, ... as ordinary elements and the tokenizer stays
   in the data state, so their content is markup; "/>" closes a start tag only there, and "<![CDATA[" opens a section
   only there. The tree builder decides all of this from its stack of open elements, its insertion mode and its list
   of active formatting elements. The spine follows it alone while every tag inside <svg> or <math> is one whose
   handling needs none of that state: an SVG/MathML start tag, the end tag of an open SVG/MathML element, and an <svg>
   or <math> inside an integration point. Any other tag there (an HTML start tag, an end tag that reaches past the
   SVG/MathML elements, a start tag inside an integration point), and a <template> or <frameset> start tag anywhere,
   hand the rest of the document to a th_shadow: the tree builder replays the input before that tag once, then runs on
   each token the rewriter reads and steers the tokenizer itself. A spine node keeps TH_NS_HTML so selectors match it
   as before; the namespace lives on the rw_open. */

#define RW_TEXT_INTEGRATION 1
#define RW_HTML_INTEGRATION 2

/* ASCII case-insensitive match of an attribute value against a lowercase ASCII string. */
static int rw_value_ci_eq(const th_node_attr *attr, const char *target) {
    return attr->value_len == (Py_ssize_t)strlen(target) && starts_with_ascii_ci(attr->value, attr->value_len, target);
}

/* Which integration point a new SVG/MathML element is, if any (is_mathml_text_integration and is_html_integration in
   dom/foreign.h): inside one the HTML rules take the start tags. Read before a handler can edit the attributes, as the
   tree builder reads the start tag. */
static uint8_t rw_integration(uint8_t ns, const th_node *node) {
    switch (node->atom) {
    case TH_TAG_FOREIGNOBJECT:
    case TH_TAG_DESC:
    case TH_TAG_TITLE:
        return ns == TH_NS_SVG ? RW_HTML_INTEGRATION : 0;
    case TH_TAG_MI:
    case TH_TAG_MO:
    case TH_TAG_MN:
    case TH_TAG_MS:
    case TH_TAG_MTEXT:
        return ns == TH_NS_MATHML ? RW_TEXT_INTEGRATION : 0;
    case TH_TAG_ANNOTATION_XML:
        for (Py_ssize_t index = 0; ns == TH_NS_MATHML && index < node->attr_count; index++) {
            const th_node_attr *attr = &node->attrs[index];
            if (attr->name_atom == TH_ATTR_ENCODING && attr->value != NULL &&
                (rw_value_ci_eq(attr, "text/html") || rw_value_ci_eq(attr, "application/xhtml+xml"))) {
                return RW_HTML_INTEGRATION;
            }
        }
        return 0;
    default:
        return 0;
    }
}

/* Whether an attribute name (lowercased) is one of color, face and size, which make a <font> start tag break out of
   foreign content. */
static int rw_font_breakout_attr(const char *name, Py_ssize_t len) {
    static const char *const presentational[] = {"color", "face", "size"};
    for (size_t which = 0; which < sizeof(presentational) / sizeof(presentational[0]); which++) {
        if (len == (Py_ssize_t)strlen(presentational[which]) && memcmp(name, presentational[which], (size_t)len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Whether a <font> start tag still breaks out of foreign content as a handler left it: only with color, face or size,
   so removing the last of them turns the tag into one that no longer closes the foreign elements. */
static int rw_font_breakout(rw_ctx *ctx, const th_node *node) {
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t len;
        const char *name = th_attr_name(ctx->tree, node->attrs[index].name_atom, &len);
        if (rw_font_breakout_attr(name, len)) {
            return 1;
        }
    }
    return 0;
}

/* Write the end tag of an open element at `at`, which has room for the text_len + 3 code points it takes. */
static void rw_end_tag_at(Py_UCS4 *at, const th_node *node) {
    at[0] = '<';
    at[1] = '/';
    memcpy(at + 2, node->text, (size_t)node->text_len * sizeof(Py_UCS4));
    at[node->text_len + 2] = '>';
}

/* Append the end tag of an open element. */
static void rw_emit_end_tag(rw_out *out, const th_node *node) {
    rw_out_ascii(out, "</");
    rw_out_run(out, node->text, node->text_len);
    rw_out_char(out, '>');
}

/* Whether the spine's innermost element is an SVG/MathML element: until a th_shadow runs, exactly when the tree
   builder's current node is one. */
static int rw_spine_foreign(const rw_ctx *ctx) {
    return ctx->depth > 0 && ctx->stack[ctx->depth - 1].ns != TH_NS_HTML;
}

/* Whether any handler is registered: without one the output is the input, so a misread of it changes nothing. */
/* The spine and the th_shadow read the input; the output is the input with the handlers' edits, and a browser parses
   the output. Edits to attributes, to comments and to the text of a raw-text element leave every later token read as
   in the input, as does removing a raw-text element. Dropping any other tag or writing anywhere else can change it:
   without a <desc> its <title> child is an SVG element whose text is markup, without a <font> start tag the </font>
   no longer ends an <svg>, and a removed <img> moves where a formatting element is rebuilt and so what a later end tag
   closes. Only SVG/MathML content, templates and framesets turn such a difference into tokens read differently, so
   once such an edit is made the first of their tags (or the edit itself, inside one) starts the mirror: tree
   construction over the output written so far, then over what is written as it is written. Each token is written so
   that the output reads it as the input did: raw text only the output opened gets its end tag first, the text of a
   raw-text element the output reads as markup is escaped, a CDATA section where the output opens none becomes text
   and a bogus comment where it opens one becomes a comment, and an SVG/MathML element an end tag closed in the input
   but not in the output gets its own end tag. */

static th_node **rw_mirror_refs(const rw_ctx *ctx) {
    return ctx->stack != NULL ? &ctx->stack[0].mirrored : NULL;
}

/* Feed the mirror what was written since it last read. compact lets it copy its tree, which moves the spine's
   mirrored pointers and invalidates every other pointer into it. */
static void rw_mirror_sync(rw_ctx *ctx, int compact) {
    /* a pending tail may still be escaped, so the mirror reads it once it settles */
    Py_ssize_t end = ctx->out.tail > 0 ? ctx->out.tail - 1 : ctx->out.len;
    if (ctx->mirrored == end) {
        return;
    }
    Py_ssize_t count = compact ? ctx->depth : -1;
    /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
    if (th_shadow_mirror_feed(ctx->mirror, ctx->out.data + ctx->mirrored, end - ctx->mirrored, rw_mirror_refs(ctx),
                              count, sizeof(rw_open)) < 0) {
        /* GCOVR_EXCL_START: allocation-failure path */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    /* GCOVR_EXCL_BR_STOP */
    ctx->mirrored = end;
}

static TH_NOINLINE void rw_mirror_start(rw_ctx *ctx) {
    ctx->mirror = th_shadow_mirror_new();
    if (ctx->mirror == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        /* GCOVR_EXCL_START: allocation-failure path */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
}

static th_node *rw_mirror_current(const rw_ctx *ctx) {
    Py_ssize_t count;
    th_node *const *open = th_shadow_open(ctx->mirror, &count);
    return count > 0 ? open[count - 1] : NULL;
}

static int rw_buf_eq(const th_buf *left, const th_buf *right) {
    if (left->len != right->len) {
        return 0;
    }
    for (Py_ssize_t pos = 0; pos < left->len; pos++) {
        if (buf_read(left, pos) != buf_read(right, pos)) {
            return 0;
        }
    }
    return 1;
}

/* Before a token: raw text the output reads where the input reads markup would swallow the token, so it gets its end
   tag first. The text of the raw-text element the input reads it in, and the end tag leaving it, read on as they do
   in the input; PLAINTEXT has no end tag and swallows anything. */
static TH_NOINLINE void rw_mirror_before(rw_ctx *ctx, const th_token *token) {
    rw_mirror_sync(ctx, 1);
    const th_buf *name;
    enum th_initial_state model = th_shadow_mirror_model(ctx->mirror, &name);
    if (model == TH_INIT_DATA || model == TH_INIT_PLAINTEXT) {
        return;
    }
    if (token->kind == TH_END_TAG ? rw_buf_eq(&token->name, name)
                                  : (token->kind == TH_TEXT || token->kind == TH_CHARREF) && rw_source_raw(ctx) &&
                                        rw_name_is(ctx->stack[ctx->depth - 1].node, name)) {
        return;
    }
    rw_out_ascii(&ctx->out, "</");
    for (Py_ssize_t pos = 0; pos < name->len; pos++) {
        rw_out_char(&ctx->out, buf_read(name, pos));
    }
    rw_out_char(&ctx->out, '>');
    rw_mirror_sync(ctx, 1);
}

/* How a leaf token is written while the mirror runs (rw_emit_leaf). */
enum rw_leaf_form { RW_AS_READ, RW_AS_TEXT, RW_AS_COMMENT };

/* Whether a comment token is a bogus comment "<![CDATA[...>", which opens a CDATA section where the output's current
   node is foreign. */
static int rw_opens_cdata(const rw_ctx *ctx, const th_token *token) {
    static const char opener[] = "<![CDATA[";
    if (token->src_span < (Py_ssize_t)sizeof(opener) - 1) {
        return 0;
    }
    int kind;
    const void *data = th_tok_input_data(ctx->sm, &kind);
    for (size_t pos = 0; pos < sizeof(opener) - 1; pos++) {
        if (PyUnicode_READ(kind, data, token->src_off + (Py_ssize_t)pos) != (Py_UCS4)opener[pos]) {
            return 0;
        }
    }
    return 1;
}

/* The form a leaf token takes in the output so the output reads it as the input did. */
static TH_NOINLINE enum rw_leaf_form rw_mirror_reading(rw_ctx *ctx, const th_token *token) {
    rw_mirror_sync(ctx, 0); /* after what a handler wrote before the token */
    if (token->kind == TH_COMMENT) {
        return th_shadow_foreign(ctx->mirror) && rw_opens_cdata(ctx, token) ? RW_AS_COMMENT : RW_AS_READ;
    }
    if (token->kind == TH_TEXT && token->has_source) { /* a CDATA section, copied with its delimiters */
        return th_shadow_foreign(ctx->mirror) ? RW_AS_READ : RW_AS_TEXT;
    }
    return rw_source_raw(ctx) && !rw_output_raw(ctx, &ctx->stack[ctx->depth - 1]) ? RW_AS_TEXT : RW_AS_READ;
}

/* After an end tag is written: the SVG/MathML elements it closed in the input that the output still has open get
   their end tags, innermost first. */
static TH_NOINLINE void rw_mirror_close(rw_ctx *ctx) {
    rw_mirror_sync(ctx, 0);
    for (Py_ssize_t index = ctx->closing_len - 1; index >= 0; index--) {
        if (!ctx->closing[index].mirrored->left_stack) {
            rw_emit_end_tag(&ctx->out, ctx->closing[index].node);
            rw_mirror_sync(ctx, 0);
        }
        rw_free_node(ctx, ctx->closing[index].node);
    }
    ctx->closing_len = 0;
}

/* Hold a closed spine element for rw_mirror_close; frees it instead when it cannot grow the list. */
static void rw_mirror_hold(rw_ctx *ctx, const rw_open *open) {
    if (ctx->closing_len == ctx->closing_cap) {
        size_t cap = 0;
        size_t bytes = 0;
        /* the spine depth bounds the list, so the size never overflows */
        th_grow_cap((size_t)(ctx->closing_len + 1), (size_t)ctx->closing_cap, 8, sizeof(rw_closed), &cap, &bytes);
        rw_closed *grown = PyMem_Realloc(ctx->closing, bytes);
        if (grown == NULL) {               /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->error = 1;                /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();              /* GCOVR_EXCL_LINE: allocation-failure path */
            rw_free_node(ctx, open->node); /* GCOVR_EXCL_LINE: allocation-failure path */
            return;                        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->closing = grown;
        ctx->closing_cap = (Py_ssize_t)cap;
    }
    ctx->closing[ctx->closing_len++] = (rw_closed){.node = open->node, .mirrored = open->mirrored};
}

/* --- the open elements per name --- */

/* The slot a hash names, or the unused slot it would take. */
static size_t rw_names_slot(const rw_names *names, uint32_t hash) {
    size_t mask = names->cap - 1;
    for (size_t slot = hash & mask;; slot = (slot + 1) & mask) {
        TH_WORK_STEP();
        if (!names->slots[slot].used || names->slots[slot].hash == hash) {
            return slot;
        }
    }
}

static Py_ssize_t rw_names_count(const rw_names *names, uint32_t hash) {
    return names->cap == 0 ? 0 : names->slots[rw_names_slot(names, hash)].count;
}

static void rw_names_free(rw_names *names) {
    if (names->slots != names->inline_slots) {
        PyMem_Free(names->slots);
    }
}

/* Start the table in its inline slots, or double it, and move the names it holds into it. Returns -1 on allocation
   failure. */
static int rw_names_grow(rw_names *names) {
    size_t cap = names->cap == 0 ? RW_NAMES_INLINE : names->cap * 2;
    rw_name_slot *slots = names->cap == 0 ? names->inline_slots : PyMem_Calloc(cap, sizeof(rw_name_slot));
    if (slots == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    rw_names grown = {.slots = slots, .cap = cap};
    for (size_t index = 0; index < names->cap; index++) {
        if (names->slots[index].used) {
            grown.slots[rw_names_slot(&grown, names->slots[index].hash)] = names->slots[index];
            grown.used++;
        }
    }
    rw_names_free(names);
    names->slots = slots;
    names->cap = cap;
    names->used = grown.used;
    return 0;
}

/* Count one more open element under a hash. Returns -1 on allocation failure. */
static int rw_names_add(rw_names *names, uint32_t hash) {
    if ((names->used + 1) * 2 > names->cap && rw_names_grow(names) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                                        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    size_t slot = rw_names_slot(names, hash);
    if (!names->slots[slot].used) {
        names->slots[slot].used = 1;
        names->slots[slot].hash = hash;
        names->used++;
    }
    names->slots[slot].count++;
    return 0;
}

static void rw_names_release(rw_names *names, uint32_t hash) {
    names->slots[rw_names_slot(names, hash)].count--;
}

/* Count an element pushed on the spine under its known tag atom, or under the keyed hash of its custom name, once the
   counts exist. Returns -1 on allocation failure. */
static int rw_open_name(rw_ctx *ctx, rw_open *open) {
    if (ctx->counts == NULL) {
        return 0;
    }
    if (open->node->atom != TH_TAG_UNKNOWN) {
        ctx->counts->atoms[open->node->atom]++;
        return 0;
    }
    open->name_hash = th_name_hash(open->node->text, open->node->text_len * (Py_ssize_t)sizeof(Py_UCS4));
    return rw_names_add(&ctx->counts->names, open->name_hash);
}

static void rw_close_name(rw_ctx *ctx, const rw_open *open) {
    if (ctx->counts == NULL) {
        return;
    }
    if (open->node->atom != TH_TAG_UNKNOWN) {
        ctx->counts->atoms[open->node->atom]--;
        return;
    }
    rw_names_release(&ctx->counts->names, open->name_hash);
}

static void rw_counts_free(rw_ctx *ctx) {
    if (ctx->counts != NULL) {
        rw_names_free(&ctx->counts->names);
        PyMem_Free(ctx->counts);
        ctx->counts = NULL;
    }
}

/* Build the counts from the spine as it stands, so every element on it is counted from here on. Returns -1 with an
   error set on allocation failure. */
static int rw_counts_build(rw_ctx *ctx) {
    ctx->counts = PyMem_Calloc(1, sizeof(rw_counts));
    if (ctx->counts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->error = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();      /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;             /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < ctx->depth; index++) {
        if (rw_open_name(ctx, &ctx->stack[index]) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            rw_counts_free(ctx);                         /* GCOVR_EXCL_LINE: allocation-failure path */
            ctx->error = 1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();                            /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    return 0;
}

/* A stray end tag walked the whole spine without a match. Walks add up over the call, and once they outweigh the spine
   several times over, the counts are built so later stray end tags skip the walk. Returns -1 with an error set on
   allocation failure. */
static int rw_note_stray(rw_ctx *ctx) {
    ctx->walked += ctx->depth;
    if (ctx->walked <= 4 * ctx->depth + 64) {
        return 0;
    }
    return rw_counts_build(ctx);
}

static int rw_end_names(const th_node *node, const th_token *token, uint16_t atom);

/* The keyed hash of an end tag's custom name: the hash rw_open_name gives the same code points on the node. Returns -1
   with MemoryError set on allocation failure. */
static int rw_token_hash(rw_ctx *ctx, const th_buf *name, uint32_t *hash) {
    if (name->len > ctx->name_cap) {
        Py_UCS4 *grown = PyMem_Realloc(ctx->name_buf, (size_t)name->len * sizeof(Py_UCS4));
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ctx->name_buf = grown;
        ctx->name_cap = name->len;
    }
    for (Py_ssize_t index = 0; index < name->len; index++) {
        ctx->name_buf[index] = buf_read(name, index);
    }
    *hash = th_name_hash(ctx->name_buf, name->len * (Py_ssize_t)sizeof(Py_UCS4));
    return 0;
}

/* Whether an open element has an end tag's name, so the scan of the spine for its match can find one. Needs the counts.
   A custom name compares by hash, so a collision costs a scan and never a missed match. Returns -1 with MemoryError set
   on allocation failure. */
static int rw_end_name_open(rw_ctx *ctx, const th_token *token, uint16_t atom) {
    /* the innermost element is the usual match, so its name is compared before any count or hash is taken */
    if (ctx->depth > 0 && rw_end_names(ctx->stack[ctx->depth - 1].node, token, atom)) {
        return 1;
    }
    if (atom != TH_TAG_UNKNOWN) {
        return ctx->counts->atoms[atom] > 0;
    }
    uint32_t hash;
    if (rw_token_hash(ctx, &token->name, &hash) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return rw_names_count(&ctx->counts->names, hash) > 0;
}

/* Close the open elements above the first `keep`, which a tag ends in a browser with no end tag of their own, so
   their trailing edits are flushed here, where they end. Returns how many it closed: their entries stay in the slots
   above the new depth, each with the output offset it ended at, until the caller knows what becomes of the tag and
   hands them to rw_release_foreign. */
static Py_ssize_t rw_pop_to(rw_ctx *ctx, Py_ssize_t keep) {
    Py_ssize_t closed = ctx->depth - keep;
    while (ctx->depth > keep) {
        rw_open *open = &ctx->stack[--ctx->depth];
        rw_close_name(ctx, open);
        if (open->drop_content) {
            ctx->suppress--;
        }
        int emit = ctx->suppress == 0;
        if (emit && open->append_html != NULL) {
            rw_out_str(&ctx->out, open->append_html);
        }
        /* only an element whose start tag was written is open in the output */
        open->end_at = emit && !open->drop_end_tag ? ctx->out.len : -1;
        if (emit && open->after_html != NULL) {
            rw_out_str(&ctx->out, open->after_html);
        }
        Py_XDECREF(open->append_html);
        Py_XDECREF(open->after_html);
    }
    return closed;
}

/* Whether an element rw_pop_to closed under a dropped tag gets an end tag of its own. Under a dropped end tag an HTML
   element gets none: the output has no end tag to close it, so it stays open there as the output's own formatting stack
   carries it, as it does when the end tag is not dropped. */
static int rw_releases_end_tag(const rw_open *open, int end_tag) {
    return open->end_at >= 0 && !(end_tag && open->ns == TH_NS_HTML);
}

/* Let go of the elements rw_pop_to closed. The tag that closed them ends them in a browser only while it is in the
   output: once a handler drops it they stay open there, and the <title>, <style>, <xmp>, ... after it, which the
   tokenizer rightly read as HTML raw text, are SVG/MathML elements whose content is markup. `dropped` says the tag is
   not written (or no longer breaks out), so each element gets the end tag the tag stood for, at the offset the
   element ended at: ahead of its own after() content and of whatever the dropped tag's handler wrote before it.
   `hold` says the tag is an end tag the output keeps while the mirror runs, which may still close fewer elements
   there: the elements the mirror paired go to rw_mirror_close instead of being freed. */
static void rw_release_foreign(rw_ctx *ctx, Py_ssize_t closed, int dropped, int hold, int end_tag) {
    if (closed == 0) {
        return;
    }
    rw_open *popped = ctx->stack + ctx->depth; /* outermost first, so by descending end_at */
    rw_out *out = &ctx->out;
    Py_ssize_t extra = 0;
    for (Py_ssize_t index = 0; dropped && index < closed; index++) {
        if (rw_releases_end_tag(&popped[index], end_tag)) {
            extra += popped[index].node->text_len + 3;
        }
    }
    if (out->len + extra > out->room) {
        rw_out_make_room(out, extra, '<');
    }
    if (extra > 0 && !out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        /* one pass from the back: each stretch of output moves once, past the end tags that go in before it */
        Py_ssize_t tail = out->len;
        out->len += extra;
        for (Py_ssize_t index = 0; index < closed; index++) {
            rw_open *open = &popped[index];
            if (!rw_releases_end_tag(open, end_tag)) {
                continue;
            }
            if (open->end_at + 1 == out->moved) { /* it ended after a tail escaped since */
                open->end_at += 3;
            }
            memmove(out->data + open->end_at + extra, out->data + open->end_at,
                    (size_t)(tail - open->end_at) * sizeof(Py_UCS4));
            extra -= open->node->text_len + 3;
            rw_end_tag_at(out->data + open->end_at + extra, open->node);
            tail = open->end_at;
        }
    }
    for (Py_ssize_t index = 0; index < closed; index++) {
        ctx->open_len -= popped[index].long_values;
        if (hold && popped[index].end_at >= 0 && popped[index].mirrored != NULL) {
            rw_mirror_hold(ctx, &popped[index]);
        } else {
            rw_free_node(ctx, popped[index].node);
        }
    }
}

/* The namespace an <svg> or <math> start tag opens under the HTML rules. */
static uint8_t rw_opened_ns(uint16_t atom) {
    return atom == TH_TAG_SVG ? TH_NS_SVG : TH_NS_MATHML;
}

/* The start tags the spine cannot read without the foreign-content rules or the tree builder: the two that open
   foreign content, and the two whose insertion modes ignore start tags a browser then never switches the tokenizer
   for (a <textarea> after <template><col>, a <title> inside <frameset>). */
static int rw_watched(uint16_t atom) {
    return atom == TH_TAG_SVG || atom == TH_TAG_MATH || atom == TH_TAG_TEMPLATE || atom == TH_TAG_FRAMESET;
}

/* "<![CDATA[" opens a section only while the tree builder's current node is foreign. Until a th_shadow runs only a
   foreign tag can change that; after, th_shadow_step tells the tokenizer. */
static TH_NOINLINE void rw_sync_cdata(rw_ctx *ctx) {
    int cdata = rw_spine_foreign(ctx);
    if (ctx->shadow == NULL && cdata != ctx->cdata) {
        th_tok_set_cdata(ctx->sm, cdata);
        ctx->cdata = cdata;
    }
}

/* Whether a start tag needs tree builder state the spine does not keep (see above). The spine's current node is the
   tree builder's here. Outside foreign content that is <template> and <frameset>; inside it, a tag that breaks out
   and, inside an integration point, any tag the HTML rules take but an <svg> or <math>, which they insert as they
   would anywhere. */
static int rw_start_needs_shadow(const rw_ctx *ctx, const th_token *token, uint16_t atom) {
    if (!rw_spine_foreign(ctx)) {
        return atom == TH_TAG_TEMPLATE || atom == TH_TAG_FRAMESET;
    }
    int opens_foreign = atom == TH_TAG_SVG || atom == TH_TAG_MATH;
    switch (ctx->stack[ctx->depth - 1].integration) {
    case RW_TEXT_INTEGRATION:
        return !opens_foreign && atom != TH_TAG_MGLYPH && atom != TH_TAG_MALIGNMARK;
    case RW_HTML_INTEGRATION:
        return !opens_foreign;
    default:
        return is_foreign_breakout(atom) ||
               (atom == TH_TAG_FONT &&
                (token_has_attr(token, "color") || token_has_attr(token, "face") || token_has_attr(token, "size")));
    }
}

/* The namespace of a start tag that rw_start_needs_shadow lets the spine read: the foreign-content rules keep the
   current node's, and the HTML rules give <svg> and <math> their own, inside an integration point or annotation-xml.
   Outside foreign content that leaves only <svg> and <math>. */
static uint8_t rw_start_ns(const rw_ctx *ctx, uint16_t atom) {
    if (!rw_spine_foreign(ctx)) {
        return rw_opened_ns(atom);
    }
    const rw_open *current = &ctx->stack[ctx->depth - 1];
    int html_rules = (atom == TH_TAG_SVG || atom == TH_TAG_MATH) &&
                     (current->integration != 0 || current->node->atom == TH_TAG_ANNOTATION_XML);
    return html_rules ? rw_opened_ns(atom) : current->ns;
}

/* The spine entry whose start tag began at line:col, or -1; the spine holds its entries in source order. */
static Py_ssize_t rw_entry_at(const rw_ctx *ctx, Py_ssize_t line, Py_ssize_t col) {
    Py_ssize_t low = 0;
    Py_ssize_t high = ctx->depth;
    while (low < high) {
        Py_ssize_t mid = low + (high - low) / 2;
        const rw_open *entry = &ctx->stack[mid];
        if (entry->line < line || (entry->line == line && entry->col < col)) {
            low = mid + 1;
        } else {
            high = mid;
        }
    }
    return low < ctx->depth && ctx->stack[low].line == line && ctx->stack[low].col == col ? low : -1;
}

/* Hand the rest of the document to a th_shadow at `token`, which it has not seen: the tree builder replays the input
   before the tag, and each element on the spine it still has open is paired with its own by where the start tag
   began. The spine keeps the elements the tree builder closed without an end tag, or never opened, which stay
   unpaired. Returns 0, or -1 with MemoryError set. */
/* The element pointers of the spine, for th_shadow_compact to move. */
static th_node **rw_refs(const rw_ctx *ctx) {
    return ctx->stack != NULL ? &ctx->stack[0].shadow : NULL;
}

/* The input offset of the code point at line and column, counting line breaks as the tokenizer does. Plain text has no
   source span, so its offset is found this way. */
static Py_ssize_t rw_input_offset(const rw_ctx *ctx, Py_ssize_t line, Py_ssize_t col) {
    int kind;
    const void *data = th_tok_input_data(ctx->sm, &kind);
    Py_ssize_t index = 0;
    Py_ssize_t at_line = 1;
    Py_ssize_t at_col = 0;
    while (at_line != line || at_col != col) {
        if (PyUnicode_READ(kind, data, index) == '\n') {
            at_line++;
            at_col = 0;
        } else {
            at_col++;
        }
        index++;
    }
    return index;
}

/* The output offset of the body the input opened at line and column. Tokens before the first edit read as they did in
   the input, so their output offsets are their input offsets. The edit's own token sits at edit_line:edit_col, where
   the output offset is edit_at. */
static Py_ssize_t rw_body_at(const rw_ctx *ctx, Py_ssize_t line, Py_ssize_t col) {
    if (line == ctx->edit_line && col == ctx->edit_col) {
        return ctx->edit_at;
    }
    return rw_input_offset(ctx, line, col);
}

/* Record the output offset of the token that opened the body, the first time the tree has one. */
static void rw_note_body(rw_ctx *ctx, Py_ssize_t at) {
    Py_ssize_t line;
    Py_ssize_t col;
    if (ctx->body_at < 0 && ctx->shadow != NULL && th_shadow_body(ctx->shadow, &line, &col)) {
        ctx->body_at = at;
    }
}

/* Pair each element the tree has open with the spine entry it began as, and give each entry the nearest entry from it
   down that the tree paired. */
static void rw_pair_tree(rw_ctx *ctx) {
    Py_ssize_t count;
    th_node *const *open = th_shadow_open(ctx->shadow, &count);
    /* only the top TH_MAX_TREE_DEPTH open elements are paired, so a pairing costs the same at any depth */
    for (Py_ssize_t index = count > TH_MAX_TREE_DEPTH ? count - TH_MAX_TREE_DEPTH : 0; index < count; index++) {
        TH_WORK_STEP();
        /* an element the tree builder made up (html, a body, a tbody, a formatting element's clone) is at line 0 */
        Py_ssize_t at = rw_entry_at(ctx, node_pos(open[index])[0], node_pos(open[index])[1]);
        if (at >= 0) {
            ctx->stack[at].shadow = open[index];
        }
    }
    /* the entries below the window keep the links they had when they were in it */
    for (Py_ssize_t index = rw_window_floor(ctx); index < ctx->depth; index++) {
        TH_WORK_STEP();
        rw_open *entry = &ctx->stack[index];
        entry->followed = (int32_t)(entry->shadow != NULL ? index : index > 0 ? ctx->stack[index - 1].followed : -1);
    }
}

static int rw_follow_tree(rw_ctx *ctx, Py_ssize_t offset) {
    ctx->shadow = ctx->parked != NULL ? ctx->parked : th_shadow_new(ctx->sm);
    ctx->parked = NULL;
    if (ctx->shadow == NULL || /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        th_shadow_catch_up(ctx->shadow, offset, rw_refs(ctx), ctx->depth, sizeof(rw_open)) < 0) {
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t line;
    Py_ssize_t col;
    if (ctx->body_at < 0 && th_shadow_body(ctx->shadow, &line, &col)) {
        ctx->body_at = rw_body_at(ctx, line, col);
    }
    rw_pair_tree(ctx);
    return 0;
}

/* Start following the input at the token that follows the first edit recorded by rw_note_edit. The tree replays from
   the beginning and stops once it has a body, parked there, so a document whose body opens early costs little. A body
   the replay opened is placed by rw_body_at. A tree with no body yet keeps following the input from here. */
static TH_NOINLINE void rw_start_following(rw_ctx *ctx, const th_token *token) {
    ctx->track = 2;
    Py_ssize_t offset = rw_input_offset(ctx, token->line, token->col);
    if (ctx->read_changed) {
        rw_follow_tree(ctx, offset);
        return;
    }
    th_shadow *shadow = th_shadow_new(ctx->sm);
    if (shadow == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int stopped = th_shadow_catch_body(shadow, offset, rw_refs(ctx), ctx->depth, sizeof(rw_open));
    if (stopped < 0) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        ctx->error = 1;         /* GCOVR_EXCL_LINE: allocation-failure path */
        th_shadow_free(shadow); /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();       /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                 /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (!stopped) {
        ctx->shadow = shadow;
        rw_pair_tree(ctx);
        return;
    }
    Py_ssize_t line;
    Py_ssize_t col;
    th_shadow_body(shadow, &line, &col);
    ctx->body_at = rw_body_at(ctx, line, col);
    if (!th_shadow_parkable(shadow)) {
        th_shadow_free(shadow);
        rw_follow_tree(ctx, offset);
        return;
    }
    ctx->parked = shadow;
    ctx->cdata = rw_spine_foreign(ctx);
    th_tok_set_cdata(ctx->sm, ctx->cdata);
}

/* Record the first edit that can change the output's markup, while no tree follows the input: the position of its
   token, from which rw_start_following follows. `at` is the output offset the token's output starts at. */
static void rw_note_edit(rw_ctx *ctx, const th_token *token, Py_ssize_t at) {
    ctx->changed = 1;
    if (ctx->track == 0 && ctx->shadow == NULL && ctx->parked == NULL) {
        ctx->track = 1;
        ctx->edit_line = token->line;
        ctx->edit_col = token->col;
        ctx->edit_at = at;
    }
}

static th_node *rw_shadow_current(const rw_ctx *ctx) {
    Py_ssize_t count;
    th_node *const *open = th_shadow_open(ctx->shadow, &count);
    return count > 0 ? open[count - 1] : NULL;
}

/* Run a token through the th_shadow and return the element that was current before it; on failure the drive stops
   with MemoryError set. The spine's element pointers stay valid; any other is stale once this returns. */
static TH_NOINLINE th_node *rw_shadow_step(rw_ctx *ctx, th_token *token) {
    th_node *before = NULL;
    /* GCOVR_EXCL_BR_START: allocation failure cannot be forced from a test */
    if (th_shadow_compact(ctx->shadow, rw_refs(ctx), ctx->depth, sizeof(rw_open)) < 0 ||
        (before = rw_shadow_current(ctx), th_shadow_step(ctx->shadow, token) < 0)) {
        /* GCOVR_EXCL_BR_STOP */
        /* GCOVR_EXCL_START: allocation-failure path */
        ctx->error = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* GCOVR_EXCL_STOP */
    return before;
}

/* Where the spine closes down to after the tree builder ran a tag: the outermost of the innermost run of followed
   elements it took off its stack, with everything the spine has open above it, or ctx->depth. An element the adoption
   agency took from under others that stay open closes once they do. */
static Py_ssize_t rw_left_from(const rw_ctx *ctx) {
    Py_ssize_t keep = ctx->depth;
    Py_ssize_t index = ctx->depth > 0 ? ctx->stack[ctx->depth - 1].followed : -1;
    while (index >= 0 && ctx->stack[index].shadow->left_stack) {
        TH_WORK_STEP();
        keep = index;
        index = index > 0 ? ctx->stack[index - 1].followed : -1;
    }
    return keep;
}

/* The namespace of a start tag that may meet the foreign-content rules: one while a foreign element is on the spine,
   one rw_watched names, and any once a th_shadow runs. *self_terminating says it leaves no element open, *model is the
   content model the tree builder switched the tokenizer to (left at -1 for none), *closed how many open elements it
   closed (see rw_release_foreign), *shadow the tree builder's element it opened, and *ignored whether the tree builder
   ignored the tag, building no node for it. */
static TH_NOINLINE uint8_t rw_foreign_start(rw_ctx *ctx, th_token *token, uint16_t atom, int *self_terminating,
                                            int *model, Py_ssize_t *closed, th_node **shadow, int *ignored) {
    if (ctx->shadow == NULL) {
        if (!rw_start_needs_shadow(ctx, token, atom)) {
            *self_terminating = token->self_closing; /* the element is SVG/MathML */
            return rw_start_ns(ctx, atom);
        }
        if (rw_follow_tree(ctx, token->src_off) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return TH_NS_HTML;                         /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    const th_node *before = rw_shadow_step(ctx, token);
    *closed = rw_pop_to(ctx, rw_left_from(ctx));
    /* An element the tag opened is the current node now. A tag whose insertion mode opens an element of its own first
       (a <col> its <colgroup>) leaves that one, and a void or self-closing tag none. A tag the tree builder ignores
       opens a spine element all the same, as one outside foreign content does, but switches no content model. */
    th_node *now = rw_shadow_current(ctx);
    if (now == before || now->atom != atom) {
        *self_terminating = token->self_closing_acknowledged || th_tag_is_void(atom);
        *ignored = !th_shadow_built(ctx->shadow);
        return TH_NS_HTML;
    }
    *self_terminating = 0;
    *shadow = now;
    if (now->ns == TH_NS_HTML) {
        *model = rw_content_model(&token->name);
    }
    return now->ns;
}

/* The bookkeeping of a start tag rw_foreign_start read: its integration point, the nearest followed entry, and the
   SVG/MathML and integration point entries from it down. A start tag outside foreign content leaves the counts at 0:
   without a th_shadow running the spine has no HTML entry above a foreign one, as such a tag starts one first. */
static TH_NOINLINE void rw_track_start(const rw_ctx *ctx, rw_open *open) {
    open->integration = open->ns != TH_NS_HTML ? rw_integration(open->ns, open->node) : 0;
    const rw_open *below = ctx->depth > 0 ? &ctx->stack[ctx->depth - 1] : NULL;
    open->followed = (int32_t)(open->shadow != NULL ? ctx->depth : below != NULL ? below->followed : -1);
    open->foreign_open = (uint16_t)((below != NULL ? below->foreign_open : 0) + (open->ns != TH_NS_HTML));
    open->points_open = (uint16_t)((below != NULL ? below->points_open : 0) + (open->integration != 0));
}

/* Hand the input back to the spine after a tag, when it reads what follows as the tree builder would until a tag it
   cannot read starts the th_shadow again: outside templates and framesets, with no SVG/MathML element open or inside
   one the tree builder has current, and no integration point open. The tree waits with what tree construction still
   reaches, and catches up from here when it starts again. */
static TH_NOINLINE void rw_park(rw_ctx *ctx, const th_token *token) {
    if (ctx->body_at < 0 || ctx->read_changed) {
        return;
    }
    if (ctx->depth > 0) {
        const rw_open *top = &ctx->stack[ctx->depth - 1];
        if (top->points_open > 0 || (top->foreign_open > 0 && top->shadow != rw_shadow_current(ctx))) {
            return;
        }
    }
    if (!th_shadow_parkable(ctx->shadow)) {
        return;
    }
    th_shadow_park(ctx->shadow, token);
    ctx->parked = ctx->shadow;
    ctx->shadow = NULL;
    ctx->cdata = rw_spine_foreign(ctx);
    th_tok_set_cdata(ctx->sm, ctx->cdata);
}

/* Let go of the elements a start tag closed once its handlers ran: a handler that removed the tag, or took the last
   color, face or size off a <font>, left nothing in the output to close them. */
static TH_NOINLINE void rw_release_start(rw_ctx *ctx, Py_ssize_t closed, const rw_handle *handle, const rw_open *open) {
    int dropped = handle != NULL && handle->removed;
    if (open->edited && open->node->atom == TH_TAG_FONT && !rw_font_breakout(ctx, open->node)) {
        dropped = 1;
    }
    rw_release_foreign(ctx, closed, dropped, 0, 0);
}

/* Note whether a handler's edits to an element can change how a parse reads the markup after it, and start the
   mirror when that can already matter (see rw_mirror_before). Writing before or after the element, or in place of its
   tags, puts text or markup into the surrounding content; keeping the tags but writing their content puts it into the
   element's own. Both are harmless only inside a raw-text element, which also makes removing one harmless. An attribute
   counts only when the tree builder reads it (th_builder_reads_attr); any other attribute change leaves the tree as it
   is. `written` is the output length before the handlers ran. */
static TH_NOINLINE void rw_note_element_edits(rw_ctx *ctx, const rw_handle *handle, const rw_open *open, int foreign,
                                              Py_ssize_t written) {
    uint16_t atom = open->node->atom;
    int around =
        ctx->out.len != written || open->after_html != NULL ||
        (handle->removed && (handle->replace_html != NULL || !open->drop_content || open->append_html != NULL));
    int inside = handle->removed || handle->before_html != NULL || open->append_html != NULL;
    int read_attr = open->reads_attr;
    if (read_attr) {
        ctx->read_changed = 1;
        if (ctx->track == 0 && ctx->shadow == NULL && ctx->parked != NULL) {
            ctx->track = 1;
        }
    }
    if (!around && !inside && !read_attr) {
        return;
    }
    if (!around && !read_attr && open->ns == TH_NS_HTML && open->model >= 0 && atom != TH_TAG_XMP &&
        atom != TH_TAG_PLAINTEXT) {
        /* an <iframe> or <textarea> start tag keeps a later <frameset> from replacing the body */
        ctx->frameset_hazard |= handle->removed && (atom == TH_TAG_IFRAME || atom == TH_TAG_TEXTAREA);
        return;
    }
    ctx->edited = 1;
    if (foreign && ctx->mirror == NULL) {
        rw_mirror_start(ctx);
    }
}

/* The output's current node before the start tag of an element that keeps its tags is written, for rw_mirror_opened. */
static TH_NOINLINE th_node *rw_mirror_opening(rw_ctx *ctx) {
    rw_mirror_sync(ctx, 0);
    return rw_mirror_current(ctx);
}

/* Once that start tag is written: pair an SVG/MathML element that stays open with the element the output opened for
   it, and when a handler wrote text unescaped as the element's raw text but the output reads markup there, write that
   text escaped instead. */
static TH_NOINLINE void rw_mirror_opened(rw_ctx *ctx, rw_open *open, rw_handle *handle, const th_node *was) {
    rw_mirror_sync(ctx, 0);
    if (open->ns != TH_NS_HTML) {
        th_node *now = rw_mirror_current(ctx);
        if (now != was) {
            open->mirrored = now;
        }
    }
    if (open->literal && !rw_output_raw(ctx, open)) {
        open->literal = 0; /* only a handler sets it, so handle is set */
        Py_XSETREF(handle->before_html, handle->before_text);
        handle->before_text = NULL;
        Py_XSETREF(open->append_html, ctx->append_text);
        ctx->append_text = NULL;
    }
    Py_CLEAR(ctx->append_text); /* the tags stay, so the content stays raw text */
}

/* A frameset that replaces the body drops the body's content from the input, but the output still holds what was
   written there. Cut that content out, from where the body opened up to the frameset's own output at `written`, so
   the output reads as the input does once the frameset is gone; the frameset's own output, a mark a handler wrote
   before it for one, stays. Then start the mirror over on what is left. */
static TH_NOINLINE void rw_drop_body(rw_ctx *ctx, Py_ssize_t written) {
    rw_out *out = &ctx->out;
    memmove(out->data + ctx->body_at, out->data + written, (size_t)(out->len - written) * sizeof(Py_UCS4));
    out->len -= written - ctx->body_at;
    out->tail = 0;
    out->room = out->cap;
    out->moved = 0;
    out->rcdata = 0;
    ctx->mirrored = 0;
    for (Py_ssize_t index = 0; index < ctx->depth; index++) {
        ctx->stack[index].mirrored = NULL;
    }
    th_shadow_free(ctx->mirror);
    rw_mirror_start(ctx);
}

static void rw_handle_element(rw_ctx *ctx, th_token *token) {
    uint16_t atom = token->atom;
    Py_ssize_t closed = 0;
    uint8_t ns = TH_NS_HTML;
    th_node *shadow = NULL;
    /* the trailing slash ends only an SVG/MathML element, and only an HTML element is void: on an HTML start tag a
       browser ignores the slash, so <title/> and <script/> still open their element and switch the content model,
       and an SVG <input> or <link> stays open until its end tag */
    int self_terminating;
    int ignored = 0;
    /* the raw-text content models belong to the HTML elements the tag opens: a foreign <title> or <style> holds
       markup */
    int model = -1;
    /* ordinary HTML outside <svg> and <math> skips the foreign-content rules with this one test */
    if (ctx->mirror != NULL) {
        rw_mirror_before(ctx, token);
    }
    int foreign = ctx->shadow != NULL || rw_spine_foreign(ctx) || rw_watched(atom);
    if (foreign) {
        if (ctx->mirror == NULL && rw_watched(atom) &&
            (ctx->edited || (atom == TH_TAG_FRAMESET && ctx->frameset_hazard))) {
            rw_mirror_start(ctx);
            if (ctx->error) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            rw_mirror_before(ctx, token);
        }
        ns = rw_foreign_start(ctx, token, atom, &self_terminating, &model, &closed, &shadow, &ignored);
        if (ctx->error) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    } else {
        self_terminating = th_tag_is_void(atom);
        model = rw_content_model(&token->name);
    }
    int suppressed = ctx->suppress > 0;
    th_node *parent = ctx->depth > 0 ? ctx->stack[ctx->depth - 1].node : ctx->document;
    th_node *node = rw_make_node(ctx, token, atom, parent);
    if (node == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        rw_release_foreign(ctx, closed, 0, 0, 0); /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->error = 1;                           /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();                         /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }

    rw_open open = {.node = node,
                    .model = (int8_t)model,
                    .ns = ns,
                    .shadow = shadow,
                    .line = (uint32_t)token->line,
                    .col = (uint32_t)token->col};
    if (foreign) {
        rw_track_start(ctx, &open);
    }
    rw_handle *handle = NULL;
    Py_ssize_t written = ctx->out.len;
    rw_note_body(ctx, written);
    for (Py_ssize_t index = 0; index < ctx->rule_count && !suppressed && !ctx->error; index++) {
        if (!selector_matches(node, ctx->rules[index].compiled, NULL, &ctx->value_memo)) {
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
        /* before the next selector, the start-tag writer or the spine reads the array */
        if (ctx->attr_index != NULL || ctx->attr_dead != 0) {
            rw_attrs_settle(ctx, node);
        }
    }

    /* the output past this point is the handlers' and the release's; the frameset's own output starts after the end
       tags the release wrote ahead of it */
    Py_ssize_t handled = ctx->out.len;
    if (closed > 0) {
        rw_release_start(ctx, closed, handle, &open);
    }

    if (ctx->error) {
        if (handle != NULL) { /* GCOVR_EXCL_BR_LINE: NULL needs a handle-alloc failure, unforceable */
            handle->live = 0;
            Py_DECREF(handle);
        }
        Py_XDECREF(open.append_html);
        rw_free_node(ctx, node);
        return;
    }
    if (handle != NULL) {
        rw_note_element_edits(ctx, handle, &open, foreign, written);
        if (ctx->error) {                 /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            handle->live = 0;             /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(handle);            /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_XDECREF(open.append_html); /* GCOVR_EXCL_LINE: allocation-failure path */
            rw_free_node(ctx, node);      /* GCOVR_EXCL_LINE: allocation-failure path */
            return;                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (open.reads_attr || handle->removed || ctx->out.len != written || handle->before_html != NULL ||
            open.append_html != NULL || open.after_html != NULL) {
            rw_note_edit(ctx, token, written);
        }
    }

    int emit = !suppressed;
    int removed = handle != NULL && handle->removed;
    /* an unedited frameset replaces the body in the output as in the input, so only an edit can need the cut */
    if (atom == TH_TAG_FRAMESET && ctx->edited && ctx->body_at >= 0 && ctx->shadow != NULL &&
        th_shadow_discarded(ctx->shadow)) {
        rw_drop_body(ctx, written + ctx->out.len - handled);
    }
    /* once an edit has changed the markup, the output can read a tag the tree builder ignored as an element (an edit
       may have let a later <frameset> replace the body), so such a tag keeps neither its start nor its end tag */
    int ignored_dropped = ignored && closed == 0 && ctx->changed;
    if (ignored_dropped) {
        open.drop_end_tag = 1;
    }
    if (removed) {
        /* the tags are dropped, so nothing a handler wrote lands between them: there is no raw-text content left to
           check, and what was appended to or prepended to a raw-text element is ordinary text now */
        open.literal = 0;
        if (ctx->append_text != NULL) {
            Py_SETREF(open.append_html, ctx->append_text);
            ctx->append_text = NULL;
        }
        if (handle->before_text != NULL && !handle->subtree) {
            Py_XSETREF(handle->before_html, handle->before_text);
            handle->before_text = NULL;
        }
    } else if (handle != NULL && ctx->mirror == NULL) {
        /* the tags stay, so the content stays raw text; while the mirror runs, rw_mirror_opened decides */
        Py_CLEAR(ctx->append_text);
    }

    if (emit) {
        if (removed) {
            if (handle->replace_html != NULL) {
                rw_out_content(&ctx->out, handle->replace_html, handle->replace_raw);
            }
        } else if (!ignored_dropped) {
            int mirroring = ctx->mirror != NULL;
            const th_node *was = mirroring ? rw_mirror_opening(ctx) : NULL;
            if (open.edited) {
                rw_emit_start_tag(ctx, node, token->self_closing);
            } else {
                rw_emit_verbatim(&ctx->out, ctx->sm, token);
            }
            if (mirroring) {
                rw_mirror_opened(ctx, &open, handle, was);
            }
        }
        open.content_start = ctx->out.len;
        /* prepend content, then any inner replacement, all inside the element */
        if (handle != NULL && handle->before_html != NULL && !handle->subtree) {
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
        rw_free_node(ctx, node);
        if (foreign) {
            rw_sync_cdata(ctx);
        }
        return;
    }

    if (handle != NULL) {
        handle->live = 0;
        Py_DECREF(handle);
    }

    if (ctx->depth == ctx->stack_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(ctx->depth + 1), (size_t)ctx->stack_cap, 32, sizeof(rw_open), &cap, &bytes);
        if (!grew) {                 /* GCOVR_EXCL_BR_LINE: the depth guard caps growth first */
            ctx->error = 1;          /* GCOVR_EXCL_LINE: unreachable overflow-guard path */
            PyErr_NoMemory();        /* GCOVR_EXCL_LINE */
            rw_free_node(ctx, node); /* GCOVR_EXCL_LINE */
            return;                  /* GCOVR_EXCL_LINE */
        }
        rw_open *grown = PyMem_Realloc(ctx->stack, bytes);
        if (grown == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->error = 1;          /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();        /* GCOVR_EXCL_LINE */
            rw_free_node(ctx, node); /* GCOVR_EXCL_LINE */
            return;                  /* GCOVR_EXCL_LINE */
        }
        ctx->stack = grown;
        if (cap > TH_MAX_TREE_DEPTH && ctx->stack_cap <= TH_MAX_TREE_DEPTH) {
            if (rw_go_deep(ctx, 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->error = 1;           /* GCOVR_EXCL_LINE: allocation-failure path */
                PyErr_NoMemory();         /* GCOVR_EXCL_LINE */
                rw_free_node(ctx, node);  /* GCOVR_EXCL_LINE */
                return;                   /* GCOVR_EXCL_LINE */
            }
        }
        ctx->stack_cap = (Py_ssize_t)cap;
    }
    if (rw_open_name(ctx, &open) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        /* GCOVR_EXCL_START: allocation-failure path */
        if (handle != NULL) {  /* GCOVR_EXCL_LINE: allocation-failure path */
            handle->live = 0;  /* GCOVR_EXCL_LINE: allocation-failure path */
            Py_DECREF(handle); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* GCOVR_EXCL_STOP */
        Py_XDECREF(open.append_html); /* GCOVR_EXCL_LINE: allocation-failure path */
        rw_free_node(ctx, node);      /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx->error = 1;               /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory();             /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    open.long_values = rw_long_values(node);
    ctx->open_len += open.long_values;
    ctx->stack[ctx->depth++] = open;
    if (open.drop_content) {
        ctx->suppress++;
    }
    /* a rawtext/rcdata element switches the tokenizer's content model */
    if (open.model >= 0) {
        if (open.drop_end_tag && ctx->suppress == 0) {
            ctx->suppress = -1;
        }
        if (ctx->shadow == NULL) { /* a th_shadow switched it already */
            th_tok_switch(ctx->sm, (enum th_initial_state)open.model);
        }
    }
    if (foreign) {
        rw_sync_cdata(ctx);
    }
}

/* Text written unescaped into a raw-text element must not end it before its real end tag: `</name` closes the element
   wherever it appears, and in a script `<!--` can start an escaped section that swallows the end tag. The pieces
   are checked together, as written, because two harmless appends can join into an end tag. Sets ValueError and
   returns 1 when the content would end early. */
static int rw_raw_content_closes_early(rw_ctx *ctx, const rw_open *element) {
    const Py_UCS4 *content = ctx->out.data + element->content_start;
    Py_ssize_t len = ctx->out.len - element->content_start;
    const th_node *node = element->node;
    int script = element->model == TH_INIT_SCRIPT_DATA;
    for (Py_ssize_t index = 0; index + 1 < len; index++) {
        if (content[index] != '<') {
            continue;
        }
        int comment = script && index + 3 < len && content[index + 1] == '!' && content[index + 2] == '-' &&
                      content[index + 3] == '-';
        int end_tag = content[index + 1] == '/' && index + 2 + node->text_len <= len;
        for (Py_ssize_t pos = 0; end_tag && pos < node->text_len; pos++) {
            end_tag = lower_ascii(content[index + 2 + pos]) == node->text[pos];
        }
        if (comment || end_tag) {
            PyObject *name = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, node->text, node->text_len);
            if (name != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                PyErr_Format(PyExc_ValueError,
                             script ? "text written into <%U> must not contain '</%U' or '<!--'"
                                    : "text written into <%U> must not contain '</%U'",
                             name, name);
                Py_DECREF(name);
            }
            ctx->error = 1;
            return 1;
        }
    }
    return 0;
}

/* Whether an end tag names an open element: a known tag matches by atom, an unknown (custom) tag by its lowercased
   name. */
static int rw_end_names(const th_node *node, const th_token *token, uint16_t atom) {
    if (node->atom != atom) {
        return 0;
    }
    if (atom != TH_TAG_UNKNOWN) {
        return 1;
    }
    if (node->text_len != token->name.len) {
        return 0;
    }
    for (Py_ssize_t pos = 0; pos < node->text_len; pos++) {
        if (node->text[pos] != PyUnicode_READ(token->name.kind, token->name.data, pos)) {
            return 0;
        }
    }
    return 1;
}

/* The open element an end tag closes while a foreign element is on the spine or a th_shadow runs, or -1 once the tag
   is handled in full. *end_from is where the open elements above the match start to get end tags of their own. */
static TH_NOINLINE Py_ssize_t rw_foreign_end(rw_ctx *ctx, th_token *token, uint16_t atom, Py_ssize_t *end_from) {
    int named = 1;
    if (ctx->counts != NULL) {
        named = rw_end_name_open(ctx, token, atom);
        if (named < 0) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            ctx->error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (ctx->shadow == NULL) {
        /* the foreign-content rules close the nearest SVG/MathML element of that name, and hand </p>, </br> and a tag
           that names none of them to the HTML rules */
        /* a br is void and never stays open, so named is false for it and this loop skips it as before */
        int svg_named = named && atom != TH_TAG_P;
        for (Py_ssize_t index = ctx->depth - 1;
             svg_named && index >= rw_window_floor(ctx) &&
             ctx->stack[index].ns != TH_NS_HTML; /* GCOVR_EXCL_BR_LINE: hash collision */
             index--) {
            if (rw_end_names(ctx->stack[index].node, token, atom)) {
                /* dropped, the end tag closes none of the SVG/MathML elements above it in the output */
                if (ctx->stack[index].drop_end_tag) {
                    *end_from = index + 1;
                }
                return index;
            }
        }
        if (rw_follow_tree(ctx, token->src_off) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
            return -1;                                 /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_ssize_t open_before;
    Py_ssize_t open_after;
    th_shadow_open(ctx->shadow, &open_before);
    rw_shadow_step(ctx, token);
    th_shadow_open(ctx->shadow, &open_after);
    /* a stray end tag the tree builder ignored builds and closes nothing: the output must not read it as an element */
    int ignored = !th_shadow_built(ctx->shadow) && open_before == open_after && ctx->changed;
    /* the element the tag names, unless one the tree builder keeps open sits above it */
    Py_ssize_t match = -1;
    for (Py_ssize_t index = ctx->depth - 1; named && index >= rw_window_floor(ctx); index--) {
        TH_WORK_STEP();
        const rw_open *open = &ctx->stack[index];
        if (open->shadow != NULL && !open->shadow->left_stack) {
            break;
        }
        if (rw_end_names(open->node, token, atom)) {
            match = index;
            break;
        }
    }
    if (match < 0) {
        if (ctx->counts == NULL && rw_note_stray(ctx) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        /* a stray end tag still closes what the tree builder closed for it, such as </p> the SVG/MathML elements */
        rw_release_foreign(ctx, rw_pop_to(ctx, rw_left_from(ctx)), 0, ctx->mirror != NULL, 1);
        if (ctx->suppress <= 0 && !ignored) {
            rw_emit_verbatim(&ctx->out, ctx->sm, token);
        }
        if (ctx->closing_len > 0) {
            rw_mirror_close(ctx);
        }
        return -1;
    }
    /* the elements above the match end where it does, with their trailing edits; dropped, the end tag closes none of
       them in the output, so each gets the end tag it stood for (see rw_release_foreign) */
    int dropped = ctx->stack[match].drop_end_tag;
    rw_release_foreign(ctx, rw_pop_to(ctx, match + 1), dropped, !dropped && ctx->mirror != NULL, 1);
    return match;
}

static void rw_handle_end(rw_ctx *ctx, th_token *token) {
    if (ctx->mirror != NULL) {
        rw_mirror_before(ctx, token);
    }
    uint16_t atom = token->atom;
    int foreign = ctx->shadow != NULL || rw_spine_foreign(ctx);
    Py_ssize_t end_from = ctx->depth;
    Py_ssize_t match = -1;
    if (foreign) {
        Py_ssize_t written = ctx->out.len;
        match = rw_foreign_end(ctx, token, atom, &end_from);
        rw_note_body(ctx, written);
        if (match < 0) {
            return;
        }
    } else {
        int named = 1;
        if (ctx->counts != NULL) {
            named = rw_end_name_open(ctx, token, atom);
            if (named < 0) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                ctx->error = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                return;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        /* find the matching open element, top-down, unless the counts show no open element has its name */
        for (Py_ssize_t index = ctx->depth - 1; named && index >= rw_window_floor(ctx); index--) {
            TH_WORK_STEP();
            if (rw_end_names(ctx->stack[index].node, token, atom)) {
                match = index;
                break;
            }
        }
        if (match < 0) {
            if (ctx->counts == NULL && rw_note_stray(ctx) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            /* a stray end tag with no open match: emit it verbatim */
            if (ctx->suppress <= 0) {
                rw_emit_verbatim(&ctx->out, ctx->sm, token);
            }
            return;
        }
    }
    /* pop the intermediate unclosed elements, then the match: the end tag being handled closes them in a browser too,
       so they get no end tags of their own, except from end_from up (see rw_foreign_end) */
    for (Py_ssize_t index = ctx->depth - 1; index > match; index--) {
        rw_open *open = &ctx->stack[index];
        rw_close_name(ctx, open);
        if (open->drop_content) {
            ctx->suppress--;
        }
        if (index >= end_from && ctx->suppress == 0 && !open->drop_end_tag) {
            rw_emit_end_tag(&ctx->out, open->node);
        }
        Py_XDECREF(open->append_html);
        Py_XDECREF(open->after_html);
        ctx->open_len -= open->long_values;
        rw_free_node(ctx, open->node);
    }
    ctx->depth = match + 1; /* an error below leaves only the closing element for rw_ctx_clear */
    rw_open *closing = &ctx->stack[match];
    if (closing->drop_content) {
        ctx->suppress--;
    }
    int emit = ctx->suppress <= 0;
    if (emit && closing->append_html != NULL) {
        rw_out_str(&ctx->out, closing->append_html);
    }
    if (emit && closing->literal && rw_raw_content_closes_early(ctx, closing)) {
        return;
    }
    if (closing->drop_end_tag) {
        if (ctx->suppress < 0) { /* the escaped text of an unwrapped raw-text element ends with it */
            ctx->suppress = 0;
        }
    } else if (emit) {
        rw_emit_verbatim(&ctx->out, ctx->sm, token);
    }
    if (foreign && ctx->closing_len > 0) { /* only rw_foreign_end holds elements */
        rw_mirror_close(ctx);
    }
    if (emit && closing->after_html != NULL) {
        rw_out_str(&ctx->out, closing->after_html);
    }
    Py_XDECREF(closing->append_html);
    Py_XDECREF(closing->after_html);
    ctx->open_len -= closing->long_values;
    rw_close_name(ctx, closing);
    rw_free_node(ctx, closing->node);
    ctx->depth = match;
    if (foreign) {
        rw_sync_cdata(ctx);
    }
}

/* Hold the tail of source text written as read for rw_out_make_room to settle: a `<` at its end, or in RCDATA an
   end tag cut short, read as text there because what followed in the source ended it, but a handler can drop or
   change what follows, and what it writes in its place, or the markup a dropped node exposes, would join the tail
   into a tag. Raw text reads `<` as text whatever follows it, and a comment, doctype or CDATA section ends in one only
   at the end of the input. */
static TH_NOINLINE void rw_hold_tail(rw_ctx *ctx, const th_token *token) {
    const rw_open *top = ctx->depth > 0 ? &ctx->stack[ctx->depth - 1] : NULL;
    int rcdata = top != NULL && top->model == TH_INIT_RCDATA;
    if (token->kind != TH_TEXT || token->has_source || (top != NULL && top->model >= 0 && !rcdata)) {
        return;
    }
    rw_out *out = &ctx->out;
    Py_ssize_t start = out->len - (token->is_slice ? token->src_len : token->text.len);
    Py_ssize_t lt = out->len - 1;
    if (out->data[lt] != '<') {
        Py_ssize_t slash = lt;
        while (rcdata && slash > start && is_ascii_alpha(out->data[slash])) {
            slash--;
        }
        if (out->data[slash] != '/' || slash == start || out->data[slash - 1] != '<') {
            return;
        }
        lt = slash - 1;
    }
    out->tail = lt + 1;
    out->rcdata = rcdata;
    out->room = out->len;
}

/* Whether the output at this point is content the tokenizer reads as data, outside any raw-text or RCDATA element. */
static int rw_data_context(const rw_ctx *ctx) {
    return ctx->depth == 0 || ctx->stack[ctx->depth - 1].model < 0;
}

/* Write a text run gathered from several source spans. A token dropped between the pieces (an empty end tag, or one a
   handler removed) can leave a `<` in front of letters, `/`, `!` or `?`, which the output reads as markup, so such a
   `<` is escaped. A `<` or `</` at the end of the run is left to the tail, which the next write settles. */
static TH_NOINLINE int rw_emit_gathered(rw_out *out, const th_buf *text) {
    Py_ssize_t len = text->len;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = buf_read(text, index);
        Py_UCS4 next = index + 1 < len ? buf_read(text, index + 1) : 0;
        if (ch == '<' && rw_lt_joins(next) && (next != '/' || index + 2 < len)) {
            rw_out_ascii(out, "&lt;");
        } else {
            rw_out_char(out, ch);
        }
    }
    return !out->failed;
}

/* Write an unedited leaf token as it was read. */
static void rw_emit_read_leaf(rw_ctx *ctx, const th_token *token) {
    int written = token->kind == TH_TEXT && !token->is_slice && !token->has_source && rw_data_context(ctx)
                      ? rw_emit_gathered(&ctx->out, &token->text)
                      : rw_emit_verbatim(&ctx->out, ctx->sm, token);
    if (!written) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
        return;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS4 last = ctx->out.data[ctx->out.len - 1];
    if (last == '<' || last == '/' ||
        (is_ascii_alpha(last) && ctx->depth > 0 && ctx->stack[ctx->depth - 1].model == TH_INIT_RCDATA)) {
        rw_hold_tail(ctx, token);
    }
}

/* Without the tags of a raw-text or RCDATA element, a browser reads the `<` and bare `&` of its text as markup, so an
   unedited text run whose tags were dropped is escaped. An RCDATA character reference is written as its decoded,
   escaped text: copied as written, a reference without its semicolon would join the text after the element. */
static void rw_emit_escaped(rw_ctx *ctx, const th_token *token) {
    if (token->is_slice) {
        int kind;
        const char *base = th_tok_input_data(ctx->sm, &kind);
        rw_out_escaped(&ctx->out, kind, base + token->src_start * kind, token->src_len);
        return;
    }
    rw_out_escaped(&ctx->out, token->text.kind, token->text.data, token->text.len);
}

/* Write an unedited leaf token in the form rw_mirror_reading picked. */
static TH_NOINLINE void rw_emit_leaf(rw_ctx *ctx, const th_token *token, enum rw_leaf_form form) {
    if (form == RW_AS_TEXT) {
        rw_emit_escaped(ctx, token);
    } else if (form == RW_AS_COMMENT) {
        rw_out_ascii(&ctx->out, "<!--");
        for (Py_ssize_t pos = 0; pos < token->text.len; pos++) {
            rw_out_char(&ctx->out, buf_read(&token->text, pos));
        }
        rw_out_ascii(&ctx->out, "-->");
    } else {
        rw_emit_read_leaf(ctx, token);
    }
}

/* Note whether a text, comment or doctype handler's edits can change how a parse reads the markup after the token,
   and start the mirror inside SVG/MathML content (see rw_note_element_edits). Text in a raw-text element, and a
   comment's own text, never can; the doctype picks the quirks mode the tree builder runs in. */
static TH_NOINLINE void rw_note_leaf_edits(rw_ctx *ctx, const rw_handle *handle) {
    int around = handle->before_html != NULL || handle->after_html != NULL || handle->replace_html != NULL;
    if (handle->kind == RW_COMMENT ? !around
                                   : (!around && !handle->removed && handle->set_text == NULL) ||
                                         (handle->kind == RW_TEXT && rw_source_raw(ctx))) {
        return;
    }
    ctx->edited = 1;
    if (ctx->mirror == NULL && (ctx->shadow != NULL || rw_spine_foreign(ctx))) {
        rw_mirror_start(ctx);
    }
}

/* Run a text/comment/doctype handler and emit the result; escape writes an unedited token with rw_emit_escaped. */
static inline void rw_call_leaf_handler(rw_ctx *ctx, const th_token *token, PyObject *handler, enum rw_kind kind,
                                        int escape) {
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
    rw_note_leaf_edits(ctx, handle);
    if (ctx->error) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(handle); /* GCOVR_EXCL_LINE: allocation-failure path */
        return;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (handle->removed || handle->before_html != NULL || handle->after_html != NULL || handle->set_text != NULL) {
        rw_note_edit(ctx, token, ctx->out.len);
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
            rw_out_raw(&ctx->out, handle->set_text);
            rw_out_ascii(&ctx->out, "-->");
        } else {
            rw_out_content(&ctx->out, handle->set_text, handle->set_text_raw);
        }
    } else if (escape) {
        rw_emit_escaped(ctx, token);
    } else if (ctx->mirror != NULL) {
        rw_emit_leaf(ctx, token, rw_mirror_reading(ctx, token));
    } else {
        rw_emit_read_leaf(ctx, token);
    }
    if (handle->after_html != NULL) {
        rw_out_str(&ctx->out, handle->after_html);
    }
    Py_DECREF(handle);
}

/* A token in the text of a raw-text or RCDATA element whose tags were dropped. Kept out of line: inlined, its copy of
   the handler call makes rw_handle_leaf save registers before its fast checks on every token. */
static TH_NOINLINE void rw_handle_escaped_leaf(rw_ctx *ctx, const th_token *token, PyObject *handler,
                                               enum rw_kind kind) {
    if (handler == NULL) {
        rw_emit_escaped(ctx, token);
        return;
    }
    rw_call_leaf_handler(ctx, token, handler, kind, 1);
}

/* rw_handle_leaf while the mirror runs. */
static TH_NOINLINE void rw_handle_mirrored_leaf(rw_ctx *ctx, const th_token *token, PyObject *handler,
                                                enum rw_kind kind) {
    rw_mirror_before(ctx, token);
    if (ctx->suppress != 0) {
        if (ctx->suppress < 0) {
            rw_handle_escaped_leaf(ctx, token, handler, kind);
        }
        return;
    }
    if (handler == NULL) {
        rw_emit_leaf(ctx, token, rw_mirror_reading(ctx, token));
        return;
    }
    rw_call_leaf_handler(ctx, token, handler, kind, 0);
}

/* Drive a text/comment/doctype token through its handler and emit the result. */
static void rw_handle_leaf(rw_ctx *ctx, th_token *token, PyObject *handler, enum rw_kind kind) {
    if (ctx->shadow != NULL) {
        rw_shadow_step(ctx, token); /* text and comments move the tree builder too (frameset-ok, table text, ...) */
        rw_note_body(ctx, ctx->out.len);
    }
    if (ctx->mirror != NULL) {
        rw_handle_mirrored_leaf(ctx, token, handler, kind);
        return;
    }
    if (ctx->suppress != 0) {
        if (ctx->suppress < 0) {
            rw_handle_escaped_leaf(ctx, token, handler, kind);
        }
        return;
    }
    if (handler == NULL) {
        rw_emit_read_leaf(ctx, token);
        return;
    }
    rw_call_leaf_handler(ctx, token, handler, kind, 0);
}

/* Free everything the context owns. */
#ifdef TH_WORK_STEPS
/* Recount the open elements from the spine, once the drive has ended with the spine still holding what is open, and
   fail the call when the counts differ, so a drift shows in the test that causes it. Only the work-step build checks.
 */
static void rw_names_check(rw_ctx *ctx) {
    if (ctx->counts == NULL) {
        return;
    }
    uint64_t steps = th_work_steps; /* the walk is the check's own, so the steps it takes are not the rewrite's */
    Py_ssize_t atoms[TH_TAG_XMP + 1] = {0};
    rw_names counted = {0};
    for (Py_ssize_t index = 0; index < ctx->depth; index++) {
        const rw_open *open = &ctx->stack[index];
        if (open->node->atom != TH_TAG_UNKNOWN) {
            atoms[open->node->atom]++;
        } else if (rw_names_add(&counted, open->name_hash) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            rw_names_free(&counted);                              /* GCOVR_EXCL_LINE: allocation-failure path */
            ctx->error = 1;                                       /* GCOVR_EXCL_LINE: allocation-failure path */
            PyErr_NoMemory();                                     /* GCOVR_EXCL_LINE: allocation-failure path */
            return;                                               /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    /* GCOVR_EXCL_START: a drift is a bug in the counts, which no input reaches */
    int drift = memcmp(atoms, ctx->counts->atoms, sizeof(atoms)) != 0;
    Py_ssize_t open_total = 0;
    for (size_t slot = 0; slot < ctx->counts->names.cap; slot++) {
        open_total += ctx->counts->names.slots[slot].count;
    }
    Py_ssize_t counted_total = 0;
    for (size_t slot = 0; slot < counted.cap; slot++) {
        if (counted.slots[slot].used) {
            counted_total += counted.slots[slot].count;
            drift |= rw_names_count(&ctx->counts->names, counted.slots[slot].hash) != counted.slots[slot].count;
        }
    }
    drift |= open_total != counted_total;
    th_work_steps = steps;
    rw_names_free(&counted);
    if (drift) {
        PyErr_SetString(PyExc_RuntimeError, "rewrite: the open element counts differ from the spine");
        ctx->error = 1;
    }
    /* GCOVR_EXCL_STOP */
}
#endif

static void rw_ctx_clear(rw_ctx *ctx) {
    for (Py_ssize_t index = 0; index < ctx->depth; index++) {
        Py_XDECREF(ctx->stack[index].append_html);
        Py_XDECREF(ctx->stack[index].after_html);
        rw_free_node(ctx, ctx->stack[index].node);
    }
    th_shadow_free(ctx->shadow); /* its text spans the tokenizer's input, freed below */
    th_shadow_free(ctx->parked);
    if (ctx->mirror != NULL) {
        th_shadow_free(ctx->mirror);
        PyMem_Free(ctx->closing); /* empty: rw_mirror_close lets go of each element right after the tag is written */
    }
    rw_values_flush(ctx);
    Py_XDECREF(ctx->append_text); /* still set only when a handler failed */
    PyMem_Free(ctx->stack);
    rw_counts_free(ctx);
    PyMem_Free(ctx->name_buf);
    rw_deep_flush(ctx);
    for (Py_ssize_t index = 0; index < ctx->rule_count; index++) {
        PyMem_Free(ctx->rules[index].deep);
        selector_free(ctx->rules[index].source);
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
            if (reason == NULL) { /* GCOVR_EXCL_BR_LINE: only a failed interning returns no reason */
                PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(pair);  /* GCOVR_EXCL_LINE: allocation-failure path */
                return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            PyErr_Format(state->selector_error, "selector %R is not streamable: %s", selector, reason);
            Py_DECREF(pair);
            return -1;
        }
        if (sel_stack_check(compiled->chain) < 0) {
            selector_free(compiled);
            Py_DECREF(pair);
            return -1;
        }
        ctx->rules[ctx->rule_count] = (rw_rule){compiled, Py_NewRef(handler), compiled, NULL};
        ctx->rule_count++;
        Py_DECREF(pair);
    }
    if (rw_go_deep(ctx, 2) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();         /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                /* GCOVR_EXCL_LINE: allocation-failure path */
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
    /* Python's randomized bytes hash salts the index, so chosen names cannot be precomputed to share one slot */
    PyObject *salt = PyBytes_FromStringAndSize("turbohtml:rewrite-attr", 22);
    if (salt == NULL) {     /* GCOVR_EXCL_BR_LINE: bytes allocation cannot be forced from a test */
        rw_ctx_clear(&ctx); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    ctx.attr_seed = (uint64_t)PyObject_Hash(salt);
    Py_DECREF(salt);
    ctx.edit_line = -1;
    ctx.body_at = -1;
    Py_ssize_t length = PyUnicode_GET_LENGTH(source);
    th_tok_feed(ctx.sm, PyUnicode_KIND(source), PyUnicode_DATA(source), length);
    th_tok_close(ctx.sm);

    th_token *token;
    enum th_step step = TH_STEP_DONE;
    Py_BEGIN_CRITICAL_SECTION(module);
    while (!ctx.error) {
        step = th_tok_next(ctx.sm, &token);
        if (step != TH_STEP_TOKEN) {
            break;
        }
        if (ctx.track == 1) {
            rw_start_following(&ctx, token);
        }
        switch (token->kind) {
        case TH_START_TAG:
            rw_handle_element(&ctx, token);
            if (ctx.shadow != NULL) {
                rw_park(&ctx, token);
            }
            break;
        case TH_END_TAG:
            rw_handle_end(&ctx, token);
            if (ctx.shadow != NULL) {
                rw_park(&ctx, token);
            }
            break;
        case TH_TEXT:
        case TH_CHARREF:
            rw_handle_leaf(&ctx, token, ctx.text_handler, RW_TEXT);
            break;
        case TH_COMMENT:
            rw_handle_leaf(&ctx, token, ctx.comment_handler, RW_COMMENT);
            break;
        case TH_PI:
            rw_handle_leaf(&ctx, token, NULL, RW_COMMENT);
            break;
        default: /* TH_DOCTYPE */
            rw_handle_leaf(&ctx, token, ctx.doctype_handler, RW_DOCTYPE);
            break;
        }
    }
#ifdef TH_WORK_STEPS
    if (!ctx.error) {
        rw_names_check(&ctx);
    }
#endif
    Py_END_CRITICAL_SECTION();
    /* checked once after the loop: a per-token check cost rewrite 26,000 instructions a call */
    if (step == TH_STEP_ERROR) { /* GCOVR_EXCL_BR_LINE: the only step error is an out-of-memory condition */
        PyErr_NoMemory();        /* GCOVR_EXCL_LINE: allocation-failure path */
        ctx.error = 1;           /* GCOVR_EXCL_LINE: allocation-failure path */
    } /* GCOVR_EXCL_LINE: llvm flags the OOM branch's closing brace */

    /* an unclosed element at EOF: flush its trailing edits and free the spine */
    while (ctx.depth > 0 && !ctx.error) {
        rw_open *open = &ctx.stack[ctx.depth - 1];
        if (open->drop_content) {
            ctx.suppress--;
        }
        int emit = ctx.suppress <= 0;
        if (emit && open->append_html != NULL) {
            rw_out_str(&ctx.out, open->append_html);
        }
        /* PLAINTEXT has no end tag, so nothing written into it can end it */
        if (emit && open->literal && open->model != TH_INIT_PLAINTEXT && rw_raw_content_closes_early(&ctx, open)) {
            break; /* still on the spine, so rw_ctx_clear frees it */
        }
        if (emit && open->after_html != NULL) {
            rw_out_str(&ctx.out, open->after_html);
        }
        Py_XDECREF(open->append_html);
        Py_XDECREF(open->after_html);
        ctx.open_len -= open->long_values;
        rw_free_node(&ctx, open->node);
        ctx.depth--;
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
    if (PyModule_AddObjectRef(module, "_RewriteHandle", state->rewrite_handle_type) < 0) { /* GCOVR_EXCL_BR_LINE */
        return -1;                                                                         /* GCOVR_EXCL_LINE */
    }
    return 0;
}

/* The tree builder's attribute reads, for the tests that pin the rewriter's trigger to them: a namespace ("html", "svg"
   or "math"), a tag name and an attribute name. */
PyObject *turbohtml_builder_reads_attr(PyObject *Py_UNUSED(module), PyObject *args) {
    const char *ns_name;
    const char *tag;
    Py_ssize_t tag_len;
    const char *name;
    Py_ssize_t name_len;
    if (!PyArg_ParseTuple(args, "ss#s#", &ns_name, &tag, &tag_len, &name, &name_len)) {
        return NULL;
    }
    uint8_t ns;
    if (strcmp(ns_name, "html") == 0) {
        ns = TH_NS_HTML;
    } else if (strcmp(ns_name, "svg") == 0) {
        ns = TH_NS_SVG;
    } else if (strcmp(ns_name, "math") == 0) {
        ns = TH_NS_MATHML;
    } else {
        PyErr_Format(PyExc_ValueError, "unknown namespace %s", ns_name);
        return NULL;
    }
    return PyBool_FromLong(th_builder_reads_attr(ns, th_tag_lookup(tag, tag_len), name, name_len));
}
