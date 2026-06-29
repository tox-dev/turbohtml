/* Aggressive, round-trip-safe CSS minification, porting tdewolff/minify's CSS algorithm.

   Two entry points mirror tdewolff's two modes: th_minify_css for a full stylesheet (rules, at-rules, nesting) and
   th_minify_css_inline for a bare declaration list as in a style= attribute. The pipeline is: a zero-copy tokenizer
   that points its tokens straight into the source code-point buffer and hops whitespace with the shared SWAR lane
   probe; a recursive-descent grammar over those tokens; and a value engine that shortens numbers, dimensions and
   colors and folds a handful of shorthands. Every transform is value-preserving, so the output reparses to the same
   declared styles.

   Rendered value components are interned into a per-call code-point pool; a component refers to its text by
   (offset, length) into that pool so the pool can grow without invalidating components.

   The engine core (tokenizer, grammar, value engine, th_minify_css_bytes) touches no CPython runtime: it allocates
   through the css_malloc macros and writes to css_buf. The CPython binding (the PyObject entry points) is compiled
   only into the extension, behind CSS_MINIFY_STANDALONE, so a pure-C harness can run the core under
   AddressSanitizer/LeakSanitizer and libFuzzer. */

#include "core/common.h"
#include "data/css_colors.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* clang and gcc expose __builtin_{mul,add}_overflow, but MSVC does not on every target (notably arm64), so the calc
   engine routes its checked 64-bit arithmetic through these. The product's high word, from the __mulh intrinsic MSVC
   ships for x64 and arm64, must sign-extend the low word for a 64x64 multiply not to overflow; a signed addition
   overflows when both operands share a sign the result does not. */
#if defined(_MSC_VER)
#include <intrin.h>
static inline int css_mul_overflow(long long left, long long right, long long *out) {
    long long high = __mulh(left, right);
    *out = (long long)((unsigned long long)left * (unsigned long long)right);
    return high != (*out >> 63);
}
static inline int css_add_overflow(long long left, long long right, long long *out) {
    *out = (long long)((unsigned long long)left + (unsigned long long)right);
    return ((left ^ *out) & (right ^ *out)) < 0;
}
#else
static inline int css_mul_overflow(long long left, long long right, long long *out) {
    return __builtin_mul_overflow(left, right, out);
}
static inline int css_add_overflow(long long left, long long right, long long *out) {
    return __builtin_add_overflow(left, right, out);
}
#endif

/* The engine works on the input's UTF-8 bytes, one byte per code unit. CSS structure is pure ASCII, so a non-ASCII
   byte (>= 0x80) only ever appears inside a string, comment, url or identifier, where it is copied through verbatim;
   the minified bytes are valid UTF-8 that the binding decodes back to a str. Bytes mean four times less memory traffic
   than 4-byte code points and no widening, and -- unlike dispatching on the str's storage kind -- this path is taken
   even when one stray em-dash or bullet in a comment would otherwise make the whole str 2-byte. */
typedef unsigned char css_char;

/* The engine routes every allocation through these so the core compiles two ways: in the extension they are the
   PyMem (pymalloc) allocator, identical codegen to calling it directly; under -DCSS_MINIFY_STANDALONE they are libc
   malloc so the pure-C harness runs with no interpreter, no suppressions and no libpython. The selection is a
   compile-time macro, so there is no runtime indirection. */
#ifdef CSS_MINIFY_STANDALONE
#define css_malloc(size) malloc(size)
#define css_realloc(ptr, size) realloc((ptr), (size))
#define css_free(ptr) free(ptr)
#else
#define css_malloc(size) PyMem_Malloc(size)
#define css_realloc(ptr, size) PyMem_Realloc((ptr), (size))
#define css_free(ptr) PyMem_Free(ptr)
#endif

/* A growable code-point buffer, the engine's only output sink. It mirrors the shared serializer sbuf but routes its
   allocation through css_realloc so the core stays allocator-agnostic without touching shared infrastructure. */
typedef struct {
    css_char *data;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} css_buf;

static inline void cbuf_reserve(css_buf *buffer, Py_ssize_t extra) {
    if (buffer->len + extra <= buffer->cap) {
        return;
    }
    Py_ssize_t cap = buffer->cap ? buffer->cap : 256;
    while (cap < buffer->len + extra) {
        cap *= 2;
    }
    css_char *grown = css_realloc(buffer->data, (size_t)cap * sizeof(css_char));
    if (grown == NULL) {    /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        buffer->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        return;             /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buffer->data = grown;
    buffer->cap = cap;
}

static inline void cbuf_putc(css_buf *buffer, css_char character) {
    cbuf_reserve(buffer, 1);
    if (buffer->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    buffer->data[buffer->len++] = character;
}

static inline void cbuf_put_run(css_buf *buffer, const css_char *text, Py_ssize_t len) {
    cbuf_reserve(buffer, len);
    if (buffer->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    memcpy(buffer->data + buffer->len, text, (size_t)len * sizeof(css_char));
    buffer->len += len;
}

static inline void cbuf_puts(css_buf *buffer, const char *text) {
    Py_ssize_t len = (Py_ssize_t)strlen(text);
    cbuf_reserve(buffer, len);
    if (buffer->failed) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return;           /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        buffer->data[buffer->len + index] = (css_char)(unsigned char)text[index];
    }
    buffer->len += len;
}

static inline void cbuf_free(css_buf *buffer) {
    css_free(buffer->data);
}

typedef enum {
    CSS_WS,
    CSS_COMMENT,
    CSS_STR,
    CSS_AT,
    CSS_HASH,
    CSS_NUM,
    CSS_URANGE,
    CSS_URL,
    CSS_IDENT,
    CSS_DELIM,
} css_kind;

/* A token points into the source buffer (zero-copy). For CSS_NUM, text is the numeric part and unit the dimension;
   for CSS_DELIM, delim is the single character. */
typedef struct {
    const css_char *text;
    Py_ssize_t text_len;
    Py_ssize_t unit_len;
    css_kind kind;
    css_char delim;
} css_token;

typedef struct {
    css_token *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} token_vec;

static void token_vec_push(token_vec *vec, css_token token) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 64;
        css_token *grown = css_realloc(vec->items, (size_t)cap * sizeof(css_token));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        vec->items = grown;
        vec->cap = cap;
    }
    vec->items[vec->len++] = token;
}

/* Character-class bitmask over the 7-bit ASCII range, the rcssmin trick: the hot tokenizer classifiers become a single
   indexed load + mask rather than a chain of range comparisons. A code point >= 0x80 is handled by the callers. */
#define CSS_CM_WS 1
#define CSS_CM_DIGIT 2
#define CSS_CM_IDENT 4
#define CSS_CM_HEX 8

static const unsigned char css_charmask[128] = {
    0,  0,  0,  0,  0,  0,  0,  0,  0,  1,  1, 0, 1, 1, 0, 0, /* 0x00: \t \n \f \r */
    0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0, 0, 0, 0, 0, 0, /* 0x10 */
    1,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0, 0, 0, 4, 0, 0, /* 0x20: space, '-' */
    14, 14, 14, 14, 14, 14, 14, 14, 14, 14, 0, 0, 0, 0, 0, 0, /* 0x30: 0-9 */
    0,  12, 12, 12, 12, 12, 12, 4,  4,  4,  4, 4, 4, 4, 4, 4, /* 0x40: A-F, G-O */
    4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4, 0, 4, 0, 0, 4, /* 0x50: P-Z, '\\', '_' */
    0,  12, 12, 12, 12, 12, 12, 4,  4,  4,  4, 4, 4, 4, 4, 4, /* 0x60: a-f, g-o */
    4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4, 0, 0, 0, 0, 0, /* 0x70: p-z */
};

static inline int css_is_ws(css_char character) {
    return character < 128 && (css_charmask[character] & CSS_CM_WS);
}

static inline css_char css_lower(css_char character) {
    return (character >= 'A' && character <= 'Z') ? character + 32 : character;
}

static inline int css_is_digit(css_char character) {
    return character < 128 && (css_charmask[character] & CSS_CM_DIGIT);
}

/* An identifier code point: alphanumeric, -, _, backslash escape, or any non-ASCII. */
static inline int css_is_ident(css_char character) {
    return character >= 0x80 || (css_charmask[character] & CSS_CM_IDENT);
}

static inline int css_is_hex(css_char character) {
    return character < 128 && (css_charmask[character] & CSS_CM_HEX);
}

/* Whether text[pos..] begins a numeric token: a digit, a dot before a digit, or a sign before either. */
static int css_starts_number(const css_char *text, Py_ssize_t pos, Py_ssize_t length) {
    css_char character = text[pos];
    if (css_is_digit(character)) {
        return 1;
    }
    if (character == '.' && pos + 1 < length && css_is_digit(text[pos + 1])) {
        return 1;
    }
    if ((character == '+' || character == '-') && pos + 1 < length) {
        css_char following = text[pos + 1];
        if (css_is_digit(following)) {
            return 1;
        }
        if (following == '.' && pos + 2 < length && css_is_digit(text[pos + 2])) {
            return 1;
        }
    }
    return 0;
}

/* Match [+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)? at pos, returning the end index. */
static Py_ssize_t css_scan_number(const css_char *text, Py_ssize_t pos, Py_ssize_t length) {
    /* the sole caller guards this with css_starts_number, so the optional sign and the digit/dot after it are always
       present -- no leading bounds check is needed before the first digit run */
    Py_ssize_t scan = pos;
    if (text[scan] == '+' || text[scan] == '-') {
        scan++;
    }
    if (text[scan] == '.') {
        scan++;
        while (scan < length && css_is_digit(text[scan])) {
            scan++;
        }
    } else {
        while (scan < length && css_is_digit(text[scan])) {
            scan++;
        }
        if (scan < length && text[scan] == '.') {
            scan++;
            while (scan < length && css_is_digit(text[scan])) {
                scan++;
            }
        }
    }
    if (scan < length && (text[scan] == 'e' || text[scan] == 'E')) {
        Py_ssize_t exp = scan + 1;
        if (exp < length && (text[exp] == '+' || text[exp] == '-')) {
            exp++;
        }
        if (exp < length && css_is_digit(text[exp])) {
            exp++;
            while (exp < length && css_is_digit(text[exp])) {
                exp++;
            }
            scan = exp;
        }
    }
    return scan;
}

/* Tokenize the whole source into vec; tokens point into source (zero-copy). */
static void css_tokenize(const css_char *source, Py_ssize_t length, token_vec *vec) {
    Py_ssize_t pos = 0;
    while (pos < length) {
        css_char character = source[pos];
        css_token token = {0};
        if (css_is_ws(character)) {
            Py_ssize_t scan = pos + 1;
            while (scan < length && css_is_ws(source[scan])) {
                scan++;
            }
            token.kind = CSS_WS;
            token.text = &source[pos];
            token.text_len = 1;
            token_vec_push(vec, token);
            pos = scan;
        } else if (character == '/' && pos + 1 < length && source[pos + 1] == '*') {
            Py_ssize_t scan = pos + 2;
            while (scan + 1 < length && !(source[scan] == '*' && source[scan + 1] == '/')) {
                scan++;
            }
            Py_ssize_t end = scan + 1 < length ? scan + 2 : length;
            token.kind = CSS_COMMENT;
            token.text = &source[pos];
            token.text_len = end - pos;
            token_vec_push(vec, token);
            pos = end;
        } else if (character == '"' || character == '\'') {
            Py_ssize_t scan = pos + 1;
            while (scan < length) {
                if (source[scan] == '\\' && scan + 1 < length) {
                    scan += (source[scan + 1] == '\r' && scan + 2 < length && source[scan + 2] == '\n') ? 3 : 2;
                    continue;
                }
                if (source[scan] == character) {
                    scan++;
                    break;
                }
                if (source[scan] == '\n' || source[scan] == '\r' || source[scan] == '\f') {
                    break;
                }
                scan++;
            }
            token.kind = CSS_STR;
            token.text = &source[pos];
            token.text_len = scan - pos;
            token_vec_push(vec, token);
            pos = scan;
        } else if (character == '@' && pos + 1 < length && css_is_ident(source[pos + 1])) {
            Py_ssize_t scan = pos + 1;
            while (scan < length && css_is_ident(source[scan])) {
                scan++;
            }
            token.kind = CSS_AT;
            token.text = &source[pos];
            token.text_len = scan - pos;
            token_vec_push(vec, token);
            pos = scan;
        } else if (character == '#' && pos + 1 < length && css_is_ident(source[pos + 1])) {
            Py_ssize_t scan = pos + 1;
            while (scan < length && css_is_ident(source[scan])) {
                scan++;
            }
            token.kind = CSS_HASH;
            token.text = &source[pos];
            token.text_len = scan - pos;
            token_vec_push(vec, token);
            pos = scan;
        } else if (css_starts_number(source, pos, length)) {
            Py_ssize_t after_number = css_scan_number(source, pos, length);
            Py_ssize_t unit_end = after_number;
            if (unit_end < length && source[unit_end] == '%') {
                unit_end++;
            } else {
                while (unit_end < length && ((source[unit_end] >= 'a' && source[unit_end] <= 'z') ||
                                             (source[unit_end] >= 'A' && source[unit_end] <= 'Z'))) {
                    unit_end++;
                }
            }
            token.kind = CSS_NUM;
            token.text = &source[pos];
            token.text_len = after_number - pos;
            token.unit_len = unit_end - after_number;
            token_vec_push(vec, token);
            pos = unit_end;
        } else if ((character == 'u' || character == 'U') && pos + 2 < length && source[pos + 1] == '+' &&
                   (css_is_hex(source[pos + 2]) || source[pos + 2] == '?')) {
            Py_ssize_t scan = pos + 2;
            Py_ssize_t digits = 0;
            while (scan < length && digits < 6 && (css_is_hex(source[scan]) || source[scan] == '?')) {
                scan++;
                digits++;
            }
            if (scan < length && source[scan] == '-') {
                scan++;
                digits = 0;
                while (scan < length && digits < 6 && css_is_hex(source[scan])) {
                    scan++;
                    digits++;
                }
            }
            token.kind = CSS_URANGE;
            token.text = &source[pos];
            token.text_len = scan - pos;
            token_vec_push(vec, token);
            pos = scan;
        } else if (css_is_ident(character)) {
            Py_ssize_t scan = pos;
            while (scan < length && css_is_ident(source[scan])) {
                scan += (source[scan] == '\\' && scan + 1 < length) ? 2 : 1;
            }
            int is_url = scan - pos == 3 && css_lower(source[pos]) == 'u' && css_lower(source[pos + 1]) == 'r' &&
                         css_lower(source[pos + 2]) == 'l' && scan < length && source[scan] == '(';
            if (is_url) {
                /* scan to the closing ')', but skip over a quoted argument so a ')' inside a quoted data URI
                   (e.g. an SVG transform="rotate(45)") does not terminate the token early */
                Py_ssize_t end = scan + 1;
                while (end < length && source[end] != ')') {
                    if (source[end] == '"' || source[end] == '\'') {
                        css_char quote = source[end];
                        end++;
                        while (end < length && source[end] != quote) {
                            end += (source[end] == '\\' && end + 1 < length) ? 2 : 1;
                        }
                        if (end < length) {
                            end++;
                        }
                        continue;
                    }
                    end += (source[end] == '\\' && end + 1 < length) ? 2 : 1;
                }
                if (end < length) {
                    end++;
                }
                token.kind = CSS_URL;
                token.text = &source[pos];
                token.text_len = end - pos;
                token_vec_push(vec, token);
                pos = end;
            } else {
                token.kind = CSS_IDENT;
                token.text = &source[pos];
                token.text_len = scan - pos;
                token_vec_push(vec, token);
                pos = scan;
            }
        } else {
            token.kind = CSS_DELIM;
            token.text = &source[pos];
            token.text_len = 1;
            token.delim = character;
            token_vec_push(vec, token);
            pos++;
        }
    }
}

/* A rendered value component refers to its already-minified text by (offset, length) into a per-call code-point pool,
   so the pool can grow without invalidating earlier components. isfunc is 0, 1 (function/url, no following space) or
   2 (a comma/slash separator); kind drives the per-property handlers and the spacing rules. */
typedef enum {
    CK_SEP,
    CK_FUNC,
    CK_IDENT,
    CK_DIM,
    CK_NUM,
    CK_HASH,
    CK_URL,
    CK_STR,
    CK_DELIM,
    CK_PCT,
} css_compkind;

typedef struct {
    Py_ssize_t off;
    Py_ssize_t len;
    int isfunc;
    css_compkind kind;
} css_comp;

typedef struct {
    css_comp *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} comp_vec;

static void comp_vec_push(comp_vec *vec, css_comp comp) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 16;
        css_comp *grown = css_realloc(vec->items, (size_t)cap * sizeof(css_comp));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
            return;          /* GCOVR_EXCL_LINE: allocation-failure path, unreachable from a test */
        }
        vec->items = grown;
        vec->cap = cap;
    }
    vec->items[vec->len++] = comp;
}

/* Append a run of code points to the pool and return its starting offset. */
static Py_ssize_t pool_run(css_buf *pool, const css_char *text, Py_ssize_t len) {
    Py_ssize_t off = pool->len;
    cbuf_put_run(pool, text, len);
    return off;
}

/* Append an ASCII C string to the pool and return its starting offset. */
static Py_ssize_t pool_cstr(css_buf *pool, const char *text) {
    Py_ssize_t off = pool->len;
    cbuf_puts(pool, text);
    return off;
}

