/* Internationalized host ToASCII: turn a Unicode host into its ASCII (punycode) form the way the WHATWG URL Standard's
   host parser does, the domain-to-ASCII step the _urls.py cleaner delegates here instead of Python's IDNA-2003 codec
   (tox-dev/turbohtml#478).

   The WHATWG host parser runs Unicode IDNA ToASCII with Transitional_Processing=false and, for the non-strict cleaner
   this backs, UseSTD3ASCIIRules=false -- that is UTS #46, not the older IDNA-2003 str.encode("idna") the shim used to
   reach, which mis-handles ess-zett and final sigma. Processing is three steps over the pinned Unicode tables in
   data/idna_table.h: map each code point (lowercasing, fold-away, or drop), normalize the result to NFC, then per label
   punycode-encode any that carries a non-ASCII code point. An already-ASCII "xn--" label is decoded to validate and
   canonicalize it, and a label the decoder rejects is kept verbatim (an advisory error the best-effort cleaner
   ignores), so the mechanical output matches the UTS #46 conformance vectors' toASCII column for every well-formed,
   in-limit host. The host fails, and the shim falls back to the lowercased form, on a disallowed code point, an
   unpaired surrogate, or an input that is past TH_IDNA_MAX_INPUT or that mapping and normalizing lengthen past it. */

/* The ToASCII core (map_host, nfc, puny_encode/decode, emit_label) is pure Py_UCS4 buffer arithmetic and touches
   CPython only through the two boundary functions at the foot of the file. Defining TH_IDNA_STANDALONE swaps the
   CPython typedefs for stdint ones and drops those boundary functions, so tools/fuzz/idna_harness.c can push arbitrary
   Unicode through the RFC 3492 accumulator and the punycode output bound under ASan/UBSan with no interpreter -- the
   jstypes.h / JM_STANDALONE pattern the JS minifier already uses (tox-dev/turbohtml#478). */
#ifdef TH_IDNA_STANDALONE
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#define TH_IDNA_ALLOC malloc
#define TH_IDNA_FREE free
typedef uint32_t Py_UCS4;
typedef ptrdiff_t Py_ssize_t;
#else
#include "core/common.h"
#define TH_IDNA_ALLOC PyMem_Malloc
#define TH_IDNA_FREE PyMem_Free
#endif

#include "core/work_steps.h"
#include "data/idna_table.h"

/* RFC 3492 (Punycode) bootstring parameters for the IDNA profile, plus the sentinel bound its integer arithmetic must
   not overflow (the reference implementation's maxint over a 32-bit accumulator). */
enum {
    PUNY_BASE = 36,
    PUNY_TMIN = 1,
    PUNY_TMAX = 26,
    PUNY_SKEW = 38,
    PUNY_DAMP = 700,
    PUNY_INITIAL_BIAS = 72,
    PUNY_INITIAL_N = 128,
    PUNY_MAXINT = 0x7FFFFFFF,
    PUNY_DELIMITER = '-',
};

/* Unicode 3.12 Hangul syllable composition constants, so the 11172 precomposed syllables need no decomposition or
   composition rows: their canonical (de)composition is arithmetic. */
enum {
    HANGUL_SBASE = 0xAC00,
    HANGUL_LBASE = 0x1100,
    HANGUL_VBASE = 0x1161,
    HANGUL_TBASE = 0x11A7,
    HANGUL_LCOUNT = 19,
    HANGUL_VCOUNT = 21,
    HANGUL_TCOUNT = 28,
    HANGUL_NCOUNT = HANGUL_VCOUNT * HANGUL_TCOUNT,
    HANGUL_SCOUNT = HANGUL_LCOUNT * HANGUL_NCOUNT,
};

/* The longest host ToASCII accepts. The URL Standard leaves overlong labels undefined (whatwg/url#824), so this matches
   ada's max_domain_input_bytes = 16384 DoS bound, counted in code points, the unit the work and the buffers scale in.
   It is three orders of magnitude above the 253 octets DNS allows, so it refuses no real host. The UTS #46 mapping
   turns one code point into up to 18 and NFC can lengthen its output again, so idna_to_ascii holds the host to the cap
   after each of the two as well: those are the lengths the punycode passes run on. */
enum { TH_IDNA_MAX_INPUT = 16384 };

/* The shortest label the punycode passes handle by sorting rather than by the RFC 3492 sample loops, which cost
   O(n^2) in the label length. DNS caps a label at 63 octets, so a host a resolver accepts never takes the sorted
   path, and below this length the sample loops are cheaper than the sort's setup. */
enum { PUNY_SORTED_MIN = 64 };

/* What puny_encode and puny_decode return in place of a length (PUNY_INVALID: a label that cannot be encoded, or a
   malformed payload), and emit_encoded and emit_label in place of a write offset. */
enum {
    PUNY_INVALID = -1,
    PUNY_NO_MEMORY = -2,
};

/* The UTS #46 mapping row covering `cp`: the ranges tile the code space with no gap the loader leaves, so a code point
   above the last row (past U+10FFFF cannot occur) resolves to that row's keep status. Binary search over first/last. */
static const th_idna_map_row *map_row(Py_UCS4 cp) {
    int lo = 0;
    int hi = th_idna_map_count - 1;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (cp > th_idna_map[mid].last) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return &th_idna_map[lo];
}

/* The canonical combining class of `cp`, 0 when the sorted table has no row for it (the default, and every starter). */
static uint8_t ccc_of(Py_UCS4 cp) {
    if (cp < th_idna_ccc[0].code) {
        return 0;
    }
    int lo = 0;
    int hi = th_idna_ccc_count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (th_idna_ccc[mid].code < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < th_idna_ccc_count && th_idna_ccc[lo].code == cp) {
        return th_idna_ccc[lo].ccc;
    }
    return 0;
}

