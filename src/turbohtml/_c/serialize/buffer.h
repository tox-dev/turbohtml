/* The growable code-point buffer every serialize output mode writes into: the
   struct and its reserve/append hot path, as `static inline` so each serialize
   translation unit keeps its own inlined copy of the append loop with no
   cross-TU call. internal.h includes this in place of the buffer block it used
   to hold, so a translation unit that pulls internal.h sees the same
   definitions in the same order it did before. */

#ifndef TURBOHTML_SERIALIZE_BUFFER_H
#define TURBOHTML_SERIALIZE_BUFFER_H

#include <Python.h>

#include "core/vec.h" /* th_grow_cap overflow-safe buffer growth */

#include <stdint.h>
#include <string.h>

struct th_clone_seen {
    uint64_t *slots;
    Py_ssize_t cap;
    Py_ssize_t len;
};

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
    /* added to len for the attribute characters parser clones may still write: each clone attribute takes back twice
       what it wrote, once as the output it is and once as the allowance it spent (see ser_clone_admit) */
    Py_ssize_t clone_credit;
    /* the value sets this output has already written a clone of, allocated by the first clone that needs one */
    struct th_clone_seen *clone_seen;
} sbuf;

/* Grow the buffer so at least extra more code points fit, doubling so a run of
   appends stays amortized O(1). */
static inline void sbuf_reserve(sbuf *out, Py_ssize_t extra) {
    if (out->len + extra <= out->cap) {
        return;
    }
    size_t cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)(out->len + extra), (size_t)out->cap, 256, sizeof(Py_UCS4), &cap, &bytes);
    if (!grew) {         /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        out->failed = 1; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
        return;          /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    Py_UCS4 *grown = PyMem_Realloc(out->data, bytes);
    if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    out->data = grown;
    out->cap = (Py_ssize_t)cap;
}

static inline void sbuf_putc(sbuf *out, Py_UCS4 character) {
    sbuf_reserve(out, 1);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    out->data[out->len++] = character;
}

/* Append a run of code points in one bulk copy after a single capacity check. */
static inline void sbuf_put_run(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    if (len == 0) { /* an empty run carries a NULL text pointer, and memcpy declares its source non-null even for 0 */
        return;
    }
    if (text == NULL) {  /* GCOVR_EXCL_BR_LINE: lazy text allocation failure */
        out->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
        return;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    sbuf_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    memcpy(out->data + out->len, text, (size_t)len * sizeof(Py_UCS4));
    out->len += len;
}

static inline void sbuf_puts(sbuf *out, const char *str) {
    Py_ssize_t len = (Py_ssize_t)strlen(str);
    sbuf_reserve(out, len);
    if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;        /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        out->data[out->len + index] = (Py_UCS4)(unsigned char)str[index];
    }
    out->len += len;
}

static inline void sbuf_put_ucs4(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    sbuf_put_run(out, text, len);
}

/* Decode the code point whose UTF-8 sequence starts at bytes[*index] and move *index past it. A truncated
   tail, which the UTF-8 encoder never writes, reads as U+FFFD and ends the run. */
static inline Py_UCS4 utf8_next(const char *bytes, Py_ssize_t len, Py_ssize_t *index) {
    unsigned char lead = (unsigned char)bytes[*index];
    if (lead < 0x80) {
        *index += 1;
        return lead;
    }
    if (lead < 0xE0 && *index + 1 < len) { /* GCOVR_EXCL_BR_LINE: callers pass whole sequences */
        Py_UCS4 character = (Py_UCS4)(lead & 0x1F) << 6 | ((unsigned char)bytes[*index + 1] & 0x3F);
        *index += 2;
        return character;
    }
    if (lead < 0xF0 && *index + 2 < len) { /* GCOVR_EXCL_BR_LINE: callers pass whole sequences */
        Py_UCS4 character = (Py_UCS4)(lead & 0x0F) << 12 | ((unsigned char)(bytes[*index + 1] & 0x3F)) << 6 |
                            ((unsigned char)bytes[*index + 2] & 0x3F);
        *index += 3;
        return character;
    }
    if (*index + 3 < len) { /* GCOVR_EXCL_BR_LINE: callers pass whole sequences */
        Py_UCS4 character = (Py_UCS4)(lead & 0x07) << 18 | ((unsigned char)(bytes[*index + 1] & 0x3F)) << 12 |
                            ((unsigned char)(bytes[*index + 2] & 0x3F)) << 6 |
                            ((unsigned char)bytes[*index + 3] & 0x3F);
        *index += 4;
        return character;
    }
    *index = len;  /* GCOVR_EXCL_LINE: a truncated tail, which the UTF-8 encoder never writes */
    return 0xFFFD; /* GCOVR_EXCL_LINE: unreachable from a test */
}

/* Write well-formed UTF-8 bytes (an interned attribute name) as code points. The
   common all-ASCII name is copied in bulk; only a name with a byte >= 0x80
   (a foreign mixed-case attribute) takes the per-code-point decoder. */
static inline void sbuf_put_utf8(sbuf *out, const char *bytes, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len && (unsigned char)bytes[index] < 0x80) {
        index++;
    }
    if (index > 0) {
        sbuf_reserve(out, index);
        if (out->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t ascii = 0; ascii < index; ascii++) {
            out->data[out->len + ascii] = (Py_UCS4)(unsigned char)bytes[ascii];
        }
        out->len += index;
    }
    while (index < len) {
        sbuf_putc(out, utf8_next(bytes, len, &index));
    }
}

#endif /* TURBOHTML_SERIALIZE_BUFFER_H */