/* Case-insensitively compare a code-point run against an ASCII literal. */
static int css_run_ieq(const css_char *text, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len && ascii[index]; index++) {
        if (css_lower(text[index]) != (css_char)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return index == len && ascii[index] == '\0';
}

static int comp_ieq(const css_buf *pool, const css_comp *comp, const char *ascii) {
    return css_run_ieq(pool->data + comp->off, comp->len, ascii);
}

/* Decimal integer width of a (possibly negative) exponent. */
static int css_int_width(int value) {
    int width = value < 0 ? 1 : 0;
    unsigned magnitude = value < 0 ? (unsigned)(-value) : (unsigned)value;
    do {
        width++;
        magnitude /= 10;
    } while (magnitude);
    return width;
}

/* Append the decimal digits of value to the pool (sign handled by the caller for the mantissa exponent). */
static void pool_put_int(css_buf *pool, int value) {
    char buffer[16];
    int written = snprintf(buffer, sizeof(buffer), "%d", value);
    for (int index = 0; index < written; index++) {
        cbuf_putc(pool, (css_char)(unsigned char)buffer[index]);
    }
}

/* Shorten a numeric run into the pool (tdewolff minifyNumber): drop a leading +, redundant zeros, and use e-notation
   when shorter (only when scientific, i.e. for dimensions/percentages). Returns the rendered (offset, length). */
static void css_format_number(css_buf *pool, const css_char *num, Py_ssize_t numlen, int scientific,
                              Py_ssize_t *out_off, Py_ssize_t *out_len) {
    /* every caller passes a number-token or built-rational text with at least one digit, so numlen >= 1 */
    Py_ssize_t start = pool->len;
    int negative = num[0] == '-';
    Py_ssize_t cursor = (num[0] == '+' || num[0] == '-') ? 1 : 0;
    Py_ssize_t mant_end = numlen;
    int exponent = 0;
    /* the tokenizer only folds an e/E into a number token when valid exponent digits follow (css_scan_number), so the
       exponent here is always well-formed -- no malformed-exponent fallback is needed */
    for (Py_ssize_t index = cursor; index < numlen; index++) {
        if (num[index] == 'e' || num[index] == 'E') {
            mant_end = index;
            Py_ssize_t exp_pos = index + 1;
            int exp_neg = 0;
            if (num[exp_pos] == '+' || num[exp_pos] == '-') {
                exp_neg = num[exp_pos] == '-';
                exp_pos++;
            }
            for (; exp_pos < numlen; exp_pos++) {
                exponent = exponent * 10 + (int)(num[exp_pos] - '0');
            }
            if (exp_neg) {
                exponent = -exponent;
            }
            break;
        }
    }
    /* collect the mantissa's digits (without the dot), tracking the integer-part length and leading-zero count */
    css_char digits[128];
    Py_ssize_t digit_count = 0;
    Py_ssize_t int_len = 0;
    int seen_dot = 0;
    for (Py_ssize_t index = cursor; index < mant_end; index++) {
        if (num[index] == '.') {
            seen_dot = 1;
            continue;
        }
        if (!seen_dot) {
            int_len++;
        }
        if (digit_count < (Py_ssize_t)(sizeof(digits) / sizeof(digits[0]))) {
            digits[digit_count++] = num[index];
        }
    }
    Py_ssize_t lead = 0;
    while (lead < digit_count && digits[lead] == '0') {
        lead++;
    }
    Py_ssize_t point = int_len + exponent - lead;
    Py_ssize_t first = lead;
    Py_ssize_t last = digit_count;
    while (last > first && digits[last - 1] == '0') {
        last--;
    }
    if (last == first) {
        cbuf_putc(pool, '0');
        *out_off = start;
        *out_len = pool->len - start;
        return;
    }
    Py_ssize_t significant = last - first;
    Py_ssize_t scale = point - significant;
    Py_ssize_t plain_len = scale >= 0 ? significant + scale : (-scale >= significant ? 1 - scale : significant + 1);
    int use_sci = 0;
    if (scientific && scale != 0) {
        Py_ssize_t sci_len = significant + 1 + css_int_width((int)scale);
        if (sci_len < plain_len) {
            use_sci = 1;
        }
    }
    if (negative) {
        cbuf_putc(pool, '-');
    }
    if (use_sci) {
        cbuf_put_run(pool, digits + first, significant);
        cbuf_putc(pool, 'e');
        pool_put_int(pool, (int)scale);
    } else if (scale >= 0) {
        cbuf_put_run(pool, digits + first, significant);
        for (Py_ssize_t index = 0; index < scale; index++) {
            cbuf_putc(pool, '0');
        }
    } else if (-scale >= significant) {
        cbuf_putc(pool, '.');
        for (Py_ssize_t index = 0; index < -scale - significant; index++) {
            cbuf_putc(pool, '0');
        }
        cbuf_put_run(pool, digits + first, significant);
    } else {
        cbuf_put_run(pool, digits + first, significant + scale);
        cbuf_putc(pool, '.');
        cbuf_put_run(pool, digits + first + significant + scale, -scale);
    }
    *out_off = start;
    *out_len = pool->len - start;
}

/* Known dimension units, lower-cased for the zero-collapse check. */
static int css_unit_known(const css_char *unit, Py_ssize_t len) {
    static const char *const units[] = {"px", "em", "rem", "ex",  "ch",   "vw",   "vh",   "vmin", "vmax", "cm",
                                        "mm", "in", "pt",  "pc",  "q",    "deg",  "grad", "rad",  "turn", "s",
                                        "ms", "hz", "khz", "dpi", "dpcm", "dppx", "fr",   "%"};
    for (size_t index = 0; index < sizeof(units) / sizeof(units[0]); index++) {
        if (css_run_ieq(unit, len, units[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_unit_zero_droppable(const css_char *unit, Py_ssize_t len) {
    static const char *const zero_units[] = {"ch", "cm", "deg", "em",  "ex",   "grad", "in",   "mm",   "pc", "pt",
                                             "px", "q",  "rad", "rem", "turn", "vh",   "vmax", "vmin", "vw"};
    for (size_t index = 0; index < sizeof(zero_units) / sizeof(zero_units[0]); index++) {
        if (css_run_ieq(unit, len, zero_units[index])) {
            return 1;
        }
    }
    return 0;
}

/* Render a dimension (number + unit) into the pool: shorten the number, lower-case a known unit, and drop the unit
   when the value is 0 and the unit is a length/angle. */
static void css_format_dimension(css_buf *pool, const css_token *token, int drop_zero_unit, Py_ssize_t *out_off,
                                 Py_ssize_t *out_len) {
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(pool, token->text, token->text_len, token->unit_len != 0, &off, &len);
    int is_zero = len == 1 && pool->data[off] == '0';
    if (drop_zero_unit && is_zero && css_unit_zero_droppable((token->text + token->text_len), token->unit_len)) {
        *out_off = off;
        *out_len = len;
        return;
    }
    if (css_unit_known((token->text + token->text_len), token->unit_len)) {
        for (Py_ssize_t index = 0; index < token->unit_len; index++) {
            cbuf_putc(pool, css_lower((token->text + token->text_len)[index]));
        }
    } else {
        cbuf_put_run(pool, (token->text + token->text_len), token->unit_len);
    }
    *out_off = off;
    *out_len = pool->len - off;
}

/* keyword -> shortest hash via the first-byte bucketed table; returns 1 and sets (off,len) on a hit. */
static int css_name_to_hex(css_buf *pool, const css_char *name, Py_ssize_t len, Py_ssize_t *out_off,
                           Py_ssize_t *out_len) {
    css_char first = css_lower(name[0]);
    if (first < 'a' || first > 'z') {
        return 0;
    }
    for (int index = th_css_name_first[first]; index < th_css_name_first[first + 1]; index++) {
        const th_css_color_entry *entry = &th_css_name_to_hex[index];
        if (entry->key_len == len && css_run_ieq(name, len, entry->key)) {
            *out_off = pool_cstr(pool, entry->val);
            *out_len = entry->val_len;
            return 1;
        }
    }
    return 0;
}

/* hash string (with '#', lower-case) -> shortest keyword via binary search; returns the entry or NULL. */
static const th_css_color_entry *css_hex_to_name(const css_char *hash, Py_ssize_t len) {
    int low = 0;
    int high = th_css_hex_count - 1;
    while (low <= high) {
        int mid = (low + high) / 2;
        const th_css_color_entry *entry = &th_css_hex_to_name[mid];
        Py_ssize_t index = 0;
        int order = 0;
        for (; index < len && index < entry->key_len; index++) {
            css_char left = hash[index];
            css_char right = (css_char)(unsigned char)entry->key[index];
            if (left != right) {
                order = left < right ? -1 : 1;
                break;
            }
        }
        if (order == 0) {
            order = (int)(len - entry->key_len);
        }
        if (order == 0) {
            return entry;
        }
        if (order < 0) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return NULL;
}

/* Try to shorten an ident (color keyword) or hash; returns 1 and sets (off,len) when it changes, else 0. */
static int css_color_keyword_or_hash(css_buf *pool, const css_token *token, int is_color_context, Py_ssize_t *out_off,
                                     Py_ssize_t *out_len) {
    if (token->kind == CSS_IDENT) {
        if (!is_color_context) {
            return 0;
        }
        return css_name_to_hex(pool, token->text, token->text_len, out_off, out_len);
    }
    /* a hash: lower-case it, fold #rrggbbaa with aa==ff/00, then map to a keyword or a 3/4-digit short form */
    css_char data[10];
    Py_ssize_t len = token->text_len;
    if (len > 9) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        data[index] = css_lower(token->text[index]);
    }
    if (len == 9 && data[7] == data[8]) {
        if (data[7] == 'f') {
            len = 7;
        } else if (data[7] == '0') {
            data[0] = '#';
            data[1] = data[2] = data[3] = data[4] = '0';
            len = 5;
        }
    }
    const th_css_color_entry *named = css_hex_to_name(data, len);
    if (named != NULL) {
        *out_off = pool_cstr(pool, named->val);
        *out_len = named->val_len;
        return 1;
    }
    if (len == 7 && data[1] == data[2] && data[3] == data[4] && data[5] == data[6]) {
        css_char packed[4] = {'#', data[1], data[3], data[5]};
        *out_off = pool_run(pool, packed, 4);
        *out_len = 4;
        return 1;
    }
    if (len == 9 && data[1] == data[2] && data[3] == data[4] && data[5] == data[6] && data[7] == data[8]) {
        css_char packed[5] = {'#', data[1], data[3], data[5], data[7]};
        *out_off = pool_run(pool, packed, 5);
        *out_len = 5;
        return 1;
    }
    /* unchanged except for lower-casing */
    *out_off = pool_run(pool, data, len);
    *out_len = len;
    return 1;
}

/* Drop CSS line continuations (a backslash followed by a newline) from a run, appending the rest to the pool. */
static void css_strip_continuations(css_buf *pool, const css_char *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        if (text[index] == '\\' && index + 1 < len && (text[index + 1] == '\n' || text[index + 1] == '\r')) {
            if (text[index + 1] == '\r' && index + 2 < len && text[index + 2] == '\n') {
                index += 3;
            } else {
                index += 2;
            }
            continue;
        }
        cbuf_putc(pool, text[index]);
        index++;
    }
}

/* Whether a url body can drop its quotes: non-empty and free of whitespace, quotes, parens and backslash. */
static int css_url_unquotable(const css_char *text, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        css_char character = text[index];
        if (css_is_ws(character) || character == '"' || character == '\'' || character == '(' || character == ')' ||
            character == '\\') {
            return 0;
        }
    }
    return 1;
}

static void css_minify_string(css_buf *pool, const css_char *text, Py_ssize_t len, Py_ssize_t *out_off,
                              Py_ssize_t *out_len) {
    Py_ssize_t off = pool->len;
    if (len < 2) {
        cbuf_put_run(pool, text, len);
        *out_off = off;
        *out_len = len;
        return;
    }
    css_char quote = text[0];
    int closed = text[len - 1] == quote;
    Py_ssize_t body_len = closed ? len - 2 : len - 1;
    cbuf_putc(pool, quote);
    css_strip_continuations(pool, text + 1, body_len);
    cbuf_putc(pool, quote);
    *out_off = off;
    *out_len = pool->len - off;
}

static void css_minify_url(css_buf *pool, const css_char *text, Py_ssize_t len, Py_ssize_t *out_off,
                           Py_ssize_t *out_len) {
    Py_ssize_t off = pool->len;
    cbuf_put_run(pool, text, 4 < len ? 4 : len); /* the "url(" prefix */
    Py_ssize_t inner_start = 4;
    Py_ssize_t inner_end = len;
    int closed = len > 4 && text[len - 1] == ')';
    if (closed) {
        inner_end--;
    }
    while (inner_start < inner_end && css_is_ws(text[inner_start])) {
        inner_start++;
    }
    while (inner_end > inner_start && css_is_ws(text[inner_end - 1])) {
        inner_end--;
    }
    Py_ssize_t inner_len = inner_end - inner_start;
    const css_char *inner = text + inner_start;
    if (inner_len >= 2 && (inner[0] == '"' || inner[0] == '\'') && inner[inner_len - 1] == inner[0]) {
        /* a quoted body: strip continuations, then drop the quotes when the body is url-unquotable */
        css_buf scratch = {NULL, 0, 0, 0};
        css_strip_continuations(&scratch, inner + 1, inner_len - 2);
        if (css_url_unquotable(scratch.data, scratch.len)) {
            cbuf_put_run(pool, scratch.data, scratch.len);
        } else {
            cbuf_putc(pool, inner[0]);
            cbuf_put_run(pool, scratch.data, scratch.len);
            cbuf_putc(pool, inner[0]);
        }
        cbuf_free(&scratch);
    } else {
        cbuf_put_run(pool, inner, inner_len);
    }
    if (closed) {
        cbuf_putc(pool, ')');
    }
    *out_off = off;
    *out_len = pool->len - off;
}

/* Values within this of 0 or 1 snap to 0/1, matching tdewolff's alpha and percentage rounding. */
#define CSS_EPS 1e-5

static double css_run_to_double(const css_char *text, Py_ssize_t len) {
    char buffer[64];
    Py_ssize_t copy = len < 63 ? len : 63;
    for (Py_ssize_t index = 0; index < copy; index++) {
        buffer[index] = (char)text[index]; /* numeric token text is ASCII [+-.0-9eE] only */
    }
    buffer[copy] = '\0';
    return strtod(buffer, NULL);
}

static double css_hue_to_rgb(double lower, double upper, double hue) {
    if (hue < 0) {
        hue += 1.0;
    }
    if (hue > 1) {
        hue -= 1.0;
    }
    if (hue * 6.0 < 1.0) {
        return lower + (upper - lower) * hue * 6.0;
    }
    if (hue * 2.0 < 1.0) {
        return upper;
    }
    if (hue * 3.0 < 2.0) {
        return lower + (upper - lower) * (2.0 / 3.0 - hue) * 6.0;
    }
    return lower;
}

static void css_hsl_to_rgb(double hue, double saturation, double lightness, double *red, double *green, double *blue) {
    double upper = lightness <= 0.5 ? lightness * (saturation + 1.0) : lightness + saturation - lightness * saturation;
    double lower = lightness * 2.0 - upper;
    *red = css_hue_to_rgb(lower, upper, hue + 1.0 / 3.0);
    *green = css_hue_to_rgb(lower, upper, hue);
    *blue = css_hue_to_rgb(lower, upper, hue - 1.0 / 3.0);
}

/* Render an opaque rgb triple as the shortest hex or color keyword. */
static void css_rgb_to_hex(css_buf *pool, double red, double green, double blue, Py_ssize_t *out_off,
                           Py_ssize_t *out_len) {
    int channels[3] = {(int)(red * 255.0 + 0.5), (int)(green * 255.0 + 0.5), (int)(blue * 255.0 + 0.5)};
    char hex[8];
    snprintf(hex, sizeof(hex), "#%02x%02x%02x", channels[0], channels[1], channels[2]);
    css_char wide[7];
    for (int index = 0; index < 7; index++) {
        wide[index] = (css_char)(unsigned char)hex[index];
    }
    const th_css_color_entry *named = css_hex_to_name(wide, 7);
    if (named != NULL) {
        *out_off = pool_cstr(pool, named->val);
        *out_len = named->val_len;
        return;
    }
    if (hex[1] == hex[2] && hex[3] == hex[4] && hex[5] == hex[6]) {
        css_char packed[4] = {'#', wide[1], wide[3], wide[5]};
        *out_off = pool_run(pool, packed, 4);
        *out_len = 4;
        return;
    }
    *out_off = pool_run(pool, wide, 7);
    *out_len = 7;
}

/* Append num_pct(text): swap between number and percentage forms, whichever is shorter (tdewolff). */
static void css_append_num_pct(css_buf *out, const css_char *text, Py_ssize_t len) {
    if (len == 3 && text[len - 1] == '%' && text[1] == '0') {
        cbuf_putc(out, '.');
        cbuf_putc(out, text[0]);
        return;
    }
    if (len > 2 && text[0] == '.' && text[1] == '0') {
        if (text[2] == '0') {
            cbuf_putc(out, '.');
            cbuf_put_run(out, text + 3, len - 3);
            cbuf_putc(out, '%');
            return;
        }
        if (len == 3) {
            cbuf_putc(out, text[2]);
            cbuf_putc(out, '%');
            return;
        }
    }
    cbuf_put_run(out, text, len);
}

/* Append a color-function component: the minified number, with a percent sign when it is a percentage. */
static void css_append_color_number(css_buf *out, const css_token *raw, int is_pct) {
    css_buf scratch = {NULL, 0, 0, 0};
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(&scratch, raw->text, raw->text_len, is_pct, &off, &len);
    cbuf_put_run(out, scratch.data + off, len);
    if (is_pct) {
        cbuf_putc(out, '%');
    }
    cbuf_free(&scratch);
}

/* Try to fold an rgb()/rgba()/hsl()/hsla() call to a hex, keyword, transparent or shortest functional form. Returns 1
   and sets (off, len) on success, 0 when the name is not a color function or the argument shape is not a color. */
static int css_try_color_func(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                              Py_ssize_t name_len, Py_ssize_t *out_off, Py_ssize_t *out_len) {
    int is_rgb = css_run_ieq(name, name_len, "rgb") || css_run_ieq(name, name_len, "rgba");
    int is_hsl = css_run_ieq(name, name_len, "hsl") || css_run_ieq(name, name_len, "hsla");
    if (!is_rgb && !is_hsl) {
        return 0;
    }
    double values[4];
    int types[4]; /* 1 = percentage, 0 = number */
    const css_token *raws[4];
    int count = 0;
    int has_slash = 0;
    int has_comma = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '/') {
            has_slash = 1;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            has_comma = 1;
        }
        if (token->kind == CSS_NUM) {
            if (count >= 4) {
                return 0;
            }
            double magnitude = css_run_to_double(token->text, token->text_len);
            int is_pct = token->unit_len == 1 && (token->text + token->text_len)[0] == '%';
            if (is_pct) {
                magnitude /= 100.0;
                if (magnitude < CSS_EPS) {
                    magnitude = 0.0;
                } else if (magnitude > 1.0 - CSS_EPS) {
                    magnitude = 1.0;
                }
            }
            values[count] = magnitude;
            types[count] = is_pct;
            raws[count] = token;
            count++;
        }
    }
    if (count < 3) {
        return 0;
    }
    double alpha = 1.0;
    int has_alpha = count == 4;
    if (has_alpha) {
        int all_zero = 1;
        for (int index = 0; index < count; index++) {
            if (values[index] >= CSS_EPS) {
                all_zero = 0;
            }
        }
        if (all_zero) {
            *out_off = pool_cstr(pool, "transparent");
            *out_len = 11;
            return 1;
        }
        if (values[3] > 1.0 - CSS_EPS) {
            has_alpha = 0;
            count = 3;
        } else {
            alpha = values[3];
        }
    }
    /* alpha stays exactly 1.0 only on the paths that also clear has_alpha (count==3, or a >=1 fourth value) */
    if (alpha == 1.0) {
        if (is_rgb) {
            double channels[3];
            for (int index = 0; index < 3; index++) {
                double channel = values[index];
                if (!types[index]) {
                    channel /= 255.0;
                    channel = channel < CSS_EPS ? 0.0 : (channel > 1.0 - CSS_EPS ? 1.0 : channel);
                }
                channels[index] = channel;
            }
            css_rgb_to_hex(pool, channels[0], channels[1], channels[2], out_off, out_len);
            return 1;
        }
        /* the is_rgb block above always returns, and a color func is rgb or hsl, so reaching here means is_hsl */
        if (!types[0] && types[1] && types[2]) {
            double hue = values[0] / 360.0;
            hue -= floor(hue);
            double red;
            double green;
            double blue;
            css_hsl_to_rgb(hue, values[1], values[2], &red, &green, &blue);
            css_rgb_to_hex(pool, red, green, blue, out_off, out_len);
            return 1;
        }
    }
    /* not opaque: rebuild the function in shortest form, keeping rgb percentages as integers when exact 20% steps */
    css_buf result = {NULL, 0, 0, 0};
    for (Py_ssize_t index = 0; index < name_len; index++) {
        cbuf_putc(&result, css_lower(name[index]));
    }
    cbuf_putc(&result, '(');
    const char *separator = has_slash ? " " : (has_comma ? "," : " ");
    int exact = is_rgb;
    if (is_rgb) {
        for (int index = 0; index < 3; index++) {
            double scaled = values[index] * 5.0;
            if (!types[index] || fabs(scaled - floor(scaled + 0.5)) >= 1e-3) {
                exact = 0;
            }
        }
    }
    for (int index = 0; index < 3; index++) {
        if (index > 0) {
            cbuf_puts(&result, separator);
        }
        if (is_rgb && exact) {
            char buffer[8];
            int written = snprintf(buffer, sizeof(buffer), "%d", (int)(values[index] * 255.0 + 0.5));
            for (int pos = 0; pos < written; pos++) {
                cbuf_putc(&result, (css_char)(unsigned char)buffer[pos]);
            }
        } else {
            css_append_color_number(&result, raws[index], types[index]);
        }
    }
    if (has_alpha) {
        css_buf alpha_text = {NULL, 0, 0, 0};
        css_append_color_number(&alpha_text, raws[3], types[3]);
        cbuf_puts(&result, has_slash ? "/" : ",");
        css_append_num_pct(&result, alpha_text.data, alpha_text.len);
        cbuf_free(&alpha_text);
    }
    cbuf_putc(&result, ')');
    *out_off = pool_run(pool, result.data, result.len);
    *out_len = result.len;
    cbuf_free(&result);
    return 1;
}

/* A token cursor for the grammar. */
typedef struct {
    token_vec *vec;
    Py_ssize_t index;
} cursor;

static css_token *cursor_peek(cursor *cur) {
    return cur->index < cur->vec->len ? &cur->vec->items[cur->index] : NULL;
}

/* Advance the cursor over a run of component values up to a top-level stop delimiter, honoring (){}[] nesting, and
   return the end index; the run is [start, end). */
static Py_ssize_t css_read_until(cursor *cur, const char *stops) {
    int depth = 0;
    while (cur->index < cur->vec->len) {
        css_token *token = &cur->vec->items[cur->index];
        if (token->kind == CSS_DELIM) {
            css_char delim = token->delim;
            if (depth == 0 && strchr(stops, (int)delim) != NULL && delim != 0) {
                break;
            }
            if (delim == '(' || delim == '[' || delim == '{') {
                depth++;
            } else if ((delim == ')' || delim == ']' || delim == '}') && depth > 0) {
                /* a stray depth-0 ')' or ']' is consumed below; a depth-0 '}' always ends the run at the stop check
                   above, since every caller includes '}' in its stop set */
                depth--;
            }
        }
        cur->index++;
    }
    return cur->index;
}

/* Find the index of the ')' matching an opening '(' that sits at open_paren, scanning over delim tokens. */
static Py_ssize_t css_match_paren(token_vec *vec, Py_ssize_t open_paren, Py_ssize_t end) {
    int depth = 1;
    Py_ssize_t index = open_paren + 1;
    while (index < end) { /* depth is decremented to 0 only with an immediate break, so it is always >0 at the test */
        if (vec->items[index].kind == CSS_DELIM) {
            if (vec->items[index].delim == '(') {
                depth++;
            } else if (vec->items[index].delim == ')') {
                depth--;
                if (depth == 0) {
                    break;
                }
            }
        }
        index++;
    }
    return index;
}

static int css_is_math_func(const css_char *name, Py_ssize_t len) {
    return css_run_ieq(name, len, "calc") || css_run_ieq(name, len, "min") || css_run_ieq(name, len, "max") ||
           css_run_ieq(name, len, "clamp");
}

/* Trim trailing spaces already written to a scratch buffer (the func-arg comma rule). */
static void css_rtrim(css_buf *buffer) {
    while (buffer->len > 0 && buffer->data[buffer->len - 1] == ' ') {
        buffer->len--;
    }
}

static void css_minify_func_args(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                                 Py_ssize_t name_len, css_buf *out);

static int css_arg_is_zero(const css_char *text, Py_ssize_t len) {
    return (len == 1 && text[0] == '0') || (len == 2 && text[0] == '0' && text[1] == '%');
}

/* Drop a redundant trailing argument from a transform function: translate(x,0) -> translate(x) and
   translate(0,0) -> translate(0) (the omitted Y defaults to 0), scale(n,n) -> scale(n). These are exact identities,
   so the transform is value-safe; translate3d and the single-axis variants are intentionally left untouched. */
static void css_collapse_transform_args(const css_char *name, Py_ssize_t name_len, css_buf *args) {
    int is_translate = css_run_ieq(name, name_len, "translate");
    int is_scale = css_run_ieq(name, name_len, "scale");
    if (!is_translate && !is_scale) {
        return;
    }
    Py_ssize_t comma = -1;
    int depth = 0;
    for (Py_ssize_t index = 0; index < args->len; index++) {
        css_char character = args->data[index];
        if (character == '(') {
            depth++;
        } else if (character == ')') {
            depth--;
        } else if (character == ',' && depth == 0) {
            if (comma != -1) { /* more than two arguments: not a form we collapse */
                return;
            }
            comma = index;
        }
    }
    if (comma < 0) {
        return;
    }
    Py_ssize_t first_len = comma;
    const css_char *second = args->data + comma + 1;
    Py_ssize_t second_len = args->len - comma - 1;
    if ((is_translate && css_arg_is_zero(second, second_len)) ||
        (is_scale && first_len == second_len &&
         memcmp(args->data, second, (size_t)first_len * sizeof(css_char)) == 0)) {
        args->len = first_len;
    }
}

/* Render a function `name(args)` into the pool and return its (offset, length). The function text is assembled in a
   local buffer first: minifying the arguments uses the pool as scratch, so building straight into the pool would
   interleave that scratch into the function's bytes. */
static void css_render_function(css_buf *pool, token_vec *vec, Py_ssize_t name_index, Py_ssize_t close_index,
                                Py_ssize_t *out_off, Py_ssize_t *out_len) {
    css_token *name_token = &vec->items[name_index];
    css_buf function = {NULL, 0, 0, 0};
    cbuf_put_run(&function, name_token->text, name_token->text_len);
    cbuf_putc(&function, '(');
    css_buf args = {NULL, 0, 0, 0};
    css_minify_func_args(pool, vec, name_index + 2, close_index, name_token->text, name_token->text_len, &args);
    css_collapse_transform_args(name_token->text, name_token->text_len, &args);
    cbuf_put_run(&function, args.data, args.len);
    cbuf_free(&args);
    cbuf_putc(&function, ')');
    *out_off = pool_run(pool, function.data, function.len);
    *out_len = function.len;
    cbuf_free(&function);
}

/* A calc() simplifier. It evaluates the expression exactly with rational arithmetic and bails -- keeping the input
   verbatim -- on anything it cannot prove safe: an opaque term (var/min/max/clamp/env/an unknown function), units
   that do not combine, integer overflow, or a non-terminating decimal result. So every rewrite is value-exact. */
#define CALC_MAX_TERMS 16
#define CALC_MAX_UNIT 12

typedef struct {
    long long num;
    long long den;
} crat;

typedef struct {
    crat coeff;
    char unit[CALC_MAX_UNIT];
    int unit_len;
} cterm;

typedef struct {
    cterm terms[CALC_MAX_TERMS];
    int count;
    int ok;
} csum;

typedef struct {
    token_vec *vec;
    Py_ssize_t pos;
    Py_ssize_t end;
} calc_parser;

/* The sole caller (rat_set) passes a strictly positive denominator as right, so right is already positive and the
   resulting gcd is always >= 1. */
static long long css_gcd(long long left, long long right) {
    left = left < 0 ? -left : left;
    while (right != 0) {
        long long rem = left % right;
        left = right;
        right = rem;
    }
    return left;
}

/* The denominator is always strictly positive here: css_num_to_rat builds it as a power of ten, rat_mul/rat_add
   multiply already-positive denominators, and division bails on a zero divisor and sign-normalizes the inverse before
   multiplying (calc_parse_product), so neither a zero nor a negative denominator can reach this point. */
static int rat_set(crat *out, long long num, long long den) {
    long long divisor = css_gcd(num, den);
    out->num = num / divisor;
    out->den = den / divisor;
    return 1;
}

static int rat_mul(crat *out, crat left, crat right) {
    long long num;
    long long den;
    if (css_mul_overflow(left.num, right.num, &num) || css_mul_overflow(left.den, right.den, &den)) {
        return 0;
    }
    return rat_set(out, num, den);
}

static int rat_add(crat *out, crat left, crat right) {
    long long cross_left;
    long long cross_right;
    long long num;
    long long den;
    if (css_mul_overflow(left.num, right.den, &cross_left) || css_mul_overflow(right.num, left.den, &cross_right) ||
        css_add_overflow(cross_left, cross_right, &num) || css_mul_overflow(left.den, right.den, &den)) {
        return 0;
    }
    return rat_set(out, num, den);
}

/* Parse a CSS number string exactly into a rational; returns 0 on overflow. The text is a whole number token from the
   tokenizer's css_scan_number grammar, so it always carries a digit, any exponent has digits, and the parse consumes
   the entire string -- integer overflow is the only failure. */
static int css_num_to_rat(const css_char *text, Py_ssize_t len, crat *out) {
    long long num = 0;
    int frac_digits = 0;
    int negative = 0;
    Py_ssize_t index = 0;
    if (text[index] == '+' || text[index] == '-') { /* len >= 1: a NUM token's numeric part is never empty */
        negative = text[index] == '-';
        index++;
    }
    for (; index < len && text[index] >= '0' && text[index] <= '9'; index++) {
        if (css_mul_overflow(num, 10, &num) || css_add_overflow(num, text[index] - '0', &num)) {
            return 0;
        }
    }
    if (index < len && text[index] == '.') {
        index++;
        /* css_scan_number only admits digits in the fractional run, so no char below '0' reaches the loop body */
        for (; index < len && text[index] <= '9'; index++) {
            if (css_mul_overflow(num, 10, &num) || css_add_overflow(num, text[index] - '0', &num)) {
                return 0;
            }
            frac_digits++;
        }
    }
    int exponent = 0;
    int exp_negative = 0;
    /* after the mantissa, the only remaining char a number token can carry is the exponent marker e/E */
    if (index < len) {
        index++;
        /* css_scan_number includes the exponent marker only when digits follow, so a char is always present here */
        if (text[index] == '+' || text[index] == '-') {
            exp_negative = text[index] == '-';
            index++;
        }
        /* the exponent's digit run is the final part of the numeric text, so every remaining char is a digit */
        for (; index < len; index++) {
            exponent = exponent * 10 + (text[index] - '0');
        }
    }
    if (negative) {
        num = -num;
    }
    int power = (exp_negative ? -exponent : exponent) - frac_digits;
    long long den = 1;
    if (power >= 0) {
        for (int step = 0; step < power; step++) {
            if (css_mul_overflow(num, 10, &num)) {
                return 0;
            }
        }
    } else {
        for (int step = 0; step < -power; step++) {
            if (css_mul_overflow(den, 10, &den)) {
                return 0;
            }
        }
    }
    return rat_set(out, num, den);
}

/* Add a term to a sum, combining a like unit. Zero terms are kept (not dropped) so a result that cancels to zero
   keeps its unit -- a 0% must not become a bare 0, which is invalid for a percentage-only property like font-stretch;
   the formatter drops the redundant zeros at the end. Returns 0 on overflow or running out of term slots. */
static int csum_add_term(csum *sum, const cterm *term) {
    for (int index = 0; index < sum->count; index++) {
        if (sum->terms[index].unit_len == term->unit_len &&
            memcmp(sum->terms[index].unit, term->unit, (size_t)term->unit_len) == 0) {
            return rat_add(&sum->terms[index].coeff, sum->terms[index].coeff, term->coeff);
        }
    }
    if (sum->count >= CALC_MAX_TERMS) {
        return 0;
    }
    sum->terms[sum->count++] = *term;
    return 1;
}

/* A sum is a scalar when it is empty (zero) or a single unitless term. */
static int csum_as_scalar(const csum *sum, crat *out) {
    if (sum->count == 0) {
        out->num = 0;
        out->den = 1;
        return 1;
    }
    if (sum->count == 1 && sum->terms[0].unit_len == 0) {
        *out = sum->terms[0].coeff;
        return 1;
    }
    return 0;
}

static int csum_scale(csum *sum, crat factor) {
    if (factor.num == 0) {
        sum->count = 0;
        return 1;
    }
    for (int index = 0; index < sum->count; index++) {
        if (!rat_mul(&sum->terms[index].coeff, sum->terms[index].coeff, factor)) {
            return 0;
        }
    }
    return 1;
}

static void calc_skip_ws(calc_parser *parser) {
    while (parser->pos < parser->end &&
           (parser->vec->items[parser->pos].kind == CSS_WS || parser->vec->items[parser->pos].kind == CSS_COMMENT)) {
        parser->pos++;
    }
}

static csum calc_parse_sum(calc_parser *parser);

static csum calc_parse_value(calc_parser *parser) {
    csum result = {0};
    calc_skip_ws(parser);
    if (parser->pos >= parser->end) {
        return result;
    }
    css_token *token = &parser->vec->items[parser->pos];
    if (token->kind == CSS_NUM) {
        parser->pos++;
        if (token->unit_len >= CALC_MAX_UNIT) {
            return result;
        }
        cterm term;
        if (!css_num_to_rat(token->text, token->text_len, &term.coeff)) {
            return result;
        }
        term.unit_len = (int)token->unit_len;
        for (int index = 0; index < term.unit_len; index++) {
            term.unit[index] = (char)css_lower((token->text + token->text_len)[index]);
        }
        result.ok = 1;
        csum_add_term(&result, &term); /* the first term always fits an empty sum (no like-unit, a free slot) */
        return result;
    }
    if (token->kind == CSS_DELIM && token->delim == '(') {
        parser->pos++;
        result = calc_parse_sum(parser);
        calc_skip_ws(parser);
        if (parser->pos < parser->end && parser->vec->items[parser->pos].kind == CSS_DELIM &&
            parser->vec->items[parser->pos].delim == ')') {
            parser->pos++;
        } else {
            result.ok = 0;
        }
        return result;
    }
    if (token->kind == CSS_IDENT && parser->pos + 1 < parser->end &&
        parser->vec->items[parser->pos + 1].kind == CSS_DELIM && parser->vec->items[parser->pos + 1].delim == '(' &&
        css_run_ieq(token->text, token->text_len, "calc")) {
        Py_ssize_t close = css_match_paren(parser->vec, parser->pos + 1, parser->end);
        calc_parser inner = {parser->vec, parser->pos + 2, close};
        result = calc_parse_sum(&inner);
        calc_skip_ws(&inner);
        if (inner.pos != close) {
            result.ok = 0;
        }
        parser->pos = close + 1;
        return result;
    }
    return result; /* an opaque term (var/min/max/clamp/ident/...) cannot be evaluated */
}

static csum calc_parse_product(calc_parser *parser) {
    csum acc = calc_parse_value(parser);
    if (!acc.ok) {
        return acc;
    }
    for (;;) {
        calc_skip_ws(parser);
        if (parser->pos >= parser->end) {
            break;
        }
        css_token *token = &parser->vec->items[parser->pos];
        if (token->kind != CSS_DELIM || (token->delim != '*' && token->delim != '/')) {
            break;
        }
        int is_div = token->delim == '/';
        parser->pos++;
        csum rhs = calc_parse_value(parser);
        if (!rhs.ok) {
            acc.ok = 0;
            return acc;
        }
        crat scalar;
        if (is_div) {
            if (csum_as_scalar(&rhs, &scalar) && scalar.num != 0) {
                crat inverse = {scalar.den, scalar.num};
                if (inverse.den < 0) {
                    inverse.num = -inverse.num;
                    inverse.den = -inverse.den;
                }
                if (!csum_scale(&acc, inverse)) {
                    acc.ok = 0;
                    return acc;
                }
            } else {
                acc.ok = 0;
                return acc;
            }
        } else if (csum_as_scalar(&rhs, &scalar)) {
            if (!csum_scale(&acc, scalar)) {
                acc.ok = 0;
                return acc;
            }
        } else if (csum_as_scalar(&acc, &scalar)) {
            if (!csum_scale(&rhs, scalar)) {
                acc.ok = 0;
                return acc;
            }
            acc = rhs;
        } else {
            acc.ok = 0;
            return acc;
        }
    }
    return acc;
}

static csum calc_parse_sum(calc_parser *parser) {
    csum acc = calc_parse_product(parser);
    if (!acc.ok) {
        return acc;
    }
    for (;;) {
        calc_skip_ws(parser);
        if (parser->pos >= parser->end) {
            break;
        }
        css_token *token = &parser->vec->items[parser->pos];
        /* a lone '-' surrounded by spaces tokenizes as an identifier (it is a valid identifier start), not a delim,
           so the subtraction operator is recognized in both forms; '+' is always a delim */
        int is_plus = token->kind == CSS_DELIM && token->delim == '+';
        int is_sub = token->kind == CSS_IDENT && token->text_len == 1 && token->text[0] == '-';
        if (!is_plus && !is_sub) {
            break;
        }
        parser->pos++;
        csum rhs = calc_parse_product(parser);
        if (!rhs.ok) {
            acc.ok = 0;
            return acc;
        }
        for (int index = 0; index < rhs.count; index++) {
            cterm term = rhs.terms[index];
            if (is_sub) {
                term.coeff.num = -term.coeff.num;
            }
            if (!csum_add_term(&acc, &term)) {
                acc.ok = 0;
                return acc;
            }
        }
    }
    return acc;
}

/* Format a rational exactly: an integer, or a terminating decimal, then minified. Returns 0 if non-terminating. */
static int css_format_rat(crat value, css_buf *out, int scientific) {
    css_char raw[40];
    Py_ssize_t raw_len = 0;
    long long num = value.num;
    long long den = value.den;
    int negative = num < 0;
    if (negative) {
        num = -num;
    }
    if (den == 1) {
        char buffer[24];
        int written = snprintf(buffer, sizeof(buffer), "%lld", num);
        if (negative) {
            raw[raw_len++] = '-';
        }
        for (int index = 0; index < written; index++) {
            raw[raw_len++] = (css_char)(unsigned char)buffer[index];
        }
    } else {
        long long reduced = den;
        int twos = 0;
        int fives = 0;
        while (reduced % 2 == 0) {
            reduced /= 2;
            twos++;
        }
        while (reduced % 5 == 0) {
            reduced /= 5;
            fives++;
        }
        if (reduced != 1) {
            return 0; /* non-terminating decimal: keep the expression verbatim */
        }
        int magnitude = twos > fives ? twos : fives;
        long long power = 1;
        for (int step = 0; step < magnitude; step++) {
            if (css_mul_overflow(power, 10, &power)) {
                return 0;
            }
        }
        long long scaled;
        if (css_mul_overflow(num, power / den, &scaled)) {
            return 0;
        }
        char buffer[24];
        int written = snprintf(buffer, sizeof(buffer), "%lld", scaled);
        if (negative) {
            raw[raw_len++] = '-';
        }
        int int_digits = written - magnitude;
        if (int_digits <= 0) {
            raw[raw_len++] = '0';
            raw[raw_len++] = '.';
            for (int pad = 0; pad < -int_digits; pad++) {
                raw[raw_len++] = '0';
            }
            for (int index = 0; index < written; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
        } else {
            for (int index = 0; index < int_digits; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
            raw[raw_len++] = '.';
            for (int index = int_digits; index < written; index++) {
                raw[raw_len++] = (css_char)(unsigned char)buffer[index];
            }
        }
    }
    Py_ssize_t off;
    Py_ssize_t len;
    css_format_number(out, raw, raw_len, scientific, &off, &len);
    return 1;
}

static int css_format_cterm(css_buf *out, const cterm *term) {
    /* a dimensioned coefficient shortens with scientific notation like any dimension (calc(1e3px) -> 1e3px, not
       1000px); a unitless result must not (matching the plain-number rule), so gate scientific on the unit */
    if (!css_format_rat(term->coeff, out, term->unit_len > 0)) {
        return 0;
    }
    for (int index = 0; index < term->unit_len; index++) {
        cbuf_putc(out, (css_char)(unsigned char)term->unit[index]);
    }
    return 1;
}

static int css_char_unit_droppable(const char *unit, int len) {
    css_char wide[CALC_MAX_UNIT];
    for (int index = 0; index < len; index++) {
        wide[index] = (css_char)(unsigned char)unit[index];
    }
    return css_unit_zero_droppable(wide, len);
}

/* Format a zero term, keeping the unit only where a 0 of that unit is not the same as a bare 0 (so 0px -> 0 but a
   0% / 0s / 0deg... keeps its unit, matching the dimension rules). */
static void css_format_zero_term(css_buf *out, const cterm *term) {
    cbuf_putc(out, '0');
    if (term->unit_len > 0 && !css_char_unit_droppable(term->unit, term->unit_len)) {
        for (int index = 0; index < term->unit_len; index++) {
            cbuf_putc(out, (css_char)(unsigned char)term->unit[index]);
        }
    }
}

/* Try to simplify calc(args); returns 1 and writes the shortest exact form to the pool, 0 to keep the input. */
static int css_try_calc(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, Py_ssize_t *out_off,
                        Py_ssize_t *out_len) {
    calc_parser parser = {vec, start, end};
    csum sum = calc_parse_sum(&parser);
    calc_skip_ws(&parser);
    if (!sum.ok || parser.pos != end) {
        return 0;
    }
    /* the sum keeps canceled (zero) terms to preserve units; drop them now unless every term is zero */
    cterm nonzero[CALC_MAX_TERMS];
    int nonzero_count = 0;
    for (int index = 0; index < sum.count; index++) {
        if (sum.terms[index].coeff.num != 0) {
            nonzero[nonzero_count++] = sum.terms[index];
        }
    }
    css_buf result = {NULL, 0, 0, 0};
    int formatted = 1;
    if (nonzero_count == 0) {
        if (sum.count == 1) {
            css_format_zero_term(&result, &sum.terms[0]);
        } else {
            cbuf_putc(&result, '0');
        }
    } else if (nonzero_count == 1) {
        formatted = css_format_cterm(&result, &nonzero[0]);
    } else {
        cbuf_puts(&result, "calc(");
        formatted = css_format_cterm(&result, &nonzero[0]);
        for (int index = 1; formatted && index < nonzero_count; index++) {
            cterm term = nonzero[index];
            int negative = term.coeff.num < 0;
            if (negative) {
                term.coeff.num = -term.coeff.num;
            }
            cbuf_puts(&result, negative ? " - " : " + ");
            formatted = css_format_cterm(&result, &term);
        }
        cbuf_putc(&result, ')');
    }
    if (!formatted) {
        cbuf_free(&result);
        return 0;
    }
    *out_off = pool_run(pool, result.data, result.len);
    *out_len = result.len;
    cbuf_free(&result);
    return 1;
}

/* Render a function value, first trying to simplify a calc()/fold an rgb()/hsl() color, else the generic form.
   ends_paren is set when the rendered text is a function call (so the assembler glues the following component). */
static void css_emit_function(css_buf *pool, token_vec *vec, Py_ssize_t name_index, Py_ssize_t close_index,
                              Py_ssize_t *out_off, Py_ssize_t *out_len, int *ends_paren) {
    css_token *name_token = &vec->items[name_index];
    if (css_run_ieq(name_token->text, name_token->text_len, "calc") &&
        css_try_calc(pool, vec, name_index + 2, close_index, out_off, out_len)) {
        /* a successful calc/color render always emits at least one byte, so out_len is positive */
        *ends_paren = pool->data[*out_off + *out_len - 1] == ')';
        return;
    }
    if (css_try_color_func(pool, vec, name_index + 2, close_index, name_token->text, name_token->text_len, out_off,
                           out_len)) {
        *ends_paren = pool->data[*out_off + *out_len - 1] == ')';
        return;
    }
    css_render_function(pool, vec, name_index, close_index, out_off, out_len);
    *ends_paren = 1;
}

/* Minify a function's argument list into out (tdewolff minifyFunctionArgs), recursing for nested functions. var()
   keeps its raw fallback; calc/min/max/clamp keep their spaced operators. */
static void css_minify_func_args(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *name,
                                 Py_ssize_t name_len, css_buf *out) {
    int is_var = css_run_ieq(name, name_len, "var");
    int keep_ws = css_is_math_func(name, name_len);
    int pending_ws = 0;
    Py_ssize_t index = start;
    while (index < end) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            index++;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            css_rtrim(out);
            cbuf_putc(out, ',');
            pending_ws = 0;
            index++;
            continue;
        }
        /* a lone '-' tokenizes as an identifier, never a delim, so only '+' appears as a delim operator here */
        if (!is_var && keep_ws && token->kind == CSS_DELIM && token->delim == '+') {
            if (out->len > 0 && out->data[out->len - 1] != ' ') {
                cbuf_putc(out, ' ');
            }
            cbuf_putc(out, token->delim);
            cbuf_putc(out, ' ');
            pending_ws = 0;
            index++;
            continue;
        }
        /* a leading space is kept only between two value pieces, never after '(' or ',' */
        if (pending_ws && out->len > 0 && out->data[out->len - 1] != ' ' && out->data[out->len - 1] != ',' &&
            out->data[out->len - 1] != '(') {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_IDENT && index + 1 < end && vec->items[index + 1].kind == CSS_DELIM &&
            vec->items[index + 1].delim == '(') {
            Py_ssize_t close_index = css_match_paren(vec, index + 1, end);
            Py_ssize_t off;
            Py_ssize_t len;
            int ends_paren;
            css_emit_function(pool, vec, index, close_index, &off, &len, &ends_paren);
            cbuf_put_run(out, pool->data + off, len);
            index = close_index + 1;
            continue;
        }
        if (token->kind == CSS_NUM) {
            if (is_var) { /* var() keeps its fallback verbatim, so the number and unit are not minified */
                cbuf_put_run(out, token->text, token->text_len);
                cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
            } else {
                Py_ssize_t off;
                Py_ssize_t len;
                css_format_dimension(pool, token, !keep_ws, &off, &len);
                cbuf_put_run(out, pool->data + off, len);
            }
        } else if (token->kind == CSS_STR) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_string(pool, token->text, token->text_len, &off, &len);
            if (!is_var && css_run_ieq(name, name_len, "local") && len >= 2 &&
                css_url_unquotable(pool->data + off + 1, len - 2)) {
                cbuf_put_run(out, pool->data + off + 1, len - 2);
            } else {
                cbuf_put_run(out, pool->data + off, len);
            }
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
        index++;
    }
    css_rtrim(out);
}

/* Render a value's tokens [start, end) into components in the pool. */
static void css_render_components(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, int is_color,
                                  comp_vec *comps) {
    Py_ssize_t index = start;
    while (index < end) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            index++;
            continue;
        }
        css_comp comp = {0};
        if (token->kind == CSS_DELIM && (token->delim == ',' || token->delim == '/')) {
            comp.off = pool->len;
            cbuf_putc(pool, token->delim);
            comp.len = 1;
            comp.isfunc = 2;
            comp.kind = CK_SEP;
            comp_vec_push(comps, comp);
            index++;
            continue;
        }
        if (token->kind == CSS_IDENT && index + 1 < end && vec->items[index + 1].kind == CSS_DELIM &&
            vec->items[index + 1].delim == '(') {
            Py_ssize_t close_index = css_match_paren(vec, index + 1, end);
            int ends_paren;
            css_emit_function(pool, vec, index, close_index, &comp.off, &comp.len, &ends_paren);
            comp.isfunc = ends_paren ? 1 : 0;
            comp.kind = ends_paren ? CK_FUNC : CK_IDENT;
            comp_vec_push(comps, comp);
            index = close_index + 1;
            continue;
        }
        if (token->kind == CSS_NUM) {
            css_format_dimension(pool, token, 1, &comp.off, &comp.len);
            comp.kind = token->unit_len ? CK_DIM : CK_NUM;
        } else if (token->kind == CSS_HASH) {
            if (!css_color_keyword_or_hash(pool, token, 1, &comp.off, &comp.len)) {
                comp.off = pool_run(pool, token->text, token->text_len); /* GCOVR_EXCL_LINE: hash always renders */
                comp.len = token->text_len;                              /* GCOVR_EXCL_LINE */
            }
            comp.kind = CK_HASH;
        } else if (token->kind == CSS_IDENT) {
            if (!css_color_keyword_or_hash(pool, token, is_color, &comp.off, &comp.len)) {
                comp.off = pool_run(pool, token->text, token->text_len);
                comp.len = token->text_len;
            }
            comp.kind = CK_IDENT;
        } else if (token->kind == CSS_URL) {
            css_minify_url(pool, token->text, token->text_len, &comp.off, &comp.len);
            comp.isfunc = 1;
            comp.kind = CK_URL;
        } else if (token->kind == CSS_STR) {
            css_minify_string(pool, token->text, token->text_len, &comp.off, &comp.len);
            comp.kind = CK_STR;
        } else {
            comp.off = pool_run(pool, token->text, token->text_len);
            comp.len = token->text_len;
            comp.kind = CK_DELIM;
        }
        /* every branch above renders at least one char (a delim is text_len 1, a string keeps its quotes, etc.) */
        comp_vec_push(comps, comp);
        index++;
    }
}

