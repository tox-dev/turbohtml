/* WHATWG HTML tokenizer state machine.

   The machine consumes code points from an owned input buffer one at a time.
   Each state is a label in the big switch in run(); the structure mirrors the
   spec's "tokenizer" section so the two can be read side by side. Output is
   produced through two reusable token records — one for coalesced text runs and
   one for the markup token (tag, comment, doctype) that ends a run — held in a
   short pending queue and handed to the caller one at a time.

   Resumption: when the machine needs a character that has not been fed yet and
   end-of-file has not been signaled, it rewinds to the last safe point and
   reports NEED_MORE. Two states use multi-character lookahead (markup
   declaration open and the character-reference helper); both save the position
   of the opening character so a rewind re-runs them cleanly once more input
   arrives. Everything else consumes a single character per step, so suspending
   is just leaving the state unchanged and returning.

   Parse errors defined by the spec are handled (the recovery transitions are
   taken) but not reported; the public API exposes the token stream, not the
   error stream. */

#include "tokenizer_sm.h"

#include <string.h>

#include "charref.h"

#define REPLACEMENT 0xFFFD

/* ------------------------------------------------------------------ buffers */

static void buf_init(th_buf *buf) {
    buf->data = NULL;
    buf->len = 0;
    buf->cap = 0;
}

static void buf_free(th_buf *buf) {
    PyMem_Free(buf->data);
    buf_init(buf);
}

static void buf_reset(th_buf *buf) {
    buf->len = 0;
}

static int buf_push(th_buf *buf, Py_UCS4 ch) {
    if (buf->len == buf->cap) {
        Py_ssize_t cap = buf->cap ? buf->cap * 2 : 16;
        Py_UCS4 *grown = PyMem_Resize(buf->data, Py_UCS4, (size_t)cap); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        buf->data = grown;
        buf->cap = cap;
    }
    buf->data[buf->len++] = ch;
    return 0;
}

/* ------------------------------------------------------------------- states */

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

/* --------------------------------------------------------------- tokenizer */

struct th_tokenizer {
    enum state state;
    int oom; /* an allocation failed; reported once as TH_STEP_ERROR */

    th_buf input;    /* owned, newline-normalized code points */
    Py_ssize_t pos;  /* next code point to read */
    int last_cr;     /* the last fed code point was '\r' (CRLF may span feeds) */
    int eof;         /* close() was called */
    Py_ssize_t line; /* 1-based, advanced as input is consumed */
    Py_ssize_t col;
    Py_ssize_t mark_line; /* position of the '<' that opened the current tag-ish
                             construct; tokens and '<' text fallbacks begin here */
    Py_ssize_t mark_col;

    th_buf text; /* coalesced character run */
    Py_ssize_t text_line;
    Py_ssize_t text_col;
    int text_open; /* a run is in progress */

    th_token tok;     /* tag/comment/doctype under construction */
    th_attr *attr;    /* attribute under construction (points into tok.attrs) */
    th_attr oom_attr; /* writable sink for attribute data after an allocation failure */
    th_buf last_tag;  /* last emitted start tag name, for appropriate end tags */
    th_buf temp;      /* spec "temporary buffer" for raw-text end tags and script */

    th_token text_record; /* materialized text run handed to the caller */

    th_token *queue[2];
    int queue_head;
    int queue_len;
    th_token *returned; /* reset at the next call so its buffers can be reused */
    int done;
};

static void token_reset(th_token *tok) {
    buf_reset(&tok->name);
    buf_reset(&tok->text);
    for (Py_ssize_t i = 0; i < tok->attr_count; i++) {
        buf_reset(&tok->attrs[i].name);
        buf_reset(&tok->attrs[i].value);
        tok->attrs[i].has_value = 0;
    }
    tok->attr_count = 0;
    tok->self_closing = 0;
    buf_reset(&tok->public_id);
    buf_reset(&tok->system_id);
    tok->has_public_id = 0;
    tok->has_system_id = 0;
    tok->force_quirks = 0;
}

static void token_free(th_token *tok);

static int buf_copy(th_buf *dst, const th_buf *src) {
    buf_reset(dst);
    for (Py_ssize_t i = 0; i < src->len; i++) {
        if (buf_push(dst, src->data[i]) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                         /* GCOVR_EXCL_LINE */
        }
    }
    return 0;
}

int th_token_copy(th_token *dst, const th_token *src) {
    dst->kind = src->kind;
    dst->self_closing = src->self_closing;
    dst->has_public_id = src->has_public_id;
    dst->has_system_id = src->has_system_id;
    dst->force_quirks = src->force_quirks;
    dst->line = src->line;
    dst->col = src->col;
    /* failures accumulate bitwise so no per-copy error branch is emitted */
    int failed = buf_copy(&dst->name, &src->name) | buf_copy(&dst->text, &src->text) |
                 buf_copy(&dst->public_id, &src->public_id) | buf_copy(&dst->system_id, &src->system_id);
    if (src->attr_count > dst->attr_cap) {
        th_attr *grown =
            PyMem_Resize(dst->attrs, th_attr, (size_t)src->attr_count); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t i = dst->attr_cap; i < src->attr_count; i++) {
            buf_init(&grown[i].name);
            buf_init(&grown[i].value);
        }
        dst->attrs = grown;
        dst->attr_cap = src->attr_count;
    }
    for (Py_ssize_t i = 0; i < src->attr_count; i++) {
        failed |= buf_copy(&dst->attrs[i].name, &src->attrs[i].name);
        failed |= buf_copy(&dst->attrs[i].value, &src->attrs[i].value);
        dst->attrs[i].has_value = src->attrs[i].has_value;
    }
    dst->attr_count = src->attr_count;
    return failed;
}

