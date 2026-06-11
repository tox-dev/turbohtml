/* HTML escaping.

   Most strings contain nothing that needs escaping, so escape() runs two
   passes: a counting pass that sizes the result (and proves a no-op when the
   count is zero), and a writing pass. The 1-byte path works sixteen bytes per
   step with NEON / SSE2 where available (a SWAR word elsewhere): the counting
   pass accumulates per-special growth branchlessly, and the writing pass turns
   the comparison result into a position bitmask so untouched stretches are
   copied wholesale and only actual specials are rewritten. UCS-2 / UCS-4 test
   a 64-bit word holding four / two code points with SWAR, so all specials are
   matched in one pass instead of one PyUnicode_FindChar() sweep per special. */

#include "turbohtml.h"

#include <stdint.h>
#include <string.h>

#if defined(_MSC_VER)
#include <intrin.h>
static inline int ctz64(uint64_t value) {
    unsigned long index;
    _BitScanForward64(&index, value);
    return (int)index;
}
#else
#define ctz64(value) __builtin_ctzll(value)
#endif

static inline Py_ssize_t escape_extra(Py_UCS4 character, int quote) {
    switch (character) {
    case '&':
        return 4; /* "&amp;" replaces one character with five */
    case '<':
    case '>':
        return 3; /* "&lt;" / "&gt;" */
    case '"':
        return quote ? 5 : 0; /* "&quot;" */
    case '\'':
        return quote ? 5 : 0; /* "&#x27;" */
    default:
        return 0;
    }
}

#if defined(__aarch64__) || defined(_M_ARM64)

#include <arm_neon.h>

#define BLOCK_BYTES 16

/* Every special has a unique low nibble ('"' 0x22, '&' 0x26, '\'' 0x27, '<'
   0x3C, '>' 0x3E), so one table lookup on the low nibble plus one comparison
   classifies a whole block (the PSHUFB trick from pulldown-cmark). Index 0
   holds 0x7F, which no byte with a zero low nibble equals, so it never
   produces a false match. The growth tables map the same nibbles to how many
   characters that special's replacement adds (&amp; adds 4, &lt;/&gt; add 3,
   &quot;/&#x27; add 5). */
static const uint8_t NIBBLE_SPECIALS[2][16] = {
    {0x7F, 0, 0, 0, 0, 0, '&', 0, 0, 0, 0, 0, '<', 0, '>', 0},
    {0x7F, 0, '"', 0, 0, 0, '&', '\'', 0, 0, 0, 0, '<', 0, '>', 0},
};
static const uint8_t NIBBLE_GROWTH[2][16] = {
    {0, 0, 0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 3, 0, 3, 0},
    {0, 0, 5, 0, 0, 0, 4, 5, 0, 0, 0, 0, 3, 0, 3, 0},
};

/* How many characters of growth escaping this block adds, all lanes at once:
   the match mask selects each lane's growth, summed horizontally. */
static inline Py_ssize_t block_extra(const uint8_t *block, int quote) {
    uint8x16_t bytes = vld1q_u8(block);
    uint8x16_t nibbles = vandq_u8(bytes, vdupq_n_u8(0x0F));
    uint8x16_t matches = vceqq_u8(bytes, vqtbl1q_u8(vld1q_u8(NIBBLE_SPECIALS[quote]), nibbles));
    uint8x16_t growth = vqtbl1q_u8(vld1q_u8(NIBBLE_GROWTH[quote]), nibbles);
    return (Py_ssize_t)vaddvq_u8(vandq_u8(matches, growth));
}

/* Four mask bits per byte (the narrowing-shift trick: NEON has no movemask);
   SPECIAL_INDEX / SPECIAL_CLEAR below hide the per-arch mask layout. */
static inline uint64_t block_special_mask(const uint8_t *block, int quote) {
    uint8x16_t bytes = vld1q_u8(block);
    uint8x16_t nibbles = vandq_u8(bytes, vdupq_n_u8(0x0F));
    uint8x16_t hits = vceqq_u8(bytes, vqtbl1q_u8(vld1q_u8(NIBBLE_SPECIALS[quote]), nibbles));
    return vget_lane_u64(vreinterpret_u64_u8(vshrn_n_u16(vreinterpretq_u16_u8(hits), 4)), 0);
}

