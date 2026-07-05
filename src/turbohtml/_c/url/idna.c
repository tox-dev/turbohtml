/* Internationalized host ToASCII: turn a Unicode host into its ASCII (punycode) form the way the WHATWG URL Standard's
   host parser does, the domain-to-ASCII step the _urls.py cleaner delegates here instead of Python's IDNA-2003 codec
   (tox-dev/turbohtml#478).

   The WHATWG host parser runs Unicode IDNA ToASCII with Transitional_Processing=false and, for the non-strict cleaner
   this backs, UseSTD3ASCIIRules=false -- that is UTS #46, not the older IDNA-2003 str.encode("idna") the shim used to
   reach, which mis-handles ess-zett and final sigma. Processing is three steps over the pinned Unicode tables in
   data/idna_table.h: map each code point (lowercasing, fold-away, or drop), normalize the result to NFC, then per label
   punycode-encode any that carries a non-ASCII code point. An already-ASCII "xn--" label is decoded to validate and
   canonicalize it, and a label the decoder rejects is kept verbatim (an advisory error the best-effort cleaner
   ignores), so the mechanical output matches the UTS #46 conformance vectors' toASCII column. Only a code point
   punycode cannot encode (an unpaired surrogate) fails the whole host, which the shim catches to fall back to the
   lowercased form. */

#include "core/common.h"

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

/* The full canonical decomposition row for `cp`, or NULL when it does not decompose (Hangul is handled by the caller).
 */
