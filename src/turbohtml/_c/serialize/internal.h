/* Serialization primitives shared by every output mode (#document, HTML,
   minify, markdown, text, readability). The escape formatters, the
   start/close-tag writers and the attribute ordering live here as
   `static inline`; the growable buffer they fill is included from buffer.h and
   the markdown/plain-text block predicates from markdown_internal.h, at the
   positions their definitions used to occupy, so a translation unit that
   includes this header sees the same definitions in the same order as before
   the split. */

#ifndef TURBOHTML_SERIALIZE_INTERNAL_H
#define TURBOHTML_SERIALIZE_INTERNAL_H

#include "dom/tree.h"
#include "dom/tree_internal.h" /* struct th_tree, need_text, is_void_atom */

#include "core/ascii.h"      /* is_space, the shared HTML ASCII-whitespace predicate */
#include "core/common.h"     /* SWAR lane probes for the serializer's clean-run scan */
#include "core/vec.h"        /* th_grow_cap overflow-safe buffer growth */
#include "core/work_steps.h" /* TH_WORK_STEP, the loop counter the complexity tests read */
#include "data/entity_names.h"

#include <stdlib.h>
#include <string.h>

#include "serialize/buffer.h" /* the sbuf struct and its reserve/append hot path */
#include "serialize/xml_text.h"

/* The #document attribute sort works in fixed stack buffers: at most this many
   attributes are ordered, each rendered name capped at this many bytes. */
#define MAX_SORTED_ATTRS 64
#define MAX_ATTR_NAME 128

/* The escape policy serialize()/encode() expose through the Formatter enum. */
enum th_formatter {
    TH_FMT_WHATWG,  /* conformant minimal escaping: & < > nbsp in text, & < > " nbsp in attrs */
    TH_FMT_MINIMAL, /* the three structural characters, plus the quote that delimits an attribute value */
    TH_FMT_NAMED,   /* HTML named entities for every character that has one */
};

/* Whether a character has a named reference under the NAMED formatter. The ASCII
   ones are & " < > and CR, so the generated table is only consulted past 0x7F. */
static inline int sbuf_named_special(Py_UCS4 character) {
    if (character < 0x80) {
        return character == '&' || character == '"' || character == '<' || character == '>' || character == '\r';
    }
    return th_entity_name(character) != NULL;
}

/* The structural special set tested over a 64-bit word of two UCS-4 code points: & < > and CR.
   CR must be written as &#13; in every formatter: input preprocessing folds a raw CR to LF on
   reparse, so the output would read back as a different tree. Each probe sets the matching lane's
   high bit; a nonzero result means a special is in the pair. */
static inline uint64_t sbuf_special_mask(uint64_t word) {
    return swar_haslane32(word, '&') | swar_haslane32(word, '<') | swar_haslane32(word, '>') |
           swar_haslane32(word, '\r');
}

/* The special set plus the no-break space WHATWG folds and the " that ends an attribute value.
   Only the NEON and scalar text scans use it; the SSE2 scan tests both in its vector block. */
static inline uint64_t sbuf_value_mask(uint64_t word, int escape_quote, int escape_nbsp) {
    uint64_t mask = sbuf_special_mask(word);
    if (escape_nbsp) {
        mask |= swar_haslane32(word, 0xA0);
    }
    if (escape_quote) {
        mask |= swar_haslane32(word, '"');
    }
    return mask;
}

/* Skip step for the clean-run scan: the same special set tested a vector at a time rather than a
   64-bit pair. The tree holds text as UCS-4, so every serialized run pays this scan. On NEON and the
   scalar build the step only skips: when it reports a hit the SWAR pair loop in sbuf_find_special
   rescans the block and names the exact code point. SSE2 names it from the vector mask instead, since
   most runs are under a dozen code points and the pair loop would cost more than the step it follows.
   escape.c carries the same per-arch split for its sizing scan. */
#if !defined(TH_FORCE_SCALAR) && (defined(__aarch64__) || defined(_M_ARM64))

#include <arm_neon.h>

#define SBUF_SCAN_STEP 4

static inline int sbuf_block_has_special(const Py_UCS4 *text, int escape_quote, int escape_nbsp) {
    uint32x4_t lanes = vld1q_u32((const uint32_t *)text);
    uint32x4_t hits = vceqq_u32(lanes, vdupq_n_u32('&'));
    hits = vorrq_u32(hits, vorrq_u32(vceqq_u32(lanes, vdupq_n_u32('<')), vceqq_u32(lanes, vdupq_n_u32('>'))));
    hits = vorrq_u32(hits, vceqq_u32(lanes, vdupq_n_u32('\r')));
    if (escape_quote) {
        hits = vorrq_u32(hits, vceqq_u32(lanes, vdupq_n_u32('"')));
    }
    if (escape_nbsp) {
        hits = vorrq_u32(hits, vceqq_u32(lanes, vdupq_n_u32(0xA0)));
    }
    return vmaxvq_u32(hits) != 0;
}

#elif !defined(TH_FORCE_SCALAR) && (defined(__SSE2__) || defined(_M_X64))

#include <emmintrin.h>

#define SBUF_SCAN_STEP 8
#define SBUF_VECTOR_FIND

#if defined(_MSC_VER)
#include <intrin.h>
static inline int sbuf_ctz(unsigned value) {
    unsigned long index;
    _BitScanForward(&index, value);
    return (int)index;
}
#else
#define sbuf_ctz __builtin_ctz
#endif

/* Two vectors of four code points pack into one of eight 16-bit lanes. Every special is below 0x100 and
   the saturating pack maps anything wider to 0x7FFF, so the 16-bit compares match exactly the same code
   points as 32-bit compares would, at half the compares per code point. Lane k owns mask bits 2k and
   2k+1. */
static inline unsigned sbuf_block_special_bits(const Py_UCS4 *low_half, const Py_UCS4 *high_half, int escape_quote,
                                               int escape_nbsp) {
    __m128i low = _mm_loadu_si128((const __m128i *)low_half);
    __m128i high = _mm_loadu_si128((const __m128i *)high_half);
    __m128i lanes = _mm_packs_epi32(low, high);
    __m128i hits = _mm_cmpeq_epi16(lanes, _mm_set1_epi16('&'));
    hits = _mm_or_si128(
        hits, _mm_or_si128(_mm_cmpeq_epi16(lanes, _mm_set1_epi16('<')), _mm_cmpeq_epi16(lanes, _mm_set1_epi16('>'))));
    hits = _mm_or_si128(hits, _mm_cmpeq_epi16(lanes, _mm_set1_epi16('\r')));
    if (escape_quote) {
        hits = _mm_or_si128(hits, _mm_cmpeq_epi16(lanes, _mm_set1_epi16('"')));
    }
    if (escape_nbsp) {
        hits = _mm_or_si128(hits, _mm_cmpeq_epi16(lanes, _mm_set1_epi16(0xA0)));
    }
    return (unsigned)_mm_movemask_epi8(hits);
}

/* The first special at or after index, or len. Past the last full step one block laid over the final
   eight code points, with the lanes before index shifted out, covers the rest. A run of four to seven
   pairs its first and last four code points in one block; under four there is nothing to load, so a
   bitmap over the ASCII specials classifies each code point. */
static inline Py_ssize_t sbuf_find_special(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len, int escape_quote,
                                           int escape_nbsp) {
    while (index + SBUF_SCAN_STEP <= len) {
        unsigned bits = sbuf_block_special_bits(&text[index], &text[index + 4], escape_quote, escape_nbsp);
        if (bits != 0) {
            return index + sbuf_ctz(bits) / 2;
        }
        index += SBUF_SCAN_STEP;
    }
    if (index == len) {
        return len;
    }
    if (len >= SBUF_SCAN_STEP) {
        Py_ssize_t base = len - SBUF_SCAN_STEP;
        unsigned bits =
            sbuf_block_special_bits(&text[base], &text[base + 4], escape_quote, escape_nbsp) >> (2 * (index - base));
        return bits != 0 ? index + sbuf_ctz(bits) / 2 : len;
    }
    if (len >= 4) {
        /* the low lanes hold code points 0-3 and the high lanes len-4..len-1, so a code point in both
           shows twice; the low lanes come first, so the lowest kept bit is still the first special */
        Py_ssize_t first_high = index + 8 - len > 4 ? index + 8 - len : 4;
        unsigned keep = (index < 4 ? 0xFFu << (2 * index) & 0xFFu : 0) | (0xFFFFu << (2 * first_high) & 0xFF00u);
        unsigned bits = sbuf_block_special_bits(text, &text[len - 4], escape_quote, escape_nbsp) & keep;
        if (bits == 0) {
            return len;
        }
        Py_ssize_t lane = sbuf_ctz(bits) / 2;
        return lane < 4 ? lane : len - 8 + lane;
    }
    uint64_t ascii = 1ULL << '&' | 1ULL << '<' | 1ULL << '>' | 1ULL << '\r' | (escape_quote ? 1ULL << '"' : 0);
    for (; index < len; index++) {
        Py_UCS4 character = text[index];
        if (character < 64 ? (ascii >> character) & 1 : escape_nbsp && character == 0xA0) {
            return index;
        }
    }
    return len;
}

#else

#define SBUF_SCAN_STEP UCS4_LANES

static inline int sbuf_block_has_special(const Py_UCS4 *text, int escape_quote, int escape_nbsp) {
    uint64_t word;
    memcpy(&word, text, sizeof(word));
    return sbuf_value_mask(word, escape_quote, escape_nbsp) != 0;
}

#endif

#ifndef SBUF_VECTOR_FIND
/* The first special at or after index, or len. The pair probes name it from a word built
   low-lane-first, so the position is independent of byte order and of which character class matched. */
static inline Py_ssize_t sbuf_find_special(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len, int escape_quote,
                                           int escape_nbsp) {
    while (index + SBUF_SCAN_STEP <= len && !sbuf_block_has_special(&text[index], escape_quote, escape_nbsp)) {
        index += SBUF_SCAN_STEP;
    }
    for (; index + UCS4_LANES <= len; index += UCS4_LANES) {
        uint64_t word;
        memcpy(&word, &text[index], sizeof(word));
        if (sbuf_value_mask(word, escape_quote, escape_nbsp) != 0) {
            uint64_t ordered = (uint64_t)text[index] | ((uint64_t)text[index + 1] << 32);
            uint64_t omask = sbuf_value_mask(ordered, escape_quote, escape_nbsp);
            return index + (Py_ssize_t)((omask & 0x80000000ULL) == 0);
        }
    }
    /* one code point trails the last full pair; pad with a non-special lane so the same mask probe
       classifies it without a buffer overread */
    if (index < len && sbuf_value_mask((uint64_t)text[index], escape_quote, escape_nbsp) != 0) {
        return index;
    }
    return len;
}
#endif

