/* ASCII code-point classification shared by every stage of the C core.

   The tokenizer, tree builder, selector and XPath parsers, encoding sniffer,
   link/date/sanitize scanners, and both standalone minifier engines each need to
   ask the same handful of questions about a code point: is it an ASCII letter, a
   digit, a hex digit, HTML whitespace; what is its ASCII-lowercase form. Kept as a
   Python-free header (like core/vec.h) so a translation unit inlines each helper as
   the comparison it replaces while there is one definition to audit, and so the
   css/js engines compile it in their JM_STANDALONE builds with no CPython runtime.

   Every helper takes a uint32_t: a Py_UCS4, a byte, or a css_char widens to it, and
   a signed char that carried a high bit widens to a large value that no ASCII range
   accepts, so the classification stays correct without the caller masking first. */

#ifndef TURBOHTML_CORE_ASCII_H
#define TURBOHTML_CORE_ASCII_H

#include <stdint.h>

/* ASCII-lowercase: fold A-Z, leave every other code point (including the Latin-1
   and non-ASCII letters) untouched. This is the case fold the HTML, CSS, and URL
   specs mean by "ASCII lowercase"; a caller that needs Latin-1 or Unicode folding
   spells that out itself. */
static inline uint32_t lower_ascii(uint32_t ch) {
    return (ch >= 'A' && ch <= 'Z') ? ch + 0x20 : ch;
}

static inline int is_ascii_alpha(uint32_t ch) {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

static inline int is_ascii_digit(uint32_t ch) {
    return ch >= '0' && ch <= '9';
}

static inline int is_ascii_hexdigit(uint32_t ch) {
    return is_ascii_digit(ch) || ((ch | 0x20) >= 'a' && (ch | 0x20) <= 'f');
}

/* The WHATWG HTML "ASCII whitespace" set: TAB, LF, FF, CR, SPACE. Tree
   construction and the serializers classify against the full set. Input
   preprocessing folds a literal CR in the byte stream to LF, so the tokenizer's
   raw scan never sees U+000D, but a CR decoded from a character reference (&#13;)
   bypasses preprocessing and reaches tree construction and the tree-walking
   consumers, where U+000D is whitespace like any other. */
static inline int is_space(uint32_t ch) {
    return ch == '\t' || ch == '\n' || ch == '\x0c' || ch == '\r' || ch == ' ';
}

#endif /* TURBOHTML_CORE_ASCII_H */
