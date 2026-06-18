/* Scalar escaping primitives shared by the per-arch escape translation units.

   escape.c compiles the SSE2 / NEON / SWAR 16-byte clean-run scan; escape_avx2.c
   compiles a 32-byte AVX2 scan in its own -mavx2 unit (linked in only on x86).
   Both reuse the same scalar sizing (escape_extra) and the same scalar specials
   rewrite (write_escaped) so the per-special replacement logic stays single
   sourced: only the clean-run scan/copy width differs between the units. */

#ifndef TURBOHTML_ESCAPE_SHARED_H
#define TURBOHTML_ESCAPE_SHARED_H

#include "turbohtml.h"

static inline Py_ssize_t escape_extra(Py_UCS4 character, int quote) {
    switch (character) {
    case '&':
        return 4; /* "&amp;" replaces one character with five */
    case '<':
    case '>':
        return 3; /* "&lt;" / "&gt;" */
    case '"':     /* "&quot;" */
    case '\'':    /* "&#x27;" */
        return quote ? 5 : 0;
    default:
        return 0;
    }
}

static inline Py_ssize_t write_escaped(int kind, void *data, Py_ssize_t offset, Py_UCS4 character, int quote) {
    const char *replacement = NULL;
    int replacement_len = 0;
    switch (character) {
    case '&':
        replacement = "&amp;";
        replacement_len = 5;
        break;
    case '<':
        replacement = "&lt;";
        replacement_len = 4;
        break;
    case '>':
        replacement = "&gt;";
        replacement_len = 4;
        break;
    case '"':
        if (quote) {
            replacement = "&quot;";
            replacement_len = 6;
        }
        break;
    case '\'':
        if (quote) {
            replacement = "&#x27;";
            replacement_len = 6;
        }
        break;
    default:
        break;
    }
    if (replacement != NULL) {
        for (int index = 0; index < replacement_len; index++) {
            PyUnicode_WRITE(kind, data, offset + index, (Py_UCS4)replacement[index]);
        }
        return replacement_len;
    }
    PyUnicode_WRITE(kind, data, offset, character);
    return 1;
}

#endif /* TURBOHTML_ESCAPE_SHARED_H */
