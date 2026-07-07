/* WHATWG HTML tokenizer state machine.

   The machine consumes code points from an owned input buffer one at a time.
   Each state is a label in the big switch in run(); the structure mirrors the
   spec's "tokenizer" section so the two read side by side. Two reusable token
   records carry output: one for coalesced text runs, one for the markup token
   (tag, comment, doctype) that ends a run. A short pending queue holds them and
   hands them to the caller one at a time.

   Resumption: when the machine needs a character that has not been fed yet and
   end-of-file has not been signaled, it rewinds to the last safe point and
   reports NEED_MORE. Two states use multi-character lookahead (markup
   declaration open and the character-reference helper); both save the position
   of the opening character so a rewind re-runs them cleanly once more input
   arrives. Everything else consumes a single character per step, so suspending
   leaves the state unchanged and returns.

   The spec's parse errors take their recovery transitions but go unreported;
   the public API exposes the token stream, not the error stream. */

#include "tokenizer/statemachine.h"

#include "core/ascii.h"
#include "core/vec.h"

#include <stdint.h>
#include <string.h>

#if defined(__aarch64__) || defined(_M_ARM64)
#include <arm_neon.h>
#define TH_SCAN_NEON 1
#elif defined(__SSE2__) || defined(_M_X64) || (defined(_M_IX86_FP) && _M_IX86_FP >= 2)
#include <emmintrin.h>
#define TH_SCAN_SSE2 1
#endif

#include "tokenizer/charref.h"

#define REPLACEMENT 0xFFFD

static void buf_init(th_buf *buf) {
    buf->data = NULL;
    buf->len = 0;
    buf->cap = 0;
    buf->kind = PyUnicode_1BYTE_KIND;
}

static void buf_free(th_buf *buf) {
    PyMem_Free(buf->data);
    buf_init(buf);
}

static void buf_reset(th_buf *buf) {
    buf->len = 0;
    buf->kind = PyUnicode_1BYTE_KIND;
}

/* Grow the storage to at least need bytes. */
static int buf_ensure(th_buf *buf, Py_ssize_t need) {
    if (need <= buf->cap) {
        return 0;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)need, (size_t)buf->cap, 16, sizeof(char), &cap, &bytes);
    if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    char *grown = PyMem_Realloc(buf->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buf->data = grown;
    buf->cap = (Py_ssize_t)cap;
    return 0;
}

/* Widen the storage so code points of width kind fit, rewriting the existing
   content in place from the back (safe because the copy never overlaps). */