/* Return `cp`'s NFC quick-check value: 0 Yes, 1 No, 2 Maybe. */
static uint8_t qc_of(Py_UCS4 cp) {
    if (cp < th_idna_qc[0].first) {
        return 0;
    }
    int lo = 0;
    int hi = th_idna_qc_count - 1;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (cp > th_idna_qc[mid].last) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    const th_idna_qc_row *row = &th_idna_qc[lo];
    return cp >= row->first && cp <= row->last ? row->value : 0;
}

/* Return 1 when Unicode's NFC quick check reaches a conclusive Yes for input[0,len). */
static int nfc_is_normalized(const Py_UCS4 *input, Py_ssize_t len) {
    uint8_t last_class = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 cp = input[index];
        uint8_t klass = ccc_of(cp);
        if ((klass != 0 && klass < last_class) || qc_of(cp) != 0) {
            return 0;
        }
        last_class = klass;
    }
    return 1;
}
/* The full canonical decomposition row for `cp`, or NULL when it does not decompose (Hangul is handled by the caller).
 */
static const th_idna_decomp_row *decomp_row(Py_UCS4 cp) {
    if (cp < th_idna_decomp[0].code) {
        return NULL;
    }
    int lo = 0;
    int hi = th_idna_decomp_count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (th_idna_decomp[mid].code < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < th_idna_decomp_count && th_idna_decomp[lo].code == cp) {
        return &th_idna_decomp[lo];
    }
    return NULL;
}

/* The tabled canonical composition of the pair (first, second), or 0 when the sorted table pairs them into nothing; 0
   is a safe "no composition" sentinel because U+0000 is never a composition target. */
static Py_UCS4 table_compose(Py_UCS4 first, Py_UCS4 second) {
    /* Every canonical composition pairs a starter with a combining mark, so no row's `second` falls below the table's
       lowest one. A run of unaccented text makes NFC probe adjacent starters, whose second is an ordinary letter; one
       comparison rejects that pair instead of a search that cannot match. */
    if (second < th_idna_comp_second_min) {
        return 0;
    }
    int lo = 0;
    int hi = th_idna_comp_count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (th_idna_comp[mid].first < first ||
            (th_idna_comp[mid].first == first && th_idna_comp[mid].second < second)) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < th_idna_comp_count && th_idna_comp[lo].first == first && th_idna_comp[lo].second == second) {
        return th_idna_comp[lo].composed;
    }
    return 0;
}

/* The canonical composition of (first, second), 0 when they do not compose: a leading + vowel jamo into an LV syllable,
   an LV syllable + trailing jamo into an LVT syllable (both the Unicode 3.12 arithmetic), or the tabled pair. The jamo
   ranges are probed with one unsigned comparison each -- an out-of-range second underflows past the count -- so a
   malformed jamo pair falls straight through to the table. */
static Py_UCS4 pair_compose(Py_UCS4 first, Py_UCS4 second) {
    Py_UCS4 lindex = first - HANGUL_LBASE;
    Py_UCS4 vindex = second - HANGUL_VBASE;
    if (lindex < HANGUL_LCOUNT && vindex < HANGUL_VCOUNT) {
        return HANGUL_SBASE + (lindex * HANGUL_VCOUNT + vindex) * HANGUL_TCOUNT;
    }
    Py_UCS4 sindex = first - HANGUL_SBASE;
    Py_UCS4 tindex = second - HANGUL_TBASE - 1; /* shift the trailing run [TBASE+1, TBASE+TCOUNT) onto [0, TCOUNT-1) */
    if (sindex < HANGUL_SCOUNT && sindex % HANGUL_TCOUNT == 0 && tindex < HANGUL_TCOUNT - 1) {
        return first + tindex + 1;
    }
    return table_compose(first, second);
}

/* Apply the UTS #46 mapping to input[0,in_len): keep, replace with the pool sequence, or drop each code point. `out`
   holds at most in_len * (longest mapping) code points; returns the mapped length, or -1 on a code point UTS #46
   disallows in a host (validity criterion 6), an unpaired surrogate included. */
static Py_ssize_t map_host(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < in_len; index++) {
        Py_UCS4 cp = input[index];
        if (cp < 0x80) {
            out[at++] = cp >= 'A' && cp <= 'Z' ? cp + ('a' - 'A') : cp;
            continue;
        }
        const th_idna_map_row *row = map_row(cp);
        if (row->status == 1) {
            for (uint8_t offset = 0; offset < row->length; offset++) {
                out[at++] = th_idna_map_pool[row->offset + offset];
            }
        } else if (row->status == 3) {
            return -1;
        } else if (row->status != 2) { /* 0 keeps the code point; 2 drops it (the ignored set) */
            out[at++] = cp;
        }
    }
    return at;
}

/* Canonically decompose input[0,in_len) into `out` (Hangul arithmetically, everything else through the table), the
   first half of Normalization Form C. `out` holds at most in_len * (longest decomposition) code points; returns the
   decomposed length. */
static Py_ssize_t nfc_decompose(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < in_len; index++) {
        Py_UCS4 cp = input[index];
        Py_UCS4 sindex = cp - HANGUL_SBASE;
        if (sindex < HANGUL_SCOUNT) {
            out[at++] = HANGUL_LBASE + sindex / HANGUL_NCOUNT;
            out[at++] = HANGUL_VBASE + sindex % HANGUL_NCOUNT / HANGUL_TCOUNT;
            if (sindex % HANGUL_TCOUNT != 0) {
                out[at++] = HANGUL_TBASE + sindex % HANGUL_TCOUNT;
            }
            continue;
        }
        const th_idna_decomp_row *row = decomp_row(cp);
        if (row == NULL) {
            out[at++] = cp;
        } else {
            for (uint8_t offset = 0; offset < row->length; offset++) {
                out[at++] = th_idna_decomp_pool[row->offset + offset];
            }
        }
    }
    return at;
}

