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

/* ---- CJK multi-byte candidates (issue #182, phase 3) -----------------------

   chardetng scores the CJK candidates by feeding each byte to an encoding_rs
   decoder and classifying every scalar it emits. turbohtml has no encoding_rs, so
   each candidate drives the matching CPython incremental codec instead and applies
   the same scoring. The two decoders agree on well-formed CJK text, which is what
   decides the guess. chardetng additionally keeps a few malformed byte sequences
   alive with a large penalty (Shift_JIS-2004, the Mac extensions, unmapped Big5 and
   GBK pairs); the strict CPython codec disqualifies them, but those candidates lose
   the strict-max either way, so the winner is unchanged. The scoring constants are
   chardetng's, with the integer divisions pre-evaluated. */
#define TH_CJK_BASE 41
#define TH_CJK_SECONDARY 20
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
#define TH_EUCKR_EUC_HANGUL 42
#define TH_EUCKR_HANJA 10
#define TH_EUCKR_HANJA_AFTER_HANGUL_PENALTY (-410)
#define TH_EUCKR_LONG_WORD_PENALTY (-6)
#define TH_GBK_LEVEL_1 41
#define TH_GBK_LEVEL_2 20
#define TH_GBK_NON_EUC 5
#define TH_GBK_PUA_PENALTY (-410)

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
    PyObject *decoder;
    th_cjk_kind kind;
    int alive;
    long score;
    unsigned char prev_byte;
    unsigned char prev_prev_byte;
    int prev;
    long pending;
    int has_pending;
    int hwk_state;
    int hwk_seen;
    int non_ascii_seen;
    unsigned long word_len;
} th_cjk_candidate;

/* chardetng's cjk_extra_score: a frequency bonus that is larger the earlier the
   scalar appears in the 128-entry most-frequent table for its script. */