/* The c14n text scan: it stops at & < > and CR, which take a reference, and at a character XML 1.0 cannot hold,
   which the writer drops. A run of C14N_WIDE_RUN code points or more is tested a block at a time, so the
   invalid-character test costs nothing per code point there; a shorter run takes the scalar c14n_find_stop, which
   costs less than setting the block test up. */
#define C14N_WIDE_RUN 16

#if defined(SBUF_VECTOR_FIND)

/* A surrogate or U+FFFE/U+FFFF lane, tested on the 32-bit lanes because the saturating pack folds them together with
   every other code point at or above 0x8000. */
static inline __m128i c14n_wide_invalid(__m128i lanes) {
    __m128i surrogate =
        _mm_and_si128(_mm_cmpgt_epi32(lanes, _mm_set1_epi32(0xD7FF)), _mm_cmplt_epi32(lanes, _mm_set1_epi32(0xE000)));
    return _mm_or_si128(surrogate, _mm_cmpeq_epi32(_mm_or_si128(lanes, _mm_set1_epi32(1)), _mm_set1_epi32(0xFFFF)));
}

/* The packed 16-bit lanes and mask layout of sbuf_block_special_bits. Tab and newline are flagged with the other
   controls, which keeps the test to three compares, and c14n_first_stop_lane passes over them. The wide test runs
   only when a lane reaches the surrogate range, which the OR of both halves bounds from above. */
static inline unsigned c14n_block_candidate_bits(const Py_UCS4 *block) {
    __m128i low = _mm_loadu_si128((const __m128i *)block);
    __m128i high = _mm_loadu_si128((const __m128i *)(block + 4));
    __m128i lanes = _mm_packs_epi32(low, high);
    __m128i hits = _mm_cmplt_epi16(lanes, _mm_set1_epi16(0x20));
    hits = _mm_or_si128(hits, _mm_cmpeq_epi16(lanes, _mm_set1_epi16('&')));
    /* < and > differ only in bit 1 */
    hits = _mm_or_si128(hits, _mm_cmpeq_epi16(_mm_or_si128(lanes, _mm_set1_epi16(2)), _mm_set1_epi16('>')));
    if (_mm_movemask_epi8(_mm_cmpgt_epi32(_mm_or_si128(low, high), _mm_set1_epi32(0xD7FF))) != 0) {
        hits = _mm_or_si128(hits, _mm_packs_epi32(c14n_wide_invalid(low), c14n_wide_invalid(high)));
    }
    return (unsigned)_mm_movemask_epi8(hits);
}

/* The first lane flagged in bits that is a stop, or -1, passing over tab and newline. */
static inline int c14n_first_stop_lane(const Py_UCS4 *block, unsigned bits) {
    while (bits != 0) {
        int lane = sbuf_ctz(bits) / 2;
        if (block[lane] != '\t' && block[lane] != '\n') {
            return lane;
        }
        bits &= ~(3u << (2 * lane));
    }
    return -1;
}

/* The first stop at or after index, or len, for a run of at least eight code points; past the last full block one
   block laid over the final eight, with the lanes before index masked off, covers the rest. Kept out of line: inlined
   into a writer loop that calls the allocator, the vector constants are rebuilt and spilled on every run. */
TH_NOINLINE static Py_ssize_t c14n_find_text_stop_wide(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    while (index + SBUF_SCAN_STEP <= len) {
        unsigned bits = c14n_block_candidate_bits(&text[index]);
        if (bits != 0) {
            int lane = c14n_first_stop_lane(&text[index], bits);
            if (lane >= 0) {
                return index + lane;
            }
        }
        index += SBUF_SCAN_STEP;
    }
    if (index == len) {
        return len;
    }
    Py_ssize_t base = len - SBUF_SCAN_STEP;
    unsigned bits = c14n_block_candidate_bits(&text[base]) & 0xFFFFu << (2 * (index - base));
    int lane = c14n_first_stop_lane(&text[base], bits);
    return lane >= 0 ? base + lane : len;
}

#elif !defined(TH_FORCE_SCALAR) && (defined(__aarch64__) || defined(_M_ARM64))

static inline int c14n_block_has_stop(const Py_UCS4 *text) {
    uint32x4_t lanes = vld1q_u32((const uint32_t *)text);
    uint32x4_t literal = vorrq_u32(vceqq_u32(lanes, vdupq_n_u32('\t')), vceqq_u32(lanes, vdupq_n_u32('\n')));
    uint32x4_t hits = vbicq_u32(vcltq_u32(lanes, vdupq_n_u32(0x20)), literal);
    hits = vorrq_u32(hits, vceqq_u32(lanes, vdupq_n_u32('&')));
    hits = vorrq_u32(hits, vorrq_u32(vceqq_u32(lanes, vdupq_n_u32('<')), vceqq_u32(lanes, vdupq_n_u32('>'))));
    hits = vorrq_u32(hits, vcltq_u32(vsubq_u32(lanes, vdupq_n_u32(0xD800)), vdupq_n_u32(0x800)));
    hits = vorrq_u32(hits, vceqq_u32(vorrq_u32(lanes, vdupq_n_u32(1)), vdupq_n_u32(0xFFFF)));
    return vmaxvq_u32(hits) != 0;
}

/* NEON skips the blocks without a stop and leaves the block holding one to the scalar scan. */
static inline Py_ssize_t c14n_find_text_stop_wide(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    while (index + SBUF_SCAN_STEP <= len && !c14n_block_has_stop(&text[index])) {
        index += SBUF_SCAN_STEP;
    }
    return c14n_find_stop(text, index, len, C14N_TEXT_STOPS);
}

#else

static inline Py_ssize_t c14n_find_text_stop_wide(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    return c14n_find_stop(text, index, len, C14N_TEXT_STOPS);
}

#endif

/* Whether a code point lies between `?` and the surrogates, where c14n text has nothing to escape or drop. The
   subtraction folds both bounds into one unsigned compare. */
static inline int c14n_text_plain(Py_UCS4 character) {
    return character - 0x3F < 0xD800 - 0x3F;
}

/* The first c14n text stop at or after index, or len. A short run tests four plain code points per bounds check and
   gives the rest (spaces, digits and punctuation, and code points from the surrogates up) the exact test. */
static inline Py_ssize_t c14n_find_text_stop(const Py_UCS4 *text, Py_ssize_t index, Py_ssize_t len) {
    if (len - index >= C14N_WIDE_RUN) {
        return c14n_find_text_stop_wide(text, index, len);
    }
    while (index + 4 <= len) {
        if (c14n_text_plain(text[index])) {
            if (!c14n_text_plain(text[index + 1])) {
                index += 1;
            } else if (!c14n_text_plain(text[index + 2])) {
                index += 2;
            } else if (!c14n_text_plain(text[index + 3])) {
                index += 3;
            } else {
                index += 4;
                continue;
            }
        }
        if (c14n_stop(text[index], C14N_TEXT_STOPS)) {
            return index;
        }
        index++;
    }
    return c14n_find_stop(text, index, len, C14N_TEXT_STOPS);
}

/* Emit the replacement for a flagged character. */
static inline void sbuf_put_special(sbuf *out, Py_UCS4 character, int formatter) {
    const char *name = formatter == TH_FMT_NAMED ? th_entity_name(character) : NULL;
    if (name != NULL) {
        sbuf_putc(out, '&');
        sbuf_puts(out, name);
        sbuf_putc(out, ';');
    } else if (character == '&') {
        sbuf_puts(out, "&amp;");
    } else if (character == '<') {
        sbuf_puts(out, "&lt;");
    } else if (character == '>') {
        sbuf_puts(out, "&gt;");
    } else if (character == '"') {
        sbuf_puts(out, "&quot;");
    } else if (character == '\r') {
        sbuf_puts(out, "&#13;");
    } else {
        sbuf_puts(out, "&nbsp;"); /* the only remaining flagged character is U+00A0 */
    }
}

/* Append NAMED-formatter text: bulk-copy each run with no named reference and
   rewrite the rest. NAMED consults a table past 0x7F, so it stays scalar. */
static inline void sbuf_put_named_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        const char *name = NULL;
        while (index < len) {
            if (text[index] < 0x80) {
                if (sbuf_named_special(text[index])) {
                    break;
                }
            } else if ((name = th_entity_name(text[index])) != NULL) {
                break;
            }
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            if (name != NULL) {
                sbuf_putc(out, '&');
                sbuf_puts(out, name);
                sbuf_putc(out, ';');
            } else {
                sbuf_put_special(out, text[index], TH_FMT_NAMED);
            }
            index++;
        }
    }
}

/* Append text under the WHATWG or MINIMAL formatter, bulk-copying each run with
   nothing to escape and rewriting only the specials between. The tree stores
   text as UCS-4, so the clean-run scan (sbuf_find_special) hops SBUF_SCAN_STEP
   code points at a time over the long unescaped spans real documents are made of
   instead of classifying every character. */
static inline void sbuf_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len, int in_attr, int formatter) {
    if (formatter == TH_FMT_NAMED) {
        sbuf_put_named_text(out, text, len);
        return;
    }
    int escape_quote = in_attr; /* every formatter wraps the value in double quotes */
    int escape_nbsp = formatter == TH_FMT_WHATWG;
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t special = sbuf_find_special(text, index, len, escape_quote, escape_nbsp);
        if (special > index) {
            sbuf_put_run(out, &text[index], special - index);
        }
        if (special == len) {
            break;
        }
        sbuf_put_special(out, text[special], formatter);
        index = special + 1;
    }
}

/* XML 1.0 §2.5 forbids `--` and a trailing `-` in a comment; spacing them changes no parsed XML comment. */
static inline void sbuf_put_xml_comment(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    int previous_hyphen = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (xml_char_invalid(character)) {
            continue;
        }
        if (previous_hyphen && character == '-') {
            sbuf_putc(out, ' ');
        }
        sbuf_putc(out, character);
        previous_hyphen = character == '-';
    }
    if (previous_hyphen) {
        sbuf_putc(out, ' ');
    }
}

/* The HTML comment start states end a comment at a leading `>` or `->`. */
static inline void sbuf_put_comment_start(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t first = 0;
    while (first < len && xml_char_invalid(text[first])) {
        first++;
    }
    Py_ssize_t second = first + 1;
    while (second < len && xml_char_invalid(text[second])) {
        second++;
    }
    if (first < len && (text[first] == '>' || (text[first] == '-' && second < len && text[second] == '>'))) {
        sbuf_putc(out, ' ');
    }
}

/* HTML content ends both "<?...>" and "<![CDATA[...]]>" at the first ">", so a node holding one needs another form. */
static inline int ucs4_has_gt(const Py_UCS4 *text, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (text[index] == '>') {
            return 1;
        }
    }
    return 0;
}