/* Equal combining classes must retain input order (Unicode canonical ordering, spec 3.11). `ordered` holds `len` code
   points. */
static void nfc_order_run(Py_UCS4 *seq, Py_ssize_t len, Py_UCS4 *ordered) {
    Py_ssize_t offsets[256] = {0};
    for (Py_ssize_t index = 0; index < len; index++) {
        offsets[ccc_of(seq[index])]++;
    }
    Py_ssize_t total = 0;
    for (size_t klass = 0; klass < 256; klass++) {
        const Py_ssize_t count = offsets[klass];
        offsets[klass] = total;
        total += count;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        TH_WORK_STEP();
        ordered[offsets[ccc_of(seq[index])]++] = seq[index];
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        seq[index] = ordered[index];
    }
}

/* Put each maximal run of combining marks (non-zero class) into canonical order: a stable sort by combining class, so
   equal-class marks keep their input order (Unicode canonical ordering, spec 3.11). The two or three marks real text
   has in a row take an insertion sort; a long run found out of order takes nfc_order_run, as in unicode/normalize.c,
   because the insertion sort is O(n^2) in the run and a host can be one run. `scratch` holds `len` code points. */
static void nfc_order(Py_UCS4 *seq, Py_ssize_t len, Py_UCS4 *scratch) {
    Py_ssize_t run_start = 0;
    for (Py_ssize_t index = 1; index < len; index++) {
        Py_UCS4 cp = seq[index];
        uint8_t klass = ccc_of(cp);
        if (klass == 0) {
            run_start = index + 1;
            continue;
        }
        Py_ssize_t back = index;
        if (ccc_of(seq[index - 1]) > klass) {
            if (index - run_start >= 32) {
                Py_ssize_t end = index + 1;
                while (end < len && ccc_of(seq[end]) != 0) {
                    end++;
                }
                nfc_order_run(seq + run_start, end - run_start, scratch);
                index = end - 1;
                continue;
            }
            do {
                TH_WORK_STEP();
                seq[back] = seq[back - 1];
                back--;
            } while (back > 0 && ccc_of(seq[back - 1]) > klass);
        }
        seq[back] = cp;
    }
}

/* Canonically compose the ordered sequence in place, the second half of NFC: fold each combining mark that is not
   blocked into its starter (Hangul arithmetically, everything else through the table). Returns the composed length. A
   mark is blocked when a preceding mark of equal-or-higher class sits between it and the starter (last_class tracks
   that). */
static Py_ssize_t nfc_compose(Py_UCS4 *seq, Py_ssize_t len) {
    Py_ssize_t out_len = 0;
    Py_ssize_t starter_at = -1;
    int last_class = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 cp = seq[index];
        int klass = ccc_of(cp);
        if (starter_at >= 0 && (last_class < klass || last_class == 0)) {
            Py_UCS4 composed = pair_compose(seq[starter_at], cp);
            if (composed != 0) {
                seq[starter_at] = composed;
                continue;
            }
        }
        if (klass == 0) {
            starter_at = out_len;
        }
        last_class = klass;
        seq[out_len++] = cp;
    }
    return out_len;
}

/* Normalize map-output input[0,in_len) to NFC in `out` (holding at most in_len * longest-decomposition code points,
   and as many again past them for nfc_order's scratch); returns the normalized length. */
static Py_ssize_t nfc(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 *out) {
    Py_ssize_t len = nfc_decompose(input, in_len, out);
    nfc_order(out, len, out + len);
    return nfc_compose(out, len);
}

/* RFC 3492 bias adaptation. */
static Py_ssize_t puny_adapt(Py_ssize_t delta, Py_ssize_t numpoints, int first) {
    delta = first ? delta / PUNY_DAMP : delta / 2;
    delta += delta / numpoints;
    Py_ssize_t k = 0;
    while (delta > (PUNY_BASE - PUNY_TMIN) * PUNY_TMAX / 2) {
        delta /= PUNY_BASE - PUNY_TMIN;
        k += PUNY_BASE;
    }
    return k + (PUNY_BASE - PUNY_TMIN + 1) * delta / (delta + PUNY_SKEW);
}

/* The threshold t for round k of the generalized variable-length integer, clamped to [TMIN, TMAX] around the bias. */
static Py_ssize_t puny_threshold(Py_ssize_t k, Py_ssize_t bias) {
    if (k <= bias + PUNY_TMIN) {
        return PUNY_TMIN;
    }
    if (k >= bias + PUNY_TMAX) {
        return PUNY_TMAX;
    }
    return k - bias;
}

/* Write `q` into out at `at` as a generalized variable-length integer under `bias`; returns the new write offset. */
static inline Py_ssize_t puny_write_delta(Py_UCS4 *out, Py_ssize_t at, Py_ssize_t q, Py_ssize_t bias) {
    for (Py_ssize_t k = PUNY_BASE;; k += PUNY_BASE) {
        Py_ssize_t t = puny_threshold(k, bias);
        if (q < t) {
            break;
        }
        Py_ssize_t digit = t + (q - t) % (PUNY_BASE - t);
        out[at++] = digit < 26 ? (Py_UCS4)('a' + digit) : (Py_UCS4)('0' + digit - 26);
        q = (q - t) / (PUNY_BASE - t);
    }
    out[at++] = (Py_UCS4)('a' + q); /* the loop broke on q < t <= TMAX, so the final digit is a letter */
    return at;
}