/* Assemble components into the value buffer: a single space between components, except after a function/url/separator,
   before a parenthesised piece, or around a bare delimiter. */
static void css_assemble(css_buf *pool, comp_vec *comps, css_buf *out) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (index > 0) {
            css_comp *prev = &comps->items[index - 1];
            int starts_paren = pool->data[comp->off] == '('; /* every assembled comp has len >= 1 */
            int glued = comp->isfunc == 2 || prev->isfunc == 1 || prev->isfunc == 2 || starts_paren ||
                        comp->kind == CK_DELIM || prev->kind == CK_DELIM;
            if (!glued) {
                cbuf_putc(out, ' ');
            }
        }
        cbuf_put_run(out, pool->data + comp->off, comp->len);
    }
}

/* Property classification for the value pipeline. */
static int css_prop_is_color(const css_char *prop, Py_ssize_t len) {
    static const char *const color_props[] = {"color",
                                              "background-color",
                                              "border-color",
                                              "border-top-color",
                                              "border-right-color",
                                              "border-bottom-color",
                                              "border-left-color",
                                              "outline-color",
                                              "text-decoration-color",
                                              "caret-color",
                                              "column-rule-color",
                                              "fill",
                                              "stroke",
                                              "stop-color",
                                              "flood-color",
                                              "lighting-color",
                                              "text-emphasis-color"};
    for (size_t index = 0; index < sizeof(color_props) / sizeof(color_props[0]); index++) {
        if (css_run_ieq(prop, len, color_props[index])) {
            return 1;
        }
    }
    return 0;
}