void th_token_clear(th_token *tok) {
    token_free(tok);
}

static void token_free(th_token *tok) {
    buf_free(&tok->name);
    buf_free(&tok->text);
    for (Py_ssize_t i = 0; i < tok->attr_cap; i++) {
        buf_free(&tok->attrs[i].name);
        buf_free(&tok->attrs[i].value);
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
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    memset(self, 0, sizeof(*self));
    th_tok_reset(self);
    return self;
}

/* Append ch to buf, recording any allocation failure on the tokenizer; the
   sticky flag keeps the per-character hot paths free of error branches and is
   checked once per th_tok_next call. */
static void push(th_tokenizer *self, th_buf *buf, Py_UCS4 ch) {
    if (buf_push(buf, ch) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        self->oom = 1;           /* GCOVR_EXCL_LINE */
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
    buf_reset(&self->text);
    self->text_open = 0;
    token_reset(&self->tok);
    buf_reset(&self->last_tag);
    buf_reset(&self->temp);
    self->queue_head = 0;
    self->queue_len = 0;
    self->returned = NULL;
    self->done = 0;
}

void th_tok_free(th_tokenizer *self) {
    buf_free(&self->input);
    buf_free(&self->text);
    token_free(&self->tok);
    token_free(&self->text_record);
    buf_free(&self->oom_attr.name);
    buf_free(&self->oom_attr.value);
    buf_free(&self->last_tag);
    buf_free(&self->temp);
    PyMem_Free(self);
}

void th_tok_switch(th_tokenizer *self, enum th_initial_state state) {
    static const enum state initial_states[] = {ST_DATA, ST_RCDATA, ST_RAWTEXT, ST_SCRIPT, ST_PLAINTEXT, ST_CDATA};
    self->state = initial_states[state];
}

void th_tok_set_initial(th_tokenizer *self, enum th_initial_state state, const Py_UCS4 *last_tag,
                        Py_ssize_t last_tag_len) {
    th_tok_switch(self, state);
    buf_reset(&self->last_tag);
    for (Py_ssize_t i = 0; i < last_tag_len; i++) {
        push(self, &self->last_tag, last_tag[i]);
    }
}

/* Append fed code points, normalizing CRLF and CR to LF per the spec's input
   preprocessing so downstream states and emitted text never see '\r'. The loop
   is stamped once per storage kind so each instantiation reads its width
   directly and vectorizes instead of dispatching per character. */
#define FEED_LOOP(reader)                                                                                              \
    for (Py_ssize_t i = 0; i < length; i++) {                                                                          \
        Py_UCS4 ch = (reader)[i];                                                                                      \
        if (ch == '\n' && self->last_cr) {                                                                             \
            self->last_cr = 0;                                                                                         \
            continue; /* the CR was already appended as LF; drop the LF of CRLF */                                     \
        }                                                                                                              \
        self->last_cr = ch == '\r';                                                                                    \
        push(self, &self->input, ch == '\r' ? '\n' : ch);                                                              \
    }

void th_tok_feed(th_tokenizer *self, int kind, const void *data, Py_ssize_t length) {
    if (kind == PyUnicode_1BYTE_KIND) {
        FEED_LOOP((const Py_UCS1 *)data);
    } else if (kind == PyUnicode_2BYTE_KIND) {
        FEED_LOOP((const Py_UCS2 *)data);
    } else {
        FEED_LOOP((const Py_UCS4 *)data);
    }
}

#undef FEED_LOOP

void th_tok_close(th_tokenizer *self) {
    self->eof = 1;
}

/* --------------------------------------------------------------- emitting */

static void enqueue(th_tokenizer *self, th_token *tok) {
    self->queue[(self->queue_head + self->queue_len) % 2] = tok;
    self->queue_len++;
}

/* Move the coalesced run into text_record (swapping storage, no copy) and
   queue it. */
static void flush_text(th_tokenizer *self) {
    self->text_open = 0;
    if (self->text.len == 0) {
        return;
    }
    th_token *rec = &self->text_record;
    token_reset(rec);
    rec->kind = TH_TEXT;
    rec->line = self->text_line;
    rec->col = self->text_col;
    th_buf swap = rec->text;
    rec->text = self->text;
    self->text = swap;
    buf_reset(&self->text);
    enqueue(self, rec);
}

static void text_push(th_tokenizer *self, Py_UCS4 ch) {
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->line;
        self->text_col = self->col;
    }
    push(self, &self->text, ch);
}

/* Queue the markup token, flushing any pending text run ahead of it. */
static void emit_tok(th_tokenizer *self) {
    flush_text(self);
    enqueue(self, &self->tok);
}

static void start_tag(th_tokenizer *self, int end_tag, Py_UCS4 first) {
    token_reset(&self->tok);
    self->tok.kind = end_tag ? TH_END_TAG : TH_START_TAG;
    self->tok.line = self->mark_line;
    self->tok.col = self->mark_col;
    push(self, &self->tok.name, first);
}

/* Begin a new attribute and point self->attr at it. On allocation failure the
   oom_attr sink keeps subsequent appends writing into valid storage until the
   sticky flag is reported. */
static void new_attr(th_tokenizer *self) {
    th_token *tok = &self->tok;
    if (tok->attr_count == tok->attr_cap) {
        Py_ssize_t cap = tok->attr_cap ? tok->attr_cap * 2 : 4;
        th_attr *grown = PyMem_Resize(tok->attrs, th_attr, (size_t)cap); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (grown == NULL) {              /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            self->oom = 1;                /* GCOVR_EXCL_LINE */
            self->attr = &self->oom_attr; /* GCOVR_EXCL_LINE */
            return;                       /* GCOVR_EXCL_LINE */
        }
        for (Py_ssize_t i = tok->attr_cap; i < cap; i++) {
            buf_init(&grown[i].name);
            buf_init(&grown[i].value);
            grown[i].has_value = 0;
        }
        tok->attrs = grown;
        tok->attr_cap = cap;
    }
    th_attr *attr = &tok->attrs[tok->attr_count++];
    buf_reset(&attr->name);
    buf_reset(&attr->value);
    attr->has_value = 0;
    self->attr = attr;
}

/* When a start tag is emitted, remember its name for appropriate-end-tag
   checks; spec discards attributes on end tags but we keep the structure. */
static void remember_start_tag(th_tokenizer *self) {
    buf_reset(&self->last_tag);
    for (Py_ssize_t i = 0; i < self->tok.name.len; i++) {
        push(self, &self->last_tag, self->tok.name.data[i]);
    }
}

static inline Py_UCS4 lower_ascii(Py_UCS4 ch) {
    return (ch >= 'A' && ch <= 'Z') ? ch + 0x20 : ch;
}

static inline int is_ascii_alpha(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

static inline int is_space(Py_UCS4 ch) {
    return ch == '\t' || ch == '\n' || ch == '\x0c' || ch == ' ';
}

/* The accumulated end-tag name matches the last start tag (appropriate end
   tag) — only such an end tag closes a raw-text/RCDATA/script element. */
static int appropriate_end_tag(th_tokenizer *self) {
    if (self->tok.name.len != self->last_tag.len) {
        return 0;
    }
    return memcmp(self->tok.name.data, self->last_tag.data, (size_t)self->tok.name.len * sizeof(Py_UCS4)) == 0;
}

static void text_begin(th_tokenizer *self) {
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->line;
        self->text_col = self->col;
    }
}

/* Open a text run at the marked '<' — used when a tag-ish construct turns out
   to be text after its opening characters were already consumed. */
static void text_begin_mark(th_tokenizer *self) {
    if (!self->text_open) {
        self->text_open = 1;
        self->text_line = self->mark_line;
        self->text_col = self->mark_col;
    }
}

/* Append input[pos..stop) — a run guaranteed free of newlines — to the text
   buffer in one step. The plain-text states scan ahead for their next special
   character and move whole runs here instead of dispatching per character
   (html5ever and lexbor take the same fast path). */
static void text_append_run(th_tokenizer *self, Py_ssize_t stop) {
    Py_ssize_t n = stop - self->pos;
    text_begin(self);
    th_buf *buf = &self->text;
    if (buf->cap - buf->len < n) {
        Py_ssize_t cap = buf->cap ? buf->cap * 2 : 16;
        while (cap - buf->len < n) {
            cap *= 2;
        }
        Py_UCS4 *grown = PyMem_Resize(buf->data, Py_UCS4, (size_t)cap); /* GCOVR_EXCL_BR_LINE: size-overflow guard */
        if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            self->oom = 1;    /* GCOVR_EXCL_LINE */
            self->pos = stop; /* GCOVR_EXCL_LINE */
            return;           /* GCOVR_EXCL_LINE */
        }
        buf->data = grown;
        buf->cap = cap;
    }
    memcpy(buf->data + buf->len, self->input.data + self->pos, (size_t)n * sizeof(Py_UCS4));
    buf->len += n;
    self->pos = stop;
    self->col += n;
}