static inline void sbuf_put_html_pi(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    if (ucs4_has_gt(text, len)) {
        sbuf_puts(out, "<!--?");
        sbuf_put_xml_comment(out, text, len);
        sbuf_puts(out, "-->");
        return;
    }
    sbuf_puts(out, "<?");
    sbuf_put_ucs4(out, text, len);
    sbuf_putc(out, '>');
}

/* An empty node holds a NULL buffer, and C leaves even NULL + 0 undefined. */
static inline void sbuf_put_ucs4_tail(sbuf *out, const Py_UCS4 *text, Py_ssize_t start, Py_ssize_t len) {
    sbuf_put_ucs4(out, start == 0 ? text : text + start, len - start);
}

/* Whether a run of code points holds a CR. The OR of the comparisons has no branch in it, so the compiler vectorizes
 * it. */
static inline int sbuf_run_has_cr(const Py_UCS4 *text, Py_ssize_t len) {
    Py_UCS4 hit = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        hit |= text[index] == '\r';
    }
    return hit != 0;
}

/* Raw-text content with each CR or CRLF written as LF: a raw-text element has no character reference for a CR, and a
   parse folds a raw CR to LF, so the LF is what a reparse reads back. */
static inline void sbuf_put_rawtext(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    if (!sbuf_run_has_cr(text, len)) {
        sbuf_put_ucs4_tail(out, text, 0, len);
        return;
    }
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (text[index] != '\r') {
            continue;
        }
        sbuf_put_ucs4(out, text + start, index - start);
        sbuf_putc(out, '\n');
        if (index + 1 < len && text[index + 1] == '\n') {
            index++;
        }
        start = index + 1;
    }
    sbuf_put_ucs4_tail(out, text, start, len);
}

/* A raw-text child's text. A parsed text node is a span of the normalized input (the HTML tree builder and the XML
   parser both fold CR before a span is taken), so it holds no CR and only owned text is scanned. */
static inline void sbuf_put_rawtext_node(sbuf *out, th_tree *tree, th_node *node) {
    int parsed = text_is_span(node);
    Py_UCS4 *text = need_text(tree, node);
    if (parsed) {
        sbuf_put_ucs4(out, text, node->text_len);
    } else {
        sbuf_put_rawtext(out, text, node->text_len);
    }
}

/* The HTML tokenizer also ends a comment at `-->` or `--!>`; parsed data does not hold these, DOM-built data can. */
static inline void sbuf_put_comment(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    sbuf_put_comment_start(out, text, len);
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 1; index + 1 < len; index++) {
        if (text[index] != '-' || text[index - 1] != '-') {
            continue;
        }
        Py_UCS4 next = text[index + 1];
        if (next == '>' || (next == '!' && index + 2 < len && text[index + 2] == '>')) {
            sbuf_put_ucs4(out, text + start, index + 1 - start);
            sbuf_putc(out, ' ');
            start = index + 1;
        }
    }
    sbuf_put_ucs4_tail(out, text, start, len);
}

/* Dropped characters leave a `?` and a `>` adjacent in the output, so the `>` is looked for past them. */
static inline int xml_pi_gt_follows(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t index) {
    while (index < len && xml_char_invalid(text[index])) {
        index++;
    }
    return index < len && text[index] == '>';
}

/* A `?>` in the data would end the instruction, and XML forbids C0 controls. */
static inline void sbuf_put_xml_pi_data(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t start = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (xml_char_invalid(text[index])) {
            sbuf_put_ucs4(out, text + start, index - start);
            start = index + 1;
        } else if (text[index] == '?' && xml_pi_gt_follows(text, len, index + 1)) {
            sbuf_put_ucs4(out, text + start, index + 1 - start);
            sbuf_putc(out, ' ');
            start = index + 1;
        }
    }
    sbuf_put_ucs4_tail(out, text, start, len);
}

/* `]]>` cannot appear inside a CDATA section, so the section closes after `]]` and a new one starts at `>`. The
   scan skips the characters XML forbids, since dropping one can bring a `]]` and a `>` together. */
static inline void sbuf_put_xml_cdata(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    sbuf_puts(out, "<![CDATA[");
    Py_ssize_t start = 0;
    int closing_brackets = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 character = text[index];
        if (xml_char_invalid(character)) {
            sbuf_put_ucs4(out, text + start, index - start);
            start = index + 1;
            continue;
        }
        if (character == '>' && closing_brackets >= 2) {
            sbuf_put_ucs4(out, text + start, index - start);
            sbuf_puts(out, "]]><![CDATA[");
            start = index;
        }
        closing_brackets = character == ']' ? closing_brackets + 1 : 0;
    }
    sbuf_put_ucs4_tail(out, text, start, len);
    sbuf_puts(out, "]]>");
}

/* An element whose text children serialize literally rather than escaped: the
   WHATWG literal set is style/script/xmp/iframe/noembed/noframes/plaintext.
   noscript carries the rawtext flag for tokenization but is raw-text only when the
   tree was parsed with the scripting flag on; otherwise its content is escaped. */
static inline int is_rawtext_element(const th_node *node, int scripting) {
    /* the caller passes only element nodes, so this skips the node-type check */
    if (node->ns != TH_NS_HTML) {
        return 0; /* foreign elements (svg, mathml) never carry html raw-text content */
    }
    if (node->atom == TH_TAG_SCRIPT || node->atom == TH_TAG_PLAINTEXT) {
        return 1;
    }
    return (node->tag_flags & TH_TAG_RAWTEXT) && (node->atom != TH_TAG_NOSCRIPT || scripting);
}

/* A raw-text element's first child that is not text, or NULL: a parse gives such an element only text children, so
   anything else came from the DOM API. */
static inline th_node *rawtext_markup_child(const th_node *node) {
    th_node *child = node->first_child;
    while (child != NULL && (child->type == TH_NODE_TEXT || child->type == TH_NODE_CDATA)) {
        child = child->next_sibling;
    }
    return child;
}

/* Append the parsed source's code points [start, end), widening the input to UCS-4. */
void sbuf_put_source(sbuf *out, th_tree *tree, Py_ssize_t start, Py_ssize_t end);

/* Append a raw-text element's content from its first markup child on, as the compact layout writes it, dropping
   comments when the minifier strips them. */
void ser_put_rawtext_markup(sbuf *out, th_tree *tree, th_node *element, th_node *from, const th_serialize_opts *opts,
                            int strip_comments);

/* Write a doctype in HTML syntax with the public and system identifiers that choose the document mode, or the
   shortest malformed spelling that keeps a forced quirks mode; an identifier HTML cannot spell is left out. */
void ser_put_html_doctype(sbuf *out, th_tree *tree, th_node *node);

/* Whether a reparse closes node where the tree does once its end tag is left out: the WHATWG optional-tag rule on the
   tree as it stands, for the minifier and for the lossless serializer. formatting_depth counts the formatting elements
   open around node, trailing_formatting says one lies on node's last-child path, quirks is the mode the output
   reparses in. */
int ser_end_tag_omissible(th_tree *tree, th_node *node, int strip_comments, int formatting_depth,
                          int trailing_formatting, int quirks);
/* Write the end tag a script needs ahead of its own when its content, which the caller wrote at out[content..], leaves
   a re-parse in the script data double escaped state: after a "<!--" that no "-->" ended and, inside it, a "<script"
   start tag that no "</script" end tag ended. There the next "</script>" only ends the double escape, so one end tag
   does not close the element. A parse makes such text only in a script the input ended inside, but the walk is in tree
   order, where the "after head" and "after body" rules and foster parenting put that script ahead of a comment or
   style the source had before it: left open it would run on to a "</script>" inside one and turn the rest of that
   node into markup. Writes nothing for an element that is not a script, and callers skip a script nested in another
   raw-text element, whose end tag is text there. verbatim says out[content..] is the element's text unchanged: a
   script whose content is one Text node then keeps a clean result on that node (script_text_closes), so serializing
   the same tree again skips the scan. The parser sets that bit on the text of a script its end tag closed. */
void ser_script_end_scan(sbuf *out, th_node *element, Py_ssize_t content, th_node *alone);
static inline void ser_end_script_double_escape(sbuf *out, th_node *element, Py_ssize_t content, int verbatim) {
    if (element->atom != TH_TAG_SCRIPT) {
        return;
    }
    th_node *text = element->first_child;
    th_node *alone = verbatim && text != NULL && text->type == TH_NODE_TEXT && text->next_sibling == NULL ? text : NULL;
    if (alone == NULL || !alone->script_text_closes) {
        ser_script_end_scan(out, element, content, alone);
    }
}

/* The WHATWG fragment-serialization void set extends the parser's void elements
   with `frame`: it is emitted as a start tag only, never an end tag. `frame` is
   absent from is_void_atom because, unlike a true void element, it is a normal
   open element during parsing (in frameset mode it is inserted childless), so
   widening is_void_atom would wrongly make a stray in-body <frame> swallow no
   following content. */
static inline int is_serialize_void_atom(uint16_t atom) {
    return atom == TH_TAG_FRAME || is_void_atom(atom);
}

/* The doctype name length: build_doctype_text stores "name" optionally followed
   by ` "public" "system"`, but HTML serialization emits only the name. */
static inline Py_ssize_t doctype_name_len(const th_node *node) {
    Py_ssize_t index = 0;
    while (index < node->text_len && node->text[index] != ' ') {
        index++;
    }
    return index;
}

/* A pre/textarea/listing element whose first child is a text node beginning with
   a newline needs an extra leading newline on output: the parser drops one such
   newline when reading, so re-emitting it keeps the round trip faithful. */
static inline int ser_leading_newline_at(th_tree *tree, th_node *node, int strip_comments) {
    if (node->ns != TH_NS_HTML) {
        return 0;
    }
    if (node->atom != TH_TAG_PRE && node->atom != TH_TAG_TEXTAREA && node->atom != TH_TAG_LISTING) {
        return 0;
    }
    th_node *first = node->first_child;
    while (first != NULL && strip_comments && first->type == TH_NODE_COMMENT) {
        first = first->next_sibling;
    }
    if (first == NULL || first->type != TH_NODE_TEXT || first->text_len == 0) {
        return 0;
    }
    const Py_UCS4 *text = need_text(tree, first);
    return text != NULL && text[0] == '\n'; /* GCOVR_EXCL_BR_LINE: NULL is a lazy text OOM the walk reports */
}

static inline int ser_needs_leading_newline(th_tree *tree, th_node *node) {
    return ser_leading_newline_at(tree, node, 0);
}

/* ASCII case-insensitive compare of a UCS-4 attribute value against a lowercase
   literal: the value matches when it has the same length and folds to the same
   bytes (only A-Z fold, the only case mapping the labels here need). */
