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

#include <string.h>

typedef struct {
    Py_UCS4 *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
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
        unsigned char lead = (unsigned char)bytes[index];
        Py_UCS4 character;
        if (lead < 0x80) {
            character = lead;
            index += 1;
        } else if (lead < 0xE0) {
            character = (Py_UCS4)(lead & 0x1F) << 6 | ((unsigned char)bytes[index + 1] & 0x3F);
            index += 2;
        } else if (lead < 0xF0) {
            character = (Py_UCS4)(lead & 0x0F) << 12 | ((unsigned char)(bytes[index + 1] & 0x3F)) << 6 |
                        ((unsigned char)bytes[index + 2] & 0x3F);
            index += 3;
        } else {
            character = (Py_UCS4)(lead & 0x07) << 18 | ((unsigned char)(bytes[index + 1] & 0x3F)) << 12 |
                        ((unsigned char)(bytes[index + 2] & 0x3F)) << 6 | ((unsigned char)bytes[index + 3] & 0x3F);
            index += 4;
        }
        sbuf_putc(out, character);
    }
}

#endif /* TURBOHTML_SERIALIZE_BUFFER_H */
