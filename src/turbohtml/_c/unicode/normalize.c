/* Unicode normalization (UAX #15): the four forms NFC, NFD, NFKC, NFKD over a whole string, the transform stdlib
   unicodedata.normalize does, in C. The pipeline is the textbook three steps over the pinned tables in
   data/normalize_table.h -- decompose each code point (canonically for the C/D forms, compatibly for the K forms),
   put the combining marks into canonical order, then, for the composing forms, recompose -- fronted by a quick check
   that returns already-normalized text untouched so the common case pays only one linear scan.

   The decomposition, combining-class, and composition tables are generated straight from the interpreter's unicodedata
   (tools/generate_normalize.py), so the engine is an exact reimplementation of the same data unicodedata carries;
   Hangul is (de)composed arithmetically (Unicode 3.12) and needs no rows. This shares no table with url/idna.c, whose
   NFC is a private step of ToASCII bound to the UTS #46 mapping; the two stay decoupled. */

#include "core/common.h"

#include "data/normalize_table.h"

enum { TH_NFC = 0, TH_NFD = 1, TH_NFKC = 2, TH_NFKD = 3 };

enum { TH_QC_YES = 0, TH_QC_NO = 1, TH_QC_MAYBE = 2 };

/* Unicode 3.12 Hangul syllable constants: the 11172 precomposed syllables (de)compose arithmetically, no table rows. */
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

/* The canonical combining class of `cp`, 0 when the sorted table has no row for it (the default, every starter). */
static uint8_t ccc_of(Py_UCS4 cp) {
    int lo = 0;
    int hi = th_norm_ccc_count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (th_norm_ccc[mid].code < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < th_norm_ccc_count && th_norm_ccc[lo].code == cp) {
        return th_norm_ccc[lo].ccc;
    }
    return 0;
}

/* The decomposition row for `cp` in a table of `count` rows, or NULL when the code point is absent. */
static const th_norm_decomp_row *decomp_row(const th_norm_decomp_row *table, int count, Py_UCS4 cp) {
    int lo = 0;
    int hi = count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (table[mid].code < cp) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < count && table[lo].code == cp) {
        return &table[lo];
    }
    return NULL;
}

/* Whether `cp` has a decomposition at the requested depth (Hangul aside): the quick check's decompose-form No test.
   NFKD looks in the compatibility table first, then the canonical one it does not duplicate; NFD looks canonical only.
 */
static int decomposes(Py_UCS4 cp, int compat) {
    if (compat && decomp_row(th_norm_compat, th_norm_compat_count, cp) != NULL) {
        return 1;
    }
    return decomp_row(th_norm_canon, th_norm_canon_count, cp) != NULL;
}

/* The tabled canonical composition of the pair (first, second), or 0 when the sorted table pairs them into nothing; 0
   is a safe "no composition" sentinel because U+0000 is never a composition target. */
static Py_UCS4 table_compose(Py_UCS4 first, Py_UCS4 second) {
    int lo = 0;
    int hi = th_norm_comp_count;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (th_norm_comp[mid].first < first ||
            (th_norm_comp[mid].first == first && th_norm_comp[mid].second < second)) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < th_norm_comp_count && th_norm_comp[lo].first == first && th_norm_comp[lo].second == second) {
        return th_norm_comp[lo].composed;
    }
    return 0;
}

/* The canonical composition of (first, second), 0 when they do not compose: a leading + vowel jamo into an LV syllable,
   an LV syllable + trailing jamo into an LVT syllable (both the Unicode 3.12 arithmetic), or the tabled pair. Each jamo
   range is probed with one unsigned comparison, so a malformed pair underflows past the count and falls to the table.
 */
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

/* The NFC (column 0) or NFKC (column 1) quick-check value of `cp`: Yes when the sorted range table has no row. */
static uint8_t qc_of(Py_UCS4 cp, int nfkc) {
    int lo = 0;
    int hi = th_norm_qc_count - 1;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (cp > th_norm_qc[mid].last) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    const th_norm_qc_row *row = &th_norm_qc[lo];
    if (cp >= row->first && cp <= row->last) {
        return nfkc ? row->nfkc : row->nfc;
    }
    return TH_QC_YES;
}

/* The quick-check value of `cp` for `form`. The decompose forms have no Maybe: a code point is No exactly when it
   decomposes (has a row, or is a Hangul syllable), Yes otherwise. */
static uint8_t quick_value(Py_UCS4 cp, int form) {
    if (form == TH_NFC || form == TH_NFKC) {
        return qc_of(cp, form == TH_NFKC);
    }
    Py_UCS4 sindex = cp - HANGUL_SBASE;
    if (sindex < HANGUL_SCOUNT || decomposes(cp, form == TH_NFKD)) {
        return TH_QC_NO;
    }
    return TH_QC_YES;
}

/* Whether `input` is already in `form`: TH_QC_YES normalized, TH_QC_NO not, TH_QC_MAYBE undecided (a mark that may fold
   into a preceding starter, resolved by normalizing and comparing). Any combining mark out of canonical order settles
   it as not-normalized straight away. UAX #15 quick check. */
static int quick_check(int kind, const void *data, Py_ssize_t len, int form) {
    uint8_t last_class = 0;
    int result = TH_QC_YES;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 cp = PyUnicode_READ(kind, data, index);
        uint8_t klass = ccc_of(cp);
        if (klass != 0 && klass < last_class) {
            return TH_QC_NO;
        }
        uint8_t value = quick_value(cp, form);
        if (value == TH_QC_NO) {
            return TH_QC_NO;
        }
        if (value == TH_QC_MAYBE) {
            result = TH_QC_MAYBE;
        }
        last_class = klass;
    }
    return result;
}