static inline int ser_value_iequals(const Py_UCS4 *value, Py_ssize_t len, const char *ascii) {
    Py_ssize_t index = 0;
    for (; index < len && ascii[index] != '\0'; index++) {
        Py_UCS4 character = value[index];
        if (character >= 'A' && character <= 'Z') {
            character += 'a' - 'A';
        }
        if (character != (Py_UCS4)(unsigned char)ascii[index]) {
            return 0;
        }
    }
    return index == len && ascii[index] == '\0';
}

/* How a <meta> element declares the document encoding, for the meta_charset
   normalizer: 1 a `charset` attribute, 2 an http-equiv="content-type" with a
   `content` attribute, 0 neither (or not an HTML <meta>). */
static inline int ser_meta_charset_kind(th_tree *tree, const th_node *node) {
    if (node->ns != TH_NS_HTML || node->atom != TH_TAG_META) {
        return 0;
    }
    if (th_node_attr_find(tree, (th_node *)node, "charset", 7) >= 0) {
        return 1;
    }
    Py_ssize_t equiv = th_node_attr_find(tree, (th_node *)node, "http-equiv", 10);
    if (equiv >= 0 && th_node_attr_find(tree, (th_node *)node, "content", 7) >= 0 &&
        ser_value_iequals(node->attrs[equiv].value, node->attrs[equiv].value_len, "content-type")) {
        return 2;
    }
    return 0;
}

/* Whether head already declares a charset through a direct <meta> child, so the
   meta_charset option normalizes that one in place instead of injecting another. */
static inline int ser_head_has_charset_meta(th_tree *tree, const th_node *head) {
    for (th_node *child = head->first_child; child != NULL; child = child->next_sibling) {
        if (child->type == TH_NODE_ELEMENT && ser_meta_charset_kind(tree, child) != 0) {
            return 1;
        }
    }
    return 0;
}

/* Write the declared label. When no label names the codec, the placeholder is not written: the caller refuses the
   output instead, so a codec is only refused for a document that would declare it. */
static inline void ser_put_label(sbuf *out, const th_serialize_opts *opts) {
    if (opts->charset_missing != NULL) {
        *opts->charset_missing = 1;
        return;
    }
    if (opts->charset_written != NULL) {
        *opts->charset_written = 1;
    }
    sbuf_put_utf8(out, opts->charset, opts->charset_len);
}

/* Emit a fresh charset declaration, <meta charset="LABEL">. */
static inline void ser_emit_meta_charset(sbuf *out, const th_serialize_opts *opts) {
    sbuf_puts(out, "<meta charset=\"");
    ser_put_label(out, opts);
    sbuf_puts(out, "\">");
}

/* Lexicographic compare of two interned attribute names by bytes, the shorter
   name ordering first on a shared prefix (the html5lib alphabetical-attributes
   order over the stored, parser-lowercased name). */
static inline int ser_name_cmp(const char *left, Py_ssize_t left_len, const char *right, Py_ssize_t right_len) {
    Py_ssize_t min_len = left_len < right_len ? left_len : right_len;
    int order = memcmp(left, right, (size_t)min_len);
    if (order != 0) {
        return order;
    }
    return left_len < right_len ? -1 : left_len > right_len;
}

/* Small attribute sets avoid allocating and populating separate sort keys. */
static inline void ser_sort_order(th_tree *tree, const th_node *node, Py_ssize_t *order, Py_ssize_t count) {
    for (Py_ssize_t index = 0; index < count; index++) {
        order[index] = index;
    }
    for (Py_ssize_t index = 1; index < count; index++) {
        Py_ssize_t key = order[index];
        Py_ssize_t key_len;
        const char *key_name = th_attr_name(tree, node->attrs[key].name_atom, &key_len);
        Py_ssize_t prev = index - 1;
        while (prev >= 0) {
            Py_ssize_t prev_len;
            const char *prev_name = th_attr_name(tree, node->attrs[order[prev]].name_atom, &prev_len);
            if (ser_name_cmp(prev_name, prev_len, key_name, key_len) <= 0) {
                break;
            }
            order[prev + 1] = order[prev];
            prev--;
        }
        order[prev + 1] = key;
    }
}

typedef struct {
    const char *name;
    Py_ssize_t name_len;
    Py_ssize_t index;
} ser_attr_key;

static inline int ser_attr_key_cmp(const void *left_ptr, const void *right_ptr) {
    const ser_attr_key *left = left_ptr;
    const ser_attr_key *right = right_ptr;
    int order = ser_name_cmp(left->name, left->name_len, right->name, right->name_len);
    /* Original positions preserve equal-name order without shared comparator state. */
    return order + (order == 0) * ((left->index > right->index) - (left->index < right->index));
}

static inline int ser_sort_wide(th_tree *tree, const th_node *node, Py_ssize_t *order) {
    ser_attr_key *keys = PyMem_Malloc((size_t)node->attr_count * sizeof(*keys));
    if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int sorted = 1;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        keys[index].name = th_attr_name(tree, node->attrs[index].name_atom, &keys[index].name_len);
        keys[index].index = index;
        if (sorted && index > 0 &&
            ser_name_cmp(keys[index - 1].name, keys[index - 1].name_len, keys[index].name, keys[index].name_len) > 0) {
            sorted = 0;
        }
    }
    if (!sorted) {
        qsort(keys, (size_t)node->attr_count, sizeof(*keys), ser_attr_key_cmp);
    }
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        order[index] = keys[index].index;
    }
    PyMem_Free(keys);
    return 0;
}

/* The order to emit node's attributes in: NULL means source order (sorting off, or
   fewer than two attributes), otherwise a filled index array -- the caller's stack
   buffer for up to MAX_SORTED_ATTRS attributes, else a heap block freed through
   ser_attr_order_free. */
static inline Py_ssize_t *ser_attr_order(th_tree *tree, const th_node *node, int sort, Py_ssize_t *stack) {
    if (!sort || node->attr_count < 2) {
        return NULL;
    }
    Py_ssize_t *order =
        node->attr_count <= MAX_SORTED_ATTRS ? stack : PyMem_Malloc((size_t)node->attr_count * sizeof(Py_ssize_t));
    if (order == NULL) { /* GCOVR_EXCL_BR_LINE: only the heap path can fail, and OOM is unforceable */
        return NULL;     /* GCOVR_EXCL_LINE: degrade to source order on allocation failure */
    }
    if (node->attr_count <= MAX_SORTED_ATTRS) {
        ser_sort_order(tree, node, order, node->attr_count);
    } else if (ser_sort_wide(tree, node, order) < 0) { /* GCOVR_EXCL_BR_LINE: sort-key allocation failure */
        PyMem_Free(order); /* GCOVR_EXCL_LINE: preserve source order when allocation fails */
        return NULL;       /* GCOVR_EXCL_LINE */
    }
    return order;
}

static inline void ser_attr_order_free(Py_ssize_t *order, const Py_ssize_t *stack) {
    if (order != NULL && order != stack) {
        PyMem_Free(order);
    }
}

/* Under meta_charset, rewrite a charset meta's declaration to the target label:
   charset="LABEL" (kind 1) or content="text/html; charset=LABEL" (kind 2). Returns
   1 when it emitted the attribute, 0 when the caller should emit the stored value.
   The element is an HTML <meta>, whose attribute names the parser has lowercased,
   so a byte compare against the lowercase spelling is exact. */
static inline int ser_put_charset_value(sbuf *out, const th_serialize_opts *opts, int kind, const char *name,
                                        Py_ssize_t name_len) {
    if (kind == 1 && name_len == 7 && memcmp(name, "charset", 7) == 0) {
        sbuf_puts(out, "=\"");
        ser_put_label(out, opts);
        sbuf_putc(out, '"');
        return 1;
    }
    if (kind == 2 && name_len == 7 && memcmp(name, "content", 7) == 0) {
        sbuf_puts(out, "=\"text/html; charset=");
        ser_put_label(out, opts);
        sbuf_putc(out, '"');
        return 1;
    }
    return 0;
}

/* When meta_charset is on and node is a <head> that declares no charset of its own,
   emit the <meta charset> as its first child so the output names the encoding. The
   atom guard means this is a no-op for every element but the document head. */
static inline void ser_inject_head_meta(sbuf *out, th_tree *tree, const th_node *node, const th_serialize_opts *opts) {
    if (opts->inject_meta && node->ns == TH_NS_HTML && node->atom == TH_TAG_HEAD &&
        !ser_head_has_charset_meta(tree, node)) {
        ser_emit_meta_charset(out, opts);
    }
}

/* Whether ser_xml_ns_decls injected the foreign-root default namespace for a start tag, so
   the attribute writer drops a stored xmlns that would duplicate it (a second xmlns on the
   same tag is ill-formed). The xlink binding needs no such flag: it is injected only when
   the element carries no stored xmlns:xlink, so there is never a duplicate to drop. */
typedef struct {
    int default_ns;
} xml_ns_emitted;

static inline int ser_xml_ns_name(const char *name, Py_ssize_t len) {
    return (len == 5 && memcmp(name, "xmlns", 5) == 0) || (len > 6 && memcmp(name, "xmlns:", 6) == 0);
}

int th_xml_attr_name_wellformed_utf8(const char *name, Py_ssize_t name_len);

/* Whether an element name is an XML QName: a Name with at most one colon, neither first nor last. The XML output
   and the canonical form leave out an element whose name is not and write its children in its place. */
int th_xml_element_name_wellformed(const Py_UCS4 *name, Py_ssize_t len);

/* th_xml_element_name_wellformed of element's name, kept on the element so a tree canonicalized again, or a name
   repeated across it, is not scanned each time. */
static inline int ser_xml_element_name_wellformed(th_node *element) {
    if (element->xml_name_wellformed == 0) {
        element->xml_name_wellformed = th_xml_element_name_wellformed(element->text, element->text_len) ? 1 : 2;
    }
    return element->xml_name_wellformed == 1;
}

static inline int ser_xml_ns_uri_equals(const Py_UCS4 *value, Py_ssize_t len, const char *uri) {
    Py_ssize_t uri_len = (Py_ssize_t)strlen(uri);
    if (len != uri_len) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (value[index] != (Py_UCS4)uri[index]) {
            return 0;
        }
    }
    return 1;
}

