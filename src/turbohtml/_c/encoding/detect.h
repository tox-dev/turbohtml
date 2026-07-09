/* Content-based character encoding detection (issue #182), #included into
   dom/document.c after encoding/encoding.h.

   This is a strictly WHATWG-subordinate fallback: it runs only after the spec
   path (a BOM, the encoding argument, then the <meta> prescan) has yielded no
   encoding, and only when the caller opts in with detect_encoding=True. The spec
   result always wins, so conformance is unaffected; without the opt-in the
   fallback stays windows-1252 exactly as before.

   The design follows Firefox's chardetng (https://github.com/hsivonen/chardetng,
   MIT/Apache-2.0, Copyright Mozilla Foundation): negative matching (a single
   decode error or C1 control disqualifies a candidate encoding) combined with
   character-pair frequency scoring for the single-byte encodings. UTF-8 is
   detected structurally; the single-byte encodings each run a candidate that
   accumulates a frequency score and drops out on the first unmapped byte. The CJK
   multi-byte candidates land in a later phase. */

#include "encoding/detect_data.h"

/* chardetng scoring weights (lib.rs). */
#define TH_DETECT_LATIN_ADJACENCY_PENALTY (-50)
#define TH_DETECT_IMPLAUSIBILITY_PENALTY (-220)
#define TH_DETECT_ORDINAL_BONUS 300
#define TH_DETECT_COPYRIGHT_BONUS 222
#define TH_DETECT_IMPLAUSIBLE_LATIN_CASE_TRANSITION_PENALTY (-180)
#define TH_DETECT_NON_LATIN_CAPITALIZATION_BONUS 40
#define TH_DETECT_NON_LATIN_ALL_CAPS_PENALTY (-40)
#define TH_DETECT_NON_LATIN_MIXED_CASE_PENALTY (-20)
#define TH_DETECT_LATIN_LETTER 1
#define TH_DETECT_ASCII_DIGIT 100
#define TH_DETECT_WINDOWS_1256_ZWNJ 2

/* Every candidate that survives a detection run, with its raw chardetng score, so
   the standalone detect() surface can rank alternatives; capacity covers the 5 CJK
   candidates, the 19 single-byte candidates, and the Hebrew visual candidate.
   structural is set when the result needed no scoring at all (valid UTF-8 with a
   multi-byte sequence, or an escape-driven ISO-2022-JP stream). */
typedef struct {
    struct {
        const char *label;
        int64_t score;
    } items[25];
    int count;
    int structural;
} th_detect_scores;

/* Incremental UTF-8 validation. A stream is valid until the first malformed, overlong,
   surrogate, or truncated sequence -- one error disqualifies UTF-8, as in chardetng. Pure
   ASCII validates but leaves has_non_ascii clear, since ASCII decodes identically under the
   windows-1252 fallback and carries no evidence either way. trailing counts the continuation
   bytes still owed, so a sequence split across a feed survives the boundary; low/high carry
   the bounds the first of them must satisfy, which is what rejects an overlong form, a
   surrogate, and anything above U+10FFFF. */
typedef struct {
    int valid;
    int has_non_ascii;
    int trailing;
    unsigned char low;
    unsigned char high;
} th_utf8_scan;

static void th_utf8_scan_init(th_utf8_scan *scan) {
    scan->valid = 1;
    scan->has_non_ascii = 0;
    scan->trailing = 0;
    scan->low = 0x80;
    scan->high = 0xBF;
}

static void th_utf8_scan_feed(th_utf8_scan *scan, const unsigned char *buf, Py_ssize_t len) {
    if (!scan->valid) {
        return;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char byte = buf[index];
        if (scan->trailing > 0) {
            if (byte < scan->low || byte > scan->high) {
                scan->valid = 0;
                return;
            }
            scan->low = 0x80;
            scan->high = 0xBF;
            scan->trailing--;
            continue;
        }
        if (byte < 0x80) {
            continue;
        }
        scan->has_non_ascii = 1;
        if (byte >= 0xC2 && byte <= 0xDF) {
            scan->trailing = 1;
        } else if (byte >= 0xE0 && byte <= 0xEF) {
            scan->trailing = 2;
            scan->low = byte == 0xE0 ? 0xA0 : 0x80;  /* E0: no overlong 80..9F */
            scan->high = byte == 0xED ? 0x9F : 0xBF; /* ED: no surrogate A0..BF */
        } else if (byte >= 0xF0 && byte <= 0xF4) {
            scan->trailing = 3;
            scan->low = byte == 0xF0 ? 0x90 : 0x80;  /* F0: no overlong 80..8F */
            scan->high = byte == 0xF4 ? 0x8F : 0xBF; /* F4: nothing above U+10FFFF */
        } else {
            scan->valid = 0; /* C0/C1, F5..FF, or a stray continuation byte as lead */
            return;
        }
    }
}

/* A truncated tail at end of stream is malformed, so the sequence has to have closed. */
static int th_utf8_scan_done(const th_utf8_scan *scan) {
    return scan->valid && scan->trailing == 0;
}

/* Whole-buffer UTF-8 validity, the one-shot form the sniff in dom/document.c runs before it
   reaches the windows-1252 fallback. */
static int th_detect_is_utf8(const unsigned char *buf, Py_ssize_t len, int *has_non_ascii) {
    th_utf8_scan scan;
    th_utf8_scan_init(&scan);
    th_utf8_scan_feed(&scan, buf, len);
    *has_non_ascii = scan.has_non_ascii;
    return th_utf8_scan_done(&scan);
}

/* The byte class for a byte under a candidate encoding: bytes 0..127 index the
   lower table, 128..255 the upper. Class 255 means the byte is unmapped, which
   disqualifies the encoding. */
static unsigned char th_detect_classify(const th_detect_single_byte *data, unsigned char byte) {
    return byte < 0x80 ? data->lower[byte] : data->upper[byte & 0x7F];
}

/* Whether a class is a non-Latin alphabetic letter (windows-1256 reserves an extra
   low class for ZWNJ). */
static int th_detect_is_non_latin(const th_detect_single_byte *data, unsigned char caseless, int windows_1256) {
    int lower_bound = windows_1256 ? TH_DETECT_WINDOWS_1256_ZWNJ : 1;
    return caseless > lower_bound && caseless < data->ascii + data->non_ascii;
}

/* The frequency score chardetng assigns to a (previous, current) class pair: a
   stored probability for a pair below the boundary (255 meaning implausible), and
   the plausibility rules for pairs that straddle it. A direct port of
   SingleByteData::score. */
static int64_t th_detect_pair_score(const th_detect_single_byte *data, int current, int previous, int windows_1256) {
    int boundary = data->ascii + data->non_ascii;
    if (current < boundary && previous < boundary) {
        if ((previous == 0 && current == 0) || (previous < data->ascii && current < data->ascii)) {
            return 0;
        }
        int index = current >= data->ascii ? data->ascii * data->non_ascii +
                                                 (data->ascii + data->non_ascii) * (current - data->ascii) + previous
                                           : current * data->non_ascii + previous - data->ascii;
        unsigned char stored = data->probabilities[index];
        return stored == 255 ? TH_DETECT_IMPLAUSIBILITY_PENALTY : (int64_t)stored;
    }
    if (current < boundary) {
        /* current alphabetic below the boundary, previous above it. The ASCII-digit
           class (100) is always above the boundary (at most 72), so unlike chardetng we
           omit its always-false test here; only space and the windows-1256 ZWNJ qualify. */
        if (current == 0 || (windows_1256 && current == TH_DETECT_WINDOWS_1256_ZWNJ)) {
            return 0;
        }
        switch (previous - boundary) {
        case 1: /* implausible next to alphabetic on either side */
        case 2: /* implausible before alphabetic */
            return TH_DETECT_IMPLAUSIBILITY_PENALTY;
        case 4: /* plausible next to non-ASCII alphabetic */
            return current < data->ascii ? TH_DETECT_IMPLAUSIBILITY_PENALTY : 0;
        case 5: /* plausible next to ASCII alphabetic */
            return current < data->ascii ? 0 : TH_DETECT_IMPLAUSIBILITY_PENALTY;
        default: /* plausible-either-side (0), implausible-after-alphabetic (3), ASCII digit */
            return 0;
        }
    }
    if (previous < boundary) {
        /* current above the boundary, previous alphabetic below it; the ASCII-digit class
           is again always above the boundary, so its test is omitted */
        if (previous == 0 || (windows_1256 && previous == TH_DETECT_WINDOWS_1256_ZWNJ)) {
            return 0;
        }
        switch (current - boundary) {
        case 1: /* implausible next to alphabetic on either side */
        case 3: /* implausible after alphabetic */
            return TH_DETECT_IMPLAUSIBILITY_PENALTY;
        case 4:
            return previous < data->ascii ? TH_DETECT_IMPLAUSIBILITY_PENALTY : 0;
        case 5:
            return previous < data->ascii ? 0 : TH_DETECT_IMPLAUSIBILITY_PENALTY;
        default: /* plausible-either-side (0), implausible-before-alphabetic (2), ASCII digit */
            return 0;
        }
    }
    /* both above the boundary */
    if (current == TH_DETECT_ASCII_DIGIT || previous == TH_DETECT_ASCII_DIGIT) {
        return 0;
    }
    return TH_DETECT_IMPLAUSIBILITY_PENALTY;
}

static int th_detect_is_ascii_punct(unsigned char byte) {
    return byte == '.' || byte == ',' || byte == ':' || byte == ';' || byte == '?' || byte == '!';
}

/* The single-byte candidate kinds. One engine drives all of them; the kind selects
   the casing/word heuristics chardetng applies on top of the shared pair scoring. */
typedef enum {
    TH_SB_LATIN,           /* Latin scripts, with the windows-1252 ordinal state machine */
    TH_SB_NON_LATIN_CASED, /* Cyrillic/Greek (bicameral non-Latin) */
    TH_SB_ARABIC_FRENCH,   /* windows-1256, with French (Latin) case penalties */
    TH_SB_CASELESS,        /* Arabic/Thai (no case) */
    TH_SB_LOGICAL,         /* Hebrew in logical order (windows-1255) */
    TH_SB_VISUAL,          /* Hebrew in visual order (ISO-8859-8) */
} th_sb_kind;