/* Decompose input[0,len) into `out` (Hangul arithmetically, everything else through the tables at the `compat` depth);
   `out` holds at most len * TH_NORM_MAX_EXPANSION code points. Returns the decomposed length. */
static Py_ssize_t decompose(int kind, const void *data, Py_ssize_t len, Py_UCS4 *out, int compat) {
    Py_ssize_t at = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 cp = PyUnicode_READ(kind, data, index);
        Py_UCS4 sindex = cp - HANGUL_SBASE;
        if (sindex < HANGUL_SCOUNT) {
            out[at++] = HANGUL_LBASE + sindex / HANGUL_NCOUNT;
            out[at++] = HANGUL_VBASE + sindex % HANGUL_NCOUNT / HANGUL_TCOUNT;
            if (sindex % HANGUL_TCOUNT != 0) {
                out[at++] = HANGUL_TBASE + sindex % HANGUL_TCOUNT;
            }
            continue;
        }
        const th_norm_decomp_row *row = NULL;
        const uint32_t *pool = th_norm_canon_pool;
        if (compat && (row = decomp_row(th_norm_compat, th_norm_compat_count, cp)) != NULL) {
            pool = th_norm_compat_pool;
        } else {
            row = decomp_row(th_norm_canon, th_norm_canon_count, cp);
        }
        if (row == NULL) {
            out[at++] = cp;
        } else {
            for (uint8_t offset = 0; offset < row->length; offset++) {
                out[at++] = pool[row->offset + offset];
            }
        }
    }
    return at;
}

/* Put each maximal run of combining marks into canonical order: a stable insertion sort by combining class so equal
   marks keep input order (Unicode canonical ordering, spec 3.11). */
static void reorder(Py_UCS4 *seq, Py_ssize_t len) {
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

/* Recompose the ordered sequence in place: fold each unblocked combining mark into its starter (Hangul arithmetically,
   everything else through the table). A mark is blocked when a preceding mark of equal-or-higher class stands between
   it and the starter (last_class tracks that). Returns the composed length. */
static Py_ssize_t compose(Py_UCS4 *seq, Py_ssize_t len) {
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

/* Build a str from the code points buf[0,len), binning the width by the widest code point seen. */
static PyObject *build_result(const Py_UCS4 *buf, Py_ssize_t len) {
    Py_UCS4 maxchar = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (buf[index] > maxchar) {
            maxchar = buf[index];
        }
    }
    PyObject *result = PyUnicode_New(len, maxchar);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(result);
    void *data = PyUnicode_DATA(result);
    for (Py_ssize_t index = 0; index < len; index++) {
        PyUnicode_WRITE(kind, data, index, buf[index]);
    }
    return result;
}

/* Normalize input[0,len) to `form`, returning a new str. Runs the full decompose -> reorder -> (compose) pipeline over
   a scratch buffer bounded by len * TH_NORM_MAX_EXPANSION. */
static PyObject *normalize_full(int kind, const void *data, Py_ssize_t len, int form) {
    int compat = form == TH_NFKC || form == TH_NFKD;
    /* len is a code-point count from a live str, far under the overflow bound; keep the guard on one line for the gate
     */
    if (len > PY_SSIZE_T_MAX / (Py_ssize_t)(TH_NORM_MAX_EXPANSION * sizeof(Py_UCS4))) { /* GCOVR_EXCL_BR_LINE */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: a str this long cannot be allocated to reach here */
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)len * TH_NORM_MAX_EXPANSION * sizeof(Py_UCS4));
    if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = decompose(kind, data, len, buf, compat);
    reorder(buf, count);
    if (form == TH_NFC || form == TH_NFKC) {
        count = compose(buf, count);
    }
    PyObject *result = build_result(buf, count);
    PyMem_Free(buf);
    return result;
}

/* _normalize(form, text): the four forms selected by form 0..3 (NFC, NFD, NFKC, NFKD). Already-normalized text is
   returned as the same object; otherwise the full pipeline runs. */
PyObject *turbohtml_normalize(PyObject *Py_UNUSED(module), PyObject *args) {
    int form = 0;
    PyObject *text = NULL;
    if (!PyArg_ParseTuple(args, "iU", &form, &text)) {
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    if (quick_check(kind, data, len, form) == TH_QC_YES) {
        return Py_NewRef(text);
    }
    return normalize_full(kind, data, len, form);
}

/* _is_normalized(form, text): True when text is already in form. A quick-check Maybe is settled by normalizing and
   comparing, exactly as unicodedata.is_normalized does. */
PyObject *turbohtml_is_normalized(PyObject *Py_UNUSED(module), PyObject *args) {
    int form = 0;
    PyObject *text = NULL;
    if (!PyArg_ParseTuple(args, "iU", &form, &text)) {
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    int quick = quick_check(kind, data, len, form);
    if (quick == TH_QC_YES) {
        Py_RETURN_TRUE;
    }
    if (quick == TH_QC_NO) {
        Py_RETURN_FALSE;
    }
    PyObject *normalized = normalize_full(kind, data, len, form);
    if (normalized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;          /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int equal = PyUnicode_Compare(normalized, text) == 0;
    Py_DECREF(normalized);
    if (equal) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}
