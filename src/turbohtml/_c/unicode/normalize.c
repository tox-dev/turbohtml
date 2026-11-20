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

/* The offset into a two-stage table's blocks for `offset`, the code point less the table's base: the block `index`
   picks, then the low bits of the offset. */
static inline Py_UCS4 two_stage_offset(const uint8_t *index, unsigned shift, Py_UCS4 offset) {
    return ((Py_UCS4)index[offset >> shift] << shift) | (offset & ((1u << shift) - 1));
}

/* The canonical combining class of `cp`, 0 outside the table (every starter and every code point below its base). */
static uint8_t ccc_of(Py_UCS4 cp) {
    Py_UCS4 offset = cp - TH_NORM_CCC_BASE;
    if (offset > TH_NORM_CCC_SPAN) {
        return 0;
    }
    return th_norm_ccc_blocks[two_stage_offset(th_norm_ccc_index, TH_NORM_CCC_SHIFT, offset)];
}

/* The 1-based canonical decomposition row of `cp`, 0 when it has none. */
static uint16_t canon_number(Py_UCS4 cp) {
    Py_UCS4 offset = cp - TH_NORM_CANON_BASE;
    if (offset > TH_NORM_CANON_SPAN) {
        return 0;
    }
    return th_norm_canon_blocks[two_stage_offset(th_norm_canon_index, TH_NORM_CANON_SHIFT, offset)];
}

/* The 1-based compatibility decomposition row of `cp`, 0 when it has none. */
static uint16_t compat_number(Py_UCS4 cp) {
    Py_UCS4 offset = cp - TH_NORM_COMPAT_BASE;
    if (offset > TH_NORM_COMPAT_SPAN) {
        return 0;
    }
    return th_norm_compat_blocks[two_stage_offset(th_norm_compat_index, TH_NORM_COMPAT_SHIFT, offset)];
}

/* Whether `cp` has a decomposition at the requested depth (Hangul aside): the quick check's decompose-form No test.
   NFKD looks in the compatibility table first, then the canonical one it does not duplicate; NFD looks canonical only.
 */
static int decomposes(Py_UCS4 cp, int compat) {
    return (compat && compat_number(cp) != 0) || canon_number(cp) != 0;
}

/* The tabled canonical composition of the pair (first, second), or 0 when no pair composes; 0 is a safe "no
   composition" sentinel because U+0000 is never a composition target. */
static Py_UCS4 table_compose(Py_UCS4 first, Py_UCS4 second) {
    if (second < th_norm_comp_second_min) {
        return 0;
    }
    Py_UCS4 offset = first - TH_NORM_COMP_BASE;
    if (offset > TH_NORM_COMP_SPAN) {
        return 0;
    }
    uint16_t group = th_norm_comp_blocks[two_stage_offset(th_norm_comp_index, TH_NORM_COMP_SHIFT, offset)];
    if (group == 0) {
        return 0;
    }
    const th_norm_comp_row *end = th_norm_comp + th_norm_comp_group[group];
    for (const th_norm_comp_row *row = th_norm_comp + th_norm_comp_group[group - 1]; row < end; row++) {
        if (row->second == second) {
            return row->composed;
        }
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

/* The NFC (low two bits) or NFKC (high two bits) quick-check value of `cp`: Yes when the table holds no value. */
static uint8_t qc_of(Py_UCS4 cp, int nfkc) {
    Py_UCS4 offset = cp - TH_NORM_QC_BASE;
    if (offset > TH_NORM_QC_SPAN) {
        return TH_QC_YES;
    }
    uint8_t packed = th_norm_qc_blocks[two_stage_offset(th_norm_qc_index, TH_NORM_QC_SHIFT, offset)];
    return (nfkc ? packed >> 2 : packed) & 3;
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
   it as not-normalized straight away. UAX #15 quick check, stopping at the first code point that is not Yes:
   normalize runs the full pipeline from there either way, and is_normalized settles a Maybe by normalizing. */
static int quick_check(int kind, const void *data, Py_ssize_t len, int form, Py_ssize_t *first_unsettled) {
    uint8_t last_class = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 cp = PyUnicode_READ(kind, data, index);
        /* ASCII is a class-0 starter with a Yes value in every form, so it needs no table lookup. */
        if (cp < 0x80) {
            last_class = 0;
            continue;
        }
        uint8_t klass = ccc_of(cp);
        if (klass != 0 && klass < last_class) {
            *first_unsettled = index;
            return TH_QC_NO;
        }
        uint8_t value = quick_value(cp, form);
        if (value != TH_QC_YES) {
            *first_unsettled = index;
            return value;
        }
        last_class = klass;
    }
    return TH_QC_YES;
}

/* Append the decomposition row `number` (1-based) of a table, its run in the shared pool, to `out` at `at`; returns the
   new length. */
static Py_ssize_t append_row(Py_UCS4 *out, Py_ssize_t at, const uint16_t *offsets, const uint8_t *lengths,
                             uint16_t number) {
    for (uint8_t index = 0; index < lengths[number - 1]; index++) {
        out[at++] = th_norm_pool[offsets[number - 1] + index];
    }
    return at;
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
        uint16_t number = compat ? compat_number(cp) : 0;
        if (number != 0) {
            at = append_row(out, at, th_norm_compat_offset, th_norm_compat_length, number);
        } else if ((number = canon_number(cp)) != 0) {
            at = append_row(out, at, th_norm_canon_offset, th_norm_canon_length, number);
        } else {
            out[at++] = cp;
        }
    }
    return at;
}

/* Equal combining classes must retain input order (Unicode canonical ordering, spec 3.11). */
static int reorder_run(Py_UCS4 *seq, Py_ssize_t len) {
    Py_UCS4 *const ordered = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (ordered == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        return -1;         /* GCOVR_EXCL_LINE: allocation failure */
    }
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
        ordered[offsets[ccc_of(seq[index])]++] = seq[index];
    }
    memcpy(seq, ordered, (size_t)len * sizeof(Py_UCS4));
    PyMem_Free(ordered);
    return 0;
}

