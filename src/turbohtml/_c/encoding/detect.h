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

static TH_HOT void th_utf8_scan_feed(th_utf8_scan *scan, const unsigned char *buf, Py_ssize_t len) {
    if (!scan->valid) {
        return;
    }
    /* the ASCII a feed opens with changes nothing between sequences, so it is stepped over a word at a time */
    for (Py_ssize_t index = scan->trailing == 0 ? th_decode_ascii_run(buf, len) : 0; index < len; index++) {
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
static TH_HOT unsigned char th_detect_classify(const th_detect_single_byte *data, unsigned char byte) {
    return byte < 0x80 ? data->lower[byte] : data->upper[byte & 0x7F];
}

/* Whether a class is a non-Latin alphabetic letter (windows-1256 reserves an extra
   low class for ZWNJ). */
static TH_HOT int th_detect_is_non_latin(const th_detect_single_byte *data, unsigned char caseless, int windows_1256) {
    int lower_bound = windows_1256 ? TH_DETECT_WINDOWS_1256_ZWNJ : 1;
    return caseless > lower_bound && caseless < data->ascii + data->non_ascii;
}

/* The frequency score chardetng assigns to a (previous, current) class pair: a
   stored probability for a pair below the boundary (255 meaning implausible), and
   the plausibility rules for pairs that straddle it. A direct port of
   SingleByteData::score. */
static TH_HOT int64_t th_detect_pair_score(const th_detect_single_byte *data, int current, int previous,
                                           int windows_1256) {
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

/* th_detect_pair_score for every class pair of every table, so the walk reads a score instead of deriving it. A row is
   indexed previous * TH_SB_PAIR_WIDTH + current; classes run from 0 to the boundary plus five, at most 77, and the
   ASCII-digit class 100 takes the last column. A cell holds the score itself. */
#define TH_SB_PAIR_WIDTH 80
#define TH_SB_PAIR_ROWS ((int)(sizeof(th_detect_single_byte_data) / sizeof(th_detect_single_byte_data[0])))
#define TH_SB_WINDOWS_1256_ROW 14
static int16_t th_sb_pairs[TH_SB_PAIR_ROWS][TH_SB_PAIR_WIDTH * TH_SB_PAIR_WIDTH];

/* Everything the walk asks of a byte's class, for every byte of every table, packed so one load answers it: the
   caseless class, the upper-case bit, whether the byte is unmapped, whether the class is a non-Latin letter and
   whether it is a letter below the boundary, and the class's pair-table column, plain and as a row offset. A
   candidate keeps the word of its previous byte, and the zero word stands for the space class it starts after. */
#define TH_SB_INFO_CASELESS 0x7Fu
#define TH_SB_INFO_UPPER 0x80u
#define TH_SB_INFO_DEAD 0x100u
#define TH_SB_INFO_NON_LATIN 0x200u
#define TH_SB_INFO_ALPHA 0x400u
#define TH_SB_INFO_COLUMN(info) (((info) >> 11) & 0x7Fu)
#define TH_SB_INFO_ROW(info) ((info) >> 18)
static uint32_t th_sb_infos[TH_SB_PAIR_ROWS][256];
/* For the scripts other than Latin, the most a byte adds to the score after the byte before it, read through two
   per-byte tables: th_sb_bound_before points at the row of pairs for the byte before, and th_sb_bound_after indexes
   that row for the byte itself. Both go by
   column, except that a byte before splits into a lower-case letter of the script and the rest, which the
   capitalized-word bonus tells apart, and ibm866's no-break space gets an index of its own on both sides. */
#define TH_SB_BOUND_AFTER (TH_SB_PAIR_WIDTH + 1)
#define TH_SB_BOUND_BEFORE (2 * TH_SB_PAIR_WIDTH + 1)
static int16_t th_sb_bound_pairs[TH_SB_PAIR_ROWS][TH_SB_BOUND_BEFORE * TH_SB_BOUND_AFTER];
static const int16_t *th_sb_bound_before[TH_SB_PAIR_ROWS][256];
static uint8_t th_sb_bound_after[TH_SB_PAIR_ROWS][256];
/* For the Latin scripts, the most a byte can add after any byte: a looser bound than the pairs give, but one load a
   byte, and enough for a Latin candidate on text in another script, whose bytes pair poorly whatever comes before. */
static uint16_t th_sb_gain[TH_SB_PAIR_ROWS][256];

static TH_HOT int th_sb_pair_column(int cls) {
    return cls == TH_DETECT_ASCII_DIGIT ? TH_SB_PAIR_WIDTH - 1 : cls;
}

static void th_cjk_freq_build(void);
static void th_sb_bound_build(void);

void th_detect_tables_build(void) {
    for (int row = 0; row < TH_SB_PAIR_ROWS; row++) {
        const th_detect_single_byte *data = &th_detect_single_byte_data[row];
        int windows_1256 = row == TH_SB_WINDOWS_1256_ROW;
        for (int byte = 0; byte < 256; byte++) {
            unsigned char cls = th_detect_classify(data, (unsigned char)byte);
            unsigned caseless = cls & TH_SB_INFO_CASELESS;
            unsigned column = (unsigned)th_sb_pair_column((int)caseless);
            th_sb_infos[row][byte] =
                cls == 255
                    ? TH_SB_INFO_DEAD
                    : caseless | (cls & TH_SB_INFO_UPPER) |
                          (th_detect_is_non_latin(data, (unsigned char)caseless, windows_1256) ? TH_SB_INFO_NON_LATIN
                                                                                               : 0) |
                          (caseless > 0 && (int)caseless < data->ascii + data->non_ascii ? TH_SB_INFO_ALPHA : 0) |
                          (column << 11) | ((column * TH_SB_PAIR_WIDTH) << 18);
        }
        int classes = data->ascii + data->non_ascii + 6;
        for (int previous = 0; previous <= classes; previous++) {
            int prev_class = previous == classes ? TH_DETECT_ASCII_DIGIT : previous;
            for (int current = 0; current <= classes; current++) {
                int cur_class = current == classes ? TH_DETECT_ASCII_DIGIT : current;
                int64_t score = th_detect_pair_score(data, cur_class, prev_class, row == TH_SB_WINDOWS_1256_ROW);
                th_sb_pairs[row][th_sb_pair_column(prev_class) * TH_SB_PAIR_WIDTH + th_sb_pair_column(cur_class)] =
                    (int16_t)score;
            }
        }
    }
    th_sb_bound_build();
    th_cjk_freq_build();
}

static TH_HOT int64_t th_sb_pair(const int16_t *pairs, uint32_t current, uint32_t previous) {
    return pairs[TH_SB_INFO_ROW(previous) + TH_SB_INFO_COLUMN(current)];
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
    const int16_t *pairs;
    const uint32_t *infos;
    th_sb_kind kind;
    int windows_1252;
    int koi8u;
    int ibm866;
    int alive;
    int64_t score;
    uint32_t prev; /* the previous byte's th_sb_infos word */
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
    cand->pairs = th_sb_pairs[data - th_detect_single_byte_data];
    cand->infos = th_sb_infos[data - th_detect_single_byte_data];
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
static TH_HOT void th_sb_ordinal(th_sb_candidate *cand, unsigned char byte, unsigned char caseless) {
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

/* The last space in buf[first, end), or -1. Scanned by hand because memrchr is missing on Windows. */
static Py_ssize_t th_sb_last_space(const unsigned char *buf, Py_ssize_t first, Py_ssize_t end) {
    for (Py_ssize_t index = end - 1; index >= first; index--) {
        if (buf[index] == ' ') {
            return index;
        }
    }
    return -1;
}

/* Reset a live candidate to the state its last space leaves it in, once the bytes before that space are skipped. No
   candidate scores an ASCII byte that follows an ASCII byte, so the skipped bytes move state and charge nothing:
   - Latin (windows-1250/1252/1254/1257, ISO-8859-2/4/13): pair scores, the adjacency penalty and the case-transition
     penalty all sit under !ascii_pair, and the run penalty is zero once the previous byte is ASCII.
   - windows-1256: pair and adjacency scores sit under !ascii_pair, and ASCII is never a non-Latin letter, so the word
     length is already closed by the byte before the run.
   - Cyrillic and Greek: the only ASCII charge is a case bonus or penalty, and after an ASCII byte the case state is
     MIX with an empty word or SPACE, both of which charge nothing.
   - Hebrew, logical and visual: the plausible-punctuation counts and pair scores sit under !ascii_pair.
   - windows-1252 ordinal machine: bonuses fire only in the EXPECTING_* and COPYRIGHT states, which only an 0xAA, 0xBA
     or 0xA9 byte enters, and every ASCII-driven state falls to SPACE on a space. */
static TH_HOT void th_sb_skip_to_space(th_sb_candidate *cand) {
    cand->case_state = TH_LAT_SPACE;
    cand->ordinal_state = TH_ORD_SPACE;
    cand->prev = cand->infos[' '];
    cand->prev_ascii = 1;
    cand->prev_non_ascii = 0;
    cand->prev_was_a0 = 0;
    cand->prev_punctuation = 0;
}

/* Where the walk next looks for a skip from `from` on, or len when it never will: the second byte of an ASCII run,
   since no candidate scores an ASCII byte after an ASCII byte. Only runs where two words at an 8-byte stride are all
   ASCII qualify, as a skip needs 16 bytes to be worth the space scan. Measuring the run waits until a live candidate
   gets there, so one that dies before it scans nothing. */
static Py_ssize_t th_sb_next_jump(const unsigned char *buf, Py_ssize_t from, Py_ssize_t len) {
    for (Py_ssize_t index = (from + 7) & ~(Py_ssize_t)7; index + 16 <= len; index += 8) {
        uint64_t low;
        uint64_t high;
        memcpy(&low, buf + index, sizeof(low));
        memcpy(&high, buf + index + 8, sizeof(high));
        if (((low | high) & UINT64_C(0x8080808080808080)) == 0) {
            Py_ssize_t start = index;
            while (start > from && buf[start - 1] < 0x80) {
                start--;
            }
            return start + 1;
        }
    }
    return len;
}

/* The walk a disqualified Hebrew logical candidate keeps from start on: it scores nothing, but still counts the ASCII
   punctuation after a Hebrew letter that the visual candidate's tiebreak needs, skipping bytes it cannot map. Kept out
   of th_sb_feed_loop so a live candidate does not test for this on every byte. */
static void th_sb_track_punctuation(th_sb_candidate *cand, const unsigned char *buf, Py_ssize_t start, Py_ssize_t len) {
    const uint32_t *infos = cand->infos;
    for (Py_ssize_t index = start; index < len; index++) {
        unsigned char byte = buf[index];
        uint32_t info = infos[byte];
        if (info & TH_SB_INFO_DEAD) {
            continue;
        }
        int ascii = byte < 0x80;
        if (!(cand->prev_ascii && ascii) && (info & TH_SB_INFO_CASELESS) == 0 && (cand->prev & TH_SB_INFO_NON_LATIN) &&
            th_detect_is_ascii_punct(byte)) {
            cand->plausible_punctuation++;
        }
        cand->prev_ascii = ascii;
        cand->prev = info;
    }
}

/* The Latin kinds' penalty for a byte after `run` non-ASCII bytes in a row. */
static TH_HOT int64_t th_sb_latin_run_penalty(unsigned run) {
    return run <= 2 ? 0 : run == 3 ? -5 : run == 4 ? -20 : -200;
}

/* The Latin kinds' case machine: an upper-case letter after a lower-case one, or a lower-case one after all caps,
   costs the transition penalty unless both bytes are ASCII. */
static TH_HOT void th_sb_latin_case(th_sb_candidate *cand, int latin_alpha, int upper, int ascii_pair) {
    if (!latin_alpha) {
        cand->case_state = TH_LAT_SPACE;
    } else if (!upper) {
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
}

/* Feed the whole input to one single-byte candidate, accumulating its score or
   disqualifying it on the first unmapped byte. Ports the per-kind feed methods. */
static TH_HOT void th_sb_feed_loop(th_sb_candidate *out, const unsigned char *buf, Py_ssize_t len,
                                   int track_logical_punctuation, int skip_runs, const int kind, const int western) {
    if (!out->alive) {
        th_sb_track_punctuation(out, buf, 0, len);
        return;
    }
    /* The walk runs on a copy the compiler keeps in registers: through the pointer, every one-byte state store could
       alias the table fields, so each byte would reload them and spill the state back. */
    th_sb_candidate local = *out;
    th_sb_candidate *cand = &local;
    const int16_t *pairs = cand->pairs;
    const uint32_t *infos = cand->infos;
    Py_ssize_t jump_from = skip_runs ? th_sb_next_jump(buf, 0, len) : len;
    Py_ssize_t index = 0;
    /* The walk runs up to where a skip may start and takes it there, so the per-byte loop only tests its own bound. */
    for (;;) {
        for (; index < jump_from; index++) {
            unsigned char byte = buf[index];
            uint32_t info = infos[byte];
            if (info & TH_SB_INFO_DEAD) {
                goto dead;
            }
            unsigned char caseless = (unsigned char)(info & TH_SB_INFO_CASELESS);
            unsigned char prev = (unsigned char)(cand->prev & TH_SB_INFO_CASELESS);
            int upper = (info & TH_SB_INFO_UPPER) != 0;
            int ascii = byte < 0x80;
            int non_latin = (info & TH_SB_INFO_NON_LATIN) != 0;

            if (kind == TH_SB_LATIN || kind == TH_SB_ARABIC_FRENCH) {
                int ascii_pair =
                    kind == TH_SB_LATIN ? (cand->prev_non_ascii == 0 && ascii) : (cand->prev_ascii && ascii);
                if (kind == TH_SB_LATIN) {
                    cand->score += th_sb_latin_run_penalty(cand->prev_non_ascii);
                }
                th_sb_latin_case(
                    cand, kind == TH_SB_LATIN ? (info & TH_SB_INFO_ALPHA) != 0 : (caseless == TH_DETECT_LATIN_LETTER),
                    upper, ascii_pair);
                if (kind == TH_SB_ARABIC_FRENCH) {
                    if (non_latin) {
                        cand->current_word_len++;
                    } else {
                        if (cand->current_word_len > cand->longest_word) {
                            cand->longest_word = cand->current_word_len;
                        }
                        cand->current_word_len = 0;
                    }
                    if (!ascii_pair) {
                        cand->score += th_sb_pair(pairs, info, cand->prev);
                        if (prev == TH_DETECT_LATIN_LETTER && non_latin) {
                            cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                        } else if (caseless == TH_DETECT_LATIN_LETTER && (cand->prev & TH_SB_INFO_NON_LATIN)) {
                            cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                        }
                    }
                } else {
                    int ascii_ish = ascii_pair || (ascii && prev == 0) || (caseless == 0 && cand->prev_non_ascii == 0);
                    if (!ascii_ish) {
                        cand->score += th_sb_pair(pairs, info, cand->prev);
                    }
                    if (western) {
                        th_sb_ordinal(cand, byte, caseless);
                    }
                }
                if (kind == TH_SB_LATIN) {
                    cand->prev_non_ascii = ascii ? 0 : cand->prev_non_ascii + 1;
                } else {
                    cand->prev_ascii = ascii;
                }
                cand->prev = info;
                if (kind == TH_SB_LATIN && !ascii) {
                    /* Until the next ASCII byte no pair reads as ASCII, so the walk through the rest of a non-ASCII run
                       always scores the pair and drops those tests. */
                    while (index + 1 < jump_from && buf[index + 1] >= 0x80) {
                        index++;
                        byte = buf[index];
                        info = infos[byte];
                        if (info & TH_SB_INFO_DEAD) {
                            goto dead;
                        }
                        cand->score +=
                            th_sb_latin_run_penalty(cand->prev_non_ascii) + th_sb_pair(pairs, info, cand->prev);
                        th_sb_latin_case(cand, (info & TH_SB_INFO_ALPHA) != 0, (info & TH_SB_INFO_UPPER) != 0, 0);
                        if (western) {
                            th_sb_ordinal(cand, byte, (unsigned char)(info & TH_SB_INFO_CASELESS));
                        }
                        cand->prev_non_ascii++;
                        cand->prev = info;
                    }
                }
                continue;
            }

            /* The caseless/Hebrew/non-Latin-cased kinds share the word-length + adjacency
               core; the case machine and punctuation bookkeeping differ. */
            int ascii_pair = cand->prev_ascii && ascii;
            if (kind == TH_SB_NON_LATIN_CASED) {
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
                } else if (!upper) {
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
                int skip_ibm866 = cand->ibm866 &&
                                  ((is_a0 && (cand->prev_was_a0 || prev == 0)) || (caseless == 0 && cand->prev_was_a0));
                if (!skip_ibm866) {
                    cand->score += th_sb_pair(pairs, info, cand->prev);
                }
                int prev_non_latin = (cand->prev & TH_SB_INFO_NON_LATIN) != 0;
                if (kind == TH_SB_LOGICAL && caseless == 0 && prev_non_latin && th_detect_is_ascii_punct(byte)) {
                    cand->plausible_punctuation++;
                } else if (kind == TH_SB_VISUAL && non_latin && cand->prev_punctuation) {
                    cand->plausible_punctuation++;
                }
                if (prev == TH_DETECT_LATIN_LETTER && non_latin) {
                    cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                } else if (caseless == TH_DETECT_LATIN_LETTER && prev_non_latin) {
                    cand->score += TH_DETECT_LATIN_ADJACENCY_PENALTY;
                }
            }
            cand->prev_ascii = ascii;
            cand->prev = info;
            /* only ibm866 reads the no-break-space flag and only the visual candidate the punctuation one */
            if (kind == TH_SB_NON_LATIN_CASED) {
                cand->prev_was_a0 = is_a0;
            }
            if (kind == TH_SB_VISUAL) {
                cand->prev_punctuation = caseless == 0 && th_detect_is_ascii_punct(byte);
            }
        }
        if (jump_from == len) {
            *out = local;
            return;
        }
        Py_ssize_t run_end = index + th_decode_ascii_run(buf + index, len - index);
        Py_ssize_t space = run_end - index >= 16 ? th_sb_last_space(buf, index, run_end) : -1;
        if (space >= 0) {
            th_sb_skip_to_space(cand);
            index = space + 1;
        }
        jump_from = th_sb_next_jump(buf, run_end, len);
    }
dead:
    cand->alive = 0;
    *out = local;
    if (track_logical_punctuation) {
        th_sb_track_punctuation(out, buf, index + 1, len);
    }
}

/* Whether buf has an ASCII run long enough for the run skip to pay off: two adjacent all-ASCII words at an 8-byte
   stride, which every run of 24 bytes holds. The answer is the same for every candidate, so the caller asks once; a
   feed without such a run, short or mostly non-ASCII like Cyrillic or Greek text, then takes the loop without the
   per-byte skip check, which costs every candidate on every byte. Missing a shorter run only forgoes a skip. */
static int th_sb_skippable(const unsigned char *buf, Py_ssize_t len) {
    if (len < 64) {
        return 0;
    }
    int previous = 0;
    for (Py_ssize_t index = 0; index + 8 <= len; index += 8) {
        uint64_t word;
        memcpy(&word, buf + index, sizeof(word));
        int ascii = (word & UINT64_C(0x8080808080808080)) == 0;
        if (previous && ascii) {
            return 1;
        }
        previous = ascii;
    }
    return 0;
}

/* Each kind gets its own copy of the loop with the kind constant, so a candidate's walk carries none of the other
   kinds' branches; a feed th_sb_skippable rules out does not look for skips at all. */
static void th_sb_feed(th_sb_candidate *cand, const unsigned char *buf, Py_ssize_t len, int track_logical_punctuation,
                       int skippable) {
    /* A viable visual candidate still needs punctuation from a disqualified logical candidate. */
    if (!cand->alive && !track_logical_punctuation) {
        return;
    }
    switch (cand->kind) {
    case TH_SB_LATIN:
        if (cand->windows_1252) {
            th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_LATIN, 1);
        } else {
            th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_LATIN, 0);
        }
        break;
    case TH_SB_NON_LATIN_CASED:
        th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_NON_LATIN_CASED, 0);
        break;
    case TH_SB_ARABIC_FRENCH:
        th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_ARABIC_FRENCH, 0);
        break;
    case TH_SB_CASELESS:
        th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_CASELESS, 0);
        break;
    case TH_SB_LOGICAL:
        th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_LOGICAL, 0);
        break;
    default:
        th_sb_feed_loop(cand, buf, len, track_logical_punctuation, skippable, TH_SB_VISUAL, 0);
        break;
    }
}
/* NOLINTEND(bugprone-branch-clone) */

