/* The phone-number recognizer: the C port of tools/phone_model.py over data/phone_table.h.

   One call handles one run of digit groups: expand around the triggering digit into groups and their separators,
   reject the shapes libphonenumber's matcher rejects (dates, page ranges, timestamps, unbalanced brackets) plus the
   identifier shapes it does not (IPv4, payment cards, labeled identifiers), then read the candidate at each group
   boundary, longest first, the way parseHelper would for each configured region: an international prefix commits to
   a country code, the region's own country code may be stripped, or the national prefix is stripped and the number
   validated against the numbering plan the calling code routes to. Every pattern decision is a table walk; the only
   loops are over the digits of one candidate, so the cost is bounded by the run's size. */

#include "clean/phone.h"

#include "data/phone_table.h"

#include <stdint.h>
#include <string.h>

#define END_SYMBOL 10
#define GENERAL_BIT 0x8000u
#define PRIORITY_ACCEPT 0x8000u
#define PRIORITY_FINAL 0x4000u
#define MAX_LEAD_PUNCTUATION 4
/* two lead groups, each a lead character and its punctuation */
#define MAX_LEAD_CHARS ((size_t)2 * (MAX_LEAD_PUNCTUATION + 1))
#define MAX_LEADING_ZEROS 10
#define MAX_LABEL_LENGTH 12
#define DIGITS_CAPACITY (TH_PHONE_DIGIT_BUFFER + TH_PHONE_NSN_CAPACITY)
#define TYPE_COUNT 10
#define NO_REGION (-1)

typedef struct {
    size_t digits_offset; /* into run.digits */
    uint8_t count;
    uint8_t separator_is_dots; /* each character before the group is a full stop, the IPv4 shape */
    uint8_t separator_has_extension_marker;
    size_t start;
    size_t end;
} group_record;

typedef struct {
    size_t start;
    size_t end;
    int plus;
    group_record groups[TH_PHONE_MAX_GROUPS + 1];
    size_t group_count;
    char digits[TH_PHONE_DIGIT_BUFFER];
    size_t digit_count;
    char extension[TH_PHONE_MAX_EXTENSION + 1];
    uint8_t extension_len;
    size_t extension_end;
    uint32_t poison; /* bit per group */
    size_t second_number_cut;
} run_record;

typedef struct {
    char data[DIGITS_CAPACITY];
    size_t len;
} digit_string;

enum reading_source { SOURCE_DEFAULT_REGION = 0, SOURCE_PLUS = 1, SOURCE_IDD = 2, SOURCE_OWN_CODE = 3 };

typedef struct {
    uint16_t country_code;
    uint16_t group; /* index into th_phone_groups */
    digit_string nsn;
    int region;
    uint8_t type;
    uint8_t source; /* enum reading_source, the CountryCodeSource of the reading */
    int general;
    int found;
} reading;

static const uint16_t type_precedence[TYPE_COUNT] = {
    TH_PHONE_PREMIUM_RATE, TH_PHONE_TOLL_FREE, TH_PHONE_SHARED_COST, TH_PHONE_VOIP,       TH_PHONE_PERSONAL_NUMBER,
    TH_PHONE_PAGER,        TH_PHONE_UAN,       TH_PHONE_VOICEMAIL,   TH_PHONE_FIXED_LINE, TH_PHONE_MOBILE,
};

static int in_ranges(uint32_t code, const uint32_t *ranges, size_t count) {
    size_t low = 0;
    size_t high = count;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        if (code < ranges[2 * middle]) {
            high = middle;
        } else if (code > ranges[2 * middle + 1]) {
            low = middle + 1;
        } else {
            return 1;
        }
    }
    return 0;
}

int th_phone_digit_value(uint32_t code) {
    if (code >= '0' && code <= '9') {
        return (int)(code - '0');
    }
    if (code < 0x660) {
        return -1;
    }
    uint32_t page = code >> 8;
    if (!(th_phone_nd_pages[page >> 3] >> (page & 7) & 1)) {
        return -1;
    }
    for (size_t index = 0; index < TH_PHONE_ND_RANGE_COUNT; index++) {
        uint32_t first = th_phone_nd_ranges[3 * index];
        uint32_t last = th_phone_nd_ranges[3 * index + 1];
        if (code >= first && code <= last) {
            return (int)(code - th_phone_nd_ranges[3 * index + 2]);
        }
    }
    return -1;
}

static int is_latin_letter(uint32_t code) {
    return in_ranges(code, th_phone_latin_ranges, TH_PHONE_LATIN_RANGE_COUNT);
}

/* extractPossibleNumber's UNWANTED_END_CHARS refuses \p{L}, \p{N} and `#` after a number. */
static int is_letter(uint32_t code) {
    return in_ranges(code, th_phone_letter_ranges, TH_PHONE_LETTER_RANGE_COUNT);
}

static int is_number_mark(uint32_t code) {
    return in_ranges(code, th_phone_number_ranges, TH_PHONE_NUMBER_RANGE_COUNT);
}

static int is_invalid_punctuation(uint32_t code) {
    return code == '%' || in_ranges(code, th_phone_currency_ranges, TH_PHONE_CURRENCY_RANGE_COUNT);
}

static int is_plus(uint32_t code) {
    return code == '+' || code == 0xFF0B;
}

static int is_opener(uint32_t code) {
    return code == '(' || code == '[' || code == 0xFF08 || code == 0xFF3B;
}

static int is_closer(uint32_t code) {
    return code == ')' || code == ']' || code == 0xFF09 || code == 0xFF3D;
}

/* libphonenumber's VALID_PUNCTUATION: what may sit between two digit groups of one candidate. */
static int is_punctuation(uint32_t code) {
    switch (code) {
    case '-':
    case 'x':
    case 0x2212:
    case 0x30FC:
    case ' ':
    case 0xA0:
    case 0xAD:
    case 0x200B:
    case 0x2060:
    case 0x3000:
    case '(':
    case ')':
    case 0xFF08:
    case 0xFF09:
    case 0xFF3B:
    case 0xFF3D:
    case '.':
    case '[':
    case ']':
    case '/':
    case '~':
    case 0x2053:
    case 0x223C:
    case 0xFF5E:
        return 1;
    default:
        return (code >= 0x2010 && code <= 0x2015) || (code >= 0xFF0D && code <= 0xFF0F);
    }
}

/* The extension markers that are also punctuation a run allows, so a group after one may be the extension. */
static int is_extension_marker(uint32_t code) {
    return code == 'x' || code == '~' || code == 0xFF5E;
}

static int is_full_stop(uint32_t code) {
    return code == '.' || code == 0xFF0E;
}

static const th_phone_dfa *dfa_at(uint16_t index) {
    return &th_phone_dfas[index];
}

static uint16_t dfa_step(const th_phone_dfa *dfa, uint16_t state, uint16_t symbol) {
    size_t offset = dfa->next_offset + (size_t)state * dfa->symbols + symbol;
    return dfa->wide ? th_phone_rows16[offset] : th_phone_rows8[offset];
}

static uint16_t dfa_accept(const th_phone_dfa *dfa, uint16_t state) {
    return th_phone_accepts[dfa->accept_offset + state];
}

/* Java's lookingAt end, or -1; the priority automaton's final state names how many digits back the accepted end
   lies. */
static int priority_match_end(uint16_t dfa_index, const char *digits, size_t len) {
    const th_phone_dfa *dfa = dfa_at(dfa_index);
    uint16_t state = 1;
    for (size_t position = 0; position < len; position++) {
        uint16_t word = dfa_accept(dfa, state);
        if (word & PRIORITY_FINAL) {
            return (int)position - (int)((word >> 8) & 0x1F);
        }
        state = dfa_step(dfa, state, (uint16_t)(digits[position] - '0'));
        if (state == 0) {
            return -1;
        }
    }
    uint16_t word = dfa_accept(dfa, state);
    if (word & PRIORITY_FINAL) {
        return (int)len - (int)((word >> 8) & 0x1F);
    }
    state = dfa_step(dfa, state, END_SYMBOL);
    if (state == 0) {
        return -1;
    }
    /* the end symbol only leads to an accepting state or the dead one */
    word = dfa_accept(dfa, state);
    return (int)len - (int)((word >> 8) & 0x1F);
}

typedef struct {
    uint16_t pc;
    int16_t slots[TH_PHONE_NFA_SLOTS];
} pike_thread;

typedef struct {
    pike_thread threads[TH_PHONE_NFA_THREADS + 1];
    size_t count;
} pike_list;

/* Add the closure of `pc` to the list, depth-first in priority order: a SPLIT's preferred arm first, so the list
   order is Java's evaluation order. The programs have no loops (the generator refuses unbounded repeats here) and
   the generator sizes TH_PHONE_NFA_THREADS by the largest closure, so no op is reached twice and the list never
   fills. */
static void pike_add(const th_phone_nfa *program, pike_list *list, uint16_t pc, const int16_t *slots, int position) {
    uint16_t stack[TH_PHONE_NFA_THREADS + 1];
    int16_t stack_slots[TH_PHONE_NFA_THREADS + 1][TH_PHONE_NFA_SLOTS];
    size_t depth = 0;
    stack[depth] = pc;
    memcpy(stack_slots[depth], slots, sizeof(stack_slots[depth]));
    depth++;
    while (depth > 0) {
        depth--;
        uint16_t current = stack[depth];
        int16_t current_slots[TH_PHONE_NFA_SLOTS];
        memcpy(current_slots, stack_slots[depth], sizeof(current_slots));
        const th_phone_nfa_op *op = &th_phone_nfa_ops[program->first + current];
        if (op->op == TH_PHONE_NFA_SPLIT) {
            stack[depth] = op->alt;
            memcpy(stack_slots[depth], current_slots, sizeof(current_slots));
            depth++;
            stack[depth] = op->next;
            memcpy(stack_slots[depth], current_slots, sizeof(current_slots));
            depth++;
        } else if (op->op == TH_PHONE_NFA_SAVE) {
            current_slots[op->arg] = (int16_t)position;
            stack[depth] = op->next;
            memcpy(stack_slots[depth], current_slots, sizeof(current_slots));
            depth++;
        } else {
            list->threads[list->count].pc = current;
            memcpy(list->threads[list->count].slots, current_slots, sizeof(current_slots));
            list->count++;
        }
    }
}

/* The span of capture `group` on the highest-priority path matching exactly `digits` (the end asserted after them),
   which is the path Java's backtracker took once the priority automaton fixed that end. Returns 0 when the group did
   not participate. */