/* XML Namespaces 1.0 forbids empty prefixed bindings and reserves the xml and xmlns URIs. */
static inline int ser_xml_ns_binding_valid(const char *name, Py_ssize_t name_len, const Py_UCS4 *value,
                                           Py_ssize_t value_len) {
    int is_default = name_len == 5;
    if (!is_default && (value_len == 0 || !th_xml_attr_name_wellformed_utf8(name + 6, name_len - 6))) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < value_len; index++) {
        if (xml_char_invalid(value[index])) {
            return 0;
        }
    }
    int xml_uri = ser_xml_ns_uri_equals(value, value_len, "http://www.w3.org/XML/1998/namespace");
    int xmlns_uri = ser_xml_ns_uri_equals(value, value_len, "http://www.w3.org/2000/xmlns/");
    if (is_default) {
        return !xml_uri && !xmlns_uri;
    }
    if (xmlns_uri || (name_len == 11 && memcmp(name + 6, "xmlns", 5) == 0)) {
        return 0;
    }
    if (name_len == 9 && memcmp(name + 6, "xml", 3) == 0) {
        return xml_uri;
    }
    return !xml_uri;
}

/* A namespace declaration in scope where a subtree is cut out of an XML tree. */
typedef struct {
    const char *prefix; /* the UTF-8 bytes after "xmlns:" in the stored name; none for the default namespace */
    Py_ssize_t prefix_len;
    const th_node_attr *attr;
    Py_ssize_t order;      /* nearest ancestor first, then attribute position: the order a start tag copies them in */
    Py_ssize_t redeclared; /* declarations in force where the start tag is written that hide this one */
    int used;              /* a wide scope: already listed for the start tag being written */
} ser_xml_ns_binding;

/* Up to this many declarations are kept in the scope itself, in the order a start tag copies them, and each one is
   searched for in the subtree on its own, stopping at its first use. A wider scope is allocated, sorted by prefix,
   and every name of the subtree is looked up in it once, so the work stays linear in the subtree. */
#define SER_XML_NS_INLINE 8

/* The bindings a subtree written on its own inherits. Every child of the boundary inherits the same ones, so they are
   gathered once per serialization (per chunk of a streamed one) and each child looks the names of its subtree up in
   them. Climbing the ancestors for every child, and searching the subtree for every declaration met on the way,
   multiplies the three sizes. */
struct th_xml_ns_scope {
    const th_node *boundary;        /* parent of the subtree roots; NULL above a root that has none */
    ser_xml_ns_binding *bindings;   /* the nearest declaration of each name */
    ser_xml_ns_binding **used;      /* a wide scope: what the start tag being written copies */
    ser_xml_ns_binding *default_ns; /* the binding of the default namespace, or NULL */
    Py_ssize_t count;
    Py_ssize_t used_count;
    Py_ssize_t hidden; /* a wide scope: declarations on open elements of the subtree being read that hide a binding */
    Py_ssize_t walked; /* ancestors and attributes the gather visited, its cost; 0 until it has run */
    int sorted;        /* bindings is allocated and sorted by prefix, past SER_XML_NS_INLINE */
    ser_xml_ns_binding inline_bindings[SER_XML_NS_INLINE];
};

static inline th_xml_ns_scope *ser_xml_ns_scope_init(th_xml_ns_scope *scope, const th_node *boundary) {
    scope->boundary = boundary;
    scope->default_ns = NULL;
    scope->count = 0;
    scope->used_count = 0;
    scope->hidden = 0;
    scope->walked = 0;
    scope->sorted = 0;
    return scope;
}

static inline void ser_xml_ns_scope_free(th_xml_ns_scope *scope) {
    if (scope->sorted) {
        PyMem_Free(scope->bindings);
    }
}

static inline int ser_xml_ns_binding_cmp(const void *left_ptr, const void *right_ptr) {
    const ser_xml_ns_binding *left = left_ptr;
    const ser_xml_ns_binding *right = right_ptr;
    int order = ser_name_cmp(left->prefix, left->prefix_len, right->prefix, right->prefix_len);
    /* The nearest declaration of a prefix sorts first, which is the one that stays in scope. */
    return order + (order == 0) * ((left->order > right->order) - (left->order < right->order));
}

static inline int ser_xml_ns_used_cmp(const void *left_ptr, const void *right_ptr) {
    const ser_xml_ns_binding *left = *(ser_xml_ns_binding *const *)left_ptr;
    const ser_xml_ns_binding *right = *(ser_xml_ns_binding *const *)right_ptr;
    return (left->order > right->order) - (left->order < right->order);
}

/* The code point of the stored, well-formed UTF-8 at bytes[*position], moving *position past it. */
static inline Py_UCS4 ser_utf8_next(const char *bytes, Py_ssize_t *position) {
    const unsigned char *lead = (const unsigned char *)bytes + *position;
    if (lead[0] >= 0xF0) {
        *position += 4;
        return (Py_UCS4)(lead[0] & 0x07) << 18 | (Py_UCS4)(lead[1] & 0x3F) << 12 | (Py_UCS4)(lead[2] & 0x3F) << 6 |
               (lead[3] & 0x3F);
    }
    if (lead[0] >= 0xE0) {
        *position += 3;
        return (Py_UCS4)(lead[0] & 0x0F) << 12 | (Py_UCS4)(lead[1] & 0x3F) << 6 | (lead[2] & 0x3F);
    }
    if (lead[0] >= 0xC0) {
        *position += 2;
        return (Py_UCS4)(lead[0] & 0x1F) << 6 | (lead[1] & 0x3F);
    }
    *position += 1;
    return lead[0];
}

/* Order a declared prefix against one spelled in code points, the start of an element name, the way ser_name_cmp
   orders two declared prefixes: UTF-8 bytes compare in code point order. */
static inline int ser_xml_prefix_cmp(const char *declared, Py_ssize_t declared_len, const Py_UCS4 *prefix,
                                     Py_ssize_t prefix_len) {
    Py_ssize_t prefix_position = 0;
    for (Py_ssize_t position = 0; position < declared_len;) {
        Py_UCS4 character = ser_utf8_next(declared, &position);
        if (prefix_position == prefix_len || prefix[prefix_position] < character) {
            return 1;
        }
        if (prefix[prefix_position] > character) {
            return -1;
        }
        prefix_position++;
    }
    return prefix_position < prefix_len ? -1 : 0;
}

/* The binding in scope for a prefix, given as the UTF-8 bytes of an attribute name or, when those are NULL, as the
   code points of an element name; NULL when no ancestor declares it. A sorted scope is bisected, an inline one read
   in order. */