/* Render a custom property or otherwise-raw value: collapse whitespace and comments, keep everything else verbatim. */
static void css_render_raw_value(token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    int pending_ws = 0;
    int any_ws = 0;
    Py_ssize_t written = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            any_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (pending_ws && written > 0) {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_NUM) {
            cbuf_put_run(out, token->text, token->text_len);
            cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
        written++;
    }
    /* strip(): trailing space cannot occur (only inserted before a piece); a pure-whitespace value folds to one space
     */
    if (written == 0 && any_ws) {
        cbuf_putc(out, ' ');
    }
}

static int css_prop_in(const css_char *prop, Py_ssize_t len, const char *const *list, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (css_run_ieq(prop, len, list[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_comp_text_eq(const css_buf *pool, const css_comp *left, const css_comp *right) {
    return left->len == right->len &&
           memcmp(pool->data + left->off, pool->data + right->off, (size_t)left->len * sizeof(css_char)) == 0;
}

static int css_comp_kw(const css_buf *pool, const css_comp *comp, const char *keyword) {
    return comp->kind == CK_IDENT && comp_ieq(pool, comp, keyword);
}

static void css_set_comp(css_buf *pool, css_comp *comp, const char *text, css_compkind kind) {
    comp->off = pool_cstr(pool, text);
    comp->len = (Py_ssize_t)strlen(text);
    comp->isfunc = 0;
    comp->kind = kind;
}

static int css_comp_is_zero(const css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_NUM && comp->kind != CK_DIM && comp->kind != CK_PCT) {
        return 0;
    }
    char buffer[64];
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < comp->len && written < 63; index++) {
        css_char character = pool->data[comp->off + index];
        if (character == '%') {
            break;
        }
        buffer[written++] = (char)character; /* numeric/percentage value text is ASCII only */
    }
    buffer[written] = '\0';
    return strtod(buffer, NULL) == 0.0;
}

/* Replace an ident component naming a color with its shortest hash. */
static void css_minify_color_comp(css_buf *pool, css_comp *comp) {
    if (comp->kind != CK_IDENT) {
        return;
    }
    Py_ssize_t off;
    Py_ssize_t len;
    if (css_name_to_hex(pool, pool->data + comp->off, comp->len, &off, &len)) {
        comp->off = off;
        comp->len = len;
        comp->kind = CK_HASH;
        comp->isfunc = 0;
    }
}

static const char *const CSS_BOX_PROPS[] = {"margin", "padding",       "border-width",  "border-style",
                                            "inset",  "border-radius", "scroll-margin", "scroll-padding",
                                            "gap",    "grid-gap",      "overflow",      "overscroll-behavior"};

static const char *const CSS_CURRENTCOLOR_INIT[] = {"border-left-color",     "border-right-color",
                                                    "border-top-color",      "border-bottom-color",
                                                    "text-decoration-color", "text-emphasis-color"};

static const char *const CSS_SINGLE_COLOR[] = {"color", "caret-color", "outline-color",
                                               "fill",  "stroke",      "background-color"};

/* Collapse a 2-4 value box shorthand (margin/padding/...) when the mirrored edges are equal. */
static void css_handle_box(const css_buf *pool, comp_vec *comps) {
    /* dispatched only when the value has no separator (css_apply_handler), so no component is a CK_SEP here */
    Py_ssize_t count = comps->len;
    if (count < 2 || count > 4) {
        return;
    }
    if (count == 4 && css_comp_text_eq(pool, &comps->items[3], &comps->items[1])) {
        count = 3;
    }
    if (count == 3 && css_comp_text_eq(pool, &comps->items[2], &comps->items[0])) {
        count = 2;
    }
    if (count == 2 && css_comp_text_eq(pool, &comps->items[1], &comps->items[0])) {
        count = 1;
    }
    comps->len = count;
}

static void css_handle_font_weight(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (css_comp_kw(pool, comp, "normal")) {
            css_set_comp(pool, comp, "400", CK_NUM);
        } else if (css_comp_kw(pool, comp, "bold")) {
            css_set_comp(pool, comp, "700", CK_NUM);
        }
    }
}

static void css_handle_flex_basis(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (css_comp_kw(pool, comp, "initial")) {
            css_set_comp(pool, comp, "auto", CK_IDENT);
        } else if (css_comp_is_zero(pool, comp)) {
            css_set_comp(pool, comp, "0", CK_NUM);
        }
    }
}

static void css_handle_flex_grow(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (css_comp_kw(pool, &comps->items[index], "initial")) {
            css_set_comp(pool, &comps->items[index], "0", CK_NUM);
        }
    }
}

static void css_handle_flex_shrink(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (css_comp_kw(pool, &comps->items[index], "initial")) {
            css_set_comp(pool, &comps->items[index], "1", CK_NUM);
        }
    }
}

static void css_handle_color_single(css_buf *pool, comp_vec *comps, const css_char *prop, Py_ssize_t prop_len) {
    int currentcolor_init =
        css_prop_in(prop, prop_len, CSS_CURRENTCOLOR_INIT, sizeof(CSS_CURRENTCOLOR_INIT) / sizeof(char *));
    int is_background = css_run_ieq(prop, prop_len, "background-color");
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp *comp = &comps->items[index];
        if (comp->kind == CK_SEP) {
            continue;
        }
        if (currentcolor_init && css_comp_kw(pool, comp, "currentcolor")) {
            css_set_comp(pool, comp, "initial", CK_IDENT);
            continue;
        }
        css_minify_color_comp(pool, comp);
        if (is_background && css_comp_kw(pool, comp, "transparent")) {
            css_set_comp(pool, comp, "initial", CK_IDENT);
        }
    }
}

static void css_handle_color_drop(css_buf *pool, comp_vec *comps, const css_char *prop, Py_ssize_t prop_len) {
    static const char *const border_drop[] = {"none", "currentcolor", "medium"};
    static const char *const outline_drop[] = {"invert", "none", "medium"};
    static const char *const column_drop[] = {"currentcolor", "none", "medium"};
    static const char *const decoration_drop[] = {"currentcolor", "none", "solid"};
    static const char *const emphasis_drop[] = {"currentcolor", "none"};
    const char *const *drop = border_drop;
    size_t drop_count = 3;
    if (css_run_ieq(prop, prop_len, "outline")) {
        drop = outline_drop;
    } else if (css_run_ieq(prop, prop_len, "column-rule")) {
        drop = column_drop;
    } else if (css_run_ieq(prop, prop_len, "text-decoration")) {
        drop = decoration_drop;
    } else if (css_run_ieq(prop, prop_len, "text-emphasis")) {
        drop = emphasis_drop;
        drop_count = 2;
    }
    Py_ssize_t write = 0;
    int any_value = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp comp = comps->items[index];
        if (comp.kind == CK_SEP) {
            comps->items[write++] = comp;
            continue;
        }
        if (comp.kind == CK_IDENT) {
            int dropped = 0;
            for (size_t which = 0; which < drop_count; which++) {
                if (comp_ieq(pool, &comp, drop[which])) {
                    dropped = 1;
                    break;
                }
            }
            if (dropped) {
                continue;
            }
        }
        css_minify_color_comp(pool, &comp);
        comps->items[write++] = comp;
        any_value = 1;
    }
    comps->len = write;
    if (!any_value) {
        comps->len = 0;
        css_comp none = {0};
        css_set_comp(pool, &none, "none", CK_IDENT);
        comp_vec_push(comps, none);
    }
}

static void css_handle_border_color(css_buf *pool, comp_vec *comps) {
    Py_ssize_t write = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        css_comp comp = comps->items[index];
        if (comp.kind == CK_SEP) {
            continue;
        }
        if (css_comp_kw(pool, &comp, "currentcolor")) {
            css_set_comp(pool, &comp, "initial", CK_IDENT);
        } else {
            css_minify_color_comp(pool, &comp);
        }
        comps->items[write++] = comp;
    }
    comps->len = write;
    if (write > 0) {
        int all_equal = 1;
        for (Py_ssize_t index = 1; index < write; index++) {
            if (!css_comp_text_eq(pool, &comps->items[index], &comps->items[0])) {
                all_equal = 0;
                break;
            }
        }
        if (all_equal) {
            comps->len = 1;
        }
    }
}

static void css_handle_text_shadow(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind != CK_SEP) {
            css_minify_color_comp(pool, &comps->items[index]);
        }
    }
}

/* Collapse a two-keyword background-repeat run (repeat repeat -> repeat, repeat no-repeat -> repeat-x, ...). */
static Py_ssize_t css_collapse_repeat_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    if (count == 2 && items[0].kind == CK_IDENT && items[1].kind == CK_IDENT) {
        if (css_comp_text_eq(pool, &items[0], &items[1])) {
            return 1;
        }
        if (comp_ieq(pool, &items[0], "repeat") && comp_ieq(pool, &items[1], "no-repeat")) {
            css_set_comp(pool, &items[0], "repeat-x", CK_IDENT);
            return 1;
        }
        if (comp_ieq(pool, &items[0], "no-repeat") && comp_ieq(pool, &items[1], "repeat")) {
            css_set_comp(pool, &items[0], "repeat-y", CK_IDENT);
            return 1;
        }
    }
    return count;
}

/* Apply a per-run transform across comma-separated runs in place, rebuilding comps. */
typedef Py_ssize_t (*css_run_transform)(css_buf *pool, css_comp *items, Py_ssize_t count);

static void css_handle_runs(css_buf *pool, comp_vec *comps, css_run_transform transform) {
    comp_vec result = {NULL, 0, 0, 0};
    Py_ssize_t run_start = 0;
    Py_ssize_t index = 0;
    while (index <= comps->len) {
        int at_sep = index < comps->len && comps->items[index].kind == CK_SEP;
        if (index == comps->len || at_sep) {
            Py_ssize_t kept = transform(pool, comps->items + run_start, index - run_start);
            for (Py_ssize_t pos = 0; pos < kept; pos++) {
                comp_vec_push(&result, comps->items[run_start + pos]);
            }
            if (at_sep) {
                comp_vec_push(&result, comps->items[index]);
            }
            run_start = index + 1;
        }
        index++;
    }
    css_free(comps->items);
    *comps = result;
}

static Py_ssize_t css_collapse_size_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    if (count == 2 && comp_ieq(pool, &items[1], "auto")) {
        return 1;
    }
    return count;
}

static Py_ssize_t css_collapse_box_shadow_run(css_buf *pool, css_comp *items, Py_ssize_t count) {
    /* css_handle_runs splits on CK_SEP, so a run never contains a separator -- every item is a value to color-minify */
    for (Py_ssize_t index = 0; index < count; index++) {
        css_minify_color_comp(pool, &items[index]);
    }
    if (count == 1 && css_comp_kw(pool, &items[0], "initial")) {
        css_set_comp(pool, &items[0], "none", CK_IDENT);
        return 1;
    }
    Py_ssize_t numbers[8];
    Py_ssize_t number_count = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if ((items[index].kind == CK_NUM || items[index].kind == CK_DIM) && number_count < 8) {
            numbers[number_count++] = index;
        }
    }
    /* drop a zero blur/spread (the 4th then 3rd length); delete the element in place, since a color may follow it */
    if (number_count == 4 && css_comp_is_zero(pool, &items[numbers[3]])) {
        for (Py_ssize_t index = numbers[3]; index + 1 < count; index++) {
            items[index] = items[index + 1];
        }
        count--;
        number_count = 3;
    }
    if (number_count == 3 && css_comp_is_zero(pool, &items[numbers[2]])) {
        for (Py_ssize_t index = numbers[2]; index + 1 < count; index++) {
            items[index] = items[index + 1];
        }
        count--;
    }
    return count;
}

