/* Content-based language detection (roadmap #459), a native-C port of whatlang's
   trigram model (https://github.com/greyblake/whatlang-rs, MIT). #included into
   dom/document.c, so Python.h and its PyUnicode macros are already in scope.

   The pipeline mirrors whatlang: detect the dominant Unicode script, then either
   answer directly for a single-language script, disambiguate Han text between
   Chinese and Japanese by its kana ratio, or -- for a multi-language script --
   rank the candidate languages by the Cavnar-Trenkle distance between the text's
   most-frequent character trigrams and each language's embedded 300-trigram
   profile. The embedded ranges, profiles, and metadata come from language_data.h,
   generated from whatlang so the model is its published one, not a homegrown copy. */

#include "encoding/language_data.h"

#include <stdlib.h>
#include <string.h>

/* whatlang's trigram constants (src/trigrams/mod.rs): a missing or maximally
   displaced trigram costs 300, the total distance saturates at 300*300, and only
   the 600 most-frequent text trigrams carry a rank. */
#define TH_LANG_MAX_TRIGRAM_DISTANCE 300u
#define TH_LANG_MAX_TOTAL_DISTANCE (TH_LANG_MAX_TRIGRAM_DISTANCE * TH_LANG_MAX_TRIGRAM_DISTANCE)
#define TH_LANG_TEXT_TRIGRAMS_SIZE 600u

/* No multi-language script has more candidates than Latin's 37 profiles. */
#define TH_LANG_MAX_CANDIDATES 40

typedef struct {
    int script;
    int lang;
    double confidence;
} th_lang_result;

/* is_stop_char (src/utils.rs): ASCII space, punctuation, and digits carry no
   signal for script or language, so they split words rather than form trigrams. */
static int th_lang_is_stop(uint32_t cp) {
    static const uint32_t stop_ranges[3][2] = {{0x0000, 0x0040}, {0x005B, 0x0060}, {0x007B, 0x007E}};
    for (int index = 0; index < 3; index++) {
        if (cp >= stop_ranges[index][0] && cp <= stop_ranges[index][1]) {
            return 1;
        }
    }
    return 0;
}

/* The script table is sorted by low bound and disjoint, so the first range whose
   low bound has not yet been passed decides membership; a code point below it
   belongs to no script (an unclassified symbol) and the scan can stop. */
static int th_lang_script_of(uint32_t cp) {
    for (size_t index = 0; index < sizeof(th_lang_script_ranges) / sizeof(th_lang_script_ranges[0]); index++) {
        if (cp < th_lang_script_ranges[index].low) {
            break;
        }
        if (cp <= th_lang_script_ranges[index].high) {
            return th_lang_script_ranges[index].script;
        }
    }
    return -1;
}

static void th_lang_count_scripts(int kind, const void *data, Py_ssize_t len, uint32_t counts[TH_LANG_SCRIPT_COUNT]) {
    memset(counts, 0, sizeof(uint32_t) * TH_LANG_SCRIPT_COUNT);
    for (Py_ssize_t index = 0; index < len; index++) {
        uint32_t cp = PyUnicode_READ(kind, data, index);
        if (th_lang_is_stop(cp)) {
            continue;
        }
        int script = th_lang_script_of(cp);
        if (script >= 0) {
            counts[script]++;
        }
    }
}

/* The dominant script is the most frequent; ties break toward the lower script id
   (whatlang's initial counter order), and an all-zero count means no script. */
static int th_lang_main_script(const uint32_t counts[TH_LANG_SCRIPT_COUNT]) {
    int best = -1;
    uint32_t best_count = 0;
    for (int script = 0; script < TH_LANG_SCRIPT_COUNT; script++) {
        if (counts[script] > best_count) {
            best_count = counts[script];
            best = script;
        }
    }
    return best;
}

static uint64_t th_lang_pack(uint32_t a, uint32_t b, uint32_t c) {
    return ((uint64_t)a << 42) | ((uint64_t)b << 21) | (uint64_t)c;
}

static uint32_t th_lang_to_trigram_char(uint32_t cp) {
    return th_lang_is_stop(cp) ? (uint32_t)' ' : cp;
}

static int th_lang_cmp_u64(const void *left, const void *right) {
    uint64_t a = *(const uint64_t *)left;
    uint64_t b = *(const uint64_t *)right;
    return (a > b) - (a < b);
}

typedef struct {
    uint64_t key;
    uint32_t count;
} th_lang_ranked_trigram;

/* Sort text trigrams by descending occurrence, then descending key -- whatlang's
   (count, trigram) descending order, which the packed key preserves since it keeps
   the (first, second, third) code-point ordering. */
