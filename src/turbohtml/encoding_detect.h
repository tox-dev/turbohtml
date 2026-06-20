/* Content-based character encoding detection (issue #182), #included into
   tree_type.c after encoding.h.

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

#include "encoding_detect_data.h"

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

/* Validate a byte run as well-formed UTF-8, setting *has_non_ascii when it holds
   at least one multi-byte sequence. Returns 1 for valid UTF-8, 0 on the first
   malformed, overlong, surrogate, or truncated sequence -- a single error
   disqualifies UTF-8, as in chardetng. Pure ASCII is valid UTF-8 but leaves
   *has_non_ascii 0, since ASCII decodes identically under the windows-1252
   fallback and carries no evidence either way. */
static int th_detect_is_utf8(const unsigned char *buf, Py_ssize_t len, int *has_non_ascii) {
    *has_non_ascii = 0;
    Py_ssize_t index = 0;
    while (index < len) {
        unsigned char lead = buf[index];
        if (lead < 0x80) {
            index++;
            continue;
        }
        *has_non_ascii = 1;
        Py_ssize_t trailing;
        unsigned char first_low;
        unsigned char first_high;
        /* the first continuation byte carries the lower bound that rejects an overlong
           form and the upper bound that rejects surrogates / values above U+10FFFF; the
           rest of the continuation bytes are the full 0x80..0xBF */
        if (lead >= 0xC2 && lead <= 0xDF) {
            trailing = 1;
            first_low = 0x80;
            first_high = 0xBF;
        } else if (lead >= 0xE0 && lead <= 0xEF) {
            trailing = 2;
            first_low = lead == 0xE0 ? 0xA0 : 0x80;  /* E0: no overlong 80..9F */
            first_high = lead == 0xED ? 0x9F : 0xBF; /* ED: no surrogate A0..BF */
        } else if (lead >= 0xF0 && lead <= 0xF4) {
            trailing = 3;
            first_low = lead == 0xF0 ? 0x90 : 0x80;  /* F0: no overlong 80..8F */
            first_high = lead == 0xF4 ? 0x8F : 0xBF; /* F4: nothing above U+10FFFF */
        } else {
            return 0; /* C0/C1, F5..FF, or a stray continuation byte as lead */
        }
        if (index + trailing >= len) {
            return 0; /* truncated trailing sequence */
        }
        if (buf[index + 1] < first_low || buf[index + 1] > first_high) {
            return 0;
        }
        for (Py_ssize_t offset = 2; offset <= trailing; offset++) {
            if (buf[index + offset] < 0x80 || buf[index + offset] > 0xBF) {
                return 0;
            }
        }
        index += trailing + 1;
    }
    return 1;
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
static long th_detect_pair_score(const th_detect_single_byte *data, int current, int previous, int windows_1256) {
    int boundary = data->ascii + data->non_ascii;
    if (current < boundary && previous < boundary) {
        if ((previous == 0 && current == 0) || (previous < data->ascii && current < data->ascii)) {
            return 0;
        }
        int index = current >= data->ascii ? data->ascii * data->non_ascii +
                                                 (data->ascii + data->non_ascii) * (current - data->ascii) + previous
                                           : current * data->non_ascii + previous - data->ascii;
        unsigned char stored = data->probabilities[index];
        return stored == 255 ? TH_DETECT_IMPLAUSIBILITY_PENALTY : (long)stored;
    }
    if (current < boundary) {
        /* current alphabetic below the boundary, previous above it */
        if (current == 0 || current == TH_DETECT_ASCII_DIGIT ||
            (windows_1256 && current == TH_DETECT_WINDOWS_1256_ZWNJ)) {
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
        /* current above the boundary, previous alphabetic below it */
        if (previous == 0 || previous == TH_DETECT_ASCII_DIGIT ||
            (windows_1256 && previous == TH_DETECT_WINDOWS_1256_ZWNJ)) {
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
    long score;
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

static void th_sb_init(th_sb_candidate *cand, const th_detect_single_byte *data, th_sb_kind kind) {
    memset(cand, 0, sizeof(*cand));
    cand->data = data;
    cand->kind = kind;
    cand->alive = 1;
    cand->prev_ascii = 1;
    cand->case_state = TH_LAT_SPACE; /* both case enums start at 0 == Space */
    cand->ordinal_state = TH_ORD_SPACE;
    cand->windows_1252 = strcmp(data->label, "windows-1252") == 0;
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
                long penalty = cand->prev_non_ascii <= 2   ? 0
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
                    cand->score += TH_DETECT_NON_LATIN_MIXED_CASE_PENALTY * (long)cand->current_word_len;
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

/* The candidate's final score, or 0 (not scored) when it is disqualified or, for a
   non-Latin script, has not seen a word of at least two non-Latin letters. */
static int th_sb_final_score(const th_sb_candidate *cand, long *out) {
    if (!cand->alive) {
        return 0;
    }
    if (cand->kind != TH_SB_LATIN && cand->longest_word < 2) {
        return 0;
    }
    *out = cand->score;
    return 1;
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
#define TH_DETECT_ISO_8859_8_INDEX 13

/* Guess the encoding of a declaration-less byte stream, or NULL when it is pure
   ASCII (the caller then keeps the windows-1252 fallback, which decodes ASCII
   identically). UTF-8 is resolved structurally; otherwise the single-byte
   candidates run and the highest-scoring one wins, defaulting to windows-1252. The
   CJK candidates land in a later phase. */
static const th_encoding_entry *th_encoding_detect(const unsigned char *buf, Py_ssize_t len) {
    int has_non_ascii;
    if (th_detect_is_utf8(buf, len, &has_non_ascii) && has_non_ascii) {
        return th_encoding_lookup("utf-8", 5);
    }
    if (!has_non_ascii) {
        return NULL;
    }
    th_sb_candidate candidates[TH_SB_COUNT];
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_init(&candidates[slot], &th_detect_single_byte_data[th_sb_candidate_table[slot].data_index],
                   th_sb_candidate_table[slot].kind);
        th_sb_feed(&candidates[slot], buf, len);
    }
    th_sb_candidate visual;
    th_sb_init(&visual, &th_detect_single_byte_data[TH_DETECT_ISO_8859_8_INDEX], TH_SB_VISUAL);
    th_sb_feed(&visual, buf, len);

    const char *winner = "windows-1252";
    long max = 0;
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        long score;
        if (th_sb_final_score(&candidates[slot], &score) && score > max) {
            max = score;
            winner = candidates[slot].data->label;
        }
    }
    long visual_score;
    if (th_sb_final_score(&visual, &visual_score) && (visual_score > max || strcmp(winner, "windows-1255") == 0) &&
        visual.plausible_punctuation > candidates[TH_SB_LOGICAL_SLOT].plausible_punctuation) {
        winner = "iso-8859-8";
    }
    return th_encoding_lookup(winner, (Py_ssize_t)strlen(winner));
}