/* -------------------------------------------------------- character refs */

/* Resolve a character reference starting at the '&' under self->pos, appending
   the decoded code points to dest. in_attr selects the legacy attribute rule
   (a named reference without a trailing ';' is left literal when followed by
   '=' or an ASCII alphanumeric). Returns the number of input code points to
   consume (always >= 1) or -1 to suspend for more input; allocation failures
   land on the sticky oom flag. The numeric rules follow the tokenizer spec, which emits control and
   noncharacter code points rather than dropping them the way unescape() does. */
static Py_ssize_t consume_charref(th_tokenizer *self, th_buf *dest, int in_attr) {
    const Py_UCS4 *s = self->input.data;
    Py_ssize_t len = self->input.len;
    Py_ssize_t amp = self->pos;
    Py_ssize_t p = amp + 1;

    if (p >= len) {
        if (!self->eof) {
            return -1;
        }
        push(self, dest, '&');
        return 1;
    }

    Py_UCS4 first = s[p];
    if (first == '#') {
        Py_ssize_t cursor = p + 1;
        int hex = 0;
        if (cursor >= len && !self->eof) {
            return -1;
        }
        if (cursor < len && (s[cursor] == 'x' || s[cursor] == 'X')) {
            hex = 1;
            cursor++;
        }
        Py_UCS4 num = 0;
        int overflow = 0;
        Py_ssize_t first_digit = cursor;
        while (cursor < len) {
            Py_UCS4 digit = s[cursor];
            if (hex) {
                int value = charref_hex_value(digit);
                if (value < 0) {
                    break;
                }
                num = num * 16 + (Py_UCS4)value;
            } else {
                if (digit < '0' || digit > '9') {
                    break;
                }
                num = num * 10 + (digit - '0');
            }
            if (num > 0x110000) {
                num = 0x110000;
                overflow = 1;
            }
            cursor++;
        }
        if (cursor >= len && !self->eof) {
            return -1; /* the run of digits might continue */
        }
        if (cursor == first_digit) {
            /* "&#" or "&#x" with no digits is not a reference: emit it literally */
            push(self, dest, '&');
            push(self, dest, '#');
            if (hex) {
                push(self, dest, s[p + 1]);
            }
            return hex ? 3 : 2;
        }
        /* cursor < len or eof here: the digits-at-buffer-end case already suspended */
        Py_ssize_t end = cursor;
        if (cursor < len && s[cursor] == ';') {
            end = cursor + 1;
        }
        Py_UCS4 replacement;
        if (overflow || (num >= 0xD800 && num <= 0xDFFF) || num > 0x10FFFF) {
            push(self, dest, REPLACEMENT);
        } else if (charref_find_invalid(num, &replacement)) {
            push(self, dest, replacement);
        } else {
            push(self, dest, num);
        }
        return end - amp;
    }

    if (!is_ascii_alpha(first) && !(first >= '0' && first <= '9')) {
        /* "&" not followed by '#' or a name start is a literal ampersand */
        push(self, dest, '&');
        return 1;
    }

    /* Named reference: collect ASCII alphanumerics (the table's name alphabet)
       plus an optional ';', then take the longest table match. */
    Py_UCS4 chars[HTML5_MAX_NAME_LEN];
    char ascii[HTML5_MAX_NAME_LEN + 1];
    int name_len = 0;
    Py_ssize_t cursor = p;
    while (cursor < len && name_len < HTML5_MAX_NAME_LEN) {
        Py_UCS4 candidate = s[cursor];
        if (!is_ascii_alpha(candidate) && !(candidate >= '0' && candidate <= '9')) {
            break;
        }
        chars[name_len] = candidate;
        ascii[name_len] = (char)candidate;
        name_len++;
        cursor++;
    }
    if (cursor >= len && name_len == HTML5_MAX_NAME_LEN) {
        /* full buffer, fine to stop */
    } else if (cursor >= len && !self->eof) {
        return -1; /* more name characters might follow */
    }
    int semicolon = 0;
    if (cursor < len && s[cursor] == ';') {
        ascii[name_len] = ';';
        semicolon = 1;
    } else if (cursor >= len && !self->eof) {
        return -1; /* a ';' might still follow */
    }
    int token_len = name_len + semicolon;

    const html5_entity *entity = charref_find_entity(ascii, token_len);
    int match_len = token_len;
    int match_semicolon = semicolon;
    if (entity == NULL) {
        for (int prefix = name_len - 1; prefix >= 2; prefix--) {
            entity = charref_find_entity(ascii, prefix);
            if (entity != NULL) {
                match_len = prefix;
                match_semicolon = 0;
                break;
            }
        }
    }

    if (entity == NULL) {
        /* no match: emit '&' and the consumed name characters literally */
        push(self, dest, '&');
        for (int i = 0; i < name_len; i++) {
            push(self, dest, chars[i]);
        }
        return 1 + name_len;
    }

    if (in_attr && !match_semicolon) {
        Py_UCS4 after = (match_len < name_len) ? chars[match_len] : (cursor < len ? s[cursor] : 0);
        if (after == '=' || is_ascii_alpha(after) || (after >= '0' && after <= '9')) {
            /* legacy rule: leave the reference literal inside an attribute */
            push(self, dest, '&');
            for (int i = 0; i < name_len; i++) {
                push(self, dest, chars[i]);
            }
            return 1 + name_len;
        }
    }

    push(self, dest, entity->cp0);
    if (entity->cp1) {
        push(self, dest, entity->cp1);
    }
    /* characters consumed past the matched name are emitted literally */
    for (int i = match_len; i < name_len; i++) {
        push(self, dest, chars[i]);
    }
    return 1 + name_len + match_semicolon;
}