#define SPECIAL_INDEX(mask) (ctz64(mask) >> 2)
#define SPECIAL_CLEAR(mask, index) ((mask) & ~(0xFULL << ((index) * 4)))

/* Wide sizing scan: UCS-4 tests four code points per vector (UCS-2 stays on
   the SWAR probes; both an eight-lane step and a 64-bit vector test measured
   no better than SWAR on real and dense inputs). */
#define UCS2_STEP UCS2_LANES
#define UCS4_STEP 4
#define word_has_special16 swar_word_has_special16

static inline int word_has_special32(const uint32_t *block, int quote) {
    uint32x4_t lanes = vld1q_u32(block);
    uint32x4_t hits = vorrq_u32(vorrq_u32(vceqq_u32(lanes, vdupq_n_u32('&')), vceqq_u32(lanes, vdupq_n_u32('<'))),
                                vceqq_u32(lanes, vdupq_n_u32('>')));
    if (quote) {
        hits = vorrq_u32(hits, vorrq_u32(vceqq_u32(lanes, vdupq_n_u32('"')), vceqq_u32(lanes, vdupq_n_u32('\''))));
    }
    return vmaxvq_u32(hits) != 0;
}

#elif defined(__SSE2__) || defined(_M_X64)

#include <emmintrin.h>

#define BLOCK_BYTES 16

/* How many characters of growth escaping this block adds, all lanes at once:
   each comparison yields 0xFF per match, masked down to that special's growth
   (&amp; adds 4, &lt;/&gt; add 3, &quot;/&#x27; add 5), summed horizontally. */
static inline Py_ssize_t block_extra(const uint8_t *block, int quote) {
    __m128i bytes = _mm_loadu_si128((const __m128i *)block);
    __m128i extras = _mm_and_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('&')), _mm_set1_epi8(4));
    extras = _mm_add_epi8(extras, _mm_and_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('<')), _mm_set1_epi8(3)));
    extras = _mm_add_epi8(extras, _mm_and_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('>')), _mm_set1_epi8(3)));
    if (quote) {
        extras = _mm_add_epi8(extras, _mm_and_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('"')), _mm_set1_epi8(5)));
        extras = _mm_add_epi8(extras, _mm_and_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('\'')), _mm_set1_epi8(5)));
    }
    __m128i sums = _mm_sad_epu8(extras, _mm_setzero_si128());
    return (Py_ssize_t)(_mm_cvtsi128_si32(sums) + _mm_extract_epi16(sums, 4));
}

/* One mask bit per byte; SPECIAL_INDEX / SPECIAL_CLEAR hide the layout. */
static inline uint64_t block_special_mask(const uint8_t *block, int quote) {
    __m128i bytes = _mm_loadu_si128((const __m128i *)block);
    __m128i hits =
        _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('&')), _mm_cmpeq_epi8(bytes, _mm_set1_epi8('<'))),
                     _mm_cmpeq_epi8(bytes, _mm_set1_epi8('>')));
    if (quote) {
        hits = _mm_or_si128(
            hits, _mm_or_si128(_mm_cmpeq_epi8(bytes, _mm_set1_epi8('"')), _mm_cmpeq_epi8(bytes, _mm_set1_epi8('\''))));
    }
    return (uint64_t)(unsigned)_mm_movemask_epi8(hits);
}

#define SPECIAL_INDEX(mask) ctz64(mask)
#define SPECIAL_CLEAR(mask, index) ((mask) & ((mask) - 1))

/* Wide sizing scan: vector variants are unvalidated on this project's
   benchmark hardware, so x86 keeps the portable SWAR probes. */
#define UCS2_STEP UCS2_LANES
#define UCS4_STEP UCS4_LANES
#define word_has_special16 swar_word_has_special16
#define word_has_special32 swar_word_has_special32

#else

#define BLOCK_BYTES 8

#define SWAR_ONES 0x0101010101010101ULL
#define SWAR_HIGHS 0x8080808080808080ULL

static inline uint64_t swar_hasbyte(uint64_t word, uint8_t byte) {
    uint64_t lanes = word ^ (SWAR_ONES * byte);
    return (lanes - SWAR_ONES) & ~lanes & SWAR_HIGHS;
}