/* qsort order of puny_encode_sorted's keys, ascending. */
static int puny_key_order(const void *left, const void *right) {
    uint64_t first = *(const uint64_t *)left;
    uint64_t second = *(const uint64_t *)right;
    return (first > second) - (first < second);
}

/* puny_encode for a label of at least PUNY_SORTED_MIN code points, with the `basic` code points already written to
   out[0,at). It visits the non-basic code points in the order the decoder inserts them, by value and then by position
   (`keys`, sorted once), and writes for each the distance the decoder's state <n, i> travels from the previous one:
   (value - n) * (handled + 1) + rank - i, where rank counts the already handled code points left of it. `left_of` is a
   binary indexed tree over the positions that answers rank, so a label costs O(len log len). */
static Py_ssize_t puny_encode_sorted(const Py_UCS4 *label, Py_ssize_t len, Py_ssize_t basic, Py_UCS4 *out,
                                     Py_ssize_t at) {
    uint64_t *keys = TH_IDNA_ALLOC((size_t)len * sizeof(uint64_t) + (size_t)(len + 1) * sizeof(uint32_t));
    if (keys == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PUNY_NO_MEMORY; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    uint32_t *left_of = (uint32_t *)(keys + len);
    Py_ssize_t pending = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        left_of[index + 1] = label[index] < PUNY_INITIAL_N; /* the basic code points are handled from the start */
        if (label[index] >= PUNY_INITIAL_N) {
            keys[pending++] = ((uint64_t)label[index] << 32) | (uint64_t)index;
        }
    }
    for (Py_ssize_t node = 1; node <= len; node++) { /* fold each node into its parent: the tree, built in O(len) */
        Py_ssize_t parent = node + (node & -node);
        if (parent <= len) {
            left_of[parent] += left_of[node];
        }
    }
    qsort(keys, (size_t)pending, sizeof(uint64_t), puny_key_order);
    Py_UCS4 n = PUNY_INITIAL_N;
    Py_ssize_t i = 0;
    Py_ssize_t bias = PUNY_INITIAL_BIAS;
    Py_ssize_t handled = basic;
    for (Py_ssize_t turn = 0; turn < pending; turn++) {
        TH_WORK_STEP();
        Py_UCS4 value = (Py_UCS4)(keys[turn] >> 32);
        Py_ssize_t index = (Py_ssize_t)(keys[turn] & 0xFFFFFFFF);
        Py_ssize_t rank = 0;
        for (Py_ssize_t node = index; node > 0; node -= node & -node) {
            rank += left_of[node];
        }
        /* The sample encoder reaches the same sum by adding non-negative terms and fails once the running total passes
           PUNY_MAXINT, which is when the whole does; under 2^21 * 2^15, it fits 64 bits. */
        int64_t delta = (int64_t)(value - n) * (handled + 1) + rank - i;
        if (delta > PUNY_MAXINT) {
            at = PUNY_INVALID;
            break;
        }
        at = puny_write_delta(out, at, (Py_ssize_t)delta, bias);
        bias = puny_adapt((Py_ssize_t)delta, handled + 1, handled == basic);
        for (Py_ssize_t node = index + 1; node <= len; node += node & -node) {
            left_of[node]++;
        }
        n = value;
        i = rank + 1;
        handled++;
    }
    TH_IDNA_FREE(keys);
    return at;
}

/* Encode label[0,len) with RFC 3492 into `out` as ASCII code points (no "xn--" prefix); returns the output length,
   PUNY_INVALID when the arithmetic would overflow (the A3 punycode failure), or PUNY_NO_MEMORY. The sample loop below
   rescans the label for each distinct code point, so a long label goes to puny_encode_sorted. */
static Py_ssize_t puny_encode(const Py_UCS4 *label, Py_ssize_t len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    Py_ssize_t handled = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (label[index] < PUNY_INITIAL_N) {
            out[at++] = label[index];
            handled++;
        }
    }
    Py_ssize_t basic = handled;
    if (basic > 0) {
        out[at++] = PUNY_DELIMITER;
    }
    if (len >= PUNY_SORTED_MIN) {
        return puny_encode_sorted(label, len, basic, out, at);
    }
    /* Under PUNY_SORTED_MIN code points a delta stays below 0x110000 * PUNY_SORTED_MIN + PUNY_SORTED_MIN, far from
       PUNY_MAXINT, so this loop needs no overflow check. */
    Py_UCS4 n = PUNY_INITIAL_N;
    Py_ssize_t delta = 0;
    Py_ssize_t bias = PUNY_INITIAL_BIAS;
    while (handled < len) {
        Py_UCS4 smallest = 0x10FFFF + 1;
        for (Py_ssize_t index = 0; index < len; index++) {
            if (label[index] >= n && label[index] < smallest) {
                smallest = label[index];
            }
        }
        delta += (smallest - n) * (handled + 1);
        n = smallest;
        for (Py_ssize_t index = 0; index < len; index++) {
            TH_WORK_STEP();
            if (label[index] < n) {
                delta++;
            } else if (label[index] == n) {
                at = puny_write_delta(out, at, delta, bias);
                bias = puny_adapt(delta, handled + 1, handled == basic);
                delta = 0;
                handled++;
            }
        }
        delta++;
        n++;
    }
    return at;
}

/* The digit value of a base-36 code point (a-z -> 0..25, 0-9 -> 26..35), or -1 when it is not a punycode digit. The
   caller only decodes labels the UTS #46 mapping already lowercased, so an uppercase letter never reaches here. */
static int puny_digit(Py_UCS4 cp) {
    if (cp >= 'a' && cp <= 'z') {
        return (int)(cp - 'a');
    }
    if (cp >= '0' && cp <= '9') {
        return (int)(cp - '0') + 26;
    }
    return -1;
}

