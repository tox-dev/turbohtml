/* The HTML "parse a srcset attribute" descriptor step for one image candidate. The link scanner and the sanitizer both
   call it, so they cannot disagree about which candidates a browser keeps. Python-free, like core/refresh.h. */

#ifndef TURBOHTML_CORE_SRCSET_H
#define TURBOHTML_CORE_SRCSET_H

#include "core/ascii.h"

#include <stddef.h>
#include <stdint.h>

/* Both scanners run srcset_descriptors_valid once per candidate, and GCC kept it out of line, which measured slower
   than inlining its fast path and leaving the full check as a call. The coverage build compiles at -O0, where an
   inlined copy lands in every call site and gcc counts its branches once per copy, so it keeps one copy there. */
#if defined(_MSC_VER)
#define SRCSET_INLINE __forceinline
#define SRCSET_OUTLINE __declspec(noinline)
#elif defined(__OPTIMIZE__)
#define SRCSET_INLINE inline __attribute__((always_inline))
#define SRCSET_OUTLINE __attribute__((noinline))
#else
#define SRCSET_INLINE inline
#define SRCSET_OUTLINE
#endif

typedef enum {
    SRCSET_INVALID,
    SRCSET_WIDTH,
    SRCSET_DENSITY,
    SRCSET_HEIGHT,
} srcset_kind;

/* A valid non-negative integer with a non-zero digit: the browser discards a zero width or height. */
static inline int srcset_positive_integer(const uint32_t *value, ptrdiff_t start, ptrdiff_t end) {
    int nonzero = 0;
    if (start == end) {
        return 0;
    }
    for (ptrdiff_t pos = start; pos < end; pos++) {
        if (!is_ascii_digit(value[pos])) {
            return 0;
        }
        nonzero |= value[pos] != '0';
    }
    return nonzero;
}

/* A valid floating-point number that is not below zero: an optional '-', digits, a '.' with digits, or both, then an
   optional exponent. A zero density is kept, a negative one is not. */
static inline int srcset_density(const uint32_t *value, ptrdiff_t start, ptrdiff_t end) {
    int negative = start < end && value[start] == '-';
    int nonzero = 0;
    ptrdiff_t pos = start + negative;
    ptrdiff_t integer = pos;
    while (pos < end && is_ascii_digit(value[pos])) {
        nonzero |= value[pos] != '0';
        pos++;
    }
    if (pos < end && value[pos] == '.') {
        pos++;
        ptrdiff_t fraction = pos;
        while (pos < end && is_ascii_digit(value[pos])) {
            nonzero |= value[pos] != '0';
            pos++;
        }
        if (pos == fraction) {
            return 0;
        }
    } else if (pos == integer) {
        return 0;
    }
    if (pos < end && (value[pos] == 'e' || value[pos] == 'E')) {
        pos++;
        if (pos < end && (value[pos] == '+' || value[pos] == '-')) {
            pos++;
        }
        ptrdiff_t exponent = pos;
        while (pos < end && is_ascii_digit(value[pos])) {
            pos++;
        }
        if (pos == exponent) {
            return 0;
        }
    }
    return pos == end && !(negative && nonzero);
}

static inline srcset_kind srcset_classify(const uint32_t *value, ptrdiff_t start, ptrdiff_t end) {
    switch (value[end - 1]) {
    case 'w':
        return srcset_positive_integer(value, start, end - 1) ? SRCSET_WIDTH : SRCSET_INVALID;
    case 'h':
        return srcset_positive_integer(value, start, end - 1) ? SRCSET_HEIGHT : SRCSET_INVALID;
    case 'x':
        return srcset_density(value, start, end - 1) ? SRCSET_DENSITY : SRCSET_INVALID;
    default:
        return SRCSET_INVALID;
    }
}

/* srcset_descriptors_valid for any descriptor list: tokenizes it and classifies each token. A parenthesised group is
   one descriptor up to its close parenthesis, so a comma or space inside it does not end the candidate. */
static SRCSET_OUTLINE int srcset_descriptors_checked(const uint32_t *value, ptrdiff_t len, ptrdiff_t *pos) {
    int valid = 1;
    int width = 0;
    int density = 0;
    int height = 0;
    ptrdiff_t cursor = *pos;
    while (cursor < len && value[cursor] != ',') {
        if (is_space(value[cursor])) {
            cursor++;
            continue;
        }
        ptrdiff_t start = cursor;
        while (cursor < len && !is_space(value[cursor]) && value[cursor] != ',') {
            if (value[cursor] == '(') {
                while (cursor < len && value[cursor] != ')') {
                    cursor++;
                }
            }
            if (cursor < len) {
                cursor++;
            }
        }
        srcset_kind kind = srcset_classify(value, start, cursor);
        if (kind == SRCSET_WIDTH && !width && !density) {
            width = 1;
        } else if (kind == SRCSET_DENSITY && !width && !density && !height) {
            density = 1;
        } else if (kind == SRCSET_HEIGHT && !height && !density) {
            height = 1;
        } else {
            valid = 0;
        }
    }
    *pos = cursor;
    return valid && (!height || width);
}

/* Reads the descriptors of the candidate whose URL ended at *pos, on a whitespace character or at len, leaving *pos on
   the comma that ends the candidate (or at len). Returns 0 when a descriptor is invalid, a width or density repeats or
   the two are mixed, or an h has no w. Nearly every candidate is a URL, one space and one integer width or density
   right before the comma, which this decides while skipping it; any other list goes through
   srcset_descriptors_checked. */
static SRCSET_INLINE int srcset_descriptors_valid(const uint32_t *value, ptrdiff_t len, ptrdiff_t *pos) {
    if (*pos == len) {
        return 1;
    }
    ptrdiff_t digits = *pos + 1;
    ptrdiff_t cursor = digits;
    while (cursor < len && is_ascii_digit(value[cursor])) {
        cursor++;
    }
    if (cursor > digits && cursor < len && (value[cursor] == 'x' || (value[cursor] == 'w' && value[digits] != '0'))) {
        cursor++;
        if (cursor == len || value[cursor] == ',') {
            *pos = cursor;
            return 1;
        }
    }
    return srcset_descriptors_checked(value, len, pos);
}

#endif