/* The has-byte mask sets only each matching lane's high bit, so shifting it to
   the lanes' low bits and multiplying by ones sums the lanes into the top byte. */
static inline Py_ssize_t swar_count(uint64_t mask) {
    return (Py_ssize_t)((((mask) >> 7) * SWAR_ONES) >> 56);
}

/* How many characters of growth escaping this block adds, all lanes at once
   (&amp; adds 4, &lt;/&gt; add 3, &quot;/&#x27; add 5). */
static inline Py_ssize_t block_extra(const uint8_t *block, int quote) {
    uint64_t word;
    memcpy(&word, block, sizeof(word));
    Py_ssize_t extra = 4 * swar_count(swar_hasbyte(word, '&')) + 3 * swar_count(swar_hasbyte(word, '<')) +
                       3 * swar_count(swar_hasbyte(word, '>'));
    if (quote) {
        extra += 5 * swar_count(swar_hasbyte(word, '"')) + 5 * swar_count(swar_hasbyte(word, '\''));
    }
    return extra;
}

/* One mask bit per byte, in each lane's high bit; SPECIAL_INDEX / SPECIAL_CLEAR
   hide the layout. */
static inline uint64_t block_special_mask(const uint8_t *block, int quote) {
    uint64_t word;
    memcpy(&word, block, sizeof(word));
    uint64_t mask = swar_hasbyte(word, '&') | swar_hasbyte(word, '<') | swar_hasbyte(word, '>');
    if (quote) {
        mask |= swar_hasbyte(word, '"') | swar_hasbyte(word, '\'');
    }
    return mask;
}

#define SPECIAL_INDEX(mask) (ctz64(mask) >> 3)
#define SPECIAL_CLEAR(mask, index) ((mask) & ((mask) - 1))

/* Wide sizing scan: no vectors here, fall back to the SWAR word probes. */
#define UCS2_STEP UCS2_LANES
#define UCS4_STEP UCS4_LANES
#define word_has_special16 swar_word_has_special16
#define word_has_special32 swar_word_has_special32

#endif

/* The wide write phase keeps the fine-grained SWAR probes from turbohtml.h on
   every arch: one special poisons only four / two code points into the scalar
   rewrite, where a full SIMD step would poison eight / four. The wide sizing
   scan uses the per-arch UCS2_STEP / UCS4_STEP tests above instead, because
   its dirty-window cost is one escape_extra per lane regardless of width. */

static inline int swar_word_has_special16(const uint16_t *block, int quote) {
    uint64_t word;
    memcpy(&word, block, sizeof(word));
    uint64_t mask = swar_haslane16(word, '&') | swar_haslane16(word, '<') | swar_haslane16(word, '>');
    if (quote) {
        mask |= swar_haslane16(word, '"') | swar_haslane16(word, '\'');
    }
    return mask != 0;
}