static inline ser_xml_ns_binding *ser_xml_ns_find(const th_xml_ns_scope *scope, const char *utf8, const Py_UCS4 *ucs4,
                                                  Py_ssize_t len) {
    Py_ssize_t low = 0;
    Py_ssize_t high = scope->count;
    while (low < high) {
        Py_ssize_t middle = scope->sorted ? low + (high - low) / 2 : low;
        ser_xml_ns_binding *binding = &scope->bindings[middle];
        int order = utf8 != NULL ? ser_name_cmp(binding->prefix, binding->prefix_len, utf8, len)
                                 : ser_xml_prefix_cmp(binding->prefix, binding->prefix_len, ucs4, len);
        if (order == 0) {
            return binding;
        }
        if (order > 0 && scope->sorted) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    return NULL;
}

/* The prefix a namespace declaration binds, or NULL when name declares none a name of the subtree could use: a
   prefix holding a colon matches nothing, since a name's prefix ends at its first colon. */
static inline const char *ser_xml_ns_declared_prefix(const char *name, Py_ssize_t name_len, Py_ssize_t *prefix_len) {
    if (!ser_xml_ns_name(name, name_len)) {
        return NULL;
    }
    *prefix_len = name_len == 5 ? 0 : name_len - 6;
    const char *prefix = name + name_len - *prefix_len;
    return memchr(prefix, ':', (size_t)*prefix_len) == NULL ? prefix : NULL;
}

static inline void ser_xml_ns_add(th_xml_ns_scope *scope, const char *prefix, Py_ssize_t prefix_len,
                                  const th_node_attr *attr) {
    ser_xml_ns_binding *binding = &scope->bindings[scope->count];
    binding->prefix = prefix;
    binding->prefix_len = prefix_len;
    binding->attr = attr;
    binding->order = scope->count++;
    binding->redeclared = 0;
    binding->used = 0;
}

/* Collect the declarations on the boundary and above it, keeping the nearest of each name: that one hides the rest.
   The first pass fills the inline bindings; only a scope that outgrows them takes a second pass into an allocation. */
static inline void ser_xml_ns_gather(sbuf *out, th_tree *tree, th_xml_ns_scope *scope) {
    scope->bindings = scope->inline_bindings;
    Py_ssize_t total = 0;
    for (const th_node *ancestor = scope->boundary; ancestor != NULL; ancestor = ancestor->parent) {
        TH_WORK_STEP();
        scope->walked++;
        if (ancestor->type != TH_NODE_ELEMENT) {
            continue;
        }
        scope->walked += ancestor->attr_count;
        for (Py_ssize_t position = 0; position < ancestor->attr_count; position++) {
            Py_ssize_t name_len;
            Py_ssize_t prefix_len;
            const char *name = th_attr_name(tree, ancestor->attrs[position].name_atom, &name_len);
            const char *prefix = ser_xml_ns_declared_prefix(name, name_len, &prefix_len);
            if (prefix == NULL) {
                continue;
            }
            total++;
            if (scope->count < SER_XML_NS_INLINE && ser_xml_ns_find(scope, prefix, NULL, prefix_len) == NULL) {
                ser_xml_ns_add(scope, prefix, prefix_len, &ancestor->attrs[position]);
            }
        }
    }
    if (total > SER_XML_NS_INLINE) {
        ser_xml_ns_binding *bindings =
            PyMem_Malloc((size_t)total * (sizeof(ser_xml_ns_binding) + sizeof(ser_xml_ns_binding *)));
        if (bindings == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            scope->count = 0;   /* GCOVR_EXCL_LINE: allocation-failure path */
            out->failed = 1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            return;             /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        scope->bindings = bindings;
        scope->used = (ser_xml_ns_binding **)(bindings + total);
        scope->sorted = 1;
        scope->count = 0;
        for (const th_node *ancestor = scope->boundary; ancestor != NULL; ancestor = ancestor->parent) {
            for (Py_ssize_t position = 0; ancestor->type == TH_NODE_ELEMENT && position < ancestor->attr_count;
                 position++) {
                Py_ssize_t name_len;
                Py_ssize_t prefix_len;
                const char *name = th_attr_name(tree, ancestor->attrs[position].name_atom, &name_len);
                const char *prefix = ser_xml_ns_declared_prefix(name, name_len, &prefix_len);
                if (prefix != NULL) {
                    ser_xml_ns_add(scope, prefix, prefix_len, &ancestor->attrs[position]);
                }
            }
        }
        qsort(bindings, (size_t)total, sizeof(ser_xml_ns_binding), ser_xml_ns_binding_cmp);
        Py_ssize_t kept = 1;
        for (Py_ssize_t index = 1; index < total; index++) {
            if (ser_name_cmp(bindings[kept - 1].prefix, bindings[kept - 1].prefix_len, bindings[index].prefix,
                             bindings[index].prefix_len) != 0) {
                bindings[kept++] = bindings[index];
            }
        }
        scope->count = kept;
    }
    /* Looked up as an empty element-name prefix: through the byte compare, gcc cannot tell a stored prefix length is
       not negative and fails -Wstringop-overread. */
    scope->default_ns = ser_xml_ns_find(scope, NULL, NULL, 0);
}

/* Whether an element name, spelled in code points, starts with prefix and a colon. */
static inline int ser_xml_qname_uses(const Py_UCS4 *name, Py_ssize_t name_len, const char *prefix,
                                     Py_ssize_t prefix_len) {
    Py_ssize_t name_position = 0;
    for (Py_ssize_t position = 0; position < prefix_len; name_position++) {
        if (name_position == name_len || name[name_position] != ser_utf8_next(prefix, &position)) {
            return 0;
        }
    }
    return name_position < name_len && name[name_position] == ':';
}

static inline int ser_xml_name_unprefixed(const th_node *element) {
    for (Py_ssize_t position = 0; position < element->text_len; position++) {
        if (element->text[position] == ':') {
            return 0;
        }
    }
    return 1;
}

/* Whether element's own name or one of its attribute names uses an inherited binding: an attribute never takes the
   default namespace. */
static inline int ser_xml_ns_element_uses(th_tree *tree, const th_node *element, const ser_xml_ns_binding *binding) {
    Py_ssize_t prefix_len = binding->prefix_len;
    if (prefix_len == 0) {
        return ser_xml_name_unprefixed(element);
    }
    if (ser_xml_qname_uses(element->text, element->text_len, binding->prefix, prefix_len)) {
        return 1;
    }
    for (Py_ssize_t position = 0; position < element->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, element->attrs[position].name_atom, &name_len);
        if (name_len > prefix_len && name[prefix_len] == ':' &&
            memcmp(name, binding->prefix, (size_t)prefix_len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* The nearest of node and its ancestors up to root that declares the name of an inherited binding again, or NULL
   when none does and the binding is in force at node. */
static inline const th_node *ser_xml_ns_redeclared_at(th_tree *tree, const th_node *node, const th_node *root,
                                                      const ser_xml_ns_binding *binding) {
    Py_ssize_t prefix_len = binding->prefix_len;
    for (const th_node *cursor = node;; cursor = cursor->parent) {
        TH_WORK_STEP();
        for (Py_ssize_t position = 0; position < cursor->attr_count; position++) {
            Py_ssize_t name_len;
            const char *name = th_attr_name(tree, cursor->attrs[position].name_atom, &name_len);
            if (name_len == prefix_len + (prefix_len == 0 ? 5 : 6) && ser_xml_ns_name(name, name_len) &&
                memcmp(name + name_len - prefix_len, binding->prefix, (size_t)prefix_len) == 0) {
                return cursor;
            }
        }
        if (cursor == root) {
            return NULL;
        }
    }
}

/* Whether the subtree of root uses an inherited binding where no element of it declares the name again. The search
   stops at the first use in force. A use under a declaration of the name climbs to that declaration and skips the rest
   of its subtree, so no element is climbed through twice. */
static inline int ser_xml_ns_used_below(th_tree *tree, const th_node *root, const ser_xml_ns_binding *binding) {
    for (const th_node *node = root;;) {
        TH_WORK_STEP();
        if (node->type == TH_NODE_ELEMENT && ser_xml_ns_element_uses(tree, node, binding)) {
            node = ser_xml_ns_redeclared_at(tree, node, root, binding);
            if (node == NULL) {
                return 1;
            }
        } else if (node->first_child != NULL) {
            node = node->first_child;
            continue;
        }
        while (node != root && node->next_sibling == NULL) {
            node = node->parent;
        }
        if (node == root) {
            return 0;
        }
        node = node->next_sibling;
    }
}

/* In a wide scope, entering an element of the subtree that declares a name hides the inherited binding from it and
   its descendants (delta 1); leaving it brings the binding back (delta -1). Returns whether an attribute name has a
   colon. */
static inline int ser_xml_ns_redeclare(th_tree *tree, th_xml_ns_scope *scope, const th_node *node, Py_ssize_t delta) {
    int prefixed = 0;
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        Py_ssize_t name_len;
        Py_ssize_t prefix_len;
        const char *name = th_attr_name(tree, node->attrs[position].name_atom, &name_len);
        prefixed |= memchr(name, ':', (size_t)name_len) != NULL;
        const char *prefix = ser_xml_ns_declared_prefix(name, name_len, &prefix_len);
        ser_xml_ns_binding *binding = prefix != NULL ? ser_xml_ns_find(scope, prefix, NULL, prefix_len) : NULL;
        if (binding != NULL) {
            binding->redeclared += delta;
            scope->hidden += delta;
        }
    }
    return prefixed;
}

static inline void ser_xml_ns_use(th_xml_ns_scope *scope, ser_xml_ns_binding *binding) {
    if (binding != NULL && binding->redeclared == 0 && !binding->used) {
        binding->used = 1;
        scope->used[scope->used_count++] = binding;
    }
}

/* Enter an element of the subtree in a wide scope: hide the bindings it declares again, then list those its names
   rely on, the prefix of its own name, or the default namespace when that has none, and the prefix of each attribute
   name. */
static inline void ser_xml_ns_enter(th_tree *tree, th_xml_ns_scope *scope, const th_node *node) {
    if (node->type != TH_NODE_ELEMENT) {
        return;
    }
    int prefixed = ser_xml_ns_redeclare(tree, scope, node, 1);
    Py_ssize_t colon = 0;
    while (colon < node->text_len && node->text[colon] != ':') {
        colon++;
    }
    if (colon == node->text_len) {
        ser_xml_ns_use(scope, scope->default_ns);
    } else if (colon > 0) {
        ser_xml_ns_use(scope, ser_xml_ns_find(scope, NULL, node->text, colon));
    }
    for (Py_ssize_t position = 0; prefixed && position < node->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[position].name_atom, &name_len);
        const char *prefix_end = memchr(name, ':', (size_t)name_len);
        if (prefix_end != NULL && prefix_end != name) {
            ser_xml_ns_use(scope, ser_xml_ns_find(scope, name, NULL, prefix_end - name));
        }
    }
}

/* Leaving an element brings back what it hid; with nothing hidden there is nothing to look up. */
static inline void ser_xml_ns_leave(th_tree *tree, th_xml_ns_scope *scope, const th_node *node) {
    if (scope->hidden != 0 && node->type == TH_NODE_ELEMENT) {
        ser_xml_ns_redeclare(tree, scope, node, -1);
    }
}

/* List in scope->used, in copy order, the bindings of a wide scope node's subtree uses: one walk looks every name
   up, and stops once every binding is listed. */
static inline void ser_xml_ns_list_used(th_tree *tree, th_xml_ns_scope *scope, const th_node *node) {
    for (const th_node *cursor = node;;) {
        TH_WORK_STEP();
        ser_xml_ns_enter(tree, scope, cursor);
        int complete = scope->used_count == scope->count;
        if (!complete && cursor->first_child != NULL) {
            cursor = cursor->first_child;
            continue;
        }
        for (;;) {
            ser_xml_ns_leave(tree, scope, cursor);
            if (cursor == node || (!complete && cursor->next_sibling != NULL)) {
                break;
            }
            cursor = cursor->parent;
        }
        if (cursor == node) {
            break;
        }
        cursor = cursor->next_sibling;
    }
    qsort(scope->used, (size_t)scope->used_count, sizeof(ser_xml_ns_binding *), ser_xml_ns_used_cmp);
}

/* The declarations of the left-out run a subtree root stands in, unwrap->decls[chain] on, hide the bindings they
   declare again (delta 1) until the root's start tag is written (delta -1). The run's own copies are written by
   ser_xml_unwrapped_ns_decls, which skips the ones that are not valid bindings. */
static inline void ser_xml_ns_hide_run(th_tree *tree, th_xml_ns_scope *scope, const th_xml_unwrap *unwrap,
                                       Py_ssize_t chain, Py_ssize_t delta) {
    for (Py_ssize_t index = chain; index < unwrap->decls_len; index++) {
        const th_node_attr *attr = unwrap->decls[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        Py_ssize_t prefix_len = name_len == 5 ? 0 : name_len - 6;
        ser_xml_ns_binding *binding = ser_xml_ns_binding_valid(name, name_len, attr->value, attr->value_len)
                                          ? ser_xml_ns_find(scope, name + name_len - prefix_len, NULL, prefix_len)
                                          : NULL;
        if (binding != NULL) {
            binding->redeclared += delta;
        }
    }
}

/* Write one inherited binding onto the start tag, charged to spend when that is set. Returns 0, with ValueError set,
   once the copies exceed the allowance. */
static inline int ser_xml_ns_copy(sbuf *out, th_tree *tree, const ser_xml_ns_binding *binding, th_xml_ns_spend *spend) {
    Py_ssize_t name_len;
    const char *name = th_attr_name(tree, binding->attr->name_atom, &name_len);
    Py_ssize_t start = out->len;
    if (spend != NULL && !xml_ns_copy_funded(spend->copied, spend->flushed + start,
                                             name_len + binding->attr->value_len + 4, spend->base)) {
        PyErr_Format(PyExc_ValueError,
                     "the namespace declarations the children inherit exceed the allowance of %zd characters "
                     "plus %d per character of other output; serialize the element itself to write them once",
                     spend->base, TH_XML_NS_PER_CHAR);
        out->failed = 1;
        return 0;
    }
    sbuf_putc(out, ' ');
    sbuf_put_utf8(out, name, name_len);
    sbuf_puts(out, "=\"");
    sbuf_put_xml_text(out, binding->attr->value, binding->attr->value_len, 1);
    sbuf_putc(out, '"');
    if (spend != NULL) {
        spend->copied += out->len - start; /* what was written: escaping can lengthen the value */
    }
    return 1;
}

/* Copy onto the start tag of a subtree root the bindings its subtree uses and does not declare itself. A root that
   stands in a left-out run, unwrap->decls[chain] on, does not copy what the run declares again. When every child of
   the boundary gets its own copy, spend is the account those copies are charged to; past its allowance the
   serialization fails with ValueError, since output without the declarations would reparse into other namespaces. */
static inline void ser_xml_inherited_ns_decls(sbuf *out, th_tree *tree, const th_node *node, th_xml_ns_scope *scope,
                                              xml_ns_emitted emitted, const th_xml_unwrap *unwrap, Py_ssize_t chain,
                                              th_xml_ns_spend *spend) {
    if (out->failed) { /* nothing more is written, so there is no use working out what to copy */
        return;
    }
    if (scope->walked == 0) {
        ser_xml_ns_gather(out, tree, scope);
    }
    if (scope->count == 0) {
        return;
    }
    ser_xml_ns_hide_run(tree, scope, unwrap, chain, 1);
    if (scope->sorted) {
        ser_xml_ns_list_used(tree, scope, node);
    }
    Py_ssize_t listed = scope->sorted ? scope->used_count : scope->count;
    scope->used_count = 0;
    for (Py_ssize_t index = 0; index < listed; index++) {
        ser_xml_ns_binding *binding = scope->sorted ? scope->used[index] : &scope->bindings[index];
        binding->used = 0;
        if ((binding->prefix_len == 0 && emitted.default_ns) ||
            (!scope->sorted && (binding->redeclared != 0 || !ser_xml_ns_used_below(tree, node, binding)))) {
            continue;
        }
        if (!ser_xml_ns_copy(out, tree, binding, spend)) {
            break;
        }
    }
    ser_xml_ns_hide_run(tree, scope, unwrap, chain, -1);
}

static inline int ser_xml_declares_xlink(th_tree *tree, const th_node *node) {
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[position].name_atom, &name_len);
        if (name_len == 11 && memcmp(name, "xmlns:xlink", 11) == 0) {
            return 1;
        }
    }
    return 0;
}

/* Whether an ancestor of node carries xmlns:xlink, moving the walk on to node. Start tags are written in document
   order, so the nodes from the last tag up to node's parent are those that closed in between, and the outermost
   declaration still holds unless its element is one of them. Each node is passed once that way, where scanning every
   ancestor for each tag is quadratic in the depth. Only the first tag to ask, and one under a parent whose own tag
   was skipped as unwritable, scan the ancestors. */
static inline int ser_xml_xlink_inherited(th_tree *tree, const th_node *node, th_xml_xlink_walk *walk) {
    const th_node *scope = walk->scope;
    const th_node *closed = walk->last;
    for (; closed != NULL && closed != node->parent; closed = closed->parent) {
        TH_WORK_STEP();
        if (closed == scope) {
            scope = NULL; /* the outermost one: nothing above it carries the declaration */
        }
    }
    if (closed == NULL) {
        scope = NULL;
        for (const th_node *ancestor = node->parent; ancestor != NULL; ancestor = ancestor->parent) {
            TH_WORK_STEP();
            if (ser_xml_declares_xlink(tree, ancestor)) {
                scope = ancestor;
            }
        }
    }
    int inherited = scope != NULL;
    walk->last = node;
    walk->scope = !inherited && ser_xml_declares_xlink(tree, node) ? node : scope;
    return inherited;
}

/* Make room for one more `size`-byte item after the `len` an array holds. Returns the array, moved
   when it grew, or NULL on allocation failure with the array left as it was. */
static inline void *ser_reserve(void *items, Py_ssize_t len, Py_ssize_t *cap, size_t size) {
    if (len < *cap) {
        return items;
    }
    size_t grown_cap;
    size_t bytes;
    int grew = th_grow_cap((size_t)len + 1, (size_t)*cap, 8, size, &grown_cap, &bytes);
    if (!grew) {     /* GCOVR_EXCL_BR_LINE: size overflow needs a length no allocation could hold */
        return NULL; /* GCOVR_EXCL_LINE: size-overflow path, unreachable from a test */
    }
    void *grown = PyMem_Realloc(items, bytes);
    if (grown != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        *cap = (Py_ssize_t)grown_cap;
    }
    return grown;
}

/* The frame of element when it is the left-out element the walk is innermost in, NULL otherwise. */
static inline const th_xml_unwrap_frame *ser_xml_unwrapped(const th_xml_unwrap *unwrap, const th_node *element) {
    return unwrap->len > 0 && unwrap->frames[unwrap->len - 1].node == element ? &unwrap->frames[unwrap->len - 1] : NULL;
}

void ser_xml_unwrapped_ns_decls(sbuf *out, th_tree *tree, const th_node *node, xml_ns_emitted emitted,
                                int injected_xlink, th_xml_unwrap *unwrap, Py_ssize_t chain);

/* Copy onto node the namespace declarations of what its tag stands in for: the ancestors above the boundary when node
   is written right below it, after those of a left-out run between the two, or the left-out run node is a child of. */
static inline void ser_xml_copied_ns_decls(sbuf *out, th_tree *tree, const th_node *node, xml_ns_emitted emitted,
                                           int injected_xlink, const th_serialize_opts *opts,
                                           const th_xml_unwrap_frame *unwrapped, const th_node *parent) {
    th_xml_unwrap *unwrap = opts->xml_unwrap;
    if (unwrapped != NULL && unwrapped->chain < unwrap->decls_len) {
        ser_xml_unwrapped_ns_decls(out, tree, node, emitted, injected_xlink, unwrap, unwrapped->chain);
    }
    if (opts->xml_scope != NULL && parent == opts->xml_scope->boundary) {
        ser_xml_inherited_ns_decls(
            out, tree, node, opts->xml_scope, emitted, unwrap, unwrapped != NULL ? unwrapped->chain : unwrap->decls_len,
            unwrapped != NULL && opts->xml_ns_spend == NULL ? &unwrap->spend : opts->xml_ns_spend);
    }
}

/* Emit the default namespace on a foreign subtree root and a missing xlink binding.
   XML trees keep inherited xlink declarations; HTML trees need the built-in URI. A
   child of a left-out element is written against the nearest ancestor whose tag is. */
static inline xml_ns_emitted ser_xml_ns_decls(sbuf *out, th_tree *tree, const th_node *node,
                                              const th_serialize_opts *opts) {
    xml_ns_emitted emitted = {0};
    const th_xml_unwrap_frame *unwrapped = ser_xml_unwrapped(opts->xml_unwrap, node->parent);
    const th_node *parent = unwrapped == NULL ? node->parent : unwrapped->anchor;
    if (parent == NULL ? node->ns != TH_NS_HTML : parent->ns != node->ns) {
        sbuf_puts(out, node->ns == TH_NS_HTML  ? " xmlns=\"\""
                       : node->ns == TH_NS_SVG ? " xmlns=\"http://www.w3.org/2000/svg\""
                                               : " xmlns=\"http://www.w3.org/1998/Math/MathML\"");
        emitted.default_ns = 1;
    }
    th_xml_xlink_walk *walk = opts->xml_xlink;
    if (node->attr_count == 0) {
        if (walk != NULL && walk->last != NULL) {
            ser_xml_xlink_inherited(tree, node, walk);
        }
        ser_xml_copied_ns_decls(out, tree, node, emitted, 0, opts, unwrapped, parent);
        return emitted;
    }
    int has_xlink_attr = 0;
    int has_xlink_decl = 0;
    for (Py_ssize_t position = 0; position < node->attr_count; position++) {
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, node->attrs[position].name_atom, &name_len);
        if (name_len == 11 && memcmp(name, "xmlns:xlink", 11) == 0 &&
            ser_xml_ns_binding_valid(name, name_len, node->attrs[position].value, node->attrs[position].value_len)) {
            has_xlink_decl = 1;
        } else if (name_len >= 6 && memcmp(name, "xlink:", 6) == 0) {
            has_xlink_attr = 1;
        }
    }
    /* Once a tag has asked, every later one keeps the walk's place, whether or not it asks itself. */
    if (walk != NULL && (walk->last != NULL || (has_xlink_attr && !has_xlink_decl)) &&
        ser_xml_xlink_inherited(tree, node, walk)) {
        has_xlink_decl = 1;
    }
    /* A local binding is written with attributes; an XML ancestor already binds the prefix. */
    int injected_xlink = has_xlink_attr && !has_xlink_decl;
    if (injected_xlink) {
        sbuf_puts(out, " xmlns:xlink=\"http://www.w3.org/1999/xlink\"");
    }
    ser_xml_copied_ns_decls(out, tree, node, emitted, injected_xlink, opts, unwrapped, parent);
    return emitted;
}

/* XML Name character classes over ASCII: bit 0 NameStartChar, bit 1 NameChar.
   Non-ASCII names use the XML Name ranges. A table keeps gcc and clang counting
   the same branches, which a chained comparison would not. */
static const unsigned char XML_NAME_FLAGS[128] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 2, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 0, 0, 0, 0, 0,
    0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 3,
    0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0,
};