/* chardetng feeds one space to every single-byte candidate once the stream ends, so a word or an ordinal or a
   copyright sign that runs up against EOF still reaches the word-boundary arms of its scorer. Without it a Cyrillic
   word loses its capitalization bonus and windows-1251 loses to GBK on the bare word "Привет". */
static void th_sb_feed_eof(th_sb_candidate *cand, int track_logical_punctuation) {
    th_sb_feed(cand, (const unsigned char *)" ", 1, track_logical_punctuation, 0);
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

static void th_sb_bound_build(void) {
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        int row = th_sb_candidate_table[slot].data_index;
        if (th_sb_candidate_table[slot].kind == TH_SB_LATIN) {
            int most[TH_SB_PAIR_WIDTH] = {0};
            for (int previous = 0; previous < TH_SB_PAIR_WIDTH; previous++) {
                for (int current = 0; current < TH_SB_PAIR_WIDTH; current++) {
                    int score = th_sb_pairs[row][previous * TH_SB_PAIR_WIDTH + current];
                    most[current] = score > most[current] ? score : most[current];
                }
            }
            /* windows-1252's ordinal or copyright bonus can fire on a space-like byte or a digit, once a byte */
            for (int byte = 0; byte < 256; byte++) {
                uint32_t info = th_sb_infos[row][byte];
                unsigned caseless = info & TH_SB_INFO_CASELESS;
                int bonus = row == TH_DETECT_WINDOWS_1252_INDEX && (caseless == 0 || caseless == TH_DETECT_ASCII_DIGIT)
                                ? TH_DETECT_ORDINAL_BONUS - TH_DETECT_IMPLAUSIBILITY_PENALTY
                                : 0;
                th_sb_gain[row][byte] = (uint16_t)(bonus + most[TH_SB_INFO_COLUMN(info)]);
            }
            continue;
        }
        /* What each index stands for: its column, whether its bytes are of the script, whether an ASCII byte maps
           to it, and for an index before, whether its bytes are lower-case letters of the script. A column's bytes
           share a class, so all but the ASCII flag hold for every byte an index covers. */
        int cased = th_sb_candidate_table[slot].kind == TH_SB_NON_LATIN_CASED;
        int ibm866 = strcmp(th_detect_single_byte_data[row].label, "ibm866") == 0;
        struct {
            int column;
            unsigned char non_latin, ascii, lower, used;
        } earlier[TH_SB_BOUND_BEFORE] = {0}, later[TH_SB_BOUND_AFTER] = {0};
        for (int byte = 0; byte < 256; byte++) {
            uint32_t info = th_sb_infos[row][byte];
            int column = (int)TH_SB_INFO_COLUMN(info);
            int script = (info & TH_SB_INFO_NON_LATIN) != 0;
            int nbsp = ibm866 && byte == 0xA0;
            int lower = cased && script && !(info & TH_SB_INFO_UPPER);
            int before = nbsp ? 2 * TH_SB_PAIR_WIDTH : lower ? TH_SB_PAIR_WIDTH + column : column;
            int after = nbsp ? TH_SB_PAIR_WIDTH : column;
            th_sb_bound_before[row][byte] = th_sb_bound_pairs[row] + (ptrdiff_t)before * TH_SB_BOUND_AFTER;
            th_sb_bound_after[row][byte] = (uint8_t)after;
            earlier[before].column = later[after].column = column;
            earlier[before].non_latin |= script;
            later[after].non_latin |= script;
            earlier[before].ascii |= byte < 0x80;
            later[after].ascii |= byte < 0x80;
            earlier[before].lower = lower;
            earlier[before].used = later[after].used = 1;
        }
        for (int before = 0; before < TH_SB_BOUND_BEFORE; before++) {
            for (int after = 0; after < TH_SB_BOUND_AFTER; after++) {
                /* only the index pairs some two bytes reach are ever read */
                if (!earlier[before].used || !later[after].used) {
                    continue;
                }
                int previous = earlier[before].column;
                int current = later[after].column;
                int nbsp_before = before == 2 * TH_SB_PAIR_WIDTH;
                int nbsp_after = after == TH_SB_PAIR_WIDTH;
                /* the walk's skips, its pair and its adjacency penalty, then the bonus a capitalized word ends on */
                int skip = (nbsp_after && (nbsp_before || previous == 0)) || (current == 0 && nbsp_before);
                int adjacent = (previous == TH_DETECT_LATIN_LETTER && later[after].non_latin) ||
                               (current == TH_DETECT_LATIN_LETTER && earlier[before].non_latin);
                int score = (skip ? 0 : th_sb_pairs[row][previous * TH_SB_PAIR_WIDTH + current]) +
                            (adjacent ? TH_DETECT_LATIN_ADJACENCY_PENALTY : 0) +
                            (earlier[before].lower && !later[after].non_latin && current != TH_DETECT_LATIN_LETTER
                                 ? TH_DETECT_NON_LATIN_CAPITALIZATION_BONUS
                                 : 0);
                /* the walk scores nothing between two ASCII bytes, so where both indexes could be those, the bound
                   is at least zero and needs no test of the bytes themselves. The tables class ASCII as space, Latin
                   letter or digit, which pair to zero, so the floor never lifts a score; it keeps the bound sound
                   for a table that would not. */
                int both_ascii = earlier[before].ascii & later[after].ascii;
                th_sb_bound_pairs[row][before * TH_SB_BOUND_AFTER + after] =
                    (int16_t)(score < 0 && both_ascii ? 0 : score); /* GCOVR_EXCL_BR_LINE: see above */
            }
        }
    }
}
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
   scalar appears in the 128-entry most-frequent table for its script. The tables are
   read through open-addressed copies th_detect_tables_build fills, keyed by scalar, so a
   lookup probes a slot or two rather than scanning all 128. */