static int th_lang_cmp_ranked(const void *left, const void *right) {
    const th_lang_ranked_trigram *a = left;
    const th_lang_ranked_trigram *b = right;
    if (a->count != b->count) {
        return (a->count < b->count) - (a->count > b->count);
    }
    return (a->key < b->key) - (a->key > b->key);
}

/* Extract the text's ranked trigrams. Returns the number kept (the 600 most
   frequent, or fewer), writing them sorted by key into out for a binary-search
   lookup; out[i].count doubles as the trigram's rank. Returns -1 on allocation
   failure. */
static Py_ssize_t th_lang_text_trigrams(int kind, const void *data, Py_ssize_t len, th_lang_ranked_trigram **out) {
    uint64_t *keys = PyMem_Malloc(sizeof(uint64_t) * (size_t)(len + 1));
    if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t total = 0;
    uint32_t c1 = ' ';
    /* the caller only scores trigrams once a script was found, so len is at least 1 */
    uint32_t c2 = th_lang_to_trigram_char(PyUnicode_READ(kind, data, 0));
    for (Py_ssize_t index = 1; index <= len; index++) {
        uint32_t c3 = index < len ? th_lang_to_trigram_char(PyUnicode_READ(kind, data, index)) : (uint32_t)' ';
        if (!(c2 == ' ' && (c1 == ' ' || c3 == ' '))) {
            keys[total++] = th_lang_pack(c1, c2, c3);
        }
        c1 = c2;
        c2 = c3;
    }
    qsort(keys, (size_t)total, sizeof(uint64_t), th_lang_cmp_u64);
    th_lang_ranked_trigram *ranked = PyMem_Malloc(sizeof(th_lang_ranked_trigram) * (size_t)(total + 1));
    if (ranked == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(keys); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t unique = 0;
    for (Py_ssize_t index = 0; index < total; index++) {
        if (unique > 0 && ranked[unique - 1].key == keys[index]) {
            ranked[unique - 1].count++;
        } else {
            ranked[unique].key = keys[index];
            ranked[unique].count = 1;
            unique++;
        }
    }
    PyMem_Free(keys);
    qsort(ranked, (size_t)unique, sizeof(th_lang_ranked_trigram), th_lang_cmp_ranked);
    Py_ssize_t kept = unique < (Py_ssize_t)TH_LANG_TEXT_TRIGRAMS_SIZE ? unique : (Py_ssize_t)TH_LANG_TEXT_TRIGRAMS_SIZE;
    for (Py_ssize_t index = 0; index < kept; index++) {
        ranked[index].count = (uint32_t)index; /* the rank replaces the occurrence count */
    }
    qsort(ranked, (size_t)kept, sizeof(th_lang_ranked_trigram), th_lang_cmp_u64);
    *out = ranked;
    return kept;
}

static int th_lang_rank_of(const th_lang_ranked_trigram *ranked, Py_ssize_t kept, uint64_t key, uint32_t *rank) {
    Py_ssize_t low = 0;
    Py_ssize_t high = kept - 1;
    while (low <= high) {
        Py_ssize_t mid = low + (high - low) / 2;
        if (ranked[mid].key == key) {
            *rank = ranked[mid].count;
            return 1;
        }
        if (ranked[mid].key < key) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }
    return 0;
}

/* whatlang's calculate_distance (src/trigrams/detection.rs): sum each profile
   trigram's rank displacement (300 when absent), then correct for a text with
   fewer than 300 unique trigrams. The u32 arithmetic wraps exactly as whatlang's
   release build does, and the saturation clamps the result to the total maximum. */
static uint32_t th_lang_distance(const th_lang_profile *profile, const th_lang_ranked_trigram *ranked,
                                 Py_ssize_t kept) {
    uint32_t total = 0;
    for (uint16_t index = 0; index < profile->count; index++) {
        uint64_t key = th_lang_pack(profile->trigrams[index].a, profile->trigrams[index].b, profile->trigrams[index].c);
        uint32_t rank;
        if (th_lang_rank_of(ranked, kept, key, &rank)) {
            total += rank > index ? rank - index : index - rank;
        } else {
            total += TH_LANG_MAX_TRIGRAM_DISTANCE;
        }
    }
    uint32_t count = (uint32_t)kept;
    if (TH_LANG_MAX_TRIGRAM_DISTANCE > count) {
        total -= (TH_LANG_MAX_TRIGRAM_DISTANCE - count) * TH_LANG_MAX_TRIGRAM_DISTANCE;
    }
    return total > TH_LANG_MAX_TOTAL_DISTANCE ? TH_LANG_MAX_TOTAL_DISTANCE : total;
}

/* whatlang's calculate_confidence (src/core/confidence.rs): the gap between the
   top two scores, relative to a hyperbola that widens for short texts. */
static double th_lang_confidence(double highest, double second, uint32_t count) {
    if (highest == 0.0) {
        return 0.0;
    }
    if (second == 0.0) {
        return highest;
    }
    double confident_rate = (3.0 / (double)count) + 0.015;
    double rate = (highest - second) / second;
    return rate > confident_rate ? 1.0 : rate / confident_rate;
}

typedef struct {
    uint8_t lang;
    uint32_t distance;
    double score;
} th_lang_scored;

static int th_lang_cmp_scored(const void *left, const void *right) {
    const th_lang_scored *a = left;
    const th_lang_scored *b = right;
    return (a->distance > b->distance) - (a->distance < b->distance);
}

static int th_lang_detect_trigrams(int lkind, const void *ldata, Py_ssize_t llen, int group, const uint8_t *allow,
                                   th_lang_result *result) {
    th_lang_ranked_trigram *ranked;
    Py_ssize_t kept = th_lang_text_trigrams(lkind, ldata, llen, &ranked);
    if (kept < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;  /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    const th_lang_profile_list *list = &th_lang_profile_lists[group];
    uint32_t max_dist = (uint32_t)kept * TH_LANG_MAX_TRIGRAM_DISTANCE;
    th_lang_scored scored[TH_LANG_MAX_CANDIDATES];
    int count = 0;
    for (uint8_t index = 0; index < list->count; index++) {
        const th_lang_profile *profile = &list->profiles[index];
        if (!allow[profile->lang]) {
            continue;
        }
        uint32_t distance = th_lang_distance(profile, ranked, kept);
        scored[count].lang = profile->lang;
        scored[count].distance = distance;
        scored[count].score = (double)(max_dist - distance) / (double)max_dist;
        count++;
    }
    PyMem_Free(ranked);
    qsort(scored, (size_t)count, sizeof(th_lang_scored), th_lang_cmp_scored);
    if (count == 0) {
        result->lang = -1;
        return 0;
    }
    result->lang = scored[0].lang;
    result->confidence = count > 1 ? th_lang_confidence(scored[0].score, scored[1].score, (uint32_t)kept) : 1.0;
    return 0;
}

/* whatlang's Han disambiguation (src/core/detect.rs): Han text is Chinese unless
   enough of the surrounding characters are kana, in which case it is Japanese. */
static void th_lang_detect_mandarin(const uint32_t counts[TH_LANG_SCRIPT_COUNT], const uint8_t *allow,
                                    th_lang_result *result) {
    int allow_cmn = allow[TH_LANG_ID_CMN];
    int allow_jpn = allow[TH_LANG_ID_JPN];
    if (allow_cmn && allow_jpn) {
        uint32_t japanese = counts[TH_LANG_SCRIPT_KATAKANA] + counts[TH_LANG_SCRIPT_HIRAGANA];
        double ratio = (double)japanese / (double)(counts[TH_LANG_SCRIPT_MANDARIN] + japanese);
        if (ratio > 0.2) {
            result->lang = TH_LANG_ID_JPN;
            result->confidence = 1.0;
        } else if (ratio > 0.05) {
            result->lang = TH_LANG_ID_JPN;
            result->confidence = 0.5;
        } else if (ratio > 0.02) {
            result->lang = TH_LANG_ID_CMN;
            result->confidence = 0.5;
        } else {
            result->lang = TH_LANG_ID_CMN;
            result->confidence = 1.0;
        }
    } else if (allow_cmn) {
        result->lang = TH_LANG_ID_CMN;
        result->confidence = 1.0;
    } else if (allow_jpn) {
        result->lang = TH_LANG_ID_JPN;
        result->confidence = 1.0;
    } else {
        result->lang = -1;
    }
}

static int th_lang_detect(int okind, const void *odata, Py_ssize_t olen, int lkind, const void *ldata, Py_ssize_t llen,
                          const uint8_t *allow, th_lang_result *result) {
    uint32_t counts[TH_LANG_SCRIPT_COUNT];
    th_lang_count_scripts(okind, odata, olen, counts);
    result->script = th_lang_main_script(counts);
    result->lang = -1;
    result->confidence = 0.0;
    if (result->script < 0) {
        return 0;
    }
    th_lang_script_group group = th_lang_script_groups[result->script];
    if (group.kind == 0) {
        if (allow[group.payload]) {
            result->lang = group.payload;
            result->confidence = 1.0;
        }
        return 0;
    }
    if (group.kind == 2) {
        th_lang_detect_mandarin(counts, allow, result);
        return 0;
    }
    return th_lang_detect_trigrams(lkind, ldata, llen, group.payload, allow, result);
}