static int buf_promote(th_buf *buf, int kind) {
    if (buf_ensure(buf, buf->len * kind) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                              /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int narrow = buf->kind;
    buf->kind = kind;
    for (Py_ssize_t index = buf->len - 1; index >= 0; index--) {
        buf_write(buf, index, PyUnicode_READ(narrow, buf->data, index));
    }
    return 0;
}

/* Buffers store code points at the narrowest width their content needs (the
   compact-unicode layout), so building a Python string from a buffer is a
   plain copy and an all-ASCII document never pays for UCS4. Equal content
   always has equal kind because reset returns to the 1-byte width. */
static int buf_push(th_buf *buf, Py_UCS4 ch) {
    if (ch < 0x100 && buf->kind == PyUnicode_1BYTE_KIND) {
        /* the dominant path: a Latin-1 code point into a 1-byte buffer */
        if (buf->len == buf->cap && buf_ensure(buf, buf->len + 1) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
            return -1;                                                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        ((Py_UCS1 *)buf->data)[buf->len++] = (Py_UCS1)ch;
        return 0;
    }
    int width = ucs_width(ch);
    if (width > buf->kind && buf_promote(buf, width) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (buf_ensure(buf, (buf->len + 1) * buf->kind) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                         /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    buf_write(buf, buf->len++, ch);
    return 0;
}

enum state {
    ST_DATA,
    ST_RCDATA,
    ST_RAWTEXT,
    ST_SCRIPT,
    ST_PLAINTEXT,
    ST_TAG_OPEN,
    ST_END_TAG_OPEN,
    ST_TAG_NAME,
    ST_RCDATA_LT,
    ST_RCDATA_END_OPEN,
    ST_RCDATA_END_NAME,
    ST_RAWTEXT_LT,
    ST_RAWTEXT_END_OPEN,
    ST_RAWTEXT_END_NAME,
    ST_SCRIPT_LT,
    ST_SCRIPT_END_OPEN,
    ST_SCRIPT_END_NAME,
    ST_SCRIPT_ESC_START,
    ST_SCRIPT_ESC_START_DASH,
    ST_SCRIPT_ESCAPED,
    ST_SCRIPT_ESCAPED_DASH,
    ST_SCRIPT_ESCAPED_DASH_DASH,
    ST_SCRIPT_ESCAPED_LT,
    ST_SCRIPT_ESCAPED_END_OPEN,
    ST_SCRIPT_ESCAPED_END_NAME,
    ST_SCRIPT_DOUBLE_ESC_START,
    ST_SCRIPT_DOUBLE_ESCAPED,
    ST_SCRIPT_DOUBLE_ESCAPED_DASH,
    ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH,
    ST_SCRIPT_DOUBLE_ESCAPED_LT,
    ST_SCRIPT_DOUBLE_ESC_END,
    ST_BEFORE_ATTR_NAME,
    ST_ATTR_NAME,
    ST_AFTER_ATTR_NAME,
    ST_BEFORE_ATTR_VALUE,
    ST_ATTR_VALUE_DQ,
    ST_ATTR_VALUE_SQ,
    ST_ATTR_VALUE_UNQ,
    ST_AFTER_ATTR_VALUE_QUOTED,
    ST_SELF_CLOSING_START_TAG,
    ST_BOGUS_COMMENT,
    ST_MARKUP_DECL_OPEN,
    ST_COMMENT_START,
    ST_COMMENT_START_DASH,
    ST_COMMENT,
    ST_COMMENT_LT,
    ST_COMMENT_LT_BANG,
    ST_COMMENT_LT_BANG_DASH,
    ST_COMMENT_LT_BANG_DASH_DASH,
    ST_COMMENT_END_DASH,
    ST_COMMENT_END,
    ST_COMMENT_END_BANG,
    ST_DOCTYPE,
    ST_BEFORE_DOCTYPE_NAME,
    ST_DOCTYPE_NAME,
    ST_AFTER_DOCTYPE_NAME,
    ST_AFTER_DOCTYPE_PUBLIC_KW,
    ST_BEFORE_DOCTYPE_PUBLIC_ID,
    ST_DOCTYPE_PUBLIC_ID_DQ,
    ST_DOCTYPE_PUBLIC_ID_SQ,
    ST_AFTER_DOCTYPE_PUBLIC_ID,
    ST_BETWEEN_DOCTYPE_PUB_SYS,
    ST_AFTER_DOCTYPE_SYSTEM_KW,
    ST_BEFORE_DOCTYPE_SYSTEM_ID,
    ST_DOCTYPE_SYSTEM_ID_DQ,
    ST_DOCTYPE_SYSTEM_ID_SQ,
    ST_AFTER_DOCTYPE_SYSTEM_ID,
    ST_BOGUS_DOCTYPE,
    ST_CDATA,
    ST_CDATA_BRACKET,
    ST_CDATA_END,
};

/* One open-addressing slot in the per-tag duplicate-attribute index. A slot is
   occupied only when its epoch matches the tokenizer's current attr_epoch, so a
   new tag "clears" the whole table by bumping the epoch -- an O(1) reset that a
   per-tag memset over a table grown to the largest tag ever seen would itself
   turn quadratic. index points into tok.attrs; the name hash lives on the
   attribute (th_attr.name_hash). */
typedef struct {
    uint64_t epoch;
    Py_ssize_t index;
} th_attr_slot;

struct th_tokenizer {
    enum state state;
    int oom;                /* an allocation failed; reported once as TH_STEP_ERROR */
    int cdata_ok;           /* the adjusted current node is foreign, so <![CDATA[ opens a
                               real CDATA section instead of a bogus comment */
    int resolve_references; /* fold references into text (1) or split them into
                               TH_CHARREF tokens (0) */
    int capture_source;     /* record the verbatim source span of markup tokens */
    int capture_locations;  /* stamp the granular tag/attribute source spans */
    int building;           /* a markup token is mid-construction, so its source
                               span's opening '<' must survive input compaction */

    th_buf input;    /* owned, newline-normalized code points */
    Py_ssize_t pos;  /* next code point to read */
    int last_cr;     /* the last fed code point was '\r' (CRLF may span feeds) */
    int eof;         /* close() was called */
    Py_ssize_t line; /* 1-based, advanced as input is consumed */
    Py_ssize_t col;
    Py_ssize_t mark_line; /* position of the '<' that opened the current tag-ish
                             construct; tokens and '<' text fallbacks begin here */
    Py_ssize_t mark_col;
    Py_ssize_t mark_pos; /* input index of that '<', for the verbatim source span */

    th_buf text; /* coalesced character run */
    Py_ssize_t text_line;
    Py_ssize_t text_col;
    int text_open;          /* a run is in progress */
    Py_ssize_t slice_start; /* while the run is an untouched span of the input */
    Py_ssize_t slice_len;
    int input_borrowed; /* input.data is caller-owned storage, never freed here */

    th_token tok;     /* tag/comment/doctype under construction */
    th_attr *attr;    /* attribute under construction (points into tok.attrs) */
    th_attr oom_attr; /* writable sink for attribute data after an allocation failure */

    th_attr_slot *attr_seen;  /* per-tag open-addressing set of kept attribute names */
    Py_ssize_t attr_seen_cap; /* power-of-two slot count, 0 until the first attribute */
    uint64_t attr_epoch;      /* current tag generation; other epochs mark empty slots */

    th_buf last_tag; /* last emitted start tag name, for appropriate end tags */
    th_buf temp;     /* spec "temporary buffer" for raw-text end tags and script */

    th_token text_record;    /* materialized text run handed to the caller */
    th_token charref_record; /* an unresolved character reference handed out */
    th_buf ref;              /* scratch for a reference's resolved code points */
    Py_ssize_t ref_line;     /* position of the '&' that opened the reference */
    Py_ssize_t ref_col;

    th_token *queue[2];
    int queue_head;
    int queue_len;
    th_token *returned; /* reset at the next call so its buffers can be reused */
    int done;

    th_error_sink *err_sink; /* parse errors are recorded here when non-NULL */
    const char *eof_code;    /* the parse-error code to report if EOF arrives with
                                this tag/comment/doctype construct still open */
    int eof_reported;        /* the construct's EOF error has already been recorded */
};

static void token_reset(th_token *tok) {
    buf_reset(&tok->name);
    buf_reset(&tok->text);
    for (Py_ssize_t index = 0; index < tok->attr_count; index++) {
        buf_reset(&tok->attrs[index].name);
        buf_reset(&tok->attrs[index].value);
        tok->attrs[index].has_value = 0;
    }
    tok->attr_count = 0;
    tok->self_closing = 0;
    tok->is_pi = 0;
    buf_reset(&tok->public_id);
    buf_reset(&tok->system_id);
    tok->has_public_id = 0;
    tok->has_system_id = 0;
    tok->force_quirks = 0;
    tok->is_slice = 0;
    tok->src_off = 0;
    tok->src_span = 0;
    tok->has_source = 0;
}

static void token_free(th_token *tok);

static int buf_copy(th_buf *dst, const th_buf *src) {
    if (buf_ensure(dst, src->len * src->kind) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;                                   /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(dst->data, src->data, (size_t)(src->len * src->kind));
    dst->len = src->len;
    dst->kind = src->kind;
    return 0;
}

void th_token_clear(th_token *tok) {
    token_free(tok);
}

static void token_free(th_token *tok) {
    buf_free(&tok->name);
    buf_free(&tok->text);
    for (Py_ssize_t index = 0; index < tok->attr_cap; index++) {
        buf_free(&tok->attrs[index].name);
        buf_free(&tok->attrs[index].value);
    }
    PyMem_Free(tok->attrs);
    tok->attrs = NULL;
    tok->attr_cap = 0;
    tok->attr_count = 0;
    buf_free(&tok->public_id);
    buf_free(&tok->system_id);
}

th_tokenizer *th_tok_new(void) {
    th_tokenizer *self = PyMem_New(th_tokenizer, 1);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memset(self, 0, sizeof(*self));
    self->resolve_references = 1;
    self->capture_source = 0;
    th_tok_reset(self);
    return self;
}

void th_tok_set_options(th_tokenizer *self, int resolve_references, int capture_source) {
    self->resolve_references = resolve_references;
    self->capture_source = capture_source;
}

void th_tok_capture_locations(th_tokenizer *self, int on) {
    self->capture_locations = on;
}

/* Append ch to buf, recording any allocation failure on the tokenizer; the
   sticky flag keeps the per-character hot paths free of error branches and
   th_tok_next checks it once per call. */
static void push(th_tokenizer *self, th_buf *buf, Py_UCS4 ch) {
    if (buf_push(buf, ch) < 0) /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        self->oom = 1;         /* GCOVR_EXCL_LINE: out-of-memory path, unreachable from a test */
}

int th_error_sink_push(th_error_sink *sink, const char *code, Py_ssize_t line, Py_ssize_t col) {
    if (sink->len == sink->cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(sink->cap + 1), (size_t)sink->cap, 8, sizeof(th_parse_error), &cap, &bytes);
        if (!grew) {   /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            return -1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_parse_error *grown = PyMem_Realloc(sink->items, bytes);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        sink->items = grown;
        sink->cap = (Py_ssize_t)cap;
    }
    sink->items[sink->len++] = (th_parse_error){code, line, col};
    return 0;
}

void th_error_sink_free(th_error_sink *sink) {
    PyMem_Free(sink->items);
    sink->items = NULL;
    sink->len = 0;
    sink->cap = 0;
}

void th_tok_set_error_sink(th_tokenizer *self, th_error_sink *sink) {
    self->err_sink = sink;
}

/* Record a parse error at the current input position. The sink check keeps the
   sink-free path (the standalone tokenizer) free of any per-error work; a sink's
   own allocation failure silently drops the error rather than failing the parse. */
static void tok_error(th_tokenizer *self, const char *code) {
    if (self->err_sink != NULL) {
        th_error_sink_push(self->err_sink, code, self->line, self->col);
    }
}

void th_tok_reset(th_tokenizer *self) {
    self->state = ST_DATA;
    self->oom = 0;
    self->attr = NULL;
    buf_reset(&self->input);
    self->pos = 0;
    self->last_cr = 0;
    self->eof = 0;
    self->line = 1;
    self->col = 0;
    self->mark_line = 1;
    self->mark_col = 0;
    self->mark_pos = 0;
    self->building = 0;
    buf_reset(&self->text);
    self->text_open = 0;
    self->slice_start = 0;
    self->slice_len = 0;
    buf_reset(&self->ref);
    token_reset(&self->tok);
    token_reset(&self->charref_record);
    buf_reset(&self->last_tag);
    buf_reset(&self->temp);
    self->queue_head = 0;
    self->queue_len = 0;
    self->returned = NULL;
    self->done = 0;
    self->eof_code = NULL;
    self->eof_reported = 0;
    /* err_sink is owned and set by the caller (the tree builder); a reset of the
       streaming tokenizer keeps it as configured rather than detaching it */
}

void th_tok_free(th_tokenizer *self) {
    if (self->input_borrowed) {
        buf_init(&self->input);
    }
    buf_free(&self->input);
    buf_free(&self->text);
    buf_free(&self->ref);
    token_free(&self->tok);
    token_free(&self->text_record);
    token_free(&self->charref_record);
    buf_free(&self->oom_attr.name);
    buf_free(&self->oom_attr.value);
    buf_free(&self->last_tag);
    buf_free(&self->temp);
    PyMem_Free(self->attr_seen);
    PyMem_Free(self);
}

void th_tok_switch(th_tokenizer *self, enum th_initial_state state) {
    static const enum state initial_states[] = {ST_DATA, ST_RCDATA, ST_RAWTEXT, ST_SCRIPT, ST_PLAINTEXT, ST_CDATA};
    self->state = initial_states[state];
}

void th_tok_set_cdata(th_tokenizer *self, int allowed) {
    self->cdata_ok = allowed;
}

void th_tok_widen_input(th_tokenizer *self, int kind) {
    self->input.kind = kind;
}

void th_tok_set_initial(th_tokenizer *self, enum th_initial_state state, const Py_UCS4 *last_tag,
                        Py_ssize_t last_tag_len) {
    th_tok_switch(self, state);
    buf_reset(&self->last_tag);
    for (Py_ssize_t index = 0; index < last_tag_len; index++) {
        push(self, &self->last_tag, last_tag[index]);
    }
}

/* Append chunk[from..to) to the input buffer, widening either side as needed. */
static void input_append(th_tokenizer *self, int kind, const void *data, Py_ssize_t from, Py_ssize_t to) {
    th_buf *buf = &self->input;
    Py_ssize_t count = to - from;
    int promote = kind > buf->kind ? buf_promote(buf, kind) : 0;
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (promote < 0 || buf_ensure(buf, (buf->len + count) * buf->kind) < 0) {
        self->oom = 1;
        return;
    }
    /* GCOVR_EXCL_STOP */
    if (kind == buf->kind) {
        memcpy((char *)buf->data + buf->len * kind, (const char *)data + from * kind, (size_t)(count * kind));
        buf->len += count;
    } else {
        /* a narrow chunk into a buffer a wider chunk promoted earlier */
        for (Py_ssize_t index = from; index < to; index++) {
            buf_write(buf, buf->len++, PyUnicode_READ(kind, data, index));
        }
    }
}

/* Append fed code points, normalizing CRLF and CR to LF per the spec's input
   preprocessing so downstream states and emitted text never see '\r'. Runs
   between carriage returns move as one block; only the '\r' handling itself
   is per character. */
/* Reclaim the consumed prefix of the input buffer so a long-lived streaming
   tokenizer does not grow without bound. The only live offsets into the buffer
   are `pos` and, while a text run is open, `slice_start`; an emitted record still
   spanning the buffer (queue_len != 0) pins it, so skip until it has drained. */
static void compact_input(th_tokenizer *self) {
    if (self->queue_len != 0) {
        return;
    }
    Py_ssize_t keep_from = self->slice_len > 0 ? self->slice_start : self->pos;
    /* a markup token whose source is being captured spans from its opening '<'
       (mark_pos) to a '>' not yet seen, so its prefix must survive the reclaim */
    if (self->capture_source && self->building && self->mark_pos < keep_from) {
        keep_from = self->mark_pos;
    }
    if (keep_from <= 0) {
        return;
    }
    th_buf *buf = &self->input;
    Py_ssize_t remaining = buf->len - keep_from;
    if (remaining > 0) {
        memmove(buf->data, (char *)buf->data + keep_from * buf->kind, (size_t)(remaining * buf->kind));
    }
    buf->len = remaining;
    self->pos -= keep_from;
    self->slice_start = 0;
    self->mark_pos -= keep_from;
}

void th_tok_feed(th_tokenizer *self, int kind, const void *data, Py_ssize_t length) {
    if (self->pos > 0) { /* nothing is consumed yet on a one-shot or first feed, so skip the call */
        compact_input(self);
    }
    Py_ssize_t start = 0;
    if (length > 0 && self->last_cr) {
        self->last_cr = 0;
        if (PyUnicode_READ(kind, data, 0) == '\n') {
            start = 1; /* the CR was already appended as LF; drop the LF of CRLF */
        }
    }
    while (start < length) {
        Py_ssize_t cr;
        if (kind == PyUnicode_1BYTE_KIND) {
            const char *hit = memchr((const char *)data + start, '\r', (size_t)(length - start));
            cr = hit ? hit - (const char *)data : length;
        } else {
            cr = start;
            while (cr < length && PyUnicode_READ(kind, data, cr) != '\r') {
                cr++;
            }
        }
        input_append(self, kind, data, start, cr);
        if (cr == length) {
            return;
        }
        push(self, &self->input, '\n');
        if (cr + 1 < length) {
            start = cr + 1 + (PyUnicode_READ(kind, data, cr + 1) == '\n');
        } else {
            self->last_cr = 1;
            return;
        }
    }
}

void th_tok_borrow_input(th_tokenizer *self, int kind, const void *data, Py_ssize_t length) {
    self->input.data = (void *)data;
    self->input.len = length;
    self->input.cap = 0;
    self->input.kind = kind;
    self->input_borrowed = 1;
}

const void *th_tok_input_data(const th_tokenizer *self, int *kind) {
    *kind = self->input.kind;
    return self->input.data;
}

void th_tok_close(th_tokenizer *self) {
    self->eof = 1;
}

static void enqueue(th_tokenizer *self, th_token *tok) {
    self->queue[(self->queue_head + self->queue_len) % 2] = tok;
    self->queue_len++;
}

/* Move the coalesced run into text_record (swapping storage, no copy) and
   queue it. */
static void flush_text(th_tokenizer *self) {
    self->text_open = 0;
    th_token *rec = &self->text_record;
    if (self->slice_len > 0) {
        /* the run is an untouched span of the input: hand out indices only */
        token_reset(rec);
        rec->kind = TH_TEXT;
        rec->is_slice = 1;
        rec->src_start = self->slice_start;
        rec->src_len = self->slice_len;
        rec->line = self->text_line;
        rec->col = self->text_col;
        self->slice_len = 0;
        enqueue(self, rec);
        return;
    }
    if (self->text.len == 0) {
        return;
    }
    token_reset(rec);
    rec->kind = TH_TEXT;
    rec->is_slice = 0;
    rec->line = self->text_line;
    rec->col = self->text_col;
    th_buf swap = rec->text;
    rec->text = self->text;
    self->text = swap;
    buf_reset(&self->text);
    enqueue(self, rec);
}

/* Append the input span [start, start+count) to an arbitrary buffer with one bulk
   copy (or a widening loop), used to scan quoted attribute values in blocks
   rather than character by character. */
static void buf_append_input(th_tokenizer *self, th_buf *buf, Py_ssize_t start, Py_ssize_t count) {
    int promote = self->input.kind > buf->kind ? buf_promote(buf, self->input.kind) : 0;
    /* GCOVR_EXCL_START: allocation failure cannot be forced from a test */
    if (promote < 0 || buf_ensure(buf, (buf->len + count) * buf->kind) < 0) {
        self->oom = 1;
        return;
    }
    /* GCOVR_EXCL_STOP */
    if (self->input.kind == buf->kind) {
        memcpy((char *)buf->data + buf->len * buf->kind, (const char *)self->input.data + start * buf->kind,
               (size_t)(count * buf->kind));
        buf->len += count;
    } else {
        for (Py_ssize_t index = 0; index < count; index++) {
            buf_write(buf, buf->len++, buf_read(&self->input, start + index));
        }
    }
}

/* Copy the input span [start, start+count) into the text buffer, widening when the
   input is at a wider width than the text. */
static void copy_input_range(th_tokenizer *self, Py_ssize_t start, Py_ssize_t count) {
    buf_append_input(self, &self->text, start, count);
}

/* Copy the pending slice span into the text buffer; from here on the run is
   materialized (a synthesized character or a reference is about to land). */
static void text_materialize(th_tokenizer *self) {
    if (self->slice_len > 0) {
        copy_input_range(self, self->slice_start, self->slice_len);
        self->slice_len = 0;
    }
}

static void text_push(th_tokenizer *self, Py_UCS4 ch) {
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->line;
        self->text_col = self->col;
    }
    /* a character whose value sits at the cursor extends (or starts) the
       zero-copy span; anything synthesized materializes the run first */
    if (self->text.len == 0 && self->pos < self->input.len && ch == buf_read(&self->input, self->pos) &&
        (self->slice_len == 0 || self->pos == self->slice_start + self->slice_len)) {
        if (self->slice_len == 0) {
            self->slice_start = self->pos;
        }
        self->slice_len++;
        return;
    }
    text_materialize(self);
    push(self, &self->text, ch);
}

/* Queue the markup token, flushing any pending text run ahead of it. The
   verbatim source runs from the opening '<' (src_off, stamped when the token
   began) to the current cursor, just past the '>' the emit consumed. */
static void emit_tok(th_tokenizer *self) {
    if (self->capture_source) {
        self->tok.src_span = self->pos - self->tok.src_off;
        self->tok.has_source = 1;
    }
    self->building = 0;
    /* the construct is complete, so a later EOF no longer reports against it */
    self->eof_code = NULL;
    flush_text(self);
    enqueue(self, &self->tok);
}

/* Record where the markup token under construction began, so its source span
   covers the opening '<' (still protected from compaction since MARK) once the
   closing '>' is reached. */
static void begin_markup_source(th_tokenizer *self) {
    self->tok.src_off = self->mark_pos;
}

static void start_tag(th_tokenizer *self, int end_tag, Py_UCS4 first) {
    token_reset(&self->tok);
    self->tok.kind = end_tag ? TH_END_TAG : TH_START_TAG;
    self->tok.line = self->mark_line;
    self->tok.col = self->mark_col;
    begin_markup_source(self);
    self->eof_code = "eof-in-tag"; /* until the tag is emitted, EOF is eof-in-tag */
    push(self, &self->tok.name, first);
}

/* Begin a new attribute and point self->attr at it. On allocation failure the
   oom_attr sink keeps subsequent appends writing into valid storage until the
   sticky flag is reported. */
static void new_attr(th_tokenizer *self) {
    th_token *tok = &self->tok;
    if (tok->attr_count == tok->attr_cap) {
        size_t cap;
        size_t bytes;
        int grew = th_grow_cap((size_t)(tok->attr_cap + 1), (size_t)tok->attr_cap, 4, sizeof(th_attr), &cap, &bytes);
        if (!grew) {       /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
            self->oom = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            self->attr = &self->oom_attr; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
            return;                       /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        }
        th_attr *grown = PyMem_Realloc(tok->attrs, bytes);
        if (grown == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            self->oom = 1;                /* GCOVR_EXCL_LINE: out-of-memory path, unreachable from a test */
            self->attr = &self->oom_attr; /* GCOVR_EXCL_LINE: out-of-memory path, unreachable from a test */
            return;                       /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        for (Py_ssize_t index = tok->attr_cap; index < (Py_ssize_t)cap; index++) {
            buf_init(&grown[index].name);
            buf_init(&grown[index].value);
            grown[index].has_value = 0;
        }
        tok->attrs = grown;
        tok->attr_cap = cap;
    }
    th_attr *attr = &tok->attrs[tok->attr_count++];
    buf_reset(&attr->name);
    buf_reset(&attr->value);
    attr->has_value = 0;
    if (self->capture_locations) {
        /* the cursor sits on the first name code point (or the '=' a name may open
           with); the end span is filled as the name and any value complete */
        attr->name_line = self->line;
        attr->name_col = self->col;
        attr->name_off = self->pos;
    }
    self->attr = attr;
}

/* Two attribute names are the same when they hold identical code points. Reading
   per code point compares across storage widths without a width guard, so a hash
   collision between a narrow and a wide name of equal length still resolves
   correctly. */
static int attr_name_dup(const th_attr *a, const th_attr *b) {
    if (a->name.len != b->name.len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < a->name.len; index++) {
        if (PyUnicode_READ(a->name.kind, a->name.data, index) != PyUnicode_READ(b->name.kind, b->name.data, index)) {
            return 0;
        }
    }
    return 1;
}

/* FNV-1a over a name's raw storage bytes. Equal names share width and content
   (buffers reset to the 1-byte kind and promote only on demand), so equal names
   always hash equal; a hash collision between distinct names is resolved by
   attr_name_dup. */
static uint32_t attr_name_hash(const th_buf *name) {
    const unsigned char *bytes = name->data;
    Py_ssize_t count = name->len * name->kind;
    uint32_t hash = 2166136261u;
    for (Py_ssize_t index = 0; index < count; index++) {
        hash = (hash ^ bytes[index]) * 16777619u;
    }
    return hash;
}

/* Return 1 when the tag already carries attrs[cur]'s name, else record cur and
   return 0. Open addressing keyed by the name hash keeps duplicate detection
   O(1) amortized, so a tag with n distinct attributes costs O(n) rather than the
   O(n^2) a pairwise scan would pay on adversarial input. When the table crosses
   half full it doubles and re-seats attrs[0, cur) -- which by construction are
   exactly the distinct names kept so far, since a dropped duplicate never holds
   a permanent slot; the fresh table is zeroed to epoch 0, which no tag uses. */
static int attr_seen_probe(th_tokenizer *self, Py_ssize_t cur) {
    const th_attr *attrs = self->tok.attrs;
    if (cur * 2 >= self->attr_seen_cap) { /* keep the load factor <= 1/2 */
        Py_ssize_t cap = self->attr_seen_cap ? self->attr_seen_cap * 2 : 16;
        th_attr_slot *table = PyMem_New(th_attr_slot, (size_t)cap); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (table == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            self->oom = 1;   /* GCOVR_EXCL_LINE: allocation-failure path */
            return 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memset(table, 0, (size_t)cap * sizeof(*table));
        PyMem_Free(self->attr_seen);
        self->attr_seen = table;
        self->attr_seen_cap = cap;
        Py_ssize_t rmask = cap - 1;
        for (Py_ssize_t index = 0; index < cur; index++) {
            Py_ssize_t rslot = (Py_ssize_t)(attrs[index].name_hash & (uint32_t)rmask);
            while (table[rslot].epoch == self->attr_epoch) {
                rslot = (rslot + 1) & rmask;
            }
            table[rslot].epoch = self->attr_epoch;
            table[rslot].index = index;
        }
    }
    Py_ssize_t mask = self->attr_seen_cap - 1;
    Py_ssize_t slot = (Py_ssize_t)(attrs[cur].name_hash & (uint32_t)mask);
    while (self->attr_seen[slot].epoch == self->attr_epoch) {
        if (attr_name_dup(&attrs[self->attr_seen[slot].index], &attrs[cur])) {
            return 1;
        }
        slot = (slot + 1) & mask;
    }
    self->attr_seen[slot].epoch = self->attr_epoch;
    self->attr_seen[slot].index = cur;
    return 0;
}

/* Extend the current attribute's source span to the cursor (a no-op unless
   locations are being captured). Called on leaving the name state and after each
   value delimiter, so the span ends past the last name or value code point. */
static void attr_end_here(th_tokenizer *self) {
    if (self->capture_locations) {
        self->attr->end_line = self->line;
        self->attr->end_col = self->col;
        self->attr->end_off = self->pos;
    }
}

/* Per WHATWG, on leaving the attribute name state: if the token already carries
   an attribute with this exact name it is a duplicate-attribute parse error and
   the new attribute is dropped, so the first occurrence wins. The dropped slot's
   value is routed to the oom sink so the value states keep writing into valid
   storage. The first attribute of each tag bumps the epoch, which clears the
   duplicate index in O(1) without walking the table. */
static void finish_attr_name(th_tokenizer *self) {
    attr_end_here(self); /* a valueless attribute ends here; a value overwrites it */
    th_token *tok = &self->tok;
    Py_ssize_t cur = tok->attr_count - 1;
    if (cur == 0) {
        /* a tag's first attribute starts a fresh epoch, clearing the duplicate index in
           O(1); a 64-bit counter bumped once per tag cannot wrap in any real run */
        self->attr_epoch++;
    }
    tok->attrs[cur].name_hash = attr_name_hash(&tok->attrs[cur].name);
    if (attr_seen_probe(self, cur)) {
        tok_error(self, "duplicate-attribute");
        tok->attr_count = cur;
        self->attr = &self->oom_attr;
    }
}

/* When a start tag is emitted, remember its name for appropriate-end-tag
   checks; spec discards attributes on end tags but we keep the structure. */
static void remember_start_tag(th_tokenizer *self) {
    if (buf_copy(&self->last_tag, &self->tok.name) < 0) /* GCOVR_EXCL_BR_LINE: allocation failure */
        self->oom = 1;                                  /* GCOVR_EXCL_LINE: alloc-failure path */
}

/* The accumulated end-tag name matches the last start tag (appropriate end
   tag). Only such an end tag closes a raw-text/RCDATA/script element. */
static int appropriate_end_tag(th_tokenizer *self) {
    if (self->tok.name.len != self->last_tag.len) {
        return 0;
    }
    if (self->tok.name.kind != self->last_tag.kind) {
        return 0;
    }
    return memcmp(self->tok.name.data, self->last_tag.data, (size_t)(self->tok.name.len * self->tok.name.kind)) == 0;
}

static void text_begin(th_tokenizer *self) {
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->line;
        self->text_col = self->col;
    }
}

/* Open a text run at the marked '<', used when a tag-ish construct turns out to
   be text after its opening characters were consumed. The construct produced no
   markup token, so its source mark no longer needs protecting. */
static void text_begin_mark(th_tokenizer *self) {
    self->building = 0;
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->mark_line;
        self->text_col = self->mark_col;
    }
}

/* Append input[pos..stop) to the text buffer in one step, advancing the
   line/column counters by the run's newline structure. The plain-text states
   scan ahead for their next special character and move whole runs here instead
   of dispatching per character (html5ever and lexbor take the same fast path).
   newlines is how many '\n' the run holds and col the column the run leaves the
   cursor at (characters after its last newline); a newline-free run passes 0/0
   and only advances the column by its length. */
static void text_append_run_lc(th_tokenizer *self, Py_ssize_t stop, Py_ssize_t newlines, Py_ssize_t col) {
    Py_ssize_t count = stop - self->pos;
    text_begin(self);
    if (self->text.len == 0 && (self->slice_len == 0 || self->pos == self->slice_start + self->slice_len)) {
        /* still an untouched span of the input: extend the indices, copy nothing */
        if (self->slice_len == 0) {
            self->slice_start = self->pos;
        }
        self->slice_len += count;
    } else {
        text_materialize(self);
        copy_input_range(self, self->pos, count);
    }
    self->pos = stop;
    if (newlines > 0) {
        self->line += newlines;
        self->col = col;
    } else {
        self->col += count;
    }
}

/* Append a run guaranteed free of newlines (every plain-text state but DATA,
   whose scan stops at every newline). */
static void text_append_run(th_tokenizer *self, Py_ssize_t stop) {
    text_append_run_lc(self, stop, 0, 0);
}

static void init_markup(th_tokenizer *self, enum th_kind kind) {
    token_reset(&self->tok);
    self->tok.kind = kind;
    self->tok.line = self->mark_line;
    self->tok.col = self->mark_col;
    begin_markup_source(self);
}

/* Queue a TH_CHARREF token for an unresolved reference: the resolved code points
   (already decoded into self->ref) become its data, and [amp, amp + consumed) is
   its verbatim source. Flush any pending text run ahead of it so order holds. */
static void emit_charref(th_tokenizer *self, Py_ssize_t amp, Py_ssize_t consumed) {
    flush_text(self);
    th_token *rec = &self->charref_record;
    token_reset(rec);
    rec->kind = TH_CHARREF;
    th_buf swap = rec->text;
    rec->text = self->ref;
    self->ref = swap;
    buf_reset(&self->ref);
    rec->src_off = amp;
    rec->src_span = consumed;
    rec->has_source = 1;
    rec->line = self->ref_line;
    rec->col = self->ref_col;
    enqueue(self, rec);
}

/* Emit "</" plus the raw end-tag-name characters held in temp as text, used
   when a raw-text/RCDATA/script end tag turns out not to match. */
static void rawtext_fallback(th_tokenizer *self, enum state ret) {
    text_begin_mark(self);
    text_push(self, '<');
    text_push(self, '/');
    for (Py_ssize_t index = 0; index < self->temp.len; index++) {
        text_push(self, buf_read(&self->temp, index));
    }
    self->state = ret;
}

static void finish_tag(th_tokenizer *self) {
    self->state = ST_DATA;
    if (self->capture_locations) {
        /* the cursor has just passed the closing '>', so it marks the tag's end span */
        self->tok.end_line = self->line;
        self->tok.end_col = self->col;
        self->tok.end_off = self->pos;
    }
    if (self->tok.kind == TH_START_TAG) {
        remember_start_tag(self);
    }
    emit_tok(self);
}

enum run_result { RUN_EMITTED, RUN_NEED_MORE, RUN_DONE };

/* Consume the current code point, keeping the line/column counters current.
   Branchless so call sites where the character is already known (and never a
   newline) carry no untakeable branch. */
#define CONSUME()                                                                                                      \
    do {                                                                                                               \
        Py_ssize_t nl = ch == '\n';                                                                                    \
        self->line += nl;                                                                                              \
        self->col = (self->col + 1) * (1 - nl);                                                                        \
        self->pos++;                                                                                                   \
    } while (0)

/* Remember the position of a '<' about to be consumed; tokens it opens and
   text fallbacks that re-emit it report this position (Token.line/col). */
/* The marked '<' opens a construct that may become a token whose source span
   starts here, so from now until it resolves to a token (emit_tok) or to text
   (text_begin_mark) its prefix must survive input compaction. */
#define MARK()                                                                                                         \
    do {                                                                                                               \
        self->mark_line = self->line;                                                                                  \
        self->mark_col = self->col;                                                                                    \
        self->mark_pos = self->pos;                                                                                    \
        self->building = 1;                                                                                            \
    } while (0)

/* These two always return, so they are plain blocks rather than do/while(0)
   wrappers: the dead loop-exit edge a while(0) leaves behind lands on the
   caller's closing brace and reads as an uncovered line. */
#define EOF_FLUSH()                                                                                                    \
    {                                                                                                                  \
        flush_text(self);                                                                                              \
        return RUN_DONE;                                                                                               \
    }

/* Emit the markup token and return to the data state; on the EOF paths the
   data state then reports DONE, so a token is never emitted twice. */
#define EMIT_MARKUP()                                                                                                  \
    {                                                                                                                  \
        self->state = ST_DATA;                                                                                         \
        emit_tok(self);                                                                                                \
        return RUN_EMITTED;                                                                                            \
    }

#define TH_ONES UINT64_C(0x0101010101010101)
#define TH_HIGHS UINT64_C(0x8080808080808080)
#define TH_HASZERO(word) (((word) - TH_ONES) & ~(word) & TH_HIGHS)

#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif

/* Find the first of up to four stop bytes (duplicates allowed) in the 1-byte
   input from position index on. A vector loop skips 16-byte blocks containing
   no stop byte (NEON or SSE2, the same shape html5ever's data-state fast path
   uses), falling back to the eight-byte SWAR scan escape.c uses elsewhere;
   the scalar tail then pinpoints the stop, comparing with bitwise or so every
   lane is one branch. */
static Py_ssize_t scan_stops_ucs1(const th_tokenizer *self, Py_ssize_t index, Py_UCS1 s1, Py_UCS1 s2, Py_UCS1 s3,
                                  Py_UCS1 s4) {
    const Py_UCS1 *data = (const Py_UCS1 *)self->input.data;
    Py_ssize_t len = self->input.len;
#if defined(TH_SCAN_NEON)
    uint8x16_t v1 = vdupq_n_u8(s1), v2 = vdupq_n_u8(s2), v3 = vdupq_n_u8(s3), v4 = vdupq_n_u8(s4);
    while (index + 16 <= len) {
        uint8x16_t block = vld1q_u8(data + index);
        uint8x16_t hit = vorrq_u8(vorrq_u8(vceqq_u8(block, v1), vceqq_u8(block, v2)),
                                  vorrq_u8(vceqq_u8(block, v3), vceqq_u8(block, v4)));
        if (vmaxvq_u8(hit)) {
            break;
        }
        index += 16;
    }
#elif defined(TH_SCAN_SSE2)
    __m128i v1 = _mm_set1_epi8((char)s1), v2 = _mm_set1_epi8((char)s2);
    __m128i v3 = _mm_set1_epi8((char)s3), v4 = _mm_set1_epi8((char)s4);
    while (index + 16 <= len) {
        __m128i block = _mm_loadu_si128((const __m128i *)(data + index));
        __m128i hit = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(block, v1), _mm_cmpeq_epi8(block, v2)),
                                   _mm_or_si128(_mm_cmpeq_epi8(block, v3), _mm_cmpeq_epi8(block, v4)));
        if (_mm_movemask_epi8(hit)) {
            break;
        }
        index += 16;
    }
#else
    uint64_t m1 = TH_ONES * s1, m2 = TH_ONES * s2, m3 = TH_ONES * s3, m4 = TH_ONES * s4;
    while (index + 8 <= len) {
        uint64_t word;
        memcpy(&word, data + index, sizeof(word));
        if (TH_HASZERO(word ^ m1) | TH_HASZERO(word ^ m2) | TH_HASZERO(word ^ m3) | TH_HASZERO(word ^ m4)) {
            break;
        }
        index += 8;
    }
#endif
    while (index < len && !((data[index] == s1) | (data[index] == s2) | (data[index] == s3) | (data[index] == s4))) {
        index++;
    }
    return index;
}

#if defined(TH_SCAN_NEON)
static TH_NOINLINE Py_ssize_t scan_data_ucs1_neon_long(const th_tokenizer *self, Py_ssize_t index, Py_ssize_t newlines,
                                                       Py_ssize_t *out_newlines, Py_ssize_t *out_last_nl) {
    const Py_UCS1 *data = (const Py_UCS1 *)self->input.data;
    Py_ssize_t len = self->input.len;
    uint8x16_t amp = vdupq_n_u8('&'), lt = vdupq_n_u8('<'), nlv = vdupq_n_u8('\n');

    while (index + 32 <= len) {
        uint8x16_t left = vld1q_u8(data + index);
        uint8x16_t left_hit = vorrq_u8(vceqq_u8(left, amp), vceqq_u8(left, lt));
        if (vmaxvq_u8(left_hit)) {
            goto scalar_tail;
        }
        newlines += vaddvq_u8(vshrq_n_u8(vceqq_u8(left, nlv), 7));

        uint8x16_t right = vld1q_u8(data + index + 16);
        uint8x16_t right_hit = vorrq_u8(vceqq_u8(right, amp), vceqq_u8(right, lt));
        if (vmaxvq_u8(right_hit)) {
            index += 16;
            goto scalar_tail;
        }
        newlines += vaddvq_u8(vshrq_n_u8(vceqq_u8(right, nlv), 7));
        index += 32;
    }
    while (index + 16 <= len) {
        uint8x16_t block = vld1q_u8(data + index);
        if (vmaxvq_u8(vorrq_u8(vceqq_u8(block, amp), vceqq_u8(block, lt)))) {
            goto scalar_tail;
        }
        newlines += vaddvq_u8(vshrq_n_u8(vceqq_u8(block, nlv), 7)); /* 0xFF >> 7 == 1 per match */
        index += 16;
    }

scalar_tail:
    while (index < len && data[index] != '&' && data[index] != '<') {
        newlines += data[index] == '\n';
        index++;
    }
    *out_newlines = newlines;
    Py_ssize_t last_nl = -1;
    if (newlines > 0) {
        last_nl = index - 1;
        while (data[last_nl] != '\n') {
            last_nl--;
        }
    }
    *out_last_nl = last_nl;
    return index;
}
#elif defined(TH_SCAN_SSE2)
static TH_NOINLINE Py_ssize_t scan_data_ucs1_sse2_long(const th_tokenizer *self, Py_ssize_t index, Py_ssize_t newlines,
                                                       Py_ssize_t *out_newlines, Py_ssize_t *out_last_nl) {
    const Py_UCS1 *data = (const Py_UCS1 *)self->input.data;
    Py_ssize_t len = self->input.len;
    __m128i amp = _mm_set1_epi8('&'), lt = _mm_set1_epi8('<'), nlv = _mm_set1_epi8('\n');
    __m128i onesv = _mm_set1_epi8(1), zerov = _mm_setzero_si128();

    while (index + 32 <= len) {
        __m128i left = _mm_loadu_si128((const __m128i *)(data + index));
        __m128i left_hit = _mm_or_si128(_mm_cmpeq_epi8(left, amp), _mm_cmpeq_epi8(left, lt));
        if (_mm_movemask_epi8(left_hit)) {
            goto scalar_tail;
        }
        __m128i ones = _mm_and_si128(_mm_cmpeq_epi8(left, nlv), onesv);
        __m128i sums = _mm_sad_epu8(ones, zerov);
        newlines += _mm_cvtsi128_si32(sums) + _mm_extract_epi16(sums, 4);

        __m128i right = _mm_loadu_si128((const __m128i *)(data + index + 16));
        __m128i right_hit = _mm_or_si128(_mm_cmpeq_epi8(right, amp), _mm_cmpeq_epi8(right, lt));
        if (_mm_movemask_epi8(right_hit)) {
            index += 16;
            goto scalar_tail;
        }
        ones = _mm_and_si128(_mm_cmpeq_epi8(right, nlv), onesv);
        sums = _mm_sad_epu8(ones, zerov);
        newlines += _mm_cvtsi128_si32(sums) + _mm_extract_epi16(sums, 4);
        index += 32;
    }
    while (index + 16 <= len) {
        __m128i block = _mm_loadu_si128((const __m128i *)(data + index));
        if (_mm_movemask_epi8(_mm_or_si128(_mm_cmpeq_epi8(block, amp), _mm_cmpeq_epi8(block, lt)))) {
            goto scalar_tail;
        }
        /* sum one-per-newline across the lanes with SAD (escape.c's reduction), so
           the count needs no popcount intrinsic MSVC lacks */
        __m128i ones = _mm_and_si128(_mm_cmpeq_epi8(block, nlv), onesv);
        __m128i sums = _mm_sad_epu8(ones, zerov);
        newlines += _mm_cvtsi128_si32(sums) + _mm_extract_epi16(sums, 4);
        index += 16;
    }

scalar_tail:
    while (index < len && data[index] != '&' && data[index] != '<') {
        newlines += data[index] == '\n';
        index++;
    }
    *out_newlines = newlines;
    Py_ssize_t last_nl = -1;
    if (newlines > 0) {
        last_nl = index - 1;
        while (data[last_nl] != '\n') {
            last_nl--;
        }
    }
    *out_last_nl = last_nl;
    return index;
}
#endif

/* The DATA-state text scan: find the next '&' or '<' from index on while folding
   in line/column tracking, so a multi-line prose run between tags is one vector
   sweep rather than one scan per wrapped line. *out_newlines receives how many
   newlines the run [index, return) holds, and *out_last_nl the index of its last
   newline (-1 when none), from which the caller derives the ending column. The
   per-block newline count is a SIMD population count; the last newline alone
   needs a position, found by a short backward scan bounded by the final line. */
static Py_ssize_t scan_data_ucs1(const th_tokenizer *self, Py_ssize_t index, Py_ssize_t *out_newlines,
                                 Py_ssize_t *out_last_nl) {
    const Py_UCS1 *data = (const Py_UCS1 *)self->input.data;
    Py_ssize_t len = self->input.len;
    Py_ssize_t newlines = 0;
#if defined(TH_SCAN_NEON)
    uint8x16_t amp = vdupq_n_u8('&'), lt = vdupq_n_u8('<'), nlv = vdupq_n_u8('\n');
    while (index + 16 <= len) {
        uint8x16_t block = vld1q_u8(data + index);
        if (vmaxvq_u8(vorrq_u8(vceqq_u8(block, amp), vceqq_u8(block, lt)))) {
            break;
        }
        newlines += vaddvq_u8(vshrq_n_u8(vceqq_u8(block, nlv), 7)); /* 0xFF >> 7 == 1 per match */
        index += 16;
        if (index + 32 <= len) {
            return scan_data_ucs1_neon_long(self, index, newlines, out_newlines, out_last_nl);
        }
    }
#elif defined(TH_SCAN_SSE2)
    __m128i amp = _mm_set1_epi8('&'), lt = _mm_set1_epi8('<'), nlv = _mm_set1_epi8('\n');
    while (index + 16 <= len) {
        __m128i block = _mm_loadu_si128((const __m128i *)(data + index));
        if (_mm_movemask_epi8(_mm_or_si128(_mm_cmpeq_epi8(block, amp), _mm_cmpeq_epi8(block, lt)))) {
            break;
        }
        /* sum one-per-newline across the lanes with SAD (escape.c's reduction), so
           the count needs no popcount intrinsic MSVC lacks */
        __m128i ones = _mm_and_si128(_mm_cmpeq_epi8(block, nlv), _mm_set1_epi8(1));
        __m128i sums = _mm_sad_epu8(ones, _mm_setzero_si128());
        newlines += _mm_cvtsi128_si32(sums) + _mm_extract_epi16(sums, 4);
        index += 16;
        if (index + 32 <= len) {
            return scan_data_ucs1_sse2_long(self, index, newlines, out_newlines, out_last_nl);
        }
    }
#else
    uint64_t amp = TH_ONES * '&', lt = TH_ONES * '<', nlv = TH_ONES * '\n';
    while (index + 8 <= len) {
        uint64_t word;
        memcpy(&word, data + index, sizeof(word));
        if (TH_HASZERO(word ^ amp) | TH_HASZERO(word ^ lt)) {
            break;
        }
        newlines += (Py_ssize_t)(((TH_HASZERO(word ^ nlv) >> 7) * TH_ONES) >> 56);
        index += 8;
    }
#endif
    while (index < len && data[index] != '&' && data[index] != '<') {
        newlines += data[index] == '\n';
        index++;
    }
    *out_newlines = newlines;
    /* newlines > 0 guarantees a '\n' lies in [start, index), so the backward walk
       is bounded by that newline and needs no explicit start guard (the same
       guaranteed-terminator pattern th_node_doctype_ids uses). */
    Py_ssize_t last_nl = -1;
    if (newlines > 0) {
        last_nl = index - 1;
        while (data[last_nl] != '\n') {
            last_nl--;
        }
    }
    *out_last_nl = last_nl;
    return index;
}

/* The 2-byte twin of scan_stops_ucs1: the stop set is ASCII, so a code point
   that is not a stop never equals one of the narrow comparands. */
static Py_ssize_t scan_stops_ucs2(const th_tokenizer *self, Py_ssize_t index, Py_UCS2 s1, Py_UCS2 s2, Py_UCS2 s3,
                                  Py_UCS2 s4) {
    const Py_UCS2 *data = (const Py_UCS2 *)self->input.data;
    Py_ssize_t len = self->input.len;
#if defined(TH_SCAN_NEON)
    uint16x8_t v1 = vdupq_n_u16(s1), v2 = vdupq_n_u16(s2), v3 = vdupq_n_u16(s3), v4 = vdupq_n_u16(s4);
    while (index + 8 <= len) {
        uint16x8_t block = vld1q_u16(data + index);
        uint16x8_t hit = vorrq_u16(vorrq_u16(vceqq_u16(block, v1), vceqq_u16(block, v2)),
                                   vorrq_u16(vceqq_u16(block, v3), vceqq_u16(block, v4)));
        if (vmaxvq_u16(hit)) {
            break;
        }
        index += 8;
    }
#elif defined(TH_SCAN_SSE2)
    __m128i v1 = _mm_set1_epi16((short)s1), v2 = _mm_set1_epi16((short)s2);
    __m128i v3 = _mm_set1_epi16((short)s3), v4 = _mm_set1_epi16((short)s4);
    while (index + 8 <= len) {
        __m128i block = _mm_loadu_si128((const __m128i *)(data + index));
        __m128i hit = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi16(block, v1), _mm_cmpeq_epi16(block, v2)),
                                   _mm_or_si128(_mm_cmpeq_epi16(block, v3), _mm_cmpeq_epi16(block, v4)));
        if (_mm_movemask_epi8(hit)) {
            break;
        }
        index += 8;
    }
#endif
    while (index < len && !((data[index] == s1) | (data[index] == s2) | (data[index] == s3) | (data[index] == s4))) {
        index++;
    }
    return index;
}

