/* Shared ASCII and compact-unicode-buffer primitives.

   The tokenizer and the tree builder both classify ASCII bytes and read the
   width-tagged th_buf storage; keeping these as static inline helpers in one
   header means each translation unit still inlines them (no call, no linkage)
   while there is a single definition to maintain. */

#ifndef TURBOHTML_ASCII_H
#define TURBOHTML_ASCII_H

#include "tokenizer/statemachine.h" /* th_buf */

/* The storage width a code point needs; matches the PyUnicode kind values. */
static inline int ucs_width(Py_UCS4 ch) {
    return ch < 0x100 ? PyUnicode_1BYTE_KIND : ch < 0x10000 ? PyUnicode_2BYTE_KIND : PyUnicode_4BYTE_KIND;
}

static inline Py_UCS4 buf_read(const th_buf *buf, Py_ssize_t index) {
    return PyUnicode_READ(buf->kind, buf->data, index);
}

static inline void buf_write(th_buf *buf, Py_ssize_t index, Py_UCS4 ch) {
    PyUnicode_WRITE(buf->kind, buf->data, index, ch);
}

static inline Py_UCS4 lower_ascii(Py_UCS4 ch) {
    return (ch >= 'A' && ch <= 'Z') ? ch + 0x20 : ch;
}

static inline int is_ascii_alpha(Py_UCS4 ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

/* The HTML "ASCII whitespace" set. Input preprocessing folds a literal CR to LF,
   so the tokenizer's raw scan never sees U+000D; a CR decoded from a character
   reference (&#13;) bypasses preprocessing and reaches tree construction, where
   U+000D is whitespace in every insertion mode. */
static inline int is_space(Py_UCS4 ch) {
    return ch == '\t' || ch == '\n' || ch == '\x0c' || ch == '\r' || ch == ' ';
}

#endif /* TURBOHTML_ASCII_H */