/* Whether an attribute's name can be written into an XML start tag: a stored
   namespace declaration ser_xml_ns_decls already emitted for this tag is dropped
   because a second copy would make the tag ill-formed (but any other xmlns:prefix a
   caller declared is kept), and a name carrying a character XML forbids in a Name --
   the tag-soup `=`, `/`, `<`, quotes and spaces the HTML parser keeps but XML cannot
   -- is dropped so the tag stays well-formed. */
static inline int xml_attr_name_writable(const char *name, Py_ssize_t name_len, xml_ns_emitted emitted) {
    int is_default_decl = name_len == 5 && memcmp(name, "xmlns", 5) == 0;
    if (is_default_decl && emitted.default_ns) {
        return 0;
    }
    unsigned char first = (unsigned char)name[0];
    if (first >= 0x80) {
        return th_xml_attr_name_wellformed_utf8(name, name_len);
    }
    if (first == ':' || (XML_NAME_FLAGS[first] & 0x1) == 0) {
        return 0;
    }
    int colon = 0;
    for (Py_ssize_t index = 1; index < name_len; index++) {
        unsigned char character = (unsigned char)name[index];
        if (character >= 0x80) {
            return th_xml_attr_name_wellformed_utf8(name, name_len);
        }
        if ((XML_NAME_FLAGS[character] & 0x2) == 0) {
            return 0;
        }
        if (character == ':') {
            if (index == name_len - 1 || colon) {
                return 0;
            }
            colon = 1;
        }
    }
    return 1;
}

/* Whether CommonMark reads an attribute name inside raw HTML: an ASCII
   [A-Za-z_:][A-Za-z0-9_.:-]* (spec 6.6), the same classes as an ASCII XML Name. */
static inline int commonmark_attr_name_valid(const char *name, Py_ssize_t name_len) {
    for (Py_ssize_t index = 0; index < name_len; index++) {
        unsigned char character = (unsigned char)name[index];
        if (character >= 0x80 || ((unsigned)XML_NAME_FLAGS[character] & (index == 0 ? 0x1U : 0x2U)) == 0) {
            return 0;
        }
    }
    return 1;
}

/* The tree builder reopens an unclosed formatting element in every block after it, attributes and all. The output a
   writer has produced funds the attributes of those clones, so a value spelled once cannot multiply the output. This is
   where a clone's attributes start in out, or -1 for an element the source wrote, which pays for itself. */