/* Append a color-function-style filter value (progid alpha shortening), returning 1 when handled. */
static void css_handle_filter(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *prop,
                              Py_ssize_t prop_len, css_buf *out) {
    css_buf joined = {NULL, 0, 0, 0};
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_NUM) {
            cbuf_put_run(&joined, token->text, token->text_len);
            cbuf_put_run(&joined, (token->text + token->text_len), token->unit_len);
        } else if (token->kind == CSS_STR) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_string(pool, token->text, token->text_len, &off, &len);
            cbuf_put_run(&joined, pool->data + off, len);
        } else if (token->kind == CSS_URL) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_minify_url(pool, token->text, token->text_len, &off, &len);
            cbuf_put_run(&joined, pool->data + off, len);
        } else {
            cbuf_put_run(&joined, token->text, token->text_len);
        }
    }
    const char *legacy = "progid:dximagetransform.microsoft.alpha(opacity=";
    Py_ssize_t legacy_len = (Py_ssize_t)strlen(legacy);
    int is_ms = css_run_ieq(prop, prop_len, "-ms-filter");
    int quote = joined.len > 0 && (joined.data[0] == '"' || joined.data[0] == '\'');
    Py_ssize_t scan = is_ms && quote ? 1 : 0;
    int matches_legacy = joined.len - scan >= legacy_len;
    for (Py_ssize_t index = 0; matches_legacy && index < legacy_len; index++) {
        if (css_lower(joined.data[scan + index]) != (css_char)(unsigned char)legacy[index]) {
            matches_legacy = 0;
        }
    }
    /* matches_legacy implies joined.len >= legacy_len (48), so it is always positive here */
    if (!is_ms && matches_legacy && joined.data[joined.len - 1] == ')') {
        cbuf_puts(out, "alpha(opacity=");
        cbuf_put_run(out, joined.data + legacy_len, joined.len - legacy_len);
    } else if (is_ms && quote && matches_legacy) {
        cbuf_putc(out, joined.data[0]);
        cbuf_puts(out, "alpha(opacity=");
        cbuf_put_run(out, joined.data + 1 + legacy_len, joined.len - 1 - legacy_len);
    } else {
        cbuf_put_run(out, joined.data, joined.len);
    }
    cbuf_free(&joined);
}

static int css_parse_unicode_range(const css_char *data, Py_ssize_t len, long long *low, long long *high) {
    Py_ssize_t pos = 2;
    long long start = 0;
    Py_ssize_t wildcard_at = 0;
    while (pos < len && data[pos] != '-') {
        start *= 16;
        css_char character = css_lower(data[pos]);
        /* a U+ token's range body is only [0-9a-f?]; '-' ends this loop, so no char below '0' reaches here */
        if (character <= '9') {
            start += character - '0';
        } else if (character >= 'a') {
            start += character - 'a' + 10;
        } else if (wildcard_at == 0) {
            wildcard_at = pos; /* the only remaining body char is '?' */
        }
        pos++;
    }
    long long end = start;
    if (wildcard_at != 0) {
        long long span = 1;
        for (Py_ssize_t index = 0; index < len - wildcard_at; index++) {
            span *= 16;
        }
        end = start + span - 1;
    } else if (pos < len) { /* the first loop stops only at '-' or end, so a non-end position is always '-' */
        pos++;
        end = 0;
        while (pos < len) {
            end *= 16;
            css_char character = css_lower(data[pos]);
            /* the end body is only [0-9a-f] (no '?'), so the two hex classes are exhaustive */
            if (character <= '9') {
                end += character - '0';
            } else {
                end += character - 'a' + 10;
            }
            pos++;
        }
        if (end <= start) {
            end = start;
        }
    }
    *low = start;
    *high = end;
    return 1;
}

/* Minify a unicode-range value, returning 1 when handled (every token was a range), 0 to fall through. */
static int css_handle_unicode_range(token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    Py_ssize_t capacity = 16;
    long long (*ranges)[2] = css_malloc((size_t)capacity * sizeof(*ranges));
    if (ranges == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return 0;         /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t count = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ',') {
            continue;
        }
        if (token->kind != CSS_URANGE) {
            css_free(ranges);
            return 0;
        }
        if (count == capacity) {
            capacity *= 2;
            long long (*grown)[2] = css_realloc(ranges, (size_t)capacity * sizeof(*ranges));
            if (grown == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                css_free(ranges); /* GCOVR_EXCL_LINE */
                return 0;         /* GCOVR_EXCL_LINE */
            }
            ranges = grown;
        }
        css_parse_unicode_range(token->text, token->text_len, &ranges[count][0], &ranges[count][1]);
        count++;
    }
    for (Py_ssize_t outer = 1; outer < count; outer++) {
        long long low = ranges[outer][0];
        long long high = ranges[outer][1];
        Py_ssize_t inner = outer - 1;
        while (inner >= 0 && ranges[inner][0] > low) {
            ranges[inner + 1][0] = ranges[inner][0];
            ranges[inner + 1][1] = ranges[inner][1];
            inner--;
        }
        ranges[inner + 1][0] = low;
        ranges[inner + 1][1] = high;
    }
    Py_ssize_t merged = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if (merged > 0 && ranges[index][0] <= ranges[merged - 1][1] + 1) {
            if (ranges[index][1] > ranges[merged - 1][1]) {
                ranges[merged - 1][1] = ranges[index][1];
            }
        } else {
            ranges[merged][0] = ranges[index][0];
            ranges[merged][1] = ranges[index][1];
            merged++;
        }
    }
    for (Py_ssize_t index = 0; index < merged; index++) {
        if (index != 0) {
            cbuf_putc(out, ',');
        }
        long long low = ranges[index][0];
        long long high = ranges[index][1];
        char buffer[32];
        if (low == high) {
            int written = snprintf(buffer, sizeof(buffer), "U+%llX", low);
            for (int pos = 0; pos < written; pos++) {
                cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
            }
        } else if (low == 0 && high == 0x10FFFF) {
            cbuf_puts(out, "initial");
        } else {
            int nibble = 0;
            while (nibble < 6 && ((low >> (nibble * 4)) & 0xF) == 0 && ((high >> (nibble * 4)) & 0xF) == 0xF) {
                nibble++;
            }
            int wildcards = nibble;
            while (nibble < 6) {
                if (((low >> (nibble * 4)) & 0xF) != ((high >> (nibble * 4)) & 0xF)) {
                    wildcards = 0;
                    break;
                }
                nibble++;
            }
            if (wildcards != 0) {
                long long prefix = low >> (wildcards * 4);
                cbuf_puts(out, "U+");
                if (prefix != 0) {
                    int written = snprintf(buffer, sizeof(buffer), "%llX", prefix);
                    for (int pos = 0; pos < written; pos++) {
                        cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
                    }
                }
                for (int pos = 0; pos < wildcards; pos++) {
                    cbuf_putc(out, '?');
                }
            } else {
                int written = snprintf(buffer, sizeof(buffer), "U+%llX-%llX", low, high);
                for (int pos = 0; pos < written; pos++) {
                    cbuf_putc(out, (css_char)(unsigned char)buffer[pos]);
                }
            }
        }
    }
    css_free(ranges);
    return 1;
}

/* A background-position value during the keyword<->offset normalization passes. data refers to pool text by
   (off, len); ident records the edge keyword so the normalizer can rewrite right/bottom to left/top offsets. */
typedef enum { PVK_COMMA, PVK_IDENT, PVK_PCT, PVK_NUM, PVK_LEN } pv_kind;
typedef enum { PVI_NONE, PVI_LEFT, PVI_RIGHT, PVI_TOP, PVI_BOTTOM, PVI_CENTER } pv_ident;
typedef struct {
    pv_kind kind;
    Py_ssize_t off;
    Py_ssize_t len;
    pv_ident ident;
} pos_val;

/* The only caller guards this with comp->kind == CK_IDENT, so the component is always an identifier here. */
static pv_ident css_pv_ident_of(css_buf *pool, const css_comp *comp) {
    if (comp_ieq(pool, comp, "left")) {
        return PVI_LEFT;
    }
    if (comp_ieq(pool, comp, "right")) {
        return PVI_RIGHT;
    }
    if (comp_ieq(pool, comp, "top")) {
        return PVI_TOP;
    }
    if (comp_ieq(pool, comp, "bottom")) {
        return PVI_BOTTOM;
    }
    if (comp_ieq(pool, comp, "center")) {
        return PVI_CENTER;
    }
    return PVI_NONE;
}

static void css_pv_set(css_buf *pool, pos_val *value, pv_kind kind, const char *text, pv_ident ident) {
    value->kind = kind;
    value->off = pool_cstr(pool, text);
    value->len = (Py_ssize_t)strlen(text);
    value->ident = ident;
}

static int css_pv_is_zero(css_buf *pool, const pos_val *value) {
    if (value->kind == PVK_IDENT) {
        return 0;
    }
    char buffer[64];
    Py_ssize_t written = 0;
    for (Py_ssize_t index = 0; index < value->len && written < 63; index++) {
        css_char character = pool->data[value->off + index];
        if (character == '%') {
            break;
        }
        buffer[written++] = (char)character; /* numeric/percentage value text is ASCII only */
    }
    buffer[written] = '\0';
    return strtod(buffer, NULL) == 0.0;
}

static void css_pv_del(pos_val *vals, Py_ssize_t *count, Py_ssize_t index) {
    for (Py_ssize_t pos = index; pos + 1 < *count; pos++) {
        vals[pos] = vals[pos + 1];
    }
    (*count)--;
}

/* Resolve a 1-4 value background-position run to the shortest equivalent offsets, porting tdewolff's bgPosRun. */
static void css_normalize_position_run(css_buf *pool, pos_val *vals, Py_ssize_t *count_io) {
    Py_ssize_t count = *count_io;
    if (count == 3 || count == 4) {
        Py_ssize_t kept = count;
        Py_ssize_t order[2] = {count - 1, 1};
        for (int which = 0; which < 2; which++) {
            Py_ssize_t pos = order[which];
            /* pos is count-1 or 1, and kept>2 holds only before any delete, so pos<count is always true here */
            if (kept > 2 && css_pv_is_zero(pool, &vals[pos])) {
                css_pv_del(vals, &count, pos);
                kept--;
            }
        }
        Py_ssize_t axis = (count > 2 && vals[2].kind == PVK_IDENT) ? 2 : 1;
        pos_val offsets[2];
        int has_offset[2] = {0, 0};
        int offsets_len = 2;
        Py_ssize_t loop[2] = {axis, 0};
        for (int which = 0; which < 2; which++) {
            Py_ssize_t pos = loop[which];
            if (pos + 1 < count && pos + 1 != axis) {
                if (vals[pos + 1].kind == PVK_PCT && (vals[pos].ident == PVI_RIGHT || vals[pos].ident == PVI_BOTTOM)) {
                    char digits[64];
                    Py_ssize_t written = 0;
                    for (Py_ssize_t index = 0; index < vals[pos + 1].len - 1 && written < 63; index++) {
                        css_char character = pool->data[vals[pos + 1].off + index];
                        digits[written++] = (char)character; /* percentage value text is ASCII only */
                    }
                    digits[written] = '\0';
                    char flipped[24];
                    /* long long, not long: long is 32-bit on Windows, so a percentage that overflows it would flip to
                       a different value there than on a 64-bit platform */
                    snprintf(flipped, sizeof(flipped), "%lld%%", 100 - strtoll(digits, NULL, 10));
                    vals[pos + 1].off = pool_cstr(pool, flipped);
                    vals[pos + 1].len = (Py_ssize_t)strlen(flipped);
                    if (vals[pos].ident == PVI_RIGHT) {
                        css_pv_set(pool, &vals[pos], PVK_IDENT, "left", PVI_LEFT);
                    } else {
                        css_pv_set(pool, &vals[pos], PVK_IDENT, "top", PVI_TOP);
                    }
                }
                if (vals[pos].ident == PVI_LEFT) {
                    offsets[0] = vals[pos + 1];
                    has_offset[0] = 1;
                } else if (vals[pos].ident == PVI_TOP) {
                    offsets[1] = vals[pos + 1];
                    has_offset[1] = 1;
                }
            } else if (vals[pos].ident == PVI_LEFT) {
                css_pv_set(pool, &offsets[0], PVK_NUM, "0", PVI_NONE);
                has_offset[0] = 1;
            } else if (vals[pos].ident == PVI_TOP) {
                css_pv_set(pool, &offsets[1], PVK_NUM, "0", PVI_NONE);
                has_offset[1] = 1;
            } else if (vals[pos].ident == PVI_RIGHT) {
                css_pv_set(pool, &offsets[0], PVK_PCT, "100%", PVI_NONE);
                has_offset[0] = 1;
                vals[pos].ident = PVI_LEFT;
            } else if (vals[pos].ident == PVI_BOTTOM) {
                css_pv_set(pool, &offsets[1], PVK_PCT, "100%", PVI_NONE);
                has_offset[1] = 1;
                vals[pos].ident = PVI_TOP;
            }
        }
        if (vals[0].ident == PVI_CENTER || vals[axis].ident == PVI_CENTER) {
            if (vals[0].ident == PVI_LEFT || vals[axis].ident == PVI_LEFT) {
                offsets_len = 1;
            } else if (vals[0].ident == PVI_TOP || vals[axis].ident == PVI_TOP) {
                css_pv_set(pool, &offsets[0], PVK_PCT, "50%", PVI_NONE);
                has_offset[0] = 1;
            }
        }
        if (has_offset[0] && (offsets_len == 1 || has_offset[1])) {
            /* the guard above makes has_offset[which] true for every which in [0, offsets_len) */
            Py_ssize_t rebuilt = 0;
            for (int which = 0; which < offsets_len; which++) {
                vals[rebuilt++] = offsets[which];
            }
            count = rebuilt;
        }
    }
    if (count == 1 || count == 2) {
        if (count == 1 && (vals[0].ident == PVI_TOP || vals[0].ident == PVI_BOTTOM)) {
            *count_io = count;
            return;
        }
        if (count == 2 && ((vals[0].ident == PVI_TOP || vals[0].ident == PVI_BOTTOM) ||
                           (vals[1].ident == PVI_LEFT || vals[1].ident == PVI_RIGHT))) {
            pos_val swap = vals[0];
            vals[0] = vals[1];
            vals[1] = swap;
        }
        Py_ssize_t limit = count;
        Py_ssize_t pos = 0;
        while (pos < limit) {
            pos_val *value = &vals[pos];
            if (value->kind == PVK_IDENT) {
                if (value->ident == PVI_LEFT || value->ident == PVI_TOP) {
                    css_pv_set(pool, value, PVK_NUM, "0", PVI_NONE);
                } else if (value->ident == PVI_RIGHT || value->ident == PVI_BOTTOM) {
                    css_pv_set(pool, value, PVK_PCT, "100%", PVI_NONE);
                } else if (value->ident == PVI_CENTER) {
                    if (pos == 0) {
                        css_pv_set(pool, value, PVK_PCT, "50%", PVI_NONE);
                    } else {
                        css_pv_del(vals, &count, 1);
                        limit--;
                    }
                }
            } else if (pos == 1 && value->kind == PVK_PCT && css_run_ieq(pool->data + value->off, value->len, "50%")) {
                css_pv_del(vals, &count, 1);
                limit--;
            } else if (value->kind == PVK_PCT && pool->data[value->off] == '0') {
                css_pv_set(pool, value, PVK_NUM, "0", PVI_NONE);
            }
            pos++;
        }
    }
    *count_io = count;
}

/* Convert a component run [start, end) to position values and normalize it into out_run; returns the value count. */
static Py_ssize_t css_position_run(css_buf *pool, const css_comp *items, Py_ssize_t count, pos_val *out_run) {
    Py_ssize_t run_count = 0;
    for (Py_ssize_t index = 0; index < count && run_count < 64; index++) {
        const css_comp *comp = &items[index];
        pos_val value;
        value.off = comp->off;
        value.len = comp->len;
        value.ident = PVI_NONE;
        if (comp->kind == CK_IDENT) {
            value.kind = PVK_IDENT;
            value.ident = css_pv_ident_of(pool, comp);
        } else if (comp->kind == CK_DIM && pool->data[comp->off + comp->len - 1] == '%') {
            /* a CK_DIM component always has at least one character, so its last byte is in bounds */
            value.kind = PVK_PCT;
        } else if (comp->kind == CK_NUM) {
            value.kind = PVK_NUM;
        } else {
            value.kind = PVK_LEN;
        }
        out_run[run_count++] = value;
    }
    css_normalize_position_run(pool, out_run, &run_count);
    return run_count;
}

static void css_emit_position_run(comp_vec *result, const pos_val *run, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        css_comp comp;
        comp.off = run[index].off;
        comp.len = run[index].len;
        comp.isfunc = 0;
        comp.kind = run[index].kind == PVK_PCT ? CK_PCT : (run[index].kind == PVK_IDENT ? CK_IDENT : CK_NUM);
        comp_vec_push(result, comp);
    }
}

static void css_handle_background_position(css_buf *pool, comp_vec *comps) {
    comp_vec result = {NULL, 0, 0, 0};
    css_comp run_items[64];
    pos_val run[64];
    Py_ssize_t run_count = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP &&
                       pool->data[comps->items[index].off] == ','; /* a SEP comp is always len 1 */
        if (index == comps->len || at_comma) {
            Py_ssize_t resolved = css_position_run(pool, run_items, run_count, run);
            if (!first_run) {
                css_comp sep;
                sep.off = pool_cstr(pool, ",");
                sep.len = 1;
                sep.isfunc = 2;
                sep.kind = CK_SEP;
                comp_vec_push(&result, sep);
            }
            first_run = 0;
            css_emit_position_run(&result, run, resolved);
            run_count = 0;
        } else if (run_count < 64) {
            run_items[run_count++] = comps->items[index];
        }
    }
    css_free(comps->items);
    *comps = result;
}

/* Replace vec[at, at+remove) with the add_count components from add (which must not alias vec). Every call site
   replaces a run with no more components than it removes -- minification never expands a value -- so this only ever
   shrinks or rewrites in place and never has to grow the backing store. */
static void comp_vec_splice(comp_vec *vec, Py_ssize_t at, Py_ssize_t remove, const css_comp *add,
                            Py_ssize_t add_count) {
    if (add_count != remove) {
        memmove(&vec->items[at + add_count], &vec->items[at + remove],
                (size_t)(vec->len - at - remove) * sizeof(css_comp));
    }
    for (Py_ssize_t index = 0; index < add_count; index++) {
        vec->items[at + index] = add[index];
    }
    vec->len += add_count - remove;
}

static int css_func_name_is(css_buf *pool, const css_comp *comp, const char *name) {
    if (comp->kind != CK_FUNC) {
        return 0;
    }
    /* a CK_FUNC component always contains its '(' within comp->len, so the scan always finds it before the end */
    Py_ssize_t paren = 0;
    while (pool->data[comp->off + paren] != '(') {
        paren++;
    }
    return css_run_ieq(pool->data + comp->off, paren, name);
}

static int css_comp_is_lp(css_buf *pool, const css_comp *comp) {
    /* a percentage value component is a CK_DIM (unit '%') here; CK_PCT is only produced for position output, which is
       never re-examined through this predicate, so a bare number or dimension is the length/percentage case */
    if (comp->kind == CK_NUM || comp->kind == CK_DIM) {
        return 1;
    }
    return css_func_name_is(pool, comp, "calc") || css_func_name_is(pool, comp, "min") ||
           css_func_name_is(pool, comp, "max") || css_func_name_is(pool, comp, "clamp");
}

static int css_comp_is_position_keyword(css_buf *pool, const css_comp *comp) {
    return css_comp_kw(pool, comp, "left") || css_comp_kw(pool, comp, "right") || css_comp_kw(pool, comp, "top") ||
           css_comp_kw(pool, comp, "bottom") || css_comp_kw(pool, comp, "center");
}

static int css_comp_is_repeat_keyword(css_buf *pool, const css_comp *comp) {
    return comp->kind == CK_IDENT && (css_comp_kw(pool, comp, "space") || css_comp_kw(pool, comp, "round") ||
                                      css_comp_kw(pool, comp, "repeat") || css_comp_kw(pool, comp, "no-repeat"));
}

static void css_position_single(css_buf *pool, const css_comp *items, Py_ssize_t count, comp_vec *out) {
    pos_val run[64];
    Py_ssize_t resolved = css_position_run(pool, items, count, run);
    css_emit_position_run(out, run, resolved);
}

