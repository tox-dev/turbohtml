/* WHATWG HTML tokenizer state machine — pure C, no Python objects.

   This file is the algorithm. It reads code points from an internal buffer and
   emits token records through a small queue; it never creates PyObjects (only
   PyMem allocations for its growable buffers). The Python layer in
   tokenizer_type.c owns an instance, feeds it code points, and turns the
   emitted records into Token objects. Keeping the machine free of Python lets
   it be read straight against the spec and tested in isolation.

   The machine is resumable: feed() appends code points and steps until it runs
   out of input, suspending mid-token if necessary; close() supplies the
   end-of-file signal that flushes the final token. State, the token under
   construction, and all lookahead live in th_tokenizer across calls. */

#ifndef TURBOHTML_TOKENIZER_SM_H
#define TURBOHTML_TOKENIZER_SM_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* Emitted token kinds. Character tokens are coalesced into TEXT runs. */
enum th_kind {
    TH_TEXT,
    TH_START_TAG,
    TH_END_TAG,
    TH_COMMENT,
    TH_DOCTYPE,
};

/* Growable code-point buffer stored at the narrowest PyUnicode kind its
   content needs, so ASCII data stays one byte per character end to end.
   data may be NULL while len == 0; cap is in bytes. */
typedef struct {
    void *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int kind; /* PyUnicode_{1,2,4}BYTE_KIND */
} th_buf;

typedef struct {
    th_buf name;
    th_buf value;
    int has_value; /* 0 for a valueless attribute (maps to None in Python) */
} th_attr;

/* One completed token. Buffers are owned and reused across tokens via reset;
   the Python layer copies what it needs out of them when it builds a Token.
   A TEXT token that is an untouched span of the input carries is_slice with
   src_start/src_len instead of filling text; resolve it against
   th_tok_input_data before the next th_tok_next call. */
typedef struct {
    enum th_kind kind;
    int is_slice;
    Py_ssize_t src_start;
    Py_ssize_t src_len;
    th_buf name; /* tag name (lowercased) or DOCTYPE name */
    th_buf text; /* TEXT run or COMMENT data */
    th_attr *attrs;
    Py_ssize_t attr_count;
    Py_ssize_t attr_cap;
    int self_closing;
    /* DOCTYPE only */
    th_buf public_id;
    th_buf system_id;
    int has_public_id;
    int has_system_id;
    int force_quirks;
    /* 1-based source position where the token began */
    Py_ssize_t line;
    Py_ssize_t col;
} th_token;

/* Content-model states a consumer may start in. The public tokenizer always
   starts in DATA; the others are reachable through tag transitions and are
   selectable directly only by the conformance harness. */
enum th_initial_state {
    TH_INIT_DATA,
    TH_INIT_RCDATA,
    TH_INIT_RAWTEXT,
    TH_INIT_SCRIPT_DATA,
    TH_INIT_PLAINTEXT,
    TH_INIT_CDATA,
};

typedef struct th_tokenizer th_tokenizer;

/* Lifecycle. th_tok_new returns NULL on allocation failure. */
th_tokenizer *th_tok_new(void);
void th_tok_free(th_tokenizer *self);
void th_tok_reset(th_tokenizer *self);

/* Configure the starting content model and the last start tag name used for
   appropriate-end-tag checks. Call before the first feed; used by the harness. */
void th_tok_set_initial(th_tokenizer *self, enum th_initial_state state, const Py_UCS4 *last_tag,
                        Py_ssize_t last_tag_len);

/* Testing hook: pre-set the empty input buffer's storage width so the
   kind-stamped tokenizer cores can all be exercised with the same (mostly
   ASCII) conformance data; tokenization is invariant to storage width. */
void th_tok_widen_input(th_tokenizer *self, int kind);

/* Switch the current content model. The public tokenizer calls this after a
   start tag for a raw-text element (script, style, title, textarea, ...) so the
   element's contents tokenize correctly; the spec assigns this to tree
   construction, which the state machine itself does not perform. */
void th_tok_switch(th_tokenizer *self, enum th_initial_state state);

/* Append code points read from a unicode object's storage. An allocation
   failure is reported by the next th_tok_next call as TH_STEP_ERROR. */
void th_tok_feed(th_tokenizer *self, int kind, const void *data, Py_ssize_t length);

/* Use caller-owned storage as the whole input without copying. Only valid on
   a fresh tokenizer and for data free of '\r' (the caller checks); the
   storage must stay alive and unchanged until the machine is freed, and no
   th_tok_feed or th_tok_reset may follow. tokenize() borrows and never exposes
   the machine; streaming feed() copies. */
void th_tok_borrow_input(th_tokenizer *self, int kind, const void *data, Py_ssize_t length);

/* The storage backing slice tokens: sets *kind, returns the base pointer. */
const void *th_tok_input_data(const th_tokenizer *self, int *kind);

/* Signal end of input; the next th_tok_next calls flush remaining tokens. */
void th_tok_close(th_tokenizer *self);

/* Outcomes of th_tok_next. */
enum th_step {
    TH_STEP_TOKEN,     /* *out points to a completed token (valid until next call) */
    TH_STEP_NEED_MORE, /* input exhausted before EOF; feed more or close */
    TH_STEP_DONE,      /* EOF reached and everything flushed */
    TH_STEP_ERROR,     /* a buffer allocation failed; a Python error is NOT set here */
};

/* Advance until one token is produced or the machine stalls. */
enum th_step th_tok_next(th_tokenizer *self, th_token **out);

/* Free the buffers a consumer took ownership of (the moved-out text run). */
void th_token_clear(th_token *tok);

#endif /* TURBOHTML_TOKENIZER_SM_H */
