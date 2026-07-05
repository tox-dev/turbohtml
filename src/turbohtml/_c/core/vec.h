/* Overflow-safe growth arithmetic shared by every growable buffer in the C core.

   The tree builder, tokenizer, the serializers, and both standalone minifier engines each keep a
   grow-on-demand array behind a "reserve enough for N more" call. They differ in element type,
   capacity type, and allocator, so the piece worth sharing is the arithmetic, not the allocation.
   th_grow_cap doubles a capacity until it covers the requested length and reports the byte size for
   the caller's own realloc. It is a header-only static inline, so every translation unit inlines it
   as the hand-rolled loop it replaces, leaving one definition to audit and no call overhead.

   Both multiplies are bounded before they run, so a length crafted to wrap size_t cannot
   underallocate: the caller gets a clean failure and aborts instead of writing past a short buffer.
   That is the class of libxml2's CVE-2022-29824, a doubling loop whose size computation wraps,
   closed once for the whole tree. The bound test divides by a nonzero constant, so it also holds on
   the toolchains without __builtin_mul_overflow (MSVC on arm64). */

#ifndef TURBOHTML_CORE_VEC_H
#define TURBOHTML_CORE_VEC_H

#include <stddef.h>
#include <stdint.h>

/* Grow `current` (doubling from `initial` when it is 0) until it reaches `needed` elements, writing
   the chosen capacity to *cap_out and its `capacity * elem_size` byte size to *bytes_out. Returns 1
   on success, or 0 when doubling or the byte size would exceed SIZE_MAX, so the caller reports OOM
   without touching its buffer. Counts and sizes are size_t; a caller whose capacity field is
   narrower casts in and clamps the result back. */
static inline int th_grow_cap(size_t needed, size_t current, size_t initial, size_t elem_size, size_t *cap_out,
                              size_t *bytes_out) {
    size_t cap = current ? current : initial;
    while (cap < needed) {
        if (cap > SIZE_MAX / 2) {
            return 0;
        }
        cap *= 2;
    }
    if (cap > SIZE_MAX / elem_size) {
        return 0;
    }
    *cap_out = cap;
    *bytes_out = cap * elem_size;
    return 1;
}

#endif /* TURBOHTML_CORE_VEC_H */