static const th_idna_decomp_row *decomp_row(Py_UCS4 cp) {
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
   holds at most in_len * (longest mapping) code points; returns the mapped length. */
static Py_ssize_t map_host(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < in_len; index++) {
        const th_idna_map_row *row = map_row(input[index]);
        if (row->status == 1) {
            for (uint8_t offset = 0; offset < row->length; offset++) {
                out[at++] = th_idna_map_pool[row->offset + offset];
            }
        } else if (row->status != 2) { /* 0 keeps the code point; 2 drops it (the ignored set) */
            out[at++] = input[index];
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

/* Put each maximal run of combining marks (non-zero class) into canonical order: a stable sort by combining class, done
   as insertion sort so equal-class marks keep their input order (Unicode canonical ordering, spec 3.11). */
static void nfc_order(Py_UCS4 *seq, Py_ssize_t len) {
    for (Py_ssize_t index = 1; index < len; index++) {
        Py_UCS4 cp = seq[index];
        uint8_t klass = ccc_of(cp);
        if (klass == 0) {
            continue;
        }
        Py_ssize_t back = index;
        while (back > 0 && ccc_of(seq[back - 1]) > klass) {
            seq[back] = seq[back - 1];
            back--;
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

/* Normalize map-output input[0,in_len) to NFC in `out` (holding at most in_len * longest-decomposition code points);
   returns the normalized length. */
static Py_ssize_t nfc(const Py_UCS4 *input, Py_ssize_t in_len, Py_UCS4 *out) {
    Py_ssize_t len = nfc_decompose(input, in_len, out);
    nfc_order(out, len);
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

/* Encode label[0,len) with RFC 3492 into `out` as ASCII code points (no "xn--" prefix); returns the output length, or
   -1 when a code point is an unpaired surrogate or the arithmetic would overflow (the A3 punycode failure). */
static Py_ssize_t puny_encode(const Py_UCS4 *label, Py_ssize_t len, Py_UCS4 *out) {
    Py_ssize_t at = 0;
    Py_ssize_t handled = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (label[index] >= 0xD800 && label[index] <= 0xDFFF) {
            return -1; /* an unpaired surrogate has no scalar value to encode */
        }
        if (label[index] < PUNY_INITIAL_N) {
            out[at++] = label[index];
            handled++;
        }
    }
    Py_ssize_t basic = handled;
    if (basic > 0) {
        out[at++] = PUNY_DELIMITER;
    }
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
        if (smallest - n > (PUNY_MAXINT - delta) / (handled + 1)) {
            return -1;
        }
        delta += (smallest - n) * (handled + 1);
        n = smallest;
        for (Py_ssize_t index = 0; index < len; index++) {
            if (label[index] < n) {
                if (++delta > PUNY_MAXINT) {
                    return -1;
                }
            } else if (label[index] == n) {
                Py_ssize_t q = delta;
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

/* Decode the RFC 3492 payload input[0,len) (the ASCII code points after "xn--") into `out`; returns the decoded length,
   or -1 on any malformed input (a non-digit, an overflow, a decoded code point out of range). The caller only decodes
   an all-ASCII label, so the basic run is copied without a range check. `out` holds at most len+1 code points, since
   decoding never emits more code points than the input has. */
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
    Py_UCS4 n = PUNY_INITIAL_N;
    Py_ssize_t i = 0;
    Py_ssize_t bias = PUNY_INITIAL_BIAS;
    while (read < len) {
        Py_ssize_t old_i = i;
        Py_ssize_t w = 1;
        for (Py_ssize_t k = PUNY_BASE;; k += PUNY_BASE) {
            if (read >= len) {
                return -1;
            }
            int digit = puny_digit(input[read++]);
            if (digit < 0 || digit > (PUNY_MAXINT - i) / w) {
                return -1;
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
                return -1;
            }
            /* GCOVR_EXCL_STOP */
            w *= PUNY_BASE - t;
        }
        Py_ssize_t out_len = at + 1;
        bias = puny_adapt(i - old_i, out_len, old_i == 0);
        n += (Py_UCS4)(i / out_len);
        i %= out_len;
        if (n > 0x10FFFF || (n >= 0xD800 && n <= 0xDFFF)) {
            return -1;
        }
        for (Py_ssize_t shift = at; shift > i; shift--) {
            out[shift] = out[shift - 1];
        }
        out[i++] = n;
        at++;
    }
    return at;
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

/* Encode the non-ASCII label span[0,len) as "xn--"+punycode into out at `at`; returns the new write offset, or -1 when
   punycode cannot encode it. */
static Py_ssize_t emit_encoded(Py_UCS4 *out, Py_ssize_t at, const Py_UCS4 *span, Py_ssize_t len, Py_UCS4 *scratch) {
    Py_ssize_t encoded = puny_encode(span, len, scratch);
    if (encoded < 0) {
        return -1;
    }
    out[at++] = 'x';
    out[at++] = 'n';
    out[at++] = '-';
    out[at++] = '-';
    return emit_span(out, at, scratch, encoded);
}

/* Convert one label span[0,len) to ASCII, writing into out at `at`; returns the new write offset, or -1 when the label
   holds a code point punycode cannot encode. A non-ASCII label is encoded; an ASCII "xn--" label is decoded to validate
   and canonicalize it (kept verbatim when the decoder rejects it); any other ASCII label passes through. */
static Py_ssize_t emit_label(Py_UCS4 *out, Py_ssize_t at, const Py_UCS4 *span, Py_ssize_t len, Py_UCS4 *scratch) {
    if (!span_is_ascii(span, len)) {
        return emit_encoded(out, at, span, len, scratch);
    }
    if (!has_xn_prefix(span, len)) {
        return emit_span(out, at, span, len);
    }
    Py_ssize_t decoded = puny_decode(span + 4, len - 4, scratch);
    if (decoded < 0) {
        return emit_span(out, at, span, len); /* the decoder rejected it: keep the label verbatim (advisory error) */
    }
    if (span_is_ascii(scratch, decoded)) {
        return emit_span(out, at, scratch, decoded);
    }
    return emit_encoded(out, at, scratch, decoded, scratch + decoded);
}

/* th_url_to_ascii(host): the WHATWG domain-to-ASCII engine, a borrowed str host in, a new ASCII str out, or NULL with a
   ValueError set when a label holds a code point punycode cannot encode (an unpaired surrogate). The shim catches that
   to fall back to the lowercased host. */
PyObject *th_url_to_ascii(PyObject *host) {
    Py_ssize_t in_len = PyUnicode_GET_LENGTH(host);
    int kind = PyUnicode_KIND(host);
    const void *data = PyUnicode_DATA(host);
    Py_UCS4 *input = PyMem_Malloc((size_t)(in_len + 1) * sizeof(Py_UCS4));
    Py_UCS4 *mapped = PyMem_Malloc((size_t)(in_len * 18 + 1) * sizeof(Py_UCS4));
    if (input == NULL || mapped == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(input);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(mapped);                /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();           /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < in_len; index++) {
        input[index] = PyUnicode_READ(kind, data, index);
    }
    Py_ssize_t mapped_len = map_host(input, in_len, mapped);
    Py_UCS4 *norm = PyMem_Malloc((size_t)(mapped_len * 4 + 1) * sizeof(Py_UCS4));
    if (norm == NULL) {          /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(input);       /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(mapped);      /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t norm_len = nfc(mapped, mapped_len, norm);
    /* punycode encoding expands a label by at most ~7x (a base-36 delta run per code point), and the xn-- re-encode
       path holds the decoded label plus its re-encoding; 16x the normalized length bounds both with room to spare. */
    Py_UCS4 *out = PyMem_Malloc((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    Py_UCS4 *scratch = PyMem_Malloc((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    if (out == NULL || scratch == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(input);                /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(mapped);               /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(norm);                 /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(out);                  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(scratch);              /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory();          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t at = 0;
    Py_ssize_t label_start = 0;
    int failed = 0;
    for (Py_ssize_t index = 0; index <= norm_len; index++) {
        if (index < norm_len && norm[index] != '.') {
            continue;
        }
        if (label_start > 0) {
            out[at++] = '.';
        }
        at = emit_label(out, at, norm + label_start, index - label_start, scratch);
        if (at < 0) {
            failed = 1;
            break;
        }
        label_start = index + 1;
    }
    PyObject *result;
    if (failed) {
        PyErr_SetString(PyExc_ValueError, "host cannot be encoded to ASCII");
        result = NULL;
    } else {
        result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, at);
    }
    PyMem_Free(input);
    PyMem_Free(mapped);
    PyMem_Free(norm);
    PyMem_Free(out);
    PyMem_Free(scratch);
    return result;
}

/* _url_to_ascii(host): the shim's domain-to-ASCII entry, a str host in, its ASCII form out; raises ValueError when a
   label carries a code point punycode cannot encode. */
PyObject *turbohtml_url_to_ascii(PyObject *Py_UNUSED(module), PyObject *arg) {
    return th_url_to_ascii(arg);
}