static int reorder(Py_UCS4 *seq, Py_ssize_t len) {
    Py_ssize_t run_start = 0;
    for (Py_ssize_t index = 1; index < len; index++) {
        Py_UCS4 cp = seq[index];
        uint8_t klass = ccc_of(cp);
        if (klass == 0) {
            run_start = index + 1;
            continue;
        }
        /* Stable counting avoids quadratic shifts in long, out-of-order mark runs. */
        if (index - run_start >= 32 && ccc_of(seq[index - 1]) > klass) {
            Py_ssize_t end = index + 1;
            while (end < len && ccc_of(seq[end]) != 0) {
                end++;
            }
            if (reorder_run(seq + run_start, end - run_start) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                return -1;                                           /* GCOVR_EXCL_LINE: allocation failure */
            }
            index = end - 1;
            continue;
        }
        Py_ssize_t back = index;
        while (back > 0 && ccc_of(seq[back - 1]) > klass) {
            seq[back] = seq[back - 1];
            back--;
        }
        seq[back] = cp;
    }
    return 0;
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

static const char *const form_names[] = {"NFC", "NFD", "NFKC", "NFKD"};

/* The (form, text) arguments of normalize and is_normalized, with the errors their Python wrappers raised: a form
   outside the four names is a ValueError, one that cannot be a dict key the TypeError a dict lookup raises. */
static int parse_form_text(const char *name, PyObject *const *args, Py_ssize_t count, PyObject *kwnames, int *form) {
    if (kwnames != NULL) {
        PyErr_Format(PyExc_TypeError, "%s() takes no keyword arguments", name);
        return -1;
    }
    if (count != 2) {
        PyErr_Format(PyExc_TypeError, "%s() takes exactly 2 arguments (%zd given)", name, count);
        return -1;
    }
    PyObject *form_name = args[0];
    if (PyUnicode_Check(form_name)) {
        for (int code = TH_NFC; code <= TH_NFKD; code++) {
            if (PyUnicode_CompareWithASCIIString(form_name, form_names[code]) == 0) {
                *form = code;
                if (!PyUnicode_Check(args[1])) {
                    PyErr_Format(PyExc_TypeError, "argument 2 must be str, not %.200s",
                                 args[1] == Py_None ? "None" : Py_TYPE(args[1])->tp_name);
                    return -1;
                }
                return 0;
            }
        }
    } else {
        /* the Python wrapper looked the name up in a dict, so an unhashable one raises that lookup's TypeError */
        PyObject *forms = PyDict_New();
        int found = forms == NULL ? -1 : PyDict_Contains(forms, form_name); /* GCOVR_EXCL_BR_LINE: allocation */
        Py_XDECREF(forms);
        if (found < 0) {
            return -1;
        }
    }
    PyErr_Format(PyExc_ValueError, "invalid normalization form %R", form_name);
    return -1;
}

/* Both spans determine the width: normalization can remove the input's widest character. */
static PyObject *build_result(int input_kind, const void *input_data, Py_ssize_t prefix, const Py_UCS4 *buf,
                              Py_ssize_t len) {
    Py_UCS4 maxchar = 0;
    for (Py_ssize_t index = 0; index < prefix; index++) {
        Py_UCS4 character = PyUnicode_READ(input_kind, input_data, index);
        if (character > maxchar) {
            maxchar = character;
        }
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (buf[index] > maxchar) {
            maxchar = buf[index];
        }
    }
    PyObject *result = PyUnicode_New(prefix + len, th_str_maxchar(maxchar));
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int kind = PyUnicode_KIND(result);
    void *data = PyUnicode_DATA(result);
    for (Py_ssize_t index = 0; index < prefix; index++) {
        PyUnicode_WRITE(kind, data, index, PyUnicode_READ(input_kind, input_data, index));
    }
    /* one loop per width, so the store needs no per-character kind switch */
    if (kind == PyUnicode_1BYTE_KIND) {
        Py_UCS1 *out = (Py_UCS1 *)data + prefix;
        for (Py_ssize_t index = 0; index < len; index++) {
            out[index] = (Py_UCS1)buf[index];
        }
    } else if (kind == PyUnicode_2BYTE_KIND) {
        Py_UCS2 *out = (Py_UCS2 *)data + prefix;
        for (Py_ssize_t index = 0; index < len; index++) {
            out[index] = (Py_UCS2)buf[index];
        }
    } else {
        memcpy((Py_UCS4 *)data + prefix, buf, (size_t)len * sizeof(Py_UCS4));
    }
    return result;
}

/* Keep the last settled starter with the suffix so marks and Hangul can compose across the first change. */
static PyObject *normalize_full(int kind, const void *data, Py_ssize_t len, int form, Py_ssize_t first_unsettled) {
    Py_ssize_t prefix = first_unsettled;
    while (prefix > 0) {
        prefix--;
        if (ccc_of(PyUnicode_READ(kind, data, prefix)) == 0) {
            break;
        }
    }
    int compat = form == TH_NFKC || form == TH_NFKD;
    /* len is a code-point count from a live str, far under the overflow bound; keep the guard on one line for the gate
     */
    if (len > PY_SSIZE_T_MAX / (Py_ssize_t)(TH_NORM_MAX_EXPANSION * sizeof(Py_UCS4))) { /* GCOVR_EXCL_BR_LINE */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: a str this long cannot be allocated to reach here */
    }
    Py_UCS4 *buf = PyMem_Malloc((size_t)(len - prefix) * TH_NORM_MAX_EXPANSION * sizeof(Py_UCS4));
    if (buf == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t count = decompose(kind, (const char *)data + prefix * kind, len - prefix, buf, compat);
    if (reorder(buf, count) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(buf);           /* GCOVR_EXCL_LINE: allocation failure */
        return PyErr_NoMemory();   /* GCOVR_EXCL_LINE: allocation failure */
    }
    if (form == TH_NFC || form == TH_NFKC) {
        count = compose(buf, count);
    }
    PyObject *result = build_result(kind, data, prefix, buf, count);
    PyMem_Free(buf);
    return result;
}

/* normalize(form, text): the four forms named by form, the same object back when already normalized, otherwise the
   full pipeline. */
PyObject *turbohtml_normalize(PyObject *Py_UNUSED(module), PyObject *const *args, Py_ssize_t count, PyObject *kwnames) {
    int form;
    if (parse_form_text("normalize", args, count, kwnames, &form) < 0) {
        return NULL;
    }
    PyObject *text = args[1];
    if (PyUnicode_IS_ASCII(text)) {
        return Py_NewRef(text);
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t first_unsettled;
    if (quick_check(kind, data, len, form, &first_unsettled) == TH_QC_YES) {
        return Py_NewRef(text);
    }
    return normalize_full(kind, data, len, form, first_unsettled);
}

/* _is_normalized(form, text): True when text is already in form. A quick-check Maybe is settled by normalizing and
   comparing, exactly as unicodedata.is_normalized does. */
PyObject *turbohtml_is_normalized(PyObject *Py_UNUSED(module), PyObject *const *args, Py_ssize_t count,
                                  PyObject *kwnames) {
    int form;
    if (parse_form_text("is_normalized", args, count, kwnames, &form) < 0) {
        return NULL;
    }
    PyObject *text = args[1];
    if (PyUnicode_IS_ASCII(text)) {
        Py_RETURN_TRUE;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_ssize_t first_unsettled;
    int quick = quick_check(kind, data, len, form, &first_unsettled);
    if (quick == TH_QC_YES) {
        Py_RETURN_TRUE;
    }
    if (quick == TH_QC_NO) {
        Py_RETURN_FALSE;
    }
    PyObject *normalized = normalize_full(kind, data, len, form, first_unsettled);
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

/* _collapse_whitespace(text) -> str: the text's whitespace-separated words joined by single spaces, the
   " ".join(text.split()) fold, splitting on the Unicode White_Space characters str.split() splits on. */
PyObject *turbohtml_collapse_whitespace(PyObject *Py_UNUSED(module), PyObject *text) {
    if (!PyUnicode_Check(text)) {
        PyErr_SetString(PyExc_TypeError, "text must be a str");
        return NULL;
    }
    Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    Py_UCS4 *out = PyMem_Malloc((size_t)(len > 0 ? len : 1) * sizeof(Py_UCS4));
    if (out == NULL) {           /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t written = 0;
    int pending_space = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 ch = PyUnicode_READ(kind, data, index);
        if (Py_UNICODE_ISSPACE(ch)) {
            pending_space = written > 0; /* a run before the first word is dropped, one after it becomes a space */
            continue;
        }
        if (pending_space) {
            out[written++] = ' ';
            pending_space = 0;
        }
        out[written++] = ch;
    }
    PyObject *result = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, written);
    PyMem_Free(out);
    return result;
}
