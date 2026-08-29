/* The phone-number recognizer behind linkify's digit trigger.

   A port of tools/phone_model.py: a run of digit groups is segmented, checked for the shapes that are not numbers,
   and then read the way libphonenumber's parseHelper reads it (IDD, the default region's own country code, or a
   national number with its prefix stripped), against numbering plans compiled to DFAs in data/phone_table.h. The
   unit has no CPython dependency: text arrives through a reader callback so tools/fuzz/phone_harness.c can drive it
   under the sanitizers with no interpreter. */
#ifndef TURBOHTML_CLEAN_PHONE_H
#define TURBOHTML_CLEAN_PHONE_H

#include <stddef.h>
#include <stdint.h>

#define TH_PHONE_MAX_REGIONS 8
/* a run's last group starts within this many code points of its first digit */
#define TH_PHONE_MAX_RUN_CHARS 250
#define TH_PHONE_MAX_GROUPS 21
#define TH_PHONE_MAX_GROUP_DIGITS 20
/* the last group may hold TH_PHONE_MAX_GROUP_DIGITS digits past the run limit */
#define TH_PHONE_DIGIT_BUFFER (TH_PHONE_MAX_RUN_CHARS + TH_PHONE_MAX_GROUP_DIGITS)
#define TH_PHONE_MAX_EXTENSION 20
#define TH_PHONE_NSN_CAPACITY 18
/* a formatted number: a template application per group split of at most TH_PHONE_MAX_NSN digits, the calling code,
   the RFC 3966 scheme and an extension */
#define TH_PHONE_FORMAT_CAPACITY 512

enum th_phone_type {
    TH_PHONE_FIXED_LINE = 0,
    TH_PHONE_MOBILE = 1,
    TH_PHONE_TOLL_FREE = 2,
    TH_PHONE_PREMIUM_RATE = 3,
    TH_PHONE_SHARED_COST = 4,
    TH_PHONE_PERSONAL_NUMBER = 5,
    TH_PHONE_VOIP = 6,
    TH_PHONE_PAGER = 7,
    TH_PHONE_UAN = 8,
    TH_PHONE_VOICEMAIL = 9,
    TH_PHONE_FIXED_LINE_OR_MOBILE = 10,
    TH_PHONE_UNKNOWN = 11,
};

/* Leniency.STRICT_GROUPING and EXACT_GROUPING on top of VALID: the written digit groups must follow the number's
   format, or an alternate format of its calling code, loosely or exactly. */
enum th_phone_grouping {
    TH_PHONE_GROUPING_ANY = 0,
    TH_PHONE_GROUPING_STRICT = 1,
    TH_PHONE_GROUPING_EXACT = 2,
};

/* Read the code point at `index`; `text` is whatever the caller handed th_phone_find. */
typedef uint32_t (*th_phone_read)(const void *text, size_t index);

typedef struct {
    const char *text;
    uint8_t len;
} th_phone_label;

typedef struct {
    uint16_t regions[TH_PHONE_MAX_REGIONS]; /* indexes into th_phone_regions, in fallback order, distinct */
    uint8_t region_count;
    uint8_t require_valid;
    uint8_t require_separators;
    uint8_t skip_card_numbers;
    uint8_t require_national_prefix; /* VALID's isNationalPrefixPresentIfRequired applies */
    uint8_t grouping;                /* enum th_phone_grouping */
    uint8_t parsing_extensions;      /* also read the auto-dialling extension forms parse accepts (`,,12`, `;12`) */
    uint8_t national_floor;
    uint16_t type_mask;           /* accepted resolved types, bit i = enum th_phone_type i, eleven bits */
    const th_phone_label *labels; /* sorted, lowercase ASCII */
    size_t label_count;
} th_phone_config;

typedef struct {
    size_t start;
    size_t end;
    uint16_t country_code;
    uint8_t nsn_len;
    char nsn[TH_PHONE_NSN_CAPACITY];
    uint8_t ext_len;
    char ext[TH_PHONE_MAX_EXTENSION + 1];
    int region;   /* index into th_phone_regions, or -1 in possible mode when no region claims the number */
    uint8_t type; /* enum th_phone_type */
} th_phone_match;

/* Recognize the phone number whose run of digits contains `digit_pos`, never expanding left past `left_bound`.
   Returns 1 and fills `match`, or 0. `*retry` is the next position a digit may start a probe at: the match end (or
   the end of a discarded second extension) on success, the next candidate group on failure. */
int th_phone_find(th_phone_read read, const void *text, size_t len, size_t left_bound, size_t digit_pos,
                  const th_phone_config *config, th_phone_match *match, size_t *retry);

/* Read `text[start:end]` as one number, the way phonenumbers' parse reads a string it is handed: from the first plus
   or digit (an RFC 3966 `tel:` scheme among what is skipped) to the last digit, letter or `#`, cut at a second
   number's `/x`; what remains is punctuation, digits and ASCII letters with an extension at its end, three or more
   letters spelling a vanity number. An RFC 3966 `;phone-context=` names the calling code local digits belong to, or
   a domain under which they read as a national number; `;isub=` and what follows it are not part of the number. A
   string over 250 characters is no number. Returns 1 and fills `match`, or 0. */
int th_phone_parse(th_phone_read read, const void *text, size_t start, size_t end, const th_phone_config *config,
                   th_phone_match *match);

/* The decimal value of a code point under Unicode Nd, or -1. */
int th_phone_digit_value(uint32_t code);

/* Fill `config->national_floor` from the configured regions and mode; 0 with no regions. */
void th_phone_config_floor(th_phone_config *config);

/* The index of the region with this two- or three-letter uppercase code, or -1. */
int th_phone_region_index(const char *code, size_t len);

/* The uppercase code of the region at a table index, with its length. */
const char *th_phone_region_code(int index, size_t *len);

/* libphonenumber's PhoneNumberFormat, in its order. */
enum th_phone_style {
    TH_PHONE_STYLE_E164 = 0,
    TH_PHONE_STYLE_INTERNATIONAL = 1,
    TH_PHONE_STYLE_NATIONAL = 2,
    TH_PHONE_STYLE_RFC3966 = 3,
};

/* formatNumber: write the number in `style` to `out` (TH_PHONE_FORMAT_CAPACITY bytes, ASCII) and return its
   length. `nsn` holds ASCII digits; `ext` may be empty. A calling code the tables do not assign formats as its bare
   national number, as libphonenumber does. */
size_t th_phone_format_number(unsigned country_code, const char *nsn, size_t nsn_len, const char *ext, size_t ext_len,
                              enum th_phone_style style, char *out);

enum th_phone_check {
    TH_PHONE_CHECK_OK = 0,
    TH_PHONE_CHECK_COUNTRY_CODE, /* no calling code group carries this country code */
    TH_PHONE_CHECK_REGION,       /* the region is not in the country code's group */
    TH_PHONE_CHECK_NUMBER,       /* the tables do not produce this national number with this region and type */
};

/* Would the recognizer ever produce this (country_code, nsn, region, type)? `nsn` holds 2 to 17 ASCII digits;
   `region` may be NULL; `type` is an enum th_phone_type, where TH_PHONE_UNKNOWN asks the possible-mode question and
   any other type the validity one. */
enum th_phone_check th_phone_number_check(unsigned country_code, const char *nsn, size_t nsn_len, const char *region,
                                          size_t region_len, int type);

#endif /* TURBOHTML_CLEAN_PHONE_H */