/* Latin case states (also used for the Arabic/French Latin casing). */
enum { TH_LAT_SPACE, TH_LAT_UPPER, TH_LAT_LOWER, TH_LAT_ALLCAPS };
/* Non-Latin cased states. */
enum { TH_NL_SPACE, TH_NL_UPPER, TH_NL_LOWER, TH_NL_UPPERLOWER, TH_NL_ALLCAPS, TH_NL_MIX };
/* The windows-1252 ordinal-indicator state machine. */
enum {
    TH_ORD_OTHER,
    TH_ORD_SPACE,
    TH_ORD_PERIOD_AFTER_N,
    TH_ORD_EXPECTING_SPACE,
    TH_ORD_EXPECTING_SPACE_UNDO,
    TH_ORD_EXPECTING_SPACE_OR_DIGIT,
    TH_ORD_EXPECTING_SPACE_OR_DIGIT_UNDO,
    TH_ORD_UPPER_N,
    TH_ORD_LOWER_N,
    TH_ORD_FEMININE_START,
    TH_ORD_DIGIT,
    TH_ORD_ROMAN,
    TH_ORD_COPYRIGHT,
};

typedef struct {
    const th_detect_single_byte *data;
    th_sb_kind kind;
    int windows_1252;
    int koi8u;
    int ibm866;
    int alive;
    int64_t score;
    unsigned char prev;
    int prev_ascii;
    unsigned prev_non_ascii;
    int case_state;
    int ordinal_state;
    unsigned long current_word_len;
    unsigned long longest_word;
    int prev_was_a0;
    int prev_punctuation;
    unsigned long plausible_punctuation;
} th_sb_candidate;

/* The row of th_detect_single_byte_data that carries the plain windows-1252 table. The Icelandic windows-1252 variant
   (row 8) shares the label but is a distinct table, so the ordinal state machine, which chardetng keys on data pointer
   identity to the one western table (LatinCandidate::new), must run for this row alone -- not the Icelandic sibling. */
#define TH_DETECT_WINDOWS_1252_INDEX 7

static void th_sb_init(th_sb_candidate *cand, const th_detect_single_byte *data, th_sb_kind kind) {
    memset(cand, 0, sizeof(*cand));
    cand->data = data;
    cand->kind = kind;
    cand->alive = 1;
    cand->prev_ascii = 1;
    cand->case_state = TH_LAT_SPACE; /* both case enums start at 0 == Space */
    cand->ordinal_state = TH_ORD_SPACE;
    cand->windows_1252 = data == &th_detect_single_byte_data[TH_DETECT_WINDOWS_1252_INDEX];
    cand->koi8u = strcmp(data->label, "koi8-u") == 0;
    cand->ibm866 = strcmp(data->label, "ibm866") == 0;
}

/* These are faithful ports of chardetng's case/ordinal state machines, where
   distinct transitions legitimately share a target state, so the branch-clone
   check is a false positive here. */
/* NOLINTBEGIN(bugprone-branch-clone) */

/* The windows-1252 ordinal-indicator scoring (Spanish/Italian "n.º", "ª", "©", ...),
   which pairwise scoring cannot capture without breaking Romanian detection. */