/* The 4-byte twin of scan_stops_ucs1. */
static Py_ssize_t scan_stops_ucs4(const th_tokenizer *self, Py_ssize_t index, Py_UCS4 s1, Py_UCS4 s2, Py_UCS4 s3,
                                  Py_UCS4 s4) {
    const Py_UCS4 *data = (const Py_UCS4 *)self->input.data;
    Py_ssize_t len = self->input.len;
#if defined(TH_SCAN_NEON)
    uint32x4_t v1 = vdupq_n_u32(s1), v2 = vdupq_n_u32(s2), v3 = vdupq_n_u32(s3), v4 = vdupq_n_u32(s4);
    while (index + 4 <= len) {
        uint32x4_t block = vld1q_u32(data + index);
        uint32x4_t hit = vorrq_u32(vorrq_u32(vceqq_u32(block, v1), vceqq_u32(block, v2)),
                                   vorrq_u32(vceqq_u32(block, v3), vceqq_u32(block, v4)));
        if (vmaxvq_u32(hit)) {
            break;
        }
        index += 4;
    }
#elif defined(TH_SCAN_SSE2)
    __m128i v1 = _mm_set1_epi32((int)s1), v2 = _mm_set1_epi32((int)s2);
    __m128i v3 = _mm_set1_epi32((int)s3), v4 = _mm_set1_epi32((int)s4);
    while (index + 4 <= len) {
        __m128i block = _mm_loadu_si128((const __m128i *)(data + index));
        __m128i hit = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi32(block, v1), _mm_cmpeq_epi32(block, v2)),
                                   _mm_or_si128(_mm_cmpeq_epi32(block, v3), _mm_cmpeq_epi32(block, v4)));
        if (_mm_movemask_epi8(hit)) {
            break;
        }
        index += 4;
    }