enum { TH_FREQ_SIMPLIFIED, TH_FREQ_KANJI, TH_FREQ_HANGUL };
#define TH_FREQ_SLOTS 1024
static struct {
    uint16_t scalar; /* 0 for an empty slot: no table holds U+0000 */
    uint8_t score;
} th_cjk_freq[3][TH_FREQ_SLOTS];

static TH_HOT unsigned th_cjk_freq_slot(uint16_t u) {
    return ((uint32_t)u * UINT32_C(40503) >> 6) & (TH_FREQ_SLOTS - 1);
}

/* A two-byte Shift_JIS or gb18030 sequence's table pointer as the sum of a per-lead and a per-trail term, each
   TH_CJK_NO_POINTER for a byte that cannot take that place, which keeps the sum negative. */
#define TH_CJK_NO_POINTER INT16_MIN
static int16_t th_cjk_shift_jis_lead[256];
static int16_t th_cjk_shift_jis_trail[256];
static int16_t th_cjk_gbk_lead[256];
static int16_t th_cjk_gbk_trail[256];

static void th_cjk_freq_build(void) {
    for (int byte = 0; byte < 256; byte++) {
        int sjis_lead = (byte >= 0x81 && byte <= 0x9F) || (byte >= 0xE0 && byte <= 0xFC);
        int sjis_trail = (byte >= 0x40 && byte <= 0x7E) || (byte >= 0x80 && byte <= 0xFC);
        int gbk_trail = byte >= 0x40 && byte <= 0xFE && byte != 0x7F;
        th_cjk_shift_jis_lead[byte] =
            (int16_t)(sjis_lead ? (byte - (byte < 0xA0 ? 0x81 : 0xC1)) * 188 : TH_CJK_NO_POINTER);
        th_cjk_shift_jis_trail[byte] = (int16_t)(sjis_trail ? byte - (byte < 0x7F ? 0x40 : 0x41) : TH_CJK_NO_POINTER);
        th_cjk_gbk_lead[byte] = (int16_t)(byte >= 0x81 && byte <= 0xFE ? (byte - 0x81) * 190 : TH_CJK_NO_POINTER);
        th_cjk_gbk_trail[byte] = (int16_t)(gbk_trail ? byte - (byte < 0x7F ? 0x40 : 0x41) : TH_CJK_NO_POINTER);
    }
    static const uint16_t *const tables[3] = {th_detect_freq_frequent_simplified, th_detect_freq_frequent_kanji,
                                              th_detect_freq_frequent_hangul};
    for (int which = 0; which < 3; which++) {
        for (int pos = 0; pos < 128; pos++) {
            uint16_t u = tables[which][pos];
            unsigned slot = th_cjk_freq_slot(u);
            while (th_cjk_freq[which][slot].scalar != 0) { /* no table repeats a scalar */
                slot = (slot + 1) & (TH_FREQ_SLOTS - 1);
            }
            th_cjk_freq[which][slot].scalar = u;
            th_cjk_freq[which][slot].score = (uint8_t)((128 - pos) / 16);
        }
    }
}