/* Resolve one background layer in place, porting tdewolff's minifyBackground over a single comma-separated run. */
static void css_background_run(css_buf *pool, comp_vec *run) {
    int had_tokens = run->len > 0;
    Py_ssize_t index = 0;
    while (index < run->len) {
        css_comp *comp = &run->items[index];
        /* css_handle_background splits on ',' SEPs, so the only SEP left in a layer run is the '/' size separator */
        if (comp->kind == CK_SEP) {
            int first_size = index + 1 < run->len && (css_comp_is_lp(pool, &run->items[index + 1]) ||
                                                      css_comp_kw(pool, &run->items[index + 1], "auto"));
            if (first_size) {
                int second_size = index + 2 < run->len && (css_comp_is_lp(pool, &run->items[index + 2]) ||
                                                           css_comp_kw(pool, &run->items[index + 2], "auto"));
                if (second_size) {
                    css_comp size[2] = {run->items[index + 1], run->items[index + 2]};
                    Py_ssize_t size_len = css_comp_kw(pool, &size[1], "auto") ? 1 : 2;
                    if (size_len == 1 && css_comp_kw(pool, &size[0], "auto")) {
                        comp_vec_splice(run, index, 3, NULL, 0);
                        index--;
                    } else {
                        comp_vec_splice(run, index + 1, 2, size, size_len);
                    }
                } else if (css_comp_kw(pool, &run->items[index + 1], "auto")) {
                    comp_vec_splice(run, index, 2, NULL, 0);
                    index--;
                }
            }
        }
        index++;
    }
    Py_ssize_t padding_box_at = -1;
    index = 0;
    while (index < run->len) {
        css_minify_color_comp(pool, &run->items[index]);
        css_comp *comp = &run->items[index];
        if (comp->kind == CK_IDENT) {
            css_comp *next = index + 1 < run->len ? &run->items[index + 1] : NULL;
            if (next != NULL && css_comp_is_repeat_keyword(pool, comp) && css_comp_is_repeat_keyword(pool, next)) {
                css_comp pair[2] = {run->items[index], run->items[index + 1]};
                Py_ssize_t repeat = css_collapse_repeat_run(pool, pair, 2);
                if (repeat == 1 && css_comp_kw(pool, &pair[0], "repeat")) {
                    comp_vec_splice(run, index, 2, NULL, 0);
                    index--;
                } else {
                    comp_vec_splice(run, index, 2, pair, repeat);
                }
                index++;
                continue;
            }
            if (css_comp_kw(pool, comp, "none") || css_comp_kw(pool, comp, "scroll") ||
                css_comp_kw(pool, comp, "transparent")) {
                comp_vec_splice(run, index, 1, NULL, 0);
                continue;
            }
            if (css_comp_kw(pool, comp, "border-box") || css_comp_kw(pool, comp, "padding-box")) {
                if (padding_box_at == -1 && css_comp_kw(pool, comp, "padding-box")) {
                    padding_box_at = index;
                } else if (padding_box_at != -1 && css_comp_kw(pool, comp, "border-box")) {
                    comp_vec_splice(run, index, 1, NULL, 0);
                    comp_vec_splice(run, padding_box_at, 1, NULL, 0);
                    index -= 2;
                }
                index++;
                continue;
            }
        } else if (comp->kind == CK_HASH && comp_ieq(pool, comp, "#0000")) {
            comp_vec_splice(run, index, 1, NULL, 0);
            continue;
        } else if (css_func_name_is(pool, comp, "var")) {
            index++;
            continue;
        }
        if (css_comp_is_lp(pool, &run->items[index]) || css_comp_is_position_keyword(pool, &run->items[index])) {
            Py_ssize_t scan = index + 1;
            while (scan < run->len &&
                   (css_comp_is_position_keyword(pool, &run->items[scan]) || css_comp_is_lp(pool, &run->items[scan]))) {
                scan++;
            }
            comp_vec position = {NULL, 0, 0, 0};
            css_position_single(pool, &run->items[index], scan - index, &position);
            /* in a single background layer run the only SEP is the '/' size separator, always len 1 */
            int has_size = scan < run->len && run->items[scan].kind == CK_SEP;
            if (!has_size && position.len == 2 && css_comp_is_zero(pool, &position.items[0]) &&
                css_comp_is_zero(pool, &position.items[1])) {
                if (run->len == 2) {
                    css_set_comp(pool, &run->items[index], "0", CK_NUM);
                    css_set_comp(pool, &run->items[index + 1], "0", CK_NUM);
                    index++;
                } else {
                    comp_vec_splice(run, index, scan - index, NULL, 0);
                    index--;
                }
            } else {
                comp_vec_splice(run, index, scan - index, position.items, position.len);
                index += position.len - 1;
            }
            css_free(position.items);
        }
        index++;
    }
    if (had_tokens && run->len == 0) {
        css_comp zero_x;
        css_comp zero_y;
        css_set_comp(pool, &zero_x, "0", CK_NUM);
        css_set_comp(pool, &zero_y, "0", CK_NUM);
        comp_vec_push(run, zero_x);
        comp_vec_push(run, zero_y);
    }
}

static void css_handle_background(css_buf *pool, comp_vec *comps) {
    comp_vec result = {NULL, 0, 0, 0};
    Py_ssize_t start = 0;
    int first_run = 1;
    for (Py_ssize_t index = 0; index <= comps->len; index++) {
        int at_comma = index < comps->len && comps->items[index].kind == CK_SEP &&
                       pool->data[comps->items[index].off] == ','; /* a SEP comp is always len 1 */
        if (index == comps->len || at_comma) {
            comp_vec run = {NULL, 0, 0, 0};
            for (Py_ssize_t pos = start; pos < index; pos++) {
                comp_vec_push(&run, comps->items[pos]);
            }
            css_background_run(pool, &run);
            if (!first_run) {
                css_comp sep;
                sep.off = pool_cstr(pool, ",");
                sep.len = 1;
                sep.isfunc = 2;
                sep.kind = CK_SEP;
                comp_vec_push(&result, sep);
            }
            first_run = 0;
            for (Py_ssize_t pos = 0; pos < run.len; pos++) {
                comp_vec_push(&result, run.items[pos]);
            }
            css_free(run.items);
            start = index + 1;
        }
    }
    css_free(comps->items);
    *comps = result;
}

static int css_flex_is_zero(css_buf *pool, const css_comp *comp) {
    Py_ssize_t len = comp->len;
    while (len > 0 && pool->data[comp->off + len - 1] == '%') {
        len--;
    }
    return len == 1 && pool->data[comp->off] == '0';
}

static int css_comp_single_digit(css_buf *pool, const css_comp *comp) {
    return comp->len == 1 && pool->data[comp->off] >= '0' && pool->data[comp->off] <= '9';
}

/* Collapse the flex shorthand to its shortest equivalent (tdewolff flexCollapse). */
static void css_handle_flex(css_buf *pool, comp_vec *comps) {
    if (comps->len == 3 && css_comp_single_digit(pool, &comps->items[0]) &&
        css_comp_single_digit(pool, &comps->items[1])) {
        int zero0 = comp_ieq(pool, &comps->items[0], "0");
        int one0 = comp_ieq(pool, &comps->items[0], "1");
        int one1 = comp_ieq(pool, &comps->items[1], "1");
        if (comp_ieq(pool, &comps->items[2], "auto")) {
            if (zero0 && one1) {
                css_set_comp(pool, &comps->items[0], "initial", CK_IDENT);
                comps->len = 1;
            } else if (one0 && one1) {
                css_set_comp(pool, &comps->items[0], "auto", CK_IDENT);
                comps->len = 1;
            } else if (zero0 && comp_ieq(pool, &comps->items[1], "0")) {
                css_set_comp(pool, &comps->items[0], "none", CK_IDENT);
                comps->len = 1;
            }
        } else if (one1 && css_flex_is_zero(pool, &comps->items[2])) {
            comps->len = 1;
        } else if (css_flex_is_zero(pool, &comps->items[2])) {
            comps->len = 2;
        }
    } else if (comps->len == 2 && comps->items[0].kind == CK_NUM && comps->items[1].kind != CK_NUM &&
               css_flex_is_zero(pool, &comps->items[1])) {
        comps->len = 1;
    }
}

static int css_is_ident_string(const css_char *text, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    Py_ssize_t index = text[0] == '-' ? 1 : 0;
    if (index >= len) {
        return 0;
    }
    css_char first = text[index];
    if (!((first >= 'a' && first <= 'z') || first == '_')) {
        return 0;
    }
    for (index++; index < len; index++) {
        css_char character = text[index];
        if (!((character >= 'a' && character <= 'z') || (character >= '0' && character <= '9') || character == '_' ||
              character == '-')) {
            return 0;
        }
    }
    return 1;
}

/* Lower-case a quoted font-family name, dropping the quotes when every space-separated word is a bare identifier. */
static css_comp css_font_family_comp(css_buf *pool, css_comp comp) {
    if (comp.kind != CK_STR || comp.len <= 2) {
        return comp;
    }
    css_char quote = pool->data[comp.off];
    Py_ssize_t body_start = comp.off + 1;
    /* css_minify_string always re-emits the matching closing quote, so a CK_STR comp always ends with it */
    Py_ssize_t body_len = comp.len - 2;
    css_buf lowered = {NULL, 0, 0, 0};
    for (Py_ssize_t index = 0; index < body_len; index++) {
        cbuf_putc(&lowered, css_lower(pool->data[body_start + index]));
    }
    int all_ident = lowered.len > 0;
    Py_ssize_t word_start = 0;
    for (Py_ssize_t index = 0; all_ident && index <= lowered.len; index++) {
        if (index == lowered.len || lowered.data[index] == ' ') {
            if (!css_is_ident_string(lowered.data + word_start, index - word_start)) {
                all_ident = 0;
            }
            word_start = index + 1;
        }
    }
    css_comp result;
    if (all_ident) {
        result.off = pool_run(pool, lowered.data, lowered.len);
        result.len = lowered.len;
        result.isfunc = 0;
        result.kind = CK_IDENT;
    } else {
        result.off = pool->len;
        cbuf_putc(pool, quote);
        cbuf_put_run(pool, lowered.data, lowered.len);
        cbuf_putc(pool, quote);
        result.len = pool->len - result.off;
        result.isfunc = 0;
        result.kind = CK_STR;
    }
    cbuf_free(&lowered);
    return result;
}

static void css_handle_font_family(css_buf *pool, comp_vec *comps) {
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        comps->items[index] = css_font_family_comp(pool, comps->items[index]);
    }
}

static int css_is_font_size_kw(css_buf *pool, const css_comp *comp) {
    static const char *const sizes[] = {"xx-small", "x-small",  "small",   "medium", "large",
                                        "x-large",  "xx-large", "smaller", "larger"};
    if (comp->kind != CK_IDENT) {
        return 0;
    }
    for (size_t index = 0; index < sizeof(sizes) / sizeof(sizes[0]); index++) {
        if (comp_ieq(pool, comp, sizes[index])) {
            return 1;
        }
    }
    return 0;
}

static int css_comp_is_slash(css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_SEP) {
        return 0;
    }
    return pool->data[comp->off] == '/'; /* a SEP comp is always len 1 */
}

static int css_comp_is_comma(css_buf *pool, const css_comp *comp) {
    if (comp->kind != CK_SEP) {
        return 0;
    }
    return pool->data[comp->off] == ','; /* a SEP comp is always len 1 */
}

/* Minify the font shorthand: lower-case the family, normalize font-weight (normal->drop, bold->700, 400->drop) and
   drop a normal line-height, porting tdewolff's minifyFont. */
static void css_handle_font(css_buf *pool, comp_vec *comps) {
    Py_ssize_t non_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind != CK_SEP) {
            non_sep++;
        }
    }
    if (non_sep <= 1) {
        return;
    }
    comp_vec values = {NULL, 0, 0, 0};
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        comp_vec_push(&values, comps->items[index]);
    }
    Py_ssize_t family = values.len - 1;
    for (Py_ssize_t index = 2; index < values.len; index++) {
        if (css_comp_is_comma(pool, &values.items[index])) {
            family = index - 1;
            break;
        }
    }
    family--;
    while (family > 0) {
        if (css_comp_is_slash(pool, &values.items[family - 1])) {
            break;
        }
        if (values.items[family].kind != CK_IDENT && values.items[family].kind != CK_STR) {
            break;
        }
        if (css_is_font_size_kw(pool, &values.items[family]) || css_comp_kw(pool, &values.items[family], "inherit") ||
            css_comp_kw(pool, &values.items[family], "initial") || css_comp_kw(pool, &values.items[family], "unset")) {
            break;
        }
        family--;
    }
    for (Py_ssize_t index = family + 1; index < values.len; index++) {
        values.items[index] = css_font_family_comp(pool, values.items[index]);
    }
    /* family was set to at most values.len-1 then decremented at least once above, so family+1 < values.len holds */
    if (pool->data[values.items[family + 1].off] == '-') {
        css_buf quoted = {NULL, 0, 0, 0};
        cbuf_putc(&quoted, '\'');
        cbuf_put_run(&quoted, pool->data + values.items[family + 1].off, values.items[family + 1].len);
        cbuf_putc(&quoted, '\'');
        values.items[family + 1].off = pool_run(pool, quoted.data, quoted.len);
        values.items[family + 1].len = quoted.len;
        values.items[family + 1].kind = CK_STR;
        cbuf_free(&quoted);
    }
    if (family > 0) {
        Py_ssize_t index = family;
        if (family > 1 && css_comp_is_slash(pool, &values.items[family - 1])) {
            if (css_comp_kw(pool, &values.items[family], "normal")) {
                comp_vec_splice(&values, family - 1, 2, NULL, 0);
            }
            index -= 2;
        }
        index--;
        while (index > -1) {
            if (css_comp_kw(pool, &values.items[index], "normal") ||
                (values.items[index].kind == CK_NUM && comp_ieq(pool, &values.items[index], "400"))) {
                comp_vec_splice(&values, index, 1, NULL, 0);
            } else if (css_comp_kw(pool, &values.items[index], "bold")) {
                css_set_comp(pool, &values.items[index], "700", CK_NUM);
            }
            index--;
        }
    }
    css_free(comps->items);
    *comps = values;
}

/* Dispatch the per-property shorthand handler over the rendered components. */
static void css_apply_handler(css_buf *pool, const css_char *prop, Py_ssize_t prop_len, comp_vec *comps) {
    int has_sep = 0;
    for (Py_ssize_t index = 0; index < comps->len; index++) {
        if (comps->items[index].kind == CK_SEP) {
            has_sep = 1;
        }
    }
    if (!has_sep && css_prop_in(prop, prop_len, CSS_BOX_PROPS, sizeof(CSS_BOX_PROPS) / sizeof(char *))) {
        css_handle_box(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background")) {
        css_handle_background(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background-position")) {
        css_handle_background_position(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "background-repeat")) {
        css_handle_runs(pool, comps, css_collapse_repeat_run);
    } else if (css_run_ieq(prop, prop_len, "background-size")) {
        css_handle_runs(pool, comps, css_collapse_size_run);
    } else if (css_run_ieq(prop, prop_len, "border") || css_run_ieq(prop, prop_len, "border-top") ||
               css_run_ieq(prop, prop_len, "border-bottom") || css_run_ieq(prop, prop_len, "border-left") ||
               css_run_ieq(prop, prop_len, "border-right") || css_run_ieq(prop, prop_len, "outline") ||
               css_run_ieq(prop, prop_len, "column-rule") || css_run_ieq(prop, prop_len, "text-decoration") ||
               css_run_ieq(prop, prop_len, "text-emphasis")) {
        css_handle_color_drop(pool, comps, prop, prop_len);
    } else if (css_run_ieq(prop, prop_len, "border-color")) {
        css_handle_border_color(pool, comps);
    } else if (css_prop_in(prop, prop_len, CSS_CURRENTCOLOR_INIT, sizeof(CSS_CURRENTCOLOR_INIT) / sizeof(char *)) ||
               css_prop_in(prop, prop_len, CSS_SINGLE_COLOR, sizeof(CSS_SINGLE_COLOR) / sizeof(char *))) {
        css_handle_color_single(pool, comps, prop, prop_len);
    } else if (css_run_ieq(prop, prop_len, "font-weight")) {
        css_handle_font_weight(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-basis")) {
        css_handle_flex_basis(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-grow") || css_run_ieq(prop, prop_len, "order")) {
        css_handle_flex_grow(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "flex-shrink")) {
        css_handle_flex_shrink(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "box-shadow")) {
        css_handle_runs(pool, comps, css_collapse_box_shadow_run);
    } else if (css_run_ieq(prop, prop_len, "text-shadow")) {
        css_handle_text_shadow(pool, comps);
    } else if (!has_sep && css_run_ieq(prop, prop_len, "flex")) {
        css_handle_flex(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "font")) {
        css_handle_font(pool, comps);
    } else if (css_run_ieq(prop, prop_len, "font-family")) {
        css_handle_font_family(pool, comps);
    }
}

/* Minify a declaration value's tokens [start, end) into out. raw covers custom properties (--*). */
/* scratch is a reusable component vector owned by the caller: resetting its length and reusing its backing buffer
   across declarations avoids a malloc/free for every value. */
static void css_minify_value(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, const css_char *prop,
                             Py_ssize_t prop_len, int raw, comp_vec *scratch, css_buf *out) {
    if (raw) {
        css_render_raw_value(vec, start, end, out);
        return;
    }
    /* prop points into the pool, which the component rendering below grows (and so reallocates); copy the name to a
       stable buffer first. No real property name approaches this length, so a longer one is truncated and simply
       matches no handler. */
    css_char name[128];
    Py_ssize_t name_len = prop_len < 128 ? prop_len : 128;
    memcpy(name, prop, (size_t)name_len * sizeof(css_char));
    if (css_run_ieq(name, name_len, "filter") || css_run_ieq(name, name_len, "-ms-filter")) {
        css_handle_filter(pool, vec, start, end, name, name_len, out);
        return;
    }
    if (css_run_ieq(name, name_len, "unicode-range") && css_handle_unicode_range(vec, start, end, out)) {
        return;
    }
    scratch->len = 0;
    css_render_components(pool, vec, start, end, css_prop_is_color(name, name_len), scratch);
    css_apply_handler(pool, name, name_len, scratch);
    css_assemble(pool, scratch, out);
}

/* Minify a selector token run [start, end) into out. */
static void css_minify_selector(token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    int pending_ws = 0;
    int attr_depth = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '[') {
            attr_depth++;
        }
        if (token->kind == CSS_DELIM && token->delim == ']' && attr_depth > 0) {
            attr_depth--;
        }
        if (token->kind == CSS_DELIM && attr_depth == 0 &&
            (token->delim == '>' || token->delim == '+' || token->delim == '~' || token->delim == ',')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && attr_depth > 0 &&
            (token->delim == '=' || token->delim == '~' || token->delim == '^' || token->delim == '$' ||
             token->delim == '*' || token->delim == '|')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        /* the caller skips leading whitespace, so pending_ws is only set after a token has been emitted: out is
           non-empty whenever a deferred descendant space is pending */
        if (pending_ws) {
            css_char last = out->data[out->len - 1];
            int blocked = last == '>' || last == '+' || last == '~' || last == ',' || last == '[' || last == '(';
            if (attr_depth > 0 && (last == '[' || last == '=' || last == '~' || last == '^' || last == '$' ||
                                   last == '*' || last == '|')) {
                blocked = 1;
            }
            if (!blocked) {
                cbuf_putc(out, ' ');
            }
        }
        pending_ws = 0;
        /* '#' before an ident always merges into a single HASH token, so the prior char is never a bare '#' here */
        int after_combinator = out->len > 0 && (out->data[out->len - 1] == '.' || out->data[out->len - 1] == ':');
        int is_custom = token->text_len >= 2 && token->text[0] == '-' && token->text[1] == '-';
        if (token->kind == CSS_IDENT && attr_depth == 0 && !after_combinator && !is_custom) {
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(out, css_lower(token->text[pos]));
            }
        } else if (token->kind == CSS_STR && attr_depth > 0) {
            /* an attribute value string drops its quotes when the inner text is a bare identifier word */
            const css_char *inner = token->text + 1;
            Py_ssize_t inner_len = token->text_len - 2;
            int bare = inner_len > 0;
            for (Py_ssize_t pos = 0; bare && pos < inner_len; pos++) {
                css_char character = inner[pos];
                if (!((character >= 'a' && character <= 'z') || (character >= 'A' && character <= 'Z') ||
                      (character >= '0' && character <= '9') || character == '_' || character == '-')) {
                    bare = 0;
                }
            }
            if (bare) {
                cbuf_put_run(out, inner, inner_len);
            } else {
                cbuf_put_run(out, token->text, token->text_len);
            }
        } else {
            if (token->kind == CSS_NUM) {
                cbuf_put_run(out, token->text, token->text_len);
                cbuf_put_run(out, (token->text + token->text_len), token->unit_len);
            } else {
                cbuf_put_run(out, token->text, token->text_len);
            }
        }
    }
    css_rtrim(out);
}

typedef struct {
    Py_ssize_t prop_off;
    Py_ssize_t prop_len;
    Py_ssize_t val_off;
    Py_ssize_t val_len;
    int important;
    int nested; /* a nested rule or at-rule kept verbatim (prop holds the whole text, val unused) */
    int dropped;
} css_decl;

typedef struct {
    css_decl *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} decl_vec;

static void decl_vec_push(decl_vec *vec, css_decl decl) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 16;
        css_decl *grown = css_realloc(vec->items, (size_t)cap * sizeof(css_decl));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        vec->items = grown;
        vec->cap = cap;
    }
    vec->items[vec->len++] = decl;
}