/* The RFC 3492 decoder loop over input[read,len), growing a decoded label of `at` code points; returns its final
   length, or PUNY_INVALID on malformed input (a non-digit, an overflow, a decoded code point out of range). With
   `inserts` NULL each code point is inserted into `out` at its position, shifting the ones after it, which is O(n^2)
   in the label; otherwise insertion number k is recorded as inserts[k] = position << 32 | code point for puny_place. */
static Py_ssize_t puny_decode_run(const Py_UCS4 *input, Py_ssize_t len, Py_ssize_t read, Py_ssize_t at, Py_UCS4 *out,
                                  uint64_t *inserts) {
    Py_ssize_t basic = at;
    Py_UCS4 n = PUNY_INITIAL_N;
    Py_ssize_t i = 0;
    Py_ssize_t bias = PUNY_INITIAL_BIAS;
    while (read < len) {
        Py_ssize_t old_i = i;
        Py_ssize_t w = 1;
        for (Py_ssize_t k = PUNY_BASE;; k += PUNY_BASE) {
            if (read >= len) {
                return PUNY_INVALID;
            }
            int digit = puny_digit(input[read++]);
            if (digit < 0 || digit > (PUNY_MAXINT - i) / w) {
                return PUNY_INVALID;
            }
            i += digit * w;
            Py_ssize_t t = puny_threshold(k, bias);
            if (digit < t) {
                break;
            }
            /* GCOVR_EXCL_START: the digit-overflow guard above fires first for any w this large -- reaching here needs
               digit >= t >= 1, yet that guard already caps digit at (PUNY_MAXINT - i) / w, which is < 1 once w passes
               PUNY_MAXINT, so w never grows enough to overflow this multiply. */
            if (w > PUNY_MAXINT / (PUNY_BASE - t)) {
                return PUNY_INVALID;
            }
            /* GCOVR_EXCL_STOP */
            w *= PUNY_BASE - t;
        }
        Py_ssize_t out_len = at + 1;
        bias = puny_adapt(i - old_i, out_len, old_i == 0);
        n += (Py_UCS4)(i / out_len);
        i %= out_len;
        if (n > 0x10FFFF || (n >= 0xD800 && n <= 0xDFFF)) {
            return PUNY_INVALID;
        }
        if (inserts != NULL) {
            inserts[at - basic] = ((uint64_t)i << 32) | n;
        } else {
            for (Py_ssize_t shift = at; shift > i; shift--) {
                TH_WORK_STEP();
                out[shift] = out[shift - 1];
            }
            out[i] = n;
        }
        i++;
        at++;
    }
    return at;
}

/* No decoded code point is this large, so puny_place uses it to mark a slot no insertion has taken. */
enum { PUNY_FREE_SLOT = 0xFFFFFFFF };

/* Lay a decoded label of `total` code points out in out[0,total) from the `count` insertions puny_decode_run recorded,
   in the order its shifting inserts leave. Taking out every later insertion gives the label as it stood right after
   this one, where it sat at its recorded position, so walking the insertions last to first, each takes the free slot
   that position counts to. `free_slots` is a binary indexed tree over the slots, so each lookup costs O(log total).
   The basic code points `basic` fill the slots left over, in order. */
static void puny_place(const Py_UCS4 *basic, const uint64_t *inserts, Py_ssize_t count, Py_ssize_t total,
                       uint32_t *free_slots, Py_UCS4 *out) {
    Py_ssize_t top = 1;
    while (top * 2 <= total) {
        top *= 2;
    }
    for (Py_ssize_t node = 1; node <= total; node++) {
        free_slots[node] = (uint32_t)(node & -node); /* every slot free: node covers lowbit(node) slots */
        out[node - 1] = PUNY_FREE_SLOT;
    }
    for (Py_ssize_t turn = count - 1; turn >= 0; turn--) {
        TH_WORK_STEP();
        Py_ssize_t wanted = (Py_ssize_t)(inserts[turn] >> 32) + 1;
        Py_ssize_t slot = 0;
        for (Py_ssize_t step = top; step > 0; step /= 2) {
            if (slot + step <= total && (Py_ssize_t)free_slots[slot + step] < wanted) {
                slot += step;
                wanted -= (Py_ssize_t)free_slots[slot];
            }
        }
        out[slot] = (Py_UCS4)(inserts[turn] & 0xFFFFFFFF);
        for (Py_ssize_t node = slot + 1; node <= total; node += node & -node) {
            free_slots[node]--;
        }
    }
    Py_ssize_t next = 0;
    for (Py_ssize_t index = 0; index < total; index++) {
        if (out[index] == PUNY_FREE_SLOT) {
            out[index] = basic[next++];
        }
    }
}

/* Decode the RFC 3492 payload input[0,len) (the ASCII code points after "xn--") into `out`; returns the decoded length,
   PUNY_INVALID on any malformed input, or PUNY_NO_MEMORY. The caller only decodes an all-ASCII label, so the basic run
   is copied without a range check. `out` holds at most len+1 code points, since decoding never emits more code points
   than the input has. A payload of PUNY_SORTED_MIN code points or more records its insertions and lays them out with
   puny_place instead of shifting the label on each one. */