static inline int swar_word_has_special32(const uint32_t *block, int quote) {
    uint64_t word;
    memcpy(&word, block, sizeof(word));
    uint64_t mask = swar_haslane32(word, '&') | swar_haslane32(word, '<') | swar_haslane32(word, '>');
    if (quote) {
        mask |= swar_haslane32(word, '"') | swar_haslane32(word, '\'');
    }
    return mask != 0;
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

PyObject *turbohtml_escape(PyObject *Py_UNUSED(module), PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"s", "quote", NULL};
    PyObject *text;
    int quote = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U|p:escape", kwlist, &text, &quote)) {
        return NULL;
    }

    int kind = PyUnicode_KIND(text);
    Py_ssize_t length = PyUnicode_GET_LENGTH(text);
    const void *data = PyUnicode_DATA(text);

    Py_ssize_t extra = 0;
    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        Py_ssize_t pos = 0;
        while (pos + BLOCK_BYTES <= length) {
            extra += block_extra(input + pos, quote);
            pos += BLOCK_BYTES;
        }
        for (; pos < length; pos++) {
            extra += escape_extra(input[pos], quote);
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        Py_ssize_t pos = 0;
        while (pos + UCS2_STEP <= length) {
            if (word_has_special16(input + pos, quote)) {
                for (int lane = 0; lane < UCS2_STEP; lane++) {
                    extra += escape_extra(input[pos + lane], quote);
                }
            }
            pos += UCS2_STEP;
        }
        for (; pos < length; pos++) {
            extra += escape_extra(input[pos], quote);
        }
    } else {
        const uint32_t *input = (const uint32_t *)data;
        Py_ssize_t pos = 0;
        while (pos + UCS4_STEP <= length) {
            if (word_has_special32(input + pos, quote)) {
                for (int lane = 0; lane < UCS4_STEP; lane++) {
                    extra += escape_extra(input[pos + lane], quote);
                }
            }
            pos += UCS4_STEP;
        }
        for (; pos < length; pos++) {
            extra += escape_extra(input[pos], quote);
        }
    }

    if (extra == 0) {
        /* normalize str subclasses to a real str even when nothing is escaped */
        return PyUnicode_FromObject(text);
    }

    /* maxchar comes from the input and escapes are ASCII, so the output kind
       always matches the input kind and clean blocks can be copied bytewise */
    Py_UCS4 maxchar = PyUnicode_MAX_CHAR_VALUE(text);
    PyObject *out = PyUnicode_New(length + extra, maxchar);
    if (out == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;   /* GCOVR_EXCL_LINE */
    }
    int out_kind = PyUnicode_KIND(out);
    void *out_data = PyUnicode_DATA(out);
    Py_ssize_t written = 0;

    if (kind == PyUnicode_1BYTE_KIND) {
        const uint8_t *input = (const uint8_t *)data;
        uint8_t *output = (uint8_t *)out_data;
        Py_ssize_t pos = 0;
        while (pos + BLOCK_BYTES <= length) {
            uint64_t mask = block_special_mask(input + pos, quote);
            if (mask == 0) {
                memcpy(output + written, input + pos, BLOCK_BYTES);
                written += BLOCK_BYTES;
            } else {
                /* copy the gap before each special wholesale, rewrite the
                   special, then copy whatever trails the last one; the gap
                   checks keep special-dense input from paying for empty copies */
                Py_ssize_t prev = 0;
                do {
                    Py_ssize_t index = SPECIAL_INDEX(mask);
                    if (index > prev) {
                        memcpy(output + written, input + pos + prev, (size_t)(index - prev));
                        written += index - prev;
                    }
                    written += write_escaped(out_kind, out_data, written, input[pos + index], quote);
                    mask = SPECIAL_CLEAR(mask, index);
                    prev = index + 1;
                } while (mask != 0);
                if (BLOCK_BYTES > prev) {
                    memcpy(output + written, input + pos + prev, (size_t)(BLOCK_BYTES - prev));
                    written += BLOCK_BYTES - prev;
                }
            }
            pos += BLOCK_BYTES;
        }
        for (; pos < length; pos++) {
            written += write_escaped(out_kind, out_data, written, input[pos], quote);
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        const uint16_t *input = (const uint16_t *)data;
        uint16_t *output = (uint16_t *)out_data;
        Py_ssize_t pos = 0;
        while (pos + UCS2_LANES <= length) {
            if (swar_word_has_special16(input + pos, quote)) {
                for (int lane = 0; lane < UCS2_LANES; lane++) {
                    written += write_escaped(out_kind, out_data, written, input[pos + lane], quote);
                }
            } else {
                memcpy(output + written, input + pos, UCS2_LANES * sizeof(uint16_t));
                written += UCS2_LANES;
            }
            pos += UCS2_LANES;
        }
        for (; pos < length; pos++) {
            written += write_escaped(out_kind, out_data, written, input[pos], quote);
        }
    } else {
        const uint32_t *input = (const uint32_t *)data;
        uint32_t *output = (uint32_t *)out_data;
        Py_ssize_t pos = 0;
        while (pos + UCS4_LANES <= length) {
            if (swar_word_has_special32(input + pos, quote)) {
                for (int lane = 0; lane < UCS4_LANES; lane++) {
                    written += write_escaped(out_kind, out_data, written, input[pos + lane], quote);
                }
            } else {
                memcpy(output + written, input + pos, UCS4_LANES * sizeof(uint32_t));
                written += UCS4_LANES;
            }
            pos += UCS4_LANES;
        }
        for (; pos < length; pos++) {
            written += write_escaped(out_kind, out_data, written, input[pos], quote);
        }
    }
    return out;
}