static void th_sb_ordinal(th_sb_candidate *cand, unsigned char byte, unsigned char caseless) {
    switch (cand->ordinal_state) {
    case TH_ORD_OTHER:
        cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        break;
    case TH_ORD_SPACE:
        if (caseless == 0) {
            /* stay */
        } else if (byte == 0xAA || byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE;
        } else if (byte == 'M' || byte == 'D' || byte == 'S') {
            cand->ordinal_state = TH_ORD_FEMININE_START;
        } else if (byte == 'N') {
            cand->ordinal_state = TH_ORD_UPPER_N;
        } else if (byte == 'n') {
            cand->ordinal_state = TH_ORD_LOWER_N;
        } else if (caseless == TH_DETECT_ASCII_DIGIT) {
            cand->ordinal_state = TH_ORD_DIGIT;
        } else if (caseless == 9 || caseless == 22 || caseless == 24) { /* I, V, X */
            cand->ordinal_state = TH_ORD_ROMAN;
        } else if (byte == 0xA9) {
            cand->ordinal_state = TH_ORD_COPYRIGHT;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    case TH_ORD_EXPECTING_SPACE:
        if (caseless == 0) {
            cand->score += TH_DETECT_ORDINAL_BONUS;
            cand->ordinal_state = TH_ORD_SPACE;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    case TH_ORD_EXPECTING_SPACE_UNDO:
        if (caseless == 0) {
            cand->score += TH_DETECT_ORDINAL_BONUS - TH_DETECT_IMPLAUSIBILITY_PENALTY;
            cand->ordinal_state = TH_ORD_SPACE;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    case TH_ORD_EXPECTING_SPACE_OR_DIGIT:
        if (caseless == 0) {
            cand->score += TH_DETECT_ORDINAL_BONUS;
            cand->ordinal_state = TH_ORD_SPACE;
        } else if (caseless == TH_DETECT_ASCII_DIGIT) {
            cand->score += TH_DETECT_ORDINAL_BONUS;
            cand->ordinal_state = TH_ORD_OTHER;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    case TH_ORD_EXPECTING_SPACE_OR_DIGIT_UNDO:
        if (caseless == 0) {
            cand->score += TH_DETECT_ORDINAL_BONUS - TH_DETECT_IMPLAUSIBILITY_PENALTY;
            cand->ordinal_state = TH_ORD_SPACE;
        } else if (caseless == TH_DETECT_ASCII_DIGIT) {
            cand->score += TH_DETECT_ORDINAL_BONUS - TH_DETECT_IMPLAUSIBILITY_PENALTY;
            cand->ordinal_state = TH_ORD_OTHER;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    case TH_ORD_UPPER_N:
        if (byte == 0xAA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_UNDO;
        } else if (byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_OR_DIGIT_UNDO;
        } else if (byte == '.') {
            cand->ordinal_state = TH_ORD_PERIOD_AFTER_N;
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    case TH_ORD_LOWER_N:
        if (byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_OR_DIGIT_UNDO;
        } else if (byte == '.') {
            cand->ordinal_state = TH_ORD_PERIOD_AFTER_N;
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    case TH_ORD_FEMININE_START:
        if (byte == 0xAA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_UNDO;
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    case TH_ORD_DIGIT:
        if (byte == 0xAA || byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE;
        } else if (caseless == TH_DETECT_ASCII_DIGIT) {
            /* stay */
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    case TH_ORD_ROMAN:
        if (byte == 0xAA || byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_UNDO;
        } else if (caseless == 9 || caseless == 22 || caseless == 24) {
            /* stay */
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    case TH_ORD_PERIOD_AFTER_N:
        if (byte == 0xBA) {
            cand->ordinal_state = TH_ORD_EXPECTING_SPACE_OR_DIGIT;
        } else {
            cand->ordinal_state = caseless == 0 ? TH_ORD_SPACE : TH_ORD_OTHER;
        }
        break;
    default: /* TH_ORD_COPYRIGHT */
        if (caseless == 0) {
            cand->score += TH_DETECT_COPYRIGHT_BONUS;
            cand->ordinal_state = TH_ORD_SPACE;
        } else {
            cand->ordinal_state = TH_ORD_OTHER;
        }
        break;
    }
}

/* Feed the whole input to one single-byte candidate, accumulating its score or
   disqualifying it on the first unmapped byte. Ports the per-kind feed methods. */
static void th_sb_feed(th_sb_candidate *cand, const unsigned char *buf, Py_ssize_t len) {
    const th_detect_single_byte *data = cand->data;
    int windows_1256 = cand->kind == TH_SB_ARABIC_FRENCH;
    for (Py_ssize_t index = 0; index < len; index++) {
        unsigned char byte = buf[index];
        unsigned char cls = th_detect_classify(data, byte);
        if (cls == 255) {
            cand->alive = 0;
            return;
        }
        unsigned char caseless = cls & 0x7F;
        int ascii = byte < 0x80;
        int non_latin = th_detect_is_non_latin(data, caseless, windows_1256);

        if (cand->kind == TH_SB_LATIN || cand->kind == TH_SB_ARABIC_FRENCH) {
            int ascii_pair =
                cand->kind == TH_SB_LATIN ? (cand->prev_non_ascii == 0 && ascii) : (cand->prev_ascii && ascii);
            if (cand->kind == TH_SB_LATIN) {
                int64_t penalty = cand->prev_non_ascii <= 2   ? 0
                                  : cand->prev_non_ascii == 3 ? -5
                                  : cand->prev_non_ascii == 4 ? -20
                                                              : -200;
                cand->score += penalty;
            }
            int latin_alpha = cand->kind == TH_SB_LATIN ? (caseless > 0 && caseless < data->ascii + data->non_ascii)
                                                        : (caseless == TH_DETECT_LATIN_LETTER);
            if (!latin_alpha) {
                cand->case_state = TH_LAT_SPACE;
            } else if ((cls >> 7) == 0) {
                if (cand->case_state == TH_LAT_ALLCAPS && !ascii_pair) {
                    cand->score += TH_DETECT_IMPLAUSIBLE_LATIN_CASE_TRANSITION_PENALTY;
                }
                cand->case_state = TH_LAT_LOWER;
            } else if (cand->case_state == TH_LAT_SPACE) {
                cand->case_state = TH_LAT_UPPER;
            } else if (cand->case_state == TH_LAT_LOWER) {
                if (!ascii_pair) {
                    cand->score += TH_DETECT_IMPLAUSIBLE_LATIN_CASE_TRANSITION_PENALTY;
                }
                cand->case_state = TH_LAT_UPPER;
            } else {
                cand->case_state = TH_LAT_ALLCAPS;
            }
            if (cand->kind == TH_SB_ARABIC_FRENCH) {
                if (non_latin) {
                    cand->current_word_len++;
                } else {
                    if (cand->current_word_len > cand->longest_word) {
                        cand->longest_word = cand->current_word_len;
                    }
                    cand->current_word_len = 0;
                }
                if (!ascii_pair) {
                    cand->score += th_detect_pair_score(data, caseless, cand->prev, 1);
                    if (cand->prev == TH_DETECT_LATIN_LETTER && non_latin) {
                        cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                    } else if (caseless == TH_DETECT_LATIN_LETTER && th_detect_is_non_latin(data, cand->prev, 1)) {
                        cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                    }
                }
            } else {
                int ascii_ish =
                    ascii_pair || (ascii && cand->prev == 0) || (caseless == 0 && cand->prev_non_ascii == 0);
                if (!ascii_ish) {
                    cand->score += th_detect_pair_score(data, caseless, cand->prev, 0);
                }
                if (cand->windows_1252) {
                    th_sb_ordinal(cand, byte, caseless);
                }
            }
            if (cand->kind == TH_SB_LATIN) {
                cand->prev_non_ascii = ascii ? 0 : cand->prev_non_ascii + 1;
            } else {
                cand->prev_ascii = ascii;
            }
            cand->prev = caseless;
            continue;
        }

        /* The caseless/Hebrew/non-Latin-cased kinds share the word-length + adjacency
           core; the case machine and punctuation bookkeeping differ. */
        int ascii_pair = cand->prev_ascii && ascii;
        if (cand->kind == TH_SB_NON_LATIN_CASED) {
            if (caseless == TH_DETECT_LATIN_LETTER) {
                cand->case_state = TH_NL_MIX;
            } else if (!non_latin) {
                if (cand->case_state == TH_NL_UPPERLOWER) {
                    cand->score += TH_DETECT_NON_LATIN_CAPITALIZATION_BONUS;
                } else if (cand->case_state == TH_NL_ALLCAPS && cand->koi8u) {
                    cand->score += TH_DETECT_NON_LATIN_ALL_CAPS_PENALTY;
                } else if (cand->case_state == TH_NL_MIX) {
                    cand->score += TH_DETECT_NON_LATIN_MIXED_CASE_PENALTY * (int64_t)cand->current_word_len;
                }
                cand->case_state = TH_NL_SPACE;
            } else if ((cls >> 7) == 0) {
                if (cand->case_state == TH_NL_SPACE) {
                    cand->case_state = TH_NL_LOWER;
                } else if (cand->case_state == TH_NL_UPPER) {
                    cand->case_state = TH_NL_UPPERLOWER;
                } else if (cand->case_state == TH_NL_ALLCAPS) {
                    cand->case_state = TH_NL_MIX;
                }
            } else if (cand->case_state == TH_NL_SPACE) {
                cand->case_state = TH_NL_UPPER;
            } else if (cand->case_state == TH_NL_UPPER) {
                cand->case_state = TH_NL_ALLCAPS;
            } else if (cand->case_state == TH_NL_LOWER || cand->case_state == TH_NL_UPPERLOWER) {
                cand->case_state = TH_NL_MIX;
            }
        }

        if (non_latin) {
            cand->current_word_len++;
        } else {
            if (cand->current_word_len > cand->longest_word) {
                cand->longest_word = cand->current_word_len;
            }
            cand->current_word_len = 0;
        }

        int is_a0 = byte == 0xA0;
        if (!ascii_pair) {
            int skip_ibm866 = cand->ibm866 && ((is_a0 && (cand->prev_was_a0 || cand->prev == 0)) ||
                                               (caseless == 0 && cand->prev_was_a0));
            if (!skip_ibm866) {
                cand->score += th_detect_pair_score(data, caseless, cand->prev, 0);
            }
            int prev_non_latin = th_detect_is_non_latin(data, cand->prev, 0);
            if (cand->kind == TH_SB_LOGICAL && caseless == 0 && prev_non_latin && th_detect_is_ascii_punct(byte)) {
                cand->plausible_punctuation++;
            } else if (cand->kind == TH_SB_VISUAL && non_latin && cand->prev_punctuation) {
                cand->plausible_punctuation++;
            }
            if (cand->prev == TH_DETECT_LATIN_LETTER && non_latin) {
                cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
            } else if (caseless == TH_DETECT_LATIN_LETTER && prev_non_latin) {
                cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
            }
        }
        cand->prev_ascii = ascii;
        cand->prev = caseless;
        cand->prev_was_a0 = is_a0;
        cand->prev_punctuation = caseless == 0 && th_detect_is_ascii_punct(byte);
    }
}
/* NOLINTEND(bugprone-branch-clone) */

/* chardetng feeds one space to every single-byte candidate once the stream ends, so a word or an ordinal or a
   copyright sign that runs up against EOF still reaches the word-boundary arms of its scorer. Without it a Cyrillic
   word loses its capitalization bonus and windows-1251 loses to GBK on the bare word "Привет". */
static void th_sb_feed_eof(th_sb_candidate *cand) {
    th_sb_feed(cand, (const unsigned char *)" ", 1);
}

/* The single-byte candidate set, in chardetng's index order so ties resolve the
   same way (the strict ">" below keeps the earliest, windows-1252 first). Each row
   is an index into th_detect_single_byte_data plus the candidate kind. */
static const struct {
    unsigned char data_index;
    unsigned char kind;
} th_sb_candidate_table[] = {
    {7, TH_SB_LATIN},            /* windows-1252 (the default) */
    {3, TH_SB_NON_LATIN_CASED},  /* windows-1251 */
    {1, TH_SB_LATIN},            /* windows-1250 */
    {2, TH_SB_LATIN},            /* iso-8859-2 */
    {14, TH_SB_ARABIC_FRENCH},   /* windows-1256 */
    {8, TH_SB_LATIN},            /* windows-1252 Icelandic */
    {11, TH_SB_LATIN},           /* windows-1254 */
    {19, TH_SB_CASELESS},        /* windows-874 */
    {12, TH_SB_LOGICAL},         /* windows-1255 (Hebrew logical) */
    {9, TH_SB_NON_LATIN_CASED},  /* windows-1253 */
    {10, TH_SB_NON_LATIN_CASED}, /* iso-8859-7 */
    {16, TH_SB_LATIN},           /* windows-1257 */
    {17, TH_SB_LATIN},           /* iso-8859-13 */
    {4, TH_SB_NON_LATIN_CASED},  /* koi8-u */
    {6, TH_SB_NON_LATIN_CASED},  /* ibm866 */
    {15, TH_SB_CASELESS},        /* iso-8859-6 */
    {0, TH_SB_LATIN},            /* windows-1258 */
    {18, TH_SB_LATIN},           /* iso-8859-4 */
    {5, TH_SB_NON_LATIN_CASED},  /* iso-8859-5 */
};

#define TH_SB_COUNT ((int)(sizeof(th_sb_candidate_table) / sizeof(th_sb_candidate_table[0])))
#define TH_SB_LOGICAL_SLOT 8 /* the windows-1255 row above, for the Hebrew tiebreak */
#define TH_DETECT_ISO_8859_8_ROW 13

static int th_tld_two_letter_cmp(const void *label, const void *row) {
    return memcmp(label, row, 2);
}

static int th_tld_punycode_cmp(const void *label, const void *row) {
    return strcmp((const char *)label, *(const char *const *)row);
}

/* The rightmost DNS label of the host the bytes came from, classified into the script it
   suggests (chardetng's classify_tld). The label arrives lower-case ASCII, Punycode for an
   internationalized domain, which is what the two sorted key tables hold; anything else, and
   any label the tables do not carry, is TH_TLD_GENERIC, the no-hint answer. */
static th_tld th_tld_classify(const char *label, Py_ssize_t len) {
    if (len == 2) {
        const char *row = bsearch(label, th_tld_two_letter_keys, sizeof(th_tld_two_letter_keys) / 2,
                                  sizeof(th_tld_two_letter_keys[0]), th_tld_two_letter_cmp);
        /* an unlisted country-code domain is a Western one, not a hintless one */
        if (row == NULL) {
            return TH_TLD_WESTERN;
        }
        return (th_tld)th_tld_two_letter_values[(row - &th_tld_two_letter_keys[0][0]) / 2];
    }
    if (len == 3) {
        static const char *const western[] = {"edu", "gov", "mil"};
        for (int row = 0; row < (int)(sizeof(western) / sizeof(western[0])); row++) {
            if (memcmp(label, western[row], 3) == 0) {
                return TH_TLD_WESTERN;
            }
        }
        return TH_TLD_GENERIC;
    }
    if (len >= 8 && memcmp(label, "xn--", 4) == 0) {
        const char *const *row =
            bsearch(label + 4, th_tld_punycode_keys, sizeof(th_tld_punycode_keys) / sizeof(th_tld_punycode_keys[0]),
                    sizeof(th_tld_punycode_keys[0]), th_tld_punycode_cmp);
        if (row != NULL) {
            return (th_tld)th_tld_punycode_values[row - th_tld_punycode_keys];
        }
    }
    return TH_TLD_GENERIC;
}

static int th_tld_is_native(th_tld tld, int index) {
    return (th_tld_native[tld] & TH_DETECT_BIT(index)) != 0;
}

/* chardetng's score_adjustment: what a non-native candidate gives up on this TLD. */
static int64_t th_tld_penalty(int64_t score, int index, th_tld tld) {
    if (score < 1) {
        return 0;
    }
    if (th_tld_zeroed[tld] & TH_DETECT_BIT(index)) {
        return score;
    }
    return score / 50 + 60;
}

/* chardetng's Candidate::score, minus the per-kind word gate its callers apply first: the TLD's
   own encoding edges ahead by one, another encoding native to it keeps its score untouched, and
   anything else pays th_tld_penalty. The penalty applies only while some native candidate still
   scores; a TLD whose script never appeared is no evidence about the candidates that did. */
static int64_t th_tld_adjust(int64_t score, int index, th_tld tld, int expectation_is_valid) {
    if (tld == TH_TLD_GENERIC) {
        return score;
    }
    if (index == th_tld_default_encoding[tld]) {
        return score + 1;
    }
    if (th_tld_is_native(tld, index) || !expectation_is_valid) {
        return score;
    }
    return score - th_tld_penalty(score, index, tld);
}

/* The candidate's final score, or 0 (not scored) when it is disqualified or has seen no word of at
   least two letters in its own script. A bicameral non-Latin script owes that word even on its native
   TLD; the caseless, Arabic-French, and Hebrew candidates are excused there. */
static int th_sb_score(const th_sb_candidate *cand, int index, th_tld tld, int expectation_is_valid, int64_t *out) {
    if (!cand->alive) {
        return 0;
    }
    if (cand->kind != TH_SB_LATIN && cand->longest_word < 2) {
        if (cand->kind == TH_SB_NON_LATIN_CASED || !th_tld_is_native(tld, index)) {
            return 0;
        }
    }
    *out = th_tld_adjust(cand->score, index, tld, expectation_is_valid);
    return 1;
}

/* CJK multi-byte candidates (issue #182, phase 3).

   chardetng scores the CJK candidates by feeding each byte to an encoding_rs decoder and classifying every scalar it
   emits. turbohtml drives its own WHATWG decoders (decode.h), which match encoding_rs byte for byte, and applies the
   same scoring. A malformed sequence does not disqualify a candidate outright: chardetng inspects the byte that failed
   (`b`) and the byte before it (`prev_byte`) and, for the Shift_JIS-2004/MacJapanese, MacKorean, MacChinese, and
   unmapped Big5/GBK/EUC-KR pairs, applies a large penalty and keeps scoring; only sequences no arm recognizes end the
   candidate. th_cjk_feed recovers `b`/`prev_byte`/`prev_prev_byte` from decode.h's error_at, and malformed_len (the
   consumed byte count) from the decoder's net advance. An end-of-stream flush of an incomplete sequence (error_at ==
   -1) is candidate death, matching chardetng's final decode_to_utf16(last=true) returning Malformed. The scoring
   constants are chardetng's, with the integer divisions pre-evaluated. */
/* int64_t so every penalty derived from it is computed wide; chardetng accumulates in i64 */
#define TH_CJK_BASE INT64_C(41)
#define TH_CJK_SECONDARY INT64_C(20)
#define TH_CJK_LATIN_ADJ (-41)
#define TH_CJ_PUNCT 20
#define TH_CJK_OTHER 5
#define TH_HWK_SCORE 1
#define TH_HWK_VOICING_SCORE 10
#define TH_SJIS_INITIAL_HWK_PENALTY (-75)
#define TH_SJIS_KANA 20
#define TH_SJIS_LEVEL_1 41
#define TH_SJIS_LEVEL_2 20
#define TH_EUCJP_KANA 54
#define TH_EUCJP_NEAR_OBSOLETE_KANA 40
#define TH_EUCJP_LEVEL_1 41
#define TH_EUCJP_LEVEL_2 20
#define TH_EUCJP_OTHER_KANJI 5
#define TH_EUCJP_INITIAL_KANA_PENALTY (-14)
#define TH_BIG5_LEVEL_1 41
#define TH_BIG5_OTHER 20
/* harsher than the other PUA penalties, so unmapped Big5 pairs do not let EUC-KR win */
#define TH_BIG5_PUA_PENALTY (-(TH_CJK_BASE * 30))
#define TH_BIG5_SINGLE_BYTE_EXTENSION_PENALTY (-(TH_CJK_BASE * 40))
#define TH_EUCKR_EUC_HANGUL 42
/* TH_CJK_SECONDARY / 5, the Windows-949/UHC Hangul that falls outside the EUC (KS X 1001) plane */
#define TH_EUCKR_NON_EUC_HANGUL 4
#define TH_EUCKR_HANJA 10
#define TH_EUCKR_HANJA_AFTER_HANGUL_PENALTY (-410)
#define TH_EUCKR_LONG_WORD_PENALTY (-6)
#define TH_EUCKR_PUA_PENALTY (TH_GBK_PUA_PENALTY - 1) /* one less than GBK's, to break the tie in GBK's favor */
#define TH_EUCKR_MAC_KOREAN_PENALTY (TH_EUCKR_PUA_PENALTY * 2)
#define TH_EUCKR_SINGLE_BYTE_EXTENSION_PENALTY (TH_EUCKR_PUA_PENALTY * 2)
#define TH_GBK_LEVEL_1 41
#define TH_GBK_LEVEL_2 20
#define TH_GBK_NON_EUC 5
#define TH_GBK_PUA_PENALTY (-(TH_CJK_BASE * 10))
#define TH_GBK_SINGLE_BYTE_EXTENSION_PENALTY (TH_GBK_PUA_PENALTY * 4)
#define TH_SJIS_PUA_PENALTY (-(TH_CJK_BASE * 10))
#define TH_SJIS_EXTENSION_PENALTY (TH_SJIS_PUA_PENALTY * 2)
#define TH_SJIS_SINGLE_BYTE_EXTENSION_PENALTY (TH_SJIS_PUA_PENALTY * 2)
/* harsher than Shift_JIS's extension penalty, else EUC-KR text misdetects as EUC-JP */
#define TH_EUCJP_EXTENSION_PENALTY (-(TH_CJK_BASE * 50))

typedef enum {
    TH_CJK_GBK,
    TH_CJK_SHIFT_JIS,
    TH_CJK_EUC_JP,
    TH_CJK_BIG5,
    TH_CJK_EUC_KR,
} th_cjk_kind;

/* Previous-character class. Cj doubles as Korean Hangul; Hanja is EUC-KR only. */
enum { TH_CJ_ASCII, TH_CJ_CJ, TH_CJ_HANJA, TH_CJ_OTHER };

/* Whether a half-width katakana voicing mark may legally follow the previous one. */
enum { TH_HWK_FORBIDDEN, TH_HWK_DAKUTEN, TH_HWK_DAKUTEN_OR_HANDAKUTEN };

typedef struct {
    const th_encoding_entry *entry;
    th_cjk_kind kind;
    int alive;
    int64_t score;
    unsigned char prev_byte;
    unsigned char prev_prev_byte;
    int prev;
    int64_t pending;
    int has_pending;
    int hwk_state;
    int hwk_seen;
    int non_ascii_seen;
    unsigned long word_len;
    /* Feeding the candidate in chunks. dec keeps the decoder's mode state across feeds. A
       sequence straddling a boundary leaves its bytes in carry, which the next feed reads
       again from the start of the sequence, the way th_decode_run's caller re-reads an
       incomplete tail. tail is the last two bytes fed, which chardetng's scoring looks back
       at when an error lands on the first byte of a chunk. */
    th_decoder dec;
    unsigned char carry[8];
    int carry_len;
    unsigned char tail[2];
    int tail_len;
} th_cjk_candidate;

/* chardetng's cjk_extra_score: a frequency bonus that is larger the earlier the
   scalar appears in the 128-entry most-frequent table for its script. */
static int64_t th_cjk_extra_score(uint16_t u, const uint16_t *table) {
    for (int pos = 0; pos < 128; pos++) {
        if (table[pos] == u) {
            return (128 - pos) / 16;
        }
    }
    return 0;
}

/* A Shift_JIS/Big5 lead byte whose trail half is ambiguous enough that a score is
   deferred until the next scalar confirms a plausible run. */
static int th_cjk_problematic_lead(unsigned char b) {
    switch (b) {
    case 0x91:
    case 0x92:
    case 0x93:
    case 0x94:
    case 0x95:
    case 0x96:
    case 0x97:
    case 0x9A:
    case 0x8A:
    case 0x9B:
    case 0x8B:
    case 0x9E:
    case 0x8E:
    case 0xB0:
        return 1;
    default:
        return 0;
    }
}

/* The GBK/EUC-KR superset of th_cjk_problematic_lead. */
static int th_cjk_more_problematic_lead(unsigned char b) {
    return th_cjk_problematic_lead(b) || b == 0x82 || b == 0x84 || b == 0x85 || b == 0xA0;
}

static void th_cjk_flush_pending(th_cjk_candidate *cand) {
    if (cand->has_pending) {
        cand->score += cand->pending;
        cand->has_pending = 0;
    }
}

/* Count s now if the previous scalar was CJ or the lead is unproblematic, else hold
   it as pending until the next scalar decides whether the run is plausible. */
static int64_t th_cjk_maybe_pending(th_cjk_candidate *cand, int64_t score, int problematic) {
    if (cand->prev == TH_CJ_CJ || !problematic) {
        return score;
    }
    cand->pending = score;
    cand->has_pending = 1;
    return 0;
}

static int th_cj_punct5(uint16_t u) {
    return u == 0x3000 || u == 0x3001 || u == 0x3002 || u == 0xFF08 || u == 0xFF09;
}

static int th_cj_punct9(uint16_t u) {
    return th_cj_punct5(u) || u == 0xFF01 || u == 0xFF0C || u == 0xFF1B || u == 0xFF1F;
}

/* The GB18030-required PUA mappings that chardetng treats as ideographs rather than penalizing as private-use, narrowed
   to the scalars the WHATWG gb18030 decoder can actually emit. Its two-byte index maps only these six, and its
   four-byte range index reaches no PUA ideograph, so chardetng's other listed scalars (U+E78D..U+E796, U+E81E,
   U+E826..U+E82C, U+E832, U+E843, U+E854, U+E864) never occur here and their tests are dropped. */
static int th_gbk_pua_ideograph(uint16_t u) {
    return (u >= 0xE816 && u <= 0xE818) || u == 0xE831 || u == 0xE83B || u == 0xE855;
}

/* NOLINTBEGIN(bugprone-branch-clone) */

static void th_cjk_score_gbk(th_cjk_candidate *cand, int written, uint16_t u, unsigned char b) {
    if (written == 2) {
        /* u is the high surrogate of a GB18030 four-byte astral scalar, always in
           0xD800..0xDBFF, so chardetng's redundant outer bounds (<= 0xDBFF, >= 0xD480)
           are dropped: plane 15/16 private use sits at 0xDB80 and up, the BMP-adjacent
           ideographs below 0xD880, and the rest scores as other. */
        th_cjk_flush_pending(cand);
        if (u >= 0xDB80) {
            cand->score += TH_GBK_PUA_PENALTY;
            cand->prev = TH_CJ_OTHER;
        } else if (u < 0xD880) {
            cand->score += TH_GBK_NON_EUC;
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            }
            cand->prev = TH_CJ_CJ;
        } else {
            cand->score += TH_CJK_OTHER;
            cand->prev = TH_CJ_OTHER;
        }
        return;
    }
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if (u == 0x20AC) {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
    } else if (u >= 0x4E00 && u <= 0x9FA5) {
        th_cjk_flush_pending(cand);
        /* a valid gb18030 trail byte never exceeds 0xFE, and a two-byte lead never
           exceeds 0xFE, so chardetng's upper bounds collapse to the lower ones */
        if (b >= 0xA1) {
            if (cand->prev_byte >= 0xA1 && cand->prev_byte <= 0xD7) {
                cand->score += TH_GBK_LEVEL_1 + th_cjk_extra_score(u, th_detect_freq_frequent_simplified);
            } else if (cand->prev_byte >= 0xD8) {
                cand->score += TH_GBK_LEVEL_2;
            } else {
                cand->score += TH_GBK_NON_EUC;
            }
        } else {
            cand->score += th_cjk_maybe_pending(cand, TH_GBK_NON_EUC, th_cjk_more_problematic_lead(cand->prev_byte));
        }
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        th_cjk_flush_pending(cand);
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (u >= 0xE000 && u < 0xF900) {
        th_cjk_flush_pending(cand);
        if (th_gbk_pua_ideograph(u)) {
            cand->score += TH_GBK_NON_EUC;
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            }
            cand->prev = TH_CJ_CJ;
        } else {
            cand->score += TH_GBK_PUA_PENALTY;
            cand->prev = TH_CJ_OTHER;
        }
    } else if (th_cj_punct9(u)) {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJ_PUNCT;
        cand->prev = TH_CJ_OTHER;
    } else if (u <= 0x7F) {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
    } else {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJK_OTHER;
        cand->prev = TH_CJ_OTHER;
    }
}

static void th_cjk_score_shift_jis(th_cjk_candidate *cand, uint16_t u, unsigned char b) {
    int hwk_prev = cand->hwk_state;
    cand->hwk_state = TH_HWK_FORBIDDEN;
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if (u >= 0xFF61 && u <= 0xFF9F) {
        if (!cand->hwk_seen) {
            cand->hwk_seen = 1;
            cand->score += TH_SJIS_INITIAL_HWK_PENALTY;
        }
        cand->has_pending = 0;
        cand->score += TH_HWK_SCORE;
        if ((u >= 0xFF76 && u <= 0xFF84) || u == 0xFF73) {
            cand->hwk_state = TH_HWK_DAKUTEN;
        } else if (u >= 0xFF8A && u <= 0xFF8E) {
            cand->hwk_state = TH_HWK_DAKUTEN_OR_HANDAKUTEN;
        } else if (u == 0xFF9E) {
            cand->score += (hwk_prev == TH_HWK_FORBIDDEN) ? TH_DETECT_IMPLAUSIBILITY_PENALTY : TH_HWK_VOICING_SCORE;
        } else if (u == 0xFF9F) {
            cand->score +=
                (hwk_prev != TH_HWK_DAKUTEN_OR_HANDAKUTEN) ? TH_DETECT_IMPLAUSIBILITY_PENALTY : TH_HWK_VOICING_SCORE;
        }
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (u >= 0x3040 && u < 0x3100) {
        th_cjk_flush_pending(cand);
        cand->score += TH_SJIS_KANA;
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        th_cjk_flush_pending(cand);
        int64_t score = (cand->prev_byte < 0x98 || (cand->prev_byte == 0x98 && b < 0x73))
                            ? TH_SJIS_LEVEL_1 + th_cjk_extra_score(u, th_detect_freq_frequent_kanji)
                            : TH_SJIS_LEVEL_2;
        cand->score += th_cjk_maybe_pending(cand, score, th_cjk_problematic_lead(cand->prev_byte));
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (u >= 0xE000 && u < 0xF900) {
        /* WHATWG Shift_JIS maps the 0xF0..0xF9 lead-byte block to this private-use area, which real Shift_JIS text
           never uses, so the run is implausible. */
        th_cjk_flush_pending(cand);
        cand->score += TH_SJIS_PUA_PENALTY;
        cand->prev = TH_CJ_OTHER;
    } else if (th_cj_punct5(u)) {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJ_PUNCT;
        cand->prev = TH_CJ_OTHER;
    } else if (u <= 0x7F) {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
    } else if (u == 0x80) {
        /* the spec passes the lone 0x80 through as U+0080, a control that overlaps the euro in windows-1252 */
        cand->has_pending = 0;
        cand->score += TH_DETECT_IMPLAUSIBILITY_PENALTY;
        cand->prev = TH_CJ_OTHER;
    } else {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJK_OTHER;
        cand->prev = TH_CJ_OTHER;
    }
}

static void th_cjk_score_euc_jp(th_cjk_candidate *cand, uint16_t u) {
    int hwk_prev = cand->hwk_state;
    cand->hwk_state = TH_HWK_FORBIDDEN;
    if (!cand->non_ascii_seen && u >= 0x80) {
        cand->non_ascii_seen = 1;
        if (u >= 0x3040 && u < 0x3100) {
            cand->score += TH_EUCJP_INITIAL_KANA_PENALTY;
        }
    }
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if (u >= 0xFF61 && u <= 0xFF9F) {
        cand->score += TH_HWK_SCORE;
        if ((u >= 0xFF76 && u <= 0xFF84) || u == 0xFF73) {
            cand->hwk_state = TH_HWK_DAKUTEN;
        } else if (u >= 0xFF8A && u <= 0xFF8E) {
            cand->hwk_state = TH_HWK_DAKUTEN_OR_HANDAKUTEN;
        } else if (u == 0xFF9E) {
            cand->score += (hwk_prev == TH_HWK_FORBIDDEN) ? TH_DETECT_IMPLAUSIBILITY_PENALTY : TH_HWK_VOICING_SCORE;
        } else if (u == 0xFF9F) {
            cand->score +=
                (hwk_prev != TH_HWK_DAKUTEN_OR_HANDAKUTEN) ? TH_DETECT_IMPLAUSIBILITY_PENALTY : TH_HWK_VOICING_SCORE;
        }
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_OTHER;
    } else if ((u >= 0x3041 && u <= 0x3093) || (u >= 0x30A1 && u <= 0x30F6)) {
        cand->score +=
            (u == 0x3090 || u == 0x3091 || u == 0x30F0 || u == 0x30F1) ? TH_EUCJP_NEAR_OBSOLETE_KANA : TH_EUCJP_KANA;
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        if (cand->prev_prev_byte == 0x8F) {
            cand->score += TH_EUCJP_OTHER_KANJI;
        } else if (cand->prev_byte < 0xD0) {
            cand->score += TH_EUCJP_LEVEL_1 + th_cjk_extra_score(u, th_detect_freq_frequent_kanji);
        } else {
            cand->score += TH_EUCJP_LEVEL_2;
        }
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (th_cj_punct5(u)) {
        cand->score += TH_CJ_PUNCT;
        cand->prev = TH_CJ_OTHER;
    } else if (u <= 0x7F) {
        cand->prev = TH_CJ_OTHER;
    } else {
        cand->score += TH_CJK_OTHER;
        cand->prev = TH_CJ_OTHER;
    }
}

/* The Big5 decoder reaches chardetng's written==2 both for an astral scalar (a surrogate pair, u the high surrogate)
   and for the four combining pointers that decode to U+00CA/U+00EA plus a combining mark (u the base letter): the base
   scores as CJK_OTHER, the astral as another hanzi. th_cjk_feed drains the combining mark decode.h still owes, so it is
   not scored a second time. */
static void th_cjk_score_big5(th_cjk_candidate *cand, int written, uint16_t u) {
    if (written == 2) {
        th_cjk_flush_pending(cand);
        if (u == 0x00CA || u == 0x00EA) {
            cand->score += TH_CJK_OTHER;
            cand->prev = TH_CJ_OTHER;
        } else {
            cand->score += th_cjk_maybe_pending(cand, TH_BIG5_OTHER, th_cjk_problematic_lead(cand->prev_byte));
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            }
            cand->prev = TH_CJ_CJ;
        }
        return;
    }
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        th_cjk_flush_pending(cand);
        int64_t score = (cand->prev_byte >= 0xA4 && cand->prev_byte <= 0xC6) ? TH_BIG5_LEVEL_1 : TH_BIG5_OTHER;
        cand->score += th_cjk_maybe_pending(cand, score, th_cjk_problematic_lead(cand->prev_byte));
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (th_cj_punct9(u)) {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJ_PUNCT;
        cand->prev = TH_CJ_OTHER;
    } else if (u <= 0x7F) {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
    } else {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJK_OTHER;
        cand->prev = TH_CJ_OTHER;
    }
}

/* The EUC-KR decoder is Windows-949/UHC, a superset of the EUC (KS X 1001) plane. chardetng distinguishes a Hangul
   syllable whose byte pair is entirely inside the EUC plane -- both the completing byte (`b`, here in_euc_range) and
   the byte before it (prev_byte, here prev_was_euc) in 0xA1..0xFE -- which scores the full EUC bonus, from a UHC-only
   syllable, which scores the far smaller non-EUC value and may be held pending. Collapsing the two made EUC-KR win over
   windows-1252/Big5/GBK/Shift_JIS across the corpus. */
static void th_cjk_score_euc_kr(th_cjk_candidate *cand, uint16_t u, unsigned char b) {
    int in_euc_range = b >= 0xA1 && b <= 0xFE; /* GCOVR_EXCL_BR_LINE: a scored scalar's byte is never 0xFF */
    int prev_was_euc = cand->prev_byte >= 0xA1 && cand->prev_byte <= 0xFE;
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ || cand->prev == TH_CJ_HANJA) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
        cand->word_len = 0;
    } else if (u >= 0xAC00 && u <= 0xD7A3) {
        th_cjk_flush_pending(cand);
        if (prev_was_euc && in_euc_range) {
            cand->score += TH_EUCKR_EUC_HANGUL + th_cjk_extra_score(u, th_detect_freq_frequent_hangul);
        } else {
            cand->score +=
                th_cjk_maybe_pending(cand, TH_EUCKR_NON_EUC_HANGUL, th_cjk_more_problematic_lead(cand->prev_byte));
        }
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
        cand->word_len++;
        if (cand->word_len > 5) {
            cand->score += TH_EUCKR_LONG_WORD_PENALTY;
        }
    } else if ((u >= 0x4E00 && u < 0xAC00) || (u >= 0xF900 && u <= 0xFA0B)) {
        th_cjk_flush_pending(cand);
        cand->score += TH_EUCKR_HANJA;
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        } else if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_EUCKR_HANJA_AFTER_HANGUL_PENALTY;
        }
        cand->prev = TH_CJ_HANJA;
        cand->word_len++;
        if (cand->word_len > 5) {
            cand->score += TH_EUCKR_LONG_WORD_PENALTY;
        }
    } else if (u >= 0x80) {
        th_cjk_flush_pending(cand);
        cand->score += TH_CJK_OTHER;
        cand->prev = TH_CJ_OTHER;
        cand->word_len = 0;
    } else {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
        cand->word_len = 0;
    }
}

/* A malformed sequence does not always end a candidate: chardetng recognizes the Shift_JIS-2004/MacJapanese,
   MacKorean, MacChinese, and unmapped Big5/GBK/EUC-KR byte pairs from `b` (the byte that failed), prev_byte, EUC-JP's
   prev_prev_byte, and malformed_len (the bytes the failed sequence consumed), applies a large penalty, and keeps
   scoring. An unrecognized sequence returns 0 to end the candidate. The single-byte-extension arms clear pending, since
   chardetng discards it (and reinitializes the decoder to clear its pending-ASCII state, which decode.h lacks). */
static int th_cjk_malformed(th_cjk_candidate *cand, unsigned char b, Py_ssize_t malformed_len) {
    unsigned char prev = cand->prev_byte;
    switch (cand->kind) {
    case TH_CJK_GBK:
        if ((prev == 0xA0 || prev == 0xFE || prev == 0xFD) && (b < 0x80 || b == 0xFF)) {
            cand->has_pending = 0;
            cand->score += TH_GBK_SINGLE_BYTE_EXTENSION_PENALTY;
            if ((b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')) {
                cand->prev = TH_CJ_ASCII;
            } else if (b == 0xFF) {
                cand->score += TH_GBK_SINGLE_BYTE_EXTENSION_PENALTY;
                cand->prev = TH_CJ_OTHER;
            } else {
                cand->prev = TH_CJ_OTHER;
            }
            return 1;
        }
        if (malformed_len == 1 && b == 0xFF) {
            cand->has_pending = 0;
            cand->score += TH_GBK_SINGLE_BYTE_EXTENSION_PENALTY;
            cand->prev = TH_CJ_OTHER;
            return 1;
        }
        return 0;
    case TH_CJK_SHIFT_JIS: {
        int lead_range = (prev >= 0x81 && prev <= 0x9F) || (prev >= 0xE0 && prev <= 0xFC);
        int trail_range = (b >= 0x40 && b <= 0x7E) || (b >= 0x80 && b <= 0xFC);
        int excluded = (prev == 0x82 && b >= 0xFA) || (prev == 0x84 && ((b >= 0xDD && b <= 0xE4) || b >= 0xFB)) ||
                       (prev == 0x86 && b >= 0xF2 && b <= 0xFA) || (prev == 0x87 && b >= 0x77 && b <= 0x7D) ||
                       (prev == 0xFC && b >= 0xF5);
        if (lead_range && trail_range && !excluded) {
            th_cjk_flush_pending(cand);
            cand->score += TH_SJIS_EXTENSION_PENALTY;
            if (prev < 0x87) { /* chardetng's approximate kana/kanji boundary for the extension block */
                cand->prev = TH_CJ_OTHER;
            } else {
                if (cand->prev == TH_CJ_ASCII) {
                    cand->score += TH_CJK_LATIN_ADJ;
                }
                cand->prev = TH_CJ_CJ;
            }
            return 1;
        }
        if (malformed_len == 1 && (b == 0xA0 || b >= 0xFD)) {
            cand->has_pending = 0;
            cand->score += TH_SJIS_SINGLE_BYTE_EXTENSION_PENALTY;
            cand->prev = TH_CJ_OTHER;
            return 1;
        }
        return 0;
    }
    case TH_CJK_EUC_JP: {
        unsigned char prev_prev = cand->prev_prev_byte;
        int in_pair =
            b >= 0xA1 && b <= 0xFE && prev >= 0xA1 && prev <= 0xFE; /* GCOVR_EXCL_BR_LINE: a lead is never 0xFF */
        int plane_1 = prev_prev != 0x8F && !(prev == 0xA8 && b >= 0xDF && b <= 0xE6) &&
                      !(prev == 0xAC && b >= 0xF4 && b <= 0xFC) && !(prev == 0xAD && b >= 0xD8 && b <= 0xDE);
        int plane_2 = prev_prev == 0x8F && prev != 0xA2 && prev != 0xA6 && prev != 0xA7 && prev != 0xA9 &&
                      prev != 0xAA && prev != 0xAB && prev != 0xED && !(prev == 0xFE && b >= 0xF7);
        if (in_pair && (plane_1 || plane_2)) {
            cand->score += TH_EUCJP_EXTENSION_PENALTY;
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            }
            cand->prev = TH_CJ_CJ;
            return 1;
        }
        return 0;
    }
    case TH_CJK_BIG5:
        if (prev >= 0x81 && prev <= 0xFE && ((b >= 0x40 && b <= 0x7E) || (b >= 0xA1 && b <= 0xFE))) {
            th_cjk_flush_pending(cand);
            cand->score += TH_BIG5_PUA_PENALTY;
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            }
            cand->prev = TH_CJ_CJ;
            return 1;
        }
        if ((prev == 0xA0 || prev == 0xFD || prev == 0xFE) && (b < 0x80 || b == 0xFF)) {
            cand->has_pending = 0;
            cand->score += TH_BIG5_SINGLE_BYTE_EXTENSION_PENALTY;
            /* every ASCII letter is in 0x40..0x7E, which the in-range PUA check above already claimed, so this
               single-byte arm only ever sees non-letters and chardetng's letter case is unreachable here */
            if ((b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')) { /* GCOVR_EXCL_BR_LINE */
                cand->prev = TH_CJ_ASCII;                           /* GCOVR_EXCL_LINE */
            } else if (b == 0xFF) {
                cand->score += TH_BIG5_SINGLE_BYTE_EXTENSION_PENALTY;
                cand->prev = TH_CJ_OTHER;
            } else {
                cand->prev = TH_CJ_OTHER;
            }
            return 1;
        }
        if (malformed_len == 1 && b == 0xFF) {
            cand->has_pending = 0;
            cand->score += TH_BIG5_SINGLE_BYTE_EXTENSION_PENALTY;
            cand->prev = TH_CJ_OTHER;
            return 1;
        }
        return 0;
    default: /* TH_CJK_EUC_KR */
        if ((prev == 0xC9 || prev == 0xFE) && b >= 0xA1 && b <= 0xFE) {
            th_cjk_flush_pending(cand);
            cand->score += TH_EUCKR_PUA_PENALTY;
            if (cand->prev == TH_CJ_ASCII) {
                cand->score += TH_CJK_LATIN_ADJ;
            } else if (cand->prev == TH_CJ_CJ) {
                cand->score += TH_EUCKR_HANJA_AFTER_HANGUL_PENALTY;
            }
            cand->prev = TH_CJ_HANJA;
            cand->word_len++;
            if (cand->word_len > 5) {
                cand->score += TH_EUCKR_LONG_WORD_PENALTY;
            }
            return 1;
        }
        if ((prev == 0xA1 || (prev >= 0xA3 && prev <= 0xA8) || (prev >= 0xAA && prev <= 0xAD)) &&
            (b >= 0x7B && b <= 0x7D)) {
            th_cjk_flush_pending(cand);
            cand->score += TH_EUCKR_MAC_KOREAN_PENALTY;
            cand->prev = TH_CJ_OTHER;
            cand->word_len = 0;
            return 1;
        }
        /* a euc-kr lead in 0x81..0x84 maps every 0x81..0xFE trail, so a malformed pair there only ever fails on a byte
           <= 0x80 or == 0xFF -- b is never in 0x81..0xFE, and no letter trail errors, so the letter case is dead */
        if (prev >= 0x81 && prev <= 0x84 && (b <= 0x80 || b == 0xFF)) { /* GCOVR_EXCL_BR_LINE */
            cand->has_pending = 0;
            cand->score += TH_EUCKR_SINGLE_BYTE_EXTENSION_PENALTY;
            if ((b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z')) { /* GCOVR_EXCL_BR_LINE */
                cand->prev = TH_CJ_ASCII;                           /* GCOVR_EXCL_LINE */
            } else if (b == 0x80 || b == 0xFF) {
                cand->score += TH_EUCKR_SINGLE_BYTE_EXTENSION_PENALTY;
                cand->prev = TH_CJ_OTHER;
            } else {
                cand->prev = TH_CJ_OTHER;
            }
            cand->word_len = 0;
            return 1;
        }
        if (malformed_len == 1 && (b == 0x80 || b == 0xFF)) {
            cand->has_pending = 0;
            cand->score += TH_EUCKR_SINGLE_BYTE_EXTENSION_PENALTY;
            cand->prev = TH_CJ_OTHER;
            cand->word_len = 0;
            return 1;
        }
        return 0;
    }
}

/* NOLINTEND(bugprone-branch-clone) */

/* Decode the whole buffer with the candidate's native WHATWG decoder and score each scalar the way chardetng scores the
   scalars encoding_rs hands it. On a malformed sequence th_cjk_malformed decides whether to penalize and continue, or
   end the candidate; an incomplete sequence flushed at end of stream (error_at == -1) always ends it. The scorers want
   the byte that completed a scalar and its two predecessors, which is the decoder position minus one; a malformed one
   instead reports the failing byte through error_at. A Big5 combination's second scalar consumes no byte, so its base
   reuses the trail byte and the mark is drained unscored. */
static void th_cjk_init(th_cjk_candidate *cand, th_cjk_kind kind, const char *label) {
    memset(cand, 0, sizeof(*cand));
    cand->entry = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
    cand->kind = kind;
    cand->alive = 1;
    cand->prev = TH_CJ_OTHER;
    th_decode_init(&cand->dec, cand->entry, NULL, 0);
}

/* The byte `back` positions before index `at`, reaching into the previous feed's tail when
   the index falls off the front of this one. chardetng reads the stream a byte at a time and
   so always has these; feeding in chunks is what makes them need a home. */
static unsigned char th_cjk_byte_before(const th_cjk_candidate *cand, const unsigned char *buf, Py_ssize_t at,
                                        Py_ssize_t back) {
    Py_ssize_t index = at - back;
    if (index >= 0) {
        return buf[index];
    }
    Py_ssize_t from_end = -index; /* 1 or 2 positions before this buffer began */
    return cand->tail_len >= from_end ? cand->tail[cand->tail_len - from_end] : 0;
}

/* Feed one chunk. final marks the end of the stream, where an unfinished sequence is an
   error rather than a boundary. A candidate the decoder has already killed reads nothing. */
static void th_cjk_feed(th_cjk_candidate *cand, const unsigned char *buf, Py_ssize_t len, int final) {
    if (!cand->alive) {
        return;
    }
    const unsigned char *data = buf;
    Py_ssize_t data_len = len;
    unsigned char *spliced = NULL;
    if (cand->carry_len > 0) {
        spliced = PyMem_Malloc((size_t)cand->carry_len + (size_t)len + 1);
        if (spliced == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            cand->alive = 0;   /* GCOVR_EXCL_LINE: allocation-failure path */
            return;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(spliced, cand->carry, (size_t)cand->carry_len);
        memcpy(spliced + cand->carry_len, buf, (size_t)len);
        data = spliced;
        data_len = cand->carry_len + len;
        cand->carry_len = 0;
    }
    cand->dec.buf = data;
    cand->dec.len = data_len;
    cand->dec.pos = 0;
    for (;;) {
        Py_ssize_t before = cand->dec.pos;
        Py_UCS4 point = 0;
        int status = th_decode_step(&cand->dec, &point);
        if (status == TH_DEC_FINISHED) {
            break;
        }
        if (status == TH_DEC_ERROR) {
            if (cand->dec.error_at < 0) {
                /* an incomplete sequence: at end of stream chardetng scores it as death, and
                   mid-stream it waits for the bytes that finish it */
                if (final) {
                    cand->alive = 0;
                } else {
                    /* a sequence is four bytes at most, so the carry always fits */
                    cand->carry_len = (int)(data_len - before);
                    memcpy(cand->carry, data + before, (size_t)cand->carry_len);
                }
                break;
            }
            cand->prev_byte = th_cjk_byte_before(cand, data, cand->dec.error_at, 1);
            cand->prev_prev_byte = th_cjk_byte_before(cand, data, cand->dec.error_at, 2);
            if (!th_cjk_malformed(cand, data[cand->dec.error_at], cand->dec.pos - before)) {
                cand->alive = 0;
                break;
            }
            continue;
        }
        int combining = cand->kind == TH_CJK_BIG5 && cand->dec.has_pending;
        if (combining) {
            Py_UCS4 mark = 0;
            th_decode_step(&cand->dec, &mark);
        }
        /* a scalar consumes at least one byte, and Big5's pending combining mark is drained
           above rather than reaching the loop, so the cursor has moved past `before` */
        Py_ssize_t at = cand->dec.pos - 1;
        unsigned char last = data[at];
        cand->prev_byte = th_cjk_byte_before(cand, data, at, 1);
        cand->prev_prev_byte = th_cjk_byte_before(cand, data, at, 2);
        /* an astral scalar (a gb18030 or Big5 four-byte sequence) reaches chardetng as its UTF-16 high surrogate */
        int written = combining || point > 0xFFFF ? 2 : 1;
        uint16_t unit = point <= 0xFFFF ? (uint16_t)point : (uint16_t)(0xD800 + ((point - 0x10000) >> 10));
        switch (cand->kind) {
        case TH_CJK_GBK:
            th_cjk_score_gbk(cand, written, unit, last);
            break;
        case TH_CJK_SHIFT_JIS:
            th_cjk_score_shift_jis(cand, unit, last);
            break;
        case TH_CJK_EUC_JP:
            th_cjk_score_euc_jp(cand, unit);
            break;
        case TH_CJK_BIG5:
            th_cjk_score_big5(cand, written, unit);
            break;
        default: /* TH_CJK_EUC_KR */
            th_cjk_score_euc_kr(cand, unit, last);
            break;
        }
    }
    /* The lookback remembers the last two bytes the decoder actually consumed. Bytes left in
       carry have not been read yet, so counting them here would show the next feed a byte
       twice: once as its own data[0], once as the byte before it. */
    Py_ssize_t consumed = data_len - cand->carry_len;
    if (consumed >= 2) {
        cand->tail[0] = data[consumed - 2];
        cand->tail[1] = data[consumed - 1];
        cand->tail_len = 2;
    } else if (consumed == 1) {
        cand->tail[0] = cand->tail_len >= 2 ? cand->tail[1] : 0;
        cand->tail[1] = data[0];
        cand->tail_len = 2;
    }
    cand->dec.buf = NULL;
    cand->dec.len = 0;
    cand->dec.pos = 0;
    PyMem_Free(spliced);
}

/* ISO-2022-JP is 7-bit and escape-driven: chardetng returns it when the stream has
   no high byte, contains an escape, and decodes cleanly as ISO-2022-JP. The decoder carries
   its shift state between characters, so it lives on the scan rather than on the stack, and
   an escape split across a feed leaves its bytes in carry. */
typedef struct {
    th_decoder dec;
    int esc_seen;
    int alive;
    unsigned char carry[8];
    int carry_len;
} th_jp_scan;

static void th_jp_scan_init(th_jp_scan *scan) {
    th_decode_init(&scan->dec, th_encoding_lookup("iso-2022-jp", 11), NULL, 0);
    scan->esc_seen = 0;
    scan->alive = 1;
    scan->carry_len = 0;
}

static void th_jp_scan_feed(th_jp_scan *scan, const unsigned char *buf, Py_ssize_t len, int final) {
    if (!scan->alive) {
        return;
    }
    Py_ssize_t start = 0;
    if (!scan->esc_seen) {
        /* A stream with no escape is not ISO-2022-JP whatever else it holds, so the decoder never
           reads it: finding the escape with memchr is what keeps detect() on an ASCII document
           cheap. Until one arrives the decoder sits in its ASCII state, which accepts every byte
           but the two shift codes and leaves nothing to remember, so the bytes before the escape
           need one pass for those. A byte at or above 0x80 makes the caller ignore this scan. */
        const unsigned char *escape = memchr(buf, 0x1B, (size_t)len);
        Py_ssize_t prefix = escape == NULL ? len : escape - buf;
        /* 0x0E and 0x0F are the two bytes the ASCII state rejects, and a chunk that carries one
           before the escape kills the candidate whether or not the escape ever arrives */
        if (memchr(buf, 0x0E, (size_t)prefix) != NULL || memchr(buf, 0x0F, (size_t)prefix) != NULL) {
            scan->alive = 0;
            return;
        }
        if (escape == NULL) {
            return;
        }
        start = prefix;
        scan->esc_seen = 1;
    }
    buf += start;
    len -= start;
    const unsigned char *data = buf;
    Py_ssize_t data_len = len;
    unsigned char *spliced = NULL;
    if (scan->carry_len > 0) {
        spliced = PyMem_Malloc((size_t)scan->carry_len + (size_t)len + 1);
        if (spliced == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            scan->alive = 0;   /* GCOVR_EXCL_LINE: allocation-failure path */
            return;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        memcpy(spliced, scan->carry, (size_t)scan->carry_len);
        memcpy(spliced + scan->carry_len, buf, (size_t)len);
        data = spliced;
        data_len = scan->carry_len + len;
        scan->carry_len = 0;
    }
    scan->dec.buf = data;
    scan->dec.len = data_len;
    scan->dec.pos = 0;
    for (;;) {
        Py_ssize_t before = scan->dec.pos;
        Py_UCS4 point = 0;
        int status = th_decode_step(&scan->dec, &point);
        if (status == TH_DEC_FINISHED) {
            break;
        }
        if (status == TH_DEC_ERROR) {
            if (scan->dec.error_at < 0 && !final) {
                scan->carry_len = (int)(data_len - before);
                memcpy(scan->carry, data + before, (size_t)scan->carry_len);
            } else {
                scan->alive = 0;
            }
            break;
        }
    }
    scan->dec.buf = NULL;
    scan->dec.len = 0;
    scan->dec.pos = 0;
    PyMem_Free(spliced);
}

static int th_jp_scan_done(const th_jp_scan *scan) {
    return scan->alive && scan->esc_seen;
}

/* Whole-buffer ISO-2022-JP liveness, the one-shot form. */
static int th_iso2022jp_alive(const unsigned char *buf, Py_ssize_t len) {
    th_jp_scan scan;
    th_jp_scan_init(&scan);
    th_jp_scan_feed(&scan, buf, len, 1);
    return th_jp_scan_done(&scan);
}

/* Every candidate chardetng scores, fed byte by byte and read once at the end. The stream is
   the only implementation: th_encoding_detect drives it with a single feed, so the
   differential test against chardetng covers the same scoring the chunked path uses. */
typedef struct {
    th_utf8_scan utf8;
    th_jp_scan jp;
    th_cjk_candidate cjk[5];
    th_sb_candidate sb[TH_SB_COUNT];
    th_sb_candidate visual;
} th_detect_stream;

/* chardetng's candidate order, which the strict ">" in the winner loop needs: an earlier
   candidate keeps a tie. */
static const struct {
    th_cjk_kind kind;
    const char *label;
} th_cjk_slots[5] = {
    {TH_CJK_GBK, "gbk"},   {TH_CJK_EUC_JP, "euc-jp"}, {TH_CJK_EUC_KR, "euc-kr"}, {TH_CJK_SHIFT_JIS, "shift_jis"},
    {TH_CJK_BIG5, "big5"},
};

static void th_detect_stream_init(th_detect_stream *stream) {
    th_utf8_scan_init(&stream->utf8);
    th_jp_scan_init(&stream->jp);
    for (int slot = 0; slot < 5; slot++) {
        th_cjk_init(&stream->cjk[slot], th_cjk_slots[slot].kind, th_cjk_slots[slot].label);
    }
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_init(&stream->sb[slot], &th_detect_single_byte_data[th_sb_candidate_table[slot].data_index],
                   th_sb_candidate_table[slot].kind);
    }
    th_sb_init(&stream->visual, &th_detect_single_byte_data[TH_DETECT_ISO_8859_8_ROW], TH_SB_VISUAL);
}

/* The two candidate arrays laid end to end under chardetng's indices, so one number addresses any
   candidate the TLD tables name. Index 3..8 is a CJK slot; 8..27 a single-byte one. */
static int th_detect_alive(const th_detect_stream *stream, int index) {
    if (index < TH_DETECT_WESTERN_INDEX) {
        return stream->cjk[index - TH_DETECT_GBK_INDEX].alive;
    }
    return stream->sb[index - TH_DETECT_WESTERN_INDEX].alive;
}

static const char *th_detect_label(int index) {
    if (index < TH_DETECT_WESTERN_INDEX) {
        return th_cjk_slots[index - TH_DETECT_GBK_INDEX].label;
    }
    return th_detect_single_byte_data[th_sb_candidate_table[index - TH_DETECT_WESTERN_INDEX].data_index].label;
}

/* Whether the TLD's expectation survived the bytes: some encoding native to it still scores. When none
   does, chardetng reads the TLD as mistaken rather than as evidence and stops penalizing the candidates
   that did score. A Chinese or Central European TLD tries its sibling script first, since the two share
   a domain; *tld takes that sibling. */
static int th_detect_expectation(const th_detect_stream *stream, th_tld *tld) {
    if (*tld != TH_TLD_GENERIC) {
        for (int index = TH_DETECT_GBK_INDEX; index <= TH_DETECT_CYRILLIC_ISO_INDEX; index++) {
            if (th_tld_is_native(*tld, index) && th_detect_alive(stream, index)) {
                return 1;
            }
        }
    }
    static const struct {
        th_tld from;
        th_tld to;
        int index;
    } siblings[] = {
        {TH_TLD_SIMPLIFIED, TH_TLD_TRADITIONAL, TH_DETECT_BIG5_INDEX},
        {TH_TLD_TRADITIONAL, TH_TLD_SIMPLIFIED, TH_DETECT_GBK_INDEX},
        {TH_TLD_CENTRAL_WINDOWS, TH_TLD_CENTRAL_ISO, TH_DETECT_CENTRAL_ISO_INDEX},
        {TH_TLD_CENTRAL_ISO, TH_TLD_CENTRAL_WINDOWS, TH_DETECT_CENTRAL_WINDOWS_INDEX},
    };
    for (int row = 0; row < (int)(sizeof(siblings) / sizeof(siblings[0])); row++) {
        if (*tld == siblings[row].from && th_detect_alive(stream, siblings[row].index)) {
            *tld = siblings[row].to;
            return 1;
        }
    }
    return 0;
}

/* Feed the scored candidates only. th_encoding_detect calls this directly, having already
   ruled out the pure-ASCII and valid-UTF-8 answers that need no scoring at all. */
static void th_detect_candidates_feed(th_detect_stream *stream, const unsigned char *buf, Py_ssize_t len, int final) {
    for (int slot = 0; slot < 5; slot++) {
        th_cjk_feed(&stream->cjk[slot], buf, len, final);
    }
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_feed(&stream->sb[slot], buf, len);
    }
    th_sb_feed(&stream->visual, buf, len);
    if (final) {
        for (int slot = 0; slot < TH_SB_COUNT; slot++) {
            th_sb_feed_eof(&stream->sb[slot]);
        }
        th_sb_feed_eof(&stream->visual);
    }
}

static void th_detect_stream_feed(th_detect_stream *stream, const unsigned char *buf, Py_ssize_t len, int final) {
    th_utf8_scan_feed(&stream->utf8, buf, len);
    th_jp_scan_feed(&stream->jp, buf, len, final);
    th_detect_candidates_feed(stream, buf, len, final);
}

/* The scored answer: the surviving CJK and single-byte candidates compete in chardetng's
   strict-max, defaulting to the TLD's own encoding, with the Hebrew visual/logical tiebreak
   last. Every survivor lands in *scores so detect() can rank alternatives. */
static const th_encoding_entry *th_detect_pick_winner(const th_detect_stream *stream, th_tld tld,
                                                      th_detect_scores *scores) {
    /* even a TLD the bytes contradict names the fallback: chardetng reads the default before
       th_detect_expectation moves tld to its sibling script */
    const char *winner = th_detect_label(th_tld_default_encoding[tld]);
    int64_t max = 0;
    int expectation_is_valid = th_detect_expectation(stream, &tld);
    for (int slot = 0; slot < 5; slot++) {
        if (stream->cjk[slot].alive) {
            int64_t score =
                th_tld_adjust(stream->cjk[slot].score, TH_DETECT_GBK_INDEX + slot, tld, expectation_is_valid);
            scores->items[scores->count].label = th_cjk_slots[slot].label;
            scores->items[scores->count].score = score;
            scores->count++;
            if (score > max) {
                max = score;
                winner = th_cjk_slots[slot].label;
            }
        }
    }
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        int64_t score;
        if (th_sb_score(&stream->sb[slot], TH_DETECT_WESTERN_INDEX + slot, tld, expectation_is_valid, &score)) {
            scores->items[scores->count].label = stream->sb[slot].data->label;
            scores->items[scores->count].score = score;
            scores->count++;
            if (score > max) {
                max = score;
                winner = stream->sb[slot].data->label;
            }
        }
    }
    /* Hebrew tiebreak. The visual candidate scores off the ISO-8859-8 tables and never enters the loop
       above, so it can outscore the whole field as well as tie windows-1255; the order the punctuation
       suits wins. Outscoring the field takes an input where windows-1255 is disqualified for want of a
       two-letter word, which no corpus here reaches, so the two ways in fold bitwise rather than
       short-circuit, leaving no branch a test cannot take. */
    int64_t visual_score;
    if (th_sb_score(&stream->visual, TH_DETECT_VISUAL_INDEX, tld, expectation_is_valid, &visual_score)) {
        scores->items[scores->count].label = stream->visual.data->label;
        scores->items[scores->count].score = visual_score;
        scores->count++;
        int contends = (visual_score > max) | (strcmp(winner, "windows-1255") == 0);
        if (contends && stream->visual.plausible_punctuation > stream->sb[TH_SB_LOGICAL_SLOT].plausible_punctuation) {
            winner = "iso-8859-8";
        }
    }
    return th_encoding_lookup(winner, (Py_ssize_t)strlen(winner));
}

/* The stream's answer once every byte has been fed, or NULL when the stream is pure ASCII
   (the caller then keeps the windows-1252 fallback, which decodes ASCII identically).
   ISO-2022-JP and UTF-8 are resolved structurally and need no scoring, so a TLD cannot
   overrule them any more than it can overrule a byte-order mark. */
static const th_encoding_entry *th_detect_stream_finish(const th_detect_stream *stream, th_tld tld,
                                                        th_detect_scores *scores) {
    scores->count = 0;
    scores->structural = 0;
    if (!stream->utf8.has_non_ascii) {
        if (th_jp_scan_done(&stream->jp)) {
            scores->structural = 1;
            return th_encoding_lookup("iso-2022-jp", 11);
        }
        return NULL;
    }
    if (th_utf8_scan_done(&stream->utf8)) {
        scores->structural = 1;
        return th_encoding_lookup("utf-8", 5);
    }
    return th_detect_pick_winner(stream, tld, scores);
}

/* Guess the encoding of a declaration-less byte stream in one shot. The two structural
   answers come first, so an ASCII or UTF-8 document never pays for candidate scoring. */
static const th_encoding_entry *th_encoding_detect(const unsigned char *buf, Py_ssize_t len, th_tld tld,
                                                   th_detect_scores *scores) {
    scores->count = 0;
    scores->structural = 0;
    int has_non_ascii;
    int is_utf8 = th_detect_is_utf8(buf, len, &has_non_ascii);
    if (!has_non_ascii) {
        if (th_iso2022jp_alive(buf, len)) {
            scores->structural = 1;
            return th_encoding_lookup("iso-2022-jp", 11);
        }
        return NULL;
    }
    if (is_utf8) {
        scores->structural = 1;
        return th_encoding_lookup("utf-8", 5);
    }
    th_detect_stream stream;
    th_detect_stream_init(&stream);
    th_detect_candidates_feed(&stream, buf, len, 1);
    return th_detect_pick_winner(&stream, tld, scores);
}