static int pike_group_span(uint16_t program_index, const char *digits, size_t len, uint8_t group, size_t *span_start,
                           size_t *span_end) {
    const th_phone_nfa *program = &th_phone_nfas[program_index];
    pike_list lists[2];
    int16_t initial[TH_PHONE_NFA_SLOTS];
    memset(initial, -1, sizeof(initial));
    memset(&lists[0], 0, sizeof(lists[0]));
    pike_add(program, &lists[0], program->start, initial, 0);
    size_t current = 0;
    for (size_t position = 0; position < len; position++) {
        pike_list *next = &lists[1 - current];
        memset(next, 0, sizeof(*next));
        uint32_t symbol = (uint32_t)(digits[position] - '0');
        for (size_t index = 0; index < lists[current].count; index++) {
            pike_thread *thread = &lists[current].threads[index];
            const th_phone_nfa_op *op = &th_phone_nfa_ops[program->first + thread->pc];
            if (op->op == TH_PHONE_NFA_CHAR && (th_phone_nfa_classes[op->arg] >> symbol & 1)) {
                pike_add(program, next, op->next, thread->slots, (int)position + 1);
            }
        }
        current = 1 - current;
    }
    /* the priority automaton accepted these digits, so a thread stands at MATCH or at an end assertion whose closure
       is a MATCH; the first in priority order is Java's path */
    pike_list *final_list = &lists[1 - current];
    memset(final_list, 0, sizeof(*final_list));
    for (size_t index = 0; index < lists[current].count; index++) {
        pike_thread *thread = &lists[current].threads[index];
        const th_phone_nfa_op *op = &th_phone_nfa_ops[program->first + thread->pc];
        if (op->op == TH_PHONE_NFA_MATCH) {
            final_list->threads[final_list->count] = *thread;
            final_list->count++;
        } else if (op->op == TH_PHONE_NFA_ASSERT_END) {
            pike_add(program, final_list, op->next, thread->slots, (int)len);
        }
    }
    int16_t open = final_list->threads[0].slots[2 * (size_t)group];
    if (open < 0) {
        return 0;
    }
    *span_start = (size_t)open;
    *span_end = (size_t)final_list->threads[0].slots[2 * (size_t)group + 1];
    return 1;
}

static uint16_t plan_accept(const th_phone_region *region, const char *digits, size_t len, uint16_t *type_mask) {
    const th_phone_dfa *dfa = dfa_at(region->plan);
    uint16_t state = 1;
    for (size_t position = 0; position < len; position++) {
        state = dfa_step(dfa, state, (uint16_t)(digits[position] - '0'));
        if (state == 0) {
            *type_mask = 0;
            return 0;
        }
    }
    uint16_t word = dfa_accept(dfa, state);
    uint16_t label = word & 0xFu;
    *type_mask = label ? th_phone_type_masks[region->labels + label - 1] : 0;
    return word;
}

static int general_matches(const th_phone_region *region, const char *digits, size_t len) {
    uint16_t type_mask;
    return (plan_accept(region, digits, len, &type_mask) & GENERAL_BIT) != 0;
}

static uint8_t resolve_type(uint16_t type_mask) {
    for (size_t index = 0; index < TYPE_COUNT; index++) {
        uint16_t bit = type_precedence[index];
        if (type_mask >> bit & 1) {
            if (bit == TH_PHONE_FIXED_LINE && (type_mask >> TH_PHONE_MOBILE & 1)) {
                return TH_PHONE_FIXED_LINE_OR_MOBILE;
            }
            return (uint8_t)bit;
        }
    }
    return TH_PHONE_UNKNOWN;
}

enum length_result {
    LENGTH_POSSIBLE,
    LENGTH_TOO_SHORT,
    LENGTH_TOO_LONG,
    LENGTH_LOCAL_ONLY,
    LENGTH_INVALID,
};

static enum length_result length_result(const th_phone_region *region, size_t len) {
    /* a run holds up to 240 digits, past the 32 lengths the masks describe and past any plan's longest */
    if (len >= 32) {
        return LENGTH_TOO_LONG;
    }
    if (region->possible_local_only >> len & 1) {
        return LENGTH_LOCAL_ONLY;
    }
    uint32_t national = region->possible_national;
    size_t shortest = 0;
    while (!(national >> shortest & 1)) {
        shortest++;
    }
    size_t longest = 31;
    while (!(national >> longest & 1)) {
        longest--;
    }
    if (len < shortest) {
        return LENGTH_TOO_SHORT;
    }
    if (len > longest) {
        return LENGTH_TOO_LONG;
    }
    return (national >> len & 1) ? LENGTH_POSSIBLE : LENGTH_INVALID;
}

static size_t longest_possible(const th_phone_region *region) {
    uint32_t lengths = region->possible_national | region->possible_local_only;
    size_t longest = 0;
    for (size_t length = 0; length < 32; length++) {
        if (lengths >> length & 1) {
            longest = length;
        }
    }
    return longest;
}

static void copy_digits(digit_string *target, const char *source, size_t len) {
    memcpy(target->data, source, len);
    target->len = len;
}

/* maybeStripNationalPrefixAndCarrierCode: an empty prefix match counts, as lookingAt's does, and the generalDesc
   guard lets the rewrite stand. */
static int strip(const th_phone_region *region, const digit_string *input, digit_string *output) {
    if (region->national_prefix == 0xFFFF) {
        return 0;
    }
    int end = priority_match_end(region->national_prefix, input->data, input->len);
    if (end < 0) {
        return 0;
    }
    const th_phone_prefix_tag *tag = &th_phone_prefix_tags[region->prefix_tag];
    digit_string transformed;
    transformed.len = 0;
    size_t span_start = 0;
    size_t span_end = 0;
    if (tag->group != 0 &&
        pike_group_span(tag->program, input->data, (size_t)end, tag->group, &span_start, &span_end)) {
        memcpy(transformed.data, tag->literal, tag->literal_len);
        transformed.len = tag->literal_len;
        memcpy(transformed.data + transformed.len, input->data + span_start, span_end - span_start);
        transformed.len += span_end - span_start;
    }
    size_t rest = input->len - (size_t)end;
    memcpy(transformed.data + transformed.len, input->data + end, rest);
    transformed.len += rest;
    if (general_matches(region, input->data, input->len) &&
        !general_matches(region, transformed.data, transformed.len)) {
        return 0;
    }
    *output = transformed;
    return 1;
}

/* parseHelper's length adoption: with `adopt`, a rewrite that leaves a non-viable length is discarded. */
static void strip_prefix(const th_phone_region *region, const digit_string *input, int adopt, digit_string *output) {
    digit_string transformed;
    if (!strip(region, input, &transformed)) {
        *output = *input;
        return;
    }
    if (adopt) {
        enum length_result result = length_result(region, transformed.len);
        if (result == LENGTH_TOO_SHORT || result == LENGTH_LOCAL_ONLY || result == LENGTH_INVALID) {
            *output = *input;
            return;
        }
    }
    *output = transformed;
}

static int dfa_looking_at(uint16_t index, const char *digits, size_t len) {
    const th_phone_dfa *dfa = dfa_at(index);
    uint16_t state = 1;
    for (size_t position = 0; position < len; position++) {
        state = dfa_step(dfa, state, (uint16_t)(digits[position] - '0'));
        if (state == 0) {
            return 0;
        }
        if (dfa_accept(dfa, state)) {
            return 1;
        }
    }
    return 0;
}

/* A format's pattern is a run of digit groups, so `matches()` is a bound on the digit count. */
static int format_fits(const th_phone_format *format, size_t len) {
    size_t low = 0;
    size_t high = 0;
    for (size_t index = 0; index < format->group_count; index++) {
        low += format->groups[index] >> 4;
        high += format->groups[index] & 0xFu;
    }
    return low <= len && len <= high;
}

/* chooseFormattingPatternForNumber's order: the first fitting format wins; the international list lacks the NA
   formats. */
static const th_phone_format *choose_format(const th_phone_format *formats, size_t count, const char *nsn, size_t len,
                                            int intl) {
    for (size_t index = 0; index < count; index++) {
        const th_phone_format *format = &formats[index];
        if ((intl && format->intl == 0xFFFF) || !dfa_looking_at(format->leading, nsn, len) ||
            !format_fits(format, len)) {
            continue;
        }
        return format;
    }
    return NULL;
}

/* Leniency.VALID's isNationalPrefixPresentIfRequired for a number read through the default region: the calling
   code's main region picks the number format the way chooseFormattingPatternForNumber does, and when that format
   writes the national prefix, the raw digits must have carried one. */
static int prefix_present_if_required(const th_phone_region *main, const char *raw, size_t raw_len,
                                      const digit_string *nsn) {
    const th_phone_format *format =
        choose_format(&th_phone_formats[main->format_first], main->format_count, nsn->data, nsn->len, 0);
    if (format == NULL || !format->requires_prefix) {
        return 1;
    }
    digit_string input;
    copy_digits(&input, raw, raw_len);
    digit_string stripped;
    return strip(main, &input, &stripped);
}

static int route(const th_phone_group *group, const char *digits, size_t len, uint16_t *word_out, uint16_t *mask_out) {
    if (group->count == 1) {
        uint16_t index = th_phone_group_regions[group->first] & 0x7FFFu;
        *word_out = plan_accept(&th_phone_regions[index], digits, len, mask_out);
        return index;
    }
    /* a shared code whose members have no leadingDigits (+61: AU, CC, CX) has no router and routes by typed match */
    uint32_t routed_set = 0;
    if (group->router != 0xFFFF) {
        const th_phone_dfa *router = dfa_at(group->router);
        uint16_t state = 1;
        for (size_t position = 0; position < len && state != 0; position++) {
            state = dfa_step(router, state, (uint16_t)(digits[position] - '0'));
        }
        routed_set = state ? th_phone_router_sets[dfa_accept(router, state)] : 0;
    }
    for (size_t position = 0; position < group->count; position++) {
        uint16_t entry = th_phone_group_regions[group->first + position];
        uint16_t index = entry & 0x7FFFu;
        uint16_t mask;
        uint16_t word = plan_accept(&th_phone_regions[index], digits, len, &mask);
        if (entry & 0x8000u) {
            if (routed_set >> position & 1) {
                *word_out = word;
                *mask_out = mask;
                return index;
            }
            continue;
        }
        if ((word & GENERAL_BIT) && resolve_type(mask) != TH_PHONE_UNKNOWN) {
            *word_out = word;
            *mask_out = mask;
            return index;
        }
    }
    *word_out = 0;
    *mask_out = 0;
    return NO_REGION;
}

static void cap_leading_zeros(digit_string *nsn) {
    size_t zeros = 0;
    while (zeros + 1 < nsn->len && nsn->data[zeros] == '0') {
        zeros++;
    }
    if (zeros > MAX_LEADING_ZEROS) {
        size_t drop = zeros - MAX_LEADING_ZEROS;
        memmove(nsn->data, nsn->data + drop, nsn->len - drop);
        nsn->len -= drop;
    }
}

