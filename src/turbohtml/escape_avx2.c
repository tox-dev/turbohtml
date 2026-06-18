/* AVX2 32-byte clean-run scan for the 1-byte escape path.

   This unit is compiled with -mavx2 and linked into _html only on x86 builds
   whose compiler accepts that flag; escape.c selects it at runtime through
   __builtin_cpu_supports("avx2") and otherwise keeps the SSE2 16-byte path, so
   a Haswell-or-newer CPU widens the clean run from 16 to 32 bytes per step
   while pre-AVX2 CPUs are unaffected. Only the clean-run sizing scan and copy
   widen: the per-special sizing (escape_extra) and rewrite (write_escaped) stay
   scalar and shared with escape.c via escape_shared.h, so the two units cannot
   diverge on what a special expands to.

   Self-guarded to x86: meson only compiles this unit on x86 targets, and the
   guard also keeps non-x86 tooling (clang-tidy on an ARM dev box) from choking
   on <immintrin.h>, which it processes regardless of the build. */

#if defined(__x86_64__) || defined(__i386__) || defined(_M_X64) || defined(_M_IX86)

#include "turbohtml.h"

#include "escape_shared.h"

#include <immintrin.h>
#include <stdint.h>
#include <string.h>

#define AVX2_BLOCK_BYTES 32

/* How many characters of growth escaping this block adds, all 32 lanes at once:
   each comparison yields 0xFF per match, masked to that special's growth
   (&amp; adds 4, &lt;/&gt; add 3, &quot;/&#x27; add 5). _mm256_sad_epu8 sums the
   bytes into four 64-bit partials (one per 8-byte lane), summed scalar. A lane
   holds at most one special, so per-byte growth never exceeds 5 and the per-
   8-byte partial never exceeds 40: no overflow. */
static inline Py_ssize_t block_extra_avx2(const uint8_t *block, int quote) {
    __m256i bytes = _mm256_loadu_si256((const __m256i *)block);
    __m256i extras = _mm256_and_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('&')), _mm256_set1_epi8(4));
    extras =
        _mm256_add_epi8(extras, _mm256_and_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('<')), _mm256_set1_epi8(3)));
    extras =
        _mm256_add_epi8(extras, _mm256_and_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('>')), _mm256_set1_epi8(3)));
    if (quote) {
        extras = _mm256_add_epi8(
            extras, _mm256_and_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('"')), _mm256_set1_epi8(5)));
        extras = _mm256_add_epi8(
            extras, _mm256_and_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('\'')), _mm256_set1_epi8(5)));
    }
    __m256i sums = _mm256_sad_epu8(extras, _mm256_setzero_si256());
    return (Py_ssize_t)(_mm256_extract_epi64(sums, 0) + _mm256_extract_epi64(sums, 1) + _mm256_extract_epi64(sums, 2) +
                        _mm256_extract_epi64(sums, 3));
}

/* One mask bit per byte (bit i set iff byte i is a special); _mm256_movemask_epi8
   packs the per-lane high bits, so a trailing-zero count gives the lowest special
   index and clearing the lowest set bit advances to the next. */
static inline uint32_t block_special_mask_avx2(const uint8_t *block, int quote) {
    __m256i bytes = _mm256_loadu_si256((const __m256i *)block);
    __m256i hits = _mm256_or_si256(_mm256_or_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('&')),
                                                   _mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('<'))),
                                   _mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('>')));
    if (quote) {
        hits = _mm256_or_si256(hits, _mm256_or_si256(_mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('"')),
                                                     _mm256_cmpeq_epi8(bytes, _mm256_set1_epi8('\''))));
    }
    return (uint32_t)_mm256_movemask_epi8(hits);
}

Py_ssize_t turbohtml_escape_count_1byte_avx2(const uint8_t *input, Py_ssize_t length, int quote) {
    Py_ssize_t extra = 0;
    Py_ssize_t pos = 0;
    while (pos + AVX2_BLOCK_BYTES <= length) {
        extra += block_extra_avx2(input + pos, quote);
        pos += AVX2_BLOCK_BYTES;
    }
    for (; pos < length; pos++) {
        extra += escape_extra(input[pos], quote);
    }
    return extra;
}

Py_ssize_t turbohtml_escape_write_1byte_avx2(const uint8_t *input, Py_ssize_t length, int out_kind, void *out_data,
                                             int quote) {
    uint8_t *output = (uint8_t *)out_data;
    Py_ssize_t written = 0;
    Py_ssize_t pos = 0;
    while (pos + AVX2_BLOCK_BYTES <= length) {
        uint32_t mask = block_special_mask_avx2(input + pos, quote);
        if (mask == 0) {
            memcpy(output + written, input + pos, AVX2_BLOCK_BYTES);
            written += AVX2_BLOCK_BYTES;
        } else {
            /* copy the gap before each special wholesale, rewrite the special,
               then copy whatever trails the last one; the gap checks keep
               special-dense input from paying for empty copies */
            Py_ssize_t prev = 0;
            do {
                Py_ssize_t index = __builtin_ctz(mask);
                if (index > prev) {
                    memcpy(output + written, input + pos + prev, (size_t)(index - prev));
                    written += index - prev;
                }
                written += write_escaped(out_kind, out_data, written, input[pos + index], quote);
                mask &= mask - 1;
                prev = index + 1;
            } while (mask != 0);
            if (AVX2_BLOCK_BYTES > prev) {
                memcpy(output + written, input + pos + prev, (size_t)(AVX2_BLOCK_BYTES - prev));
                written += AVX2_BLOCK_BYTES - prev;
            }
        }
        pos += AVX2_BLOCK_BYTES;
    }
    for (; pos < length; pos++) {
        written += write_escaped(out_kind, out_data, written, input[pos], quote);
    }
    return written;
}

#endif /* x86 */