static TH_HOT int64_t th_cjk_extra_score(uint16_t u, int which) {
    for (unsigned slot = th_cjk_freq_slot(u);; slot = (slot + 1) & (TH_FREQ_SLOTS - 1)) {
        if (th_cjk_freq[which][slot].scalar == u) {
            return th_cjk_freq[which][slot].score;
        }
        if (th_cjk_freq[which][slot].scalar == 0) {
            return 0;
        }
    }
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

static TH_HOT void th_cjk_flush_pending(th_cjk_candidate *cand) {
    if (cand->has_pending) {
        cand->score += cand->pending;
        cand->has_pending = 0;
    }
}

/* Count s now if the previous scalar was CJ or the lead is unproblematic, else hold
   it as pending until the next scalar decides whether the run is plausible. */
static TH_HOT int64_t th_cjk_maybe_pending(th_cjk_candidate *cand, int64_t score, int problematic) {
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

static TH_HOT void th_cjk_score_gbk(th_cjk_candidate *cand, int written, uint16_t u, unsigned char b) {
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
    /* the arms test disjoint ranges, the common ideographs first */
    if (u >= 0x4E00 && u <= 0x9FA5) {
        th_cjk_flush_pending(cand);
        /* a valid gb18030 trail byte never exceeds 0xFE, and a two-byte lead never
           exceeds 0xFE, so chardetng's upper bounds collapse to the lower ones */
        if (b >= 0xA1) {
            if (cand->prev_byte >= 0xA1 && cand->prev_byte <= 0xD7) {
                cand->score += TH_GBK_LEVEL_1 + th_cjk_extra_score(u, TH_FREQ_SIMPLIFIED);
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
    } else if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
    } else if (u == 0x20AC) {
        cand->has_pending = 0;
        cand->prev = TH_CJ_OTHER;
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

static TH_HOT void th_cjk_score_shift_jis(th_cjk_candidate *cand, uint16_t u, unsigned char b) {
    int hwk_prev = cand->hwk_state;
    cand->hwk_state = TH_HWK_FORBIDDEN;
    /* the arms test disjoint ranges, the common kanji and kana first */
    if ((u >= 0x3400 && u < 0xA000) || (u >= 0xF900 && u < 0xFB00)) {
        th_cjk_flush_pending(cand);
        int64_t score = (cand->prev_byte < 0x98 || (cand->prev_byte == 0x98 && b < 0x73))
                            ? TH_SJIS_LEVEL_1 + th_cjk_extra_score(u, TH_FREQ_KANJI)
                            : TH_SJIS_LEVEL_2;
        cand->score += th_cjk_maybe_pending(cand, score, th_cjk_problematic_lead(cand->prev_byte));
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
    } else if ((u >= 'a' && u <= 'z') || (u >= 'A' && u <= 'Z')) {
        cand->has_pending = 0;
        if (cand->prev == TH_CJ_CJ) {
            cand->score += TH_CJK_LATIN_ADJ;
        }
        cand->prev = TH_CJ_ASCII;
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
            cand->score += TH_EUCJP_LEVEL_1 + th_cjk_extra_score(u, TH_FREQ_KANJI);
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
            cand->score += TH_EUCKR_EUC_HANGUL + th_cjk_extra_score(u, TH_FREQ_HANGUL);
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
            cand->dec.pos = cand->dec.error_at + 1;
            return 1;
        }
        if (malformed_len == 1 && b == 0xFF) {
            /* the decoder pushed the rest of the sequence back along with this 0xFF; chardetng resets its decoder
               here, so those bytes are dropped rather than read again */
            cand->has_pending = 0;
            cand->score += TH_GBK_SINGLE_BYTE_EXTENSION_PENALTY;
            cand->prev = TH_CJ_OTHER;
            cand->dec.pos = cand->dec.error_at + 1;
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
            cand->dec.pos = cand->dec.error_at + 1;
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

/* Jump to the last byte of an ASCII run the scorers would otherwise walk one byte at a time. Every decoder step returns
   with its pending bytes consumed or cleared, so the decoder is idle here; the run qualifies when the previous scalar
   was ASCII or other, since nothing in the run can change that. The scorers charge for ASCII only after a CJ scalar,
   and the last byte still runs through the scorer, so its resets (word_len, hwk_state, the cleared pending mark)
   cover the whole run. Per kind:
   - GBK and Big5 charge the Latin adjacency penalty only after a CJ scalar; a held scalar is left to the scorer.
   - Shift_JIS and EUC-JP clear hwk_state on every scalar, so the last byte's clear is the one that matters.
   - EUC-KR zeroes word_len on every ASCII scalar, so the last byte's zero is the one that matters.
   The skipped bytes decode to themselves in every candidate decoder (decode.h's ASCII copy relies on the same fact). */
static void th_cjk_skip_ascii_run(th_cjk_candidate *cand, const unsigned char *data, Py_ssize_t data_len) {
    Py_ssize_t here = cand->dec.pos;
    if (here >= data_len || data[here] >= 0x80 || cand->has_pending ||
        (cand->prev != TH_CJ_ASCII && cand->prev != TH_CJ_OTHER)) {
        return;
    }
    Py_ssize_t run = th_decode_ascii_run(data + here, data_len - here);
    cand->dec.pos = here + run - 1;
}

/* The common Shift_JIS and gb18030 scalars decoded and scored in place, from `pos` on, until a byte this does not
   cover: a two-byte pair the decoder's tables map, and for Shift_JIS a half-width katakana byte. It mirrors
   th_dec_shift_jis and th_dec_gb18030 with the decoder idle between scalars, and stops for anything else, which
   th_decode_step then reads byte by byte, so ASCII, errors, pushback and the four-byte gb18030 form keep their one
   implementation. It starts two bytes in, so the scorers' look-back stays inside data. Returns where it stopped. */
static TH_HOT Py_ssize_t th_cjk_fast_run(th_cjk_candidate *cand, const unsigned char *data, Py_ssize_t data_len,
                                         Py_ssize_t pos, const th_cjk_kind kind) {
    if (pos < 2) {
        return pos;
    }
    while (pos + 1 < data_len) {
        unsigned char lead = data[pos];
        unsigned char trail = data[pos + 1];
        uint16_t unit;
        Py_ssize_t at = pos + 1;
        if (kind == TH_CJK_SHIFT_JIS) {
            if ((unsigned)(lead - 0xA1) <= 0xDF - 0xA1) {
                unit = (uint16_t)(0xFF61 - 0xA1 + lead);
                at = pos;
            } else {
                int pointer = th_cjk_shift_jis_lead[lead] + th_cjk_shift_jis_trail[trail];
                if (pointer < 0) {
                    break;
                }
                if (pointer >= 8836 && pointer <= 10715) {
                    unit = (uint16_t)(0xE000 - 8836 + pointer);
                } else if (th_jis0208[pointer] != 0) {
                    unit = th_jis0208[pointer];
                } else {
                    break;
                }
            }
        } else {
            int pointer = th_cjk_gbk_lead[lead] + th_cjk_gbk_trail[trail];
            if (pointer < 0) {
                break;
            }
            unit = th_gb18030[pointer];
        }
        cand->prev_byte = data[at - 1];
        cand->prev_prev_byte = data[at - 2];
        if (kind == TH_CJK_SHIFT_JIS) {
            th_cjk_score_shift_jis(cand, unit, data[at]);
        } else {
            th_cjk_score_gbk(cand, 1, unit, data[at]);
        }
        pos = at + 1;
    }
    return pos;
}

/* The decode-and-score loop, specialized per candidate kind so each copy drops the other kinds' tests. */
static TH_HOT void th_cjk_walk(th_cjk_candidate *cand, const unsigned char *data, Py_ssize_t data_len, int final,
                               const th_cjk_kind kind) {
    for (;;) {
        if (kind == TH_CJK_SHIFT_JIS || kind == TH_CJK_GBK) {
            cand->dec.pos = th_cjk_fast_run(cand, data, data_len, cand->dec.pos, kind);
        }
        th_cjk_skip_ascii_run(cand, data, data_len);
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
        int combining = kind == TH_CJK_BIG5 && cand->dec.has_pending;
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
        switch (kind) {
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
            PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation-failure path */
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
    switch (cand->kind) {
    case TH_CJK_GBK:
        th_cjk_walk(cand, data, data_len, final, TH_CJK_GBK);
        break;
    case TH_CJK_SHIFT_JIS:
        th_cjk_walk(cand, data, data_len, final, TH_CJK_SHIFT_JIS);
        break;
    case TH_CJK_EUC_JP:
        th_cjk_walk(cand, data, data_len, final, TH_CJK_EUC_JP);
        break;
    case TH_CJK_BIG5:
        th_cjk_walk(cand, data, data_len, final, TH_CJK_BIG5);
        break;
    default:
        th_cjk_walk(cand, data, data_len, final, TH_CJK_EUC_KR);
        break;
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
        /* the decoder starts at the escape: nothing before it changes its state, and a stream that never has one
           never needs the decoder */
        th_decode_init(&scan->dec, th_encoding_lookup("iso-2022-jp", 11), NULL, 0);
    }
    buf += start;
    len -= start;
    const unsigned char *data = buf;
    Py_ssize_t data_len = len;
    unsigned char *spliced = NULL;
    if (scan->carry_len > 0) {
        spliced = PyMem_Malloc((size_t)scan->carry_len + (size_t)len + 1);
        if (spliced == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation-failure path */
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
        th_decoder rewind = scan->dec;
        Py_ssize_t before = scan->dec.pos;
        Py_UCS4 point = 0;
        int status = th_decode_step(&scan->dec, &point);
        if (status == TH_DEC_FINISHED) {
            break;
        }
        if (status == TH_DEC_ERROR) {
            if (scan->dec.error_at < 0 && !final) {
                /* An incomplete escape changes the mode; replay its bytes from the earlier state. */
                scan->dec = rewind;
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

/* Every candidate chardetng scores, fed byte by byte and read once at the end. th_encoding_detect drives them with a
   single feed and the stream with one per chunk, so the differential test against chardetng covers the same scoring
   the chunked path uses. */
typedef struct {
    th_cjk_candidate cjk[5];
    th_sb_candidate sb[TH_SB_COUNT];
    th_sb_candidate visual;
} th_detect_candidates;

/* A stream's candidates are allocated at its first non-ASCII byte: a stream that never has one, ASCII or settled by a
   byte-order mark, is answered without them, so it neither allocates nor initializes them. */
typedef struct {
    th_utf8_scan utf8;
    th_jp_scan jp;
    th_detect_candidates *candidates;
    int started;
    unsigned char tail[2];
    int tail_len;
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

static void th_detect_candidates_init(th_detect_candidates *candidates) {
    th_detect_tables_ensure();
    for (int slot = 0; slot < 5; slot++) {
        th_cjk_init(&candidates->cjk[slot], th_cjk_slots[slot].kind, th_cjk_slots[slot].label);
    }
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_init(&candidates->sb[slot], &th_detect_single_byte_data[th_sb_candidate_table[slot].data_index],
                   th_sb_candidate_table[slot].kind);
    }
    th_sb_init(&candidates->visual, &th_detect_single_byte_data[TH_DETECT_ISO_8859_8_ROW], TH_SB_VISUAL);
}

static void th_detect_stream_init(th_detect_stream *stream) {
    th_utf8_scan_init(&stream->utf8);
    th_jp_scan_init(&stream->jp);
    stream->candidates = NULL;
    stream->started = 0;
    stream->tail[0] = 0;
    stream->tail[1] = 0;
    stream->tail_len = 0;
}

static void th_detect_stream_clear(th_detect_stream *stream) {
    PyMem_Free(stream->candidates);
    stream->candidates = NULL;
}

/* The two candidate arrays laid end to end under chardetng's indices, so one number addresses any
   candidate the TLD tables name. Index 3..8 is a CJK slot; 8..27 a single-byte one. */
static int th_detect_alive(const th_detect_candidates *stream, int index) {
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
static int th_detect_expectation(const th_detect_candidates *stream, th_tld *tld) {
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
static TH_HOT void th_detect_candidates_feed(th_detect_candidates *stream, const unsigned char *buf, Py_ssize_t len,
                                             int final) {
    for (int slot = 0; slot < 5; slot++) {
        th_cjk_feed(&stream->cjk[slot], buf, len, final);
    }
    int skippable = th_sb_skippable(buf, len);
    th_sb_feed(&stream->visual, buf, len, 0, skippable);
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_feed(&stream->sb[slot], buf, len, slot == TH_SB_LOGICAL_SLOT && stream->visual.alive, skippable);
    }
    if (final) {
        for (int slot = 0; slot < TH_SB_COUNT; slot++) {
            th_sb_feed_eof(&stream->sb[slot], slot == TH_SB_LOGICAL_SLOT && stream->visual.alive);
        }
        th_sb_feed_eof(&stream->visual, 0);
    }
}

#define TH_SB_BOUND_BLOCK 256

/* Fill row[block] for every block from `first` on with the most the bytes from there to the end, and the end-of-stream
   space, can add to the candidate's score. A Latin candidate sums th_sb_gain here. For the other scripts, which
   th_sb_bound_rows_group sums, the walk's pair score, Latin adjacency penalty, and ibm866's skip around a no-break
   space hang only on the byte and the one before it, since the walk's previous-byte state is always that byte (a
   skipped ASCII run resumes after a space, which is that byte); th_sb_bound_pairs holds them per pair of bytes. The
   walk's other terms hang on state: the case penalties only subtract, and the capitalized-word bonus is counted
   wherever it could fire, on a byte outside the script and the Latin letters after a lower-case letter of the script.
 */
static void th_sb_bound_rows(const th_sb_candidate *cand, const unsigned char *buf, Py_ssize_t len, int64_t *row,
                             Py_ssize_t first, Py_ssize_t blocks) {
    const uint16_t *gain = th_sb_gain[cand->data - th_detect_single_byte_data];
    row[blocks] = gain[' '];
    for (Py_ssize_t block = blocks - 1; block >= first; block--) {
        Py_ssize_t from = block * TH_SB_BOUND_BLOCK;
        Py_ssize_t to = from + TH_SB_BOUND_BLOCK < len ? from + TH_SB_BOUND_BLOCK : len;
        int64_t sum = 0;
        for (Py_ssize_t index = from; index < to; index++) {
            sum += gain[buf[index]];
        }
        row[block] = row[block + 1] + sum;
    }
}

/* The non-Latin rows of one or two candidates in one pass, `count` a constant once inlined, so each candidate's tables
   and running sum stay in registers; more than two would spill them. */
static TH_HOT void th_sb_bound_rows_group(const th_sb_candidate *const *cands, int64_t *const *rows, const int count,
                                          const unsigned char *buf, Py_ssize_t len, Py_ssize_t first,
                                          Py_ssize_t blocks) {
    const int16_t *const *before[2];
    const uint8_t *after[2];
    const int16_t *previous[2];
    for (int which = 0; which < count; which++) {
        Py_ssize_t table = cands[which]->data - th_detect_single_byte_data;
        before[which] = th_sb_bound_before[table];
        after[which] = th_sb_bound_after[table];
        rows[which][blocks] = before[which][buf[len - 1]][after[which][' ']];
    }
    for (Py_ssize_t block = blocks - 1; block >= first; block--) {
        Py_ssize_t from = block * TH_SB_BOUND_BLOCK;
        Py_ssize_t to = from + TH_SB_BOUND_BLOCK < len ? from + TH_SB_BOUND_BLOCK : len;
        int64_t sum[2] = {0, 0};
        for (int which = 0; which < count; which++) {
            previous[which] = before[which][buf[from - 1]];
        }
        for (Py_ssize_t index = from; index < to; index++) {
            unsigned char byte = buf[index];
            for (int which = 0; which < count; which++) {
                sum[which] += previous[which][after[which][byte]];
                previous[which] = before[which][byte];
            }
        }
        for (int which = 0; which < count; which++) {
            rows[which][block] = rows[which][block + 1] + sum[which];
        }
    }
}

static void th_sb_bound_rows_non_latin(const th_sb_candidate *const *cands, int64_t *const *rows, int count,
                                       const unsigned char *buf, Py_ssize_t len, Py_ssize_t first, Py_ssize_t blocks) {
    for (; count >= 2; count -= 2, cands += 2, rows += 2) {
        th_sb_bound_rows_group(cands, rows, 2, buf, len, first, blocks);
    }
    if (count == 1) {
        th_sb_bound_rows_group(cands, rows, 1, buf, len, first, blocks);
    }
}

/* The one-shot feed for a caller that reads only the winner and its confidence, the share of the positive scores.
   The single-byte candidates read the input a block at a time, which answers as one feed does, as the streaming
   detector shows. The first time a candidate ends a block below zero, it gets a bound on what each later block and
   the end of the input could add, one pass over the rest; from then on it stops at the first block after which the
   two cannot end positive, since it could then neither win nor count toward the share. A candidate at or above zero
   is left alone: its bound would come out positive on most text and cost a pass for nothing. The Hebrew logical
   candidate stays, since the visual one's tiebreak reads its punctuation, and a hinted TLD keeps them all, since it
   lifts its own encoding's score by one. */
static void th_detect_candidates_feed_bounded(th_detect_candidates *candidates, const unsigned char *buf,
                                              Py_ssize_t len) {
    for (int slot = 0; slot < 5; slot++) {
        th_cjk_feed(&candidates->cjk[slot], buf, len, 1);
    }
    Py_ssize_t blocks = (len + TH_SB_BOUND_BLOCK - 1) / TH_SB_BOUND_BLOCK;
    /* rest[slot * (blocks + 1) + block]: the bound from that block on, for a slot whose bound is ready */
    int64_t *rest = NULL;
    unsigned char ready[TH_SB_COUNT] = {0};
    /* the input is never empty, so the last block ends the loop */
    for (Py_ssize_t block = 0;; block++) {
        Py_ssize_t from = block * TH_SB_BOUND_BLOCK;
        Py_ssize_t count = from + TH_SB_BOUND_BLOCK < len ? TH_SB_BOUND_BLOCK : len - from;
        int skippable = th_sb_skippable(buf + from, count);
        th_sb_feed(&candidates->visual, buf + from, count, 0, skippable);
        for (int slot = 0; slot < TH_SB_COUNT; slot++) {
            th_sb_feed(&candidates->sb[slot], buf + from, count, slot == TH_SB_LOGICAL_SLOT && candidates->visual.alive,
                       skippable);
        }
        if (from + count == len) {
            break;
        }
        /* the candidates that first end a block below zero get their bounds, the non-Latin ones in one pass */
        const th_sb_candidate *pending[TH_SB_COUNT];
        int64_t *pending_rows[TH_SB_COUNT];
        int pending_count = 0;
        for (int slot = 0; slot < TH_SB_COUNT; slot++) {
            th_sb_candidate *cand = &candidates->sb[slot];
            if (!cand->alive || slot == TH_SB_LOGICAL_SLOT || ready[slot] || cand->score >= 0) {
                continue;
            }
            if (rest == NULL) {
                rest = PyMem_Malloc((size_t)(TH_SB_COUNT * (blocks + 1)) * sizeof(int64_t));
                if (rest == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    break;          /* GCOVR_EXCL_LINE: allocation-failure path, which only forgoes the stop */
                }
            }
            int64_t *row = rest + (Py_ssize_t)slot * (blocks + 1);
            ready[slot] = 1;
            if (cand->kind == TH_SB_LATIN) {
                th_sb_bound_rows(cand, buf, len, row, block + 1, blocks);
            } else {
                pending[pending_count] = cand;
                pending_rows[pending_count++] = row;
            }
        }
        th_sb_bound_rows_non_latin(pending, pending_rows, pending_count, buf, len, block + 1, blocks);
        for (int slot = 0; slot < TH_SB_COUNT; slot++) {
            th_sb_candidate *cand = &candidates->sb[slot];
            if (ready[slot] && cand->alive && cand->score + rest[(Py_ssize_t)slot * (blocks + 1) + block + 1] <= 0) {
                cand->alive = 0;
            }
        }
    }
    PyMem_Free(rest);
    for (int slot = 0; slot < TH_SB_COUNT; slot++) {
        th_sb_feed_eof(&candidates->sb[slot], slot == TH_SB_LOGICAL_SLOT && candidates->visual.alive);
    }
    th_sb_feed_eof(&candidates->visual, 0);
}

/* The offset of the first non-ASCII or ESC byte, or len. chardetng's feed starts the candidates two bytes before it,
   so the single-byte and CJK candidates never score the ASCII before that point; the stream and the one-shot path
   both match that. The ISO-2022-JP scan still sees the whole prefix. */
static Py_ssize_t th_detect_first_event(const unsigned char *buf, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index + (Py_ssize_t)sizeof(uint64_t) <= len) {
        uint64_t word;
        memcpy(&word, buf + index, sizeof(word));
        /* a byte of ESC is a zero byte of escapes, which borrowing 1 from every byte flags in its high bit */
        uint64_t escapes = word ^ UINT64_C(0x1B1B1B1B1B1B1B1B);
        if ((word | ((escapes - UINT64_C(0x0101010101010101)) & ~escapes)) & UINT64_C(0x8080808080808080)) {
            break;
        }
        index += (Py_ssize_t)sizeof(uint64_t);
    }
    while (index < len && buf[index] < 0x80 && buf[index] != 0x1B) {
        index++;
    }
    return index;
}

/* Keep the last two bytes of an all-ASCII chunk, the context a first event at offset 0 or 1 of a later chunk reads. */
static void th_detect_stream_keep_tail(th_detect_stream *stream, const unsigned char *buf, Py_ssize_t len) {
    if (len >= 2) {
        memcpy(stream->tail, buf + len - 2, 2);
        stream->tail_len = 2;
    } else if (len == 1) {
        stream->tail[0] = stream->tail[1];
        stream->tail[1] = buf[0];
        stream->tail_len = stream->tail_len == 2 ? 2 : stream->tail_len + 1;
    }
}

static void th_detect_stream_start(th_detect_stream *stream, const unsigned char *buf, Py_ssize_t len, int final) {
    Py_ssize_t first = th_detect_first_event(buf, len);
    if (first == len) {
        th_detect_stream_keep_tail(stream, buf, len);
        return;
    }
    stream->candidates = PyMem_Malloc(sizeof(*stream->candidates));
    if (stream->candidates == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();             /* GCOVR_EXCL_LINE: allocation-failure path */
        return;                       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_detect_candidates_init(stream->candidates);
    stream->started = 1;
    if (first < 2 && stream->tail_len > 0) {
        Py_ssize_t context = 2 - first < stream->tail_len ? 2 - first : stream->tail_len;
        th_detect_candidates_feed(stream->candidates, stream->tail + 2 - context, context, 0);
    }
    Py_ssize_t skip = first < 2 ? 0 : first - 2;
    th_detect_candidates_feed(stream->candidates, buf + skip, len - skip, final);
}

static void th_detect_stream_feed(th_detect_stream *stream, const unsigned char *buf, Py_ssize_t len, int final) {
    th_utf8_scan_feed(&stream->utf8, buf, len);
    th_jp_scan_feed(&stream->jp, buf, len, final);
    if (stream->started) {
        th_detect_candidates_feed(stream->candidates, buf, len, final);
        return;
    }
    th_detect_stream_start(stream, buf, len, final);
}

/* The scored answer: the surviving CJK and single-byte candidates compete in chardetng's
   strict-max, defaulting to the TLD's own encoding, with the Hebrew visual/logical tiebreak
   last. Every survivor lands in *scores so detect() can rank alternatives. */
static const th_encoding_entry *th_detect_pick_winner(const th_detect_candidates *stream, th_tld tld,
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
    return th_detect_pick_winner(stream->candidates, tld, scores);
}

/* Guess the encoding of a declaration-less byte stream in one shot. The two structural
   answers come first, so an ASCII or UTF-8 document never pays for candidate scoring. */
static const th_encoding_entry *th_encoding_detect(const unsigned char *buf, Py_ssize_t len, th_tld tld,
                                                   int winner_only, th_detect_scores *scores) {
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
    th_detect_candidates candidates;
    th_detect_candidates_init(&candidates);
    Py_ssize_t first = th_detect_first_event(buf, len);
    Py_ssize_t start = first < 2 ? 0 : first - 2;
    if (winner_only && tld == TH_TLD_GENERIC) {
        th_detect_candidates_feed_bounded(&candidates, buf + start, len - start);
    } else {
        th_detect_candidates_feed(&candidates, buf + start, len - start, 1);
    }
    return th_detect_pick_winner(&candidates, tld, scores);
}