/* isValidNumber, or isPossibleNumber in possible mode, over a calling-code group. */
static void validate(const th_phone_config *config, const th_phone_group *group, const digit_string *nsn,
                     reading *result) {
    result->found = 0;
    if (nsn->len < 2 || nsn->len > TH_PHONE_MAX_NSN) {
        return;
    }
    uint16_t word;
    uint16_t mask;
    int region = route(group, nsn->data, nsn->len, &word, &mask);
    if (config->require_valid) {
        if (region == NO_REGION || !(word & GENERAL_BIT)) {
            return;
        }
        uint8_t type = resolve_type(mask);
        if (type == TH_PHONE_UNKNOWN || !(config->type_mask >> type & 1)) {
            return;
        }
        result->type = type;
        result->general = 1;
    } else {
        const th_phone_region *main = &th_phone_regions[group->main];
        uint32_t lengths = main->possible_national | main->possible_local_only;
        if (!(lengths >> nsn->len & 1)) {
            return;
        }
        result->type = TH_PHONE_UNKNOWN;
        result->general = region != NO_REGION && (word & GENERAL_BIT) != 0;
    }
    result->country_code = group->country_code;
    result->group = (uint16_t)(group - th_phone_groups);
    result->region = region;
    copy_digits(&result->nsn, nsn->data, nsn->len);
    cap_leading_zeros(&result->nsn);
    result->found = 1;
}

/* -1 when no calling code is assigned; callers give at least three digits, so the probe reads in bounds. */
static int group_of_country_code(const char *digits, size_t *code_length) {
    if (digits[0] == '0') {
        return -1;
    }
    unsigned value = 0;
    for (size_t length = 1; length <= 3; length++) {
        value = value * 10 + (unsigned)(digits[length - 1] - '0');
        const uint8_t *table = length == 1 ? th_phone_cc1 : length == 2 ? th_phone_cc2 : th_phone_cc3;
        if (table[value] != 0xFF) {
            *code_length = length;
            return table[value];
        }
    }
    return -1;
}

/* parseHelper's regionMetadata switch: the calling code's main region strips the national prefix. */
static int read_international(const th_phone_config *config, const char *digits, size_t len, reading *result) {
    result->found = 0;
    if (len <= 2) {
        return 1;
    }
    size_t code_length = 0;
    int group_index = group_of_country_code(digits, &code_length);
    if (group_index < 0) {
        return 0;
    }
    if (len - code_length < 2) {
        return 1;
    }
    const th_phone_group *group = &th_phone_groups[group_index];
    digit_string rest;
    copy_digits(&rest, digits + code_length, len - code_length);
    digit_string nsn;
    strip_prefix(&th_phone_regions[group->main], &rest, 1, &nsn);
    validate(config, group, &nsn, result);
    return 1;
}

/* Which of parseHelper's readings a default region may make: all three, the international prefix alone (a bare
   digit run under require_separators), or the two that carry a country code (the retry after a plus that led to no
   calling code). */
enum reading_mode { READ_ANY = 0, READ_IDD = 1, READ_WITH_CODE = 2 };

/* parseHelper with a default region: an international prefix commits to a country code, the region's own country
   code may be stripped after the extraction test, otherwise the national prefix is stripped and the group validates. */
static void read_national(const th_phone_config *config, uint16_t region_index, const char *digits, size_t len,
                          enum reading_mode mode, reading *result) {
    result->found = 0;
    const th_phone_region *region = &th_phone_regions[region_index];
    int idd_end = priority_match_end(region->idd, digits, len);
    if (idd_end > 0 && ((size_t)idd_end >= len || digits[idd_end] != '0')) {
        if (len - (size_t)idd_end > 2) {
            read_international(config, digits + idd_end, len - (size_t)idd_end, result);
            result->source = SOURCE_IDD;
        }
        return;
    }
    if (mode == READ_IDD) {
        return;
    }
    const th_phone_group *group = &th_phone_groups[region->group];
    char code[4];
    size_t code_length = 0;
    unsigned country_code = region->country_code;
    do {
        code[code_length++] = (char)('0' + country_code % 10);
        country_code /= 10;
    } while (country_code);
    for (size_t index = 0; index < code_length / 2; index++) {
        char swap = code[index];
        code[index] = code[code_length - 1 - index];
        code[code_length - 1 - index] = swap;
    }
    if (len > code_length && memcmp(digits, code, code_length) == 0) {
        digit_string after_code;
        copy_digits(&after_code, digits + code_length, len - code_length);
        digit_string potential;
        strip_prefix(region, &after_code, 0, &potential);
        int full_general = general_matches(region, digits, len);
        if ((!full_general && general_matches(region, potential.data, potential.len)) ||
            len > longest_possible(region)) {
            digit_string nsn;
            const th_phone_region *parse_region = group->main == region_index ? region : &th_phone_regions[group->main];
            strip_prefix(parse_region, &potential, 1, &nsn);
            validate(config, group, &nsn, result);
            result->source = SOURCE_OWN_CODE;
            return;
        }
    }
    if (mode == READ_WITH_CODE) {
        return;
    }
    digit_string input;
    copy_digits(&input, digits, len);
    digit_string nsn;
    strip_prefix(region, &input, 1, &nsn);
    validate(config, group, &nsn, result);
    result->source = SOURCE_DEFAULT_REGION;
    if (result->found && config->require_valid && config->require_national_prefix &&
        !prefix_present_if_required(&th_phone_regions[group->main], digits, len, &result->nsn)) {
        result->found = 0;
    }
}

/* Java's lead, `(?:[lead][punct]{0,4}){0,2}`, consumed exactly over the text before the digits: a lead character,
   then the greedy punctuation run, at most twice. Every lead character that is also punctuation gets taken by the
   greedy run of the group before it, so the regex never needs to give punctuation back. */
static int lead_groups_match_from(th_phone_read read, const void *text, size_t position, size_t end, int groups_left) {
    if (position == end) {
        return 1;
    }
    uint32_t code = read(text, position);
    if (groups_left == 0 || !(is_plus(code) || is_opener(code))) {
        return 0;
    }
    position++;
    size_t punctuation = 0;
    while (punctuation < MAX_LEAD_PUNCTUATION && position < end && is_punctuation(read(text, position))) {
        position++;
        punctuation++;
    }
    return lead_groups_match_from(read, text, position, end, groups_left - 1);
}

static int lead_groups_match(th_phone_read read, const void *text, size_t start, size_t end) {
    return lead_groups_match_from(read, text, start, end, 2);
}

/* MATCHING_BRACKETS over the candidate: an optional leading opener, an optional leading "text then closer", then at
   most three balanced pairs and nothing else. */
static int brackets_match(th_phone_read read, const void *text, size_t start, size_t end) {
    size_t position = start;
    if (is_opener(read(text, position))) {
        position++;
    }
    size_t content = 0;
    size_t scan = position;
    while (scan < end && !is_opener(read(text, scan)) && !is_closer(read(text, scan))) {
        scan++;
        content++;
    }
    if (scan < end && is_closer(read(text, scan)) && content > 0) {
        position = scan + 1;
        content = 0;
        scan = position;
        while (scan < end && !is_opener(read(text, scan)) && !is_closer(read(text, scan))) {
            scan++;
            content++;
        }
    }
    if (content == 0) {
        return 0;
    }
    position = scan;
    int pairs = 0;
    while (position < end) {
        uint32_t code = read(text, position);
        if (is_closer(code)) {
            return 0;
        }
        if (!is_opener(code)) {
            position++;
            continue;
        }
        if (pairs == 3) {
            return 0;
        }
        position++;
        size_t inner = 0;
        while (position < end && !is_opener(read(text, position)) && !is_closer(read(text, position))) {
            position++;
            inner++;
        }
        if (position >= end || !is_closer(read(text, position)) || inner == 0) {
            return 0;
        }
        position++;
        pairs++;
    }
    return 1;
}

static int second_number_start(th_phone_read read, const void *text, size_t len, size_t slash, size_t *cut_end) {
    size_t position = slash + 1;
    while (position < len && read(text, position) == ' ') {
        position++;
    }
    if (position >= len || read(text, position) != 'x') {
        return 0;
    }
    position++;
    while (position < len && th_phone_digit_value(read(text, position)) >= 0) {
        position++;
    }
    *cut_end = position;
    return 1;
}

static uint16_t extension_symbol(uint32_t code) {
    if (th_phone_digit_value(code) >= 0) {
        return 1;
    }
    size_t low = 0;
    size_t high = TH_PHONE_EXT_CLASS_COUNT;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        uint32_t first = th_phone_ext_classes[3 * middle];
        uint32_t last = th_phone_ext_classes[3 * middle + 1];
        if (code < first) {
            high = middle;
        } else if (code > last) {
            low = middle + 1;
        } else {
            return (uint16_t)th_phone_ext_classes[3 * middle + 2];
        }
    }
    return 0;
}

static uint16_t extension_dfa(const th_phone_config *config) {
    return config->parsing_extensions ? TH_PHONE_EXT_PARSING_DFA : TH_PHONE_EXT_DFA;
}

static int walk_extension(th_phone_read read, const void *text, size_t len, uint16_t grammar, size_t tail_start,
                          size_t *tail_end, char *ext_digits, uint8_t *ext_len) {
    const th_phone_dfa *dfa = dfa_at(grammar);
    uint16_t state = 1;
    char digits[TH_PHONE_MAX_EXTENSION + 1];
    uint8_t count = 0;
    int found = 0;
    for (size_t position = tail_start; position < len; position++) {
        uint32_t code = read(text, position);
        int value = th_phone_digit_value(code);
        state = dfa_step(dfa, state, extension_symbol(code));
        if (state == 0) {
            break;
        }
        /* the grammar bounds the digits to TH_PHONE_MAX_EXTENSION, admits nothing but `#` after them and accepts only
           once one is seen, so the digits collected are the extension's */
        if (value >= 0) {
            digits[count++] = (char)('0' + value);
        }
        if (dfa_accept(dfa, state)) {
            found = 1;
            *tail_end = position + 1;
            memcpy(ext_digits, digits, count);
            *ext_len = count;
        }
    }
    return found;
}