/* Build a declaration from a segment [start, end). Returns 1 if a declaration was produced. */
static int css_make_declaration(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, comp_vec *scratch,
                                css_decl *decl) {
    Py_ssize_t colon = -1;
    for (Py_ssize_t index = start; index < end; index++) {
        if (vec->items[index].kind == CSS_DELIM && vec->items[index].delim == ':') {
            colon = index;
            break;
        }
    }
    if (colon < 0) {
        return 0;
    }
    /* the property name is the text before the colon, with surrounding whitespace trimmed */
    /* the caller (css_parse_declarations) skips leading whitespace/comments, so start is the first property token */
    Py_ssize_t prop_start = start;
    Py_ssize_t prop_end = colon;
    while (prop_end > prop_start && vec->items[prop_end - 1].kind == CSS_WS) {
        prop_end--;
    }
    if (prop_start >= prop_end) {
        return 0;
    }
    /* the raw property text spans from the first to the last non-ws token; build it for the --* check and interning */
    int is_custom = vec->items[prop_start].kind == CSS_IDENT && vec->items[prop_start].text_len >= 2 &&
                    vec->items[prop_start].text[0] == '-' && vec->items[prop_start].text[1] == '-';
    Py_ssize_t prop_off = pool->len;
    for (Py_ssize_t index = prop_start; index < prop_end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_NUM) {
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(pool, css_lower(token->text[pos]));
            }
            for (Py_ssize_t pos = 0; pos < token->unit_len; pos++) {
                cbuf_putc(pool, css_lower((token->text + token->text_len)[pos]));
            }
        } else if (token->kind != CSS_WS) {
            for (Py_ssize_t pos = 0; pos < token->text_len; pos++) {
                cbuf_putc(pool, css_lower(token->text[pos]));
            }
        }
    }
    Py_ssize_t prop_len = pool->len - prop_off;

    /* scan the value for a trailing !important */
    Py_ssize_t value_start = colon + 1;
    Py_ssize_t value_end = end;
    int important = 0;
    for (Py_ssize_t index = value_start; index < value_end; index++) {
        if (vec->items[index].kind == CSS_DELIM && vec->items[index].delim == '!') {
            Py_ssize_t rest = index + 1;
            while (rest < value_end && vec->items[rest].kind == CSS_WS) {
                rest++;
            }
            if (rest < value_end && vec->items[rest].kind == CSS_IDENT &&
                css_run_ieq(vec->items[rest].text, vec->items[rest].text_len, "important")) {
                important = 1;
                value_end = index;
            }
            break;
        }
    }

    css_buf value = {NULL, 0, 0, 0};
    css_minify_value(pool, vec, value_start, value_end, pool->data + prop_off, prop_len, is_custom, scratch, &value);
    Py_ssize_t val_off = pool_run(pool, value.data, value.len);
    Py_ssize_t val_len = value.len;
    cbuf_free(&value);

    decl->prop_off = prop_off;
    decl->prop_len = prop_len;
    decl->val_off = val_off;
    decl->val_len = val_len;
    decl->important = important;
    decl->nested = 0;
    decl->dropped = 0;
    return 1;
}

/* The space-separated set of properties a shorthand fully overrides (including itself), or NULL when prop is not a
   shorthand. Resolving this once per declaration -- instead of rescanning the table for every pair -- keeps dedup off
   the O(n^2 * table) path that a rule with hundreds of custom properties would otherwise hit. */
static const char *css_longhand_list(const css_char *prop, Py_ssize_t prop_len) {
    static const char *const shorthands[] = {
        "background",  "font",         "border",        "border-width",  "border-style", "border-color",
        "border-top",  "border-right", "border-bottom", "border-left",   "margin",       "padding",
        "column-rule", "animation",    "columns",       "flex",          "flex-flow",    "grid",
        "grid-area",   "grid-row",     "grid-column",   "grid-template", "list-style",   "offset",
        "outline",     "overflow",     "place-content", "place-items",   "place-self",   "text-decoration",
        "transition"};
    // NOLINTBEGIN(bugprone-suspicious-missing-comma): each entry is one shorthand's longhand list as a single
    // space-separated string; clang-format wraps the long ones across lines, which is string concatenation, not a
    // missing comma between array elements
    static const char *const longhands[] = {
        "background background-image background-position background-size background-repeat background-origin "
        "background-clip background-attachment background-color",
        "font font-style font-variant font-weight font-stretch font-size font-family line-height",
        "border border-width border-top-width border-right-width border-bottom-width border-left-width border-style "
        "border-top-style border-right-style border-bottom-style border-left-style border-color border-top-color "
        "border-right-color border-bottom-color border-left-color",
        "border-width border-top-width border-right-width border-bottom-width border-left-width",
        "border-style border-top-style border-right-style border-bottom-style border-left-style",
        "border-color border-top-color border-right-color border-bottom-color border-left-color",
        "border-top border-top-width border-top-style border-top-color",
        "border-right border-right-width border-right-style border-right-color",
        "border-bottom border-bottom-width border-bottom-style border-bottom-color",
        "border-left border-left-width border-left-style border-left-color",
        "margin margin-top margin-right margin-bottom margin-left",
        "padding padding-top padding-right padding-bottom padding-left",
        "column-rule column-rule-width column-rule-style column-rule-color",
        "animation animation-name animation-duration animation-timing-function animation-delay "
        "animation-iteration-count animation-direction animation-fill-mode animation-play-state",
        "columns column-width column-count",
        "flex flex-basis flex-grow flex-shrink",
        "flex-flow flex-direction flex-wrap",
        "grid grid-template-rows grid-template-columns grid-template-areas grid-auto-rows grid-auto-columns "
        "grid-auto-flow grid-column-gap grid-row-gap column-gap row-gap",
        "grid-area grid-row-start grid-column-start grid-row-end grid-column-end",
        "grid-row grid-row-start grid-row-end",
        "grid-column grid-column-start grid-column-end",
        "grid-template grid-template-rows grid-template-columns grid-template-areas",
        "list-style list-style-image list-style-position list-style-type",
        "offset offset-position offset-path offset-distance offset-anchor offset-rotate",
        "outline outline-width outline-style outline-color",
        "overflow overflow-x overflow-y",
        "place-content align-content justify-content",
        "place-items align-items justify-items",
        "place-self align-self justify-self",
        "text-decoration text-decoration-color text-decoration-line text-decoration-thickness",
        "transition transition-property transition-duration transition-timing-function transition-delay"};
    // NOLINTEND(bugprone-suspicious-missing-comma)
    /* a custom property and any name without a hyphen-or-known-shorthand shape is never a shorthand */
    if (prop_len < 4) {
        return NULL;
    }
    for (size_t index = 0; index < sizeof(shorthands) / sizeof(shorthands[0]); index++) {
        if (css_run_ieq(prop, prop_len, shorthands[index])) {
            return longhands[index];
        }
    }
    return NULL;
}

static int css_prop_in_list(const css_char *prop, Py_ssize_t prop_len, const char *list) {
    while (*list) {
        const char *word = list;
        while (*list && *list != ' ') {
            list++;
        }
        if ((Py_ssize_t)(list - word) == prop_len) {
            int same = 1;
            for (Py_ssize_t pos = 0; pos < prop_len; pos++) {
                if (prop[pos] != (css_char)(unsigned char)word[pos]) {
                    same = 0;
                    break;
                }
            }
            if (same) {
                return 1;
            }
        }
        while (*list == ' ') {
            list++;
        }
    }
    return 0;
}

/* The pairwise dedup, fine for the typical small rule. */
static void css_dedup_pairwise(css_buf *pool, decl_vec *decls) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        if (later->nested) {
            continue;
        }
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        const char *list = css_longhand_list(prop, prop_len);
        for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
            css_decl *prev = &decls->items[earlier];
            if (prev->nested || prev->dropped || prev->important != later->important) {
                continue;
            }
            const css_char *prior = pool->data + prev->prop_off;
            int overrides = prev->prop_len == prop_len;
            for (Py_ssize_t pos = 0; overrides && pos < prop_len; pos++) {
                overrides = prop[pos] == prior[pos];
            }
            if (!overrides && list != NULL) {
                overrides = css_prop_in_list(prior, prev->prop_len, list);
            }
            if (overrides) {
                prev->dropped = 1;
            }
        }
    }
}

/* A property -> most-recent-active-declaration entry, keyed by (lowercased name, importance). */
typedef struct {
    Py_ssize_t prop_off;
    Py_ssize_t prop_len;
    int important;
    Py_ssize_t index;
    int used;
} dedup_entry;

static uint32_t css_hash_run(const css_char *text, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(text[pos] & 0xFF)) * 16777619u;
    }
    return hash;
}

static uint32_t css_hash_cstr(const char *text, Py_ssize_t len) {
    uint32_t hash = 2166136261u;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        hash = (hash ^ (uint32_t)(unsigned char)text[pos]) * 16777619u;
    }
    return hash;
}

/* Find the slot for a code-point key (a property name from the pool): the matching entry, or the empty slot to fill. */
static Py_ssize_t dedup_slot_run(dedup_entry *table, uint32_t mask, const css_buf *pool, const css_char *key,
                                 Py_ssize_t key_len, int important) {
    Py_ssize_t slot = css_hash_run(key, key_len) & mask;
    while (table[slot].used) {
        if (table[slot].important == important && table[slot].prop_len == key_len &&
            memcmp(pool->data + table[slot].prop_off, key, (size_t)key_len * sizeof(css_char)) == 0) {
            break;
        }
        slot = (slot + 1) & mask;
    }
    return slot;
}

/* Find the slot for an ASCII key (a longhand name from the shorthand table). */
static Py_ssize_t dedup_slot_cstr(dedup_entry *table, uint32_t mask, const css_buf *pool, const char *key,
                                  Py_ssize_t key_len, int important) {
    Py_ssize_t slot = css_hash_cstr(key, key_len) & mask;
    while (table[slot].used) {
        if (table[slot].important == important && table[slot].prop_len == key_len) {
            int same = 1;
            for (Py_ssize_t pos = 0; pos < key_len; pos++) {
                if (pool->data[table[slot].prop_off + pos] != (css_char)(unsigned char)key[pos]) {
                    same = 0;
                    break;
                }
            }
            if (same) {
                break;
            }
        }
        slot = (slot + 1) & mask;
    }
    return slot;
}

/* Drop earlier declarations a later one fully overrides. Pairwise for a small rule; for a large one (a framework
   :root with hundreds of custom properties) a hash of property -> most-recent declaration makes it linear, since each
   later declaration overrides only its own name and -- when it is a shorthand -- the few names in its longhand list. */
#define CSS_DEDUP_STACK 512
static void css_dedup(css_buf *pool, decl_vec *decls) {
    if (decls->len <= 32) {
        css_dedup_pairwise(pool, decls);
        return;
    }
    Py_ssize_t cap = 64;
    while (cap < decls->len * 2) {
        cap *= 2;
    }
    /* keep the hash table on the stack for the common rule (up to 256 declarations); only a framework-sized :root
       spills to the heap */
    dedup_entry stack_table[CSS_DEDUP_STACK];
    dedup_entry *table = stack_table;
    if (cap > CSS_DEDUP_STACK) {
        table = css_malloc((size_t)cap * sizeof(dedup_entry));
        if (table == NULL) {                 /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            css_dedup_pairwise(pool, decls); /* GCOVR_EXCL_LINE */
            return;                          /* GCOVR_EXCL_LINE */
        }
    }
    memset(table, 0, (size_t)cap * sizeof(dedup_entry));
    uint32_t mask = (uint32_t)(cap - 1);
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *later = &decls->items[index];
        if (later->nested) {
            continue;
        }
        const css_char *prop = pool->data + later->prop_off;
        Py_ssize_t prop_len = later->prop_len;
        int important = later->important;
        const char *list = css_longhand_list(prop, prop_len);
        if (list == NULL) {
            Py_ssize_t slot = dedup_slot_run(table, mask, pool, prop, prop_len, important);
            if (table[slot].used && !decls->items[table[slot].index].dropped) {
                decls->items[table[slot].index].dropped = 1;
            }
        } else {
            const char *cursor = list;
            while (*cursor) {
                const char *word = cursor;
                while (*cursor && *cursor != ' ') {
                    cursor++;
                }
                Py_ssize_t slot = dedup_slot_cstr(table, mask, pool, word, cursor - word, important);
                if (table[slot].used && !decls->items[table[slot].index].dropped) {
                    decls->items[table[slot].index].dropped = 1;
                }
                while (*cursor == ' ') {
                    cursor++;
                }
            }
        }
        Py_ssize_t home = dedup_slot_run(table, mask, pool, prop, prop_len, important);
        table[home].prop_off = later->prop_off;
        table[home].prop_len = prop_len;
        table[home].important = important;
        table[home].index = index;
        table[home].used = 1;
    }
    if (table != stack_table) {
        css_free(table);
    }
}

static int css_decl_value_eq(const css_buf *pool, const css_decl *left, const css_decl *right) {
    return left->val_len == right->val_len && memcmp(pool->data + left->val_off, pool->data + right->val_off,
                                                     (size_t)left->val_len * sizeof(css_char)) == 0;
}

static int css_decl_is_wide_keyword(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    Py_ssize_t len = decl->val_len;
    return css_run_ieq(value, len, "inherit") || css_run_ieq(value, len, "initial") ||
           css_run_ieq(value, len, "unset") || css_run_ieq(value, len, "revert") ||
           css_run_ieq(value, len, "revert-layer");
}

/* A value carrying a custom-property substitution cannot be hoisted into a shorthand: var()/env() expand at the
   shorthand level, where a multi-token or guaranteed-invalid expansion would change which sub-properties are set. */
static int css_decl_has_substitution(const css_buf *pool, const css_decl *decl) {
    const css_char *value = pool->data + decl->val_off;
    for (Py_ssize_t index = 0; index + 4 <= decl->val_len; index++) {
        if ((css_lower(value[index]) == 'v' && css_lower(value[index + 1]) == 'a' &&
             css_lower(value[index + 2]) == 'r' && value[index + 3] == '(') ||
            (css_lower(value[index]) == 'e' && css_lower(value[index + 1]) == 'n' &&
             css_lower(value[index + 2]) == 'v' && value[index + 3] == '(')) {
            return 1;
        }
    }
    return 0;
}

static Py_ssize_t css_find_active_decl(const decl_vec *decls, const css_buf *pool, const char *name) {
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (!decl->dropped && !decl->nested && css_run_ieq(pool->data + decl->prop_off, decl->prop_len, name)) {
            return index;
        }
    }
    return -1;
}

static int css_count_active_prefixed(const decl_vec *decls, const css_buf *pool, const char *prefix, Py_ssize_t len) {
    int count = 0;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        const css_decl *decl = &decls->items[index];
        if (decl->dropped || decl->nested || decl->prop_len < len) {
            continue;
        }
        int match = 1;
        for (Py_ssize_t pos = 0; pos < len; pos++) {
            if (css_lower(pool->data[decl->prop_off + pos]) != (css_char)(unsigned char)prefix[pos]) {
                match = 0;
                break;
            }
        }
        count += match;
    }
    return count;
}

/* Merge the four box longhands (top, right, bottom, left order) into their shorthand when it is value-safe: all four
   present and the only sub-properties of that prefix in the rule (so no logical margin-inline/-block to reorder),
   same importance, no var()/env() substitution, and -- if any is a CSS-wide keyword -- all four the identical
   keyword. The shorthand replaces the last longhand and a re-dedup removes any now-redundant earlier declaration. */
static void css_merge_box(css_buf *pool, decl_vec *decls, const char *shorthand, const char *const longhands[4],
                          const char *prefix) {
    Py_ssize_t idx[4];
    for (int edge = 0; edge < 4; edge++) {
        idx[edge] = css_find_active_decl(decls, pool, longhands[edge]);
        if (idx[edge] < 0) {
            return;
        }
    }
    if (css_count_active_prefixed(decls, pool, prefix, (Py_ssize_t)strlen(prefix)) != 4) {
        return;
    }
    int important = decls->items[idx[0]].important;
    for (int edge = 0; edge < 4; edge++) {
        if (decls->items[idx[edge]].important != important ||
            css_decl_has_substitution(pool, &decls->items[idx[edge]])) {
            return;
        }
    }
    int any_wide = 0;
    for (int edge = 0; edge < 4; edge++) {
        any_wide |= css_decl_is_wide_keyword(pool, &decls->items[idx[edge]]);
    }
    css_buf value = {NULL, 0, 0, 0};
    if (any_wide) {
        for (int edge = 0; edge < 4; edge++) {
            if (!css_decl_is_wide_keyword(pool, &decls->items[idx[edge]]) ||
                !css_decl_value_eq(pool, &decls->items[idx[edge]], &decls->items[idx[0]])) {
                return; /* a CSS-wide keyword cannot mix with other values in a shorthand */
            }
        }
        cbuf_put_run(&value, pool->data + decls->items[idx[0]].val_off, decls->items[idx[0]].val_len);
    } else {
        int kept = 4;
        if (css_decl_value_eq(pool, &decls->items[idx[3]], &decls->items[idx[1]])) {
            kept = 3;
        }
        if (kept == 3 && css_decl_value_eq(pool, &decls->items[idx[2]], &decls->items[idx[0]])) {
            kept = 2;
        }
        if (kept == 2 && css_decl_value_eq(pool, &decls->items[idx[1]], &decls->items[idx[0]])) {
            kept = 1;
        }
        for (int edge = 0; edge < kept; edge++) {
            if (edge > 0) {
                cbuf_putc(&value, ' ');
            }
            cbuf_put_run(&value, pool->data + decls->items[idx[edge]].val_off, decls->items[idx[edge]].val_len);
        }
    }
    Py_ssize_t last = idx[0];
    for (int edge = 1; edge < 4; edge++) {
        if (idx[edge] > last) {
            last = idx[edge];
        }
    }
    css_decl *merged = &decls->items[last];
    merged->prop_off = pool_cstr(pool, shorthand);
    merged->prop_len = (Py_ssize_t)strlen(shorthand);
    merged->val_off = pool_run(pool, value.data, value.len);
    merged->val_len = value.len;
    merged->important = important;
    for (int edge = 0; edge < 4; edge++) {
        if (idx[edge] != last) {
            decls->items[idx[edge]].dropped = 1;
        }
    }
    cbuf_free(&value);
    css_dedup(pool, decls);
}

static void css_merge_shorthands(css_buf *pool, decl_vec *decls) {
    static const char *const margin[4] = {"margin-top", "margin-right", "margin-bottom", "margin-left"};
    static const char *const padding[4] = {"padding-top", "padding-right", "padding-bottom", "padding-left"};
    css_merge_box(pool, decls, "margin", margin, "margin-");
    css_merge_box(pool, decls, "padding", padding, "padding-");
}