/* ----------------------------------------------------------------- run */

static void init_markup(th_tokenizer *self, enum th_kind kind) {
    token_reset(&self->tok);
    self->tok.kind = kind;
    self->tok.line = self->mark_line;
    self->tok.col = self->mark_col;
}

/* Compare the avail available code points against keyword: 2 = full match,
   1 = a proper prefix (more input could complete it), 0 = mismatch. */
static int match_kw(const Py_UCS4 *s, Py_ssize_t avail, const char *keyword, int klen, int fold) {
    int n = avail < klen ? (int)avail : klen;
    for (int i = 0; i < n; i++) {
        Py_UCS4 c = fold ? lower_ascii(s[i]) : s[i];
        if ((Py_UCS4)(unsigned char)keyword[i] != c) {
            return 0;
        }
    }
    return avail >= klen ? 2 : 1;
}

/* Emit "</" plus the raw end-tag-name characters held in temp as text, used
   when a raw-text/RCDATA/script end tag turns out not to match. */
static void rawtext_fallback(th_tokenizer *self, enum state ret) {
    text_begin_mark(self);
    text_push(self, '<');
    text_push(self, '/');
    for (Py_ssize_t i = 0; i < self->temp.len; i++) {
        text_push(self, self->temp.data[i]);
    }
    self->state = ret;
}