#endif
    while (index < len && !((data[index] == s1) | (data[index] == s2) | (data[index] == s3) | (data[index] == s4))) {
        index++;
    }
    return index;
}

/* Stamp the tokenizer core once per input storage width. */
#define TH_NAME(name) name##_ucs1
#define TH_CHAR Py_UCS1
#define TH_KIND PyUnicode_1BYTE_KIND
#define TH_UCS1 1
#define TH_SCAN scan_stops_ucs1
#include "tokenizer/statemachine_run.h"
#undef TH_NAME
#undef TH_CHAR
#undef TH_KIND
#undef TH_UCS1
#undef TH_SCAN

#define TH_NAME(name) name##_ucs2
#define TH_CHAR Py_UCS2
#define TH_KIND PyUnicode_2BYTE_KIND
#define TH_UCS1 0
#define TH_SCAN scan_stops_ucs2
#include "tokenizer/statemachine_run.h"
#undef TH_NAME
#undef TH_CHAR
#undef TH_KIND
#undef TH_UCS1
#undef TH_SCAN

#define TH_NAME(name) name##_ucs4
#define TH_CHAR Py_UCS4
#define TH_KIND PyUnicode_4BYTE_KIND
#define TH_UCS1 0
#define TH_SCAN scan_stops_ucs4
#include "tokenizer/statemachine_run.h"
#undef TH_NAME
#undef TH_CHAR
#undef TH_KIND
#undef TH_UCS1
#undef TH_SCAN