static void css_render_declarations(css_buf *pool, decl_vec *decls, css_buf *out) {
    css_dedup(pool, decls);
    css_merge_shorthands(pool, decls);
    int first = 1;
    for (Py_ssize_t index = 0; index < decls->len; index++) {
        css_decl *decl = &decls->items[index];
        if (decl->dropped) {
            continue;
        }
        if (!first) {
            cbuf_putc(out, ';');
        }
        first = 0;
        if (decl->nested) {
            cbuf_put_run(out, pool->data + decl->prop_off, decl->prop_len);
            continue;
        }
        cbuf_put_run(out, pool->data + decl->prop_off, decl->prop_len);
        cbuf_putc(out, ':');
        cbuf_put_run(out, pool->data + decl->val_off, decl->val_len);
        if (decl->important) {
            cbuf_puts(out, "!important");
        }
    }
}

static int css_is_nested_rule_at(const css_char *name, Py_ssize_t len) {
    return css_run_ieq(name, len, "@media") || css_run_ieq(name, len, "@supports") ||
           css_run_ieq(name, len, "@document") || css_run_ieq(name, len, "@-moz-document") ||
           css_run_ieq(name, len, "@container") || css_run_ieq(name, len, "@layer") || css_run_ieq(name, len, "@scope");
}

static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls);
static void css_parse_rules(css_buf *pool, cursor *cur, int top, css_buf *out);

/* Render an at-rule prelude into out (a leading space unless it opens with '('). */
static void css_at_prelude(css_buf *pool, token_vec *vec, Py_ssize_t start, Py_ssize_t end, css_buf *out) {
    Py_ssize_t mark = out->len;
    int pending_ws = 0;
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *token = &vec->items[index];
        if (token->kind == CSS_WS) {
            pending_ws = 1;
            continue;
        }
        if (token->kind == CSS_COMMENT) {
            continue;
        }
        if (token->kind == CSS_URL) {
            Py_ssize_t off;
            Py_ssize_t len;
            /* an at-rule url() keeps a quoted body, or wraps a bare body in quotes */
            if (token->text_len > 4 && token->text[token->text_len - 1] == ')') {
                Py_ssize_t body_start = 4;
                Py_ssize_t body_end = token->text_len - 1;
                while (body_start < body_end && css_is_ws(token->text[body_start])) {
                    body_start++;
                }
                while (body_end > body_start && css_is_ws(token->text[body_end - 1])) {
                    body_end--;
                }
                if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',') {
                    cbuf_putc(out, ' ');
                }
                pending_ws = 0;
                if (body_end > body_start && (token->text[body_start] == '"' || token->text[body_start] == '\'')) {
                    cbuf_put_run(out, token->text + body_start, body_end - body_start);
                } else {
                    cbuf_putc(out, '"');
                    cbuf_put_run(out, token->text + body_start, body_end - body_start);
                    cbuf_putc(out, '"');
                }
                continue;
            }
            if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',') {
                cbuf_putc(out, ' ');
            }
            pending_ws = 0;
            cbuf_put_run(out, token->text, token->text_len);
            (void)off;
            (void)len;
            continue;
        }
        if (token->kind == CSS_DELIM && (token->delim == ':' || token->delim == ',')) {
            css_rtrim(out);
            cbuf_putc(out, token->delim);
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '(') {
            if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',') {
                cbuf_putc(out, ' ');
            }
            cbuf_putc(out, '(');
            pending_ws = 0;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == ')') {
            css_rtrim(out);
            cbuf_putc(out, ')');
            pending_ws = 0;
            continue;
        }
        if (pending_ws && out->len > mark && out->data[out->len - 1] != '(' && out->data[out->len - 1] != ',' &&
            out->data[out->len - 1] != ':') {
            cbuf_putc(out, ' ');
        }
        pending_ws = 0;
        if (token->kind == CSS_NUM) {
            Py_ssize_t off;
            Py_ssize_t len;
            css_format_dimension(pool, token, 1, &off, &len);
            cbuf_put_run(out, pool->data + off, len);
        } else {
            cbuf_put_run(out, token->text, token->text_len);
        }
    }
    css_rtrim(out);
    /* a non-empty prelude not opening with '(' is preceded by a single space */
    if (out->len > mark && out->data[mark] != '(') {
        cbuf_reserve(out, 1);
        memmove(out->data + mark + 1, out->data + mark, (size_t)(out->len - mark) * sizeof(css_char));
        out->data[mark] = ' ';
        out->len++;
    }
}

static void css_parse_at(css_buf *pool, cursor *cur, css_buf *out) {
    css_token *name_token = &cur->vec->items[cur->index];
    const css_char *name = name_token->text;
    Py_ssize_t name_len = name_token->text_len;
    cur->index++;
    Py_ssize_t prelude_start = cur->index;
    Py_ssize_t prelude_end = css_read_until(cur, ";{}");
    css_token *token = cursor_peek(cur);
    /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
    if (token && token->delim == '{') {
        cur->index++;
        for (Py_ssize_t pos = 0; pos < name_len; pos++) {
            cbuf_putc(out, css_lower(name[pos]));
        }
        css_at_prelude(pool, cur->vec, prelude_start, prelude_end, out);
        cbuf_putc(out, '{');
        if (css_is_nested_rule_at(name, name_len)) {
            css_parse_rules(pool, cur, 0, out);
        } else {
            decl_vec decls = {NULL, 0, 0, 0};
            css_parse_declarations(pool, cur, &decls);
            css_render_declarations(pool, &decls, out);
            css_free(decls.items);
        }
        cbuf_putc(out, '}');
        return;
    }
    if (token && token->delim == ';') {
        cur->index++;
    }
    for (Py_ssize_t pos = 0; pos < name_len; pos++) {
        cbuf_putc(out, css_lower(name[pos]));
    }
    css_at_prelude(pool, cur->vec, prelude_start, prelude_end, out);
}

/* Parse a declaration list (between { }); appends declarations (and nested rules) to decls. */
static void css_parse_declarations(css_buf *pool, cursor *cur, decl_vec *decls) {
    comp_vec scratch = {NULL, 0, 0, 0}; /* reused across this list's values, freed once below */
    while (cur->index < cur->vec->len) {
        css_token *token = cursor_peek(cur);
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            cur->index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '}') {
            cur->index++;
            break;
        }
        if (token->kind == CSS_AT) {
            css_buf nested = {NULL, 0, 0, 0};
            css_parse_at(pool, cur, &nested);
            css_decl decl = {0};
            decl.prop_off = pool_run(pool, nested.data, nested.len);
            decl.prop_len = nested.len;
            decl.nested = 1;
            cbuf_free(&nested);
            decl_vec_push(decls, decl);
            continue;
        }
        Py_ssize_t segment_start = cur->index;
        Py_ssize_t segment_end = css_read_until(cur, ";{}");
        css_token *terminator = cursor_peek(cur);
        /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
        if (terminator && terminator->delim == '{') {
            cur->index++;
            decl_vec inner = {NULL, 0, 0, 0};
            css_parse_declarations(pool, cur, &inner);
            css_buf selector = {NULL, 0, 0, 0};
            css_minify_selector(cur->vec, segment_start, segment_end, &selector);
            css_buf body = {NULL, 0, 0, 0};
            css_render_declarations(pool, &inner, &body);
            css_decl decl = {0};
            decl.prop_off = pool->len;
            cbuf_put_run(pool, selector.data, selector.len);
            cbuf_putc(pool, '{');
            cbuf_put_run(pool, body.data, body.len);
            cbuf_putc(pool, '}');
            decl.prop_len = pool->len - decl.prop_off;
            decl.nested = 1;
            css_free(inner.items);
            cbuf_free(&selector);
            cbuf_free(&body);
            decl_vec_push(decls, decl);
            continue;
        }
        int closed_brace = terminator && terminator->delim == '}';
        /* the '{' terminator already returned above, so a non-NULL terminator here is always ';' or '}' */
        if (terminator) {
            cur->index++;
        }
        css_decl decl = {0};
        if (css_make_declaration(pool, cur->vec, segment_start, segment_end, &scratch, &decl)) {
            decl_vec_push(decls, decl);
        }
        if (closed_brace) {
            break;
        }
    }
    css_free(scratch.items);
}

/* A top-level node collected before serialization, so adjacent qualified rules can be merged. is_rule holds a
   selector + rendered body (no braces); otherwise it is opaque text (an at-rule, a bang comment, or stray recovery
   text), which breaks rule adjacency. at_statement marks an at-statement that needs a ';' before the next node. */
typedef struct {
    int is_rule;
    Py_ssize_t sel_off;
    Py_ssize_t sel_len;
    Py_ssize_t body_off;
    Py_ssize_t body_len;
    Py_ssize_t text_off;
    Py_ssize_t text_len;
    int at_statement;
    int dropped;
} rule_item;

typedef struct {
    rule_item *items;
    Py_ssize_t len;
    Py_ssize_t cap;
    int failed;
} rule_vec;

static void rule_vec_push(rule_vec *vec, rule_item item) {
    if (vec->len == vec->cap) {
        Py_ssize_t cap = vec->cap ? vec->cap * 2 : 16;
        rule_item *grown = css_realloc(vec->items, (size_t)cap * sizeof(rule_item));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            vec->failed = 1; /* GCOVR_EXCL_LINE */
            return;          /* GCOVR_EXCL_LINE */
        }
        vec->items = grown;
        vec->cap = cap;
    }
    vec->items[vec->len++] = item;
}

/* Parse a qualified rule into item (selector + rendered body), or recover a stray segment as opaque text. */
static void css_parse_qualified(css_buf *pool, cursor *cur, rule_item *item) {
    item->is_rule = 0;
    item->dropped = 0;
    item->at_statement = 0;
    item->text_off = 0;
    item->text_len = 0;
    Py_ssize_t prelude_start = cur->index;
    Py_ssize_t prelude_end = css_read_until(cur, "{};");
    css_token *token = cursor_peek(cur);
    /* css_read_until stops only on a stop DELIM (or EOF -> NULL peek), so a non-NULL peek is always that DELIM */
    if (token && token->delim == '{') {
        cur->index++;
        decl_vec decls = {NULL, 0, 0, 0};
        css_parse_declarations(pool, cur, &decls);
        css_buf body = {NULL, 0, 0, 0};
        css_render_declarations(pool, &decls, &body);
        if (body.len > 0) {
            /* render the selector straight into the pool (it reads only the source tokens, never the pool scratch),
               which avoids a per-rule temporary buffer allocation; the body needs its own buffer because rendering it
               uses the pool as value scratch, so it is interned afterwards */
            item->is_rule = 1;
            item->sel_off = pool->len;
            css_minify_selector(cur->vec, prelude_start, prelude_end, pool);
            item->sel_len = pool->len - item->sel_off;
            item->body_off = pool_run(pool, body.data, body.len);
            item->body_len = body.len;
        }
        css_free(decls.items);
        cbuf_free(&body);
        return;
    }
    if (token && token->delim == ';') {
        cur->index++;
    }
    /* a stray segment with no block: keep its trimmed text verbatim (error recovery). The caller (css_parse_rules)
       consumes leading whitespace/comments before dispatching here, so only the trailing edge needs trimming. */
    Py_ssize_t start = prelude_start;
    Py_ssize_t end = prelude_end;
    while (end > start && cur->vec->items[end - 1].kind == CSS_WS) {
        end--;
    }
    css_buf text = {NULL, 0, 0, 0};
    for (Py_ssize_t index = start; index < end; index++) {
        css_token *piece = &cur->vec->items[index];
        cbuf_put_run(&text, piece->text, piece->text_len);
        if (piece->kind == CSS_NUM) {
            cbuf_put_run(&text, (piece->text + piece->text_len), piece->unit_len);
        }
    }
    item->text_off = pool_run(pool, text.data, text.len);
    item->text_len = text.len;
    cbuf_free(&text);
}

static int rule_run_eq(const css_buf *pool, Py_ssize_t a_off, Py_ssize_t a_len, Py_ssize_t b_off, Py_ssize_t b_len) {
    return a_len == b_len && memcmp(pool->data + a_off, pool->data + b_off, (size_t)a_len * sizeof(css_char)) == 0;
}

/* Re-minify "prev_body;it_body" so a same-selector merge dedups overlapping declarations the same way one rule would.
 */
static void css_merge_rule_bodies(css_buf *pool, rule_item *prev, const rule_item *it) {
    css_buf combined = {NULL, 0, 0, 0};
    cbuf_put_run(&combined, pool->data + prev->body_off, prev->body_len);
    cbuf_putc(&combined, ';');
    cbuf_put_run(&combined, pool->data + it->body_off, it->body_len);
    token_vec tokens = {NULL, 0, 0, 0};
    css_tokenize(combined.data, combined.len, &tokens);
    cursor inner = {&tokens, 0};
    decl_vec decls = {NULL, 0, 0, 0};
    css_parse_declarations(pool, &inner, &decls);
    css_buf body = {NULL, 0, 0, 0};
    css_render_declarations(pool, &decls, &body);
    prev->body_off = pool_run(pool, body.data, body.len);
    prev->body_len = body.len;
    css_free(tokens.items);
    css_free(decls.items);
    cbuf_free(&body);
    cbuf_free(&combined);
}

/* Merge consecutive qualified rules: same selector -> combine declaration bodies; identical body -> combine
   selectors into a list. Only adjacent rules merge (an opaque node between them resets adjacency), so the cascade is
   never reordered. */
static void css_merge_adjacent_rules(css_buf *pool, rule_vec *items) {
    Py_ssize_t prev = -1;
    for (Py_ssize_t index = 0; index < items->len; index++) {
        rule_item *it = &items->items[index];
        if (!it->is_rule) {
            prev = -1;
            continue;
        }
        if (prev >= 0) {
            rule_item *target = &items->items[prev];
            if (rule_run_eq(pool, target->sel_off, target->sel_len, it->sel_off, it->sel_len)) {
                css_merge_rule_bodies(pool, target, it);
                it->dropped = 1;
                continue;
            }
            if (rule_run_eq(pool, target->body_off, target->body_len, it->body_off, it->body_len)) {
                css_buf selector = {NULL, 0, 0, 0};
                cbuf_put_run(&selector, pool->data + target->sel_off, target->sel_len);
                cbuf_putc(&selector, ',');
                cbuf_put_run(&selector, pool->data + it->sel_off, it->sel_len);
                target->sel_off = pool_run(pool, selector.data, selector.len);
                target->sel_len = selector.len;
                cbuf_free(&selector);
                it->dropped = 1;
                continue;
            }
        }
        prev = index;
    }
}

/* Parse a rule list, collecting nodes so adjacent rules can be merged, then serialize. At the top level, declarations
   between rules are stray text; nested (inside an at-block) a '}' ends the list. */
static void css_parse_rules(css_buf *pool, cursor *cur, int top, css_buf *out) {
    rule_vec items = {NULL, 0, 0, 0};
    while (cur->index < cur->vec->len) {
        css_token *token = cursor_peek(cur);
        if (token->kind == CSS_WS || token->kind == CSS_COMMENT) {
            /* a comment token's text always starts with the slash-star the tokenizer matched */
            if (token->kind == CSS_COMMENT && token->text_len >= 3 && token->text[2] == '!') {
                /* a bang comment is preserved (whitespace-collapsed) as an opaque node */
                css_buf text = {NULL, 0, 0, 0};
                cbuf_puts(&text, "/*!");
                int pending = 0;
                for (Py_ssize_t pos = 3; pos < token->text_len - 2; pos++) {
                    if (css_is_ws(token->text[pos])) {
                        pending = 1;
                    } else {
                        if (pending && text.len > 3) {
                            cbuf_putc(&text, ' ');
                        }
                        pending = 0;
                        cbuf_putc(&text, token->text[pos]);
                    }
                }
                cbuf_puts(&text, "*/");
                rule_item item = {0};
                item.text_off = pool_run(pool, text.data, text.len);
                item.text_len = text.len;
                rule_vec_push(&items, item);
                cbuf_free(&text);
            }
            cur->index++;
            continue;
        }
        if (token->kind == CSS_DELIM && token->delim == '}') {
            cur->index++;
            if (!top) {
                break;
            }
            continue;
        }
        if (token->kind == CSS_AT) {
            css_buf piece = {NULL, 0, 0, 0};
            css_parse_at(pool, cur, &piece);
            /* css_parse_at always emits at least the lowercased at-rule name, so piece is never empty */
            rule_item item = {0};
            item.text_off = pool_run(pool, piece.data, piece.len);
            item.text_len = piece.len;
            /* an at-statement produced no block: detect by the absence of a closing '}' */
            item.at_statement = piece.data[piece.len - 1] != '}';
            rule_vec_push(&items, item);
            cbuf_free(&piece);
        } else {
            rule_item item = {0};
            css_parse_qualified(pool, cur, &item);
            if (item.is_rule || item.text_len > 0) {
                rule_vec_push(&items, item);
            }
        }
    }
    css_merge_adjacent_rules(pool, &items);
    int prev_at_statement = 0;
    for (Py_ssize_t index = 0; index < items.len; index++) {
        rule_item *item = &items.items[index];
        if (item->dropped) {
            continue;
        }
        if (prev_at_statement) {
            cbuf_putc(out, ';');
        }
        if (item->is_rule) {
            cbuf_put_run(out, pool->data + item->sel_off, item->sel_len);
            cbuf_putc(out, '{');
            cbuf_put_run(out, pool->data + item->body_off, item->body_len);
            cbuf_putc(out, '}');
            prev_at_statement = 0;
        } else {
            cbuf_put_run(out, pool->data + item->text_off, item->text_len);
            prev_at_statement = item->at_statement;
        }
    }
    css_free(items.items);
}

/* The allocator-agnostic core: minify a code-point view into a freshly allocated buffer (free with css_free). The
   harness and the CPython binding both call this; it touches no CPython runtime. */
css_char *th_minify_css_bytes(const css_char *view, Py_ssize_t length, int inline_mode, Py_ssize_t *out_len) {
    token_vec tokens = {NULL, 0, 0, 0};
    /* presize from the input: tokens average a few code points each and the output never exceeds the input, so one
       allocation up front avoids the geometric realloc churn (and its repeated copies) on a large stylesheet */
    Py_ssize_t token_guess = length / 4 < 64 ? 64 : length / 4;
    css_token *token_store = css_malloc((size_t)token_guess * sizeof(css_token));
    if (token_store != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        tokens.items = token_store;
        tokens.cap = token_guess;
    }
    css_tokenize(view, length, &tokens);
    css_buf pool = {NULL, 0, 0, 0};
    css_buf out = {NULL, 0, 0, 0};
    /* the pool holds the value scratch plus every interned selector and body, so it runs to roughly twice the input */
    cbuf_reserve(&pool, length * 2);
    cbuf_reserve(&out, length);
    cursor cur = {&tokens, 0};
    if (inline_mode) {
        decl_vec decls = {NULL, 0, 0, 0};
        css_parse_declarations(&pool, &cur, &decls);
        css_render_declarations(&pool, &decls, &out);
        css_free(decls.items);
    } else {
        css_parse_rules(&pool, &cur, 1, &out);
    }
    css_free(tokens.items);
    cbuf_free(&pool);
    *out_len = out.len;
    return out.data;
}

#ifndef CSS_MINIFY_STANDALONE
static PyObject *css_minify_entry(PyObject *arg, int inline_mode) {
    if (!PyUnicode_Check(arg)) {
        PyErr_SetString(PyExc_TypeError, "argument must be str");
        return NULL;
    }
    /* the str's UTF-8 view is cached and, for an ASCII str, aliases its storage (no copy); a str with a lone
       surrogate has no UTF-8 form and is rejected with the encode error */
    Py_ssize_t length = 0;
    const char *utf8 = PyUnicode_AsUTF8AndSize(arg, &length);
    if (utf8 == NULL) {
        return NULL;
    }
    Py_ssize_t out_len = 0;
    css_char *data = th_minify_css_bytes((const css_char *)utf8, length, inline_mode, &out_len);
    if (data == NULL && out_len != 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory();        /* GCOVR_EXCL_LINE */
    }
    PyObject *result = PyUnicode_DecodeUTF8((const char *)data, out_len, "strict");
    PyMem_Free(data);
    return result;
}

PyObject *turbohtml_minify_css(PyObject *Py_UNUSED(module), PyObject *arg) {
    return css_minify_entry(arg, 0);
}

PyObject *turbohtml_minify_css_inline(PyObject *Py_UNUSED(module), PyObject *arg) {
    return css_minify_entry(arg, 1);
}
#endif /* !CSS_MINIFY_STANDALONE */