static Py_ssize_t puny_decode(const Py_UCS4 *input, Py_ssize_t len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    Py_ssize_t last_delim = -1;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (input[index] == PUNY_DELIMITER) {
            last_delim = index;
        }
    }
    Py_ssize_t read = 0;
    if (last_delim > 0) { /* a delimiter with no basic run before it is not consumed, so its '-' decodes as a digit */
        for (; read < last_delim; read++) {
            out[at++] = input[read];
        }
        read = last_delim + 1;
    }
    if (len < PUNY_SORTED_MIN) {
        return puny_decode_run(input, len, read, at, out, NULL);
    }
    uint64_t *inserts = TH_IDNA_ALLOC((size_t)len * sizeof(uint64_t) + (size_t)(len + 1) * sizeof(uint32_t));
    if (inserts == NULL) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PUNY_NO_MEMORY; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t total = puny_decode_run(input, len, read, at, out, inserts);
    if (total >= 0) {
        puny_place(input, inserts, total - at, total, (uint32_t *)(inserts + len), out);
    }
    TH_IDNA_FREE(inserts);
    return total;
}

/* Whether span[0,len) is a code-point-for-code-point ASCII "xn--" prefix (mapping has already lowercased any letters).
 */
static int has_xn_prefix(const Py_UCS4 *span, Py_ssize_t len) {
    return len >= 4 && span[0] == 'x' && span[1] == 'n' && span[2] == '-' && span[3] == '-';
}

/* Whether span[0,len) is all ASCII, so it needs neither punycode encoding nor decoding beyond an "xn--" probe. */
static int span_is_ascii(const Py_UCS4 *span, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (span[index] >= PUNY_INITIAL_N) {
            return 0;
        }
    }
    return 1;
}

/* Copy span[0,len) verbatim into out at `at`; returns the new write offset. */
static Py_ssize_t emit_span(Py_UCS4 *out, Py_ssize_t at, const Py_UCS4 *span, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        out[at++] = span[index];
    }
    return at;
}

/* Encode the non-ASCII label span[0,len) as "xn--"+punycode into out at `at`; returns the new write offset, or
   puny_encode's failure. */
static Py_ssize_t emit_encoded(Py_UCS4 *out, Py_ssize_t at, const Py_UCS4 *span, Py_ssize_t len, Py_UCS4 *scratch) {
    Py_ssize_t encoded = puny_encode(span, len, scratch);
    if (encoded < 0) {
        return encoded;
    }
    out[at++] = 'x';
    out[at++] = 'n';
    out[at++] = '-';
    out[at++] = '-';
    return emit_span(out, at, scratch, encoded);
}

/* Convert one label span[0,len) to ASCII, writing into out at `at`; returns the new write offset, PUNY_INVALID when the
   label holds a code point punycode cannot encode, or PUNY_NO_MEMORY. A non-ASCII label is encoded; an ASCII "xn--"
   label is decoded to validate and canonicalize it (kept verbatim when the decoder rejects it); any other ASCII label
   passes through. */
static Py_ssize_t emit_label(Py_UCS4 *out, Py_ssize_t at, const Py_UCS4 *span, Py_ssize_t len, Py_UCS4 *scratch) {
    if (!span_is_ascii(span, len)) {
        return emit_encoded(out, at, span, len, scratch);
    }
    if (!has_xn_prefix(span, len)) {
        return emit_span(out, at, span, len);
    }
    Py_ssize_t decoded = puny_decode(span + 4, len - 4, scratch);
    if (decoded == PUNY_NO_MEMORY) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PUNY_NO_MEMORY;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (decoded < 0) {
        return emit_span(out, at, span, len); /* the decoder rejected it: keep the label verbatim (advisory error) */
    }
    if (span_is_ascii(scratch, decoded)) {
        /* A punycode label must decode to at least one non-ASCII code point; one that decodes to only ASCII is a UTS
           #46 step-4 (P4) validity failure -- the RUSTSEC-2024-0421 / CVE-2024-12224 equivalence bypass, where an
           "xn--" label and its bare-ASCII decoding would canonicalize to the same host. Keep such a label verbatim
           (the same advisory-error disposition a failed decode gets), except an empty decode, whose empty label the
           conformance vectors resolve to "". */
        return decoded == 0 ? at : emit_span(out, at, span, len);
    }
    return emit_encoded(out, at, scratch, decoded, scratch + decoded);
}

/* The bidi class and joining type rows of data/idna_table.h, in the order tools/generate_idna.py numbers them. */
typedef enum {
    BIDI_L,
    BIDI_R,
    BIDI_AL,
    BIDI_EN,
    BIDI_ES,
    BIDI_ET,
    BIDI_AN,
    BIDI_CS,
    BIDI_NSM,
    BIDI_BN,
    BIDI_B,
    BIDI_S,
    BIDI_WS,
    BIDI_ON,
    BIDI_LRE,
    BIDI_LRO,
    BIDI_RLE,
    BIDI_RLO,
    BIDI_PDF,
    BIDI_LRI,
    BIDI_RLI,
    BIDI_FSI,
    BIDI_PDI,
} idna_bidi_class;

typedef enum {
    JOIN_U,
    JOIN_C,
    JOIN_D,
    JOIN_L,
    JOIN_R,
    JOIN_T,
} idna_joining_type;

#define IDNA_VIRAMA_CCC 9
#define IDNA_ZWNJ 0x200C
#define IDNA_ZWJ 0x200D

static uint8_t property_value(const th_idna_property_row *rows, int count, Py_UCS4 cp) {
    int lo = 0;
    int hi = count - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (cp < rows[mid].first) {
            hi = mid - 1;
        } else if (cp > rows[mid].last) {
            lo = mid + 1;
        } else {
            return rows[mid].value;
        }
    }
    return 0;
}

static uint8_t bidi_of(Py_UCS4 cp) {
    return property_value(th_idna_bidi, th_idna_bidi_count, cp);
}

static uint8_t joining_of(Py_UCS4 cp) {
    return property_value(th_idna_joining, th_idna_joining_count, cp);
}