static int segment(th_phone_read read, const void *text, size_t len, uint16_t grammar, size_t left_bound,
                   size_t digit_pos, run_record *run) {
    memset(run, 0, sizeof(*run));
    size_t digits_start = digit_pos;
    size_t position = digits_start;
    int separator_dots = 0;
    int separator_marker = 0;
    for (;;) {
        group_record *group = &run->groups[run->group_count];
        group->digits_offset = run->digit_count;
        group->start = position;
        group->count = 0;
        int value;
        while (position < len && (value = th_phone_digit_value(read(text, position))) >= 0) {
            if (group->count >= TH_PHONE_MAX_GROUP_DIGITS) {
                group->count = TH_PHONE_MAX_GROUP_DIGITS + 1;
                break;
            }
            run->digits[run->digit_count++] = (char)('0' + value);
            group->count++;
            position++;
        }
        if (group->count > TH_PHONE_MAX_GROUP_DIGITS) {
            run->digit_count = group->digits_offset;
            break;
        }
        group->end = position;
        group->separator_is_dots = (uint8_t)separator_dots;
        group->separator_has_extension_marker = (uint8_t)separator_marker;
        run->group_count++;
        if (run->group_count == TH_PHONE_MAX_GROUPS) {
            break;
        }
        size_t probe = position;
        size_t punctuation = 0;
        int next_dots = 1;
        separator_marker = 0;
        int cut = 0;
        while (probe < len && punctuation < MAX_LEAD_PUNCTUATION) {
            uint32_t code = read(text, probe);
            if (!is_punctuation(code)) {
                break;
            }
            if (code == '/' && second_number_start(read, text, len, probe, &run->second_number_cut)) {
                cut = 1;
                break;
            }
            next_dots &= is_full_stop(code);
            if (is_extension_marker(code)) {
                separator_marker = 1;
            }
            probe++;
            punctuation++;
        }
        if (cut || probe == position || probe >= len || th_phone_digit_value(read(text, probe)) < 0 ||
            probe - digits_start > TH_PHONE_MAX_RUN_CHARS) {
            break;
        }
        separator_dots = next_dots;
        position = probe;
    }
    if (run->group_count == 0) {
        return 0;
    }
    run->end = run->groups[run->group_count - 1].end;
    size_t earliest = digits_start > left_bound + MAX_LEAD_CHARS ? digits_start - MAX_LEAD_CHARS : left_bound;
    /* the bracket rule belongs to parseAndVerify, which read_chunk applies per chunk: a lead whose bracket never
       closes still starts the candidate, and the text before that bracket is the chunk extractInnerMatch offers */
    run->start = digits_start;
    for (size_t candidate = earliest; candidate < digits_start; candidate++) {
        if (lead_groups_match(read, text, candidate, digits_start)) {
            run->start = candidate;
            break;
        }
    }
    for (size_t position_in_lead = run->start; position_in_lead < digits_start; position_in_lead++) {
        if (is_plus(read(text, position_in_lead))) {
            run->plus = 1;
        }
    }
    run->extension_end = run->end;
    size_t tail_end;
    char ext_digits[TH_PHONE_MAX_EXTENSION + 1];
    uint8_t ext_len;
    if (walk_extension(read, text, len, grammar, run->end, &tail_end, ext_digits, &ext_len)) {
        memcpy(run->extension, ext_digits, ext_len);
        run->extension_len = ext_len;
        run->extension_end = tail_end;
    } else if (run->group_count > 1 && run->groups[run->group_count - 1].separator_has_extension_marker &&
               walk_extension(read, text, len, grammar, run->groups[run->group_count - 2].end, &tail_end, ext_digits,
                              &ext_len) &&
               tail_end >= run->end) {
        /* PATTERN reads `x1234` as a punctuation-separated digit block, so a `#` after it stays outside the match */
        run->group_count--;
        run->digit_count = run->groups[run->group_count].digits_offset;
        memcpy(run->extension, ext_digits, ext_len);
        run->extension_len = ext_len;
    }
    return 1;
}

static int group_value(const run_record *run, const group_record *group, unsigned *value) {
    if (group->count > 3) {
        return 0;
    }
    unsigned total = 0;
    for (size_t index = 0; index < group->count; index++) {
        total = total * 10 + (unsigned)(run->digits[group->digits_offset + index] - '0');
    }
    *value = total;
    return 1;
}

/* PhoneNumberMatcher.SLASH_SEPARATED_DATES across three groups: a single slash on each side of a one- or two-digit
   middle part of at most 39, and a year part of at least two digits. The regex is unanchored, so its first part is
   satisfied by the last digit of whatever group precedes the slash. */
static int is_slash_date(th_phone_read read, const void *text, const run_record *run, size_t index) {
    const group_record *first = &run->groups[index];
    const group_record *second = &run->groups[index + 1];
    const group_record *third = &run->groups[index + 2];
    if (second->count > 2 || third->count < 2 || second->start - first->end != 1 || third->start - second->end != 1) {
        return 0;
    }
    if (read(text, first->end) != '/' || read(text, second->end) != '/') {
        return 0;
    }
    return second->count == 1 || run->digits[second->digits_offset] <= '3';
}

/* PhoneNumberMatcher.TIME_STAMPS at the run's end followed by TIME_STAMPS_SUFFIX: eight digits shaped
   [12]ddd[01]d[0-3]d, split only after the year and after the month and only by a single `-` or `/`, then spaces, a
   two-digit hour starting 0-2, and `:MM` right after the run. Returns the count of groups it spans, 0 when none. */
static size_t timestamp_groups(th_phone_read read, const void *text, size_t len, const run_record *run) {
    if (run->group_count < 2) {
        return 0;
    }
    const group_record *hour = &run->groups[run->group_count - 1];
    if (hour->count != 2 || run->digits[hour->digits_offset] > '2') {
        return 0;
    }
    for (size_t position = run->groups[run->group_count - 2].end; position < hour->start; position++) {
        if (read(text, position) != ' ') {
            return 0;
        }
    }
    if (run->end + 2 >= len || read(text, run->end) != ':') {
        return 0;
    }
    uint32_t tens = read(text, run->end + 1);
    uint32_t units = read(text, run->end + 2);
    if (tens < '0' || tens > '5' || units < '0' || units > '9') {
        return 0;
    }
    size_t spanned = 1;
    size_t accumulated = 0;
    size_t index = run->group_count - 1;
    while (accumulated < 8 && index > 0) {
        index--;
        const group_record *group = &run->groups[index];
        if (accumulated > 0) {
            uint32_t separator = read(text, group->end);
            if (run->groups[index + 1].start - group->end != 1 || (separator != '-' && separator != '/') ||
                (accumulated != 2 && accumulated != 4)) {
                return 0;
            }
        }
        accumulated += group->count;
        spanned++;
    }
    if (accumulated < 8) {
        return 0;
    }
    const char *stamp = run->digits + hour->digits_offset - 8;
    if ((stamp[0] != '1' && stamp[0] != '2') || stamp[4] > '1' || stamp[6] > '3') {
        return 0;
    }
    return spanned;
}

/* A further dotted group on either side means no address; each address poisons only its own four groups, so a
   number written after it keeps its first group. */
static void poison_ipv4(run_record *run) {
    if (run->plus) {
        return;
    }
    for (size_t index = 0; index + 4 <= run->group_count; index++) {
        int address = index == 0 || !run->groups[index].separator_is_dots;
        address &= index + 4 == run->group_count || !run->groups[index + 4].separator_is_dots;
        for (size_t offset = 0; offset < 4; offset++) {
            unsigned value;
            const group_record *group = &run->groups[index + offset];
            address &= (offset == 0 || group->separator_is_dots) && group_value(run, group, &value) && value <= 255;
        }
        if (address) {
            run->poison |= 0xFu << index;
        }
    }
}

static int is_space_separator(uint32_t code) {
    return code == ' ' || code == 0xA0 || code == 0x3000;
}

/* Does whitespace sit between a group and the one before it? An identifier label reaches over hyphens and dots
   (`Order 650-253-0000`), not over the space that starts a new token (`Order 12345, 650-253-0000`). */
static int separator_has_space(th_phone_read read, const void *text, const run_record *run, size_t index) {
    for (size_t position = run->groups[index - 1].end; position < run->groups[index].start; position++) {
        uint32_t code = read(text, position);
        if (is_space_separator(code)) {
            return 1;
        }
    }
    return 0;
}

static int label_before(th_phone_read read, const void *text, size_t start, const th_phone_config *config) {
    if (config->label_count == 0) {
        return 0;
    }
    static const char marks[] = "\t\n\r.:#-";
    size_t position = start;
    while (position > 0) {
        uint32_t code = read(text, position - 1);
        if (!is_space_separator(code) && !(code < 0x80 && memchr(marks, (int)code, sizeof marks - 1) != NULL)) {
            break;
        }
        position--;
    }
    size_t word_end = position;
    char word[MAX_LABEL_LENGTH + 1];
    size_t length = 0;
    while (position > 0 && length < MAX_LABEL_LENGTH) {
        uint32_t code = read(text, position - 1);
        if (!((code >= 'a' && code <= 'z') || (code >= 'A' && code <= 'Z'))) {
            break;
        }
        position--;
        length++;
    }
    if (length == 0 || (position > 0 && is_latin_letter(read(text, position - 1)))) {
        return 0;
    }
    for (size_t index = 0; index < length; index++) {
        word[index] = (char)(read(text, position + index) | 0x20);
    }
    word[length] = '\0';
    (void)word_end;
    size_t low = 0;
    size_t high = config->label_count;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        const th_phone_label *label = &config->labels[middle];
        size_t compared = label->len < length ? label->len : length;
        int order = memcmp(word, label->text, compared);
        if (order == 0) {
            order = length < label->len ? -1 : length > label->len ? 1 : 0;
        }
        if (order == 0) {
            return 1;
        }
        if (order < 0) {
            high = middle;
        } else {
            low = middle + 1;
        }
    }
    return 0;
}

static int luhn_over(const run_record *run, size_t first, size_t end_group) {
    size_t begin = run->groups[first].digits_offset;
    size_t stop = run->groups[end_group - 1].digits_offset + run->groups[end_group - 1].count;
    unsigned total = 0;
    size_t position = 0;
    for (size_t index = stop; index > begin; index--) {
        unsigned value = (unsigned)(run->digits[index - 1] - '0');
        if (position % 2 == 1) {
            value *= 2;
            if (value > 9) {
                value -= 9;
            }
        }
        total += value;
        position++;
    }
    return total % 10 == 0;
}

/* skip_card_numbers: a run that is one Luhn-valid card shape (4-4-4-4, 4-4-4-4-3, 4-6-5, 4-6-4) is poisoned whole,
   and an unbroken group of 13 to 19 Luhn-valid digits is poisoned wherever it sits, so a card written before a
   phone leaves the phone readable. A spaced card window inside a longer run is not told apart from two numbers
   whose leading digits happen to pass Luhn (`5005 000123 5005 000123`), so the shapes apply to whole runs only. */
static void poison_cards(run_record *run) {
    static const uint8_t shapes[][5] = {{4, 4, 4, 4, 0}, {4, 4, 4, 4, 3}, {4, 6, 5, 0, 0}, {4, 6, 4, 0, 0}};
    for (size_t index = 0; index < run->group_count; index++) {
        uint8_t count = run->groups[index].count;
        if (count >= 13 && count <= 19 && luhn_over(run, index, index + 1)) {
            run->poison |= 1u << index;
        }
    }
    for (size_t shape = 0; shape < sizeof(shapes) / sizeof(shapes[0]); shape++) {
        size_t matched = 0;
        while (matched < run->group_count && matched < 5 && run->groups[matched].count == shapes[shape][matched]) {
            matched++;
        }
        /* the shape's match is a variable because folding it into the condition changes what the link-time
           optimizer inlines here, which costs an unrelated benchmark 8% on the performance gate */
        int whole = matched == run->group_count && (matched == 5 || shapes[shape][matched] == 0);
        if (whole && luhn_over(run, 0, run->group_count)) {
            run->poison |= (1u << run->group_count) - 1u;
        }
    }
}

static void poison(th_phone_read read, const void *text, size_t len, const th_phone_config *config, run_record *run) {
    for (size_t index = 0; index + 2 < run->group_count; index++) {
        if (is_slash_date(read, text, run, index)) {
            run->poison |= 7u << index;
        }
    }
    size_t stamp = timestamp_groups(read, text, len, run);
    if (stamp) {
        run->poison |= ((1u << stamp) - 1u) << (run->group_count - stamp);
    }
    poison_ipv4(run);
    if (config->skip_card_numbers && !run->plus) {
        poison_cards(run);
    }
    if (label_before(read, text, run->start, config)) {
        size_t index = 0;
        do {
            run->poison |= 1u << index;
            index++;
        } while (index < run->group_count && !separator_has_space(read, text, run, index));
    }
}