static long th_cjk_extra_score(uint16_t u, const uint16_t *table) {
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
static long th_cjk_maybe_pending(th_cjk_candidate *cand, long s, int problematic) {
    if (cand->prev == TH_CJ_CJ || !problematic) {
        return s;
    }
    cand->pending = s;
    cand->has_pending = 1;
    return 0;
}

static int th_cj_punct5(uint16_t u) {
    return u == 0x3000 || u == 0x3001 || u == 0x3002 || u == 0xFF08 || u == 0xFF09;
}

static int th_cj_punct9(uint16_t u) {
    return th_cj_punct5(u) || u == 0xFF01 || u == 0xFF0C || u == 0xFF1B || u == 0xFF1F;
}

/* The GB18030-required PUA mappings that chardetng treats as ideographs rather than
   penalizing as private-use. */
static int th_gbk_pua_ideograph(uint16_t u) {
    return (u >= 0xE78D && u <= 0xE796) || (u >= 0xE816 && u <= 0xE818) || u == 0xE81E || u == 0xE826 || u == 0xE82B ||
           u == 0xE82C || u == 0xE831 || u == 0xE832 || u == 0xE83B || u == 0xE843 || u == 0xE854 || u == 0xE855 ||
           u == 0xE864;
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
    } else if (u >= 0x3400 && u < 0xA000) {
        /* the strict CPython shift_jis codec never emits the U+F900..U+FAFF compatibility
           ideographs chardetng also matches here, so that range is dropped */
        th_cjk_flush_pending(cand);
        long s = (cand->prev_byte < 0x98 || (cand->prev_byte == 0x98 && b < 0x73))
                     ? TH_SJIS_LEVEL_1 + th_cjk_extra_score(u, th_detect_freq_frequent_kanji)
                     : TH_SJIS_LEVEL_2;
        cand->score += th_cjk_maybe_pending(cand, s, th_cjk_problematic_lead(cand->prev_byte));
        if (cand->prev == TH_CJ_ASCII) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_CJ;
    } else if (th_cj_punct5(u)) {
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
    /* chardetng's Shift_JIS scorer also penalizes the private-use area and the lone
       0x80, but the strict CPython shift_jis codec emits neither, so those arms are
       unreachable here and omitted. */
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
    } else if (u >= 0x3400 && u < 0xA000) {
        /* like shift_jis, the euc_jp codec never emits the U+F900..U+FAFF compatibility
           ideographs, so chardetng's second range is dropped */
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

/* The strict CPython big5 codec emits one BMP scalar per sequence -- no astral
   scalars and no two-scalar combining output -- so chardetng's written==2 arm is
   unreachable here and omitted. */
static void th_cjk_score_big5(th_cjk_candidate *cand, uint16_t u) {
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        th_cjk_flush_pending(cand);
        long s = (cand->prev_byte >= 0xA4 && cand->prev_byte <= 0xC6) ? TH_BIG5_LEVEL_1 : TH_BIG5_OTHER;
        cand->score += th_cjk_maybe_pending(cand, s, th_cjk_problematic_lead(cand->prev_byte));
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

/* The strict CPython euc_kr codec decodes only the EUC (KS X 1001) plane, where every
   Hangul syllable has both bytes in 0xA1..0xFE; chardetng's non-EUC Hangul arm (the
   Windows-949/UHC extension) is therefore unreachable here and omitted. */
static void th_cjk_score_euc_kr(th_cjk_candidate *cand, uint16_t u) {
    if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ || cand->prev == TH_CJ_HANJA) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
        cand->word_len = 0;
    } else if (u >= 0xAC00 && u <= 0xD7A3) {
        th_cjk_flush_pending(cand);
        cand->score += TH_EUCKR_EUC_HANGUL + th_cjk_extra_score(u, th_detect_freq_frequent_hangul);
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

/* NOLINTEND(bugprone-branch-clone) */

/* Drive one CPython incremental codec a byte at a time, mirroring chardetng's
   decode-and-classify loop. A decode error disqualifies the candidate. */
static void th_cjk_feed(th_cjk_candidate *cand, const unsigned char *buf, Py_ssize_t len) {
    for (Py_ssize_t i = 0; i < len; i++) {
        unsigned char b = buf[i];
        PyObject *chunk = PyBytes_FromStringAndSize((const char *)&b, 1);
        if (chunk == NULL) { /* GCOVR_EXCL_START -- single-byte allocation only fails on OOM */
            PyErr_Clear();
            cand->alive = 0;
            return;
        } /* GCOVR_EXCL_STOP */
        PyObject *out = PyObject_CallMethod(cand->decoder, "decode", "O", chunk);
        Py_DECREF(chunk);
        if (out == NULL) {
            PyErr_Clear();
            cand->alive = 0;
            return;
        }
        Py_ssize_t n = PyUnicode_GET_LENGTH(out);
        if (n > 0) {
            /* each one-byte feed completes at most one scalar, so the decoded string is a
               single code point; an astral one (GB18030 four-byte) maps to written==2 via
               its UTF-16 high surrogate, mirroring chardetng's encoding_rs output */
            Py_UCS4 cp0 = PyUnicode_READ_CHAR(out, 0);
            int written = cp0 > 0xFFFF ? 2 : 1;
            uint16_t u = cp0 <= 0xFFFF ? (uint16_t)cp0 : (uint16_t)(0xD800 + ((cp0 - 0x10000) >> 10));
            switch (cand->kind) {
            case TH_CJK_GBK:
                th_cjk_score_gbk(cand, written, u, b);
                break;
            case TH_CJK_SHIFT_JIS:
                th_cjk_score_shift_jis(cand, u, b);
                break;
            case TH_CJK_EUC_JP:
                th_cjk_score_euc_jp(cand, u);
                break;
            case TH_CJK_BIG5:
                th_cjk_score_big5(cand, u);
                break;
            default: /* TH_CJK_EUC_KR */
                th_cjk_score_euc_kr(cand, u);
                break;
            }
        }
        Py_DECREF(out);
        cand->prev_prev_byte = cand->prev_byte;
        cand->prev_byte = b;
    }
    PyObject *flush = PyObject_CallMethod(cand->decoder, "decode", "y#i", "", (Py_ssize_t)0, 1);
    if (flush == NULL) {
        PyErr_Clear();
        cand->alive = 0;
        return;
    }
    Py_DECREF(flush);
}

/* Run one CJK candidate end to end against the strict CPython codec named by entry,
   updating *winner/*max when it both survives and beats the running best. */
static void th_cjk_run(th_cjk_kind kind, const char *label, const unsigned char *buf, Py_ssize_t len,
                       const char **winner, long *max) {
    const th_encoding_entry *entry = th_encoding_lookup(label, (Py_ssize_t)strlen(label));
    PyObject *decoder = PyCodec_IncrementalDecoder(entry->codec, "strict");
    if (decoder == NULL) { /* GCOVR_EXCL_START -- every CJK codec name is a built-in codec */
        PyErr_Clear();
        return;
    } /* GCOVR_EXCL_STOP */
    th_cjk_candidate cand = {.decoder = decoder, .kind = kind, .alive = 1, .prev = TH_CJ_OTHER};
    th_cjk_feed(&cand, buf, len);
    Py_DECREF(decoder);
    if (cand.alive && cand.score > *max) {
        *max = cand.score;
        *winner = label;
    }
}

/* ISO-2022-JP is 7-bit and escape-driven: chardetng returns it when the stream has
   no high byte, contains an escape, and decodes cleanly as ISO-2022-JP. */
static int th_iso2022jp_alive(const unsigned char *buf, Py_ssize_t len) {
    int esc = 0;
    for (Py_ssize_t i = 0; i < len; i++) {
        if (buf[i] == 0x1B) {
            esc = 1;
            break;
        }
    }
    if (!esc) {
        return 0;
    }
    const th_encoding_entry *entry = th_encoding_lookup("iso-2022-jp", 11);
    PyObject *decoded = PyUnicode_Decode((const char *)buf, len, entry->codec, "strict");
    if (decoded == NULL) {
        PyErr_Clear();
        return 0;
    }
    Py_DECREF(decoded);
    return 1;
}

/* Guess the encoding of a declaration-less byte stream, or NULL when it is pure
   ASCII (the caller then keeps the windows-1252 fallback, which decodes ASCII
   identically). ISO-2022-JP and UTF-8 are resolved structurally; otherwise the CJK
   and single-byte candidates compete in chardetng's strict-max, defaulting to
   windows-1252, with the Hebrew visual/logical tiebreak applied last. */
static const th_encoding_entry *th_encoding_detect(const unsigned char *buf, Py_ssize_t len) {
    int has_non_ascii;
    int is_utf8 = th_detect_is_utf8(buf, len, &has_non_ascii);
    if (!has_non_ascii) {
        if (th_iso2022jp_alive(buf, len)) {
            return th_encoding_lookup("iso-2022-jp", 11);
        }
        return NULL;
    }
    if (is_utf8) {
        return th_encoding_lookup("utf-8", 5);
    }

    const char *winner = "windows-1252";
    long max = 0;

    /* CJK candidates run first, matching chardetng's index order so the strict ">"
       below resolves ties the same way (an earlier candidate keeps a tie). */
    th_cjk_run(TH_CJK_GBK, "gbk", buf, len, &winner, &max);
    th_cjk_run(TH_CJK_EUC_JP, "euc-jp", buf, len, &winner, &max);
    th_cjk_run(TH_CJK_EUC_KR, "euc-kr", buf, len, &winner, &max);
    th_cjk_run(TH_CJK_SHIFT_JIS, "shift_jis", buf, len, &winner, &max);
    th_cjk_run(TH_CJK_BIG5, "big5", buf, len, &winner, &max);

    th_sb_candidate candidates[TH_SB_COUNT];
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_init(&candidates[slot], &th_detect_single_byte_data[th_sb_candidate_table[slot].data_index],
                   th_sb_candidate_table[slot].kind);
        th_sb_feed(&candidates[slot], buf, len);
    }
    th_sb_candidate visual;
    th_sb_init(&visual, &th_detect_single_byte_data[TH_DETECT_ISO_8859_8_INDEX], TH_SB_VISUAL);
    th_sb_feed(&visual, buf, len);

    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        long score;
        if (th_sb_final_score(&candidates[slot], &score) && score > max) {
            max = score;
            winner = candidates[slot].data->label;
        }
    }
    /* Hebrew tiebreak. The visual (ISO-8859-8) and logical (windows-1255) candidates
       score Hebrew text identically -- they differ only in punctuation plausibility --
       so the visual order can only ever tie windows-1255, never outscore the field;
       chardetng's visual_score > max test is dead here and dropped. The branches are
       fully nested so each decision is counted on its own. */
    long visual_score;
    if (th_sb_final_score(&visual, &visual_score)) {
        if (strcmp(winner, "windows-1255") == 0) {
            if (visual.plausible_punctuation > candidates[TH_SB_LOGICAL_SLOT].plausible_punctuation) {
                winner = "iso-8859-8";
            }
        }
    }
    return th_encoding_lookup(winner, (Py_ssize_t)strlen(winner));
}