static enum run_result run(th_tokenizer *self) {
    if (self->input.kind == PyUnicode_1BYTE_KIND) {
        return run_ucs1(self);
    }
    if (self->input.kind == PyUnicode_2BYTE_KIND) {
        return run_ucs2(self);
    }
    return run_ucs4(self);
}

enum th_step th_tok_next(th_tokenizer *self, th_token **out) {
    if (self->returned != NULL) {
        token_reset(self->returned);
        self->returned = NULL;
    }
    for (;;) {
        if (self->oom) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return TH_STEP_ERROR; /* GCOVR_EXCL_LINE: the only step error is an out-of-memory condition */
        }
        if (self->queue_len > 0) {
            th_token *tok = self->queue[self->queue_head];
            self->queue_head = (self->queue_head + 1) % 2;
            self->queue_len--;
            self->returned = tok;
            *out = tok;
            return TH_STEP_TOKEN;
        }
        if (self->done) {
            return TH_STEP_DONE;
        }
        switch (run(self)) { /* GCOVR_EXCL_BR_LINE: enum-complete switch; the out-of-range edge is unreachable */
        case RUN_NEED_MORE:
            return TH_STEP_NEED_MORE;
        case RUN_DONE:
            self->done = 1;
            continue;
        case RUN_EMITTED:
            continue;
        }
    }
}