static int is_wide_hyphen(uint32_t code) {
    return (code >= 0x2012 && code <= 0x2015) || code == 0xFF0D;
}

typedef struct {
    size_t start;
    size_t end;
} text_range;

/* One whole plus each rule's splits: two for the slash, one per opener (two lead characters and four per separator)
   plus one, two each for the hyphen rules, and one per group plus one for the dot and space rules. */
#define MAX_CHUNKS (1 + 2 + (2 + 4 * (TH_PHONE_MAX_GROUPS - 1) + 1) + 2 + 2 + 2 * (TH_PHONE_MAX_GROUPS + 1))
#define NO_POSITION SIZE_MAX

typedef struct {
    text_range ranges[MAX_CHUNKS];
    size_t count;
} chunk_list;

/* The rules overlap, so one range can arrive twice. */
static void push_chunk(chunk_list *chunks, size_t start, size_t end) {
    for (size_t index = 0; index < chunks->count; index++) {
        if (chunks->ranges[index].start == start && chunks->ranges[index].end == end) {
            return;
        }
    }
    chunks->ranges[chunks->count].start = start;
    chunks->ranges[chunks->count].end = end;
    chunks->count++;
}

/* A candidate ends on a digit or an extension's last character, not on a separator, so these runs stop inside it. */
static size_t skip_space_separators(th_phone_read read, const void *text, size_t position) {
    while (is_space_separator(read(text, position))) {
        position++;
    }
    return position;
}

/* The text ranges parseAndVerify sees for one candidate: the whole, then PhoneNumberMatcher.INNER_MATCHES in its
   order: the part after the first slash run, each bracketed part, the parts around a spaced hyphen, around a wide
   hyphen, between dots, between spaces; each rule first offers the text before its first match. */
static void enumerate_chunks(th_phone_read read, const void *text, size_t start, size_t end, chunk_list *chunks) {
    chunks->count = 0;
    push_chunk(chunks, start, end);
    for (size_t position = start; position < end; position++) {
        if (read(text, position) == '/') {
            push_chunk(chunks, start, position);
            size_t after = position;
            while (read(text, after) == '/') {
                after++;
            }
            push_chunk(chunks, after, end);
            break;
        }
    }
    size_t previous = NO_POSITION;
    for (size_t position = start; position < end; position++) {
        if (read(text, position) == '(') {
            push_chunk(chunks, previous == NO_POSITION ? start : previous, position);
            previous = position;
        }
    }
    if (previous != NO_POSITION) {
        push_chunk(chunks, previous, end);
    }
    for (size_t position = start; position + 1 < end; position++) {
        uint32_t code = read(text, position);
        uint32_t following = read(text, position + 1);
        if ((is_space_separator(code) && following == '-') || (code == '-' && is_space_separator(following))) {
            push_chunk(chunks, start, position);
            push_chunk(chunks, skip_space_separators(read, text, position + (code == '-' ? 1 : 2)), end);
            break;
        }
    }
    for (size_t position = start; position < end; position++) {
        if (is_wide_hyphen(read(text, position))) {
            push_chunk(chunks, start, position);
            push_chunk(chunks, skip_space_separators(read, text, position + 1), end);
            break;
        }
    }
    size_t chunk_start = NO_POSITION;
    for (size_t position = start; position < end;) {
        if (read(text, position) != '.') {
            position++;
            continue;
        }
        push_chunk(chunks, chunk_start == NO_POSITION ? start : chunk_start, position);
        while (read(text, position) == '.') {
            position++;
        }
        chunk_start = skip_space_separators(read, text, position);
        position = chunk_start;
    }
    if (chunk_start != NO_POSITION) {
        push_chunk(chunks, chunk_start, end);
    }
    chunk_start = NO_POSITION;
    for (size_t position = start; position < end;) {
        if (!is_space_separator(read(text, position))) {
            position++;
            continue;
        }
        push_chunk(chunks, chunk_start == NO_POSITION ? start : chunk_start, position);
        chunk_start = skip_space_separators(read, text, position);
        position = chunk_start;
    }
    if (chunk_start != NO_POSITION) {
        push_chunk(chunks, chunk_start, end);
    }
}

/* PhoneNumberMatcher.PUB_PAGES on the chunk: "pages 1-5 (3 pages)", ASCII digits, hyphens, digits, up to four
   spaces and a bracketed count, is not a number. Its \d and \s are ASCII, and of Java's \s only the space is
   punctuation a candidate can hold. */
static int is_pub_pages(th_phone_read read, const void *text, const run_record *run, size_t first, size_t last,
                        size_t chunk_end) {
    for (size_t index = first; index + 1 < last; index++) {
        const group_record *left = &run->groups[index];
        const group_record *right = &run->groups[index + 1];
        if (right->count > 5 || read(text, left->end - 1) >= 0x80) {
            continue;
        }
        int hyphens_only = 1;
        for (size_t position = left->end; position < right->start; position++) {
            hyphens_only &= read(text, position) == '-';
        }
        int ascii_digits = 1;
        for (size_t position = right->start; position < right->end; position++) {
            ascii_digits &= read(text, position) < 0x80;
        }
        if (!hyphens_only || !ascii_digits) {
            continue;
        }
        /* a separator holds at most four characters, so fewer than the regex's four spaces can precede the bracket */
        size_t probe = right->end;
        while (probe < chunk_end && read(text, probe) == ' ') {
            probe++;
        }
        if (probe + 1 < chunk_end && read(text, probe) == '(' && read(text, probe + 1) < 0x80 &&
            th_phone_digit_value(read(text, probe + 1)) >= 0) {
            return 1;
        }
    }
    return 0;
}

static int is_hex_digit(uint32_t code) {
    return (code >= '0' && code <= '9') || (code | 0x20) - 'a' < 6;
}

static int is_address_chain_char(uint32_t code) {
    return is_hex_digit(code) || code == ':' || code == '[' || code == ']';
}

/* A bracketed IPv6 literal with a port is 47 characters; a longer chain of address characters is prose. */
#define MAX_ADDRESS_CHARS 48

/* RFC 4291's text form without an IPv4 tail, bare or in brackets with a port: up to eight hextets of one to four hex
   digits, `::` at most once and standing for one hextet or more, no lone colon at either end. The chain holds the
   candidate's digits, so a colon at its start always has a character after it. */
static int is_ipv6_literal(th_phone_read read, const void *text, size_t left, size_t right) {
    size_t position = left;
    int bracketed = read(text, position) == '[';
    position += (size_t)bracketed;
    if (read(text, position) == ':' && read(text, position + 1) != ':') {
        return 0;
    }
    size_t hextets = 0;
    size_t digits = 0;
    size_t colon_run = 0;
    int gap = 0;
    for (; position < right; position++) {
        uint32_t code = read(text, position);
        if (is_hex_digit(code)) {
            colon_run = 0;
            digits++;
            if (digits > 4) {
                return 0;
            }
            continue;
        }
        if (code != ':') {
            break;
        }
        hextets += digits > 0;
        digits = 0;
        colon_run++;
        if (colon_run == 2 && gap) {
            return 0;
        }
        gap |= colon_run == 2;
        if (colon_run > 2) {
            return 0;
        }
    }
    hextets += digits > 0;
    if (colon_run == 1) {
        return 0;
    }
    if (gap && hextets > 7) {
        return 0;
    }
    if (!gap && hextets != 8) {
        return 0;
    }
    if (!bracketed) {
        return position == right;
    }
    if (position >= right) {
        return 0;
    }
    if (read(text, position) != ']') {
        return 0;
    }
    position++;
    if (position == right) {
        return 1;
    }
    if (read(text, position) != ':') {
        return 0;
    }
    size_t port = 0;
    for (position++; position < right && th_phone_digit_value(read(text, position)) >= 0; position++) {
        port++;
    }
    if (port == 0) {
        return 0;
    }
    if (port > 5) {
        return 0;
    }
    return position == right;
}

/* Does the candidate sit inside an IPv6 literal (`2001:db8::8888`, `[::1]:8080`), whose hextets and port would
   otherwise offer themselves as numbers? The chain of address characters around it is read as one, so a chain past
   the length of any literal, or one that only looks like hextets (`6502530000:6502530000:1`), is not an address. */
static int in_address_chain(th_phone_read read, const void *text, size_t len, size_t start, size_t end) {
    if (end - start > MAX_ADDRESS_CHARS) {
        return 0;
    }
    size_t left = start;
    while (left > 0 && start - left < MAX_ADDRESS_CHARS && is_address_chain_char(read(text, left - 1))) {
        left--;
    }
    size_t right = end;
    while (right < len && right - end < MAX_ADDRESS_CHARS && is_address_chain_char(read(text, right))) {
        right++;
    }
    int overflows = (left > 0 && is_address_chain_char(read(text, left - 1))) +
                    (right < len && is_address_chain_char(read(text, right)));
    return overflows == 0 && right - left > end - start && is_ipv6_literal(read, text, left, right);
}

static int blocked_by_neighbors(th_phone_read read, const void *text, size_t len, const th_phone_config *config,
                                size_t start, size_t end) {
    uint32_t before = start > 0 ? read(text, start - 1) : 0;
    uint32_t after = end < len ? read(text, end) : 0;
    if (before == '@' || after == '@' || in_address_chain(read, text, len, start, end)) {
        return 1;
    }
    if (!config->require_valid) {
        return 0;
    }
    uint32_t first = read(text, start);
    int leads = is_plus(first) || is_opener(first);
    if (before && !leads && (is_latin_letter(before) || is_invalid_punctuation(before))) {
        return 1;
    }
    return after && (is_latin_letter(after) || is_invalid_punctuation(after));
}

/* containsOnlyValidXChars at VALID: `xx` precedes a carrier code and the digits after it are the number itself; a
   lone `x` precedes the extension and the digits after it are the extension. Only the lowercase `x` is punctuation a
   run allows, so no other spelling reaches the chunk. */
static int x_rules_hold(th_phone_read read, const void *text, const th_phone_config *config, size_t start, size_t end,
                        const reading *result, const char *ext, size_t ext_len) {
    if (!config->require_valid) {
        return 1;
    }
    for (size_t position = start; position + 1 < end; position++) {
        if (read(text, position) != 'x') {
            continue;
        }
        int carrier = read(text, position + 1) == 'x';
        digit_string after;
        after.len = 0;
        for (size_t scan = position + (carrier ? 2 : 1); scan < end; scan++) {
            int value = th_phone_digit_value(read(text, scan));
            if (value >= 0) {
                after.data[after.len++] = (char)('0' + value);
            }
        }
        /* after a carrier code the digits are the number's, read the way isNumberMatch reads them: with the
           extension's own digits behind them */
        const char *expected = carrier ? result->nsn.data : ext;
        size_t expected_len = carrier ? result->nsn.len + ext_len : ext_len;
        /* GCOVR_EXCL_BR_START: the digits after the marker are a suffix of the chunk's own digits, so equal lengths
           imply equal digits and memcmp never decides */
        if (after.len != expected_len || memcmp(after.data, expected, expected_len - (carrier ? ext_len : 0)) != 0) {
            return 0;
        }
        /* GCOVR_EXCL_BR_STOP */
        position += (size_t)carrier;
    }
    return 1;
}

