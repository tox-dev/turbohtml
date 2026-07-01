#ifndef TURBOHTML_CSS_TOKENIZE_H
#define TURBOHTML_CSS_TOKENIZE_H

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

#if defined(_MSC_VER)
static inline int css_ctz64(uint64_t value) {
    unsigned long index;
    _BitScanForward64(&index, value);
    return (int)index;
}
#else
#define css_ctz64(value) __builtin_ctzll(value)
#endif

/* Scanning an identifier is the tokenizer's hottest loop, so its common run -- ASCII letters, digits, '-' and '_' with
   no escape or non-ASCII byte -- advances sixteen bytes per step on NEON/SSE2 (a scalar byte loop elsewhere). The
   caller resumes scalar handling at the first byte the fast run rejects (a '\' escape, a non-ASCII byte, or a stop
   character), so the result is byte-identical to a pure scalar scan; only the plain stretch is skipped faster. */
#if defined(__aarch64__) || defined(_M_ARM64)
#include <arm_neon.h>
#define CSS_IDENT_SIMD 1
static inline uint64_t css_ident_stop_mask(const unsigned char *p) {
    uint8x16_t bytes = vld1q_u8(p);
    uint8x16_t plain = vorrq_u8(vorrq_u8(vandq_u8(vcgeq_u8(bytes, vdupq_n_u8('0')), vcleq_u8(bytes, vdupq_n_u8('9'))),
                                         vandq_u8(vcgeq_u8(bytes, vdupq_n_u8('A')), vcleq_u8(bytes, vdupq_n_u8('Z')))),
                                vorrq_u8(vandq_u8(vcgeq_u8(bytes, vdupq_n_u8('a')), vcleq_u8(bytes, vdupq_n_u8('z'))),
                                         vorrq_u8(vceqq_u8(bytes, vdupq_n_u8('-')), vceqq_u8(bytes, vdupq_n_u8('_')))));
    uint8x16_t stop = vmvnq_u8(plain);
    return vget_lane_u64(vreinterpret_u64_u8(vshrn_n_u16(vreinterpretq_u16_u8(stop), 4)), 0);
}
#define CSS_IDENT_STOP_INDEX(mask) (css_ctz64(mask) >> 2)
#elif defined(__SSE2__) || defined(_M_X64)
#include <emmintrin.h>
#define CSS_IDENT_SIMD 1
static inline uint64_t css_ident_stop_mask(const unsigned char *p) {
    __m128i bytes = _mm_loadu_si128((const __m128i *)p);
    __m128i digit =
        _mm_and_si128(_mm_cmpgt_epi8(bytes, _mm_set1_epi8('0' - 1)), _mm_cmplt_epi8(bytes, _mm_set1_epi8('9' + 1)));
    __m128i upper =
        _mm_and_si128(_mm_cmpgt_epi8(bytes, _mm_set1_epi8('A' - 1)), _mm_cmplt_epi8(bytes, _mm_set1_epi8('Z' + 1)));
    __m128i lower =
        _mm_and_si128(_mm_cmpgt_epi8(bytes, _mm_set1_epi8('a' - 1)), _mm_cmplt_epi8(bytes, _mm_set1_epi8('z' + 1)));
    __m128i punct = _mm_or_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('-')), _mm_cmpeq_epi8(bytes, _mm_set1_epi8('_')));
    __m128i plain = _mm_or_si128(_mm_or_si128(digit, upper), _mm_or_si128(lower, punct));
    return (~(uint64_t)(unsigned int)_mm_movemask_epi8(plain)) & 0xFFFFULL;
}
#define CSS_IDENT_STOP_INDEX(mask) css_ctz64(mask)
#endif

/* The count of leading plain-identifier bytes (ASCII alnum, '-', '_') in [p, p+avail). */
static inline Py_ssize_t css_ident_plain_run(const unsigned char *p, Py_ssize_t avail) {
    Py_ssize_t index = 0;
#if defined(CSS_IDENT_SIMD)
    for (; index + 16 <= avail; index += 16) {
        uint64_t stop = css_ident_stop_mask(p + index);
        if (stop != 0) {
            return index + (Py_ssize_t)CSS_IDENT_STOP_INDEX(stop);
        }
    }
#endif
    while (index < avail) {
        unsigned char c = p[index];
        if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '-' || c == '_')) {
            break;
        }
        index++;
    }
    return index;
}

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
    memcpy(buffer->data + buffer->len, text, (size_t)len);
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
            Py_ssize_t end = length; /* an unterminated comment runs to EOF */
            while (scan + 1 < length) {
                const css_char *star = memchr(source + scan, '*', (size_t)(length - scan - 1));
                if (star == NULL) {
                    break;
                }
                scan = (Py_ssize_t)(star - source);
                if (source[scan + 1] == '/') {
                    end = scan + 2;
                    break;
                }
                scan++;
            }
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
                scan += (source[scan] == '\\' && scan + 1 < length) ? 2 : 1; /* keep an escaped char in the name */
            }
            token.kind = CSS_AT;
            token.text = &source[pos];
            token.text_len = scan - pos;
            token_vec_push(vec, token);
            pos = scan;
        } else if (character == '#' && pos + 1 < length && css_is_ident(source[pos + 1])) {
            Py_ssize_t scan = pos + 1;
            while (scan < length && css_is_ident(source[scan])) {
                scan += (source[scan] == '\\' && scan + 1 < length) ? 2 : 1; /* keep an escaped char in the hash */
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
            for (;;) {
                scan += css_ident_plain_run(source + scan, length - scan);
                if (scan >= length || !css_is_ident(source[scan])) {
                    break;
                }
                /* a non-plain ident byte: a '\' escape advances two, a non-ASCII continuation one */
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

#endif /* TURBOHTML_CSS_TOKENIZE_H */