static uint8_t mark_of(Py_UCS4 cp) {
    return property_value(th_idna_mark, th_idna_mark_count, cp);
}

/* RFC 5893 section 2, the Bidi rule UTS #46 applies with CheckBidi: a label with no R, AL or AN is left-to-right and
   passes. Any other label must start with R or AL: a left-to-right start admits no R, AL or AN, so it fails. */
static int bidi_label_ok(const Py_UCS4 *label, Py_ssize_t len) {
    int bidi_label = 0;
    for (Py_ssize_t index = 0; index < len && !bidi_label; index++) {
        uint8_t klass = bidi_of(label[index]);
        bidi_label = klass == BIDI_R || klass == BIDI_AL || klass == BIDI_AN;
    }
    if (!bidi_label) {
        return 1;
    }
    uint8_t first = bidi_of(label[0]);
    if (first != BIDI_R && first != BIDI_AL) {
        return 0;
    }
    Py_ssize_t last = len - 1;
    while (last > 0 && bidi_of(label[last]) == BIDI_NSM) {
        last--;
    }
    uint8_t ending = bidi_of(label[last]);
    int saw_en = 0;
    int saw_an = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        uint8_t klass = bidi_of(label[index]);
        int allowed = klass == BIDI_R || klass == BIDI_AL || klass == BIDI_AN || klass == BIDI_EN || klass == BIDI_ES ||
                      klass == BIDI_CS || klass == BIDI_ET || klass == BIDI_ON || klass == BIDI_BN || klass == BIDI_NSM;
        if (!allowed) {
            return 0;
        }
        saw_en |= klass == BIDI_EN;
        saw_an |= klass == BIDI_AN;
    }
    return (ending == BIDI_R || ending == BIDI_AL || ending == BIDI_EN || ending == BIDI_AN) && !(saw_en && saw_an);
}

/* RFC 5892 appendix A.1: a ZWNJ at `at` is valid after a virama, or between a joining letter (L or D, skipping
   transparent T) on its left and a right-joining one (R or D, skipping T) on its right. */
static int zwnj_joins(const Py_UCS4 *label, Py_ssize_t len, Py_ssize_t at) {
    Py_ssize_t before = at;
    while (before > 0 && joining_of(label[before - 1]) == JOIN_T) {
        before--;
    }
    if (before == 0 || (joining_of(label[before - 1]) != JOIN_L && joining_of(label[before - 1]) != JOIN_D)) {
        return 0;
    }
    Py_ssize_t after = at + 1;
    while (after < len && joining_of(label[after]) == JOIN_T) {
        after++;
    }
    return after < len && (joining_of(label[after]) == JOIN_R || joining_of(label[after]) == JOIN_D);
}

/* RFC 5892 appendix A.1 and A.2 (CheckJoiners): a ZWJ needs a virama before it, a ZWNJ a virama or a joining context.
 */
static int joiners_ok(const Py_UCS4 *label, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        if (label[index] != IDNA_ZWNJ && label[index] != IDNA_ZWJ) {
            continue;
        }
        if (index > 0 && ccc_of(label[index - 1]) == IDNA_VIRAMA_CCC) {
            continue;
        }
        if (label[index] == IDNA_ZWJ || !zwnj_joins(label, len, index)) {
            return 0;
        }
    }
    return 1;
}

/* Whether a non-empty label is a valid U-label: each code point maps to itself, the label is in NFC, and it passes
   the Bidi and joiner rules. `scratch` holds the NFC comparison and needs room for eight code points per input. */
static int label_valid(const Py_UCS4 *label, Py_ssize_t len, Py_UCS4 *scratch) {
    if (mark_of(label[0]) != 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (map_row(label[index])->status != 0) {
            return 0;
        }
    }
    if (!nfc_is_normalized(label, len)) {
        Py_ssize_t normal = nfc(label, len, scratch);
        if (normal != len) {
            return 0;
        }
        for (Py_ssize_t index = 0; index < len; index++) {
            if (scratch[index] != label[index]) {
                return 0;
            }
        }
    }
    return bidi_label_ok(label, len) && joiners_ok(label, len);
}

/* UTS #46 validity for a host that has non-ASCII input: every label is a valid U-label, and an "xn--" label is valid
   punycode whose decoding is a valid U-label that is not ASCII. An all-ASCII host skips these checks, as WHATWG and
   Node do, so an ASCII "xn--" label keeps the verbatim advisory handling emit_label gives it. Returns 1 when valid, 0
   when a label is not, and -1 when a buffer cannot be allocated. */
static int host_labels_valid(const Py_UCS4 *norm, Py_ssize_t norm_len, Py_UCS4 *scratch) {
    Py_ssize_t label_start = 0;
    for (Py_ssize_t index = 0; index <= norm_len; index++) {
        if (index < norm_len && norm[index] != '.') {
            continue;
        }
        const Py_UCS4 *label = norm + label_start;
        Py_ssize_t len = index - label_start;
        label_start = index + 1;
        if (!has_xn_prefix(label, len)) {
            if (!span_is_ascii(label, len) && !label_valid(label, len, scratch)) {
                return 0;
            }
            continue;
        }
        if (!span_is_ascii(label, len)) {
            return 0;
        }
        Py_ssize_t decoded = puny_decode(label + 4, len - 4, scratch);
        if (decoded == PUNY_NO_MEMORY) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;                   /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (decoded <= 0 || span_is_ascii(scratch, decoded) || !label_valid(scratch, decoded, scratch + decoded)) {
            return 0;
        }
    }
    return 1;
}

typedef enum {
    IDNA_OK,
    IDNA_DISALLOWED,
    IDNA_ENCODING,
    IDNA_TOO_LONG,
    IDNA_NO_MEMORY,
} idna_status;