typedef struct {
    char data[TH_PHONE_FORMAT_CAPACITY];
    size_t len;
} text_buffer;

static void append_text(text_buffer *buffer, const char *text, size_t len) {
    memcpy(buffer->data + buffer->len, text, len);
    buffer->len += len;
}

static void append_string(text_buffer *buffer, const char *text) {
    while (*text != '\0') {
        buffer->data[buffer->len++] = *text++;
    }
}

/* Matcher.replaceAll(template) over the digits: each match splits the digits into the format's groups the way
   Java's backtracking does, every group taking the most digits that still leave the later groups their minimum,
   and the digits no further match covers pass through. A format the chooser picked fits, so one match takes all. */
static void format_digits(const th_phone_format *format, const char *template, const char *digits, size_t len,
                          text_buffer *out) {
    size_t minimum = 0;
    for (size_t index = 0; index < format->group_count; index++) {
        minimum += format->groups[index] >> 4;
    }
    size_t position = 0;
    while (len - position >= minimum) {
        size_t starts[TH_PHONE_FORMAT_GROUPS + 1];
        size_t cursor = position;
        size_t later = minimum;
        for (size_t index = 0; index < format->group_count; index++) {
            later -= format->groups[index] >> 4;
            size_t take = format->groups[index] & 0xFu;
            if (take > len - cursor - later) {
                take = len - cursor - later;
            }
            starts[index] = cursor;
            cursor += take;
        }
        starts[format->group_count] = cursor;
        for (const char *at = template; *at != '\0'; at++) {
            if (*at == '$') {
                size_t group = (size_t)(at[1] - '1');
                append_text(out, digits + starts[group], starts[group + 1] - starts[group]);
                at++;
            } else {
                append_text(out, at, 1);
            }
        }
        position = cursor;
    }
    append_text(out, digits + position, len - position);
}

static int is_template_separator(char byte) {
    static const char separators[] = {' ', '-', '.', '(', ')'};
    return memchr(separators, byte, sizeof separators) != NULL;
}

/* RFC 3966's rewrite of a formatted national number: the leading separators go, each other separator run becomes
   one hyphen. No template ends on a separator, so no run is pending at the end. */
static void hyphenate(text_buffer *buffer) {
    size_t written = 0;
    int pending = 0;
    for (size_t index = 0; index < buffer->len; index++) {
        if (is_template_separator(buffer->data[index])) {
            pending = written > 0;
            continue;
        }
        if (pending) {
            buffer->data[written++] = '-';
            pending = 0;
        }
        buffer->data[written++] = buffer->data[index];
    }
    buffer->len = written;
}

static size_t write_country_code(unsigned country_code, char *out) {
    size_t len = 0;
    char digits[4];
    do {
        digits[len++] = (char)('0' + country_code % 10);
        country_code /= 10;
    } while (country_code);
    for (size_t index = 0; index < len; index++) {
        out[index] = digits[len - 1 - index];
    }
    return len;
}

/* The candidate as the matcher normalizes it for the grouping checks: every digit an ASCII digit, every other code
   point kept. A run may reach TH_PHONE_MAX_RUN_CHARS from its first digit and then hold one more group, after its
   lead characters and before an extension; a chunk that read as a number is far shorter, its digits being a country
   code, a prefix and a national number. */
#define CANDIDATE_CAPACITY (MAX_LEAD_CHARS + TH_PHONE_MAX_RUN_CHARS + TH_PHONE_MAX_GROUP_DIGITS + 128)

typedef struct {
    uint32_t data[CANDIDATE_CAPACITY];
    size_t len;
} candidate_text;

static void normalize_candidate(th_phone_read read, const void *text, size_t start, size_t end,
                                candidate_text *candidate) {
    candidate->len = 0;
    for (size_t position = start; position < end; position++) {
        uint32_t code = read(text, position);
        int value = th_phone_digit_value(code);
        candidate->data[candidate->len++] = value >= 0 ? (uint32_t)('0' + value) : code;
    }
}

/* Callers keep `at + needle_len` inside the candidate: the search loop by its bound, the group checks by their
   own. */
static int candidate_has_at(const candidate_text *candidate, size_t at, const char *needle, size_t needle_len) {
    for (size_t index = 0; index < needle_len; index++) {
        if (candidate->data[at + index] != (uint32_t)(unsigned char)needle[index]) {
            return 0;
        }
    }
    return 1;
}

static int candidate_find(const candidate_text *candidate, const char *needle, size_t needle_len, size_t from) {
    for (size_t at = from; at + needle_len <= candidate->len; at++) {
        if (candidate_has_at(candidate, at, needle, needle_len)) {
            return (int)at;
        }
    }
    return -1;
}

/* The candidate's digit runs the way NON_DIGITS_PATTERN.split cuts them: a run of non-digits ends one, a candidate
   that starts with a bracket has an empty run first, and Java's split drops the empty run a trailing `#` would
   leave. A chunk holds at most TH_PHONE_MAX_GROUPS groups and one extension. */
static size_t candidate_runs(const candidate_text *candidate, size_t runs[][2]) {
    size_t count = 0;
    size_t index = 0;
    while (index < candidate->len) {
        size_t start = index;
        while (index < candidate->len && th_phone_digit_value(candidate->data[index]) >= 0) {
            index++;
        }
        runs[count][0] = start;
        runs[count][1] = index - start;
        count++;
        while (index < candidate->len && th_phone_digit_value(candidate->data[index]) < 0) {
            index++;
        }
    }
    return count;
}

/* containsMoreThanOneSlashInNationalNumber: two slashes make a date or a fraction, unless the first one only sets
   off the country code the candidate wrote. A reading that carries its country code starts on those digits, so the
   digits before the first slash are the code whenever there are as many of them. */
static int more_than_one_slash(const candidate_text *candidate, const reading *result) {
    int first = candidate_find(candidate, "/", 1, 0);
    if (first < 0) {
        return 0;
    }
    int second = candidate_find(candidate, "/", 1, (size_t)first + 1);
    if (second < 0) {
        return 0;
    }
    if (result->source == SOURCE_PLUS || result->source == SOURCE_OWN_CODE) {
        char code[4];
        size_t before = 0;
        for (size_t index = 0; index < (size_t)first; index++) {
            before += th_phone_digit_value(candidate->data[index]) >= 0;
        }
        if (before == write_country_code(result->country_code, code)) {
            return candidate_find(candidate, "/", 1, (size_t)second + 1) >= 0;
        }
    }
    return 1;
}

typedef struct {
    const char *text;
    size_t len;
} text_span;

/* The digit groups of the national number written with `format` (bare when NULL), the way getNationalNumberGroups
   reads them off the RFC 3966 form: the runs between hyphens. Each group holds a digit, so there are at most
   TH_PHONE_MAX_NSN. */
static size_t formatted_groups(const th_phone_format *format, const char *template, const reading *result,
                               text_buffer *body, text_span *groups) {
    body->len = 0;
    if (format == NULL) {
        append_text(body, result->nsn.data, result->nsn.len);
    } else {
        format_digits(format, template, result->nsn.data, result->nsn.len, body);
        hyphenate(body);
    }
    size_t count = 0;
    size_t start = 0;
    for (size_t index = 0; index <= body->len; index++) {
        if (index == body->len || body->data[index] == '-') {
            groups[count].text = body->data + start;
            groups[count].len = index - start;
            count++;
            start = index + 1;
        }
    }
    return count;
}

/* allNumberGroupsRemainGrouped: each formatted group occurs in the candidate in order, after the country code when
   the candidate wrote one; a digit right after the first group in a plan with a national prefix means the rest was
   written unbroken, which passes only when the candidate carries the whole national number from that group on. */
static int groups_remain_grouped(const reading *result, const candidate_text *candidate, const text_span *groups,
                                 size_t count, const char *ext, size_t ext_len) {
    int from = 0;
    if (result->source != SOURCE_DEFAULT_REGION) {
        char code[4];
        size_t code_len = write_country_code(result->country_code, code);
        from = candidate_find(candidate, code, code_len, 0) + (int)code_len;
    }
    for (size_t index = 0; index < count; index++) {
        from = candidate_find(candidate, groups[index].text, groups[index].len, (size_t)from);
        if (from < 0) {
            return 0;
        }
        from += (int)groups[index].len;
        if (index == 0 && (size_t)from < candidate->len) {
            const th_phone_region *main = &th_phone_regions[th_phone_groups[result->group].main];
            if (main->has_ndd && th_phone_digit_value(candidate->data[from]) >= 0) {
                /* a prefix transform can make the national number longer than the candidate's tail */
                size_t nsn_start = (size_t)from - groups[0].len;
                return nsn_start + result->nsn.len <= candidate->len &&
                       candidate_has_at(candidate, nsn_start, result->nsn.data, result->nsn.len);
            }
        }
    }
    return candidate_find(candidate, ext, ext_len, (size_t)from) >= 0;
}

/* allNumberGroupsAreExactlyPresent: the candidate's digit runs, read backwards from the number's last one, equal
   the formatted groups, and the run holding the first group may end with it (a country code or national prefix
   written onto it). A run that holds the whole national number passes as written. */
static int groups_exactly_present(const reading *result, const candidate_text *candidate, const text_span *groups,
                                  size_t count, size_t ext_len) {
    /* zeroed because gcc cannot see that a candidate always holds digits, so the splitter always writes a run */
    size_t runs[TH_PHONE_MAX_GROUPS + 3][2] = {{0}};
    size_t run_count = candidate_runs(candidate, runs);
    int at = (int)run_count - (ext_len > 0 ? 2 : 1);
    if (run_count == 1) {
        return 1;
    }
    for (size_t offset = 0; offset + result->nsn.len <= runs[at][1]; offset++) {
        if (candidate_has_at(candidate, runs[at][0] + offset, result->nsn.data, result->nsn.len)) {
            return 1;
        }
    }
    size_t formatted = count - 1;
    /* no plan's prefix transform writes more than the first group, so the runs do not run out before the groups;
       the run bound stays as part of one test so a future plan cannot step outside the array */
    while ((formatted > 0) & (at >= 0)) {
        if (runs[at][1] != groups[formatted].len ||
            !candidate_has_at(candidate, runs[at][0], groups[formatted].text, groups[formatted].len)) {
            return 0;
        }
        formatted--;
        at--;
    }
    if (at < 0 || runs[at][1] < groups[0].len) {
        return 0;
    }
    return candidate_has_at(candidate, runs[at][0] + runs[at][1] - groups[0].len, groups[0].text, groups[0].len);
}