static void finish_tag(th_tokenizer *self) {
    self->state = ST_DATA;
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
        Py_ssize_t nl = self->input.data[self->pos] == '\n';                                                           \
        self->line += nl;                                                                                              \
        self->col = (self->col + 1) * (1 - nl);                                                                        \
        self->pos++;                                                                                                   \
    } while (0)

/* Remember the position of a '<' about to be consumed; tokens it opens and
   text fallbacks that re-emit it report this position (html.parser getpos). */
#define MARK()                                                                                                         \
    do {                                                                                                               \
        self->mark_line = self->line;                                                                                  \
        self->mark_col = self->col;                                                                                    \
    } while (0)

#define EOF_FLUSH()                                                                                                    \
    do {                                                                                                               \
        flush_text(self);                                                                                              \
        return RUN_DONE;                                                                                               \
    } while (0)

/* Emit the markup token and return to the data state; on the EOF paths the
   data state then reports DONE, so a token is never emitted twice. */
#define EMIT_MARKUP()                                                                                                  \
    do {                                                                                                               \
        self->state = ST_DATA;                                                                                         \
        emit_tok(self);                                                                                                \
        return RUN_EMITTED;                                                                                            \
    } while (0)

static enum run_result run(th_tokenizer *self) {
    for (;;) {
        int at_eof;
        Py_UCS4 ch;
        if (self->pos < self->input.len) {
            ch = self->input.data[self->pos];
            at_eof = 0;
        } else if (self->eof) {
            ch = 0;
            at_eof = 1;
        } else {
            return RUN_NEED_MORE;
        }

        switch (self->state) { /* GCOVR_EXCL_BR_LINE: enum-complete switch; the out-of-range edge is unreachable */
        case ST_DATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '&' && ch != '<' && ch != '\n') {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == '&' || c == '<' || c == '\n') {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            if (ch == '&') {
                text_begin(self);
                Py_ssize_t n = consume_charref(self, &self->text, 0);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_TAG_OPEN;
                continue;
            }
            text_push(self, ch);
            CONSUME();
            continue;

        case ST_RCDATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '&' && ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == '&' || c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            if (ch == '&') {
                text_begin(self);
                Py_ssize_t n = consume_charref(self, &self->text, 0);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RCDATA_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_RAWTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_RAWTEXT_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '<' && ch != '\n' && ch != 0) {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == '<' || c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_PLAINTEXT:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != '\n' && ch != 0) {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == '\n' || c == 0) {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_TAG_OPEN:
            if (at_eof) {
                text_begin_mark(self);
                text_push(self, '<');
                EOF_FLUSH();
            }
            if (ch == '!') {
                CONSUME();
                self->state = ST_MARKUP_DECL_OPEN;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_END_TAG_OPEN;
                continue;
            }
            if (is_ascii_alpha(ch)) {
                start_tag(self, 0, lower_ascii(ch));
                CONSUME();
                self->state = ST_TAG_NAME;
                continue;
            }
            if (ch == '?') {
                init_markup(self, TH_COMMENT);
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_DATA;
            continue;

        case ST_END_TAG_OPEN:
            if (at_eof) {
                text_begin_mark(self);
                text_push(self, '<');
                text_push(self, '/');
                EOF_FLUSH();
            }
            if (is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                CONSUME();
                self->state = ST_TAG_NAME;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                self->state = ST_DATA;
                continue;
            }
            init_markup(self, TH_COMMENT);
            self->state = ST_BOGUS_COMMENT;
            continue;

        case ST_TAG_NAME:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_RCDATA_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_RCDATA_END_OPEN;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_RCDATA;
            continue;

        case ST_RCDATA_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_RCDATA_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_RCDATA;
            continue;

        case ST_RCDATA_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_RCDATA);
            continue;

        case ST_RAWTEXT_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_RAWTEXT_END_OPEN;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_RAWTEXT;
            continue;

        case ST_RAWTEXT_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_RAWTEXT_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_RAWTEXT;
            continue;

        case ST_RAWTEXT_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_RAWTEXT);
            continue;

        case ST_SCRIPT_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_SCRIPT_END_OPEN;
                continue;
            }
            if (!at_eof && ch == '!') {
                CONSUME();
                text_begin_mark(self);
                text_push(self, '<');
                text_push(self, '!');
                self->state = ST_SCRIPT_ESC_START;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_SCRIPT_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_SCRIPT);
            continue;

        case ST_SCRIPT_ESC_START:
            if (!at_eof && ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESC_START_DASH;
                continue;
            }
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_ESC_START_DASH:
            if (!at_eof && ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH_DASH;
                continue;
            }
            self->state = ST_SCRIPT;
            continue;

        case ST_SCRIPT_ESCAPED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_ESCAPED_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_ESCAPED_DASH_DASH;
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_DASH_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                continue;
            }
            if (ch == '<') {
                MARK();
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_LT;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                text_push(self, '>');
                self->state = ST_SCRIPT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                self->state = ST_SCRIPT_ESCAPED_END_OPEN;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                buf_reset(&self->temp);
                text_begin_mark(self);
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESC_START;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_END_OPEN:
            if (!at_eof && is_ascii_alpha(ch)) {
                start_tag(self, 1, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                self->state = ST_SCRIPT_ESCAPED_END_NAME;
                continue;
            }
            text_begin_mark(self);
            text_push(self, '<');
            text_push(self, '/');
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_ESCAPED_END_NAME:
            if (!at_eof && is_space(ch) && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (!at_eof && ch == '/' && appropriate_end_tag(self)) {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (!at_eof && ch == '>' && appropriate_end_tag(self)) {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->tok.name, lower_ascii(ch));
                push(self, &self->temp, ch);
                CONSUME();
                continue;
            }
            rawtext_fallback(self, ST_SCRIPT_ESCAPED);
            continue;

        case ST_SCRIPT_DOUBLE_ESC_START:
            if (!at_eof && (is_space(ch) || ch == '/' || ch == '>')) {
                CONSUME();
                text_push(self, ch);
                self->state = (self->temp.len == 6 && memcmp(self->temp.data, U"script", 6 * sizeof(Py_UCS4)) == 0)
                                  ? ST_SCRIPT_DOUBLE_ESCAPED
                                  : ST_SCRIPT_ESCAPED;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->temp, lower_ascii(ch));
                text_push(self, ch);
                CONSUME();
                continue;
            }
            self->state = ST_SCRIPT_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_DASH;
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH;
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_DASH_DASH:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '-') {
                CONSUME();
                text_push(self, '-');
                continue;
            }
            if (ch == '<') {
                CONSUME();
                text_push(self, '<');
                self->state = ST_SCRIPT_DOUBLE_ESCAPED_LT;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                text_push(self, '>');
                self->state = ST_SCRIPT;
                continue;
            }
            text_push(self, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESCAPED_LT:
            if (!at_eof && ch == '/') {
                CONSUME();
                buf_reset(&self->temp);
                text_push(self, '/');
                self->state = ST_SCRIPT_DOUBLE_ESC_END;
                continue;
            }
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_SCRIPT_DOUBLE_ESC_END:
            if (!at_eof && (is_space(ch) || ch == '/' || ch == '>')) {
                CONSUME();
                text_push(self, ch);
                self->state = (self->temp.len == 6 && memcmp(self->temp.data, U"script", 6 * sizeof(Py_UCS4)) == 0)
                                  ? ST_SCRIPT_ESCAPED
                                  : ST_SCRIPT_DOUBLE_ESCAPED;
                continue;
            }
            if (!at_eof && is_ascii_alpha(ch)) {
                push(self, &self->temp, lower_ascii(ch));
                text_push(self, ch);
                CONSUME();
                continue;
            }
            self->state = ST_SCRIPT_DOUBLE_ESCAPED;
            continue;

        case ST_BEFORE_ATTR_NAME:
            if (at_eof || ch == '/' || ch == '>') {
                self->state = ST_AFTER_ATTR_NAME;
                continue;
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            new_attr(self);
            if (ch == '=') {
                push(self, &self->attr->name, ch);
                CONSUME();
            }
            self->state = ST_ATTR_NAME;
            continue;

        case ST_ATTR_NAME:
            if (at_eof || is_space(ch) || ch == '/' || ch == '>') {
                self->state = ST_AFTER_ATTR_NAME;
                continue;
            }
            if (ch == '=') {
                CONSUME();
                self->state = ST_BEFORE_ATTR_VALUE;
                continue;
            }
            push(self, &self->attr->name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_AFTER_ATTR_NAME:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '=') {
                CONSUME();
                self->state = ST_BEFORE_ATTR_VALUE;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            new_attr(self);
            self->state = ST_ATTR_NAME;
            continue;

        case ST_BEFORE_ATTR_VALUE:
            if (!at_eof && is_space(ch)) {
                CONSUME();
                continue;
            }
            self->attr->has_value = 1;
            if (!at_eof && ch == '"') {
                CONSUME();
                self->state = ST_ATTR_VALUE_DQ;
                continue;
            }
            if (!at_eof && ch == '\'') {
                CONSUME();
                self->state = ST_ATTR_VALUE_SQ;
                continue;
            }
            if (!at_eof && ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_ATTR_VALUE_UNQ;
            continue;

        case ST_ATTR_VALUE_DQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '"') {
                CONSUME();
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = consume_charref(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_ATTR_VALUE_SQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '\'') {
                CONSUME();
                self->state = ST_AFTER_ATTR_VALUE_QUOTED;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = consume_charref(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_ATTR_VALUE_UNQ:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '&') {
                Py_ssize_t n = consume_charref(self, &self->attr->value, 1);
                if (n == -1) {
                    return RUN_NEED_MORE;
                }
                self->pos += n;
                self->col += n;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            push(self, &self->attr->value, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_AFTER_ATTR_VALUE_QUOTED:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_ATTR_NAME;
                continue;
            }
            if (ch == '/') {
                CONSUME();
                self->state = ST_SELF_CLOSING_START_TAG;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_BEFORE_ATTR_NAME;
            continue;

        case ST_SELF_CLOSING_START_TAG:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch == '>') {
                CONSUME();
                self->tok.self_closing = 1;
                finish_tag(self);
                return RUN_EMITTED;
            }
            self->state = ST_BEFORE_ATTR_NAME;
            continue;

        case ST_BOGUS_COMMENT:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_MARKUP_DECL_OPEN: {
            const Py_UCS4 *s = self->input.data + self->pos;
            Py_ssize_t avail = self->input.len - self->pos;
            int m = match_kw(s, avail, "--", 2, 0);
            if (m == 2) {
                self->pos += 2;
                self->col += 2;
                init_markup(self, TH_COMMENT);
                self->state = ST_COMMENT_START;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = match_kw(s, avail, "doctype", 7, 1);
            if (m == 2) {
                self->pos += 7;
                self->col += 7;
                self->state = ST_DOCTYPE;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = match_kw(s, avail, "[CDATA[", 7, 0);
            if (m == 2) {
                self->pos += 7;
                self->col += 7;
                init_markup(self, TH_COMMENT);
                for (const char *k = "[CDATA["; *k; k++) {
                    push(self, &self->tok.text, (Py_UCS4)(unsigned char)*k);
                }
                self->state = ST_BOGUS_COMMENT;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            init_markup(self, TH_COMMENT);
            self->state = ST_BOGUS_COMMENT;
            continue;
        }

        case ST_COMMENT_START:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_START_DASH;
                continue;
            }
            if (!at_eof && ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_START_DASH:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '<') {
                push(self, &self->tok.text, '<');
                CONSUME();
                self->state = ST_COMMENT_LT;
                continue;
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END_DASH;
                continue;
            }
            push(self, &self->tok.text, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;

        case ST_COMMENT_LT:
            if (!at_eof && ch == '!') {
                push(self, &self->tok.text, '!');
                CONSUME();
                self->state = ST_COMMENT_LT_BANG;
                continue;
            }
            if (!at_eof && ch == '<') {
                push(self, &self->tok.text, '<');
                CONSUME();
                continue;
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_LT_BANG:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_LT_BANG_DASH;
                continue;
            }
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_LT_BANG_DASH:
            if (!at_eof && ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_LT_BANG_DASH_DASH;
                continue;
            }
            self->state = ST_COMMENT_END_DASH;
            continue;

        case ST_COMMENT_LT_BANG_DASH_DASH:
            self->state = ST_COMMENT_END;
            continue;

        case ST_COMMENT_END_DASH:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                CONSUME();
                self->state = ST_COMMENT_END;
                continue;
            }
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_END:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '!') {
                CONSUME();
                self->state = ST_COMMENT_END_BANG;
                continue;
            }
            if (ch == '-') {
                push(self, &self->tok.text, '-');
                CONSUME();
                continue;
            }
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '-');
            self->state = ST_COMMENT;
            continue;

        case ST_COMMENT_END_BANG:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '-') {
                push(self, &self->tok.text, '-');
                push(self, &self->tok.text, '-');
                push(self, &self->tok.text, '!');
                CONSUME();
                self->state = ST_COMMENT_END_DASH;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '-');
            push(self, &self->tok.text, '!');
            self->state = ST_COMMENT;
            continue;

        case ST_DOCTYPE:
            if (at_eof) {
                init_markup(self, TH_DOCTYPE);
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_NAME;
                continue;
            }
            self->state = ST_BEFORE_DOCTYPE_NAME;
            continue;

        case ST_BEFORE_DOCTYPE_NAME:
            if (at_eof) {
                init_markup(self, TH_DOCTYPE);
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            init_markup(self, TH_DOCTYPE);
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            self->state = ST_DOCTYPE_NAME;
            continue;

        case ST_DOCTYPE_NAME:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_NAME;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.name, ch == 0 ? REPLACEMENT : lower_ascii(ch));
            CONSUME();
            continue;

        case ST_AFTER_DOCTYPE_NAME: {
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            const Py_UCS4 *s = self->input.data + self->pos;
            Py_ssize_t avail = self->input.len - self->pos;
            int m = match_kw(s, avail, "public", 6, 1);
            if (m == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_PUBLIC_KW;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            m = match_kw(s, avail, "system", 6, 1);
            if (m == 2) {
                self->pos += 6;
                self->col += 6;
                self->state = ST_AFTER_DOCTYPE_SYSTEM_KW;
                continue;
            }
            if (m == 1 && !self->eof) {
                return RUN_NEED_MORE;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;
        }

        case ST_AFTER_DOCTYPE_PUBLIC_KW:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_PUBLIC_ID;
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_public_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_PUBLIC_ID_DQ : ST_DOCTYPE_PUBLIC_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BEFORE_DOCTYPE_PUBLIC_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_public_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_PUBLIC_ID_DQ : ST_DOCTYPE_PUBLIC_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_DOCTYPE_PUBLIC_ID_DQ:
        case ST_DOCTYPE_PUBLIC_ID_SQ: {
            Py_UCS4 quote = (self->state == ST_DOCTYPE_PUBLIC_ID_DQ) ? '"' : '\'';
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (ch == quote) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_PUBLIC_ID;
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.public_id, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;
        }

        case ST_AFTER_DOCTYPE_PUBLIC_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BETWEEN_DOCTYPE_PUB_SYS;
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BETWEEN_DOCTYPE_PUB_SYS:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_AFTER_DOCTYPE_SYSTEM_KW:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                self->state = ST_BEFORE_DOCTYPE_SYSTEM_ID;
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BEFORE_DOCTYPE_SYSTEM_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '"' || ch == '\'') {
                self->tok.has_system_id = 1;
                self->state = (ch == '"') ? ST_DOCTYPE_SYSTEM_ID_DQ : ST_DOCTYPE_SYSTEM_ID_SQ;
                CONSUME();
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            self->tok.force_quirks = 1;
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_DOCTYPE_SYSTEM_ID_DQ:
        case ST_DOCTYPE_SYSTEM_ID_SQ: {
            Py_UCS4 quote = (self->state == ST_DOCTYPE_SYSTEM_ID_DQ) ? '"' : '\'';
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (ch == quote) {
                CONSUME();
                self->state = ST_AFTER_DOCTYPE_SYSTEM_ID;
                continue;
            }
            if (ch == '>') {
                self->tok.force_quirks = 1;
                CONSUME();
                EMIT_MARKUP();
            }
            push(self, &self->tok.system_id, ch == 0 ? REPLACEMENT : ch);
            CONSUME();
            continue;
        }

        case ST_AFTER_DOCTYPE_SYSTEM_ID:
            if (at_eof) {
                self->tok.force_quirks = 1;
                EMIT_MARKUP();
            }
            if (is_space(ch)) {
                CONSUME();
                continue;
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            self->state = ST_BOGUS_DOCTYPE;
            continue;

        case ST_BOGUS_DOCTYPE:
            if (at_eof) {
                EMIT_MARKUP();
            }
            if (ch == '>') {
                CONSUME();
                EMIT_MARKUP();
            }
            CONSUME();
            continue;

        case ST_CDATA:
            if (at_eof) {
                EOF_FLUSH();
            }
            if (ch != ']' && ch != '\n') {
                Py_ssize_t stop = self->pos + 1;
                while (stop < self->input.len) {
                    Py_UCS4 c = self->input.data[stop];
                    if (c == ']' || c == '\n') {
                        break;
                    }
                    stop++;
                }
                text_append_run(self, stop);
                continue;
            }
            if (ch == ']') {
                CONSUME();
                self->state = ST_CDATA_BRACKET;
                continue;
            }
            text_push(self, ch);
            CONSUME();
            continue;

        case ST_CDATA_BRACKET:
            if (!at_eof && ch == ']') {
                CONSUME();
                self->state = ST_CDATA_END;
                continue;
            }
            text_push(self, ']');
            self->state = ST_CDATA;
            continue;

        case ST_CDATA_END:
            if (!at_eof && ch == '>') {
                CONSUME();
                self->state = ST_DATA;
                continue;
            }
            if (!at_eof && ch == ']') {
                text_push(self, ']');
                CONSUME();
                continue;
            }
            text_push(self, ']');
            text_push(self, ']');
            self->state = ST_CDATA;
            continue;
        }
    }
}

enum th_step th_tok_next(th_tokenizer *self, th_token **out) {
    if (self->returned != NULL) {
        token_reset(self->returned);
        self->returned = NULL;
    }
    for (;;) {
        if (self->oom) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return TH_STEP_ERROR; /* GCOVR_EXCL_LINE */
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