static idna_status idna_to_ascii(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 **output, Py_ssize_t *output_len) {
    Py_UCS4 *mapped = TH_IDNA_ALLOC((size_t)(in_len * 18 + 1) * sizeof(Py_UCS4));
    if (mapped == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return IDNA_NO_MEMORY; /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t norm_len = map_host(input, in_len, mapped);
    if (norm_len < 0) {
        TH_IDNA_FREE(mapped);
        return IDNA_DISALLOWED;
    }
    if (norm_len > TH_IDNA_MAX_INPUT) { /* the mapped length is the one normalization allocates and works in */
        TH_IDNA_FREE(mapped);
        return IDNA_TOO_LONG;
    }
    const Py_UCS4 *norm = mapped;
    Py_UCS4 *normalized = NULL;
    if (!nfc_is_normalized(mapped, norm_len)) {
        normalized = TH_IDNA_ALLOC((size_t)(norm_len * 8 + 1) * sizeof(Py_UCS4));
        if (normalized == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            TH_IDNA_FREE(mapped);  /* GCOVR_EXCL_LINE: allocation-failure path */
            return IDNA_NO_MEMORY; /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        norm_len = nfc(mapped, norm_len, normalized);
        norm = normalized;
        if (norm_len > TH_IDNA_MAX_INPUT) { /* the normalized length is the one the punycode passes work in */
            TH_IDNA_FREE(mapped);
            TH_IDNA_FREE(normalized);
            return IDNA_TOO_LONG;
        }
    }
    /* 16x bounds both punycode expansion and decoded ACE re-encoding scratch. */
    Py_UCS4 *out = TH_IDNA_ALLOC((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    Py_UCS4 *scratch = TH_IDNA_ALLOC((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    if (out == NULL || scratch == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        TH_IDNA_FREE(mapped);             /* GCOVR_EXCL_LINE: allocation-failure path */
        TH_IDNA_FREE(normalized);         /* GCOVR_EXCL_LINE: allocation-failure path */
        TH_IDNA_FREE(out);                /* GCOVR_EXCL_LINE: allocation-failure path */
        TH_IDNA_FREE(scratch);            /* GCOVR_EXCL_LINE: allocation-failure path */
        return IDNA_NO_MEMORY;            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    idna_status status = IDNA_OK;
    if (!span_is_ascii(input, in_len)) {
        int valid = host_labels_valid(norm, norm_len, scratch);
        if (valid < 0) {             /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            status = IDNA_NO_MEMORY; /* GCOVR_EXCL_LINE: allocation-failure path */
        } else if (!valid) {
            status = IDNA_DISALLOWED;
        }
    }
    Py_ssize_t at = 0;
    Py_ssize_t label_start = 0;
    for (Py_ssize_t index = 0; status == IDNA_OK && index <= norm_len; index++) {
        if (index < norm_len && norm[index] != '.') {
            continue;
        }
        if (label_start > 0) {
            out[at++] = '.';
        }
        at = emit_label(out, at, norm + label_start, index - label_start, scratch);
        if (at < 0) {
            status = at == PUNY_NO_MEMORY ? IDNA_NO_MEMORY : IDNA_ENCODING; /* GCOVR_EXCL_BR_LINE: allocation failure */
            break;
        }
        label_start = index + 1;
    }
    TH_IDNA_FREE(mapped);
    TH_IDNA_FREE(normalized);
    TH_IDNA_FREE(scratch);
    if (status == IDNA_OK) {
        *output = out;
        *output_len = at;
    } else {
        TH_IDNA_FREE(out);
    }
    return status;
}

#ifndef TH_IDNA_STANDALONE
PyObject *th_url_to_ascii(PyObject *host) {
    Py_ssize_t in_len = PyUnicode_GET_LENGTH(host);
    if (in_len > TH_IDNA_MAX_INPUT) {
        PyErr_Format(PyExc_ValueError, "host of %zd code points exceeds the IDNA input limit of %d; shorten the host",
                     in_len, TH_IDNA_MAX_INPUT);
        return NULL;
    }
    Py_UCS4 *input = PyMem_Malloc((size_t)(in_len + 1) * sizeof(Py_UCS4));
    if (input == NULL) {         /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(host);
    const void *data = PyUnicode_DATA(host);
    for (Py_ssize_t index = 0; index < in_len; index++) {
        input[index] = PyUnicode_READ(kind, data, index);
    }
    Py_UCS4 *output;
    Py_ssize_t output_len;
    idna_status status = idna_to_ascii(input, in_len, &output, &output_len);
    PyMem_Free(input);
    if (status == IDNA_DISALLOWED) {
        PyErr_SetString(PyExc_ValueError, "host contains a code point UTS #46 disallows; remove it");
        return NULL;
    }
    if (status == IDNA_ENCODING) {
        PyErr_SetString(PyExc_ValueError, "host cannot be encoded to ASCII");
        return NULL;
    }
    if (status == IDNA_TOO_LONG) {
        PyErr_Format(PyExc_ValueError, "host maps past the IDNA input limit of %d code points; shorten the host",
                     TH_IDNA_MAX_INPUT);
        return NULL;
    }
    if (status == IDNA_NO_MEMORY) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory();    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, output, output_len);
    PyMem_Free(output);
    return result;
}

/* _url_to_ascii(host): the shim's domain-to-ASCII entry, a str host in, its ASCII form out; raises ValueError when a
   label carries a code point punycode cannot encode. */
PyObject *turbohtml_url_to_ascii(PyObject *Py_UNUSED(module), PyObject *arg) {
    return th_url_to_ascii(arg);
}
#endif /* TH_IDNA_STANDALONE */