static int groups_hold(const th_phone_config *config, const reading *result, const candidate_text *candidate,
                       const text_span *groups, size_t count, const char *ext, size_t ext_len) {
    if (config->grouping == TH_PHONE_GROUPING_STRICT) {
        return groups_remain_grouped(result, candidate, groups, count, ext, ext_len);
    }
    return groups_exactly_present(result, candidate, groups, count, ext_len);
}

/* checkNumberGroupingIsValid's order: the number's own format first, then each alternate format of its calling
   code whose leading digits it matches. */
static int grouping_holds(th_phone_read read, const void *text, const th_phone_config *config, size_t start, size_t end,
                          const reading *result, const char *ext, size_t ext_len) {
    if (config->grouping == TH_PHONE_GROUPING_ANY) {
        return 1;
    }
    candidate_text candidate;
    normalize_candidate(read, text, start, end, &candidate);
    if (more_than_one_slash(&candidate, result)) {
        return 0;
    }
    const th_phone_group *group = &th_phone_groups[result->group];
    const th_phone_region *main = &th_phone_regions[group->main];
    const th_phone_format *format =
        choose_format(&th_phone_formats[main->format_first], main->format_count, result->nsn.data, result->nsn.len, 1);
    text_buffer body;
    text_span groups[TH_PHONE_MAX_NSN + 1];
    size_t count =
        formatted_groups(format, format == NULL ? NULL : th_phone_templates + format->intl, result, &body, groups);
    if (groups_hold(config, result, &candidate, groups, count, ext, ext_len)) {
        return 1;
    }
    for (size_t index = 0; index < group->alt_count; index++) {
        const th_phone_format *alternate = &th_phone_alt_formats[group->alt_first + index];
        if (!dfa_looking_at(alternate->leading, result->nsn.data, result->nsn.len)) {
            continue;
        }
        count = formatted_groups(alternate, th_phone_templates + alternate->national, result, &body, groups);
        if (groups_hold(config, result, &candidate, groups, count, ext, ext_len)) {
            return 1;
        }
    }
    return 0;
}

typedef struct {
    reading result;
    size_t end;
    char ext[TH_PHONE_MAX_EXTENSION + 1];
    uint8_t ext_len;
} chunk_match;

/* parseAndVerify on one chunk: a plus reads internationally, otherwise each configured region in turn. */
static int read_chunk(th_phone_read read, const void *text, size_t len, const th_phone_config *config,
                      const run_record *run, size_t first_group, size_t end_group, size_t start, size_t end,
                      chunk_match *found) {
    size_t first = first_group;
    while (first < end_group && run->groups[first].start < start) {
        first++;
    }
    size_t last = end_group;
    while (last > first && run->groups[last - 1].end > end) {
        last--;
    }
    if (first == last) {
        return 0;
    }
    found->ext_len = 0;
    found->end = run->groups[last - 1].end;
    if (run->extension_len && last == run->group_count && end >= run->extension_end) {
        memcpy(found->ext, run->extension, run->extension_len);
        found->ext_len = run->extension_len;
        found->end = run->extension_end;
    } else if (last - first >= 2 && run->groups[last - 1].separator_has_extension_marker) {
        size_t tail_end;
        char ext_digits[TH_PHONE_MAX_EXTENSION + 1];
        uint8_t ext_len;
        if (walk_extension(read, text, len, extension_dfa(config), run->groups[last - 2].end, &tail_end, ext_digits,
                           &ext_len) &&
            tail_end >= found->end) {
            memcpy(found->ext, ext_digits, ext_len);
            found->ext_len = ext_len;
            last--;
        }
    }
    if (!brackets_match(read, text, start, found->end) || is_pub_pages(read, text, run, first, last, found->end) ||
        blocked_by_neighbors(read, text, len, config, start, found->end)) {
        return 0;
    }
    int plus = 0;
    int gap = 0;
    for (size_t position = start; position < run->groups[first].start; position++) {
        if (is_plus(read(text, position))) {
            /* VALID_PHONE_NUMBER takes plus signs only at the start, so `+ +1` is not a viable number */
            if (gap) {
                return 0;
            }
            plus = 1;
        } else if (plus) {
            gap = 1;
        }
    }
    const char *digits = run->digits + run->groups[first].digits_offset;
    size_t digit_count =
        run->groups[last - 1].digits_offset + run->groups[last - 1].count - run->groups[first].digits_offset;
    found->result.found = 0;
    if (plus && read_international(config, digits, digit_count, &found->result)) {
        found->result.source = SOURCE_PLUS;
    } else {
        /* after a plus that led to no calling code, parseHelper drops the plus and lets the default region's
           international prefix or own code read the digits; it never reads them as a national number */
        enum reading_mode mode = plus                                              ? READ_WITH_CODE
                                 : config->require_separators && last - first == 1 ? READ_IDD
                                                                                   : READ_ANY;
        for (size_t position = 0; position < config->region_count && !found->result.found; position++) {
            read_national(config, config->regions[position], digits, digit_count, mode, &found->result);
        }
    }
    return found->result.found &&
           x_rules_hold(read, text, config, start, found->end, &found->result, found->ext, found->ext_len) &&
           grouping_holds(read, text, config, start, found->end, &found->result, found->ext, found->ext_len);
}

int th_phone_find(th_phone_read read, const void *text, size_t len, size_t left_bound, size_t digit_pos,
                  const th_phone_config *config, th_phone_match *match, size_t *retry) {
    run_record run;
    if (!segment(read, text, len, extension_dfa(config), left_bound, digit_pos, &run)) {
        size_t end = digit_pos;
        while (end < len && th_phone_digit_value(read(text, end)) >= 0) {
            end++;
        }
        *retry = end;
        return 0;
    }
    *retry = run.second_number_cut > 0 ? run.second_number_cut : run.extension_end;
    if (!run.plus && config->region_count == 0) {
        return 0;
    }
    if (run.digit_count < (run.plus ? 3 : config->national_floor)) {
        return 0;
    }
    poison(read, text, len, config, &run);
    chunk_list chunks;
    chunk_match found;
    size_t first = 0;
    while (first < run.group_count) {
        if (run.poison >> first & 1) {
            first++;
            continue;
        }
        size_t end_group = first;
        while (end_group < run.group_count && !(run.poison >> end_group & 1)) {
            end_group++;
        }
        size_t start = first == 0 ? run.start : run.groups[first].start;
        size_t end = end_group == run.group_count ? run.extension_end : run.groups[end_group - 1].end;
        enumerate_chunks(read, text, start, end, &chunks);
        for (size_t index = 0; index < chunks.count; index++) {
            const text_range *range = &chunks.ranges[index];
            if (!read_chunk(read, text, len, config, &run, first, end_group, range->start, range->end, &found)) {
                continue;
            }
            match->start = range->start;
            match->end = found.end;
            match->country_code = found.result.country_code;
            match->nsn_len = (uint8_t)found.result.nsn.len;
            memcpy(match->nsn, found.result.nsn.data, found.result.nsn.len);
            match->nsn[found.result.nsn.len] = '\0';
            match->ext_len = found.ext_len;
            memcpy(match->ext, found.ext, found.ext_len);
            match->ext[found.ext_len] = '\0';
            match->region = found.result.region;
            match->type = found.result.type;
            *retry = found.end > run.second_number_cut ? found.end : run.second_number_cut;
            return 1;
        }
        first = end_group;
    }
    return 0;
}

static int text_has_at(th_phone_read read, const void *text, size_t at, size_t end, const char *needle) {
    for (size_t index = 0; needle[index] != '\0'; index++) {
        if (at + index >= end || read(text, at + index) != (uint32_t)(unsigned char)needle[index]) {
            return 0;
        }
    }
    return 1;
}

/* `end` when absent; RFC 3966 parameter names are lowercase, so the match does not fold case. */
static size_t find_parameter(th_phone_read read, const void *text, size_t start, size_t end, const char *needle) {
    for (size_t position = start; position < end; position++) {
        if (read(text, position) == ';' && text_has_at(read, text, position, end, needle)) {
            return position;
        }
    }
    return end;
}

/* RFC 3966's visual separators, the only marks a global number's digits may carry. */
static int is_visual_separator(uint32_t code) {
    static const char marks[] = "-.()";
    return code < 0x80 && memchr(marks, (int)code, sizeof marks - 1) != NULL;
}

static int is_ascii_letter(uint32_t code) {
    return (code | 0x20) - 'a' < 26;
}

/* RFC 3966's domainname as libphonenumber checks it: labels of ASCII letters and digits with hyphens inside them,
   joined by single dots, the last label starting with a letter, one dot allowed after it. */
static int is_rfc_domain(th_phone_read read, const void *text, size_t start, size_t end) {
    size_t position = start;
    int top_starts_with_letter = 0;
    while (position < end) {
        uint32_t code = read(text, position);
        int letter = is_ascii_letter(code);
        if (!letter && th_phone_digit_value(code) < 0) {
            return 0;
        }
        top_starts_with_letter = letter;
        while (position < end && is_ascii_letter(code) + (th_phone_digit_value(code) >= 0) + (code == '-') > 0) {
            position++;
            if (position < end) {
                code = read(text, position);
            }
        }
        if (read(text, position - 1) == '-') {
            return 0;
        }
        if (position == end) {
            break;
        }
        if (code != '.') {
            return 0;
        }
        position++;
    }
    return top_starts_with_letter;
}

static uint32_t read_candidate(const void *text, size_t index) {
    return ((const candidate_text *)text)->data[index];
}

/* 1 with a global number (`+49`) in `prefix`, whose digits go before the local number; 0 with a domain, under
   which the digits read as a national number; -1 otherwise, and the whole text is no number. */
static int context_prefix(th_phone_read read, const void *text, size_t value, size_t end, candidate_text *prefix) {
    size_t value_end = value;
    while (value_end < end && read(text, value_end) != ';') {
        value_end++;
    }
    prefix->len = 0;
    if (value == value_end) {
        return -1;
    }
    if (!is_plus(read(text, value))) {
        return is_rfc_domain(read, text, value, value_end) ? 0 : -1;
    }
    prefix->data[prefix->len++] = '+';
    for (size_t position = value + 1; position < value_end; position++) {
        uint32_t code = read(text, position);
        int digit = th_phone_digit_value(code);
        if (digit >= 0) {
            /* a calling code and a national number bound the digits a context can lend a number */
            if (prefix->len > 3 + TH_PHONE_MAX_NSN) {
                return -1;
            }
            prefix->data[prefix->len++] = (uint32_t)('0' + digit);
        } else if (!is_visual_separator(code)) {
            return -1;
        }
    }
    return prefix->len > 1 ? 1 : -1;
}

/* MAX_INPUT_STRING_LENGTH: parse refuses a longer string before reading any of it. */
#define MAX_PARSE_CHARS 250

/* UNWANTED_END_CHAR_PATTERN: a trailing character that is neither a number, a letter nor `#`. */
static int is_unwanted_end(uint32_t code) {
    return !is_number_mark(code) && !is_letter(code) && code != '#';
}