static inline Py_ssize_t ser_clone_start(const sbuf *out, const th_node *node) {
    return node->tag_flags & TH_ELEM_IMPLIED ? out->len : -1;
}

static inline int ser_clone_first(sbuf *out, const th_node *node) {
    int added = th_clone_seen_add(&out->clone_seen, th_node_values_seq(node));
    out->failed |= added < 0;
    return added;
}

static inline int ser_clone_fits(const sbuf *out, Py_ssize_t start, Py_ssize_t cost) {
    return cost <= TH_CLONE_ALLOWANCE + out->clone_credit + out->len - 2 * (out->len - start);
}

static inline int ser_clone_admit(sbuf *out, Py_ssize_t start, const th_node *node, Py_ssize_t cost) {
    int added = ser_clone_first(out, node);
    if (added != 0) {
        return added > 0;
    }
    return ser_clone_fits(out, start, cost);
}

/* Charge the attributes the clone wrote since start, free or not, so each one is spent once as output and once as the
   allowance; a no-op for an element the source wrote. */
static inline void ser_clone_settle(sbuf *out, Py_ssize_t start) {
    if (start >= 0) {
        out->clone_credit -= 2 * (out->len - start);
    }
}

/* The characters the attributes of a clone's start tag cost, the total ser_clone_admit weighs as a whole. */
static inline Py_ssize_t ser_clone_attrs_cost(th_tree *tree, const th_node *node) {
    Py_ssize_t cost = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        Py_ssize_t name_len;
        th_attr_name(tree, node->attrs[index].name_atom, &name_len);
        cost += name_len + node->attrs[index].value_len;
    }
    return cost;
}

/* Whether a start tag leaves the attribute named name out: under XML a name XML cannot hold or a namespace binding that
   would not reparse, and under Markdown a name CommonMark does not read. */
static inline int ser_open_attr_left_out(const char *name, Py_ssize_t name_len, const th_node_attr *attr,
                                         const th_serialize_opts *opts, xml_ns_emitted emitted) {
    return (opts->xml && (!xml_attr_name_writable(name, name_len, emitted) ||
                          (ser_xml_ns_name(name, name_len) &&
                           !ser_xml_ns_binding_valid(name, name_len, attr->value, attr->value_len)))) ||
           (opts->markdown && !commonmark_attr_name_valid(name, name_len));
}

/* The characters the attributes a clone's start tag writes cost, which ser_clone_admit weighs as a whole. */
static inline Py_ssize_t ser_open_attrs_cost(th_tree *tree, const th_node *node, const th_serialize_opts *opts,
                                             xml_ns_emitted emitted) {
    Py_ssize_t cost = 0;
    for (Py_ssize_t index = 0; index < node->attr_count; index++) {
        const th_node_attr *attr = &node->attrs[index];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (!ser_open_attr_left_out(name, name_len, attr, opts, emitted)) {
            cost += name_len + attr->value_len;
        }
    }
    return cost;
}

/* Whether a clone's start tag is left without its attributes: a first copy of a value set keeps them, and a repeat
   keeps them only when the allowance covers them. Out of line, so ordinary elements never reach it. */
static TH_NOINLINE int ser_open_clone_refused(sbuf *out, Py_ssize_t start, th_tree *tree, const th_node *node,
                                              const th_serialize_opts *opts, xml_ns_emitted emitted) {
    int added = ser_clone_first(out, node);
    if (added != 0) {
        return added < 0;
    }
    return !ser_clone_fits(out, start, ser_open_attrs_cost(tree, node, opts, emitted));
}

/* Write an element's start tag up to but not including its closing delimiter,
   "<tag attrs...": attributes in source order, or in name order when opts asks;
   values double-quoted and escaped under the formatter (or the XML rules when
   opts->xml), with a charset meta's declaration normalized when meta_charset is on.
   The caller appends ">" (or "/>" for an empty XML element) so it can choose the
   self-closing form after inspecting the children. */
static inline void ser_open_tag(sbuf *out, th_tree *tree, th_node *node, const th_serialize_opts *opts) {
    sbuf_putc(out, '<');
    sbuf_put_ucs4(out, node->text, node->text_len);
    xml_ns_emitted emitted = {0};
    if (opts->xml) {
        emitted = ser_xml_ns_decls(out, tree, node, opts);
    }
    Py_ssize_t stack_order[MAX_SORTED_ATTRS];
    Py_ssize_t *order = ser_attr_order(tree, node, opts->sort_attributes, stack_order);
    int charset_kind = opts->inject_meta && !opts->xml ? ser_meta_charset_kind(tree, node) : 0;
    /* Markdown's raw HTML spends the budget its prose funds instead */
    Py_ssize_t clone_start = opts->clone_budget == NULL ? ser_clone_start(out, node) : -1;
    Py_ssize_t attr_count = node->attr_count;
    if (clone_start >= 0 && ser_open_clone_refused(out, clone_start, tree, node, opts, emitted)) {
        attr_count = 0;
    }
    for (Py_ssize_t position = 0; position < attr_count; position++) {
        th_node_attr *attr = &node->attrs[order != NULL ? order[position] : position];
        Py_ssize_t name_len;
        const char *name = th_attr_name(tree, attr->name_atom, &name_len);
        if (ser_open_attr_left_out(name, name_len, attr, opts, emitted)) {
            continue;
        }
        if (opts->clone_budget != NULL) {
            /* the tree builder reopens an unclosed formatting element in every block after it, attributes and all:
               a written start tag funds one repeat of each attribute, a clone spends, and an attribute the budget
               does not cover is left out, so a value spelled once cannot multiply Markdown's raw HTML */
            Py_ssize_t cost = name_len + attr->value_len;
            if (!(node->tag_flags & TH_ELEM_IMPLIED)) {
                *opts->clone_budget += cost;
            } else if (cost > *opts->clone_budget) {
                continue;
            } else {
                *opts->clone_budget -= cost;
            }
        }
        TH_WORK_STEPS_ADD(name_len + attr->value_len);
        sbuf_putc(out, ' ');
        sbuf_put_utf8(out, name, name_len);
        if (ser_put_charset_value(out, opts, charset_kind, name, name_len)) {
            continue;
        }
        sbuf_puts(out, "=\"");
        if (opts->xml) {
            sbuf_put_xml_text(out, attr->value, attr->value_len, 1);
        } else if ((opts->markdown == TH_MARKDOWN_HTML_CELL || opts->markdown == TH_MARKDOWN_HTML_INLINE) &&
                   attr->value_len > 1) {
            for (Py_ssize_t index = 0; index < attr->value_len; index++) {
                if (attr->value[index] == ' ' && ((index > 0 && attr->value[index - 1] == ' ') ||
                                                  (index + 1 < attr->value_len && attr->value[index + 1] == ' '))) {
                    sbuf_puts(out, "&#32;");
                } else {
                    sbuf_put_text(out, attr->value + index, 1, 1, opts->formatter);
                }
            }
        } else {
            sbuf_put_text(out, attr->value, attr->value_len, 1, opts->formatter);
        }
        sbuf_putc(out, '"');
    }
    ser_clone_settle(out, clone_start);
    ser_attr_order_free(order, stack_order);
}

static inline void ser_close_tag(sbuf *out, th_node *node) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, node->text, node->text_len);
    sbuf_putc(out, '>');
}

/* Hand a filled sbuf back to the caller, normalizing an empty result to a real
   zero-length allocation so NULL unambiguously means failure. */
static inline Py_UCS4 *sbuf_finish(sbuf *out, Py_ssize_t *out_len) {
    th_clone_seen_free(out->clone_seen);
    out->clone_seen = NULL;
    if (out->failed) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(out->data); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    *out_len = out->len;
    if (out->data == NULL) {
        out->data = PyMem_Malloc(1); /* GCOVR_EXCL_BR_LINE: empty output, allocation cannot be forced to fail */
    }
    return out->data;
}

/* When serializing a whole parsed document, the output length tracks the input
   length closely, so reserve it up front. That turns the buffer's geometric
   doubling (about ten reallocs and ~2x the output in memmoves for a 235 kB page)
   into a single allocation. A subtree serialize keeps the default growth, since
   the input length would wildly over-reserve. */
static inline void sbuf_presize_for_root(sbuf *out, th_tree *tree, th_node *node) {
    if (node == th_tree_document(tree)) {
        /* the end tags, quotes and escapes the serializer adds can run the output a little past the input; that
           overshoot would double the buffer through one realloc whose copy depends on whether the heap happens to
           leave room to grow in place, so the headroom keeps the common case to the one allocation */
        sbuf_reserve(out, tree->length + tree->length / 16 + 64);
    }
}

/* The NAMED formatter writes most non-ASCII characters as a reference several times their length, so its output
   from non-ASCII input usually outgrows the input; reserving twice the input costs no more peak memory than the
   doubling that overshoot triggers, and skips its copy. */
static inline void sbuf_presize_for_markup(sbuf *out, th_tree *tree, th_node *node, int formatter) {
    if (formatter == TH_FMT_NAMED && node == th_tree_document(tree) &&
        (tree->kind != PyUnicode_1BYTE_KIND || !th_tree_input_ascii(tree, tree->length))) {
        sbuf_reserve(out, 2 * tree->length + 64);
        return;
    }
    sbuf_presize_for_root(out, tree, node);
}

static inline void sbuf_presize_for_c14n(sbuf *out, th_tree *tree) {
    /* Sparse input may be mostly comments that canonicalization drops. */
    if (tree->length >= 4096 && tree->length <= 16384 && tree->node_seq >= (uint32_t)(tree->length / 32)) {
        sbuf_reserve(out, tree->length + 64);
    }
}

/* True only for the foreign attribute names the spec puts in a namespace; those
   serialize with a space (prefix localname). Plain "xmlns" has no prefix in its
   stored name but still belongs to the xmlns namespace, so the renderer prepends
   one. An arbitrary xml:/xlink: name not in the table keeps its literal colon. */
static inline int foreign_attr_namespaced(const char *lower, Py_ssize_t len) {
    static const char *const NAMESPACED[] = {
        "xlink:actuate", "xlink:arcrole", "xlink:href", "xlink:role", "xlink:show",  "xlink:title",
        "xlink:type",    "xml:lang",      "xml:space",  "xmlns",      "xmlns:xlink",
    };
    for (size_t index = 0; index < sizeof(NAMESPACED) / sizeof(NAMESPACED[0]); index++) {
        if ((Py_ssize_t)strlen(NAMESPACED[index]) == len && memcmp(NAMESPACED[index], lower, (size_t)len) == 0) {
            return 1;
        }
    }
    return 0;
}

#include "serialize/markdown_internal.h" /* the markdown/plain-text block predicates */

#endif /* TURBOHTML_SERIALIZE_INTERNAL_H */