/* buildNationalNumberForParsing: with a `phone-context`, the text after the first `tel:` up to the parameter, behind
   the context's calling code when it has one; otherwise the text from the first plus or digit, less the unwanted
   end characters and anything from a second number's `/x`. `;isub=` ends either. Returns 0 when the context is no
   calling code and no domain. */
static int number_to_parse(th_phone_read read, const void *text, size_t start, size_t end, candidate_text *number) {
    number->len = 0;
    size_t context = find_parameter(read, text, start, end, ";phone-context=");
    size_t last = context;
    size_t first = start;
    if (context < end) {
        if (context_prefix(read, text, context + 15, end, number) < 0) {
            return 0;
        }
        for (size_t position = start; position + 4 <= context; position++) {
            if (text_has_at(read, text, position, context, "tel:")) {
                first = position + 4;
                break;
            }
        }
    } else {
        while (first < end && !is_plus(read(text, first)) && th_phone_digit_value(read(text, first)) < 0) {
            first++;
        }
        while (last > first && is_unwanted_end(read(text, last - 1))) {
            last--;
        }
        for (size_t position = first; position < last; position++) {
            uint32_t code = read(text, position);
            size_t cut;
            if ((code == '/' || code == '\\') && second_number_start(read, text, last, position, &cut)) {
                last = position;
            }
        }
    }
    for (size_t position = first; position < last; position++) {
        number->data[number->len++] = read(text, position);
    }
    size_t isub = find_parameter(read_candidate, number, 1, number->len, ";isub=");
    if (isub < number->len) {
        number->len = isub;
    }
    return 1;
}

/* VALID_PHONE_NUMBER_PATTERN and maybeStripExtension on the text after its plus signs: punctuation, digits and
   ASCII letters, three digits before the first letter, and at the end an extension in a written form, whose start
   is the number's end. Returns that end, or 0 when the text is no number. */
static size_t viable_number_end(const th_phone_config *config, const candidate_text *number, size_t start,
                                char *ext_digits, uint8_t *ext_len) {
    *ext_len = 0;
    size_t digits = 0;
    size_t third_digit = 0;
    size_t end = number->len;
    for (size_t position = start; position < number->len; position++) {
        uint32_t code = number->data[position];
        if (th_phone_digit_value(code) >= 0) {
            digits++;
            third_digit = digits == 3 ? position + 1 : third_digit;
        } else if (!is_punctuation(code) && !(digits >= 3 && is_ascii_letter(code))) {
            end = position;
            break;
        }
    }
    if (digits < 3) {
        return 0;
    }
    for (size_t position = third_digit; position <= end; position++) {
        size_t tail_end;
        if (walk_extension(read_candidate, number, number->len, extension_dfa(config), position, &tail_end, ext_digits,
                           ext_len) &&
            tail_end == number->len) {
            return position;
        }
    }
    *ext_len = 0;
    return end == number->len ? end : 0;
}

/* The keypad digit of a vanity letter, ALPHA_MAPPINGS. */
static char vanity_digit(uint32_t code) {
    static const char keypad[] = "22233344455566677778889999";
    return keypad[(code | 0x20) - 'a'];
}

/* parse's normalize: with three or more letters the text is a vanity number and each letter is its keypad digit;
   with fewer, the letters go the way of everything else that is not a digit. */
static void normalize_number(const candidate_text *number, size_t start, size_t end, digit_string *digits) {
    size_t letters = 0;
    for (size_t position = start; position < end; position++) {
        letters += is_ascii_letter(number->data[position]);
    }
    digits->len = 0;
    for (size_t position = start; position < end; position++) {
        uint32_t code = number->data[position];
        int value = th_phone_digit_value(code);
        if (value >= 0) {
            digits->data[digits->len++] = (char)('0' + value);
        } else if (letters >= 3 && is_ascii_letter(code)) {
            digits->data[digits->len++] = vanity_digit(code);
        }
    }
}

int th_phone_parse(th_phone_read read, const void *text, size_t start, size_t end, const th_phone_config *config,
                   th_phone_match *match) {
    candidate_text number;
    if (end - start > MAX_PARSE_CHARS || !number_to_parse(read, text, start, end, &number)) {
        return 0;
    }
    size_t plus_end = 0;
    while (plus_end < number.len && is_plus(number.data[plus_end])) {
        plus_end++;
    }
    char ext_digits[TH_PHONE_MAX_EXTENSION + 1];
    uint8_t ext_len;
    size_t number_end = viable_number_end(config, &number, plus_end, ext_digits, &ext_len);
    if (number_end == 0) {
        return 0;
    }
    digit_string digits;
    normalize_number(&number, plus_end, number_end, &digits);
    reading result;
    result.found = 0;
    if (plus_end == 0 || !read_international(config, digits.data, digits.len, &result)) {
        /* parseHelper's retry after a plus that led to no calling code: the region's international prefix or own
           code may still read the digits, never a national number */
        enum reading_mode mode = plus_end > 0 ? READ_WITH_CODE : READ_ANY;
        for (size_t position = 0; position < config->region_count && !result.found; position++) {
            read_national(config, config->regions[position], digits.data, digits.len, mode, &result);
        }
    }
    if (!result.found) {
        return 0;
    }
    match->start = start;
    match->end = end;
    match->country_code = result.country_code;
    match->nsn_len = (uint8_t)result.nsn.len;
    memcpy(match->nsn, result.nsn.data, result.nsn.len);
    match->nsn[result.nsn.len] = '\0';
    match->ext_len = ext_len;
    memcpy(match->ext, ext_digits, ext_len);
    match->ext[ext_len] = '\0';
    match->region = result.region;
    match->type = result.type;
    return 1;
}

void th_phone_config_floor(th_phone_config *config) {
    uint8_t floor = 0;
    for (size_t index = 0; index < config->region_count; index++) {
        const th_phone_region *region = &th_phone_regions[config->regions[index]];
        uint8_t candidate = config->require_valid ? region->floor_valid : region->floor_possible;
        if (index == 0 || candidate < floor) {
            floor = candidate;
        }
    }
    config->national_floor = floor;
}

int th_phone_region_index(const char *code, size_t len) {
    for (size_t index = 0; index < TH_PHONE_REGION_COUNT; index++) {
        const th_phone_region *region = &th_phone_regions[index];
        if (region->code_len == len && memcmp(region->code, code, len) == 0) {
            return (int)index;
        }
    }
    return -1;
}

const char *th_phone_region_code(int index, size_t *len) {
    const th_phone_region *region = &th_phone_regions[index];
    *len = region->code_len;
    return region->code;
}

static int group_of_code_value(unsigned country_code) {
    for (size_t index = 0; index < TH_PHONE_GROUP_COUNT; index++) {
        if (th_phone_groups[index].country_code == country_code) {
            return (int)index;
        }
    }
    return -1;
}

enum th_phone_check th_phone_number_check(unsigned country_code, const char *nsn, size_t nsn_len, const char *region,
                                          size_t region_len, int type) {
    int group_index = group_of_code_value(country_code);
    if (group_index < 0) {
        return TH_PHONE_CHECK_COUNTRY_CODE;
    }
    const th_phone_group *group = &th_phone_groups[group_index];
    int region_index = NO_REGION;
    if (region != NULL) {
        for (size_t position = 0; position < group->count; position++) {
            uint16_t index = th_phone_group_regions[group->first + position] & 0x7FFFu;
            const th_phone_region *member = &th_phone_regions[index];
            if (member->code_len == region_len && memcmp(member->code, region, region_len) == 0) {
                region_index = index;
            }
        }
        if (region_index == NO_REGION) {
            return TH_PHONE_CHECK_REGION;
        }
    }
    digit_string capped;
    copy_digits(&capped, nsn, nsn_len);
    cap_leading_zeros(&capped);
    if (capped.len != nsn_len) {
        return TH_PHONE_CHECK_NUMBER;
    }
    uint16_t word;
    uint16_t mask;
    int routed = route(group, nsn, nsn_len, &word, &mask);
    if (type == TH_PHONE_UNKNOWN) {
        const th_phone_region *main = &th_phone_regions[group->main];
        uint32_t lengths = main->possible_national | main->possible_local_only;
        int possible = lengths >> nsn_len & 1;
        return possible && routed == region_index ? TH_PHONE_CHECK_OK : TH_PHONE_CHECK_NUMBER;
    }
    if (routed == NO_REGION || routed != region_index || !(word & GENERAL_BIT) || resolve_type(mask) != type) {
        return TH_PHONE_CHECK_NUMBER;
    }
    return TH_PHONE_CHECK_OK;
}

size_t th_phone_format_number(unsigned country_code, const char *nsn, size_t nsn_len, const char *ext, size_t ext_len,
                              enum th_phone_style style, char *out) {
    text_buffer result;
    result.len = 0;
    if (style == TH_PHONE_STYLE_E164) {
        append_text(&result, "+", 1);
        result.len += write_country_code(country_code, result.data + result.len);
        append_text(&result, nsn, nsn_len);
        memcpy(out, result.data, result.len);
        return result.len;
    }
    int group_index = group_of_code_value(country_code);
    if (group_index < 0) {
        memcpy(out, nsn, nsn_len);
        return nsn_len;
    }
    const th_phone_region *main = &th_phone_regions[th_phone_groups[group_index].main];
    int intl = style != TH_PHONE_STYLE_NATIONAL;
    const th_phone_format *format =
        choose_format(&th_phone_formats[main->format_first], main->format_count, nsn, nsn_len, intl);
    text_buffer body;
    body.len = 0;
    if (format == NULL) {
        append_text(&body, nsn, nsn_len);
    } else {
        format_digits(format, th_phone_templates + (intl ? format->intl : format->national), nsn, nsn_len, &body);
    }
    char code[4];
    size_t code_len = write_country_code(country_code, code);
    /* RFC 3966 composes a global number from E.164, so a longer one is written as a local number whose context is
       its calling code, the extension first among the parameters as the RFC orders them */
    int global = code_len + nsn_len <= 15;
    if (style == TH_PHONE_STYLE_RFC3966) {
        hyphenate(&body);
        append_text(&result, "tel:", 4);
        if (global) {
            append_text(&result, "+", 1);
            append_text(&result, code, code_len);
            append_text(&result, "-", 1);
        }
    } else if (style == TH_PHONE_STYLE_INTERNATIONAL) {
        append_text(&result, "+", 1);
        result.len += write_country_code(country_code, result.data + result.len);
        append_text(&result, " ", 1);
    }
    append_text(&result, body.data, body.len);
    if (ext_len > 0) {
        if (style == TH_PHONE_STYLE_RFC3966) {
            append_text(&result, ";ext=", 5);
        } else if (main->ext_prefix != 0xFFFF) {
            append_string(&result, th_phone_templates + main->ext_prefix);
        } else {
            append_text(&result, " ext. ", 6);
        }
        append_text(&result, ext, ext_len);
    }
    if (style == TH_PHONE_STYLE_RFC3966 && !global) {
        append_text(&result, ";phone-context=+", 16);
        append_text(&result, code, code_len);
    }
    memcpy(out, result.data, result.len);
    return result.len;
}
